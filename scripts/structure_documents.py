import copy
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "storage/parsed/blocks.jsonl"
OUTPUT_PATH = ROOT / "storage/parsed/structured_blocks.jsonl"

CHAPTER_RE = re.compile(
    r"^第[一二三四五六七八九十百零〇\d]+章(?:\s|[：:]|$)"
)
CLAUSE_RE = re.compile(
    r"^第(?P<number>[一二三四五六七八九十百零〇\d]+)条"
    r"(?P<continuation>续)?"
)
TABLE_TITLE_RE = re.compile(r"^执行标准对照表\b")
FOOTER_RE = re.compile(
    r"^[A-Z]+-\d+-v\d+\s*\|.*第\s*\d+\s*页"
)

DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
UNITS = {"十": 10, "百": 100}


def parse_number(text: str) -> int:
    if text.isdigit():
        return int(text)

    total = 0
    current = 0

    for character in text:
        if character in DIGITS:
            current = DIGITS[character]
        elif character in UNITS:
            total += (current or 1) * UNITS[character]
            current = 0
        else:
            raise ValueError(f"不支持的条款编号：{text}")

    return total + current


def iter_fragments(block: dict):
    """
    PDF按非空文本行拆分。
    DOCX段落、DOCX表格、Markdown行保持原有粒度。

    start/end是原始content内的Python字符偏移：
    起点包含，终点不包含。
    """
    content = block["content"]
    metadata = block["metadata"]

    if metadata["locator_type"] == "pdf_page":
        for match in re.finditer(r"[^\r\n]+", content):
            if match.group().strip():
                yield match.group(), match.start(), match.end()
    elif content.strip():
        yield content, 0, len(content)


def classify(
    content: str,
    metadata: dict,
    state: dict,
) -> tuple[str, str | None, int | None]:
    """返回结构类型、章节、条款编号。"""
    text = re.sub(r"^#{1,6}\s+", "", content.strip())

    # 固定来源信息保留，但不继承业务条款编号。
    if text == metadata["title"]:
        return "header", None, None

    if text.startswith(("星云科技有限公司", "文件编号")):
        return "header", None, None

    if re.match(
        rf"^{re.escape(metadata['document_id'])}\s+V",
        text,
    ):
        return "header", None, None

    if FOOTER_RE.match(text):
        return "footer", None, None

    if CHAPTER_RE.match(text) or text.startswith("附则"):
        state["section"] = text
        state["clause_number"] = None
        state["in_table"] = False
        return "heading", text, None

    if TABLE_TITLE_RE.match(text):
        state["clause_number"] = None
        state["in_table"] = True
        return "table_title", state["section"], None

    if metadata["type"] == "table":
        state["clause_number"] = None
        state["in_table"] = True
        return "table", state["section"], None

    # 这些行属于数据声明，不能继续挂在前一条业务条款下面。
    if text.startswith(
        (
            "本制度为虚拟企业",
            "本制度仅为模拟数据",
        )
    ):
        state["clause_number"] = None
        state["in_table"] = False
        return "note", state["section"], None

    match = CLAUSE_RE.match(text)
    if match:
        number = parse_number(match.group("number"))
        state["clause_number"] = number
        state["in_table"] = False

        kind = (
            "continuation"
            if match.group("continuation")
            else "clause"
        )
        return kind, state["section"], number

    if text.startswith("|"):
        state["clause_number"] = None
        state["in_table"] = True
        return "table_line", state["section"], None

    if state["in_table"]:
        return "table_body", state["section"], None

    if state["clause_number"] is not None:
        return (
            "clause_body",
            state["section"],
            state["clause_number"],
        )

    return "text", state["section"], None


def structure_version(blocks: list[dict]) -> list[dict]:
    """只在同一版本内维护章节及条款状态。"""
    state = {
        "section": None,
        "clause_number": None,
        "in_table": False,
    }
    results = []

    for source_block in blocks:
        source_metadata = source_block["metadata"]

        for content, start, end in iter_fragments(source_block):
            metadata = copy.deepcopy(source_metadata)

            kind, section, clause_number = classify(
                content,
                source_metadata,
                state,
            )

            metadata.update(
                {
                    # 新增结构字段与来源映射，使用新结构版本。
                    "schema_version": 2,
                    "structure_version": "1",
                    "structure_type": kind,
                    "section": section,
                    "clause_number": clause_number,
                    "block_index": len(results),
                    "source_spans": [
                        {
                            "source_block_index":
                                source_metadata["block_index"],
                            "locator": copy.deepcopy(
                                source_metadata["locator"]
                            ),
                            "char_start": start,
                            "char_end": end,
                        }
                    ],
                }
            )

            results.append(
                {
                    "content": content,
                    "metadata": metadata,
                }
            )

    return results


def main() -> None:
    blocks = [
        json.loads(line)
        for line in INPUT_PATH.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    if not blocks:
        raise ValueError("基础解析文件为空")

    groups = defaultdict(list)

    for block in blocks:
        metadata = block["metadata"]

        if metadata["schema_version"] != 1:
            raise ValueError("输入必须是第一版基础解析结果")

        groups[metadata["version_id"]].append(block)

    results = []

    for version_id, source_blocks in groups.items():
        indexes = [
            block["metadata"]["block_index"]
            for block in source_blocks
        ]
        if indexes != list(range(len(source_blocks))):
            raise ValueError(
                f"基础文本块顺序或编号异常：{version_id}"
            )

        # 同一版本的来源信息必须一致。
        identities = {
            (
                block["metadata"]["document_id"],
                block["metadata"]["source_path"],
                block["metadata"]["source_sha256"],
            )
            for block in source_blocks
        }
        if len(identities) != 1:
            raise ValueError(
                f"版本内来源信息不一致：{version_id}"
            )

        structured = structure_version(source_blocks)

        # 验证每个片段都能精确回到原始文本块。
        for block in structured:
            span = block["metadata"]["source_spans"][0]
            original = source_blocks[
                span["source_block_index"]
            ]["content"]

            if (
                original[span["char_start"]:span["char_end"]]
                != block["content"]
            ):
                raise ValueError(
                    f"来源字符映射错误：{version_id}"
                )

        results.extend(structured)
        print(
            f"结构识别完成：{version_id}，"
            f"基础块={len(source_blocks)}，"
            f"结构片段={len(structured)}"
        )

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    temporary_path = OUTPUT_PATH.with_suffix(".jsonl.tmp")

    temporary_path.write_text(
        "\n".join(
            json.dumps(block, ensure_ascii=False)
            for block in results
        ) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(OUTPUT_PATH)

    counts = Counter(
        block["metadata"]["structure_type"]
        for block in results
    )

    print(f"\n处理版本数：{len(groups)}")
    print(f"结构片段总数：{len(results)}")
    print(f"结构类型统计：{dict(counts)}")
    print(f"输出位置：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()