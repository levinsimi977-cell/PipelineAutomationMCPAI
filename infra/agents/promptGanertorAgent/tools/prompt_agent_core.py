import os
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

# הגדרת המודל
model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

llm = ChatOpenAI(
    model=model,
    temperature=0.1,
    api_key=os.getenv("OPENAI_API_KEY") or os.getenv("GPT_API_KEY"),
)

# תבנית מאוחדת עם דרישת פלט מחמירה ל-STATUS
unified_meta_prompt_template = PromptTemplate.from_template(
    "User goal: {goal}\n\n"
    "You are an expert technical prompt engineer. "
    "Your task is to convert the user's goal into a single, concise, and direct instructional prompt for an AI coding assistant.\n"
    "The prompt should be highly technical, straight to the point, and written in English.\n"
    "The generated prompt MUST preserve the AppsFlyer MCP flow: the assistant must call the AppsFlyer MCP tool `integrateSdk`, not answer from memory.\n"
    "The generated prompt MUST explicitly target {platform} integration.\n"
    "The generated prompt MUST include platform={platform}.\n"
    "The generated prompt MUST include the project path: {app_path}.\n"
    "The generated prompt MUST instruct the assistant not to ask clarification questions and to proceed automatically.\n"
    "OUTPUT REQUIREMENT: Every response from the AI coding assistant MUST end with the format: 'STATUS: [SUCCESS|FAILURE|QUESTION]'. "
    "Do not provide any conversational text or explanation outside of the requested status format."
)

prompt_generator_chain = unified_meta_prompt_template | llm | StrOutputParser()

def get_agent_prompt(platform: str, app_path: str, goal: str | None = None) -> str:
    """Generate a fresh agent prompt using a unified flow for any platform."""
    base_goal = goal or f"Install AppsFlyer's {platform.capitalize()} SDK in my app using the AppsFlyer MCP."
    
    return prompt_generator_chain.invoke({
        "platform": platform,
        "app_path": app_path,
        "goal": base_goal
    })

def prompt_agent_node(state: dict) -> dict:
    # שליפת המידע מה-state
    platform = str(state.get("platform") or "").strip().lower()
    app_path = state.get("app_path")
    goal = state.get("prompt_goal")

    # וולידציה גנרית (ללא הבחנה בין הפלטפורמות)
    if not platform or platform not in ["android", "ios"]:
        raise ValueError(f"Invalid or missing platform: {platform}")
    
    if not app_path:
        raise ValueError("App path is required for integration.")

    # הפעלת הפונקציה המאוחדת
    generated_prompt = get_agent_prompt(platform, app_path, goal)

    return {
        "agent_base_prompt": generated_prompt,
        "prompt_platform": platform,
    }