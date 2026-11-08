import copy
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "storage/parsed/normalized_blocks.jsonl"
OUTPUT_PATH = ROOT / "storage/parsed/cleaned_blocks.jsonl"

CLAUSE_START_RE = re.compile(
    r"^第[一二三四五六七八九十百零〇\d]+条"
)
SENTENCE_ENDINGS = set("。！？；：.!?;:")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def clean_pdf_line_breaks(content: str) -> tuple[str, list[int]]:
    """
    删除判断为PDF排版折行的换行符。

    保留：
    - 空行；
    - 句末、分号、冒号后的换行；
    - 新条款及续条前的换行；
    - 两侧均为英文或数字的模糊边界。

    返回清理后的正文，以及被删除换行符在输入正文中的位置。
    """
    removed_offsets = []

    for index, character in enumerate(content):
        if character != "\n":
            continue

        left = content[:index].rstrip()
        right = content[index + 1:].lstrip()

        if not left or not right:
            continue

        if (
            content[index - 1:index] == "\n"
            or content[index + 1:index + 2] == "\n"
        ):
            continue

        if left[-1] in SENTENCE_ENDINGS:
            continue

        if CLAUSE_START_RE.match(right):
            continue

        # 无法确认是英文单词、编号还是数值时，保留原换行。
        if (
            left[-1].isascii()
            and left[-1].isalnum()
            and right[0].isascii()
            and right[0].isalnum()
        ):
            continue

        removed_offsets.append(index)

    removed = set(removed_offsets)
    cleaned = "".join(
        character
        for index, character in enumerate(content)
        if index not in removed
    )

    return cleaned, removed_offsets


def restore_line_breaks(
    content: str,
    removed_offsets: list[int],
) -> str:
    """按输入位置顺序恢复换行，用于验证清理可逆。"""
    restored = content

    for offset in removed_offsets:
        restored = (
            restored[:offset]
            + "\n"
            + restored[offset:]
        )

    return restored


def main() -> None:
    blocks = [
        json.loads(line)
        for line in INPUT_PATH.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    require(bool(blocks), "规范化文件为空")

    results = []
    changed_units = 0
    removed_newlines = 0

    for source in blocks:
        source_metadata = source["metadata"]

        require(
            source_metadata["schema_version"] == 3,
            "输入必须是第三版规范化结果",
        )

        original = source["content"]
        cleaned = original
        offsets = []

        # 只处理PDF条款和附则；不改变表格、DOCX和Markdown。
        if (
            source_metadata["source_format"] == "pdf"
            and source_metadata["structure_type"]
            in {"clause", "appendix"}
        ):
            cleaned, offsets = clean_pdf_line_breaks(original)

        require(
            restore_line_breaks(cleaned, offsets) == original,
            "清理后无法恢复原文："
            f"{source_metadata['version_id']}/"
            f"{source_metadata['block_index']}",
        )
        require(
            re.sub(r"\s+", "", cleaned)
            == re.sub(r"\s+", "", original),
            "清理改变了非空白正文",
        )

        result = copy.deepcopy(source)
        result["content"] = cleaned
        result["metadata"].update(
            {
                "schema_version": 4,
                "text_cleanup": {
                    "version": "1",
                    "input_path":
                        "storage/parsed/normalized_blocks.jsonl",
                    "input_block_index":
                        source_metadata["block_index"],
                    "removed_newline_offsets": offsets,
                },
            }
        )
        results.append(result)

        if offsets:
            changed_units += 1
            removed_newlines += len(offsets)

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

    print(f"单元总数：{len(results)}")
    print(f"清理单元数：{changed_units}")
    print(f"删除排版换行：{removed_newlines}")
    print(f"输出位置：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()