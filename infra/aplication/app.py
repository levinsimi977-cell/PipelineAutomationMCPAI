"""
Application task runner for application-1 tasks 3 + 4.

Task 3: install/connect MCP inside the work environment and check that it is alive.
Task 4: validate that the selected application/project is usable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict

from infra.aplication.app_validator import validate_application
from infra.aplication.mcp_environment import check_mcp_alive


def build_application_report(
    app_path: Path,
    workdir: Path,
    run_build_check: bool = False,
) -> Dict[str, Any]:
    app_validation = validate_application(app_path=app_path, run_build_check=run_build_check)

    return {
        "application_validation": app_validation,
        "workdir": str(Path(workdir).resolve()),
    }


async def run_tasks_3_and_4(
    app_path: Path,
    workdir: Path,
    run_build_check: bool = False,
) -> Dict[str, Any]:
    app_report = build_application_report(
        app_path=app_path,
        workdir=workdir,
        run_build_check=run_build_check,
    )

    mcp_report = await check_mcp_alive(
        workdir=workdir,
        app_id=os.getenv("APP_ID"),
        dev_key=os.getenv("APPSFLYER_DEV_KEY") or os.getenv("DEV_KEY"),
    )

    final_status = "OK"

    if app_report["application_validation"].get("status") != "OK":
        final_status = "FAILED"

    if mcp_report.get("status") != "OK":
        final_status = "FAILED"

    return {
        "status": final_status,
        "task_3_mcp_alive": mcp_report,
        "task_4_application_validation": app_report["application_validation"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run application-1 tasks 3 and 4.")
    parser.add_argument("--app-path", required=True, help="Path to the selected application/project.")
    parser.add_argument("--workdir", default=".", help="Work directory where MCP should be checked.")
    parser.add_argument(
        "--run-build-check",
        action="store_true",
        help="Run xcodebuild/gradle lightweight project checks when possible.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    result = asyncio.run(
        run_tasks_3_and_4(
            app_path=Path(args.app_path),
            workdir=Path(args.workdir),
            run_build_check=args.run_build_check,
        )
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
