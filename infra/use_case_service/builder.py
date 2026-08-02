from __future__ import annotations

from typing import Optional

from infra.use_case_service.schemas import (
    AndroidPolicy,
    AnswerPolicy,
    DeeplinkPolicy,
    InAppEventPolicy,
    IOSMinimalPolicy,
    UseCaseContract,
    VerifySdkPolicy,
)


class UseCaseBuilder:
    def __init__(self) -> None:
        self._app_path: Optional[str] = None
        self._platform: Optional[str] = None
        self._prompt_goal: Optional[str] = None
        self._installation_agent_summary: Optional[str] = None
        self._app_id: Optional[str] = None
        self._dev_key: Optional[str] = None
        self._llm_model: Optional[str] = None
        self._answer_policy = AnswerPolicy()

    def with_core(
        self,
        *,
        app_path: str,
        platform: str,
        prompt_goal: str,
        installation_agent_summary: str,
        app_id: Optional[str] = None,
        dev_key: Optional[str] = None,
    ) -> "UseCaseBuilder":
        self._app_path = app_path
        self._platform = platform
        self._prompt_goal = prompt_goal
        self._installation_agent_summary = installation_agent_summary
        self._app_id = app_id
        self._dev_key = dev_key
        return self

    def with_llm_model(self, llm_model: str) -> "UseCaseBuilder":
        self._llm_model = llm_model
        return self

    def with_verify_sdk(self, *, verify_logs_ready: bool, app_launched: bool) -> "UseCaseBuilder":
        self._answer_policy.verify_sdk = VerifySdkPolicy(
            verify_logs_ready=verify_logs_ready,
            app_launched=app_launched,
        )
        return self

    def with_ios_minimal(
        self, *, use_att: bool, use_cuid: bool, use_scene_delegate: bool, use_response_listener: bool
    ) -> "UseCaseBuilder":
        self._answer_policy.ios_minimal = IOSMinimalPolicy(
            use_att=use_att,
            use_cuid=use_cuid,
            use_scene_delegate=use_scene_delegate,
            use_response_listener=use_response_listener,
        )
        return self

    def with_android(
        self, *, device_id: Optional[str], has_sha256: bool, sha256_fingerprint: Optional[str]
    ) -> "UseCaseBuilder":
        self._answer_policy.android = AndroidPolicy(
            device_id=device_id,
            has_sha256=has_sha256,
            sha256_fingerprint=sha256_fingerprint,
        )
        return self

    def with_deeplink(
        self,
        *,
        use_deep_linking: bool,
        onelink_url: Optional[str],
        url_identifier: Optional[str],
        uri_scheme: Optional[str],
        use_custom_uri_scheme: bool,
    ) -> "UseCaseBuilder":
        self._answer_policy.deeplink = DeeplinkPolicy(
            use_deep_linking=use_deep_linking,
            onelink_url=onelink_url,
            url_identifier=url_identifier,
            uri_scheme=uri_scheme,
            use_custom_uri_scheme=use_custom_uri_scheme,
        )
        return self

    def with_in_app_event(self, *, method: str, event_name: Optional[str]) -> "UseCaseBuilder":
        self._answer_policy.in_app_event = InAppEventPolicy(
            inapp_event_method=method,
            event_name=event_name,
        )
        return self

    def with_integration_policy(self, text: Optional[str]) -> "UseCaseBuilder":
        self._answer_policy.integration_policy = text
        return self

    def with_app_event_policy(self, text: Optional[str]) -> "UseCaseBuilder":
        self._answer_policy.app_event_policy = text
        return self

    def build(self) -> UseCaseContract:
        return UseCaseContract(
            app_path=self._app_path or "",
            platform=self._platform or "ios",
            prompt_goal=self._prompt_goal or "",
            answer_policy=self._answer_policy,
            installation_answers=[],
            agent_messages=[],
            installation_agent_summary=self._installation_agent_summary or "",
            app_id=self._app_id,
            dev_key=self._dev_key,

            llm_model=self._llm_model,
        )
