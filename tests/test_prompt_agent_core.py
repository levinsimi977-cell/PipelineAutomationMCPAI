import os
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if "config" not in sys.modules:
    fake_config = types.ModuleType("config")
    fake_config.APP_ID = "test-app-id"
    fake_config.DEV_KEY = "test-dev-key"
    fake_config.GEMINI_MODEL = "gemini-1.5-flash"
    fake_config.GEMINI_API_KEY = ""
    sys.modules["config"] = fake_config

from infra.agents.promptGanertorAgent.tools import prompt_agent_core  # noqa: E402


class FakeLLM:
    def invoke(self, value):
        return str(value)


def test_prompt_agent_includes_explicit_deeplink_mcp_instructions(monkeypatch):
    monkeypatch.setattr(prompt_agent_core, "llm", FakeLLM())

    state = {
        "platform": "ios",
        "app_path": "/tmp/demo-app",
        "current_use_case": {
            "platform": "ios",
            "prompt_goal": "Integrate AppsFlyer SDK and deep linking",
            "app_path": "/tmp/demo-app",
            "answer_policy": {
                "ios_minimal": {"use_att": True},
                "deeplink": {
                    "use_deep_linking": True,
                    "uri_scheme": "myapp",
                    "use_custom_uri_scheme": True,
                },
            },
        },
    }

    result = prompt_agent_core.prompt_agent_node(state)

    prompts = result["agent_prompts"]
    combined = "\n".join(prompts.values())

    assert "createIosDeepLink" in combined
    assert "guideDeepLinkTesting" in combined
    assert "verifyIosDeepLink" in combined
    assert "custom URI scheme" in combined.lower()
