"""SEC-01：API Key 本机安全存储（全应用唯一实现）。

M1 只持有 apiKeyRef，通过本模块 put/get/delete。
Windows：DPAPI（CryptProtectData / CryptUnprotectData）。
非 Windows：回退为本地加密文件（仅开发兼容，正式目标为 Windows）。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

from multi_agent_room.paths import get_config_dir


def _vault_path() -> Path:
    return get_config_dir() / "secrets.vault"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _dpapi_protect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "MultiAgentRoom",
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _xor_fallback(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _fallback_key() -> bytes:
    # 非 Windows 开发回退；生产以 DPAPI 为准
    machine = f"{os.environ.get('COMPUTERNAME', '')}|{os.environ.get('USERNAME', '')}|MultiAgentRoom"
    return machine.encode("utf-8")


class SecretStore:
    """唯一 SecretStore 实现。"""

    def put(self, secret: str, *, ref: Optional[str] = None) -> str:
        if not secret:
            raise ValueError("secret 不能为空")
        ref = ref or f"key-{uuid.uuid4().hex[:12]}"
        vault = self._load()
        raw = secret.encode("utf-8")
        if _is_windows():
            blob = _dpapi_protect(raw)
            enc = {"mode": "dpapi", "data": base64.b64encode(blob).decode("ascii")}
        else:
            blob = _xor_fallback(raw, _fallback_key())
            enc = {"mode": "dev_xor", "data": base64.b64encode(blob).decode("ascii")}
        vault[ref] = enc
        self._save(vault)
        return ref

    def get(self, ref: str) -> Optional[str]:
        vault = self._load()
        item = vault.get(ref)
        if not item:
            return None
        blob = base64.b64decode(item["data"])
        mode = item.get("mode")
        if mode == "dpapi":
            return _dpapi_unprotect(blob).decode("utf-8")
        if mode == "dev_xor":
            return _xor_fallback(blob, _fallback_key()).decode("utf-8")
        raise ValueError(f"未知加密模式: {mode}")

    def delete(self, ref: str) -> None:
        vault = self._load()
        if ref in vault:
            del vault[ref]
            self._save(vault)

    def contains_plaintext_on_disk(self, needle: str) -> bool:
        """红线：磁盘 vault / models 配置不得出现明文 needle。"""
        paths = [_vault_path(), get_config_dir() / "models.json"]
        for path in paths:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if needle and needle in text:
                return True
        return False

    def _load(self) -> dict:
        path = _vault_path()
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, vault: dict) -> None:
        path = _vault_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(vault, ensure_ascii=False, indent=2), encoding="utf-8")


# 模块级单例 — M1 必须引用此实例，禁止另建存储
_STORE: Optional[SecretStore] = None


def get_secret_store() -> SecretStore:
    global _STORE
    if _STORE is None:
        _STORE = SecretStore()
    return _STORE
