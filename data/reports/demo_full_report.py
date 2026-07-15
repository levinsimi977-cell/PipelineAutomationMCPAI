"""
Generate a fully populated demo report with realistic AppsFlyer pipeline data.

Usage:
    python -m data.reports.demo_full_report          # successful run
    python -m data.reports.demo_failed_report      # failed run
"""

from __future__ import annotations

from data.reports.build_report import generate_run_report

RUN_ID = "20260714_full_demo_ios_deeplink"
REPORT_PATH = f"data/reports/2026-07-14/{RUN_ID}/report.html"

PIPELINE_NODES = [
    "json_use_case_input",
    "artifact_generator",
    "environment_setup",
    "prompt_agent",
    "sdk_agent",
    "compilation_check",
    "user_actions",
    "emulator",
    "deep_link",
    "test_runner",
    "visual_report",
]

STATE: dict = {
    # --- Use cases ---
    "user_id": "user_demo_42",
    "use_case_ids": ["common-first-open-sdk-presence", "common-deeplink-smoke", "ios-deeplink-validation"],
    "use_case_count": 3,
    "primary_use_case_id": "common-deeplink-smoke",
    "primary_use_case_name": "Deep Link Smoke Test",
    "selected_use_cases_path": f"data/runs/{RUN_ID}/use_cases/common-deeplink-smoke.json",
    "run_id": RUN_ID,
    "platform": "ios",
    "use_case_id": "common-deeplink-smoke",
    "started_at": "2026-07-14T08:55:12",
    "ended_at": "2026-07-14T09:22:47",
    "test_status": "READY",
    "answer_policy": {
        "ios_minimal": {"use_att": True, "use_cuid": True, "use_scene_delegate": True},
        "deeplink": {"use_deep_linking": True, "onelink_url": "https://banana.onelink.me/xyz9/offers"},
        "in_app_event": {"event_name": "af_purchase"},
    },
    # --- Run & app ---
    "app_id": "com.appsflyer.onelink.basicapp",
    "app_status": "READY",
    "dev_key_configured": True,
    "dev_key_source": "environment variable APPSFLYER_DEV_KEY",
    # --- Sandbox & infrastructure ---
    "remote_url": "http://127.0.0.1:4723",
    "available_devices": "iPhone 15 Pro (iOS 17.4) — booted",
    "agent_model": "gpt-4.1",
    "app_path": "/tmp/pipeline-sandbox/20260714_ios_banana_clone",
    "original_app_path": "data/application/banana.app",
    "sandbox_path": "/tmp/pipeline-sandbox/20260714_ios_banana_clone",
    "device_id": "iPhone-15-Pro-Simulator",
    "execution_result": (
        "[appium] Server started on http://127.0.0.1:4723\n"
        "[device] iPhone 15 Pro Simulator booted\n"
        "[launch] App com.appsflyer.onelink.basicapp launched successfully"
    ),
    "environment_setup_status": "OK",
    "environment_setup_result": {"status": "OK", "task_3_mcp_alive": True},
    "task_4_application_validation": "valid ios application bundle",
    # --- MCP protocol ---
    "mcp_health_check": True,
    "task_3_mcp_alive": True,
    "mcp_tools_available": [
        "integrateSdk", "verifyIosSdk", "getTopInAppEvents", "createIosInAppEvent",
        "verifyIosInAppEvent", "createIosDeepLink", "verifyIosDeepLink",
    ],
    "mcp_tools_call": [
        "integrateSdk", "verifyIosSdk", "getTopInAppEvents", "createIosInAppEvent",
        "verifyIosInAppEvent", "createIosDeepLink", "verifyIosDeepLink",
    ],
    "mcp_tools_used": [
        "integrateSdk", "verifyIosSdk", "getTopInAppEvents", "createIosInAppEvent",
        "verifyIosInAppEvent", "createIosDeepLink", "verifyIosDeepLink",
    ],
    "mcp_tools_used_success": True,
    "mcp_integration_text": (
        "SDK integrated successfully. Launch logs confirm initialization. "
        "In-app event af_purchase verified. Deep link banana://offers opened target screen."
    ),
    # --- Agent orchestration ---
    "type_agent": "sdk_agent",
    "agent_id": "sdk_agent_session_a1b2c3",
    "question_rounds": 1,
    "fail_reason": None,
    "last_prompt_type": "verify_prompt",
    "prompt_just_run": "verify_prompt",
    "incoming_question": None,
    "last_agent_message": "Verify SDK integration and in-app event wiring for ios banana sample app.",
    "prompt_agent_node_status": "SUCCESS",
    # --- SDK / answer agents ---
    "prompt_agent_sdk": (
        "Integrate AppsFlyer SDK v6.14+ into the iOS Swift basic_app project under sandbox."
    ),
    "prompt_agent_answer": (
        "Answer installation policy questions using answer_policy from the active use case."
    ),
    "audit_path": f"data/runs/{RUN_ID}/audit.jsonl",
    # --- Post installation ---
    "emulator_checking": True,
    # --- Test results ---
    "is_tool_order_valid": True,
    "is_tool_order_valid_message": (
        "Sequence is valid.\n"
        "All required integration, in-app, and deep-link tools were invoked in the correct order for ios."
    ),
    "is_verify_deep_link": True,
    "is_verify_deep_link_message": "Deep link banana://offers opened OffersViewController with attribution params.",
    "files_modified": True,
    "applied_files": [
        "basic_app/Podfile",
        "basic_app/AppDelegate.swift",
        "basic_app/SceneDelegate.swift",
        "basic_app/BananasViewController.swift",
    ],
    "deep_link_status": "PASSED",
    # --- Compilation ---
    "compilation_passed": True,
    "compilation_result": {
        "status": "passed",
        "platform": "ios",
        "build_tool": "xcodebuild",
        "scheme": "basic_app",
        "configuration": "Debug",
        "success": True,
    },
    "audit_events": [
        {"event_type": "COMPILATION_CHECK", "status": "SUCCESS", "scheme": "basic_app"},
    ],
    # --- Final ---
    "report_path": REPORT_PATH,
    "sdk_verified": True,
    "current_node": "visual_report",
    "next_node": "end",
    "visited_compilation_check": True,
    "visited_user_actions": True,
    "use_cases_dir": f"data/runs/{RUN_ID}/use_cases",
    "current_use_case_path": None,
    "selected_use_cases": [
        {"id": "common-first-open-sdk-presence", "platform": "common"},
        {"id": "common-deeplink-smoke", "platform": "common"},
        {"id": "ios-deeplink-validation", "platform": "ios"},
    ],
    "current_use_case": {
        "id": "common-deeplink-smoke",
        "useCaseId": "common-deeplink-smoke",
        "platform": "ios",
        "app_path": "data/application/banana.app",
        "prompt_goal": "Integrate AppsFlyer SDK, wire in-app purchase event, and validate deep link handling.",
        "answer_policy": {
            "ios_minimal": {
                "use_att": True,
                "use_cuid": True,
                "use_scene_delegate": True,
                "use_response_listener": True,
            },
            "deeplink": {
                "use_deep_linking": True,
                "onelink_url": "https://banana.onelink.me/xyz9/offers",
                "url_identifier": "offers",
                "uri_scheme": "banana",
                "use_custom_uri_scheme": True,
            },
            "in_app_event": {
                "inapp_event_method": "button_tap",
                "event_name": "af_purchase",
                "event_params": {"product_id": "banana_bundle", "price": 4.99},
            },
            "verify_sdk": {"verify_logs_ready": True, "app_launched": True},
        },
    },
    "agent_prompts": {
        "integrate_prompt": (
            "Integrate AppsFlyer SDK v6.14+ into the iOS Swift basic_app project under sandbox. "
            "Configure dev key, app ID, ATT, SceneDelegate, and customer user ID."
        ),
        "event_prompt": (
            "Wire in-app event af_purchase to the Buy button on BananasViewController. "
            "Use getTopInAppEvents to pick a recommended event if needed."
        ),
        "verify_prompt": (
            "Verify SDK initialization via launch logs, confirm af_purchase in in-app logs, "
            "and validate OneLink deep link opens the offers screen."
        ),
    },
    "installation_answers": [
        {
            "round": 1,
            "question": "Does the app use SceneDelegate for lifecycle?",
            "answer": "Yes — SceneDelegate.swift handles window and deep link routing.",
        },
    ],
    "call_log": [
        {"tool": "integrateSdk"},
        {"tool": "verifyIosSdk", "action": "prepare"},
        {"tool": "verifyIosSdk", "action": "verify"},
        {"tool": "getTopInAppEvents"},
        {"tool": "createIosInAppEvent"},
        {"tool": "verifyIosInAppEvent", "action": "prepare"},
        {"tool": "verifyIosInAppEvent", "action": "verify"},
        {"tool": "createIosDeepLink"},
        {"tool": "verifyIosDeepLink", "action": "prepare"},
        {"tool": "verifyIosDeepLink", "action": "verify"},
    ],
    "mcp_sequence": {
        "platform": "ios",
        "call_log": [
            {"tool": "integrateSdk"},
            {"tool": "verifyIosSdk", "action": "prepare"},
            {"tool": "verifyIosSdk", "action": "verify"},
            {"tool": "getTopInAppEvents"},
            {"tool": "createIosInAppEvent"},
            {"tool": "verifyIosInAppEvent", "action": "prepare"},
            {"tool": "verifyIosInAppEvent", "action": "verify"},
            {"tool": "createIosDeepLink"},
            {"tool": "verifyIosDeepLink", "action": "prepare"},
            {"tool": "verifyIosDeepLink", "action": "verify"},
        ],
    },
    "nodes_log": [
        {
            "node": "json_use_case_input",
            "status": "SUCCESS",
            "message": "Materialized 3 use case JSON files under data/runs/{run_id}/use_cases/".format(run_id=RUN_ID),
        },
        {
            "node": "artifact_generator",
            "status": "SUCCESS",
            "message": "Loaded use case common-deeplink-smoke; registered answer_policy for run.",
        },
        {
            "node": "environment_setup",
            "status": "SUCCESS",
            "message": "Sandbox cloned to /tmp/pipeline-sandbox/20260714_ios_banana_clone; MCP server alive; app validated.",
        },
        {
            "node": "prompt_agent",
            "status": "SUCCESS",
            "message": "Generated integrate_prompt, event_prompt, and verify_prompt.",
        },
        {
            "node": "sdk_agent",
            "status": "Success",
            "prompt_type": "integrate_prompt",
            "message": "SDK integration pass completed — Podfile updated, AppDelegate wired.",
        },
        {
            "node": "sdk_agent",
            "status": "Success",
            "prompt_type": "event_prompt",
            "message": "In-app event af_purchase wired to Buy button.",
        },
        {
            "node": "sdk_agent",
            "status": "Success",
            "prompt_type": "verify_prompt",
            "message": "Verify pass completed — launch logs, in-app logs, and deep link validated.",
        },
        {
            "node": "compilation_check",
            "status": "SUCCESS",
            "message": "xcodebuild BUILD SUCCEEDED for basic_app scheme (Debug, iphonesimulator).",
        },
        {
            "node": "user_actions",
            "status": "SUCCESS",
            "message": "Discovered 2 in-app events from audit; validation passed.",
        },
        {
            "node": "emulator",
            "status": "SUCCESS",
            "message": "Appium server started; iPhone 15 Pro booted; app launched.",
        },
        {
            "node": "deep_link",
            "status": "SUCCESS",
            "message": "Simulated OneLink click banana://offers — destination screen matched.",
        },
        {
            "node": "test_runner",
            "status": "SUCCESS",
            "message": "Smoke test suite passed (4/4 assertions).",
        },
        {
            "node": "visual_report",
            "status": "SUCCESS",
            "message": "HTML run report generated under data/reports/2026-07-14/{run_id}/report.html".format(run_id=RUN_ID),
        },
    ],
    "nodes_logs": [
        {
            "node": "sdk_agent",
            "listener": "SUCCESS",
            "status": "INFO",
            "message": "Classifier reported SUCCESS; continuing node operation.",
            "text_preview": "I have integrated the AppsFlyer SDK. Running verifyIosSdk next.",
        },
        {
            "node": "sdk_agent",
            "listener": "QUESTION",
            "status": "SUCCESS",
            "message": "Answered question (round 1).",
            "question_preview": "Does the app use SceneDelegate for lifecycle?",
            "answer": "Yes — SceneDelegate.swift handles window and deep link routing.",
        },
        {
            "node": "sdk_agent",
            "listener": "SUCCESS",
            "status": "INFO",
            "message": "Classifier reported SUCCESS after event wiring.",
            "text_preview": "af_purchase event created on BananasViewController buyButton.",
        },
        {
            "node": "answer_question",
            "status": "SUCCESS",
            "message": "Answer policy resolved SceneDelegate question.",
        },
    ],
}

_NODE_LOGS = {
    "json_use_case_input": {"status": "Success", "message": "Materialized 3 use case JSON files."},
    "artifact_generator": {"status": "Success", "message": "Loaded use case and registered answer_policy."},
    "environment_setup": {"status": "Success", "message": "Sandbox cloned; MCP alive; app validated."},
    "prompt_agent": {"status": "Success", "message": "Generated integrate/event/verify prompts."},
    "sdk_agent": [
        {"status": "Success", "prompt_type": "integrate_prompt", "message": "SDK integration completed."},
        {"status": "Success", "prompt_type": "event_prompt", "message": "In-app event af_purchase wired."},
        {"status": "Success", "prompt_type": "verify_prompt", "message": "Verify pass completed."},
    ],
    "compilation_check": {"status": "Success", "message": "xcodebuild BUILD SUCCEEDED."},
    "user_actions": {"status": "Success", "message": "User action simulation passed."},
    "emulator": {"status": "Success", "message": "Appium started; app launched."},
    "deep_link": {"status": "Success", "message": "Deep link destination matched."},
    "test_runner": {"status": "Success", "message": "Smoke test suite passed (4/4)."},
    "visual_report": {"status": "Success", "message": f"Report saved to {REPORT_PATH}."},
}

for _node in PIPELINE_NODES:
    STATE[f"{_node}_is_visited"] = True
    STATE[f"{_node}_log"] = _NODE_LOGS[_node]

AUDIT_EVENTS: list[dict] = [
    {"timestamp": "2026-07-14T09:00:01.120", "event_type": "TOOLS_DISCOVERED", "payload": {
        "tools": ["integrateSdk", "verifyIosSdk", "getTopInAppEvents", "createIosInAppEvent",
                  "verifyIosInAppEvent", "createIosDeepLink", "verifyIosDeepLink", "guideDeepLinkTesting"],
    }},
    {"timestamp": "2026-07-14T09:00:02.340", "event_type": "AGENT_PROMPT_GENERATED", "payload": {
        "prompt": STATE["agent_prompts"]["integrate_prompt"][:500],
    }},
    {"timestamp": "2026-07-14T09:01:15.881", "event_type": "AGENT_DECISION", "payload": {
        "tool": "integrateSdk",
        "args": {"platform": "ios", "devKey": "***REDACTED***", "appId": "id1234567890", "waitForATT": 60},
    }},
    {"timestamp": "2026-07-14T09:01:22.104", "event_type": "MCP_TOOL_RESULT", "payload": {
        "tool": "integrateSdk",
        "result": (
            "SDK integrated successfully.\n"
            "Updated Podfile with AppsFlyerFramework 6.14.3.\n"
            "Modified AppDelegate.swift and SceneDelegate.swift."
        ),
    }},
    {"timestamp": "2026-07-14T09:01:22.110", "event_type": "MCP_CALL_LOG", "payload": {"tool": "integrateSdk"}},
    {"timestamp": "2026-07-14T09:01:23.005", "event_type": "PROJECT_FILE_WRITTEN", "payload": {
        "path": "basic_app/AppDelegate.swift", "action": "update",
    }},
    {"timestamp": "2026-07-14T09:02:40.552", "event_type": "AGENT_DECISION", "payload": {
        "tool": "verifyIosSdk", "args": {"action": "prepare", "simulator": "iPhone 15 Pro"},
    }},
    {"timestamp": "2026-07-14T09:02:55.318", "event_type": "MCP_TOOL_RESULT", "payload": {
        "tool": "verifyIosSdk",
        "result": "Prepare step completed. Simulator booted and app installed.",
    }},
    {"timestamp": "2026-07-14T09:02:55.320", "event_type": "MCP_CALL_LOG", "payload": {"tool": "verifyIosSdk", "action": "prepare"}},
    {"timestamp": "2026-07-14T09:03:10.774", "event_type": "AGENT_DECISION", "payload": {
        "tool": "verifyIosSdk", "args": {"action": "verify"},
    }},
    {"timestamp": "2026-07-14T09:03:18.901", "event_type": "MCP_TOOL_RESULT", "payload": {
        "tool": "verifyIosSdk",
        "result": (
            "Launch logs contain: [AppsFlyer] SDK initialized.\n"
            "Conversion data received. First open event detected."
        ),
    }},
    {"timestamp": "2026-07-14T09:03:18.905", "event_type": "MCP_CALL_LOG", "payload": {"tool": "verifyIosSdk", "action": "verify"}},
    {"timestamp": "2026-07-14T09:04:02.110", "event_type": "LISTENER_DECISION", "payload": {
        "node": "sdk_agent", "listener": "QUESTION", "status": "SUCCESS",
        "question_preview": "Does the app use SceneDelegate for lifecycle?",
        "answer": "Yes — SceneDelegate.swift handles window and deep link routing.",
    }},
    {"timestamp": "2026-07-14T09:04:02.115", "event_type": "SIMULATED_USER_REPLY", "payload": {
        "question": "Does the app use SceneDelegate for lifecycle?",
        "answer": "Yes — SceneDelegate.swift handles window and deep link routing.",
    }},
    {"timestamp": "2026-07-14T09:04:02.120", "event_type": "INSTALLATION_ANSWER", "payload": {
        "round": 1,
        "question": "Does the app use SceneDelegate for lifecycle?",
        "answer": "Yes — SceneDelegate.swift handles window and deep link routing.",
    }},
    {"timestamp": "2026-07-14T09:04:02.125", "event_type": "LISTENER_TURN", "payload": {
        "node": "sdk_agent", "action": "classify_text", "test_status": None,
        "question_rounds": 1, "agent_text_preview": "Does the app use SceneDelegate?",
        "new_mcp_calls": 0,
    }},
    {"timestamp": "2026-07-14T09:05:30.440", "event_type": "AGENT_PROMPT_GENERATED", "payload": {
        "prompt": STATE["agent_prompts"]["event_prompt"][:500],
    }},
    {"timestamp": "2026-07-14T09:06:12.667", "event_type": "AGENT_DECISION", "payload": {
        "tool": "getTopInAppEvents", "args": {"vertical": "ecommerce"},
    }},
    {"timestamp": "2026-07-14T09:06:18.992", "event_type": "MCP_TOOL_RESULT", "payload": {
        "tool": "getTopInAppEvents",
        "result": "Top events: af_purchase, af_add_to_cart, af_content_view, level_achieved",
    }},
    {"timestamp": "2026-07-14T09:06:45.221", "event_type": "AGENT_DECISION", "payload": {
        "tool": "createIosInAppEvent",
        "args": {
            "eventName": "af_purchase",
            "triggerId": "buyButton",
            "layoutFile": "BananasViewController.swift",
            "eventParams": {"product_id": "banana_bundle"},
        },
    }},
    {"timestamp": "2026-07-14T09:06:52.880", "event_type": "MCP_TOOL_RESULT", "payload": {
        "tool": "createIosInAppEvent",
        "result": "Event af_purchase wired to buyButton in BananasViewController.swift.",
    }},
    {"timestamp": "2026-07-14T09:07:20.115", "event_type": "AGENT_DECISION", "payload": {
        "tool": "verifyIosInAppEvent", "args": {"action": "prepare", "eventName": "af_purchase"},
    }},
    {"timestamp": "2026-07-14T09:07:28.440", "event_type": "MCP_TOOL_RESULT", "payload": {
        "tool": "verifyIosInAppEvent",
        "result": "Prepare step completed. Simulator ready for in-app event tap simulation.",
    }},
    {"timestamp": "2026-07-14T09:07:45.901", "event_type": "AGENT_DECISION", "payload": {
        "tool": "verifyIosInAppEvent", "args": {"action": "verify", "eventName": "af_purchase"},
    }},
    {"timestamp": "2026-07-14T09:07:58.332", "event_type": "MCP_TOOL_RESULT", "payload": {
        "tool": "verifyIosInAppEvent",
        "result": "In-app logs show af_purchase event with product_id=banana_bundle.",
    }},
    {"timestamp": "2026-07-14T09:08:30.550", "event_type": "AGENT_PROMPT_GENERATED", "payload": {
        "prompt": STATE["agent_prompts"]["verify_prompt"][:500],
    }},
    {"timestamp": "2026-07-14T09:09:10.778", "event_type": "AGENT_DECISION", "payload": {
        "tool": "createIosDeepLink",
        "args": {"url": "https://banana.onelink.me/xyz9/offers", "uriScheme": "banana"},
    }},
    {"timestamp": "2026-07-14T09:09:18.004", "event_type": "MCP_TOOL_RESULT", "payload": {
        "tool": "createIosDeepLink",
        "result": "Deep link handler registered in SceneDelegate.continueUserActivity.",
    }},
    {"timestamp": "2026-07-14T09:09:40.221", "event_type": "AGENT_DECISION", "payload": {
        "tool": "verifyIosDeepLink", "args": {"action": "prepare"},
    }},
    {"timestamp": "2026-07-14T09:09:48.667", "event_type": "MCP_TOOL_RESULT", "payload": {
        "tool": "verifyIosDeepLink",
        "result": "Prepare completed. Deep link test environment ready.",
    }},
    {"timestamp": "2026-07-14T09:10:05.992", "event_type": "AGENT_DECISION", "payload": {
        "tool": "verifyIosDeepLink", "args": {"action": "verify", "expectedScreen": "OffersViewController"},
    }},
    {"timestamp": "2026-07-14T09:10:15.118", "event_type": "MCP_TOOL_RESULT", "payload": {
        "tool": "verifyIosDeepLink",
        "result": "Deep link banana://offers opened OffersViewController. Attribution params present.",
    }},
    {"timestamp": "2026-07-14T09:10:15.125", "event_type": "MCP_SEQUENCE", "payload": STATE["mcp_sequence"]},
    {"timestamp": "2026-07-14T09:10:16.001", "event_type": "LISTENER_TEST_STATUS", "payload": {
        "node": "sdk_agent", "test_status": "READY",
    }},
    {"timestamp": "2026-07-14T09:10:20.440", "event_type": "AGENT_SESSION_CLOSED", "payload": {
        "agent_id": "sdk_agent_session_a1b2c3",
    }},
]


class _DemoRecorder:
    def all_events(self) -> list[dict]:
        return AUDIT_EVENTS


def main() -> None:
    path = generate_run_report(STATE, audit_recorder=_DemoRecorder())
    print(f"Full demo report written to:\n  {path.resolve()}")


if __name__ == "__main__":
    main()
