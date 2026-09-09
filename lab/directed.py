"""Public principal-researcher API and CLI. All legacy experiment modes remain separate."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from typing import Any

from lab.directed_contract import DirectedTaskContract
from lab.directed_engine import DirectedWorkerPool, write
from lab.directed_gate import CapabilityAndIntegrityGate, input_path, load_contract, redact, sha, verify_snapshot
from lab.directed_package import build_package, verify_package
from lab.integrity import EvidenceSigner, ProjectBusyError, ProjectRunLock, process_alive

ROOT = Path(__file__).resolve().parents[1] / "research_state"


def code_inventory(base: Path | None = None) -> dict[str, str]:
    """Pin all local lab helpers and exact membership, not just the coordinator."""
    base = base or Path(__file__).parent
    return {p.relative_to(base).as_posix(): sha(p.read_bytes()) for p in sorted(base.rglob("*.py")) if p.is_file()}


def verify_code_inventory(expected: dict, base: Path | None = None) -> None:
    if expected != code_inventory(base):
        raise ValueError("Execution code inventory changed; new run required")


@dataclass
class DirectedResult:
    task_id: str
    run_id: str
    status: str
    artifact_root: str
    claim_ledger_path: str = ""
    package_path: str = ""
    package_sha256: str = ""


def location(project_id: str, run_id: str, root: Path | None = None) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", project_id) or not re.fullmatch(
        r"directed-[a-f0-9]{32}", run_id
    ):
        raise ValueError("Invalid project/run identifier")
    return (root or ROOT) / project_id / "directed" / run_id


def validate_contract(path) -> dict:
    try:
        c, raw = load_contract(path)
        return {
            "ok": True,
            "contract_hash": sha(raw),
            "task_id": c.task_id,
            "schema": DirectedTaskContract.model_json_schema(),
            "llm_calls_made": 0,
        }
    except Exception as exc:
        return {"ok": False, "error": redact(exc), "llm_calls_made": 0}


def preflight_directed_task(path, *, input_root=None, parent_repo=None, provider=None, root=None) -> dict:
    try:
        c, _ = load_contract(path)
        project_root = (Path(root) if root else ROOT) / c.project_id
    except Exception:
        project_root = None
    return CapabilityAndIntegrityGate().check(
        path,
        input_root=Path(input_root) if input_root else None,
        parent_repo=Path(parent_repo) if parent_repo else None,
        project_root=project_root,
        provider=provider,
    )


def _launch(project_id, run_id, *, resume=False, root=None):
    command = [sys.executable, "-m", "lab.directed", "_execute", project_id, run_id]
    if resume:
        command += ["--resume"]
    if root:
        command += ["--root", str(root)]
    kwargs: dict[str, Any] = {
        "cwd": str(Path(__file__).resolve().parents[1]),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    except OSError:
        if os.name != "nt":
            raise
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(command, **kwargs)
    return process.pid


def run_directed_task(
    path, *, input_root=None, parent_repo=None, provider=None, background=False, root=None
) -> DirectedResult:
    c, raw = load_contract(path)
    project_root = (Path(root) if root else ROOT) / c.project_id
    gate = preflight_directed_task(path, input_root=input_root, parent_repo=parent_repo, provider=provider, root=root)
    if gate["gate_status"] not in {"FEASIBLE", "FEASIBLE_WITH_LIMITATIONS"}:
        # Invalid/missing capability stops before any provider or persistent research mutation.
        return DirectedResult(c.task_id, "", gate["gate_status"], "")
    with ProjectRunLock(project_root):
        registry = project_root / "directed_tasks.json"
        tasks = json.loads(registry.read_text()) if registry.exists() else {}
        if c.task_id in tasks and tasks[c.task_id] != sha(raw):
            raise ValueError("Same task_id cannot use different contract bytes")
        tasks[c.task_id] = sha(raw)
        write(registry, tasks)
        run_id = "directed-" + uuid.uuid4().hex
        folder = location(c.project_id, run_id, Path(root) if root else None)
        folder.mkdir(parents=True)
        (folder / "TASK_CONTRACT.json").write_bytes(raw)
        (folder / "inputs").mkdir()
        for entry in gate["inputs"]:
            if entry["status"] != "PASS":
                continue
            original = input_path(Path(gate["input_root"]), entry["path"])
            frozen = input_path(folder / "inputs", entry["path"])
            frozen.parent.mkdir(parents=True, exist_ok=True)
            data = original.read_bytes()
            if sha(data) != entry["sha256"]:
                raise ValueError("Input changed during snapshot")
            frozen.write_bytes(data)
        write(folder / "CAPABILITY_REPORT.json", gate)
        write(
            folder / "context.json",
            {
                "contract_hash": sha(raw),
                "input_root": gate["input_root"],
                "parent_repo": gate["parent_repo"],
                "code_hashes": code_inventory(),
                "capability_report_hash": sha((folder / "CAPABILITY_REPORT.json").read_bytes()),
                "run_id": run_id,
            },
        )
        context = json.loads((folder / "context.json").read_text())
        signer = EvidenceSigner(folder / "private")
        write(folder / "context.seal.json", {"seal": signer.sign("directed-context", context)})
        write(folder / "runtime.json", {"status": "QUEUED", "task_id": c.task_id, "run_id": run_id})
    if background:
        if provider is not None:
            raise ValueError("Injected test providers cannot cross background process boundaries")
        _launch(c.project_id, run_id, root=root)
        return DirectedResult(c.task_id, run_id, "QUEUED", str(folder))
    return _execute(c.project_id, run_id, provider=provider, root=root)


def _execute(project_id, run_id, *, provider=None, resume=False, root=None):
    try:
        return _execute_impl(project_id, run_id, provider=provider, resume=resume, root=root)
    except ProjectBusyError:
        # A competing process owns state; do not overwrite its runtime on failure.
        raise
    except Exception as exc:
        folder = location(project_id, run_id, Path(root) if root else None)
        path = folder / "runtime.json"
        if path.is_file():
            runtime = json.loads(path.read_text())
            runtime.update(interrupted_phase=runtime.get("status"), status="PAUSED_ERROR", last_error=redact(exc))
            write(path, runtime)
            project_runtime = folder.parent.parent / "runtime.json"
            if project_runtime.is_file() and json.loads(project_runtime.read_text()).get("run_id") == run_id:
                write(project_runtime, runtime)
        raise


def _execute_impl(project_id, run_id, *, provider=None, resume=False, root=None):
    folder = location(project_id, run_id, Path(root) if root else None)
    project_root = folder.parent.parent
    with ProjectRunLock(project_root):
        current = json.loads((folder / "runtime.json").read_text())
        terminal = (
            current["status"] in {"COMPLETED", "COMPLETED_WITH_OPEN_CLAIMS", "REFUTED"}
            and (folder / "result.json").is_file()
        )
        if not resume and current["status"] != "QUEUED" and not terminal:
            raise ValueError("Existing run requires explicit resume")
        context = json.loads((folder / "context.json").read_text())
        seal_path = folder / "context.seal.json"
        if not seal_path.is_file():
            raise ValueError("Frozen context seal missing")
        seal = json.loads(seal_path.read_text()).get("seal")
        if not EvidenceSigner(folder / "private").verify("directed-context", context, seal):
            raise ValueError("Frozen context seal mismatch")
        if context.get("run_id") != run_id:
            raise ValueError("Frozen context run binding mismatch")
        if sha((folder / "CAPABILITY_REPORT.json").read_bytes()) != context.get("capability_report_hash"):
            raise ValueError("Frozen capability report changed")
        c, raw = load_contract(folder / "TASK_CONTRACT.json")
        if sha(raw) != context["contract_hash"]:
            raise ValueError("Frozen contract changed")
        verify_code_inventory(context["code_hashes"])
        gate = CapabilityAndIntegrityGate().check(
            folder / "TASK_CONTRACT.json",
            input_root=Path(context["input_root"]),
            parent_repo=Path(context["parent_repo"]),
            provider=provider,
            check_lock=False,
        )
        frozen_gate = json.loads((folder / "CAPABILITY_REPORT.json").read_text())
        if gate["gate_status"] not in {"FEASIBLE", "FEASIBLE_WITH_LIMITATIONS"}:
            write(
                folder / "runtime.json",
                {"status": gate["gate_status"], "task_id": c.task_id, "run_id": run_id, "error": gate.get("error")},
            )
            return _result(folder)
        for field in ("capabilities", "provider", "endpoint", "inputs", "contract_hash"):
            if gate.get(field) != frozen_gate.get(field):
                raise ValueError("Frozen input/capability/provider universe changed")
        verify_snapshot(c, folder / "inputs", frozen_gate["inputs"])
        if terminal:
            return verified_result(folder)
        if resume:
            (project_root / "stop.flag").unlink(missing_ok=True)
            (folder / "stop.flag").unlink(missing_ok=True)
        write(project_root / "worker.json", {"pid": os.getpid(), "run_id": run_id, "mode": "directed_task"})
        pool = DirectedWorkerPool(
            c, folder / "TASK_CONTRACT.json", folder, project_root, gate, provider=provider, resume=resume
        )
        package = pool.run()
        archive, digest = build_package(package)
        verify_required_outputs(c, package)
        runtime = json.loads((folder / "runtime.json").read_text())
        final_status = runtime.get("final_status", runtime["status"])
        if final_status == "FINALIZING":
            raise ValueError("Missing final state while finalizing delivery")
        result = DirectedResult(
            c.task_id, run_id, final_status, str(folder), str(package / "CLAIM_LEDGER.json"), str(archive), digest
        )
        write(folder / "result.json", asdict(result))
        runtime["status"] = final_status
        write(folder / "runtime.json", runtime)
        write(project_root / "runtime.json", runtime)
        return result


def verified_result(folder: Path) -> DirectedResult:
    result = DirectedResult(**json.loads((folder / "result.json").read_text()))
    verified = verify_package(folder / "package")
    verify_required_outputs(load_contract(folder / "TASK_CONTRACT.json")[0], folder / "package")
    if result.run_id != folder.name or not result.package_sha256 or verified["package_sha256"] != result.package_sha256:
        raise ValueError("Final result/package binding changed")
    if Path(result.package_path).resolve() != (folder / "package" / "COMPLETE_PACKAGE.zip").resolve():
        raise ValueError("Final result points outside delivery package")
    return result


def verify_required_outputs(contract: DirectedTaskContract, package: Path) -> None:
    missing = [name for name in contract.required_outputs if not input_path(package, name).is_file()]
    if missing:
        raise ValueError("Required delivery outputs missing: " + ", ".join(missing))


def _result(folder):
    data = json.loads((folder / "runtime.json").read_text())
    if (folder / "result.json").exists():
        result = DirectedResult(**json.loads((folder / "result.json").read_text()))
        if result.status == data["status"]:
            return result
    return DirectedResult(data.get("task_id", ""), folder.name, data["status"], str(folder))


def get_directed_task_status(project_id, run_id, *, root=None):
    folder = location(project_id, run_id, Path(root) if root else None)
    data = json.loads((folder / "runtime.json").read_text())
    if data["status"] == "RUNNING" and not process_alive(int(data.get("pid", 0))):
        data["status"] = "INTERRUPTED"
    ledger = folder / "package" / "CLAIM_LEDGER.json"
    if ledger.exists():
        data["claim_ledger"] = json.loads(ledger.read_text(encoding="utf-8"))
    return data


def stop_directed_task(project_id, run_id, *, root=None):
    folder = location(project_id, run_id, Path(root) if root else None)
    runtime = json.loads((folder / "runtime.json").read_text())
    if runtime.get("run_id") != run_id or runtime.get("status") not in {"QUEUED", "RUNNING"}:
        raise ValueError("Run is not queued or running")
    (folder / "stop.flag").touch()
    project_runtime = folder.parent.parent / "runtime.json"
    if project_runtime.is_file() and json.loads(project_runtime.read_text()).get("run_id") == run_id:
        (folder.parent.parent / "stop.flag").touch()
    return {"status": "STOP_REQUESTED", "run_id": run_id}


def resume_directed_task(project_id, run_id, *, provider=None, background=False, root=None):
    if background:
        _launch(project_id, run_id, resume=True, root=root)
        folder = location(project_id, run_id, Path(root) if root else None)
        return DirectedResult(load_contract(folder / "TASK_CONTRACT.json")[0].task_id, run_id, "QUEUED", str(folder))
    return _execute(project_id, run_id, provider=provider, resume=True, root=root)


def verify_directed_task(project_id, run_id, *, root=None):
    folder = location(project_id, run_id, Path(root) if root else None)
    result = verify_package(folder / "package")
    verify_required_outputs(load_contract(folder / "TASK_CONTRACT.json")[0], folder / "package")
    return result


def package_directed_task(project_id, run_id, *, root=None):
    folder = location(project_id, run_id, Path(root) if root else None)
    verify_package(folder / "package")
    archive, digest = build_package(folder / "package")
    verify_required_outputs(load_contract(folder / "TASK_CONTRACT.json")[0], folder / "package")
    return {"path": str(archive), "sha256": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["validate", "preflight", "run", "status", "stop", "resume", "verify", "package", "_execute", "schema"],
    )
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--input-root")
    parser.add_argument("--parent-repo")
    parser.add_argument("--root")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "schema":
            result = DirectedTaskContract.model_json_schema()
        elif args.action == "validate":
            result = validate_contract(args.paths[0])
        elif args.action == "preflight":
            result = preflight_directed_task(
                args.paths[0], input_root=args.input_root, parent_repo=args.parent_repo, root=args.root
            )
        elif args.action == "run":
            result = asdict(
                run_directed_task(
                    args.paths[0],
                    input_root=args.input_root,
                    parent_repo=args.parent_repo,
                    root=args.root,
                    background=args.background,
                )
            )
        elif args.action == "_execute":
            result = asdict(_execute(*args.paths, root=args.root, resume=args.resume))
        else:
            fn = {
                "status": get_directed_task_status,
                "stop": stop_directed_task,
                "resume": resume_directed_task,
                "verify": verify_directed_task,
                "package": package_directed_task,
            }[args.action]
            result = fn(
                *args.paths, root=args.root, **({"background": args.background} if args.action == "resume" else {})
            )
            if isinstance(result, DirectedResult):
                result = asdict(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"status": "PAUSED_ERROR", "error": redact(exc)}, ensure_ascii=False))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
