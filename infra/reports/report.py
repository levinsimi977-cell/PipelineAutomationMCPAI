"""
ולידטור סדר הפעלת כלי MCP.

קלטים (חייבים להגיע מהקורא — המודול הזה לא שולף אותם בעצמו):
  - platform: פלטפורמת הריצה, למשל "android" או "ios".
              מקור צפוי: הגדרת use-case / קונפיג ריצה (למשל data/useCases/useCase.json)
              או שכבת האורקסטרציה (infra/aplication/app.py).

  - call_log: רשימה מסודרת של הפעלות כלי MCP מריצת ה-agent.
              כל רשומה: {"tool": "<name>", "action": "<optional>"} לכלי iOS רב-שלביים.
              מקור צפוי: פלט ה-SDK agent או trace של ס session MCP
              (infra/agents/sdkAgent/tools/agent.py → עובר דרך app.py).
"""


class McpToolOrderValidator:
    def __init__(self):
        # כלים המותרים ברצף לפי פלטפורמה.
        self.android_tools = {
            "integrateSdk", "verifySdk", "createDeepLink", "guideDeepLinkTesting",
            "verifyDeepLink", "createInAppEvent", "verifyInAppEvent",
        }
        self.ios_tools = {
            "integrateSdk", "verifyIosSdk", "createIosDeepLink", "guideDeepLinkTesting",
            "verifyIosDeepLink", "createIosInAppEvent", "verifyIosInAppEvent",
        }

        # כלים עצמאיים — יכולים להופיע בכל מקום בלוג ללא בדיקת סדר.
        self.independent_tools = {
            "getVersion", "fetchLogs", "getErrors", "getLaunchLogs",
            "getInAppLogs", "getConversionLogs", "getDeepLinkLogs",
        }

        # כלי קטלוג אופציונליים; אם שימשו — חייבים להופיע לפני create-event.
        self.catalog_tools = {"getTopInAppEvents", "getInAppEventsByVertical"}
        self.create_event_tools = {"createInAppEvent", "createIosInAppEvent"}

        # כללי תלות חובה: (pre, post) — post לא יופיע לפני pre.
        # post הוא מזהה השלב ש-_to_step_id מחזיר (tool_action לכלי iOS רב-שלבי).
        self.rules = [
            ("integrateSdk", "verifySdk"),
            ("integrateSdk", "verifyIosSdk_prepare"),
            ("verifyIosSdk_prepare", "verifyIosSdk_verify"),
            ("integrateSdk", "createDeepLink"),
            ("integrateSdk", "createIosDeepLink"),
            ("guideDeepLinkTesting", "verifyDeepLink"),
            ("guideDeepLinkTesting", "verifyIosDeepLink_prepare"),
            ("verifyIosDeepLink_prepare", "verifyIosDeepLink_verify"),
            ("integrateSdk", "createInAppEvent"),
            ("integrateSdk", "createIosInAppEvent"),
            ("createInAppEvent", "verifyInAppEvent"),
            ("createIosInAppEvent", "verifyIosInAppEvent_prepare"),
            ("verifyIosInAppEvent_prepare", "verifyIosInAppEvent_verify"),
        ]

    def validate_sequence(self, call_log, platform):
        """
        בודק ש-call_log עומד בכללי הפלטפורמה ובסדר תלויות הכלים.

        Args:
            call_log: הפעלות כלים לפי סדר — יש לשלוף מפלט agent/MCP (ראה docstring למעלה).
            platform: "android" או "ios" — יש לשלוף מקונפיג use-case או מ-app.
        """
        if not call_log:
            return True, "Log is empty"

        p = platform.lower()
        allowed = self.android_tools if p == "android" else self.ios_tools
        processed_log = [self._to_step_id(entry) for entry in call_log]

        for i, entry in enumerate(call_log):
            tool_name = entry["tool"]
            current_step = processed_log[i]
            prefix = processed_log[:i]

            # כלים עצמאיים — מדלגים על בדיקת סדר.
            if tool_name in self.independent_tools:
                continue

            # דוחה כלים שלא שייכים לפלטפורמת הריצה.
            if tool_name not in allowed and tool_name not in self.catalog_tools:
                return False, f"Tool '{tool_name}' not supported for '{p}'"

            # guideDeepLinkTesting דורש לפחות כלי create אחד קודם בלוג.
            if tool_name == "guideDeepLinkTesting":
                if not any(t in prefix for t in ("createDeepLink", "createIosDeepLink")):
                    return False, "Rule Violation: 'guideDeepLinkTesting' requires a 'create' tool first."
                continue

            # קטלוג אופציונלי — אם הופיע, חייב לבוא לפני create-event.
            if tool_name in self.create_event_tools:
                for cat in self.catalog_tools:
                    if cat in processed_log and cat not in prefix:
                        return False, f"Rule Violation: '{current_step}' before '{cat}'."

            # אכיפת כללי תלות (pre, post).
            for pre, post in self.rules:
                if current_step == post and pre not in prefix:
                    return False, f"Rule Violation ({p}): '{post}' requires '{pre}' first."

        return True, "Sequence is valid"

    def _to_step_id(self, entry):
        """מנרמל רשומת לוג למזהה שלב ייחודי, למשל verifyIosSdk_prepare."""
        tool = entry["tool"]
        action = entry.get("action")
        return f"{tool}_{action}" if action else tool
"""Backward-compatible entrypoint for report generation."""

from infra.user_interface_use_case.reports.reporter import (
    ReportGenerator,
    generate_html_report,
)

__all__ = ["ReportGenerator", "generate_html_report"]

