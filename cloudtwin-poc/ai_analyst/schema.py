"""Format de la réponse de l'analyste IA (validé par Pydantic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Recommendation(BaseModel):
    rule_id: str = Field(pattern=r"^RULE-[A-Z0-9]+-\d{3}$")
    action: str = Field(min_length=5, max_length=400)


class Explanation(BaseModel):
    summary: str = Field(min_length=10, max_length=1500)
    recommendations: list[Recommendation]
    source: Literal["llm", "template"] = "template"
    model: str | None = None
    warnings: list[str] = Field(default_factory=list)   # pourquoi la réponse du LLM a été écartée
