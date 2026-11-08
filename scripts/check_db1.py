from sqlalchemy import text

from app.db.database import SessionLocal


def main() -> None:
    with SessionLocal() as db:
        result = db.execute(text("SELECT 1")).scalar_one()
        assert result == 1
        print("数据库会话验证通过")


if __name__ == "__main__":
    main()