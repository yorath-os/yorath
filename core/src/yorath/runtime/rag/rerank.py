"""Reranker contract: the interface exists, off on CPU (spike); cross-encoder is Later."""

from typing import Protocol

from yorath.runtime.rag.search_types import Hit


class Reranker(Protocol):
    name: str

    async def rerank(self, query: str, hits: list[Hit]) -> list[Hit]: ...


class NoopReranker:
    name = "noop"

    async def rerank(self, query: str, hits: list[Hit]) -> list[Hit]:
        return hits
