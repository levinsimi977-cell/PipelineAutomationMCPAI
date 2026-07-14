import os
import json
from pathlib import Path

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

# הגדרת המודל - שימוש במשתני סביבה לאבטחה
llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL", "gpt-5.1"),
    temperature=0.1,
    api_key=os.getenv("OPENAI_API_KEY") or os.getenv("GPT_API_KEY"),
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
RUNS_DIR = PROJECT_ROOT / "data" / "runs"
SELECTED_USE_CASES_FILENAME = "selected_use_cases.json"

BASE_PROMPT_TEMPLATE = PromptTemplate.from_template(
    "You are an expert technical prompt engineer.\n"
    "Create the final execution prompt for an AI SDK integration agent.\n"
    "The output must be written in English and must be direct, technical, and executable.\n\n"
    "Prompt type: {prompt_type}\n"
    "Main goal: {stage_goal}\n\n"
    "Use case goal:\n{goal}\n\n"
    "Execution context:\n"
    "- Platform: {platform}\n"
    "- App path: {app_path}\n\n"
    "Relevant use-case data for this step:\n{use_case_data}\n\n"
    "Non-negotiable MCP requirements:\n"
    "- The agent MUST use AppsFlyer MCP tools whenever SDK guidance or validation is needed.\n"
    "- The agent MUST inspect the project files before editing them.\n"
    "- The agent MUST NOT answer from memory instead of calling MCP tools.\n"
    "- The agent MUST NOT invent AppsFlyer SDK setup or verification steps that are not returned by MCP.\n"
    "- If the MCP tool is unavailable or fails, the agent must report failure instead of guessing.\n\n"
    "Stage-specific instructions:\n{stage_instructions}\n\n"
    "Create one final prompt for the SDK agent now."
)

STAGE_CONFIG = {
    "integrate_prompt": {
        "goal": "Install and integrate the AppsFlyer SDK only.",
        "policy_keys": ("ios_minimal", "android"),
        "instructions": (
            "- Use the AppsFlyer MCP tool `integrateSdk` for SDK integration.\n"
            "- Apply all SDK integration requirements that are relevant to this use case.\n"
            "- Do not create in-app events in this step unless the SDK integration tool explicitly requires setup boilerplate.\n"
            "- Do not run final verification in this step."
        ),
    },
    "event_prompt": {
        "goal": "Add or validate event/deep-link behavior according to the use case.",
        "policy_keys": ("in_app_event", "deeplink"),
        "instructions": (
            "- If an in-app event is defined, guide the agent to create or connect that event in the app flow.\n"
            "- If the event already exists, guide the agent to validate that it is emitted with the expected name and parameters.\n"
            "- If no in-app event is defined, tell the agent not to invent one.\n"
            "- If deep linking is enabled, guide the agent to prepare or validate the required deep-link behavior.\n"
            "- Do not repeat SDK installation unless the previous integration is missing or broken."
        ),
    },
    "verify_prompt": {
        "goal": "Verify that the use case is completed successfully.",
        "policy_keys": ("verify_sdk", "in_app_event", "deeplink"),
        "instructions": (
            "- Verify that the SDK integration is present and initialized correctly.\n"
            "- Verify SDK logs or readiness signals when requested by the use case.\n"
            "- Verify the in-app event only if one is defined.\n"
            "- Verify deep-link behavior only if deep linking is enabled.\n"
            "- Report clear success or failure evidence. Do not guess."
        ),
    },
}


def _json_text(value) -> str:
    return json.dumps(value or {}, indent=2, ensure_ascii=False, default=str)


def _resolve_selected_cases_path(state: dict) -> Path:
    selected_cases_path = state.get("selected_use_cases_path")
    if selected_cases_path:
        return Path(selected_cases_path)

    run_id = state.get("run_id")
    if run_id:
        return RUNS_DIR / str(run_id) / SELECTED_USE_CASES_FILENAME

    raise ValueError(
        "Missing selected use case path. Expected state['selected_use_cases_path'] "
        "or state['run_id'] to resolve data/runs/<run_id>/selected_use_cases.json."
    )


def _stage_use_case_data(
    prompt_type: str,
    answer_policy: dict,
    platform: str,
    prompt_goal: str = "",
) -> str:
    config = STAGE_CONFIG[prompt_type]
    selected_policy = {
        key: answer_policy.get(key)
        for key in config["policy_keys"]
        if answer_policy.get(key) is not None
    }

    if prompt_type == "integrate_prompt":
        platform_key = "ios_minimal" if str(platform).lower() == "ios" else "android"
        selected_policy = {
            "platform_policy": answer_policy.get(platform_key),
            "full_answer_policy": answer_policy,
        }

    return _json_text({
        "prompt_goal": prompt_goal,
        "answer_policy": selected_policy,
    })


def _load_use_case_from_path(file_path: str) -> dict:
    path = Path(file_path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and not data.get("useCases"):
        return data

    use_cases = data.get("useCases") if isinstance(data, dict) else data
    if not use_cases:
        raise ValueError(f"No use cases found in: {file_path}")

    first_case = use_cases[0]

    # Some selected files may contain catalog entries that point to the real use case.
    if isinstance(first_case, dict) and first_case.get("path") and not first_case.get("prompt_goal"):
        case_path = (path.parent / first_case["path"]).resolve()
        with case_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    return first_case


def _load_fallback_use_case(state: dict) -> dict:
    """Load JSON only when required fields are missing from state.

    Uses only existing state paths:
    - current_use_case_path
    - selected_use_cases_path / run_id -> selected_use_cases.json
    """
    current_path = state.get("current_use_case_path")
    if current_path and os.path.exists(current_path):
        return _load_use_case_from_path(str(current_path))

    selected_cases_path = _resolve_selected_cases_path(state)
    if not os.path.exists(selected_cases_path):
        raise FileNotFoundError(f"Configuration file not found at: {selected_cases_path}")
    return _load_use_case_from_path(str(selected_cases_path))


def prompt_agent_node(state: dict) -> dict:
    # 1. קודם שולפים מה-state (החברה שמה שם את השדות).
    platform = state.get("platform")
    app_path = state.get("app_path")
    answer_policy = state.get("answer_policy")
    raw_goal = state.get("prompt_goal") or state.get("prompt")

    # 2. אם חסר משהו ב-state — טוענים מקובץ לפי current_use_case_path / selected_use_cases_path
    if not all([platform, app_path, answer_policy is not None, raw_goal]):
        file_case = _load_fallback_use_case(state)
        raw_goal = raw_goal or file_case.get("prompt") or file_case.get("prompt_goal") or ""
        platform = platform or file_case.get("platform", "android")
        app_path = app_path or file_case.get("app_path")
        answer_policy = answer_policy if answer_policy is not None else (file_case.get("answer_policy") or {})
    else:
        answer_policy = answer_policy or {}
        raw_goal = raw_goal or ""

    prompt_generator_chain = BASE_PROMPT_TEMPLATE | llm | StrOutputParser()
    generated_prompts = {}

    # 3. יצירת כל שלושת הפרומפטים. ה-Workflow יחליט בהמשך איזה מהם לשלוח ומתי.
    for prompt_type, stage_config in STAGE_CONFIG.items():
        generated_prompts[prompt_type] = prompt_generator_chain.invoke({
            "prompt_type": prompt_type,
            "stage_goal": stage_config["goal"],
            "goal": raw_goal,
            "platform": platform,
            "app_path": app_path,
            "use_case_data": _stage_use_case_data(
                prompt_type,
                answer_policy,
                platform,
                prompt_goal=raw_goal,
            ),
            "stage_instructions": stage_config["instructions"],
        })

    # החזרת הנתונים ל-Pipeline
    return {
        "agent_prompts": generated_prompts,
        "platform": platform,
    }