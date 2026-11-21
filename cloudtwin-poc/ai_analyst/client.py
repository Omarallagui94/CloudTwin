"""Clients LLM. Par défaut aucun : l'explication de secours est utilisée.

Variables d'environnement :
  CLOUDTWIN_LLM          "ollama" pour un modèle local ; absent = pas de LLM
  CLOUDTWIN_LLM_MODEL    nom du modèle (défaut : llama3.1)
  CLOUDTWIN_OLLAMA_URL   adresse d'Ollama (défaut : http://localhost:11434)
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Protocol


class LLMClient(Protocol):
    model: str

    def complete(self, messages: list[dict[str, str]]) -> str:
        """Renvoie le texte brut de la réponse. Lève une exception en cas d'échec."""


class OllamaClient:
    """Modèle local via Ollama : aucune donnée ne sort de la machine."""

    def __init__(self, model: str = "llama3.1", url: str = "http://localhost:11434", timeout: float = 12.0):
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout

    def complete(self, messages: list[dict[str, str]]) -> str:
        body = json.dumps({"model": self.model, "messages": messages, "format": "json",
                           "stream": False, "options": {"temperature": 0}}).encode("utf-8")
        request = urllib.request.Request(f"{self.url}/api/chat", data=body,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())["message"]["content"]


def from_env() -> LLMClient | None:
    if os.environ.get("CLOUDTWIN_LLM", "").lower() == "ollama":
        return OllamaClient(
            model=os.environ.get("CLOUDTWIN_LLM_MODEL", "llama3.1"),
            url=os.environ.get("CLOUDTWIN_OLLAMA_URL", "http://localhost:11434"),
        )
    return None
