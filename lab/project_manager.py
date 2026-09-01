from __future__ import annotations

import json
import re
import shutil
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


@dataclass
class ProjectInfo:
    project_id: str
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
    """Project-centric facade over existing research_state/<project_id> folders.

    Existing folders created before project.json existed remain discoverable. New
    metadata is additive; ResearchState file formats are left untouched.
    """

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
        metadata = {
            "project_id": pid,
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
        state.freeze_problem(
            problem,
            metadata={"project_id": pid, "title": title.strip(), "created_from": "project_manager"},
        )
        if activate:
            self.set_active(pid)
        return self.get(pid)

    def _legacy_metadata(self, root: Path) -> dict[str, Any]:
        frozen = _read_json(root / "problem_frozen.json", {})
        created = str(frozen.get("frozen_at") or "")
        return {
            "project_id": root.name,
            "title": root.name.replace("-", " ").replace("_", " ").title(),
            "description": "Eski research_state projesi (metadata otomatik türetildi).",
            "experiment": "Teorem Araştırması",
            "literature_query": "",
            "tags": [],
            "created_at": created,
            "updated_at": created,
            "archived": False,
            "status": "READY",
        }

    def _base_metadata(self, root: Path) -> dict[str, Any]:
        raw = _read_json(root / "project.json", None)
        if isinstance(raw, dict):
            return raw
        return self._legacy_metadata(root)

    def _runtime(self, root: Path) -> dict[str, Any]:
        value = _read_json(root / "runtime.json", {})
        return value if isinstance(value, dict) else {}

    def _counts(self, root: Path) -> dict[str, int]:
        data = _read_json(root / "state.json", {})
        items = data.get("items", []) if isinstance(data, dict) else []
        counts = {"OPEN": 0, "FAIL": 0, "PROVEN": 0, "KNOWN": 0, "TOTAL": 0}
        for item in items if isinstance(items, list) else []:
            status = str(item.get("status") or "")
            counts["TOTAL"] += 1
            if status in counts:
                counts[status] += 1
        return counts

    @staticmethod
    def _trace_project_id(trace_path: Path) -> str | None:
        try:
            with trace_path.open("r", encoding="utf-8") as handle:
                for i, line in enumerate(handle):
                    if i > 80:
                        break
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("project_id"):
                        return str(ev["project_id"])
        except OSError:
            return None
        return None

    def run_summaries(self, project_id: str) -> list[dict[str, Any]]:
        pid = _slug(project_id)
        rows: list[dict[str, Any]] = []
        if not self.runs_dir.exists():
            return rows
        for trace_path in self.runs_dir.glob("*/trace.jsonl"):
            if self._trace_project_id(trace_path) != pid:
                continue
            summary = _read_json(trace_path.parent / "summary.json", {})
            if not isinstance(summary, dict):
                summary = {}
            rows.append(
                {
                    "run_id": trace_path.parent.name,
                    "path": str(trace_path.parent),
                    "started_at": summary.get("started_at", ""),
                    "finished_at": summary.get("finished_at", ""),
                    "wall_time_s": float(summary.get("wall_time_s", 0) or 0),
                    "total_calls": int(summary.get("total_calls", 0) or 0),
                    "total_tokens": int(summary.get("total_tokens", 0) or 0),
                    "total_cost_usd": float(summary.get("total_cost_usd", 0) or 0),
                }
            )
        rows.sort(key=lambda x: str(x.get("started_at") or x["run_id"]), reverse=True)
        return rows

    def get(self, project_id: str) -> ProjectInfo:
        pid = _slug(project_id)
        root = self.project_root(pid)
        if not root.exists():
            raise KeyError(pid)
        metadata = self._base_metadata(root)
        frozen = _read_json(root / "problem_frozen.json", {})
        runtime = self._runtime(root)
        runs = self.run_summaries(pid)
        status = str(runtime.get("status") or metadata.get("status") or "READY")
        updated = str(runtime.get("updated_at") or metadata.get("updated_at") or "")
        return ProjectInfo(
            project_id=pid,
            title=str(metadata.get("title") or pid),
            description=str(metadata.get("description") or ""),
            problem=str(frozen.get("problem") or ""),
            experiment=str(metadata.get("experiment") or "Teorem Araştırması"),
            literature_query=str(metadata.get("literature_query") or ""),
            tags=list(metadata.get("tags") or []),
            created_at=str(metadata.get("created_at") or frozen.get("frozen_at") or ""),
            updated_at=updated,
            archived=bool(metadata.get("archived", False)),
            status=status,
            runtime=runtime,
            counts=self._counts(root),
            run_count=len(runs),
            total_tokens=sum(int(r.get("total_tokens", 0)) for r in runs),
            total_cost_usd=round(sum(float(r.get("total_cost_usd", 0)) for r in runs), 8),
            last_run=str(runs[0]["run_id"]) if runs else "",
        )

    def list_projects(self, *, include_archived: bool = False) -> list[ProjectInfo]:
        projects: list[ProjectInfo] = []
        if not self.root.exists():
            return projects
        for root in self.root.iterdir():
            if not root.is_dir() or root.name.startswith("."):
                continue
            if not any((root / marker).exists() for marker in ("project.json", "problem_frozen.json", "state.json", "runtime.json")):
                continue
            try:
                info = self.get(root.name)
            except Exception:
                continue
            if info.archived and not include_archived:
                continue
            projects.append(info)
        projects.sort(key=lambda x: x.updated_at or x.created_at or x.project_id, reverse=True)
        return projects

    def set_active(self, project_id: str) -> ProjectInfo:
        info = self.get(project_id)
        _write_json(self.active_path, {"project_id": info.project_id, "selected_at": _now()})
        self.touch(info.project_id)
        return info

    def active_project_id(self) -> str | None:
        raw = _read_json(self.active_path, {})
        pid = str(raw.get("project_id") or "") if isinstance(raw, dict) else ""
        if pid and self.project_root(pid).exists():
            return pid
        return None

    def active_project(self) -> ProjectInfo | None:
        pid = self.active_project_id()
        if not pid:
            return None
        try:
            return self.get(pid)
        except KeyError:
            return None

    def clear_active(self) -> None:
        self.active_path.unlink(missing_ok=True)

    def touch(self, project_id: str, **updates: Any) -> None:
        pid = _slug(project_id)
        root = self.project_root(pid)
        if not root.exists():
            return
        metadata = self._base_metadata(root)
        metadata.update(updates)
        metadata["project_id"] = pid
        metadata["updated_at"] = _now()
        _write_json(root / "project.json", metadata)

    def archive(self, project_id: str, archived: bool = True) -> None:
        self.touch(project_id, archived=bool(archived))
        if archived and self.active_project_id() == _slug(project_id):
            self.clear_active()

    def clone(self, project_id: str, *, title: str, new_project_id: str | None = None) -> ProjectInfo:
        source = self.get(project_id)
        return self.create_project(
            title=title,
            project_id=new_project_id,
            problem=source.problem,
            description=f"{source.description}\n\nKaynak proje: {source.project_id}".strip(),
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
