"""Embeddings through Ollama /api/embed, in batches, normalized (as in the spike)."""

import math
from typing import Protocol

import httpx


class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


class OllamaEmbeddings:
    def __init__(self, http: httpx.AsyncClient, base_url: str, model: str, batch: int = 32) -> None:
        self._http = http
        self._url = f"{base_url.rstrip('/')}/api/embed"
        self.model = model
        self._batch = batch

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            r = await self._http.post(
                self._url,
                json={"model": self.model, "input": texts[i : i + self._batch]},
                timeout=600,
            )
            r.raise_for_status()
            out.extend(normalize(v) for v in r.json()["embeddings"])
        return out
