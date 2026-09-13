import json

import httpx

from app.llm import LocalLlmClient
from app.schemas import AnalysisIn, SearchHit
from app.services import LmStudioEmbedder


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
        assert "briefing" in body["messages"][0]["content"]
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
