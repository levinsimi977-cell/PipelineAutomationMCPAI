from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

LlmModel = Literal["gpt-4o-mini", "gpt-4.1", "claude-3-5-sonnet", "claude-3-7-sonnet"]
DEFAULT_LLM_MODEL: LlmModel = "gpt-4o-mini"


class IOSMinimalPolicy(BaseModel):
    use_att: bool = True
    use_cuid: bool = False
    use_scene_delegate: bool = True
    use_response_listener: bool = True


class DeeplinkPolicy(BaseModel):
    use_deep_linking: bool = True
    onelink_url: Optional[str] = None
    url_identifier: Optional[str] = None
    uri_scheme: Optional[str] = None
    use_custom_uri_scheme: bool = False


class InAppEventPolicy(BaseModel):
    inapp_event_method: str = "none"
    event_name: Optional[str] = None
    event_params: Dict[str, Any] = Field(default_factory=dict)


class VerifySdkPolicy(BaseModel):
    verify_logs_ready: bool = True
    app_launched: bool = True
    # Opt-in: when true, emulator_node runs a best-effort Appium smoke test
    # (tap a few on-screen buttons, confirm the app stays responsive) right
    # after launching the app. Off by default since most use cases don't
    # describe any navigation behavior to verify.
    validate_basic_navigation: bool = False


class AndroidPolicy(BaseModel):
    device_id: Optional[str] = None
    has_sha256: bool = False
    sha256_fingerprint: Optional[str] = None


class RulesPolicy(BaseModel):
    default_profiles: List[str] = Field(default_factory=lambda: ["common"])
    allow_user_override: bool = True
    allowed_profiles: List[str] = Field(
        default_factory=lambda: ["common", "ios", "android", "strict"]
    )


class AnswerPolicy(BaseModel):
    ios_minimal: Optional[IOSMinimalPolicy] = None
    deeplink: Optional[DeeplinkPolicy] = None
    in_app_event: Optional[InAppEventPolicy] = None
    verify_sdk: VerifySdkPolicy = Field(default_factory=VerifySdkPolicy)
    android: Optional[AndroidPolicy] = None
    integration_policy: Optional[str] = None
    app_event_policy: Optional[str] = None


class InstallationAnswer(BaseModel):
    question: str
    answer: str


class UseCaseContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    app_path: str
    platform: Literal["common", "ios", "android"]
    prompt_goal: str

    rules_policy: RulesPolicy = Field(default_factory=RulesPolicy)
    answer_policy: AnswerPolicy

    installation_answers: List[InstallationAnswer] = Field(default_factory=list)
    agent_messages: List[str] = Field(default_factory=list)
    installation_agent_summary: str

    app_id: Optional[str] = None
    dev_key: Optional[str] = None
    llm_model: LlmModel = DEFAULT_LLM_MODEL

    @model_validator(mode="after")
    def validate_required_platform_policy(self) -> "UseCaseContract":
        if self.platform == "ios" and self.answer_policy.ios_minimal is None:
            raise ValueError("iOS use case must include answer_policy.ios_minimal")
        if self.platform == "android" and self.answer_policy.android is None:
            raise ValueError("Android use case must include answer_policy.android")
        return self

    def to_pretty_json(self) -> str:
        return self.model_dump_json(indent=2, exclude_none=True)
