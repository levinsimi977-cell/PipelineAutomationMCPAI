"""
Tests for the connected-device auto-detection helpers in
infra/agents/sdkAgent/tools/emulator.py.

These exist so emulator_node can fall back to whatever device/simulator the
user already has running when a use case doesn't configure a device_id
(e.g. answer_policy.android.device_id is unset/null) -- instead of silently
skipping device boot and only failing much later, deep in sdk_agent's
verify_prompt, with an opaque "no devices connected" error.
"""

from __future__ import annotations

import subprocess

import infra.agents.sdkAgent.tools.emulator as emulator


def _fake_run(stdout: str):
    def _run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    return _run


def test_get_connected_android_device_returns_first_ready_device(monkeypatch):
    monkeypatch.setattr(emulator, "_resolve_executable", lambda name, env: name)
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(
            "List of devices attached\n"
            "emulator-5554\tdevice\n"
            "emulator-5556\toffline\n"
        ),
    )

    assert emulator.get_connected_android_device() == "emulator-5554"


def test_get_connected_android_device_skips_non_ready_devices(monkeypatch):
    monkeypatch.setattr(emulator, "_resolve_executable", lambda name, env: name)
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run("List of devices attached\nemulator-5554\toffline\n"),
    )

    assert emulator.get_connected_android_device() is None


def test_get_connected_android_device_none_when_no_devices(monkeypatch):
    monkeypatch.setattr(emulator, "_resolve_executable", lambda name, env: name)
    monkeypatch.setattr(subprocess, "run", _fake_run("List of devices attached\n"))

    assert emulator.get_connected_android_device() is None


def test_get_connected_android_device_none_on_error(monkeypatch):
    monkeypatch.setattr(emulator, "_resolve_executable", lambda name, env: name)

    def _raise(*args, **kwargs):
        raise FileNotFoundError("adb not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    assert emulator.get_connected_android_device() is None


def test_get_connected_ios_simulator_extracts_udid(monkeypatch):
    monkeypatch.setattr(emulator, "_resolve_executable", lambda name, env: name)
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(
            "-- iOS 17.0 --\n"
            "    iPhone 15 (12345678-1234-1234-1234-123456789ABC) (Booted)\n"
        ),
    )

    assert emulator.get_connected_ios_simulator() == "12345678-1234-1234-1234-123456789ABC"


def test_get_connected_ios_simulator_none_when_none_booted(monkeypatch):
    monkeypatch.setattr(emulator, "_resolve_executable", lambda name, env: name)
    monkeypatch.setattr(subprocess, "run", _fake_run("-- iOS 17.0 --\n"))

    assert emulator.get_connected_ios_simulator() is None


def test_get_connected_device_id_uses_ios_on_darwin(monkeypatch):
    monkeypatch.setattr(emulator.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(emulator, "get_connected_ios_simulator", lambda: "some-udid")
    monkeypatch.setattr(emulator, "get_connected_android_device", lambda: "should-not-be-used")

    assert emulator.get_connected_device_id() == "some-udid"


def test_get_connected_device_id_uses_android_elsewhere(monkeypatch):
    monkeypatch.setattr(emulator.platform, "system", lambda: "Windows")
    monkeypatch.setattr(emulator, "get_connected_android_device", lambda: "emulator-5554")
    monkeypatch.setattr(emulator, "get_connected_ios_simulator", lambda: "should-not-be-used")

    assert emulator.get_connected_device_id() == "emulator-5554"


def test_list_android_avd_names_parses_output(monkeypatch):
    monkeypatch.setattr(emulator, "_resolve_executable", lambda name, env: name)
    monkeypatch.setattr(subprocess, "run", _fake_run("Pixel_5_API_33\nPixel_7_API_34\n"))

    assert emulator.list_android_avd_names() == ["Pixel_5_API_33", "Pixel_7_API_34"]


def test_list_android_avd_names_empty_on_error(monkeypatch):
    monkeypatch.setattr(emulator, "_resolve_executable", lambda name, env: name)

    def _raise(*args, **kwargs):
        raise FileNotFoundError("emulator not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    assert emulator.list_android_avd_names() == []


def test_ensure_android_emulator_running_uses_already_connected_device(monkeypatch):
    """No AVD should be booted at all when a device is already ready."""
    monkeypatch.setattr(emulator, "get_connected_android_device", lambda: "emulator-5554")
    monkeypatch.setattr(
        emulator, "list_android_avd_names", lambda: (_ for _ in ()).throw(AssertionError("should not be called"))
    )

    device_id, diagnostic = emulator.ensure_android_emulator_running()

    assert device_id == "emulator-5554"
    assert "emulator-5554" in diagnostic


def test_ensure_android_emulator_running_boots_first_avd_when_none_connected(monkeypatch):
    """
    Regression test: when nothing is running and no device_id is
    configured, the pipeline must boot an emulator itself instead of just
    reporting failure and requiring the user to start one by hand.
    """
    poll_results = iter([None, None, "emulator-5554"])
    monkeypatch.setattr(emulator, "get_connected_android_device", lambda: next(poll_results))
    monkeypatch.setattr(emulator, "list_android_avd_names", lambda: ["Pixel_5_API_33"])
    boot_calls = []
    monkeypatch.setattr(
        emulator, "start_android_emulator", lambda avd_name: boot_calls.append(avd_name) or "booting"
    )
    monkeypatch.setattr(emulator.time, "sleep", lambda seconds: None)

    device_id, diagnostic = emulator.ensure_android_emulator_running(timeout_seconds=30, poll_interval_seconds=1)

    assert boot_calls == ["Pixel_5_API_33"]
    assert device_id == "emulator-5554"
    assert "Pixel_5_API_33" in diagnostic
    assert "emulator-5554" in diagnostic


def test_ensure_android_emulator_running_none_when_no_avd_installed(monkeypatch):
    """When list_names() is genuinely empty, and only then, may the diagnostic claim no
    AVD/simulator is installed."""
    monkeypatch.setattr(emulator, "get_connected_android_device", lambda: None)
    monkeypatch.setattr(emulator, "list_android_avd_names", lambda: [])

    device_id, diagnostic = emulator.ensure_android_emulator_running()

    assert device_id is None
    assert "No AVD/simulator is installed" in diagnostic


def test_ensure_android_emulator_running_none_when_start_fails(monkeypatch):
    """
    Regression test: an AVD WAS found, but start_android_emulator() itself
    reported failure. The diagnostic must surface that failure (and the AVD
    name) instead of claiming no AVD is installed -- that claim is only
    true when list_names() is empty.
    """
    monkeypatch.setattr(emulator, "get_connected_android_device", lambda: None)
    monkeypatch.setattr(emulator, "list_android_avd_names", lambda: ["Pixel_5_API_33"])
    monkeypatch.setattr(
        emulator, "start_android_emulator", lambda avd_name: "Failed to start Android emulator: boom"
    )
    monkeypatch.setattr(emulator.time, "sleep", lambda seconds: None)

    device_id, diagnostic = emulator.ensure_android_emulator_running(timeout_seconds=0.01, poll_interval_seconds=1)

    assert device_id is None
    assert "No AVD/simulator is installed" not in diagnostic
    assert "Pixel_5_API_33" in diagnostic
    assert "Failed to start Android emulator: boom" in diagnostic
    assert "0.01" in diagnostic


def test_ensure_android_emulator_running_none_when_boot_never_ready(monkeypatch):
    """
    Regression test: an AVD WAS found and start_android_emulator() reported
    success, but it never showed up as ready via adb within the timeout
    (a plausible, slow-cold-boot outcome). The diagnostic must say so
    plainly -- naming the AVD, what start() returned, and the timeout --
    instead of the old hardcoded "no AVD/simulator is installed" message,
    which is false whenever list_names() found one or more.
    """
    monkeypatch.setattr(emulator, "get_connected_android_device", lambda: None)
    monkeypatch.setattr(emulator, "list_android_avd_names", lambda: ["Pixel_5_API_33"])
    monkeypatch.setattr(
        emulator, "start_android_emulator", lambda avd_name: "Android emulator 'Pixel_5_API_33' is booting up."
    )
    monkeypatch.setattr(emulator.time, "sleep", lambda seconds: None)

    device_id, diagnostic = emulator.ensure_android_emulator_running(timeout_seconds=0.01, poll_interval_seconds=1)

    assert device_id is None
    assert "No AVD/simulator is installed" not in diagnostic
    assert "Pixel_5_API_33" in diagnostic
    assert "is booting up" in diagnostic
    assert "0.01" in diagnostic


def test_ensure_device_running_uses_ios_on_darwin(monkeypatch):
    monkeypatch.setattr(emulator.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(emulator, "ensure_ios_simulator_running", lambda t, p: ("some-udid", "diag"))
    monkeypatch.setattr(
        emulator, "ensure_android_emulator_running", lambda t, p: (_ for _ in ()).throw(AssertionError())
    )

    assert emulator.ensure_device_running() == ("some-udid", "diag")


def test_ensure_device_running_uses_android_elsewhere(monkeypatch):
    monkeypatch.setattr(emulator.platform, "system", lambda: "Windows")
    monkeypatch.setattr(emulator, "ensure_android_emulator_running", lambda t, p: ("emulator-5554", "diag"))
    monkeypatch.setattr(
        emulator, "ensure_ios_simulator_running", lambda t, p: (_ for _ in ()).throw(AssertionError())
    )

    assert emulator.ensure_device_running() == ("emulator-5554", "diag")


# ---------------------------------------------------------------------------
# run_basic_navigation_smoke
#
# sdk_agent has no tools to build/launch/tap the app itself (see
# sdk-agent-main-rules.json rule 15); this is the pipeline's own best-effort
# stand-in for "the use case wants navigation validated"
# (answer_policy.verify_sdk.validate_basic_navigation).
# ---------------------------------------------------------------------------
class _FakeButton:
    def __init__(self, label: str):
        self.text = label

    def get_attribute(self, name):
        return None

    def click(self):
        pass


class _FakeDriver:
    def __init__(self, buttons, page_source_ok=True):
        self._buttons = buttons
        self._page_source_ok = page_source_ok

    def find_elements(self, by, value):
        return self._buttons

    @property
    def page_source(self):
        if not self._page_source_ok:
            raise RuntimeError("session dead")
        return "<xml/>"


def test_run_basic_navigation_smoke_succeeds_tapping_available_buttons(monkeypatch):
    monkeypatch.setattr(emulator.time, "sleep", lambda seconds: None)
    driver = _FakeDriver([_FakeButton("one"), _FakeButton("two")])

    result = emulator.run_basic_navigation_smoke(driver, "android")

    assert result["status"] == "Success"
    assert [t["label"] for t in result["taps_performed"]] == ["one", "two"]
    assert all(t["status"] == "ok" for t in result["taps_performed"])


def test_run_basic_navigation_smoke_respects_max_taps(monkeypatch):
    monkeypatch.setattr(emulator.time, "sleep", lambda seconds: None)
    buttons = [_FakeButton(str(i)) for i in range(5)]
    driver = _FakeDriver(buttons)

    result = emulator.run_basic_navigation_smoke(driver, "android", max_taps=2)

    assert len(result["taps_performed"]) == 2


def test_run_basic_navigation_smoke_skips_when_no_buttons_found(monkeypatch):
    monkeypatch.setattr(emulator.time, "sleep", lambda seconds: None)
    driver = _FakeDriver([])

    result = emulator.run_basic_navigation_smoke(driver, "android")

    assert result["status"] == "Skipped"
    assert result["taps_performed"] == []


def test_run_basic_navigation_smoke_fails_when_app_becomes_unresponsive(monkeypatch):
    """
    Regression test: the liveness check must run *after* each individual
    tap (via driver.page_source), not just once at the end -- so a crash
    triggered partway through is caught and reported instead of being
    masked by later taps that never happen.
    """
    monkeypatch.setattr(emulator.time, "sleep", lambda seconds: None)
    taps: list[str] = []

    class _CrashingButton(_FakeButton):
        def click(self):
            taps.append(self.text)

    class _FlakyDriver:
        def __init__(self, buttons):
            self._buttons = buttons

        def find_elements(self, by, value):
            return self._buttons

        @property
        def page_source(self):
            if len(taps) >= 2:
                raise RuntimeError("session dead")
            return "<xml/>"

    driver = _FlakyDriver([_CrashingButton("one"), _CrashingButton("two"), _CrashingButton("three")])

    result = emulator.run_basic_navigation_smoke(driver, "android")

    assert result["status"] == "Fail"
    assert len(result["taps_performed"]) == 2
    assert result["taps_performed"][0]["status"] == "ok"
    assert result["taps_performed"][-1]["status"] == "error"
    assert "unresponsive" in result["reason"]


def test_run_basic_navigation_smoke_fails_when_elements_cannot_be_queried(monkeypatch):
    class _BrokenDriver:
        def find_elements(self, by, value):
            raise RuntimeError("appium server unreachable")

    result = emulator.run_basic_navigation_smoke(_BrokenDriver(), "android")

    assert result["status"] == "Fail"
    assert result["taps_performed"] == []


# ---------------------------------------------------------------------------
# install_app_on_device
#
# Regression coverage for the bug where a freshly-booted/auto-detected
# device never had the just-built APK/.app installed on it, so
# launch_app_on_device's activate_app had nothing to bring to the
# foreground and the device was left sitting on its home screen.
# ---------------------------------------------------------------------------


def test_install_app_on_device_skips_when_artifact_missing(tmp_path):
    missing_apk = str(tmp_path / "does-not-exist.apk")

    result = emulator.install_app_on_device("android", "emulator-5554", missing_apk)

    assert "Skipped" in result


def test_install_app_on_device_android_runs_adb_install(monkeypatch, tmp_path):
    apk = tmp_path / "app-debug.apk"
    apk.write_bytes(b"fake-apk-bytes")
    monkeypatch.setattr(emulator, "_resolve_executable", lambda name, env: name)
    captured = {}

    def _fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="Success", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = emulator.install_app_on_device("android", "emulator-5554", str(apk))

    assert captured["command"] == ["adb", "-s", "emulator-5554", "install", "-r", str(apk)]
    assert "Installed" in result


def test_install_app_on_device_ios_runs_simctl_install(monkeypatch, tmp_path):
    app_bundle = tmp_path / "basic_app.app"
    app_bundle.mkdir()
    monkeypatch.setattr(emulator, "_resolve_executable", lambda name, env: name)
    captured = {}

    def _fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = emulator.install_app_on_device("ios", "some-udid", str(app_bundle))

    assert captured["command"] == ["xcrun", "simctl", "install", "some-udid", str(app_bundle)]
    assert "Installed" in result


def test_install_app_on_device_reports_failure_on_nonzero_exit(monkeypatch, tmp_path):
    apk = tmp_path / "app-debug.apk"
    apk.write_bytes(b"fake-apk-bytes")
    monkeypatch.setattr(emulator, "_resolve_executable", lambda name, env: name)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="INSTALL_FAILED"),
    )

    result = emulator.install_app_on_device("android", "emulator-5554", str(apk))

    assert "Install failed" in result
    assert "INSTALL_FAILED" in result


def test_install_app_on_device_reports_exception(monkeypatch, tmp_path):
    apk = tmp_path / "app-debug.apk"
    apk.write_bytes(b"fake-apk-bytes")

    def _raise(name, env):
        raise FileNotFoundError("adb not found")

    monkeypatch.setattr(emulator, "_resolve_executable", _raise)

    result = emulator.install_app_on_device("android", "emulator-5554", str(apk))

    assert "Install failed" in result


# ---------------------------------------------------------------------------
# launch_app_on_device
#
# Regression coverage for the bug where the Android capabilities passed to
# UiAutomator2 set only appium:deviceName (which UiAutomator2 does not use
# to select an attached device -- that's purely descriptive) and never
# appium:udid, the capability UiAutomator2 actually uses to disambiguate
# `adb devices` output. Without udid, a stale/offline emulator entry left
# over from a previous run alongside the freshly booted one makes session
# creation fail outright with "more than one device/emulator", even though
# the requested device/app were both genuinely fine.
# ---------------------------------------------------------------------------


class _FakeAppiumDriver:
    def __init__(self):
        self.activated_with: str | None = None

    def activate_app(self, app_identifier):
        self.activated_with = app_identifier


def test_launch_app_on_device_android_sets_udid_capability(monkeypatch):
    captured = {}

    def _fake_remote(remote_url, options):
        captured["remote_url"] = remote_url
        captured["capabilities"] = dict(options.capabilities)
        return _FakeAppiumDriver()

    monkeypatch.setattr(emulator.webdriver, "Remote", _fake_remote)

    result = emulator.launch_app_on_device(
        "android", "emulator-5554", "com.example.app", "http://127.0.0.1:4723"
    )

    assert isinstance(result, _FakeAppiumDriver)
    assert result.activated_with == "com.example.app"
    assert captured["capabilities"]["appium:udid"] == "emulator-5554"
    assert captured["capabilities"]["appium:deviceName"] == "emulator-5554"
    assert captured["capabilities"]["appium:appPackage"] == "com.example.app"


def test_launch_app_on_device_ios_sets_udid_capability(monkeypatch):
    captured = {}

    def _fake_remote(remote_url, options):
        captured["capabilities"] = dict(options.capabilities)
        return _FakeAppiumDriver()

    monkeypatch.setattr(emulator.webdriver, "Remote", _fake_remote)

    result = emulator.launch_app_on_device(
        "ios", "some-udid", "com.example.app", "http://127.0.0.1:4723"
    )

    assert isinstance(result, _FakeAppiumDriver)
    assert captured["capabilities"]["appium:udid"] == "some-udid"
    assert captured["capabilities"]["appium:bundleId"] == "com.example.app"


def test_launch_app_on_device_returns_error_string_when_session_creation_fails(monkeypatch):
    def _fake_remote(remote_url, options):
        raise RuntimeError("more than one device/emulator")

    monkeypatch.setattr(emulator.webdriver, "Remote", _fake_remote)

    result = emulator.launch_app_on_device(
        "android", "emulator-5554", "com.example.app", "http://127.0.0.1:4723"
    )

    assert isinstance(result, str)
    assert "Failed to connect and launch app" in result
    assert "more than one device/emulator" in result


def test_launch_app_on_device_rejects_unknown_os_type():
    result = emulator.launch_app_on_device(
        "windows", "some-device", "com.example.app", "http://127.0.0.1:4723"
    )

    assert result == "Error: os_type must be either 'android' or 'ios'."
