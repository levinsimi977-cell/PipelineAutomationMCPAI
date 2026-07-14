"""
Answer Policy Repository — in-memory storage for answer_policy objects.

Responsibility: hold a mapping of run_id -> answer_policy and let callers
register/retrieve/clear it. This module has no knowledge of JSON files,
Use Cases, LangGraph, workflow State, or prompts — it is a pure storage
component that decouples the Answer Agent from the rest of the pipeline.
"""
from __future__ import annotations


class AnswerPolicyNotFoundError(Exception):
    """Raised when get() is called for a run_id with no registered policy."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(
            f"No Answer Policy registered for run_id={run_id!r}. "
            "Did you forget to call register() before get()?"
        )


class AnswerPolicyRepository:
    """
    In-memory store of answer_policy objects, keyed by run_id.

    Responsible ONLY for storing and retrieving answer_policy dicts.
    Does not read files, does not know about Use Cases, LangGraph, or State.
    """

    def __init__(self) -> None:
        self._policies: dict[str, dict] = {}

    def register(self, run_id: str, policy: dict) -> None:
        """Store `policy` under `run_id`, overwriting any previous value."""
        self._policies[run_id] = policy

    def get(self, run_id: str) -> dict:
        """Return the answer_policy registered for `run_id`.

        Raises AnswerPolicyNotFoundError if nothing was registered for it.
        """
        try:
            return self._policies[run_id]
        except KeyError:
            raise AnswerPolicyNotFoundError(run_id) from None

    def clear(self, run_id: str) -> None:
        """Remove the answer_policy registered for `run_id`, if any."""
        self._policies.pop(run_id, None)

    def load_from_use_case(self, run_id: str, use_case: dict) -> None:
        """Extract answer_policy from an already-loaded use_case dict and
        store it for `run_id`.

        Raises ValueError if the use_case has no answer_policy — this
        method does not load files or know where use_case came from,
        it only converts an in-memory use_case into a stored policy.
        """
        policy = use_case.get("answer_policy")
        if not policy:
            raise ValueError(
                f"use_case for run_id={run_id!r} has no 'answer_policy' key. "
                "Cannot register an empty/missing Answer Policy."
            )
        self.register(run_id, policy)

_repository = AnswerPolicyRepository()


def get_answer_policy_repository() -> AnswerPolicyRepository:
    """Return the shared, module-level AnswerPolicyRepository singleton."""
    return _repository