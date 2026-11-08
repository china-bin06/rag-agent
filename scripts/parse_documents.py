import hashlib
import json
from collections import Counter
from pathlib import Path

from app.rag.parsers import parse_document


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config/documents.jsonl"
OUTPUT_PATH = ROOT / "storage/parsed/blocks.jsonl"


def resolve_source(relative_path: str) -> Path:
    if Path(relative_path).is_absolute():
        raise ValueError(
            f"源路径必须是相对路径：{relative_path}"
        )

    path = (ROOT / relative_path).resolve()

    if not path.is_relative_to(ROOT):
        raise ValueError(
            f"源路径超出项目目录：{relative_path}"
        )

    if not path.is_file():
        raise ValueError(
            f"源文件不存在：{relative_path}"
        )

    return path


def main() -> None:
    sources = [
        json.loads(line)
        for line in MANIFEST_PATH.read_text(
            encoding="utf-8-sig"
        ).splitlines()
        if line.strip()
    ]

    if not sources:
        raise ValueError("文档清单为空")

    all_blocks = []
    seen = set()
    type_counts = Counter()
    locator_counts = Counter()

    for source in sources:
        version_id = source["version_id"]

        if version_id in seen:
            raise ValueError(
                f"版本ID重复：{version_id}"
            )
        seen.add(version_id)

        path = resolve_source(source["source_path"])
        payload = path.read_bytes()

        if hashlib.sha256(payload).hexdigest() != source["sha256"]:
            raise ValueError(
                f"源文件哈希不一致：{version_id}"
            )

        if len(payload) != source["size_bytes"]:
            raise ValueError(
                f"源文件大小不一致：{version_id}"
            )

        if (
            path.suffix.lower().lstrip(".")
            != source["source_format"]
        ):
            raise ValueError(
                f"源格式与扩展名不一致：{version_id}"
            )

        blocks = parse_document(path, source)
        all_blocks.extend(blocks)

        type_counts.update(
            block["metadata"]["type"]
            for block in blocks
        )
        locator_counts.update(
            block["metadata"]["locator_type"]
            for block in blocks
        )

        print(
            f"解析成功：{version_id}，"
            f"文本块={len(blocks)}"
        )

    # 全部解析成功后再替换正式输出。
    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    temporary_path = OUTPUT_PATH.with_suffix(
        ".jsonl.tmp"
    )

    temporary_path.write_text(
        "\n".join(
            json.dumps(block, ensure_ascii=False)
            for block in all_blocks
        ) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(OUTPUT_PATH)

    print(f"\n解析完成：{len(sources)}个版本")
    print(f"文本块总数：{len(all_blocks)}")
    print(f"内容类型统计：{dict(type_counts)}")
    print(f"定位类型统计：{dict(locator_counts)}")
    print(f"输出位置：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()