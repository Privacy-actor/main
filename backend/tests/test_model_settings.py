"""界面里配置本地、云端大模型：保存、密钥只回显尾号、读取模型列表、测试连接、识别时实际使用。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.model_settings import model_settings

client = TestClient(app)
SECRET = "sk-test-secret-1234567890abcd"


class FakeProvider(BaseHTTPRequestHandler):
    """最小的 OpenAI 兼容服务：/v1/models 和 /v1/chat/completions，记录收到的请求。"""
    seen: list[dict] = []
    reject_response_format = False
    wrap_think = False

    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FakeProvider.seen.append({"path": self.path, "auth": self.headers.get("Authorization")})
        if self.path == "/v1/models":
            if self.headers.get("Authorization") not in (None, f"Bearer {SECRET}"):
                return self._send(401, {"error": {"message": "invalid key"}})
            return self._send(200, {"object": "list", "data": [{"id": "qwen3:8b"}, {"id": "qwen3:4b"}]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeProvider.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if FakeProvider.reject_response_format and "response_format" in body:
            return self._send(400, {"error": {"message": "response_format is not supported"}})
        prompt = json.loads(body["messages"][-1]["content"]) if body["messages"][-1]["content"].startswith("{") else {}
        if "candidates" in prompt:
            decisions = [{"id": item["id"], "keep": True, "label": item["label"], "certainty": "high"} for item in prompt["candidates"]]
            content = json.dumps({"decisions": decisions, "additions": []}, ensure_ascii=False)
        else:
            content = '{"ok": true}'
        if FakeProvider.wrap_think:
            content = f"<think>先想一想……{{不是 JSON}}</think>\n```json\n{content}\n```"
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}]})


@pytest.fixture()
def provider():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    FakeProvider.seen = []
    FakeProvider.reject_response_format = False
    FakeProvider.wrap_think = False
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    client.put("/api/v1/llm/settings", json={"reset": ["local", "cloud"]})


def test_defaults_come_from_env_and_keys_are_never_returned(provider):
    view = client.get("/api/v1/llm/settings").json()
    assert view["local"]["source"] == "env" and view["local"]["enabled"] is False and view["editable"] is True
    saved = client.put("/api/v1/llm/settings", json={"cloud": {"enabled": True, "provider": "deepseek", "base_url": provider, "model": "deepseek-chat", "api_key": SECRET}})
    assert saved.status_code == 200
    assert SECRET not in saved.text and saved.json()["cloud"]["api_key_hint"] == "…abcd"
    assert SECRET not in client.get("/api/v1/llm/settings").text
    models = client.get("/api/v1/models").json()
    assert models["endpoints"]["cloud"] == {"enabled": True, "model": "deepseek-chat", "host": provider.split("/")[2]}


def test_saving_without_a_key_keeps_it_only_for_the_same_host(provider):
    client.put("/api/v1/llm/settings", json={"cloud": {"enabled": True, "base_url": provider, "model": "m1", "api_key": SECRET}})
    client.put("/api/v1/llm/settings", json={"cloud": {"enabled": True, "base_url": provider, "model": "m2"}})
    assert model_settings.get("cloud")["api_key"] == SECRET and model_settings.get("cloud")["model"] == "m2"
    client.put("/api/v1/llm/settings", json={"cloud": {"enabled": True, "base_url": "https://other.example.com/v1", "model": "m2"}})
    assert model_settings.get("cloud")["api_key"] == ""


def test_validation_and_reset(provider):
    assert client.put("/api/v1/llm/settings", json={"local": {"enabled": True, "base_url": provider, "model": ""}}).status_code == 422
    assert client.put("/api/v1/llm/settings", json={"local": {"enabled": True, "base_url": "ftp://x", "model": "m"}}).status_code == 422
    client.put("/api/v1/llm/settings", json={"local": {"enabled": True, "provider": "ollama", "base_url": provider, "model": "qwen3:8b"}})
    assert client.get("/api/v1/health").json()["mode"] == "llm"
    client.put("/api/v1/llm/settings", json={"reset": ["local"]})
    assert client.get("/api/v1/llm/settings").json()["local"]["source"] == "env"
    assert client.get("/api/v1/health").json()["mode"] == "lightweight"


def test_list_models_and_test_connection(provider):
    listed = client.post("/api/v1/llm/models", json={"target": "local", "base_url": provider}).json()
    assert listed["models"] == ["qwen3:4b", "qwen3:8b"]
    tested = client.post("/api/v1/llm/test", json={"target": "local", "base_url": provider, "model": "qwen3:8b"}).json()
    assert tested["ok"] and "连接成功" in tested["message"]
    # 已保存的密钥只发给同一个地址
    client.put("/api/v1/llm/settings", json={"cloud": {"enabled": True, "base_url": provider, "model": "m", "api_key": SECRET}})
    FakeProvider.seen = []
    client.post("/api/v1/llm/models", json={"target": "cloud", "base_url": provider})
    assert FakeProvider.seen[-1]["auth"] == f"Bearer {SECRET}"
    wrong = client.post("/api/v1/llm/models", json={"target": "cloud", "base_url": provider, "api_key": "sk-wrong"})
    assert wrong.status_code == 400 and "密钥" in wrong.json()["detail"]
    unreachable = client.post("/api/v1/llm/test", json={"target": "cloud", "base_url": "http://127.0.0.1:9/v1", "model": "m"}).json()
    assert not unreachable["ok"] and "连接不上" in unreachable["message"]


def test_detection_uses_the_configured_model_and_tolerates_think_tags(provider):
    FakeProvider.wrap_think = True
    FakeProvider.reject_response_format = True
    client.put("/api/v1/llm/settings", json={"local": {"enabled": True, "provider": "ollama", "base_url": provider, "model": "qwen3:8b"}})
    result = client.post("/api/v1/detect", json={"text": "请联系心内科的王主任，或者打 13800138000。后来陈晓峰也来了。", "use_llm": True, "persist": False}).json()
    llm = next(step for step in result["trace"] if step["key"] == "llm")
    assert llm["status"] == "done", llm
    calls = [item for item in FakeProvider.seen if item["path"] == "/v1/chat/completions"]
    assert calls and all(call["body"]["model"] == "qwen3:8b" for call in calls)
    # 服务不支持 response_format 时，去掉后重试
    assert any("response_format" not in call["body"] for call in calls)


def test_editing_can_be_disabled_on_servers(provider, monkeypatch):
    monkeypatch.setattr(settings, "model_settings_editable", False)
    assert client.put("/api/v1/llm/settings", json={"local": {"enabled": False}}).status_code == 403
    assert client.post("/api/v1/llm/models", json={"target": "local", "base_url": provider}).status_code == 403
    assert client.get("/api/v1/llm/settings").json()["editable"] is False
