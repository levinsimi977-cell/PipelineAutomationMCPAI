from __future__ import annotations

from typing import Any


def validate_discovery(discovered: dict[str, Any]) -> dict[str, Any]:
    """High-level checks after the event-discovery phase.

    Confirms that events were found, each has a triggerId, XML layout
    documentation (layoutFile), and audit documentation (source contains
    "audit").
    """
    events = discovered.get("events", [])

    has_events = len(events) > 0
    trigger_ids_present = all(event.get("triggerId") for event in events)
    xml_documented = all(event.get("layoutFile") for event in events)
    audit_documented = all("audit" in event.get("source", []) for event in events)

    passed = has_events and trigger_ids_present and xml_documented and audit_documented

    return {
        "passed": passed,
        "checks": {
            "has_events": has_events,
            "trigger_ids_present": trigger_ids_present,
            "xml_documented": xml_documented,
            "audit_documented": audit_documented,
        },
        "event_count": len(events),
    }


def validate_taps(
    tap_results: list[dict[str, str]],
    discovered: dict[str, Any],
) -> dict[str, Any]:
    """High-level checks after the user-tap simulation phase.

    Confirms that a tap result exists for every discovered event and that
    each result reports status \"tapped\".
    """
    expected_count = len(discovered.get("events", []))
    actual_count = len(tap_results)
    all_events_tapped = actual_count == expected_count
    all_status_tapped = all(result.get("status") == "tapped" for result in tap_results)

    passed = expected_count > 0 and all_events_tapped and all_status_tapped

    return {
        "passed": passed,
        "checks": {
            "all_events_tapped": all_events_tapped,
            "all_status_tapped": all_status_tapped,
        },
        "expected_count": expected_count,
        "actual_count": actual_count,
        "tap_results": tap_results,
    }
