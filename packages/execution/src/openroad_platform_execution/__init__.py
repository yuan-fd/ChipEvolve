"""Execution-plane primitives for isolated EDA runs."""

from .adapter import AdapterExecution, ProcessAdapter
from .orfs_runner import ORFSRunner
from .orfs_plugin import (
    ORFS_PLUGIN_ID,
    ORFS_PLUGIN_VERSION,
    build_orfs_task,
    orfs_plugin_manifest,
)
from .process_guardian import ProcessGuardian, ProcessOutcome
from .registry import PluginRegistry
from .toolchain import ToolchainCatalog, ToolchainConfig, load_toolchain

__all__ = [
    "AdapterExecution", "ProcessAdapter", "ORFSRunner", "ProcessGuardian",
    "ProcessOutcome", "PluginRegistry", "ORFS_PLUGIN_ID", "ORFS_PLUGIN_VERSION",
    "build_orfs_task", "orfs_plugin_manifest", "ToolchainCatalog",
    "ToolchainConfig", "load_toolchain",
]
