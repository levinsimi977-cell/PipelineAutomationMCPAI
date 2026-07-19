"""
Regression tests for infra/agents/sdkAgent/tools/agent.py's live device_id
binding.

Bug this guards against: agent_prompts/tools are built once during
integrate_prompt -- before emulator_node has ever run -- and reused
unchanged for the later event_prompt/verify_prompt turns, where a real
device is actually needed (verifySdk/fetchLogs/getDeepLinkLogs/
verifyInAppEvent all take a `deviceId` argument). The LLM therefore never
reliably knew the real connected device serial and was observed guessing a
common default like "emulator-5554", which fails with "device not
connected" whenever the real, already-booted serial differs.

Fix: instead of trying to inject the device id as text into the agent's
prompt, the actual MCP tool call is intercepted and its `deviceId` argument
is forced from a plain mutable "variable" (`device_id_holder`, stored on
the agent's session) that infra/workflow/workflow_nodes.py's sdk_agent_node
keeps up to date via state["device_id"] on every call.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pydantic import BaseModel

import infra.agents.sdkAgent.tools.agent as agent


class _ArgsWithDeviceId(BaseModel):
    platform: str
    deviceId: str | None = None


class _ArgsWithoutDeviceId(BaseModel):
    platform: str


def _make_fake_tool(name, args_schema, coroutine):
    return SimpleNamespace(name=name, args_schema=args_schema, coroutine=coroutine)


def test_tool_accepts_device_id_true_when_schema_has_field():
    tool_obj = _make_fake_tool("verifySdk", _ArgsWithDeviceId, None)
    assert agent._tool_accepts_device_id(tool_obj) is True


def test_tool_accepts_device_id_false_when_schema_lacks_field():
    tool_obj = _make_fake_tool("integrateSdk", _ArgsWithoutDeviceId, None)
    assert agent._tool_accepts_device_id(tool_obj) is False


def test_tool_accepts_device_id_false_when_no_args_schema():
    tool_obj = _make_fake_tool("customTool", None, None)
    assert agent._tool_accepts_device_id(tool_obj) is False


def test_bind_live_device_id_overrides_whatever_llm_guessed(monkeypatch):
    calls = []

    async def fake_coroutine(**kwargs):
        calls.append(kwargs)
        return "ok"

    tool_obj = _make_fake_tool("verifySdk", _ArgsWithDeviceId, fake_coroutine)
    holder = {"device_id": "emulator-5556"}
    agent._bind_live_device_id([tool_obj], holder)

    result = asyncio.run(tool_obj.coroutine(platform="android", deviceId="emulator-5554"))

    assert calls == [{"platform": "android", "deviceId": "emulator-5556"}]
    assert result == "ok"


def test_bind_live_device_id_drops_key_when_holder_has_no_device():
    calls = []

    async def fake_coroutine(**kwargs):
        calls.append(kwargs)
        return "ok"

    tool_obj = _make_fake_tool("verifySdk", _ArgsWithDeviceId, fake_coroutine)
    holder = {"device_id": None}
    agent._bind_live_device_id([tool_obj], holder)

    asyncio.run(tool_obj.coroutine(platform="android", deviceId="emulator-5554"))

    assert calls == [{"platform": "android"}]


def test_bind_live_device_id_reads_holder_fresh_on_each_call():
    """The holder is mutated in place between calls (by
    run_sdk_integration_agent, from state["device_id"]) -- the wrapper must
    read it fresh every time, not capture a value once at wrap time."""
    calls = []

    async def fake_coroutine(**kwargs):
        calls.append(kwargs)
        return "ok"

    tool_obj = _make_fake_tool("fetchLogs", _ArgsWithDeviceId, fake_coroutine)
    holder = {"device_id": "emulator-5554"}
    agent._bind_live_device_id([tool_obj], holder)

    asyncio.run(tool_obj.coroutine(platform="android"))
    holder["device_id"] = "emulator-5556"
    asyncio.run(tool_obj.coroutine(platform="android"))

    assert [c.get("deviceId") for c in calls] == ["emulator-5554", "emulator-5556"]


def test_bind_live_device_id_leaves_unrelated_tools_untouched():
    async def fake_coroutine(**kwargs):
        return "ok"

    tool_obj = _make_fake_tool("integrateSdk", _ArgsWithoutDeviceId, fake_coroutine)
    agent._bind_live_device_id([tool_obj], {"device_id": "emulator-5556"})

    assert tool_obj.coroutine is fake_coroutine


class _FakeAudit:
    def write(self, *args, **kwargs):
        pass


class _FakeSdkAgent:
    async def ainvoke(self, payload, config=None):
        return {"messages": []}


def test_device_id_holder_refreshes_across_calls_for_resumed_session(monkeypatch):
    """
    Regression test: sdk_agent_node re-invokes run_sdk_integration_agent for
    each phase (integrate_prompt, event_prompt, verify_prompt) reusing the
    same agent_id/session. emulator_node only sets state["device_id"]
    between phases, so the session's device_id_holder must pick up the new
    value on the *next* call, not just at session-creation time.
    """
    monkeypatch.setattr(agent, "_AGENT_SESSIONS", {})
    created_holders = []

    async def fake_create_sdk_integration_agent(*args, **kwargs):
        created_holders.append(kwargs["device_id_holder"])
        return {"agent": _FakeSdkAgent(), "tools": [], "initial_prompt": "go"}

    monkeypatch.setattr(agent, "create_sdk_integration_agent", fake_create_sdk_integration_agent)
    monkeypatch.setattr(agent, "listener_on_agent_turn", lambda *a, **k: ("done", None, {}))

    state = {"device_id": None, "app_id": "com.example.app", "dev_key": "k"}

    result1 = asyncio.run(
        agent.run_sdk_integration_agent(
            state=state,
            project_root_str="/proj",
            platform="android",
            user_prompt="integrate the SDK",
            audit_recorder=_FakeAudit(),
        )
    )
    assert result1["status"] == "SUCCESS"
    agent_id = state["agent_id"]
    assert created_holders[0]["device_id"] is None

    # emulator_node has since booted a device for the next phase.
    state["device_id"] = "emulator-5556"

    asyncio.run(
        agent.run_sdk_integration_agent(
            state=state,
            project_root_str="/proj",
            platform="android",
            user_prompt="verify the SDK",
            audit_recorder=_FakeAudit(),
        )
    )

    # Same agent_id/session resumed (create_sdk_integration_agent not called again).
    assert state["agent_id"] == agent_id
    assert len(created_holders) == 1
    assert agent._AGENT_SESSIONS[agent_id]["device_id_holder"]["device_id"] == "emulator-5556"
