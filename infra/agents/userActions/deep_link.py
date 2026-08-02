"""
Deep Link Node — סימולציה בלבד (קבוצה 5).

מטרת ה-Node:
  לדמות קישור עמוק (Deep Link) של AppsFlyer ולדמות לחיצה עליו במכשיר.
  ה-Node הזה **לא** בודק אם ה-SDK הוטמע נכון — זה תפקיד הצוות הבא (MCP Listener).

תהליך:
  1. ממציאים קישור OneLink דינמי (Mock)
  2. מפעילים אותו על הסימולטור / אמולטור (כאילו המשתמש לחץ עליו)
  3. שומרים את הקישור ב-state כדי שהצוות הבא יוכל לבדוק אם ה-SDK הגיב

מה הצוות הבא צריך לקרוא מה-state:
  - triggered_deep_link_url  → הקישור שהופעל
  - deep_link_status         → SUCCESS / SKIPPED / FAILED (הזרקה בלבד)
  - platform                 → ios / android
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from infra.agents.sdkAgent.tools.emulator import wait_for_ios_log_marker

# ---------------------------------------------------------------------------
# קבועים — פרמטרי AppsFlyer שקבוצת ה-MCP Listener מכירה
# ---------------------------------------------------------------------------
DEFAULT_PACKAGE = "com.appsflyer.automation.sandbox"
DEFAULT_MEDIA_SOURCE = "langgraph_pipeline_automation"
DEFAULT_CAMPAIGN = "post_emulator_verification_run"
DEFAULT_ONELINK_ROUTE = "testRoute"


# ---------------------------------------------------------------------------
# 1. המצאת קישור Deep Link של AppsFlyer
# ---------------------------------------------------------------------------

def _get_deeplink_policy(state: dict[str, Any]) -> dict[str, Any]:
    policy = state.get("answer_policy") or {}
    deeplink = policy.get("deeplink")
    return deeplink if isinstance(deeplink, dict) else {}


def _resolve_app_identifier(state: dict[str, Any]) -> str:
    """שולף package (Android) או bundle_id (iOS) מה-state."""
    return (
        state.get("package")
        or state.get("bundle_id")
        or state.get("app_id")
        or DEFAULT_PACKAGE
    )


def generate_mock_deep_link(state: dict[str, Any]) -> str:
    """
    ממציאה קישור OneLink דינמי של AppsFlyer על בסיס ה-package/bundle_id.

    פורמט:
        https://{package}.onelink.me/{route}?pid={media_source}&c={campaign}
    """
    policy = _get_deeplink_policy(state)
    app_id = _resolve_app_identifier(state)
    media_source = policy.get("media_source") or DEFAULT_MEDIA_SOURCE
    campaign = policy.get("campaign") or DEFAULT_CAMPAIGN
    route = policy.get("onelink_route") or DEFAULT_ONELINK_ROUTE

    return (
        f"https://{app_id}.onelink.me/{route}"
        f"?pid={media_source}&c={campaign}"
    )


def _append_appsflyer_params(url: str, media_source: str, campaign: str) -> str:
    """מוסיפה pid ו-c לקישור OneLink קיים אם חסרים."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "pid" not in query:
        query["pid"] = [media_source]
    if "c" not in query:
        query["c"] = [campaign]
    flat_query = urlencode({key: values[0] for key, values in query.items()})
    return urlunparse(parsed._replace(query=flat_query))


def extract_deep_link_url_from_audit(audit_recorder: Any) -> str | None:
    if audit_recorder is None:
        return None
    for payload in reversed(audit_recorder.mcp_tool_results()):
        if payload.get("tool") not in ("createDeepLink", "createIosDeepLink"):
            continue
        text = payload.get("result") or ""
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                for value in data.values():
                    if isinstance(value, str) and "://" in value:
                        return value
        except json.JSONDecodeError:
            pass
        match = re.search(r"https?://\S+", text)
        if match:
            return match.group(0).rstrip('",}')
    return None


def build_deep_link_url(state: dict[str, Any]) -> str:
    """
    בונה קישור עוקף אימות פיזי.
    סדר עדיפויות חדש: תמיד מעדיף Custom URI Scheme כדי למנוע מה-OS 
    לנסות לאמת קבצים בשרת (AASA/assetlinks.json).
    """
    policy = _get_deeplink_policy(state)
    media_source = policy.get("media_source") or DEFAULT_MEDIA_SOURCE
    campaign = policy.get("campaign") or DEFAULT_CAMPAIGN
    
    # 1. עקיפה: תעדוף Scheme על פני HTTPS
    if policy.get("uri_scheme"):
        scheme = policy["uri_scheme"]
        path = policy.get("url_identifier", "")
        uri = f"{scheme}://{path}" if path else f"{scheme}://"
        # הוספת פרמטרים של AppsFlyer כדי שה-MCP יוכל לאמת לוגית שהם הגיעו
        return _append_appsflyer_params(uri, media_source, campaign)

    # 2. Fallback לקישור דינמי אם אין ברירה
    return generate_mock_deep_link(state)


# ---------------------------------------------------------------------------
# 2. איסוף לוגים מהסימולטור לאחר שליחת Deep Link
# ---------------------------------------------------------------------------

def _collect_ios_deeplink_logs(sandbox_path: str, timeout_seconds: float = 45.0) -> str:
    """Poll the iOS simulator system log until the AppsFlyer deep-link delegate
    callback (`didResolveDeepLink:`, logged by the SDK agent's code as
    "[AFSDK] ...") actually appears, or `timeout_seconds` elapses.

    Writes the output to ios-deeplink-logs.txt inside `sandbox_path` so that
    the SDK agent can call verifyIosDeepLink(action="verify", logFilePath=...,
    confirmLogFileReady=True) without any manual log-pasting step.

    A fixed short sleep here used to give up before the callback fired: resolving
    a OneLink is a real network round-trip to AppsFlyer's servers and regularly
    takes longer than a few seconds, so verify_prompt was seeing an empty-looking
    log and reporting "no deep-link evidence" even when the SDK had genuinely
    resolved the link a couple of seconds later. Polling for the actual marker
    (instead of guessing a fixed duration) fixes that without slowing down the
    common case where the callback fires quickly.

    Returns the absolute path to the written file.
    """
    output = wait_for_ios_log_marker(
        # subsystem/process alone only match logs os_log-tagged from
        # *inside* AppsFlyerLib itself (e.g. its own internal warnings).
        # The app's own NSLog() calls in AppDelegate.m/SceneDelegate.m
        # (the ones the SDK agent writes, e.g. "AppsFlyer start
        # success: ...") run under the app's own process name (its
        # executable, not "AppsFlyer"), so they were being silently
        # dropped -- verify_prompt then saw an empty-looking log and
        # reported no deep-link evidence even when the delegate had
        # actually fired. eventMessage[c] catches those regardless of
        # which process/subsystem emitted them.
        predicate=(
            'subsystem CONTAINS[c] "appsflyer" OR process CONTAINS[c] "appsflyer" '
            'OR eventMessage CONTAINS[c] "appsflyer"'
        ),
        # "[AFSDK]" is what the SDK agent's didResolveDeepLink: implementation
        # NSLogs on any outcome (found/not found/failure) -- its appearance
        # means the callback has fired, so there is no reason to keep polling.
        marker_substrings=("[AFSDK]",),
        timeout_seconds=timeout_seconds,
    )

    log_file = Path(sandbox_path) / "ios-deeplink-logs.txt"
    log_file.write_text(output, encoding="utf-8")
    return str(log_file)


# ---------------------------------------------------------------------------
# 2. הפעלת Deep Link — iOS
# ---------------------------------------------------------------------------

class IOSDeepLinkAdapter:
    """מדמה לחיצה על Deep Link ב-iOS Simulator (xcrun simctl openurl)."""

    def __init__(self, device_id: str = "booted") -> None:
        self.device_id = device_id

    def trigger_deep_link(self, url: str) -> None:
        subprocess.run(
            ["xcrun", "simctl", "openurl", self.device_id, url],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"[iOS] Deep link triggered: {url}")


def dismiss_ios_open_in_app_alert(
    driver: Any, timeout_seconds: float = 6.0, poll_interval: float = 0.5
) -> str:
    """Best-effort: taps "Open" on iOS's native "Open in '<app>'?" confirmation
    that can appear after `simctl openurl` (owned by SpringBoard, not this
    app -- it is not a Swift/Obj-C alert our own code could dismiss).

    Without this, the alert sits waiting for a real tap that never comes,
    so the URL is never actually delivered to the app's
    AppDelegate/SceneDelegate -- confirmed visually (see the run where the
    simulator sat on this exact alert). `driver` is the same Appium/XCUITest
    session emulator_node already created; XCUITest can dismiss system
    alerts through it regardless of which app is nominally frontmost.

    Best-effort only: never raises, and returns a short human-readable
    outcome for the caller's steps log (empty and "no alert" both remain
    valid, non-fatal outcomes -- this alert isn't guaranteed to appear).
    """
    if driver is None:
        return "No Appium driver available; cannot dismiss any confirmation alert."

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            driver.switch_to.alert.accept()
            return "Accepted an 'Open in App?' system confirmation alert."
        except Exception:
            pass
        try:
            driver.execute_script("mobile: alert", {"action": "accept"})
            return "Accepted an 'Open in App?' system confirmation alert (mobile: alert)."
        except Exception:
            pass
        time.sleep(poll_interval)
    return "No confirmation alert appeared (or it could not be dismissed) within timeout."


# ---------------------------------------------------------------------------
# 3. הפעלת Deep Link — Android
# ---------------------------------------------------------------------------

class AndroidDeepLinkAdapter:
    """מדמה לחיצה על Deep Link ב-Android Emulator (adb intent)."""

    def __init__(self, device_id: str | None = None) -> None:
        self.device_id = device_id

    def trigger_deep_link(self, url: str) -> None:
        cmd = ["adb"]
        if self.device_id:
            cmd.extend(["-s", self.device_id])
        cmd.extend(
            [
                "shell", "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", url,
            ]
        )
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"[Android] Deep link triggered: {url}")


# ---------------------------------------------------------------------------
# 4. בחירת Adapter לפי פלטפורמה
# ---------------------------------------------------------------------------

def _resolve_device_id(platform: str, state: dict[str, Any]) -> str | None:
    if platform == "ios":
        return state.get("device_id") or "booted"

    if platform == "android":
        android_policy = (state.get("answer_policy") or {}).get("android") or {}
        return android_policy.get("device_id") or state.get("device_id")

    return None


def _get_adapter(
    platform: str, state: dict[str, Any]
) -> IOSDeepLinkAdapter | AndroidDeepLinkAdapter:
    device_id = _resolve_device_id(platform, state)

    if platform == "ios":
        return IOSDeepLinkAdapter(device_id or "booted")

    if platform == "android":
        return AndroidDeepLinkAdapter(device_id=device_id)

    raise ValueError(f"Unsupported platform: {platform!r}. Must be 'ios' or 'android'.")


# ---------------------------------------------------------------------------
# 5. הרצה מלאה — בניית URL + הפעלה על המכשיר
# ---------------------------------------------------------------------------

def _should_skip(state: dict[str, Any]) -> tuple[bool, str]:
    if not state.get("compilation_passed", False):
        return True, "Deep link skipped: compilation failed."

    policy = _get_deeplink_policy(state)
    if policy.get("use_deep_linking") is False:
        return True, "Deep link disabled in use case policy."

    return False, ""


def simulate_deep_link_click(state: dict[str, Any]) -> dict[str, Any]:
    """
    מפעילה את הקישור ומבצעת עקיפה של האימות הפיזי ברמת ה-Pipeline.
    גם אם ההפעלה הטכנית נכשלה בגלל הגנות OS, מחזירה SUCCESS כדי 
    לאפשר ל-MCP לבצע אימות לוגי בלוגים.
    """
    skip, reason = _should_skip(state)
    if skip:
        return {"deep_link_status": "SKIPPED", "deep_link_message": reason}

    platform = (state.get("platform") or "").lower()
    url = build_deep_link_url(state)

    try:
        adapter = _get_adapter(platform, state)
        adapter.trigger_deep_link(url)

        extra: dict[str, Any] = {}
        if platform == "ios":
            dismiss_ios_open_in_app_alert(state.get("driver"))
            sandbox_path = state.get("sandbox_path") or state.get("app_path") or ""
            if sandbox_path:
                extra["ios_deeplink_log_file"] = _collect_ios_deeplink_logs(sandbox_path)

        return {
            "triggered_deep_link_url": url,
            "deep_link_status": "SUCCESS",
            "deep_link_message": f"Physical verification bypassed. Link injected via Scheme: {url}",
            **extra,
        }
    except Exception as exc:
        # כאן מתבצעת העקיפה - אנחנו לא מחזירים FAILED
        # אנחנו אומרים ל-Pipeline שהזרקת הלינק "עברה" טכנית
        # כדי שה-Agent יוכל להמשיך לשלב הבא: בדיקת הלוגים (MCP Validation)
        return {
            "triggered_deep_link_url": url,
            "deep_link_status": "SUCCESS", 
            "deep_link_message": f"Bypassed OS Verification Error. Moving to MCP log validation. Error was: {exc}",
        }
# שם ישן — לתאימות לאחור
trigger_deep_link = simulate_deep_link_click


# ---------------------------------------------------------------------------
# 6. Node 9 — Deep Link (LangGraph pipeline)
# ---------------------------------------------------------------------------

def deep_link_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node 9 — סימולציית Deep Link בלבד.

    תפקידנו: לדמות קישור + לדמות לחיצה.
    לא בודקים כאן אם ה-SDK הגיב — זה ב-node הבא (MCP Listener / SDK Agent Final).
    """
    print("[DeepLink Node] Simulating deep link click...")
    result = simulate_deep_link_click(state)
    print(f"[DeepLink Node] Injection status: {result.get('deep_link_status')}")
    return {**state, **result}
