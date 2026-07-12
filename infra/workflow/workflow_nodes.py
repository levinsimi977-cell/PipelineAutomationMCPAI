import json
import os
from pathlib import Path

from infra.application.app import run_tasks_3_and_4, setup_environment


def json_use_case_input_node(state: PipelineState) -> PipelineState:
    """Node 1: JSON Use Case Input — User enters use cases (G2)"""
    return state


def artifact_generator_node(state: PipelineState) -> PipelineState:
    """Node 2: Artifact Generator — Prompt + RULES + TEST files (G2)"""
    return state


async def environment_setup_node(state: PipelineState) -> PipelineState:
    """
    Node 3: Environment Setup — Sandbox + MCP health check (G1)

    Responsibilities:
    1. Create an isolated sandbox copy of the selected application.
    2. Check that the AppsFlyer MCP server is alive.
    3. Validate that the sandbox contains a valid mobile application.
    4. Save all results back into the pipeline state.
    """

    # Step 1: Create the sandbox environment
    environment_result = setup_environment(dict(state))

    if environment_result.get("test_status") == "FAIL":
        return {
            **state,
            **environment_result,
            "environment_setup_status": "FAILED",
        }

    sandbox_path = environment_result.get("sandbox_path")

    if not sandbox_path:
        return {
            **state,
            **environment_result,
            "test_status": "FAIL",
            "environment_setup_status": "FAILED",
            "error_reason": "Environment setup did not return a sandbox_path.",
        }

    # Step 2: MCP check + application validation
    checks_result = await run_tasks_3_and_4(
        app_path=Path(sandbox_path),
        workdir=Path(sandbox_path),
        run_build_check=bool(state.get("run_build_check", False)),
    )

    # Step 3: Determine final status
    checks_succeeded = checks_result.get("status") == "OK"

    final_test_status = "READY" if checks_succeeded else "FAIL"
    environment_setup_status = "OK" if checks_succeeded else "FAILED"

    # Step 4: Merge everything into the existing state
    return {
        **state,
        **environment_result,

        "app_path": sandbox_path,
        "sandbox_path": sandbox_path,

        "environment_setup_status": environment_setup_status,
        "test_status": final_test_status,

        "task_3_mcp_alive": checks_result.get("task_3_mcp_alive"),
        "task_4_application_validation": checks_result.get(
            "task_4_application_validation"
        ),

        "environment_setup_result": checks_result,
    }


def prompt_agent_node(state: PipelineState) -> PipelineState:
    """Node 4: Prompt Agent — enriched structured prompt (G3)"""
    return state


def sdk_agent_integration_node(state: PipelineState) -> PipelineState:
    """Node 5: SDK Agent #1 — Integration via MCP tools (G3)"""
    return state


def compilation_check_node(state: PipelineState) -> PipelineState:
    """Node 6: Compilation Check — build validation before run (G4)"""
    return state


def emulator_node(state: PipelineState) -> PipelineState:
    """Node 7: Emulator — launch compiled app (G5)"""
    return state


def user_actions_node(state: PipelineState) -> PipelineState:
    """Node 8: User Actions — simulated taps on screen (G5)"""
    return state


def deep_link_node(state: PipelineState) -> PipelineState:
    """Node 9: Deep Link — verify deep link behavior (G5)"""
    return state



def test_runner_node(state: PipelineState) -> PipelineState:
    """Node 11: Test Runner — full test suite execution (G2)"""
    return state


def visual_report_node(state: PipelineState) -> PipelineState:
    """Node 12: Visual Report — HTML audit dashboard (G2/4)"""
    return state