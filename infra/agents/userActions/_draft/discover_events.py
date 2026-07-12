#!/usr/bin/env python3
"""Discover wired in-app events from AuditRecord."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Platform = Literal["android", "ios"]


@dataclass
class DiscoveredEvent:
    event_name: str
    trigger_id: str
    layout_file: str | None = None
    view_id: str | None = None
    source: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eventName": self.event_name,
            "triggerId": self.trigger_id,
            "layoutFile": self.layout_file,
            "viewId": self.view_id,
            "source": sorted(self.source),
        }


def merge_event(
    bucket: dict[str, DiscoveredEvent],
    event_name: str,
    trigger_id: str,
    *,
    source: str,
    layout_file: str | None = None,
    view_id: str | None = None,
) -> None:
    expected = f"af_trigger_{event_name}"
    if trigger_id != expected:
        return

    item = bucket.get(event_name)
    if item is None:
        item = DiscoveredEvent(event_name=event_name, trigger_id=trigger_id)
        bucket[event_name] = item

    item.source.add(source)
    if layout_file:
        item.layout_file = layout_file
    if view_id:
        item.view_id = view_id


def parse_audit_details(details: str) -> dict[str, Any]:
    if not details:
        return {}
    try:
        payload = json.loads(details)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    extracted: dict[str, Any] = {}
    for key in ("eventName", "triggerId", "layoutFile", "viewId", "appPackage", "mainActivity", "bundleId"):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', details)
        if match:
            extracted[key] = match.group(1)
    return extracted


def discover_from_audit(audit: dict[str, Any], bucket: dict[str, DiscoveredEvent]) -> int:
    count = 0

    for item in audit.get("events", []):
        details = parse_audit_details(str(item.get("details", "")))
        event_name = details.get("eventName")
        trigger_id = details.get("triggerId")
        if not event_name or not trigger_id:
            continue
        if not str(event_name).startswith("af_"):
            continue

        merge_event(
            bucket,
            str(event_name),
            str(trigger_id),
            source="audit",
            layout_file=details.get("layoutFile"),
            view_id=details.get("viewId"),
        )
        count += 1

    return count


def build_output(
    *,
    platform: Platform,
    audit: dict[str, Any],
    bucket: dict[str, DiscoveredEvent],
    audit_count: int,
) -> dict[str, Any]:
    warnings: list[str] = []
    run_platform = audit.get("run", {}).get("platform")
    if run_platform and run_platform != platform:
        warnings.append(f"audit run.platform={run_platform} differs from --platform={platform}")

    app_package = None
    main_activity = None
    bundle_id = None
    for item in audit.get("events", []):
        details = parse_audit_details(str(item.get("details", "")))
        app_package = app_package or details.get("appPackage")
        main_activity = main_activity or details.get("mainActivity")
        bundle_id = bundle_id or details.get("bundleId")

    output: dict[str, Any] = {
        "platform": platform,
        "sources": {"audit": audit_count, "merged": len(bucket)},
        "warnings": warnings,
        "events": [bucket[key].to_dict() for key in sorted(bucket)],
    }

    if platform == "android":
        output["appPackage"] = app_package
        output["mainActivity"] = main_activity
    else:
        output["bundleId"] = bundle_id

    return output


def discover(*, audit_path: Path, platform: Platform) -> dict[str, Any]:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    bucket: dict[str, DiscoveredEvent] = {}
    audit_count = discover_from_audit(audit, bucket)
    return build_output(platform=platform, audit=audit, bucket=bucket, audit_count=audit_count)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build events.discovered.json from AuditRecord.")
    parser.add_argument("--platform", required=True, choices=["android", "ios"])
    parser.add_argument("--audit", type=Path, required=True, help="Path to AuditRecord JSON")
    parser.add_argument("--output", type=Path, default=Path("events.discovered.json"))
    args = parser.parse_args()

    result = discover(audit_path=args.audit, platform=args.platform)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "events": len(result["events"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
