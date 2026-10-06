from pathlib import Path
import uuid

from sqlalchemy import create_engine, event, inspect, text
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


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db() -> None:
    # ponytail: 原型阶段 SQLite 直接建表；数据规模或多人迁移时换 PostgreSQL + Alembic。
    Base.metadata.create_all(engine)
    if settings.database_url.startswith("sqlite"):
        case_columns = {column["name"] for column in inspect(engine).get_columns("cases")}
        case_additions = {
            "pinned": "BOOLEAN NOT NULL DEFAULT 0",
            "archived": "BOOLEAN NOT NULL DEFAULT 0",
            "section": "VARCHAR(40) NOT NULL DEFAULT '默认'",
            "category": "VARCHAR(80) NOT NULL DEFAULT ''",
            "handler": "VARCHAR(80) NOT NULL DEFAULT ''",
            "unit": "VARCHAR(120) NOT NULL DEFAULT ''",
            "summary": "TEXT NOT NULL DEFAULT ''",
            "keywords": "TEXT NOT NULL DEFAULT ''",
            "notes": "TEXT NOT NULL DEFAULT ''",
        }
        with engine.begin() as connection:
            for name, definition in case_additions.items():
                if name not in case_columns:
                    connection.execute(text(f"ALTER TABLE cases ADD COLUMN {name} {definition}"))
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
            "analysis_enabled": "BOOLEAN NOT NULL DEFAULT 0",
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
            _migrate_material_scope(connection)
            _migrate_person_profile(connection)


def _sqlite_columns(connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))}


def _migrate_material_scope(connection) -> None:
    source_columns = _sqlite_columns(connection, "source_files")
    if "conversation_id" not in source_columns:
        connection.execute(text("ALTER TABLE source_files ADD COLUMN conversation_id CHAR(32)"))
    person_columns = _sqlite_columns(connection, "persons")
    if "conversation_id" not in person_columns:
        connection.execute(text("ALTER TABLE persons ADD COLUMN conversation_id CHAR(32)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_source_files_conversation_id ON source_files (conversation_id)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_persons_conversation_id ON persons (conversation_id)"))
    ddl = connection.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='source_files'")).scalar() or ""
    compact = "".join(ddl.split()).lower()
    if "unique(case_id,sha256)" not in compact:
        return
    connection.execute(text("PRAGMA foreign_keys=OFF"))
    connection.execute(text(
        """
        CREATE TABLE source_files_scoped (
            id CHAR(32) NOT NULL PRIMARY KEY,
            case_id CHAR(32) NOT NULL,
            conversation_id CHAR(32),
            filename VARCHAR(255) NOT NULL,
            sha256 VARCHAR(64) NOT NULL,
            stored_path TEXT NOT NULL,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            FOREIGN KEY(case_id) REFERENCES cases (id) ON DELETE CASCADE
        )
        """
    ))
    connection.execute(text(
        """
        INSERT INTO source_files_scoped (id, case_id, conversation_id, filename, sha256, stored_path, imported_at)
        SELECT id, case_id, conversation_id, filename, sha256, stored_path, imported_at FROM source_files
        """
    ))
    connection.execute(text("DROP TABLE source_files"))
    connection.execute(text("ALTER TABLE source_files_scoped RENAME TO source_files"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_source_files_case_id ON source_files (case_id)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_source_files_conversation_id ON source_files (conversation_id)"))
    connection.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_person_profile(connection) -> None:
    if "persons" not in {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}:
        return
    person_columns = _sqlite_columns(connection, "persons")
    additions = {
        "wechat_alias": "VARCHAR(120)",
        "original_wechat_id": "VARCHAR(120)",
        "takeout_address": "TEXT",
    }
    for name, definition in additions.items():
        if name not in person_columns:
            connection.execute(text(f"ALTER TABLE persons ADD COLUMN {name} {definition}"))


def get_session():
    with SessionLocal() as session:
        yield session
