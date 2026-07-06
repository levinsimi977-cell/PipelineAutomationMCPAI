from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContractMetadata(BaseModel):
    """
    Shared metadata used by all Group 2 contracts.

    The schema_version field is mandatory for backward compatibility when the
    contract evolves over time.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: str = "1.0.0"

    @field_validator("schema_version")
    @classmethod
    def validate_semver_format(cls, value: str) -> str:
        """Validate semantic version shape: major.minor.patch."""
        parts = value.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise ValueError("schema_version must follow semantic format, e.g. '1.0.0'")
        return value


class BaseContract(BaseModel):
    """
    Base class for all contracts with strict validation defaults.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    metadata: ContractMetadata = Field(default_factory=ContractMetadata)

    DEFAULT_SCHEMA_VERSION: ClassVar[str] = "1.0.0"

