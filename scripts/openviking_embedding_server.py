"""OpenAI-compatible embedding endpoint for OpenViking local legal corpus."""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer


MODEL_PATH = os.getenv(
    "LEGAL_EMBEDDING_MODEL",
    "/Users/didi/Desktop/Legal/models/bge-small-zh-v1.5",
)
MODEL_NAME = os.getenv("LEGAL_EMBEDDING_MODEL_NAME", "bge-small-zh-v1.5")

app = FastAPI()
model: SentenceTransformer | None = None


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None


@app.on_event("startup")
def load_model() -> None:
    global model
    model = SentenceTransformer(MODEL_PATH)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "model": MODEL_NAME}


@app.post("/v1/embeddings")
def embeddings(request: EmbeddingRequest) -> dict[str, Any]:
    if model is None:
        raise RuntimeError("embedding model not loaded")
    texts = request.input if isinstance(request.input, list) else [request.input]
    vectors = model.encode(texts, normalize_embeddings=True).tolist()
    return {
        "object": "list",
        "model": request.model or MODEL_NAME,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }
