import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer


ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "storage/models/bge-m3"

DIMENSION = 1024
MAX_LENGTH = 512
BATCH_SIZE = 4
DEVICE = "cpu"


class EmbeddingInputError(ValueError):
    pass


def embedding_config() -> dict:
    manifest = json.loads(
        (MODEL_DIR / "download_manifest.json").read_text(
            encoding="utf-8"
        )
    )

    if manifest["repo_id"] != "BAAI/bge-m3":
        raise ValueError("模型不是预期的BGE-M3")

    return {
        "repo_id": manifest["repo_id"],
        "revision": manifest["revision"],
        "dimension": DIMENSION,
        "max_length": MAX_LENGTH,
        "batch_size": BATCH_SIZE,
        "device": DEVICE,
        "dtype": "float32",
        "normalize_embeddings": True,
        "prompt": "",
        "similarity": "cosine",
    }


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    embedding_config()

    model = SentenceTransformer(
        str(MODEL_DIR),
        device=DEVICE,
        local_files_only=True,
        trust_remote_code=False,
    )
    model = model.float()
    model.max_seq_length = MAX_LENGTH
    return model


def encode_texts(
    texts: list[str],
    *,
    show_progress: bool = False,
) -> np.ndarray:
    if not texts or any(
        not isinstance(text, str) or not text.strip()
        for text in texts
    ):
        raise ValueError("编码输入必须是非空文本列表")

    model = get_model()

    # 编码前检查，防止模型静默截断文本。
    for index, text in enumerate(texts):
        token_ids = model.tokenizer(
            text,
            truncation=False,
            add_special_tokens=True,
        )["input_ids"]

        if len(token_ids) > MAX_LENGTH:
            raise EmbeddingInputError(
                f"第{index + 1}段文本超过{MAX_LENGTH} tokens："
                f"{len(token_ids)}"
            )

    vectors = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        prompt="",
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=show_progress,
    )
    vectors = np.ascontiguousarray(vectors, dtype=np.float32)

    if vectors.shape != (len(texts), DIMENSION):
        raise ValueError(f"向量形状异常：{vectors.shape}")

    if not np.isfinite(vectors).all():
        raise ValueError("向量包含非有限值")

    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-4):
        raise ValueError("向量未正确归一化")

    return vectors
