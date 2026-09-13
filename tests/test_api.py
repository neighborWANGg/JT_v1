import json
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import app
from app.services import stable_message_id


def workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "聊天记录"
    sheet.append(["序号", "时间", "群ID", "微信号", "聊天内容", "IP登录地址"])
    sheet.append([1, "2026-09-01 10:00:00", "g1", "wxid_l3q7j5v9", "蓝盒还在原处吗", "127.0.0.1"])
    sheet.append([2, "2026-09-01 10:02:00", "g1", "u2", "昨晚没动，等通知", "127.0.0.2"])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def context_workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "聊天记录"
    sheet.append(["序号", "时间", "群ID", "微信号", "聊天内容", "IP登录地址"])
    sheet.append([1, "2026-09-01 09:58:00", "g1", "u1", "前文消息", "127.0.0.1"])
    sheet.append([2, "2026-09-01 10:00:00", "g1", "u2", "目标消息", "127.0.0.2"])
    sheet.append([3, "2026-09-01 10:02:00", "g1", "u1", "后文消息", "127.0.0.1"])
    sheet.append([4, "2026-09-01 10:01:00", "g2", "u3", "其他群消息", "127.0.0.3"])
    sheet.append([5, "2026-09-01 10:30:00", "g1", "u2", "超出时间邻域", "127.0.0.2"])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_stable_message_id():
    assert stable_message_id("abc", "聊天记录", 2) == stable_message_id("abc", "聊天记录", 2)
    assert stable_message_id("abc", "聊天记录", 2) != stable_message_id("abc", "聊天记录", 3)


def test_frontend_and_case_listing():
    with TestClient(app) as client:
        frontend = client.get("/ui").text
        assert "群聊分析研判系统" in frontend
        assert 'id="caseManageBtn"' in frontend
        assert 'id="keywordSearchBtn"' in frontend
        assert '/keyword-search' in frontend
        assert 'id="caseTable"' in frontend
        assert "无法连接后端" in frontend
        assert "location.protocol==='file:'" in frontend
        assert client.get("/cases").json() == []


def test_rejects_invalid_workbook():
    with TestClient(app) as client:
        case_id = client.post("/cases", json={"name": "测试案件", "code": "INVALID-001"}).json()["id"]
        response = client.post(
            f"/cases/{case_id}/sources",
            files={"file": ("fake.xlsx", b"not a workbook", "application/octet-stream")},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "文件不是有效的 xlsx 工作簿"


def test_end_to_end():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}

        created = client.post("/cases", json={"name": "测试案件", "code": "CASE-001"})
        assert created.status_code == 201
        case_id = created.json()["id"]
        payload = workbook_bytes()

        imported = client.post(
            f"/cases/{case_id}/sources",
            files={"file": ("chat.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert imported.status_code == 201
        assert imported.json()["imported_messages"] == 2

        stats = client.get(f"/cases/{case_id}/stats")
        assert stats.json() == {
            "source_files": 1,
            "messages": 2,
            "analyses": 0,
            "reviews": 0,
        }

        duplicate = client.post(
            f"/cases/{case_id}/sources",
            files={"file": ("chat.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert duplicate.json()["duplicate"] is True

        results = client.get(f"/cases/{case_id}/search", params={"q": "蓝盒还在原处吗"})
        assert results.status_code == 200
        assert results.json()[0]["content"] == "蓝盒还在原处吗"
        assert results.json()[0]["source_row"] == 2

        conversation = client.post(
            f"/cases/{case_id}/conversations", json={"title": "蓝盒研判"}
        )
        assert conversation.status_code == 201
        conversation_id = conversation.json()["id"]
        assert client.get(f"/cases/{case_id}/conversations").json()[0]["title"] == "蓝盒研判"

        analysis = client.post(
            f"/cases/{case_id}/analysis",
            json={"question": "蓝盒还在原处吗", "conversation_id": conversation_id, "limit": 10},
        )
        assert analysis.status_code == 201
        assert analysis.json()["review_status"] == "待复核"
        assert analysis.json()["engine"] == "awaiting-llm"
        assert analysis.json()["evidence"] == []
        turns = client.get(f"/conversations/{conversation_id}/turns")
        assert turns.status_code == 200
        assert turns.json()[0]["question"] == "蓝盒还在原处吗"
        assert turns.json()[0]["result"]["engine"] == "awaiting-llm"

        keyword_hits = client.get(f"/cases/{case_id}/keyword-search", params={"q": "蓝盒"})
        assert keyword_hits.status_code == 200
        assert keyword_hits.json()[0]["content"] == "蓝盒还在原处吗"

        updated = client.patch(
            f"/conversations/{conversation_id}",
            json={"title": "置顶蓝盒研判", "pinned": True, "unread": True, "section": "待复核"},
        )
        assert updated.status_code == 200
        assert updated.json()["pinned"] is True
        assert updated.json()["section"] == "待复核"

        forked = client.post(f"/conversations/{conversation_id}/fork", json={})
        assert forked.status_code == 201
        fork_id = forked.json()["id"]
        assert len(client.get(f"/conversations/{fork_id}/turns").json()) == 1

        other_case_id = client.post(
            "/cases", json={"name": "其他案件", "code": "CASE-002"}
        ).json()["id"]
        blocked_move = client.patch(
            f"/conversations/{conversation_id}", json={"case_id": other_case_id}
        )
        assert blocked_move.status_code == 409

        ip_analysis = client.post(
            f"/cases/{case_id}/analysis",
            json={
                "question": "wxid_l3q7j5v9的登录地址是什么",
                "conversation_id": conversation_id,
                "limit": 6,
            },
        )
        assert ip_analysis.status_code == 201
        assert ip_analysis.json()["engine"] == "structured-field-lookup"
        assert "127.0.0.1" in ip_analysis.json()["claims"][0]["statement"]
        assert ip_analysis.json()["evidence"][0]["ip_address"] == "127.0.0.1"

        followup = client.post(
            f"/cases/{case_id}/analysis",
            json={
                "question": "他的登录地址呢",
                "conversation_id": conversation_id,
                "limit": 6,
            },
        )
        assert followup.status_code == 201
        assert followup.json()["engine"] == "structured-field-lookup"
        assert "127.0.0.1" in followup.json()["claims"][0]["statement"]

        queued = client.post(
            f"/cases/{case_id}/analysis-tasks",
            json={
                "question": "wxid_l3q7j5v9的登录地址是什么",
                "conversation_id": conversation_id,
                "limit": 6,
            },
        )
        assert queued.status_code == 202
        completed = client.get(f"/analysis-tasks/{queued.json()['id']}")
        assert completed.json()["status"] == "succeeded"
        assert completed.json()["result"]["engine"] == "structured-field-lookup"

        api_unconfigured = client.post(
            f"/cases/{case_id}/analysis",
            json={"question": "蓝盒是否移动", "model_mode": "api", "limit": 2},
        )
        assert api_unconfigured.status_code == 503
        assert "前端填写 API 地址和模型名" in api_unconfigured.json()["detail"]

        analysis_id = analysis.json()["id"]
        invalid_revision = client.post(
            f"/analyses/{analysis_id}/reviews",
            json={"decision": "修改", "reviewer": "林警官", "comment": "需要修改"},
        )
        assert invalid_revision.status_code == 422

        reviewed = client.post(
            f"/analyses/{analysis_id}/reviews",
            json={"decision": "采纳", "reviewer": "林警官", "comment": "证据引用无误"},
        )
        assert reviewed.status_code == 201
        assert reviewed.json()["decision"] == "采纳"

        evidence_id = client.get(f"/cases/{case_id}/keyword-search", params={"q": "蓝盒"}).json()[0]["message_id"]
        modified = client.post(
            f"/analyses/{analysis_id}/reviews",
            json={
                "decision": "修改",
                "reviewer": "周警官",
                "comment": "降低结论等级",
                "revised_result": {
                    "judgement_level": "证据不足",
                    "claims": [{
                        "type": "fact",
                        "statement": "存在相关原始消息",
                        "evidence_refs": [evidence_id],
                    }],
                    "counter_evidence_refs": [],
                    "alternatives": [],
                    "confidence": "低",
                },
            },
        )
        assert modified.status_code == 201

        history = client.get(f"/analyses/{analysis_id}/reviews")
        assert history.status_code == 200
        assert [item["decision"] for item in history.json()] == ["采纳", "修改"]

        removed = client.delete(f"/conversations/{conversation_id}")
        assert removed.status_code == 204
        assert client.delete(f"/conversations/{fork_id}").status_code == 204
        assert client.get(f"/cases/{case_id}/conversations").json() == []
        assert client.get(f"/analyses/{analysis_id}/reviews").status_code == 404


def test_context_and_time_neighbors_stay_in_group():
    with TestClient(app) as client:
        case_id = client.post(
            "/cases", json={"name": "上下文测试", "code": "CTX-001"}
        ).json()["id"]
        imported = client.post(
            f"/cases/{case_id}/sources",
            files={"file": ("context.xlsx", context_workbook_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert imported.json()["imported_messages"] == 5

        anchor = client.get(
            f"/cases/{case_id}/search", params={"q": "目标消息", "limit": 1}
        ).json()[0]

        context = client.get(
            f"/cases/{case_id}/messages/{anchor['message_id']}/context",
            params={"before": 1, "after": 1},
        )
        assert context.status_code == 200
        assert [item["content"] for item in context.json()] == ["前文消息", "目标消息", "后文消息"]
        assert [item["relation"] for item in context.json()] == ["before", "anchor", "after"]

        neighbors = client.get(
            f"/cases/{case_id}/messages/{anchor['message_id']}/time-neighbors",
            params={"minutes": 5},
        )
        assert neighbors.status_code == 200
        assert [item["content"] for item in neighbors.json()] == ["前文消息", "目标消息", "后文消息"]
        assert [item["seconds_from_anchor"] for item in neighbors.json()] == [-120, 0, 120]

        options = client.get(f"/cases/{case_id}/filter-options").json()
        assert options["start_date"] == "2026-09-01"
        assert options["groups"] == ["g1", "g2"]
        assert options["senders"] == ["u1", "u2", "u3"]

        filtered = client.get(
            f"/cases/{case_id}/search",
            params={"q": "消息", "group_id": "g2", "sender_id": "u3"},
        )
        assert [item["content"] for item in filtered.json()] == ["其他群消息"]

        conflict = client.get(
            f"/cases/{case_id}/search",
            params={"q": "消息", "start_date": "2026-09-02", "end_date": "2026-09-01"},
        )
        assert conflict.status_code == 422
        assert conflict.json()["detail"] == "开始日期不能晚于结束日期"

        no_evidence = client.post(
            f"/cases/{case_id}/analysis",
            json={"question": "目标消息在哪里", "sender_id": "does-not-exist"},
        )
        assert no_evidence.status_code == 201
        assert no_evidence.json()["engine"] == "no-evidence"
        assert no_evidence.json()["judgement_level"] == "证据不足"


def test_case_data_management_and_audit():
    with TestClient(app) as client:
        case_id = client.post(
            "/cases", json={"name": "数据管理测试", "code": "DATA-001"}
        ).json()["id"]
        client.post(
            f"/cases/{case_id}/sources",
            files={"file": ("chat.xlsx", workbook_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        sources = client.get(f"/cases/{case_id}/sources")
        assert sources.json()[0]["filename"] == "chat.xlsx"
        audit = client.get(f"/cases/{case_id}/audit-logs")
        assert {item["action"] for item in audit.json()} >= {"case.create", "source.import"}
        exported = client.get(f"/cases/{case_id}/export")
        assert exported.status_code == 200
        assert exported.json()["case"]["code"] == "DATA-001"
        assert len(exported.json()["messages"]) == 2
        archived = client.patch(f"/cases/{case_id}", json={"archived": True})
        assert archived.json()["archived"] is True
        assert client.delete(f"/cases/{case_id}").status_code == 204
        assert client.get(f"/cases/{case_id}/stats").status_code == 404


def test_standard_retrieval_questions():
    questions = json.loads((Path(__file__).parent / "eval_questions.json").read_text(encoding="utf-8"))
    with TestClient(app) as client:
        case_id = client.post("/cases", json={"name": "检索基准", "code": "EVAL-001"}).json()["id"]
        client.post(
            f"/cases/{case_id}/sources",
            files={"file": ("chat.xlsx", workbook_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        for item in questions:
            hit = client.get(f"/cases/{case_id}/search", params={"q": item["question"], "limit": 1}).json()[0]
            for field, expected in item.items():
                if field.startswith("expected_"):
                    assert hit[field.removeprefix("expected_")] == expected
