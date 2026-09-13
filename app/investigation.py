"""人员总结、关键词检索、群聊研判算法。"""

from __future__ import annotations

import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import settings
from .mysql_repo import MysqlChat, MysqlPerson, list_chats, list_persons
from .schemas import SearchHit

SELF_REMARKS = ("", "本人", "主体", "主要人员")


@dataclass
class ChatItem:
    message_id: str
    source_file_id: uuid.UUID
    source_row: int
    sent_at: datetime | None
    group_id: str | None
    sender_id: str | None
    content: str
    ip_address: str | None
    embedding: list[float] = field(default_factory=list)


@dataclass
class GroupProfile:
    group_id: str
    member_count: int
    members: list[str]
    message_count: int
    subject_speak_count: int
    related_count: int
    related_names: list[str]
    messages: list[dict] = field(default_factory=list)
    category: str = "待研判"
    reason: str = ""


def resolve_bge_path() -> Path | None:
    candidates = [
        Path(settings.bge_model_path),
        Path(__file__).resolve().parents[3] / "model" / "bge-m3",
        Path(__file__).resolve().parents[1] / "model" / "bge-m3",
    ]
    for path in candidates:
        if path.exists() and (path / "config.json").exists():
            return path
    return None


_bge_model = None


def encode_bge(texts: list[str]) -> list[list[float]]:
    global _bge_model
    path = resolve_bge_path()
    if path is None:
        raise FileNotFoundError("未找到本地 BGE-M3 模型")
    if _bge_model is None:
        from FlagEmbedding import BGEM3FlagModel

        kwargs = {"use_fp16": True, "query_max_length": 512}
        try:
            import torch

            if torch.cuda.is_available():
                kwargs["devices"] = "cuda:0"
        except ImportError:
            pass
        _bge_model = BGEM3FlagModel(str(path), **kwargs)
    output = _bge_model.encode(
        texts,
        batch_size=8,
        max_length=512,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    return output["dense_vecs"].tolist()


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    score = sum(a * b for a, b in zip(left, right, strict=True))
    norm_l = math.sqrt(sum(a * a for a in left)) or 1
    norm_r = math.sqrt(sum(b * b for b in right)) or 1
    return score / (norm_l * norm_r)


def is_subject(person: MysqlPerson) -> bool:
    remark = (person.remark or "").strip()
    return remark in SELF_REMARKS


def subjects_of(persons: list[MysqlPerson]) -> list[MysqlPerson]:
    found = [person for person in persons if is_subject(person)]
    return found or persons[:1]


def related_of(persons: list[MysqlPerson], subjects: list[MysqlPerson]) -> list[MysqlPerson]:
    subject_ids = {person.id_card for person in subjects}
    return [person for person in persons if person.id_card not in subject_ids]


def format_person(person: MysqlPerson, title: str) -> str:
    parts = [f"{title}{person.name}"]
    fields = [
        ("性别", person.gender),
        ("身份证号", person.id_card),
        ("出生日期", person.birth_date),
        ("微信号", person.wechat_id),
        ("微信昵称", person.wx_nickname),
        ("手机号", person.phone),
        ("地址", person.delivery_address),
        ("常用IP", person.ip_address),
        ("案件信息", person.case_info),
        ("关系备注", person.remark),
    ]
    details = [f"{label}：{value}" for label, value in fields if value]
    if details:
        parts.append("；".join(details))
    return "\n".join(parts)


def summarize_persons(persons: list[MysqlPerson]) -> tuple[list[str], list[MysqlPerson], list[MysqlPerson]]:
    if not persons:
        return ["数据库中没有本案人员信息。请确认已导入人员表，且案件编号与导入时一致。"], [], []
    subjects = subjects_of(persons)
    related = related_of(persons, subjects)
    lines = [format_person(person, "【本人】") for person in subjects]
    if related:
        lines.append(f"【关系人】共 {len(related)} 人")
        lines.extend(format_person(person, "【关系人】") for person in related)
    else:
        lines.append("【关系人】未登记其他关系人")
    return lines, subjects, related


def lexical_hit(question: str, content: str) -> bool:
    query = question.strip().lower()
    if not query:
        return False
    if query in content.lower():
        return True
    tokens = [token for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{2,}", query) if token]
    return any(token in content.lower() for token in tokens)


def search_keyword_items(question: str, items: list[ChatItem], min_score: float = 0.42) -> list[tuple[ChatItem, float]]:
    """BGE-M3 关键词/语义检索，命中即输出，不做 Top10 截断。"""
    query_vector: list[float] | None = None
    if settings.embedding_backend == "bge":
        try:
            query_vector = encode_bge([question])[0]
        except Exception:
            query_vector = None

    scored: list[tuple[ChatItem, float]] = []
    for item in items:
        exact = lexical_hit(question, item.content)
        similarity = cosine(query_vector, item.embedding) if query_vector and item.embedding else 0.0
        if exact or similarity >= min_score:
            score = max(similarity, 1.0 if exact and question.strip().lower() in item.content.lower() else similarity)
            if exact:
                score = max(score, 0.99 if question.strip().lower() in item.content.lower() else 0.7)
            scored.append((item, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def chats_to_items(chats: list[MysqlChat]) -> list[ChatItem]:
    items: list[ChatItem] = []
    for chat in chats:
        source_key = f"mysql:{chat.source_file or 'import'}"
        items.append(
            ChatItem(
                message_id=chat.message_id,
                source_file_id=uuid.uuid5(uuid.NAMESPACE_URL, source_key),
                source_row=chat.source_row or 0,
                sent_at=chat.sent_at,
                group_id=chat.group_id,
                sender_id=chat.wechat_id,
                content=chat.content,
                ip_address=chat.ip_address,
                embedding=chat.embedding,
            )
        )
    return items


def to_search_hit(item: ChatItem, score: float) -> SearchHit:
    return SearchHit(
        message_id=item.message_id,
        source_file_id=item.source_file_id,
        source_row=item.source_row,
        sent_at=item.sent_at,
        group_id=item.group_id,
        sender_id=item.sender_id,
        content=item.content,
        ip_address=item.ip_address,
        score=round(float(score), 6),
    )


def build_group_profiles(
    items: list[ChatItem],
    subjects: list[MysqlPerson],
    related: list[MysqlPerson],
) -> list[GroupProfile]:
    subject_wxids = {person.wechat_id for person in subjects if person.wechat_id}
    related_map = {person.wechat_id: person for person in related if person.wechat_id}
    if not subject_wxids:
        return []

    by_group: dict[str, list[ChatItem]] = defaultdict(list)
    for item in items:
        if not item.group_id:
            continue
        by_group[item.group_id].append(item)

    subject_groups = {
        group_id
        for group_id, rows in by_group.items()
        if any(row.sender_id in subject_wxids for row in rows)
    }
    profiles: list[GroupProfile] = []
    for group_id in subject_groups:
        rows = by_group[group_id]
        senders = [row.sender_id for row in rows if row.sender_id]
        related_present = [related_map[wxid] for wxid in dict.fromkeys(senders) if wxid in related_map]
        ordered = sorted(rows, key=lambda item: (item.sent_at is None, item.sent_at, item.source_row))
        messages = [
            {
                "sender_id": row.sender_id,
                "sent_at": row.sent_at.isoformat(sep=" ", timespec="seconds") if row.sent_at else None,
                "content": (row.content or "").strip(),
            }
            for row in ordered
            if (row.content or "").strip()
        ]
        profiles.append(
            GroupProfile(
                group_id=group_id,
                member_count=len(set(senders)),
                members=sorted(set(senders)),
                message_count=len(rows),
                subject_speak_count=sum(1 for row in rows if row.sender_id in subject_wxids),
                related_count=len(related_present),
                related_names=[f"{person.name}（{person.remark or person.wechat_id}）" for person in related_present],
                messages=messages,
            )
        )
    return sort_groups_by_activity(profiles)


def sort_groups_by_activity(profiles: list[GroupProfile]) -> list[GroupProfile]:
    profiles.sort(key=lambda item: (item.subject_speak_count, item.related_count, item.message_count), reverse=True)
    return profiles


def classify_groups_heuristic(profiles: list[GroupProfile]) -> list[GroupProfile]:
    ranked = sort_groups_by_activity(profiles)
    for index, profile in enumerate(ranked, start=1):
        profile.reason = (
            f"排序第 {index}：本人发言 {profile.subject_speak_count} 次，"
            f"关系人 {profile.related_count} 人"
        )
        profile.category = "待研判"
    return ranked


def group_payload(profiles: list[GroupProfile]) -> list[dict]:
    ranked = sort_groups_by_activity(list(profiles))
    return [
        {
            "rank": index,
            "group_id": profile.group_id,
            "member_count": profile.member_count,
            "message_count": profile.message_count,
            "subject_speak_count": profile.subject_speak_count,
            "related_count": profile.related_count,
            "related_names": profile.related_names,
            "chats": profile.messages,
        }
        for index, profile in enumerate(ranked, start=1)
    ]


def apply_group_judgement(profiles: list[GroupProfile], judgements: list[dict]) -> list[GroupProfile]:
    mapping = {item.get("group_id"): item for item in judgements if item.get("group_id")}
    for profile in profiles:
        item = mapping.get(profile.group_id)
        if not item:
            continue
        category = item.get("category")
        if category in ("关键群", "普通家人群"):
            profile.category = category
        if item.get("reason"):
            profile.reason = str(item["reason"])
    return profiles


def format_group_overview(profiles: list[GroupProfile], subjects: list[MysqlPerson]) -> str:
    wxids = "、".join(sorted({person.wechat_id for person in subjects if person.wechat_id})) or "未登记"
    if not profiles:
        return f"【群聊】本人微信号（{wxids}）在已导入聊天中未发现所在群。"
    lines = [f"【群聊】本人微信号 {wxids} 共出现在 {len(profiles)} 个群"]
    for profile in profiles:
        related = "、".join(profile.related_names) if profile.related_names else "无"
        lines.append(
            f"群 {profile.group_id}：成员 {profile.member_count} 人，"
            f"发言 {profile.message_count} 条（本人 {profile.subject_speak_count} 条），"
            f"关系人 {profile.related_count} 人（{related}）"
        )
    return "\n".join(lines)


def load_case_materials(
    case_number: str,
    sqlite_items: list[ChatItem],
    case_name: str | None = None,
) -> tuple[list[MysqlPerson], list[ChatItem]]:
    persons = list_persons(case_number, case_name)
    mysql_items = chats_to_items(list_chats(case_number, case_name))
    merged: dict[str, ChatItem] = {item.message_id: item for item in sqlite_items}
    for item in mysql_items:
        merged.setdefault(item.message_id, item)
    return persons, list(merged.values())


def person_brief(person: MysqlPerson) -> dict:
    return {
        "name": person.name,
        "gender": person.gender,
        "id_card": person.id_card,
        "birth_date": person.birth_date,
        "wechat_id": person.wechat_id,
        "wx_nickname": person.wx_nickname,
        "phone": person.phone,
        "delivery_address": person.delivery_address,
        "ip_address": person.ip_address,
        "case_info": person.case_info,
        "remark": person.remark,
    }


def build_llm_briefing(persons: list[MysqlPerson], profiles: list[GroupProfile]) -> dict:
    """整理本人、关系人、群统计，只作为模型输入，不直接展示。"""
    subjects = subjects_of(persons) if persons else []
    related = related_of(persons, subjects) if persons else []
    return {
        "subjects": [person_brief(person) for person in subjects],
        "related_persons": [person_brief(person) for person in related],
        "groups": group_payload(profiles),
        "task": [
            "先总结本人全部个人信息，再分别总结各关系人信息",
            "群已按本人发言次数、关系人数量排序",
            "必须阅读每个群 chats 里的群聊原文，再结合关系人构成，判断该群疑似是什么群（如家庭群、同事群、朋友群、工作群、其他），写成 inference，并引用具体发言作为依据",
            "不要自行检索违法犯罪关键词，也不要编造材料中没有的事实",
        ],
    }
