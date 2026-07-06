"""Backward-compatible entrypoint for report generation."""

from infra.user_interface_use_case.reports.reporter import (
    ReportGenerator,
    generate_html_report,
)

__all__ = ["ReportGenerator", "generate_html_report"]

