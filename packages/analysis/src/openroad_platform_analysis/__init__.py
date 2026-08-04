"""Deterministic ORFS evidence, diagnostics, layout density, and comparisons."""

from .diagnosis import diagnose
from .pipeline import analyze_run
from .reporter import build_llm_prompt, build_report

__all__ = ["analyze_run", "build_llm_prompt", "build_report", "diagnose"]
