from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
import re
from urllib.parse import urlparse
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal, get_session, init_db
from .llm import LlmError, LocalLlmClient, llm_client
from .models import Analysis, AnalysisTask, AuditLog, Case, Conversation, Message, Review, SourceFile
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
    SearchHit,
    SourceOut,
    SourceOption,
    TimeNeighbor,
)
from .investigation import (
    ChatItem,
    build_group_profiles,
    build_llm_briefing,
    load_case_materials,
    related_of,
    search_keyword_items,
    subjects_of,
    to_search_hit,
)
from .services import (
    context_window,
    import_xlsx,
    matches_filters,
    search_messages,
    time_neighbors,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="JT 群聊分析研判 API", version="0.1.0", lifespan=lifespan)
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
FRONTEND_PATH = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ui", include_in_schema=False)
def frontend():
    if not FRONTEND_PATH.exists():
        raise HTTPException(404, "前端文件不存在")
    return FileResponse(FRONTEND_PATH)


@app.get("/cases", response_model=list[CaseOut])
def list_cases(session: Session = Depends(get_session)):
    return session.scalars(select(Case).order_by(Case.created_at.desc())).all()


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


@app.get("/cases/{case_id}/filter-options", response_model=FilterOptions)
def filter_options(case_id: UUID, session: Session = Depends(get_session)):
    require_case(case_id, session)
    messages = session.scalars(select(Message).where(Message.case_id == case_id)).all()
    dates = [message.sent_at.date() for message in messages if message.sent_at]
    sources = session.scalars(
        select(SourceFile).where(SourceFile.case_id == case_id).order_by(SourceFile.filename)
    ).all()
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
    paths = [Path(path) for path in session.scalars(select(SourceFile.stored_path).where(SourceFile.case_id == case_id))]
    analysis_ids = session.scalars(select(Analysis.id).where(Analysis.case_id == case_id)).all()
    if analysis_ids:
        session.execute(delete(Review).where(Review.analysis_id.in_(analysis_ids)))
    session.execute(delete(AnalysisTask).where(AnalysisTask.case_id == case_id))
    session.execute(delete(Analysis).where(Analysis.case_id == case_id))
    session.execute(delete(Conversation).where(Conversation.case_id == case_id))
    session.execute(delete(Message).where(Message.case_id == case_id))
    session.execute(delete(SourceFile).where(SourceFile.case_id == case_id))
    session.delete(item)
    session.commit()
    for path in paths:
        if path.is_file() and settings.data_dir in path.resolve().parents:
            path.chmod(0o600)
            path.unlink()
    return Response(status_code=204)


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
    return item


@app.post("/conversations/{conversation_id}/fork", response_model=ConversationOut, status_code=201)
def fork_conversation(
    conversation_id: UUID,
    payload: ConversationFork,
    session: Session = Depends(get_session),
):
    source = require_conversation(conversation_id, session)
    clone = Conversation(case_id=source.case_id, title=payload.title or f"{source.title} · 分叉")
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


@app.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, session: Session = Depends(get_session)):
    conversation = require_conversation(conversation_id, session)
    analysis_ids = session.scalars(
        select(Analysis.id).where(Analysis.conversation_id == conversation_id)
    ).all()
    if analysis_ids:
        session.execute(delete(Review).where(Review.analysis_id.in_(analysis_ids)))
        session.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
    session.delete(conversation)
    session.commit()
    write_audit(session, conversation.case_id, "conversation.delete", "conversation", conversation_id)
    return Response(status_code=204)


@app.get("/cases/{case_id}/stats", response_model=CaseStats)
def case_stats(case_id: UUID, session: Session = Depends(get_session)):
    require_case(case_id, session)
    return CaseStats(
        source_files=session.scalar(
            select(func.count(SourceFile.id)).where(SourceFile.case_id == case_id)
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
    session: Session = Depends(get_session),
):
    require_case(case_id, session)
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(415, "当前仅支持 xlsx 文件")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件不能超过 50 MB")
    try:
        source, count, duplicate = import_xlsx(session, case_id, file.filename, content)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    write_audit(session, case_id, "source.duplicate" if duplicate else "source.import", "source", source.id, {"filename": source.filename, "messages": count})
    return ImportOut(
        source_file_id=source.id,
        sha256=source.sha256,
        imported_messages=count,
        duplicate=duplicate,
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
    session: Session = Depends(get_session),
):
    require_case(case_id, session)
    if not q.strip():
        raise HTTPException(422, "检索问题不能为空")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, "开始日期不能晚于结束日期")
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
    )]


@app.get("/cases/{case_id}/keyword-search", response_model=list[SearchHit])
def keyword_search(
    case_id: UUID,
    q: str,
    sender_id: str | None = None,
    group_id: str | None = None,
    session: Session = Depends(get_session),
):
    case = require_case(case_id, session)
    if not q.strip():
        raise HTTPException(422, "关键词不能为空")
    sqlite_items = [
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
        for message in session.scalars(select(Message).where(Message.case_id == case_id)).all()
    ]
    _, items = load_case_materials(case.code, sqlite_items, case.name)
    items = [
        item for item in items
        if not any((
            sender_id is not None and item.sender_id != sender_id,
            group_id is not None and item.group_id != group_id,
        ))
    ]
    return [to_search_hit(item, score) for item, score in search_keyword_items(q, items)]


def run_analysis(case_id: UUID, payload: AnalysisIn, session: Session) -> AnalysisOut:
    case = require_case(case_id, session)
    history_records = []
    if payload.conversation_id:
        conversation = require_conversation(payload.conversation_id, session)
        if conversation.case_id != case_id:
            raise HTTPException(422, "研判对话不属于当前案件")
        history_records = session.scalars(
            select(Analysis)
            .where(Analysis.conversation_id == payload.conversation_id)
            .order_by(Analysis.created_at.desc(), Analysis.id.desc())
            .limit(5)
        ).all()[::-1]
    history = [
        {
            "question": item.question,
            "judgement_level": item.result.get("judgement_level"),
            "claims": [claim.get("statement") for claim in item.result.get("claims", [])[:3]],
        }
        for item in history_records
    ]
    retrieval_question = " ".join([*(item.question for item in history_records[-3:]), payload.question])
    sqlite_items = [
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
        for message in session.scalars(select(Message).where(Message.case_id == case_id)).all()
        if matches_filters(
            message,
            start_date=payload.start_date,
            end_date=payload.end_date,
            sender_id=payload.sender_id,
            group_id=payload.group_id,
            ip_address=payload.ip_address,
            source_file_id=payload.source_file_id,
        )
    ]
    persons, chat_items = load_case_materials(case.code, sqlite_items, case.name)
    chat_items = [
        item for item in chat_items
        if not any((
            payload.sender_id is not None and item.sender_id != payload.sender_id,
            payload.group_id is not None and item.group_id != payload.group_id,
            payload.ip_address is not None and item.ip_address != payload.ip_address,
        ))
    ]
    all_hits = [to_search_hit(item, 1.0) for item in chat_items]
    identifier = re.search(r"wxid_[A-Za-z0-9]+", retrieval_question, re.IGNORECASE)
    asks_ip = "登录地址" in payload.question or "IP地址" in payload.question.upper()
    exact_hits = [hit for hit in all_hits if identifier and hit.sender_id == identifier.group(0)]
    ip_hits = [hit for hit in exact_hits if hit.ip_address]
    if not chat_items:
        result = {
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
        briefing = build_llm_briefing(persons, profiles)
        if payload.model_mode == "api":
            config = payload.api_config
            base_url = str(config.base_url).rstrip("/") if config else settings.api_llm_base_url
            model = config.model if config else settings.api_llm_model
            api_key = config.api_key.get_secret_value() if config else settings.api_llm_api_key
            if not all((base_url, model)):
                raise HTTPException(503, "请在前端填写 API 地址和模型名，或配置后端默认值")
            hostname = (urlparse(base_url).hostname or "").lower()
            if settings.api_llm_allowed_hosts and hostname not in settings.api_llm_allowed_hosts:
                raise HTTPException(403, "该 API 地址不在后端允许列表中")
            api_client = LocalLlmClient(
                base_url=base_url,
                model=model,
                api_key=api_key,
                local_options=False,
            )
            try:
                model_result = api_client.analyse(payload.question, [], history, briefing)
            except LlmError as exc:
                raise HTTPException(502, str(exc)) from exc
            finally:
                api_client.client.close()
            result = {
                **model_result.model_dump(exclude={"counter_evidence_refs"}),
                "evidence": [],
                "counter_evidence": [],
                "review_status": "待复核",
                "engine": model,
            }
        elif settings.llm_enabled:
            try:
                model_result = llm_client.analyse(payload.question, [], history, briefing)
            except LlmError as exc:
                raise HTTPException(502, str(exc)) from exc
            result = {
                **model_result.model_dump(exclude={"counter_evidence_refs"}),
                "evidence": [],
                "counter_evidence": [],
                "review_status": "待复核",
                "engine": settings.llm_model,
            }
        else:
            result = {
                "judgement_level": "证据不足",
                "claims": [],
                "evidence": [],
                "counter_evidence": [],
                "alternatives": [{
                    "hypothesis": "人员、关系人和群聊统计已整理，等待大模型生成总结与群研判",
                    "required_checks": ["切换到 API 模型并填写接口，或启用本地大模型"],
                }],
                "confidence": "低",
                "review_status": "待复核",
                "engine": "awaiting-llm",
            }
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
