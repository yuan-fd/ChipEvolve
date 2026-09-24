"""Errors raised at the Query-Agent request boundary."""

from __future__ import annotations


class QueryError(Exception):
    """A request that cannot be answered from the evidence boundary."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message
