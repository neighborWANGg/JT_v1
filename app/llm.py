import json
import re

import httpx
from pydantic import ValidationError

from .config import settings
from .schemas import ModelAnalysis, SearchHit


class LlmError(RuntimeError):
    pass


SYSTEM_PROMPT = """/no_think
你是侦查辅助分析工具，不替代人工判断。
输入材料分两类，都必须使用：
1. briefing：已从案件库整理的本人、关系人、群统计，属于授权材料。
2. evidence：群聊原文。讨论具体发言时才引用其中的 message_id。
只能依据 briefing 与 evidence 作答，不得补充外部事实，不得输出思考过程。
当问题是总结人员、介绍某人，或判断群类型时：
- 必须根据 briefing 写出 claims：先总结本人全部个人信息，再分别总结各关系人
- 必须阅读每个群 chats 中的群聊原文，再按 groups 已排好的顺序判断每个群疑似是什么群（家庭群、同事群、朋友群、工作群、其他等），type 用 inference，并引用具体发言内容作为依据
- 不要自行检索违法犯罪关键词
- 来自 briefing 的 claims 的 evidence_refs 可以为空
- 只要 briefing 里有对应人员或群，judgement_level 不得为“证据不足”
仅当 briefing 与 evidence 都没有与问题相关的材料时，才输出“证据不足”。
讨论群聊原文时：事实主张应引用 evidence 的 message_id；推断必须标注 inference。
必须主动识别否认、未执行、歧义与替代解释。
包含“没、未、不是、取消、等通知”等直接否认或未执行表述的相关消息，必须放入 counter_evidence_refs。
事实陈述不得补充材料中没有明确出现的人、物或动作。"""

JSON_OBJECT_PROMPT = """
必须只输出一个 JSON 对象，不要输出 Markdown 或解释。JSON 字段如下：
{
  "judgement_level": "明确证据|较强迹象|存在迹象但歧义|证据不足|存在相反证据",
  "claims": [{"type": "fact|inference", "statement": "字符串", "evidence_refs": ["message_id"]}],
  "counter_evidence_refs": ["message_id"],
  "alternatives": [{"hypothesis": "字符串", "required_checks": ["字符串"]}],
  "confidence": "低|中|高"
}
若 briefing.subjects 非空，judgement_level 用“明确证据”或“较强迹象”，claims 必须包含本人总结、各关系人总结，以及在阅读各群 chats 原文后对每个群疑似类型的判断。
"""


def _extract_json(content: str) -> str:
    text = (content or "").strip()
    if not text:
        raise LlmError("模型返回空内容。请使用 deepseek-chat，不要使用 deepseek-reasoner")
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


class LocalLlmClient:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str = "",
        local_options: bool = True,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.local_options = local_options
        api_key = api_key.strip().removeprefix("Bearer ").strip()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self.client = httpx.Client(timeout=settings.llm_timeout_seconds, transport=transport, headers=headers)

    def analyse(
        self,
        question: str,
        evidence: list[SearchHit],
        history: list[dict] | None = None,
        briefing: dict | None = None,
    ) -> ModelAnalysis:
        evidence_payload = [
            {
                "message_id": hit.message_id,
                "time": hit.sent_at.isoformat() if hit.sent_at else None,
                "group_id": hit.group_id,
                "sender_id": hit.sender_id,
                "content": hit.content,
                "ip_address": hit.ip_address,
            }
            for hit in evidence
        ]
        system_prompt = SYSTEM_PROMPT if self.local_options else SYSTEM_PROMPT + JSON_OBJECT_PROMPT
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "conversation_history": history or [],
                            "history_note": "历史仅用于理解追问，不可作为事实证据",
                            "briefing": briefing or {},
                            "briefing_note": (
                                "briefing 是授权案件材料，不是空证据。"
                                "evidence 为空不代表没有材料。"
                                "请根据 briefing 生成可读总结和群研判，不要原样罗列字段，也不要输出证据不足。"
                            ),
                            "evidence": evidence_payload,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0.1,
            "max_tokens": 600 if self.local_options else 4096,
            "response_format": (
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "evidence_analysis",
                        "strict": True,
                        "schema": ModelAnalysis.model_json_schema(),
                    },
                }
                if self.local_options
                else {"type": "json_object"}
            ),
        }
        if self.local_options:
            request_body.update({
                "reasoning_effort": "none",
                "chat_template_kwargs": {"enable_thinking": False},
            })
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                json=request_body,
            )
            if response.is_error:
                detail = response.text.strip()[:800] or response.reason_phrase
                raise LlmError(f"模型服务 HTTP {response.status_code}：{detail}")
            message = response.json()["choices"][0]["message"]
            content = message.get("content") or message.get("reasoning_content") or ""
            result = ModelAnalysis.model_validate_json(_extract_json(content))
        except LlmError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValidationError, json.JSONDecodeError) as exc:
            raise LlmError(f"模型服务返回无效结果：{exc}") from exc

        allowed_refs = {hit.message_id for hit in evidence}
        if allowed_refs:
            returned_refs = {
                ref for claim in result.claims for ref in claim.evidence_refs
            } | set(result.counter_evidence_refs)
            unknown_refs = returned_refs - allowed_refs
            if unknown_refs:
                raise LlmError(f"模型引用了不存在的证据：{', '.join(sorted(unknown_refs))}")
        return result

    def judge_groups(self, subject_names: list[str], groups: list[dict]) -> list[dict]:
        if not groups:
            return []
        system_prompt = (
            SYSTEM_PROMPT
            if self.local_options
            else SYSTEM_PROMPT
            + """
必须只输出一个 JSON 对象，不要输出 Markdown 或解释。JSON 字段如下：
{
  "groups": [{"group_id": "字符串", "category": "关键群|普通家人群", "reason": "字符串"}]
}
"""
        )
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "阅读每个群的 chats 原文，判断该群疑似是什么类型",
                            "subjects": subject_names,
                            "rules": [
                                "排序依据：本人发言次数、关系人数量",
                                "必须阅读 chats 里的群聊原文，再结合关系人构成判断类型",
                                "输出疑似类型，如家庭群、同事群、朋友群、工作群、其他，并引用具体发言",
                            ],
                            "groups": groups,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0.1,
            "max_tokens": 800 if self.local_options else 2048,
            "response_format": {"type": "json_object"},
        }
        if self.local_options:
            request_body.update({
                "reasoning_effort": "none",
                "chat_template_kwargs": {"enable_thinking": False},
            })
        try:
            response = self.client.post(f"{self.base_url}/chat/completions", json=request_body)
            if response.is_error:
                detail = response.text.strip()[:800] or response.reason_phrase
                raise LlmError(f"模型服务 HTTP {response.status_code}：{detail}")
            message = response.json()["choices"][0]["message"]
            content = message.get("content") or message.get("reasoning_content") or ""
            payload = json.loads(_extract_json(content))
        except LlmError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LlmError(f"群研判模型返回无效结果：{exc}") from exc
        groups_out = payload.get("groups") if isinstance(payload, dict) else payload
        if not isinstance(groups_out, list):
            raise LlmError("群研判模型未返回 groups 数组")
        return groups_out


llm_client = LocalLlmClient()
