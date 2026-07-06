from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

USE_CASES_PATH = Path(__file__).resolve().parent.parent / "data" / "useCases" / "useCase.json"

PLATFORMS = ["ios", "android"]
DEPENDENCY_MANAGERS = ["cocoapods", "spm"]


def load_use_cases() -> list[dict]:
    if not USE_CASES_PATH.exists():
        return []
    raw = USE_CASES_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


def save_use_cases(use_cases: list[dict]) -> None:
    USE_CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    USE_CASES_PATH.write_text(json.dumps(use_cases, indent=2, ensure_ascii=False), encoding="utf-8")


def create_use_case(
    name: str,
    platform: str,
    app_id: str,
    app_path: str,
    dev_key: str,
    prompt_goal: str,
    use_att: bool,
    use_cuid: bool,
    use_scene_delegate: bool,
    use_response_listener: bool,
    dependency_manager: str,
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name.strip(),
        "platform": platform,
        "app_id": app_id.strip(),
        "app_path": app_path.strip(),
        "dev_key": dev_key.strip(),
        "prompt_goal": prompt_goal.strip(),
        "answer_policy": {
            "use_att": use_att,
            "use_cuid": use_cuid,
            "use_scene_delegate": use_scene_delegate,
            "use_response_listener": use_response_listener,
            "dependency_manager": dependency_manager,
        },
        "installation_answers": [],
        "agent_messages": [],
        "installation_agent_summary": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


st.set_page_config(page_title="Use Case Builder", page_icon="🧪", layout="centered")
st.title("🧪 Use Case Builder")
st.caption("Create a test use case for the AppsFlyer SDK automation pipeline.")

with st.form("use_case_form", clear_on_submit=True):
    name = st.text_input("Use case name *", placeholder="e.g. iOS onelink deep link test")
    platform = st.selectbox("Platform *", PLATFORMS)
    app_id = st.text_input("App ID", placeholder="e.g. id123456789 / com.example.app")
    app_path = st.text_input("App path", placeholder="e.g. ./today_app")
    dev_key = st.text_input("Dev Key", placeholder="AppsFlyer dev key")
    prompt_goal = st.text_area(
        "Prompt goal *",
        placeholder="Describe in free text what the agent should test, e.g. "
        "\"Integrate the AppsFlyer SDK, enable deep linking, and verify a "
        "direct OneLink open reaches the app.\"",
        height=150,
    )

    st.markdown("**Answer policy**")
    policy_col1, policy_col2 = st.columns(2)
    with policy_col1:
        use_att = st.checkbox("Use ATT")
        use_scene_delegate = st.checkbox("Use Scene Delegate")
    with policy_col2:
        use_cuid = st.checkbox("Use CUID")
        use_response_listener = st.checkbox("Use Response Listener")
    dependency_manager = st.selectbox("Dependency manager", DEPENDENCY_MANAGERS)

    submitted = st.form_submit_button("Create use case")

if submitted:
    if not name or not prompt_goal:
        st.error("Use case name and prompt goal are required.")
    else:
        use_cases = load_use_cases()
        use_cases.append(
            create_use_case(
                name,
                platform,
                app_id,
                app_path,
                dev_key,
                prompt_goal,
                use_att,
                use_cuid,
                use_scene_delegate,
                use_response_listener,
                dependency_manager,
            )
        )
        save_use_cases(use_cases)
        st.success(f"Use case '{name}' created and saved.")

st.divider()
st.subheader("Existing use cases")

use_cases = load_use_cases()
if not use_cases:
    st.info("No use cases yet. Create one above.")
else:
    for case in reversed(use_cases):
        with st.expander(f"{case.get('name', 'Untitled')} ({case.get('platform', '?')})"):
            st.write(f"**ID:** {case.get('id')}")
            st.write(f"**App ID:** {case.get('app_id') or '—'}")
            st.write(f"**App path:** {case.get('app_path') or '—'}")
            st.write(f"**Dev Key:** {case.get('dev_key') or '—'}")
            st.write(f"**Created at:** {case.get('created_at')}")
            st.write("**Prompt goal:**")
            st.write(case.get("prompt_goal") or case.get("prompt") or "—")

            policy = case.get("answer_policy") or {}
            if policy:
                st.write("**Answer policy:**")
                st.json(policy)

            summary = case.get("installation_agent_summary")
            answers = case.get("installation_answers") or []
            messages = case.get("agent_messages") or []
            st.write(
                f"**Run status:** {len(answers)} answers logged, "
                f"{len(messages)} agent messages"
                + (f" — summary: {summary}" if summary else " — not run yet")
            )

            if st.button("Delete", key=f"delete_{case.get('id')}"):
                remaining = [c for c in use_cases if c.get("id") != case.get("id")]
                save_use_cases(remaining)
                st.rerun()
