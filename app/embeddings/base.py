from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    dim: int

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Return the embedding vector for `text`."""
