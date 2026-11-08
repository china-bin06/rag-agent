from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    model = SentenceTransformer(
        str(ROOT / "storage/models/bge-m3"),
        device="cpu",
        local_files_only=True,
        trust_remote_code=False,
    )
    model.max_seq_length = 512

    texts = [
        "员工出差住宿报销上限是多少？",
        "普通员工住宿报销上限为400元每晚。",
    ]

    for text in texts:
        tokens = model.tokenizer(
            text,
            truncation=False,
            add_special_tokens=True,
        )["input_ids"]
        if len(tokens) > model.max_seq_length:
            raise ValueError("文本超过本次基线长度限制")

    vectors = model.encode(
        texts,
        batch_size=4,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).astype(np.float32)

    if vectors.shape != (2, 1024):
        raise ValueError(f"向量形状异常：{vectors.shape}")

    if not np.isfinite(vectors).all():
        raise ValueError("向量包含非有限数值")

    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-4):
        raise ValueError("向量未正确归一化")

    print("Vector shape:", vectors.shape)
    print("Vector norms:", norms)
    print("Cosine similarity:", float(vectors[0] @ vectors[1]))


if __name__ == "__main__":
    main()