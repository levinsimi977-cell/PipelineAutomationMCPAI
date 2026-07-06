from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..schemas import (
    AndroidPolicy,
    AnswerPolicy,
    DeepLinkPolicy,
    IOSMinimalPolicy,
    InAppEventPolicy,
    UseCaseContract,
    VerifySDKPolicy,
)


class UseCaseBuilder:
    """
    Fluent builder for creating UseCaseContract instances.

    This keeps Streamlit or API layers thin and prevents duplicated assembly logic.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> "UseCaseBuilder":
        """Reset builder state for a fresh use case creation."""
        self._app_path: Optional[str] = None
        self._platform: Optional[str] = None
        self._prompt_goal: Optional[str] = None
        self._installation_summary: Optional[str] = None
        self._installation_answers: List[Dict[str, Any]] = []
        self._agent_messages: List[str] = []

        self._ios_minimal: Optional[IOSMinimalPolicy] = None
        self._deeplink: Optional[DeepLinkPolicy] = None
        self._in_app_event: Optional[InAppEventPolicy] = None
        self._verify_sdk: VerifySDKPolicy = VerifySDKPolicy()
        self._android: Optional[AndroidPolicy] = None
        return self

    def with_core(
        self,
        app_path: str,
        platform: str,
        prompt_goal: str,
        installation_agent_summary: str,
    ) -> "UseCaseBuilder":
        """Set top-level required fields."""
        self._app_path = app_path
        self._platform = platform
        self._prompt_goal = prompt_goal
        self._installation_summary = installation_agent_summary
        return self

    def with_ios_minimal(
        self,
        use_att: bool,
        use_cuid: bool,
        use_scene_delegate: bool,
        use_response_listener: bool,
    ) -> "UseCaseBuilder":
        """Attach iOS minimal policy."""
        self._ios_minimal = IOSMinimalPolicy(
            use_att=use_att,
            use_cuid=use_cuid,
            use_scene_delegate=use_scene_delegate,
            use_response_listener=use_response_listener,
        )
        return self

    def with_deeplink(
        self,
        use_deep_linking: bool,
        onelink_url: Optional[str] = None,
        url_identifier: Optional[str] = None,
        uri_scheme: Optional[str] = None,
        use_custom_uri_scheme: bool = False,
    ) -> "UseCaseBuilder":
        """Attach deep link policy."""
        self._deeplink = DeepLinkPolicy(
            use_deep_linking=use_deep_linking,
            onelink_url=onelink_url,
            url_identifier=url_identifier,
            uri_scheme=uri_scheme,
            use_custom_uri_scheme=use_custom_uri_scheme,
        )
        return self

    def with_in_app_event(
        self,
        method: str = "none",
        event_name: Optional[str] = None,
        event_params: Optional[Dict[str, Any]] = None,
    ) -> "UseCaseBuilder":
        """Attach in-app event policy."""
        self._in_app_event = InAppEventPolicy(
            inapp_event_method=method,  # validated by Pydantic Literal
            event_name=event_name,
            event_params=event_params or {},
        )
        return self

    def with_verify_sdk(
        self,
        verify_logs_ready: bool = True,
        app_launched: bool = True,
    ) -> "UseCaseBuilder":
        """Attach SDK verification policy."""
        self._verify_sdk = VerifySDKPolicy(
            verify_logs_ready=verify_logs_ready,
            app_launched=app_launched,
        )
        return self

    def with_android(
        self,
        device_id: Optional[str] = None,
        has_sha256: bool = False,
        sha256_fingerprint: Optional[str] = None,
    ) -> "UseCaseBuilder":
        """Attach Android-specific policy."""
        self._android = AndroidPolicy(
            device_id=device_id,
            has_sha256=has_sha256,
            sha256_fingerprint=sha256_fingerprint,
        )
        return self

    def with_installation_answers(
        self, installation_answers: List[Dict[str, Any]]
    ) -> "UseCaseBuilder":
        """Set installation answers collected from user/system prompts."""
        self._installation_answers = installation_answers
        return self

    def with_agent_messages(self, agent_messages: List[str]) -> "UseCaseBuilder":
        """Set agent messages for traceability."""
        self._agent_messages = agent_messages
        return self

    def build(self) -> UseCaseContract:
        """Create and validate UseCaseContract instance."""
        answer_policy = AnswerPolicy(
            ios_minimal=self._ios_minimal,
            deeplink=self._deeplink,
            in_app_event=self._in_app_event,
            verify_sdk=self._verify_sdk,
            android=self._android,
        )

        return UseCaseContract(
            app_path=self._app_path or "",
            platform=(self._platform or "ios"),
            prompt_goal=self._prompt_goal or "",
            answer_policy=answer_policy,
            installation_answers=self._installation_answers,
            agent_messages=self._agent_messages,
            installation_agent_summary=self._installation_summary or "",
        )

    def build_json(self, indent: int = 2) -> str:
        """Create and serialize contract JSON."""
        return self.build().model_dump_json(indent=indent, exclude_none=True)


class UseCaseFactory:
    """
    Factory for common enterprise use case presets.
    """

    @staticmethod
    def create_ios_deeplink(
        app_path: str,
        prompt_goal: str,
        onelink_url: str,
        url_identifier: str,
        uri_scheme: str,
        installation_agent_summary: str,
    ) -> UseCaseContract:
        """Create a validated iOS deep link use case preset."""
        return (
            UseCaseBuilder()
            .with_core(
                app_path=app_path,
                platform="ios",
                prompt_goal=prompt_goal,
                installation_agent_summary=installation_agent_summary,
            )
            .with_ios_minimal(
                use_att=True,
                use_cuid=False,
                use_scene_delegate=True,
                use_response_listener=True,
            )
            .with_deeplink(
                use_deep_linking=True,
                onelink_url=onelink_url,
                url_identifier=url_identifier,
                uri_scheme=uri_scheme,
                use_custom_uri_scheme=True,
            )
            .with_in_app_event(method="none")
            .with_verify_sdk(verify_logs_ready=True, app_launched=True)
            .with_agent_messages(["Factory preset: iOS deep link"])
            .build()
        )

    @staticmethod
    def create_android_deeplink(
        app_path: str,
        prompt_goal: str,
        onelink_url: str,
        url_identifier: str,
        installation_agent_summary: str,
        device_id: Optional[str] = None,
    ) -> UseCaseContract:
        """Create a validated Android deep link use case preset."""
        return (
            UseCaseBuilder()
            .with_core(
                app_path=app_path,
                platform="android",
                prompt_goal=prompt_goal,
                installation_agent_summary=installation_agent_summary,
            )
            .with_deeplink(
                use_deep_linking=True,
                onelink_url=onelink_url,
                url_identifier=url_identifier,
                uri_scheme="myapp",
                use_custom_uri_scheme=True,
            )
            .with_in_app_event(method="none")
            .with_verify_sdk(verify_logs_ready=True, app_launched=True)
            .with_android(device_id=device_id, has_sha256=False)
            .with_agent_messages(["Factory preset: Android deep link"])
            .build()
        )

