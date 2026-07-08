"""
Task 3 — MCP installation/connection health check inside the work environment.

This module does not permanently install anything into the user's machine.
It verifies that the MCP runtime command is available and then tries to connect
and list the MCP tools. If tools are returned, the MCP server is considered alive.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_MCP_COMMAND = "npx"
DEFAULT_MCP_ARGS = ["-y", "@appsflyer/sdk-mcp-server"]


@dataclass
class MCPHealthResult:
    status: str
    alive: bool
    workdir: str
    command: str
    args: List[str]
    tools: List[str]
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class MCPEnvironmentChecker:
    """Checks whether the AppsFlyer MCP server can start and expose tools."""

    def __init__(
        self,
        workdir: Path,
        command: str = DEFAULT_MCP_COMMAND,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> None:
        self.workdir = Path(workdir).resolve()
        self.command = command
        self.args = args if args is not None else list(DEFAULT_MCP_ARGS)
        self.env = env or {}

    def _base_failure(self, error: str, details: Optional[Dict[str, Any]] = None) -> MCPHealthResult:
        return MCPHealthResult(
            status="FAILED",
            alive=False,
            workdir=str(self.workdir),
            command=self.command,
            args=self.args,
            tools=[],
            error=error,
            details=details,
        )

    def validate_workdir(self) -> Optional[MCPHealthResult]:
        if not self.workdir.exists():
            return self._base_failure(f"Workdir does not exist: {self.workdir}")

        if not self.workdir.is_dir():
            return self._base_failure(f"Workdir is not a directory: {self.workdir}")

        return None

    def validate_runtime_command(self) -> Optional[MCPHealthResult]:
        resolved_command = shutil.which(self.command)

        if not resolved_command:
            return self._base_failure(
                f"MCP command not found: {self.command}",
                {
                    "hint": "Install Node.js/npm so npx is available, or configure a different MCP command.",
                },
            )

        return None

    async def check_alive(self) -> MCPHealthResult:
        workdir_error = self.validate_workdir()
        if workdir_error:
            return workdir_error

        command_error = self.validate_runtime_command()
        if command_error:
            return command_error

        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except Exception as exc:
            return self._base_failure(
                "Missing dependency: langchain-mcp-adapters",
                {
                    "exception_type": type(exc).__name__,
                    "exception": str(exc),
                    "hint": "Add langchain-mcp-adapters to requirements.txt and install dependencies.",
                },
            )

        merged_env = dict(os.environ)
        merged_env.update(self.env)

        try:
            client = MultiServerMCPClient(
                {
                    "appsflyer-sdk-mcp": {
                        "transport": "stdio",
                        "command": self.command,
                        "args": self.args,
                        "env": merged_env,
                    }
                }
            )

            tools = await asyncio.wait_for(client.get_tools(), timeout=60)

            tool_names = [getattr(tool, "name", str(tool)) for tool in tools]

            if not tool_names:
                return self._base_failure(
                    "MCP server started but returned no tools.",
                    {"tool_count": 0},
                )

            return MCPHealthResult(
                status="OK",
                alive=True,
                workdir=str(self.workdir),
                command=self.command,
                args=self.args,
                tools=tool_names,
                error=None,
                details={"tool_count": len(tool_names)},
            )

        except Exception as exc:
            return self._base_failure(
                "MCP health check failed.",
                {
                    "exception_type": type(exc).__name__,
                    "exception": str(exc),
                },
            )


async def check_mcp_alive(
    workdir: Path,
    app_id: Optional[str] = None,
    dev_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Convenience function for Task 3."""

    env: Dict[str, str] = {}

    if app_id:
        env["APP_ID"] = app_id

    if dev_key:
        env["DEV_KEY"] = dev_key

    checker = MCPEnvironmentChecker(workdir=workdir, env=env)
    result = await checker.check_alive()
    return result.to_dict()
