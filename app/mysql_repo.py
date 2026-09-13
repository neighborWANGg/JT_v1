"""读取 MySQL JT 库中的人员与群聊（由 import_to_mysql.py 导入）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv() -> None:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    for env_path in (WORKSPACE_ROOT / ".env", APP_ROOT / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class MysqlPerson:
    id_card: str
    name: str
    gender: str
    birth_date: str | None
    delivery_address: str | None
    ip_address: str | None
    wechat_id: str | None
    wx_nickname: str | None
    phone: str | None
    case_info: str | None
    remark: str | None


@dataclass(frozen=True)
class MysqlChat:
    message_id: str
    wechat_id: str
    sent_at: datetime | None
    group_id: str | None
    content: str
    ip_address: str | None
    embedding: list[float]
    source_file: str | None
    source_row: int | None


def _parse_wechat(raw) -> tuple[str | None, str | None, str | None]:
    if not raw:
        return None, None, None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return None, None, None
    if isinstance(data, dict):
        return data.get("wxid"), data.get("nickname"), data.get("phone")
    if isinstance(data, list) and data:
        return str(data[0]), None, None
    return None, None, None


def _connect():
    _load_dotenv()
    password = os.getenv("MYSQL_PASSWORD")
    if not password:
        raise RuntimeError("未配置 MYSQL_PASSWORD")
    import pymysql
    from pymysql.cursors import DictCursor

    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=password,
        database=os.getenv("MYSQL_DATABASE", "JT"),
        charset="utf8mb4",
        cursorclass=DictCursor,
    )


def resolve_mysql_case_number(case_number: str, case_name: str | None = None) -> str:
    """前端案件编号与导入时不一致时，按名称或库中唯一案件回退。"""
    wanted_number = (case_number or "").strip()
    wanted_name = (case_name or "").strip()
    numbers: list[str] = []
    try:
        conn = _connect()
    except Exception:
        return wanted_number
    try:
        with conn.cursor() as cursor:
            if wanted_number:
                cursor.execute(
                    "SELECT case_number FROM persons WHERE case_number = %s LIMIT 1",
                    (wanted_number,),
                )
                if cursor.fetchone():
                    return wanted_number
                cursor.execute(
                    "SELECT case_number FROM chat_messages WHERE case_number = %s LIMIT 1",
                    (wanted_number,),
                )
                if cursor.fetchone():
                    return wanted_number
            if wanted_name:
                cursor.execute(
                    "SELECT case_number FROM persons WHERE case_name = %s LIMIT 1",
                    (wanted_name,),
                )
                row = cursor.fetchone()
                if row:
                    return row["case_number"]
                cursor.execute(
                    "SELECT case_number FROM chat_messages WHERE case_name = %s LIMIT 1",
                    (wanted_name,),
                )
                row = cursor.fetchone()
                if row:
                    return row["case_number"]
            cursor.execute(
                """
                SELECT case_number FROM (
                    SELECT case_number FROM persons
                    UNION
                    SELECT case_number FROM chat_messages
                ) cases
                """
            )
            numbers = [row["case_number"] for row in cursor.fetchall() if row.get("case_number")]
    except Exception:
        return wanted_number
    finally:
        conn.close()
    unique = list(dict.fromkeys(numbers))
    if len(unique) == 1:
        return unique[0]
    return wanted_number


def list_persons(case_number: str, case_name: str | None = None) -> list[MysqlPerson]:
    resolved = resolve_mysql_case_number(case_number, case_name)
    try:
        conn = _connect()
    except Exception:
        return []
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id_card, name, gender, birth_date, delivery_address,
                       ip_address, wechat_info, case_info, remark
                FROM persons
                WHERE case_number = %s
                ORDER BY CASE WHEN remark IS NULL OR remark = '' THEN 0 ELSE 1 END, name
                """,
                (resolved,),
            )
            rows = cursor.fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    persons: list[MysqlPerson] = []
    for row in rows:
        wxid, nickname, phone = _parse_wechat(row.get("wechat_info"))
        birth = row.get("birth_date")
        persons.append(
            MysqlPerson(
                id_card=row["id_card"],
                name=row["name"],
                gender=row.get("gender") or "未知",
                birth_date=str(birth) if birth else None,
                delivery_address=row.get("delivery_address"),
                ip_address=row.get("ip_address"),
                wechat_id=wxid,
                wx_nickname=nickname,
                phone=phone,
                case_info=row.get("case_info"),
                remark=row.get("remark") or None,
            )
        )
    return persons


def list_chats(case_number: str, case_name: str | None = None) -> list[MysqlChat]:
    resolved = resolve_mysql_case_number(case_number, case_name)
    try:
        conn = _connect()
    except Exception:
        return []
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT message_id, wechat_id, sent_at, group_id, content,
                       ip_address, embedding, source_file, source_row
                FROM chat_messages
                WHERE case_number = %s
                ORDER BY sent_at, source_row
                """,
                (resolved,),
            )
            rows = cursor.fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    chats: list[MysqlChat] = []
    for row in rows:
        raw = row.get("embedding")
        if isinstance(raw, str):
            embedding = json.loads(raw)
        elif isinstance(raw, list):
            embedding = raw
        else:
            embedding = []
        chats.append(
            MysqlChat(
                message_id=row["message_id"],
                wechat_id=row["wechat_id"],
                sent_at=row.get("sent_at"),
                group_id=row.get("group_id"),
                content=row["content"],
                ip_address=row.get("ip_address"),
                embedding=[float(x) for x in embedding],
                source_file=row.get("source_file"),
                source_row=row.get("source_row"),
            )
        )
    return chats
