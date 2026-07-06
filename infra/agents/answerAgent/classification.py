def _regex_validate_classification(text: str, llm_classification: str) -> str | None:
    """
    Regex-based validation of LLM classification — independent verification.
    
    Returns:
        - The validated classification if regex confirms
        - None if regex validation fails
    """
    text_lower = (text or "").lower()
    
    # Success patterns (English + Hebrew)
    success_patterns = [
        r"\b(completed|finished|done|successfully|success)\b",
        r"\b(השלמתי|הסתיים|בוצע|בהצלחה)\b",
        r"installation\s+(is\s+)?complete",
        r"התקנה\s+(הסתיימה|הושלמה)",
    ]
    
    # Failure patterns (English + Hebrew)
    failure_patterns = [
        r"\b(error|failed|failure|exception|cannot|unable)\b",
        r"\b(שגיאה|נכשל|כשל|לא\s+הצלחתי|בעיה)\b",
        r"stack\s+trace",
        r"traceback",
    ]
    
    # Question patterns (English + Hebrew)
    question_patterns = [
        r"\?\s*$",  # Ends with question mark
        r"^(do you|should i|can you|would you|האם|מה|איך|למה|האם אתה|האם את)\b",
        r"\b(please provide|need to know|what is|which|האם תוכל|אנא ספק)\b",
    ]
    
    has_success = any(re.search(pattern, text_lower) for pattern in success_patterns)
    has_failure = any(re.search(pattern, text_lower) for pattern in failure_patterns)
    has_question = any(re.search(pattern, text_lower) for pattern in question_patterns)
    
    # Validate LLM classification with regex
    if llm_classification == "SUCCESS" and has_success and not has_failure:
        return "SUCCESS"
    if llm_classification == "FAILURE" and has_failure:
        return "FAILURE"
    if llm_classification == "QUESTION" and has_question:
        return "QUESTION"
    
    # If LLM said success but we see failure markers, override
    if llm_classification == "SUCCESS" and has_failure:
        return None  # Validation failed
    
    # If regex strongly suggests different classification
    if has_question and not has_success and not has_failure:
        return "QUESTION"
    if has_failure:
        return "FAILURE"
    if has_success and not has_failure and not has_question:
        return "SUCCESS"
    
    return None  # Unable to validate


def _llm_classify_response(text: str, conversation_history: str = "") -> str:
    """
    Use LLM to classify the agent's last response.
    
    Returns: "SUCCESS", "FAILURE", or "QUESTION"
    """
    if not getattr(config, "GEMINI_API_KEY", ""):
        # Fallback to regex-only if no API key
        if re.search(r"\?\s*$", text.lower()):
            return "QUESTION"
        if re.search(r"\b(error|failed|exception)\b", text.lower()):
            return "FAILURE"
        return "SUCCESS"
    
    classification_prompt = f"""You are a response classifier for an installation agent.

Analyze the LAST MESSAGE from the agent and classify it into ONE of these categories:

1. **SUCCESS** - The agent completed its task successfully
   - Installation finished
   - All steps completed
   - No errors or questions remaining

2. **FAILURE** - The agent encountered an error or failed
   - Error messages
   - Exceptions
   - Unable to proceed
   - Something broke

3. **QUESTION** - The agent is waiting for user input
   - Asking a question
   - Needs clarification
   - Waiting for a decision

Conversation history (for context):
{conversation_history[:1000] if conversation_history else "Not available"}

LAST MESSAGE to classify:
{text}

Respond with ONLY ONE WORD: SUCCESS, FAILURE, or QUESTION"""

    try:
        response = _llm().invoke(classification_prompt)
        content = response.content if hasattr(response, "content") else str(response)
        classification = str(content or "").strip().upper()
        
        # Ensure valid response
        if classification in {"SUCCESS", "FAILURE", "QUESTION"}:
            return classification
    except Exception:
        pass
    
    # Fallback to regex
    if re.search(r"\?\s*$", text.lower()):
        return "QUESTION"
    if re.search(r"\b(error|failed|exception)\b", text.lower()):
        return "FAILURE"
    return "SUCCESS"


def classify_agent_response(state: dict[str, Any]) -> dict[str, str]:
    """
    Classify the agent's response with dual validation (LLM + Regex).
    
    This function should be called when the LLM finishes or is waiting.
    It determines if the response is: SUCCESS, FAILURE, or QUESTION.
    
    Args:
        state: The agent state containing conversation history
        
    Returns:
        dict with keys:
            - classification: "SUCCESS", "FAILURE", or "QUESTION"
            - confidence: "high" (both LLM and regex agree) or "medium" (only one agrees)
            - validated: True if regex validation passed, False otherwise
            - raw_text: The text that was classified
    
    Example usage:
        result = classify_agent_response(state)
        if result['classification'] == 'QUESTION':
            # Don't send back to LLM, wait for user
            pass
        elif result['classification'] == 'SUCCESS':
            # Continue to next step
            pass
        elif result['classification'] == 'FAILURE':
            # Handle error
            pass
    """
    # Extract the last agent message
    last_message = None
    raw_text = ""
    
    # Try different state keys where the message might be
    if state.get("last_agent_message"):
        last_message = state["last_agent_message"]
        raw_text = getattr(last_message, "content", None) or str(last_message)
    elif state.get("agent_messages") and isinstance(state["agent_messages"], list):
        for msg in reversed(state["agent_messages"]):
            role = getattr(msg, "type", None) or msg.__class__.__name__
            if "ai" in role.lower() or "assistant" in role.lower():
                last_message = msg
                raw_text = getattr(msg, "content", None) or str(msg)
                break
    
    if not raw_text:
        # Fallback: couldn't find message, assume waiting
        return {
            "classification": "QUESTION",
            "confidence": "low",
            "validated": False,
            "raw_text": "",
        }
    
    # Build conversation context for LLM
    conversation_history = _format_agent_context(state)
    
    # Step 1: LLM classification
    llm_classification = _llm_classify_response(raw_text, conversation_history)
    
    # Step 2: Regex validation
    regex_classification = _regex_validate_classification(raw_text, llm_classification)
    
    # Determine final classification and confidence
    if regex_classification and regex_classification == llm_classification:
        # Both agree - high confidence
        return {
            "classification": llm_classification,
            "confidence": "high",
            "validated": True,
            "raw_text": raw_text[:500],
        }
    elif regex_classification:
        # Regex override - medium confidence
        return {
            "classification": regex_classification,
            "confidence": "medium",
            "validated": True,
            "raw_text": raw_text[:500],
        }
    else:
        # Only LLM classification - medium confidence
        return {
            "classification": llm_classification,
            "confidence": "medium",
            "validated": False,
            "raw_text": raw_text[:500],
        }

