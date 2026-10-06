import json
import os
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Analysis, Case, Conversation, Review, SourceFile


def project_directory(case_id) -> Path:
    return settings.data_dir / str(case_id)


def sync_project(session: Session, case: Case) -> Path:
    root = project_directory(case.id)
    data_dir = root / "data"
    memory_dir = root / "conversations"
    agent_dir = root / "agent" / "skills"
    data_dir.mkdir(parents=True, exist_ok=True)
    memory_dir.mkdir(parents=True, exist_ok=True)
    agent_dir.mkdir(parents=True, exist_ok=True)

    sources = session.scalars(select(SourceFile).where(SourceFile.case_id == case.id)).all()
    conversations = session.scalars(
        select(Conversation).where(Conversation.case_id == case.id).order_by(Conversation.created_at, Conversation.id)
    ).all()
    write_json(root / "project.json", {
        "schema_version": 1,
        "id": case.id,
        "name": case.name,
        "code": case.code,
        "pinned": case.pinned,
        "archived": case.archived,
        "section": case.section,
        "created_at": case.created_at,
        "sources": [{
            "id": source.id,
            "filename": source.filename,
            "sha256": source.sha256,
            "path": relative_project_path(root, Path(source.stored_path)),
            "imported_at": source.imported_at,
        } for source in sources],
        "conversations": [{"id": item.id, "title": item.title} for item in conversations],
    })

    expected = set()
    for conversation in conversations:
        path = memory_dir / f"{conversation.id}.json"
        expected.add(path.name)
        turns = session.scalars(
            select(Analysis).where(Analysis.conversation_id == conversation.id).order_by(Analysis.created_at, Analysis.id)
        ).all()
        write_json(path, {
            "schema_version": 1,
            "case_id": case.id,
            "case_code": case.code,
            "conversation": {
                "id": conversation.id,
                "title": conversation.title,
                "pinned": conversation.pinned,
                "unread": conversation.unread,
                "archived": conversation.archived,
                "section": conversation.section,
                "created_at": conversation.created_at,
            },
            "turns": [{
                "id": turn.id,
                "question": turn.question,
                "result": turn.result,
                "engine": turn.engine,
                "created_at": turn.created_at,
                "reviews": [{
                    "id": review.id,
                    "decision": review.decision,
                    "reviewer": review.reviewer,
                    "comment": review.comment,
                    "revised_result": review.revised_result,
                    "created_at": review.created_at,
                } for review in session.scalars(
                    select(Review).where(Review.analysis_id == turn.id).order_by(Review.created_at, Review.id)
                )],
            } for turn in turns],
        })
    for path in memory_dir.glob("*.json"):
        if path.name not in expected:
            path.unlink()
    return root


def list_project_files(root: Path) -> list[dict]:
    return [{
        "path": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "modified_at": path.stat().st_mtime,
    } for path in sorted(root.rglob("*")) if path.is_file()]


def open_in_file_manager(root: Path) -> None:
    if os.name != "nt":
        raise OSError("当前仅支持在 Windows 资源管理器中打开")
    getattr(os, "startfile")(str(root))


def remove_project(case_id) -> None:
    root = project_directory(case_id).resolve()
    data_root = settings.data_dir.resolve()
    if root.parent != data_root or root.name != str(case_id):
        raise RuntimeError("拒绝删除案件目录之外的路径")
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                path.chmod(0o600)
        shutil.rmtree(root)


def relative_project_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def write_json(path: Path, payload) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=lambda value: value.isoformat() if hasattr(value, "isoformat") else str(value)),
        encoding="utf-8",
    )
    temporary.replace(path)
