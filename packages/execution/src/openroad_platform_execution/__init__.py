"""Execution-plane primitives for isolated EDA runs."""

from .adapter import AdapterExecution, ProcessAdapter
from .orfs_runner import ORFSRunner
from .process_guardian import ProcessGuardian, ProcessOutcome
from .registry import PluginRegistry

__all__ = [
    "AdapterExecution", "ProcessAdapter", "ORFSRunner", "ProcessGuardian",
    "ProcessOutcome", "PluginRegistry",
]
