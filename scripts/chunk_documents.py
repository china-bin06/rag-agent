import copy
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "storage/parsed/table_ready_blocks.jsonl"
OUTPUT_PATH = ROOT / "storage/chunks/chunks.jsonl"

# 按正文字符数计算，不是Token数，也不包含上下文前缀。
TARGET_BODY_CHARS = 200
MAX_BODY_CHARS = 400
CHUNKER_VERSION = "1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def group_blocks(blocks: list[dict]) -> list[list[dict]]:
    groups = []
    pending = []

    def flush() -> None:
        if pending:
            groups.append(list(pending))
            pending.clear()

    for block in blocks:
        metadata = block["metadata"]

        require(
            len(block["content"]) <= MAX_BODY_CHARS,
            "单个内容单元超长，需要另行设计拆分规则："
            f"{metadata['version_id']}/"
            f"{metadata['block_index']}",
        )

        # 表格、附则独立成片。
        if metadata["structure_type"] != "clause":
            flush()
            groups.append([block])
            continue

        if pending:
            first = pending[0]["metadata"]
            previous = pending[-1]["metadata"]
            body = "\n\n".join(
                item["content"] for item in pending
            )

            compatible = (
                metadata["version_id"] == first["version_id"]
                and metadata["section"] == first["section"]
                and metadata["clause_number"]
                == previous["clause_number"] + 1
            )

            if (
                not compatible
                or len(body) >= TARGET_BODY_CHARS
                or len(body) + 2 + len(block["content"])
                > MAX_BODY_CHARS
            ):
                flush()

        pending.append(block)

    flush()
    return groups


def build_chunk(group: list[dict], index: int) -> dict:
    first = group[0]["metadata"]
    source_kind = first["structure_type"]

    require(
        all(
            item["metadata"]["version_id"] == first["version_id"]
            and item["metadata"]["document_id"] == first["document_id"]
            and item["metadata"]["source_sha256"]
            == first["source_sha256"]
            and item["metadata"]["structure_type"] == source_kind
            for item in group
        ),
        "切片包含不兼容的来源单元",
    )

    section = first["section"]
    prefix = (
        f"制度：{first['title']}（V{first['version']}）\n"
        f"章节：{section or '未标注'}\n\n"
    )

    body = "\n\n".join(
        item["content"] for item in group
    )
    content = prefix + body

    source_units = []
    source_spans = []
    sections = []
    clause_numbers = []

    offset = len(prefix)

    for item in group:
        metadata = item["metadata"]
        end = offset + len(item["content"])

        source_units.append(
            {
                "block_index": metadata["block_index"],
                "structure_type": metadata["structure_type"],
                "section": metadata["section"],
                "clause_number": metadata["clause_number"],
                # 相对于本切片content，左闭右开。
                "content_start": offset,
                "content_end": end,
            }
        )

        require(
            content[offset:end] == item["content"],
            "切片正文位置映射错误",
        )

        for source_span in metadata["source_spans"]:
            span = copy.deepcopy(source_span)
            span["source_unit_index"] = metadata["block_index"]
            source_spans.append(span)

        for value in metadata["sections"]:
            if value not in sections:
                sections.append(value)

        number = metadata["clause_number"]
        if number is not None:
            clause_numbers.append(number)

        offset = end + 2

    # 同样的内容、来源及切片参数生成同样的ID。
    identity = {
        "version_id": first["version_id"],
        "source_sha256": first["source_sha256"],
        "source_unit_indexes": [
            item["metadata"]["block_index"]
            for item in group
        ],
        "content": content,
        "chunker_version": CHUNKER_VERSION,
        "target_body_chars": TARGET_BODY_CHARS,
        "max_body_chars": MAX_BODY_CHARS,
    }
    chunk_id = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    metadata = {
        "schema_version": 6,
        "chunk_id": chunk_id,
        "chunk_index": index,
        "chunk_type": (
            "clause_group"
            if source_kind == "clause"
            else source_kind
        ),
        "document_id": first["document_id"],
        "version_id": first["version_id"],
        "title": first["title"],
        "version": first["version"],
        "source_path": first["source_path"],
        "source_format": first["source_format"],
        "source_sha256": first["source_sha256"],
        "effective_from": first["effective_from"],
        "effective_to": first["effective_to"],
        "section": section,
        "sections": sections,
        "clause_numbers": clause_numbers,
        "locator_type": "source_spans",
        "source_spans": source_spans,
        "input_path": "storage/parsed/table_ready_blocks.jsonl",
        "source_units": source_units,
        "parser": copy.deepcopy(first["parser"]),
        "chunker": {
            "version": CHUNKER_VERSION,
            "target_body_chars": TARGET_BODY_CHARS,
            "max_body_chars": MAX_BODY_CHARS,
        },
        "table_rows": (
            copy.deepcopy(first["table_rows"])
            if source_kind == "table"
            else None
        ),
        "table_extraction": (
            copy.deepcopy(first.get("table_extraction"))
            if source_kind == "table"
            else None
        ),
    }

    return {
        "content": content,
        "metadata": metadata,
    }


def main() -> None:
    blocks = [
        json.loads(line)
        for line in INPUT_PATH.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    require(bool(blocks), "表格恢复文件为空")

    versions = defaultdict(list)

    for block in blocks:
        metadata = block["metadata"]

        require(
            metadata["schema_version"] == 5,
            "输入必须是第五版表格恢复结果",
        )
        require(
            metadata["structure_type"]
            in {"clause", "table", "appendix"},
            "发现不支持的内容类型",
        )

        if metadata["structure_type"] == "table":
            require(
                metadata["table_status"] == "structured",
                "存在尚未结构化的表格",
            )

        versions[metadata["version_id"]].append(block)

    chunks = []
    chunk_ids = set()

    for version_id, source_blocks in versions.items():
        require(
            [
                block["metadata"]["block_index"]
                for block in source_blocks
            ] == list(range(len(source_blocks))),
            f"来源单元顺序或编号异常：{version_id}",
        )

        groups = group_blocks(source_blocks)
        used_indexes = []

        for index, group in enumerate(groups):
            chunk = build_chunk(group, index)
            chunk_id = chunk["metadata"]["chunk_id"]

            require(
                chunk_id not in chunk_ids,
                "发现重复切片ID",
            )
            chunk_ids.add(chunk_id)

            used_indexes.extend(
                item["metadata"]["block_index"]
                for item in group
            )
            chunks.append(chunk)

        require(
            used_indexes == list(range(len(source_blocks))),
            f"来源单元遗漏、重复或顺序错误：{version_id}",
        )

        print(
            f"切片完成：{version_id}，"
            f"内容单元={len(source_blocks)}，"
            f"切片={len(groups)}"
        )

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    temporary_path = OUTPUT_PATH.with_suffix(".jsonl.tmp")

    temporary_path.write_text(
        "\n".join(
            json.dumps(chunk, ensure_ascii=False)
            for chunk in chunks
        ) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(OUTPUT_PATH)

    counts = Counter(
        chunk["metadata"]["chunk_type"]
        for chunk in chunks
    )

    print(f"\n版本数：{len(versions)}")
    print(f"切片总数：{len(chunks)}")
    print(f"切片类型：{dict(counts)}")
    print(f"输出位置：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()