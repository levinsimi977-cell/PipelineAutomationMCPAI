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

# OpenAI model / API key for answer_agent.py's LLM path
# (infra/agents/answerAgent/answer_agent.py -> ChatOpenAI).
# Prefer setting OPENAI_API_KEY or GPT_API_KEY in the project .env —
# answer_agent reads those via os.getenv (load_project_env).
# Leave empty to disable the LLM path (UnansweredQuestionError).
OPENAI_MODEL = "gpt-5.1"
OPENAI_API_KEY = ""
