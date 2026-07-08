from typing import Dict, Any

def answer_policy_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract rules / policy from use_case and inject into state
    for the Answer Agent to use later.
    """
    # שליפת ה-use case מתוך ה-state (מכיל את ה-JSON שלך)
    use_case = state.get("use_case", {})

    # תיקון: שולפים לפי המפתח האמיתי ב-JSON שלך - "answer_policy"
    rules = use_case.get("answer_policy")

    # מכניסים ל-state תחת המפתח שסוכן התשובות מצפה לו
    state["answer_policy"] = rules
    return state