"""Tests for sdk_project_tools (runPodInstall + integrateSdk hint shim)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.agents.sdkAgent.tools import sdk_project_tools as spt


class _FakeAudit:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def write(self, event_type: str, payload: dict):
        self.events.append((event_type, payload))


def test_podfile_pod_line_regex_detects_dependencies():
    assert spt._PODFILE_POD_LINE_RE.search("  pod 'AppsFlyerFramework'\n")
    assert not spt._PODFILE_POD_LINE_RE.search("target 'obj-c' do\nend\n")


def test_append_ios_pod_dependency_hint_adds_soft_line():
    text = "**1. Install AppsFlyer SDK with CocoaPods**\nIn your `Podfile`, add:\n"
    enriched = spt.append_ios_pod_dependency_hint(text)
    assert "dependencies must be installed" in enriched


def test_append_ios_pod_dependency_hint_is_idempotent():
    text = "Podfile updated.\nAfter CocoaPods setup steps, dependencies must be installed."
    assert spt.append_ios_pod_dependency_hint(text) == text


def test_find_podfile_directory_prefers_shallowest(tmp_path: Path):
    root = tmp_path / "sandbox"
    deep = root / "nested" / "ios"
    deep.mkdir(parents=True)
    (root / "Podfile").write_text("target 'app' do\nend\n", encoding="utf-8")
    (deep / "Podfile").write_text("target 'nested' do\nend\n", encoding="utf-8")
    assert spt.find_podfile_directory(root) == root


def test_run_pod_install_skips_when_podfile_has_no_pods(tmp_path: Path):
    project_root = tmp_path / "sandbox"
    ios_dir = project_root / "obj-c"
    ios_dir.mkdir(parents=True)
    (ios_dir / "Podfile").write_text("target 'obj-c' do\nend\n", encoding="utf-8")
    audit = _FakeAudit()
    tool = spt.build_run_pod_install_tool(project_root, "ios", audit)
    result = json.loads(tool.invoke({}))
    assert result["status"] == "SKIPPED"
    assert audit.events[-1][0] == "POD_INSTALL"


def test_run_pod_install_runs_pod_install(monkeypatch, tmp_path: Path):
    project_root = tmp_path / "sandbox"
    ios_dir = project_root / "obj-c"
    ios_dir.mkdir(parents=True)
    (ios_dir / "Podfile").write_text(
        "target 'obj-c' do\n  pod 'AppsFlyerFramework'\nend\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_run_pod_install_command(work_dir: Path, *, timeout: int = 120):
        calls.append(str(work_dir))
        return {"status": "OK", "working_directory": str(work_dir)}

    monkeypatch.setattr(spt, "run_pod_install_command", fake_run_pod_install_command)
    audit = _FakeAudit()
    tool = spt.build_run_pod_install_tool(project_root, "ios", audit)
    result = json.loads(tool.invoke({}))
    assert result["status"] == "OK"
    assert calls == [str(ios_dir)]


def test_wrap_integrate_sdk_with_ios_hint(monkeypatch):
    async def fake_integrate(**kwargs):
        return "Add to Podfile:\npod 'AppsFlyerFramework'"

    tool_obj = SimpleNamespace(name="integrateSdk", coroutine=fake_integrate)
    spt.wrap_integrate_sdk_with_ios_hint([tool_obj])
    result = asyncio.run(tool_obj.coroutine(platform="ios"))
    assert "dependencies must be installed" in result


def test_wrap_integrate_sdk_leaves_android_untouched():
    async def fake_integrate(**kwargs):
        return "Android Gradle setup only"

    tool_obj = SimpleNamespace(name="integrateSdk", coroutine=fake_integrate)
    spt.wrap_integrate_sdk_with_ios_hint([tool_obj])
    result = asyncio.run(tool_obj.coroutine(platform="android"))
    assert result == "Android Gradle setup only"
