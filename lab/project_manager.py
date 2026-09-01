from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.research_state import ResearchState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-").lower()
    return value[:60] or "research"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


@dataclass
class ProjectInfo:
    project_id: str
    project_uuid: str
    title: str
    description: str
    problem: str
    experiment: str = "Teorem Araştırması"
    literature_query: str = ""
    tags: list[str] | None = None
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    status: str = "READY"
    runtime: dict[str, Any] | None = None
    counts: dict[str, int] | None = None
    run_count: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    last_run: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProjectManager:
    """Project-centric facade with immutable project UUIDs and indexed run history."""

    def __init__(self, root: str | Path = "research_state", runs_dir: str | Path = "runs"):
        self.root = Path(root)
        self.runs_dir = Path(runs_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_path = self.root / ".active_project.json"

    def project_root(self, project_id: str) -> Path:
        return self.root / _slug(project_id)

    def create_project(
        self,
        *,
        title: str,
        problem: str,
        project_id: str | None = None,
        description: str = "",
        experiment: str = "Teorem Araştırması",
        literature_query: str = "",
        tags: list[str] | None = None,
        activate: bool = True,
    ) -> ProjectInfo:
        if not title.strip():
            raise ValueError("Proje adı boş olamaz.")
        if not problem.strip():
            raise ValueError("Başlangıç problemi boş olamaz.")
        pid = _slug(project_id or title)
        root = self.project_root(pid)
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f"`{pid}` project_id zaten var.")
        root.mkdir(parents=True, exist_ok=True)
        now = _now()
        project_uuid = uuid.uuid4().hex
        metadata = {
            "project_id": pid,
            "project_uuid": project_uuid,
            "title": title.strip(),
            "description": description.strip(),
            "experiment": experiment,
            "literature_query": literature_query.strip(),
            "tags": [str(x).strip() for x in (tags or []) if str(x).strip()],
            "created_at": now,
            "updated_at": now,
            "archived": False,
            "status": "READY",
        }
        _write_json(root / "project.json", metadata)
        state = ResearchState(root)
        state.freeze_problem(problem, metadata={"project_id": pid, "project_uuid": project_uuid, "title": title.strip(), "created_from": "project_manager"})
        if activate:
            self.set_active(pid)
        return self.get(pid)

    def _legacy_metadata(self, root: Path) -> dict[str, Any]:
        frozen = _read_json(root / "problem_frozen.json", {})
        created = str(frozen.get("frozen_at") or "")
        return {
            "project_id": root.name,
            "project_uuid": uuid.uuid4().hex,
            "title": root.name.replace("-", " ").replace("_", " ").title(),
            "description": "Eski research_state projesi (metadata otomatik türetildi).",
            "experiment": "Teorem Araştırması",
            "literature_query": "",
            "tags": [],
            "created_at": created,
            "updated_at": created,
            "archived": False,
            "status": "READY",
            "uuid_migrated_at": _now(),
        }

    def _base_metadata(self, root: Path) -> dict[str, Any]:
        path = root / "project.json"
        raw = _read_json(path, None)
        if not isinstance(raw, dict):
            raw = self._legacy_metadata(root)
            _write_json(path, raw)
            return raw
        if not str(raw.get("project_uuid") or "").strip():
            raw = dict(raw)
            raw["project_uuid"] = uuid.uuid4().hex
            raw["uuid_migrated_at"] = _now()
            _write_json(path, raw)
        return raw

    def _runtime(self, root: Path) -> dict[str, Any]:
        value = _read_json(root / "runtime.json", {})
        return value if isinstance(value, dict) else {}

    def _counts(self, root: Path) -> dict[str, int]:
        try:
            items = ResearchState(root).list_items()
        except Exception:
            items = []
        counts = {"OPEN": 0, "FAIL": 0, "PROVEN": 0, "KNOWN": 0, "TOTAL": 0}
        for item in items:
            counts["TOTAL"] += 1
            if item.status in counts:
                counts[item.status] += 1
        return counts

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows = []
        if not path.exists():
            return rows
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
        return rows

    def _indexed_runs(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for row in self._read_jsonl(self.runs_dir / "index.jsonl"):
            run_id = str(row.get("run_id") or "")
            if not run_id:
                continue
            merged = dict(latest.get(run_id) or {})
            merged.update(row)
            latest[run_id] = merged
        return latest

    @staticmethod
    def _trace_identity(trace_path: Path) -> tuple[str | None, str | None, str | None]:
        pid = None
        project_uuid = None
        first_ts = None
        try:
            with trace_path.open("r", encoding="utf-8") as handle:
                for i, line in enumerate(handle):
                    if i > 120:
                        break
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    first_ts = first_ts or (str(ev.get("ts")) if ev.get("ts") else None)
                    if ev.get("project_id"):
                        pid = str(ev["project_id"])
                    if ev.get("project_uuid"):
                        project_uuid = str(ev["project_uuid"])
                    if pid and project_uuid:
                        break
        except OSError:
            pass
        return pid, project_uuid, first_ts

    def _summary_row(self, run_dir: Path) -> dict[str, Any]:
        summary = _read_json(run_dir / "summary.json", {})
        return {
            "run": run_dir.name,
            "run_dir": str(run_dir),
            "started_at": summary.get("started_at", ""),
            "finished_at": summary.get("finished_at", ""),
            "wall_time_s": float(summary.get("wall_time_s", 0.0) or 0.0),
            "calls": int(summary.get("total_calls", 0) or 0),
            "tokens": int(summary.get("total_tokens", 0) or 0),
            "cost_usd": float(summary.get("total_cost_usd", 0.0) or 0.0),
        }

    def run_summaries(self, project_id: str) -> list[dict[str, Any]]:
        pid = _slug(project_id)
        root = self.project_root(pid)
        if not root.exists():
            return []
        metadata = self._base_metadata(root)
        current_uuid = str(metadata.get("project_uuid") or "")
        rows: list[dict[str, Any]] = []
        indexed = self._indexed_runs()
        matched_ids: set[str] = set()
        for run_id, row in indexed.items():
            if str(row.get("project_id") or "") != pid:
                continue
            row_uuid = str(row.get("project_uuid") or "")
            if row_uuid and row_uuid != current_uuid:
                continue
            run_dir = Path(str(row.get("run_dir") or self.runs_dir / run_id))
            if not run_dir.exists():
                continue
            rows.append(self._summary_row(run_dir))
            matched_ids.add(run_id)
        if rows:
            return sorted(rows, key=lambda x: str(x.get("started_at") or x.get("run")), reverse=True)

        # One-time compatibility fallback for pre-index runs. New UI paths do not
        # repeatedly scan traces once index.jsonl exists.
        created_at = _parse_time(str(metadata.get("created_at") or ""))
        if self.runs_dir.exists():
            for trace_path in self.runs_dir.glob("*/trace.jsonl"):
                trace_pid, trace_uuid, first_ts = self._trace_identity(trace_path)
                if trace_pid != pid:
                    continue
                if trace_uuid:
                    if trace_uuid != current_uuid:
                        continue
                elif created_at:
                    ts = _parse_time(first_ts or "")
                    if ts and ts < created_at:
                        continue
                rows.append(self._summary_row(trace_path.parent))
        return sorted(rows, key=lambda x: str(x.get("started_at") or x.get("run")), reverse=True)

    def get(self, project_id: str) -> ProjectInfo:
        pid = _slug(project_id)
        root = self.project_root(pid)
        if not root.exists():
            raise KeyError(pid)
        meta = self._base_metadata(root)
        frozen = _read_json(root / "problem_frozen.json", {})
        runtime = self._runtime(root)
        runs = self.run_summaries(pid)
        status = str(runtime.get("status") or meta.get("status") or "READY")
        return ProjectInfo(
            project_id=pid,
            project_uuid=str(meta.get("project_uuid") or ""),
            title=str(meta.get("title") or pid),
            description=str(meta.get("description") or ""),
            problem=str(frozen.get("problem") or ""),
            experiment=str(meta.get("experiment") or "Teorem Araştırması"),
            literature_query=str(meta.get("literature_query") or ""),
            tags=list(meta.get("tags") or []),
            created_at=str(meta.get("created_at") or ""),
            updated_at=str(meta.get("updated_at") or ""),
            archived=bool(meta.get("archived", False)),
            status=status,
            runtime=runtime,
            counts=self._counts(root),
            run_count=len(runs),
            total_tokens=sum(int(r.get("tokens", 0) or 0) for r in runs),
            total_cost_usd=sum(float(r.get("cost_usd", 0.0) or 0.0) for r in runs),
            last_run=str(runs[0].get("run") if runs else ""),
        )

    def list_projects(self, *, include_archived: bool = False) -> list[ProjectInfo]:
        projects = []
        for root in self.root.iterdir():
            if not root.is_dir():
                continue
            if not any((root / name).exists() for name in ("project.json", "problem_frozen.json", "state.json", "runtime.json")):
                continue
            try:
                info = self.get(root.name)
            except Exception:
                continue
            if info.archived and not include_archived:
                continue
            projects.append(info)
        return sorted(projects, key=lambda p: p.updated_at or p.created_at, reverse=True)

    def set_active(self, project_id: str) -> ProjectInfo:
        info = self.get(project_id)
        _write_json(self.active_path, {"project_id": info.project_id, "project_uuid": info.project_uuid, "updated_at": _now()})
        return info

    def active_project_id(self) -> str | None:
        raw = _read_json(self.active_path, {})
        pid = str(raw.get("project_id") or "") if isinstance(raw, dict) else ""
        return pid or None

    def active_project(self) -> ProjectInfo | None:
        pid = self.active_project_id()
        if not pid:
            return None
        try:
            info = self.get(pid)
        except KeyError:
            self.clear_active()
            return None
        active = _read_json(self.active_path, {})
        stored_uuid = str(active.get("project_uuid") or "") if isinstance(active, dict) else ""
        if stored_uuid and stored_uuid != info.project_uuid:
            self.clear_active()
            return None
        return info

    def clear_active(self) -> None:
        self.active_path.unlink(missing_ok=True)

    def touch(self, project_id: str, **updates: Any) -> ProjectInfo:
        pid = _slug(project_id)
        root = self.project_root(pid)
        meta = self._base_metadata(root)
        allowed = {"title", "description", "experiment", "literature_query", "tags", "archived", "status"}
        for key, value in updates.items():
            if key in allowed:
                meta[key] = value
        meta["updated_at"] = _now()
        _write_json(root / "project.json", meta)
        return self.get(pid)

    def archive(self, project_id: str, archived: bool = True) -> ProjectInfo:
        info = self.touch(project_id, archived=bool(archived))
        if archived and self.active_project_id() == info.project_id:
            self.clear_active()
        return info

    def clone(self, project_id: str, *, title: str, new_project_id: str | None = None) -> ProjectInfo:
        source = self.get(project_id)
        return self.create_project(
            title=title,
            project_id=new_project_id,
            problem=source.problem,
            description=source.description,
            experiment=source.experiment,
            literature_query=source.literature_query,
            tags=list(source.tags or []),
            activate=False,
        )

    def delete(self, project_id: str) -> None:
        pid = _slug(project_id)
        root = self.project_root(pid)
        if not root.exists():
            raise KeyError(pid)
        if self.active_project_id() == pid:
            self.clear_active()
        shutil.rmtree(root)
