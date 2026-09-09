"""Read-only, bounded discovery of explicitly registered directed run roots."""
from __future__ import annotations

import json
import os
import importlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 16 * 1024 * 1024
ACTIVE = {"QUEUED", "RUNNING", "FINALIZING", "STOP_REQUESTED"}
STATUS_LABELS = {
    'RUNNING': 'Çalışıyor', 'QUEUED': 'Sırada', 'FINALIZING': 'Sonuçlar hazırlanıyor',
    'COMPLETED': 'Tamamlandı', 'COMPLETED_WITH_OPEN_CLAIMS': 'Tamamlandı · açık iddialar var',
    'PARTIAL': 'Kısmi sonuç', 'TIMEOUT': 'Süre doldu', 'STOPPED': 'Durduruldu',
    'STOP_REQUESTED': 'Durdurma bekleniyor', 'BLOCKED_DEPENDENCY': 'Önceki sonuca bağlı',
    'INFRASTRUCTURE_FAILURE': 'Çalıştırma hatası', 'INPUT_INTEGRITY_FAILURE': 'Girdi bütünlüğü hatası',
    'BUDGET_EXHAUSTED': 'Bütçe sınırına ulaşıldı', 'INTERRUPTED': 'Kesildi',
    'PAUSED_ERROR': 'Hata nedeniyle duraklatıldı', 'PENDING': 'Henüz başlamadı',
}


def status_label(value: Any) -> str:
    return STATUS_LABELS.get(str(value), str(value or 'Bilinmiyor'))


def filter_runs(rows: list[dict[str, Any]], query: str = '', active_only: bool = False):
    query = query.strip().casefold()
    result = []
    for row in rows:
        state = row.get('runtime', {})
        if active_only and state.get('status') not in ACTIVE:
            continue
        searchable = ' '.join(str(x or '') for x in (
            row.get('project_id'), row.get('run_id'), state.get('task_id'), state.get('status'),
            status_label(state.get('status')),
        )).casefold()
        if query in searchable:
            result.append(row)
    return sorted(result, key=lambda r: (r.get('runtime', {}).get('status') not in ACTIVE, -r.get('modified', 0)))


def contained(root: Path, path: Path) -> Path:
    root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("Kayıt kökü dışına çıkan bağlantı reddedildi.")
    return resolved


def read_json(root: Path, path: Path) -> dict[str, Any]:
    path = contained(root, path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("İzleme dosyası 16 MB sınırını aşıyor.")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON nesnesi bekleniyor.")
    return data


def register_root(config: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("Var olan kayıt klasörünün tam yolunu girin.")
    path = path.resolve(strict=True)
    if path == Path(path.anchor) or path == Path.home():
        raise ValueError("Disk veya kullanıcı kökü yerine deney kayıt klasörünü seçin.")
    roots = load_roots(config)
    if path not in roots:
        roots.append(path)
    config.parent.mkdir(parents=True, exist_ok=True)
    temporary = config.with_suffix(".tmp")
    temporary.write_text(json.dumps([str(p) for p in roots], ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, config)
    return path


def load_roots(config: Path) -> list[Path]:
    if not config.exists():
        return []
    raw = config.read_bytes()
    if len(raw) > 64_000:
        raise ValueError("Kayıt kökü listesi çok büyük.")
    data = json.loads(raw)
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ValueError("Kayıt kökü listesi geçersiz.")
    return [Path(x) for x in data if Path(x).is_absolute()]


def discover_runs(roots: list[Path], limit: int = 1000) -> list[dict[str, Any]]:
    """Inspect only root/project/directed/run/runtime.json, never recursive home scans."""
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for project in root.iterdir():
                if not project.is_dir():
                    continue
                directed = project / "directed"
                if not directed.is_dir():
                    continue
                contained(root, directed)
                for folder in directed.iterdir():
                    if not folder.is_dir():
                        continue
                    try:
                        runtime = contained(root, folder / "runtime.json")
                        if str(runtime) in seen:
                            continue
                        seen.add(str(runtime))
                        stat = runtime.stat()
                        try:
                            data = read_json(root, runtime)
                            error = None
                        except (OSError, ValueError, UnicodeError) as exc:
                            data, error = {}, str(exc)
                        runs.append({"root": str(root.resolve()), "folder": str(runtime.parent),
                                     "project_id": project.name, "run_id": folder.name,
                                     "modified": stat.st_mtime, "runtime": data, "error": error})
                    except (OSError, ValueError):
                        continue
                    if len(runs) >= limit:
                        return sorted(runs, key=lambda x: x["modified"], reverse=True)
        except (OSError, ValueError):
            continue
    return sorted(runs, key=lambda x: x["modified"], reverse=True)


def heartbeat_warning(runtime: dict[str, Any], now: datetime | None = None) -> str | None:
    if runtime.get("status") not in ACTIVE:
        return None
    pid = runtime.get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        try:
            if not importlib.import_module("psutil").pid_exists(pid):
                return "Kayıttaki süreç artık bulunamıyor; çalışıyor etiketi eski olabilir."
        except (ImportError, OSError):
            pass
    try:
        at = datetime.fromisoformat(str(runtime.get("heartbeat_at", "")).replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        seconds = ((now or datetime.now(timezone.utc)) - at).total_seconds()
    except ValueError:
        return "Canlılık zamanı yok; çalışıyor durumu doğrulanamıyor."
    if seconds > 30:
        return f"Son canlılık kaydı {int(seconds)} saniye önce. Süreç durmuş veya bağlantı kesilmiş olabilir."
    return None


def lane_names(root: Path, folder: Path, runtime: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    contract = folder / "TASK_CONTRACT.json"
    if contract.exists():
        plan = read_json(root, contract).get("agent_plan", {})
        if isinstance(plan, dict):
            for entry in plan.get("lanes", []):
                if isinstance(entry, dict) and isinstance(entry.get("lane_id"), str):
                    names.add(entry["lane_id"])
    for field in ("active_lanes", "completed_lanes", "lanes"):
        value = runtime.get(field, {})
        if isinstance(value, (dict, list)):
            names.update(x for x in value if isinstance(x, str))
    lanes = folder / "lanes"
    if lanes.is_dir():
        contained(root, lanes)
        for lane in lanes.iterdir():
            if lane.is_dir():
                try:
                    contained(root, lane)
                    names.add(lane.name)
                except (ValueError, OSError):
                    pass
    return sorted(n for n in names if n and n not in {".", ".."} and "/" not in n and "\\" not in n)


def lane_snapshot(root: Path, folder: Path, lane: str) -> dict[str, Any]:
    if lane not in lane_names(root, folder, {}):
        raise ValueError("Çalışma kolu bulunamadı.")
    candidate = folder / "lanes" / lane
    if not candidate.exists():
        return {}
    base = contained(root, candidate)
    partial = read_json(root, base / "PARTIAL.json") if (base / "PARTIAL.json").exists() else {}
    response = read_json(root, base / "RESPONSE.json") if (base / "RESPONSE.json").exists() else {}
    if partial and response and (base / "PARTIAL.json").stat().st_mtime_ns > (base / "RESPONSE.json").stat().st_mtime_ns:
        response = {}  # A retry may have streamed since the previous attempt's response.
    result = read_json(root, base / "RESULT.json") if (base / "RESULT.json").exists() else {}
    return {"reasoning": response.get("provider_reasoning") or partial.get("reasoning", ""),
            "content": response.get("content") or partial.get("content", ""),
            "reasoning_details": response.get("reasoning_details") or partial.get("reasoning_details", []),
            "result": result, "provider_attempts": partial.get("provider_attempts", [])}
