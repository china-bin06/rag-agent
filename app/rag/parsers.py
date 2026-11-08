from importlib.metadata import version as package_version
from pathlib import Path

from docx import Document as open_docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader


PIPELINE_VERSION = "1"

PARSER_PACKAGES = {
    "pdf": "pypdf",
    "docx": "python-docx",
    "md": None,
}


def make_block(
    content: str,
    locator_type: str,
    block_type: str = "text",
    table_rows: list[list[str]] | None = None,
    **positions,
) -> dict:
    """构造统一文本块，无法取得的位置保留为None。"""
    locator = {
        "page_start": None,
        "page_end": None,
        "paragraph_index": None,
        "line_start": None,
        "line_end": None,
        "table_index": None,
    }
    locator.update(positions)

    return {
        "content": content,
        "metadata": {
            "type": block_type,
            "section": None,
            "clause_number": None,
            "locator_type": locator_type,
            "locator": locator,
            "table_rows": table_rows,
        },
    }


def parse_pdf(path: Path) -> list[dict]:
    reader = PdfReader(path)
    blocks = []

    for page_number, page in enumerate(reader.pages, start=1):
        # 保留页级文本；此处尚不识别独立表格或章节。
        content = page.extract_text() or ""

        if not content.strip():
            raise ValueError(
                f"PDF页面没有可提取文本：{path.name}，"
                f"第{page_number}页"
            )

        blocks.append(
            make_block(
                content=content,
                locator_type="pdf_page",
                page_start=page_number,
                page_end=page_number,
            )
        )

    return blocks


def parse_docx(path: Path) -> list[dict]:
    document = open_docx(path)
    blocks = []

    paragraph_index = 0
    table_index = 0

    # 按原文顺序交替读取段落和表格。
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            # 空段落也参与编号，与document.paragraphs保持一致。
            paragraph_index += 1

            if not item.text.strip():
                continue

            blocks.append(
                make_block(
                    content=item.text,
                    locator_type="docx_paragraph",
                    paragraph_index=paragraph_index,
                )
            )

        elif isinstance(item, Table):
            table_index += 1
            rows = [
                [cell.text for cell in row.cells]
                for row in item.rows
            ]

            if not any(
                cell.strip()
                for row in rows
                for cell in row
            ):
                continue

            content = "\n".join(
                "\t".join(row)
                for row in rows
            )

            blocks.append(
                make_block(
                    content=content,
                    locator_type="docx_table",
                    block_type="table",
                    table_rows=rows,
                    table_index=table_index,
                )
            )

    return blocks


def parse_markdown(path: Path) -> list[dict]:
    blocks = []
    lines = path.read_text(
        encoding="utf-8-sig"
    ).splitlines()

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue

        blocks.append(
            make_block(
                content=line,
                locator_type="markdown_line",
                line_start=line_number,
                line_end=line_number,
            )
        )

    return blocks


PARSERS = {
    "pdf": parse_pdf,
    "docx": parse_docx,
    "md": parse_markdown,
}


def parse_document(path: Path, source: dict) -> list[dict]:
    """将单个版本解析为统一的content + metadata结构。"""
    source_format = source["source_format"]
    parser = PARSERS.get(source_format)

    if parser is None:
        raise ValueError(f"不支持的文件格式：{source_format}")

    blocks = parser(path)

    if not blocks:
        raise ValueError(f"未解析出正文：{path.name}")

    if (
        source_format == "pdf"
        and len(blocks) != source["page_count"]
    ):
        raise ValueError(
            f"PDF页数与清单不一致：{source['version_id']}"
        )

    package = PARSER_PACKAGES[source_format]
    parser_info = {
        "name": package or "python-text",
        "version": package_version(package) if package else "1",
        "pipeline_version": PIPELINE_VERSION,
    }

    for index, block in enumerate(blocks):
        block["metadata"].update(
            {
                "schema_version": 1,
                "document_id": source["document_id"],
                "version_id": source["version_id"],
                "title": source["title"],
                "version": source["version"],
                "source_path": source["source_path"],
                "source_format": source_format,
                "source_sha256": source["sha256"],
                "effective_from": source["effective_from"],
                "effective_to": source["effective_to"],
                "block_index": index,
                "parser": dict(parser_info),
            }
        )

    return blocks