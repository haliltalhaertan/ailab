from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.integrity import EvidenceSigner


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StepStore:
    """SQLite-backed durable cache/partial/snapshot store.

    Completed step payloads are HMAC-sealed. This detects direct SQLite payload
    edits before cached LLM/tool evidence can be reused. The default project-local
    key is protection against accidental/manual edits; an external
    ``LAB_EVIDENCE_HMAC_KEY`` is required if the key must not live beside data.
    """

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "research_steps.sqlite3"
        self.signer = EvidenceSigner(self.root)
        self._init_db()
        self._migrate_legacy_json()
        self._seal_existing_steps_once()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS steps (
                    step_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    fingerprint TEXT,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS partials (
                    step_key TEXT PRIMARY KEY,
                    fingerprint TEXT,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS iteration_snapshots (
                    iteration INTEGER PRIMARY KEY,
                    ledger_revision TEXT NOT NULL,
                    ledger_context TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def _migrate_legacy_json(self) -> None:
        with self._connect() as con:
            done = con.execute("SELECT value FROM meta WHERE key='legacy_json_migrated'").fetchone()
            if done:
                return
            cache_path = self.root / "step_cache.json"
            partial_path = self.root / "partial_steps.json"
            if cache_path.exists():
                try:
                    cache = json.loads(cache_path.read_text(encoding="utf-8"))
                except Exception:
                    cache = {}
                if isinstance(cache, dict):
                    for key, value in cache.items():
                        if not isinstance(value, dict):
                            continue
                        con.execute(
                            "INSERT OR IGNORE INTO steps(step_key,status,fingerprint,payload_json,updated_at) VALUES(?,?,?,?,?)",
                            (
                                str(key),
                                str(value.get("status") or "COMPLETE"),
                                value.get("fingerprint"),
                                json.dumps(value, ensure_ascii=False),
                                str(value.get("completed_at") or _now()),
                            ),
                        )
            if partial_path.exists():
                try:
                    partials = json.loads(partial_path.read_text(encoding="utf-8"))
                except Exception:
                    partials = {}
                if isinstance(partials, dict):
                    for key, value in partials.items():
                        if not isinstance(value, dict):
                            continue
                        con.execute(
                            "INSERT OR IGNORE INTO partials(step_key,fingerprint,payload_json,updated_at) VALUES(?,?,?,?)",
                            (
                                str(key),
                                value.get("fingerprint"),
                                json.dumps(value, ensure_ascii=False),
                                str(value.get("updated_at") or _now()),
                            ),
                        )
            con.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES('legacy_json_migrated',?)",
                (_now(),),
            )

    def _step_signature_payload(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean = dict(payload)
        clean.pop("_evidence_signature", None)
        clean.pop("_evidence_key_mode", None)
        return {"step_key": key, "payload": clean}

    def _seal_step(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean = dict(payload)
        clean.pop("_evidence_signature", None)
        clean.pop("_evidence_key_mode", None)
        clean["_evidence_signature"] = self.signer.sign(
            "step_cache:v1", self._step_signature_payload(key, clean)
        )
        clean["_evidence_key_mode"] = self.signer.mode
        return clean

    def _step_valid(self, key: str, payload: dict[str, Any]) -> bool:
        signature = str(payload.get("_evidence_signature") or "")
        return self.signer.verify(
            "step_cache:v1",
            self._step_signature_payload(key, payload),
            signature,
        )

    def _seal_existing_steps_once(self) -> None:
        with self._connect() as con:
            done = con.execute("SELECT value FROM meta WHERE key='signed_cache_migrated_v1'").fetchone()
            if done:
                return
            rows = con.execute("SELECT step_key,payload_json FROM steps").fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                sealed = self._seal_step(str(row["step_key"]), payload)
                con.execute(
                    "UPDATE steps SET payload_json=?, updated_at=? WHERE step_key=?",
                    (json.dumps(sealed, ensure_ascii=False), _now(), str(row["step_key"])),
                )
            con.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES('signed_cache_migrated_v1',?)",
                (_now(),),
            )

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        try:
            value = json.loads(row["payload_json"])
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    def get_step(self, key: str) -> dict[str, Any] | None:
        with self._connect() as con:
            payload = self._decode(
                con.execute("SELECT payload_json FROM steps WHERE step_key=?", (key,)).fetchone()
            )
        if payload is None or not self._step_valid(key, payload):
            return None
        return payload

    def put_step(self, key: str, value: dict[str, Any]) -> None:
        payload = self._seal_step(key, dict(value))
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO steps(step_key,status,fingerprint,payload_json,updated_at) VALUES(?,?,?,?,?)",
                (
                    key,
                    str(payload.get("status") or "COMPLETE"),
                    payload.get("fingerprint"),
                    json.dumps(payload, ensure_ascii=False),
                    _now(),
                ),
            )

    def delete_step(self, key: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM steps WHERE step_key=?", (key,))

    def get_partial(self, key: str) -> dict[str, Any] | None:
        with self._connect() as con:
            return self._decode(
                con.execute("SELECT payload_json FROM partials WHERE step_key=?", (key,)).fetchone()
            )

    def put_partial(self, key: str, value: dict[str, Any]) -> None:
        payload = dict(value)
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO partials(step_key,fingerprint,payload_json,updated_at) VALUES(?,?,?,?)",
                (key, payload.get("fingerprint"), json.dumps(payload, ensure_ascii=False), _now()),
            )

    def clear_partial(self, key: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM partials WHERE step_key=?", (key,))

    def counts(self) -> dict[str, int]:
        with self._connect() as con:
            steps = int(con.execute("SELECT COUNT(*) FROM steps WHERE status='COMPLETE'").fetchone()[0])
            partials = int(con.execute("SELECT COUNT(*) FROM partials").fetchone()[0])
        return {"complete_steps": steps, "partials": partials}

    def list_steps(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT step_key,status,fingerprint,payload_json,updated_at FROM steps ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        output = []
        for row in rows:
            payload = self._decode(row) or {}
            output.append(
                {
                    "step_key": row["step_key"],
                    "status": row["status"],
                    "fingerprint": row["fingerprint"],
                    "model": payload.get("model"),
                    "sealed": self._step_valid(str(row["step_key"]), payload),
                    "updated_at": row["updated_at"],
                }
            )
        return output

    def list_partials(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT step_key,payload_json,updated_at FROM partials ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        result = []
        for row in rows:
            payload = self._decode(row) or {}
            result.append({"step_key": row["step_key"], "updated_at": row["updated_at"], **payload})
        return result

    def get_iteration_snapshot(self, iteration: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT ledger_revision,ledger_context,payload_json,updated_at FROM iteration_snapshots WHERE iteration=?",
                (int(iteration),),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except Exception:
            payload = {}
        return {
            "iteration": int(iteration),
            "ledger_revision": row["ledger_revision"],
            "ledger_context": row["ledger_context"],
            "updated_at": row["updated_at"],
            **(payload if isinstance(payload, dict) else {}),
        }

    def put_iteration_snapshot(
        self,
        iteration: int,
        *,
        ledger_revision: str,
        ledger_context: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO iteration_snapshots(iteration,ledger_revision,ledger_context,payload_json,updated_at) VALUES(?,?,?,?,?)",
                (
                    int(iteration),
                    ledger_revision,
                    ledger_context,
                    json.dumps(payload or {}, ensure_ascii=False),
                    _now(),
                ),
            )

    def update_iteration_payload(self, iteration: int, **updates: Any) -> dict[str, Any]:
        snapshot = self.get_iteration_snapshot(iteration)
        if snapshot is None:
            raise KeyError(f"iteration snapshot missing: {iteration}")
        reserved = {"iteration", "ledger_revision", "ledger_context", "updated_at"}
        payload = {k: v for k, v in snapshot.items() if k not in reserved}
        payload.update(updates)
        self.put_iteration_snapshot(
            iteration,
            ledger_revision=str(snapshot["ledger_revision"]),
            ledger_context=str(snapshot["ledger_context"]),
            payload=payload,
        )
        return self.get_iteration_snapshot(iteration) or {}
