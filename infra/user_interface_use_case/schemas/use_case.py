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


class AnswerPolicy(BaseContract):
    """Flexible wrapper around all supported policy blocks."""

    ios_minimal: Optional[IOSMinimalPolicy] = None
    deeplink: Optional[DeepLinkPolicy] = None
    in_app_event: Optional[InAppEventPolicy] = None
    verify_sdk: VerifySDKPolicy = Field(default_factory=VerifySDKPolicy)
    android: Optional[AndroidPolicy] = None


class UseCaseContract(BaseContract):
    """
    Root use case contract consumed by Group 2, Group 3, and Group 4.
    """

    app_path: str
    platform: Platform
    prompt_goal: str
    answer_policy: AnswerPolicy
    installation_answers: List[Dict[str, Any]] = Field(default_factory=list)
    agent_messages: List[str] = Field(default_factory=list)
    installation_agent_summary: str

    @field_validator("app_path", "prompt_goal", "installation_agent_summary")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        """Ensure mandatory text fields are not empty."""
        if not value or not value.strip():
            raise ValueError("Field must not be empty")
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

