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


def tap_trigger(driver: webdriver.Remote, trigger_id: str, wait_seconds: float) -> None:
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

        tap_trigger(driver, trigger_id, wait_seconds)
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
    return webdriver.Remote(appium_url, options=options)


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
