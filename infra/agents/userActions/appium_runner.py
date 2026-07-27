#!/usr/bin/env python3
"""Tap discovered AppsFlyer triggerIds via Appium (User Actions phase)."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Literal

from appium import webdriver
from appium.options.common import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy

Platform = Literal["android", "ios"]


def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open(encoding="utf-8") as handle:
        return json.load(handle)


_UPPER_XPATH_TRANSLATE = 'translate({attr},"abcdefghijklmnopqrstuvwxyz","ABCDEFGHIJKLMNOPQRSTUVWXYZ")'


def navigate_to_screen(driver: webdriver.Remote, navigation_path: list[str], wait_seconds: float) -> None:
    """Tap on-screen elements by visible text/label, in order, to reach the screen
    that hosts a triggerId not present on the app's main/launch screen (see
    navigationPath in events.wired.json / write_events_manifest).

    Matches case-insensitively: the SDK agent (an LLM) writes navigationPath
    as free text and isn't consistent about casing (e.g. "APPLES" vs the
    real on-screen "Apples") -- write_events_manifest's own check is
    case-insensitive too (it only confirms the label exists somewhere in the
    project), so an exact-case match here recreated the same
    NoSuchElementException that check was meant to prevent.
    """
    for label in navigation_path:
        label_upper = label.upper()
        xpath = (
            f'//*[{_UPPER_XPATH_TRANSLATE.format(attr="@text")}="{label_upper}" '
            f'or {_UPPER_XPATH_TRANSLATE.format(attr="@content-desc")}="{label_upper}"]'
        )
        driver.find_element(AppiumBy.XPATH, xpath).click()
        time.sleep(wait_seconds)


def tap_trigger(
    driver: webdriver.Remote,
    trigger_id: str,
    wait_seconds: float,
    navigation_path: list[str] | None = None,
) -> None:
    if navigation_path:
        navigate_to_screen(driver, navigation_path, wait_seconds)
    driver.find_element(AppiumBy.ACCESSIBILITY_ID, trigger_id).click()
    time.sleep(wait_seconds)


def run_discovered_events(
    driver: webdriver.Remote,
    config: dict[str, Any],
    only_event: str | None,
    wait_seconds: float,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for event in config.get("events", []):
        event_name = event.get("eventName")
        trigger_id = event.get("triggerId")
        if not event_name or not trigger_id:
            continue
        if only_event and event_name != only_event:
            continue

        tap_trigger(driver, trigger_id, wait_seconds, event.get("navigationPath"))
        results.append(
            {
                "eventName": event_name,
                "triggerId": trigger_id,
                "status": "tapped",
            }
        )
    return results


def build_driver(appium_url: str, config: dict[str, Any], platform: Platform) -> webdriver.Remote:
    options = AppiumOptions()
    capabilities: dict[str, Any] = {"appium:noReset": True}

    if platform == "android":
        capabilities["platformName"] = "Android"
        capabilities["appium:automationName"] = "UiAutomator2"
        if config.get("appPackage"):
            capabilities["appium:appPackage"] = config["appPackage"]
        if config.get("mainActivity"):
            capabilities["appium:appActivity"] = config["mainActivity"]
    else:
        capabilities["platformName"] = "iOS"
        capabilities["appium:automationName"] = "XCUITest"
        if config.get("bundleId"):
            capabilities["appium:bundleId"] = config["bundleId"]

    options.load_capabilities(capabilities)
    driver = webdriver.Remote(appium_url, options=options)
    # This session is created fresh right after the previous step
    # (emulator_node) just reinstalled + relaunched the app and quit its own
    # driver -- the first screen can still be mid-render at that exact
    # moment. Without this, find_element() below fails immediately
    # (NoSuchElementException) instead of polling for the element to
    # actually appear, which is what made navigate_to_screen's very first
    # lookup flaky right after a fresh install/launch.
    driver.implicitly_wait(10)
    return driver


def main() -> int:
    parser = argparse.ArgumentParser(description="Tap events from events.discovered.json")
    parser.add_argument("--platform", required=True, choices=["android", "ios"])
    parser.add_argument("--config", type=Path, default=Path("events.discovered.json"))
    parser.add_argument("--event", default=None, help="Run only one eventName")
    parser.add_argument("--appium-url", default="http://127.0.0.1:4723")
    parser.add_argument("--wait", type=float, default=2.0)
    args = parser.parse_args()

    config = load_config(args.config.resolve())
    config_platform = config.get("platform")
    if config_platform and config_platform != args.platform:
        raise SystemExit(f"Config platform={config_platform} differs from --platform={args.platform}")

    driver = build_driver(args.appium_url, config, args.platform)

    try:
        results = run_discovered_events(driver, config, args.event, args.wait)
    finally:
        driver.quit()

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
