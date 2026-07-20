# 法规论点与证据问答助手

本项目是一个本地法规优先的内部 Web 试点。业务系统不调用大模型 API，而是通过本机 `codex exec` 和当前 ChatGPT 登录生成、审核结构化答案。

## 已实现

- 自动发现本地目录中的 PDF、Markdown、纯文本法规并按 SHA-256 去重；
- 扫描型PDF使用本机 Tesseract OCR，不调用外部OCR服务；
- A1/A2/B/C/D 法源等级、审核状态、生效日期和版本关系；
- 按 PDF 页码及 Article 上下文切分证据；
- 本地多查询 BM25 风格检索、条款邻近扩展与法源等级加权；
- A1/A2 直接证据门槛；
- `codex --ask-for-approval never exec --json --output-schema` 非交互调用；
- 只读沙箱、无 Codex 自主网络搜索、临时任务目录；
- 专业详版答案生成，以及事实真实性和回答完整性两阶段复核；
- 证据 ID、文件、页码和英文引文的确定性校验；
- 无证据时阻断确定性结论；
- Vue 3 问答、法规原文内联展示、服务商重点、法规库、PDF证据预览和 Codex 状态页面；
- 小团队试点队列：默认并发 2、队列 20。
- 同一浏览器会话最近5轮有限记忆；当前问题与历史问题独立检索、近似历史自动去重，历史仅帮助理解指代，不能压过本轮正式法源或充当法规证据。

## 数据边界

法规文件、提取文本、检索索引和审计记录保存在本机。用户问题及检索后选中的法规片段会通过当前 Codex 的 ChatGPT 登录会话发送给 OpenAI。该方案不是离线模型部署。

后端不会读取或保存 OpenAI API Key，也不会启用 Codex `--search`。Codex运行依据官方[非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode.md)。

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

已配置的正式法规保留A1/A2等既有等级；新发现文件默认以B级 `published` 状态加入检索，避免未经管理员确认的草案直接支撑确定性法律结论。管理员可在“法规资料库”页面调整等级、发布或停用，并点击“同步本地目录”立即扫描新增文件。页面和后端均不提供文件上传或网络归档接口。

系统默认同时检索当天有效法规，以及在回答截止日期前已正式公布、但尚未生效的法规。后者会在答案和每条引用中醒目标注生效日期，不会表述为当前已经生效的义务。请求可设置 `include_future_rules=false`，切换为仅检索截止日期当日有效法规。

`POST /api/questions` 和 `POST /api/search` 均支持：

- `as_of_date`：回答截止日期；不填写时为当天；
- `include_future_rules`：是否纳入已公布待生效规则，默认 `true`。

问答页面会为浏览器生成随机 `conversation_id`。后端仅取同一会话最近5个已完成任务，并压缩为问题、答案摘要和论点标题；检索使用最近2个问题补充主题。点击“新建会话”会更换会话ID并停止使用旧上下文，旧审计记录仍保留在本地数据库中。

## 角色

试点版提供两级权限契约：

- 普通接口默认角色为 `user`；
- 本地目录同步、审核和等级调整要求请求头 `X-Role: admin`。

正式接入企业 OIDC 时，用网关或认证中间件把组织角色映射为上述两种应用角色；不要继续信任客户端自行提交的 `X-Role`。

## 测试

```bash
cd legal-assistant/backend
.venv/bin/pytest -q
```

不调用真实 Codex 的服务测试可设置：

```bash
CODEX_MOCK=true CODEX_VERIFY_ENABLED=false .venv/bin/python -m app
```

## 当前试点边界

- 当前检索实现为本地 BM25 风格检索，接口已经与后续 BGE-M3/pgvector 检索器解耦；
- 扫描PDF通过本机Tesseract识别；识别质量受扫描清晰度影响，管理员仍需核对原文件；
- 不提供网页上传或网络归档入口；新增法规由管理员放入本地法规目录后同步；
- `X-Role` 只用于本机试点，不等同于正式身份认证；
- Codex限额、ChatGPT登录失效或网络错误会使任务失败，不会返回未审核的半成品答案。
