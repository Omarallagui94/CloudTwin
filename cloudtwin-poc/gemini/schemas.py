"""Validated contracts at the Gemini trust boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RequestedPropertyChange(BaseModel):
    property: str = Field(min_length=1, max_length=100)
    value: str | int | float | bool | None = None


class ParsedInfrastructureChange(BaseModel):
    operation: Literal["UPDATE", "CREATE", "DELETE", "SCALE", "EXPOSE", "UNKNOWN"]
    provider: Literal["aws", "kubernetes", "unknown"]
    resource_type: str | None = None
    resource_name: str | None = None
    namespace: str | None = None
    requested_changes: list[RequestedPropertyChange] = Field(default_factory=list, max_length=8)
    summary: str = Field(min_length=1, max_length=500)
    ambiguous: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)


class GeminiAnalysisExplanation(BaseModel):
    summary: str = Field(min_length=1, max_length=1500)
    what_changes: str = Field(min_length=1, max_length=1500)
    impact: str = Field(min_length=1, max_length=1500)
    why_it_is_risky: str = Field(min_length=1, max_length=1500)
    affected_components: list[str] = Field(default_factory=list, max_length=50)
    recommended_actions: list[str] = Field(default_factory=list, max_length=20)
