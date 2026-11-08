from sqlalchemy import select

from app.core.permissions import can_manage_knowledge_base
from app.core.security import hash_password
from app.db.database import SessionLocal
from app.models import Department, Role, User


DEPARTMENTS = {
    "engineering": "研发部",
    "hr": "人力资源部",
    "finance": "财务部",
    "procurement": "采购部",
    "it": "IT部",
    "management": "管理层",
}

ROLES = {
    "employee": "普通员工",
    "manager": "管理人员",
    "finance": "财务人员",
    "hr": "人力资源人员",
    "procurement": "采购人员",
    "it_admin": "IT管理员",
}

# 用户ID、登录名、部门代码、角色代码
USERS = [
    ("U001", "employee01", "engineering", ["employee"]),
    ("U002", "manager01", "engineering", ["employee", "manager"]),
    ("U003", "finance01", "finance", ["employee", "finance"]),
    ("U004", "hr01", "hr", ["employee", "hr"]),
    ("U005", "purchase01", "procurement", ["employee", "procurement"]),
    ("U006", "admin01", "it", ["employee", "it_admin"]),
]

INITIAL_PASSWORD = "123456"


def main() -> None:
    summaries = []

    # 正常退出自动提交；发生异常自动回滚并关闭会话。
    with SessionLocal.begin() as db:
        departments = {}
        for code, name in DEPARTMENTS.items():
            department = db.scalar(
                select(Department).where(Department.code == code)
            )
            if department is None:
                department = Department(code=code, name=name)
                db.add(department)
            else:
                department.name = name

            departments[code] = department

        roles = {}
        for code, name in ROLES.items():
            role = db.scalar(
                select(Role).where(Role.code == code)
            )
            if role is None:
                role = Role(code=code, name=name)
                db.add(role)
            else:
                role.name = name

            roles[code] = role

        # 当前 autoflush=False，显式写入以获得部门和角色的ID。
        # flush 不会提交事务。
        db.flush()

        for user_id, username, department_code, role_codes in USERS:
            username_owner = db.scalar(
                select(User).where(User.username == username)
            )
            if username_owner is not None and username_owner.id != user_id:
                raise ValueError(
                    f"登录名 {username} 已被用户 {username_owner.id} 占用"
                )

            user = db.get(User, user_id)
            created = user is None

            if created:
                user = User(
                    id=user_id,
                    username=username,
                    password_hash=hash_password(INITIAL_PASSWORD),
                    department=departments[department_code],
                    is_active=True,
                )
                db.add(user)
            else:
                user.username = username
                user.department = departments[department_code]
                user.is_active = True

            # 替换为配置中的角色集合，避免重复追加。
            user.roles = [roles[code] for code in role_codes]
            db.flush()

            action = "新增" if created else "同步"
            management = (
                "允许" if can_manage_knowledge_base(user) else "拒绝"
            )
            summaries.append(
                f"{action} {user.id} {user.username} "
                f"部门={user.department.code} "
                f"角色={','.join(sorted(role_codes))} "
                f"知识库管理={management}"
            )

    # 事务提交成功后才输出成功信息。
    print("身份数据初始化完成")
    for summary in summaries:
        print(summary)


if __name__ == "__main__":
    main()