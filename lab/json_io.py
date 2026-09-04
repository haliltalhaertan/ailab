from __future__ import annotations

import json
import re
from typing import Any


class StructuredOutputError(ValueError):
    """Raised when an LLM response cannot be converted to the required JSON object."""


class IncompleteJSONObject(dict[str, Any]):
    """A parseable prefix from a provider-truncated structured response.

    Complete descriptive fields are preserved, but load-bearing decision fields
    resolve to conservative values. The raw provider text remains in trace.jsonl,
    so this safety normalization does not erase provenance.
    """

    _SAFE_DECISIONS: dict[str, Any] = {
        "status": "OPEN",
        "decision": "REVISE",
        "verdict": "INCONCLUSIVE",
        "counterexample": "",
        "target_proposal": {},
    }

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._SAFE_DECISIONS:
            return self._SAFE_DECISIONS[key]
        return super().get(key, default)

    def __getitem__(self, key: str) -> Any:
        if key in self._SAFE_DECISIONS:
            return self._SAFE_DECISIONS[key]
        return super().__getitem__(key)


def strip_code_fence(text: str) -> str:
    raw = str(text or "").strip().lstrip("\ufeff")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json|javascript|js)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```\s*$", "", raw)
    return raw.strip()


def object_slice(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start >= 0 and end > start else text


def repair_string_escapes(text: str) -> str:
    r"""Repair common almost-JSON while preserving the actual field text.

    In particular, LLMs often emit raw LaTeX (``\Omega``) inside JSON strings.
    JSON does not allow ``\O``, so invalid in-string backslashes are escaped.
    """

    out: list[str] = []
    i = 0
    in_string = False
    hex_digits = set("0123456789abcdefABCDEF")
    simple_escapes = set('"\\/bfnrt')
    while i < len(text):
        ch = text[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue
        if ch == '"':
            out.append(ch)
            in_string = False
            i += 1
            continue
        if ch == "\\":
            if i + 1 >= len(text):
                out.append("\\\\")
                i += 1
                continue
            nxt = text[i + 1]
            if nxt in simple_escapes:
                out.extend(("\\", nxt))
                i += 2
                continue
            if nxt == "u" and i + 5 < len(text) and all(c in hex_digits for c in text[i + 2 : i + 6]):
                out.append(text[i : i + 6])
                i += 6
                continue
            out.append("\\\\")
            i += 1
            continue
        if ord(ch) < 0x20:
            controls = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}
            out.append(controls.get(ch, f"\\u{ord(ch):04x}"))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def repair_json_text(text: str) -> str:
    repaired = repair_string_escapes(text)
    return re.sub(r",\s*([}\]])", r"\1", repaired)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object or fail closed.

    This never returns ``{"raw": ...}``. Callers that require structured output
    must either repair once explicitly or pause the research step.
    """

    raw = strip_code_fence(text)
    sliced = object_slice(raw)
    candidates: list[str] = []
    for candidate in (raw, sliced, repair_json_text(sliced)):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(value, dict):
            raise StructuredOutputError("Structured response must be a JSON object.")
        return value
    detail = ""
    if isinstance(last_error, json.JSONDecodeError):
        detail = f" line={last_error.lineno} column={last_error.colno}"
    raise StructuredOutputError(f"Could not parse required JSON object.{detail}")


def parse_truncated_object_prefix(text: str) -> IncompleteJSONObject:
    """Recover only complete top-level fields already present in a cut-off object.

    The function closes the object immediately before an already-observed
    top-level comma. It never synthesizes a missing key, value or verdict. The
    result has a distinct type so evidence gates cannot mistake a parseable
    prefix for a complete structured decision.
    """

    raw = strip_code_fence(text)
    start = raw.find("{")
    if start < 0:
        raise StructuredOutputError("Truncated response has no JSON object prefix.")
    source = raw[start:]
    depth = 0
    in_string = False
    escaped = False
    boundaries: list[int] = []
    for index, ch in enumerate(source):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "{[":
            depth += 1
            continue
        if ch in "}]":
            depth = max(0, depth - 1)
            continue
        if ch == "," and depth == 1:
            boundaries.append(index)

    for boundary in reversed(boundaries):
        candidate = repair_json_text(source[:boundary].rstrip() + "}")
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value:
            return IncompleteJSONObject(value)
    raise StructuredOutputError("No complete top-level JSON fields could be recovered from truncated output.")


def repair_instruction(raw: str, *, truncated: bool = False) -> str:
    if truncated:
        return (
            "The provider ended the previous response because of a token length limit. "
            "Recover ONLY keys and values that are already completely present in the malformed prefix. "
            "Do not guess, create, complete, infer or replace any missing key, value, verdict or claim. "
            "Omit an incomplete field entirely. Return ONLY one valid JSON object containing that recovered prefix.\n\n"
            "TRUNCATED RESPONSE:\n" + str(raw)
        )
    return (
        "Your previous response was required to be one valid JSON object but could not be parsed. "
        "Repair formatting only: preserve the substantive content, keys and verdicts; do not add new claims. "
        "Return ONLY the complete valid JSON object, with JSON-standard escaping and no markdown.\n\n"
        "MALFORMED RESPONSE:\n" + str(raw)
    )
