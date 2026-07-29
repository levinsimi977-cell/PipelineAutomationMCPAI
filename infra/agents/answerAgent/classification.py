"""
classification.py — reads the status word the installation LLM writes as
the last word before it stops (SUCCESS / FAILURE / QUESTION) and returns it.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any


class Classification(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    QUESTION = "QUESTION"


_STATUS_WORDS: dict[str, Classification] = {
    "success": Classification.SUCCESS,
    "failure": Classification.FAILURE,
    "fail": Classification.FAILURE,
    "question": Classification.QUESTION,
    "הצלחה": Classification.SUCCESS,
    "כישלון": Classification.FAILURE,
    "שאלה": Classification.QUESTION,
}

# Strips wrapping punctuation/markdown (quotes, **, ., !) around the last
# word without touching the letters themselves (Hebrew letters count as
# \w in Python's Unicode-aware regex, so they're kept intact).
_WORD_WRAP_RE = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def _extract_text(state: Any) -> str:
    """Pull the LLM's message text out of state["last_agent_message"]."""
    message = state.get("last_agent_message") if isinstance(state, dict) else state
    if isinstance(message, str):
        return message
    return str(getattr(message, "content", message) or "")


def classify_llm_output(state: Any) -> Classification:
    """Read the last word of the LLM's message in `state` and map it to a Classification."""
    text = _extract_text(state).strip()
    if not text:
        raise ValueError("Empty message — no status word found.")

    last_word = _WORD_WRAP_RE.sub("", text.split()[-1]).lower()
    status = _STATUS_WORDS.get(last_word)
    if status is None:
        raise ValueError(f"Last word {last_word!r} is not a recognized status.")
    return status
