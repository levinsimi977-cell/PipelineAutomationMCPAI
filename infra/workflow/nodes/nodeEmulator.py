from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

from infra.agents.sdkAgent.tools.emulator import (
    setup_appium_environment,
    start_appium_server,
    list_devices,
    start_device,
    install_app_on_device,
    launch_app_on_device,
    ensure_device_running,
    get_connected_device_id,
    run_basic_navigation_smoke,
)

if TYPE_CHECKING:
    from infra.workflow.workflow_nodes import PipelineState


def _resolve_built_artifact_path(state: "PipelineState") -> str | None:
    """
    Reads the APK/`.app` bundle path `compilation_check_node` discovered
    after a successful build (`CompilationResult.extra["apk_path"]` /
    `["app_bundle_path"]`). Handles both the live `CompilationResult`
    dataclass and a plain dict (e.g. after a checkpoint round-trip).
    """
    compilation_result = state.get("compilation_result")
    if compilation_result is None:
        return None
    extra = getattr(compilation_result, "extra", None)
    if extra is None and isinstance(compilation_result, dict):
        extra = compilation_result.get("extra")
    if not isinstance(extra, dict):
        return None
    return extra.get("apk_path") or extra.get("app_bundle_path")


_ANDROID_NS = "http://schemas.android.com/apk/res/android"


def _find_launcher_activity(apk_path: str) -> str | None:
    """Reads the app's AndroidManifest.xml (next to the built APK, at
    <project_root>/app/src/main/AndroidManifest.xml) and returns the
    activity carrying the MAIN/LAUNCHER intent-filter.

    Passed as `appActivity` so Appium doesn't have to resolve it itself via
    ADB right after install -- that lookup is flaky (can fail with "Unable
    to resolve the launchable activity") on a freshly installed package.
    Best-effort: returns None on any error, and launch_app_on_device falls
    back to Appium's own resolution in that case.
    """
    try:
        # apk_path: <project_root>/app/build/outputs/apk/debug/app-debug.apk
        project_root = Path(apk_path).parents[5]
        manifest_path = project_root / "app" / "src" / "main" / "AndroidManifest.xml"
        root = ET.parse(manifest_path).getroot()
        for activity in root.iter("activity"):
            for intent_filter in activity.findall("intent-filter"):
                actions = {a.get(f"{{{_ANDROID_NS}}}name") for a in intent_filter.findall("action")}
                categories = {c.get(f"{{{_ANDROID_NS}}}name") for c in intent_filter.findall("category")}
                if "android.intent.action.MAIN" in actions and "android.intent.category.LAUNCHER" in categories:
                    return activity.get(f"{{{_ANDROID_NS}}}name")
    except Exception:
        pass
    return None


def emulator_node(state: PipelineState) -> dict:
    """Node 7: Emulator — launch compiled app (G5)

    Runs the full launch sequence in a single call:
      1. setup_appium_environment  — install Appium + platform driver
      2. start_appium_server       — start Appium server on port 4723
      3. start_device(device_id)   — boot the target device / simulator; or,
                                      if none is configured, use whatever's
                                      already running, or auto-boot the
                                      first installed AVD/simulator
      4. install_app_on_device     — install the APK/.app the compilation
                                      step just built (if not already on
                                      the device), so there's something for
                                      the next step to activate
      5. launch_app_on_device      — connect Appium and activate the app
      6. run_basic_navigation_smoke — optional (see
                                      answer_policy.verify_sdk.validate_basic_navigation
                                      below); taps a few on-screen buttons
                                      and confirms the app stays responsive

    Required state keys:
      - os_type        : "android" or "ios"
      - app_identifier : package name (Android) or bundle ID (iOS)

    Optional state keys:
      - device_id      : AVD name (Android) or simulator UUID (iOS) to
                         boot. When absent, falls back to whatever
                         device/simulator is already running, auto-booting
                         one if nothing is.
      - device_boot_timeout_seconds : how long to wait for an auto-booted
                         device to come online (default: 180 -- a real
                         local Android emulator cold boot commonly takes
                         well over a minute, especially without hardware
                         acceleration on a loaded dev machine, so 90s was
                         cutting it close and produced false "not
                         installed" reports for AVDs that just hadn't
                         finished booting yet).
      - remote_url     : Appium server URL (default: http://127.0.0.1:4723)
      - answer_policy.verify_sdk.validate_basic_navigation : when true and
                         the app launched successfully, runs a best-effort
                         navigation smoke test and fails the run
                         (test_status="FAIL") if the app becomes
                         unresponsive while being tapped.

    Returns only the fields that changed so LangGraph can merge them into
    the shared graph state — including `device_id`, so a device found via
    auto-detection is available to later nodes (e.g. deep_link).
    """
    configured_device_id = state.get("device_id")
    os_type = state.get("platform")
    app_identifier = state.get("app_id")
    remote_url = state.get("remote_url", "http://127.0.0.1:4723")

    steps: list[str] = []
    driver_instance: Any = None
    devices_listing: str = ""
    device_id = configured_device_id
    install_result: str | None = None
    launch_result: str | None = None
    boot_result: str | None = None

    try:
        # Step 1 — install Appium + platform driver
        steps.append(f"[setup] {setup_appium_environment()}")

        # Step 2 — start Appium server
        steps.append(f"[server] {start_appium_server()}")

        # Step 3 — list available devices/simulators on the current platform
        devices_listing = list_devices()
        steps.append(f"[devices] {devices_listing}")

        # Step 4 — boot the specific device, or fall back to whatever's
        # already running, or boot one automatically. Many use cases (esp.
        # "common" ones with no answer_policy.android.device_id) don't
        # configure a device at all. Without this fallback, the pipeline
        # used to silently skip booting anything and only fail much later,
        # deep in sdk_agent's verify_prompt, with an opaque "no devices
        # connected" error from fetchLogs/verifySdk -- and even just
        # falling back to an already-running device still required the
        # user to manually start one first every time. ensure_device_running
        # boots the first installed AVD/simulator itself when nothing is
        # already up, and waits for it to come online.
        if configured_device_id:
            steps.append(f"[device] {start_device(configured_device_id)}")
        else:
            # ensure_device_running now returns (device_id, diagnostic) instead of a bare
            # device_id -- a bare None couldn't tell "no AVD/simulator installed at all" apart
            # from "one was found but never came up in time", so the old hardcoded message here
            # used to claim "no AVD/simulator is installed" even when list_devices() (above) had
            # just listed several. boot_result carries the real reason through to nodes_log.
            device_id, boot_result = ensure_device_running(
                # 180s wasn't always enough for a genuine cold boot (no
                # Quick Boot snapshot, no hardware acceleration) -- 300s
                # gives it real room before giving up.
                timeout_seconds=state.get("device_boot_timeout_seconds", 300)
            )
            if device_id:
                steps.append(f"[device] No device_id configured; using device/simulator: {device_id}")
            else:
                steps.append(f"[device] Skipped: no device_id configured. {boot_result}")

        # Step 4b — resolve the *real* adb serial for Android. A configured
        # device_id is often an AVD name (e.g. "Pixel_8a") or a guessed
        # serial, and start_android_emulator() returns immediately after a
        # fixed sleep without confirming boot actually finished -- `adb -s`
        # only works with the true serial (e.g. "emulator-5554"), never an
        # AVD name. Poll for it instead of trusting device_id as-is.
        if device_id and (os_type or "").lower() == "android":
            resolved = get_connected_device_id()
            deadline = time.time() + state.get("device_boot_timeout_seconds", 300)
            while not resolved and time.time() < deadline:
                time.sleep(3)
                resolved = get_connected_device_id()
            if resolved and resolved != device_id:
                steps.append(f"[device] Resolved real adb serial: {resolved} (was {device_id!r}).")
                device_id = resolved
            elif not resolved:
                steps.append(f"[device] '{device_id}' never became visible to adb.")

        # Step 5 — install the freshly built APK/.app (if any) onto the
        # device before trying to activate it. Without this, a device that
        # doesn't already have the app installed (e.g. one that was just
        # auto-booted, or any other clean device/simulator) is left sitting
        # on its home screen: launch_app_on_device's activate_app has
        # nothing installed to bring to the foreground.
        artifact_path = _resolve_built_artifact_path(state)
        if device_id and artifact_path:
            install_result = install_app_on_device(os_type, device_id, artifact_path)
            steps.append(f"[install] {install_result}")

        # Step 6 — connect Appium and launch the app
        if not device_id:
            steps.append("[launch] Skipped: no device available.")
        elif not all([os_type, app_identifier]):
            steps.append("[launch] Skipped: os_type or app_identifier is missing from state.")
        else:
            app_activity = (
                _find_launcher_activity(artifact_path)
                if artifact_path and (os_type or "").lower() == "android"
                else None
            )
            driver_result = launch_app_on_device(os_type, device_id, app_identifier, remote_url, app_activity)
            if isinstance(driver_result, str):
                launch_result = driver_result
                steps.append(f"[launch] {driver_result}")
            else:
                driver_instance = driver_result
                launch_result = "App launched successfully, driver is ready."
                steps.append(f"[launch] {launch_result}")

    except Exception as e:
        steps.append(f"[error] Node execution failed: {str(e)}")

    if driver_instance is not None:
        node_status = "Success"
    elif device_id:
        node_status = "Fail"
    else:
        node_status = "Skipped"

    nodes_log = [
        *(state.get("nodes_log") or []),
        # Every other pipeline node appends to nodes_log -- this one didn't,
        # so the Emulator step always showed up as "Not executed" in
        # workflow-status summaries even when it clearly ran (and even when
        # it's the reason a later step, e.g. verify_prompt's fetchLogs,
        # fails with "no devices connected"). Surface the outcome here,
        # right where it happens, instead of only reverse-engineerable from
        # a downstream failure several nodes later.
        #
        # install_result/launch_result/steps carry the *actual* strings
        # install_app_on_device()/launch_app_on_device() returned (e.g. the
        # real adb/Appium error), not just the device_id/app_launched
        # booleans below -- those alone can't tell you *why* a launch
        # failed, and build_report.py's HTML report is built entirely from
        # nodes_log (it never reads execution_result), while the sandbox
        # dir and data/runs/<run_id>/ (incl. audit.jsonl) are both deleted
        # shortly after the report is built. Without this, that reason is
        # gone forever once the run finishes.
        {
            "node": "emulator",
            "status": node_status,
            "details": {
                "device_id": device_id,
                "app_launched": driver_instance is not None,
                "boot_result": boot_result,
                "install_result": install_result,
                "launch_result": launch_result,
                "steps": steps,
            },
        },
    ]

    result: dict[str, Any] = {
        "available_devices": devices_listing,
        "execution_result": "\n".join(steps),
        "driver": driver_instance,
        "device_id": device_id,
        # build_report.py only shows this node as "Visited" via an explicit
        # "{node}_is_visited" state key, or by falling back to the node's
        # own log entries -- but that fallback counts as visited only when
        # the aggregate status is "passed", so a *failed* emulator run used
        # to be reported as "Visited: No" despite clearly having run.
        "emulator_is_visited": True,
    }

    # A configured/resolved device_id but no working driver means install
    # and/or launch genuinely failed (not just "nothing configured") --
    # stop the run here instead of silently continuing to the next stage
    # as if the app were actually running on a device.
    if node_status == "Fail":
        result["test_status"] = "FAIL"
        # Without this, the report's error box fell back to showing the
        # (unrelated, often successful) compilation log instead of the real
        # reason the run failed, since fail_reason was never populated here.
        result["fail_reason"] = launch_result or install_result or "Emulator failed to launch the app."

    # Step 7 — optional navigation smoke test. The sdk_agent has no tools to
    # build/launch/tap the app itself (see sdk-agent-main-rules.json rule
    # 15), so any use case that actually wants "navigation" verified
    # (answer_policy.verify_sdk.validate_basic_navigation) needs the
    # pipeline itself to do it -- right here, while we still hold the
    # freshly launched driver.
    validate_navigation = (
        (state.get("answer_policy") or {}).get("verify_sdk") or {}
    ).get("validate_basic_navigation")
    if driver_instance is not None and validate_navigation:
        smoke_result = run_basic_navigation_smoke(driver_instance, os_type)
        nodes_log.append({
            "node": "navigation_smoke",
            "status": smoke_result["status"],
            "details": smoke_result,
        })
        if smoke_result["status"] == "Fail":
            result["test_status"] = "FAIL"

    result["nodes_log"] = nodes_log
    return result


def route_from_emulator(state: PipelineState) -> str:
    """Conditional edge out of `emulator`.

    - test_status == "FAIL" (emulator itself failed to launch the app)   -> test_runner
    - prompt_just_run == "integrate_prompt"                       -> sdk_agent
    - prompt_just_run == "event_prompt" and visited_user_actions  -> sdk_agent
    - prompt_just_run == "event_prompt" and not visited_user_actions -> user_actions

    Deliberately reads `prompt_just_run` (the phase that just completed),
    NOT `last_prompt_type`: sdk_agent_node advances `last_prompt_type` to the
    *next* phase as soon as the current one succeeds — before
    compilation_check/emulator even run for the phase that just finished.
    Checking `last_prompt_type == "event_prompt"` here would therefore be
    true one phase too early (right after integrate_prompt, before
    event_prompt — the phase that actually wires the in-app event via
    write_events_manifest — has run at all), sending the pipeline to
    user_actions before there's anything for it to discover/tap.
    """
    if state.get("test_status") == "FAIL":
        return "test_runner"
    if (
        state.get("prompt_just_run") == "event_prompt"
        and not state.get("visited_user_actions", False)
    ):
        return "user_actions"
    return "sdk_agent"
