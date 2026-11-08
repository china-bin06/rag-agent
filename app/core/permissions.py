import json
from functools import lru_cache
from pathlib import Path

from app.models import Document, User


ROOT = Path(__file__).resolve().parents[2]
ACL_PATH = ROOT / "config/document_acl.json"


@lru_cache(maxsize=1)
def load_permission_policy() -> dict:
    policy = json.loads(
        ACL_PATH.read_text(encoding="utf-8-sig")
    )

    if (
        policy.get("default_effect") != "deny"
        or policy.get("inactive_user") != "deny_all"
        or policy.get("management_policy") != "role_or_department"
    ):
        raise ValueError("不支持的全局权限策略")

    for field, valid_field in (
        ("super_admin_departments", "valid_departments"),
        ("management_departments", "valid_departments"),
        ("management_roles", "valid_roles"),
    ):
        values = policy.get(field)
        if (
            not isinstance(values, list)
            or not all(isinstance(value, str) for value in values)
            or not set(values) <= set(policy[valid_field])
        ):
            raise ValueError(f"全局权限配置不合法：{field}")

    return policy


def can_manage_knowledge_base(user: User) -> bool:
    """知识库管理：启用，并且部门或角色满足配置。"""
    if not user.is_active:
        return False

    policy = load_permission_policy()
    role_codes = {role.code for role in user.roles}

    return (
        user.department.code in policy["management_departments"]
        or bool(role_codes & set(policy["management_roles"]))
    )


def can_read_document(user: User, document: Document) -> bool:
    """制度阅读：停用拒绝，IT部门覆盖，否则按文档授权。"""
    if not user.is_active:
        return False

    policy = load_permission_policy()

    if user.department.code in policy["super_admin_departments"]:
        return True

    # 未支持的策略默认拒绝。
    if document.access_policy != "role_or_department":
        return False

    user_roles = {role.code for role in user.roles}
    allowed_roles = {role.code for role in document.allowed_roles}
    allowed_departments = {
        department.code
        for department in document.allowed_departments
    }

    return (
        user.department.code in allowed_departments
        or bool(user_roles & allowed_roles)
    )