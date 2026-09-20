import asyncio

from app.embeddings.base import EmbeddingProvider


class SentenceTransformerProvider(EmbeddingProvider):
    """Local model with micro-batching.

    Inference is CPU-bound and ~5-20x more efficient per text when batched, but
    requests arrive one at a time. `embed` enqueues; a single collector task
    takes whatever has piled up (up to `max_batch`) and encodes it in one call.
    There is no artificial wait: an idle server encodes immediately, and batches
    form naturally only while a previous batch is still running.
    """

    def __init__(
        self,
        model_name: str,
        dim: int,
        max_batch: int = 32,
        device: str = "cpu",
        torch_threads: int = 0,
    ):
        self.model_name = model_name
        self.dim = dim
        self.max_batch = max_batch
        self.device = device
        self.torch_threads = torch_threads
        self._model = None
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _encode_batch(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            if self.torch_threads:
                torch.set_num_threads(self.torch_threads)
            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model.encode(
            texts, normalize_embeddings=True, batch_size=self.max_batch
        ).tolist()

    def _ensure_worker(self) -> None:
        loop = asyncio.get_running_loop()
        if self._task is None or self._task.done() or self._loop is not loop:
            self._loop = loop
            self._queue = asyncio.Queue()
            self._task = loop.create_task(self._run(self._queue))

    async def _run(self, queue: asyncio.Queue) -> None:
        while True:
            batch = [await queue.get()]
            while len(batch) < self.max_batch and not queue.empty():
                batch.append(queue.get_nowait())
            try:
                # Blocking inference stays off the event loop.
                vectors = await asyncio.to_thread(self._encode_batch, [t for t, _ in batch])
            except Exception as exc:
                for _, fut in batch:
                    if not fut.done():
                        fut.set_exception(exc)
                continue
            for (_, fut), vec in zip(batch, vectors):
                if not fut.done():
                    fut.set_result(vec)

    async def embed(self, text: str) -> list[float]:
        self._ensure_worker()
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put((text, fut))
        return await fut
