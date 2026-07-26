"""
In-process registry of per-run resources that may exist before LangGraph
commits the next state snapshot (e.g. sandbox created mid-node, then crash).

Teardown reads this registry so cleanup still works when `latest_state` is
stale relative to what the crashing node already allocated.
"""
from __future__ import annotations

import os
import threading
from typing import Any

_lock = threading.Lock()
_sandboxes: dict[str, set[str]] = {}
_agents: dict[str, set[str]] = {}
_drivers: dict[str, list[Any]] = {}


def should_keep_failed_run_artifacts() -> bool:
    """
    Debugging aid: a run ending with test_status == FAIL normally has its
    sandbox (compiled project + xcodebuild logs) and data/runs/<run_id>/
    (audit.jsonl) deleted before there's any chance to inspect what went
    wrong -- deleted from two separate places (visual_report_node's
    _clear_run_dir, and this module's own sandbox cleanup in
    release_run_resources). Both call this same check so a FAILED run's
    artifacts are consistently kept on disk for post-mortem inspection.

    Set PIPELINE_KEEP_FAILED_RUN_ARTIFACTS=0 to restore the old
    always-delete behavior; defaults to keeping them.
    """
    return os.environ.get("PIPELINE_KEEP_FAILED_RUN_ARTIFACTS", "1") != "0"


def register_sandbox(run_id: str, path: str) -> None:
    if not run_id or not path:
        return
    with _lock:
        _sandboxes.setdefault(str(run_id), set()).add(str(path))


def unregister_sandbox(run_id: str | None, path: str | None) -> None:
    if not run_id or not path:
        return
    with _lock:
        paths = _sandboxes.get(str(run_id))
        if not paths:
            return
        paths.discard(str(path))
        if not paths:
            _sandboxes.pop(str(run_id), None)


def forget_sandbox_path(path: str | None) -> None:
    """Remove `path` from every run entry (used after disk cleanup)."""
    if not path:
        return
    needle = str(path)
    with _lock:
        for run_id, paths in list(_sandboxes.items()):
            paths.discard(needle)
            if not paths:
                _sandboxes.pop(run_id, None)


def register_agent(run_id: str, agent_id: str) -> None:
    if not run_id or not agent_id:
        return
    with _lock:
        _agents.setdefault(str(run_id), set()).add(str(agent_id))


def unregister_agent(run_id: str | None, agent_id: str | None) -> None:
    if not run_id or not agent_id:
        return
    with _lock:
        ids = _agents.get(str(run_id))
        if not ids:
            return
        ids.discard(str(agent_id))
        if not ids:
            _agents.pop(str(run_id), None)


def register_driver(run_id: str, driver: Any) -> None:
    if not run_id or driver is None:
        return
    with _lock:
        _drivers.setdefault(str(run_id), []).append(driver)


def unregister_driver(run_id: str | None, driver: Any) -> None:
    """Remove one driver handle after quit so the registry does not retain dead refs."""
    if not run_id or driver is None:
        return
    with _lock:
        drivers = _drivers.get(str(run_id))
        if not drivers:
            return
        remaining = [d for d in drivers if d is not driver]
        if remaining:
            _drivers[str(run_id)] = remaining
        else:
            _drivers.pop(str(run_id), None)


def release_run_resources(
    run_id: str,
    state: dict[str, Any] | None = None,
    *,
    delete_sandboxes: bool = True,
) -> None:
    """
    Best-effort release of agents, drivers, and sandboxes for `run_id`.

    Merges handles from the registry with whatever is still on `state`, so
    either source alone is enough. Never raises.

    `delete_sandboxes=False` still closes SDK agents and quits Appium
    drivers, but leaves the sandbox directory on disk (used to preserve
    artifacts for post-mortem debugging of a FAILED run).
    """
    state = state or {}
    rid = str(run_id)

    with _lock:
        agent_ids = set(_agents.pop(rid, set()))
        drivers = list(_drivers.pop(rid, []))
        # Popped so this run stops owning it, but deliberately not deleted
        # from disk here anymore: sandboxes are left in place after a run
        # finishes so the built app can still be inspected in between runs.
        # The next run's environment_setup_node deletes whatever is left
        # over (see cleanup_stale_sandboxes() in infra/application/app.py)
        # right before creating its own sandbox.
        _sandboxes.pop(rid, None)

    agent_from_state = state.get("agent_id")
    if agent_from_state:
        agent_ids.add(str(agent_from_state))

    driver_from_state = state.get("driver")
    if driver_from_state is not None:
        drivers.append(driver_from_state)

    audit_recorder = state.get("audit_recorder")
    try:
        from infra.agents.sdkAgent.tools.agent import close_sdk_integration_agent_by_id

        for agent_id in agent_ids:
            try:
                close_sdk_integration_agent_by_id(agent_id, audit_recorder)
            except Exception:
                pass
    except Exception:
        pass

    for driver in drivers:
        try:
            quit_fn = getattr(driver, "quit", None)
            if callable(quit_fn):
                quit_fn()
        except Exception:
            pass

    if delete_sandboxes:
        try:
            from infra.application.app import cleanup_environment

            for path in sandbox_paths:
                try:
                    cleanup_environment(path)
                except Exception:
                    pass
        except Exception:
            pass
