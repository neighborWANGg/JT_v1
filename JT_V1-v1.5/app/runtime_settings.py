import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import settings
from .llm import normalize_llm_base_url
from .project_store import project_directory, write_json


def agent_directory(case_id) -> Path:
    root = project_directory(case_id) / "agent"
    (root / "skills").mkdir(parents=True, exist_ok=True)
    return root


def defaults() -> dict:
    return {
        "skills": [], "skills_configured": False, "mcp": [],
        "system_prompts": [{"id": "builtin", "name": "内置研判规则", "enabled": True, "content": "", "builtin": True}],
        "model": {
            "mode": "local",
            "local": {"base_url": settings.llm_base_url, "model": settings.llm_model, "enabled": settings.llm_enabled},
            "api": {"base_url": settings.api_llm_base_url, "model": settings.api_llm_model, "api_key": settings.api_llm_api_key},
            "api_presets": [],
            "temperature": 0.1, "top_p": 0.9, "max_tokens": 2048,
        },
    }


def load_runtime_settings(case_id) -> dict:
    path = agent_directory(case_id) / "settings.json"
    current = defaults()
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        for key in ("skills", "skills_configured", "mcp", "system_prompts"):
            if key in saved:
                current[key] = saved[key]
        if "system_prompt" in saved and "system_prompts" not in saved:
            old = saved["system_prompt"]
            current["system_prompts"] = [{"id": "builtin", "name": "内置研判规则", "enabled": old.get("enabled", True), "content": old.get("content", ""), "builtin": True}]
        if "model" in saved:
            for key, value in saved["model"].items():
                if isinstance(value, dict) and isinstance(current["model"].get(key), dict):
                    current["model"][key].update(value)
                else:
                    current["model"][key] = value
    return current


def save_runtime_settings(case_id, payload: dict) -> dict:
    current = load_runtime_settings(case_id)
    incoming_api = payload.get("model", {}).get("api", {})
    if not incoming_api.get("api_key"):
        payload.setdefault("model", {}).setdefault("api", {})["api_key"] = current["model"]["api"].get("api_key", "")
    model = payload.setdefault("model", {})
    if isinstance(model.get("local"), dict) and model["local"].get("base_url"):
        model["local"]["base_url"] = normalize_llm_base_url(model["local"]["base_url"])
    if isinstance(model.get("api"), dict) and model["api"].get("base_url"):
        model["api"]["base_url"] = normalize_llm_base_url(model["api"]["base_url"])
    current_presets = {item.get("id"): item for item in current.get("model", {}).get("api_presets", []) if item.get("id")}
    merged_presets = []
    for preset in model.get("api_presets", []) or []:
        if not preset.get("api_key"):
            preset["api_key"] = current_presets.get(preset.get("id"), {}).get("api_key", "")
        if preset.get("base_url"):
            preset["base_url"] = normalize_llm_base_url(preset["base_url"])
        merged_presets.append(preset)
    model["api_presets"] = merged_presets
    payload["skills_configured"] = True
    write_json(agent_directory(case_id) / "settings.json", payload)
    return payload


def load_chat_labels(case_id) -> dict:
    path = agent_directory(case_id) / "chat_labels.json"
    current = {"groups": {}, "senders": {}}
    if not path.exists():
        return current
    saved = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(saved.get("groups"), dict):
        current["groups"] = saved["groups"]
    if isinstance(saved.get("senders"), dict):
        current["senders"] = saved["senders"]
    return current


def save_chat_labels(case_id, payload: dict) -> dict:
    groups = {}
    for key, value in (payload.get("groups") or {}).items():
        item = value if isinstance(value, dict) else {}
        groups[str(key)] = {
            "type": str(item.get("type") or "").strip(),
            "type_source": "manual" if item.get("type_source") == "manual" else "analysis",
            "hidden": bool(item.get("hidden")),
        }
    senders = {}
    for key, value in (payload.get("senders") or {}).items():
        item = value if isinstance(value, dict) else {"name": value}
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        senders[str(key)] = {
            "name": name,
            "name_source": "manual" if item.get("name_source") == "manual" else "analysis",
        }
    saved = {"groups": groups, "senders": senders}
    write_json(agent_directory(case_id) / "chat_labels.json", saved)
    return saved


def public_settings(payload: dict) -> dict:
    result = json.loads(json.dumps(payload))
    key = result["model"]["api"].pop("api_key", "")
    result["model"]["api"]["api_key_set"] = bool(key)
    presets = []
    for preset in result.get("model", {}).get("api_presets", []) or []:
        preset_key = preset.pop("api_key", "")
        preset["api_key_set"] = bool(preset_key)
        presets.append(preset)
    result["model"]["api_presets"] = presets
    return result


def list_skills(case_id) -> list[dict]:
    roots = [
        ("项目", agent_directory(case_id) / "skills"),
        ("全局", Path.home() / ".codex" / "skills"),
        ("全局", Path.home() / ".agents" / "skills"),
    ]
    runtime = load_runtime_settings(case_id)
    enabled = set(runtime.get("skills", []))
    enable_all = not runtime.get("skills_configured", False)
    found = {}
    for scope, root in roots:
        if not root.exists():
            continue
        for path in root.rglob("SKILL.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"(?m)^name:\s*['\"]?([^'\"\r\n]+)", text)
            name = (match.group(1).strip() if match else path.parent.name)
            skill_id = f"{scope}:{name}"
            description_match = re.search(r"(?ms)^description:\s*>?\s*(.*?)(?=\n[a-zA-Z_-]+:|\n---)", text)
            description = " ".join(description_match.group(1).split()) if description_match else "暂无说明"
            found.setdefault(skill_id, {"id": skill_id, "name": name, "description": description, "scope": scope, "path": str(path), "enabled": enable_all or skill_id in enabled})
    return sorted(found.values(), key=lambda item: (item["scope"] != "项目", item["name"].lower()))


def enabled_skill_prompt(case_id, ids: list[str]) -> str:
    paths = {item["id"]: item["path"] for item in list_skills(case_id)}
    chunks = []
    for skill_id in ids:
        if skill_id in paths:
            chunks.append(Path(paths[skill_id]).read_text(encoding="utf-8", errors="replace"))
    return "\n\n以下为已启用 Skill，请遵守其中与当前研判相关的规则：\n" + "\n\n".join(chunks) if chunks else ""


def fetch_models(base_url: str, api_key: str = "") -> list[str]:
    token = api_key.strip().removeprefix("Bearer ").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    root = normalize_llm_base_url(base_url)
    if not root:
        raise ValueError("请填写完整接口地址，需以 http:// 或 https:// 开头")
    roots = [root]
    if not roots[0].endswith("/v1"):
        roots.append(f"{roots[0]}/v1")
    last_error = None
    for root in roots:
        try:
            response = httpx.get(f"{root}/models", headers=headers, timeout=15)
            response.raise_for_status()
            payload = response.json()
            items = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(items, list):
                items = []
            names = []
            for item in items:
                if isinstance(item, str) and item.strip():
                    names.append(item.strip())
                elif isinstance(item, dict) and item.get("id"):
                    names.append(str(item["id"]))
            if names:
                return sorted(set(names))
        except Exception as exc:
            last_error = exc
    if last_error:
        message = str(last_error)
        if "protocol" in message.lower():
            raise ValueError("请填写完整接口地址，需以 http:// 或 https:// 开头") from last_error
        raise last_error
    return []


def record_usage(case_id, engine: str, mode: str, usage: dict) -> None:
    path = agent_directory(case_id) / "usage.json"
    events = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    events.append({
        "at": datetime.now(timezone.utc).isoformat(), "engine": engine, "mode": mode,
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    })
    write_json(path, events[-500:])


def usage_summary(case_id) -> dict:
    path = agent_directory(case_id) / "usage.json"
    events = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return {
        "requests": len(events),
        "prompt_tokens": sum(item["prompt_tokens"] for item in events),
        "completion_tokens": sum(item["completion_tokens"] for item in events),
        "total_tokens": sum(item["total_tokens"] for item in events),
        "events": events[-24:],
    }
