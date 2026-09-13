from pathlib import Path
import uuid

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


if settings.database_url.startswith("sqlite:///"):
    Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    # ponytail: 原型阶段 SQLite 直接建表；数据规模或多人迁移时换 PostgreSQL + Alembic。
    Base.metadata.create_all(engine)
    if settings.database_url.startswith("sqlite"):
        case_columns = {column["name"] for column in inspect(engine).get_columns("cases")}
        if "archived" not in case_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE cases ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0"))
        columns = {column["name"] for column in inspect(engine).get_columns("analyses")}
        if "conversation_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE analyses ADD COLUMN conversation_id CHAR(32)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_analyses_conversation_id ON analyses (conversation_id)"))
        conversation_columns = {column["name"] for column in inspect(engine).get_columns("conversations")}
        additions = {
            "pinned": "BOOLEAN NOT NULL DEFAULT 0",
            "unread": "BOOLEAN NOT NULL DEFAULT 0",
            "archived": "BOOLEAN NOT NULL DEFAULT 0",
            "section": "VARCHAR(40) NOT NULL DEFAULT '默认'",
        }
        with engine.begin() as connection:
            for name, definition in additions.items():
                if name not in conversation_columns:
                    connection.execute(text(f"ALTER TABLE conversations ADD COLUMN {name} {definition}"))
        with engine.begin() as connection:
            case_ids = connection.execute(
                text("SELECT DISTINCT case_id FROM analyses WHERE conversation_id IS NULL")
            ).scalars().all()
            for case_id in case_ids:
                conversation_id = uuid.uuid4().hex
                connection.execute(
                    text("INSERT INTO conversations (id, case_id, title) VALUES (:id, :case_id, :title)"),
                    {"id": conversation_id, "case_id": case_id, "title": "历史研判"},
                )
                connection.execute(
                    text("UPDATE analyses SET conversation_id = :conversation_id WHERE case_id = :case_id AND conversation_id IS NULL"),
                    {"conversation_id": conversation_id, "case_id": case_id},
                )


def get_session():
    with SessionLocal() as session:
        yield session
