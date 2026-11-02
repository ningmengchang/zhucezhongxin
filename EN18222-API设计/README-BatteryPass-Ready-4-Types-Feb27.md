# BatteryPass-Ready 2.0 四种电池 OpenAPI 使用说明

## 交付文件

- `openapi-battery-pass-ready-v2.0-4-battery-types-feb27.yaml`：可导入 Apifox 的 OpenAPI 3.1 主文件。
- `tools/generate_battery_pass_openapi_four_types.py`：根据四份 BatteryPass-Ready 2.0 JSON Schema 和 Data Attribute Longlist 重新生成 YAML。
- `tools/validate_battery_pass_openapi_four_types.py`：验证原始文件未变、四种类型、字段数、枚举、引用、示例及读写规则。

原有 `openapi.yaml` 和 `openapi-battery-pass-ready-v2.0-ev-feb27.yaml` 均未修改。

## 根结构

新文件不使用公共扁平结构。`compressed` 请求和响应的 DPP 固定为 BatteryPass-Ready 2.0 原生包装：

```json
{
  "dppHeader": {
    "granularity": {
      "dppGranularityValue": "item"
    },
    "economicOperatorId": "https://id.example.com/operators/economic-operator-001",
    "lastUpdated": "2027-02-18T08:00:00Z",
    "dppStatus": {
      "dppStatusValue": "active"
    },
    "dppSchemaVersion": "BatteryPass-Ready:2.0:ev:Feb2027",
    "uniqueProductIdentifier": "https://id.example.com/batteries/ev-battery-000001",
    "digitalProductPassportId": "https://passport.example.com/dpp/ev-battery-000001"
  },
  "batteryPassportData": {
    "SymbolsLabelsAndDocumentationOfConformity": {},
    "PerformanceAndDurability": {},
    "IdentifiersAndProductData": {},
    "CircularityAndResourceEfficiency": {},
    "BatteryMaterialsAndComposition": {}
  }
}
```

根节点只允许 `dppHeader` 和 `batteryPassportData`；把头字段或五个业务分组平铺到根节点会校验失败。

`full` 仍保留同样的两层包装：`dppHeader` 不变，`batteryPassportData` 中的五个分组展开成 EN 18223 `DataElement` 对象。`full` 只用于读取，不作为创建或更新请求体。

## 四种电池类型

| 类型 | `batteryCategoryValue` | 2027-02 字段数 | JSON Schema `required` 数 |
|---|---|---:|---:|
| EV | `electric vehicle battery` | 68 | 56 |
| LMT | `LMT battery` | 67 | 56 |
| Other Industrial >2 kWh | `industrial/non-stationary battery` | 64 | 47 |
| Stationary Industrial >2 kWh | `industrial/stationary battery` | 67 | 56 |

类型判别路径为：

```text
$.batteryPassportData.IdentifiersAndProductData.batteryCategory.batteryCategoryValue
```

`Industrial_Without_BMS_Feb27_2.0.json` 是单独的特殊 profile，没有并入本次“四种电池”接口。

## 接口与表示规则

| operationId | 方法与路径 | 主要输入/输出规则 |
|---|---|---|
| `readDppById` | `GET /v1/dpps/{dppId}` | `representation=compressed|full` |
| `updateDppById` | `PATCH /v1/dpps/{dppId}` | 仅接受原生包装的 RFC 7396 merge patch |
| `deleteDppById` | `DELETE /v1/dpps/{dppId}` | 删除 DPP |
| `readDppByProductId` | `GET /v1/dppsByProductId/{productId}` | `representation=compressed|full` |
| `readDppVersionByIdAndDate` | `GET /v1/dppsByIdAndDate/{dppId}` | 按时间读取，支持两种表示 |
| `readDppIdsByProductIds` | `POST /v1/dppsByProductIds` | 根据产品 ID 批量查 DPP ID |
| `createDpp` | `POST /v1/dpps` | 仅接受四种类型的 `compressed` 包装对象 |
| `registerProductDpp` | `POST /v1/registerDPP` | 注册产品与 DPP 的关联 |
| `readDataElement` | `GET /v1/dpps/{dppId}/elements/{elementIdPath}` | 支持 `compressed|full` |
| `updateDataElement` | `PATCH /v1/dpps/{dppId}/elements/{elementIdPath}` | 只更新目标 compressed 值 |

写接口不接受 `representation`。`digitalProductPassportId`、`uniqueProductIdentifier` 和 `batteryCategory` 在 DPP 创建后不允许通过 merge patch 修改。

包装结构的更新示例：

```json
{
  "batteryPassportData": {
    "PerformanceAndDurability": {
      "remainingCapacity": {
        "amperehourMiliamperehourValue": 175,
        "ampereHourMiliamperehourUnit": "Ah"
      }
    }
  }
}
```

单字段路径示例：

```text
$.batteryPassportData.PerformanceAndDurability.remainingCapacity
```

## Apifox 导入

1. 在 Apifox 中选择“导入项目/导入数据”。
2. 选择 OpenAPI，导入 `openapi-battery-pass-ready-v2.0-4-battery-types-feb27.yaml`。
3. 创建请求查看 `CompressedDigitalProductPassport`；该模型通过 `oneOf` 引用四种类型。
4. 如果 Apifox 在 `oneOf` 联合层不展开全部字段，直接打开下列具体模型：
   - `BatteryPassReadyEvFeb27`
   - `BatteryPassReadyLmtFeb27`
   - `BatteryPassReadyOtherIndustrialAbove2kWhFeb27`
   - `BatteryPassReadyStationaryIndustrialAbove2kWhFeb27`
5. 每个创建接口已配置四份完整示例；JSON Schema 的枚举同时写入标准 `enum` 和 Apifox 可识别的 `x-apifox-enum`。

## 源文件和注意事项

本接口使用下列 BatteryPass-Ready 2.0 February-2027 JSON Schema：

- `EV_Feb27_2.0.json`
- `LMT_Feb_27_2.0.json`
- `Other_Industrial_Above_2kWh_Feb27_2.0.json`
- `Stationary_Industrial_Above_2kWh_Feb27_2.0.json`

字段说明、适用性、访问权限、更新要求和法规引用来自 `2026_BatteryPass-Ready_DataAttributeLongList_v2.0.xlsx`。条件必填字段除 JSON Schema 校验外，还需结合 Longlist 的适用性规则处理。

`full` 示例的 `dictionaryReference` 使用 `example.com` 实施占位 URI，不是欧盟官方数据字典地址，上线前需替换。

## 重新生成与验证

```bash
cd /home/ningmengchang/Desktop/注册中心/EN18222-API设计
python tools/generate_battery_pass_openapi_four_types.py
python tools/validate_battery_pass_openapi_four_types.py
npx --yes @redocly/cli@1.34.5 lint openapi-battery-pass-ready-v2.0-4-battery-types-feb27.yaml --format stylish
```

验证器会再次用四份原始 JSON Schema 校验四份 compressed 示例，并校验 full 示例、本地 `$ref`、枚举、合并补丁、类型判别路径、原文件哈希和生成结果确定性。
