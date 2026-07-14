from __future__ import annotations

import json
from pathlib import Path

from infra.agents.userActions.appium_runner import build_driver, run_discovered_events
from infra.agents.userActions.validators import validate_discovery, validate_taps


def load_events_manifest(manifest_path: Path) -> dict:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    for event in data.get("events", []):
        event.setdefault("source", ["manifest"])
    return data


def run_user_actions_pipeline(
    *,
    manifest_path: Path,
    platform: str,
    appium_url: str = "http://127.0.0.1:4723",
    wait_seconds: float = 2.0,
    only_event: str | None = None,
) -> dict:
    discovered = load_events_manifest(manifest_path)
    discovery_validation = validate_discovery(discovered)
    if not discovery_validation["passed"]:
        return {
            "status": "Fail",
            "phase": "discovery",
            "discovery_validation": discovery_validation,
            "tap_validation": None,
        }

    driver = build_driver(appium_url, discovered, platform)
    try:
        tap_results = run_discovered_events(driver, discovered, only_event, wait_seconds)
    finally:
        driver.quit()

    tap_validation = validate_taps(tap_results, discovered)
    return {
        "status": "Success" if tap_validation["passed"] else "Fail",
        "phase": "taps",
        "discovery_validation": discovery_validation,
        "tap_validation": tap_validation,
        "event_count": len(discovered.get("events", [])),
        "tap_count": len(tap_results),
    }
