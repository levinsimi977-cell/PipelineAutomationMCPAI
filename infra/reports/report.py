"""
MCP tool-order validator.

Inputs (must be supplied by the caller — this module does NOT fetch them):
  - platform: target platform for the run, e.g. "android" or "ios".
              Expected source: use-case / run config (e.g. data/useCases/useCase.json)
              or the orchestration layer (infra/aplication/app.py).

  - call_log: ordered list of MCP tool invocations from the agent run.
              Each entry: {"tool": "<name>", "action": "<optional>"} for multi-step iOS tools.
              Expected source: SDK agent output or MCP session trace
              (infra/agents/sdkAgent/tools/agent.py → passed through app.py).
"""


class McpToolOrderValidator:
    def __init__(self):
        # Platform-specific tools allowed in a validated sequence.
        self.android_tools = {
            "integrateSdk", "verifySdk", "createDeepLink", "guideDeepLinkTesting",
            "verifyDeepLink", "createInAppEvent", "verifyInAppEvent",
        }
        self.ios_tools = {
            "integrateSdk", "verifyIosSdk", "createIosDeepLink", "guideDeepLinkTesting",
            "verifyIosDeepLink", "createIosInAppEvent", "verifyIosInAppEvent",
        }

        # Tools that may appear anywhere in the log without order constraints.
        self.independent_tools = {
            "getVersion", "fetchLogs", "getErrors", "getLaunchLogs",
            "getInAppLogs", "getConversionLogs", "getDeepLinkLogs",
        }

        # Optional catalog lookups; if used, they must appear before create-event tools.
        self.catalog_tools = {"getTopInAppEvents", "getInAppEventsByVertical"}
        self.create_event_tools = {"createInAppEvent", "createIosInAppEvent"}

        # Mandatory dependency rules: (pre, post) — post must not appear before pre.
        # post is the step id returned by _to_step_id (tool_action for multi-step iOS tools).
        self.rules = [
            ("integrateSdk", "verifySdk"),
            ("integrateSdk", "verifyIosSdk_prepare"),
            ("verifyIosSdk_prepare", "verifyIosSdk_verify"),
            ("integrateSdk", "createDeepLink"),
            ("integrateSdk", "createIosDeepLink"),
            ("guideDeepLinkTesting", "verifyDeepLink"),
            ("guideDeepLinkTesting", "verifyIosDeepLink_prepare"),
            ("verifyIosDeepLink_prepare", "verifyIosDeepLink_verify"),
            ("integrateSdk", "createInAppEvent"),
            ("integrateSdk", "createIosInAppEvent"),
            ("createInAppEvent", "verifyInAppEvent"),
            ("createIosInAppEvent", "verifyIosInAppEvent_prepare"),
            ("verifyIosInAppEvent_prepare", "verifyIosInAppEvent_verify"),
        ]

    def validate_sequence(self, call_log, platform):
        """
        Validate that call_log respects platform rules and tool dependency order.

        Args:
            call_log: ordered tool invocations — fetch from agent/MCP run output (see module docstring).
            platform: "android" or "ios" — fetch from use-case config or app orchestration.
        """
        if not call_log:
            return True, "Log is empty"

        p = platform.lower()
        allowed = self.android_tools if p == "android" else self.ios_tools
        processed_log = [self._to_step_id(entry) for entry in call_log]

        for i, entry in enumerate(call_log):
            tool_name = entry["tool"]
            current_step = processed_log[i]
            prefix = processed_log[:i]

            # Independent tools are ignored for ordering checks.
            if tool_name in self.independent_tools:
                continue

            # Reject tools that do not belong to the target platform.
            if tool_name not in allowed and tool_name not in self.catalog_tools:
                return False, f"Tool '{tool_name}' not supported for '{p}'"

            # guideDeepLinkTesting requires at least one create tool earlier in the log.
            if tool_name == "guideDeepLinkTesting":
                if not any(t in prefix for t in ("createDeepLink", "createIosDeepLink")):
                    return False, "Rule Violation: 'guideDeepLinkTesting' requires a 'create' tool first."
                continue

            # Catalog is optional, but if present it must come before create-event tools.
            if tool_name in self.create_event_tools:
                for cat in self.catalog_tools:
                    if cat in processed_log and cat not in prefix:
                        return False, f"Rule Violation: '{current_step}' before '{cat}'."

            # Enforce mandatory (pre, post) dependency rules.
            for pre, post in self.rules:
                if current_step == post and pre not in prefix:
                    return False, f"Rule Violation ({p}): '{post}' requires '{pre}' first."

        return True, "Sequence is valid"

    def _to_step_id(self, entry):
        """Normalize a log entry to a unique step id, e.g. verifyIosSdk_prepare."""
        tool = entry["tool"]
        action = entry.get("action")
        return f"{tool}_{action}" if action else tool
