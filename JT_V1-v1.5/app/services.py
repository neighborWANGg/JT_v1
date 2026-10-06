import hashlib
import math
import re
from datetime import date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path
from typing import Protocol
from zipfile import BadZipFile

import httpx
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Message, Person, PersonAccount, SourceFile, WeChatFriend

REQUIRED_COLUMNS = ("序号", "时间", "群ID", "微信号", "聊天内容", "IP登录地址")
COUNTER_TERMS = ("没", "未", "不是", "取消", "别动", "等通知")
FIELD_PATTERN = re.compile(r"wxid_[A-Za-z0-9]+|(?:\d{1,3}\.){3}\d{1,3}|(?:群ID|群号)[：:\s]*([\w-]+)", re.I)


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """供开发和测试使用，不代表语义相似度。"""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._encode_one(text) for text in texts]

    @staticmethod
    def _encode_one(text: str) -> list[float]:
        raw = bytearray()
        counter = 0
        while len(raw) < settings.embedding_dimension:
            raw.extend(hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest())
            counter += 1
        vector = [(value / 127.5) - 1 for value in raw[: settings.embedding_dimension]]
        norm = math.sqrt(sum(value * value for value in vector)) or 1
        return [value / norm for value in vector]


class BgeEmbedder:
    def __init__(self) -> None:
        from FlagEmbedding import BGEM3FlagModel

        self.model = BGEM3FlagModel(settings.bge_model_path, use_fp16=True)

    def encode(self, texts: list[str]) -> list[list[float]]:
        output = self.model.encode(
            texts,
            batch_size=8,
            max_length=512,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return output["dense_vecs"].tolist()


class LmStudioEmbedder:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.client = httpx.Client(timeout=60, transport=transport)

    def encode(self, texts: list[str]) -> list[list[float]]:
        response = self.client.post(
            f"{settings.embedding_base_url}/embeddings",
            json={"model": settings.embedding_model, "input": texts},
        )
        response.raise_for_status()
        return [
            item["embedding"]
            for item in sorted(response.json()["data"], key=lambda item: item["index"])
        ]


def build_embedder() -> Embedder:
    if settings.embedding_backend == "bge":
        return BgeEmbedder()
    if settings.embedding_backend == "lmstudio":
        return LmStudioEmbedder()
    return HashEmbedder()


embedder: Embedder = build_embedder()


def scoped_source_ids(session: Session, case_id, conversation_id=None) -> list:
    statement = select(SourceFile.id).where(SourceFile.case_id == case_id)
    if conversation_id:
        statement = statement.where(
            or_(SourceFile.conversation_id.is_(None), SourceFile.conversation_id == conversation_id)
        )
    else:
        statement = statement.where(SourceFile.conversation_id.is_(None))
    return list(session.scalars(statement).all())


def scoped_messages(session: Session, case_id, conversation_id=None):
    source_ids = scoped_source_ids(session, case_id, conversation_id)
    if not source_ids:
        return []
    return session.scalars(
        select(Message).where(Message.case_id == case_id, Message.source_file_id.in_(source_ids))
    ).all()


def scoped_source_paths(session: Session, case_id, conversation_id=None) -> list[Path]:
    statement = select(SourceFile.stored_path).where(SourceFile.case_id == case_id)
    if conversation_id:
        statement = statement.where(
            or_(SourceFile.conversation_id.is_(None), SourceFile.conversation_id == conversation_id)
        )
    else:
        statement = statement.where(SourceFile.conversation_id.is_(None))
    return [Path(path) for path in session.scalars(statement).all()]


def stable_message_id(file_hash: str, sheet: str, row_number: int, case_id=None, conversation_id=None) -> str:
    scope = f"{case_id or ''}:{conversation_id}" if conversation_id else f"{case_id or ''}"
    return hashlib.sha256(f"{scope}:{file_hash}:{sheet}:{row_number}".encode("utf-8")).hexdigest()


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = _text(value)
    if not text:
        return None
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"无法解析时间：{text}") from exc


PERSON_SELF_MARKS = {"", "本人", "主体", "主要人员"}
FRIEND_SHEET_MARKS = ("好友", "通讯录", "联系人")


def _sheet_columns(headers: list) -> dict[str, int]:
    return {str(name or "").strip(): index for index, name in enumerate(headers) if str(name or "").strip()}


def _cell(row, columns: dict[str, int], *names: str) -> str | None:
    for name in names:
        index = columns.get(name)
        if index is not None and index < len(row):
            return _text(row[index])
    return None


def _relation_column(columns: dict[str, int]) -> str | None:
    for name in ("关系", "关系类型", "与老K关系"):
        if name in columns:
            return name
    return next((name for name in columns if "关系" in name), None)


def _pick(columns: dict[str, int], *names: str) -> int | None:
    for name in names:
        if name in columns:
            return columns[name]
    return None


def _is_chat_sheet(columns: dict[str, int]) -> bool:
    return (
        _pick(columns, "群ID", "群号", "群号(GROUP)") is not None
        and _pick(columns, "微信号", "发送方账号ID(SENDID)", "发送方账号ID") is not None
        and _pick(columns, "聊天内容", "聊天内容(CONTENT)") is not None
    )


def _is_person_sheet(columns: dict[str, int]) -> bool:
    return "姓名" in columns and any(name in columns for name in ("身份证号", "关系", "关系类型", "微信号"))


def _is_relation_sheet(columns: dict[str, int]) -> bool:
    return any(name in columns for name in ("主体", "当事人")) and any(name in columns for name in ("关系人", "关联人"))


def _is_friend_sheet(title: str, columns: dict[str, int]) -> bool:
    if _is_chat_sheet(columns):
        return False
    if any(mark in (title or "") for mark in FRIEND_SHEET_MARKS):
        return True
    if any(name in columns for name in ("好友昵称", "好友微信号", "好友微信ID", "好友微信id", "呢称", "好友原始账号")):
        return True
    if "身份证号" in columns or "群ID" in columns or "聊天内容" in columns:
        return False
    has_wechat = any(name in columns for name in ("微信ID", "微信号", "原始微信号"))
    has_name = any(name in columns for name in ("昵称", "微信昵称", "备注"))
    return has_wechat and has_name and "姓名" not in columns


def _looks_like_wxid(value: str | None) -> bool:
    return bool(value) and str(value).lower().startswith("wxid_")


def _split_wechat_fields(row, columns: dict[str, int], *prefix: str) -> tuple[str | None, str | None, str | None]:
    lead = "".join(prefix)
    wxid = _cell(row, columns, f"{lead}微信ID", f"{lead}微信id")
    alias = _cell(row, columns, f"{lead}微信号")
    original = _cell(row, columns, f"{lead}原始微信号", f"{lead}原始账号")
    if not wxid and _looks_like_wxid(alias):
        wxid, alias = alias, None
    if not wxid and original:
        wxid = original
    if not wxid:
        wxid = alias
        if _looks_like_wxid(wxid):
            alias = None
    return wxid, alias, original


def _file_owner(filename: str | None) -> tuple[str | None, str | None]:
    stem = Path(filename or "").stem.strip()
    if not stem:
        return None, None
    for token in ("微信好友", "好友列表", "好友信息", "通讯录", "联系人", "_好友", "-好友", "好友"):
        if token in stem:
            owner = stem.split(token)[0].strip(" _-")
            return (owner or None), None
    if "_" in stem:
        name, rest = stem.split("_", 1)
        name = name.strip(" _-")
        rest = rest.strip()
        if name and rest and rest != "群聊":
            return name, rest
        if name and rest == "群聊":
            return name, None
    return None, None


def _owner_from_filename(filename: str | None) -> str | None:
    name, _wechat = _file_owner(filename)
    return name


def _upsert_person(session: Session, case_id, payload: dict, conversation_id=None) -> Person:
    existing = session.scalar(select(Person).where(Person.case_id == case_id, Person.id_card == payload["id_card"]))
    incoming_wechat = payload.get("wechat_id")
    if existing:
        if conversation_id is None:
            existing.conversation_id = None
        keep_wechat = existing.wechat_id if existing.wechat_id and incoming_wechat and existing.wechat_id != incoming_wechat else None
        for key, value in payload.items():
            if key not in {"id_card", "conversation_id"} and value is not None:
                if keep_wechat and key in {"wechat_id", "wechat_alias", "original_wechat_id", "wx_nickname"}:
                    continue
                setattr(existing, key, value)
        session.flush()
        if keep_wechat:
            _upsert_account(
                session,
                existing,
                incoming_wechat,
                payload.get("wechat_alias"),
                payload.get("original_wechat_id"),
                payload.get("wx_nickname"),
                conversation_id,
            )
        else:
            _upsert_account(
                session,
                existing,
                incoming_wechat or existing.wechat_id,
                payload.get("wechat_alias") or existing.wechat_alias,
                payload.get("original_wechat_id") or existing.original_wechat_id,
                payload.get("wx_nickname") or existing.wx_nickname,
                conversation_id,
            )
        return existing
    person = Person(case_id=case_id, conversation_id=conversation_id, **payload)
    session.add(person)
    session.flush()
    _upsert_account(
        session,
        person,
        payload.get("wechat_id"),
        payload.get("wechat_alias"),
        payload.get("original_wechat_id"),
        payload.get("wx_nickname"),
        conversation_id,
    )
    return person


def _upsert_account(session: Session, person: Person, wechat_id, wechat_alias, original_wechat_id, nickname, conversation_id=None) -> None:
    if not any((wechat_id, wechat_alias, original_wechat_id)):
        return
    accounts = session.scalars(select(PersonAccount).where(PersonAccount.person_id == person.id)).all()
    incoming = {str(value).strip().lower() for value in (wechat_id, wechat_alias, original_wechat_id) if value}
    for account in accounts:
        existing = {str(value).strip().lower() for value in (account.wechat_id, account.wechat_alias, account.original_wechat_id) if value}
        if incoming & existing:
            if conversation_id is None:
                account.conversation_id = None
            if wechat_id and not account.wechat_id:
                account.wechat_id = wechat_id
            if wechat_alias and not account.wechat_alias:
                account.wechat_alias = wechat_alias
            if original_wechat_id and not account.original_wechat_id:
                account.original_wechat_id = original_wechat_id
            if nickname and not account.nickname:
                account.nickname = nickname
            return
    session.add(PersonAccount(
        case_id=person.case_id,
        person_id=person.id,
        conversation_id=conversation_id if conversation_id is not None else person.conversation_id,
        wechat_id=wechat_id,
        wechat_alias=wechat_alias,
        original_wechat_id=original_wechat_id,
        nickname=nickname,
    ))


def _upsert_friend(session: Session, case_id, payload: dict, conversation_id=None) -> bool:
    owner_keys = {str(value).strip().lower() for value in (payload.get("owner_wechat_id"), payload.get("owner_alias"), payload.get("owner_name")) if value}
    friend_keys = {str(value).strip().lower() for value in (payload.get("friend_wechat_id"), payload.get("friend_alias"), payload.get("friend_original_wechat_id")) if value}
    if not friend_keys:
        return False
    rows = session.scalars(
        select(WeChatFriend).where(
            WeChatFriend.case_id == case_id,
            WeChatFriend.owner_name == payload.get("owner_name"),
        )
    ).all() if payload.get("owner_name") else session.scalars(
        select(WeChatFriend).where(WeChatFriend.case_id == case_id, WeChatFriend.owner_wechat_id == payload.get("owner_wechat_id"))
    ).all()
    for row in rows:
        existing_owner = {str(value).strip().lower() for value in (row.owner_wechat_id, row.owner_alias, row.owner_name) if value}
        existing_friend = {str(value).strip().lower() for value in (row.friend_wechat_id, row.friend_alias, row.friend_original_wechat_id) if value}
        if owner_keys & existing_owner and friend_keys & existing_friend:
            if conversation_id is None:
                row.conversation_id = None
            for key, value in payload.items():
                if value and not getattr(row, key, None):
                    setattr(row, key, value)
            return False
    session.add(WeChatFriend(case_id=case_id, conversation_id=conversation_id, **payload))
    return True


def ensure_uploaded_owner(session: Session, case_id, filename: str | None, conversation_id=None, owner_name=None) -> None:
    name, wechat = _file_owner(filename)
    if not name and owner_name:
        name = str(owner_name).strip() or None
    if not name:
        return
    people = session.scalars(select(Person).where(Person.case_id == case_id)).all()
    target = None
    wechat_key = (wechat or "").strip().lower()
    if wechat_key:
        for person in people:
            keys = {str(value).strip().lower() for value in (person.wechat_id, person.wechat_alias, person.original_wechat_id) if value}
            if wechat_key in keys:
                target = person
                break
    if target is None:
        for person in people:
            if person.name == name and (person.remark or "").strip() in PERSON_SELF_MARKS:
                target = person
                break
    payload = {
        "id_card": target.id_card if target else f"导入:{wechat or name}",
        "name": name,
        "remark": "本人",
    }
    if not target:
        payload["gender"] = "未知"
    if wechat:
        payload["wechat_id"] = wechat
    _upsert_person(session, case_id, payload, conversation_id)


def import_friend_rows(session: Session, case_id, rows, columns: dict[str, int], filename: str, source_file_id, conversation_id=None, owner_name=None) -> int:
    file_name, file_wechat = _file_owner(filename)
    filename_owner = file_name or owner_name
    ensure_uploaded_owner(session, case_id, filename, conversation_id, owner_name)
    imported = 0
    for row in rows:
        wxid, alias, original = _split_wechat_fields(row, columns, "好友")
        if not wxid:
            wxid, alias, original = _split_wechat_fields(row, columns)
        nickname = _cell(row, columns, "好友昵称", "昵称", "微信昵称", "呢称", "好友呢称") or _cell(row, columns, "姓名")
        remark = _cell(row, columns, "好友备注", "备注")
        owner_name = _cell(row, columns, "主体", "当事人", "账号本人", "机主", "所属人", "所属账号", "归属人") or filename_owner
        owner_wxid, owner_alias, _owner_original = _split_wechat_fields(row, columns, "本人")
        if not owner_wxid:
            owner_wxid = _cell(row, columns, "所属微信ID", "账号微信ID", "归属微信号")
            owner_alias = owner_alias or _cell(row, columns, "所属微信号", "账号微信号")
        if not owner_wxid and file_wechat:
            owner_wxid = file_wechat
        if not any((wxid, alias, original, nickname)):
            continue
        if _upsert_friend(session, case_id, {
            "source_file_id": source_file_id,
            "owner_name": owner_name,
            "owner_wechat_id": owner_wxid,
            "owner_alias": owner_alias,
            "friend_nickname": nickname,
            "friend_remark": remark,
            "friend_wechat_id": wxid,
            "friend_alias": alias,
            "friend_original_wechat_id": original,
        }, conversation_id):
            imported += 1
    return imported


def import_persons_from_workbook(session: Session, case_id, workbook, conversation_id=None, filename: str | None = None, source_file_id=None, owner_name=None) -> tuple[int, int]:
    imported = 0
    friends = 0
    for sheet in workbook.worksheets:
        rows = sheet.iter_rows(values_only=True)
        columns = _sheet_columns(list(next(rows, [])))
        if _is_chat_sheet(columns):
            continue
        if _is_friend_sheet(sheet.title, columns):
            friends += import_friend_rows(session, case_id, rows, columns, filename or "", source_file_id, conversation_id, owner_name)
            continue
        if _is_person_sheet(columns):
            relation_name = _relation_column(columns)
            parsed = []
            for row in rows:
                name = _cell(row, columns, "姓名")
                if not name:
                    continue
                relation = _cell(row, columns, relation_name) if relation_name else ""
                wechat_id, wechat_alias, original_wechat_id = _split_wechat_fields(row, columns)
                parsed.append({
                    "name": name,
                    "id_card": _cell(row, columns, "身份证号") or f"{name}:{wechat_id or wechat_alias or ''}",
                    "gender": _cell(row, columns, "性别") or "未知",
                    "birth_date": _cell(row, columns, "出生日期", "生日"),
                    "wechat_id": wechat_id,
                    "wechat_alias": wechat_alias,
                    "original_wechat_id": original_wechat_id,
                    "wx_nickname": _cell(row, columns, "微信昵称"),
                    "phone": _cell(row, columns, "手机号"),
                    "delivery_address": _cell(row, columns, "快递地址"),
                    "takeout_address": _cell(row, columns, "外卖地址"),
                    "ip_address": _cell(row, columns, "常用IP", "IP登录地址", "登录IP"),
                    "case_info": _cell(row, columns, "案件信息"),
                    "relation": relation or "",
                })
            subject = next((item["name"] for item in parsed if item["relation"] in PERSON_SELF_MARKS), None) or owner_name or (parsed[0]["name"] if parsed else "")
            for item in parsed:
                relation = item.pop("relation")
                remark = None
                if relation not in PERSON_SELF_MARKS:
                    remark = f"{subject}的{relation}" if subject and relation else (relation or None)
                _upsert_person(session, case_id, {**item, "remark": remark}, conversation_id)
                imported += 1
            continue
        if not _is_relation_sheet(columns):
            continue
        for row in rows:
            subject = _cell(row, columns, "主体", "当事人") or owner_name
            related = _cell(row, columns, "关系人", "关联人")
            if not subject or not related:
                continue
            relation = _cell(row, columns, "关系类型", "关系") or "关系人"
            wechat_id, wechat_alias, original_wechat_id = _split_wechat_fields(row, columns)
            ip_address = _cell(row, columns, "IP地址", "IP登录地址", "常用IP")
            description = _cell(row, columns, "关系说明", "备注")
            existing_subject = next((
                person for person in session.scalars(select(Person).where(Person.case_id == case_id, Person.name == subject)).all()
                if (person.remark or "").strip() in PERSON_SELF_MARKS
            ), None)
            if existing_subject is None:
                _upsert_person(session, case_id, {
                    "id_card": subject,
                    "name": subject,
                    "gender": "未知",
                    "wechat_id": None,
                    "ip_address": None,
                    "remark": None,
                }, conversation_id)
                imported += 1
            _upsert_person(session, case_id, {
                "id_card": _cell(row, columns, "身份证号") or f"{related}:{wechat_id or ''}",
                "name": related,
                "gender": "未知",
                "wechat_id": wechat_id,
                "wechat_alias": wechat_alias,
                "original_wechat_id": original_wechat_id,
                "ip_address": ip_address,
                "remark": description or f"{subject}的{relation}",
            }, conversation_id)
            imported += 1
    return imported, friends


def import_xlsx(session: Session, case_id, filename: str, payload: bytes, conversation_id=None, owner_name=None) -> tuple[SourceFile, int, bool, int, int]:
    file_hash = hashlib.sha256(payload).hexdigest()
    existing_query = select(SourceFile).where(SourceFile.case_id == case_id, SourceFile.sha256 == file_hash)
    if conversation_id:
        existing_query = existing_query.where(SourceFile.conversation_id == conversation_id)
    else:
        existing_query = existing_query.where(SourceFile.conversation_id.is_(None))
    existing = session.scalar(existing_query)
    if existing:
        count = session.query(Message).filter(Message.source_file_id == existing.id).count()
        persons = session.query(Person).filter(Person.case_id == case_id).count()
        friends = session.query(WeChatFriend).filter(WeChatFriend.source_file_id == existing.id).count()
        return existing, count, True, persons, friends

    try:
        workbook = load_workbook(BytesIO(payload), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException) as exc:
        raise ValueError("文件不是有效的 xlsx 工作簿") from exc

    case_dir = settings.data_dir / str(case_id) / "data"
    case_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower() or ".xlsx"
    stored_name = f"{file_hash}{suffix}" if not conversation_id else f"{file_hash}-{conversation_id}{suffix}"
    stored = case_dir / stored_name
    stored.write_bytes(payload)
    stored.chmod(0o444)

    source = SourceFile(
        case_id=case_id,
        conversation_id=conversation_id,
        filename=Path(filename).name,
        sha256=file_hash,
        stored_path=str(stored),
    )
    session.add(source)
    session.flush()

    parsed = []
    for sheet in workbook.worksheets:
        rows = sheet.iter_rows(values_only=True)
        headers = list(next(rows, []) or [])
        columns = _sheet_columns(headers)
        if sheet.title == "聊天记录" and not _is_chat_sheet(columns):
            missing = [name for name in REQUIRED_COLUMNS if name not in columns]
            raise ValueError(f"Excel 缺少字段：{', '.join(missing)}")
        if not _is_chat_sheet(columns):
            continue
        group_index = _pick(columns, "群ID", "群号", "群号(GROUP)")
        sender_index = _pick(columns, "微信号", "发送方账号ID(SENDID)", "发送方账号ID")
        content_index = _pick(columns, "聊天内容", "聊天内容(CONTENT)")
        time_index = _pick(columns, "时间", "消息发出时间(CAPTURE_TIME)", "消息发出时间")
        ip_index = _pick(columns, "IP登录地址", "发送方IP(SENDIP)", "发送方IP")
        sequence_index = _pick(columns, "序号")

        def cell_at(row, index):
            if index is None or index >= len(row):
                return None
            return row[index]

        for row_number, row in enumerate(rows, start=2):
            content = _text(cell_at(row, content_index))
            if content:
                parsed.append((sheet.title, row_number, row, content, group_index, sender_index, time_index, ip_index, sequence_index))
    vectors = embedder.encode([item[3] for item in parsed]) if parsed else []
    if parsed and len(vectors) != len(parsed):
        raise RuntimeError("向量数量与消息数量不一致")
    for (sheet_title, row_number, row, content, group_index, sender_index, time_index, ip_index, sequence_index), vector in zip(parsed, vectors, strict=True):
        def cell_at(index):
            if index is None or index >= len(row):
                return None
            return row[index]

        session.add(Message(
            id=stable_message_id(file_hash, sheet_title, row_number, case_id, conversation_id),
            case_id=case_id,
            source_file_id=source.id,
            source_row=row_number,
            sequence=_text(cell_at(sequence_index)),
            sent_at=_datetime(cell_at(time_index)),
            group_id=_text(cell_at(group_index)),
            sender_id=_text(cell_at(sender_index)),
            content=content,
            ip_address=_text(cell_at(ip_index)),
            embedding=vector,
        ))

    workbook.close()
    ensure_uploaded_owner(session, case_id, filename, conversation_id, owner_name)
    people_book = load_workbook(BytesIO(payload), read_only=True, data_only=True)
    person_count, friend_count = import_persons_from_workbook(
        session, case_id, people_book, conversation_id, filename, source.id, owner_name
    )
    people_book.close()
    if not parsed and person_count == 0 and friend_count == 0:
        raise ValueError("Excel 缺少工作表：聊天记录、人员信息或微信好友")
    session.commit()
    return source, len(parsed), False, person_count, friend_count


def _terms(text: str) -> set[str]:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", text.lower())
    ascii_terms = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    return ascii_terms | {chinese[index:index + 2] for index in range(max(0, len(chinese) - 1))}


def search_messages(
    session: Session,
    case_id,
    question: str,
    limit: int | None = None,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    sender_id: str | None = None,
    group_id: str | None = None,
    ip_address: str | None = None,
    source_file_id=None,
    conversation_id=None,
):
    query_vector = embedder.encode([question])[0]
    statement = select(Message).where(Message.case_id == case_id)
    source_ids = scoped_source_ids(session, case_id, conversation_id)
    statement = statement.where(Message.source_file_id.in_(source_ids or ["__none__"]))
    if start_date:
        statement = statement.where(Message.sent_at >= datetime.combine(start_date, time.min))
    if end_date:
        statement = statement.where(Message.sent_at <= datetime.combine(end_date, time.max))
    for field, value in (
        (Message.sender_id, sender_id),
        (Message.group_id, group_id),
        (Message.ip_address, ip_address),
        (Message.source_file_id, source_file_id),
    ):
        if value is not None:
            statement = statement.where(field == value)
    messages = session.scalars(statement).all()
    query_terms = _terms(question)
    field_values = {
        (match.group(1) or match.group(0)).strip().lower()
        for match in FIELD_PATTERN.finditer(question)
    }

    # ponytail: SQLite 原型在应用内扫描；超过约 10 万条消息时迁移 pgvector/OpenSearch。
    scored = []
    for message in messages:
        similarity = sum(a * b for a, b in zip(query_vector, message.embedding, strict=True))
        message_terms = _terms(message.content)
        lexical = len(query_terms & message_terms) / max(1, len(query_terms))
        fields = {value.lower() for value in (message.sender_id, message.group_id, message.ip_address) if value}
        field_bonus = 2.0 * len(field_values & fields)
        counter_bonus = 0.6 * sum(term in question and term in message.content for term in COUNTER_TERMS)
        exact_bonus = 0.5 if question.strip().lower() in message.content.lower() else 0.0
        scored.append((message, similarity + lexical + field_bonus + counter_bonus + exact_bonus))
    ranked = sorted(scored, key=lambda item: item[1], reverse=True)
    if limit is None:
        return ranked
    return ranked[:limit]


def matches_filters(
    message: Message,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    sender_id: str | None = None,
    group_id: str | None = None,
    ip_address: str | None = None,
    source_file_id=None,
) -> bool:
    return not any((
        start_date and (not message.sent_at or message.sent_at.date() < start_date),
        end_date and (not message.sent_at or message.sent_at.date() > end_date),
        sender_id is not None and message.sender_id != sender_id,
        group_id is not None and message.group_id != group_id,
        ip_address is not None and message.ip_address != ip_address,
        source_file_id is not None and message.source_file_id != source_file_id,
    ))


def context_window(
    session: Session,
    case_id,
    message_id: str,
    before: int,
    after: int,
) -> list[tuple[Message, int]]:
    anchor = session.get(Message, message_id)
    if not anchor or anchor.case_id != case_id:
        raise LookupError("消息不存在")

    statement = select(Message).where(
        Message.case_id == case_id,
        Message.source_file_id == anchor.source_file_id,
    )
    statement = statement.where(
        Message.group_id == anchor.group_id
        if anchor.group_id is not None
        else Message.group_id.is_(None)
    )
    messages = session.scalars(statement.order_by(Message.source_row)).all()
    anchor_index = next(index for index, message in enumerate(messages) if message.id == message_id)
    start = max(0, anchor_index - before)
    end = min(len(messages), anchor_index + after + 1)
    return [(messages[index], index - anchor_index) for index in range(start, end)]


def time_neighbors(
    session: Session,
    case_id,
    message_id: str,
    minutes: int,
    limit: int,
) -> list[tuple[Message, int]]:
    anchor = session.get(Message, message_id)
    if not anchor or anchor.case_id != case_id:
        raise LookupError("消息不存在")
    if anchor.sent_at is None:
        return [(anchor, 0)]

    statement = select(Message).where(
        Message.case_id == case_id,
        Message.sent_at.between(
            anchor.sent_at - timedelta(minutes=minutes),
            anchor.sent_at + timedelta(minutes=minutes),
        ),
    )
    statement = statement.where(
        Message.group_id == anchor.group_id
        if anchor.group_id is not None
        else Message.source_file_id == anchor.source_file_id
    )
    messages = session.scalars(
        statement.order_by(Message.sent_at, Message.source_row).limit(limit)
    ).all()
    return [
        (message, round((message.sent_at - anchor.sent_at).total_seconds()))
        for message in messages
    ]


def expand_context(
    session: Session,
    case_id,
    scored_messages: list[tuple[Message, float]],
    before: int,
    after: int,
) -> list[tuple[Message, float]]:
    expanded: dict[str, tuple[Message, float]] = {
        message.id: (message, score) for message, score in scored_messages
    }
    for anchor, anchor_score in scored_messages:
        for message, distance in context_window(session, case_id, anchor.id, before, after):
            context_score = anchor_score - abs(distance) * 0.01
            current = expanded.get(message.id)
            if current is None or context_score > current[1]:
                expanded[message.id] = (message, context_score)
    return sorted(
        expanded.values(),
        key=lambda item: (
            item[0].sent_at or datetime.min,
            item[0].source_row,
        ),
    )
