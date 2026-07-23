"""
Comprehensive pytest suite for ``answer_agent.py``.

Design goals
------------
* Pure ``pytest`` (fixtures + ``monkeypatch``; no unittest).
* Zero external services: OpenAI, the filesystem scan and ``subprocess.run``
  are all mocked, and no real ``OPENAI_API_KEY`` is ever required.
* Tests are grouped by the function under test and kept independent so they
  can run in any order.

Import bootstrap
----------------
``answer_agent`` does ``from prompts.answer_templates import ...`` (which
pulls in ``langchain_core``). Before importing the module we therefore:

1. put the ``answerAgent`` package dir on ``sys.path`` so ``prompts`` resolves,
2. stub ``langchain_core.prompts.PromptTemplate`` *only if* it is not installed.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Import bootstrap (runs once, at collection time, before importing the SUT)
# ---------------------------------------------------------------------------

# 1) Make ``answer_agent`` and its ``prompts`` package importable.
_ANSWER_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "infra", "agents", "answerAgent")
)
if _ANSWER_AGENT_DIR not in sys.path:
    sys.path.insert(0, _ANSWER_AGENT_DIR)

# 2) Stub langchain_core.prompts.PromptTemplate if the dependency is missing so
#    the suite runs with no third-party packages installed.
try:  # pragma: no cover - exercised only when langchain_core is absent
    import langchain_core.prompts  # noqa: F401
except Exception:  # ImportError or any partial-install error
    _lc = types.ModuleType("langchain_core")
    _lc_prompts = types.ModuleType("langchain_core.prompts")

    class _StubPromptTemplate:
        """Minimal stand-in supporting ``from_template`` + ``format``."""

        def __init__(self, template: str) -> None:
            self.template = template

        @classmethod
        def from_template(cls, template: str) -> "_StubPromptTemplate":
            return cls(template)

        def format(self, **kwargs: object) -> str:
            return self.template.format(**kwargs)

    _lc_prompts.PromptTemplate = _StubPromptTemplate
    _lc.prompts = _lc_prompts
    sys.modules["langchain_core"] = _lc
    sys.modules["langchain_core.prompts"] = _lc_prompts

import answer_agent  # noqa: E402  (import after bootstrap on purpose)
from answer_agent import (  # noqa: E402
    MAX_QUESTION_ROUNDS,
    UnansweredQuestionError,
    answer_question,
    answer_question_node,
    build_prompt_with_answers,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_config(monkeypatch):
    """Reset the module-level config constants before every test so tests
    stay independent.

    ``OPENAI_API_KEY`` defaults to empty -> ``_llm_answer`` short-circuits to
    ``None`` and no LLM is ever contacted unless a test opts in via
    ``with_api_key``.
    """
    monkeypatch.setattr(answer_agent, "OPENAI_API_KEY", "", raising=False)
    monkeypatch.setattr(answer_agent, "DEV_KEY", "", raising=False)
    monkeypatch.setattr(answer_agent, "APP_ID", "test-app-id", raising=False)
    monkeypatch.setattr(answer_agent, "OPENAI_MODEL", "gpt-test", raising=False)


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    """Neutralise all filesystem + subprocess access.

    ``_scan_environment_facts`` is the single boundary that walks the disk and
    calls ``subprocess.run``.  We replace it with a pure function returning a
    controllable dict, so no real IO ever happens.  Tests that care about
    environment facts mutate the returned dict.
    """
    facts: dict[str, str] = {}

    def _fake_scan(app_path: str = "", platform: str = "") -> dict[str, str]:
        return dict(facts)

    monkeypatch.setattr(answer_agent, "_scan_environment_facts", _fake_scan)
    return facts


@pytest.fixture
def with_api_key(monkeypatch):
    """Opt-in helper: pretend an OpenAI key is configured."""
    monkeypatch.setattr(answer_agent, "OPENAI_API_KEY", "fake-key-123")


TEST_RUN_ID = "test-run-id"


@pytest.fixture(autouse=True)
def registered_answer_policy():
    """Register (and clean up) an answer_policy for ``TEST_RUN_ID``.

    Any test that exercises the *real* ``_llm_answer`` body (i.e. doesn't
    monkeypatch it away entirely) goes through ``_format_test_decisions``,
    which does ``get_answer_policy_repository().get(state["run_id"])`` with
    no fallback -- it raises if nothing was registered for that run_id, and
    a plain ``state["run_id"]`` lookup raises KeyError if the key is absent
    altogether. Tests that need the real LLM path to run must include
    ``"run_id": TEST_RUN_ID`` in their state dict.
    """
    from infra.agents.answerAgent.answer_policy_repository import (
        get_answer_policy_repository,
    )

    repo = get_answer_policy_repository()
    repo.register(TEST_RUN_ID, {})
    yield
    repo.clear(TEST_RUN_ID)


def _policy_state(**policy) -> dict:
    """Build a minimal state whose ``answer_policy`` holds the given fields."""
    return {"answer_policy": dict(policy)}


class _FakeResponse:
    """Mimics a LangChain message object with a ``.content`` attribute."""

    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """Records the prompt and returns a canned response (or raises)."""

    def __init__(self, content="LLM answer", error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls: list[str] = []

    def invoke(self, prompt):
        self.calls.append(prompt)
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.content)


# ===========================================================================
# _deterministic_answer()
# ===========================================================================
#
# NOTE: classify_question() (regex-based topic routing) was removed from
# answer_agent.py in 41270e5 "Improve answer agent responses" in favor of the
# LLM-driven flow below -- its TestClassifyQuestion suite was removed here
# along with the now-nonexistent import; it had been silently failing this
# whole module's collection (ImportError) since that commit.


# ===========================================================================
# answer_question()
# ===========================================================================


class TestAnswerQuestion:
    def test_no_api_key_raises(self):
        # No OPENAI_API_KEY -> _llm_answer short-circuits to None -> raise.
        # There is no rule-based fallback anymore (see NOTE above).
        with pytest.raises(UnansweredQuestionError):
            answer_question({"platform": "ios"}, "What platform is this?")

    def test_llm_path(self, monkeypatch, with_api_key):
        # API key set -> the LLM answer is returned verbatim.
        fake_llm = _FakeLLM(content="Do the next integration step.")
        monkeypatch.setattr(answer_agent, "_llm", lambda: fake_llm)

        answer = answer_question(
            {"platform": "android", "run_id": TEST_RUN_ID},
            "What should I do next to finish integration?",
        )
        assert answer == "Do the next integration step."
        assert fake_llm.calls, "the mocked LLM should have been invoked"

    def test_raises_when_llm_answer_returns_none(self, monkeypatch, with_api_key):
        # API key set, but the LLM yields nothing -> no fallback exists, so
        # answer_question must raise rather than fabricate an answer.
        monkeypatch.setattr(answer_agent, "_llm_answer", lambda *a, **k: None)

        with pytest.raises(UnansweredQuestionError):
            answer_question(_policy_state(app_launched=True), "Did you launch the app?")

    def test_llm_failure_raises(self, monkeypatch, with_api_key):
        # The real _llm_answer runs, its invoke() raises, the exception is
        # swallowed internally (returns None) -- with no deterministic
        # fallback left, answer_question must raise.
        failing = _FakeLLM(error=RuntimeError("network down"))
        monkeypatch.setattr(answer_agent, "_llm", lambda: failing)

        state = {**_policy_state(app_launched=False), "run_id": TEST_RUN_ID}
        with pytest.raises(UnansweredQuestionError):
            answer_question(state, "Did you launch the app?")
        assert failing.calls, "the LLM should have been attempted before failing"

    def test_raises_when_no_answer_exists(self):
        # No key, empty state → nothing can answer it; the original question
        # text is preserved on the exception for the caller to log/report.
        with pytest.raises(UnansweredQuestionError) as exc_info:
            answer_question({}, "Tell me an unrelated story.")
        assert exc_info.value.question == "Tell me an unrelated story."

    def test_llm_path_raises_when_all_paths_empty(self, monkeypatch, with_api_key):
        # LLM primary returns nothing AND deterministic chain is empty → raise.
        monkeypatch.setattr(answer_agent, "_llm_answer", lambda *a, **k: None)
        with pytest.raises(UnansweredQuestionError):
            answer_question({}, "How does this whole system work?")


# ===========================================================================
# answer_question_node()
# ===========================================================================


class TestAnswerQuestionNode:
    def test_no_incoming_question_skips(self):
        result = answer_question_node({})
        logs = result["nodes_log"]
        assert logs[-1]["node"] == "answer_question"
        assert logs[-1]["status"] == "SKIP"
        # Nothing else should be mutated on a skip.
        assert "installation_answers" not in result
        assert "question_rounds" not in result

    def test_successful_answer(self, monkeypatch):
        monkeypatch.setattr(answer_agent, "answer_question", lambda state, q: "mocked answer")
        result = answer_question_node({"incoming_question": "How?"})

        assert result["question_rounds"] == 1
        assert result["installation_answers"] == [
            {"question": "How?", "answer": "mocked answer"}
        ]
        assert result["nodes_log"][-1]["status"] == "SUCCESS"

    def test_installation_answers_are_appended(self, monkeypatch):
        monkeypatch.setattr(answer_agent, "answer_question", lambda state, q: "A2")
        state = {
            "incoming_question": "Q2",
            "installation_answers": [{"question": "Q1", "answer": "A1"}],
        }
        result = answer_question_node(state)
        assert result["installation_answers"] == [
            {"question": "Q1", "answer": "A1"},
            {"question": "Q2", "answer": "A2"},
        ]

    def test_question_rounds_incremented(self, monkeypatch):
        monkeypatch.setattr(answer_agent, "answer_question", lambda state, q: "ok")
        result = answer_question_node({"incoming_question": "Q", "question_rounds": 3})
        assert result["question_rounds"] == 4

    def test_nodes_log_are_preserved(self, monkeypatch):
        monkeypatch.setattr(answer_agent, "answer_question", lambda state, q: "ok")
        prior = [{"node": "other", "status": "SUCCESS", "message": "prev"}]
        result = answer_question_node({"incoming_question": "Q", "nodes_log": prior})
        assert result["nodes_log"][0] == prior[0]
        assert result["nodes_log"][-1]["node"] == "answer_question"

    def test_maximum_question_limit_returns_fail(self, monkeypatch):
        # Guard: answer_question must NOT be called once the limit is exceeded.
        def _boom(state, q):  # pragma: no cover - should never run
            raise AssertionError("answer_question must not be called past the limit")

        monkeypatch.setattr(answer_agent, "answer_question", _boom)
        state = {"incoming_question": "Q", "question_rounds": MAX_QUESTION_ROUNDS}
        result = answer_question_node(state)

        assert result["test_status"] == "FAIL"
        assert result["question_rounds"] == MAX_QUESTION_ROUNDS + 1
        assert result["nodes_log"][-1]["status"] == "FAIL"


# ===========================================================================
# build_prompt_with_answers()
# ===========================================================================


class TestBuildPromptWithAnswers:
    BASE = "Install the SDK."

    def test_empty_answers_returns_original_prompt(self):
        assert build_prompt_with_answers(self.BASE, []) == self.BASE

    def test_single_qa_appended(self):
        answers = [{"question": "Platform?", "answer": "iOS only."}]
        result = build_prompt_with_answers(self.BASE, answers)
        assert result.startswith(self.BASE)
        assert "Q: Platform?" in result
        assert "A: iOS only." in result
        assert "Do not ask the same questions again." in result

    def test_multiple_qa_appended_in_order(self):
        answers = [
            {"question": "Platform?", "answer": "iOS only."},
            {"question": "Dev key?", "answer": "Use Dev Key: ABC."},
        ]
        result = build_prompt_with_answers(self.BASE, answers)
        assert result.index("Q: Platform?") < result.index("Q: Dev key?")
        assert "A: Use Dev Key: ABC." in result

    def test_malformed_entries_ignored(self):
        # Non-dicts and entries missing question/answer are dropped; only the
        # single valid pair survives.
        answers = [
            "not a dict",
            {"question": "only question"},
            {"answer": "only answer"},
            {},
            {"question": "Good?", "answer": "Yes."},
        ]
        result = build_prompt_with_answers(self.BASE, answers)
        assert "Q: Good?" in result
        assert "A: Yes." in result
        assert "only question" not in result
        assert "only answer" not in result

    def test_all_malformed_returns_base_prompt(self):
        answers = ["x", {"question": "no answer"}, {}]
        assert build_prompt_with_answers(self.BASE, answers) == self.BASE
