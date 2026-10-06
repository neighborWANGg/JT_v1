import json

import httpx

from app.llm import LocalLlmClient, enforce_grounded_answer, normalize_llm_base_url
from app.schemas import ModelAnalysis
from app.schemas import AnalysisIn, SearchHit
from app.services import LmStudioEmbedder


def test_normalize_llm_base_url_adds_protocol():
    assert normalize_llm_base_url("api.deepseek.com/v1") == "https://api.deepseek.com/v1"
    assert normalize_llm_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"
    assert normalize_llm_base_url("127.0.0.1:1234/v1") == "http://127.0.0.1:1234/v1"
    assert normalize_llm_base_url("") == ""


def test_llm_structured_output_and_evidence_validation():
    hit = SearchHit(
        message_id="msg-1",
        source_file_id="d6c5458e-8768-4b24-8ddd-cdf36a90de01",
        source_row=2,
        sent_at=None,
        group_id="g1",
        sender_id="u1",
        content="今晚换到北边库房",
        ip_address="127.0.0.1",
        score=0.9,
    )
    model_output = {
        "judgement_level": "较强迹象",
        "claims": [{"type": "inference", "statement": "存在位置变更表达", "evidence_refs": ["msg-1"]}],
        "counter_evidence_refs": [],
        "alternatives": [{"hypothesis": "仅讨论计划", "required_checks": ["核查后续消息"]}],
        "confidence": "中",
    }

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert str(request.url) == "https://api.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        assert body["model"] == "api-model"
        assert body["response_format"]["type"] == "json_object"
        assert "json" in body["messages"][0]["content"].lower()
        assert "reasoning_effort" not in body
        assert "chat_template_kwargs" not in body
        user_payload = json.loads(body["messages"][1]["content"])
        assert user_payload["conversation_history"][0]["question"] == "上一个问题"
        assert "不可作为事实证据" in user_payload["history_note"]
        assert user_payload["briefing"]["subjects"][0]["name"] == "周凯"
        assert "evidence 为空不代表没有材料" in user_payload["briefing_note"]
        assert "必须先回答 question" in user_payload["briefing_note"]
        assert "briefing" in body["messages"][0]["content"]
        assert "不要改成人员档案" in body["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(model_output, ensure_ascii=False)}}]})

    result = LocalLlmClient(
        httpx.MockTransport(handler),
        base_url="https://api.example/v1",
        model="api-model",
        api_key="test-key",
        local_options=False,
    ).analyse(
        "是否在转移物品",
        [hit],
        [{"question": "上一个问题"}],
        {"subjects": [{"name": "周凯"}], "related_persons": [], "groups": []},
    )
    assert result.judgement_level == "较强迹象"
    assert result.claims[0].evidence_refs == ["msg-1"]


def test_api_retries_when_provider_rejects_json_object():
    hit = SearchHit(
        message_id="msg-1",
        source_file_id="d6c5458e-8768-4b24-8ddd-cdf36a90de01",
        source_row=2,
        sent_at=None,
        group_id="g1",
        sender_id="u1",
        content="今晚换到北边库房",
        ip_address="127.0.0.1",
        score=0.9,
    )
    calls = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "unknown field response_format"}})
        assert body["model"] == "qwen-plus"
        assert "thinking" not in body
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "judgement_level": "明确证据",
            "claims": [{"type": "fact", "statement": "记录中有北边库房", "evidence_refs": ["msg-1"]}],
            "counter_evidence_refs": [],
            "alternatives": [],
            "confidence": "高",
        }, ensure_ascii=False)}}]})

    result = LocalLlmClient(
        httpx.MockTransport(handler),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        api_key="test-key",
        local_options=False,
    ).analyse("库房在哪", [hit], [], {})
    assert len(calls) == 2
    assert result.judgement_level == "明确证据"
    assert result.claims[0].evidence_refs == ["msg-1"]


def test_api_analysis_accepts_incomplete_deepseek_json():
    hit = SearchHit(
        message_id="msg-1",
        source_file_id="d6c5458e-8768-4b24-8ddd-cdf36a90de01",
        source_row=2,
        sent_at=None,
        group_id="g1",
        sender_id="u1",
        content="今晚换到北边库房",
        ip_address="127.0.0.1",
        score=0.9,
    )
    raw = (
        '{ "judgement_level": "明确证据", "extra_note": "ignore me",'
        ' "claims": [ { "type": "fact", "statement": "周凯是本人" },'
        ' { "type": "fact", "statement": "关系人刘桂香", "evidence_refs": ["ghost-id"] },'
        ' { "type": "fact", "statement": "未写完的群'
    )

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["max_tokens"] >= 8192
        return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})

    result = LocalLlmClient(
        httpx.MockTransport(handler),
        base_url="https://api.deepseek.com/v1",
        model="deepseek-flash",
        api_key="test-key",
        local_options=False,
    ).analyse("你好", [hit], [], {"subjects": [{"name": "周凯"}]})
    assert result.judgement_level == "明确证据"
    assert result.claims[0].statement == "周凯是本人"
    assert result.claims[1].statement == "关系人刘桂香"
    assert result.claims[0].evidence_refs == []
    assert all("ghost-id" not in claim.evidence_refs for claim in result.claims)


def test_lmstudio_embedding_order():
    def handler(request: httpx.Request):
        assert json.loads(request.content)["model"] == "text-embedding-nomic-embed-text-v1.5"
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 0, "embedding": [1.0, 0.0]},
        ]})

    assert LmStudioEmbedder(httpx.MockTransport(handler)).encode(["a", "b"]) == [
        [1.0, 0.0],
        [0.0, 1.0],
    ]


def test_grounding_harness_repairs_ignored_direct_evidence():
    hit = SearchHit(
        message_id="fish-1", source_file_id="d6c5458e-8768-4b24-8ddd-cdf36a90de01",
        source_row=8, sent_at=None, group_id="g1", sender_id="u1",
        content="这道鱼寓意年年有余", ip_address=None, score=10,
    )
    empty = ModelAnalysis.model_validate({
        "judgement_level": "证据不足", "claims": [], "counter_evidence_refs": [],
        "alternatives": [], "confidence": "低",
    })
    repaired = enforce_grounded_answer("饭局上是否有鱼", empty, [hit])
    assert repaired.judgement_level == "明确证据"
    assert repaired.claims[0].evidence_refs == ["fish-1"]


def test_frontend_api_config_is_validated_and_kept_secret():
    payload = AnalysisIn.model_validate({
        "question": "测试 API 调用",
        "model_mode": "api",
        "api_config": {
            "base_url": "https://api.example/v1",
            "model": "api-model",
            "api_key": "secret",
        },
    })
    assert str(payload.api_config.base_url).rstrip("/") == "https://api.example/v1"
    assert payload.api_config.api_key.get_secret_value() == "secret"
    assert "secret" not in repr(payload)
