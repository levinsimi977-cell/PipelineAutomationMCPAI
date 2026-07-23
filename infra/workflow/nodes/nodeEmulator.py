from __future__ import annotations

import subprocess
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
    run_basic_navigation_smoke,
    wait_for_ios_log_marker,
    read_ios_appsflyer_uid,
)

if TYPE_CHECKING:
    from infra.workflow.workflow_nodes import PipelineState


def _read_bundle_id_from_app(app_path: str) -> str | None:
    """Read CFBundleIdentifier from a built .app bundle's Info.plist.

    iOS needs two different IDs: the App Store numeric ID (for AppsFlyer)
    and the Bundle Identifier (for Appium). This reads the latter directly
    from the compiled artifact so it's always accurate.
    """
    plist = Path(app_path) / "Info.plist"
    if not plist.exists():
        return None
    result = subprocess.run(
        ["plutil", "-extract", "CFBundleIdentifier", "raw", str(plist)],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or None


def _collect_ios_sdk_logs(
    sandbox_path: str,
    device_id: str | None = None,
    bundle_id: str | None = None,
    timeout_seconds: float = 45.0,
) -> str | None:
    """Collect AppsFlyer SDK startup/conversion-data lines from the iOS
    simulator's system log, polling until the SDK's start completion handler
    has actually logged something (or `timeout_seconds` elapses).

    verifyIosSdk (unlike verifyIosDeepLink) has no automated log collection
    today -- it still tells the agent to ask the user to paste Xcode debug
    logs into ios-sdk-logs.txt, which never happens in this pipeline, so
    that verify call always reports "log file is empty". This mirrors
    _collect_ios_deeplink_logs in deep_link.py (same log predicate: it
    already covers SDK-start/conversion lines, not just deep-link ones) but
    runs right after launch instead of after a deep link is sent, and
    writes to a different, dedicated file so it doesn't clash with the
    deep-link one collected later.

    A fixed short sleep here used to give up before `startWithCompletionHandler:`
    fired (that also needs a network round-trip), leaving the file looking
    empty even when the SDK started successfully a moment later. Polling for
    the actual "start success"/"start error" marker fixes that.

    verifyIosSdk also specifically looks for a UID/app-ID/IDFV payload as
    proof of a real session -- the SDK agent's own onConversionDataSuccess:
    implementation typically just stores that data instead of logging it, so
    it never appears in the console log no matter how long we wait. When
    `device_id`/`bundle_id` are given, we append the UID AppsFlyerLib already
    persisted on disk (read_ios_appsflyer_uid) so verifyIosSdk has real
    evidence to find, without touching any code the SDK agent wrote.

    Returns the absolute path to the written file, or None if collection
    itself failed (best-effort -- must never fail the emulator node).
    """
    try:
        output = wait_for_ios_log_marker(
            # See the matching comment in _collect_ios_deeplink_logs
            # (deep_link.py) -- subsystem/process alone miss the app's
            # own NSLog() calls (e.g. "AppsFlyer start success: ..." in
            # AppDelegate.m), which run under the app's own process
            # name, not "AppsFlyer". eventMessage[c] catches those too.
            predicate=(
                'subsystem CONTAINS[c] "appsflyer" OR process CONTAINS[c] "appsflyer" '
                'OR eventMessage CONTAINS[c] "appsflyer"'
            ),
            marker_substrings=("[AppsFlyer] start",),
            timeout_seconds=timeout_seconds,
        )

        if device_id and bundle_id:
            uid = read_ios_appsflyer_uid(device_id, bundle_id)
            if uid:
                output += f"\n[pipeline] AppsFlyerUID (read from on-device NSUserDefaults): {uid}\n"

        log_file = Path(sandbox_path) / "ios-sdk-logs.txt"
        log_file.write_text(output, encoding="utf-8")
        return str(log_file)
    except Exception:
        return None


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
    ios_sdk_log_file: str | None = None

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
                timeout_seconds=state.get("device_boot_timeout_seconds", 180)
            )
            if device_id:
                steps.append(f"[device] No device_id configured; using device/simulator: {device_id}")
            else:
                steps.append(f"[device] Skipped: no device_id configured. {boot_result}")

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
            # iOS: Appium needs the Bundle ID (e.g. com.AppsFlyer.KamperDemo),
            # not the App Store numeric ID (e.g. 1512793879) that AppsFlyer uses.
            # Read it from the built .app so it's always correct.
            if os_type == "ios" and artifact_path:
                bundle_id = _read_bundle_id_from_app(artifact_path) or app_identifier
            else:
                bundle_id = app_identifier
            driver_result = launch_app_on_device(os_type, device_id, bundle_id, remote_url)
            if isinstance(driver_result, str):
                launch_result = driver_result
                steps.append(f"[launch] {driver_result}")
            else:
                driver_instance = driver_result
                launch_result = "App launched successfully, driver is ready."
                steps.append(f"[launch] {launch_result}")

                if os_type == "ios":
                    sandbox_path = state.get("sandbox_path") or state.get("app_path")
                    if sandbox_path:
                        ios_sdk_log_file = _collect_ios_sdk_logs(
                            str(sandbox_path), device_id=device_id, bundle_id=bundle_id
                        )
                        if ios_sdk_log_file:
                            steps.append(f"[sdk-logs] Collected to {ios_sdk_log_file}")

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
    }
    if ios_sdk_log_file:
        result["ios_sdk_log_file"] = ios_sdk_log_file

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
    if (
        state.get("prompt_just_run") == "event_prompt"
        and not state.get("visited_user_actions", False)
    ):
        return "user_actions"
    return "sdk_agent"
