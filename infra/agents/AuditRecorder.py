import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

class AuditRecorder:
    def __init__(self, run_dir: Path):
        """
        Constructor: Initializes the Audit Recorder.
        Receives a path (run_dir) where the log file (audit.jsonl) will be saved.
        Initializes an empty list in memory (self.events) to store the events.
        """
        self.run_dir = run_dir
        self.audit_log_path = run_dir / "audit.jsonl"
        self.events: List[Dict[str, Any]] = []


    def write(self, event_type: str, payload: Dict[str, Any]):
        """
        Write function: Records a new event.
        Receives the event type (event_type) and its data (payload).
        Adds a timestamp, saves the event to the in-memory list,
        and immediately writes it as a new line in the audit.jsonl file.
        """
        event = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "payload": payload,
        }

        self.events.append(event)

        line = json.dumps(event, ensure_ascii=False) + "\n"
        try:
            with self.audit_log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except FileNotFoundError:
            # The run directory can legitimately be gone by the time this
            # fires — e.g. a late/overlapping write racing the end-of-run
            # cleanup in workflow_nodes.py's _clear_run_dir(). The event is
            # already kept in self.events above; recreate the directory and
            # retry once so a merely-missing folder doesn't lose the entry,
            # but never let a disappeared run directory crash the pipeline.
            try:
                self.run_dir.mkdir(parents=True, exist_ok=True)
                with self.audit_log_path.open("a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass


    def agent_decisions(self) -> List[Dict[str, Any]]:
        """
        Getter function: Retrieves all the tools and decisions the LLM (Agent) decided to execute.
        Filters the events list and returns only the payload of events with type "AGENT_DECISION".
        """
        return [
            e["payload"]
            for e in self.events
            if e["event_type"] == "AGENT_DECISION"
        ]


    def mcp_tool_results(self) -> List[Dict[str, Any]]:
        """
        Getter function: Retrieves MCP_TOOL_RESULT payloads, enriched with the
        "action" argument (e.g. "prepare"/"verify" for iOS two-step tools)
        taken from the matching AGENT_DECISION that triggered it - matched by
        tool name, in call order. Existing fields ("tool", "status",
        "is_error", "result") are unchanged; "action" is only added when the
        matching decision actually had one.
        """
        pending_by_tool: Dict[str, List[Dict[str, Any]]] = {}
        results: List[Dict[str, Any]] = []
        for e in self.events:
            if e["event_type"] == "AGENT_DECISION":
                decision = e["payload"]
                pending_by_tool.setdefault(decision.get("tool"), []).append(decision)
            elif e["event_type"] == "MCP_TOOL_RESULT":
                payload = dict(e["payload"])
                queue = pending_by_tool.get(payload.get("tool"))
                if queue:
                    matching_decision = queue.pop(0)
                    action = (matching_decision.get("args") or {}).get("action")
                    if action:
                        payload["action"] = action
                results.append(payload)
        return results
    def simulated_user_replies(self) -> List[Dict[str, Any]]:
        """
        Getter function: Retrieves all messages and responses received from the "Simulated User" during the conversation.
        Filters the events list and returns only the payload of events with type "SIMULATED_USER_REPLY".
        """
        return [
            e["payload"]
            for e in self.events
            if e["event_type"] == "SIMULATED_USER_REPLY"
        ]

    def all_events(self) -> List[Dict[str, Any]]:
        """
        Getter function: Retrieves all events recorded in the audit log.
        Returns the entire events list.
        """
        return self.events

    def clear_memory(self) -> None:
        """
        Drop in-memory events so the next use case's report is isolated.

        Does not truncate audit.jsonl on disk — the full run history remains
        append-only for debugging; only the live list used by per-UC reports
        is cleared.
        """
        self.events.clear()
