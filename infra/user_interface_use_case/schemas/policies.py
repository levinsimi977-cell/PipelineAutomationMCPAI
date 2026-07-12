from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import Field, model_validator

from .base import BaseContract


class IOSMinimalPolicy(BaseContract):
    """iOS minimal setup policy fields."""

    use_att: bool
    use_cuid: bool
    use_scene_delegate: bool
    use_response_listener: bool


class DeepLinkPolicy(BaseContract):
    """Deep link policy fields for both iOS and Android."""

    use_deep_linking: bool = False
    onelink_url: Optional[str] = None
    url_identifier: Optional[str] = None
    uri_scheme: Optional[str] = None
    use_custom_uri_scheme: bool = False

    @model_validator(mode="after")
    def validate_dependencies(self) -> "DeepLinkPolicy":
        """Require specific deep link fields only when enabled."""
        if self.use_deep_linking:
            missing = []
            if not self.onelink_url:
                missing.append("onelink_url")
            if not self.url_identifier:
                missing.append("url_identifier")
            if missing:
                raise ValueError(
                    "Missing required deep link fields: " + ", ".join(missing)
                )

        if self.use_custom_uri_scheme and not self.uri_scheme:
            raise ValueError("uri_scheme is required when use_custom_uri_scheme=true")

        return self


class InAppEventPolicy(BaseContract):
    """In-app event policy fields."""

    inapp_event_method: Literal["none", "log_event", "validate_payload", "custom"] = (
        "none"
    )
    event_name: Optional[str] = None
    event_params: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dependencies(self) -> "InAppEventPolicy":
        """Require event_name only when event flow is active."""
        if self.inapp_event_method != "none" and not self.event_name:
            raise ValueError(
                "event_name is required when inapp_event_method is not 'none'"
            )
        return self


class VerifySDKPolicy(BaseContract):
    """SDK readiness verification checks."""

    verify_logs_ready: bool = True
    app_launched: bool = True


class AndroidPolicy(BaseContract):
    """Android-specific policy fields."""

    device_id: Optional[str] = None
    has_sha256: bool = False
    sha256_fingerprint: Optional[str] = None

    @model_validator(mode="after")
    def validate_dependencies(self) -> "AndroidPolicy":
        """Require sha256 fingerprint when has_sha256 is true."""
        if self.has_sha256 and not self.sha256_fingerprint:
            raise ValueError("sha256_fingerprint is required when has_sha256=true")
        return self

