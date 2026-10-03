import re
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _clean(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = " ".join(_CONTROL_CHARS.sub(" ", value).split())
    if not cleaned:
        raise ValueError("This field cannot be empty.")
    return cleaned


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(max_length=60)
    location: str = Field(max_length=60)
    email: EmailStr | None = None
    level: str | None = Field(default=None, max_length=30)
    skills: str | None = Field(default=None, max_length=200)

    @field_validator("role", "location", "level", "skills", mode="before")
    @classmethod
    def clean_text(cls, value: Any) -> Any:
        return _clean(value) if value is not None else None

    @field_validator("email", mode="before")
    @classmethod
    def trim_email(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value
