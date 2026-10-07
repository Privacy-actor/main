"""大模型接入设置：本地、云端各一套（服务地址、模型名、密钥）。

在“部署与插件”页里修改后保存在本机数据库（settings 表的 llm_endpoints），立即生效，不用改 .env、不用重启；
从没在界面里设置过的一侧，沿用 .env 里的 PRIVSHIELD_LLM_* 配置。密钥只在服务端使用，接口只回显尾号。
"""
from __future__ import annotations

from threading import Lock
from typing import Any
from urllib.parse import urlparse

from .config import settings

TARGETS = ("local", "cloud")


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def key_hint(key: str) -> str:
    """密钥的尾号，界面上提示“已保存，尾号 …abcd”。很短的密钥（本地服务常用的占位符）不显示。"""
    key = key or ""
    return f"…{key[-4:]}" if len(key) >= 12 else ""


class ModelSettings:
    def __init__(self) -> None:
        self._storage = None
        self._stored: dict[str, Any] | None = None
        self._lock = Lock()

    def attach(self, storage) -> None:
        self._storage = storage
        self._stored = None

    def _load(self) -> dict[str, Any]:
        with self._lock:
            if self._stored is None:
                value = self._storage.get_setting("llm_endpoints", {}) if self._storage is not None else {}
                self._stored = value if isinstance(value, dict) else {}
            return self._stored

    def _env(self, target: str) -> dict[str, Any]:
        if target == "local":
            return {"enabled": settings.llm_enabled, "provider": "env", "base_url": settings.llm_base_url,
                    "model": settings.llm_model, "api_key": settings.llm_api_key, "source": "env"}
        return {"enabled": settings.llm_cloud_configured, "provider": "env", "base_url": settings.llm_cloud_base_url,
                "model": settings.llm_cloud_model, "api_key": settings.llm_cloud_api_key, "source": "env"}

    def get(self, target: str) -> dict[str, Any]:
        """实际生效的一侧配置（含密钥，只在服务端使用）。"""
        stored = self._load().get(target)
        if not isinstance(stored, dict):
            return self._env(target)
        item = {
            "enabled": bool(stored.get("enabled")), "provider": str(stored.get("provider") or "custom"),
            "base_url": str(stored.get("base_url") or ""), "model": str(stored.get("model") or ""),
            "api_key": str(stored.get("api_key") or ""), "source": "user",
        }
        item["enabled"] = item["enabled"] and bool(item["base_url"].strip() and item["model"].strip())
        return item

    def public(self, target: str) -> dict[str, Any]:
        item = self.get(target)
        return {
            "enabled": item["enabled"], "provider": item["provider"], "base_url": item["base_url"], "model": item["model"],
            "api_key_set": bool(item["api_key"]), "api_key_hint": key_hint(item["api_key"]), "source": item["source"],
        }

    def update(self, target: str, patch: dict[str, Any]) -> None:
        """保存一侧的设置。api_key 为 None 表示沿用已保存的密钥，空字符串表示清除。"""
        if target not in TARGETS:
            raise ValueError("target 只能是 local 或 cloud")
        current = self.get(target)
        api_key = current["api_key"] if patch.get("api_key") is None else str(patch["api_key"]).strip()
        # 换了服务地址（主机不同）又没有重新填写密钥时，不把旧密钥带到新地址
        if patch.get("api_key") is None and _host(str(patch.get("base_url") or "")) != _host(current["base_url"]):
            api_key = ""
        item = {
            "enabled": bool(patch.get("enabled")), "provider": str(patch.get("provider") or "custom")[:40],
            "base_url": str(patch.get("base_url") or "").strip().rstrip("/"), "model": str(patch.get("model") or "").strip(),
            "api_key": api_key,
        }
        with self._lock:
            stored = dict(self._stored or {})
            stored[target] = item
            if self._storage is not None:
                self._storage.set_setting("llm_endpoints", stored)
            self._stored = stored

    def reset(self, target: str) -> None:
        """删除界面里的设置，回到 .env。"""
        with self._lock:
            stored = dict(self._stored if self._stored is not None else (self._storage.get_setting("llm_endpoints", {}) if self._storage else {}))
            stored.pop(target, None)
            if self._storage is not None:
                self._storage.set_setting("llm_endpoints", stored)
            self._stored = stored

    def saved_key_for(self, target: str, base_url: str) -> str:
        """测试连接、读取模型列表时沿用已保存的密钥：只在地址主机与保存的一致时才给，避免把密钥发到别处。"""
        current = self.get(target)
        return current["api_key"] if _host(base_url) == _host(current["base_url"]) else ""


model_settings = ModelSettings()
