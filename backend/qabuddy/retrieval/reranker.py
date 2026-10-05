"""Cross-encoder reranking via FastEmbed (ONNX, CPU)."""

from __future__ import annotations


class Reranker:
    def __init__(self, model_name: str):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.model_name = model_name
        self._model = TextCrossEncoder(model_name=model_name)

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        return [float(s) for s in self._model.rerank(query, texts, batch_size=16)]
