# EN 18222:2026 可开发接口设计

本目录现保留两个独立的 OpenAPI 版本：

- `openapi.yaml`：初始 EN 18222/EN 18223 通用实施版，已原样保留。
- `openapi-battery-pass-ready-v2.0-ev-feb27.yaml`：可直接导入 Apifox 的 BatteryPass-Ready 2.0、EV、February 2027 实施版。
- `README-BatteryPass-Ready-EV-Feb27.md`：新版的数据范围、读写策略、字段结构、Apifox 导入和验证说明。
- 本文其余部分：初始版的接口语义、字段字典、处理规则和实施决策。

> BatteryPass 项目开发应使用新文件及其专用 README；下文中的 `battery` 演示字段和写接口 `representation` 仅属于初始版，不得与 BatteryPass 实施版混用。

> 两个实施版均保持 EN 18223 的根级 DPP 头序列化：`digitalProductPassportId`、`granularity`、`dppStatus` 等直接位于 DPP 根对象。BatteryPass-Ready 2.0 源 Schema 的 `dppHeader` 包装仅作为内部映射模型，不暴露给 `/v1` 接口。

设计基线为 EN 18222:2026，并用 EN 18223:2026 补足 DPP 载荷字段。JSON 交换、安全传输和历史归档还分别依赖 EN 18216、EN 18219、EN 18221。

> 重要：这是一份“符合标准结构的实施配置文件”，不是欧盟委员会线上 DPP Registry 的官方 OpenAPI。中央 Registry 的域名、认证、产品组 Schema 和批量注册格式，应以欧盟委员会实际发布的技术文档为准。

## 1. 原始 PDF 质量结论

当前目录中的两份源文件内容完整、肉眼可读，但不适合作为最终机器处理基线：

| 文件 | 页数 | 图像分辨率 | 文字层 | 版本风险 | 结论 |
|---|---:|---:|---|---|---|
| `../EN 18222 2026.pdf` | 18 | 约 120 DPI | 无 | 首页未标出 2026-06-02 corrected and reissued；规范性引用仍未带 2026 年份 | 可阅读，不宜作为最终合规归档件 |
| `../EN 18223 2026.pdf` | 25 | 约 120 DPI | 无 | 同样早于 2026-06-02 更正版 | 可阅读，不宜作为最终合规归档件 |

已核验的正式更正版公开样张明确写明两份标准均于 2026 年 6 月 2 日由 CEN-CENELEC 更正并重新发布。公开可直接下载的版本只有 7 页样张；完整更正版可在 NEN Connect 免费注册后只读，或者从国家标准机构合法购买 PDF。样张不能替代全文，因此没有覆盖现有文件。

已确认的更正至少包括规范性引用的年份，例如：

- EN 18222 第 2 条：`EN 18221:2026`；
- EN 18223 第 2 条：`EN 18219:2026`、`EN 18221:2026`。

接口路径、方法和本设计使用的数据模型未发现因此发生改变。正式验收时仍应逐条对照授权取得的 2026-06-02 更正版全文。

## 2. 标准范围与实现边界

EN 18222 主要规定三类 API：

1. DPP 生命周期 API：创建、查询、更新、删除和历史版本读取；
2. DPP Registry 注册 API：注册一个 DPP；
3. 细粒度 API：读取、更新或移除单个数据元素。

它没有完整规定以下内容：

- 产品组的业务字段，例如电池容量、碳足迹、材料组成；这些来自产品组法规和语义 Schema；
- 统一的 OAuth/OIDC 地址、JWT claims 或角色名；
- `limit`、`cursor` 在 REST 中的确切位置及分页响应结构；
- 成功/失败响应的完整 JSON 外层封装；
- ETag、幂等键、并发控制、软删除状态机；
- 欧盟中央 Registry 的查询、修改、转移、注册证明和异步任务 API。

本设计把这些空白明确标为“实施约定”，避免开发团队把推定规则误认为标准原文。

## 3. 哪些方法必须实现

| 方法 | DPP 创建者/主服务商/备份服务商的要求 | 本配置文件 |
|---|---|---|
| ReadDPPById | 必须提供 | 已定义 |
| ReadDPPByProductId | 必须提供 | 已定义 |
| ReadDPPIdsByProductIds | 必须提供 | 已定义 |
| ReadDataElement | 建议提供 | 已定义 |
| CreateDPP | 主服务商及备份服务商建议提供 | 已定义 |
| DeleteDPPById | 主服务商及备份服务商建议提供 | 已定义 |
| ReadDPPVersionByIdAndDate | 主服务商及备份服务商建议提供 | 已定义 |
| UpdateDPPById | 存在获授权第三方更新场景时必须提供 | 已定义 |
| UpdateDataElement | 存在获授权第三方更新场景时建议提供 | 已定义 |
| RegisterProductDPP | Registry 必须提供 | 已定义 |

## 4. 接口总表

| EN 18222 方法 | HTTP | 路径 | 主要输入 | 成功输出 |
|---|---|---|---|---|
| ReadDPPById | GET | `/v1/dpps/{dppId}` | DPP ID、representation | 当前 DPP |
| ReadDPPByProductId | GET | `/v1/dppsByProductId/{productId}` | 产品 ID、representation | 当前有效的最新 DPP |
| ReadDPPVersionByIdAndDate | GET | `/v1/dppsByIdAndDate/{dppId}?date=...` | DPP ID、UTC 时点 | 指定时点有效的 DPP 版本 |
| ReadDPPIdsByProductIds | POST | `/v1/dppsByProductIds?limit=...&cursor=...` | 产品 ID 数组 | DPP ID 数组、分页信息 |
| CreateDPP | POST | `/v1/dpps` | 完整 DPP | DPP ID |
| UpdateDPPById | PATCH | `/v1/dpps/{dppId}` | RFC 7396 局部 DPP | 更新后的 DPP |
| DeleteDPPById | DELETE | `/v1/dpps/{dppId}` | DPP ID | 204，无正文 |
| RegisterProductDPP | POST | `/v1/registerDPP` | DppRegistryEntry | registrationId 或异步 correlationId |
| ReadDataElement | GET | `/v1/dpps/{dppId}/elements/{elementIdPath}` | DPP ID、JSONPath | 一个数据元素 |
| UpdateDataElement | PATCH | `/v1/dpps/{dppId}/elements/{elementIdPath}` | DPP ID、JSONPath、元素补丁 | 更新后的元素；移除时 204 |

路径参数中的 DPP ID、产品 ID 和 JSONPath 必须先按 UTF-8 执行百分号编码。不得把包含 `/`、`?`、`#` 的原始标识直接拼进 URL。

## 5. 通用协议约定

### 5.1 传输

- 只允许 HTTPS；
- TLS 1.2 是最低版本，建议 TLS 1.3；
- HTTP/2 是最低版本，建议支持 HTTP/3；
- 禁止 TLS 1.0、TLS 1.1、所有 SSL 版本以及 HTTP/1.x。

以上来自 EN 18216:2026。部署层应拒绝不满足版本要求的连接。

### 5.2 数据格式

首期强制支持：

```http
Content-Type: application/json
Accept: application/json
```

EN 18216 允许通过内容协商继续支持 XML、JSON-LD 和面向人的 HTML。为了先形成稳定的开发基线，本 OpenAPI 只承诺 JSON；增加其他格式时不得改变资源语义。

PATCH 使用：

```http
Content-Type: application/merge-patch+json
```

### 5.3 DPP 表示形式

所有返回 DPP 或 DataElement 的 GET，以及接收/返回 DPP 的 POST、PATCH，可带：

| 参数 | 含义 |
|---|---|
| `representation=compressed` | 默认值。DataElement 直接用业务字段名和值表达，元数据放在外部数据字典中 |
| `representation=full` | 返回 EN 18223 附录 A 风格的展开对象，包含 elementId、objectType、dictionaryReference 等 |

客户端省略参数时，服务端必须按 `compressed` 处理。

### 5.4 认证和字段级授权

- 公共数据读取允许匿名请求；带 Bearer Token 时可以返回该主体有权看到的受控字段。
- 创建、更新、删除和注册必须认证。
- 读取不是简单的“整本 DPP 允许/拒绝”。服务端要先计算访问权限，再对字段执行过滤或拒绝。
- 更新时每一个变更路径都要单独鉴权；一个路径失败，整个 PATCH 回滚。
- OpenAPI 中的 Bearer JWT 是部署占位方案，不是欧盟 Registry 官方认证定义。

截至 2026-08-27，EN 18239 和 EN 18246 仍处于正式批准/待发布阶段，因此权限模型和数据签名层必须可配置，不能把当前草案字段硬编码成不可迁移结构。

### 5.5 请求跟踪

客户端可发送：

```http
X-Correlation-ID: 01J6D4KAP9E2WQ0M4X8GZ6VB30
```

服务端规则：

1. 客户端提供时沿用；
2. 未提供时生成；
3. 在响应头、错误消息和跨系统日志中使用同一个值；
4. 不允许把用户身份、产品明文或其他敏感信息编码进 correlation ID。

### 5.6 并发控制

读响应返回：

```http
ETag: "dpp-v17"
```

PATCH/DELETE 建议要求：

```http
If-Match: "dpp-v17"
```

ETag 不匹配返回 HTTP 409 / `ClientResourceConflict`。ETag 是本实施配置文件增加的并发安全约定。

## 6. DPP 数据模型

### 6.1 DPP 头部

| JSON 字段 | 类型 | 必填 | 基数 | 校验与说明 |
|---|---|---:|---:|---|
| `digitalProductPassportId` | string | 是 | 1 | 全局唯一；建议采用 URI/URL 结构 |
| `uniqueProductIdentifier` | string | 是 | 1 | 必须符合 EN 18219 中选定的产品标识方案 |
| `granularity` | enum | 是 | 1 | 本配置文件固定小写：`model`、`batch`、`item` |
| `dppSchemaVersion` | string | 是 | 1 | 引用当前 DPP 所遵循的 Schema/内容规范版本 |
| `dppStatus` | string | 是 | 1 | 常见值：active、inactive、archived、invalid；允许法规新增值 |
| `lastUpdated` | UTC date-time | 是 | 1 | ISO 8601-1，必须以 `Z` 结尾 |
| `economicOperatorId` | string | 是 | 1 | 符合 EN 18219 的经济运营商标识 |
| `facilityId` | string | 否 | 0..1 | 符合 EN 18219 的设施标识 |
| `contentSpecificationIds` | string[] | 否 | 0..* | 引用横向或产品组内容规范；不得重复 |
| 其他业务字段 | DataElement | 否 | 0..* | 字段名和约束来自产品组法规、Schema 与数据字典 |

标识符不使用统一正则表达式，因为 EN 18219 允许 URL、Identification Link、DID、DOI、RFID/2D 等多种方案。系统应记录每个标识采用的 scheme，并调用对应验证器。

### 6.2 compressed JSON

适合线上读取和日常交换：

```json
{
  "digitalProductPassportId": "https://passport.example.com/dpp/018f73c0-9320-7f91-bdc9-2ea86bc98a66",
  "uniqueProductIdentifier": "https://id.example.com/01/09506000134352/21/SN-A10001",
  "granularity": "item",
  "dppSchemaVersion": "eu-battery-passport:1.0",
  "dppStatus": "active",
  "lastUpdated": "2026-08-27T06:30:00Z",
  "economicOperatorId": "iso6523-actorid-upis::0199:example-operator-001",
  "facilityId": "iso6523-actorid-upis::0199:example-facility-001",
  "contentSpecificationIds": ["eu:battery-passport:2027"],
  "battery": {
    "category": "EV",
    "ratedCapacityKWh": 82.0,
    "chemistry": "NMC811"
  }
}
```

`battery` 下的字段只是演示，不是 EN 18222/18223 规定的电池业务字段。

### 6.3 full JSON DataElement

| 对象类型 | 必填字段 | 特有规则 |
|---|---|---|
| DataElementCollection | elementId、objectType、elements | elements 至少一个，可混合多种 DataElement |
| SingleValuedDataElement | elementId、objectType、value | value 为一个 JSON 原生值；valueDataType 可选 |
| MultiValuedDataElement | elementId、objectType、value | value 是至少一个、同类型且有序的 DataElement 数组 |
| RelatedResource | elementId、objectType、contentType、url | language、resourceTitle 可选；contentType 使用 IANA MIME 类型 |
| MultiLanguageDataElement | elementId、objectType、value | value 是至少一个 `{language, value}` 对象 |

所有展开元素都可包含：

- `dictionaryReference`：指向外部数据字典中唯一语义定义的 URI；
- `elementId`：在所在容器中必须唯一，对应的绝对路径在整个 DPP 中必须唯一。

单值示例：

```json
{
  "elementId": "remainingCapacityKWh",
  "objectType": "SingleValuedDataElement",
  "dictionaryReference": "https://dictionary.example.eu/battery/remainingCapacityKWh",
  "valueDataType": "xsd:decimal",
  "value": 71.4
}
```

多语言示例：

```json
{
  "elementId": "productDescription",
  "objectType": "MultiLanguageDataElement",
  "dictionaryReference": "https://dictionary.example.eu/product/description",
  "value": [
    {"language": "en-GB", "value": "Traction battery pack"},
    {"language": "zh-CN", "value": "动力电池包"}
  ]
}
```

## 7. 接口详细定义

### 7.1 ReadDPPById

```http
GET /v1/dpps/{dppId}?representation=compressed
```

入参：

| 位置 | 名称 | 类型 | 必填 | 说明 |
|---|---|---|---:|---|
| path | dppId | string | 是 | DPP ID；整个值百分号编码 |
| query | representation | enum | 否 | compressed/full，默认 compressed |
| header | Authorization | Bearer | 否 | 访问受控字段时需要 |
| header | X-Correlation-ID | string | 否 | 调用链标识 |

成功出参：

```json
{
  "statusCode": "Success",
  "payload": {
    "digitalProductPassportId": "https://passport.example.com/dpp/018f73c0-9320-7f91-bdc9-2ea86bc98a66",
    "uniqueProductIdentifier": "https://id.example.com/01/09506000134352/21/SN-A10001",
    "granularity": "item",
    "dppSchemaVersion": "eu-battery-passport:1.0",
    "dppStatus": "active",
    "lastUpdated": "2026-08-27T06:30:00Z",
    "economicOperatorId": "iso6523-actorid-upis::0199:example-operator-001"
  }
}
```

响应头返回 `ETag`、`Last-Modified` 和 `X-Correlation-ID`。

### 7.2 ReadDPPByProductId

```http
GET /v1/dppsByProductId/{productId}?representation=compressed
```

入参与 ReadDPPById 相同，但 path 参数为 EN 18219 产品唯一标识。服务端只返回当前处于活动状态的最新版本；不存在活动版本返回 404。

当一个产品标识错误地关联多个活动 DPP 时，不得随机返回，应记录数据完整性事件并返回 409。

### 7.3 ReadDPPVersionByIdAndDate

```http
GET /v1/dppsByIdAndDate/{dppId}?date=2026-08-27T06%3A30%3A00Z&representation=full
```

| 位置 | 名称 | 类型 | 必填 | 校验 |
|---|---|---|---:|---|
| path | dppId | string | 是 | 百分号编码 |
| query | date | UTC date-time | 是 | ISO 8601-1，必须以 Z 结尾 |
| query | representation | enum | 否 | 默认 compressed |

版本选择的实施规则：

```text
validFrom <= date AND (validTo IS NULL OR date < validTo)
```

应命中且只命中一个版本；没有命中返回 404，多版本重叠返回 409 并触发完整性告警。

EN 18222 第 4.1 条的查询方法列表出现过 `ReadDPPVersionByProductIdAndDate`，但第 4.4 条、表 3 和表 16 均定义为按 DPP ID 查询。本配置文件采用表 3/表 16 的 `ReadDPPVersionByIdAndDate`。

### 7.4 ReadDPPIdsByProductIds

```http
POST /v1/dppsByProductIds?limit=100&cursor=eyJvZmZzZXQiOjEwMH0
Content-Type: application/json

[
  "https://id.example.com/01/09506000134352/21/SN-A10001",
  "did:web:example.com:products:SN-A10002"
]
```

请求体必须是数组：

- 至少 1 个产品 ID；
- 数组元素不得重复；
- 每个值按对应 EN 18219 scheme 校验；
- `cursor` 如果出现，不得为空；
- `limit` 默认 100，本配置文件上限 1000。

响应：

```json
{
  "statusCode": "Success",
  "payload": [
    "https://passport.example.com/dpp/018f73c0-9320-7f91-bdc9-2ea86bc98a66"
  ],
  "pagination": {
    "limit": 100,
    "nextCursor": "eyJvZmZzZXQiOjIwMH0",
    "hasMore": true
  }
}
```

游标必须是不透明、带完整性保护且有有限有效期的服务端令牌。客户端不得解析或自行构造游标。

### 7.5 CreateDPP

```http
POST /v1/dpps?representation=compressed
Authorization: Bearer <token>
Content-Type: application/json
```

请求体为完整 DPP，至少包含 7 个强制头部字段；业务字段还必须通过 `dppSchemaVersion` 对应的产品组 Schema 校验。

处理顺序：

1. 认证调用方并验证“创建 DPP”权限；
2. 验证 DPP ID、产品 ID、运营商 ID 和设施 ID；
3. 验证产品组 Schema 和数据字典引用；
4. 检查 DPP ID 唯一性及产品 ID/粒度约束；
5. 在同一事务中保存初始版本和审计事件；
6. 返回 201、Location 和 ETag。

响应：

```json
{
  "statusCode": "SuccessCreated",
  "digitalProductPassportId": "https://passport.example.com/dpp/018f73c0-9320-7f91-bdc9-2ea86bc98a66"
}
```

重复资源返回 409。不要用“查询后再插入”的非原子逻辑，应在数据库层建立唯一约束。

### 7.6 UpdateDPPById

```http
PATCH /v1/dpps/{dppId}?representation=compressed
Authorization: Bearer <token>
If-Match: "dpp-v17"
Content-Type: application/merge-patch+json

{
  "dppStatus": "active",
  "battery": {
    "remainingCapacityKWh": 71.4
  }
}
```

规则：

- 请求体只包含需要更新或新增的字段；
- `digitalProductPassportId`、`uniqueProductIdentifier` 在本配置文件中不可修改；
- JSON `null` 按 RFC 7396 删除可选字段；
- 合并后对完整 DPP 再做 Schema、访问权和语义校验；
- 任何字段失败都必须回滚整个请求；
- 成功时归档旧版本、创建新版本和审计事件；
- 返回更新后的完整 DPP，而不是只返回补丁。

### 7.7 DeleteDPPById

```http
DELETE /v1/dpps/{dppId}
Authorization: Bearer <token>
If-Match: "dpp-v18"
```

成功返回 HTTP 204，无正文。

“删除”应实现为生命周期结束和归档：

- 当前活动记录转为 archived/invalid 或部署定义的删除状态；
- 公开解析按法规决定返回 404、410 或归档信息；
- 版本、审计和法定留存数据不得直接物理删除；
- 备份服务和 Registry 的相应状态变更必须可追踪。

标准状态码表没有定义 410，因此严格 EN 18222 API 可统一返回 404；若采用 410，应记录为扩展。

### 7.8 RegisterProductDPP

```http
POST /v1/registerDPP
Authorization: Bearer <token>
Content-Type: application/json

{
  "uniqueProductIdentifier": "https://id.example.com/01/09506000134352/21/SN-A10001",
  "digitalProductPassportId": "https://passport.example.com/dpp/018f73c0-9320-7f91-bdc9-2ea86bc98a66",
  "uniqueEconomicOperatorIdentifier": "iso6523-actorid-upis::0199:example-operator-001",
  "backupEconomicOperatorIdentifier": "iso6523-actorid-upis::0199:example-backup-001",
  "dppApiEndpoint": "https://passport.example.com/v1"
}
```

DppRegistryEntry 字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| uniqueProductIdentifier | string | 是 | EN 18219 产品唯一标识 |
| digitalProductPassportId | string | 是 | DPP ID |
| uniqueEconomicOperatorIdentifier | string | 是 | 负责运营商的唯一标识 |
| backupEconomicOperatorIdentifier | string | 是 | 备份运营商的唯一标识；字段名是本配置文件对标准歧义的规范化 |
| dppApiEndpoint | HTTPS URI | 是 | DPP 生命周期 API 根地址 |

同步成功：

```json
{
  "statusCode": "SuccessCreated",
  "registrationId": "urn:eu:dpp:registration:01J6D4M4G3KQ8M7B7X1J15HYTZ"
}
```

异步接收：

```json
{
  "statusCode": "SuccessAccepted",
  "correlationId": "01J6D4KAP9E2WQ0M4X8GZ6VB30",
  "estimatedCompletionAt": "2026-08-27T06:31:00Z"
}
```

EN 18222 只定义了这个注册入口。Commission Implementing Regulation (EU) 2026/1778 还要求 Registry 支持语义一致性、粒度、商品编码、备份链接、版本、注册证明和数据管理。下列字段/能力不能仅凭 EN 18222 自行猜测：

- modelIdentifier、batchIdentifier、商品编码和产品组；
- 服务商及备份链接引用；
- 修改、删除、转移注册记录；
- 获取带合格电子印章及时间戳的注册证明；
- 批量注册文件和异步任务状态查询。

生产对接中央 Registry 前，应取得它发布的 authoritative Schema/OpenAPI 并单独建立适配层，不要把中央 Registry 的变化扩散到内部 DPP 生命周期 API。

### 7.9 ReadDataElement

原始 JSONPath：

```text
$['battery']['remainingCapacityKWh']
```

编码后的调用示意：

```http
GET /v1/dpps/urn%3Auuid%3A018f73c0-9320-7f91-bdc9-2ea86bc98a66/elements/%24%5B%27battery%27%5D%5B%27remainingCapacityKWh%27%5D?representation=compressed
```

为保证“一个路径对应一个元素”，只接受 RFC 9535 的绝对规范化、单一位置路径：

- 允许成员名和数组下标；
- 拒绝通配符、切片、过滤器和递归下降；
- 路径必须命中一个且仅一个节点；
- 路径不存在返回 404；多命中或语法错误返回 400。

compressed 响应：

```json
{
  "statusCode": "Success",
  "payload": 71.4
}
```

full 响应：

```json
{
  "statusCode": "Success",
  "payload": {
    "elementId": "remainingCapacityKWh",
    "objectType": "SingleValuedDataElement",
    "dictionaryReference": "https://dictionary.example.eu/battery/remainingCapacityKWh",
    "valueDataType": "xsd:decimal",
    "value": 71.4
  }
}
```

### 7.10 UpdateDataElement

更新 compressed 值：

```http
PATCH /v1/dpps/{encodedDppId}/elements/{encodedElementIdPath}?representation=compressed
Authorization: Bearer <token>
If-Match: "dpp-v18"
Content-Type: application/merge-patch+json

69.8
```

移除元素：

```http
PATCH /v1/dpps/{encodedDppId}/elements/{encodedElementIdPath}
Authorization: Bearer <token>
Content-Type: application/merge-patch+json

null
```

标准说明该方法可以增补或移除元素，但没有规定线上删除载荷。本配置文件采用 RFC 7396 的 `null` 表示移除，并返回 204。这是显式实施约定。

## 8. 状态码与错误响应

| statusCode | HTTP | 使用场景 |
|---|---:|---|
| Success | 200 | 查询或更新成功 |
| SuccessCreated | 201 | DPP 或注册记录创建成功 |
| SuccessAccepted | 202 | 请求已接收，异步处理 |
| SuccessNoContent | 204 | 删除成功，无正文 |
| ClientErrorBadRequest | 400 | 格式、必填、Schema、时间或 JSONPath 错误 |
| ClientNotAuthorized | 401 | 认证凭证缺失或无效 |
| ClientForbidden | 403 | 已认证但无权限 |
| ClientErrorResourceNotFound | 404 | 资源、版本或元素不存在 |
| ClientMethodNotAllowed | 405 | HTTP 方法不允许 |
| ClientResourceConflict | 409 | 资源已存在、版本或并发冲突 |
| ServerInternalError | 500 | 未处理的内部错误 |
| ServerNotImplemented | 501 | 可选能力或表示尚未实现 |
| ServerErrorBadGateway | 502 | 上游服务或备份服务响应无效 |

错误外层结构是本配置文件对 EN 18222 `statusCode + Result` 的固定化：

```json
{
  "statusCode": "ClientErrorBadRequest",
  "result": {
    "message": [
      {
        "messageType": "Error",
        "text": "cursor must not be empty",
        "code": "DPP-API-400-001",
        "correlationId": "01J6D4KAP9E2WQ0M4X8GZ6VB30",
        "timestamp": "2026-08-27T06:30:00Z"
      }
    ]
  }
}
```

`message` 保留标准中的单数字段名，但值是数组，因为其基数是 0..*。

错误文本不得包含数据库 SQL、堆栈、Token、密钥、内部主机名或受控 DPP 数据。

## 9. 事务、版本与归档模型

建议至少有以下持久化对象：

```text
dpp_current
  dpp_id PK
  product_id
  current_version
  status
  schema_version
  payload
  etag
  updated_at

dpp_version
  dpp_id + version PK
  valid_from
  valid_to
  payload
  payload_hash
  actor_id
  correlation_id
  change_set

dpp_audit_event
  event_id PK
  dpp_id
  action
  actor_id
  occurred_at
  correlation_id
  outcome
  changed_paths
```

更新事务：

```text
鉴权 → 读取并锁定当前版本 → 检查 If-Match → 应用补丁 → 完整 Schema 校验
→ 字段级授权校验 → 关闭旧版本有效期 → 写入新版本 → 更新 current → 写审计 → 提交
```

任何一步失败都回滚，不允许出现“部分字段成功”。历史版本和变更事件的留存按 EN 18221 及适用法规执行。

## 10. 最低验收测试

1. 省略 representation 时返回 compressed。
2. representation=full 时每个展开元素含正确 objectType。
3. 包含 `/`、`?`、`#` 的标识经百分号编码后能正确路由。
4. date 不是 UTC Z 格式时返回 400。
5. 批量产品 ID 数组为空或重复时返回 400。
6. cursor 为空、被篡改或过期时返回 400。
7. 未授权读取只返回允许的数据元素；不得先返回后在客户端隐藏。
8. 无权更新任一字段时，整个 PATCH 回滚。
9. 补丁后违反产品组 Schema 时，整个 PATCH 回滚。
10. 并发 If-Match 冲突返回 409。
11. 创建重复 DPP ID 返回 409。
12. 历史时点查询准确命中版本边界，且不存在版本重叠。
13. elementIdPath 多命中、通配或语法错误时返回 400。
14. 删除后历史版本和审计记录仍可按授权读取。
15. 401 与 403 严格区分。
16. 每个响应均带 correlation ID，且跨主/备服务保持一致。
17. 任一失败响应不泄漏内部堆栈或敏感字段。
18. TLS 1.0/1.1、SSL 和 HTTP/1.x 连接被拒绝。

## 11. 开发落地顺序

建议按以下顺序实现：

1. 先固定产品 ID、DPP ID、运营商 ID 的 scheme 和验证器；
2. 建立 DPP 头部、版本表、审计表及 compressed JSON 存储；
3. 实现三个必须读取接口；
4. 实现创建、原子 PATCH、软删除和历史版本读取；
5. 实现字段级授权和单元素 API；
6. 最后接中央 Registry 适配层、备份服务和 full 表示转换；
7. 用 `openapi.yaml` 生成客户端/服务端骨架并执行上述验收测试。

## 12. 仍需项目方确定的配置项

这些值不应写死在通用标准层：

- 实际 API 域名和证书体系；
- 产品组及其 authoritative Schema/数据字典；
- 选用的 EN 18219 标识方案；
- token issuer、audience、scope 与角色；
- 字段级访问矩阵；
- 每页 limit 上限和游标有效期；
- ETag 是否强制；
- DPP 状态机和软删除后的公开响应；
- 备份服务复制时延和故障切换指标；
- 欧盟中央 Registry 的正式 API 契约与测试环境参数。
