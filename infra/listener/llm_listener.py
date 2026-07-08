from typing import Any, Callable, Dict, List, Optional, Tuple
from infra.agents.answerAgent.answer_agent import (
    answer_question,
    build_prompt_with_answers,
    classify_question,
)

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


def listener_on_text(
    state: dict,
    node_name: str,
    text: str,
    base_prompt: Optional[str] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Classify existing text (no new LLM call). Returns (updated_prompt|None, state_updates).

    SUCCESS  → log only, caller continues node operation from the same place.
    FAIL     → test_status FAIL.
    QUESTION → answer, append to installation_answers; if base_prompt given, return updated prompt.
    """
    updates: Dict[str, Any] = {"nodes_logs": []}
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
    classification = classify_agent_response(merged_state, agent_text)
    label = classification["label"]
    reason = classification.get("reason", "")

    if label == "SUCCESS":
        updates["nodes_logs"].append({
            "node": node_name,
            "listener": "SUCCESS",
            "status": "INFO",
            "message": reason or "Agent reported success; continuing node operation.",
            "text_preview": agent_text[:200],
        })
        return None, updates

    if label == "FAIL":
        updates["nodes_logs"].append({
            "node": node_name,
            "listener": "FAIL",
            "status": "FAIL",
            "reason": reason or "Listener classified response as failure.",
            "text_preview": agent_text[:200],
        })
        updates["test_status"] = "FAIL"
        return None, updates

    # QUESTION
    question_rounds = merged_state["question_rounds"] + 1
    if question_rounds > MAX_QUESTION_ROUNDS:
        updates["nodes_logs"].append({
            "node": node_name,
            "listener": "QUESTION",
            "status": "FAIL",
            "reason": f"Exceeded max question rounds ({MAX_QUESTION_ROUNDS}).",
            "question_rounds": question_rounds,
        })
        updates["test_status"] = "FAIL"
        updates["question_rounds"] = question_rounds
        return None, updates

    question = classification.get("question") or agent_text
    answer = answer_question(merged_state, question)  # לא state
    category = classify_question(question)
    installation_answers = list(state.get("installation_answers") or [])
    qa_entry = {
        "question": question[:500],
        "answer": answer,
        "category": category,
        "round": question_rounds,
    }
    installation_answers = _merge_answers(state, installation_answers, qa_entry)

    updates["nodes_logs"].append({
        "node": node_name,
        "listener": "QUESTION",
        "status": "SUCCESS",
        "message": f"Answered question (round {question_rounds}).",
        "category": category,
        "question_preview": question[:200],
        "answer": answer,
    })
    updates["question_rounds"] = question_rounds
    updates["installation_answers"] = [qa_entry]

    updated_prompt = None
    if base_prompt:
        updated_prompt = build_prompt_with_answers(base_prompt, installation_answers)

    return updated_prompt, updates

def invoke_agent_with_listener(
    state: dict,
    base_prompt: str,
    node_name: str,
    run_mcp_tool: Callable[[str, Dict[str, Any]], str],
    *,
    is_done: Callable[[Any], bool],
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """Invoke tool-bound agent; listen continuously through tools until is_done or FAIL."""
    logs: List[Dict[str, Any]] = []
    installation_answers = list(state.get("installation_answers") or [])
    initial_answer_count = len(installation_answers)
    question_rounds = state.get("question_rounds", 0)
    platform = (state.get("platform") or "android").strip().lower()
    call_log = list(state.get("call_log") or [])
    initial_call_count = len(call_log)
    current_prompt = base_prompt
    if installation_answers:
        current_prompt = build_prompt_with_answers(base_prompt, installation_answers)

    while True:
        response = invoke_agent(current_prompt)

        if response.tool_calls:
            call_log = _append_tool_calls(call_log, response.tool_calls, platform)
            state["call_log"] = call_log

            mcp_results: List[str] = []
            updated_prompt: Optional[str] = None
            for tool_call in response.tool_calls:
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
                logs.extend(listener_updates.get("nodes_logs", []))
                if listener_updates.get("test_status") == "FAIL":
                    return response, {
                        "nodes_logs": logs,
                        "test_status": "FAIL",
                        "question_rounds": listener_updates.get("question_rounds", question_rounds),
                        "installation_answers": installation_answers[initial_answer_count:],
                        "call_log": call_log[initial_call_count:],
                        "mcp_sequence": build_mcp_sequence_payload(state, call_log),
                    }
                if listener_updates.get("question_rounds") is not None:
                    question_rounds = listener_updates["question_rounds"]
                for qa in listener_updates.get("installation_answers", []):
                    installation_answers = _merge_answers(state, installation_answers, qa)

            if updated_prompt is not None:
                current_prompt = updated_prompt
            else:
                current_prompt = build_prompt_with_answers(base_prompt, installation_answers)
            current_prompt += "\n\nMCP tool results:\n" + "\n\n".join(mcp_results)
            current_prompt += "\n\nContinue the SDK installation. Call the next required MCP tool if needed."
            continue

        agent_text = agent_content_text(response.content).strip()
        if is_done(response):
            return response, {
                "nodes_logs": logs,
                "question_rounds": question_rounds,
                "installation_answers": installation_answers[initial_answer_count:],
                "call_log": call_log[initial_call_count:],
                "mcp_sequence": build_mcp_sequence_payload(state, call_log),
            }
        merged_state = _state_for_classifier(
            state,
            last_message=response,
            installation_answers=installation_answers,
            question_rounds=question_rounds,
        )
        classification = classify_agent_response(merged_state, agent_text)
        label = classification["label"]
        reason = classification.get("reason", "")

        if label == "SUCCESS":
            logs.append({
                "node": node_name,
                "listener": "SUCCESS",
                "status": "INFO",
                "message": reason or "Agent reported success; continuing.",
                "text_preview": agent_text[:200],
            })
            current_prompt = build_prompt_with_answers(
                base_prompt,
                installation_answers,
            ) + "\n\nProceed with your task now. If SDK integration requires it, call the integrateSdk tool."
            continue

        if label == "FAIL":
            logs.append({
                "node": node_name,
                "listener": "FAIL",
                "status": "FAIL",
                "reason": reason or "Listener classified response as failure.",
                "text_preview": agent_text[:200],
            })
            return response, {
                "nodes_logs": logs,
                "test_status": "FAIL",
                "question_rounds": question_rounds,
                "installation_answers": installation_answers[initial_answer_count:],
                "call_log": call_log[initial_call_count:],
                "mcp_sequence": build_mcp_sequence_payload(state, call_log),
            }

        # QUESTION
        question_rounds += 1
        if question_rounds > MAX_QUESTION_ROUNDS:
            logs.append({
                "node": node_name,
                "listener": "QUESTION",
                "status": "FAIL",
                "reason": f"Exceeded max question rounds ({MAX_QUESTION_ROUNDS}).",
                "question_rounds": question_rounds,
            })
            return response, {
                "nodes_logs": logs,
                "test_status": "FAIL",
                "question_rounds": question_rounds,
                "installation_answers": installation_answers[initial_answer_count:],
                "call_log": call_log[initial_call_count:],
                "mcp_sequence": build_mcp_sequence_payload(state, call_log),
            }

        question = classification.get("question") or agent_text
        answer = answer_question(merged_state, question)
        category = classify_question(question)
        qa_entry = {
            "question": question[:500],
            "answer": answer,
            "category": category,
            "round": question_rounds,
        }
        installation_answers = _merge_answers(state, installation_answers, qa_entry)
        logs.append({
            "node": node_name,
            "listener": "QUESTION",
            "status": "SUCCESS",
            "message": f"Answered question (round {question_rounds}).",
            "category": category,
            "question_preview": question[:200],
            "answer": answer,
        })
        current_prompt = build_prompt_with_answers(base_prompt, installation_answers)






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
                "nodes_logs": logs,
                "question_rounds": question_rounds,
                "installation_answers": installation_answers[initial_answer_count:],
            }

        agent_text = agent_content_text(
            response.content if hasattr(response, "content") else response
        ).strip()
        merged_state = _state_for_classifier(
            state,
            last_message=response,
            installation_answers=installation_answers,
            question_rounds=question_rounds,
        )
        classification = classify_agent_response(merged_state, agent_text)
        label = classification["label"]
        reason = classification.get("reason", "")

        if label == "SUCCESS":
            logs.append({
                "node": node_name,
                "listener": "SUCCESS",
                "status": "INFO",
                "message": reason or "Model reported success; continuing.",
                "text_preview": agent_text[:200],
            })
            current_prompt = build_prompt_with_answers(base_prompt, installation_answers)
            current_prompt += "\n\nReturn the required output now."
            continue

        if label == "FAIL":
            logs.append({
                "node": node_name,
                "listener": "FAIL",
                "status": "FAIL",
                "reason": reason or "Listener classified response as failure.",
                "text_preview": agent_text[:200],
            })
            return response, {
                "nodes_logs": logs,
                "test_status": "FAIL",
                "question_rounds": question_rounds,
                "installation_answers": installation_answers[initial_answer_count:],
            }

        question_rounds += 1
        if question_rounds > MAX_QUESTION_ROUNDS:
            logs.append({
                "node": node_name,
                "listener": "QUESTION",
                "status": "FAIL",
                "reason": f"Exceeded max question rounds ({MAX_QUESTION_ROUNDS}).",
                "question_rounds": question_rounds,
            })
            return response, {
                "nodes_logs": logs,
                "test_status": "FAIL",
                "question_rounds": question_rounds,
                "installation_answers": installation_answers[initial_answer_count:],
            }

        question = classification.get("question") or agent_text
        answer = answer_question(merged_state, question)
        category = classify_question(question)
        qa_entry = {
            "question": question[:500],
            "answer": answer,
            "category": category,
            "round": question_rounds,
        }
        installation_answers = _merge_answers(state, installation_answers, qa_entry)
        logs.append({
            "node": node_name,
            "listener": "QUESTION",
            "status": "SUCCESS",
            "message": f"Answered question (round {question_rounds}).",
            "category": category,
            "question_preview": question[:200],
            "answer": answer,
        })
        current_prompt = build_prompt_with_answers(base_prompt, installation_answers)
