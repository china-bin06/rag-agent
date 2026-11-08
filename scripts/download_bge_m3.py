import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download


ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "storage/models/bge-m3"
CACHE_DIR = ROOT / "storage/hf_cache"
REPO_ID = "BAAI/bge-m3"


def main() -> None:
    # 固定本次下载的具体版本，后续不随main自动变化。
    info = HfApi().model_info(REPO_ID)
    revision = info.sha

    if not revision:
        raise RuntimeError("未获取到模型提交版本")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    snapshot_download(
        repo_id=REPO_ID,
        revision=revision,
        local_dir=str(MODEL_DIR),
        cache_dir=str(CACHE_DIR),
        force_download=True,
        allow_patterns=[
            "*.json",
            "sentencepiece.bpe.model",
            "pytorch_model.bin",
            "1_Pooling/*",
        ],
    )

    manifest = {
        "repo_id": REPO_ID,
        "revision": revision,
        "purpose": "dense_embedding",
    }
    (MODEL_DIR / "download_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("Model downloaded.")
    print(f"Revision: {revision}")
    print(f"Directory: {MODEL_DIR}")


if __name__ == "__main__":
    main()