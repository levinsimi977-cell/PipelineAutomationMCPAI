# Configuration Guide — Pipeline Automation MCP

This document is a single reference for every configuration surface in this
repository: environment variables, dependency files, data/rules JSON, MCP
server setup, application/infra settings, in-code config objects, and CLI
entry points. It reflects the state of the code, not aspirations — known
gaps and inconsistencies are called out explicitly at the end of each
section.

## Table of contents

1. [Environment variables](#1-environment-variables)
2. [Dependency management](#2-dependency-management)
3. [Data & rules configuration (`data/`)](#3-data--rules-configuration-data)
4. [MCP server configuration](#4-mcp-server-configuration)
5. [Application / infra settings](#5-application--infra-settings)
6. [Config objects / dataclasses](#6-config-objects--dataclasses)
7. [CLI entry points](#7-cli-entry-points)
8. [`.gitignore` and local-only files](#8-gitignore-and-local-only-files)
9. [Known gaps to fix](#9-known-gaps-to-fix)

---

## 1. Environment variables

### Loaded from `.env`

`infra/agents/sdkAgent/tools/agent.py` loads a **local, gitignored** `.env`
file sitting next to it:

```python
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
```

Expected file: `infra/agents/sdkAgent/tools/.env` (not committed — create it
locally).

### Variables

| Variable | Default | Used in | Purpose |
|---|---|---|---|
| `APP_ID` | `"id1512793879"` | `infra/agents/sdkAgent/tools/agent.py:17` | AppsFlyer app ID forwarded to the MCP server subprocess. |
| `APP_ID` | — | `infra/application/app.py` | Forwarded into `check_mcp_alive()` for the health check. |
| `APPSFLYER_DEV_KEY` | — (required) | `infra/agents/sdkAgent/tools/agent.py` | AppsFlyer dev key; sent to MCP as `DEV_KEY`. |
| `APPSFLYER_DEV_KEY` / `DEV_KEY` | — | `infra/application/app.py` | Either name accepted as a fallback for the MCP health check. |
| `DEV_KEY` | — | `infra/application/mcp_environment.py` | Injected into the MCP subprocess environment during health checks. |
| `OPENAI_API_KEY` | — (required) | `infra/agents/sdkAgent/tools/agent.py` | OpenAI key used by the SDK integration agent (`ChatOpenAI`). |
| `OPENAI_API_KEY` / `GPT_API_KEY` | — | `infra/agents/promptGanertorAgent/tools/prompt_agent_core.py` | Either name accepted for the prompt-generator agent. |
| `OPENAI_MODEL` | `"gpt-5.4"` | `infra/agents/promptGanertorAgent/tools/prompt_agent_core.py`, `infra/agents/answerAgent/answer_agent.py`, `infra/agents/sdkAgent/tools/agent.py` | Preferred model for all project agents, with `MODEL_NAME` as a fallback. |
| `ANDROID_HOME` / `ANDROID_SDK_ROOT` | auto-detected | `infra/agents/sdkAgent/tools/emulator.py` | Android SDK location for `adb`/emulator/Appium. |
| `LOCALAPPDATA` (Windows only) | — | `infra/agents/sdkAgent/tools/emulator.py` | Used to build the default SDK path `%LOCALAPPDATA%\Android\Sdk`. |
| `PATH` | augmented | `infra/agents/sdkAgent/tools/emulator.py` | `platform-tools`, `emulator`, and `cmdline-tools/latest/bin` are prepended. |
| `GRADLE_USER_HOME` | `C:\Shared_CI_Cache\.gradle-user-home` | `infra/agents/compilationAgent/compilation_agent.py` | Shared Gradle distribution/dependency cache used for every `gradlew assembleDebug` run (see note below). |

If `ANDROID_HOME`/`ANDROID_SDK_ROOT` are unset, the SDK path is guessed by OS:

- Windows: `%LOCALAPPDATA%\Android\Sdk`
- macOS: `~/Library/Android/sdk`
- Linux: `~/Android/Sdk`

### Shared Gradle cache (`GRADLE_USER_HOME`)

Each pipeline run copies the app into a fresh `sandboxes/run_<id>/` folder.
Without a shared cache, every run would re-download the whole Gradle
distribution (~130MB) and all Maven dependencies from scratch. To avoid
that, `compilation_agent.py` points Gradle at one fixed, dedicated cache
directory shared by all runs — only the downloaded binaries/deps are
shared, not any project source file, so runs stay isolated.

- Default: `C:\Shared_CI_Cache\.gradle-user-home` (Windows-only path — this
  repo currently only runs the Android build on Windows).
- To use a different location (e.g. on macOS/Linux, or if that drive
  doesn't exist on your machine), set `GRADLE_USER_HOME` yourself — in your
  shell or in the project-root `.env` (loaded by `infra/load_env.py`):

  ```dotenv
  GRADLE_USER_HOME=D:\dev-cache\.gradle-user-home
  ```

- The directory is created automatically on first use
  (`Path(...).mkdir(parents=True, exist_ok=True)`) — just make sure the
  drive/path you choose is writable by whichever user runs the pipeline.

### Missing `config` module (used but not committed)

`infra/agents/answerAgent/answer_agent.py` does `import config` and reads:

| Attribute | Purpose |
|---|---|
| `config.APP_ID` | Shown to the Answer Agent LLM as context. |
| `config.DEV_KEY` | Shown to the Answer Agent LLM as context. |
| `config.GEMINI_MODEL` | Google Gemini model name. |
| `config.GEMINI_API_KEY` | Gemini API key; empty ⇒ Gemini-backed answering is disabled. |

**There is no `config.py` in the repository.** Tests work around this by
injecting a fake module (`tests/test_answer_question.py`). To run the Answer
Agent for real, create `config.py` (or refactor it to read from environment
variables) exposing these four attributes.

### Hardcoded model settings (not env-driven — noted for completeness)

| Setting | Value | File |
|---|---|---|
| SDK agent model | `gpt-5.1` | `infra/agents/sdkAgent/tools/agent.py` |
| SDK agent temperature | `1.5` | `infra/agents/sdkAgent/tools/agent.py` |
| Prompt generator temperature | `0.1` | `infra/agents/promptGanertorAgent/tools/prompt_agent_core.py` |
| Answer agent temperature | `0.1` | `infra/agents/answerAgent/answer_agent.py` |

---

## 2. Dependency management

### `pyproject.toml` (root)

```toml
[project]
name = "pipeline-automation-mcp"
version = "0.1.0"
requires-python = ">=3.10"

dependencies = [
    "Appium-Python-Client>=3.1",
    "python-dotenv>=1.2.2",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "black>=24.0", "ruff>=0.4"]

[tool.setuptools.packages.find]
where = ["."]
include = ["infra*", "data*"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100

[tool.black]
line-length = 100
```

No `[project.scripts]` entry points are defined.

### `requirements.txt` (root)

```
langchain-mcp-adapters>=0.3.0   # MCP client adapter, used by the health check
pytest>=8.0                      # test runner
```

### `infra/agents/userActions/_draft/requirements.txt`

```
Appium-Python-Client>=4.0.0
```

Scoped to the draft user-actions module only; not part of the main install.

### `uv.lock`

Lockfile for [`uv`](https://github.com/astral-sh/uv), pinning resolved
versions for the project and its `dev` extras. Currently only locks
`python-dotenv` plus the dev tools — it does **not** lock the undeclared
runtime packages listed below.

### ⚠️ Undeclared runtime dependencies

The code imports several packages that appear in **none** of the files
above. They must be installed manually (e.g. `pip install langchain
langchain-openai langchain-mcp-adapters langgraph langchain-google-genai
jinja2`) until `pyproject.toml`/`requirements.txt` are updated:

- `langchain`, `langchain-core`, `langchain-openai`
- `langgraph`
- `langchain-google-genai` (Answer Agent / Gemini)
- `jinja2` (HTML report rendering)

---

## 3. Data & rules configuration (`data/`)

```
data/
├── toolsList.json                     # allow-listed MCP/tool names
├── call_log.example.json              # example MCP call-log shape for validators
├── rules/
│   ├── sdk-generic-rules.json         # common pipeline rules (all platforms)
│   ├── sdk-ios-rules.json             # iOS-specific rules (extends generic)
│   └── sdk-android-rules.json         # Android-specific rules (extends generic)
├── useCases/
│   ├── useCase.catalog.json           # index of all available use cases
│   ├── useCase.json                   # composite/example use case
│   ├── common/*.json                  # platform-agnostic use cases
│   ├── ios/*.json                     # iOS-only use cases
│   └── android/*.json                 # Android-only use cases
└── application/
    ├── Podfile                        # placeholder (empty)
    └── Info.plist                     # placeholder (empty)
```

### `data/toolsList.json`

```json
{
  "version": "1.0.0",
  "tools": [
    "simulator.start", "app.install", "app.launch",
    "appium.tap", "appium.type", "appium.swipe", "appium.waitFor",
    "appium.getCurrentScreen", "deepLink.trigger",
    "mcp.listener.start", "mcp.listener.stop",
    "sdk.verifyIntegration", "audit.createRecord"
  ]
}
```

Referenced by the `rule-use-allowed-tools-only` rule in
`sdk-generic-rules.json` to restrict which tools agents may call.

### `data/rules/*.json`

Each file has `version`, `scope`, `rules[]` (each rule: `id`, `description`,
`severity`); platform files additionally set `extends` to pull in the
generic rules.

| File | Scope highlights |
|---|---|
| `sdk-generic-rules.json` | tool allow-list, no hardcoded secrets, MCP-listener ordering, audit-record required |
| `sdk-ios-rules.json` | universal links, permission flow |
| `sdk-android-rules.json` | app links, intent handler |

### `data/useCases/useCase.catalog.json`

```json
{
  "version": "...",
  "selectionStrategy": {
    "alwaysIncludePlatform": "common",
    "alsoIncludeSelectedPlatform": true
  },
  "useCases": [ { "id": "...", "platform": "...", "path": "...", "type": "...", "enabled": true } ]
}
```

Drives which use-case JSON files get pulled into a given pipeline run.

### Individual use-case JSON files

Common schema across `useCase.json`, `useCases/common/*.json`,
`useCases/ios/*.json`, `useCases/android/*.json`:

| Key | Purpose |
|---|---|
| `app_path` | Path to the target application/project. |
| `platform` | `"ios"`, `"android"`, or `"common"`. |
| `prompt_goal` | Goal text fed into the prompt-generator agent. |
| `answer_policy` | Authoritative answers used by the Answer Agent (see sub-blocks below). |
| `installation_answers` | Pre-seeded Q&A pairs for the install flow. |
| `agent_messages` | Simulated agent conversation transcript. |
| `installation_agent_summary` | Free-text summary of the install agent's work. |

`answer_policy` sub-blocks:

- `ios_minimal`: `use_att`, `use_cuid`, `use_scene_delegate`, `use_response_listener`
- `deeplink`: `use_deep_linking`, `onelink_url`, `url_identifier`, `uri_scheme`, `use_custom_uri_scheme`
- `in_app_event`: `inapp_event_method`, `event_name`, `event_params`
- `verify_sdk`: `verify_logs_ready`, `app_launched`
- `android`: `device_id`, `has_sha256`, `sha256_fingerprint` (or `null`)

### `data/call_log.example.json`

Documents the expected MCP tool-call sequence format used by validators:
`call_log_android_example`, `call_log_ios_example`, `notes`. iOS
`verifyIosSdk` calls require an `action` of `"prepare"` or `"verify"`.

### `data/application/`

`Podfile` and `Info.plist` are empty placeholders. The sample app folders
referenced in code (`appsflyer-onelink-android-sample-apps`,
`appsflyer-onelink-ios-sample-apps`) are **not committed** and must be
supplied locally.

---

## 4. MCP server configuration

There is **no `mcp.json`** — the AppsFlyer SDK MCP server is configured
entirely in Python, in two places:

### Health check (`infra/application/mcp_environment.py`)

```python
DEFAULT_MCP_COMMAND = "npx"
DEFAULT_MCP_ARGS = ["-y", "@appsflyer/sdk-mcp-server"]

client = MultiServerMCPClient({
    "appsflyer-sdk-mcp": {
        "transport": "stdio",
        "command": self.command,
        "args": self.args,
        "env": merged_env,   # os.environ + overrides (APP_ID, DEV_KEY)
    }
})
```

- Timeout: 60 seconds (tool discovery).
- `MCPEnvironmentChecker(workdir, command=..., args=..., env=...)` lets
  callers override the command/args/env per invocation.
- `check_mcp_alive(workdir, app_id, dev_key)` is the convenience wrapper
  that injects `APP_ID`/`DEV_KEY` into the subprocess environment.

### SDK integration agent (`infra/agents/sdkAgent/tools/agent.py`)

```python
mcp_client = MultiServerMCPClient({
    "appsflyer-sdk-mcp": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@appsflyer/sdk-mcp-server"],
        "env": {"APP_ID": APP_ID, "DEV_KEY": dev_key},
    }
})
```

### MCP call-log tracking (`infra/listener/llm_listener.py`)

- `MAX_QUESTION_ROUNDS = 10` — cap on Q&A rounds with the Answer Agent.
- `call_log` / `mcp_sequence` (`{platform, call_log}`) are accumulated in
  pipeline state and checked against `data/call_log.example.json`-style
  expectations.
- `_SDK_FILE_TOOLS` lists file-related tool names excluded from the MCP
  call sequence.

---

## 5. Application / infra settings

### `infra/application/app.py`

| Setting | Value | Purpose |
|---|---|---|
| Sandbox root | `sandboxes/run_{timestamp}_{uuid}/` | Isolated working copy per run. |
| Ignored dirs when copying | `build`, `.gradle`, `.git`, `__pycache__`, `.venv`, `.idea`, `local.properties`, `.DS_Store`, `*.iml` | Kept out of the sandbox copy. |
| App base dir | `data/application` | Root for sample apps. |
| Android sample folder | `appsflyer-onelink-android-sample-apps` | Under app base dir. |
| iOS sample folder | `appsflyer-onelink-ios-sample-apps` | Under app base dir. |
| MCP env vars used | `APP_ID`, `APPSFLYER_DEV_KEY` (or `DEV_KEY`) | Forwarded to the MCP health check. |

CLI flags: see [§7](#7-cli-entry-points).

### `infra/application/mcp_environment.py`

Covered above in [§4](#4-mcp-server-configuration).

### `infra/application/app_validator.py`

| Setting | Value | Purpose |
|---|---|---|
| `IGNORED_DIRS` | `.git`, `build`, `DerivedData`, `node_modules`, `Pods` | Skipped when scanning the app project. |
| `run_build_check` | default `False` | Opt-in `xcodebuild`/`gradle` sanity check. |
| Build command timeout | 120s | `_run_command()` default. |
| Platform detection | `Podfile`/`*.xcodeproj` → iOS; `settings.gradle` → Android | `detect_platform()`. |

### `infra/agents/sdkAgent/tools/emulator.py`

| Setting | Value | Purpose |
|---|---|---|
| Appium port | `4723` (hardcoded) | `start_appium_server()`. |
| Appium URL default | `http://127.0.0.1:4723` | Status/health checks. |
| Android emulator boot wait | 15s | After launching the emulator. |
| iOS simulator boot wait | 10s | After launching the simulator. |
| Appium startup wait | 5s | After spawning the Appium process. |
| Driver selection | `XCUITest` on macOS, `UiAutomator2` otherwise | `setup_appium_environment()`. |

---

## 6. Config objects / dataclasses

No pydantic `BaseSettings` are used — configuration is plain dataclasses,
`TypedDict`s, and dicts.

| Type | File | Fields |
|---|---|---|
| `MCPHealthResult` | `infra/application/mcp_environment.py` | `status`, `alive`, `workdir`, `command`, `args`, `tools`, `error`, `details` |
| `MCPEnvironmentChecker` | `infra/application/mcp_environment.py` | `workdir` (required), `command="npx"`, `args=["-y", "@appsflyer/sdk-mcp-server"]`, `env={}` |
| `ApplicationValidationResult` | `infra/application/app_validator.py` | `status`, `app_path`, `platform`, `markers`, `key_files`, `build_check`, `error` |
| `PipelineState` (`TypedDict`) | `infra/workflow/workflow_nodes.py` | `visited_user_actions: bool`, `last_prompt_type: PromptType` |
| `NormalizedAuditEvent` | `infra/reports/reporter.py` | `index`, `timestamp`, `phase`, `source`, `event`, `status`, `details` |
| `DiscoveredEvent` | `infra/agents/userActions/_draft/discover_events.py` | `event_name`, `trigger_id`, `layout_file`, `view_id`, `source` |
| `ReportGenerator` | `infra/reports/reporter.py` | `templates_dir` (defaults to `infra/reports/templates/`) |
| `AuditRecorder` | `infra/agents/AuditRecorder.py` | `run_dir: Path` → writes `run_dir/audit.jsonl` |

### Common pipeline-state keys (runtime contract, not a formal class)

| Key | Consumers |
|---|---|
| `platform` | app setup, prompt agent, listener, answer agent |
| `app_path` | prompt agent, answer agent |
| `selected_use_cases_path` | prompt agent → `data/runs/<run_id>/selected_use_cases.json` |
| `prompt_goal`, `answer_policy`, `installation_answers`, `agent_messages` | use-case JSON ↔ answer agent |
| `last_prompt_type`, `visited_user_actions` | workflow routing (`workflow_builder.py`) |
| `call_log`, `mcp_sequence` | MCP call-sequence validation |
| `run_id`, `use_case_id`, `status`, `started_at`, `ended_at` | report generator |
| `incoming_question`, `question_rounds`, `nodes_log` | answer/listener nodes |

---

## 7. CLI entry points

### `python -m infra.application.app`

| Flag | Default | Purpose |
|---|---|---|
| `--app-path` | required | Path to the application/project to validate & test. |
| `--workdir` | `"."` | Working directory for the MCP health check. |
| `--run-build-check` | off | Also run `xcodebuild`/`gradle` sanity checks. |

### `infra/agents/userActions/_draft/discover_events.py`

| Flag | Default | Purpose |
|---|---|---|
| `--platform` | required | `android` or `ios`. |
| `--audit` | required | Path to an `AuditRecord` JSON. |
| `--output` | `events.discovered.json` | Where to write discovered events. |

### `infra/agents/userActions/_draft/appium_runner.py`

| Flag | Default | Purpose |
|---|---|---|
| `--platform` | required | `android` or `ios`. |
| `--config` | `events.discovered.json` | Discovered-events config to replay. |
| `--event` | none | Run a single named event instead of all. |
| `--appium-url` | `http://127.0.0.1:4723` | Appium server URL. |
| `--wait` | `2.0` | Seconds to wait between simulated taps. |

### Programmatic entry points (no CLI wrapper)

| Function | File | Key parameters |
|---|---|---|
| `run_sdk_integration_agent()` | `infra/agents/sdkAgent/tools/agent.py` | `project_root_str`, `platform`, `user_prompt`, `audit_recorder`, `run_id`, `max_turns=15` |
| `check_mcp_alive()` | `infra/application/mcp_environment.py` | `workdir`, `app_id`, `dev_key` |
| `setup_environment()` | `infra/application/app.py` | `state: dict` (reads `state["platform"]`) |
| `build_workflow()` / `workflow_app` | `infra/workflow/workflow_builder.py` | Compiled LangGraph workflow, no params |
| `launch_app_on_device()` | `infra/agents/sdkAgent/tools/emulator.py` | `os_type`, `device_id`, `app_identifier`, `remote_url` |

No `click`/`typer` usage — all CLIs use `argparse`.

---

## 8. `.gitignore` and local-only files

```gitignore
.env
sandboxes/
.env
```

- `.env` files (e.g. `infra/agents/sdkAgent/tools/.env`) are local-only —
  each developer creates their own with `OPENAI_API_KEY`,
  `APPSFLYER_DEV_KEY`, and optionally `APP_ID`.
- `sandboxes/` holds ephemeral per-run working copies created by
  `infra/application/app.py` and is never committed.
- `data/runs/<run_id>/` (selected use cases per run) and sample app
  folders under `data/application/` are referenced by code but not present
  in the repo — supply them locally as needed.

### Example local `.env` (create at `infra/agents/sdkAgent/tools/.env`)

```dotenv
OPENAI_API_KEY=sk-...
APPSFLYER_DEV_KEY=...
APP_ID=id1512793879
```

---

## 9. Known gaps to fix

These are inconsistencies found while auditing the configuration surface —
listed here so they aren't lost, not because they're expected behavior:

1. **Missing `config` module** — `infra/agents/answerAgent/answer_agent.py`
   imports a top-level `config` module (`APP_ID`, `DEV_KEY`,
   `GEMINI_MODEL`, `GEMINI_API_KEY`) that doesn't exist in the repo; only a
   test fake stands in for it today.
2. **Bad import path** — `infra/agents/sdkAgent/tools/agent.py` imports
   from `infra.agents.Listeners.llm_listener`, but the real module lives at
   `infra/listener/llm_listener.py`.
3. **Undeclared dependencies** — `langchain`, `langchain-core`,
   `langchain-openai`, `langgraph`, `langchain-google-genai`, and `jinja2`
   are imported but absent from `pyproject.toml`/`requirements.txt`.
4. **Missing sample apps** — `data/application/appsflyer-onelink-android-sample-apps`
   and `.../appsflyer-onelink-ios-sample-apps` are referenced but not
   committed; `Podfile`/`Info.plist` placeholders are empty.
5. **Missing schema** — the draft README references
   `infra/user_interface_use_case/reports/audit-record.schema.json`, which
   isn't in the repo.
6. **`workflow_nodes.py` are stubs** — most nodes referenced by
   `infra/workflow/workflow_builder.py` currently just pass state through
   unchanged; treat the graph shape as the source of truth, not the node
   bodies, until they're implemented.
