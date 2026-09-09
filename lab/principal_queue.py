"""Durable local handoff queue. Submission never starts a model or worker."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from lab.directed import preflight_directed_task, run_directed_task
from lab.directed_gate import load_contract, redact


def _now():
    return datetime.now(timezone.utc).isoformat()


class PrincipalQueue:
    """Explicit dispatch; uncertain launches are never automatically repeated."""

    def __init__(self, root, *, run_root=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_root = Path(run_root).resolve() if run_root else self.root / "runs"
        self.database = self.root / "queue.sqlite3"
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                payload_hash TEXT NOT NULL, contract BLOB NOT NULL,
                input_root TEXT NOT NULL, parent_repo TEXT NOT NULL, run_root TEXT NOT NULL,
                task_id TEXT NOT NULL, project_id TEXT NOT NULL, objective TEXT NOT NULL,
                state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT '', artifact_root TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '{}')""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _public(row):
        value = dict(row)
        value["contract_hash"] = hashlib.sha256(value.pop("contract")).hexdigest()
        value["detail"] = json.loads(value["detail"])
        return value

    def submit(self, contract_path, *, input_root, parent_repo, idempotency_key, expected_contract_hash=None):
        """Validate and freeze exact contract bytes; defer integrity preflight to dispatch."""
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("An idempotency key of 1–200 characters is required")
        contract, raw = load_contract(contract_path)
        if expected_contract_hash is not None and hashlib.sha256(raw).hexdigest() != expected_contract_hash:
            raise ValueError('Contract changed since preview; submission rejected')
        inputs = str(Path(input_root).resolve(strict=True))
        parent = str(Path(parent_repo).resolve(strict=True))
        if not Path(inputs).is_dir() or not Path(parent).is_dir():
            raise ValueError("input_root and parent_repo must be directories")
        binding = json.dumps([inputs, parent, str(self.run_root)], ensure_ascii=False).encode()
        digest = hashlib.sha256(raw + b"\x00" + binding).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if prior:
                if prior["payload_hash"] != digest:
                    raise ValueError("Idempotency key already binds a different payload")
                return self._public(prior)
            job_id = "job-" + uuid.uuid4().hex
            stamp = _now()
            db.execute("""INSERT INTO jobs
                (job_id,idempotency_key,payload_hash,contract,input_root,parent_repo,run_root,
                 task_id,project_id,objective,state,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, idempotency_key, digest, raw, inputs, parent, str(self.run_root),
                 contract.task_id, contract.project_id, contract.objective, "QUEUED", stamp, stamp))
            return self._public(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def list(self):
        with self._connect() as db:
            return [self._public(row) for row in db.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    def status(self, job_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        value = self._public(row)
        # Queue state and worker state deliberately remain distinct.
        value["run_status"] = None
        if row["artifact_root"]:
            runtime = Path(row["artifact_root"]) / "runtime.json"
            try:
                if runtime.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError("Runtime exceeds read limit")
                data = json.loads(runtime.read_text(encoding="utf-8"))
                if data.get("run_id") != row["run_id"]:
                    raise ValueError("Runtime run binding mismatch")
                value["run_status"] = data.get("status")
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                value["runtime_warning"] = redact(exc)
        return value

    def _update(self, job_id, state, detail, *, run_id="", artifact_root=""):
        with self._connect() as db:
            db.execute("UPDATE jobs SET state=?,detail=?,updated_at=?,run_id=?,artifact_root=? WHERE job_id=?",
                       (state, json.dumps(detail), _now(), run_id, artifact_root, job_id))

    def dispatch(self, job_id=None):
        """Claim one queued job atomically, preflight, then launch a background run once.

        An exception after claiming leaves DISPATCHING. Even if the worker started,
        a lost response cannot cause this queue to launch it again.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if job_id:
                row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row is None:
                    raise KeyError(job_id)
                if row["state"] != "QUEUED":
                    raise ValueError("Only a QUEUED job can be dispatched")
            else:
                row = db.execute("SELECT * FROM jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
                if row is None:
                    return None
            job_id = row["job_id"]
            db.execute("UPDATE jobs SET state='DISPATCHING',updated_at=? WHERE job_id=?", (_now(), job_id))
        try:
            folder = self.root / "contracts"
            folder.mkdir(exist_ok=True)
            path = folder / (job_id + ".json")
            # Reconstitute from the durable authoritative bytes, not the submission file.
            path.write_bytes(row["contract"])
            kwargs = {"input_root": row["input_root"], "parent_repo": row["parent_repo"], "root": row["run_root"]}
            gate = preflight_directed_task(path, **kwargs)
            if gate.get("gate_status") not in {"FEASIBLE", "FEASIBLE_WITH_LIMITATIONS"}:
                self._update(job_id, "BLOCKED_PREFLIGHT", {"gate_status": gate.get("gate_status"), "error": redact(str(gate.get('error', '')))})
            else:
                result = run_directed_task(path, background=True, **kwargs)
                if result.run_id:
                    self._update(job_id, "DISPATCHED", {"launch_status": result.status},
                                 run_id=result.run_id, artifact_root=result.artifact_root)
                else:
                    self._update(job_id, "BLOCKED_PREFLIGHT", {"gate_status": result.status})
        except Exception as exc:
            self._update(job_id, "DISPATCHING", {"error": redact(exc), "ambiguous_launch": True})
        return self.status(job_id)

    def retry_preflight(self, job_id):
        """Only a known no-launch preflight rejection may explicitly return to queue."""
        with self._connect() as db:
            cursor = db.execute("UPDATE jobs SET state='QUEUED',updated_at=?,detail='{}' "
                                "WHERE job_id=? AND state='BLOCKED_PREFLIGHT'", (_now(), job_id))
            if cursor.rowcount != 1:
                raise ValueError("Only BLOCKED_PREFLIGHT may be retried; uncertain launches require manual reconciliation")
        return self.status(job_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Local queue directory")
    parser.add_argument("--run-root", help="Research state directory for newly submitted jobs")
    sub = parser.add_subparsers(dest="command", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("contract")
    submit.add_argument("--input-root", required=True)
    submit.add_argument("--parent-repo", required=True)
    submit.add_argument("--idempotency-key", required=True)
    sub.add_parser("list")
    sub.add_parser("status").add_argument("job_id")
    sub.add_parser("dispatch").add_argument("job_id", nargs="?")
    sub.add_parser("retry-preflight").add_argument("job_id")
    args = parser.parse_args(argv)
    queue = PrincipalQueue(args.root, run_root=args.run_root)
    if args.command == "submit":
        result = queue.submit(args.contract, input_root=args.input_root, parent_repo=args.parent_repo,
                              idempotency_key=args.idempotency_key)
    elif args.command == "list":
        result = queue.list()
    elif args.command == "status":
        result = queue.status(args.job_id)
    elif args.command == "dispatch":
        result = queue.dispatch(args.job_id)
    else:
        result = queue.retry_preflight(args.job_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
