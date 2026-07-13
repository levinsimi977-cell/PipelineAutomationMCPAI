from __future__ import annotations

import json
import os
from typing import Literal, Optional, TypedDict, get_args



PromptType = Literal["integrate_prompt", "event_prompt", "verify_prompt"]


class PipelineState(TypedDict, total=False):
    """Shared state threaded through every node of the workflow graph."""

    visited_user_actions: bool
    last_prompt_type: PromptType

    # Use-case queue (json_use_case_input -> artifact_generator <-> visual_report loop)
    run_id: str
    selected_use_cases: list[dict]
    use_cases_dir: str
    current_use_case_path: Optional[str]
    current_use_case: dict


def json_use_case_input_node(state: PipelineState) -> PipelineState:
    """Node 1: JSON Use Case Input — User enters use cases (G2)

    Takes the use cases the user picked (`state["selected_use_cases"]`, a
    list of use-case dicts) and materializes each one as its own JSON file
    inside a fresh `use_cases_dir` folder. `current_use_case_path` is set to
    the first file so the loop (`artifact_generator` <-> `visual_report`,
    see `route_from_visual_report`) knows which case to run next.
    """
    selected_cases = state.get("selected_use_cases") or []
    run_id = state.get("run_id", "run")

    use_cases_dir = os.path.join("data", "runs", run_id, "use_cases")
    os.makedirs(use_cases_dir, exist_ok=True)

    case_paths = []
    for index, case in enumerate(selected_cases):
        case_id = case.get("id", str(index))
        case_path = os.path.join(use_cases_dir, f"{case_id}.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False, indent=2)
        case_paths.append(case_path)

    state["use_cases_dir"] = use_cases_dir
    state["current_use_case_path"] = case_paths[0] if case_paths else None
    return state


def artifact_generator_node(state: PipelineState) -> PipelineState:
    """Node 2: Artifact Generator — Prompt + RULES + TEST files (G2)

    Loads the use case currently pointed to by `current_use_case_path`
    (set by `json_use_case_input_node` on the first pass, or refreshed by
    `visual_report_node` on every following loop) into `current_use_case`
    so the rest of the pipeline works off the active case's data.
    """
    current_path = state.get("current_use_case_path")
    if current_path and os.path.exists(current_path):
        with open(current_path, "r", encoding="utf-8") as f:
            state["current_use_case"] = json.load(f)
    return state


def environment_setup_node(state: PipelineState) -> PipelineState:
    """Node 3: Environment Setup — Sandbox + MCP health check (G1)"""
    return state




def sdk_agent_node(state: PipelineState) -> PipelineState:
    """Node 5: SDK Agent — single node, revisited on every loop of the
    workflow (integration / event / verify passes).

    `state["last_prompt_type"]` tells this node which prompt is being sent
    for the current pass; `route_from_sdk_agent` reads it right after this
    node runs to decide whether to go build+run again or jump to the final
    test run.
    """
    return state


def compilation_check_node(state: PipelineState) -> PipelineState:
    """Node 6: Compilation Check — build validation before run (G4)"""
    return state


def emulator_node(state: PipelineState) -> PipelineState:
    """Node 7: Emulator — launch compiled app (G5)"""
    return state


def user_actions_node(state: PipelineState) -> PipelineState:
    """Node 8: User Actions — simulated taps on screen (G5)"""
    state["visited_user_actions"] = True
    return state


def deep_link_node(state: PipelineState) -> PipelineState:
    """Node 9: Deep Link — verify deep link behavior (G5)"""
    return state


def test_runner_node(state: PipelineState) -> PipelineState:
    """Node 10: Test Runner — full test suite execution (G2)"""
    return state


def visual_report_node(state: PipelineState) -> PipelineState:
    """Node 11: Visual Report — HTML audit dashboard (G2/4)

    Once the report for the current use case is done, checks whether more
    use-case files are still waiting in `use_cases_dir`:

    - If there are more files -> delete the file just used and point
      `current_use_case_path` at another remaining file, so the graph loops
      back into `artifact_generator` (see `route_from_visual_report`).
    - If it was the last file -> leave it in place and clear
      `current_use_case_path`, so the graph ends.
    """
    use_cases_dir = state.get("use_cases_dir")
    current_path = state.get("current_use_case_path")

    if use_cases_dir and current_path and os.path.isdir(use_cases_dir):
        remaining = sorted(
            os.path.join(use_cases_dir, name)
            for name in os.listdir(use_cases_dir)
            if name.endswith(".json")
            and os.path.join(use_cases_dir, name) != current_path
        )
        if remaining:
            if os.path.exists(current_path):
                os.remove(current_path)
            state["current_use_case_path"] = remaining[0]
        else:
            state["current_use_case_path"] = None

    return state


def route_from_sdk_agent(state: PipelineState) -> str:
    """Conditional edge out of `sdk_agent`.

    - last_prompt_type == "verify_prompt"                     -> test_runner
    - last_prompt_type in {"integrate_prompt", "event_prompt"} -> compilation_check
    """
    if state["last_prompt_type"] == "verify_prompt":
        return "test_runner"
    return "compilation_check"


def route_from_emulator(state: PipelineState) -> str:
    """Conditional edge out of `emulator`.

    - last_prompt_type == "integrate_prompt"                       -> sdk_agent
    - last_prompt_type == "event_prompt" and visited_user_actions  -> sdk_agent
    - last_prompt_type == "event_prompt" and not visited_user_actions -> user_actions
    """
    if state["last_prompt_type"] == "event_prompt" and not state["visited_user_actions"]:
        return "user_actions"
    return "sdk_agent"


def route_from_visual_report(state: PipelineState) -> str:
    """Conditional edge out of `visual_report`.

    - `current_use_case_path` still set -> another use case is waiting in
      `use_cases_dir`, loop back to `artifact_generator`.
    - `current_use_case_path` is empty/None -> no use cases left, end the run.
    """
    if state.get("current_use_case_path"):
        return "artifact_generator"
    return "end"
