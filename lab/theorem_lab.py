"""Compatibility shim for the pre-composition theorem engine.

Production code lives in :mod:`lab.theorem_engine`. Keep these names temporarily
for external scripts while avoiding a second copy of the research workflow.
"""

from lab.json_io import parse_json_object as extract_json_object
from lab.theorem_engine import IterationOutcome, TheoremResearchLab, paper_context as _paper_context

__all__ = ["TheoremResearchLab", "IterationOutcome", "extract_json_object", "_paper_context"]
