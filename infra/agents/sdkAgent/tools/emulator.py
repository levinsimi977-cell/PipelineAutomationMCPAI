import os
import re
import socket
import subprocess
import time
import platform
import shutil
import signal
import urllib.request
from pathlib import Path

from appium import webdriver
from appium.options.common import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy

# Processes this module started (Appium server / Android emulator).
# Stopped on pipeline teardown so the next Save-and-run starts clean.
_OWNED_PROCESSES: list[subprocess.Popen] = []
# Extra PIDs to kill (e.g. Appium already listening on 4723 when we reused it).
_OWNED_PIDS: set[int] = set()
# iOS simulator UUIDs we booted (no long-lived Popen to own).
_OWNED_IOS_SIMULATOR_UDIDS: list[str] = []
# True once this process used Appium on 4723 (started or reused) — stop it on teardown.
_STOP_APPIUM_PORT = False
_APPIUM_PORT = 4723


# ==========================================
# WDA Readiness Polling
# ==========================================

def _wait_for_wda_ready(
    host: str = "127.0.0.1",
    port: int = 8100,
    timeout_seconds: int = 60,
    poll_interval: float = 2.0,
) -> None:
    """Poll WDA on host:port until it accepts a TCP connection, then return.

    This replaces any fixed sleep before the Appium session: the caller proceeds
    the moment WDA is actually listening — not a second earlier or later.
    If WDA does not come up within `timeout_seconds`, we return silently and let
    the Appium session-creation retry handle the failure.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return  # WDA is ready
        except OSError:
            time.sleep(poll_interval)


def wait_for_ios_log_marker(
    predicate: str,
    marker_substrings: tuple[str, ...],
    timeout_seconds: float = 45.0,
    poll_interval: float = 3.0,
    lookback_seconds: float = 60.0,
) -> str:
    """Poll the booted iOS simulator's system log until a line containing one of
    `marker_substrings` (case-insensitive) appears under `predicate`, then return
    the captured output immediately. If nothing matches within `timeout_seconds`,
    return whatever was last captured (best-effort; caller must not fail on this).

    Replaces a fixed `sleep(N)` followed by a single `log show` snapshot: resolving
    an AppsFlyer OneLink requires a real network round-trip, whose duration varies
    run to run, so a fixed wait either wastes time or -- as seen in practice --
    gives up before the SDK's delegate callback has actually logged anything,
    making a real success look like a verification failure.
    """
    deadline = time.monotonic() + timeout_seconds
    last_output = ""
    while True:
        try:
            result = subprocess.run(
                [
                    "xcrun", "simctl", "spawn", "booted",
                    "log", "show",
                    "--predicate", predicate,
                    "--last", f"{int(lookback_seconds)}s",
                    "--style", "compact",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            last_output = result.stdout or last_output
        except Exception:
            pass

        lowered = last_output.lower()
        if any(marker.lower() in lowered for marker in marker_substrings):
            return last_output
        if time.monotonic() >= deadline:
            return last_output
        time.sleep(poll_interval)


def read_ios_appsflyer_uid(device_udid: str, bundle_id: str) -> str | None:
    """Read the AppsFlyer-generated device UID directly from the app's
    on-device NSUserDefaults preferences plist -- entirely independent of
    whether the app's own code ever logs it.

    AppsFlyerLib persists the UID it generates on first launch under the
    well-known key "AppsFlyerUserId" in the app's standard preferences file
    (Library/Preferences/<bundle_id>.plist inside its simulator container,
    e.g. .../data/Containers/Data/Application/<container-guid>/Library/
    Preferences/<bundle_id>.plist). This is the same mechanism `getAppsFlyerUID`
    reads from inside the app; reading it from disk lets the pipeline surface
    real evidence of a successful SDK session for verifyIosSdk even when the
    SDK agent's own delegate implementation never NSLogs the value it
    receives -- without touching any code the SDK agent wrote.

    Returns the UID string, or None if the simulator/container/key isn't
    found (best-effort; must never raise).
    """
    try:
        apps_dir = (
            Path.home()
            / "Library/Developer/CoreSimulator/Devices"
            / device_udid
            / "data/Containers/Data/Application"
        )
        if not apps_dir.is_dir():
            return None
        for container in apps_dir.iterdir():
            plist = container / "Library/Preferences" / f"{bundle_id}.plist"
            if not plist.exists():
                continue
            result = subprocess.run(
                ["plutil", "-extract", "AppsFlyerUserId", "raw", str(plist)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            uid = result.stdout.strip()
            if uid:
                return uid
        return None
    except Exception:
        return None


# ==========================================
# Helpers
# ==========================================


def _track_process(proc: subprocess.Popen) -> None:
    _OWNED_PROCESSES.append(proc)
    if getattr(proc, "pid", None):
        _OWNED_PIDS.add(int(proc.pid))


def _popen_kwargs() -> dict:
    """New process group on Unix so we can kill the whole emulator/Appium tree."""
    if platform.system() == "Windows":
        return {}
    return {"start_new_session": True}


def _terminate_pid_tree(pid: int) -> None:
    """Kill `pid` and its children (Windows taskkill /T; Unix process group)."""
    if not pid or pid <= 0:
        return
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                return
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, OSError):
                return
            time.sleep(0.2)
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    except Exception:
        pass


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    pid = getattr(proc, "pid", None)
    if pid:
        _terminate_pid_tree(int(pid))
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
        return
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _pids_listening_on_port(port: int) -> list[int]:
    """Best-effort PIDs bound to TCP `port` (for Appium reuse / leftover stop)."""
    pids: set[int] = set()
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        f"Get-NetTCPConnection -LocalPort {port} -State Listen "
                        "-ErrorAction SilentlyContinue | "
                        "Select-Object -ExpandProperty OwningProcess -Unique"
                    ),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.add(int(line))
        else:
            result = subprocess.run(
                ["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.add(int(line))
    except Exception:
        pass
    return sorted(pids)


def _claim_appium_port() -> None:
    """Mark Appium on 4723 as ours for teardown (including already-listening reuse)."""
    global _STOP_APPIUM_PORT
    _STOP_APPIUM_PORT = True
    for pid in _pids_listening_on_port(_APPIUM_PORT):
        _OWNED_PIDS.add(pid)


def stop_owned_device_processes() -> None:
    """
    Best-effort stop of Appium / Android emulator processes we started, and
    shutdown of iOS simulators we booted. Safe to call when nothing was started.

    Also stops Appium on 4723 when this process reused an already-listening server,
    and kills process trees so emulator qemu children do not linger on Windows.
    """
    global _STOP_APPIUM_PORT

    while _OWNED_PROCESSES:
        proc = _OWNED_PROCESSES.pop()
        try:
            _terminate_process(proc)
        except Exception:
            pass

    for pid in list(_OWNED_PIDS):
        try:
            _terminate_pid_tree(pid)
        except Exception:
            pass
    _OWNED_PIDS.clear()

    if _STOP_APPIUM_PORT:
        for pid in _pids_listening_on_port(_APPIUM_PORT):
            try:
                _terminate_pid_tree(pid)
            except Exception:
                pass
        _STOP_APPIUM_PORT = False

    if _OWNED_IOS_SIMULATOR_UDIDS and platform.system() == "Darwin":
        try:
            env = _get_augmented_env()
            xcrun_path = _resolve_executable("xcrun", env)
        except Exception:
            xcrun_path = None
            env = None
        while _OWNED_IOS_SIMULATOR_UDIDS:
            udid = _OWNED_IOS_SIMULATOR_UDIDS.pop()
            if not xcrun_path or env is None:
                continue
            try:
                subprocess.run(
                    [xcrun_path, "simctl", "shutdown", udid],
                    capture_output=True,
                    env=env,
                    check=False,
                )
            except Exception:
                pass

def _find_android_sdk_root(env: dict) -> str | None:
    """
    Locates the Android SDK root directory:
    1. ANDROID_HOME / ANDROID_SDK_ROOT, if already set and valid.
    2. Common default install locations per Operating System.
    """
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        path = env.get(var)
        if path and os.path.isdir(path):
            return path

    system = platform.system()
    if system == "Windows":
        candidate = os.path.join(env.get("LOCALAPPDATA", ""), "Android", "Sdk")
    elif system == "Darwin":
        candidate = os.path.expanduser("~/Library/Android/sdk")
    else:
        candidate = os.path.expanduser("~/Android/Sdk")

    return candidate if os.path.isdir(candidate) else None


def _get_augmented_env() -> dict:
    """
    Returns a copy of the current process environment enriched with
    ANDROID_HOME/ANDROID_SDK_ROOT and the SDK's platform-tools/emulator
    directories on PATH, auto-detected from common install locations when
    not already configured. This lets Appium/adb/emulator work correctly
    even when the Android SDK is installed but not exported system-wide.
    """
    env = os.environ.copy()
    sdk_root = _find_android_sdk_root(env)

    if sdk_root:
        env["ANDROID_HOME"] = sdk_root
        env["ANDROID_SDK_ROOT"] = sdk_root
        sdk_bin_dirs = [
            os.path.join(sdk_root, "platform-tools"),
            os.path.join(sdk_root, "emulator"),
            os.path.join(sdk_root, "cmdline-tools", "latest", "bin"),
        ]
        env["PATH"] = os.pathsep.join(sdk_bin_dirs) + os.pathsep + env.get("PATH", "")

    return env


def _resolve_executable(name: str, env: dict) -> str:
    """
    Resolves a command name to its full executable path before handing it to
    subprocess. This is required on Windows, where tools like npm/appium are
    installed as .cmd/.ps1 shims that plain subprocess.run/Popen cannot find
    without shell=True. shutil.which() correctly checks PATHEXT on Windows
    and PATH on macOS/Linux, so this keeps the code cross-platform.
    """
    resolved = shutil.which(name, path=env.get("PATH"))
    if not resolved:
        raise FileNotFoundError(f"'{name}' was not found in PATH. Make sure it is installed.")
    return resolved


# ==========================================
# 1. Environment & Server Management
# ==========================================

def setup_appium_environment() -> str:
    """
    Installs Appium globally and installs drivers based on the Operating System:
    - macOS (Darwin): Installs iOS (XCUITest) driver.
    - Windows/Linux: Installs Android (UiAutomator2) driver.
    """
    try:
        env = _get_augmented_env()

        # התקנת שרת האפיום
        npm_path = _resolve_executable("npm", env)
        subprocess.run([npm_path, "install", "-g", "appium"], check=True, capture_output=True, env=env)

        # זיהוי מערכת ההפעלה הנוכחית
        current_os = platform.system()
        appium_path = _resolve_executable("appium", env)

        if current_os == "Darwin":
            # התקנה עבור Mac
            subprocess.run([appium_path, "driver", "install", "xcuitest"], capture_output=True, env=env)
            # הערה: אם תרצה שהמאק יתקין גם אנדרואיד, אפשר להוסיף כאן גם את השורה של uiautomator2
            return "Successfully installed Appium and iOS (XCUITest) driver for Mac."

        else:
            # התקנה עבור Windows או Linux
            subprocess.run([appium_path, "driver", "install", "uiautomator2"], capture_output=True, env=env)
            return f"Successfully installed Appium and Android (UiAutomator2) driver for {current_os}."

    except Exception as e:
        return f"Installation failed. Ensure Node.js is installed. Error: {str(e)}"

def start_appium_server() -> str:
    """
    Starts the Appium server in the background on port 4723.
    """
    try:
        env = _get_augmented_env()
        # Already up — reuse, but claim the port so teardown still stops it.
        try:
            if urllib.request.urlopen(
                f"http://127.0.0.1:{_APPIUM_PORT}/status", timeout=2
            ).getcode() == 200:
                _claim_appium_port()
                return f"Appium server already listening on port {_APPIUM_PORT}."
        except Exception:
            pass
        appium_path = _resolve_executable("appium", env)
        proc = subprocess.Popen(
            [appium_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            **_popen_kwargs(),
        )
        _track_process(proc)
        _claim_appium_port()
        time.sleep(5)
        status = urllib.request.urlopen(
            f"http://127.0.0.1:{_APPIUM_PORT}/status"
        ).getcode()
        return (
            f"Appium server started and listening on port {_APPIUM_PORT}."
            if status == 200
            else "Server started but check failed."
        )
    except Exception as e:
        return f"Failed to start Appium server: {str(e)}"


# ==========================================
# 2. Android Device Management
# ==========================================

def list_android_emulators() -> str:
    """
    Lists all available Android Virtual Devices (AVDs).
    """
    try:
        env = _get_augmented_env()
        emulator_path = _resolve_executable("emulator", env)
        result = subprocess.run([emulator_path, "-list-avds"], capture_output=True, text=True, check=True, env=env)
        return f"Available Android Emulators:\n{result.stdout.strip()}" if result.stdout else "No Android emulators found."
    except Exception as e:
        return f"Failed to list Android emulators: {str(e)}"

def start_android_emulator(avd_name: str) -> str:
    """
    Starts a specific Android emulator in the background.
    Args: avd_name: The exact name of the Android emulator.
    """
    try:
        env = _get_augmented_env()
        emulator_path = _resolve_executable("emulator", env)
        proc = subprocess.Popen(
            [emulator_path, "-avd", avd_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            **_popen_kwargs(),
        )
        _track_process(proc)
        time.sleep(15) # Wait for boot
        return f"Android emulator '{avd_name}' is booting up."
    except Exception as e:
        return f"Failed to start Android emulator: {str(e)}"

# ==========================================
# 3. iOS Device Management (macOS only)
# ==========================================

def list_ios_simulators() -> str:
    """
    Lists available iOS Simulators.
    Returns the device name and its UUID, which is required to boot it.
    """
    try:
        env = _get_augmented_env()
        xcrun_path = _resolve_executable("xcrun", env)
        # xcrun simctl list devices available
        result = subprocess.run([xcrun_path, "simctl", "list", "devices", "available"], capture_output=True, text=True, check=True, env=env)
        return f"Available iOS Simulators:\n{result.stdout.strip()}"
    except Exception as e:
        return f"Failed to list iOS simulators. Note: This only works on macOS. Error: {str(e)}"

def start_ios_simulator(device_uuid: str) -> str:
    """
    Starts a specific iOS Simulator using its UUID.
    Args: device_uuid: The exact UUID of the iOS simulator.
    """
    try:
        env = _get_augmented_env()
        xcrun_path = _resolve_executable("xcrun", env)
        open_path = _resolve_executable("open", env)
        # Boot the simulator in the background
        subprocess.run([xcrun_path, "simctl", "boot", device_uuid], check=True, capture_output=True, env=env)
        _OWNED_IOS_SIMULATOR_UDIDS.append(device_uuid)
        # Open the Simulator GUI application so it is visible on screen
        subprocess.run([open_path, "-a", "Simulator"], check=True, env=env)
        # `simctl boot` returns as soon as the boot is *requested*, not once it's
        # actually complete -- a fixed sleep afterwards was a guess that was too
        # short for a cold/first boot (esp. on newer/beta iOS runtimes) and too
        # long otherwise. `bootstatus` is the real signal: it blocks exactly
        # until the simulator finishes booting, so callers only wait as long as
        # actually needed instead of guessing a duration.
        subprocess.run(
            [xcrun_path, "simctl", "bootstatus", device_uuid],
            capture_output=True, text=True, timeout=180, env=env,
        )
        return f"iOS simulator '{device_uuid}' booted successfully and is visible."
    except Exception as e:
        return f"Failed to start iOS simulator: {str(e)}"

# ==========================================
# 4. OS-Aware Wrapper Functions
# ==========================================
# פונקציות מעטפת שבודקות את מערכת ההפעלה הנוכחית ומפעילות אוטומטית
# את הפונקציה המתאימה (Android או iOS), כדי שלא יהיה צריך לבחור ידנית.

def list_devices() -> str:
    """
    Lists available devices/emulators for the current platform:
    - macOS (Darwin): lists iOS simulators.
    - Windows/Linux: lists Android emulators.
    """
    if platform.system() == "Darwin":
        return list_ios_simulators()
    return list_android_emulators()

def get_connected_android_device() -> str | None:
    """Returns the serial of the first Android device `adb devices` reports as ready
    ("device" state) AND that has actually finished booting, or None."""
    try:
        env = _get_augmented_env()
        adb_path = _resolve_executable("adb", env)
        result = subprocess.run([adb_path, "devices"], capture_output=True, text=True, check=True, env=env)
        for line in result.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                if _is_android_device_booted(adb_path, serial, env):
                    return serial
    except Exception:
        pass
    return None

def _is_android_device_booted(adb_path: str, serial: str, env: dict) -> bool:
    """True once `serial` reports sys.boot_completed == "1" (full Android boot, not just adbd up)."""
    try:
        result = subprocess.run(
            [adb_path, "-s", serial, "shell", "getprop", "sys.boot_completed"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        return result.stdout.strip() == "1"
    except Exception:
        return False



def get_connected_ios_simulator() -> str | None:
    """Returns the UDID of the first booted iOS simulator (macOS only), or None if none booted / on error."""
    try:
        env = _get_augmented_env()
        xcrun_path = _resolve_executable("xcrun", env)
        result = subprocess.run(
            [xcrun_path, "simctl", "list", "devices", "booted"], capture_output=True, text=True, check=True, env=env
        )
        match = re.search(r"\(([0-9A-Fa-f-]{36})\)", result.stdout)
        return match.group(1) if match else None
    except Exception:
        return None


def get_connected_device_id() -> str | None:
    """OS-aware wrapper: booted iOS simulator on macOS, else a connected/ready Android device."""
    if platform.system() == "Darwin":
        return get_connected_ios_simulator()
    return get_connected_android_device()


def list_android_avd_names() -> list[str]:
    """Returns the names of installed Android Virtual Devices, or [] on any failure."""
    try:
        env = _get_augmented_env()
        emulator_path = _resolve_executable("emulator", env)
        result = subprocess.run(
            [emulator_path, "-list-avds"], capture_output=True, text=True, check=True, env=env
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def list_ios_simulator_udids() -> list[str]:
    """Returns UDIDs of installed iOS simulators (not necessarily booted), or [] on any failure."""
    try:
        env = _get_augmented_env()
        xcrun_path = _resolve_executable("xcrun", env)
        result = subprocess.run(
            [xcrun_path, "simctl", "list", "devices", "available"], capture_output=True, text=True, check=True, env=env
        )
        return re.findall(r"\(([0-9A-Fa-f-]{36})\)", result.stdout)
    except Exception:
        return []


def _ensure_device_running_generic(get_connected, list_names, start, timeout_seconds, poll_interval_seconds):
    """Shared logic: use whatever is already connected, else boot the first listed device and poll for it.

    Returns (device_id, diagnostic). A bare `device_id is None` used to be the only signal callers
    had, which collapsed three very different situations into the same misleading outcome: no
    AVD/simulator installed at all vs. one was found but `start(...)` itself failed vs. one was
    found and `start(...)` reported success but simply never became ready within the timeout (a
    real local emulator cold boot commonly takes well over a minute). `diagnostic` is always a
    human-readable sentence describing which of those actually happened, so it can be surfaced
    verbatim to a human instead of guessed at from `None` alone.
    """
    device_id = get_connected()
    if device_id:
        return device_id, f"Using already-running device/simulator: {device_id}."

    names = list_names()
    if not names:
        return None, "No AVD/simulator is installed to auto-boot."

    avd_name = names[0]
    start_result = start(avd_name)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        device_id = get_connected()
        if device_id:
            return device_id, f"Booted '{avd_name}' ({start_result}) and it is now ready as {device_id}."
        time.sleep(poll_interval_seconds)
    return None, (
        f"Found AVD/simulator '{avd_name}' and attempted to start it ({start_result!r}), "
        f"but it did not become ready within {timeout_seconds}s."
    )


def ensure_android_emulator_running(
    timeout_seconds: int = 90, poll_interval_seconds: int = 3
) -> tuple[str | None, str]:
    """Returns (device_id, diagnostic) for a ready Android device, booting the first installed AVD and
    polling if none is connected. See `_ensure_device_running_generic` for what `diagnostic` covers."""
    return _ensure_device_running_generic(
        get_connected_android_device, list_android_avd_names, start_android_emulator,
        timeout_seconds, poll_interval_seconds,
    )


def ensure_ios_simulator_running(
    timeout_seconds: int = 90, poll_interval_seconds: int = 3
) -> tuple[str | None, str]:
    """Returns (device_id, diagnostic) for a booted iOS simulator's UDID (macOS only), booting the
    first installed one and polling if none is booted. See `_ensure_device_running_generic` for
    what `diagnostic` covers."""
    return _ensure_device_running_generic(
        get_connected_ios_simulator, list_ios_simulator_udids, start_ios_simulator,
        timeout_seconds, poll_interval_seconds,
    )


def ensure_device_running(timeout_seconds: int = 90, poll_interval_seconds: int = 3) -> tuple[str | None, str]:
    """OS-aware wrapper: ensures a device/simulator is running (booting one if needed) and returns
    (device_id, diagnostic). See `_ensure_device_running_generic` for what `diagnostic` covers."""
    if platform.system() == "Darwin":
        return ensure_ios_simulator_running(timeout_seconds, poll_interval_seconds)
    return ensure_android_emulator_running(timeout_seconds, poll_interval_seconds)


def start_device(device_id: str) -> str:
    """
    Starts a device/emulator for the current platform:
    - macOS (Darwin): boots the iOS simulator matching the given UUID.
    - Windows/Linux: boots the Android emulator matching the given AVD name.
    Args:
        device_id: iOS simulator UUID (on macOS) or Android AVD name (on Windows/Linux).
    """
    if platform.system() == "Darwin":
        return start_ios_simulator(device_id)
    return start_android_emulator(device_id)

# ==========================================
# 5. Universal App Launcher
# ==========================================
def install_app_on_device(os_type: str, device_id: str, app_artifact_path: str) -> str:
    """
    Installs a built app artifact (Android .apk or iOS .app bundle) onto
    the given device/simulator, so there is actually something for
    `launch_app_on_device`'s `activate_app` to bring to the foreground.

    Without this, a freshly-booted/auto-detected device never has the app
    the pipeline just built, and `activate_app` has nothing to activate —
    the device is left sitting on its home screen.
    """
    if not app_artifact_path or not os.path.exists(app_artifact_path):
        # An .apk is a file, but an iOS .app bundle is a directory -- exists()
        # covers both.
        return f"Skipped install: no built app artifact found at {app_artifact_path!r}."

    env = _get_augmented_env()
    try:
        if os_type.lower() == "android":
            adb_path = _resolve_executable("adb", env)
            command = [adb_path, "-s", device_id, "install", "-r", app_artifact_path]
        else:
            xcrun_path = _resolve_executable("xcrun", env)
            command = [xcrun_path, "simctl", "install", device_id, app_artifact_path]

        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=180)
        if result.returncode == 0:
            return f"Installed {app_artifact_path} on {device_id}."
        return f"Install failed (exit {result.returncode}): {(result.stderr or result.stdout).strip()[:400]}"
    except Exception as e:
        return f"Install failed: {e}"


def launch_app_on_device(os_type: str, device_id: str, app_identifier: str, remote_url: str) -> object:
    """
    Connects to a remote Appium server and launches the application.
    
    Args:
        os_type: 'android' or 'ios'.
        device_id: The AVD name (Android) or UUID (iOS).
        app_identifier: Package Name (Android) or Bundle ID (iOS).
        remote_url: The URL of the remote Appium server (e.g., 'http://192.168.1.100:4723' or cloud URL).
    Returns:
        The WebDriver instance if successful, or an error message string if failed.
    """
    options = AppiumOptions()
    
    if os_type.lower() == "android":
        options.set_capability("platformName", "Android")
        options.set_capability("appium:automationName", "UiAutomator2")
        options.set_capability("appium:deviceName", device_id)
        # UiAutomator2 does NOT use appium:deviceName to pick which attached
        # device/emulator to drive -- that capability is purely descriptive.
        # It selects the device via `adb devices` instead, and if that ever
        # lists more than one entry (e.g. a stale/offline emulator left over
        # from a previous run, alongside the freshly booted one), session
        # creation fails outright with "more than one device/emulator"
        # unless appium:udid pins it to a specific serial. device_id here IS
        # that serial (see get_connected_android_device/ensure_device_running
        # above), so it must also be passed as udid, not just deviceName.
        options.set_capability("appium:udid", device_id)
        options.set_capability("appium:appPackage", app_identifier)
        # Fresh/loaded emulators routinely need well over Appium's 30s default to push+start
        # uiautomator2 server; bump the timeouts most affected by a slow/loaded fresh boot.
        options.set_capability("appium:uiautomator2ServerLaunchTimeout", 90000)
        options.set_capability("appium:uiautomator2ServerInstallTimeout", 90000)
        options.set_capability("appium:adbExecTimeout", 90000)
        
    elif os_type.lower() == "ios":
        options.set_capability("platformName", "iOS")
        options.set_capability("appium:automationName", "XCUITest")
        options.set_capability("appium:udid", device_id)
        options.set_capability("appium:bundleId", app_identifier)
    else:
        return "Error: os_type must be either 'android' or 'ios'."

    try:
        # For iOS, wait until WDA is actually listening before attempting the Appium session.
        # This is event-driven: we proceed the moment the socket responds, not after a fixed delay.
        if os_type.lower() == "ios":
            _wait_for_wda_ready()

        # A freshly-booted/loaded emulator can miss even the raised uiautomator2ServerLaunchTimeout
        # above; retrying the session creation alone (not the whole function) reliably works around
        # it -- confirmed empirically that an immediate re-run of the same step succeeds every time.
        session_attempts = 3
        retry_delay_seconds = 5
        driver = None
        last_error: Exception | None = None
        for attempt in range(1, session_attempts + 1):
            try:
                driver = webdriver.Remote(remote_url, options=options)
                break
            except Exception as e:
                last_error = e
                if attempt < session_attempts:
                    time.sleep(retry_delay_seconds)
        if driver is None:
            raise last_error
        
        driver.activate_app(app_identifier)
        
        # החזרת אובייקט הדרייבר במקום להגדיר אותו כגלובלי
        return driver 
        
    except Exception as e:
        return f"Failed to connect and launch app. Error: {str(e)}"


# ==========================================
# 6. Basic Navigation Smoke Test
# ==========================================
_CLICKABLE_CLASS_NAME = {
    "android": "android.widget.Button",
    "ios": "XCUIElementTypeButton",
}


def run_basic_navigation_smoke(
    driver: object, os_type: str, max_taps: int = 3, wait_seconds: float = 1.5
) -> dict:
    """Best-effort smoke test: taps up to `max_taps` visible buttons, checking after each (via
    `driver.page_source`, a cross-platform liveness check) that the app/driver session is still alive.
    Returns {"status": "Success"|"Fail"|"Skipped", "taps_performed": [...], "reason": <on Fail/Skipped>}."""
    class_name = _CLICKABLE_CLASS_NAME.get((os_type or "").lower(), _CLICKABLE_CLASS_NAME["android"])
    taps_performed: list[dict] = []

    try:
        buttons = driver.find_elements(AppiumBy.CLASS_NAME, class_name)
    except Exception as e:
        return {"status": "Fail", "reason": f"Could not query on-screen elements: {e}", "taps_performed": taps_performed}

    if not buttons:
        return {"status": "Skipped", "reason": "No tappable buttons found on the current screen.", "taps_performed": taps_performed}

    for button in buttons[:max_taps]:
        label = "<unlabeled>"
        try:
            label = button.text or button.get_attribute("content-desc") or label
            button.click()
            time.sleep(wait_seconds)
            driver.page_source  # liveness check: raises if the session/app died
            taps_performed.append({"label": label, "status": "ok"})
        except Exception as e:
            taps_performed.append({"label": label, "status": "error", "error": str(e)})
            return {"status": "Fail", "reason": f"App became unresponsive after tapping '{label}': {e}", "taps_performed": taps_performed}

    return {"status": "Success", "taps_performed": taps_performed}


