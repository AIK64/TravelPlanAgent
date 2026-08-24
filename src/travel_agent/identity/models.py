from __future__ import annotations

from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Principal(BaseModel):
    """由可信认证适配层构造的调用主体。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    scopes: frozenset[str] = frozenset()
    authentication_method: str = Field(default="dev", min_length=1, max_length=32)

    @field_validator("tenant_id", "user_id")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identity identifier must not be blank")
        if any(character in normalized for character in "\r\n\t"):
            raise ValueError("identity identifier contains control characters")
        return normalized

    def can(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes

    @property
    def safe_user_hash(self) -> str:
        return sha256(
            f"{self.tenant_id}:{self.user_id}".encode("utf-8")
        ).hexdigest()[:16]
