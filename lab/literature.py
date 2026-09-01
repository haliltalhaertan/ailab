from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any

import httpx


class LiteratureSearchEmpty(RuntimeError):
    """Retrieval completed but returned no usable candidate records."""


@dataclass
class Paper:
    title: str
    authors: list[str]
    year: int | None
    url: str
    source: str
    doi: str | None = None
    abstract: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _norm_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _query_terms(query: str, max_terms: int = 8) -> list[str]:
    """Turn a human/LLM query into useful arXiv terms, never one giant exact phrase."""

    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", str(query or ""))
    stop = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "of", "on", "or", "the", "to", "with",
        "improve", "known", "result", "problem", "research", "prove", "show", "find",
    }
    terms: list[str] = []
    for word in words:
        value = word.strip(".-_")
        if len(value) < 2 or value.lower() in stop:
            continue
        if value.lower() not in {x.lower() for x in terms}:
            terms.append(value)
        if len(terms) >= max_terms:
            break
    return terms or ["mathematics"]


def build_arxiv_query(query: str) -> str:
    terms = _query_terms(query)
    return " AND ".join(f"all:{term}" for term in terms)


class LiteratureClient:
    """Zero-key arXiv + Crossref novelty screening.

    Empty retrieval is explicitly INCONCLUSIVE. A caller must never interpret
    zero records as evidence that a theorem/result is new.
    """

    def __init__(self, timeout_s: float = 15.0):
        self.timeout_s = timeout_s
        self.headers = {"User-Agent": "ailab-research/0.3 (literature screening)"}

    def search_arxiv(self, query: str, limit: int = 5) -> list[Paper]:
        params = {
            "search_query": build_arxiv_query(query),
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        with httpx.Client(timeout=self.timeout_s, headers=self.headers, follow_redirects=True) as client:
            response = client.get("https://export.arxiv.org/api/query", params=params)
            response.raise_for_status()
        root = ET.fromstring(response.text)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        papers: list[Paper] = []
        for entry in root.findall("a:entry", ns):
            title = _clean_text(entry.findtext("a:title", default="", namespaces=ns))
            authors = [_clean_text(a.findtext("a:name", default="", namespaces=ns)) for a in entry.findall("a:author", ns)]
            published = entry.findtext("a:published", default="", namespaces=ns)
            year = int(published[:4]) if published[:4].isdigit() else None
            url = entry.findtext("a:id", default="", namespaces=ns)
            abstract = _clean_text(entry.findtext("a:summary", default="", namespaces=ns))
            if title:
                papers.append(Paper(title, authors, year, url, "arXiv", abstract=abstract))
        return papers

    def search_crossref(self, query: str, limit: int = 5) -> list[Paper]:
        # Crossref is more tolerant of natural-language bibliographic queries than arXiv.
        compact = " ".join(_query_terms(query, max_terms=10))
        params = {"query.bibliographic": compact, "rows": limit}
        with httpx.Client(timeout=self.timeout_s, headers=self.headers, follow_redirects=True) as client:
            response = client.get("https://api.crossref.org/works", params=params)
            response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
        papers: list[Paper] = []
        for item in items:
            title = _clean_text((item.get("title") or [""])[0])
            authors = [
                " ".join(x for x in [a.get("given", ""), a.get("family", "")] if x).strip()
                for a in item.get("author", [])
            ]
            date_parts = (
                item.get("published-print", {}).get("date-parts")
                or item.get("published-online", {}).get("date-parts")
                or item.get("issued", {}).get("date-parts")
                or []
            )
            year = date_parts[0][0] if date_parts and date_parts[0] else None
            doi = item.get("DOI")
            url = item.get("URL") or (f"https://doi.org/{doi}" if doi else "")
            abstract = _clean_text(item.get("abstract")) or None
            if title:
                papers.append(Paper(title, authors, year, url, "Crossref", doi=doi, abstract=abstract))
        return papers

    def search(self, query: str, limit: int = 8) -> list[Paper]:
        each = max(2, (limit + 1) // 2)
        results: list[Paper] = []
        errors: list[str] = []
        for fn in (self.search_arxiv, self.search_crossref):
            try:
                results.extend(fn(query, each))
            except Exception as exc:
                errors.append(f"{fn.__name__}: {exc}")
        deduped: list[Paper] = []
        seen: set[str] = set()
        for paper in results:
            key = _norm_title(paper.title)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(paper)
            if len(deduped) >= limit:
                break
        if not deduped:
            if errors:
                raise RuntimeError("Literature retrieval failed: " + "; ".join(errors))
            raise LiteratureSearchEmpty(
                "Literature retrieval returned zero records. This is inconclusive and must not be used as a novelty signal."
            )
        return deduped
