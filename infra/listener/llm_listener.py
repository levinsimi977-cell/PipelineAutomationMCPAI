from typing import Any, Callable, Dict, List, Optional, Tuple

from infra.agents.AuditRecorder import AuditRecorder
from infra.agents.answerAgent.answer_agent import (
    answer_question,
    build_prompt_with_answers,
)
from infra.agents.answerAgent.classification import Classification, classify_llm_output

MAX_QUESTION_ROUNDS = 10


def _merge_answers(
    
    state: dict,
    existing: List[Dict[str, Any]],
    qa_entry: Dict[str, Any],
) -> List[Dict[str, Any]]:
    return existing + [qa_entry]

def _state_for_classifier(
    state: dict,
    *,
    last_message: Any,
    installation_answers: List[Dict[str, Any]],
    question_rounds: int,
) -> dict:
    """State with latest agent message for classifier / answer agent."""
    agent_messages = list(state.get("agent_messages") or [])
    if last_message is not None:
        agent_messages = agent_messages + [last_message]

    return {
        **state,
        "installation_answers": installation_answers,
        "question_rounds": question_rounds,
        "last_agent_message": last_message,
        "agent_messages": agent_messages,
    }
def _tool_call_to_log_entry(tool_call: Any, platform: str) -> Dict[str, Any]:
    """Convert one agent tool_call to validator call_log entry."""
    if isinstance(tool_call, dict):
        name = tool_call.get("name", "")
        args = tool_call.get("args") or {}
    else:
        name = getattr(tool_call, "name", "")
        args = getattr(tool_call, "args", {}) or {}

    entry: Dict[str, Any] = {"tool": name}

    # iOS verify — action חובה לפי הפורמט של הקולגה
    if platform == "ios" and name == "verifyIosSdk":
        action = args.get("action")
        if action:
            entry["action"] = action

    return entry


def _append_tool_calls(
    call_log: List[Dict[str, Any]],
    tool_calls: List[Any],
    platform: str,
) -> List[Dict[str, Any]]:
    for tool_call in tool_calls:
        call_log.append(_tool_call_to_log_entry(tool_call, platform))
    return call_log


def build_mcp_sequence_payload(state: dict, call_log: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Wrapper object for colleague's validator."""
    platform = (state.get("platform") or "android").strip().lower()
    return {
        "platform": platform,
        "call_log": list(call_log),
    }


def get_mcp_sequence_for_validator(state: dict) -> Dict[str, Any]:
    """Return platform + ordered MCP tool call_log for sequence validation."""
    return build_mcp_sequence_payload(state, state.get("call_log") or [])


def _agent_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content or "")


def _get_tool_calls(response: Any) -> List[Any]:
    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls:
        return list(tool_calls)
    return []


def listener_on_text(
    state: dict,
    node_name: str,
    text: str,
    base_prompt: Optional[str] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Pass agent text to Classifier and act on the result.

    Listener does not classify — it puts the AI message in state and calls
    classify_llm_output (Classifier teammate).

    SUCCESS  → log only, caller continues.
    FAILURE  → test_status FAIL.
    QUESTION → forward to answer_agent, return updated prompt if base_prompt set.
    """
    updates: Dict[str, Any] = {"nodes_log": []}
    agent_text = (text or "").strip()
    if not agent_text:
        return None, updates

    installation_answers = list(state.get("installation_answers") or [])
    question_rounds = state.get("question_rounds", 0)

    merged_state = _state_for_classifier(
        state,
        last_message=text,
        installation_answers=installation_answers,
        question_rounds=question_rounds,
    )

    try:
        status = classify_llm_output(merged_state)
    except ValueError as exc:
        updates["nodes_log"].append({
            "node": node_name,
            "listener": "UNCLASSIFIED",
            "status": "INFO",
            "message": str(exc),
            "text_preview": agent_text[:200],
        })
        return None, updates

    if status == Classification.SUCCESS:
        updates["nodes_log"].append({
            "node": node_name,
            "listener": "SUCCESS",
            "status": "INFO",
            "message": "Classifier reported SUCCESS; continuing node operation.",
            "text_preview": agent_text[:200],
        })
        return None, updates

    if status == Classification.FAILURE:
        updates["nodes_log"].append({
            "node": node_name,
            "listener": "FAIL",
            "status": "FAIL",
            "reason": "Classifier reported FAILURE.",
            "text_preview": agent_text[:200],
        })
        updates["test_status"] = "FAIL"
        return None, updates

    # QUESTION — Classifier teammate decided; listener forwards to answer_agent.
    question_rounds = merged_state["question_rounds"] + 1
    if question_rounds > MAX_QUESTION_ROUNDS:
        updates["nodes_log"].append({
            "node": node_name,
            "listener": "QUESTION",
            "status": "FAIL",
            "reason": f"Exceeded max question rounds ({MAX_QUESTION_ROUNDS}).",
            "question_rounds": question_rounds,
        })
        updates["test_status"] = "FAIL"
        updates["question_rounds"] = question_rounds
        return None, updates

    question = agent_text
    answer = answer_question(merged_state, question)
    installation_answers = list(state.get("installation_answers") or [])
    qa_entry = {
        "question": question[:500],
        "answer": answer,
        "round": question_rounds,
    }
    installation_answers = _merge_answers(state, installation_answers, qa_entry)

    updates["nodes_log"].append({
        "node": node_name,
        "listener": "QUESTION",
        "status": "SUCCESS",
        "message": f"Answered question (round {question_rounds}).",
        "question_preview": question[:200],
        "answer": answer,
    })
    updates["question_rounds"] = question_rounds
    updates["installation_answers"] = [qa_entry]

    updated_prompt = None
    if base_prompt:
        updated_prompt = build_prompt_with_answers(base_prompt, installation_answers)

    return updated_prompt, updates

def listener_on_agent_response(
    state: dict,
    response: Any,
    base_prompt: str,
    node_name: str,
    run_mcp_tool: Callable[[str, Dict[str, Any]], str],
    *,
    is_done: Callable[[Any], bool],
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """Process one agent response (way B). sdkAgent calls LLM and passes response here.

    Returns:
        action: "continue" | "done" | "fail"
        next_prompt: prompt for the next agent.invoke (None when done/fail)
        updates: nodes_log, call_log delta, installation_answers delta, etc.
    """
    updates: Dict[str, Any] = {"nodes_log": []}
    installation_answers = list(state.get("installation_answers") or [])
    initial_answer_count = len(installation_answers)
    question_rounds = state.get("question_rounds", 0)
    platform = (state.get("platform") or "android").strip().lower()
    call_log = list(state.get("call_log") or [])
    initial_call_count = len(call_log)

    tool_calls = _get_tool_calls(response)
    if tool_calls:
        call_log = _append_tool_calls(call_log, tool_calls, platform)
        state["call_log"] = call_log
        updates["call_log"] = call_log[initial_call_count:]

        mcp_results: List[str] = []
        updated_prompt: Optional[str] = None
        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                tool_name = tool_call.get("name", "")
                tool_args = dict(tool_call.get("args") or {})
            else:
                tool_name = getattr(tool_call, "name", "")
                tool_args = dict(getattr(tool_call, "args", {}) or {})

            mcp_text = run_mcp_tool(tool_name, tool_args)
            mcp_results.append(f"[{tool_name}]\n{mcp_text}")

            updated_prompt, listener_updates = listener_on_text(
                {
                    **state,
                    "installation_answers": installation_answers,
                    "question_rounds": question_rounds,
                },
                node_name,
                mcp_text,
                base_prompt=base_prompt,
            )
            updates["nodes_log"].extend(listener_updates.get("nodes_log", []))
            if listener_updates.get("test_status") == "FAIL":
                updates["test_status"] = "FAIL"
                updates["question_rounds"] = listener_updates.get("question_rounds", question_rounds)
                updates["installation_answers"] = installation_answers[initial_answer_count:]
                updates["mcp_sequence"] = build_mcp_sequence_payload(state, call_log)
                return "fail", None, updates
            if listener_updates.get("question_rounds") is not None:
                question_rounds = listener_updates["question_rounds"]
            for qa in listener_updates.get("installation_answers", []):
                installation_answers = _merge_answers(state, installation_answers, qa)

        if updated_prompt is not None:
            next_prompt = updated_prompt
        else:
            next_prompt = build_prompt_with_answers(base_prompt, installation_answers)
        next_prompt += "\n\nMCP tool results:\n" + "\n\n".join(mcp_results)
        next_prompt += "\n\nContinue the SDK installation. Call the next required MCP tool if needed."

        updates["question_rounds"] = question_rounds
        updates["installation_answers"] = installation_answers[initial_answer_count:]
        updates["mcp_sequence"] = build_mcp_sequence_payload(state, call_log)
        return "continue", next_prompt, updates

    agent_text = _agent_content_text(getattr(response, "content", response)).strip()
    if is_done(response):
        updates["question_rounds"] = question_rounds
        updates["installation_answers"] = installation_answers[initial_answer_count:]
        updates["call_log"] = call_log[initial_call_count:]
        updates["mcp_sequence"] = build_mcp_sequence_payload(state, call_log)
        return "done", None, updates

    updated_prompt, listener_updates = listener_on_text(
        state, node_name, agent_text, base_prompt=base_prompt
    )
    updates["nodes_log"].extend(listener_updates.get("nodes_log", []))
    if listener_updates.get("test_status") == "FAIL":
        updates["test_status"] = "FAIL"
        updates["question_rounds"] = listener_updates.get("question_rounds", question_rounds)
        updates["installation_answers"] = installation_answers[initial_answer_count:]
        updates["call_log"] = call_log[initial_call_count:]
        updates["mcp_sequence"] = build_mcp_sequence_payload(state, call_log)
        return "fail", None, updates
    if updated_prompt is not None:
        updates["question_rounds"] = listener_updates.get("question_rounds", question_rounds)
        updates["installation_answers"] = installation_answers[initial_answer_count:]
        if listener_updates.get("installation_answers"):
            updates["installation_answers"] = listener_updates["installation_answers"]
        updates["mcp_sequence"] = build_mcp_sequence_payload(state, call_log)
        return "continue", updated_prompt, updates

    updates["question_rounds"] = listener_updates.get("question_rounds", question_rounds)
    updates["installation_answers"] = installation_answers[initial_answer_count:]
    next_prompt = build_prompt_with_answers(
        base_prompt,
        installation_answers,
    ) + "\n\nProceed with your task now. If SDK integration requires it, call the integrateSdk tool."
    updates["mcp_sequence"] = build_mcp_sequence_payload(state, call_log)
    return "continue", next_prompt, updates


def _is_human_message(msg: Any) -> bool:
    msg_type = getattr(msg, "type", "")
    return msg_type == "human" or msg.__class__.__name__ == "HumanMessage"


def _current_turn_messages(messages: Optional[List[Any]]) -> List[Any]:
    """Messages from the latest ainvoke only (after the last HumanMessage).

    sdkAgent passes full checkpointer history; classifier and call_log must
    use the current turn slice so we do not re-classify old AI text or
    duplicate MCP tool entries.
    """
    if not messages:
        return []
    last_human_idx = -1
    for i, msg in enumerate(messages):
        if _is_human_message(msg):
            last_human_idx = i
    if last_human_idx < 0:
        return list(messages)
    return list(messages[last_human_idx + 1:])


def _last_ai_text(messages: List[Any]) -> str:
    """Return AI text from the current turn only — for Classifier."""
    for msg in reversed(_current_turn_messages(messages)):
        msg_type = getattr(msg, "type", "")
        if msg_type == "ai" or msg.__class__.__name__ == "AIMessage":
            text = _agent_content_text(getattr(msg, "content", "")).strip()
            if text:
                return text
    return ""


# File tools from sdkAgent — not part of MCP sequence validation
_SDK_FILE_TOOLS = frozenset({
    "list_project_files",
    "read_project_file",
    "write_to_project_file",
})


def _tool_call_name_and_args(tool_call: Any) -> Tuple[str, Dict[str, Any]]:
    if isinstance(tool_call, dict):
        return tool_call.get("name", ""), dict(tool_call.get("args") or {})
    return getattr(tool_call, "name", ""), dict(getattr(tool_call, "args", {}) or {})


def _extract_message_audit_events(messages: Optional[List[Any]]) -> List[Tuple[str, Dict[str, Any]]]:
    """Map sdkAgent memory messages to AuditRecorder event types."""
    events: List[Tuple[str, Dict[str, Any]]] = []
    for msg in messages or []:
        msg_type = getattr(msg, "type", "")
        class_name = msg.__class__.__name__

        if msg_type == "ai" or class_name == "AIMessage":
            for tool_call in getattr(msg, "tool_calls", None) or []:
                tool_name, tool_args = _tool_call_name_and_args(tool_call)
                if not tool_name or tool_name in _SDK_FILE_TOOLS:
                    continue
                events.append(("AGENT_DECISION", {"tool": tool_name, "args": tool_args}))

        if msg_type == "tool" or class_name == "ToolMessage":
            tool_name = getattr(msg, "name", "")
            if not tool_name or tool_name in _SDK_FILE_TOOLS:
                continue
            result_text = _agent_content_text(getattr(msg, "content", ""))
            events.append(("MCP_TOOL_RESULT", {
                "tool": tool_name,
                "result": result_text[:2000],
            }))

    return events


def _record_listener_updates_to_audit(
    audit_recorder: AuditRecorder,
    updates: Dict[str, Any],
    *,
    node_name: str,
    action: str,
    messages: Optional[List[Any]] = None,
    agent_text: str = "",
) -> None:
    """Write all listener turn data to AuditRecorder."""
    for event_type, payload in _extract_message_audit_events(messages):
        audit_recorder.write(event_type, payload)

    for entry in updates.get("call_log", []):
        audit_recorder.write("MCP_CALL_LOG", entry)

    if updates.get("mcp_sequence"):
        audit_recorder.write("MCP_SEQUENCE", updates["mcp_sequence"])

    for log in updates.get("nodes_log", []):
        audit_recorder.write("LISTENER_DECISION", log)
        if log.get("listener") == "QUESTION" and log.get("answer"):
            audit_recorder.write("SIMULATED_USER_REPLY", {
                "question": log.get("question_preview"),
                "answer": log.get("answer"),
            })

    for qa_entry in updates.get("installation_answers", []):
        audit_recorder.write("INSTALLATION_ANSWER", qa_entry)

    if updates.get("test_status"):
        audit_recorder.write("LISTENER_TEST_STATUS", {
            "node": node_name,
            "test_status": updates["test_status"],
        })

    audit_recorder.write("LISTENER_TURN", {
        "node": node_name,
        "action": action,
        "test_status": updates.get("test_status"),
        "question_rounds": updates.get("question_rounds"),
        "agent_text_preview": (agent_text or "")[:500],
        "new_mcp_calls": len(updates.get("call_log") or []),
    })


def _finalize_turn(
    state: dict,
    updates: Dict[str, Any],
    audit_recorder: Optional[AuditRecorder],
    *,
    node_name: str = "",
    action: str = "",
    messages: Optional[List[Any]] = None,
    agent_text: str = "",
) -> None:
    if audit_recorder is not None:
        _record_listener_updates_to_audit(
            audit_recorder,
            updates,
            node_name=node_name,
            action=action,
            messages=messages,
            agent_text=agent_text,
        )
    _apply_listener_updates(state, updates)


def listener_on_agent_turn(
    state: dict,
    node_name: str,
    base_prompt: str,
    messages: List[Any],
    audit_recorder: Optional[AuditRecorder] = None,
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """Process one sdkAgent turn (result['messages'] from ainvoke).
    Listener responsibilities:
      1. Receive memory messages from sdkAgent
      2. Record to Audit (if audit_recorder passed)
      3. Classify LLM text via classify_llm_output (classifier teammate)
      4. Answer QUESTION via answer_agent
      5. Return action + next_prompt + updates to sdkAgent

    Returns:
        action: "continue" | "done" | "fail"
        next_prompt: prompt for next ainvoke (None when done/fail)
        updates: nodes_log, call_log, mcp_sequence, etc.
    """
    updates: Dict[str, Any] = {"nodes_log": []}
    platform = (state.get("platform") or "android").strip().lower()

    turn_messages = _current_turn_messages(messages)
    turn_tool_calls: List[Any] = []
    for msg in turn_messages:
        calls = getattr(msg, "tool_calls", None)
        if not calls:
            continue
        for call in calls:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", "")
            if name not in _SDK_FILE_TOOLS:
                turn_tool_calls.append(call)

    call_log = list(state.get("call_log") or [])
    initial_call_count = len(call_log)
    if turn_tool_calls:
        call_log = _append_tool_calls(call_log, turn_tool_calls, platform)
        state["call_log"] = call_log
        updates["call_log"] = call_log[initial_call_count:]

    updates["mcp_sequence"] = build_mcp_sequence_payload(state, call_log)

    final_text = _last_ai_text(messages)

    updated_prompt, listener_updates = listener_on_text(
        state, node_name, final_text, base_prompt=base_prompt
    )
    updates["nodes_log"].extend(listener_updates.get("nodes_log", []))
    if "question_rounds" in listener_updates:
        updates["question_rounds"] = listener_updates["question_rounds"]
    if listener_updates.get("installation_answers"):
        updates["installation_answers"] = listener_updates["installation_answers"]

    text_upper = final_text.upper()
    if listener_updates.get("test_status") == "FAIL":
        updates["test_status"] = "FAIL"
        action, next_prompt = "fail", None
    elif updated_prompt is not None:
        action, next_prompt = "continue", updated_prompt
    elif "STATUS: SUCCESS" in text_upper:
        action, next_prompt = "done", None
    elif "STATUS: FAILURE" in text_upper:
        updates["test_status"] = "FAIL"
        action, next_prompt = "fail", None
    else:
        action, next_prompt = (
            "continue",
            base_prompt + "\n\nContinue the SDK installation. Call the next required tool if needed.",
        )

    _finalize_turn(
        state,
        updates,
        audit_recorder,
        node_name=node_name,
        action=action,
        messages=turn_messages,
        agent_text=final_text,
    )
    return action, next_prompt, updates


def _apply_listener_updates(state: dict, updates: Dict[str, Any]) -> None:
    """Merge listener deltas into state (for sdkAgent loop)."""
    if "question_rounds" in updates:
        state["question_rounds"] = updates["question_rounds"]
    if updates.get("installation_answers"):
        state["installation_answers"] = (
            list(state.get("installation_answers") or []) + updates["installation_answers"]
        )
    if updates.get("call_log"):
        state["call_log"] = list(state.get("call_log") or []) + updates["call_log"]


def invoke_plain_llm_with_listener(
    state: dict,
    base_prompt: str,
    node_name: str,
    llm_invoke: Callable[[str], Any],
    *,
    is_done: Callable[[Any], bool],
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """Invoke a plain LLM call in a listener loop until is_done(response) or FAIL."""
    logs: List[Dict[str, Any]] = []
    installation_answers = list(state.get("installation_answers") or [])
    initial_answer_count = len(installation_answers)
    question_rounds = state.get("question_rounds", 0)
    current_prompt = base_prompt
    if installation_answers:
        current_prompt = build_prompt_with_answers(base_prompt, installation_answers)

    while True:
        response = llm_invoke(current_prompt)

        if is_done(response):
            return response, {
                "nodes_log": logs,
                "question_rounds": question_rounds,
                "installation_answers": installation_answers[initial_answer_count:],
            }

        agent_text = _agent_content_text(
            response.content if hasattr(response, "content") else response
        ).strip()
        updated_prompt, listener_updates = listener_on_text(
            {
                **state,
                "installation_answers": installation_answers,
                "question_rounds": question_rounds,
            },
            node_name,
            agent_text,
            base_prompt=base_prompt,
        )
        logs.extend(listener_updates.get("nodes_log", []))
        if listener_updates.get("test_status") == "FAIL":
            return response, {
                "nodes_log": logs,
                "test_status": "FAIL",
                "question_rounds": listener_updates.get("question_rounds", question_rounds),
                "installation_answers": installation_answers[initial_answer_count:],
            }
        if listener_updates.get("question_rounds") is not None:
            question_rounds = listener_updates["question_rounds"]
        for qa in listener_updates.get("installation_answers", []):
            installation_answers = _merge_answers(state, installation_answers, qa)
        if updated_prompt is not None:
            current_prompt = updated_prompt
            continue
        current_prompt = build_prompt_with_answers(base_prompt, installation_answers)
        current_prompt += "\n\nReturn the required output now."
        continue