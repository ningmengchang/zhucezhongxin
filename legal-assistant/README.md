# 法规助手

本项目是一个本地法规优先的内部 Web 试点。业务系统不调用大模型 API，而是通过本机长驻的 Codex App Server 和当前 ChatGPT 登录生成答案。

## 已实现

- 自动发现本地目录中的 PDF、Markdown、纯文本法规并按 SHA-256 去重；
- 扫描型PDF使用本机 Tesseract OCR，不调用外部OCR服务；
- 所有本地法规文件同权重检索，并保留生效日期和版本关系；
- 按 PDF 页码及 Article 上下文切分证据；
- SQLite FTS5持久化BM25索引、等权来源融合与毫秒级候选检索；
- 通过长驻 App Server 的 STDIO JSONL 协议与 Codex 交互，不再为每个问题启动一次 `codex exec`；
- 每个会话仅启动一个临时法规 MCP，并严格核验只开放 `SearchLaw` / `ReadLawSource`，先定位再按需读取原文；
- 同一 `source_id` 每个任务只读取一次；审计轨迹记录模型思考、工具执行和答复生成耗时；
- App Server 线程使用只读沙箱和独立临时工作区，关闭 App、插件、Shell、多智能体等无关能力；本地搜索、原文读取和原生 Web Search 在同一轮中按需使用；
- Web搜索由模型自主决定是否使用以及采用哪些来源；
- 默认输出原生Markdown正文；模型根据问题自行决定标题、段落、列表、表格、视角和篇幅，系统不做格式或字数限制；
- 法规、用户指南、标准、草案、工作文件和说明材料按相关性等权选材，不以正式法规范围限定答案；
- Vue 3 问答、原生Markdown、本地/Web工具记录、资料库和原文预览；
- 小团队试点队列：默认不同会话并发 2、队列 20，同一登录会话的问题串行处理。
- 会话级有限记忆：同一登录会话复用临时 App Server thread，默认20轮或空闲2小时后自动换新；退出、被顶下线、管理员下线或服务重启时删除。

## 数据边界

法规文件、提取文本、检索索引和审计记录保存在本机。用户问题及检索后选中的法规片段会通过当前 Codex 的 ChatGPT 登录会话发送给 OpenAI。该方案不是离线模型部署。

后端不会读取或保存 OpenAI API Key。服务启动一个本机 `codex app-server --stdio`子进程，使用当前 ChatGPT 登录；问题和模型实际读取的工具返回内容会发送给 OpenAI。模型自行决定是否查询本地资料、读取原文或执行 Web 搜索。

Web结果不会自动写入本地法规资料库。只有公共HTTPS页面能够访问、且模型引用原文能在HTML或PDF中逐字匹配时，才可以进入最终报告。

## 启动

### 1. 后端

```bash
cd legal-assistant/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app
```

内网地址：`http://192.168.110.79:8090`。

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

`conversation_id` 仅为旧客户端兼容字段，可以继续提交和保存在任务记录中，但不作为记忆键。当前记忆键是服务端的登录会话 ID，因此不能通过客户端伪造或跨账号共享上下文。旧审计记录保持不变。

## 角色

系统使用内置账号和24小时HttpOnly Cookie会话，所有业务接口均要求登录。同一账号全局只保留一个有效会话，后登录会使此前客户端失效。

登录页允许自行注册普通用户。用户名按不区分大小写方式判重，密码使用Argon2id哈希保存；注册成功后返回登录页手动登录。自行注册的账号只能获得法规问答权限，不能自行指定管理员角色。

- `nmc` 为固定管理员，可使用法规问答、本地资料库、同步、使用监控和运行状态；服务启动时会确保该账号保持管理员和启用状态，但不会重置密码；
- `hekai`、`kongruixia`、`sunbo` 为普通用户，只能使用法规问答；
- 普通用户可查看自己答案实际引用的证据和原文，不能浏览完整资料库；
- 问答任务按账号隔离，不能读取其他账号的任务和答案。

密码使用Argon2id哈希保存，系统不再接受客户端提交的 `X-Role`。当前版本提供网页自助注册，但暂不提供密码修改或找回功能。

“使用监控”仅向管理员开放，显示最近30天的登录会话、IP、浏览器/设备、在线状态和完整问题处理详情；管理员可以强制下线其他会话。在线状态表示有效会话最近5分钟内有访问活动，活动时间最多每60秒写入一次。登录会话和问答记录超过30天后自动清理，账号不会被删除。

## 测试

```bash
cd legal-assistant/backend
.venv/bin/pytest -q
```

主要研究配置：

- `LAW_SEARCH_TOP_K=8`：本地搜索工具每次默认返回的候选数；
- `RESEARCH_TOTAL_TIMEOUT_SECONDS=480`：单个自主研究任务的总时间边界；
- `CODEX_CONCURRENCY=2`：不同会话的最大并发数；
- `CODEX_APP_SERVER_START_TIMEOUT_SECONDS=20`：App Server 启动及协议请求的超时秒数；
- `CODEX_CONVERSATION_MAX_TURNS=20`：单个登录会话在换新 thread 前保留的最大完成轮数；
- `CODEX_CONVERSATION_IDLE_SECONDS=7200`：会话记忆的空闲超时。

每个问题在当前 App Server thread 中启动一轮，并同时提供 `SearchLaw`、`ReadLawSource` 和原生 Web Search。模型自行判断法规原文语言、标准术语和拼写变体，并决定调用顺序及次数；搜索工具不包含中英术语映射或领域关键词加权。没有实际读取的本地来源或经过逐字核验的外部来源时，系统会阻止实质性结论并返回依据不足。App Server 异常时当前任务直接失败，不会暗中回退到 `codex exec`；后续请求会自动重启 App Server。

不调用真实 Codex 的服务测试可设置：

```bash
CODEX_MOCK=true .venv/bin/python -m app
```

## 当前试点边界

- 当前检索实现为SQLite FTS5持久化BM25索引；启动时自动校验和重建，管理员同步本地目录时增量更新并清理已删除文件；
- Web搜索范围和采用方式由模型自行判断，页面只记录实际工具调用；
- 扫描PDF通过本机Tesseract识别；识别质量受扫描清晰度影响，管理员仍需核对原文件；
- 不提供网页上传或网络归档入口；新增法规由管理员放入本地法规目录后同步；
- 当前为本机内置账号认证，尚未接入企业统一身份系统；
- Codex限额、ChatGPT登录失效或网络错误会使任务失败，不会把缺少有效来源的内容作为确定性法规结论返回。
