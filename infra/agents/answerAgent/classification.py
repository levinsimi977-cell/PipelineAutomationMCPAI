"""
classification.py — classify the installation-LLM's latest message.

Flow:
    A separate "listener" component watches the installation LLM (the one
    talking to the MCP). The moment it detects the LLM stopped, it hands us
    `state` — containing everything the LLM wrote.

    We agreed with whoever owns that LLM's prompt that it will end every
    message with exactly one status word — SUCCESS, FAILURE, or QUESTION
    (see STATUS_PROMPT_INSTRUCTION). So classification here is just:
    read the LAST WORD of that text and map it to a label.

    No free-text guessing, no regex vocabulary — the LLM's prompt is a fixed
    contract, not a language-understanding problem, which is what makes this
    reliably 100% correct: if the last word says SUCCESS, it's SUCCESS.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any


class Classification(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    QUESTION = "QUESTION"


class UnknownStatusError(Exception):
    """
    Raised when the last word of the message is not a recognized status
    keyword — e.g. the prompt instruction wasn't followed, or the message
    is empty. Never silently guessed into one of the three labels.
    """


STATUS_PROMPT_INSTRUCTION = (
    "When you finish responding, end your message with exactly one word by "
    "itself, and nothing after it: SUCCESS, FAILURE, or QUESTION."
)

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
    """
    Pull the LLM's message text out of `state["last_agent_message"]` — the
    same field answer_agent.py in this package already uses for "the
    installation LLM's latest message", so both modules read the same
    contract from the same state instead of each guessing independently.
    """
    message = state.get("last_agent_message") if isinstance(state, dict) else state
    if isinstance(message, str):
        return message
    return str(getattr(message, "content", message) or "")


def classify_llm_output(state: Any) -> Classification:
    """
    Read the last word of the LLM's message in `state` and map it to a
    Classification.

    Raises:
        UnknownStatusError: the message is empty, or its last word isn't
        one of SUCCESS/FAILURE/QUESTION (English or Hebrew).
    """
    text = _extract_text(state).strip()
    if not text:
        raise UnknownStatusError("Empty message — no status word found.")

    last_word = _WORD_WRAP_RE.sub("", text.split()[-1]).lower()
    status = _STATUS_WORDS.get(last_word)
    if status is None:
        raise UnknownStatusError(f"Last word {last_word!r} is not a recognized status.")
    return status
