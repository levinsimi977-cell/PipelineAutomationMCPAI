"""
Pipeline run report builder.

Reads existing data from:
  - workflow state (PipelineState from workflow_nodes)
  - AuditRecorder (in-memory or data/runs/<run_id>/audit.jsonl)

Writes HTML to:
  data/reports/<YYYY-MM-DD>/<run_id>/report.html
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

if TYPE_CHECKING:
    from infra.agents.AuditRecorder import AuditRecorder

DATA_REPORTS_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = DATA_REPORTS_DIR / "templates"


@dataclass
class NormalizedAuditEvent:
    index: int
    timestamp: str
    phase: str
    source: str
    event: str
    status: str
    details: str


class RunReportBuilder:
    """Build HTML report from workflow state + AuditRecorder events."""

    WORKFLOW_NODE_ORDER = [
        "json_use_case_input",
        "artifact_generator",
        "environment_setup",
        "prompt_agent",
        "sdk_agent",
        "compilation_check",
        "user_actions",
        "emulator",
        "deep_link",
        "test_runner",
        "visual_report",
    ]

    WORKFLOW_NODE_LABELS = {
        "json_use_case_input": "JSON Use Case Input",
        "artifact_generator": "Artifact Generator",
        "environment_setup": "Environment Setup",
        "prompt_agent": "Prompt Agent",
        "sdk_agent": "SDK Agent",
        "compilation_check": "Compilation Check",
        "user_actions": "User Actions",
        "emulator": "Emulator",
        "deep_link": "Deep Link",
        "test_runner": "Test Runner",
        "visual_report": "Visual Report",
    }

    STATE_SECTIONS: list[dict[str, Any]] = [
        {
            "title": "Use Cases",
            "description": "General use-case selection and paths for the current run.",
            "keys": (
                "user_id", "use_case_ids", "selected_use_cases", "selected_use_cases_path",
                "use_case_count", "primary_use_case_id", "primary_use_case_name",
                "use_cases_dir", "current_use_case_path", "current_use_case", "answer_policy",
            ),
        },
        {
            "title": "Run & Application",
            "description": "Run identity, platform, and application credentials.",
            "keys": (
                "run_id", "platform", "app_id", "app_status",
                "dev_key_configured", "dev_key_source",
            ),
        },
        {
            "title": "Sandbox & Infrastructure",
            "description": "Cloned app, Appium server, devices, and automation driver.",
            "keys": (
                "remote_url", "available_devices", "agent_model", "app_path",
                "original_app_path", "sandbox_path", "driver", "device_id",
                "execution_result", "environment_setup_status", "environment_setup_result",
                "task_4_application_validation",
            ),
        },
        {
            "title": "MCP Protocol",
            "description": "Health check and tool availability / usage against the MCP server.",
            "keys": (
                "mcp_health_check", "task_3_mcp_alive", "mcp_tools_available", "mcp_tools_availble",
                "mcp_tools_call", "mcp_tools_used", "mcp_tools_used_success", "mcp_integration_text",
                "call_log", "mcp_sequence",
            ),
        },
        {
            "title": "Agent Orchestration",
            "description": "Prompt types, rounds, installation answers, and agent status.",
            "keys": (
                "type_agent", "type_aggent", "agent_prompts", "last_prompt_type", "question_rounds",
                "installation_answers", "test_status", "last_agent_message", "last_aggent_massage",
                "prompt_just_run", "agent_id", "fail_reason", "prompt_agent_node_status",
            ),
        },
        {
            "title": "SDK Agent ↔ MCP Conversation",
            "description": "Installer prompt, answer-agent prompt, and audit path.",
            "keys": ("prompt_agent_sdk", "prompt_agent_answer", "audit_path", "audit_recorder"),
        },
        {
            "title": "Post-Installation",
            "description": "Emulator launch validation after SDK installation.",
            "keys": ("emulator_checking",),
        },
        {
            "title": "Test Results",
            "description": "Deep link, tool order, and file modification checks.",
            "keys": (
                "is_verify_deep_link", "is_verify_deep_link_message", "is_verify_deep_link_massage",
                "is_tool_order_valid", "is_tool_order_valid_message", "is_tool_order_valid_massage",
                "files_modified", "applied_files", "deep_link_status",
            ),
        },
        {
            "title": "Compilation",
            "description": "Build validation output from compilation_check_node.",
            "keys": ("compilation_passed", "compilation_result", "audit_events"),
        },
        {
            "title": "Final",
            "description": "Report output path and SDK verification summary.",
            "keys": ("report_path", "sdk_verified", "started_at", "ended_at"),
        },
    ]

    NODE_PURPOSE = {
        "json_use_case_input": "Loads selected use cases into JSON files for the run.",
        "artifact_generator": "Builds prompts, rules, and test artifacts for the active use case.",
        "environment_setup": "Clones the app sandbox and validates MCP connectivity.",
        "prompt_agent": "Generates integrate, event, and verify prompts.",
        "sdk_agent": "Runs SDK integration, in-app events, and verification via MCP.",
        "compilation_check": "Builds the sandboxed project to verify compilation.",
        "user_actions": "Simulates user interactions required for in-app validation.",
        "emulator": "Starts Appium, boots the device, and launches the app.",
        "deep_link": "Verifies deep link handling and destination screen.",
        "test_runner": "Executes the automated test suite.",
        "visual_report": "Generates the HTML run report.",
    }

    LEGACY_VISITED_KEYS = {
        "compilation_check": "visited_compilation_check",
        "user_actions": "visited_user_actions",
    }

    NON_SERIALIZABLE_KEYS = frozenset({"audit_recorder", "driver"})

    def __init__(self, templates_dir: Path | None = None) -> None:
        self.templates_dir = templates_dir or TEMPLATES_DIR
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def build(
        self,
        state: dict[str, Any],
        audit_events: list[dict[str, Any]],
        output_path: Path,
        *,
        index_url: str | None = None,
    ) -> Path:
        normalized = self._normalize_events(audit_events)
        summary = self._build_summary(state, normalized)
        workflow_detail = self._build_workflow_detail(state)
        html = self.env.get_template("run_report.html.j2").render(
            generated_at=self._format_display_datetime(),
            summary=summary,
            validation=self._build_validation(state, workflow_detail),
            state_sections=self._build_state_sections(state),
            events=normalized,
            workflow_detail=workflow_detail,
            audit_detail=self._build_audit_detail(audit_events),
            mcp_conversation=self._build_mcp_conversation(audit_events),
            mcp_validation=self._build_mcp_validation(state, audit_events),
            index_url=index_url,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        return output_path

    def evaluate(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Same pass/failed/unknown verdict shown at the top of a detail report,
        without rendering the full HTML. Used to label a use case's card on
        the run's index page with the same status the detail report agrees on.
        """
        return self._build_validation(state, self._build_workflow_detail(state))

    def _normalize_events(self, audit_events: list[dict[str, Any]]) -> list[NormalizedAuditEvent]:
        normalized: list[NormalizedAuditEvent] = []
        for index, raw in enumerate(audit_events or [], start=1):
            if "event_type" in raw and "payload" in raw:
                event_type = str(raw.get("event_type") or "unknown")
                payload = raw.get("payload") or {}
                normalized.append(
                    NormalizedAuditEvent(
                        index=index,
                        timestamp=self._normalize_timestamp(raw.get("timestamp")),
                        phase=self._phase_from_event_type(event_type),
                        source=self._infer_source(event_type, payload),
                        event=event_type,
                        status=self._status_from_event_type(event_type, payload),
                        details=self._format_payload(payload),
                    )
                )
                continue

            source = str(raw.get("source") or raw.get("component") or "unknown")
            event = str(raw.get("event") or raw.get("action") or "unknown_event")
            details = str(raw.get("details") or raw.get("message") or "")
            normalized.append(
                NormalizedAuditEvent(
                    index=index,
                    timestamp=self._normalize_timestamp(raw.get("timestamp") or raw.get("time")),
                    phase=str(raw.get("phase") or self._infer_phase(source, event, details)),
                    source=source,
                    event=event,
                    status=self._normalize_status(str(raw.get("status") or raw.get("result") or "info")),
                    details=details,
                )
            )
        return normalized

    def _collect_workflow_log_entries(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Merge nodes_log and nodes_logs from state, preserving order."""
        entries: list[dict[str, Any]] = []
        for index, log in enumerate(state.get("nodes_log") or []):
            if isinstance(log, dict):
                entries.append({**log, "_log_index": index, "_log_source": "nodes_log"})
        for index, log in enumerate(state.get("nodes_logs") or []):
            if isinstance(log, dict):
                entries.append({**log, "_log_index": index, "_log_source": "nodes_logs"})
        return entries

    def _aggregate_node_status(self, runs: list[dict[str, Any]]) -> str:
        statuses = [self._normalize_status(str(r.get("status") or "info")) for r in runs]
        if "failed" in statuses:
            return "failed"
        if "warning" in statuses:
            return "warning"
        if "passed" in statuses:
            return "passed"
        return "info"

    def _format_run_summary(self, run: dict[str, Any]) -> str:
        for key in ("message", "reason", "text_preview", "listener", "answer"):
            value = run.get(key)
            if value:
                return str(value)
        return "—"

    def _node_is_visited_key(self, node_id: str) -> str:
        return f"{node_id}_is_visited"

    def _node_log_key(self, node_id: str) -> str:
        return f"{node_id}_log"

    def _resolve_node_is_visited(
        self,
        state: dict[str, Any],
        node_id: str,
        log_entries: list[dict[str, Any]],
    ) -> bool:
        visited_key = self._node_is_visited_key(node_id)
        if visited_key in state:
            return self._parse_bool(state[visited_key]) is True
        legacy_key = self.LEGACY_VISITED_KEYS.get(node_id)
        if legacy_key and legacy_key in state:
            return self._parse_bool(state[legacy_key]) is True
        if log_entries:
            return self._aggregate_node_status(log_entries) == "passed"
        return False

    def _resolve_node_log(
        self,
        state: dict[str, Any],
        node_id: str,
        nodes_log_entries: list[dict[str, Any]],
        nodes_logs_entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        log_key = self._node_log_key(node_id)
        explicit = state.get(log_key)
        if isinstance(explicit, dict):
            return [explicit]
        if isinstance(explicit, list):
            return [entry for entry in explicit if isinstance(entry, dict)]
        merged = [
            {k: v for k, v in entry.items() if not k.startswith("_")}
            for entry in nodes_log_entries + nodes_logs_entries
        ]
        return merged

    def _node_log_status_label(self, log_entries: list[dict[str, Any]], aggregate: str) -> str:
        if not log_entries:
            return "—"
        if aggregate == "passed":
            return "Success"
        if aggregate == "failed":
            return "Fail"
        last_status = str(log_entries[-1].get("status") or "")
        normalized = self._normalize_status(last_status)
        if normalized == "passed":
            return "Success"
        if normalized == "failed":
            return "Fail"
        return last_status or "—"

    def _node_checks(
        self,
        state: dict[str, Any],
        node_id: str,
        nodes_log_entries: list[dict[str, Any]],
        nodes_logs_entries: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Human-readable checks shown when a workflow node is expanded."""
        log_entries = self._resolve_node_log(state, node_id, nodes_log_entries, nodes_logs_entries)
        visited = self._resolve_node_is_visited(state, node_id, log_entries)

        if log_entries:
            status_value = str(log_entries[-1].get("status") or "—")
        else:
            status_value = "—"

        checks: list[dict[str, str]] = [
            {"label": "Visited", "value": "Yes" if visited else "No"},
            {"label": "Status", "value": status_value},
        ]

        detail_lines: list[str] = []
        for entry in log_entries:
            summary = self._format_run_summary(entry)
            if summary == "—":
                continue
            prompt_type = entry.get("prompt_type")
            if prompt_type:
                detail_lines.append(f"{prompt_type}: {summary}")
            else:
                detail_lines.append(summary)
        if detail_lines:
            checks.append({"label": "Details", "value": "\n".join(detail_lines)})

        # Raw state, exactly as stored (state[f"{node}_log"] or the matching
        # nodes_log/nodes_logs entries) — not the human-summarized line above.
        if log_entries:
            checks.append({
                "label": "State (raw)",
                "value": self._format_state_value(log_entries),
            })

        return checks

    def _collect_logs_by_node(
        self, state: dict[str, Any]
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
        """Split nodes_log and nodes_logs entries grouped by node name."""
        nodes_log_by_node: dict[str, list[dict[str, Any]]] = {}
        nodes_logs_by_node: dict[str, list[dict[str, Any]]] = {}

        for index, log in enumerate(state.get("nodes_log") or []):
            if isinstance(log, dict):
                node_id = str(log.get("node") or log.get("current_node") or "unknown")
                nodes_log_by_node.setdefault(node_id, []).append({**log, "_log_index": index})

        for index, log in enumerate(state.get("nodes_logs") or []):
            if isinstance(log, dict):
                node_id = str(log.get("node") or log.get("current_node") or "unknown")
                nodes_logs_by_node.setdefault(node_id, []).append({**log, "_log_index": index})

        return nodes_log_by_node, nodes_logs_by_node

    def _build_workflow_detail(self, state: dict[str, Any]) -> dict[str, Any]:
        """Full workflow view: context from state + per-node status and log entries."""
        nodes_log_by_node, nodes_logs_by_node = self._collect_logs_by_node(state)
        all_node_ids = set(self.WORKFLOW_NODE_ORDER) | set(nodes_log_by_node) | set(nodes_logs_by_node)

        nodes: list[dict[str, Any]] = []
        seen: set[str] = set()

        for step, node_id in enumerate(self.WORKFLOW_NODE_ORDER, start=1):
            seen.add(node_id)
            nodes.append(self._format_workflow_node(
                state,
                step,
                node_id,
                nodes_log_by_node.get(node_id, []),
                nodes_logs_by_node.get(node_id, []),
            ))

        for node_id in sorted(all_node_ids):
            if node_id not in seen:
                nodes.append(self._format_workflow_node(
                    state,
                    len(nodes) + 1,
                    node_id,
                    nodes_log_by_node.get(node_id, []),
                    nodes_logs_by_node.get(node_id, []),
                ))

        logged = sum(1 for node in nodes if node["status"] != "not_run")
        ordered_nodes = [node for node in nodes if node["node"] in self.WORKFLOW_NODE_ORDER]
        passed = sum(1 for node in ordered_nodes if node["status"] == "passed")
        failed = sum(1 for node in ordered_nodes if node["status"] == "failed")
        not_run = sum(1 for node in ordered_nodes if node["status"] == "not_run")

        return {
            "nodes": nodes,
            "summary": {
                "total_nodes": len(self.WORKFLOW_NODE_ORDER),
                "logged_nodes": logged,
                "passed_nodes": passed,
                "failed_nodes": failed,
                "not_run_nodes": not_run,
                "all_passed": failed == 0 and not_run == 0 and passed == len(self.WORKFLOW_NODE_ORDER),
            },
        }

    def _format_workflow_node(
        self,
        state: dict[str, Any],
        step: int,
        node_id: str,
        nodes_log_entries: list[dict[str, Any]],
        nodes_logs_entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        log_entries = self._resolve_node_log(state, node_id, nodes_log_entries, nodes_logs_entries)
        checks = self._node_checks(state, node_id, nodes_log_entries, nodes_logs_entries)
        display_name = self.WORKFLOW_NODE_LABELS.get(node_id, node_id)
        status_labels = {
            "passed": "Success",
            "failed": "Failed",
            "warning": "Warning",
            "info": "Info",
            "not_run": "Skipped",
        }

        if not log_entries:
            return {
                "step": step,
                "node": node_id,
                "label": display_name,
                "status": "not_run",
                "status_label": status_labels["not_run"],
                "checks": checks,
            }

        status = self._aggregate_node_status(log_entries)
        raw_status = str(log_entries[-1].get("status") or status)

        return {
            "step": step,
            "node": node_id,
            "label": display_name,
            "status": status,
            "status_label": status_labels.get(status, raw_status),
            "checks": checks,
        }

    def _parse_bool(self, value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes", "passed", "pass"}

    def _build_validation(
        self, state: dict[str, Any], workflow_detail: dict[str, Any]
    ) -> dict[str, Any]:
        """Overall pass = MCP tool order valid AND every workflow node visited + successful."""
        tools_valid = self._parse_bool(state.get("is_tool_order_valid"))
        tool_detail = (
            state.get("is_tool_order_valid_message")
            or state.get("is_tool_order_valid_massage")
            or state.get("is_tool_order_valid_msg")
            or ""
        ).strip()
        tool_detail_line = tool_detail.split("\n", 1)[0] if tool_detail else ""

        wf_summary = workflow_detail.get("summary", {})
        total_nodes = int(wf_summary.get("total_nodes") or len(self.WORKFLOW_NODE_ORDER))
        not_run_count = int(wf_summary.get("not_run_nodes") or 0)
        failed_count = int(wf_summary.get("failed_nodes") or 0)
        passed_count = int(wf_summary.get("passed_nodes") or 0)

        all_visited = not_run_count == 0
        all_success = failed_count == 0 and passed_count == total_nodes
        all_nodes_passed = all_visited and all_success

        not_run_node_lines: list[str] = []
        failed_node_lines: list[str] = []
        for node in workflow_detail.get("nodes", []):
            if node.get("node") not in self.WORKFLOW_NODE_ORDER:
                continue
            if node.get("status") == "not_run":
                not_run_node_lines.append(node["label"])
                continue
            if node.get("status") == "passed":
                continue
            detail = next(
                (c["value"] for c in node.get("checks", []) if c.get("label") == "Details"),
                "",
            )
            line = f"{node['label']} ({node['status_label']})"
            if detail and detail != "—":
                line = f"{line}: {detail.split(chr(10), 1)[0]}"
            failed_node_lines.append(line)

        if tools_valid is True:
            tools_line = "MCP tools were invoked in the correct order."
        elif tools_valid is False:
            tools_line = "MCP tools were not invoked in the correct order."
            if tool_detail_line:
                tools_line += f" {tool_detail_line}"
        else:
            tools_line = "MCP tool order could not be verified for this run."

        if all_visited and all_success:
            workflow_line = (
                f"All {total_nodes} workflow nodes were executed and completed successfully."
            )
        elif not all_visited and not all_success:
            parts: list[str] = []
            if failed_node_lines:
                parts.append(f"Failed at {failed_node_lines[0]}")
            if not_run_node_lines:
                parts.append(
                    "Not executed: " + ", ".join(not_run_node_lines)
                )
            workflow_line = "Workflow incomplete — " + "; ".join(parts) + "."
        elif not all_visited:
            if not_run_node_lines:
                workflow_line = (
                    "Workflow incomplete — not executed: "
                    + ", ".join(not_run_node_lines)
                    + "."
                )
            else:
                workflow_line = (
                    f"Workflow incomplete — {not_run_count} of {total_nodes} nodes were not executed."
                )
        else:
            if failed_node_lines:
                workflow_line = (
                    "Workflow failed — " + "; ".join(failed_node_lines) + "."
                )
            else:
                workflow_line = (
                    f"Workflow failed — {failed_count} of {total_nodes} nodes did not pass."
                )

        message_lines = [tools_line, workflow_line]

        # Binary verdict only — no ambiguous "Unknown" state. If tool order
        # couldn't be verified (tools_valid is None) but every workflow node
        # ran and passed, that's still a Passed run; anything else is Failed.
        if tools_valid is not False and all_nodes_passed:
            passed: bool = True
            label = "Passed"
            css_class = "passed"
        else:
            passed = False
            label = "Failed"
            css_class = "failed"

        message = "\n".join(message_lines)

        return {
            "passed": passed,
            "message": message,
            "message_lines": message_lines,
            "label": label,
            "css_class": css_class,
            "tools_valid": tools_valid,
            "all_nodes_passed": all_nodes_passed,
            "all_nodes_visited": all_visited,
            "all_nodes_success": all_success,
            "failed_nodes": failed_node_lines,
            "not_run_nodes": not_run_node_lines,
        }

    def _build_audit_detail(self, audit_events: list[dict[str, Any]]) -> dict[str, Any]:
        """Structured audit view aligned with AuditRecorder storage format."""
        agent_decisions: list[dict[str, str]] = []
        mcp_tool_results: list[dict[str, str]] = []
        simulated_user_replies: list[dict[str, str]] = []
        mcp_call_log: list[dict[str, str]] = []
        other_events: list[dict[str, str]] = []

        for raw in audit_events or []:
            event_type = str(raw.get("event_type") or "unknown")
            payload = raw.get("payload") or {}
            timestamp = self._normalize_timestamp(raw.get("timestamp"))
            formatted = self._format_state_value(payload)
            row = {"timestamp": timestamp, "payload": formatted}

            if event_type == "AGENT_DECISION":
                tool = str(payload.get("tool") or "unknown")
                agent_decisions.append({**row, "tool": tool})
            elif event_type == "MCP_TOOL_RESULT":
                tool = str(payload.get("tool") or "unknown")
                mcp_tool_results.append({**row, "tool": tool})
            elif event_type == "SIMULATED_USER_REPLY":
                simulated_user_replies.append(row)
            elif event_type == "MCP_CALL_LOG":
                tool = str(payload.get("tool") or "unknown")
                mcp_call_log.append({**row, "tool": tool})
            else:
                other_events.append({
                    "timestamp": timestamp,
                    "event_type": event_type,
                    "payload": formatted,
                })

        return {
            "total_events": len(audit_events or []),
            "agent_decisions": agent_decisions,
            "mcp_tool_results": mcp_tool_results,
            "simulated_user_replies": simulated_user_replies,
            "mcp_call_log": mcp_call_log,
            "other_events": other_events,
        }

    def _build_mcp_conversation(self, audit_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pair AGENT_DECISION with the next MCP_TOOL_RESULT for the same tool."""
        conversation: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []

        for raw in audit_events or []:
            event_type = raw.get("event_type")
            if event_type not in {"AGENT_DECISION", "MCP_TOOL_RESULT"}:
                continue
            payload = raw.get("payload") or {}
            timestamp = self._normalize_timestamp(raw.get("timestamp"))
            tool = str(payload.get("tool") or "unknown")

            if event_type == "AGENT_DECISION":
                pending.append({
                    "timestamp": timestamp,
                    "tool": tool,
                    "args_json": json.dumps(payload.get("args") or {}, ensure_ascii=False, indent=2),
                    "result": None,
                })
            else:
                matched = False
                for item in reversed(pending):
                    if item["tool"] == tool and item["result"] is None:
                        item["result"] = str(payload.get("result") or "")
                        conversation.append(pending.pop(pending.index(item)))
                        matched = True
                        break
                if not matched:
                    conversation.append({
                        "timestamp": timestamp,
                        "tool": tool,
                        "args_json": "{}",
                        "result": str(payload.get("result") or ""),
                    })

        conversation.extend(pending)
        return conversation

    def _build_state_sections(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Grouped pipeline state per spec — only keys present in state are shown."""
        covered: set[str] = set()
        sections: list[dict[str, Any]] = []

        for section in self.STATE_SECTIONS:
            rows: list[dict[str, str]] = []
            for key in section["keys"]:
                if key not in state:
                    continue
                covered.add(key)
                if key in self.NON_SERIALIZABLE_KEYS:
                    rows.append({"key": key, "value": "[object — not serialized]"})
                else:
                    rows.append({"key": key, "value": self._format_state_value(state[key])})
            if rows:
                sections.append({
                    "title": section["title"],
                    "description": section.get("description", ""),
                    "rows": rows,
                })

        other_rows: list[dict[str, str]] = []
        skip = covered | self.NON_SERIALIZABLE_KEYS | frozenset({
            "nodes_log", "nodes_logs", "current_node", "next_node",
        })
        skip |= {self._node_is_visited_key(n) for n in self.WORKFLOW_NODE_ORDER}
        skip |= {self._node_log_key(n) for n in self.WORKFLOW_NODE_ORDER}
        skip |= set(self.LEGACY_VISITED_KEYS.values())

        for key in sorted(state.keys(), key=str):
            if key in skip or key.startswith("_"):
                continue
            other_rows.append({"key": key, "value": self._format_state_value(state[key])})
        if other_rows:
            sections.append({
                "title": "Other State",
                "description": "Additional keys present in state but not in the spec groups above.",
                "rows": other_rows,
            })

        per_node_rows: list[dict[str, str]] = []
        for node_id in self.WORKFLOW_NODE_ORDER:
            visited_key = self._node_is_visited_key(node_id)
            log_key = self._node_log_key(node_id)
            if visited_key in state:
                per_node_rows.append({
                    "key": visited_key,
                    "value": self._format_state_value(state[visited_key]),
                })
            if log_key in state:
                per_node_rows.append({
                    "key": log_key,
                    "value": self._format_state_value(state[log_key]),
                })
        if per_node_rows:
            sections.append({
                "title": "Workflow Node State Keys",
                "description": "Explicit per-node keys ({node}_is_visited, {node}_log) when written to state.",
                "rows": per_node_rows,
            })

        return sections

    def _build_mcp_validation(self, state: dict[str, Any], audit_events: list[dict[str, Any]]) -> dict[str, Any]:
        call_log: list[dict[str, Any]] = []
        for raw in audit_events or []:
            if raw.get("event_type") == "MCP_CALL_LOG":
                call_log.append(raw.get("payload") or {})
            elif raw.get("event_type") == "MCP_SEQUENCE":
                payload = raw.get("payload") or {}
                return {
                    "platform": payload.get("platform") or state.get("platform") or "unknown",
                    "call_log": payload.get("call_log") or call_log,
                }
        return {
            "platform": state.get("platform") or "unknown",
            "call_log": call_log or state.get("call_log") or [],
        }

    @staticmethod
    def _format_display_datetime(value: datetime | str | None = None) -> str:
        if value is None:
            dt = datetime.now()
        elif isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return str(value)
        return dt.strftime("%B %d, %Y at %I:%M %p")

    @staticmethod
    def _format_state_value(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(value)

    def _format_duration(self, state: dict[str, Any]) -> str:
        started = state.get("started_at")
        ended = state.get("ended_at")
        if not started or not ended or started == "N/A":
            return "—"
        try:
            start_dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
            total_secs = int((end_dt - start_dt).total_seconds())
            if total_secs < 0:
                return "—"
            mins, secs = divmod(total_secs, 60)
            hours, mins = divmod(mins, 60)
            if hours:
                return f"{hours}h {mins}m"
            if mins:
                return f"{mins}m {secs}s"
            return f"{secs}s"
        except (ValueError, TypeError):
            return "—"

    def _resolve_use_case_display(self, state: dict[str, Any]) -> str:
        for key in ("primary_use_case_name", "use_case_id", "primary_use_case_id", "current_use_case_path"):
            value = state.get(key)
            if value is None or value == "":
                continue
            return self._format_state_value(value)
        use_case = state.get("current_use_case") or {}
        return self._format_state_value(
            use_case.get("id") or use_case.get("useCaseId") or "—"
        )

    def _build_summary(self, state: dict[str, Any], events: list[NormalizedAuditEvent]) -> dict[str, Any]:
        run_id = state.get("run_id") or state.get("runId") or "unknown-run"

        return {
            "run_id": run_id,
            "platform": state.get("platform") or "unknown",
            "duration": self._format_duration(state),
            "started_at": state.get("started_at") or "N/A",
            "ended_at": state.get("ended_at") or datetime.now().isoformat(),
            "total_events": len(events),
            "meta_chips": [
                {"label": "Platform", "value": self._format_state_value(state.get("platform") or "unknown")},
                {"label": "Use Case", "value": self._resolve_use_case_display(state)},
                {"label": "Duration", "value": self._format_duration(state)},
            ],
        }

    @staticmethod
    def _load_audit_jsonl(path: Path) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    @staticmethod
    def _format_payload(payload: dict[str, Any]) -> str:
        if not payload:
            return ""
        try:
            return json.dumps(payload, ensure_ascii=False)[:2000]
        except TypeError:
            return str(payload)[:2000]

    @staticmethod
    def _infer_source(event_type: str, payload: dict[str, Any]) -> str:
        if event_type in {"AGENT_DECISION", "MCP_TOOL_RESULT", "MCP_CALL_LOG"}:
            return "LLM ↔ MCP"
        if event_type.startswith("LISTENER"):
            return "Listener"
        if event_type.startswith("AGENT_"):
            return "SDK Agent"
        return str(payload.get("node") or "Pipeline")

    @staticmethod
    def _phase_from_event_type(event_type: str) -> str:
        if event_type in {"AGENT_DECISION", "MCP_TOOL_RESULT"}:
            return "Agent Orchestration"
        if event_type.startswith("LISTENER"):
            return "Agent Orchestration"
        return "General"

    @staticmethod
    def _status_from_event_type(event_type: str, payload: dict[str, Any]) -> str:
        explicit = payload.get("status") or payload.get("test_status")
        if explicit:
            return RunReportBuilder._normalize_status(str(explicit))
        if payload.get("listener") == "FAIL":
            return "failed"
        if event_type == "MCP_TOOL_RESULT":
            result = str(payload.get("result") or "").lower()
            return "failed" if any(t in result for t in ("error", "failed")) else "passed"
        return "info"

    @staticmethod
    def _normalize_status(value: str) -> str:
        lowered = value.lower().strip()
        if lowered in {"ok", "success", "pass", "passed", "ready"}:
            return "passed"
        if lowered in {"warn", "warning"}:
            return "warning"
        if lowered in {"error", "failed", "fail"}:
            return "failed"
        return "info"

    @staticmethod
    def _infer_phase(source: str, event: str, details: str) -> str:
        text = f"{source} {event} {details}".lower()
        if "agent" in text or "prompt" in text:
            return "Agent Orchestration"
        if "audit" in text or "listener" in text:
            return "Audit"
        return "General"

    @staticmethod
    def _normalize_timestamp(value: Any) -> str:
        if value is None:
            return "N/A"
        return str(value)


def resolve_output_dir(run_id: str, run_date: datetime | None = None) -> Path:
    day = (run_date or datetime.now()).strftime("%Y-%m-%d")
    return DATA_REPORTS_DIR / day / run_id


def load_audit_events(
    state: dict[str, Any],
    audit_recorder: AuditRecorder | None = None,
) -> list[dict[str, Any]]:
    if audit_recorder is not None:
        return audit_recorder.all_events()

    recorder = state.get("audit_recorder")
    if recorder is not None and hasattr(recorder, "all_events"):
        return recorder.all_events()

    for candidate in (
        state.get("audit_path"),
        Path("data/runs") / str(state.get("run_id", "")) / "audit.jsonl",
    ):
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return RunReportBuilder._load_audit_jsonl(path)

    return []


def generate_run_report(
    state: dict[str, Any],
    *,
    audit_recorder: AuditRecorder | None = None,
    run_date: datetime | None = None,
) -> Path:
    """
    Generate HTML report under data/reports/<date>/<run_id>/report.html.

    Example (from visual_report_node)::

        from data.reports.build_report import generate_run_report
        generate_run_report(state)
    """
    run_id = state.get("run_id") or state.get("runId")
    if not run_id:
        from infra.workflow.use_case_loader import generate_run_id

        run_id = generate_run_id()
    run_date = run_date or datetime.now()
    output_dir = resolve_output_dir(str(run_id), run_date=run_date)
    audit_events = load_audit_events(state, audit_recorder=audit_recorder)
    return RunReportBuilder().build(state, audit_events, output_dir / "report.html")


def generate_and_attach_report(
    state: dict[str, Any],
    *,
    audit_recorder: AuditRecorder | None = None,
) -> dict[str, Any]:
    """
    Workflow-node wrapper around generate_run_report().

    Builds a single combined-state HTML report and records its path under
    state["report_path"]. Kept for standalone callers (e.g. one-off scripts)
    that just want one report for the whole state, no per-use-case index.
    For the multi-use-case workflow, see record_use_case_report() /
    attach_index_report() below instead.

    A report failure never raises: it must not hide a successful workflow
    result from the caller.
    """
    try:
        report_path = generate_run_report(state, audit_recorder=audit_recorder)
        state["report_path"] = str(report_path.resolve())
    except Exception:  # noqa: BLE001 — reporting must not break the workflow
        state.setdefault("report_path", "")
    return state


def _slugify_use_case_id(value: str) -> str:
    """Sanitize a use case id into a safe folder name."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-")
    return slug or "use-case"


def resolve_use_case_output_dir(
    run_id: str, use_case_id: str, run_date: datetime | None = None
) -> Path:
    """data/reports/<date>/<run_id>/<use-case-slug>/ — one folder per use case in the run."""
    return resolve_output_dir(run_id, run_date=run_date) / _slugify_use_case_id(use_case_id)


def record_use_case_report(
    state: dict[str, Any],
    *,
    audit_recorder: AuditRecorder | None = None,
    run_date: datetime | None = None,
) -> dict[str, Any]:
    """
    Build the detail report for the use case that just finished the pipeline.

    Must be called from visual_report_node BEFORE it advances
    current_use_case_path to the next use case — state still holds that
    use case's data (current_use_case, platform, mcp results, etc.) at this
    point. Appends a card (id, platform, status, path to its own
    report.html) to state["use_case_reports"] so the run's index page can
    list every use case once the whole run finishes.

    Never raises — a report failure must not hide a successful workflow
    result from the caller.
    """
    try:
        current_use_case = state.get("current_use_case") or {}
        cards: list[dict[str, Any]] = state.setdefault("use_case_reports", [])
        use_case_id = (
            current_use_case.get("id")
            or state.get("primary_use_case_id")
            or f"use-case-{len(cards) + 1}"
        )
        run_id = state.get("run_id") or "run"
        run_date = run_date or datetime.now()
        output_dir = resolve_use_case_output_dir(str(run_id), str(use_case_id), run_date=run_date)
        output_path = output_dir / "report.html"

        audit_events = load_audit_events(state, audit_recorder=audit_recorder)
        builder = RunReportBuilder()
        # output_path is always <run_dir>/<use-case-slug>/report.html, so the
        # run's index.html is exactly one level up — lets the detail report
        # link back to "all use cases" without knowing the run's absolute path.
        builder.build(state, audit_events, output_path, index_url="../index.html")
        validation = builder.evaluate(state)

        cards.append({
            "id": str(use_case_id),
            "platform": state.get("platform") or "unknown",
            "prompt_goal": current_use_case.get("prompt_goal") or "",
            "status_label": validation.get("label", "Failed"),
            "css_class": validation.get("css_class", "failed"),
            "report_path": str(output_path.resolve()),
            "relative_path": f"{output_path.parent.name}/{output_path.name}",
            "duration": builder._format_duration(state),
            "started_at": state.get("started_at"),
            "ended_at": state.get("ended_at"),
        })
    except Exception:  # noqa: BLE001 — reporting must not break the workflow
        pass
    return state


def _combined_duration(builder: "RunReportBuilder", cards: list[dict[str, Any]]) -> str:
    """
    Total run time across every use case in the run — from the earliest
    started_at to the latest ended_at of any use case, i.e. how long the
    whole run (all use cases together) actually took, not any single one.
    """
    starts: list[datetime] = []
    ends: list[datetime] = []
    for card in cards:
        for raw, bucket in ((card.get("started_at"), starts), (card.get("ended_at"), ends)):
            if not raw or raw == "N/A":
                continue
            try:
                bucket.append(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
            except ValueError:
                continue
    if not starts or not ends:
        return "—"
    return builder._format_duration({
        "started_at": min(starts).isoformat(),
        "ended_at": max(ends).isoformat(),
    })


def generate_index_report(
    state: dict[str, Any],
    *,
    run_date: datetime | None = None,
) -> Path:
    """Render data/reports/<date>/<run_id>/index.html — cards for every use case in the run."""
    run_id = state.get("run_id") or "run"
    run_date = run_date or datetime.now()
    output_dir = resolve_output_dir(str(run_id), run_date=run_date)
    cards = state.get("use_case_reports") or []

    builder = RunReportBuilder()

    totals = {
        "total": len(cards),
        "passed": sum(1 for c in cards if c.get("css_class") == "passed"),
        "failed": sum(1 for c in cards if c.get("css_class") != "passed"),
    }

    html = builder.env.get_template("index_report.html.j2").render(
        generated_at=builder._format_display_datetime(),
        run_id=run_id,
        cards=cards,
        totals=totals,
        total_duration=_combined_duration(builder, cards),
    )
    output_path = output_dir / "index.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def attach_index_report(
    state: dict[str, Any],
    *,
    run_date: datetime | None = None,
) -> dict[str, Any]:
    """
    Workflow-node wrapper around generate_index_report().

    Intended to be called from visual_report_node once current_use_case_path
    is exhausted (every use case in the run has its own card + detail report
    already recorded via record_use_case_report()). Records the index page's
    path under state["report_path"] so the UI opens the cards overview
    first, with each card linking to that use case's own detail report.

    A report failure never raises: it must not hide a successful workflow
    result from the caller.
    """
    try:
        report_path = generate_index_report(state, run_date=run_date)
        state["report_path"] = str(report_path.resolve())
    except Exception:  # noqa: BLE001 — reporting must not break the workflow
        state.setdefault("report_path", "")
    return state


__all__ = [
    "RunReportBuilder",
    "generate_run_report",
    "generate_and_attach_report",
    "record_use_case_report",
    "generate_index_report",
    "attach_index_report",
    "resolve_use_case_output_dir",
    "load_audit_events",
    "resolve_output_dir",
]
