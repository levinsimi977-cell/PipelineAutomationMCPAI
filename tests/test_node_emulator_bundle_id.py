from __future__ import annotations

import plistlib
from pathlib import Path

import infra.workflow.nodes.nodeEmulator as node_emulator


def test_read_bundle_id_from_app_reads_info_plist(tmp_path: Path) -> None:
    app_dir = tmp_path / "TestApp.app"
    app_dir.mkdir()
    plist_path = app_dir / "Info.plist"
    plistlib.dump({"CFBundleIdentifier": "com.example.TestApp"}, plist_path.open("wb"))

    assert node_emulator._read_bundle_id_from_app(str(app_dir)) == "com.example.TestApp"


def test_read_bundle_id_from_app_returns_none_when_info_plist_missing(tmp_path: Path) -> None:
    app_dir = tmp_path / "NoPlist.app"
    app_dir.mkdir()

    assert node_emulator._read_bundle_id_from_app(str(app_dir)) is None
