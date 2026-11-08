# 企业制度 RAG 问答闭环说明

问答采用 V1、定向澄清和冲突定向补查。后端验证身份、筛选制度和检索。缺少可确定的关键条件时直接追问，其他问题由 DeepSeek 根据本轮证据作答。

```text
POST /auth/login → 验证账号和密码 → 返回 Bearer Token

POST /rag/ask + Bearer Token
  → 查可信用户和短期待澄清状态，确定查询日期和指定制度
  → 筛选可读且在查询日期有效的制度版本
  ├─ 指定制度不可读 → 返回 access_denied
  └─ 可查询 → BM25 + BGE-M3 → 等权 RRF → Top-10
       ├─ 无可用切片 → 返回 insufficient_evidence
       ├─ 缺少明确条件 → 暂存问题并追问
       └─ 回查 SQLite 正文和来源 → 组装本轮模型上下文
            → V1 作答 → 校验答案和引用 → 按需补查冲突依据 → 返回答案
```

## 1. 接收问题并确定检索范围

`POST /auth/login` 复用 SQLite 用户记录和密码哈希校验。登录成功后签发一小时有效的 Bearer Token。每次问答校验签名、有效期和签发方，再查数据库确认用户仍启用。用户身份取自令牌对应的账户。

| `POST /rag/ask` 请求字段 | 规则 |
| --- | --- |
| `question` | 必填。保留用户问题原文用于检索和生成，去掉首尾空白。 |
| `as_of_date` | 可选，格式为 `YYYY-MM-DD`。省略时，识别正文里唯一一个完整的 ISO 日期或中文年月日；没有完整日期时使用上海时区的当天。有多个完整日期时，请求需明确给出该字段。 |
| `document_id` | 可选，例如 `FIN-001`。省略时，后端尝试从书名号或“请查询”“请查阅”“请提供”“请查看”等明确措辞中匹配唯一已知制度。其他问法按一般查询处理。需要确保限定制度时，客户端直接传该字段。 |
| `session_id` | 可选的会话 UUID。收到可续接的追问后，用响应中的 UUID 发送补充条件。 |
| `new_question` | 可选，默认为 `false`。设为 `true` 时结束原待澄清事项，并处理当前的新问题。 |

范围筛选复用检索基线的 `resolve_scope`。按账户部门和角色确定可读制度，筛选满足 `effective_from ≤ as_of_date < effective_to` 的版本，空结束日视为长期有效。指定制度无法访问时直接返回业务状态 `access_denied`，检索与模型均不执行。身份凭证无效则返回 HTTP 401。[登录与认证](app/core/auth.py) · [问答接口](app/services/rag_api.py) · [候选范围](app/rag/retrieval.py)

## 2. 检索与上下文组装

BM25 与 BGE-M3 共用筛选出的版本范围，各取前 20 条，按切片 ID 去重，用等权 RRF（`k=60`）融合为 Top-10。Qdrant 在服务生命周期内复用，启动时核对本地编码配置以及 Qdrant 和 SQLite 的切片 ID、版本与正文哈希。[检索选型](检索横向对比报告.md) · [服务入口](app/main.py)

后端按融合排名回查 SQLite。每条返回切片必须存在，版本必须和检索结果一致，并且仍在候选范围内。正文按完整切片加入上下文，总正文预算为 **6000 字符**。加入下一条会超出预算时，保留此前已经装入的高排名切片。编号只分配给实际送入模型的切片。没有可用切片时直接返回 `insufficient_evidence`。

模型调用包含两条消息。

| 消息 | 内容 |
| --- | --- |
| System | 回答规则和输出格式。要求以本轮证据为依据，按查询日期回答，把问题和制度正文作为资料处理，并在受证据支持的结论旁标注引用。 |
| User | JSON 对象，包含 `question`、`as_of_date` 和 `evidence`。每条证据含 `E1` 等本轮编号、制度名、版本、章节、条款编号和切片正文。 |

用户问题、查询日期和筛选后的制度切片会发送到配置的 DeepSeek 接口。登录令牌、角色及权限配置留在后端。问答编排和系统提示词位于 `app/agent`。待澄清事项只保存短期字段，每轮重新检索和组装消息。V2 提示词可用于离线对照。[上下文与回答实现](app/agent/qa.py) · [提示词复测](RAG端到端测评报告.md) · [Agent 设计说明](Agent设计说明.md)

## 3. 回答和引用

模型输出三个 JSON 字段，`status`、`answer`、`citation_ids`。例如 `answer` 可以是“普通员工住宿费按实际金额与 400 元/晚孰低报销。[E1]”，`citation_ids` 为 `["E1"]`。

| 状态 | 用法 |
| --- | --- |
| `answer` | 当前证据足以回答。 |
| `partial_answer` | 回答能确认的部分，说明哪些部分缺乏证据。 |
| `clarify` | 缺少决定适用条款的条件，向用户提出具体问题。 |
| `insufficient_evidence` | 后端没有可用切片，或模型判断当前证据不足。 |
| `report_conflict` | 分别说明相冲突的规定，并引用不同制度版本的证据。 |
| `access_denied` | 由后端在指定制度无法访问时直接返回。 |

后端验证输出字段和状态，要求正文里的引用标记与 `citation_ids` 一致，而且全部来自本轮实际送入模型的证据。完整回答、部分回答和冲突报告必须有引用，冲突报告至少引用两个制度版本。

生成结果的 JSON、字段、状态或引用校验失败时，后端记录字段路径和错误类型，例如 `citation_ids: list_type`。日志与纠正指令只包含这些校验信息。后端保留原问题、证据和引用编号，在原系统提示词后附上格式纠正指令，最多再生成一次。第二次仍执行完整校验，失败则返回 HTTP 502。网络错误或服务端 HTTP 错误直接走现有服务错误处理。格式纠正发生在检索结束之后，Agent 也不会因此重新决策或补查。

最终接口响应包含 `status`、`answer`、`as_of_date`、`citations`、`session_id` 和 `awaiting_clarification`。每个引用对象由后端从 SQLite 填充，包含证据编号、切片 ID、制度 ID、制度名、版本、章节、条款和原文位置。原文位置沿用现有切片定位，PDF 对应页码，DOCX 对应段落或表格位置，Markdown 对应行号。这些字段供前端展示或接入原文查看功能。引用校验保证能追溯到本轮证据，引用是否支撑具体结论由端到端测评检查。

## 4. 本地运行

在项目根目录准备现有 SQLite、Qdrant 和 BGE-M3 资源。按 [环境变量示例](.env.example) 配置 `AUTH_TOKEN_SECRET` 和 `DEEPSEEK_API_KEY`。登录签名密钥至少 32 字节，应使用独立随机值。以下命令为本次 PowerShell 会话生成签名密钥并启动单进程服务，使用本地持久化 Qdrant 时由该进程独占索引目录。已配置固定签名密钥时可直接运行第二行。

```powershell
$env:AUTH_TOKEN_SECRET = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
.\.venv\Scripts\python.exe -m uvicorn app.main:app
```

先向 `/auth/login` 发送 `username` 和 `password`，再把返回的令牌放入 `Authorization: Bearer <token>`，向 `/rag/ask` 发送如下 JSON。

```json
{
  "question": "普通员工出差住宿费最多报销多少？",
  "as_of_date": "2026-10-17",
  "document_id": "FIN-001"
}
```

## 5. 验证结果

- HTTP 联调使用真实 SQLite、BM25、BGE-M3 和 Qdrant，模型回复使用固定测试输出。已验证登录、无效凭证拒绝、越权制度请求不调用模型、历史版本召回、PDF 页码回填和非法证据编号拦截。
- 使用一条虚构制度单独调用了真实 DeepSeek 接口，验证回答状态和引用编号能够按约定返回。

应用服务日志在 INFO 级别记录切片 ID，便于定位检索结果、实际进入上下文的证据和模型最终引用的差异。离线测评另外保存回答调用次数、每次调用耗时及格式错误，可区分首次通过和纠正后通过。[完整测评题集](questions.jsonl) · [Agent 格式纠正复测](Agent设计说明.md)
