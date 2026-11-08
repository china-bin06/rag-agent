import copy
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "storage/parsed/structured_blocks.jsonl"
OUTPUT_PATH = ROOT / "storage/parsed/normalized_blocks.jsonl"

# 原始解析和结构文件保留，这些分类仅从规范化产物中排除。
EXCLUDED_TYPES = {"header", "footer", "note"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def merge_units(blocks: list[dict]) -> list[dict]:
    """同一版本内合并条款、续条、附则和表格区域。"""
    units = []
    current = None
    last_clause = None

    def new_unit(block: dict, kind: str) -> dict:
        unit = {
            "kind": kind,
            "parts": [block],
        }
        units.append(unit)
        return unit

    for block in blocks:
        metadata = block["metadata"]
        kind = metadata["structure_type"]

        if kind in EXCLUDED_TYPES:
            continue

        if kind == "heading":
            # 章节标题作为metadata上下文保留，不单独入片。
            current = None

            if (metadata["section"] or "").startswith("附则"):
                last_clause = None

        elif kind == "clause":
            current = new_unit(block, "clause")
            last_clause = current

        elif kind == "continuation":
            # 允许跨章节承接，但必须承接最近的同编号主条款。
            require(
                last_clause is not None
                and last_clause["parts"][0]["metadata"]["clause_number"]
                == metadata["clause_number"],
                "发现无法关联主条款的续条",
            )
            current = last_clause
            current["parts"].append(block)

        elif kind == "clause_body":
            require(
                current is not None
                and current["kind"] == "clause"
                and current["parts"][0]["metadata"]["clause_number"]
                == metadata["clause_number"],
                "发现无法关联主条款的正文",
            )
            current["parts"].append(block)

        elif kind == "text":
            # 当前数据的无编号正文均属于附则。
            # 新出现其他类型时停止，不静默丢弃。
            require(
                (metadata["section"] or "").startswith("附则"),
                "发现未处理的无编号正文",
            )

            if (
                current is None
                or current["kind"] != "appendix"
                or current["parts"][0]["metadata"]["section"]
                != metadata["section"]
            ):
                current = new_unit(block, "appendix")
            else:
                current["parts"].append(block)

            last_clause = None

        elif kind == "table_title":
            current = new_unit(block, "table")
            last_clause = None

        elif kind in {"table", "table_line", "table_body"}:
            require(
                current is not None
                and current["kind"] == "table",
                "发现没有表格标题或范围起点的表格内容",
            )
            current["parts"].append(block)

        else:
            raise ValueError(f"尚未处理的结构类型：{kind}")

    require(
        sum(unit["kind"] == "clause" for unit in units)
        == sum(
            block["metadata"]["structure_type"] == "clause"
            for block in blocks
        ),
        "合并前后主条款数量不一致",
    )

    return units


def summarize_locator(spans: list[dict]) -> dict:
    """
    生成供展示使用的位置摘要。
    精确引用仍以各个source_span为准。
    """
    def values(key: str) -> list[int]:
        return [
            span["locator"][key]
            for span in spans
            if span["locator"].get(key) is not None
        ]

    def minimum(key: str):
        items = values(key)
        return min(items) if items else None

    def maximum(key: str):
        items = values(key)
        return max(items) if items else None

    def single(key: str):
        items = values(key)
        if len(items) == len(spans) and len(set(items)) == 1:
            return items[0]
        return None

    return {
        "page_start": minimum("page_start"),
        "page_end": maximum("page_end"),
        "paragraph_index": single("paragraph_index"),
        "paragraph_start": minimum("paragraph_index"),
        "paragraph_end": maximum("paragraph_index"),
        "line_start": minimum("line_start"),
        "line_end": maximum("line_end"),
        "table_index": single("table_index"),
    }


def finalize_unit(unit: dict, index: int) -> dict:
    parts = unit["parts"]
    metadata = copy.deepcopy(parts[0]["metadata"])

    spans = []
    sections = []
    tables = []

    for part in parts:
        part_metadata = part["metadata"]
        section = part_metadata["section"]

        if section is not None and section not in sections:
            sections.append(section)

        for source_span in part_metadata["source_spans"]:
            span = copy.deepcopy(source_span)
            span["section"] = section
            span["structure_block_index"] = part_metadata["block_index"]
            span["structure_type"] = part_metadata["structure_type"]
            spans.append(span)

        if part_metadata["table_rows"] is not None:
            tables.append(
                copy.deepcopy(part_metadata["table_rows"])
            )

    require(
        len(tables) <= 1,
        "一个表格区域包含多个结构化表格，需要单独处理",
    )

    is_table = unit["kind"] == "table"

    metadata.update(
        {
            "schema_version": 3,
            "normalization_version": "1",
            "block_index": index,
            "type": "table" if is_table else "text",
            "structure_type": unit["kind"],
            # 主条款仍使用其起始章节，所有实际章节另行保留。
            "section": sections[0] if sections else None,
            "sections": sections,
            "clause_number": (
                metadata["clause_number"]
                if unit["kind"] == "clause"
                else None
            ),
            "locator_type": "source_spans",
            "locator": summarize_locator(spans),
            "source_spans": spans,
            "table_rows": tables[0] if tables else None,
            "table_status": (
                "structured"
                if tables
                else "pending_structure"
            ) if is_table else None,
        }
    )

    return {
        # 不删除“第四条续”等原文标记，保留引文完整性。
        "content": "\n".join(
            part["content"]
            for part in parts
        ),
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

    require(bool(blocks), "结构识别文件为空")

    groups = defaultdict(list)

    for block in blocks:
        metadata = block["metadata"]
        require(
            metadata["schema_version"] == 2,
            "输入必须是第二版结构识别结果",
        )
        groups[metadata["version_id"]].append(block)

    results = []

    for version_id, source_blocks in groups.items():
        require(
            [
                block["metadata"]["block_index"]
                for block in source_blocks
            ] == list(range(len(source_blocks))),
            f"结构片段顺序或编号异常：{version_id}",
        )

        identities = {
            (
                block["metadata"]["document_id"],
                block["metadata"]["source_path"],
                block["metadata"]["source_sha256"],
            )
            for block in source_blocks
        }
        require(
            len(identities) == 1,
            f"版本内来源信息不一致：{version_id}",
        )

        units = merge_units(source_blocks)
        normalized = [
            finalize_unit(unit, index)
            for index, unit in enumerate(units)
        ]
        results.extend(normalized)

        print(
            f"合并完成：{version_id}，"
            f"结构片段={len(source_blocks)}，"
            f"规范化单元={len(normalized)}"
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
    table_counts = Counter(
        block["metadata"]["table_status"]
        for block in results
        if block["metadata"]["type"] == "table"
    )

    print(f"\n处理版本数：{len(groups)}")
    print(f"规范化单元总数：{len(results)}")
    print(f"内容统计：{dict(counts)}")
    print(f"表格状态：{dict(table_counts)}")
    print(f"输出位置：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()