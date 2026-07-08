import os
import json
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

# הגדרת המודל - שימוש במשתני סביבה לאבטחה
llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    temperature=0.1,
    api_key=os.getenv("OPENAI_API_KEY") or os.getenv("GPT_API_KEY"),
)

# תבנית מאוחדת ללא דרישת פלט STATUS - נשמר כפי שביקשת
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
    raw_goal = first_case.get("prompt", "")
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