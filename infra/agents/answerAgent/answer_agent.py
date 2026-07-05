"""
Answer Agent — answers installation questions on behalf of the developer.

Source: Android demo_project_android_groupe@feature-mcp-with-listener
        (responseAgent.py)
T3 owns this file.

Strategy (three layers):
    1. Regex classification  → known category (platform, dev_key, …)
    2. Deterministic answer  → from test policy (+ safe environment facts)
    3. LLM fallback          → for unrecognised questions only
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI

import config
from prompts.answer_templates import ANSWER_PROMPT, QUESTION_HINTS

_FORBIDDEN_SDK_MARKERS = ("appsflyer", "com.appsflyer", "appsflyerlib")


def _answer_policy(state: dict[str, Any]) -> dict[str, Any]:
    """Test decisions from state/answer_policy — never SDK install detection."""
    policy = dict(state.get("answer_policy") or {})
    return {
        "platform": (state.get("platform") or policy.get("platform") or "").strip().lower(),
        "app_id": (state.get("app_id") or policy.get("app_id") or config.APP_ID or "").strip(),
        "dev_key": (policy.get("dev_key") or state.get("dev_key") or config.DEV_KEY or "").strip(),
        "use_response_listener": policy.get("use_response_listener"),
        "use_deep_linking": policy.get("use_deep_linking"),
        "use_scene_delegate": policy.get("use_scene_delegate"),
        "use_att": policy.get("use_att"),
        "use_cuid": policy.get("use_cuid"),
        "dependency_manager": str(policy.get("dependency_manager") or "").strip().lower(),
        "sdk_already_integrated": policy.get("sdk_already_integrated"),
        "onelink_url": str(policy.get("onelink_url") or "").strip(),
        "use_custom_uri_scheme": policy.get("use_custom_uri_scheme"),
        "deeplink_test_type": str(policy.get("deeplink_test_type") or "").strip().lower(),
        "event_name": str(policy.get("event_name") or "").strip(),
        "event_params": str(policy.get("event_params") or "").strip(),
        "event_vertical": str(policy.get("event_vertical") or "").strip(),
        "has_sha256": policy.get("has_sha256"),
        "sha256_fingerprint": str(policy.get("sha256_fingerprint") or "").strip(),
        "device_id": str(policy.get("device_id") or "").strip(),
        "verify_logs_ready": policy.get("verify_logs_ready"),
        "custom_answers": dict(policy.get("custom_answers") or {}),
        "prompt_goal": (state.get("prompt_goal") or policy.get("prompt_goal") or "").strip(),
    }


def _wants_boolean(question: str) -> bool:
    lower = (question or "").lower()
    return bool(re.search(r"true\s*/\s*false", lower)) or "(true/false)" in lower


def _wants_yes_no(question: str) -> bool:
    return "(yes/no)" in (question or "").lower()


def _fmt_bool(value: bool) -> str:
    return "True" if value else "False"


def _fmt_yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _scan_environment_facts(app_path: str, platform: str = "") -> dict[str, str]:
    """
    Pre-existing project facts only (read fresh on every call — no cache).
    Must NOT detect AppsFlyer SDK presence (would leak LLM install work).

    Android: Gradle wrapper, min/compile/target SDK from build.gradle.
    iOS/Xcode: xcodeproj, Podfile, deployment target, Swift version from project files.
    Python: only from .python-version inside the project (not the runner machine).
    """
    facts: dict[str, str] = {}
    if not app_path or not os.path.isdir(app_path):
        return facts

    # ── iOS / Xcode (project files — not IDE-specific) ───────────────────────
    for root, dirs, files in os.walk(app_path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "Pods"]
        for name in files:
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
    """Test policy fields for LLM fallback — no SDK install detection."""
    p = _answer_policy(state)
    lines = [
        f"platform: {p['platform'] or 'not set'}",
        f"app_id: {p['app_id'] or 'not set'}",
        f"dev_key_configured: {bool(p['dev_key'])}",
        f"use_response_listener: {p['use_response_listener']}",
        f"use_scene_delegate: {p['use_scene_delegate']}",
        f"use_att: {p['use_att']}",
        f"use_cuid: {p['use_cuid']}",
        f"use_deep_linking: {p['use_deep_linking']}",
        f"dependency_manager: {p['dependency_manager'] or 'not set'}",
        f"sdk_already_integrated: {p['sdk_already_integrated']}",
        f"onelink_url: {p['onelink_url'] or 'not set'}",
        f"use_custom_uri_scheme: {p['use_custom_uri_scheme']}",
        f"deeplink_test_type: {p['deeplink_test_type'] or 'not set'}",
        f"has_sha256: {p['has_sha256']}",
        f"sha256_configured: {bool(p['sha256_fingerprint'])}",
        f"device_id: {p['device_id'] or 'not set'}",
        f"event_name: {p['event_name'] or 'not set'}",
        f"event_params: {p['event_params'] or 'not set'}",
        f"verify_logs_ready: {p['verify_logs_ready']}",
    ]
    if p["prompt_goal"]:
        lines.append(f"test_goal: {p['prompt_goal']}")
    return "\n".join(lines)


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


def _llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=config.GEMINI_MODEL,
        temperature=0.1,
        google_api_key=config.GEMINI_API_KEY,
    )


def classify_question(question: str) -> str:
    """
    Match question text against QUESTION_HINTS regexes.

    Does NOT decide whether the message is a question (Dev 7 classifier).
    """
    lower = (question or "").lower()
    for category, patterns in QUESTION_HINTS.items():
        if any(re.search(pattern, lower) for pattern in patterns):
            return category
    return "general"


def _environment_answer(question: str, state: dict[str, Any]) -> str | None:
    """Answer tooling/environment questions from safe pre-existing project facts only."""
    q = (question or "").lower()
    platform = (state.get("platform") or "").strip().lower()
    facts = _scan_environment_facts((state.get("app_path") or "").strip(), platform)

    if "xcode" in q and facts.get("xcodeproj"):
        return facts["xcodeproj"]
    if "swift" in q and facts.get("swift_version"):
        return facts["swift_version"]
    if any(k in q for k in ("deployment target", "minimum os", "minimum ios", "min ios")):
        return facts.get("ios_deployment_target") or facts.get("ios_minimum_os")
    if "cocoapods" in q or "podfile" in q:
        if facts.get("has_podfile") == "true":
            return "CocoaPods (Podfile present)."
        if facts.get("has_package_swift") == "true":
            return "Swift Package Manager (Package.swift present)."
    if "python" in q:
        return facts.get("python_version") or facts.get("python_version_machine")
    if "gradle" in q:
        return facts.get("gradle_version")
    if "min sdk" in q or "minsdk" in q:
        return facts.get("min_sdk") or facts.get("ios_deployment_target") or facts.get("ios_minimum_os")
    if "compile sdk" in q or "compilesdk" in q:
        return facts.get("compile_sdk")
    if "target sdk" in q or "targetsdk" in q:
        return facts.get("target_sdk") or facts.get("ios_deployment_target")
    return None


def _deterministic_answer(
    category: str,
    question: str,
    state: dict[str, Any],
) -> str | None:
    """Return test-driven answer, or None → LLM/environment fallback."""
    p = _answer_policy(state)
    q = (question or "").lower()

    if category == "platform":
        if p["platform"] == "ios":
            return "iOS only."
        if p["platform"] == "android":
            return "Android only."
        return None

    if category == "scene_delegate":
        if p["use_scene_delegate"] is None:
            return None
        if _wants_boolean(q):
            return _fmt_bool(bool(p["use_scene_delegate"]))
        if _wants_yes_no(q):
            return _fmt_yes_no(bool(p["use_scene_delegate"]))
        return (
            "Yes, use Scene Delegate support."
            if p["use_scene_delegate"]
            else "No, I don't want Scene Delegate support."
        )

    if category == "response_listener":
        if p["use_response_listener"] is None:
            return None
        if _wants_boolean(q):
            return _fmt_bool(bool(p["use_response_listener"]))
        if _wants_yes_no(q):
            return _fmt_yes_no(bool(p["use_response_listener"]))
        return (
            "Yes, use a response listener."
            if p["use_response_listener"]
            else "No response listener needed."
        )

    if category == "att_tracking":
        if p["use_att"] is None:
            return None
        if _wants_boolean(q):
            return _fmt_bool(bool(p["use_att"]))
        if _wants_yes_no(q):
            return _fmt_yes_no(bool(p["use_att"]))
        return "No ATT support needed." if not p["use_att"] else "Yes, enable ATT."

    if category == "cuid":
        if p["use_cuid"] is None:
            return None
        if _wants_boolean(q):
            return _fmt_bool(bool(p["use_cuid"]))
        if _wants_yes_no(q):
            return _fmt_yes_no(bool(p["use_cuid"]))
        return "No CUID needed." if not p["use_cuid"] else "Yes, use CUID."

    if category == "dev_key":
        if p["dev_key"]:
            return f"Use Dev Key: {p['dev_key']}."
        return "Use the DEV_KEY configured for this test run."

    if category == "dependency_manager":
        if p["dependency_manager"] == "cocoapods":
            return "Use CocoaPods."
        if p["dependency_manager"] in {"spm", "swift_package_manager"}:
            return "Use Swift Package Manager."
        return None

    if category == "sdk_integrated":
        if p["sdk_already_integrated"] is None:
            return None
        integrated = bool(p["sdk_already_integrated"])
        if _wants_yes_no(q):
            return _fmt_yes_no(integrated)
        if _wants_boolean(q):
            return _fmt_bool(integrated)
        return (
            "Yes, the AppsFlyer SDK is already integrated."
            if integrated
            else "No, the SDK is not integrated yet."
        )

    if category == "onelink_deeplink":
        if p["use_deep_linking"] is None:
            return None
        if not p["use_deep_linking"]:
            if _wants_boolean(q):
                return _fmt_bool(False)
            if _wants_yes_no(q):
                return _fmt_yes_no(False)
            if "uri" in q:
                return "No custom URI scheme."
            return None
        if p["onelink_url"] and "onelink" in q:
            return p["onelink_url"]
        if p["use_custom_uri_scheme"] is not None and "uri" in q:
            if _wants_boolean(q):
                return _fmt_bool(bool(p["use_custom_uri_scheme"]))
            if _wants_yes_no(q):
                return _fmt_yes_no(bool(p["use_custom_uri_scheme"]))
            return _fmt_yes_no(bool(p["use_custom_uri_scheme"]))
        return None

    if category == "verify_logs":
        if p["verify_logs_ready"] is None:
            return None
        if p["verify_logs_ready"]:
            return "Yes, the log file is ready for verification."
        return "No, the log file is not ready yet."

    if category == "inapp_event":
        if p["event_vertical"] and "vertical" in q:
            return p["event_vertical"]
        if p["event_name"]:
            params = p["event_params"] or ""
            return f"Use event {p['event_name']} with parameters {params}."
        return None

    if category == "deeplink_testing":
        if p["deeplink_test_type"] == "deferred":
            return "Guide me through testing a deferred deep link."
        if p["deeplink_test_type"] == "direct":
            return "Guide me through testing a direct deep link."
        return None

    if category == "device_id":
        if p["device_id"]:
            return f"Use device ID: {p['device_id']}."
        return None

    if category == "sha256":
        if p["has_sha256"] is None:
            return None
        if not p["has_sha256"]:
            if _wants_boolean(q):
                return _fmt_bool(False)
            if _wants_yes_no(q):
                return _fmt_yes_no(False)
            return "No, I don't have SHA256 yet."
        if _wants_boolean(q):
            return _fmt_bool(True)
        if _wants_yes_no(q):
            return _fmt_yes_no(True)
        if p["sha256_fingerprint"]:
            return p["sha256_fingerprint"]
        return "Yes, I already have SHA256."

    if category == "environment":
        return _environment_answer(question, state)

    for key, answer in (p.get("custom_answers") or {}).items():
        if key.lower() in q:
            return str(answer)

    return None


def _project_context(state: dict[str, Any]) -> str:
    """Context for LLM fallback: test policy + prior answers + agent memory + environment."""
    return "\n\n".join([
        "=== test decisions ===",
        _format_test_decisions(state),
        "=== environment facts (pre-existing, not SDK install status) ===",
        _format_environment_facts(state),
        "=== prior answers ===",
        _format_prior_answers(state),
        "=== agent conversation ===",
        _format_agent_context(state),
    ])


def answer_question(state: dict[str, Any], question: str) -> str:
    """
    Produce an answer for an installation question.

    Order: deterministic (test policy) → environment facts → LLM fallback.
    """
    category = classify_question(question)

    deterministic = _deterministic_answer(category, question, state)
    if deterministic:
        return deterministic

    if category == "general":
        env_answer = _environment_answer(question, state)
        if env_answer:
            return env_answer

    if not config.GEMINI_API_KEY:
        return "Use your best professional judgment and proceed."

    prompt = ANSWER_PROMPT.format(
        test_decisions=_format_test_decisions(state),
        prior_answers=_format_prior_answers(state),
        context=_project_context(state),
        question=(question or "").strip(),
    )
    try:
        response = _llm().invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        if isinstance(content, list):
            content = " ".join(str(getattr(part, "text", part)) for part in content)
        answer = str(content or "").strip()
    except Exception:
        answer = ""

    return answer or "Use your best professional judgment and proceed."


def answer_question_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    LangGraph node: read state["incoming_question"], answer it, update state.

    T3-04b (Developer 9) — not implemented here.
    """
    ...


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
