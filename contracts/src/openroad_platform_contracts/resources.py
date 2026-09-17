"""What a task asks the platform to bound.

A resource request is the caller saying "this experiment may use at most this
much".  It is deliberately **not** a manifest field: an untrusted party writing
its own ceiling is not a ceiling.  The manifest already declares what a plugin
needs from the kernel in ``RuntimeRequirements``; how much of the machine it may
consume is the caller's decision, made by the party who owns the machine.

Every field is optional, and absent means "not bounded".  A field the platform
cannot enforce is refused at submission rather than accepted and ignored -- see
``ProcessGuardian.supports_limits``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .version import ContractError, instantiate


@dataclass(frozen=True)
class ResourceRequest:
    """Aggregate bounds on one attempt's process tree.

    All three are *aggregate*: they bound the tree, not one process in it.  That
    is the shape the failure takes -- an EDA flow is usually one process that
    grows, or a driver that spawns hundreds -- and it is also why these are not
    implemented with ``setrlimit``: a per-process rlimit would enforce a
    different rule than the one written here (per-process rather than per-tree
    CPU, virtual rather than resident memory), and a limit that means something
    other than what it says is worse than no limit.
    """

    #: CPU **time**, in seconds, summed over every process in the tree.
    #: Governs: when the platform stops the attempt for using too much CPU.
    #: Does not govern: how many cores it is given, or whether it is admitted.
    cpu_seconds: float | None = None
    #: CPU **cores** held concurrently for the attempt.
    #: Governs: what the attempt reserves, and therefore whether it starts now
    #: or waits for room.
    #: Does not govern: how much CPU time the attempt may burn.  A 4-core
    #: reservation will happily consume a thousand core-seconds if it runs long
    #: enough; ``cpu_seconds`` is the field for that, and the two are separate
    #: questions that happen to share a word.
    cpu_cores: int | None = None
    #: Resident memory, in bytes, summed over every process in the tree.
    #: Governs: both the reservation and the kill threshold -- memory is the one
    #: resource where "how much am I holding" and "how much may I hold" are the
    #: same question.
    memory_bytes: int | None = None
    #: How many processes the tree may contain at once.
    #: Governs: the kill threshold.  Not reserved, because a process count
    #: cannot be set aside for a later attempt the way a core can.
    processes: int | None = None

    @property
    def declared(self) -> bool:
        """Whether anything was actually requested."""
        return any(
            value is not None
            for value in (self.cpu_seconds, self.cpu_cores, self.memory_bytes, self.processes)
        )

    def validate(self) -> None:
        if self.cpu_seconds is not None:
            if not isinstance(self.cpu_seconds, (int, float)) or isinstance(
                self.cpu_seconds, bool
            ):
                raise ContractError("cpu_seconds must be a number")
            if self.cpu_seconds <= 0:
                raise ContractError("cpu_seconds must be positive")
        if self.cpu_cores is not None:
            if not isinstance(self.cpu_cores, int) or isinstance(self.cpu_cores, bool):
                raise ContractError("cpu_cores must be an integer")
            if self.cpu_cores < 1:
                raise ContractError("cpu_cores must be at least 1")
        if self.memory_bytes is not None:
            if not isinstance(self.memory_bytes, int) or isinstance(
                self.memory_bytes, bool
            ):
                raise ContractError("memory_bytes must be an integer")
            if self.memory_bytes <= 0:
                raise ContractError("memory_bytes must be positive")
        if self.processes is not None:
            if not isinstance(self.processes, int) or isinstance(
                self.processes, bool
            ):
                raise ContractError("processes must be an integer")
            if self.processes < 1:
                raise ContractError("processes must be at least 1")

    def describe(self) -> str:
        """One line naming what was asked for, for a failure message."""
        parts = []
        if self.cpu_seconds is not None:
            parts.append(f"{self.cpu_seconds:g}s CPU")
        if self.cpu_cores is not None:
            parts.append(f"{self.cpu_cores} CPU cores")
        if self.memory_bytes is not None:
            parts.append(f"{self.memory_bytes} bytes resident")
        if self.processes is not None:
            parts.append(f"{self.processes} processes")
        return ", ".join(parts) if parts else "no limits"

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "cpu_seconds": self.cpu_seconds,
            "cpu_cores": self.cpu_cores,
            "memory_bytes": self.memory_bytes,
            "processes": self.processes,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ResourceRequest:
        allowed = {"cpu_seconds", "cpu_cores", "memory_bytes", "processes"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ContractError(
                f"unknown ResourceRequest fields: {', '.join(unknown)}"
            )
        result = instantiate(cls, dict(payload))
        result.validate()
        return result


__all__ = ("ResourceRequest",)
