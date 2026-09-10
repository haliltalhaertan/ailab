from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.integrity import EvidenceIntegrityError, EvidenceSigner


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SIGNATURE_FIELD = "_evidence_signature"
KEY_MODE_FIELD = "_evidence_key_mode"


class StepStore:
    """SQLite-backed durable cache/partial/snapshot store.

    Every record the store serves is HMAC-sealed: completed step payloads,
    soft-resume partials and frozen iteration snapshots. A record whose seal does
    not verify is never served as trusted data, so a direct SQLite edit cannot
    feed forged evidence, forged prompt context or a forged freeze record back
    into a run. The default project-local key is protection against
    accidental/manual edits; an external ``LAB_EVIDENCE_HMAC_KEY`` is required if
    the key must not live beside data.

    Read semantics differ per record kind, deliberately, and always fail closed:

    * step cache -> an unverified row is a cache miss, so the step is recomputed
    * partial -> an unverified row is dropped, so the step restarts from scratch
    * iteration snapshot -> an unverified row raises ``EvidenceIntegrityError``;
      a freeze record is a correctness anchor and must not be silently
      re-derived from the current ledger

    The ``steps`` table also keeps ``status``/``fingerprint`` columns for
    indexing, but every trusted read derives those values from the sealed
    payload, so editing a column changes nothing the store reports.

    Rows written before sealing existed (or migrated from the legacy JSON cache)
    carry no signature. They are NOT sealed automatically: re-sealing them with a
    fresh key would turn anything planted before the upgrade into "verified"
    evidence. Such rows stay untrusted until an operator sets
    ``LAB_ADOPT_UNSEALED_CACHE=1`` once, which is a one-time migration recorded
    in ``meta`` and reported by :meth:`counts` and :meth:`adoption_record`.

    Adoption covers only rows carrying no signature at all. A row whose signature
    is present but does not verify has either been edited or was written under a
    different key; it is never adopted, so the escape hatch cannot be used to
    bless a tampered row or to blanket re-key existing evidence.
    """

    STEP_SEAL_KIND = "step_cache:v1"
    PARTIAL_SEAL_KIND = "step_partial:v1"
    SNAPSHOT_SEAL_KIND = "iteration_snapshot:v1"
    ADOPT_ENV_NAME = "LAB_ADOPT_UNSEALED_CACHE"
    ADOPTION_META_KEY = "unsealed_rows_adopted_v1"

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "research_steps.sqlite3"
        self.signer = EvidenceSigner(self.root)
        self._init_db()
        self._migrate_legacy_json()
        self._adopt_unsealed_rows_once()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _init_db(self) -> None:
        with closing(self._connect()) as con, con:
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
        """Import the pre-SQLite JSON cache without sealing it.

        The imported rows are unverified by construction, so they arrive
        untrusted and stay untrusted until an operator adopts them explicitly.
        """
        with closing(self._connect()) as con, con:
            done = con.execute("SELECT value FROM meta WHERE key='legacy_json_migrated'").fetchone()
            if done:
                return
            imported = {"steps": 0, "partials": 0}
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
                        cursor = con.execute(
                            "INSERT OR IGNORE INTO steps(step_key,status,fingerprint,payload_json,updated_at) VALUES(?,?,?,?,?)",
                            (
                                str(key),
                                str(value.get("status") or "COMPLETE"),
                                value.get("fingerprint"),
                                json.dumps(value, ensure_ascii=False),
                                str(value.get("completed_at") or _now()),
                            ),
                        )
                        imported["steps"] += int(cursor.rowcount or 0)
            if partial_path.exists():
                try:
                    partials = json.loads(partial_path.read_text(encoding="utf-8"))
                except Exception:
                    partials = {}
                if isinstance(partials, dict):
                    for key, value in partials.items():
                        if not isinstance(value, dict):
                            continue
                        cursor = con.execute(
                            "INSERT OR IGNORE INTO partials(step_key,fingerprint,payload_json,updated_at) VALUES(?,?,?,?)",
                            (
                                str(key),
                                value.get("fingerprint"),
                                json.dumps(value, ensure_ascii=False),
                                str(value.get("updated_at") or _now()),
                            ),
                        )
                        imported["partials"] += int(cursor.rowcount or 0)
            con.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES('legacy_json_migrated',?)",
                (json.dumps({"at": _now(), "unsealed": imported}, ensure_ascii=False),),
            )

    # --- mühür ---

    @staticmethod
    def _strip_seal(payload: dict[str, Any]) -> dict[str, Any]:
        clean = dict(payload)
        clean.pop(SIGNATURE_FIELD, None)
        clean.pop(KEY_MODE_FIELD, None)
        return clean

    def _keyed_signature_payload(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Signature body for the key-addressed tables.

        Steps and partials share this shape; the seal *kind* keeps the two
        domains apart, so a step payload cannot be replayed as a partial. The
        shape is fixed: existing sealed step caches depend on it byte for byte.
        """
        return {"step_key": key, "payload": self._strip_seal(payload)}

    def _snapshot_signature_payload(
        self,
        iteration: int,
        ledger_revision: str,
        ledger_context: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "iteration": int(iteration),
            "ledger_revision": str(ledger_revision),
            "ledger_context": str(ledger_context),
            "payload": self._strip_seal(payload),
        }

    @staticmethod
    def _has_signature(payload: dict[str, Any]) -> bool:
        return bool(str(payload.get(SIGNATURE_FIELD) or ""))

    def _seal(self, kind: str, body: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        sealed = self._strip_seal(payload)
        sealed[SIGNATURE_FIELD] = self.signer.sign(kind, body)
        sealed[KEY_MODE_FIELD] = self.signer.mode
        return sealed

    def _valid(self, kind: str, body: dict[str, Any], payload: dict[str, Any]) -> bool:
        return self.signer.verify(kind, body, str(payload.get(SIGNATURE_FIELD) or ""))

    def _seal_step(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean = self._strip_seal(payload)
        return self._seal(self.STEP_SEAL_KIND, self._keyed_signature_payload(key, clean), clean)

    def _step_valid(self, key: str, payload: dict[str, Any]) -> bool:
        return self._valid(self.STEP_SEAL_KIND, self._keyed_signature_payload(key, payload), payload)

    def _seal_partial(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean = self._strip_seal(payload)
        return self._seal(self.PARTIAL_SEAL_KIND, self._keyed_signature_payload(key, clean), clean)

    def _partial_valid(self, key: str, payload: dict[str, Any]) -> bool:
        return self._valid(self.PARTIAL_SEAL_KIND, self._keyed_signature_payload(key, payload), payload)

    def _adopt_unsealed_rows_once(self) -> None:
        """Seal pre-seal rows only when an operator explicitly takes them over.

        Only rows with no signature qualify. A broken signature means the row was
        edited or written under another key, and neither is adoptable.
        """
        if str(os.environ.get(self.ADOPT_ENV_NAME) or "").strip() != "1":
            return
        with closing(self._connect()) as con, con:
            if con.execute("SELECT value FROM meta WHERE key=?", (self.ADOPTION_META_KEY,)).fetchone():
                return
            adopted = {"steps": 0, "partials": 0, "iteration_snapshots": 0}
            for row in con.execute("SELECT step_key,payload_json FROM steps").fetchall():
                payload = self._decode(row)
                if payload is None or self._has_signature(payload):
                    continue
                sealed = self._seal_step(str(row["step_key"]), payload)
                con.execute(
                    "UPDATE steps SET status=?,fingerprint=?,payload_json=? WHERE step_key=?",
                    (
                        str(sealed.get("status") or "COMPLETE"),
                        sealed.get("fingerprint"),
                        json.dumps(sealed, ensure_ascii=False),
                        str(row["step_key"]),
                    ),
                )
                adopted["steps"] += 1
            for row in con.execute("SELECT step_key,payload_json FROM partials").fetchall():
                payload = self._decode(row)
                if payload is None or self._has_signature(payload):
                    continue
                sealed = self._seal_partial(str(row["step_key"]), payload)
                con.execute(
                    "UPDATE partials SET fingerprint=?,payload_json=? WHERE step_key=?",
                    (sealed.get("fingerprint"), json.dumps(sealed, ensure_ascii=False), str(row["step_key"])),
                )
                adopted["partials"] += 1
            snapshot_rows = con.execute(
                "SELECT iteration,ledger_revision,ledger_context,payload_json FROM iteration_snapshots"
            ).fetchall()
            for row in snapshot_rows:
                payload = self._decode(row)
                if payload is None or self._has_signature(payload):
                    continue
                body = self._snapshot_signature_payload(
                    int(row["iteration"]), str(row["ledger_revision"]), str(row["ledger_context"]), payload
                )
                sealed = self._seal(self.SNAPSHOT_SEAL_KIND, body, payload)
                con.execute(
                    "UPDATE iteration_snapshots SET payload_json=? WHERE iteration=?",
                    (json.dumps(sealed, ensure_ascii=False), int(row["iteration"])),
                )
                adopted["iteration_snapshots"] += 1
            con.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                (
                    self.ADOPTION_META_KEY,
                    json.dumps({"at": _now(), "key_mode": self.signer.mode, **adopted}, ensure_ascii=False),
                ),
            )

    def adoption_record(self) -> dict[str, Any]:
        """Return the recorded one-time adoption of unsealed rows, if it happened."""
        with closing(self._connect()) as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (self.ADOPTION_META_KEY,)).fetchone()
        if row is None:
            return {}
        try:
            record = json.loads(row["value"])
        except Exception:
            return {}
        return record if isinstance(record, dict) else {}

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        try:
            value = json.loads(row["payload_json"])
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    # --- step cache: doğrulanamayan satır cache miss'tir ---

    def get_step(self, key: str) -> dict[str, Any] | None:
        with closing(self._connect()) as con:
            payload = self._decode(
                con.execute("SELECT payload_json FROM steps WHERE step_key=?", (key,)).fetchone()
            )
        if payload is None or not self._step_valid(key, payload):
            return None
        return payload

    def put_step(self, key: str, value: dict[str, Any]) -> None:
        payload = self._seal_step(key, dict(value))
        with closing(self._connect()) as con, con:
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
        with closing(self._connect()) as con, con:
            con.execute("DELETE FROM steps WHERE step_key=?", (key,))

    # --- partial: doğrulanamayan yarım çıktı prompt'a girmez ---

    def get_partial(self, key: str) -> dict[str, Any] | None:
        with closing(self._connect()) as con:
            payload = self._decode(
                con.execute("SELECT payload_json FROM partials WHERE step_key=?", (key,)).fetchone()
            )
        if payload is None or not self._partial_valid(key, payload):
            return None
        return self._strip_seal(payload)

    def put_partial(self, key: str, value: dict[str, Any]) -> None:
        payload = self._seal_partial(key, dict(value))
        with closing(self._connect()) as con, con:
            con.execute(
                "INSERT OR REPLACE INTO partials(step_key,fingerprint,payload_json,updated_at) VALUES(?,?,?,?)",
                (key, payload.get("fingerprint"), json.dumps(payload, ensure_ascii=False), _now()),
            )

    def clear_partial(self, key: str) -> None:
        with closing(self._connect()) as con, con:
            con.execute("DELETE FROM partials WHERE step_key=?", (key,))

    # --- raporlama: sayılar mühürlü payload'dan türetilir ---

    def counts(self) -> dict[str, int]:
        """Report what the store will actually serve, not what the columns claim."""
        with closing(self._connect()) as con:
            step_rows = con.execute("SELECT step_key,payload_json FROM steps").fetchall()
            partial_rows = con.execute("SELECT step_key,payload_json FROM partials").fetchall()
        complete_steps = 0
        unsealed_steps = 0
        for row in step_rows:
            payload = self._decode(row)
            if payload is None or not self._step_valid(str(row["step_key"]), payload):
                unsealed_steps += 1
                continue
            if str(payload.get("status") or "COMPLETE") == "COMPLETE":
                complete_steps += 1
        partials = 0
        unsealed_partials = 0
        for row in partial_rows:
            payload = self._decode(row)
            if payload is None or not self._partial_valid(str(row["step_key"]), payload):
                unsealed_partials += 1
                continue
            partials += 1
        record = self.adoption_record()
        adopted = sum(
            int(record.get(name) or 0) for name in ("steps", "partials", "iteration_snapshots")
        )
        return {
            "complete_steps": complete_steps,
            "partials": partials,
            "unsealed_steps": unsealed_steps,
            "unsealed_partials": unsealed_partials,
            "adopted_unsealed_rows": adopted,
        }

    def list_steps(self, limit: int = 500) -> list[dict[str, Any]]:
        with closing(self._connect()) as con:
            rows = con.execute(
                "SELECT step_key,status,fingerprint,payload_json,updated_at FROM steps ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        output = []
        for row in rows:
            payload = self._decode(row) or {}
            sealed = self._step_valid(str(row["step_key"]), payload)
            output.append(
                {
                    "step_key": row["step_key"],
                    # Mühür geçerliyse sütun değil payload yetkilidir; geçersizse iki taraf da güvenilmezdir.
                    "status": str(payload.get("status") or "COMPLETE") if sealed else row["status"],
                    "fingerprint": payload.get("fingerprint") if sealed else row["fingerprint"],
                    "model": payload.get("model") if sealed else None,
                    "sealed": sealed,
                    "updated_at": row["updated_at"],
                }
            )
        return output

    def list_partials(self, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self._connect()) as con:
            rows = con.execute(
                "SELECT step_key,payload_json,updated_at FROM partials ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        result = []
        for row in rows:
            payload = self._decode(row) or {}
            sealed = self._partial_valid(str(row["step_key"]), payload)
            # Payload içeriği satır kimliğini ya da mühür raporunu gölgeleyemez.
            result.append(
                {
                    **self._strip_seal(payload),
                    "step_key": row["step_key"],
                    "updated_at": row["updated_at"],
                    "sealed": sealed,
                }
            )
        return result

    # --- iteration snapshot: dondurulmuş kayıt fail-closed okunur ---

    def get_iteration_snapshot(self, iteration: int) -> dict[str, Any] | None:
        with closing(self._connect()) as con:
            row = con.execute(
                "SELECT ledger_revision,ledger_context,payload_json,updated_at FROM iteration_snapshots WHERE iteration=?",
                (int(iteration),),
            ).fetchone()
        if row is None:
            return None
        payload = self._decode(row)
        body = self._snapshot_signature_payload(
            int(iteration), str(row["ledger_revision"]), str(row["ledger_context"]), payload or {}
        )
        if payload is None or not self._valid(self.SNAPSHOT_SEAL_KIND, body, payload):
            # Mühürsüz (mühür öncesi) satır ile mühürü uyuşmayan satır ayrı teşhistir: ilkinin
            # devralınacak bir yolu var, ikincisi kurcalanmıştır ve devralınmamalıdır.
            unsealed = payload is None or not str(payload.get(SIGNATURE_FIELD) or "")
            remedy = (
                f"Mühür öncesinden gelen bir projeyi devralıyorsan {self.ADOPT_ENV_NAME}=1 ile bir kez devral."
                if unsealed
                else "Mühür uyuşmuyor; bu kayıt devralınmamalıdır."
            )
            diagnosis = "mühürsüz kayıt" if unsealed else "mühür uyuşmazlığı"
            raise EvidenceIntegrityError(
                f"Iteration {iteration} snapshot mührü doğrulanamadı ({diagnosis}): dondurulmuş ledger "
                f"context/next task kaydı güvenilir değil, sessizce yeniden türetilmedi. {remedy}"
            )
        # Dondurulmuş alanlar payload tarafından gölgelenemez; update_iteration_payload da
        # aynı dört alanı reserved sayar, iki taraf böylece tek bir kaynağa bakar.
        return {
            **self._strip_seal(payload),
            "iteration": int(iteration),
            "ledger_revision": row["ledger_revision"],
            "ledger_context": row["ledger_context"],
            "updated_at": row["updated_at"],
        }

    def put_iteration_snapshot(
        self,
        iteration: int,
        *,
        ledger_revision: str,
        ledger_context: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        clean = self._strip_seal(dict(payload or {}))
        sealed = self._seal(
            self.SNAPSHOT_SEAL_KIND,
            self._snapshot_signature_payload(iteration, ledger_revision, ledger_context, clean),
            clean,
        )
        with closing(self._connect()) as con, con:
            con.execute(
                "INSERT OR REPLACE INTO iteration_snapshots(iteration,ledger_revision,ledger_context,payload_json,updated_at) VALUES(?,?,?,?,?)",
                (
                    int(iteration),
                    ledger_revision,
                    ledger_context,
                    json.dumps(sealed, ensure_ascii=False),
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
