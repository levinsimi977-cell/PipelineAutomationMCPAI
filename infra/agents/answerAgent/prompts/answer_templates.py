"""
Answer & classifier prompt templates + question-hint patterns.

T3-03 (Developer 8):
    QUESTION_HINTS  → topic detection (regex on question text)
    ANSWER_PROMPT   → LLM fallback only (opt-in via config)
    CLASSIFY_PROMPT → exported for agents/classifier.py (Dev 7)

Order matters: first matching category wins — put specific patterns before broad ones.
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
    "project_path": [
        r"projectpath",
        r"project path",
        r"provide.*path.*project",
        r"path to (?:the )?(?:xcode|ios|android) project",
    ],
    "url_identifier": [
        r"url identifier",
        r"urlidentifier",
        r"identifier in info\.plist",
    ],
    "uri_scheme_value": [
        r"uri scheme should i add",
        r"what unique uri scheme",
        r"unique uri scheme",
    ],
    "scene_delegate_exists": [
        r"already use a scenedelegate",
        r"does your app.*scenedelegate",
        r"already have a scenedelegate",
    ],
    "scene_delegate_deeplink": [
        r"deep linking support there too",
        r"optional appsflyer deep linking support",
        r"deeplink.*scenedelegate",
        r"scenedelegate.*deeplink",
    ],
    "onelink_deeplink": [
        r"onelink",
        r"deep\s*link",
        r"deeplink",
        r"custom uri scheme",
        r"urischeme",
    ],
    "scene_delegate_integration": [
        r"support scene\s*delegate",
        r"want.*scene\s*delegate",
        r"isdelegate",
        r"scene\s*delegate",
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
        r"app tracking transparency",
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
        r"rerun with confirmlogfileready",
    ],
    "inapp_event_method": [
        r"three options",
        r"which option would you like",
        r"which option",
        r"manually provide the event",
        r"top predefined",
        r"vertical specific",
    ],
    "inapp_event": [
        r"event name",
        r"eventparams",
        r"event parameter",
        r"in.?app event",
        r"which vertical",
        r"enter the event name",
    ],
    "deeplink_testing": [
        r"deferred or direct",
        r"completed the test",
        r"finished the test",
    ],
    "app_launched": [
        r"launch the app",
        r"launched the app",
        r"did you run the app",
        r"have you run the app",
        r"run the app",
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
        r"which swift",
        r"what swift",
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
    "=== TEST PROMPT / GOAL (what this run must achieve) ===\n"
    "{test_prompt}\n\n"
    "=== TEST DECISIONS (answer_policy — authoritative for choices) ===\n"
    "{test_decisions}\n\n"
    "=== ENVIRONMENT FACTS (pre-existing project at app_path, before install agent) ===\n"
    "{environment_facts}\n\n"
    "=== INSTALLATION AGENT WORK SO FAR (what the install LLM reported doing) ===\n"
    "{agent_work_summary}\n\n"
    "=== PRIOR Q&A IN THIS RUN ===\n"
    "{prior_answers}\n\n"
    "=== DETECTED TOPIC (regex hint — always read full question for intent) ===\n"
    "{category_hint}\n\n"
    "=== QUESTION ===\n"
    "{question}\n\n"
    "Rules:\n"
    "- CHOICES (ATT, CUID, deeplink setup, dependency manager, event method): follow TEST DECISIONS.\n"
    "- PRE-EXISTING project structure (SceneDelegate file, Podfile, Swift version): use ENVIRONMENT FACTS.\n"
    "- What was ALREADY DONE in this run (SDK step completed, deeplink added): use INSTALLATION AGENT WORK.\n"
    "- Distinguish intent:\n"
    "  * 'already installed / already use / already have / כבר מותקן' → agent work + policy sdk_already_integrated + environment\n"
    "  * 'do you want / should I add / support / configure' → TEST DECISIONS\n"
    "- Stay consistent with PRIOR Q&A.\n"
    "- Do NOT infer AppsFlyer SDK install status from project file scan.\n"
    "- If (yes/no) expected → reply yes or no only; if (true/false) → True or False only.\n"
)
