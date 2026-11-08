from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.permissions import (
    can_manage_knowledge_base,
    can_read_document,
    load_permission_policy,
)
from app.db.database import SessionLocal
from app.models import Department, Document, Role, User


EXPECTED = {
    "U001": (9, False),
    "U002": (20, False),
    "U003": (15, False),
    "U004": (12, False),
    "U005": (13, False),
    "U006": (23, True),
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_test_user(
    department: str,
    roles: list[str],
    active: bool = True,
) -> User:
    """仅创建内存对象，不写入数据库。"""
    return User(
        id="permission-test",
        username="permission-test",
        password_hash="unused",
        is_active=active,
        department=Department(
            code=department,
            name=department,
        ),
        roles=[
            Role(code=code, name=code)
            for code in roles
        ],
    )


def main() -> None:
    policy = load_permission_policy()

    with SessionLocal() as db:
        users = {
            user.id: user
            for user in db.scalars(
                select(User).options(
                    selectinload(User.department),
                    selectinload(User.roles),
                )
            ).all()
        }

        documents = {
            document.id: document
            for document in db.scalars(
                select(Document).options(
                    selectinload(Document.allowed_roles),
                    selectinload(Document.allowed_departments),
                )
            ).all()
        }

        check(
            set(documents) == set(policy["documents"]),
            "数据库制度范围与ACL不一致",
        )

        # 逐项核对数据库中的文档授权，而不只检查可读数量。
        for document_id, document in documents.items():
            rule = policy["documents"][document_id]

            check(
                document.access_policy == rule["access_policy"],
                f"{document_id}授权策略不一致",
            )
            check(
                {role.code for role in document.allowed_roles}
                == set(rule["allowed_roles"]),
                f"{document_id}角色授权不一致",
            )
            check(
                {
                    department.code
                    for department in document.allowed_departments
                }
                == set(rule["allowed_departments"]),
                f"{document_id}部门授权不一致",
            )

        for user_id, (expected_count, expected_manage) in EXPECTED.items():
            check(user_id in users, f"用户未初始化：{user_id}")
            user = users[user_id]

            check(user.is_active, f"模拟用户未启用：{user_id}")

            readable = {
                document.id
                for document in documents.values()
                if can_read_document(user, document)
            }
            manageable = can_manage_knowledge_base(user)

            check(
                len(readable) == expected_count,
                f"{user_id}可读数量错误：{len(readable)}",
            )
            check(
                manageable == expected_manage,
                f"{user_id}管理权限错误",
            )

            print(
                f"{user_id} {user.username} "
                f"可读={len(readable)} "
                f"管理={'允许' if manageable else '拒绝'}"
            )

        # 同一个财务制度，不同用户产生不同授权结果。
        check(
            not can_read_document(users["U001"], documents["FIN-003"]),
            "普通员工不应读取FIN-003",
        )
        check(
            can_read_document(users["U003"], documents["FIN-003"]),
            "财务用户应能读取FIN-003",
        )

        # 停用优先于IT部门和管理员角色。
        inactive = make_test_user("it", ["it_admin"], active=False)
        check(
            not can_manage_knowledge_base(inactive),
            "停用用户不应拥有管理权限",
        )
        check(
            all(
                not can_read_document(inactive, document)
                for document in documents.values()
            ),
            "停用用户不应读取任何制度",
        )

        # 非IT部门的it_admin可管理，但没有全库阅读特权。
        role_admin = make_test_user("engineering", ["it_admin"])
        check(
            can_manage_knowledge_base(role_admin),
            "it_admin角色应拥有管理权限",
        )
        check(
            not can_read_document(role_admin, documents["HR-004"]),
            "仅it_admin角色不应获得HR-004阅读权限",
        )
        check(
            can_read_document(role_admin, documents["SEC-003"]),
            "it_admin应能读取显式授权的SEC-003",
        )

        # IT部门覆盖规则不要求同时拥有it_admin角色。
        it_user = make_test_user("it", ["employee"])
        check(
            can_manage_knowledge_base(it_user),
            "IT部门用户应拥有管理权限",
        )
        check(
            all(
                can_read_document(it_user, document)
                for document in documents.values()
            ),
            "IT部门用户应能读取全部制度",
        )

        # 验证普通部门授权，以及两维均空时拒绝。
        department_document = Document(
            id="permission-test-document",
            access_policy="role_or_department",
            allowed_roles=[],
            allowed_departments=[
                Department(code="finance", name="财务部")
            ],
        )
        check(
            can_read_document(users["U003"], department_document),
            "部门匹配时应允许阅读",
        )

        empty_document = Document(
            id="permission-test-empty",
            access_policy="role_or_department",
            allowed_roles=[],
            allowed_departments=[],
        )
        check(
            not can_read_document(users["U001"], empty_document),
            "普通用户不应读取无任何授权的制度",
        )

    print("权限验证全部通过")


if __name__ == "__main__":
    main()