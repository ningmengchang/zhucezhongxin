# EN 18222 + BatteryPass-Ready 2.0 EV February 2027 实施说明

## 1. 开发时使用哪个文件

| 文件 | 用途 |
|---|---|
| `openapi-battery-pass-ready-v2.0-ev-feb27.yaml` | 本次实施产物；导入 Apifox，用于联调、Mock 和代码生成 |
| `openapi.yaml` | 初始 EN 18222/EN 18223 通用实施版；原样保留，不作为 BatteryPass 业务字段版 |
| `tools/generate_battery_pass_openapi.py` | 从初始 OpenAPI、BatteryPass JSON Schema 和字段 Excel 重新生成新版 |
| `tools/validate_battery_pass_openapi.py` | 执行可重复的结构、Schema、示例和接口策略验证 |

Apifox 应导入 `openapi-battery-pass-ready-v2.0-ev-feb27.yaml`，不要把 `EV_Feb27_2.0.json` 当作 OpenAPI 导入。后者是产品数据 JSON Schema，其 `$defs` 结构在 Apifox 中可能无法按 API 模型展开。

## 2. 本版的固定范围

- API 方法、路径、状态码和生命周期语义：沿用 EN 18222 实施配置。
- 电池类型：仅 EV battery。
- BatteryPass-Ready 数据模型：2.0。
- 要求时点：仅 February 2027，源 Schema 为 `EV_Feb27_2.0.json`。
- 模型源 commit：`9ff729f6a917f4f4aa7830891902d3cb2dba6d0d`。
- 属性级字段：68 个，其中 JSON Schema `required` 字段 56 个。
- 上游 `$defs`：42 个，已转为 OpenAPI `components.schemas`。
- Schema 枚举节点：16 个，均保留 `enum` 并增加 `x-apifox-enum`。

68 个属性按结构分布如下：

| 区域 | 字段数 |
|---|---:|
| EN 18223 根级 DPP 头字段（上游源名为 `dppHeader`） | 9 |
| `SymbolsLabelsAndDocumentationOfConformity` | 6 |
| `PerformanceAndDurability` | 28 |
| `IdentifiersAndProductData` | 13 |
| `CircularityAndResourceEfficiency` | 8 |
| `BatteryMaterialsAndComposition` | 4 |

`required` 只表示 JSON Schema 层的强制存在。带条件的法规适用性仍需结合 `2026_BatteryPass-Ready_DataAttributeLongList_v2.0.xlsx` 判定。每个属性在 OpenAPI 中增加了 `x-battery-pass-ready`，包含 Longlist 编号、分类、单位、数据格式、访问权、更新要求和法规引用。

## 3. 读写表示策略

| 场景 | 请求/响应表示 |
|---|---|
| CreateDPP 请求 | 仅 compressed |
| UpdateDPPById 请求 | 仅 compressed RFC 7396 merge patch |
| UpdateDataElement 请求 | 仅目标字段的 compressed 值 |
| ReadDPPById / ReadDPPByProductId / 历史读取 | `compressed` 或 `full`，默认 `compressed` |
| ReadDataElement | `compressed` 或 `full`，默认 `compressed` |

写接口不再接收 `representation` 参数。公共请求和响应均按 EN 18223 把 DPP 头字段直接放在根对象，不接受 `dppHeader` 包装层。整本 DPP 更新后，服务端必须对合并结果再执行完整 `BatteryPassReadyEvFeb27` 校验。`digitalProductPassportId` 和 `uniqueProductIdentifier` 在 PATCH Schema 中禁止修改。

## 4. compressed 结构

公共 API 使用“EN 18223 根级头字段 + BatteryPass-Ready `batteryPassportData`”。BatteryPass-Ready 2.0 源 Schema 中的 `dppHeader` 只作为服务端内部映射模型，不直接暴露给 ReadDPPById 等 `/v1` 接口：

```json
{
  "digitalProductPassportId": "https://passport.example.com/dpp/ev-battery-000001",
  "uniqueProductIdentifier": "https://id.example.com/batteries/ev-battery-000001",
  "granularity": "item",
  "dppSchemaVersion": "BatteryPass-Ready:2.0:EV:Feb2027",
  "dppStatus": "active",
  "lastUpdated": "2027-02-18T08:00:00Z",
  "economicOperatorId": "https://id.example.com/operators/economic-operator-001",
  "contentSpecificationIds": [
    "BatteryPass-Ready:2.0:EV:Feb2027"
  ],
  "batteryPassportData": {
    "SymbolsLabelsAndDocumentationOfConformity": {},
    "PerformanceAndDurability": {},
    "IdentifiersAndProductData": {},
    "CircularityAndResourceEfficiency": {},
    "BatteryMaterialsAndComposition": {}
  }
}
```

OpenAPI 内的 `components.examples.CompressedBatteryDpp` 是包含全部 68 个属性的复杂示例。验证脚本会先把公共根级头还原成 BatteryPass-Ready 的原始 `dppHeader`，再对完整文档执行 `EV_Feb27_2.0.json` 校验。

公共 DTO 与上游原始 DTO 的关键映射为：

| 公共 EN 18223 字段 | BatteryPass-Ready 原始路径 |
|---|---|
| `granularity` | `dppHeader.granularity.dppGranularityValue` |
| `dppStatus` | `dppHeader.dppStatus.dppStatusValue` |
| `contentSpecificationIds[]` | `dppHeader.contentSpecificationIds` |
| 其他头字段 | `dppHeader.<sameName>` |
| `batteryPassportData` | `batteryPassportData` |

OpenAPI 中的 `BatteryPassReadyRawEvFeb27` 完整保留上游 `dppHeader + batteryPassportData` 结构，并标记为 internal；它不被任何公共接口引用。

### Remaining capacity 的正确字段

官方模型路径为：

```text
$.batteryPassportData.PerformanceAndDurability.remainingCapacity
```

它是“数值 + 单位”对象，不是 `remainingCapacityKWh`：

```json
{
  "amperehourMiliamperehourValue": 180,
  "ampereHourMiliamperehourUnit": "Ah"
}
```

`remainingCapacity` 表示剩余容量，使用 Ah/mAh；以 kWh 表示的是 `remainingUsableBatteryEnergy`。

## 5. full 结构

full 是从同一份 compressed 规范数据派生的 EN 18223 DataElement 展开视图：

```json
{
  "digitalProductPassportId": "https://passport.example.com/dpp/ev-battery-000001",
  "uniqueProductIdentifier": "https://id.example.com/batteries/ev-battery-000001",
  "granularity": "item",
  "dppSchemaVersion": "BatteryPass-Ready:2.0:EV:Feb2027",
  "dppStatus": "active",
  "lastUpdated": "2027-02-18T08:00:00Z",
  "economicOperatorId": "https://id.example.com/operators/economic-operator-001",
  "elements": [
    {
      "elementId": "PerformanceAndDurability",
      "objectType": "DataElementCollection",
      "dictionaryReference": "https://example.com/dictionaries/battery-pass-ready/2.0/ev-feb27/batteryPassportData/PerformanceAndDurability",
      "elements": []
    }
  ]
}
```

各 DPP 读取接口 `200` 响应中的 `full` 为完整复杂示例。当前 `dictionaryReference` 使用 `example.com` 实施占位 URI，只用于联调和展示映射关系，不是欧委会正式数据字典。正式字典 URI 发布后，应替换转换层的映射，无需改变 compressed 业务结构。

## 6. Apifox 导入和查看

1. 在 Apifox 中选择“项目设置 → 导入数据 → OpenAPI/Swagger”。
2. 选择 `openapi-battery-pass-ready-v2.0-ev-feb27.yaml`。
3. 在数据模型中打开 `CompressedDigitalProductPassport` 或 `BatteryPassReadyEvFeb27`；应直接看到 `digitalProductPassportId`、`granularity`、`dppStatus` 等根级头字段。
4. 继续展开 `batteryPassportData` 引用的 `BpBatterypassportmaster`；五个电池分组均是可展开的 `components.schemas` 引用。`BpDppheader` 和 `BatteryPassReadyRawEvFeb27` 仅用于查看上游原始模型。
5. 查看 CreateDPP 的 `evFeb27` 请求示例，或查看读接口响应中的 `compressed` / `full` 两个示例。

枚举节点同时包含标准 `enum` 和 Apifox 扩展 `x-apifox-enum`。如果 Apifox 已经导入过同名初始模型，建议导入到新分支或先删除该次失败的导入记录，避免同名组件被旧缓存合并。

## 7. 生成与验证

在 `EN18222-API设计` 的上级目录执行：

```bash
python3 EN18222-API设计/tools/generate_battery_pass_openapi.py
python3 EN18222-API设计/tools/validate_battery_pass_openapi.py
```

验证脚本会检查：

- 初始 `openapi.yaml` SHA-256 未变；
- OpenAPI 3.1 YAML 可解析，所有本地 `$ref` 可解析；
- 42 个 `$defs` 完整转换；
- 68 个 EV February-2027 属性、56 个 required 属性和 16 个枚举节点；
- compressed 公共示例不包含 `dppHeader`，其头字段均为 EN 18223 根级标量/数组；
- 公共示例可无损还原为原始 BatteryPass 包装，并通过 `EV_Feb27_2.0.json`；
- full 示例通过 OpenAPI DataElement Schema；
- 读写表示策略和两个不可变标识字段。

## 8. 已知边界

- BatteryPass-Ready 2.0 源仓库自身标记为 work in progress；它是实施参考模型，不是欧委会中央 Registry 的官方 OpenAPI。
- 欧委会正式数据字典 URI、Registry 认证、批量注册和注册证明契约仍应通过适配层接入。
- EN 18223 把 `contentSpecificationIds` 定义为 0..*，因此公共 API 使用字符串数组；上游 BatteryPass-Ready 2.0 Schema 当前仅使用一个字符串。对多值请求的内部存储不应被上游该限制截断。
- 上游 `RelatedResource.url` 正则含有双重转义的 `\\s`；本版保留源 Schema 约束以便追溯。若上游后续修正，应从新源 Schema 重新生成，不要在业务代码中永久写死当前正则。
