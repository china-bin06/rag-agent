import argparse
import json
from pathlib import Path

from app.db.database import SessionLocal
from app.rag.keyword import B, K1, KeywordRanker
from app.rag.retrieval_eval import DEFAULT_RETRIEVAL_DATASET, evaluate_retrieval


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate keyword retrieval")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_RETRIEVAL_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with SessionLocal() as db:
        report = evaluate_retrieval(db, args.dataset, KeywordRanker(db))

    report["strategy"] = {
        "name": "keyword_bm25",
        "tokenizer": "han_bigram",
        "k1": K1,
        "b": B,
    }
    print(
        f"{report['dataset_name']}: {report['question_count']} questions, "
        f"{report['scored_count']} scored, "
        f"{report['access_denied_count']} explicit denials"
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
