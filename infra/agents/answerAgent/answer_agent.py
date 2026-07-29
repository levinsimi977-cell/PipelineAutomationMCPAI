"""
Answer Agent — answers installation questions on behalf of the developer.

Architecture: one LLM call answers everything. answer_question() gathers
the run's context (test policy, environment scan, agent work, prior Q&A)
and sends it + the raw question to the LLM (ANSWER_PROMPT), which also
handles multi-question splitting, consistency with prior answers, and
answer formatting — no regex/keyword pre-processing, since none of that
can be done reliably on natural language.

Raises UnansweredQuestionError if the LLM is unavailable or fails — no
fallback guessing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from infra.load_env import load_project_env
from infra.agents.answerAgent.answer_policy_repository import (
    get_answer_policy_repository,
)
from infra.agents.answerAgent.prompts.answer_templates import ANSWER_PROMPT

load_project_env()

APP_ID = os.getenv("APP_ID", "")
DEV_KEY = os.getenv("DEV_KEY", "")


def _get_openai_api_key() -> str:
    """Read API key at call time so runtime env changes are respected."""
    return os.getenv("OPENAI_API_KEY") or os.getenv("GPT_API_KEY") or ""


def _get_openai_model() -> str:
    """Read model name at call time: OPENAI_MODEL -> MODEL_NAME -> gpt-5.4"""
    return os.getenv("OPENAI_MODEL") or os.getenv("MODEL_NAME") or "gpt-5.4"

_FORBIDDEN_SDK_MARKERS = ("appsflyer", "com.appsflyer", "appsflyerlib")

MAX_QUESTION_ROUNDS = 10

class UnansweredQuestionError(Exception):
    """Raised when the LLM can't produce an answer (no API key, or the call failed)."""

    def __init__(self, question: str) -> None:
        self.question = question
        super().__init__(f"No answer available for: {(question or '')[:120]}")


def _scan_environment_facts(app_path: str, platform: str = "") -> dict[str, str]:
    """
    Pre-existing project facts only, read fresh each call. Must NOT detect
    AppsFlyer SDK presence (would leak the installation LLM's own work).
    """
    facts: dict[str, str] = {}
    if not app_path or not os.path.isdir(app_path):
        return facts

    # ── iOS / Xcode (project files — not IDE-specific) ───────────────────────
    for root, dirs, files in os.walk(app_path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "Pods"]
        for name in files:
            if "SceneDelegate" in name and name.endswith(".swift"):
                facts["has_scene_delegate_file"] = "true"
            if name.endswith(".xcodeproj"):
                rel = os.path.relpath(os.path.join(root, name), app_path)
                facts.setdefault("xcodeproj", rel.replace("\\", "/"))
            if name.endswith(".xcworkspace"):
                rel = os.path.relpath(os.path.join(root, name), app_path)
                facts.setdefault("xcworkspace", rel.replace("\\", "/"))
            if name.endswith(".pbxproj"):
                try:
                    text = open(os.path.join(root, name), encoding="utf-8", errors="ignore").read()
                    if any(m in text.lower() for m in _FORBIDDEN_SDK_MARKERS):
                        continue
                    swift = re.search(r"SWIFT_VERSION = ([\d.]+)", text)
                    if swift:
                        facts.setdefault("swift_version", swift.group(1))
                    deploy = re.search(r"IPHONEOS_DEPLOYMENT_TARGET = ([\d.]+)", text)
                    if deploy:
                        facts.setdefault("ios_deployment_target", deploy.group(1))
                except OSError:
                    pass

    if os.path.isfile(os.path.join(app_path, "Podfile")):
        facts["has_podfile"] = "true"
    if os.path.isfile(os.path.join(app_path, "Package.swift")):
        facts["has_package_swift"] = "true"

    for root, _, files in os.walk(app_path):
        for name in files:
            if not name.endswith("Info.plist"):
                continue
            try:
                text = open(os.path.join(root, name), encoding="utf-8", errors="ignore").read()
                match = re.search(
                    r"<key>MinimumOSVersion</key>\s*<string>([\d.]+)</string>",
                    text,
                )
                if match:
                    facts.setdefault("ios_minimum_os", match.group(1))
            except OSError:
                pass

    # ── Android (Gradle) ─────────────────────────────────────────────────────
    wrapper = os.path.join(app_path, "gradle", "wrapper", "gradle-wrapper.properties")
    if os.path.isfile(wrapper):
        try:
            text = open(wrapper, encoding="utf-8").read()
            match = re.search(r"distributionUrl=.*gradle-([\d.]+)", text)
            if match:
                facts["gradle_version"] = match.group(1)
        except OSError:
            pass

    for rel in ("build.gradle", "app/build.gradle", "app/build.gradle.kts"):
        path = os.path.join(app_path, rel)
        if not os.path.isfile(path):
            continue
        try:
            text = open(path, encoding="utf-8").read().lower()
            if any(marker in text for marker in _FORBIDDEN_SDK_MARKERS):
                continue
            for key, pattern in (
                ("min_sdk", r"minsdk\s*[=:]?\s*(\d+)"),
                ("compile_sdk", r"compilesdk\s*[=:]?\s*(\d+)"),
                ("target_sdk", r"targetsdk\s*[=:]?\s*(\d+)"),
            ):
                match = re.search(pattern, text, re.I)
                if match:
                    facts[key] = match.group(1)
        except OSError:
            pass

    pyver = os.path.join(app_path, ".python-version")
    if os.path.isfile(pyver):
        try:
            facts["python_version"] = open(pyver, encoding="utf-8").read().strip()
        except OSError:
            pass
    elif platform == "android":
        # Automation runner may use Python; only relevant for Android automation runs.
        try:
            out = subprocess.run(
                ["python3", "--version"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if out.stdout.strip():
                facts["python_version_machine"] = out.stdout.strip().replace("Python ", "")
        except (OSError, subprocess.TimeoutExpired):
            pass

    return facts


def _format_environment_facts(state: dict[str, Any]) -> str:
    platform = (state.get("platform") or "").strip().lower()
    facts = _scan_environment_facts((state.get("app_path") or "").strip(), platform)
    if not facts:
        return "No environment facts detected."
    return "\n".join(f"{key}: {value}" for key, value in facts.items())


def _format_test_decisions(state: dict[str, Any]) -> str:
    """
    answer_policy as raw JSON — safe, defensive access.

    This used to require state["run_id"] which raises when run_id is not
    present. Use state.get("run_id") and handle missing policy gracefully.
    """
    repo = get_answer_policy_repository()
    run_id = state.get("run_id")
    if not run_id:
        return (
            f"app_id: {APP_ID or 'not set'}\n"
            f"dev_key: {DEV_KEY or 'not set'}\n"
            "answer_policy:\nNo run_id set; no answer_policy available."
        )

    try:
        policy = repo.get(run_id)
    except Exception as exc:
        logger.exception("Failed to load answer_policy for run_id=%s: %s", run_id, type(exc).__name__)
        policy = None

    policy_text = (
        json.dumps(policy, indent=2, ensure_ascii=False, default=str)
        if policy
        else "No answer_policy configured."
    )
    return (
        f"app_id: {APP_ID or 'not set'}\n"
        f"dev_key: {DEV_KEY or 'not set'}\n"
        f"answer_policy:\n{policy_text}"
    )


def _format_test_prompt(state: dict[str, Any]) -> str:
    """Test prompt/goal and target app — what this run is trying to achieve."""
    lines: list[str] = []
    goal = (state.get("prompt_goal") or "").strip()
    if goal:
        lines.append(f"Goal: {goal}")
    app_path = (state.get("app_path") or "").strip()
    if app_path:
        lines.append(f"Target app_path: {app_path}")
    platform = (state.get("platform") or "").strip()
    if platform:
        lines.append(f"Target platform: {platform}")
    base = state.get("base_prompt") or state.get("agent_prompt")
    if base:
        lines.append(f"Installation prompt excerpt: {str(base)[:800]}")
    return "\n".join(lines) if lines else "No test prompt/goal configured."


def _format_agent_work_summary(state: dict[str, Any]) -> str:
    """What the installation LLM reported doing — from state node / agent memory."""
    sections: list[str] = []
    for key in (
        "installation_agent_summary",
        "agent_work_summary",
        "agent_actions",
        "installation_progress",
    ):
        value = state.get(key)
        if value:
            sections.append(f"{key}:\n{str(value)[:2000]}")

    agent_context = _format_agent_context(state)
    if agent_context != "No agent conversation context available.":
        sections.append(f"agent_messages:\n{agent_context}")

    return "\n\n".join(sections) if sections else "No installation agent work reported yet."


def _format_prior_answers(state: dict[str, Any]) -> str:
    entries = state.get("installation_answers") or []
    lines = [
        f"Q: {entry['question']}\nA: {entry['answer']}"
        for entry in entries
        if isinstance(entry, dict) and entry.get("question") and entry.get("answer")
    ]
    return "\n\n".join(lines) if lines else "None yet."


def _format_agent_context(state: dict[str, Any]) -> str:
    messages = state.get("agent_messages")
    if isinstance(messages, list) and messages:
        lines = []
        for msg in messages[-6:]:
            text = getattr(msg, "content", None) or str(msg)
            role = getattr(msg, "type", None) or msg.__class__.__name__
            lines.append(f"{role}: {str(text)[:500]}")
        return "\n".join(lines)

    last = state.get("last_agent_message")
    if last is not None:
        text = getattr(last, "content", None) or str(last)
        return f"last_agent_message: {str(text)[:1000]}"
    return "No agent conversation context available."


def _llm():
    from langchain_openai import ChatOpenAI

    model = _get_openai_model()
    api_key = _get_openai_api_key()

    return ChatOpenAI(
        model=model,
        temperature=0.1,
        api_key=api_key,
    )


import logging

logger = logging.getLogger(__name__)

def _llm_answer(state: dict[str, Any], question: str) -> str | None:
    """Send one ANSWER_PROMPT call to the LLM. None on any failure (no API
    key, network/API error, empty reply) — caller raises UnansweredQuestionError.

    Adds lightweight debug logging (no secrets) so runtime can show whether the
    LLM path was invoked and whether it short-circuited due to missing API key.
    Implements a small retry loop to handle transient API errors.
    """
    logger.debug("_llm_answer called; question_preview=%s", (question or "")[:200])
    api_key = _get_openai_api_key()
    if not api_key:
        logger.info("_llm_answer short-circuited: OPENAI_API_KEY not set")
        return None

    prompt = ANSWER_PROMPT.format(
        test_prompt=_format_test_prompt(state),
        test_decisions=_format_test_decisions(state),
        environment_facts=_format_environment_facts(state),
        agent_work_summary=_format_agent_work_summary(state),
        prior_answers=_format_prior_answers(state),
        question=(question or "").strip(),
    )

    max_attempts = 3
    backoff = 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            response = _llm().invoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            if isinstance(content, list):
                content = " ".join(str(getattr(part, "text", part)) for part in content)
            answer = str(content or "").strip()
            logger.debug("_llm_answer received answer_preview=%s (attempt %d)", (answer or "")[:200], attempt)
            return answer or None
        except Exception as exc:
            logger.warning("_llm_answer attempt %d/%d failed: %s", attempt, max_attempts, type(exc).__name__)
            if attempt == max_attempts:
                logger.exception("_llm_answer final failure: %s", type(exc).__name__)
                return None
            try:
                import time

                time.sleep(backoff)
                backoff *= 2
            except Exception:
                pass
    return None


def answer_question(state: dict[str, Any], question: str) -> str:
    """
    Produce an answer for an installation question — see module docstring.
    Raises UnansweredQuestionError if the LLM is unavailable or fails.
    """
    answer = _llm_answer(state, question)
    if not answer:
        raise UnansweredQuestionError(question)
    return answer

def answer_question_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    LangGraph node: read state["incoming_question"], answer it, update state.

    Wires state -> answer_question -> state return.
    Stops after MAX_QUESTION_ROUNDS questions.
    """

    question = (state.get("incoming_question") or "").strip()
    if not question:
        return {
            "nodes_log": [
                *(state.get("nodes_log") or []),
                {
                    "node": "answer_question",
                    "status": "SKIP",
                    "message": "No incoming_question provided",
                },
            ],
        }

    question_rounds = state.get("question_rounds", 0) + 1

    if question_rounds > MAX_QUESTION_ROUNDS:
        return {
            "question_rounds": question_rounds,
            "test_status": "FAIL",
            "nodes_log": [
                *(state.get("nodes_log") or []),
                {
                    "node": "answer_question",
                    "status": "FAIL",
                    "message": (
                        f"Maximum question limit ({MAX_QUESTION_ROUNDS}) exceeded."
                    ),
                },
            ],
        }

 
    try:
         answer = answer_question(state, question)
    except Exception as e:
        return {
        "question_rounds": question_rounds,
        "nodes_log": [
            *(state.get("nodes_log") or []),
            {
                "node": "answer_question",
                "status": "ERROR",
                "message": str(e),
            },
        ],
    }

    qa_entry = {
        "question": question,
        "answer": answer,
    }

    return {
        "question_rounds": question_rounds,
        "installation_answers": [
            *(state.get("installation_answers") or []),
            qa_entry,
        ],
        "nodes_log": [
            *(state.get("nodes_log") or []),
            {
                "node": "answer_question",
                "status": "SUCCESS",
                "message": f"Answered: {question}",
            },
        ],
    }

def build_prompt_with_answers(
    base_prompt: str,
    installation_answers: list[dict[str, str]],
) -> str:
    """Append prior Q&A to the next agent prompt so questions are not repeated."""
    if not installation_answers:
        return base_prompt

    qa_lines = [
        f"Q: {entry['question']}\nA: {entry['answer']}"
        for entry in installation_answers
        if isinstance(entry, dict) and entry.get("question") and entry.get("answer")
    ]
    if not qa_lines:
        return base_prompt

    return (
        f"{base_prompt}\n\n"
        "Installation clarifications already provided:\n"
        f"{chr(10).join(qa_lines)}\n\n"
        "Use these answers and continue. Do not ask the same questions again."
    )
