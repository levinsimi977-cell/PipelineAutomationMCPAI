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

        with self.audit_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


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
        Getter function: Retrieves all the answers and results returned from the tools (MCP Tools) after execution.
        Filters the events list and returns only the payload of events with type "MCP_TOOL_RESULT".
        """
        return [
            e["payload"]
            for e in self.events
            if e["event_type"] == "MCP_TOOL_RESULT"
        ]


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
