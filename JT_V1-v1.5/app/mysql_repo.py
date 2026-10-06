"""案件人员读取。数据在 SQLite，保留 MysqlPerson 名称供研判逻辑和测试复用。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Person, PersonAccount, PersonRelation, WeChatFriend


@dataclass(frozen=True)
class WeChatAccount:
    wechat_id: str | None = None
    wechat_alias: str | None = None
    original_wechat_id: str | None = None
    nickname: str | None = None


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
    wechat_alias: str | None = None
    original_wechat_id: str | None = None
    takeout_address: str | None = None
    accounts: tuple[WeChatAccount, ...] = field(default_factory=tuple)
    person_id: str | None = None


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


def list_persons(session: Session, case_id, conversation_id=None) -> list[MysqlPerson]:
    statement = select(Person).where(Person.case_id == case_id)
    account_statement = select(PersonAccount).where(PersonAccount.case_id == case_id)
    if conversation_id:
        statement = statement.where(
            or_(Person.conversation_id.is_(None), Person.conversation_id == conversation_id)
        )
        account_statement = account_statement.where(
            or_(PersonAccount.conversation_id.is_(None), PersonAccount.conversation_id == conversation_id)
        )
    else:
        statement = statement.where(Person.conversation_id.is_(None))
        account_statement = account_statement.where(PersonAccount.conversation_id.is_(None))
    rows = session.scalars(statement.order_by(Person.name)).all()
    grouped: dict = defaultdict(list)
    for account in session.scalars(account_statement).all():
        grouped[account.person_id].append(WeChatAccount(
            wechat_id=account.wechat_id,
            wechat_alias=account.wechat_alias,
            original_wechat_id=account.original_wechat_id,
            nickname=account.nickname,
        ))
    result = []
    for row in rows:
        accounts = tuple(grouped.get(row.id) or [])
        if not accounts and (row.wechat_id or row.wechat_alias or row.original_wechat_id):
            accounts = (WeChatAccount(
                wechat_id=row.wechat_id,
                wechat_alias=row.wechat_alias,
                original_wechat_id=row.original_wechat_id,
                nickname=row.wx_nickname,
            ),)
        result.append(MysqlPerson(
            id_card=row.id_card,
            name=row.name,
            gender=row.gender or "未知",
            birth_date=row.birth_date,
            delivery_address=row.delivery_address,
            ip_address=row.ip_address,
            wechat_id=row.wechat_id,
            wx_nickname=row.wx_nickname,
            phone=row.phone,
            case_info=row.case_info,
            remark=row.remark,
            wechat_alias=row.wechat_alias,
            original_wechat_id=row.original_wechat_id,
            takeout_address=row.takeout_address,
            accounts=accounts,
            person_id=str(row.id),
        ))
    return result


def list_person_relations(session: Session, case_id, conversation_id=None) -> list[dict]:
    statement = select(PersonRelation).where(PersonRelation.case_id == case_id)
    if conversation_id:
        statement = statement.where(
            or_(PersonRelation.conversation_id.is_(None), PersonRelation.conversation_id == conversation_id)
        )
    else:
        statement = statement.where(PersonRelation.conversation_id.is_(None))
    return [
        {
            "source_name": row.source_name,
            "target_name": row.target_name,
            "relation": row.relation,
            "description": row.description,
            "removed": row.removed,
        }
        for row in session.scalars(statement).all()
    ]


def list_wechat_friends(session: Session, case_id, conversation_id=None) -> list[dict]:
    statement = select(WeChatFriend).where(WeChatFriend.case_id == case_id)
    if conversation_id:
        statement = statement.where(
            or_(WeChatFriend.conversation_id.is_(None), WeChatFriend.conversation_id == conversation_id)
        )
    else:
        statement = statement.where(WeChatFriend.conversation_id.is_(None))
    return [
        {
            "owner_name": row.owner_name,
            "owner_wechat_id": row.owner_wechat_id,
            "owner_alias": row.owner_alias,
            "friend_nickname": row.friend_nickname,
            "friend_remark": row.friend_remark,
            "friend_wechat_id": row.friend_wechat_id,
            "friend_alias": row.friend_alias,
            "friend_original_wechat_id": row.friend_original_wechat_id,
        }
        for row in session.scalars(statement).all()
    ]


def list_chats(*_args, **_kwargs) -> list[MysqlChat]:
    return []


def delete_case_materials(*_args, **_kwargs) -> dict[str, int]:
    return {}
