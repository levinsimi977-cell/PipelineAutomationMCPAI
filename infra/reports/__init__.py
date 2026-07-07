"""Backward-compatible report package exports."""

from .report import ReportGenerator, generate_html_report

__all__ = ["ReportGenerator", "generate_html_report"]
