"""
MCP tool-order validator.

Inputs:
  - recorder: AuditRecorder instance (infra/agents/AuditRecorder.py).
              call_log is fetched inside this module via recorder.mcp_tool_results().
  - platform: run platform, e.g. "android" or "ios".
              Expected source: use-case config or infra/aplication/app.py.
"""

from infra.agents.AuditRecorder import AuditRecorder


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

        # Independent tools — may appear anywhere in the log without order checks.
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

    def validate_sequence(self, recorder: AuditRecorder, platform: str) -> tuple[bool, str]:
        """
        Validate MCP tool order for a run.

        Fetches call_log from recorder.mcp_tool_results() (ordered tool payloads).

        Args:
            recorder: AuditRecorder with MCP_TOOL_RESULT events from the agent run.
            platform: "android" or "ios".
        """
        call_log = recorder.mcp_tool_results()
        return self._validate_call_log(call_log, platform)

    def _validate_call_log(self, call_log, platform):
        """Run order checks on an already-built call_log list."""
        if not call_log:
            return False, "Log is empty — no tools were invoked"

        p = platform.lower()
        allowed = self.android_tools if p == "android" else self.ios_tools
        processed_log = [self._to_step_id(entry) for entry in call_log]

        for i, entry in enumerate(call_log):
            tool_name = entry["tool"]
            current_step = processed_log[i]
            prefix = processed_log[:i]

            # Independent tools — skip order validation.
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

            # Catalog is optional — if present, it must come before create-event tools.
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

"""Backward-compatible entrypoint for report generation."""

from infra.user_interface_use_case.reports.reporter import (
    ReportGenerator,
    generate_html_report,
)

__all__ = ["McpToolOrderValidator", "ReportGenerator", "generate_html_report"]
