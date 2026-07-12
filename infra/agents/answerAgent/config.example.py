"""
Template for the local `config.py` that answer_agent.py imports.

config.py itself is intentionally NOT committed to git (see .gitignore) --
it holds real credentials and an LLM API key. To run answerAgent locally:

    1. Copy this file to `config.py` in this same folder.
    2. Fill in real values below.
    3. Never commit the resulting config.py.

This file only documents the contract answer_agent.py expects from
`import config` -- it does not implement any of that agent's logic.
"""

# AppsFlyer test credentials used as a fallback when a question's answer
# needs them and they weren't already present in the run's state/policy.
# See infra/user_interface_use_case/schemas/use_case.py (UseCaseContract)
# for what these correspond to on the use-case side.
APP_ID = ""
DEV_KEY = ""

# Google Gemini model name and API key, used by answer_agent.py's LLM path
# (infra/agents/answerAgent/answer_agent.py -> ChatGoogleGenerativeAI).
# Leave GEMINI_API_KEY empty to disable the LLM path entirely -- the agent
# falls back to deterministic/regex answering when it's unset (see the
# module docstring in answer_agent.py).
GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_API_KEY = ""
