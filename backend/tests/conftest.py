"""测试环境：临时数据库，关闭所有模型和外部服务。

在导入 app 之前设置环境变量（环境变量优先于 backend/.env），
这样在本机运行 pytest 不会写进真实数据库，也不会调用 .env 里配置的大模型或云端接口。
"""
import os
import tempfile
from pathlib import Path

_TEST_DIR = Path(tempfile.mkdtemp(prefix="moyin-tests-"))

os.environ.update({
    "PRIVSHIELD_DATABASE_PATH": str(_TEST_DIR / "privshield-test.db"),
    "PRIVSHIELD_LLM_ENABLED": "false",
    "PRIVSHIELD_LLM_CLOUD_BASE_URL": "",
    "PRIVSHIELD_LLM_CLOUD_API_KEY": "",
    "PRIVSHIELD_LLM_CLOUD_MODEL": "",
    "PRIVSHIELD_NER_ENABLED": "false",
    "PRIVSHIELD_SEMANTIC_MODEL_ENABLED": "false",
    "PRIVSHIELD_KNOWLEDGE_GRAPH_REMOTE_ENABLED": "false",
})
