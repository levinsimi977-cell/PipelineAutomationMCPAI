import os
import json
from pathlib import Path

from infra.load_env import load_project_env

load_project_env()

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

from infra.use_case_service.repositories.run_repository import (
    RUNS_DIR,
    load_selected_use_cases,
)

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
    "- The agent MUST NOT invent AppsFlyer SDK setup or verification steps that are not returned by MCP, "
    "but MAY mechanically translate MCP-provided code snippets into the project's actual programming "
    "language (e.g. Swift to Objective-C) as long as the same APIs, parameters, and logic are preserved.\n"
    "- If the MCP tool is unavailable or fails, the agent must report failure instead of guessing.\n"
    "- Whatever output format you require from the agent (plain text, a structured report, or an exact "
    "JSON schema), the generated prompt MUST still instruct the agent to end its final response with a "
    "separate line reading exactly `STATUS: SUCCESS`, `STATUS: FAILURE`, or `STATUS: QUESTION`. This line "
    "is mandatory pipeline instrumentation, is ADDITIONAL to any other required output, and must never be "
    "excluded by an instruction such as \"return ONLY this JSON\" or \"no additional commentary\" — if you "
    "write such an instruction, explicitly carve out the STATUS line as the one exception to it. Without "
    "this line the pipeline cannot detect that the agent is done and will keep re-prompting it until it "
    "times out.\n\n"
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


def _pick_from_selected(selected: list, current_path) -> dict | None:
    """Choose the active use case from a list using current_use_case_path stem."""
    if not selected:
        return None

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
    return first if isinstance(first, dict) else None


def _path_under_run_dir(path: Path, run_id: str) -> bool:
    """True only if path resolves inside data/runs/<run_id>/ (this run only)."""
    try:
        path.resolve().relative_to((RUNS_DIR / str(run_id)).resolve())
        return True
    except (ValueError, OSError):
        return False


def _load_use_case_from_path(file_path: Path) -> dict:
    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and not data.get("useCases"):
        if "id" not in data:
            data = {**data, "id": file_path.stem}
        return data

    use_cases = data.get("useCases") if isinstance(data, dict) else data
    if not use_cases:
        raise ValueError(f"No use cases found in: {file_path}")

    first_case = use_cases[0]
    if isinstance(first_case, dict) and first_case.get("path") and not first_case.get("prompt_goal"):
        case_path = (file_path.parent / first_case["path"]).resolve()
        with case_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict) and "id" not in payload:
            payload = {**payload, "id": case_path.stem}
        return payload

    if isinstance(first_case, dict) and "id" not in first_case:
        first_case = {**first_case, "id": file_path.stem}
    return first_case


def _load_selected_from_disk(run_id: str) -> list[dict]:
    """Load selected use cases only from data/runs/<run_id>/ (never other runs)."""
    return load_selected_use_cases(str(run_id))


def _resolve_current_use_case(state: dict) -> dict:
    """Resolve active use case: state memory first, then data/runs/<run_id> only.

    Order:
    1. state['selected_use_cases'] (this run's in-memory list)
    2. state['current_use_case_path'] if it points inside data/runs/<run_id>/
    3. load_selected_use_cases(run_id) from data/runs/<run_id>/
    """
    current_path = state.get("current_use_case_path")
    run_id = state.get("run_id")

    selected = state.get("selected_use_cases") or []
    picked = _pick_from_selected(selected, current_path)
    if picked is not None:
        return picked

    if not run_id:
        raise ValueError(
            "Prompt Agent: state['selected_use_cases'] is empty and state['run_id'] "
            "is missing — cannot fall back to data/runs/<run_id>/."
        )

    # Active file for this run only (written by json_use_case_input under use_cases/).
    if current_path:
        path = Path(str(current_path))
        if path.is_file() and _path_under_run_dir(path, str(run_id)):
            return _load_use_case_from_path(path)

    try:
        disk_selected = _load_selected_from_disk(str(run_id))
    except Exception as exc:
        raise ValueError(
            "Prompt Agent: state['selected_use_cases'] is empty and failed to load "
            f"from data/runs/{run_id}/: {exc}"
        ) from exc

    picked = _pick_from_selected(disk_selected, current_path)
    if picked is None:
        raise ValueError(
            f"Prompt Agent: no use cases found in state or data/runs/{run_id}/."
        )
    return picked


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
    # Prefer current_use_case already set by artifact_generator; otherwise resolve
    # from selected_use_cases / data/runs/<run_id>/. Prefer sandbox app_path so
    # generated prompts match the SDK Agent workdir.
    use_case = state.get("current_use_case")
    if not isinstance(use_case, dict):
        use_case = _resolve_current_use_case(state)

    platform = (
        state.get("platform")
        or use_case.get("platform")
        or "android"
    )
    # After environment_setup, state["app_path"] / sandbox_path point at the
    # live sandbox copy. Prefer those over the catalog path in the use case.
    app_path = (
        state.get("sandbox_path")
        or state.get("app_path")
        or use_case.get("app_path")
    )
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
            "Prompt Agent: active use case is missing required fields: "
            f"{', '.join(missing)}"
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
