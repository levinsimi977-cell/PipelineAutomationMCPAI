"""
Comprehensive pytest suite for ``answer_agent.py``.

Design goals
------------
* Pure ``pytest`` (fixtures + ``monkeypatch``; no unittest).
* Zero external services: ``config``, Gemini / ``ChatGoogleGenerativeAI``,
  the filesystem scan and ``subprocess.run`` are all mocked, and no real
  ``GEMINI_API_KEY`` is ever required.
* Tests are grouped by the function under test and kept independent so they
  can run in any order.

Import bootstrap
----------------
``answer_agent`` does ``import config`` (there is no ``config.py`` in the repo)
and ``from prompts.answer_templates import ...`` (which pulls in
``langchain_core``).  Before importing the module we therefore:

1. inject a fake ``config`` module into ``sys.modules``,
2. put the ``answerAgent`` package dir on ``sys.path`` so ``prompts`` resolves,
3. stub ``langchain_core.prompts.PromptTemplate`` *only if* it is not installed.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Import bootstrap (runs once, at collection time, before importing the SUT)
# ---------------------------------------------------------------------------

# 1) Fake ``config`` module — never a real API key.
_fake_config = sys.modules.get("config")
if _fake_config is None:
    _fake_config = types.ModuleType("config")
    sys.modules["config"] = _fake_config
# Ensure every attribute answer_agent touches exists, regardless of who created
# the fake config module (another test file might have registered it first).
for _attr, _val in (
    ("APP_ID", "test-app-id"),
    ("DEV_KEY", ""),
    ("GEMINI_MODEL", "gemini-test"),
    ("GEMINI_API_KEY", ""),
):
    if not hasattr(_fake_config, _attr):
        setattr(_fake_config, _attr, _val)

# 2) Make ``answer_agent`` and its ``prompts`` package importable.
_ANSWER_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "infra", "agents", "answerAgent")
)
if _ANSWER_AGENT_DIR not in sys.path:
    sys.path.insert(0, _ANSWER_AGENT_DIR)

# 3) Stub langchain_core.prompts.PromptTemplate if the dependency is missing so
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
    classify_question,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_config(monkeypatch):
    """Reset the fake ``config`` before every test so tests stay independent.

    ``GEMINI_API_KEY`` defaults to empty → the deterministic path is used and
    no LLM is ever contacted unless a test opts in.
    """
    monkeypatch.setattr(answer_agent.config, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(answer_agent.config, "DEV_KEY", "", raising=False)
    monkeypatch.setattr(answer_agent.config, "APP_ID", "test-app-id", raising=False)
    monkeypatch.setattr(answer_agent.config, "GEMINI_MODEL", "gemini-test", raising=False)


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
    """Opt-in helper: pretend a Gemini key is configured."""
    monkeypatch.setattr(answer_agent.config, "GEMINI_API_KEY", "fake-key-123")


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
# classify_question()
# ===========================================================================


class TestClassifyQuestion:
    """Regex topic routing — first matching category wins."""

    def test_platform_question(self):
        assert classify_question("Which platform should I use?") == "platform"

    def test_dev_key_question(self):
        assert classify_question("What is the dev key?") == "dev_key"

    def test_att_question(self):
        assert classify_question("Should I enable ATT?") == "att_tracking"

    def test_scene_delegate_question(self):
        assert (
            classify_question("Does your app already use a SceneDelegate?")
            == "scene_delegate_exists"
        )

    def test_deep_link_question(self):
        assert (
            classify_question("Do you want OneLink deep linking configured?")
            == "onelink_deeplink"
        )

    def test_environment_question(self):
        assert classify_question("What Swift version does the project use?") == "environment"

    def test_unknown_question_returns_general(self):
        assert classify_question("How does this whole system work?") == "general"

    @pytest.mark.parametrize("bad", ["", None])
    def test_empty_or_none_is_general(self, bad):
        # Guards the ``(question or "")`` branch.
        assert classify_question(bad) == "general"


# ===========================================================================
# _deterministic_answer()
# ===========================================================================


class TestDeterministicAnswerPositive:
    """Each topic returns its test-driven answer given the required fields."""

    def test_platform_ios(self):
        state = {"platform": "ios"}
        assert answer_agent._deterministic_answer("platform", "platform?", state) == "iOS only."

    def test_platform_android(self):
        state = {"platform": "android"}
        assert (
            answer_agent._deterministic_answer("platform", "platform?", state) == "Android only."
        )

    def test_response_listener(self):
        state = _policy_state(use_response_listener=True)
        assert (
            answer_agent._deterministic_answer("response_listener", "use listener?", state)
            == "Yes, use a response listener."
        )

    def test_att_enabled(self):
        state = _policy_state(use_att=True)
        assert (
            answer_agent._deterministic_answer("att_tracking", "enable att?", state)
            == "Yes, enable ATT."
        )

    def test_att_yes_no_formatting(self):
        # Question phrasing forces a bare yes/no answer.
        state = _policy_state(use_att=False)
        assert (
            answer_agent._deterministic_answer("att_tracking", "ATT? (yes/no)", state) == "no"
        )

    def test_cuid_disabled(self):
        state = _policy_state(use_cuid=False)
        assert (
            answer_agent._deterministic_answer("cuid", "use cuid?", state) == "No CUID needed."
        )

    def test_dependency_manager_cocoapods(self):
        state = _policy_state(dependency_manager="cocoapods")
        assert (
            answer_agent._deterministic_answer("dependency_manager", "which dep mgr?", state)
            == "Use CocoaPods."
        )

    def test_dependency_manager_spm(self):
        state = _policy_state(dependency_manager="spm")
        assert (
            answer_agent._deterministic_answer("dependency_manager", "which dep mgr?", state)
            == "Use Swift Package Manager."
        )

    def test_sdk_integrated_true(self):
        state = _policy_state(sdk_already_integrated=True)
        assert (
            answer_agent._deterministic_answer("sdk_integrated", "already integrated?", state)
            == "Yes, the AppsFlyer SDK is already integrated."
        )

    def test_dev_key_with_value(self):
        state = _policy_state(dev_key="ABC123")
        assert (
            answer_agent._deterministic_answer("dev_key", "dev key?", state)
            == "Use Dev Key: ABC123."
        )

    def test_dev_key_without_value_uses_configured(self):
        # dev_key never returns None — falls back to a fixed sentence.
        assert (
            answer_agent._deterministic_answer("dev_key", "dev key?", {})
            == "Use the DEV_KEY configured for this test run."
        )

    def test_url_identifier(self):
        state = _policy_state(url_identifier="com.example.deeplink")
        assert (
            answer_agent._deterministic_answer("url_identifier", "url identifier?", state)
            == "com.example.deeplink"
        )

    def test_uri_scheme_value(self):
        state = _policy_state(uri_scheme="myapp")
        assert (
            answer_agent._deterministic_answer("uri_scheme_value", "which uri scheme?", state)
            == "myapp"
        )

    def test_deep_linking_returns_onelink_url(self):
        state = _policy_state(use_deep_linking=True, onelink_url="https://x.onelink.me/abc")
        assert (
            answer_agent._deterministic_answer(
                "onelink_deeplink", "What is the OneLink URL?", state
            )
            == "https://x.onelink.me/abc"
        )

    def test_deep_linking_disabled_true_false(self):
        state = _policy_state(use_deep_linking=False)
        assert (
            answer_agent._deterministic_answer(
                "onelink_deeplink", "Use deep linking? (true/false)", state
            )
            == "False"
        )

    def test_app_launched_true(self):
        state = _policy_state(app_launched=True)
        assert (
            answer_agent._deterministic_answer("app_launched", "did you launch the app?", state)
            == "Yes, the app has been launched."
        )

    def test_device_id(self):
        state = _policy_state(device_id="emulator-5554")
        assert (
            answer_agent._deterministic_answer("device_id", "device id?", state)
            == "Use device ID: emulator-5554."
        )

    def test_sha256_returns_fingerprint(self):
        state = _policy_state(has_sha256=True, sha256_fingerprint="AA:BB:CC")
        assert (
            answer_agent._deterministic_answer("sha256", "what is the sha256?", state)
            == "AA:BB:CC"
        )

    def test_sha256_absent_bool(self):
        state = _policy_state(has_sha256=False)
        assert (
            answer_agent._deterministic_answer("sha256", "do you have sha256? (yes/no)", state)
            == "no"
        )

    def test_verify_logs_ready(self):
        state = _policy_state(verify_logs_ready=True)
        assert (
            answer_agent._deterministic_answer("verify_logs", "is the log ready?", state)
            == "Yes, the log file is ready for verification."
        )

    def test_verify_logs_not_ready(self):
        state = _policy_state(verify_logs_ready=False)
        assert (
            answer_agent._deterministic_answer("verify_logs", "is the log ready?", state)
            == "No, the log file is not ready yet."
        )

    def test_inapp_event_with_name(self):
        state = _policy_state(event_name="af_purchase", event_params="{revenue: 1}")
        assert (
            answer_agent._deterministic_answer("inapp_event", "what event name?", state)
            == "Use event af_purchase with parameters {revenue: 1}."
        )

    def test_scene_delegate_exists_true(self, fake_env):
        # Environment scan reports a SceneDelegate file present.
        fake_env["has_scene_delegate_file"] = "true"
        assert (
            answer_agent._deterministic_answer(
                "scene_delegate_exists", "already use scenedelegate?", {}
            )
            == "Yes, the app already uses SceneDelegate."
        )

    def test_environment_category_delegates_to_scan(self, fake_env):
        fake_env["xcodeproj"] = "App.xcodeproj"
        assert (
            answer_agent._deterministic_answer(
                "environment", "which xcode project?", {"platform": "ios"}
            )
            == "App.xcodeproj"
        )

    def test_custom_answers_fallback(self):
        # Unknown category falls through to custom_answers keyword matching.
        state = _policy_state(custom_answers={"proxy": "use corp proxy"})
        assert (
            answer_agent._deterministic_answer(
                "general", "should I configure the proxy here?", state
            )
            == "use corp proxy"
        )


class TestDeterministicAnswerMissingFields:
    """When the required policy field is absent the function returns ``None``."""

    @pytest.mark.parametrize(
        "category, question, state",
        [
            ("platform", "platform?", {}),
            ("response_listener", "use listener?", {}),
            ("att_tracking", "enable att?", {}),
            ("cuid", "use cuid?", {}),
            ("scene_delegate_integration", "scene delegate?", {}),
            ("scene_delegate_deeplink", "deeplink in scenedelegate?", {}),
            ("sdk_integrated", "already integrated?", {}),
            ("url_identifier", "url identifier?", {}),
            ("uri_scheme_value", "which uri scheme?", {}),
            ("onelink_deeplink", "onelink?", {}),
            ("verify_logs", "log ready?", {}),
            ("inapp_event_method", "which option?", {}),
            ("inapp_event", "event name?", {}),
            ("deeplink_testing", "deferred or direct?", {}),
            ("app_launched", "launched the app?", {}),
            ("device_id", "device id?", {}),
            ("sha256", "sha256?", {}),
            ("environment", "which xcode project?", {}),
            ("dependency_manager", "which dep mgr?", {}),
        ],
    )
    def test_missing_field_returns_none(self, category, question, state):
        assert answer_agent._deterministic_answer(category, question, state) is None


# ===========================================================================
# answer_question()
# ===========================================================================


class TestAnswerQuestion:
    def test_deterministic_path(self):
        # No API key + simple platform question → deterministic answer.
        answer = answer_question({"platform": "ios"}, "What platform is this?")
        assert answer == "iOS only."

    def test_environment_fallback(self, fake_env):
        # Environment topic, no key → deterministic chain uses the (mocked) scan.
        fake_env["xcodeproj"] = "App.xcodeproj"
        answer = answer_question({"platform": "ios"}, "Which xcode project should I open?")
        assert answer == "App.xcodeproj"

    def test_llm_path(self, monkeypatch, with_api_key):
        # API key set + LLM-preferred category → LLM answer is returned verbatim.
        fake_llm = _FakeLLM(content="Do the next integration step.")
        monkeypatch.setattr(answer_agent, "_llm", lambda: fake_llm)

        answer = answer_question(
            {"platform": "android"}, "What should I do next to finish integration?"
        )
        assert answer == "Do the next integration step."
        assert fake_llm.calls, "the mocked LLM should have been invoked"

    def test_deterministic_fallback_when_llm_returns_none(self, monkeypatch, with_api_key):
        # LLM-preferred category, but the LLM yields nothing → deterministic wins.
        monkeypatch.setattr(answer_agent, "_llm_answer", lambda *a, **k: None)

        state = _policy_state(app_launched=True)
        answer = answer_question(state, "Did you launch the app?")
        assert answer == "Yes, the app has been launched."

    def test_llm_failure_falls_back_to_deterministic(self, monkeypatch, with_api_key):
        # The real _llm_answer runs, its invoke() raises, exception is swallowed,
        # and the deterministic answer is produced instead.
        failing = _FakeLLM(error=RuntimeError("network down"))
        monkeypatch.setattr(answer_agent, "_llm", lambda: failing)

        state = _policy_state(app_launched=False)
        answer = answer_question(state, "Did you launch the app?")
        assert answer == "No, the app has not been launched yet."
        assert failing.calls, "the LLM should have been attempted before falling back"

    def test_raises_when_no_answer_exists(self):
        # No key, unknown question, empty state → nothing can answer it.
        with pytest.raises(UnansweredQuestionError) as exc_info:
            answer_question({}, "Tell me an unrelated story.")
        assert exc_info.value.category == "general"

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
