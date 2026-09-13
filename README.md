# JT_V1

群聊分析研判的本地可运行闭环。当前支持案件与多轮研判对话、Excel 导入、混合检索、本地/API 模型、异步研判、人工复核、操作审计和案件数据管理。

## 安装并运行

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

前端：`http://127.0.0.1:8765/ui`  
API 文档：`http://127.0.0.1:8765/docs`。

协作请先看：

- [需求说明书](docs/需求说明书.md)
- [协作规范](docs/协作规范.md)
- [开发进度](DEVELOPMENT_PROGRESS.md)

主要接口：

- `POST /cases`：创建案件
- `PATCH /cases/{case_id}`：修改或归档案件
- `DELETE /cases/{case_id}`：删除案件及全部关联数据
- `GET /cases/{case_id}/sources`：查看来源资料清单
- `GET /cases/{case_id}/export`：导出案件 JSON
- `GET /cases/{case_id}/audit-logs`：查看操作审计记录
- `GET /cases/{case_id}/conversations`：列出案件下的研判对话
- `POST /cases/{case_id}/conversations`：新建研判对话
- `GET /conversations/{conversation_id}/turns`：读取对话内的历史研判
- `PATCH /conversations/{conversation_id}`：重命名、置顶、未读、归档、分区或移动空对话
- `POST /conversations/{conversation_id}/fork`：在本案件内分叉研判对话
- `DELETE /conversations/{conversation_id}`：删除对话及其研判、复核记录
- `POST /cases/{case_id}/sources`：导入聊天 Excel
- `GET /cases/{case_id}/search`：检索消息
- `GET /cases/{case_id}/messages/{message_id}/context`：按原始顺序读取前后文
- `GET /cases/{case_id}/messages/{message_id}/time-neighbors`：读取同群时间邻域
- `POST /cases/{case_id}/analysis`：检索、扩展上下文并生成研判
- `POST /cases/{case_id}/analysis-tasks`：创建异步研判任务
- `GET /analysis-tasks/{task_id}`：读取任务状态与结果
- `POST /analysis-tasks/{task_id}/cancel`：取消排队中或运行中的任务
- `POST /analyses/{analysis_id}/reviews`：追加采纳、修改或驳回复核
- `GET /analyses/{analysis_id}/reviews`：读取复核历史

## 启用本地 Qwen

项目通过 LM Studio 的 OpenAI 兼容接口调用本地模型，不在 FastAPI 进程中重复加载 GGUF：

```powershell
$env:LLM_ENABLED="1"
$env:LLM_BASE_URL="http://127.0.0.1:1234/v1"
$env:LLM_MODEL="qwen/qwen3.6-35b-a3b"
$env:EMBEDDING_BACKEND="lmstudio"
$env:EMBEDDING_MODEL="text-embedding-nomic-embed-text-v1.5"
uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

默认模型文件为 `D:\model\lmstudio-community\Qwen3.6-35B-A3B-GGUF\Qwen3.6-35B-A3B-Q4_K_M.gguf`，由 LM Studio 负责加载和显存管理。

切换 embedding 模型后需要重新导入案件资料，避免混用不同维度的历史向量。

## 启用 API 模型

页面可在“本地模型 / API 模型”之间切换，并可直接填写 API 地址、模型名和 Key。这些值仅随当前研判请求发送，不写入数据库或浏览器存储。也可以用后端环境变量提供默认值：

```powershell
$env:API_LLM_BASE_URL="https://你的服务地址/v1"
$env:API_LLM_MODEL="你的模型名"
$env:API_LLM_API_KEY="你的 API Key" # 内网免鉴权服务可不设置
$env:API_LLM_ALLOWED_HOSTS="api.example.com,127.0.0.1" # 可选；限制前端可填写的 API 域名
uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

API 服务需兼容 OpenAI 的 `POST /chat/completions`。本地 LM Studio 使用 JSON Schema 结构化输出；DeepSeek 等云 API 使用 `json_object`（见 `app/llm.py`）。

## 测试

```bash
pytest
```

默认数据库位于 `data/jt.db`，不需要安装或启动数据库服务。

## BGE-M3

默认使用仅供测试的确定性向量器，不代表语义相似度。需要真实模型时：

```bash
pip install -r requirements-model.txt
python down_model.py
set EMBEDDING_BACKEND=bge
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

原有一次性向量导出脚本仍保留在 `test.py`。

当单案消息量达到约 10 万或出现多用户并发需求时，再迁移到 PostgreSQL＋pgvector。
