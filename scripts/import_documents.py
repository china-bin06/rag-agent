import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.db.database import SessionLocal
from app.models import Department, Document, DocumentVersion, Role


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config/documents.jsonl"
ACL_PATH = ROOT / "config/document_acl.json"

DATE_FIELDS = (
    "effective_from",
    "effective_to",
    "published_at",
    "updated_at",
)

VERSION_FIELDS = (
    "version",
    "source_path",
    "source_format",
    "source_status",
    "effective_from",
    "effective_to",
    "published_at",
    "updated_at",
    "sha256",
    "size_bytes",
    "page_count",
    "locator_type",
    "preview_pdf_path",
    "preview_status",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def resolve_path(relative_path: str) -> Path:
    """配置中的路径统一相对于项目根目录。"""
    require(
        isinstance(relative_path, str) and bool(relative_path.strip()),
        "文件路径必须是非空字符串",
    )
    require(
        not Path(relative_path).is_absolute(),
        f"必须使用相对路径：{relative_path}",
    )

    path = (ROOT / relative_path).resolve()
    require(
        path.is_relative_to(ROOT),
        f"路径超出项目目录：{relative_path}",
    )
    return path


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_and_validate() -> tuple[list[dict], dict]:
    """读取配置并完成校验，不写入数据库。"""
    rows = [
        json.loads(line)
        for line in MANIFEST_PATH.read_text(
            encoding="utf-8-sig"
        ).splitlines()
        if line.strip()
    ]
    acl = json.loads(
        ACL_PATH.read_text(encoding="utf-8-sig")
    )

    require(bool(rows), "文档清单为空")
    require(
        acl["schema_version"] == 1,
        "不支持的ACL格式版本",
    )
    require(
        acl["path_base"] == "project_root",
        "不支持的路径基准",
    )
    require(
        acl["default_effect"] == "deny",
        "默认权限必须为拒绝",
    )
    require(
        acl["inactive_user"] == "deny_all",
        "停用用户必须拒绝访问",
    )
    require(
        acl["policy_scope"]
        == "logical_document_current_acl_applies_to_all_versions",
        "不支持的文档授权作用域",
    )

    version_ids = set()
    version_keys = set()
    groups = defaultdict(list)

    for row in rows:
        version_id = row["version_id"]
        document_id = row["document_id"]
        version_key = (document_id, row["version"])

        require(
            version_id not in version_ids,
            f"版本ID重复：{version_id}",
        )
        require(
            version_key not in version_keys,
            f"制度版本重复：{version_key}",
        )
        version_ids.add(version_id)
        version_keys.add(version_key)

        path = resolve_path(row["source_path"])
        require(
            path.is_file(),
            f"源文件不存在：{path}",
        )
        require(
            file_hash(path) == row["sha256"],
            f"源文件哈希不一致：{version_id}",
        )
        require(
            path.stat().st_size == row["size_bytes"],
            f"源文件大小不一致：{version_id}",
        )
        require(
            path.suffix.lower().lstrip(".") == row["source_format"],
            f"源格式与扩展名不一致：{version_id}",
        )
        require(
            row["source_format"] in {"pdf", "docx", "md"},
            f"不支持的源格式：{version_id}",
        )
        require(
            row["source_status"] in {"current", "archived"},
            f"不支持的来源状态：{version_id}",
        )

        if row["preview_pdf_path"] is not None:
            preview_path = resolve_path(row["preview_pdf_path"])
            require(
                preview_path.is_file(),
                f"预览文件不存在：{version_id}",
            )

        # SQLAlchemy Date字段需要Python date对象。
        for field in DATE_FIELDS:
            value = row[field]
            row[field] = (
                date.fromisoformat(value)
                if value is not None
                else None
            )

        start = row["effective_from"]
        end = row["effective_to"]

        require(
            start is not None,
            f"缺少生效日期：{version_id}",
        )
        require(
            end is None or start < end,
            f"版本有效期不合法：{version_id}",
        )

        groups[document_id].append(row)

    require(
        set(groups) == set(acl["documents"]),
        "制度清单与ACL覆盖范围不一致",
    )

    for document_id, versions in groups.items():
        require(
            len(
                {
                    (version["title"], version["category"])
                    for version in versions
                }
            ) == 1,
            f"同一制度的标题或分类不一致：{document_id}",
        )

        versions.sort(
            key=lambda version: version["effective_from"]
        )

        for previous, current in zip(versions, versions[1:]):
            require(
                previous["effective_to"] is not None
                and previous["effective_to"]
                <= current["effective_from"],
                f"版本有效期重叠：{document_id}",
            )

    for document_id, rule in acl["documents"].items():
        require(
            rule["access_policy"] == "role_or_department",
            f"不支持的授权策略：{document_id}",
        )
        require(
            rule["acl_revision"] >= 1,
            f"授权修订号不合法：{document_id}",
        )

        for field, valid_field in (
            ("allowed_roles", "valid_roles"),
            ("allowed_departments", "valid_departments"),
        ):
            values = rule[field]

            require(
                isinstance(values, list)
                and all(isinstance(value, str) for value in values),
                f"授权列表必须由字符串组成：{document_id}/{field}",
            )
            require(
                len(values) == len(set(values)),
                f"授权列表重复：{document_id}/{field}",
            )
            require(
                set(values) <= set(acl[valid_field]),
                f"授权代码不合法：{document_id}/{field}",
            )

        # 权限来源是当前项目的显式配置，不读取外部依据文件。
        basis = rule.get("basis")

        require(
            isinstance(basis, dict),
            f"权限依据说明必须是对象：{document_id}",
        )
        require(
            basis.get("type") == "project_configuration",
            f"权限依据类型不支持：{document_id}",
        )
        require(
            isinstance(basis.get("description"), str)
            and bool(basis["description"].strip()),
            f"缺少权限配置说明：{document_id}",
        )

    return rows, acl


def main() -> None:
    rows, acl = load_and_validate()

    groups = defaultdict(list)
    for row in rows:
        groups[row["document_id"]].append(row)

    created_documents = 0
    created_versions = 0

    # 正常退出时提交；任一步失败时整批回滚。
    with SessionLocal.begin() as db:
        roles = {
            role.code: role
            for role in db.scalars(select(Role)).all()
        }
        departments = {
            department.code: department
            for department in db.scalars(
                select(Department)
            ).all()
        }

        require(
            set(acl["valid_roles"]) <= set(roles),
            "数据库缺少角色，请先运行身份初始化脚本",
        )
        require(
            set(acl["valid_departments"]) <= set(departments),
            "数据库缺少部门，请先运行身份初始化脚本",
        )

        for document_id, versions in groups.items():
            rule = acl["documents"][document_id]
            first = versions[0]
            document = db.get(Document, document_id)

            if document is None:
                document = Document(id=document_id)
                db.add(document)
                created_documents += 1
            else:
                # 不自动删除清单之外的已有版本。
                # 若同一制度存在额外版本，先停止并要求核对。
                incoming_ids = {
                    version["version_id"]
                    for version in versions
                }
                existing_ids = set(
                    db.scalars(
                        select(DocumentVersion.id).where(
                            DocumentVersion.document_id
                            == document_id
                        )
                    ).all()
                )

                require(
                    existing_ids <= incoming_ids,
                    f"{document_id}存在清单未包含的数据库版本，"
                    "请先核对",
                )

            document.title = first["title"]
            document.category = first["category"]
            document.access_policy = rule["access_policy"]
            document.acl_revision = rule["acl_revision"]
            document.source_scope = rule["source_scope"]
            document.acl_basis = rule["basis"]

            # 替换为当前配置，重复执行不会重复追加。
            document.allowed_roles = [
                roles[code]
                for code in rule["allowed_roles"]
            ]
            document.allowed_departments = [
                departments[code]
                for code in rule["allowed_departments"]
            ]

            # 当前SessionLocal关闭了autoflush，显式写入父记录。
            db.flush()

            for row in versions:
                version_id = row["version_id"]
                version = db.get(DocumentVersion, version_id)

                if version is None:
                    version = DocumentVersion(
                        id=version_id,
                        document_id=document_id,
                    )
                    db.add(version)
                    created_versions += 1
                else:
                    require(
                        version.document_id == document_id,
                        f"版本所属制度发生变化：{version_id}",
                    )
                    require(
                        version.sha256 == row["sha256"],
                        f"已有版本内容发生变化：{version_id}，"
                        "请先明确版本更新策略",
                    )

                for field in VERSION_FIELDS:
                    setattr(version, field, row[field])

            db.flush()

    # 只有成功提交后才输出导入成功。
    print(
        f"导入完成：同步{len(groups)}项制度、{len(rows)}个版本；"
        f"新增{created_documents}项制度、{created_versions}个版本"
    )


if __name__ == "__main__":
    main()