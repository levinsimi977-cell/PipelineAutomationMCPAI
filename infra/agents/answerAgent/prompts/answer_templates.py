"""
Answer & classifier prompt templates + question-hint patterns.

T3-03 (Developer 8):
    QUESTION_HINTS  → topic detection (regex on question text)
    ANSWER_PROMPT   → LLM fallback only
    CLASSIFY_PROMPT → exported for agents/classifier.py (Dev 7)
"""
from __future__ import annotations

from langchain_core.prompts import PromptTemplate

__all__ = ["QUESTION_HINTS", "CLASSIFY_PROMPT", "ANSWER_PROMPT"]

# Regex on question TEXT only — does NOT read test JSON.

QUESTION_HINTS: dict[str, list[str]] = {
    "platform": [
        r"\bandroid\b.*\bios\b",
        r"\bios\b.*\bandroid\b",
        r"which platform",
        r"what platform",
        r"should i integrate for",
        r"identify the platform",
        r"פלטפורמה",
    ],
    "scene_delegate": [
        r"scene\s*delegate",
        r"isdelegate",
    ],
    "response_listener": [
        r"response\s*listener",
        r"useresponselistener",
        r"completion handler",
        r"isresponselistener",
        r"attribution callback",
    ],
    "att_tracking": [
        r"\batt\b",
        r"apptrackingtransparency",
        r"tracking transparency",
        r"isatt",
    ],
    "cuid": [
        r"\bcuid\b",
        r"customer\s*user\s*id",
        r"iscuid",
    ],
    "dev_key": [
        r"dev\s*key",
        r"devkey",
        r"developer\s*key",
        r"appsflyer\s*key",
        r"placeholder",
        r"access key",
        r"מפתח",
    ],
    "dependency_manager": [
        r"cocoapods?",
        r"swift\s*package\s*manager",
        r"\bspm\b",
        r"podfile",
        r"package\.swift",
    ],
    "onelink_deeplink": [
        r"onelink",
        r"deep\s*link",
        r"deeplink",
        r"custom uri scheme",
        r"urischeme",
    ],
    "sdk_integrated": [
        r"already integrated",
        r"sdk integrated",
        r"integrate the sdk first",
        r"have you already integrated",
    ],
    "verify_logs": [
        r"log file is ready",
        r"confirmlogfileready",
        r"confirm.*log",
        r"paste.*log",
    ],
    "inapp_event": [
        r"event name",
        r"eventparams",
        r"event parameter",
        r"in.?app event",
        r"which vertical",
        r"manually provide",
    ],
    "deeplink_testing": [
        r"deferred or direct",
        r"completed the test",
        r"finished the test",
    ],
    "device_id": [
        r"device id",
        r"multiple devices",
    ],
    "sha256": [
        r"sha256",
        r"sha-256",
    ],
    "environment": [
        r"xcode",
        r"xcodeproj",
        r"xcworkspace",
        r"deployment target",
        r"minimum os",
        r"minimum ios",
        r"swift version",
        r"gradle version",
        r"which gradle",
        r"jdk version",
        r"java version",
        r"min\s*sdk",
        r"compile\s*sdk",
        r"target\s*sdk",
        r"python version",
        r"which python",
    ],
}

CLASSIFY_PROMPT: PromptTemplate = PromptTemplate.from_template(
    "You are a strict classifier inside an automated AppsFlyer SDK installation pipeline.\n"
    "An installation agent (another LLM) is trying to install the AppsFlyer SDK.\n"
    "Read its latest message and decide what the pipeline should do next.\n\n"
    "Labels:\n"
    "- SUCCESS: the agent says it finished / completed the SDK installation.\n"
    "- FAIL: the agent says it could NOT install the SDK / gave up / hit an unrecoverable error.\n"
    "- QUESTION: the agent is asking the developer ANY technical question it needs answered to continue.\n\n"
    "Routing:\n"
    "- SUCCESS -> run_agent_prompt\n"
    "- FAIL    -> fail_node\n"
    "- QUESTION-> answer_question\n\n"
    "Agent message:\n{agent_text}\n\n"
    "STATE snapshot (JSON):\n{state_snapshot}\n\n"
    'Return ONLY a JSON object: {{"label": "SUCCESS|FAIL|QUESTION", "question": "...", '
    '"next": "run_agent_prompt|fail_node|answer_question", "reason": "..."}}'
)

ANSWER_PROMPT: PromptTemplate = PromptTemplate.from_template(
    "You answer AppsFlyer SDK installation questions on behalf of the developer.\n"
    "Be short, decisive, and technical. Return ONLY the answer text.\n\n"
    "Developer decisions for THIS test run (do NOT guess beyond this):\n"
    "{test_decisions}\n\n"
    "Previous answers you already gave in this test run:\n"
    "{prior_answers}\n\n"
    "Conversation context:\n"
    "{context}\n\n"
    "Question:\n{question}\n\n"
    "Rules:\n"
    "- Answer ONLY according to the test decisions above.\n"
    "- Stay consistent with your previous answers.\n"
    "- Do NOT assume the SDK is installed unless the test says so.\n"
    "- Do NOT mention AppsFlyer SDK install status from project files.\n"
    "- If True/False or yes/no expected, reply with exactly that format.\n"
    "- If the test does not cover this, say: Use your best professional judgment and proceed.\n"
)
