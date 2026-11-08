from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


# SQLite 配合 FastAPI 同步接口使用时，允许跨线程访问连接。
connect_args = {}
if make_url(settings.database_url).get_backend_name() == "sqlite":
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)

if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()
            
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Iterator[Session]:
    """为每次请求创建独立会话，并在请求结束后关闭。"""
    with SessionLocal() as session:
        yield session