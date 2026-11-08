import hashlib
import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.db.database import SessionLocal
from app.models import DocumentChunk, DocumentVersion


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "storage/chunks/chunks.jsonl"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def calculate_chunk_id(block: dict) -> str:
    metadata = block["metadata"]
    chunker = metadata["chunker"]

    identity = {
        "version_id": metadata["version_id"],
        "source_sha256": metadata["source_sha256"],
        "source_unit_indexes": [
            unit["block_index"]
            for unit in metadata["source_units"]
        ],
        "content": block["content"],
        "chunker_version": chunker["version"],
        "target_body_chars": chunker["target_body_chars"],
        "max_body_chars": chunker["max_body_chars"],
    }

    return hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def load_and_validate() -> list[dict]:
    blocks = [
        json.loads(line)
        for line in INPUT_PATH.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    require(bool(blocks), "切片文件为空")

    ids = set()
    groups = defaultdict(list)

    for block in blocks:
        metadata = block["metadata"]

        require(
            metadata["schema_version"] == 6,
            "不支持的切片结构版本",
        )
        require(
            isinstance(block["content"], str)
            and bool(block["content"].strip()),
            "切片正文必须是非空字符串",
        )
        require(
            metadata["chunk_type"]
            in {"clause_group", "table", "appendix"},
            "不支持的切片类型",
        )
        require(
            isinstance(metadata["chunk_index"], int)
            and metadata["chunk_index"] >= 0,
            "切片序号不合法",
        )
        require(
            bool(metadata["source_units"])
            and bool(metadata["source_spans"]),
            "切片缺少来源映射",
        )
        require(
            calculate_chunk_id(block) == metadata["chunk_id"],
            "切片ID与内容、来源或参数不一致",
        )
        require(
            metadata["chunk_id"] not in ids,
            "切片ID重复",
        )

        ids.add(metadata["chunk_id"])
        groups[metadata["version_id"]].append(
            metadata["chunk_index"]
        )

    for version_id, indexes in groups.items():
        require(
            sorted(indexes) == list(range(len(indexes))),
            f"切片序号不连续或重复：{version_id}",
        )

    return blocks


def main() -> None:
    blocks = load_and_validate()
    incoming_ids = defaultdict(set)

    for block in blocks:
        metadata = block["metadata"]
        incoming_ids[metadata["version_id"]].add(
            metadata["chunk_id"]
        )

    created = 0
    existing_count = 0

    with SessionLocal.begin() as db:
        versions = {
            item.id: item
            for item in db.scalars(
                select(DocumentVersion).where(
                    DocumentVersion.id.in_(list(incoming_ids))
                )
            ).all()
        }

        require(
            set(versions) == set(incoming_ids),
            "部分制度版本尚未导入数据库",
        )

        # 先校验数据库版本与实际源文件。
        for version_id, document_version in versions.items():
            path = (ROOT / document_version.source_path).resolve()

            require(
                not Path(document_version.source_path).is_absolute()
                and path.is_relative_to(ROOT),
                f"源文件路径不合法：{version_id}",
            )
            require(
                path.is_file(),
                f"源文件不存在：{version_id}",
            )
            require(
                hashlib.sha256(path.read_bytes()).hexdigest()
                == document_version.sha256,
                f"源文件哈希不一致：{version_id}",
            )

        existing = {
            item.id: item
            for item in db.scalars(
                select(DocumentChunk).where(
                    DocumentChunk.version_id.in_(
                        list(incoming_ids)
                    )
                )
            ).all()
        }

        for item in existing.values():
            require(
                item.id in incoming_ids[item.version_id],
                f"{item.version_id}存在当前文件未包含的旧切片，"
                "请先确认切片更新策略",
            )

        for block in blocks:
            metadata = block["metadata"]
            version_id = metadata["version_id"]
            document_version = versions[version_id]

            require(
                metadata["document_id"]
                == document_version.document_id,
                f"切片所属制度不一致：{version_id}",
            )

            for key in ("source_path", "source_format", "version"):
                require(
                    metadata[key] == getattr(document_version, key),
                    f"切片与数据库版本不一致：{version_id}/{key}",
                )

            require(
                metadata["source_sha256"] == document_version.sha256,
                f"切片来源哈希不一致：{version_id}",
            )

            for key in ("effective_from", "effective_to"):
                value = getattr(document_version, key)
                expected = value.isoformat() if value else None
                require(
                    metadata[key] == expected,
                    f"切片有效日期不一致：{version_id}/{key}",
                )

            chunk_id = metadata["chunk_id"]
            chunk = existing.get(chunk_id)
            digest = content_hash(block["content"])

            if chunk is None:
                chunk = DocumentChunk(
                    id=chunk_id,
                    version_id=version_id,
                    chunk_index=metadata["chunk_index"],
                    chunk_type=metadata["chunk_type"],
                    content=block["content"],
                    content_sha256=digest,
                    chunk_metadata=metadata,
                )
                db.add(chunk)
                created += 1
            else:
                require(
                    chunk.version_id == version_id
                    and chunk.chunk_index == metadata["chunk_index"]
                    and chunk.chunk_type == metadata["chunk_type"]
                    and chunk.content == block["content"]
                    and chunk.content_sha256 == digest,
                    f"已有切片内容或身份发生变化：{chunk_id}",
                )
                chunk.chunk_metadata = metadata
                existing_count += 1

        db.flush()

    print(
        f"切片导入完成：{len(incoming_ids)}个版本，"
        f"共{len(blocks)}条；"
        f"新增{created}条，已有{existing_count}条"
    )


if __name__ == "__main__":
    main()