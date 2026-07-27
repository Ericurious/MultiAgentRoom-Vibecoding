"""ProviderAdapter 插拔（T-M1-04b）。默认 OpenAI 兼容探活。

规划约定：凡 OpenAI 兼容 HTTP API（含 DeepSeek / 通义 / Moonshot 等）
均走同一适配器；providerId 可为厂商别名，协议层统一解析为 openai_compat。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from multi_agent_room.logging_setup import log_event
from multi_agent_room.model_config import ModelConfig
from multi_agent_room.secret_store import get_secret_store

# 优先选用的聊天模型名（命中远程 /models 列表时）
_PREFERRED_MODELS = (
    "deepseek-chat",
    "deepseek-reasoner",
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-3.5-turbo",
    "qwen-plus",
    "qwen-turbo",
    "moonshot-v1-8k",
    "glm-4-flash",
)


@dataclass
class ProbeResult:
    ok: bool
    code: str  # ok | timeout | auth | network | adapter
    message: str
    ui_text: str
    suggested_model_id: str = ""


@dataclass
class ChatTestResult:
    ok: bool
    code: str
    message: str
    reply: str = ""
    ui_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""
    raw_message: dict[str, Any] = field(default_factory=dict)


@dataclass
class ListModelsResult:
    ok: bool
    code: str
    message: str
    model_ids: list[str] = field(default_factory=list)
    ui_text: str = ""


def normalize_openai_base(url: str) -> str:
    """把用户随便填的网址规范成 …/v1（无尾斜杠）。"""
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return ""
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    # 去掉误粘贴的路径（如 /chat/completions）
    for suffix in ("/chat/completions", "/models", "/completions"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].rstrip("/")
    if raw.endswith("/v1"):
        return raw
    return raw + "/v1"


def completion_url(base_v1: str) -> str:
    return f"{base_v1.rstrip('/')}/chat/completions"


def models_url(base_v1: str) -> str:
    return f"{base_v1.rstrip('/')}/models"


def pick_preferred_model(model_ids: list[str]) -> str:
    if not model_ids:
        return ""
    lower_map = {m.lower(): m for m in model_ids}
    for pref in _PREFERRED_MODELS:
        if pref in lower_map:
            return lower_map[pref]
    # DeepSeek 常见：包含 chat 的
    for m in model_ids:
        if "chat" in m.lower() and "embed" not in m.lower():
            return m
    return model_ids[0]


def guess_display_name(base_url: str) -> str:
    try:
        host = urlparse(normalize_openai_base(base_url)).hostname or ""
    except Exception:  # noqa: BLE001
        host = ""
    host = host.lower()
    if "deepseek" in host:
        return "DeepSeek"
    if "openai" in host:
        return "OpenAI"
    if "moonshot" in host or "kimi" in host:
        return "Kimi"
    if "dashscope" in host or "aliyuncs" in host:
        return "Qwen"
    if "bigmodel" in host or "zhipu" in host:
        return "智谱"
    if host:
        return host.split(".")[0]
    return "OpenAI兼容"


def _read_http_error(exc: urllib.error.HTTPError, limit: int = 2000) -> str:
    try:
        return exc.read(limit).decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return str(exc)


class ProviderAdapter(ABC):
    id: str

    @abstractmethod
    def probe(self, cfg: ModelConfig, api_key: str) -> ProbeResult:
        raise NotImplementedError

    def list_models(self, cfg: ModelConfig, api_key: str) -> ListModelsResult:
        return ListModelsResult(False, "adapter", "不支持列出模型", ui_text="不支持")

    def chat_test(
        self, cfg: ModelConfig, api_key: str, prompt: str
    ) -> ChatTestResult:
        return ChatTestResult(
            False, "adapter", "当前适配器不支持 Chat 测试", ui_text="不支持"
        )

    def chat(
        self,
        cfg: ModelConfig,
        api_key: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 2048,
        timeout_ms: Optional[int] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
    ) -> ChatTestResult:
        return ChatTestResult(
            False, "adapter", "当前适配器不支持 Chat", ui_text="不支持"
        )


class OpenAICompatAdapter(ProviderAdapter):
    id = "openai_compat"

    def list_models(self, cfg: ModelConfig, api_key: str) -> ListModelsResult:
        base = normalize_openai_base(cfg.base_url)
        timeout_s = max(cfg.timeout_ms, 1000) / 1000.0
        url = models_url(base)
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read(200_000).decode("utf-8", errors="replace")
                if not (200 <= resp.status < 300):
                    return ListModelsResult(
                        False,
                        "adapter",
                        f"HTTP {resp.status}: {raw[:1500]}",
                        ui_text="拉取模型列表失败",
                    )
                payload = json.loads(raw)
                data = payload.get("data") or []
                ids = [
                    str(item.get("id"))
                    for item in data
                    if isinstance(item, dict) and item.get("id")
                ]
                # 过滤明显非对话
                ids = [
                    i
                    for i in ids
                    if "embed" not in i.lower() and "tts" not in i.lower()
                ]
                if not ids:
                    return ListModelsResult(
                        False,
                        "adapter",
                        f"列表为空。原始响应：{raw[:800]}",
                        ui_text="未返回可用模型",
                    )
                return ListModelsResult(
                    True, "ok", "ok", model_ids=ids, ui_text=f"发现 {len(ids)} 个模型"
                )
        except urllib.error.HTTPError as exc:
            detail = _read_http_error(exc)
            code = "auth" if exc.code in (401, 403) else "adapter"
            ui = "鉴权失败，检查 Key" if code == "auth" else "拉取模型列表失败"
            return ListModelsResult(False, code, f"HTTP {exc.code}: {detail}", ui_text=ui)
        except TimeoutError:
            return ListModelsResult(False, "timeout", "timeout", ui_text="拉取模型超时")
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", exc))
            return ListModelsResult(False, "network", reason, ui_text="无法连接 baseURL")
        except Exception as exc:  # noqa: BLE001
            return ListModelsResult(
                False, "adapter", str(exc)[:800], ui_text="拉取模型异常"
            )

    def probe(self, cfg: ModelConfig, api_key: str) -> ProbeResult:
        """探活：必要时先 /models 选模型，再 chat/completions。"""
        base = normalize_openai_base(cfg.base_url)
        timeout_s = max(cfg.timeout_ms, 1000) / 1000.0
        model_id = (cfg.model_id or "").strip()
        suggested = ""

        listed = self.list_models(cfg, api_key)
        if listed.ok and listed.model_ids:
            if not model_id or model_id not in listed.model_ids:
                # 空或乱填：自动挑一个可用模型
                suggested = pick_preferred_model(listed.model_ids)
                model_id = suggested
        elif not model_id:
            # 无法列表时给常见默认
            host = (urlparse(base).hostname or "").lower()
            model_id = "deepseek-chat" if "deepseek" in host else "gpt-4o-mini"
            suggested = model_id

        # 若列表成功但用户模型无效，上面已替换；再探活
        result = self._probe_chat(base, api_key, model_id, timeout_s)
        if result.ok:
            result.suggested_model_id = suggested or model_id
            return result

        # 400 常见原因：模型名不对 — 再试列表首选
        if listed.ok and listed.model_ids and "400" in (result.message or ""):
            alt = pick_preferred_model(listed.model_ids)
            if alt and alt != model_id:
                retry = self._probe_chat(base, api_key, alt, timeout_s)
                if retry.ok:
                    retry.suggested_model_id = alt
                    retry.ui_text = f"探活成功（已自动改用模型 {alt}）"
                    return retry
                # 拼上两次错误便于排障
                result.message = (
                    f"模型 {model_id!r} 失败：{result.message}\n"
                    f"改用 {alt!r} 仍失败：{retry.message}"
                )
                result.suggested_model_id = alt

        if listed.ok and listed.model_ids:
            result.message += (
                f"\n可用模型（节选）：{', '.join(listed.model_ids[:12])}"
            )
            if not result.suggested_model_id:
                result.suggested_model_id = pick_preferred_model(listed.model_ids)
        elif not listed.ok:
            result.message += f"\n拉取 /models 亦失败：{listed.message}"
        return result

    def _probe_chat(
        self, base_v1: str, api_key: str, model_id: str, timeout_s: float
    ) -> ProbeResult:
        url = completion_url(base_v1)
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read(4096)
                if 200 <= resp.status < 300:
                    return ProbeResult(
                        True,
                        "ok",
                        f"probe ok model={model_id}",
                        "探活成功",
                        suggested_model_id=model_id,
                    )
                text = raw.decode("utf-8", errors="replace")
                return ProbeResult(
                    False,
                    "adapter",
                    f"HTTP {resp.status}: {text[:1500]}",
                    "适配器响应异常",
                )
        except urllib.error.HTTPError as exc:
            detail = _read_http_error(exc)
            if exc.code in (401, 403):
                return ProbeResult(False, "auth", detail, "鉴权失败，检查 Key")
            return ProbeResult(
                False,
                "adapter",
                f"HTTP {exc.code}: {detail}",
                "探活失败（多为模型名/参数不兼容）",
            )
        except TimeoutError:
            return ProbeResult(False, "timeout", "timeout", "探活超时")
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", exc))
            low = reason.lower()
            if "timed out" in low or "timeout" in low:
                return ProbeResult(False, "timeout", reason, "探活超时")
            return ProbeResult(False, "network", reason, "无法连接 baseURL")
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(False, "adapter", str(exc)[:800], "适配器响应异常")

    def chat_test(
        self, cfg: ModelConfig, api_key: str, prompt: str
    ) -> ChatTestResult:
        text = (prompt or "").strip() or "请用一句话回复：pong"
        return self.chat(
            cfg,
            api_key,
            [{"role": "user", "content": text}],
            max_tokens=64,
        )

    def chat(
        self,
        cfg: ModelConfig,
        api_key: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 2048,
        timeout_ms: Optional[int] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
    ) -> ChatTestResult:
        base = normalize_openai_base(cfg.base_url)
        timeout_s = max(timeout_ms or cfg.timeout_ms, 1000) / 1000.0
        # 正式首答 / 工具循环给更长等待
        timeout_s = max(timeout_s, 90.0)
        model_id = (cfg.model_id or "").strip()
        if not model_id:
            listed = self.list_models(cfg, api_key)
            if listed.ok:
                model_id = pick_preferred_model(listed.model_ids)
            else:
                model_id = "deepseek-chat"
        clean_msgs = _normalize_chat_messages(messages)
        if not clean_msgs:
            clean_msgs = [{"role": "user", "content": "ping"}]
        url = completion_url(base)
        body: dict[str, Any] = {
            "model": model_id,
            "messages": clean_msgs,
            "max_tokens": max(16, int(max_tokens)),
        }
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read(500_000).decode("utf-8", errors="replace")
                if not (200 <= resp.status < 300):
                    return ChatTestResult(
                        False,
                        "adapter",
                        f"HTTP {resp.status}: {raw[:1500]}",
                        ui_text="模型调用失败",
                    )
                payload = json.loads(raw)
                choices = payload.get("choices") or []
                msg: dict[str, Any] = {}
                finish = ""
                if choices:
                    msg = choices[0].get("message") or {}
                    finish = str(choices[0].get("finish_reason") or "")
                reply = str(msg.get("content") or "").strip()
                tool_calls = _parse_tool_calls(msg)
                if not reply and not tool_calls:
                    reply = raw[:300]
                return ChatTestResult(
                    True,
                    "ok",
                    f"chat ok model={model_id}",
                    reply=reply,
                    ui_text="模型已回复",
                    tool_calls=tool_calls,
                    finish_reason=finish,
                    raw_message=msg,
                )
        except urllib.error.HTTPError as exc:
            detail = _read_http_error(exc)
            if exc.code in (401, 403):
                return ChatTestResult(
                    False, "auth", detail, ui_text="鉴权失败，检查 Key"
                )
            return ChatTestResult(
                False,
                "adapter",
                f"HTTP {exc.code}: {detail}",
                ui_text="模型调用失败",
            )
        except TimeoutError:
            return ChatTestResult(False, "timeout", "timeout", ui_text="模型调用超时")
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", exc))
            low = reason.lower()
            if "timed out" in low or "timeout" in low:
                return ChatTestResult(False, "timeout", reason, ui_text="模型调用超时")
            return ChatTestResult(False, "network", reason, ui_text="无法连接 baseURL")
        except Exception as exc:  # noqa: BLE001
            return ChatTestResult(
                False, "adapter", str(exc)[:800], ui_text="模型调用异常"
            )


def _normalize_chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages or []:
        role = str(m.get("role") or "user")
        item: dict[str, Any] = {"role": role}
        if role == "tool":
            item["content"] = str(m.get("content") or "")
            if m.get("tool_call_id"):
                item["tool_call_id"] = m["tool_call_id"]
            out.append(item)
            continue
        if m.get("tool_calls"):
            item["tool_calls"] = m["tool_calls"]
            # content 可为 null
            if m.get("content") is not None:
                item["content"] = m.get("content")
            else:
                item["content"] = None
            out.append(item)
            continue
        content = m.get("content")
        if content is None:
            continue
        text = str(content)
        if not text.strip() and role != "assistant":
            continue
        item["content"] = text
        out.append(item)
    return out


def _parse_tool_calls(msg: dict[str, Any]) -> list[dict[str, Any]]:
    raw = msg.get("tool_calls") or []
    parsed: list[dict[str, Any]] = []
    for tc in raw:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = str(fn.get("name") or tc.get("name") or "")
        args_raw = fn.get("arguments") if "function" in tc else tc.get("arguments")
        args: dict[str, Any] = {}
        if isinstance(args_raw, dict):
            args = args_raw
        elif isinstance(args_raw, str) and args_raw.strip():
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
        if name:
            parsed.append(
                {
                    "id": str(tc.get("id") or ""),
                    "name": name,
                    "arguments": args,
                }
            )
    return parsed


_REGISTRY: dict[str, ProviderAdapter] = {}

_PROVIDER_ALIASES: dict[str, str] = {
    "openai_compat": "openai_compat",
    "openai": "openai_compat",
    "deepseek": "openai_compat",
    "qwen": "openai_compat",
    "dashscope": "openai_compat",
    "moonshot": "openai_compat",
    "kimi": "openai_compat",
    "zhipu": "openai_compat",
    "glm": "openai_compat",
    "yi": "openai_compat",
    "lingyi": "openai_compat",
    "groq": "openai_compat",
    "together": "openai_compat",
    "mistral": "openai_compat",
    "azure_openai": "openai_compat",
}


def register_adapter(adapter: ProviderAdapter) -> None:
    _REGISTRY[adapter.id] = adapter


def resolve_provider_id(provider_id: str) -> str:
    raw = (provider_id or "").strip() or "openai_compat"
    key = raw.lower()
    if key in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[key]
    ensure_default_adapters()
    if key in _REGISTRY:
        return key
    log_event(
        "provider_alias_fallback",
        f"未知 providerId={raw!r}，按 openai_compat 兼容协议处理",
    )
    return "openai_compat"


def get_adapter(provider_id: str) -> ProviderAdapter:
    ensure_default_adapters()
    resolved = resolve_provider_id(provider_id)
    if resolved not in _REGISTRY:
        raise KeyError(f"未知 providerId: {provider_id}；已注册: {list(_REGISTRY)}")
    return _REGISTRY[resolved]


def ensure_default_adapters() -> None:
    if "openai_compat" not in _REGISTRY:
        register_adapter(OpenAICompatAdapter())


def resolve_api_key(cfg: ModelConfig) -> Optional[str]:
    if not cfg.api_key_ref:
        return None
    return get_secret_store().get(cfg.api_key_ref)
