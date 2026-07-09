import os
import json
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

# הגדרת המודל - שימוש במשתני סביבה לאבטחה
llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL", "gpt-5.1"),
    temperature=0.1,
    api_key=os.getenv("OPENAI_API_KEY") or os.getenv("GPT_API_KEY"),
)

# תבנית מאוחדת ללא דרישת פלט STATUS - נשמר כפי שביקשת
unified_meta_prompt_template = PromptTemplate.from_template(
    "User goal: {goal}\n\n"
    "You are an expert technical prompt engineer. "
    "Create the final execution prompt for an AI SDK integration agent.\n"
    "The output must be written in English and must be direct, technical, and executable.\n\n"
    "Non-negotiable MCP requirements:\n"
    "- The agent MUST use the AppsFlyer MCP tool `integrateSdk` for SDK integration.\n"
    "- The agent MUST NOT answer from memory instead of calling MCP tools.\n"
    "- The agent MUST NOT invent AppsFlyer SDK setup steps that are not returned by MCP.\n"
    "- If the MCP tool is unavailable or fails, the agent must report failure instead of guessing.\n"
    "- The agent must target platform={platform}.\n"
    "- The app path is: {app_path}.\n\n"
    "Use case details:\n{goal}\n"
)

prompt_generator_chain = unified_meta_prompt_template | llm | StrOutputParser()

def prompt_agent_node(state: dict) -> dict:
    # 1. קבלת הנתיב לקובץ ה-Use Cases מה-state
    # ה-Pipeline מספק את המיקום הדינמי: data/runs/<run_id>/selected_use_cases.json
    selected_cases_path = state.get("selected_use_cases_path")
    app_path = state.get("app_path")
    
    if not selected_cases_path or not os.path.exists(selected_cases_path):
        raise FileNotFoundError(f"Configuration file not found at: {selected_cases_path}")

    # 2. קריאת הנתונים מהקובץ
    with open(selected_cases_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    # 3. חילוץ המידע מה-Use Case הראשון ברשימה
    first_case = data.get("useCases", [])[0]
    raw_goal = first_case.get("prompt") or first_case.get("prompt_goal") or ""
    platform = first_case.get("platform", "android")

    # 4. הפעלת ה-Chain עם המבנה המדויק שביקשת
    generated_prompt = prompt_generator_chain.invoke({
        "goal": raw_goal,
        "platform": platform,
        "app_path": app_path
    })

    # החזרת הנתונים ל-Pipeline
    return {
        "agent_base_prompt": generated_prompt,
        "prompt_platform": platform,
        "raw_goal": raw_goal
    }