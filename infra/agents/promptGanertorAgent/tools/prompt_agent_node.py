from .prompt_agent_android import get_android_agent_prompt
from .prompt_agent_ios import get_ios_agent_prompt


def prompt_agent_node(state: dict) -> dict:
    platform = str(state.get("platform") or "").strip().lower()
    app_path = state.get("app_path")
    goal = state.get("prompt_goal")

    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("State must contain a non-empty prompt_goal")

    if platform == "android":
        generated_prompt = get_android_agent_prompt(app_path, goal)
    elif platform == "ios":
        generated_prompt = get_ios_agent_prompt(app_path, goal)
    else:
        raise ValueError(f"Unsupported platform: {platform}")

    return {
        "agent_base_prompt": generated_prompt,
        "prompt_platform": platform,
    }