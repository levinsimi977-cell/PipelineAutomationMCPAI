from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from jinja2 import Environment, FileSystemLoader, select_autoescape


@dataclass
class NormalizedAuditEvent:
    """Normalized audit event used by rendering layer."""

    index: int
    timestamp: str
    phase: str
    source: str
    event: str
    status: str
    details: str


class ReportGenerator:
    """
    Scalable report generator with strict separation of parsing and view.

    - Parsing and normalization logic stays in Python.
    - HTML structure and styling stay in Jinja templates.
    """

    def __init__(self, templates_dir: str | Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parent
        default_templates = base_dir / "templates"
        self.templates_dir = Path(templates_dir) if templates_dir else default_templates

        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def generate_html(self, state: Dict[str, Any], audit_events: List[Dict[str, Any]]) -> str:
        """Render dashboard HTML string."""
        normalized_events = self._normalize_events(audit_events)
        summary = self._build_summary(state, normalized_events)
        phase_rows = self._build_phase_rows(normalized_events)
        failed_rows = [event for event in normalized_events if event.status == "failed"]

        template = self.env.get_template("report.html.j2")
        return template.render(
            generated_at=datetime.utcnow().isoformat() + "Z",
            summary=summary,
            phase_rows=phase_rows,
            failed_rows=failed_rows,
            events=normalized_events,
        )

    def write_html_report(
        self,
        state: Dict[str, Any],
        audit_events: List[Dict[str, Any]],
        output_path: str | Path,
    ) -> Path:
        """Generate and persist report HTML."""
        html_content = self.generate_html(state, audit_events)
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html_content, encoding="utf-8")
        return target

    def _normalize_events(self, audit_events: List[Dict[str, Any]]) -> List[NormalizedAuditEvent]:
        """Normalize unstable event shapes from different audit emitters."""
        normalized: List[NormalizedAuditEvent] = []
        for index, raw in enumerate(audit_events or [], start=1):
            source = str(raw.get("source") or raw.get("component") or "unknown")
            event = str(raw.get("event") or raw.get("action") or "unknown_event")
            details = str(raw.get("details") or raw.get("message") or "")
            status = self._normalize_status(str(raw.get("status") or raw.get("result") or "info"))
            phase = str(raw.get("phase") or self._infer_phase(source, event, details))
            timestamp = self._normalize_timestamp(raw.get("timestamp") or raw.get("time"))

            normalized.append(
                NormalizedAuditEvent(
                    index=index,
                    timestamp=timestamp,
                    phase=phase,
                    source=source,
                    event=event,
                    status=status,
                    details=details,
                )
            )
        return normalized

    def _build_summary(
        self, state: Dict[str, Any], events: List[NormalizedAuditEvent]
    ) -> Dict[str, Any]:
        """Build top-level summary for KPI cards."""
        status_counter = Counter(event.status for event in events)

        summary_status = state.get("status")
        if not summary_status:
            summary_status = "failed" if status_counter.get("failed", 0) else "passed"

        return {
            "run_id": state.get("run_id") or state.get("runId") or state.get("id") or "unknown-run",
            "platform": state.get("platform") or "unknown",
            "use_case_id": state.get("use_case_id") or state.get("useCaseId") or "unknown-use-case",
            "status": summary_status,
            "started_at": state.get("started_at") or state.get("start_time") or "N/A",
            "ended_at": state.get("ended_at") or state.get("end_time") or "N/A",
            "total_events": len(events),
            "passed_events": status_counter.get("passed", 0),
            "warning_events": status_counter.get("warning", 0),
            "failed_events": status_counter.get("failed", 0),
        }

    def _build_phase_rows(self, events: List[NormalizedAuditEvent]) -> List[Dict[str, Any]]:
        """Aggregate events by execution phase."""
        bucket = defaultdict(lambda: {"events": 0, "passed": 0, "warning": 0, "failed": 0})
        for event in events:
            bucket[event.phase]["events"] += 1
            if event.status in bucket[event.phase]:
                bucket[event.phase][event.status] += 1

        return [
            {"phase": phase, **counts}
            for phase, counts in sorted(bucket.items(), key=lambda item: item[0].lower())
        ]

    @staticmethod
    def _normalize_status(value: str) -> str:
        """Normalize status aliases to a strict set."""
        lowered = value.lower().strip()
        if lowered in {"ok", "success", "pass", "passed"}:
            return "passed"
        if lowered in {"warn", "warning"}:
            return "warning"
        if lowered in {"error", "failed", "fail", "exception"}:
            return "failed"
        return "info"

    @staticmethod
    def _infer_phase(source: str, event: str, details: str) -> str:
        """Infer phase from free-text signals."""
        text = f"{source} {event} {details}".lower()
        if any(token in text for token in ["sandbox", "clone", "cleanup"]):
            return "Preparation"
        if any(token in text for token in ["install", "dependency", "sdk setup"]):
            return "Installation"
        if any(token in text for token in ["build", "compile", "xcode", "gradle"]):
            return "Build"
        if any(token in text for token in ["validate", "schema", "pydantic"]):
            return "Validation"
        if any(token in text for token in ["agent", "prompt", "answer"]):
            return "Agent Orchestration"
        if any(token in text for token in ["appium", "tap", "swipe", "type", "deeplink", "deep link"]):
            return "Execution"
        if any(token in text for token in ["audit", "report", "listener"]):
            return "Audit"
        return "General"

    @staticmethod
    def _normalize_timestamp(value: Any) -> str:
        """Normalize timestamp values to readable UTC text."""
        if value is None:
            return "N/A"
        if isinstance(value, (int, float)):
            return datetime.utcfromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S UTC")
        return str(value)


def generate_html_report(state: dict, audit_events: list, output_path: str) -> None:
    """Compatibility function for existing callers."""
    generator = ReportGenerator()
    generator.write_html_report(state=state, audit_events=audit_events, output_path=output_path)


