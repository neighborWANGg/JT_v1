from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
import re
from urllib.parse import urlparse
from uuid import UUID
import uuid

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal, get_session, init_db
from .llm import LlmError, LocalLlmClient, SYSTEM_PROMPT, enforce_grounded_answer, normalize_llm_base_url
from .models import Analysis, AnalysisTask, AuditLog, Case, Conversation, Message, Person, PersonAccount, PersonRelation, Review, SourceFile, WeChatFriend
from .schemas import (
    AnalysisIn,
    AnalysisOut,
    AnalysisTaskOut,
    CaseCreate,
    CaseOut,
    CaseStats,
    CaseUpdate,
    ConversationCreate,
    ConversationFork,
    ConversationOut,
    ConversationTurnOut,
    ConversationUpdate,
    ContextMessage,
    ImportOut,
    AuditLogOut,
    FilterOptions,
    ReviewCreate,
    ReviewOut,
    ProjectFolderOut,
    SearchHit,
    SourceOut,
    SourceOption,
    TimeNeighbor,
    RuntimeSettingsIn,
    ModelListIn,
    ChatLabelsIn,
    FriendScreenIn,
    PersonCreateIn,
    PersonUpdateIn,
    RelationUpdateIn,
)
from .investigation import (
    ChatItem,
    build_case_graph,
    build_group_profiles,
    build_llm_briefing,
    evidence_harness,
    load_case_materials,
    related_of,
    relationship_rows_from_sources,
    apply_manual_relations,
    UPLOADED_REMARKS,
    groups_with_uploaded_people,
    screen_wechat_friends,
    search_keyword_items,
    subjects_of,
    uploaded_people_index,
    to_search_hit,
    wants_full_profile,
)
from .services import (
    context_window,
    _file_owner,
    import_xlsx,
    matches_filters,
    scoped_messages,
    scoped_source_paths,
    search_messages,
    time_neighbors,
)
from .mysql_repo import list_person_relations, list_persons, list_wechat_friends
from .project_store import list_project_files, open_in_file_manager, remove_project, sync_project
from .runtime_settings import (
    enabled_skill_prompt, fetch_models, list_skills, load_chat_labels, load_runtime_settings,
    public_settings, record_usage, save_chat_labels, save_runtime_settings, usage_summary,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as session:
        for case in session.scalars(select(Case)):
            sync_project(session, case)
    yield


app = FastAPI(title="JT 群聊分析研判 API", version="0.1.0", lifespan=lifespan)
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
FRONTEND_PATH = FRONTEND_DIR / "design-sample.html"
LEGACY_FRONTEND_PATH = FRONTEND_DIR / "index.html"
THEME_PATH = FRONTEND_DIR / "theme.config.css"
APP_JS_PATH = FRONTEND_DIR / "app.js"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ui", include_in_schema=False)
def frontend():
    if not FRONTEND_PATH.exists():
        raise HTTPException(404, "前端文件不存在")
    return FileResponse(FRONTEND_PATH, headers={"Cache-Control": "no-store"})


@app.get("/ui/legacy", include_in_schema=False)
def legacy_frontend():
    return FileResponse(LEGACY_FRONTEND_PATH)


@app.get("/theme.config.css", include_in_schema=False)
def frontend_theme():
    return FileResponse(THEME_PATH, media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/app.js", include_in_schema=False)
def frontend_script():
    return FileResponse(APP_JS_PATH, media_type="text/javascript", headers={"Cache-Control": "no-store"})


@app.get("/cases", response_model=list[CaseOut])
def list_cases(session: Session = Depends(get_session)):
    return session.scalars(select(Case).order_by(Case.pinned.desc(), Case.created_at.desc())).all()


@app.post("/cases", response_model=CaseOut, status_code=201)
def create_case(payload: CaseCreate, session: Session = Depends(get_session)):
    item = Case(**payload.model_dump())
    session.add(item)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "案件编号已存在") from exc
    session.refresh(item)
    write_audit(session, item.id, "case.create", "case", item.id, {"code": item.code})
    return item


def write_audit(session: Session, case_id, action: str, target_type: str, target_id, details=None):
    session.add(AuditLog(
        case_id=case_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details=details or {},
    ))
    session.commit()
    if case_id and (case := session.get(Case, case_id)):
        sync_project(session, case)


def require_case(case_id: UUID, session: Session) -> Case:
    item = session.get(Case, case_id)
    if not item:
        raise HTTPException(404, "案件不存在")
    return item


@app.patch("/cases/{case_id}", response_model=CaseOut)
def update_case(case_id: UUID, payload: CaseUpdate, session: Session = Depends(get_session)):
    item = require_case(case_id, session)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(item, field, value)
    write_audit(session, case_id, "case.update", "case", case_id, changes)
    session.refresh(item)
    return item


@app.get("/cases/{case_id}/sources", response_model=list[SourceOut])
def list_sources(case_id: UUID, session: Session = Depends(get_session)):
    require_case(case_id, session)
    return session.scalars(
        select(SourceFile).where(SourceFile.case_id == case_id).order_by(SourceFile.imported_at.desc())
    ).all()


@app.get("/cases/{case_id}/folder", response_model=ProjectFolderOut)
def case_folder(case_id: UUID, session: Session = Depends(get_session)):
    root = sync_project(session, require_case(case_id, session))
    return ProjectFolderOut(path=str(root), files=list_project_files(root))


@app.post("/cases/{case_id}/folder/open")
def open_case_folder(case_id: UUID, session: Session = Depends(get_session)):
    root = sync_project(session, require_case(case_id, session))
    try:
        open_in_file_manager(root)
    except OSError as exc:
        raise HTTPException(501, str(exc)) from exc
    write_audit(session, case_id, "case.folder.open", "case", case_id)
    return {"path": str(root)}


@app.get("/cases/{case_id}/settings")
def get_runtime_settings(case_id: UUID, session: Session = Depends(get_session)):
    require_case(case_id, session)
    return public_settings(load_runtime_settings(case_id))


@app.put("/cases/{case_id}/settings")
def update_runtime_settings(case_id: UUID, payload: RuntimeSettingsIn, session: Session = Depends(get_session)):
    require_case(case_id, session)
    stored = payload.model_dump(mode="json")
    stored["model"]["api"]["api_key"] = payload.model.api.api_key.get_secret_value()
    for index, preset in enumerate(payload.model.api_presets):
        stored["model"]["api_presets"][index]["api_key"] = preset.api_key.get_secret_value()
    saved = save_runtime_settings(case_id, stored)
    write_audit(session, case_id, "settings.update", "case", case_id, {})
    return public_settings(saved)


@app.get("/cases/{case_id}/skills")
def get_runtime_skills(case_id: UUID, session: Session = Depends(get_session)):
    require_case(case_id, session)
    return list_skills(case_id)


@app.post("/cases/{case_id}/models")
def get_available_models(case_id: UUID, payload: ModelListIn, session: Session = Depends(get_session)):
    require_case(case_id, session)
    base_url = normalize_llm_base_url(str(payload.base_url))
    if not base_url:
        raise HTTPException(422, "请填写完整接口地址，需以 http:// 或 https:// 开头")
    hostname = (urlparse(base_url).hostname or "").lower()
    if settings.api_llm_allowed_hosts and hostname not in settings.api_llm_allowed_hosts:
        raise HTTPException(403, "该模型接口地址不在后端允许列表中")
    try:
        api_key = payload.api_key.get_secret_value()
        stored = load_runtime_settings(case_id)
        if not api_key:
            api_key = stored["model"]["api"].get("api_key", "")
        if not api_key:
            for preset in stored["model"].get("api_presets", []) or []:
                if str(preset.get("base_url") or "").rstrip("/") == base_url and preset.get("api_key"):
                    api_key = preset["api_key"]
                    break
        return {"models": fetch_models(base_url, api_key)}
    except Exception as exc:
        message = str(exc)
        if "protocol" in message.lower() or "请填写完整接口地址" in message:
            raise HTTPException(422, "请填写完整接口地址，需以 http:// 或 https:// 开头") from exc
        raise HTTPException(502, f"读取模型列表失败：{exc}") from exc


@app.get("/cases/{case_id}/usage")
def get_model_usage(case_id: UUID, session: Session = Depends(get_session)):
    require_case(case_id, session)
    return usage_summary(case_id)


@app.get("/cases/{case_id}/filter-options", response_model=FilterOptions)
def filter_options(case_id: UUID, conversation_id: UUID | None = None, session: Session = Depends(get_session)):
    require_case(case_id, session)
    scope = resolve_material_scope(case_id, conversation_id, session)
    messages = scoped_messages(session, case_id, scope)
    dates = [message.sent_at.date() for message in messages if message.sent_at]
    source_ids = list({message.source_file_id for message in messages})
    sources = session.scalars(
        select(SourceFile).where(SourceFile.id.in_(source_ids)).order_by(SourceFile.filename)
    ).all() if source_ids else []
    return FilterOptions(
        start_date=min(dates) if dates else None,
        end_date=max(dates) if dates else None,
        senders=sorted({message.sender_id for message in messages if message.sender_id}),
        groups=sorted({message.group_id for message in messages if message.group_id}),
        ip_addresses=sorted({message.ip_address for message in messages if message.ip_address}),
        sources=[SourceOption(id=source.id, filename=source.filename) for source in sources],
    )


@app.get("/cases/{case_id}/audit-logs", response_model=list[AuditLogOut])
def list_audit_logs(case_id: UUID, limit: int = 100, session: Session = Depends(get_session)):
    require_case(case_id, session)
    return session.scalars(
        select(AuditLog).where(AuditLog.case_id == case_id).order_by(AuditLog.created_at.desc()).limit(min(limit, 500))
    ).all()


@app.get("/cases/{case_id}/export")
def export_case(case_id: UUID, session: Session = Depends(get_session)):
    item = require_case(case_id, session)
    payload = {
        "case": CaseOut.model_validate(item),
        "sources": session.scalars(select(SourceFile).where(SourceFile.case_id == case_id)).all(),
        "messages": session.scalars(select(Message).where(Message.case_id == case_id).order_by(Message.sent_at, Message.source_row)).all(),
        "conversations": session.scalars(select(Conversation).where(Conversation.case_id == case_id)).all(),
        "analyses": session.scalars(select(Analysis).where(Analysis.case_id == case_id)).all(),
    }
    write_audit(session, case_id, "case.export", "case", case_id)
    return JSONResponse(
        jsonable_encoder(payload),
        headers={"Content-Disposition": f'attachment; filename="{item.code}.json"'},
    )


@app.delete("/cases/{case_id}", status_code=204)
def delete_case(case_id: UUID, session: Session = Depends(get_session)):
    item = require_case(case_id, session)
    analysis_ids = session.scalars(select(Analysis.id).where(Analysis.case_id == case_id)).all()
    if analysis_ids:
        session.execute(delete(Review).where(Review.analysis_id.in_(analysis_ids)))
    session.execute(delete(AnalysisTask).where(AnalysisTask.case_id == case_id))
    session.execute(delete(Analysis).where(Analysis.case_id == case_id))
    session.execute(delete(Conversation).where(Conversation.case_id == case_id))
    session.execute(delete(Message).where(Message.case_id == case_id))
    session.execute(delete(WeChatFriend).where(WeChatFriend.case_id == case_id))
    session.execute(delete(PersonRelation).where(PersonRelation.case_id == case_id))
    session.execute(delete(PersonAccount).where(PersonAccount.case_id == case_id))
    session.execute(delete(Person).where(Person.case_id == case_id))
    session.execute(delete(SourceFile).where(SourceFile.case_id == case_id))
    session.execute(delete(AuditLog).where(AuditLog.case_id == case_id))
    session.delete(item)
    session.commit()
    remove_project(case_id)
    return Response(status_code=204)


def resolve_material_scope(case_id: UUID, conversation_id: UUID | None, session: Session) -> UUID | None:
    if conversation_id is None:
        return None
    conversation = require_conversation(conversation_id, session)
    if conversation.case_id != case_id:
        raise HTTPException(422, "研判对话不属于当前案件")
    return conversation.id


def messages_to_chat_items(messages) -> list[ChatItem]:
    return [
        ChatItem(
            message_id=message.id,
            source_file_id=message.source_file_id,
            source_row=message.source_row,
            sent_at=message.sent_at,
            group_id=message.group_id,
            sender_id=message.sender_id,
            content=message.content,
            ip_address=message.ip_address,
            embedding=message.embedding or [],
        )
        for message in messages
    ]


def require_conversation(conversation_id: UUID, session: Session) -> Conversation:
    item = session.get(Conversation, conversation_id)
    if not item:
        raise HTTPException(404, "研判对话不存在")
    return item


@app.get("/cases/{case_id}/conversations", response_model=list[ConversationOut])
def list_conversations(case_id: UUID, session: Session = Depends(get_session)):
    require_case(case_id, session)
    return session.scalars(
        select(Conversation)
        .where(Conversation.case_id == case_id)
        .order_by(Conversation.pinned.desc(), Conversation.created_at.desc())
    ).all()


@app.post("/cases/{case_id}/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(
    case_id: UUID,
    payload: ConversationCreate,
    session: Session = Depends(get_session),
):
    require_case(case_id, session)
    item = Conversation(case_id=case_id, title=payload.title)
    session.add(item)
    session.commit()
    session.refresh(item)
    write_audit(session, case_id, "conversation.create", "conversation", item.id)
    return item


@app.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    session: Session = Depends(get_session),
):
    item = require_conversation(conversation_id, session)
    source_case_id = item.case_id
    changes = payload.model_dump(exclude_unset=True)
    target_case_id = changes.pop("case_id", None)
    if target_case_id and target_case_id != item.case_id:
        require_case(target_case_id, session)
        if session.scalar(select(func.count(Analysis.id)).where(Analysis.conversation_id == item.id)):
            raise HTTPException(409, "已有研判记录的对话不能跨案件移动，以免破坏证据边界")
        item.case_id = target_case_id
    for field, value in changes.items():
        setattr(item, field, value)
    session.commit()
    session.refresh(item)
    write_audit(session, item.case_id, "conversation.update", "conversation", item.id, changes)
    if source_case_id != item.case_id and (source_case := session.get(Case, source_case_id)):
        sync_project(session, source_case)
    return item


@app.post("/cases/{case_id}/conversations/archive")
def archive_case_conversations(case_id: UUID, session: Session = Depends(get_session)):
    require_case(case_id, session)
    result = session.execute(
        update(Conversation).where(Conversation.case_id == case_id).values(archived=True)
    )
    write_audit(session, case_id, "conversation.archive_all", "case", case_id, {"count": result.rowcount})
    return {"archived": result.rowcount}


@app.post("/conversations/{conversation_id}/fork", response_model=ConversationOut, status_code=201)
def fork_conversation(
    conversation_id: UUID,
    payload: ConversationFork,
    session: Session = Depends(get_session),
):
    source = require_conversation(conversation_id, session)
    clone = Conversation(
        case_id=source.case_id,
        title=payload.title or f"{source.title} · 分叉",
        analysis_enabled=source.analysis_enabled,
    )
    session.add(clone)
    session.flush()
    for turn in session.scalars(
        select(Analysis).where(Analysis.conversation_id == source.id).order_by(Analysis.created_at)
    ):
        session.add(Analysis(
            case_id=source.case_id,
            conversation_id=clone.id,
            question=turn.question,
            result=turn.result,
            engine=turn.engine,
        ))
    session.commit()
    session.refresh(clone)
    write_audit(session, source.case_id, "conversation.fork", "conversation", clone.id, {"source_id": str(source.id)})
    return clone


@app.get("/conversations/{conversation_id}/turns", response_model=list[ConversationTurnOut])
def conversation_turns(conversation_id: UUID, session: Session = Depends(get_session)):
    require_conversation(conversation_id, session)
    return session.scalars(
        select(Analysis)
        .where(Analysis.conversation_id == conversation_id)
        .order_by(Analysis.created_at, Analysis.id)
    ).all()


def promote_conversation_materials(session: Session, case_id: UUID, conversation_id: UUID) -> None:
    sources = session.scalars(select(SourceFile).where(SourceFile.conversation_id == conversation_id)).all()
    for source in sources:
        existing = session.scalar(
            select(SourceFile).where(
                SourceFile.case_id == case_id,
                SourceFile.sha256 == source.sha256,
                SourceFile.conversation_id.is_(None),
                SourceFile.id != source.id,
            )
        )
        if existing:
            session.execute(
                update(Message).where(Message.source_file_id == source.id).values(source_file_id=existing.id)
            )
            session.delete(source)
        else:
            source.conversation_id = None
    persons = session.scalars(select(Person).where(Person.conversation_id == conversation_id)).all()
    for person in persons:
        existing = session.scalar(
            select(Person).where(
                Person.case_id == case_id,
                Person.id_card == person.id_card,
                Person.conversation_id.is_(None),
                Person.id != person.id,
            )
        )
        if existing:
            session.execute(
                update(PersonAccount)
                .where(PersonAccount.person_id == person.id)
                .values(person_id=existing.id, conversation_id=None)
            )
            session.delete(person)
        else:
            person.conversation_id = None
    for account in session.scalars(select(PersonAccount).where(PersonAccount.conversation_id == conversation_id)).all():
        account.conversation_id = None
    for row in session.scalars(select(WeChatFriend).where(WeChatFriend.conversation_id == conversation_id)).all():
        row.conversation_id = None
    for row in session.scalars(select(PersonRelation).where(PersonRelation.conversation_id == conversation_id)).all():
        row.conversation_id = None
    session.flush()


@app.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, session: Session = Depends(get_session)):
    conversation = require_conversation(conversation_id, session)
    case_id = conversation.case_id
    analysis_ids = session.scalars(
        select(Analysis.id).where(Analysis.conversation_id == conversation_id)
    ).all()
    if analysis_ids:
        session.execute(delete(Review).where(Review.analysis_id.in_(analysis_ids)))
        session.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
    promote_conversation_materials(session, case_id, conversation_id)
    session.delete(conversation)
    session.commit()
    write_audit(session, case_id, "conversation.delete", "conversation", conversation_id)
    return Response(status_code=204)


@app.get("/cases/{case_id}/stats", response_model=CaseStats)
def case_stats(case_id: UUID, session: Session = Depends(get_session)):
    require_case(case_id, session)
    return CaseStats(
        source_files=session.scalar(
            select(func.count(SourceFile.id)).where(SourceFile.case_id == case_id)
        ) or 0,
        case_source_files=session.scalar(
            select(func.count(SourceFile.id)).where(
                SourceFile.case_id == case_id, SourceFile.conversation_id.is_(None)
            )
        ) or 0,
        messages=session.scalar(
            select(func.count(Message.id)).where(Message.case_id == case_id)
        ) or 0,
        analyses=session.scalar(
            select(func.count(Analysis.id)).where(Analysis.case_id == case_id)
        ) or 0,
        reviews=session.scalar(
            select(func.count(Review.id))
            .join(Analysis, Review.analysis_id == Analysis.id)
            .where(Analysis.case_id == case_id)
        ) or 0,
    )


@app.post("/cases/{case_id}/sources", response_model=ImportOut, status_code=201)
async def upload_source(
    case_id: UUID,
    file: UploadFile = File(...),
    conversation_id: UUID | None = None,
    owner_name: str | None = None,
    session: Session = Depends(get_session),
):
    require_case(case_id, session)
    scope = resolve_material_scope(case_id, conversation_id, session)
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(415, "当前仅支持 xlsx 文件")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件不能超过 50 MB")
    try:
        source, count, duplicate, person_count, friend_count = import_xlsx(
            session,
            case_id,
            file.filename,
            content,
            conversation_id=scope,
            owner_name=_text_or_none(owner_name),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    write_audit(
        session,
        case_id,
        "source.duplicate" if duplicate else "source.import",
        "source",
        source.id,
        {
            "filename": source.filename,
            "messages": count,
            "persons": person_count,
            "friends": friend_count,
            "scope": "conversation" if scope else "case",
            "conversation_id": str(scope) if scope else None,
        },
    )
    return ImportOut(
        source_file_id=source.id,
        sha256=source.sha256,
        imported_messages=count,
        imported_persons=person_count,
        imported_friends=friend_count,
        duplicate=duplicate,
        conversation_id=source.conversation_id,
        scope="conversation" if source.conversation_id else "case",
    )


def to_hit(message, score) -> SearchHit:
    return SearchHit(
        message_id=message.id,
        source_file_id=message.source_file_id,
        source_row=message.source_row,
        sent_at=message.sent_at,
        group_id=message.group_id,
        sender_id=message.sender_id,
        content=message.content,
        ip_address=message.ip_address,
        score=round(float(score), 6),
    )


def context_fields(message) -> dict:
    return {
        "message_id": message.id,
        "source_file_id": message.source_file_id,
        "source_row": message.source_row,
        "sent_at": message.sent_at,
        "group_id": message.group_id,
        "sender_id": message.sender_id,
        "content": message.content,
        "ip_address": message.ip_address,
    }


@app.get(
    "/cases/{case_id}/messages/{message_id}/context",
    response_model=list[ContextMessage],
)
def get_context(
    case_id: UUID,
    message_id: str,
    before: int = 3,
    after: int = 3,
    session: Session = Depends(get_session),
):
    require_case(case_id, session)
    if not 0 <= before <= 20 or not 0 <= after <= 20:
        raise HTTPException(422, "before 和 after 必须在 0 到 20 之间")
    try:
        window = context_window(session, case_id, message_id, before, after)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return [
        ContextMessage(
            **context_fields(message),
            relation="anchor" if distance == 0 else ("before" if distance < 0 else "after"),
            distance=distance,
        )
        for message, distance in window
    ]


@app.get(
    "/cases/{case_id}/messages/{message_id}/time-neighbors",
    response_model=list[TimeNeighbor],
)
def get_time_neighbors(
    case_id: UUID,
    message_id: str,
    minutes: int = 30,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    require_case(case_id, session)
    if not 1 <= minutes <= 1440 or not 1 <= limit <= 500:
        raise HTTPException(422, "minutes 必须在 1 到 1440 之间，limit 必须在 1 到 500 之间")
    try:
        neighbors = time_neighbors(session, case_id, message_id, minutes, limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return [
        TimeNeighbor(
            **context_fields(message),
            relation="anchor" if seconds == 0 else ("before" if seconds < 0 else "after"),
            seconds_from_anchor=seconds,
        )
        for message, seconds in neighbors
    ]


@app.get("/cases/{case_id}/search", response_model=list[SearchHit])
def search(
    case_id: UUID,
    q: str,
    limit: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    sender_id: str | None = None,
    group_id: str | None = None,
    ip_address: str | None = None,
    source_file_id: UUID | None = None,
    conversation_id: UUID | None = None,
    session: Session = Depends(get_session),
):
    require_case(case_id, session)
    if not q.strip():
        raise HTTPException(422, "检索问题不能为空")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    scope = resolve_material_scope(case_id, conversation_id, session)
    return [to_hit(message, score) for message, score in search_messages(
        session,
        case_id,
        q,
        limit,
        start_date=start_date,
        end_date=end_date,
        sender_id=sender_id,
        group_id=group_id,
        ip_address=ip_address,
        source_file_id=source_file_id,
        conversation_id=scope,
    )]


@app.get("/cases/{case_id}/keyword-search", response_model=list[SearchHit])
def keyword_search(
    case_id: UUID,
    q: str,
    sender_id: str | None = None,
    group_id: str | None = None,
    conversation_id: UUID | None = None,
    session: Session = Depends(get_session),
):
    case = require_case(case_id, session)
    if not q.strip():
        raise HTTPException(422, "关键词不能为空")
    scope = resolve_material_scope(case_id, conversation_id, session)
    sqlite_items = messages_to_chat_items(scoped_messages(session, case_id, scope))
    _, items = load_case_materials(case.code, sqlite_items, case.name, list_persons(session, case.id, scope))
    items = [
        item for item in items
        if not any((
            sender_id is not None and item.sender_id != sender_id,
            group_id is not None and item.group_id != group_id,
        ))
    ]
    return [to_search_hit(item, score) for item, score in search_keyword_items(q, items)]


def case_materials(case: Case, session: Session, conversation_id=None):
    sqlite_items = messages_to_chat_items(scoped_messages(session, case.id, conversation_id))
    return load_case_materials(case.code, sqlite_items, case.name, list_persons(session, case.id, conversation_id))


def case_chat_items(case: Case, session: Session, conversation_id=None) -> list[ChatItem]:
    return case_materials(case, session, conversation_id)[1]


@app.get("/cases/{case_id}/persons")
def list_case_persons(case_id: UUID, conversation_id: UUID | None = None, session: Session = Depends(get_session)):
    require_case(case_id, session)
    scope = resolve_material_scope(case_id, conversation_id, session)
    return [
        {
            "name": person.name,
            "wechat_id": person.wechat_id,
            "wx_nickname": person.wx_nickname,
        }
        for person in list_persons(session, case_id, scope)
        if person.name or person.wechat_id
    ]


@app.get("/cases/{case_id}/chat-labels")
def get_chat_labels(case_id: UUID, session: Session = Depends(get_session)):
    require_case(case_id, session)
    return load_chat_labels(case_id)


@app.put("/cases/{case_id}/chat-labels")
def update_chat_labels(case_id: UUID, payload: ChatLabelsIn, session: Session = Depends(get_session)):
    require_case(case_id, session)
    saved = save_chat_labels(case_id, payload.model_dump())
    write_audit(session, case_id, "chat-labels.update", "case", case_id, {})
    return saved


GROUP_TYPE_CLAIM = re.compile(
    r"群\s*[「“\"']?([A-Za-z0-9_-]+)[」”\"']?\s*[，,：:]*\s*疑似(?:为|是)\s*(.+?)(?=：|:|；|;|，|,|成员包括|成员仅|$)"
)
FOCUS_CASE_CLAIM = re.compile(
    r"群\s*[「“\"']?([A-Za-z0-9_-]+)[」”\"']?\s*[，,]\s*重点群"
)
KEYWORD_SPLIT = re.compile(r"[,，、;；\s]+")


def parse_focus_keywords(text: str) -> list[str]:
    seen: list[str] = []
    for part in KEYWORD_SPLIT.split(text or ""):
        word = part.strip()
        if len(word) < 2 or word in seen:
            continue
        seen.append(word)
        if len(seen) >= 30:
            break
    return seen


def group_wechat_owners(session: Session, messages: list[ChatItem], persons) -> dict[str, list[str]]:
    file_ids = {item.source_file_id for item in messages if item.source_file_id}
    if not file_ids:
        return {}
    files = session.scalars(select(SourceFile).where(SourceFile.id.in_(file_ids))).all()
    wechat_by_name: dict[str, str] = {}
    for person in persons:
        remark = (person.remark or "").strip()
        name = (person.name or "").strip()
        wechat = (person.wechat_id or person.wechat_alias or "").strip()
        if remark in UPLOADED_REMARKS and name and wechat:
            wechat_by_name.setdefault(name, wechat)
    labels: dict = {}
    for source in files:
        name, wechat = _file_owner(source.filename)
        if not name:
            continue
        account = (wechat or wechat_by_name.get(name) or "").strip()
        labels[source.id] = f"{name}的微信 {account}".strip() if account else f"{name}的微信"
    found: dict[str, list[str]] = {}
    for item in messages:
        label = labels.get(item.source_file_id)
        if not item.group_id or not label:
            continue
        owners = found.setdefault(item.group_id, [])
        if label not in owners:
            owners.append(label)
    return {group_id: sorted(owners) for group_id, owners in found.items()}


def group_focus_terms(messages: list[ChatItem], keywords: list[str]) -> dict[str, list[str]]:
    if not keywords:
        return {}
    needles = [(word, word.casefold()) for word in keywords]
    found: dict[str, list[str]] = {}
    for item in messages:
        if not item.group_id or not item.content:
            continue
        content = item.content.casefold()
        hits = found.setdefault(item.group_id, [])
        for word, needle in needles:
            if needle in content and word not in hits:
                hits.append(word)
    return {group_id: terms for group_id, terms in found.items() if terms}


def parse_inferred_group_types(statements: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for statement in statements:
        for match in GROUP_TYPE_CLAIM.finditer(str(statement or "")):
            group_id = match.group(1).strip()
            label = match.group(2).strip(" 。；;，,")
            if group_id and label:
                found[group_id] = label
    return found


def parse_case_focus_groups(statements: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for statement in statements:
        text = str(statement or "")
        if "可能相关" not in text:
            continue
        for match in FOCUS_CASE_CLAIM.finditer(text):
            group_id = match.group(1).strip()
            labels = found.setdefault(group_id, [])
            if group_id and "可能相关" not in labels:
                labels.append("可能相关")
    return found


def case_focus_groups(session: Session, case_id, conversation_id) -> dict[str, list[str]]:
    statement = select(Analysis).where(Analysis.case_id == case_id)
    if conversation_id:
        statement = statement.where(
            or_(Analysis.conversation_id == conversation_id, Analysis.conversation_id.is_(None))
        )
    rows = session.scalars(statement).all()
    statements = [
        str(claim.get("statement") or "")
        for row in rows
        for claim in (row.result or {}).get("claims") or []
    ]
    return parse_case_focus_groups(statements)


def inferred_group_types(session: Session, case_id) -> dict[str, str]:
    rows = session.scalars(select(Analysis).where(Analysis.case_id == case_id)).all()
    statements = [
        str(claim.get("statement") or "")
        for row in rows
        for claim in (row.result or {}).get("claims") or []
    ]
    return parse_inferred_group_types(statements)


@app.get("/cases/{case_id}/graph")
def case_graph(case_id: UUID, conversation_id: UUID | None = None, session: Session = Depends(get_session)):
    case = require_case(case_id, session)
    scope = resolve_material_scope(case_id, conversation_id, session)
    persons, items = case_materials(case, session, scope)
    paths = scoped_source_paths(session, case_id, scope)
    relationships, hidden_pairs, relation_overrides, hidden_names = apply_manual_relations(
        relationship_rows_from_sources(paths),
        list_person_relations(session, case_id, scope),
    )
    friends = list_wechat_friends(session, case_id, scope)
    return build_case_graph(persons, items, relationships, friends, hidden_pairs, relation_overrides, hidden_names)


@app.post("/cases/{case_id}/friend-screen")
def friend_screen(case_id: UUID, payload: FriendScreenIn, conversation_id: UUID | None = None, session: Session = Depends(get_session)):
    case = require_case(case_id, session)
    names = [name.strip() for name in payload.names if str(name or "").strip()]
    if len(names) < 2:
        raise HTTPException(422, "请至少选择两个人进行好友筛查")
    scope = resolve_material_scope(case_id, conversation_id, session)
    persons, _items = case_materials(case, session, scope)
    friends = list_wechat_friends(session, case_id, scope)
    return screen_wechat_friends(persons, friends, names)


def _text_or_none(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def require_case_person(session: Session, case_id, person_id: UUID) -> Person:
    person = session.get(Person, person_id)
    if not person or person.case_id != case_id:
        raise HTTPException(404, "人员不存在")
    return person


def upsert_person_relation(session: Session, case_id, scope, source_name: str, target_name: str, relation: str, removed: bool = False) -> PersonRelation:
    source_name = source_name.strip()
    target_name = target_name.strip()
    relation = (relation or "关系人").strip() or "关系人"
    statement = select(PersonRelation).where(
        PersonRelation.case_id == case_id,
        PersonRelation.source_name == source_name,
        PersonRelation.target_name == target_name,
    )
    if scope:
        statement = statement.where(
            or_(PersonRelation.conversation_id.is_(None), PersonRelation.conversation_id == scope)
        )
    else:
        statement = statement.where(PersonRelation.conversation_id.is_(None))
    row = session.scalar(statement)
    if row is None:
        row = PersonRelation(
            case_id=case_id,
            conversation_id=scope,
            source_name=source_name,
            target_name=target_name,
            relation=relation,
            removed=removed,
        )
        session.add(row)
    else:
        row.relation = relation
        row.removed = removed
    return row


def is_subject_person(person: Person) -> bool:
    return (person.remark or "").strip() in ("", "本人", "主体", "主要人员")


def apply_person_fields(person: Person, payload: PersonUpdateIn) -> None:
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key in {"owner_name", "relation", "as_subject"}:
            continue
        if key == "id_card":
            text = _text_or_none(value)
            if text:
                person.id_card = text
            continue
        if key == "name":
            text = _text_or_none(value)
            if text:
                person.name = text
            continue
        setattr(person, key, _text_or_none(value) if key != "gender" else (_text_or_none(value) or "未知"))


def rename_person_links(session: Session, case_id, old_name: str, new_name: str) -> None:
    if old_name == new_name:
        return
    for row in session.scalars(select(PersonRelation).where(PersonRelation.case_id == case_id)).all():
        if row.source_name == old_name:
            row.source_name = new_name
        if row.target_name == old_name:
            row.target_name = new_name
    prefix = f"{old_name}的"
    for person in session.scalars(select(Person).where(Person.case_id == case_id)).all():
        remark = (person.remark or "").strip()
        if remark.startswith(prefix):
            person.remark = f"{new_name}的{remark[len(prefix):]}"


def sync_related_remark(session: Session, case_id, owner_name: str, target_name: str, relation: str) -> None:
    person = session.scalar(select(Person).where(Person.case_id == case_id, Person.name == target_name))
    if person:
        person.remark = f"{owner_name}的{relation}"


@app.patch("/cases/{case_id}/persons/{person_id}")
def update_case_person(case_id: UUID, person_id: UUID, payload: PersonUpdateIn, session: Session = Depends(get_session)):
    require_case(case_id, session)
    person = require_case_person(session, case_id, person_id)
    old_name = person.name
    if payload.id_card and payload.id_card.strip() != person.id_card:
        clash = session.scalar(
            select(Person).where(
                Person.case_id == case_id,
                Person.id_card == payload.id_card.strip(),
                Person.id != person.id,
            )
        )
        if clash:
            raise HTTPException(409, "该身份证号已存在")
    apply_person_fields(person, payload)
    if payload.as_subject is True:
        person.remark = "本人"
    if person.name != old_name:
        rename_person_links(session, case_id, old_name, person.name)
    session.flush()
    write_audit(session, case_id, "person.update", "person", person.id, {"name": person.name})
    return {"id": str(person.id), "name": person.name}


@app.post("/cases/{case_id}/persons", status_code=201)
def create_related_person(case_id: UUID, payload: PersonCreateIn, conversation_id: UUID | None = None, session: Session = Depends(get_session)):
    require_case(case_id, session)
    scope = resolve_material_scope(case_id, conversation_id, session)
    name = payload.name.strip()
    owner = (payload.owner_name or "").strip()
    relation = (payload.relation or "关系人").strip() or "关系人"
    id_card = _text_or_none(payload.id_card) or f"manual-{uuid.uuid4().hex[:16]}"
    existing = session.scalar(select(Person).where(Person.case_id == case_id, Person.id_card == id_card))
    if existing:
        raise HTTPException(409, "该身份证号已存在")
    if not owner:
        clash = session.scalar(select(Person).where(Person.case_id == case_id, Person.name == name))
        if clash:
            if (clash.remark or "").strip() == "本人":
                raise HTTPException(409, "该当事人已存在")
            clash.remark = "本人"
            session.flush()
            write_audit(session, case_id, "person.update", "person", clash.id, {"name": name, "as_subject": True})
            return {"id": str(clash.id), "name": clash.name, "role": "subject"}
    person = Person(
        case_id=case_id,
        conversation_id=None if not owner else scope,
        id_card=id_card,
        name=name,
        gender=_text_or_none(payload.gender) or "未知",
        delivery_address=_text_or_none(payload.delivery_address),
        takeout_address=_text_or_none(payload.takeout_address),
        wechat_id=_text_or_none(payload.wechat_id),
        wechat_alias=_text_or_none(payload.wechat_alias),
        original_wechat_id=_text_or_none(payload.original_wechat_id),
        wx_nickname=_text_or_none(payload.wx_nickname),
        phone=_text_or_none(payload.phone),
        remark="本人" if not owner else f"{owner}的{relation}",
    )
    session.add(person)
    session.flush()
    if owner:
        upsert_person_relation(session, case_id, scope, owner, name, relation, False)
    write_audit(session, case_id, "person.create", "person", person.id, {"name": name, "role": "subject" if not owner else "related"})
    return {"id": str(person.id), "name": person.name, "role": "subject" if not owner else "related"}


@app.delete("/cases/{case_id}/persons/{person_id}", status_code=204)
def delete_related_person(
    case_id: UUID,
    person_id: UUID,
    conversation_id: UUID | None = None,
    as_subject: bool = False,
    session: Session = Depends(get_session),
):
    require_case(case_id, session)
    person = require_case_person(session, case_id, person_id)
    scope = resolve_material_scope(case_id, conversation_id, session)
    subject = is_subject_person(person)
    if subject and not as_subject:
        raise HTTPException(422, "当事人请在左侧当事人栏删除")
    name = person.name
    victims = [person]
    if subject:
        prefix = f"{name}的"
        victims.extend(
            row
            for row in session.scalars(select(Person).where(Person.case_id == case_id, Person.id != person.id)).all()
            if (row.remark or "").startswith(prefix)
        )
    names = {row.name for row in victims}
    for victim in victims:
        session.execute(delete(PersonAccount).where(PersonAccount.person_id == victim.id))
        session.delete(victim)
    if subject:
        session.execute(
            delete(PersonRelation).where(
                PersonRelation.case_id == case_id,
                or_(PersonRelation.source_name.in_(names), PersonRelation.target_name.in_(names)),
            )
        )
    else:
        upsert_person_relation(session, case_id, scope, name, name, "已删除", True)
    write_audit(session, case_id, "person.delete", "person", person_id, {"name": name, "as_subject": as_subject})
    return Response(status_code=204)


@app.put("/cases/{case_id}/relations")
def update_case_relation(case_id: UUID, payload: RelationUpdateIn, conversation_id: UUID | None = None, session: Session = Depends(get_session)):
    require_case(case_id, session)
    scope = resolve_material_scope(case_id, conversation_id, session)
    source = payload.source.strip()
    target = payload.target.strip()
    relation = payload.relation.strip()
    if source == target:
        raise HTTPException(422, "不能把人员关系改成指向自己")
    upsert_person_relation(session, case_id, scope, source, target, relation, payload.removed)
    write_audit(session, case_id, "relation.update", "person", f"{source}->{target}", {"relation": relation, "removed": payload.removed})
    return {"source": source, "target": target, "relation": relation, "removed": payload.removed}


@app.get("/cases/{case_id}/groups")
def list_case_groups(case_id: UUID, conversation_id: UUID | None = None, session: Session = Depends(get_session)):
    case = require_case(case_id, session)
    scope = resolve_material_scope(case_id, conversation_id, session)
    grouped: dict[str, list[ChatItem]] = {}
    for item in case_chat_items(case, session, scope):
        if item.group_id:
            grouped.setdefault(item.group_id, []).append(item)
    inferred = inferred_group_types(session, case_id)
    analysis_on = False
    if conversation_id:
        conversation = session.get(Conversation, conversation_id)
        analysis_on = bool(conversation and conversation.case_id == case_id and conversation.analysis_enabled)
    flat_messages = [item for rows in grouped.values() for item in rows]
    owner_map = group_wechat_owners(session, flat_messages, list_persons(session, case_id, scope))
    focus_map = group_focus_terms(
        flat_messages,
        parse_focus_keywords(case.keywords or "") if analysis_on else [],
    )
    people_map = {}
    case_map = {}
    if analysis_on:
        people_map = groups_with_uploaded_people(
            flat_messages,
            uploaded_people_index(list_persons(session, case_id, scope), list_wechat_friends(session, case_id, scope)),
        )
        case_map = case_focus_groups(session, case_id, conversation_id)
    result = []
    for group_id, messages in grouped.items():
        ordered = sorted(messages, key=lambda item: (item.sent_at or datetime.min, item.source_row))
        latest = ordered[-1]
        terms = focus_map.get(group_id, [])
        people = people_map.get(group_id, [])
        case_reasons = case_map.get(group_id, [])
        result.append({
            "group_id": group_id,
            "message_count": len(messages),
            "member_count": len({item.sender_id for item in messages if item.sender_id}),
            "latest_at": latest.sent_at,
            "latest_content": latest.content,
            "inferred_type": inferred.get(group_id, ""),
            "focus": bool(people or terms or case_reasons),
            "focus_terms": terms,
            "focus_people": people,
            "focus_case": case_reasons,
            "wechat_owners": owner_map.get(group_id, []),
        })
    result.sort(key=lambda item: item["latest_at"] or datetime.min, reverse=True)
    result.sort(key=lambda item: not item["focus"])
    return result


@app.get("/cases/{case_id}/group-messages", response_model=list[SearchHit])
def list_group_messages(
    case_id: UUID,
    group_id: str,
    limit: int = 500,
    conversation_id: UUID | None = None,
    session: Session = Depends(get_session),
):
    case = require_case(case_id, session)
    scope = resolve_material_scope(case_id, conversation_id, session)
    items = [item for item in case_chat_items(case, session, scope) if item.group_id == group_id]
    items.sort(key=lambda item: (item.sent_at or datetime.min, item.source_row))
    return [to_search_hit(item, 1.0) for item in items[-min(max(limit, 1), 5000):]]


def source_file_label(session: Session, source: SourceFile) -> str:
    count = session.scalar(select(func.count(Message.id)).where(Message.source_file_id == source.id)) or 0
    if count:
        return f"{source.filename}（聊天记录，{count} 条消息）"
    return f"{source.filename}（人员或关系材料）"


def case_overview_result(case: Case, session: Session, conversation_id) -> dict:
    sources = session.scalars(
        select(SourceFile).where(SourceFile.case_id == case.id).order_by(SourceFile.imported_at)
    ).all()
    case_files = [item for item in sources if item.conversation_id is None]
    conv_files = [item for item in sources if conversation_id and item.conversation_id == conversation_id]
    claims = [{
        "type": "fact",
        "statement": "；".join([
            f"案件名称：{case.name}",
            f"案件编号：{case.code}",
            f"案件类型：{getattr(case, 'category', '') or '未填写'}",
            f"承办人：{getattr(case, 'handler', '') or '未填写'}",
            f"承办单位：{getattr(case, 'unit', '') or '未填写'}",
            f"简要案情：{getattr(case, 'summary', '') or '未填写'}",
            f"重点关键词：{getattr(case, 'keywords', '') or '未填写'}",
        ]),
        "evidence_refs": [],
    }]
    if case_files:
        claims.append({
            "type": "fact",
            "statement": "案件材料（所有对话可用）：" + "；".join(source_file_label(session, item) for item in case_files),
            "evidence_refs": [],
        })
    else:
        claims.append({"type": "fact", "statement": "尚未导入案件材料。", "evidence_refs": []})
    if conv_files:
        claims.append({
            "type": "fact",
            "statement": "当前对话专属材料：" + "；".join(source_file_label(session, item) for item in conv_files),
            "evidence_refs": [],
        })
    claims.append({
        "type": "inference",
        "statement": "当前只能交流案件基本信息和文件清单。点击「材料智能分析」后，才会按原规则研判聊天记录和人员材料。",
        "evidence_refs": [],
    })
    return {
        "subjects": [case.name],
        "judgement_level": "明确证据",
        "claims": claims,
        "evidence": [],
        "counter_evidence": [],
        "alternatives": [{
            "hypothesis": "文件清单不等于材料内容已研判",
            "required_checks": ["点击材料智能分析后再核查聊天原文和人员关系"],
        }],
        "confidence": "高",
        "review_status": "待复核",
        "engine": "case-overview",
    }


def representative_evidence(chat_items, limit: int):
    groups: dict[str, list] = {}
    for item in chat_items:
        groups.setdefault(item.group_id or "", []).append(item)
    if not groups:
        return [to_search_hit(item, 1.0) for item in chat_items[: max(limit, 1)]]
    per_group = max(1, max(limit, 1) // max(len(groups), 1))
    hits = []
    for rows in groups.values():
        ordered = sorted(rows, key=lambda item: (item.sent_at is None, item.sent_at, item.source_row))
        for item in ordered[:per_group]:
            hits.append(to_search_hit(item, 1.0))
            if len(hits) >= max(limit, 1):
                return hits
    return hits


def run_analysis(case_id: UUID, payload: AnalysisIn, session: Session) -> AnalysisOut:
    case = require_case(case_id, session)
    history_records = []
    conversation = None
    if payload.conversation_id:
        conversation = require_conversation(payload.conversation_id, session)
        if conversation.case_id != case_id:
            raise HTTPException(422, "研判对话不属于当前案件")
        history_records = session.scalars(
            select(Analysis)
            .where(Analysis.conversation_id == payload.conversation_id)
            .order_by(Analysis.created_at.desc(), Analysis.id.desc())
            .limit(8)
        ).all()[::-1]
    if conversation is not None and not conversation.analysis_enabled:
        result = case_overview_result(case, session, conversation.id)
        record = Analysis(
            case_id=case_id,
            conversation_id=payload.conversation_id,
            question=payload.question,
            result=result,
            engine=result["engine"],
        )
        session.add(record)
        session.commit()
        write_audit(session, case_id, "analysis.create", "analysis", record.id, {"engine": result["engine"]})
        return AnalysisOut(id=record.id, **result)
    history_records = [item for item in history_records if item.engine != "case-overview"]
    history = [
        {
            "question": item.question,
            "judgement_level": item.result.get("judgement_level"),
            "claims": [claim.get("statement") for claim in item.result.get("claims", [])[:3]],
        }
        for item in history_records
    ]
    retrieval_question = " ".join([*(item.question for item in history_records[-3:]), payload.question])
    scope = resolve_material_scope(case_id, payload.conversation_id, session)
    sqlite_items = [
        item
        for item in messages_to_chat_items(scoped_messages(session, case_id, scope))
        if matches_filters(
            item,
            start_date=payload.start_date,
            end_date=payload.end_date,
            sender_id=payload.sender_id,
            group_id=payload.group_id,
            ip_address=payload.ip_address,
            source_file_id=payload.source_file_id,
        )
    ]
    persons, chat_items = load_case_materials(case.code, sqlite_items, case.name, list_persons(session, case_id, scope))
    chat_items = [
        item for item in chat_items
        if not any((
            payload.sender_id is not None and item.sender_id != payload.sender_id,
            payload.group_id is not None and item.group_id != payload.group_id,
            payload.ip_address is not None and item.ip_address != payload.ip_address,
        ))
    ]
    evidence_hits = evidence_harness(
        payload.question,
        chat_items,
        limit=payload.limit,
        before=payload.context_before,
        after=payload.context_after,
    )
    if not evidence_hits and retrieval_question != payload.question:
        evidence_hits = evidence_harness(
            retrieval_question,
            chat_items,
            limit=payload.limit,
            before=payload.context_before,
            after=payload.context_after,
        )
    if wants_full_profile(payload.question) and not evidence_hits and chat_items:
        evidence_hits = representative_evidence(chat_items, payload.limit)
    uploaded_index = uploaded_people_index(persons, list_wechat_friends(session, case_id, scope))
    uploaded_names = sorted(set(uploaded_index.values()))
    uploaded_group_marks = groups_with_uploaded_people(chat_items, uploaded_index)
    all_hits = [to_search_hit(item, 1.0) for item in chat_items]
    identifier = re.search(r"wxid_[A-Za-z0-9]+", retrieval_question, re.IGNORECASE)
    matched_people = list(dict.fromkeys(person.name for person in persons if person.name and person.name in payload.question))
    analysis_subjects = matched_people or ([identifier.group(0)] if identifier else [])
    if wants_full_profile(payload.question) and not analysis_subjects:
        analysis_subjects = [person.name for person in subjects_of(persons) if person.name]
    asks_ip = "登录地址" in payload.question or "IP地址" in payload.question.upper()
    exact_hits = [hit for hit in all_hits if identifier and hit.sender_id == identifier.group(0)]
    ip_hits = [hit for hit in exact_hits if hit.ip_address]
    if not chat_items and not persons:
        result = {
            "subjects": analysis_subjects,
            "judgement_level": "证据不足",
            "claims": [],
            "evidence": [],
            "counter_evidence": [],
            "alternatives": [{
                "hypothesis": "当前筛选条件下没有匹配消息",
                "required_checks": ["放宽或清空筛选条件后重新研判"],
            }],
            "confidence": "低",
            "review_status": "待复核",
            "engine": "no-evidence",
        }
    elif asks_ip and ip_hits:
        addresses = list(dict.fromkeys(hit.ip_address for hit in ip_hits))
        refs = [next(hit.message_id for hit in ip_hits if hit.ip_address == address) for address in addresses]
        result = {
            "subjects": analysis_subjects,
            "judgement_level": "明确证据",
            "claims": [{
                "type": "fact",
                "statement": f"{identifier.group(0)} 的IP登录地址为：{'、'.join(addresses)}",
                "evidence_refs": refs,
            }],
            "evidence": [hit.model_dump(mode="json") for hit in ip_hits],
            "counter_evidence": [],
            "alternatives": [{
                "hypothesis": "登录地址字段反映记录值，不等同于人员实际所在地",
                "required_checks": ["结合登录时间、运营商归属和原始日志复核"],
            }],
            "confidence": "高",
            "review_status": "待复核",
            "engine": "structured-field-lookup",
        }
    else:
        subjects = subjects_of(persons) if persons else []
        related = related_of(persons, subjects) if persons else []
        profiles = build_group_profiles(chat_items, subjects, related)
        briefing = build_llm_briefing(persons, profiles, payload.question)
        focus_keywords = parse_focus_keywords(case.keywords or "")
        briefing["case"] = {
            "name": case.name,
            "category": case.category or "",
            "summary": case.summary or "",
            "keywords": focus_keywords,
        }
        task_notes = [
            "重点群满足其中一条就要标出，两条都满足也要标出。",
            "条件一：同一个群里出现两名及以上已导入人员。写成：群{群ID}，重点群，已导入人员包括{姓名}",
            "条件二：群聊撞上重点关键词，或根据案件名称、类型和简要案情判断该群可能与案情有关。写成：群{群ID}，重点群，可能相关，依据：{引用的原话}",
        ]
        if focus_keywords:
            task_notes.append("先阅读案件信息和重点关键词，再阅读群聊原文，提取与这些关键词相关、以及与简要案情可能相关的发言")
        if uploaded_names:
            briefing["uploaded_people"] = uploaded_names
            task_notes.append(f"已导入人员为：{'、'.join(uploaded_names)}。只根据已上传数据识别这些人。")
        briefing["task"] = [*task_notes, *briefing.get("task", [])]
        runtime = load_runtime_settings(case_id)
        model_settings = runtime["model"]
        prompt_parts = []
        for item in runtime["system_prompts"]:
            if item["enabled"]:
                if item.get("builtin"):
                    prompt_parts.append(SYSTEM_PROMPT)
                if item["content"]:
                    prompt_parts.append(item["content"])
        prompt = "\n\n".join(prompt_parts)
        prompt += enabled_skill_prompt(case_id, runtime.get("skills", []))
        if payload.model_mode == "api":
            config = payload.api_config
            api_settings = model_settings["api"]
            base_url = normalize_llm_base_url(str(config.base_url) if config else api_settings.get("base_url", ""))
            model = config.model if config else api_settings["model"]
            api_key = config.api_key.get_secret_value() if config else api_settings.get("api_key", "")
            if not all((base_url, model)):
                raise HTTPException(503, "请在前端填写完整 API 地址和模型名，地址需以 http:// 或 https:// 开头")
            hostname = (urlparse(base_url).hostname or "").lower()
            if settings.api_llm_allowed_hosts and hostname not in settings.api_llm_allowed_hosts:
                raise HTTPException(403, "该 API 地址不在后端允许列表中")
            api_client = LocalLlmClient(
                base_url=base_url,
                model=model,
                api_key=api_key,
                local_options=False,
                temperature=model_settings["temperature"],
                top_p=model_settings["top_p"],
                max_tokens=max(int(model_settings.get("max_tokens") or 0), 8192),
                system_prompt=prompt,
            )
            try:
                model_result = api_client.analyse(payload.question, evidence_hits, history, briefing)
                model_result = enforce_grounded_answer(payload.question, model_result, evidence_hits)
            except LlmError as exc:
                raise HTTPException(502, str(exc)) from exc
            finally:
                api_client.client.close()
            counter_refs = set(model_result.counter_evidence_refs)
            result = {
                "subjects": analysis_subjects,
                **model_result.model_dump(exclude={"counter_evidence_refs"}),
                "evidence": [hit.model_dump(mode="json") for hit in evidence_hits if hit.message_id not in counter_refs],
                "counter_evidence": [hit.model_dump(mode="json") for hit in evidence_hits if hit.message_id in counter_refs],
                "review_status": "待复核",
                "engine": model,
            }
            record_usage(case_id, model, "api", api_client.last_usage)
        elif model_settings["local"].get("enabled", settings.llm_enabled):
            local_settings = model_settings["local"]
            local_client = LocalLlmClient(
                base_url=local_settings["base_url"], model=local_settings["model"],
                temperature=model_settings["temperature"], top_p=model_settings["top_p"],
                max_tokens=model_settings["max_tokens"], system_prompt=prompt,
            )
            try:
                model_result = local_client.analyse(payload.question, evidence_hits, history, briefing)
                model_result = enforce_grounded_answer(payload.question, model_result, evidence_hits)
            except LlmError as exc:
                raise HTTPException(502, str(exc)) from exc
            finally:
                local_client.client.close()
            counter_refs = set(model_result.counter_evidence_refs)
            result = {
                "subjects": analysis_subjects,
                **model_result.model_dump(exclude={"counter_evidence_refs"}),
                "evidence": [hit.model_dump(mode="json") for hit in evidence_hits if hit.message_id not in counter_refs],
                "counter_evidence": [hit.model_dump(mode="json") for hit in evidence_hits if hit.message_id in counter_refs],
                "review_status": "待复核",
                "engine": local_settings["model"],
            }
            record_usage(case_id, local_settings["model"], "local", local_client.last_usage)
        else:
            has_material = bool(persons or chat_items)
            result = {
                "subjects": analysis_subjects,
                "judgement_level": "存在迹象但歧义" if has_material else "证据不足",
                "claims": [{
                    "type": "inference",
                    "statement": "人员、关系人和群聊材料已接入，等待大模型按原规则生成总结与群研判。",
                    "evidence_refs": [hit.message_id for hit in evidence_hits[:3]],
                }] if has_material else [],
                "evidence": [hit.model_dump(mode="json") for hit in evidence_hits],
                "counter_evidence": [],
                "alternatives": [{
                    "hypothesis": "人员、关系人和群聊统计已整理，等待大模型生成总结与群研判",
                    "required_checks": ["切换到 API 模型并填写接口，或启用本地大模型"],
                }],
                "confidence": "低",
                "review_status": "待复核",
                "engine": "awaiting-llm",
            }
    if wants_full_profile(payload.question) and uploaded_group_marks:
        notes = [{
            "type": "inference",
            "statement": f"群{group_id}，重点群，已导入人员包括{'、'.join(names)}",
            "evidence_refs": [],
        } for group_id, names in sorted(uploaded_group_marks.items())]
        result["claims"] = [*notes, *(result.get("claims") or [])]
    record = Analysis(
        case_id=case_id,
        conversation_id=payload.conversation_id,
        question=payload.question,
        result=result,
        engine=result["engine"],
    )
    session.add(record)
    session.commit()
    write_audit(session, case_id, "analysis.create", "analysis", record.id, {"engine": result["engine"]})
    return AnalysisOut(id=record.id, **result)


@app.post("/cases/{case_id}/analysis", response_model=AnalysisOut, status_code=201)
def analyse(case_id: UUID, payload: AnalysisIn, session: Session = Depends(get_session)):
    return run_analysis(case_id, payload, session)


def task_out(task: AnalysisTask, session: Session) -> AnalysisTaskOut:
    result = None
    if task.analysis_id:
        analysis = session.get(Analysis, task.analysis_id)
        if analysis:
            result = AnalysisOut(id=analysis.id, **analysis.result)
    return AnalysisTaskOut(
        id=task.id,
        case_id=task.case_id,
        status=task.status,
        analysis_id=task.analysis_id,
        error=task.error,
        result=result,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def run_analysis_task(task_id: UUID, payload: AnalysisIn) -> None:
    with SessionLocal() as session:
        task = session.get(AnalysisTask, task_id)
        if not task or task.status == "cancelled":
            return
        task.status = "running"
        session.commit()
        try:
            result = run_analysis(task.case_id, payload, session)
            session.refresh(task)
            if task.status == "cancelled":
                session.execute(delete(Analysis).where(Analysis.id == result.id))
            else:
                task.status = "succeeded"
                task.analysis_id = result.id
            session.commit()
        except Exception as exc:
            session.rollback()
            task = session.get(AnalysisTask, task_id)
            if task and task.status != "cancelled":
                task.status = "failed"
                task.error = exc.detail if isinstance(exc, HTTPException) else str(exc)
                session.commit()


@app.post("/cases/{case_id}/analysis-tasks", response_model=AnalysisTaskOut, status_code=202)
def create_analysis_task(
    case_id: UUID,
    payload: AnalysisIn,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
):
    require_case(case_id, session)
    if payload.conversation_id:
        conversation = require_conversation(payload.conversation_id, session)
        if conversation.case_id != case_id:
            raise HTTPException(422, "研判对话不属于当前案件")
    stored_request = payload.model_dump(mode="json")
    if stored_request.get("api_config"):
        stored_request["api_config"]["api_key"] = ""
    task = AnalysisTask(case_id=case_id, request=stored_request)
    session.add(task)
    session.commit()
    session.refresh(task)
    background.add_task(run_analysis_task, task.id, payload)
    return task_out(task, session)


@app.get("/analysis-tasks/{task_id}", response_model=AnalysisTaskOut)
def get_analysis_task(task_id: UUID, session: Session = Depends(get_session)):
    task = session.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(404, "研判任务不存在")
    return task_out(task, session)


@app.post("/analysis-tasks/{task_id}/cancel", response_model=AnalysisTaskOut)
def cancel_analysis_task(task_id: UUID, session: Session = Depends(get_session)):
    task = session.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(404, "研判任务不存在")
    if task.status in {"queued", "running"}:
        task.status = "cancelled"
        session.commit()
        session.refresh(task)
    return task_out(task, session)


@app.post("/analyses/{analysis_id}/reviews", response_model=ReviewOut, status_code=201)
def create_review(
    analysis_id: UUID,
    payload: ReviewCreate,
    session: Session = Depends(get_session),
):
    analysis = session.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(404, "研判记录不存在")
    if payload.revised_result is not None:
        refs = {
            ref
            for claim in payload.revised_result.claims
            for ref in claim.evidence_refs
        } | set(payload.revised_result.counter_evidence_refs)
        existing_refs = set(
            session.scalars(
                select(Message.id).where(
                    Message.case_id == analysis.case_id,
                    Message.id.in_(refs),
                )
            ).all()
        ) if refs else set()
        if unknown_refs := refs - existing_refs:
            raise HTTPException(
                422,
                f"修改结果引用了不存在的本案证据：{', '.join(sorted(unknown_refs))}",
            )
    review = Review(
        analysis_id=analysis_id,
        decision=payload.decision,
        reviewer=payload.reviewer,
        comment=payload.comment,
        revised_result=(
            payload.revised_result.model_dump(mode="json")
            if payload.revised_result is not None
            else None
        ),
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    write_audit(session, analysis.case_id, "review.create", "review", review.id, {"decision": review.decision})
    return review


@app.get("/analyses/{analysis_id}/reviews", response_model=list[ReviewOut])
def list_reviews(analysis_id: UUID, session: Session = Depends(get_session)):
    if not session.get(Analysis, analysis_id):
        raise HTTPException(404, "研判记录不存在")
    return session.scalars(
        select(Review)
        .where(Review.analysis_id == analysis_id)
        .order_by(Review.created_at, Review.id)
    ).all()
