from __future__ import annotations

from typing import TYPE_CHECKING, Any

from infra.agents.sdkAgent.tools.emulator import (
    setup_appium_environment,
    start_appium_server,
    list_devices,
    start_device,
    launch_app_on_device,
)

if TYPE_CHECKING:
    from infra.workflow.workflow_nodes import PipelineState


def emulator_node(state: PipelineState) -> dict:
    """Node 7: Emulator — launch compiled app (G5)

    Runs the full launch sequence in a single call:
      1. setup_appium_environment  — install Appium + platform driver
      2. start_appium_server       — start Appium server on port 4723
      3. start_device(device_id)   — boot the target device / simulator
      4. launch_app_on_device      — connect Appium and activate the app

    Required state keys:
      - device_id      : AVD name (Android) or simulator UUID (iOS)
      - os_type        : "android" or "ios"
      - app_identifier : package name (Android) or bundle ID (iOS)

    Optional state keys:
      - remote_url     : Appium server URL (default: http://127.0.0.1:4723)

    Returns only the fields that changed so LangGraph can merge them into
    the shared graph state.
    """
    device_id = state.get("device_id")
    os_type = state.get("platform")
    app_identifier = state.get("app_id")
    remote_url = state.get("remote_url", "http://127.0.0.1:4723")

    steps: list[str] = []
    driver_instance: Any = None
    devices_listing: str = ""

    try:
        # Step 1 — install Appium + platform driver
        steps.append(f"[setup] {setup_appium_environment()}")

        # Step 2 — start Appium server
        steps.append(f"[server] {start_appium_server()}")

        # Step 3 — list available devices/simulators on the current platform
        devices_listing = list_devices()
        steps.append(f"[devices] {devices_listing}")

        # Step 4 — boot the specific device
        if not device_id:
            steps.append("[device] Skipped: device_id is missing from state.")
        else:
            steps.append(f"[device] {start_device(device_id)}")

            # Step 5 — connect Appium and launch the app
            if not all([os_type, app_identifier]):
                steps.append("[launch] Skipped: os_type or app_identifier is missing from state.")
            else:
                driver_result = launch_app_on_device(os_type, device_id, app_identifier, remote_url)
                if isinstance(driver_result, str):
                    steps.append(f"[launch] {driver_result}")
                else:
                    driver_instance = driver_result
                    steps.append("[launch] App launched successfully, driver is ready.")

    except Exception as e:
        steps.append(f"[error] Node execution failed: {str(e)}")

    return {
        "available_devices": devices_listing,
        "execution_result": "\n".join(steps),
        "driver": driver_instance,
    }


def route_from_emulator(state: PipelineState) -> str:
    """Conditional edge out of `emulator`.

    - last_prompt_type == "integrate_prompt"                       -> sdk_agent
    - last_prompt_type == "event_prompt" and visited_user_actions  -> sdk_agent
    - last_prompt_type == "event_prompt" and not visited_user_actions -> user_actions
    """
    if (
        state.get("last_prompt_type") == "event_prompt"
        and not state.get("visited_user_actions", False)
    ):
        return "user_actions"
    return "sdk_agent"
