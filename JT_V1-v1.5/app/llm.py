import json
import re

import httpx
from pydantic import ValidationError

from .config import settings
from .schemas import ModelAnalysis, SearchHit


class LlmError(RuntimeError):
    pass


def normalize_llm_base_url(value: str | None) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        url = ("http://" if re.match(r"^(localhost|127\.0\.0\.1)[:/]?", url, re.I) else "https://") + url
    return url.rstrip("/")


def enforce_grounded_answer(question: str, result: "ModelAnalysis", evidence: list[SearchHit]) -> "ModelAnalysis":
    """修复模型忽略“是否存在”类直接原文命中的情况。"""
    existence = re.search(r"(?:有没有|是否|有无)(?:有)?([\u4e00-\u9fff]{1,4})", question)
    focus = existence.group(1).rstrip("吗么呢") if existence else ""
    direct = next((hit for hit in evidence if focus and focus in hit.content), None)
    if direct and (result.judgement_level == "证据不足" or not result.claims):
        return ModelAnalysis.model_validate({
            "judgement_level": "明确证据",
            "claims": [{
                "type": "fact",
                "statement": f"案件数据明确记载：“{direct.content}”据此可确认案件记录中有“{focus}”。",
                "evidence_refs": [direct.message_id],
            }],
            "counter_evidence_refs": result.counter_evidence_refs,
            "alternatives": result.alternatives,
            "confidence": "高",
        })
    return result


SYSTEM_PROMPT = """/no_think
你是侦查辅助分析工具，不替代人工判断。
必须先回答用户的当前问题。打招呼、追问、具体核查，都只回答该问题，不要改成人员档案或全量群研判。
输入材料分两类，需要时使用：
1. briefing：已从案件库整理的本人、关系人、群统计，属于授权材料。
2. evidence：群聊原文。讨论具体发言时才引用其中的 message_id。
只能依据 briefing 与 evidence 作答，不得补充外部事实，不得输出思考过程。
当问题是总结人员、介绍某人，或判断群类型时：
- 必须根据 briefing 写出 claims：先总结本人全部个人信息，再分别总结各关系人
- 必须阅读每个群 chats 中的群聊原文，再按 groups 已排好的顺序判断每个群疑似是什么群（家庭群、同事群、朋友群、工作群、其他等），type 用 inference，并引用具体发言内容作为依据
- 群类型 inference 的 statement 写成一句：群{群名称或群ID}，疑似为{类型}，成员包括{成员}，聊天内容涉及{聊天要点}
- 不要自行检索违法犯罪关键词
- 来自 briefing 的 claims 的 evidence_refs 可以为空
- 只要 briefing 里有对应人员或群，judgement_level 不得为“证据不足”
其他问题只输出与该问题直接相关的 claims，禁止把本人信息、关系人或全部群类型整份复述。
仅当 briefing 与 evidence 都没有与问题相关的材料时，才输出“证据不足”。
讨论群聊原文时：事实主张应引用 evidence 的 message_id；推断必须标注 inference。
回答前必须先检查 evidence；其中按相关度排序的直接字段或原文命中必须在结论中明确回应，不得跳过后仅凭常识作答。
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
claims 必须直接回答当前 question。
不要因为 briefing.subjects 非空就输出全部人员总结或全部群类型。
仅当问题明确要求总结人员、介绍某人、关系人或判断群类型时，才按 briefing 写这些内容。
"""


def _message_text(message: dict) -> str:
    content = message.get("content") or message.get("reasoning_content") or ""
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
        content = "".join(parts)
    return content if isinstance(content, str) else str(content or "")


def _extract_json(content: str) -> str:
    text = (content or "").strip()
    if not text:
        raise LlmError("模型返回空内容。请确认模型名与当前接口匹配，需要时可关闭思考模式后再试。")
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    if start >= 0:
        return text[start:]
    return text


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _repair_truncated_json(text: str) -> str:
    stack: list[str] = []
    in_string = False
    escape = False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]" and stack:
            stack.pop()
    repaired = text.rstrip()
    if in_string:
        repaired += '"'
    repaired = repaired.rstrip().rstrip(",")
    while stack:
        repaired += stack.pop()
    return repaired


def _loads_json_object(content: str) -> dict:
    text = _extract_json(content)
    candidates = []
    end = text.rfind("}")
    if end > 0:
        candidates.append(text[: end + 1])
    candidates.append(text)
    candidates.append(_repair_truncated_json(text))
    last_error: Exception | None = None
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(payload, dict):
            return payload
        last_error = TypeError("模型返回的不是 JSON 对象")
    preview = " ".join(text.split())[:180]
    raise LlmError(
        "模型没有返回完整的研判 JSON，请再发一次。"
        + (f" 原文开头：{preview}" if preview else "")
    ) from last_error


def _normalize_analysis_payload(payload: dict) -> dict:
    allowed_levels = {"明确证据", "较强迹象", "存在迹象但歧义", "证据不足", "存在相反证据"}
    level = str(payload.get("judgement_level") or "").strip()
    if level not in allowed_levels:
        level = next((item for item in allowed_levels if item in level), "证据不足")
    claims = []
    for item in payload.get("claims") or []:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement") or item.get("content") or "").strip()
        if not statement:
            continue
        claim_type = str(item.get("type") or "").strip().lower()
        claims.append({
            "type": "fact" if claim_type in {"fact", "事实", "factual"} else "inference",
            "statement": statement,
            "evidence_refs": _as_str_list(item.get("evidence_refs") or item.get("evidence")),
        })
    alternatives = []
    for item in payload.get("alternatives") or []:
        if not isinstance(item, dict):
            continue
        hypothesis = str(item.get("hypothesis") or item.get("statement") or "").strip()
        if not hypothesis:
            continue
        alternatives.append({
            "hypothesis": hypothesis,
            "required_checks": _as_str_list(item.get("required_checks") or item.get("checks")),
        })
    confidence = str(payload.get("confidence") or "").strip()
    if confidence not in {"低", "中", "高"}:
        confidence = "中"
    return {
        "judgement_level": level,
        "claims": claims,
        "counter_evidence_refs": _as_str_list(payload.get("counter_evidence_refs")),
        "alternatives": alternatives,
        "confidence": confidence,
    }


def _parse_model_analysis(content: str) -> ModelAnalysis:
    payload = _loads_json_object(content)
    try:
        return ModelAnalysis.model_validate(_normalize_analysis_payload(payload))
    except ValidationError as exc:
        preview = " ".join((content or "").split())[:180]
        raise LlmError(
            "模型返回的研判结果无法解析，请再发一次。"
            + (f" 原文开头：{preview}" if preview else "")
        ) from exc


def _keep_known_refs(result: ModelAnalysis, evidence: list[SearchHit]) -> ModelAnalysis:
    allowed_refs = {hit.message_id for hit in evidence}
    if not allowed_refs:
        return result
    data = result.model_dump()
    for claim in data["claims"]:
        claim["evidence_refs"] = [ref for ref in claim["evidence_refs"] if ref in allowed_refs]
    data["counter_evidence_refs"] = [ref for ref in data["counter_evidence_refs"] if ref in allowed_refs]
    return ModelAnalysis.model_validate(data)


class LocalLlmClient:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str = "",
        local_options: bool = True,
        temperature: float = 0.1,
        top_p: float = 0.9,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.base_url = normalize_llm_base_url(base_url or settings.llm_base_url)
        if not self.base_url:
            raise LlmError("请填写完整 API 地址，需以 http:// 或 https:// 开头")
        self.model = model or settings.llm_model
        self.local_options = local_options
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.last_usage = {}
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
        system_prompt = self.system_prompt if self.system_prompt is not None else SYSTEM_PROMPT
        if not self.local_options:
            system_prompt += JSON_OBJECT_PROMPT
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "conversation_history": history or [],
                    "history_note": "历史仅用于理解追问，不可作为事实证据",
                    "briefing": briefing or {},
                    "briefing_note": (
                        "briefing 是授权案件材料，供需要时查阅。"
                        "evidence 为空不代表没有材料。"
                        "必须先回答 question；与问题无关的人员信息和群类型不要输出。"
                    ),
                    "evidence": evidence_payload,
                    "evidence_note": "evidence 已按直接字段命中、原文命中和上下文相关度排序。必须先回答首位直接命中，再结合后续上下文解释。",
                },
                ensure_ascii=False,
            ),
        })
        request_body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens or (600 if self.local_options else 8192),
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
        elif "deepseek" in f"{self.base_url} {self.model}".lower():
            request_body["thinking"] = {"type": "disabled"}
        try:
            payload = self._chat_completion(request_body)
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if not isinstance(choices, list) or not choices:
                raise LlmError("模型服务没有返回可用结果。请先点「获取模型」选择当前接口中的模型名。")
            message = choices[0].get("message") if isinstance(choices[0], dict) else {}
            self.last_usage = payload.get("usage", {})
            content = _message_text(message or {})
            result = _parse_model_analysis(content)
        except LlmError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            message = str(exc)
            if "protocol" in message.lower():
                raise LlmError("请填写完整 API 地址，需以 http:// 或 https:// 开头") from exc
            raise LlmError(f"模型服务返回无效结果：{exc}") from exc
        return _keep_known_refs(result, evidence)

    def _chat_completion(self, request_body: dict) -> dict:
        response = self.client.post(f"{self.base_url}/chat/completions", json=request_body)
        if response.status_code in {400, 422}:
            retry_body = {
                key: value for key, value in request_body.items()
                if key not in {"thinking", "response_format"}
            }
            if retry_body != request_body:
                response = self.client.post(f"{self.base_url}/chat/completions", json=retry_body)
        if response.is_error:
            detail = response.text.strip()[:800] or response.reason_phrase
            raise LlmError(f"模型服务 HTTP {response.status_code}：{detail}")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            preview = " ".join((response.text or "").split())[:180]
            raise LlmError(
                "模型服务返回无效结果。"
                + (f" 原文开头：{preview}" if preview else "")
            ) from exc

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
            "max_tokens": 800 if self.local_options else 4096,
            "response_format": {"type": "json_object"},
        }
        if self.local_options:
            request_body.update({
                "reasoning_effort": "none",
                "chat_template_kwargs": {"enable_thinking": False},
            })
        elif "deepseek" in f"{self.base_url} {self.model}".lower():
            request_body["thinking"] = {"type": "disabled"}
        try:
            payload = self._chat_completion(request_body)
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if not isinstance(choices, list) or not choices:
                raise LlmError("群研判模型没有返回可用结果。请确认当前接口和模型名匹配。")
            message = choices[0].get("message") if isinstance(choices[0], dict) else {}
            content = _message_text(message or {})
            payload = _loads_json_object(content)
        except LlmError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LlmError(f"群研判模型返回无效结果：{exc}") from exc
        groups_out = payload.get("groups") if isinstance(payload, dict) else payload
        if not isinstance(groups_out, list):
            raise LlmError("群研判模型未返回 groups 数组")
        return groups_out


llm_client = LocalLlmClient()
