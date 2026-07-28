#בדיקת סדר הפעלת הכלים

#יבוא קובץ הרקורדר
from infra.agents.AuditRecorder import AuditRecorder

#פונקציה המקבלת רשימת הכלים שהופעלו בפורמט המדויק ופלטפורמה ומחזירה רשימה מעודכנת
def return_update_list(mcp_tool_results: list[dict], platform: str) -> list:
    platform_key = (platform or "").lower()

#אם הפלטפורמה היא ios
    if platform_key == "ios":
        return [
            #השדות שרוצים להחזיר עבור כל כלי ברשימה
            {"tool": entry.get("tool"), "action": entry.get("action")}
            #רץ על כל הכלים ברשימה שהתקבלה
            for entry in mcp_tool_results
            # רק כלים שבאמת הופעלו ע"י ה-mcp: status == "success"
            if entry.get("status") == "success"
        ]
#אם הפלטפורמה היא android
    return [
        #רוצה לשמור רק את שם הכלי
        entry.get("tool")
        #רץ על כל יומן הכלים ברשימה המקורית
        for entry in mcp_tool_results
        # רק כלים שבאמת הופעלו ע"י ה-mcp: status == "success"
        if entry.get("status") == "success"
    ]

#פונקציה השולפת רשימה בפורמט מעודכן מהרקורדר
def get_updated_list_from_recorder(recorder: AuditRecorder, platform: str) -> list:
#מעבירה את הרשימה שמחזירה מהרקורדר לפונקציה ששולפת רשימה בפורמט מעודכן
    return return_update_list(recorder.mcp_tool_results(), platform)


#מחלקה שבודקת סדר הפעלת הכלים
class McpToolOrderValidator:

    # כלים אופציונליים — לא מחייבים integrateSdk ולא משפיעים על זרימת החובה
    OPTIONAL = {
        "getInAppEventsByVertical", "guideDeepLinkTesting",
        "getErrors", "fetchLogs", "getLaunchLogs", "getInAppLogs",
        "getConversionLogs", "getDeepLinkLogs", "getVersion",
    }

    # אם אחד מהכלים האלה הופעל — נדרשת זרימת in-app מלאה
    IN_APP_TRIGGERS = {
        "getTopInAppEvents", "getInAppEventsByVertical",
        "createInAppEvent", "createIosInAppEvent", "verifyInAppEvent", "verifyIosInAppEvent",
    }

    # אם אחד מהכלים האלה הופעל — נדרשת זרימת deep-link מלאה
    DEEP_LINK_TRIGGERS = {
        "createDeepLink", "createIosDeepLink", "verifyDeepLink", "verifyIosDeepLink", "guideDeepLinkTesting",
    }

    # כללי סדר: (קודם, אחרי) — הכלי השני חייב להופיע אחרי הראשון
    ORDER_RULES = [
        ("integrateSdk", "verifySdk"),
        ("integrateSdk", "verifyIosSdk_prepare"), ("verifyIosSdk_prepare", "verifyIosSdk_verify"),
        ("integrateSdk", "createDeepLink"), ("integrateSdk", "createIosDeepLink"),
        ("createDeepLink", "verifyDeepLink"),
        ("createIosDeepLink", "verifyIosDeepLink_prepare"), ("verifyIosDeepLink_prepare", "verifyIosDeepLink_verify"),
        ("integrateSdk", "createInAppEvent"), ("integrateSdk", "createIosInAppEvent"),
        ("getTopInAppEvents", "createInAppEvent"), ("getTopInAppEvents", "createIosInAppEvent"),
        ("createInAppEvent", "verifyInAppEvent"),
        ("createIosInAppEvent", "verifyIosInAppEvent_prepare"), ("verifyIosInAppEvent_prepare", "verifyIosInAppEvent_verify"),
    ]

    # הגדרות לפי פלטפורמה: כלים מותרים, חובה לכל זרימה, ושלב verify בנתיב guide
    REQUIRED = {
        "android": {
            "allowed": {"integrateSdk", "verifySdk", "createDeepLink", "guideDeepLinkTesting", "verifyDeepLink",
                        "createInAppEvent", "verifyInAppEvent", "getTopInAppEvents", "getInAppEventsByVertical"},
            "verify": ["verifySdk"],
            "in_app": ["getTopInAppEvents", "createInAppEvent", "verifyInAppEvent"],
            "deep_link": ["createDeepLink", "verifyDeepLink"],
            "guide_verify": "verifyDeepLink",
        },
        "ios": {
            "allowed": {"integrateSdk", "verifyIosSdk", "createIosDeepLink", "guideDeepLinkTesting", "verifyIosDeepLink",
                        "createIosInAppEvent", "verifyIosInAppEvent", "getTopInAppEvents", "getInAppEventsByVertical"},
            "verify": ["verifyIosSdk_prepare", "verifyIosSdk_verify"],
            "in_app": ["getTopInAppEvents", "createIosInAppEvent", "verifyIosInAppEvent_prepare", "verifyIosInAppEvent_verify"],
            "deep_link": ["createIosDeepLink", "verifyIosDeepLink_prepare", "verifyIosDeepLink_verify"],
            "guide_verify": "verifyIosDeepLink_prepare",
        },
    }

#הפונקציה שעושה את נקודת הכניסה לבדיקת הסדר
    def validate_sequence(self, recorder: AuditRecorder, state: dict) -> tuple[bool, str]:
        platform = (state.get("platform") or "").strip().lower()
        if not platform:
            return False, "Missing 'platform' in state"
        #מעבירה לבדיקה את הרשימה המסוננת/מפורמטת (לא את הלוג הגולמי) —
        #כך שהיא בודקת סדר על אותה רשימה בדיוק שreturn_update_list מפיקה,
        #בלי לשכפל שוב את לוגיקת הסינון (status=="success") וההוצאה
        #tool/action
        return self._validate(return_update_list(recorder.mcp_tool_results(), platform), platform)

    def _validate(self, invoked_tools, platform):
        """ליבת הבדיקה — אוסף את כל הטעויות ומחזיר אותן יחד."""
        # רשימה ריקה / פלטפורמה לא נתמכת — אין טעם להמשיך
        if not invoked_tools:
            return False, "Log is empty — no tools were invoked"
        if platform not in self.REQUIRED:
            return False, f"Unsupported platform: '{platform}'"

        cfg = self.REQUIRED[platform]
        # invoked_tools הוא הפלט של return_update_list (לא לוג גולמי!) —
        # כלומר רק כלים שבאמת הופעלו בהצלחה ע"י ה-mcp. ל-ios זו רשימת
        # dict-ים {"tool":..., "action":...}, לכל פלטפורמה אחרת זו רשימת
        # מחרוזות (שם כלי בלבד) — _tool_name/_step למטה יודעות להתמודד
        # עם שתי הצורות.
        tools = [self._tool_name(e) for e in invoked_tools]  # שמות כלים בלבד
        invoked = set(tools)                            # סט של כלים שהופעלו
        steps = [self._step(e) for e in invoked_tools]  # כולל action (למשל verifyIosSdk_prepare)
        present = invoked | set(steps)                  # נוכחות לפי שם כלי או שלב
        errors: list[str] = []

        # רק כלים אופציונליים (לוגים וכו') — תקין בלי integrateSdk
        if not (invoked - self.OPTIONAL):
            return True, "Sequence is valid — only optional diagnostic tools were invoked"

        # integrateSdk חייב להיות קיים ולהיות הראשון מבין הכלים הנדרשים
        if "integrateSdk" not in invoked:
            errors.append("Missing required tool: integrateSdk")
        if next((t for t in tools if t not in self.OPTIONAL), None) != "integrateSdk":
            errors.append("integrateSdk must be the first required tool invoked")

        # בדיקת כלים חובה לפי זרימה: אינטגרציה / in-app / deep-link
        for label, required in (("integration", cfg["verify"]),
                                ("in-app", cfg["in_app"]) if invoked & self.IN_APP_TRIGGERS else (None, []),
                                ("deep-link", cfg["deep_link"]) if invoked & self.DEEP_LINK_TRIGGERS else (None, [])):
            if label and (err := self._missing(present, required, label)):
                errors.append(err)

        # אם guide הופעל — חובה create → guide → verify
        if "guideDeepLinkTesting" in invoked:
            errors.extend(self._guide_order(tools, steps, cfg["guide_verify"]))

        # בדיקת כל כלי: פלטפורמה, קטלוג אופציונלי, וכללי סדר
        for i, tool in enumerate(tools):
            if tool in self.OPTIONAL:
                continue
            if tool not in cfg["allowed"]:
                errors.append(f"Tool '{tool}' not supported for '{platform}'")
            # אם נקרא getInAppEventsByVertical — חייב לבוא לפני create
            if tool in {"createInAppEvent", "createIosInAppEvent"} and "getInAppEventsByVertical" in invoked:
                if "getInAppEventsByVertical" not in tools[:i]:
                    errors.append(
                        f"Rule Violation: '{steps[i]}' was invoked before optional catalog 'getInAppEventsByVertical'"
                    )
            prefix = steps[:i]
            for pre, post in self.ORDER_RULES:
                if steps[i] == post and pre not in prefix:
                    errors.append(f"Rule Violation ({platform}): '{post}' requires '{pre}' first")

        if errors:
            return False, self._format_errors(errors)
        return True, "Sequence is valid"

    @staticmethod
    def _format_errors(errors: list[str]) -> str:
        """מאחד רשימת טעויות להודעה אחת, בלי כפילויות."""
        unique = list(dict.fromkeys(errors))
        return "Validation failed:\n" + "\n".join(f"- {err}" for err in unique)

    def _missing(self, present, required, label):
        """מחזיר הודעת שגיאה אם חסרים כלים חובה בזרימה, אחרת None."""
        missing = [r for r in required if r not in present]
        return f"Missing required {label} tool(s): {', '.join(missing)}" if missing else None

    def _guide_order(self, tools, steps, verify_step):
        """בודק סדר guide: createDeepLink לפני guide, ו-guide לפני verify."""
        errors: list[str] = []
        idx = tools.index("guideDeepLinkTesting")
        if not (set(tools[:idx]) & {"createDeepLink", "createIosDeepLink"}):
            errors.append("Rule Violation: 'guideDeepLinkTesting' requires createDeepLink or createIosDeepLink first")
        for i, step in enumerate(steps):
            if step == verify_step and "guideDeepLinkTesting" not in tools[:i]:
                errors.append(
                    f"Rule Violation: '{verify_step}' requires 'guideDeepLinkTesting' first when guide was invoked"
                )
        return errors

    @staticmethod
    def _tool_name(entry):
        """מוציאה שם כלי מרשומה, בין אם היא dict (ios) ובין אם מחרוזת (android)."""
        return entry["tool"] if isinstance(entry, dict) else entry

    @staticmethod
    def _step(entry):
        """ממיר רשומה לשלב: tool או tool_action (ל-iOS prepare/verify).
        entry יכולה להיות dict {"tool":..., "action":...} (ios) או
        מחרוזת עם שם הכלי בלבד (android) — במקרה כזה אין action."""
        if isinstance(entry, dict):
            tool, action = entry.get("tool"), entry.get("action")
        else:
            tool, action = entry, None
        return f"{tool}_{action}" if action else tool


#פונקציה ששומרת את המשתנים לסטייט
def update_state(state: dict, recorder: AuditRecorder) -> None:
   
    #שומרת במשתנה את תוצאות בדיקת לוגיקת הכלים
    validator = McpToolOrderValidator()
    #שומרת ב2 משתנים את התוצאה והודעה מתאימה
    is_valid, message = validator.validate_sequence(recorder=recorder, state=state)
    #שומרת את התוצאה בסטייט
    state["is_tool_order_valid"] = is_valid
    #שומרת את הודעת התוצאה בסטייט
    state["is_tool_order_valid_message"] = message




from infra.reports.reporter import ReportGenerator, generate_html_report

__all__ = [
    "return_update_list",
    "get_updated_list_from_recorder",
    "McpToolOrderValidator",
    "update_state",
    "ReportGenerator",
    "generate_html_report",
]
