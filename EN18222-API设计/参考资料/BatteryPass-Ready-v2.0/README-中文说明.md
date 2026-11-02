# BatteryPass-Ready v2.0 语义模型与字段包

下载日期：2026-09-02  
官方字段版本：Data Attribute Longlist v2.0，August 2026  
官方模型版本：BatteryPass-Ready Data Model 2.0（Work in progress）  
模型快照提交：`9ff729f6a917f4f4aa7830891902d3cb2dba6d0d`，2026-08-30

## 官方来源

- 资源页：<https://thebatterypass.eu/battery-pass-ready/resources/>
- v2.0 发布说明：<https://thebatterypass.eu/news/now-published-data-attribute-longlist-v2-0-and-data-model-v2-0/>
- 官方模型仓库：<https://git.gefeg.com/bp/BatteryPassDataModel>

## 本目录内容

- `2026_BatteryPass-Ready_DataAttributeLongList_v2.0.xlsx`
  - 官方原始字段清单。
  - 包含 `Data attribute longlist_v2.0`、`Technical references` 和 `Change log_v2.0` 等工作表。
  - 共 92 个实施数据属性，其中 71 个在 `Mandatory from Feb 2027` 列标记为 `yes`。
- `BatteryPass-Ready-v2.0-field-index.csv`
  - 从官方 Excel 主字段表机械导出的 UTF-8 CSV，便于程序检索和导入。
  - 保留英文原始字段、类别适用性、欧委会指南映射、法规引用、单位、格式、访问权限、动静态特征和粒度等列。
- `BatteryPassDataModel/BatteryPass-Ready/JSONSchemaFiles/v2.0/`
  - 10 个 JSON Schema，采用 JSON Schema Draft 2020-12。
  - 五类电池各有“完整要求版”和“2027年2月要求版”。
- `BatteryPassDataModel/BatteryPass-Ready/XSDFiles/v2.0/`
  - 同类模型的 XSD 表示。
- `BatteryPassDataModel/BatteryPass-Ready/StructureDiagrams/v2.0/`
  - 10 个可在浏览器中打开的交互式 HTML 结构图。
- `BatteryPassDataModel/BatteryPass-Ready/AdditionalDocuments/v2.0/BP-Ready_data_model_codelists_v2.0.pdf`
  - v2.0 代码表说明。
- `BatteryPassDataModel/BatteryPass-Ready/AdditionalDocuments/Changelog/changelog.md`
  - v1.3 到 v2.0 的破坏性变更说明。

## JSON模型结构

v2.0 根结构固定为：

```json
{
  "dppHeader": {},
  "batteryPassportData": {}
}
```

`dppHeader` 的主要属性包括：

- `granularity`
- `economicOperatorId`
- `contentSpecificationIds`
- `facilityId`
- `lastUpdated`
- `dppStatus`
- `dppSchemaVersion`
- `uniqueProductIdentifier`
- `digitalProductPassportId`

`batteryPassportData` 下分为七个业务组：

- `symbolsLabelsAndDocumentationOfConformity`
- `supplyChainDueDiligence`
- `performanceAndDurability`
- `identifiersAndProductData`
- `circularityAndResourceEfficiency`
- `batteryMaterialsAndComposition`
- `batteryCarbonFootprint`

## 如何选择Schema

| 电池类别 | 2027年2月实施版 | 完整要求版 |
|---|---|---|
| EV | `EV_Feb27_2.0.json` | `EV_2.0.json` |
| LMT | `LMT_Feb_27_2.0.json` | `LMT_2.0.json` |
| 无BMS工业电池 | `Industrial_Without_BMS_Feb27_2.0.json` | `Industrial_Without_BMS_2.0.json` |
| 其他大于2 kWh工业电池 | `Other_Industrial_Above_2kWh_Feb27_2.0.json` | `Other_Industrial_Above_2kWh_2.0.json` |
| 固定式大于2 kWh工业电池 | `Stationary_Industrial_Above_2kWh_Feb27_2.0.json` | `Stationary_Industrial_Above_2kWh_2.0.json` |

当前面向 2027-02-18 首次合规上线时，优先使用 `*Feb27*` Schema；完整要求版包含后续时期或当前尚未生效的要求。

## 重要质量与合规提示

1. 官方 README 明确标注模型为 `Work in progress`，资源仍可能调整。
2. 条件适用字段目前在 JSON Schema 中暂时表现为 optional，不能仅根据 `required` 数组判断法律义务；必须同时核对 Excel 的电池类别列和条件说明。
3. 本次检查确认 10 个 JSON Schema 均为合法 JSON。
4. 官方 XSD 中使用了 `DPP Header`、`urn:/Data types` 等不规范 namespace URI，严格 XML 工具会给出 namespace 警告；现阶段建议以 JSON Schema 作为开发和校验基线。
5. v2.0 是破坏性版本：根结构变为 `dppHeader` 与 `batteryPassportData`，大量字段统一为 lowerCamelCase，并新增 `RelatedResource`、危害物质结构和代码表。
6. 部分自定义访问权限关键字同时存在 `x-Access-Right` 与 `x-Access-Rights` 拼写，解析时需要兼容。
7. BatteryPass-Ready 是产业实施模型，不等于欧盟官方最终语义目录；字段的法规必填性仍应以 Regulation (EU) 2023/1542、实施/授权法案和欧委会最新版指南为准。

