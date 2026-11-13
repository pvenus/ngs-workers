from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, model_validator

class GenerateRequest(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def validate_hq_raw(cls, value):
        from .hq_contract import MODEL, validate_payload
        if isinstance(value, dict) and value.get("model") == MODEL:
            validate_payload(value)
        return value

    prompt: str = Field(min_length=1, max_length=2000)
    duration_seconds: float = Field(default=3.0, ge=0.25, le=30.0)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    model: str = "small-sfx"
    kind: Literal["sfx", "music"] = "sfx"
    idempotency_key: str | None = Field(default=None, max_length=200)
    generation_settings: dict = Field(default_factory=dict)
    title: str | None = Field(default=None, max_length=300)
    use_case: str | None = Field(default=None, max_length=300)
    brief: dict = Field(default_factory=dict)
    prompt_version: str = Field(default="1", max_length=80)

class LoopPostprocessRequest(BaseModel):
    artifact_id: str
    crossfade_ms: int = Field(default=50, ge=0, le=2000)
