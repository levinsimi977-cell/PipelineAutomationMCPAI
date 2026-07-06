"""
Generates dynamic agent prompts using LangChain + GPT.

Install (once):
  pip install langchain-openai langchain-core
"""
import os

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

# from .config import OPENAI_MODEL
model=os.getenv("OPENAI_MODEL", "gpt-4o-mini")

DEFAULT_GOAL = (
    "Install AppsFlyer's Android SDK in my app using the AppsFlyer MCP. "
    "The target platform is android and useResponseListener must be false."
)

llm = ChatOpenAI(
    model=OPENAI_MODEL,
    temperature=0.1,
    api_key=os.getenv("OPENAI_API_KEY") or os.getenv("GPT_API_KEY"),
)

meta_prompt_template = PromptTemplate.from_template(
   "User goal: {goal}\n\n"
    "You are an expert technical prompt engineer. "
    "Your task is to convert the user's goal into a single, concise, and direct instructional prompt for an AI coding assistant.\n"
    "The prompt should be highly technical, straight to the point, and written in English.\n"
    "The generated prompt MUST preserve the AppsFlyer MCP flow: the assistant must call the AppsFlyer MCP tool `integrateSdk`, not answer from memory.\n"
    "The generated prompt MUST explicitly target Android integration.\n"
    "The generated prompt MUST include platform=Android.\n"
    "The generated prompt MUST include the Android app project path when provided.\n"
    "The generated prompt MUST instruct the assistant not to ask clarification questions and to proceed automatically.\n"
    "Return ONLY the generated prompt, without any conversational text or quotes."
)
prompt_generator_chain = meta_prompt_template | llm | StrOutputParser()


def get_android_agent_prompt(app_path: str, goal: str | None = None) -> str:
    """Generate a fresh agent prompt for the given project path."""
    user_goal = goal or DEFAULT_GOAL

    if app_path:
        user_goal = f"{user_goal} Android project path: {app_path}"
    return prompt_generator_chain.invoke({"goal": user_goal})
