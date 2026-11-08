# 企业制度智能问答平台

面向星云科技有限公司的内部制度查询平台。员工登录后可用自然语言查询请假、差旅报销、采购等制度，按指定日期查看适用版本，并通过引用核对制度名称、条款和出处。系统在检索前检查访问权限，生成时流式显示草稿，校验通过后展示正式回答和引用。问答记录保留在当前页面。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 前端 | React、TypeScript、Vite |
| 后端 | Python、FastAPI、Uvicorn、SSE 流式响应 |
| 登录与数据 | JWT、Argon2、SQLAlchemy、SQLite、Alembic |
| 文档与检索 | PDF、DOCX、Markdown；BM25 与 BGE-M3 向量检索，使用 Qdrant 本地索引和 RRF 融合 |
| 回答生成 | DeepSeek API |

## 本地启动

下面的命令以 Windows PowerShell 为例。使用 Python 3.12，以及 Vite 支持的 Node.js 版本（20.19+ 的 20.x，或 22.12+）。

### 1. 安装依赖并配置后端

在项目根目录执行：

```powershell
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

在根目录的 `.env` 中，将生成的随机字符串填到 `AUTH_TOKEN_SECRET`，将模型服务的 API 密钥填到 `DEEPSEEK_API_KEY`。已配置签名密钥的环境可沿用原值。示例配置和字段说明见 [.env.example](.env.example)。

### 2. 首次初始化数据库和索引

已有可用数据库和索引时可跳过。本步骤会创建本地 SQLite 数据库、初始化演示账号，并从仓库的制度原文重建索引。首次下载 BGE-M3 模型需要联网。

```powershell
New-Item -ItemType Directory -Force storage | Out-Null
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m scripts.init_identity
.\.venv\Scripts\python.exe -m scripts.import_documents
.\.venv\Scripts\python.exe -m scripts.parse_documents
.\.venv\Scripts\python.exe -m scripts.structure_documents
.\.venv\Scripts\python.exe -m scripts.normalize_documents
.\.venv\Scripts\python.exe -m scripts.clean_line_breaks
.\.venv\Scripts\python.exe -m scripts.restore_tables
.\.venv\Scripts\python.exe -m scripts.chunk_documents
.\.venv\Scripts\python.exe -m scripts.import_chunks
.\.venv\Scripts\python.exe -m scripts.download_bge_m3
.\.venv\Scripts\python.exe -m scripts.import_chunk_vectors
```

本地演示可用 `employee01`，初始密码见[账号初始化脚本](scripts/init_identity.py)中的 `INITIAL_PASSWORD`。

### 3. 启动前后端

在项目根目录启动后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开一个 PowerShell，从项目根目录启动前端：

```powershell
Set-Location frontend
npm ci
npm run dev -- --host 127.0.0.1
```

打开[登录页](http://127.0.0.1:5173/)。前端会把登录与问答请求代理到本机 FastAPI。后端提供[接口文档](http://127.0.0.1:8000/docs)与[健康检查](http://127.0.0.1:8000/health)。

## 目录与验证

| 路径 | 内容 |
| --- | --- |
| `app/` | API、权限、会话与检索 |
| `frontend/` | 登录页和问答工作台 |
| `config/`、`data/` | 制度清单、访问规则与制度原文 |
| `alembic/`、`scripts/` | 数据库迁移、知识库初始化和评测 |
| `storage/` | 本地模型、数据库、索引、解析产物和评测结果 |
| `tests/`、`docs/` | 后端测试和设计文档 |

- 后端测试在根目录运行 `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。
- 前端构建在 `frontend/` 运行 `npm run build`，产物写入 `frontend/dist/`。
- Python 依赖集中在 `requirements.txt`，前端依赖由 `frontend/package-lock.json` 锁定。

更多交互与接口设计见[前端设计方案](docs/前端设计方案.md)。
