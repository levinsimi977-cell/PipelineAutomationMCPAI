
import asyncio
from pathlib import Path
from infra.agents.AuditRecorder import AuditRecorder
from infra.agents.sdkAgent.tools.agent import (
    run_sdk_integration_agent,
    close_sdk_integration_agent,
)
async def main():
    run_dir = Path("data/runs/manual_test")
    run_dir.mkdir(parents=True, exist_ok=True)  # AuditRecorder.write() expects this to exist
    audit_recorder = AuditRecorder(run_dir=run_dir)
    state = {"agent_id": 0, "platform": "android"}  # agent_id=0 -> creates a new agent session
    result = await run_sdk_integration_agent(
        state=state,
        project_root_str=str(Path("sandboxes/manual_test_android").resolve()),
        platform="android",
        user_prompt="Integrate the AppsFlyer SDK into this app.",
        audit_recorder=audit_recorder,
    )
    print("RESULT:", result)
    print("agent_id in state after call:", state["agent_id"])
    # Call again to continue the SAME conversation (e.g. simulating verify_prompt):
    result2 = await run_sdk_integration_agent(
        state=state,  # same state dict -> agent_id is now non-zero, resumes the session
        project_root_str=str(Path("sandboxes/manual_test_android").resolve()),
        platform="android",
        user_prompt="Now verify the SDK integration.",
        audit_recorder=audit_recorder,
    )
    print("RESULT 2:", result2)
    # When truly done (e.g. after the verify pass), free the session:
    close_sdk_integration_agent(state, audit_recorder)
asyncio.run(main())