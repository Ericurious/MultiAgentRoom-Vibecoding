"""M1 服务层：CRUD、探活状态机、就绪门禁。"""

from __future__ import annotations

from typing import Any, Optional

from multi_agent_room.adapters import (
    ChatTestResult,
    ListModelsResult,
    ProbeResult,
    ensure_default_adapters,
    get_adapter,
    guess_display_name,
    normalize_openai_base,
    pick_preferred_model,
    resolve_api_key,
)
from multi_agent_room.logging_setup import log_event, log_probe_result
from multi_agent_room.model_config import (
    ModelConfig,
    ModelConfigStore,
    new_config_id,
    utc_now_iso,
)
from multi_agent_room.secret_store import get_secret_store

_ERROR_KEEP = 8000


class ModelService:
    def __init__(self, store: Optional[ModelConfigStore] = None) -> None:
        ensure_default_adapters()
        self.store = store or ModelConfigStore()
        self.secrets = get_secret_store()

    def list_models(self) -> list[ModelConfig]:
        return self.store.list_all()

    def discover_remote_models(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_ms: int = 15000,
        provider_id: str = "openai_compat",
    ) -> ListModelsResult:
        """仅凭 baseURL + Key 拉取远端模型列表（不落库）。"""
        ensure_default_adapters()
        base = normalize_openai_base(base_url)
        if not base:
            return ListModelsResult(
                False, "adapter", "baseURL 为空", ui_text="请填写 API 地址"
            )
        if not (api_key or "").strip():
            return ListModelsResult(
                False, "auth", "missing key", ui_text="请填写 API Key"
            )
        tmp = ModelConfig(
            config_id="tmp",
            provider_id=provider_id,
            display_name="tmp",
            api_key_ref="",
            base_url=base,
            model_id="",
            timeout_ms=timeout_ms,
        )
        return get_adapter(provider_id).list_models(tmp, api_key.strip())

    def add_from_endpoint(
        self,
        *,
        base_url: str,
        api_key: str,
        display_name: str = "",
        model_id: str = "",
        timeout_ms: int = 15000,
        provider_id: str = "openai_compat",
        auto_probe: bool = True,
    ) -> tuple[ModelConfig, Optional[ProbeResult], ListModelsResult]:
        """最低门槛添加：只需 API 地址 + Key；自动规范 /v1、拉模型、选默认、探活。"""
        base = normalize_openai_base(base_url)
        if not base:
            raise ValueError("请填写 API 地址（baseURL）")
        key = (api_key or "").strip()
        if not key:
            raise ValueError("请填写 API Key")

        listed = self.discover_remote_models(
            base_url=base, api_key=key, timeout_ms=timeout_ms, provider_id=provider_id
        )
        chosen = (model_id or "").strip()
        if listed.ok and listed.model_ids:
            if not chosen or chosen not in listed.model_ids:
                chosen = pick_preferred_model(listed.model_ids)
        if not chosen:
            # 拉列表失败时仍可保存，探活阶段再猜
            host = base.lower()
            chosen = "deepseek-chat" if "deepseek" in host else "gpt-4o-mini"

        name = (display_name or "").strip() or guess_display_name(base)
        cfg = self.add_model(
            display_name=name,
            base_url=base,
            model_id=chosen,
            api_key=key,
            provider_id=provider_id,
            timeout_ms=timeout_ms,
        )
        probe_result: Optional[ProbeResult] = None
        if auto_probe:
            cfg, probe_result = self.probe(cfg.config_id)
        return cfg, probe_result, listed

    def add_model(
        self,
        *,
        display_name: str,
        base_url: str,
        model_id: str,
        api_key: str,
        provider_id: str = "openai_compat",
        timeout_ms: int = 15000,
        enabled: bool = True,
    ) -> ModelConfig:
        ensure_default_adapters()
        get_adapter(provider_id)  # 未知 provider 立即失败
        base = normalize_openai_base(base_url) or base_url.strip()
        ref = self.secrets.put(api_key)
        mid = model_id.strip()
        cfg = ModelConfig(
            config_id=new_config_id(),
            provider_id=provider_id,
            display_name=display_name.strip() or mid or guess_display_name(base),
            api_key_ref=ref,
            base_url=base,
            model_id=mid,
            timeout_ms=timeout_ms,
            enabled=enabled,
            status="unknown",
        )
        self.store.upsert(cfg)
        log_event("model_add", f"id={cfg.config_id} base={cfg.base_url}")
        return cfg

    def update_model(
        self,
        config_id: str,
        *,
        display_name: Optional[str] = None,
        base_url: Optional[str] = None,
        model_id: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_ms: Optional[int] = None,
        enabled: Optional[bool] = None,
        provider_id: Optional[str] = None,
    ) -> ModelConfig:
        cfg = self.store.get(config_id)
        if not cfg:
            raise KeyError(f"模型不存在: {config_id}")
        if display_name is not None:
            cfg.display_name = display_name
        if base_url is not None:
            cfg.base_url = normalize_openai_base(base_url) or base_url.strip()
        if model_id is not None:
            cfg.model_id = model_id.strip()
        if timeout_ms is not None:
            cfg.timeout_ms = timeout_ms
        if enabled is not None:
            cfg.enabled = enabled
        if provider_id is not None:
            get_adapter(provider_id)
            cfg.provider_id = provider_id
        if api_key:
            if cfg.api_key_ref:
                self.secrets.delete(cfg.api_key_ref)
            cfg.api_key_ref = self.secrets.put(api_key)
            cfg.status = "unknown"
        self.store.upsert(cfg)
        return cfg

    def set_enabled(self, config_id: str, enabled: bool) -> ModelConfig:
        return self.update_model(config_id, enabled=enabled)

    def delete_model(self, config_id: str) -> None:
        cfg = self.store.get(config_id)
        if not cfg:
            return
        if cfg.api_key_ref:
            self.secrets.delete(cfg.api_key_ref)
        self.store.delete(config_id)
        log_event("model_delete", f"id={config_id}")

    def probe(self, config_id: str) -> tuple[ModelConfig, ProbeResult]:
        cfg = self.store.get(config_id)
        if not cfg:
            raise KeyError(f"模型不存在: {config_id}")
        cfg.status = "probing"
        self.store.upsert(cfg)

        key = resolve_api_key(cfg)
        if not key:
            result = ProbeResult(False, "auth", "missing key", "鉴权失败，检查 Key")
            return self._apply_probe(cfg, result)

        adapter = get_adapter(cfg.provider_id)
        result = adapter.probe(cfg, key)
        return self._apply_probe(cfg, result)

    def chat_test(
        self, config_id: str, prompt: str = "请用一句话回复：pong"
    ) -> tuple[ModelConfig, ChatTestResult]:
        """真实短对话测 Key；成功则同步标 ready。"""
        cfg = self.store.get(config_id)
        if not cfg:
            raise KeyError(f"模型不存在: {config_id}")
        key = resolve_api_key(cfg)
        if not key:
            return cfg, ChatTestResult(
                False, "auth", "missing key", ui_text="鉴权失败，检查 Key"
            )
        adapter = get_adapter(cfg.provider_id)
        result = adapter.chat_test(cfg, key, prompt)
        if result.ok:
            cfg.status = "ready"
            cfg.last_error = ""
            cfg.last_error_code = ""
            cfg.last_probe_at = utc_now_iso()
            self.store.upsert(cfg)
            log_event("chat_test_ok", f"id={config_id}")
        else:
            cfg.status = "failed"
            cfg.last_error = (result.message or "")[:_ERROR_KEEP]
            cfg.last_error_code = result.code  # type: ignore[assignment]
            cfg.last_probe_at = utc_now_iso()
            self.store.upsert(cfg)
            log_event("chat_test_fail", f"id={config_id} code={result.code}")
        return cfg, result

    def chat(
        self,
        config_id: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 2048,
        timeout_ms: Optional[int] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> tuple[ModelConfig, ChatTestResult]:
        """正式对话调用（W1 / 工具循环）；不因单次失败改写 ready。"""
        cfg = self.store.get(config_id)
        if not cfg:
            raise KeyError(f"模型不存在: {config_id}")
        key = resolve_api_key(cfg)
        if not key:
            return cfg, ChatTestResult(
                False, "auth", "missing key", ui_text="鉴权失败，检查 Key"
            )
        adapter = get_adapter(cfg.provider_id)
        result = adapter.chat(
            cfg,
            key,
            messages,
            max_tokens=max_tokens,
            timeout_ms=timeout_ms,
            tools=tools,
            tool_choice=tool_choice,
        )
        if not result.ok:
            log_event("chat_fail", f"id={config_id} code={result.code}")
        else:
            n_tools = len(result.tool_calls or [])
            log_event(
                "chat_ok",
                f"id={config_id} chars={len(result.reply or '')} tools={n_tools}",
            )
        return cfg, result

    def _apply_probe(self, cfg: ModelConfig, result: ProbeResult) -> tuple[ModelConfig, ProbeResult]:
        cfg.last_probe_at = utc_now_iso()
        # 探活成功时自动写回纠正后的模型 ID / 规范 baseURL
        suggested = (result.suggested_model_id or "").strip()
        if suggested and suggested != cfg.model_id:
            cfg.model_id = suggested
        norm = normalize_openai_base(cfg.base_url)
        if norm and norm != cfg.base_url:
            cfg.base_url = norm
        if result.ok:
            cfg.status = "ready"
            cfg.last_error = ""
            cfg.last_error_code = ""
        else:
            cfg.status = "failed"
            cfg.last_error = (result.message or "")[:_ERROR_KEEP]
            cfg.last_error_code = result.code  # type: ignore[assignment]
        self.store.upsert(cfg)
        log_probe_result(cfg.config_id, result.code, result.ui_text)
        return cfg, result

    def list_bindable(self) -> list[ModelConfig]:
        """房间大脑候选：enabled && status==ready。"""
        return [m for m in self.store.list_all() if m.enabled and m.status == "ready"]

    def can_bind(self, config_id: str) -> tuple[bool, str]:
        cfg = self.store.get(config_id)
        if not cfg:
            return False, "模型不存在"
        if not cfg.enabled:
            return False, "模型已禁用，不可选为房间大脑"
        if cfg.status != "ready":
            return False, f"模型未就绪（status={cfg.status}），请先探活成功"
        return True, "ok"
