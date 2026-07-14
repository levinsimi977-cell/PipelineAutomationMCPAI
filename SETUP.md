# Local setup: secrets & gitignored files

This project's `.gitignore` deliberately excludes a few things that contain
real credentials or an API key. Nothing in git ever has real secrets in it —
which means a fresh clone is missing files it actually needs to run. This
doc lists every one of them: what it's for, whether you need to create it
yourself, and where to get the values.

## 1. `infra/agents/answerAgent/config.py` — you must create this

`answer_agent.py` (owned by the answerAgent team) does `import config` and
expects it to define:

| Name | Type | What it's for |
|---|---|---|
| `APP_ID` | `str` | Fallback AppsFlyer App ID used when a question needs it and it isn't already present in the run's state/policy |
| `DEV_KEY` | `str` | Fallback AppsFlyer Dev Key, same situation as above |
| `GEMINI_MODEL` | `str` | Gemini model name for the agent's LLM-backed answering path, e.g. `"gemini-1.5-flash"` |
| `GEMINI_API_KEY` | `str` | Google Gemini API key. Leave empty to disable the LLM path — the agent falls back to deterministic/regex answering when this is unset |

**To set it up:**

```bash
cp infra/agents/answerAgent/config.example.py infra/agents/answerAgent/config.py
# then edit config.py and fill in real values
```

`config.example.py` *is* committed (it has no real values in it) so you can
always see the current contract without needing someone to hand you a copy.
`config.py` itself is gitignored — if `git status` ever shows it as a change
you're about to commit, stop and double-check you're not about to leak a key.

## 2. `data/runs/` — nothing to do, this one creates itself

This holds the use-case selections saved from the UI's "Save selection for
this run" button (see `infra/user_interface_use_case/repositories/run_repository.py`).
Each selected use case is written as its own file directly inside `data/runs/`
(named `{session_id}__{use_case_id}.json`), so pulling everything chosen for
a run is just "grab every file in this folder" — no per-session subfolders
or aggregate manifest to parse. It's gitignored because each saved file
embeds that run's resolved AppsFlyer credentials. You don't need to create
this folder or put anything in it — it's created automatically the first
time anyone saves a selection, and files are meant to be deleted again once
a run's result has been reported (see that module's docstring for the full
lifecycle). If it's empty or missing on a fresh clone, that's expected, not
a bug.

## 3. Known gap (not yet fixed): `data/useCases/custom/`

Unlike `data/runs/`, this folder is **not** gitignored, but a custom use
case created through the UI with credentials filled in *does* get its
`app_id`/`dev_key` written into that JSON file (base64-obfuscated only, not
encrypted — see `infra/user_interface_use_case/utils/secrets.py`). This was
flagged in a code review as a real risk: if you create a custom use case
with real credentials, don't commit the resulting file under
`data/useCases/custom/` unless you're sure it's meant to be shared. This
doc will be updated if/when that gets locked down properly (e.g. by
gitignoring that folder too, or by never persisting credentials into custom
use case files at all).

---

(`.gitignore` also excludes `__pycache__/` / `*.pyc` — those are just
compiled bytecode, not secrets, and need no setup at all.)
