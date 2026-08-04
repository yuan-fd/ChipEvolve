from __future__ import annotations

import re
from typing import Protocol

from .reporter import build_llm_prompt


class AnalysisClient(Protocol):
    def chat(self, system: str, user: str) -> str: ...


SYSTEM_PROMPT = (
    "You are an EDA physical-design analyst. Write the report in Chinese. "
    "Use only numbers present in the supplied evidence. State explicitly when "
    "a metric is unavailable, and do not turn hypotheses into facts."
)


def generate_analysis(report: dict, *, client: AnalysisClient) -> str:
    """Generate optional prose; callers own provider configuration and credentials."""
    result = client.chat(SYSTEM_PROMPT, build_llm_prompt(report))
    text = (result or "").strip() or "[LLM analysis returned no content]"
    text = re.sub(r"\*\*|\*|__|~~", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

