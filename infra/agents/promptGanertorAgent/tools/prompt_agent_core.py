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


def _resolve_current_use_case(state: dict) -> dict:
    """Pick the active use case from official state field selected_use_cases.

    Uses current_use_case_path (when looping) to choose the matching item;
    otherwise returns the first selected use case.
    """
    selected = state.get("selected_use_cases") or []
    if not selected:
        raise ValueError(
            "Prompt Agent expected state['selected_use_cases'] "
            "(list of use-case dicts) to be set by the workflow."
        )

    current_path = state.get("current_use_case_path")
    if current_path:
        stem = Path(str(current_path)).stem
        for case in selected:
            if not isinstance(case, dict):
                continue
            if str(case.get("id", "")) == stem:
                return case
        if stem.isdigit():
            index = int(stem)
            if 0 <= index < len(selected) and isinstance(selected[index], dict):
                return selected[index]

    first = selected[0]
    if not isinstance(first, dict):
        raise ValueError("state['selected_use_cases'][0] must be a use-case dict.")
    return first


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


def prompt_agent_node(state: dict) -> dict:
    # מקור האמת: selected_use_cases (+ current_use_case_path לבחירת הפעיל).
    # answer_policy ברמת state (אם קיים) גובר על זה שבתוך ה-use case.
    use_case = _resolve_current_use_case(state)

    platform = use_case.get("platform") or "android"
    app_path = use_case.get("app_path")
    raw_goal = use_case.get("prompt_goal") or use_case.get("prompt") or ""
    answer_policy = state.get("answer_policy") or use_case.get("answer_policy") or {}

    missing = [
        name
        for name, value in (
            ("platform", platform),
            ("app_path", app_path),
            ("prompt_goal", raw_goal),
            ("answer_policy", answer_policy if answer_policy else None),
        )
        if value is None or value == ""
    ]
    if missing:
        raise ValueError(
            "Prompt Agent: active use case in state['selected_use_cases'] "
            f"is missing required fields: {', '.join(missing)}"
        )

    prompt_generator_chain = BASE_PROMPT_TEMPLATE | llm | StrOutputParser()
    generated_prompts = {}

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

    return {
        "agent_prompts": generated_prompts,
        "platform": platform,
    }
