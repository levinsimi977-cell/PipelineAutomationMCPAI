"""Builds iOS prompt instructions for the prompt agent node."""

DEFAULT_GOAL = (
    "Install AppsFlyer's iOS SDK in my app using the AppsFlyer MCP. "
    "The target platform is ios."
)


def get_ios_agent_prompt(app_path: str, goal: str | None = None) -> str:
    """Generate iOS instructions for the next agent in the pipeline."""
    user_goal = goal or DEFAULT_GOAL

    if app_path:
        user_goal = f"{user_goal} iOS project path: {app_path}"

    return (
        f"User goal: {user_goal}\n\n"
        "You are an expert technical prompt engineer. "
        "Convert the user's goal into a single, concise, and direct instructional prompt "
        "for an AI coding assistant.\n"
        "The prompt should be highly technical, straight to the point, and written in English.\n"
        "The generated prompt MUST preserve the AppsFlyer MCP flow: the assistant must call "
        "the AppsFlyer MCP tool `integrateSdk`, not answer from memory.\n"
        "The generated prompt MUST explicitly target iOS integration.\n"
        "The generated prompt MUST include platform=ios.\n"
        "The generated prompt MUST include the iOS app project path when provided.\n"
        "The generated prompt MUST instruct the assistant not to ask clarification questions "
        "and to proceed automatically.\n"
        "Return ONLY the generated prompt, without any conversational text or quotes."
    )