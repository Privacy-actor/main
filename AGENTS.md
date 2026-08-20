# Codex 常驻规则

本文件放**操作规则**。规范内容(字段、数字、阈值、口径)一律以 `finetune/docs/规格.md` 为准,本文件不复制。

---

## 仓库结构与分工

```
backend/     队友的。FastAPI 后端、规则层、NER 层、LLM 适配层
frontend/    队友的。
finetune/    我的工作区。数据生成、SFT、评测
```

**`backend/` 和 `frontend/` 默认只读。**

需要改后端时:先停下来说明**改哪个文件、改几行、为什么非改不可**,等人确认。目前规格里唯一批准的后端改动是 `llm_adapter.py` 的 find-all(见规格的「唯一的后端改动」一节),除此之外一律先问。

**可以自由 import `backend/app/` 下的模块**——派生脚本必须跑真实上游,这是设计如此。只读不写。

---

## 环境铁律

**永远显式使用项目虚拟环境的解释器,不要依赖 PATH 上的 `python`。**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe finetune\scripts\xxx.py
```

原因:本机 PATH 上的 `python` 是 `C:\ProgramData\anaconda3\python.exe`,不是项目 venv。直接 `python -m pytest` 会报 `ModuleNotFoundError: No module named 'fastapi'`,而真实原因是解释器选错了。**报依赖缺失之前,先打印 `sys.executable`。**

不要修改 PATH,不要 `conda activate`,不要假设 shell 已激活 venv。

装包也一样:`.\.venv\Scripts\python.exe -m pip install xxx`。

## 网络铁律

**Hugging Face 与 hf-mirror.com 在本机均不可达。** 模型一律走 ModelScope:

```bash
pip install modelscope
modelscope download --model <name> --local_dir <path>
```

不要重试 HF,不要找代理绕过。

## 唯一规范源

`finetune/docs/规格.md` 是唯一规范源。

- 代码里的常量,规格里有的**必须与之一致**,不得凭记忆写死另一个值
- 代码与规格冲突 → **停下来报告**,不要自行选一边
- 规格内部自相矛盾 → **停下来报告**,不要自行解释
- 引用规格用小节标题名(如「gold 数据格式」),不要用编号——编号会随编辑漂移

## 不要重写已验证的代码

`finetune/scripts/primitives.py` 里的解析器、结构化 PII 生成器、`find_all` **已通过测试,不要重写、不要"改进"**。

跑 `.\.venv\Scripts\python.exe finetune\scripts\primitives.py` 可复现全部七项自检。

**偏移写错是静默失败**——机械校验、schema 校验全都能过,要到评测时才发现,而那时数据已经全部生成完了。

## 不得自行决定的事

以下一律停下来问:

1. 实体标签的语义边界(什么算 PERSON、ADDRESS 与 LOCATION 怎么分)
2. 某条合成文本是否自然、是否有逻辑矛盾
3. 某个英文姓名是否是现实中的公众人物
4. 数据的类别配比、语种配比、长度配比
5. 任何样本是否应进入冻结测试集
6. 测试集冻结后是否允许重建
7. API 成本止损阈值
8. **是否把一次人工审阅发现转成永久硬断言**

第 8 条最重要。上一轮把人工发现逐条正则化,一个词表元组连续 25 次拒掉合法输出,烧掉 160 秒和 25 次调用、产出 0 条。**开放式自然语言语义不能用有限词表做硬门槛。**

## 失败处理:丢弃优先于修复

生成阶段遇到畸形输出 → **丢弃,重新生成**。

**禁止**把上一次的错误内容回灌进 prompt 让模型改。这会让模型围着错误做局部改写,直接导致模板坍缩。

止损:
- 单样本重试上限 3 次
- 同一错误签名连续 3 次 → 停止,报告
- 全局上限见规格的「API 预算与止损」
- **确定性的程序断言失败不重试**(重试不会改变结果,只会花钱)

## 提交纪律

- 提交前 `git status --short`,**禁止 `git add .`**
- 只提交 `finetune/` 和根目录 `AGENTS.md`;碰到 `backend/` 或 `frontend/` 的改动先问
- 脚本与文档分开提交;影响指标的脚本单独提交
- 提交信息描述系统行为,不写"按某某要求"
- 生成 manifest 前必须先提交生成脚本,否则 manifest 里的 `generator_commit` 指向一个不含生成器的旧提交

## 任务报告格式

```
## 做了什么
（改动的文件列表 + 每个文件一句话）

## 实测结果
（命令 + 原样粘贴的输出，不要复述、不要总结）

## 遇到的问题
（原始报错原样贴出。解决不了就说解决不了，不要绕过）

## 我做过但没被要求的判断
（任何规格没写死、你自己选了的地方）

## 需要人拍板的
```

最后两节最重要。规格总有没覆盖到的地方,你在那里做的选择必须显式说出来,不能埋在代码里。

## 只读调查任务

不修改/创建/删除任何文件,不 git 写操作,不装包,不启动服务。任何 key/token 只显示前 6 位。失败原样贴报错,不绕过。
