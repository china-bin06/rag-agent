import argparse
import json
from contextlib import closing
from pathlib import Path

from qdrant_client import QdrantClient

from app.db.database import SessionLocal
from app.rag.embeddings import embedding_config
from app.rag.retrieval_eval import DEFAULT_RETRIEVAL_DATASET, evaluate_retrieval
from app.rag.vector import QDRANT_PATH, VectorRanker, validate_vector_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate dense vector retrieval")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_RETRIEVAL_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with SessionLocal() as db, closing(QdrantClient(path=str(QDRANT_PATH))) as client:
        validate_vector_index(client, db)
        report = evaluate_retrieval(db, args.dataset, VectorRanker(client))

    report["strategy"] = {
        "name": "vector_bge_m3",
        "embedding": embedding_config(),
        "search": "qdrant_local_exact",
    }
    print(
        f"{report['dataset_name']}: {report['question_count']} questions, "
        f"{report['scored_count']} scored"
    )
    for k, metrics in report["summary"].items():
        print(
            f"Top-{k}: Recall={metrics['recall']:.4f}  MRR={metrics['mrr']:.4f}  "
            f"conflicts={metrics['all_conflicting_versions_hit']}/{report['conflict_count']}"
        )

    if args.output:
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
