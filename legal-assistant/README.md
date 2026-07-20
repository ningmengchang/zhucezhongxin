# 法规答复与证据问答助手

本项目是一个本地法规优先的内部 Web 试点。业务系统不调用大模型 API，而是通过本机 `codex exec` 和当前 ChatGPT 登录生成结构化答案。

## 已实现

- 自动发现本地目录中的 PDF、Markdown、纯文本法规并按 SHA-256 去重；
- 扫描型PDF使用本机 Tesseract OCR，不调用外部OCR服务；
- 所有本地法规文件同权重检索，并保留生效日期和版本关系；
- 按 PDF 页码及 Article 上下文切分证据；
- SQLite FTS5持久化BM25索引、等权来源融合与毫秒级候选检索；
- 通过临时STDIO MCP向单次Codex开放 `SearchLaw` / `ReadLawSource`，先定位再按需读取原文；
- 同一 `source_id` 每个任务只读取一次；审计轨迹记录模型思考、工具执行和答复生成耗时；
- `codex --ask-for-approval never exec --json --output-schema` 非交互调用，本地研究阶段不使用大模型API；
- 只读沙箱和临时任务目录；仅在本地证据不足时执行1轮 Codex 原生Web Search；
- Web来源限欧盟官方域名，并执行HTTPS、公共地址、重定向和官方页面原文核验；
- 默认生成“一句直接结论 + 一张动态业务表”；模型根据问题生成2至5个业务列，不预设服务商或其他角色视角；
- 法规要求、官方解释、本地标准指导、工程建议和不确定事项五级内容性质标注；
- 证据 ID、文件、页码和英文引文的确定性校验；
- 无证据时阻断确定性结论；
- Vue 3 问答、服务商责任边界、本地/Web来源标签、折叠法规依据与检索过程、法规库和证据预览；
- 小团队试点队列：默认并发 2、队列 20。
- 同一浏览器会话最近5轮有限记忆；当前问题与历史问题独立检索、近似历史自动去重，历史仅帮助理解指代，不能压过本轮正式法源或充当法规证据。

## 数据边界

法规文件、提取文本、检索索引和审计记录保存在本机。用户问题及检索后选中的法规片段会通过当前 Codex 的 ChatGPT 登录会话发送给 OpenAI。该方案不是离线模型部署。

后端不会读取或保存 OpenAI API Key。本地检索阶段不启用网络；只有本地证据评估为不足时，单独的Web研究阶段才会为 `codex exec` 启用 `--search`。Web查询、搜索结果及官方页面内容会通过当前ChatGPT登录会话处理，页面会与本地法规来源分别标明。

Web结果不会自动写入本地法规资料库。只有允许域名中的HTTPS页面能够访问、且模型引用原文能在官方HTML或PDF中逐字匹配时，才可以进入最终报告。

## 启动

### 1. 后端

```bash
cd legal-assistant/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app
```

内网地址：`http://192.168.110.79:8090`，交互文档：`http://192.168.110.79:8090/docs`。

检查本机 Codex：

```bash
codex --version
codex login status
```

### 2. 前端

```bash
cd legal-assistant/frontend
npm install
npm run dev
```

开发模式打开 `http://192.168.110.79:5173`。

生产式本机预览：

```bash
npm run build
cd ../backend
.venv/bin/python -m app
```

后端检测到 `frontend/dist` 后会直接托管静态页面。

依赖和前端均准备完成后，也可以直接运行：

```bash
bash legal-assistant/start.sh
```

## 本地法规资料库

服务启动时默认递归扫描 `LEGAL_ASSISTANT_SOURCE_DIR`。目录内所有 PDF、Markdown 和纯文本文件均作为法规资料自动入库；应用源码目录、隐藏目录和README说明文件会被排除。

资料库不设置文件等级或发布、停用状态。目录中发现并成功提取的全部文件都会立即以相同权重参与检索，包括名称或内容标明为草案的文件。管理员可在“法规资料库”页面点击“同步本地目录”扫描新增文件；页面和后端均不提供文件上传、等级设置、审核状态或网络归档接口。

系统默认同时检索当天有效法规，以及在回答截止日期前已正式公布、但尚未生效的法规。后者会在答案和每条引用中醒目标注生效日期，不会表述为当前已经生效的义务。请求可设置 `include_future_rules=false`，切换为仅检索截止日期当日有效法规。

`POST /api/questions` 和 `POST /api/search` 均支持：

- `as_of_date`：回答截止日期；不填写时为当天；
- `include_future_rules`：是否纳入已公布待生效规则，默认 `true`。

问答页面会为浏览器生成随机 `conversation_id`。后端仅取同一会话最近5个已完成任务，并压缩为问题、答案摘要和论点标题；检索使用最近2个问题补充主题。点击“新建会话”会更换会话ID并停止使用旧上下文，旧审计记录仍保留在本地数据库中。

## 角色

试点版提供两级权限契约：

- 普通接口默认角色为 `user`；
- 本地目录同步要求请求头 `X-Role: admin`。

正式接入企业 OIDC 时，用网关或认证中间件把组织角色映射为上述两种应用角色；不要继续信任客户端自行提交的 `X-Role`。

## 测试

```bash
cd legal-assistant/backend
.venv/bin/pytest -q
```

主要研究配置：

- `LAW_RETRIEVAL_MODE=mcp`：默认使用单次Codex工具循环；紧急回滚可设为`legacy`；
- `LAW_SEARCH_TOP_K=8`：每次法规候选数；
- `LAW_SEARCH_MAX_CALLS=4`、`LAW_READ_MAX_CALLS=8`：单个问题的搜索和原文阅读上限；

- `RESEARCH_LOCAL_ROUNDS=3`：legacy回滚模式的安全上限；
- `RESEARCH_WEB_ROUNDS=1`
- `RESEARCH_MAX_EVIDENCE=60`
- `WEB_SEARCH_ENABLED=true`
- `WEB_MAX_SOURCES=5`
- `WEB_OFFICIAL_DOMAINS=europa.eu,eur-lex.europa.eu,op.europa.eu,echa.europa.eu`

默认MCP模式由同一次Codex自主执行最多4次候选搜索，并对准备引用的来源执行最多8次原文读取；只有 `ReadLawSource` 实际返回的来源才能进入最终引用。Web搜索不由普通内容缺口触发，仅在用户明确要求联网，或本地按需阅读后仍缺关键正式法规、最新实施文件或官方解释时执行。`RESEARCH_LOCAL_ROUNDS`只供legacy回滚模式使用。

不调用真实 Codex 的服务测试可设置：

```bash
CODEX_MOCK=true .venv/bin/python -m app
```

## 当前试点边界

- 当前检索实现为SQLite FTS5持久化BM25索引；启动时自动校验和重建，管理员同步本地目录时增量更新并清理已删除文件；
- Web搜索用于补齐本地正式法规或官方解释缺口，不替代本地法规库；第三方网页不作为法律结论依据；
- 扫描PDF通过本机Tesseract识别；识别质量受扫描清晰度影响，管理员仍需核对原文件；
- 不提供网页上传或网络归档入口；新增法规由管理员放入本地法规目录后同步；
- `X-Role` 只用于本机试点，不等同于正式身份认证；
- Codex限额、ChatGPT登录失效或网络错误会使任务失败，不会把缺少有效来源的内容作为确定性法规结论返回。
