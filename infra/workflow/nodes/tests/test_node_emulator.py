"""
Regression tests for infra/workflow/nodes/nodeEmulator.py::route_from_emulator.

Bug this guards against: the routing used to key off `last_prompt_type`,
which sdk_agent_node already advances to the *next* phase as soon as the
current one succeeds. That made the "did event_prompt just run?" check true
one phase too early (right after integrate_prompt succeeds, before
event_prompt ever runs), sending the pipeline to `user_actions` before the
SDK agent had any chance to wire an in-app event / write
events.wired.json -- and never re-checking it once event_prompt actually
completed (since last_prompt_type had moved on to verify_prompt by then).

The fix reads `prompt_just_run` (the phase that just completed) instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import infra.workflow.nodes.nodeEmulator as node_emulator_module
from infra.workflow.nodes.nodeEmulator import emulator_node, route_from_emulator


def test_routes_to_sdk_agent_right_after_integrate_prompt():
    """Right after integrate_prompt succeeds, last_prompt_type has already
    advanced to "event_prompt" -- but event_prompt hasn't run yet, so this
    must NOT go to user_actions."""
    state = {
        "prompt_just_run": "integrate_prompt",
        "last_prompt_type": "event_prompt",
        "visited_user_actions": False,
    }

    assert route_from_emulator(state) == "sdk_agent"


def test_routes_to_user_actions_right_after_event_prompt():
    """Right after event_prompt actually completes, last_prompt_type has
    already advanced to "verify_prompt" -- but prompt_just_run correctly
    still says "event_prompt", so this must go to user_actions."""
    state = {
        "prompt_just_run": "event_prompt",
        "last_prompt_type": "verify_prompt",
        "visited_user_actions": False,
    }

    assert route_from_emulator(state) == "user_actions"


def test_routes_to_sdk_agent_when_user_actions_already_visited():
    state = {
        "prompt_just_run": "event_prompt",
        "last_prompt_type": "verify_prompt",
        "visited_user_actions": True,
    }

    assert route_from_emulator(state) == "sdk_agent"


def test_routes_to_sdk_agent_when_prompt_just_run_missing():
    state = {"last_prompt_type": "event_prompt", "visited_user_actions": False}

    assert route_from_emulator(state) == "sdk_agent"


def _patch_emulator_basics(
    monkeypatch,
    *,
    connected_device_id=None,
    boot_diagnostic="No AVD/simulator is installed to auto-boot.",
):
    monkeypatch.setattr(node_emulator_module, "setup_appium_environment", lambda: "ok")
    monkeypatch.setattr(node_emulator_module, "start_appium_server", lambda: "ok")
    monkeypatch.setattr(node_emulator_module, "list_devices", lambda: "some-devices")
    monkeypatch.setattr(
        node_emulator_module,
        "ensure_device_running",
        lambda timeout_seconds=180: (connected_device_id, boot_diagnostic),
    )


def test_emulator_node_boots_configured_device_without_auto_detect(monkeypatch):
    """
    Regression test: when a use case explicitly configures device_id (e.g.
    answer_policy.android.device_id), that AVD/UUID must be booted via
    start_device -- auto-detection must not be consulted at all.
    """
    _patch_emulator_basics(monkeypatch, connected_device_id="should-not-be-used")
    start_device_calls = []
    monkeypatch.setattr(
        node_emulator_module, "start_device", lambda device_id: start_device_calls.append(device_id) or "booted"
    )
    fake_driver = object()
    monkeypatch.setattr(
        node_emulator_module,
        "launch_app_on_device",
        lambda os_type, device_id, app_identifier, remote_url: fake_driver,
    )

    result = emulator_node({"device_id": "my-avd", "platform": "android", "app_id": "com.example.app"})

    assert start_device_calls == ["my-avd"]
    assert result["device_id"] == "my-avd"
    assert result["nodes_log"][-1] == {
        "node": "emulator",
        "status": "Success",
        "details": {
            "device_id": "my-avd",
            "app_launched": True,
            "boot_result": None,
            "install_result": None,
            "launch_result": "App launched successfully, driver is ready.",
            "steps": [
                "[setup] ok",
                "[server] ok",
                "[devices] some-devices",
                "[device] booted",
                "[launch] App launched successfully, driver is ready.",
            ],
        },
    }


def test_emulator_node_falls_back_to_connected_device_when_unconfigured(monkeypatch):
    """
    Regression test: use cases with no device_id configured at all (e.g.
    answer_policy.android is null) must fall back to whatever
    device/simulator is already running, instead of silently skipping
    device setup and only failing much later in sdk_agent's verify_prompt.
    """
    _patch_emulator_basics(monkeypatch, connected_device_id="emulator-5554")
    monkeypatch.setattr(
        node_emulator_module, "start_device", lambda device_id: pytest_fail_if_called()
    )
    launch_calls = []
    fake_driver = object()

    def _fake_launch(os_type, device_id, app_identifier, remote_url):
        launch_calls.append(device_id)
        return fake_driver

    monkeypatch.setattr(node_emulator_module, "launch_app_on_device", _fake_launch)

    result = emulator_node({"platform": "android", "app_id": "com.example.app"})

    assert launch_calls == ["emulator-5554"]
    assert result["device_id"] == "emulator-5554"
    assert result["nodes_log"][-1]["status"] == "Success"
    assert result["nodes_log"][-1]["details"]["device_id"] == "emulator-5554"
    assert result["nodes_log"][-1]["details"]["launch_result"] == "App launched successfully, driver is ready."


def test_emulator_node_skips_launch_when_no_device_available(monkeypatch):
    _patch_emulator_basics(monkeypatch, connected_device_id=None)

    result = emulator_node({"platform": "android", "app_id": "com.example.app"})

    assert result["device_id"] is None
    assert result["driver"] is None
    assert "no device_id configured" in result["execution_result"]
    assert result["nodes_log"][-1] == {
        "node": "emulator",
        "status": "Skipped",
        "details": {
            "device_id": None,
            "app_launched": False,
            "boot_result": "No AVD/simulator is installed to auto-boot.",
            "install_result": None,
            "launch_result": None,
            "steps": [
                "[setup] ok",
                "[server] ok",
                "[devices] some-devices",
                "[device] Skipped: no device_id configured. "
                "No AVD/simulator is installed to auto-boot.",
                "[launch] Skipped: no device available.",
            ],
        },
    }


def test_emulator_node_reports_avd_found_but_boot_timed_out_instead_of_claiming_none_installed(monkeypatch):
    """
    Regression test for the exact bug reported: list_devices() finds real AVDs, but the old
    hardcoded message claimed "no AVD/simulator is installed to auto-boot" regardless of why
    ensure_device_running() returned None. The [device] step must instead surface the real
    diagnostic (naming the AVD that was found and that it never came up in time), and must never
    claim none is installed when one plainly was.
    """
    boot_diagnostic = (
        "Found AVD/simulator 'Pixel_3a_API_34_extension_level_7_x86_64' and attempted to start it "
        "(\"Android emulator 'Pixel_3a_API_34_extension_level_7_x86_64' is booting up.\"), but it "
        "did not become ready within 90s."
    )
    _patch_emulator_basics(monkeypatch, connected_device_id=None, boot_diagnostic=boot_diagnostic)

    result = emulator_node({"platform": "android", "app_id": "com.example.app"})

    device_step = next(s for s in result["nodes_log"][-1]["details"]["steps"] if s.startswith("[device]"))
    assert "No AVD/simulator is installed" not in device_step
    assert "Pixel_3a_API_34_extension_level_7_x86_64" in device_step
    assert "did not become ready within 90s" in device_step
    assert result["nodes_log"][-1]["details"]["boot_result"] == boot_diagnostic


def test_emulator_node_passes_boot_timeout_from_state(monkeypatch):
    _patch_emulator_basics(monkeypatch, connected_device_id=None)
    calls = []
    monkeypatch.setattr(
        node_emulator_module,
        "ensure_device_running",
        lambda timeout_seconds=180: calls.append(timeout_seconds) or (None, "diag"),
    )

    emulator_node({"platform": "android", "app_id": "com.example.app", "device_boot_timeout_seconds": 30})

    assert calls == [30]


def test_emulator_node_defaults_boot_timeout_to_180_seconds(monkeypatch):
    """
    Regression test: the previous 90s default was found to be too aggressive for a real local
    Android emulator cold boot (commonly 60-180s+, especially without hardware acceleration on a
    loaded dev machine) -- it's the most plausible reason a genuinely-installed AVD never came up
    in time in the reported run. The default was raised to 180s at its single source of truth here.
    """
    _patch_emulator_basics(monkeypatch, connected_device_id=None)
    calls = []
    monkeypatch.setattr(
        node_emulator_module,
        "ensure_device_running",
        lambda timeout_seconds=180: calls.append(timeout_seconds) or (None, "diag"),
    )

    emulator_node({"platform": "android", "app_id": "com.example.app"})

    assert calls == [180]


def test_emulator_node_appends_to_existing_nodes_log(monkeypatch):
    _patch_emulator_basics(monkeypatch, connected_device_id=None)

    result = emulator_node({"platform": "android", "app_id": "com.example.app", "nodes_log": [{"node": "prior"}]})

    assert result["nodes_log"][0] == {"node": "prior"}
    assert result["nodes_log"][-1]["node"] == "emulator"


def pytest_fail_if_called():
    raise AssertionError("start_device must not be called when device_id was auto-detected")


# ---------------------------------------------------------------------------
# Navigation smoke test wiring
#
# sdk_agent has no tools to build/launch/tap the app itself (see
# sdk-agent-main-rules.json rule 15) -- when a use case actually wants
# "navigation" verified (answer_policy.verify_sdk.validate_basic_navigation),
# emulator_node must run the real Appium smoke check itself, right after
# launching the app.
# ---------------------------------------------------------------------------
def _patch_launch_success(monkeypatch, *, connected_device_id="emulator-5554", fake_driver=None):
    _patch_emulator_basics(monkeypatch, connected_device_id=connected_device_id)
    fake_driver = fake_driver if fake_driver is not None else object()
    monkeypatch.setattr(
        node_emulator_module,
        "launch_app_on_device",
        lambda os_type, device_id, app_identifier, remote_url: fake_driver,
    )
    return fake_driver


def test_emulator_node_skips_navigation_smoke_when_policy_flag_is_unset(monkeypatch):
    _patch_launch_success(monkeypatch)
    monkeypatch.setattr(
        node_emulator_module, "run_basic_navigation_smoke", lambda driver, os_type: pytest_fail_if_called()
    )

    result = emulator_node({"platform": "android", "app_id": "com.example.app"})

    assert result["nodes_log"][-1]["node"] == "emulator"
    assert "test_status" not in result


def test_emulator_node_skips_navigation_smoke_when_app_did_not_launch(monkeypatch):
    """Even if the policy flag is set, there's nothing to tap through when
    launch itself failed/was skipped."""
    _patch_emulator_basics(monkeypatch, connected_device_id=None)
    monkeypatch.setattr(
        node_emulator_module, "run_basic_navigation_smoke", lambda driver, os_type: pytest_fail_if_called()
    )

    result = emulator_node({
        "platform": "android",
        "app_id": "com.example.app",
        "answer_policy": {"verify_sdk": {"validate_basic_navigation": True}},
    })

    assert result["driver"] is None


def test_emulator_node_runs_navigation_smoke_and_logs_success(monkeypatch):
    fake_driver = _patch_launch_success(monkeypatch)
    smoke_calls = []

    def _fake_smoke(driver, os_type):
        smoke_calls.append((driver, os_type))
        return {"status": "Success", "taps_performed": [{"label": "one", "status": "ok"}]}

    monkeypatch.setattr(node_emulator_module, "run_basic_navigation_smoke", _fake_smoke)

    result = emulator_node({
        "platform": "android",
        "app_id": "com.example.app",
        "answer_policy": {"verify_sdk": {"validate_basic_navigation": True}},
    })

    assert smoke_calls == [(fake_driver, "android")]
    assert result["nodes_log"][-1]["node"] == "navigation_smoke"
    assert result["nodes_log"][-1]["status"] == "Success"
    assert "test_status" not in result


def test_emulator_node_fails_run_when_navigation_smoke_fails(monkeypatch):
    _patch_launch_success(monkeypatch)
    monkeypatch.setattr(
        node_emulator_module,
        "run_basic_navigation_smoke",
        lambda driver, os_type: {
            "status": "Fail",
            "reason": "App became unresponsive after tapping 'one': boom",
            "taps_performed": [{"label": "one", "status": "error", "error": "boom"}],
        },
    )

    result = emulator_node({
        "platform": "android",
        "app_id": "com.example.app",
        "answer_policy": {"verify_sdk": {"validate_basic_navigation": True}},
    })

    assert result["test_status"] == "FAIL"
    assert result["nodes_log"][-1] == {
        "node": "navigation_smoke",
        "status": "Fail",
        "details": {
            "status": "Fail",
            "reason": "App became unresponsive after tapping 'one': boom",
            "taps_performed": [{"label": "one", "status": "error", "error": "boom"}],
        },
    }


# ---------------------------------------------------------------------------
# Install-before-launch wiring
#
# Regression coverage for the bug where a device that didn't already have
# the app installed (e.g. a freshly auto-booted emulator) was left sitting
# on its home screen: compilation_check builds an APK/.app but nothing
# installed it, so launch_app_on_device's activate_app had nothing to
# bring to the foreground.
# ---------------------------------------------------------------------------


@dataclass
class _FakeCompilationResult:
    extra: dict = field(default_factory=dict)


def test_resolve_built_artifact_path_reads_dataclass_extra():
    state = {"compilation_result": _FakeCompilationResult(extra={"apk_path": "/tmp/app-debug.apk"})}

    assert node_emulator_module._resolve_built_artifact_path(state) == "/tmp/app-debug.apk"


def test_resolve_built_artifact_path_reads_dict_extra():
    """`compilation_result` may be a plain dict (e.g. after a checkpoint round-trip)."""
    state = {"compilation_result": {"extra": {"app_bundle_path": "/tmp/basic_app.app"}}}

    assert node_emulator_module._resolve_built_artifact_path(state) == "/tmp/basic_app.app"


def test_resolve_built_artifact_path_none_when_missing():
    assert node_emulator_module._resolve_built_artifact_path({}) is None
    assert node_emulator_module._resolve_built_artifact_path({"compilation_result": _FakeCompilationResult()}) is None


def test_emulator_node_installs_built_apk_before_launch(monkeypatch):
    fake_driver = _patch_launch_success(monkeypatch)
    install_calls = []
    monkeypatch.setattr(
        node_emulator_module,
        "install_app_on_device",
        lambda os_type, device_id, artifact_path: install_calls.append((os_type, device_id, artifact_path))
        or "Installed.",
    )

    result = emulator_node({
        "platform": "android",
        "app_id": "com.example.app",
        "compilation_result": _FakeCompilationResult(extra={"apk_path": "/tmp/app-debug.apk"}),
    })

    assert install_calls == [("android", "emulator-5554", "/tmp/app-debug.apk")]
    assert "[install] Installed." in result["execution_result"]
    assert result["driver"] is fake_driver


def test_emulator_node_skips_install_when_no_artifact_built(monkeypatch):
    _patch_launch_success(monkeypatch)
    monkeypatch.setattr(
        node_emulator_module, "install_app_on_device", lambda *a, **k: pytest_fail_if_called()
    )

    result = emulator_node({"platform": "android", "app_id": "com.example.app"})

    assert "[install]" not in result["execution_result"]


def test_emulator_node_skips_install_when_no_device_available(monkeypatch):
    _patch_emulator_basics(monkeypatch, connected_device_id=None)
    monkeypatch.setattr(
        node_emulator_module, "install_app_on_device", lambda *a, **k: pytest_fail_if_called()
    )

    result = emulator_node({
        "platform": "android",
        "app_id": "com.example.app",
        "compilation_result": _FakeCompilationResult(extra={"apk_path": "/tmp/app-debug.apk"}),
    })

    assert result["driver"] is None


# ---------------------------------------------------------------------------
# Diagnostic fields in nodes_log (reporting gap fix)
#
# build_report.py's HTML report is built entirely from state["nodes_log"] --
# it never reads state["execution_result"] -- and the sandbox dir plus
# data/runs/<run_id>/ (incl. audit.jsonl) are both deleted shortly after the
# report is built. So whatever install_app_on_device()/launch_app_on_device()
# actually returned (the real adb/Appium error) must be captured in
# nodes_log's details right here, or it's unrecoverable once the run ends.
# ---------------------------------------------------------------------------


def test_emulator_node_surfaces_install_failure_reason_in_nodes_log(monkeypatch):
    _patch_emulator_basics(monkeypatch, connected_device_id="emulator-5554")
    monkeypatch.setattr(
        node_emulator_module,
        "install_app_on_device",
        lambda os_type, device_id, artifact_path: "Install failed (exit 1): INSTALL_FAILED_INVALID_APK",
    )
    monkeypatch.setattr(
        node_emulator_module,
        "launch_app_on_device",
        lambda os_type, device_id, app_identifier, remote_url: "Failed to connect and launch app. Error: boom",
    )

    result = emulator_node({
        "platform": "android",
        "app_id": "com.example.app",
        "compilation_result": _FakeCompilationResult(extra={"apk_path": "/tmp/app-debug.apk"}),
    })

    details = result["nodes_log"][-1]["details"]
    assert result["nodes_log"][-1]["status"] == "Fail"
    assert details["device_id"] == "emulator-5554"
    assert details["app_launched"] is False
    assert details["install_result"] == "Install failed (exit 1): INSTALL_FAILED_INVALID_APK"
    assert details["launch_result"] == "Failed to connect and launch app. Error: boom"
    assert "[install] Install failed (exit 1): INSTALL_FAILED_INVALID_APK" in details["steps"]
    assert "[launch] Failed to connect and launch app. Error: boom" in details["steps"]


def test_emulator_node_surfaces_launch_failure_reason_when_install_succeeds(monkeypatch):
    _patch_emulator_basics(monkeypatch, connected_device_id="emulator-5554")
    monkeypatch.setattr(
        node_emulator_module,
        "install_app_on_device",
        lambda os_type, device_id, artifact_path: "Installed /tmp/app-debug.apk on emulator-5554.",
    )
    monkeypatch.setattr(
        node_emulator_module,
        "launch_app_on_device",
        lambda os_type, device_id, app_identifier, remote_url: (
            "Failed to connect and launch app. Error: Original error: "
            "An unknown server-side error occurred"
        ),
    )

    result = emulator_node({
        "platform": "android",
        "app_id": "com.example.app",
        "compilation_result": _FakeCompilationResult(extra={"apk_path": "/tmp/app-debug.apk"}),
    })

    details = result["nodes_log"][-1]["details"]
    assert details["install_result"] == "Installed /tmp/app-debug.apk on emulator-5554."
    assert details["launch_result"] == (
        "Failed to connect and launch app. Error: Original error: "
        "An unknown server-side error occurred"
    )
    assert result["execution_result"] == "\n".join(details["steps"])
