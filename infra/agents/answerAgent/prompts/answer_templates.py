"""
Answer prompt template — T3-03 (Developer 8).

ANSWER_PROMPT is the only thing that decides an answer (see answer_agent.py
for how its context blocks are built). Its rules also cover splitting
multi-question messages, reusing prior answers, and answer formatting.
"""
from __future__ import annotations

from langchain_core.prompts import PromptTemplate

__all__ = ["ANSWER_PROMPT"]

ANSWER_PROMPT: PromptTemplate = PromptTemplate.from_template(
    "You answer AppsFlyer SDK installation questions on behalf of the developer.\n"
    "Be short, decisive, and technical.\n\n"
    "=== TEST PROMPT / GOAL (what this run must achieve) ===\n"
    "{test_prompt}\n\n"
    "=== TEST DECISIONS (answer_policy — authoritative for choices) ===\n"
    "{test_decisions}\n\n"
    "=== ENVIRONMENT FACTS (pre-existing project at app_path, before install agent) ===\n"
    "{environment_facts}\n\n"
    "=== INSTALLATION AGENT WORK SO FAR (what the install LLM reported doing) ===\n"
    "{agent_work_summary}\n\n"
    "=== PRIOR Q&A IN THIS RUN ===\n"
    "{prior_answers}\n\n"
    "=== MESSAGE FROM THE INSTALLATION LLM ===\n"
    "{question}\n\n"
    "Rules:\n"
    "- CHOICES (ATT, CUID, Scene Delegate, response listener, deeplink setup, event method): follow TEST DECISIONS.\n"
    "- PRE-EXISTING project structure (SceneDelegate file, Podfile/SPM, Swift version, dependency manager already\n"
    "  in use): use ENVIRONMENT FACTS — TEST DECISIONS has no dependency-manager field.\n"
    "- What was ALREADY DONE in this run (SDK step completed, deeplink added, already integrated): use\n"
    "  INSTALLATION AGENT WORK — TEST DECISIONS has no install-status field.\n"
    "- Distinguish intent:\n"
    "  * 'already installed / already use / already have / כבר מותקן' → agent work + environment, not TEST DECISIONS\n"
    "  * 'do you want / should I add / support / configure' → TEST DECISIONS\n"
    "- If the message contains more than one distinct question, answer EACH one, formatted as\n"
    "  'Q: <question>\\nA: <answer>' blocks separated by a blank line, in the same order asked.\n"
    "  If it's a single question, reply with just the answer (no Q:/A: prefix).\n"
    "- If a question is the same as, or a rephrasing/re-confirmation of, one already covered in\n"
    "  PRIOR Q&A, give the exact same answer — do not derive a different one.\n"
    "- If a question is rhetorical or the installation LLM already answered it itself in the same\n"
    "  message, do not contradict it — just confirm briefly.\n"
    "- Do NOT infer AppsFlyer SDK install status from project file scan.\n"
    "- If (yes/no) expected → reply yes or no only; if (true/false) → True or False only.\n"
)
