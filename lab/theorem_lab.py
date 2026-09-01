"""Compatibility shim for the pre-composition theorem engine.

Production workflow logic lives in :mod:`lab.theorem_engine`; integrity/security
invariants are layered by :mod:`lab.integrity_theorem_lab` and exported here too.
"""

from lab.integrity_theorem_lab import TheoremResearchLab
from lab.json_io import parse_json_object as extract_json_object
from lab.theorem_engine import IterationOutcome, paper_context as _paper_context

__all__ = ["TheoremResearchLab", "IterationOutcome", "extract_json_object", "_paper_context"]
