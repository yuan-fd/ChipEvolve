"""The protected evaluation boundary.

The kernel owns the boundary; a plugin owns the domain.  This package contains
no parser for any tool's output and no vendor identifier -- G1 enforces that.
"""

from .boundary import (
    EvaluationError,
    EvaluationOutcome,
    PluginBackedEvaluator,
    resolve_evaluator,
    VERDICT_ARTIFACT_KIND,
    VERDICT_FILENAME,
)

__all__ = (
    "EvaluationError", "EvaluationOutcome", "PluginBackedEvaluator",
    "resolve_evaluator", "VERDICT_ARTIFACT_KIND", "VERDICT_FILENAME",
)
