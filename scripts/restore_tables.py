import copy
import hashlib
import json
import re
from collections import Counter
from importlib.metadata import version
from pathlib import Path

import pdfplumber


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "storage/parsed/cleaned_blocks.jsonl"
OUTPUT_PATH = ROOT / "storage/parsed/table_ready_blocks.jsonl"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def resolve_source(metadata: dict) -> Path:
    relative_path = metadata["source_path"]

    require(
        not Path(relative_path).is_absolute(),
        f"必须使用相对路径：{relative_path}",
    )

    path = (ROOT / relative_path).resolve()

    require(
        path.is_relative_to(ROOT),
        f"源路径超出项目：{relative_path}",
    )
    require(path.is_file(), f"源文件不存在：{relative_path}")
    require(
        hashlib.sha256(path.read_bytes()).hexdigest()
        == metadata["source_sha256"],
        f"源文件哈希不一致：{relative_path}",
    )

    return path


def validate_rows(rows: list[list[str]]) -> None:
    require(
        isinstance(rows, list) and len(rows) >= 2,
        "表格至少需要表头和一行数据",
    )
    require(
        all(
            isinstance(row, list)
            and all(isinstance(cell, str) for cell in row)
            for row in rows
        ),
        "表格必须由字符串单元格组成",
    )

    width = len(rows[0])
    require(width >= 2, "表格至少需要两列")
    require(
        all(len(row) == width for row in rows),
        "表格各行列数不一致",
    )
    require(
        all(cell.strip() for cell in rows[0]),
        "表格存在空表头",
    )


def parse_markdown_table(content: str) -> list[list[str]]:
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip().startswith("|")
    ]

    require(len(lines) >= 3, "Markdown表格不完整")

    rows = []

    for line in lines:
        # 当前数据集不含转义竖线；出现时停止，避免误拆列。
        require(
            r"\|" not in line,
            "当前解析器暂不支持包含转义竖线的Markdown表格",
        )
        require(
            line.endswith("|"),
            "Markdown表格行缺少结束竖线",
        )
        rows.append(
            [
                cell.strip()
                for cell in line[1:-1].split("|")
            ]
        )

    require(
        len(rows[1]) == len(rows[0])
        and all(
            re.fullmatch(r":?-{3,}:?", cell)
            for cell in rows[1]
        ),
        "Markdown表格分隔行不合法",
    )

    # 分隔行属于Markdown语法，不是业务数据。
    result = [rows[0], *rows[2:]]
    validate_rows(result)
    return result


def extract_pdf_table(
    path: Path,
    block: dict,
) -> tuple[list[list[str]], dict]:
    metadata = block["metadata"]

    pages = {
        span["locator"]["page_start"]
        for span in metadata["source_spans"]
        if span["locator"]["page_start"] is not None
    }

    require(
        len(pages) == 1,
        "当前数据应为单页表格，跨页表格需要另外处理",
    )
    page_number = next(iter(pages))

    # 第一行是“执行标准对照表 T01”，其余为原表格文字。
    body = "\n".join(block["content"].splitlines()[1:])
    expected_text = normalize_text(body)

    matches = []

    with pdfplumber.open(path) as pdf:
        require(
            1 <= page_number <= len(pdf.pages),
            "表格页码超出PDF范围",
        )

        page = pdf.pages[page_number - 1]

        for table in page.find_tables():
            extracted = table.extract()

            if not extracted:
                continue

            # 不猜测合并单元格或缺失单元格。
            if any(
                not isinstance(cell, str)
                for row in extracted
                for cell in row
            ):
                continue

            rows = [
                [cell.strip() for cell in row]
                for row in extracted
            ]
            flattened = normalize_text(
                "".join(
                    cell
                    for row in rows
                    for cell in row
                )
            )

            # 必须与此前识别出的表格正文完全对应。
            if flattened == expected_text:
                validate_rows(rows)
                matches.append(
                    (rows, list(table.bbox))
                )

    require(
        len(matches) == 1,
        f"无法唯一匹配PDF表格：{metadata['version_id']}",
    )

    rows, bbox = matches[0]

    return rows, {
        "method": "pdfplumber",
        "version": version("pdfplumber"),
        "page": page_number,
        "bbox": bbox,
        "bbox_format": ["x0", "top", "x1", "bottom"],
        "coordinate_origin": "top_left",
        "coordinate_unit": "point",
    }


def render_table(title: str, rows: list[list[str]]) -> str:
    """统一渲染为Markdown表格，单元格原值另存于table_rows。"""
    def escape(cell: str) -> str:
        return (
            cell.replace("\\", "\\\\")
            .replace("|", r"\|")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "<br>")
        )

    def render_row(row: list[str]) -> str:
        return "| " + " | ".join(
            escape(cell) for cell in row
        ) + " |"

    lines = [
        title,
        render_row(rows[0]),
        render_row(["---"] * len(rows[0])),
        *[render_row(row) for row in rows[1:]],
    ]

    return "\n".join(lines)


def restore_table(block: dict) -> dict:
    result = copy.deepcopy(block)
    metadata = result["metadata"]
    source_format = metadata["source_format"]
    path = resolve_source(metadata)

    title = re.sub(
        r"^#{1,6}\s+",
        "",
        block["content"].splitlines()[0].strip(),
    )
    require(
        title.startswith("执行标准对照表"),
        "未找到预期的表格标题",
    )

    if source_format == "pdf":
        rows, extraction = extract_pdf_table(path, block)

    elif source_format == "md":
        rows = parse_markdown_table(block["content"])
        extraction = {
            "method": "markdown_pipe_table",
            "version": "1",
        }

    elif source_format == "docx":
        rows = copy.deepcopy(metadata["table_rows"])
        validate_rows(rows)
        extraction = {
            "method": "existing_docx_rows",
            "version": metadata["parser"]["version"],
        }

    else:
        raise ValueError(
            f"不支持的表格来源格式：{source_format}"
        )

    validate_rows(rows)

    result["content"] = render_table(title, rows)
    metadata.update(
        {
            "table_rows": rows,
            "table_status": "structured",
            "table_extraction": extraction,
            "table_input": {
                "path": "storage/parsed/cleaned_blocks.jsonl",
                "block_index": metadata["block_index"],
            },
        }
    )

    return result


def main() -> None:
    blocks = [
        json.loads(line)
        for line in INPUT_PATH.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    require(bool(blocks), "正文清理文件为空")

    results = []
    table_counts = Counter()
    seen = set()

    for block in blocks:
        metadata = block["metadata"]
        require(
            metadata["schema_version"] == 4,
            "输入必须是第四版正文清理结果",
        )

        key = (
            metadata["version_id"],
            metadata["block_index"],
        )
        require(key not in seen, f"单元标识重复：{key}")
        seen.add(key)

        if metadata["structure_type"] == "table":
            result = restore_table(block)
            table_counts[metadata["source_format"]] += 1
            print(
                f"表格恢复完成：{metadata['version_id']}，"
                f"行数={len(result['metadata']['table_rows'])}"
            )
        else:
            # 非表格正文不做任何修改。
            result = copy.deepcopy(block)

        result["metadata"]["schema_version"] = 5
        result["metadata"]["table_recovery_version"] = "1"
        results.append(result)

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

    print(f"\n单元总数：{len(results)}")
    print(f"结构化表格：{sum(table_counts.values())}")
    print(f"表格来源：{dict(table_counts)}")
    print(f"输出位置：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()