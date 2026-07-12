from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, field_validator, model_validator

from .base import BaseContract
from .policies import (
    AndroidPolicy,
    DeepLinkPolicy,
    IOSMinimalPolicy,
    InAppEventPolicy,
    VerifySDKPolicy,
)

Platform = Literal["ios", "android"]

LlmModel = Literal[
    "claude-sonnet-5-thinking-high",
    "claude-opus-4-8-thinking-high",
    "gpt-5.5-medium",
    "gpt-5.3-codex",
    "composer-2.5-fast",
    "grok-build-0.1",
]

DEFAULT_LLM_MODEL: LlmModel = "claude-sonnet-5-thinking-high"


class AnswerPolicy(BaseContract):
    """Flexible wrapper around all supported policy blocks."""

    ios_minimal: Optional[IOSMinimalPolicy] = None
    deeplink: Optional[DeepLinkPolicy] = None
    in_app_event: Optional[InAppEventPolicy] = None
    verify_sdk: VerifySDKPolicy = Field(default_factory=VerifySDKPolicy)
    android: Optional[AndroidPolicy] = None
    integration_policy: Optional[str] = Field(
        default=None,
        description=(
            "Free-form notes/requirements regarding the SDK integration itself "
            "(e.g. required SDK versions, initialization constraints, auth rules)."
        ),
    )
    app_event_policy: Optional[str] = Field(
        default=None,
        description=(
            "Free-form notes/requirements regarding AppEvents (e.g. custom "
            "parameters to track, triggers, or naming conventions)."
        ),
    )


class UseCaseContract(BaseContract):
    """
    Root use case contract consumed by Group 2, Group 3, and Group 4.
    """

    app_path: str
    platform: Platform
    prompt_goal: str = Field(
        ...,
        description=(
            "The testing goal for this use case, in your own words — e.g. what to "
            "verify plus anything else you want the agent to do."
        ),
    )
    answer_policy: AnswerPolicy
    installation_answers: List[Dict[str, Any]] = Field(default_factory=list)
    agent_messages: List[str] = Field(default_factory=list)
    installation_agent_summary: str
    llm_model: LlmModel = DEFAULT_LLM_MODEL
    app_id: Optional[str] = None
    dev_key: Optional[str] = None

    @field_validator("app_path", "prompt_goal", "installation_agent_summary")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        """Ensure mandatory text fields are not empty."""
        if not value or not value.strip():
            raise ValueError("Field must not be empty")
        return value

    @field_validator("app_id", "dev_key")
    @classmethod
    def validate_optional_non_blank(cls, value: Optional[str]) -> Optional[str]:
        """Allow these fields to be absent, but reject blank/whitespace-only values."""
        if value is not None and not value.strip():
            raise ValueError("Field must not be blank when provided")
        return value

    @model_validator(mode="after")
    def validate_platform_policies(self) -> "UseCaseContract":
        """Enforce platform-specific policy combinations."""
        if self.platform == "ios":
            if self.answer_policy.ios_minimal is None:
                raise ValueError("ios_minimal policy is required for iOS use cases")
            if self.answer_policy.android is not None:
                raise ValueError("android policy must not be provided for iOS use cases")
            if not self.app_path.lower().endswith((".ipa", ".app", ".zip")):
                raise ValueError("iOS app_path should end with .ipa, .app, or .zip")

        if self.platform == "android":
            if self.answer_policy.android is None:
                raise ValueError("android policy is required for Android use cases")
            if self.answer_policy.ios_minimal is not None:
                raise ValueError(
                    "ios_minimal policy must not be provided for Android use cases"
                )
            if not self.app_path.lower().endswith((".apk", ".aab", ".zip")):
                raise ValueError("Android app_path should end with .apk, .aab, or .zip")

        return self

    @classmethod
    def from_file(cls, file_path: str | Path) -> "UseCaseContract":
        """Load and validate a JSON use case file."""
        return cls.model_validate_json(Path(file_path).read_text(encoding="utf-8"))

    def to_pretty_json(self) -> str:
        """Serialize model to indented JSON text."""
        return self.model_dump_json(indent=2, exclude_none=True)

