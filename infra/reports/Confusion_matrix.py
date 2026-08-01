from infra.workflow.workflow_nodes import PipelineState


def is_valid_order(state: PipelineState) -> bool:
    return state.get("is_tool_order_valid", False)


def is_sdk_agent_successful(state: PipelineState) -> bool:
    # סינון: לוקחים מהלוג הכללי רק את הריצות של הצומת שלנו
    sdk_logs = [log for log in state.get("nodes_log", []) if log.get("node") == "sdk_agent"]

    # מחזירים True אך ורק אם הצומת אכן רצה לפחות פעם אחת, ו*כל* הריצות שלה הן "Success"
    return bool(sdk_logs) and all(log.get("status") == "Success" for log in sdk_logs)


def evaluate_state_results(state: PipelineState):
    resultsdk = is_sdk_agent_successful(state)
    result_tool_order = is_valid_order(state)
    if resultsdk and result_tool_order:
        return "True Positive"
    if resultsdk and not result_tool_order:
        return "False Positive"
    if not resultsdk and not result_tool_order:
        return "True Negative"
    if not resultsdk and result_tool_order:
        return "False Negative"
