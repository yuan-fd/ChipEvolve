"""Public evidence-business facade for the Query-Agent application.

The implementation is split by replaceable concern; this module keeps the
application's import surface stable for callers and tests.
"""

from .catalog import (
    MAX_RUNS,
    artifact_selection,
    artifact_view,
    designs,
    integer,
    limit,
    run_filters,
)
from .errors import QueryError
from .planner import plan_question, structured_query
from .report import MAX_REPORT_RUNS, run_report

__all__ = (
    "MAX_REPORT_RUNS", "MAX_RUNS", "QueryError", "artifact_selection",
    "artifact_view", "designs", "integer", "limit", "plan_question",
    "run_filters", "run_report", "structured_query",
)
