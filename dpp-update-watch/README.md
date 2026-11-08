# DPP 更新跟踪

在本机定期检查欧委会的 DPP 语义资源，保存原文件与变化记录，并通过局域网网页查看、标记处理。抓取、对比、报告和定时运行都由普通程序完成，不调用大模型，不需要模型 Token、API Key 或登录欧盟注册中心。

监控来源：[欧委会 DPP Resources & documentation](https://single-market-economy.ec.europa.eu/single-market/digital-product-passport/resources-documentation_en)。默认重点跟踪页面中的 **Semantic Data Artifacts** 和 **Semantic Data Guide**。页面日期、下载链接和附件实际内容分别检查；即使文件名、网页日期不变，附件变化仍能被识别。

## 打开与运行

运行环境为 Python 3.10 或以上，核心监控功能仅使用 Python 标准库，无需 `pip install`。钉钉推送另依赖本机已安装并登录的 DWS 命令行；它使用现有钉钉身份，不依赖大模型。

已有 10 月 30 日官方原文时，可以先导入参考基线，再执行在线检查：

```bash
python app.py seed
python app.py check
```

默认基线清单路径在 `config.json` 的 `baseline_manifest` 中；也可以使用 `python app.py seed --manifest /实际清单路径.json`。导入不会覆盖已经跟踪的新版资源，也不等同于一次在线检查。

在本目录运行一次检查：

```bash
python app.py check
```

手动启动局域网查看页，绑定地址与端口默认读取 `config.json`：

```bash
python app.py serve
```

局域网访问地址：[http://192.168.110.79:8768](http://192.168.110.79:8768)。如果已启用下面的网页用户服务，直接打开即可，无需再手动启动一个服务。

服务按配置监听 `0.0.0.0`，并核对来源地址和访问主机：默认允许 `192.168.110.0/24` 以及本机回环地址，主机名为 `192.168.110.79`、`127.0.0.1`、`localhost`。局域网用户共享查看与处理标记，不设独立登录账号。配置中的 `allowed_hosts`、`allowed_networks` 和 `public_url` 应随实际内网地址维护。

## 自动运行时间

安装本机用户定时器和网页服务：

```bash
python schedule.py install
python schedule.py status
```

检查时间固定为**每天布鲁塞尔时间 18:30**：

| 欧洲时间 | 对应北京时间 |
|---|---|
| 冬令时 CET，UTC+1 | 次日 01:30 |
| 夏令时 CEST，UTC+2 | 次日 00:30 |

定时器使用 `Europe/Brussels`，随夏令时自动切换。页面记录统一显示北京时间（`display_timezone=Asia/Shanghai`），与调度时区分别配置。`Persistent=true` 会在定时器恢复时补跑关机期间错过的检查一次；不会逐天补发多次。主机需要开机并联网，用户服务需要可用的用户会话；安装程序不会自动开启用户驻留，也不会使用 root 或修改其他应用的服务。

本机创建的五个用户服务：

- `futris-dpp-watch.timer`：每日定时触发检查。
- `futris-dpp-watch.service`：执行一次检查后退出。
- `futris-dpp-watch-web.service`：提供局域网网页，监听地址和端口读取配置。
- `futris-dpp-watch-notify.timer`：开机后约2分钟启动，之后每5分钟处理通知队列。
- `futris-dpp-watch-notify.service`：只重试待发送消息或查询发送回执，不抓取官网资源。

查看运行记录：

```bash
systemctl --user list-timers futris-dpp-watch.timer
journalctl --user -u futris-dpp-watch.service -n 80
journalctl --user -u futris-dpp-watch-web.service -n 80
journalctl --user -u futris-dpp-watch-notify.service -n 80
```

停止自动检查和网页服务：

```bash
python schedule.py uninstall
```

这条命令仅移除本项目确认归属的服务，不删除已保存的历史文件与报告。移动整个项目目录后，需先在原位置卸载服务，再从新位置重新安装。

## 如何看结果

- **没有变化**：本次抓取及资源检查成功，和此前内容一致。
- **发现变化**：生成待处理记录，保留来源、前后版本、文件校验值和规则差异。
- **检查失败或部分失败**：明确显示网络、解析或下载错误；保留上次成功内容，不把失败误报为没有变化。
- **已查看／忽略**：只改变处理标记，不删除证据，也不回退自动对比基线。后续仍从最近成功下载的内容继续比较。

附件原始 SHA-256 与 ZIP 内文件内容分别比较。仅 ZIP 重新压缩、时间戳变化等情况，会与字段内容变化区分。字段新增、删除、类型、必填或范围调整可以定位到具体文件和 JSON Pointer；SHACL／词表变化保留文件差异摘要。

报告中“可能不兼容”等判断由固定规则生成，供研发判断影响，不等于欧委会确认的兼容性结论。工具不会自动替换业务系统的数据模型，也不执行完整 SHACL 合规校验。

提醒同时保留本机桌面通知、网页待处理列表和钉钉个人消息。桌面通知依赖本机通知服务；网页和报告保留完整变化证据。原始文件、检查历史和报告默认保留，不自动清理。

## 钉钉个人提醒

钉钉消息只发送给配置中确认的当前用户**宁蒙昌**，不发部门群或其他联系人。只有发现资源变化、官方目录变化、新资源或检查失败时才进入通知队列；每天检查没有变化时不发消息。

配置位于 `config.json` 的 `dingtalk` 对象，包含是否启用、DWS 可执行文件的绝对路径、已登录的组织／用户 Profile、接收人和允许通知的事件种类。配置不保存模型 Token、DWS 登录凭证或钉钉密码；登录状态由现有 DWS 管理。

```bash
python app.py notify-pending
```

这条命令手动处理本地通知队列，不检查官网。正常检查结束会尝试发送新消息；额外的通知定时器负责后续重试、查询回执和中断恢复，浏览器或大模型关闭后仍能运行。

通知通过持久化发件箱保存。同一条记录使用稳定 UUID，重试沿用该 UUID，并核对实际发送回执；已确认发送成功的消息不重复发送。发送请求已受理、但还未拿到最终结果时，优先继续查询已有任务的状态。

如果 DWS 未登录、登录过期或 Profile 不可用，消息保留在队列并标明阻塞原因，先恢复对应 DWS 登录再继续发送；不会为此不断创建新消息。仅回执明确标注可重试的失败按间隔重试；发送超时或结果不明确时保留未知状态，不自动重发。`retry_seconds` 默认300秒；这只控制通知队列，不改变官网每天一次的检查频率。

网页刷新与官方检查是两件事：**后端每天按欧洲时间检查官网，网页每 30 秒读取一次本机检查结果；检查执行中每 2 秒刷新进度。**网页切到后台时暂停自动刷新，回到页面立即刷新。关闭浏览器不会停止后端的定时检查，也不会停止其他局域网用户查看。

配置与数据位置：

| 路径 | 内容 |
|---|---|
| `config.json` | 官方页面、重点资源、检查超时、监听地址、端口、局域网允许范围、网页刷新与钉钉通知选项 |
| `data/watch.sqlite3` | 资源状态、检查历史、更新处理标记及通知发件箱 |
| `data/snapshots/<SHA-256>` | 以实际内容校验值命名的原始下载文件 |
| `data/reports/<记录ID>.html`、`.json` | 可阅读的中文报告与完整机器记录 |

来源页面上的其他文档标题、说明、日期或入口变化也会记入目录变化；默认只下载并深入对比两份语义资源。需要额外跟踪 OpenAPI 等附件时，可扩展 `config.json` 的 `watch` 列表，再运行检查。修改监听地址、端口或通知重试间隔后重新运行 `python schedule.py install`，安装器会更新并按需重启相关服务；欧洲检查时间保持不变。

完整设计见 [功能设计.md](功能设计.md)。

报告 HTML／JSON 是事件创建时的证据快照；当前“待确认／已确认／忽略”以网页和 SQLite 记录为准。

## 验证代码

后端运行 `python -m unittest discover -s tests -q`。页面刷新行为可用 `node tests/test_ui_polling.cjs` 验证；Node仅用于这项开发测试，日常监控仍只需Python。

恢复DWS登录或修复推送配置后，可运行 `python app.py notify-resume` 重新处理可安全恢复的受阻记录。超过安全重试时限或发送结果未知的记录需人工核对，不自动换UUID重发。

打包下载版的钉钉推送默认关闭，接收人及账号配置留空；不携带本机登录凭据。部署到其他机器时需用该机器已经登录的DWS账号完成配置。
