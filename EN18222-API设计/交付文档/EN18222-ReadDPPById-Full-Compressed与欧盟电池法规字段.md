# EN 18222 ReadDPPById 接口、Full/Compressed 表示与欧盟电池护照法规字段

版本：1.2（法规数据点中英文对照版）  
整理日期：2026-08-27  
适用对象：DPP 平台产品、架构、后端、前端和接口联调人员

## 1. 结论先行

1. `ReadDPPById` 是 HTTP GET 读取接口，请求没有 JSON Body。
2. `full` 和 `compressed` 通过查询参数 `representation` 选择，描述的是响应中 DPP 的表示方式。
3. 欧盟已经公布电池护照的 71 个法规业务数据点及法律来源。
4. 欧盟尚未完全公布可直接用于编码的最终电池语义目录，包括统一的 `elementId`、正式 `dictionaryReference` URI、所有单位、精度、格式和枚举。
5. 本文中的驼峰字段名、`.example.com` URI 和业务数值属于可开发的临时实施映射，不是欧盟官方机器字段。

## 2. 法规及资料来源

- Regulation (EU) 2023/1542，尤其是 Article 77、Annex VI 和 Annex XIII。
- European Commission，《Digital Batteries Passport – data points by category》，Version 2.0，2026-08-15。
- EN 18222:2026：DPP API 逻辑方法和接口行为。
- EN 18223:2026：DPP 数据模型以及 full/compressed 表示结构。

官方资料：

- 欧委会电池护照 71 项数据指南：<https://single-market-economy.ec.europa.eu/document/download/cd1e5e6c-4a4a-4b99-995a-49eb6916187e_en?filename=Digital+Batteries+Passport+-+data+point+by+category.pdf>
- 欧委会发布说明：<https://single-market-economy.ec.europa.eu/news/guidance-support-preparations-digital-batteries-passport-2026-08-21_en>
- Regulation (EU) 2023/1542 合并文本：<https://eur-lex.europa.eu/eli/reg/2023/1542/2025-07-31/eng/pdf>
- 欧盟 DPP Registry：<https://single-market-economy.ec.europa.eu/single-market/digital-product-passport/dpp-registry_en>

## 3. 欧盟电池护照 71 个真实法规数据点

英文名称逐项采用欧委会 Version 2.0 指南。中文列为便于研发理解的工程翻译，不代替英文法规原文。

| 编号 | Data point name (official English) | 中文对照（工程翻译） | 法律来源 | 2027-02 起适用说明 |
|---:|---|---|---|---|
| 1 | Unique identifier | 唯一标识符 | BR Article 77(3) | EV、LMT、工业电池必填 |
| 2 | Identity of who is registering and/or is responsible for the battery passport | 注册和/或负责电池护照的主体身份 | BR Article 77(3) | 全类别必填 |
| 3 | Manufacturer name, registered trade name or registered trademark | 制造商名称、注册商号或注册商标 | BR Annex VI A(1) | 全类别必填 |
| 4 | Manufacturer postal address, indicating a single contact point | 制造商邮政地址及单一联系点 | BR Annex VI A(1) | 全类别必填 |
| 5 | If available, manufacturer web and email address | 制造商网站和电子邮箱（如有） | BR Annex VI A(1) | 信息存在时填写 |
| 6 | Battery category | 电池类别 | BR Annex VI A(2) | 全类别必填 |
| 7 | Model identification and batch or serial number, or product number or another element allowing their identification | 型号及批次号、序列号、产品号或其他识别信息 | BR Annex VI A(2) | 全类别必填 |
| 8 | The place of manufacturer (geographical location of a battery manufacturing plant) | 制造地点（电池制造厂的地理位置） | BR Annex VI A(3) | 全类别必填 |
| 9 | The date of manufacturing (month and year) | 制造日期（月和年） | BR Annex VI A(4) | 全类别必填 |
| 10 | The weight | 重量 | BR Annex VI A(5) | 全类别必填 |
| 11 | The capacity | 容量 | BR Annex VI A(6) | 全类别必填 |
| 12 | The chemistry | 化学体系 | BR Annex VI A(7) | 全类别必填 |
| 13 | The hazardous substances present in the battery, other than mercury, cadmium or lead | 电池内除汞、镉、铅以外的有害物质 | BR Annex VI A(8) | 全类别必填 |
| 14 | Usable extinguishing agent | 可使用的灭火剂 | BR Annex VI A(9) | 全类别必填 |
| 15 | Critical raw materials present in the battery in a concentration of more than 0,1 % weight by weight | 电池中质量浓度超过 0.1% 的关键原材料 | BR Annex VI A(10) | 全类别必填 |
| 16 | The material composition of the battery, including its chemistry, hazardous substances present in the battery, other than mercury, cadmium or lead, and critical raw materials present in the battery | 电池材料组成，包括化学体系、有害物质和关键原材料 | BR Annex XIII 1(b) | 与前述字段重复，暂不单独填写/展示 |
| 17 | The carbon footprint declaration | 碳足迹声明 | BR Annex XIII 1(c) | 截至 2027-02 格式尚待实施法案规定 |
| 18 | The carbon footprint label | 碳足迹标签 | BR Annex XIII 1(c) | 截至 2027-02 格式尚待实施法案规定 |
| 19 | Information on responsible sourcing as indicated in the report on battery due diligence policy referred to in Article 52(3) | 电池尽职调查政策报告中的负责任采购信息 | BR Annex XIII 1(d) | 预计随 Article 48 从 2027-08 起适用 |
| 20 | Percentage share of cobalt that is present in active materials and that has been recovered from battery manufacturing waste or post-consumer waste | 活性材料中来自制造废料或消费后废弃物回收的钴占比 | BR Annex XIII 1(e) | 依 Article 8 及相关授权法案实施 |
| 21 | Percentage share of lithium that is present in active materials and that has been recovered from battery manufacturing waste or post-consumer waste | 活性材料中来自制造废料或消费后废弃物回收的锂占比 | BR Annex XIII 1(e) | 依 Article 8 及相关授权法案实施 |
| 22 | Percentage share of nickel that is present in active materials and that has been recovered from battery manufacturing waste or post-consumer waste | 活性材料中来自制造废料或消费后废弃物回收的镍占比 | BR Annex XIII 1(e) | 依 Article 8 及相关授权法案实施 |
| 23 | The percentage share of lead that is present in the battery and that has been recovered from waste | 电池中来自废弃物回收的铅占比 | BR Annex XIII 1(e) | 依 Article 8 及相关授权法案实施 |
| 24 | The share of renewable content | 可再生来源内容物占比 | BR Annex XIII 1(f) | 全类别必填 |
| 25 | Rated capacity (in Ah) | 额定容量（Ah） | BR Annex XIII 1(g) | 与数据点 11 重复，暂不重复展示 |
| 26 | Minimal voltage, with temperature range when relevant | 最低电压，以及相关时对应的温度范围 | BR Annex XIII 1(h) | 全类别必填 |
| 27 | Nominal voltage, with temperature range when relevant | 标称电压，以及相关时对应的温度范围 | BR Annex XIII 1(h) | 全类别必填 |
| 28 | Maximum voltage, with temperature range when relevant | 最高电压，以及相关时对应的温度范围 | BR Annex XIII 1(h) | 全类别必填 |
| 29 | Original power capability (in Watts) | 初始功率能力（W） | BR Annex XIII 1(i) | 全类别必填 |
| 30 | Power limits, with temperature range when relevant | 功率限制，以及相关时对应的温度范围 | BR Annex XIII 1(i) | 全类别必填 |
| 31 | Expected battery lifetime expressed in cycles | 以循环次数表示的预期电池寿命 | BR Annex XIII 1(j) | EV、LMT 必填；部分工业电池适用 |
| 32 | Reference test used for expected battery lifetime expressed in cycles | 预期循环寿命采用的参考测试 | BR Annex XIII 1(j) | EV、LMT 必填；部分工业电池适用 |
| 33 | Capacity threshold for exhaustion | 判定电池耗尽的容量阈值 | BR Annex XIII 1(k) | EV 必填；LMT、工业电池暂不展示 |
| 34 | Temperature range the battery can withstand when not in use (reference test) | 电池停用时可承受的温度范围（参考测试） | BR Annex XIII 1(l) | 全类别必填 |
| 35 | Period for which the commercial warranty for the calendar life applies | 商业质保适用的日历寿命期限 | BR Annex XIII 1(m) | 存在商业质保时填写 |
| 36 | Initial round trip energy efficiency | 初始往返能量效率 | BR Annex XIII 1(n) | EV、LMT 必填；部分工业电池适用 |
| 37 | Round trip energy efficiency at 50 % of cycle-life | 循环寿命达到 50% 时的往返能量效率 | BR Annex XIII 1(n) | EV、LMT 必填；部分工业电池适用 |
| 38 | Internal battery cell and pack resistance | 电芯和电池包内部电阻 | BR Annex XIII 1(o) | 全类别必填 |
| 39 | C-rate of relevant cycle-life test | 相关循环寿命测试采用的 C 倍率 | BR Annex XIII 1(p) | EV、LMT 必填；部分工业电池适用 |
| 40 | The marking requirements laid down in Article 13(4) | Article 13(4) 规定的标识信息 | BR Annex XIII 1(q) | 全类别必填 |
| 41 | The marking requirements laid down in Article 13(5) | Article 13(5) 规定的标识信息 | BR Annex XIII 1(q) | 含镉或铅且达到适用条件时填写 |
| 42 | The EU declaration of conformity referred to in Article 18 | Article 18 所述欧盟符合性声明 | BR Annex XIII 1(r) | 全类别必填 |
| 43 | The information regarding the prevention and management of waste batteries laid down in Article 74(1), points (a) to (f). | Article 74(1)(a)-(f) 的废电池预防和管理信息 | BR Annex XIII 1(s) | 全类别必填 |
| 44 | Clear, understandable and readable instructions for use in a format that makes it possible to print, download and save them on an electronic device so that the user can access them at all times, in particular during a breakdown of the battery | 可打印、下载、保存并可随时访问的清晰易懂使用说明，尤其供电池故障时使用 | BR Annex XIII 1(t) | 截至 2027-02 等待 Omnibus 后续规定 |
| 45 | Detailed composition, including materials used in the cathode, anode and electrolyte | 详细组成，包括阴极、阳极和电解质所用材料 | BR Annex XIII 2(a) | 全类别必填 |
| 46 | Part numbers for components | 组件零件编号 | BR Annex XIII 2(b) | 全类别必填 |
| 47 | Contact details of sources for replacement spares | 替换备件来源的联系信息 | BR Annex XIII 2(b) | 全类别必填 |
| 48 | Dismantling information, including at least: exploded diagrams of the battery system/pack showing the location of battery cells; disassembly sequences; type and number of fastening techniques to be unlocked; tools required for disassembly; warnings if risk of damaging parts exist; amount of cells used and layout | 拆解信息，包括爆炸图、拆卸顺序、紧固方式、工具、损坏风险警告、电芯数量和布局等 | BR Annex XIII 2(c) | 全类别必填 |
| 49 | Safety measures | 安全措施 | BR Annex XIII 2(d) | 全类别必填 |
| 50 | Results of test reports proving compliance with the requirements laid down in this Regulation or any delegated or implementing act adopted pursuant to this Regulation | 证明符合法规及相关授权或实施法案要求的测试报告结果 | BR Annex XIII 3 | 全类别必填 |
| 51 | Rated capacity (in Ah) | 动态额定容量（Ah） | BR Annex XIII 4(a) | EV、LMT 为数据点 11 的动态值；工业电池按适用性 |
| 52 | Capacity fade (in %) | 容量衰减（%） | BR Annex XIII 4(a) | EV、LMT 必填；工业电池按适用性 |
| 53 | Power (in W) | 当前功率（W） | BR Annex XIII 4(a) | EV、LMT 必填；工业电池按适用性 |
| 54 | Power fade (in %) | 功率衰减（%） | BR Annex XIII 4(a) | EV、LMT 必填；工业电池按适用性 |
| 55 | Internal resistance (in Ω) | 内部电阻（Ω） | BR Annex XIII 4(a) | EV、LMT 必填；工业电池按适用性 |
| 56 | Internal resistance increase (in %) | 内部电阻增长（%） | BR Annex XIII 4(a) | EV、LMT 必填；工业电池按适用性 |
| 57 | Where applicable, energy round trip efficiency (in %) | 适用时的能量往返效率（%） | BR Annex XIII 4(a) | 全类别按适用性 |
| 58 | Where applicable, energy round trip efficiency fade (in %) | 适用时的能量往返效率衰减（%） | BR Annex XIII 4(a) | 全类别按适用性 |
| 59 | The expected life-time of the battery under the reference conditions for which it has been designed, in terms of cycles, except for non-cycle applications | 设计参考条件下以循环次数表示的预期寿命，非循环应用除外 | BR Annex XIII 4(a) | EV、LMT 必填；工业电池按适用性 |
| 60 | The expected life-time of the battery under the reference conditions for which it has been designed, in terms of calendar years | 设计参考条件下以日历年表示的预期寿命 | BR Annex XIII 4(a) | EV、LMT 必填；工业电池按适用性 |
| 61 | Information on the state of health of the battery pursuant to Article 14: state of certified energy (SOCE) | Article 14 电池健康状态：认证能量状态（SOCE） | BR Annex XIII 4(b) | EV 必填；LMT、工业电池暂不展示 |
| 62 | Information on the state of health of the battery pursuant to Article 14: remaining capacity | Article 14 电池健康状态：剩余容量 | BR Annex XIII 4(b) | LMT 必填；工业电池按适用性；EV 暂不展示 |
| 63 | Information on the state of health of the battery pursuant to Article 14: where possible, the remaining power capability | Article 14 电池健康状态：可行时的剩余功率能力 | BR Annex XIII 4(b) | LMT 必填；工业电池按适用性；EV 暂不展示 |
| 64 | Information on the state of health of the battery pursuant to Article 14: where possible, the remaining round trip efficiency | Article 14 电池健康状态：可行时的剩余往返效率 | BR Annex XIII 4(b) | LMT 必填；工业电池按适用性；EV 暂不展示 |
| 65 | Information on the state of health of the battery pursuant to Article 14: [where possible,] the evolution of self-discharging rates | Article 14 电池健康状态：可行时的自放电率变化 | BR Annex XIII 4(b) | LMT 必填；工业电池按适用性；EV 暂不展示 |
| 66 | Information on the state of health of the battery pursuant to Article 14: where possible, the ohmic resistance | Article 14 电池健康状态：可行时的欧姆内阻 | BR Annex XIII 4(b) | LMT 必填；工业电池按适用性；EV 暂不展示 |
| 67 | Information on the status of the battery, defined as ‘original’, ‘repurposed’, ‘re-used’, ‘remanufactured’ or ‘waste’; | 电池状态：原始、改变用途、再使用、再制造或废弃 | BR Annex XIII 4(c) | 全类别必填 |
| 68 | The number of charging and discharging cycles | 充放电循环次数 | BR Annex XIII 4(d) | 全类别按适用性 |
| 69 | Negative events, such as accidents | 事故等负面事件 | BR Annex XIII 4(d) | 全类别按适用性 |
| 70 | Periodically recorded information on the operating environmental conditions, including temperature | 周期记录的运行环境条件，包括温度 | BR Annex XIII 4(d) | 全类别按适用性 |
| 71 | Periodically recorded information on the state of charge | 周期记录的荷电状态（SOC） | BR Annex XIII 4(d) | 全类别按适用性 |

## 4. ReadDPPById 的真实 HTTP 入参

接口：

```http
GET /v1/dpps/{dppId}?representation={full|compressed}
```

参数：

| 位置 | 参数 | 类型 | 必需 | 说明 |
|---|---|---|---|---|
| Path | `dppId` | string / DppIdentifier | 是 | DPP 唯一标识，放入 URL 路径前执行 UTF-8 百分号编码 |
| Query | `representation` | enum | 否 | `full` 或 `compressed`；本实施配置默认 `compressed` |
| Header | `Accept` | string | 建议 | `application/json` |
| Header | `Authorization` | string | 视权限 | `Bearer <JWT>`，公开字段可允许匿名读取 |
| Header | `X-Correlation-ID` | string | 否 | 调用链跟踪标识 |
| Body | 无 | — | — | GET 接口不传 JSON 请求体 |

## 5. Full 请求参数 JSON

以下 JSON 是为了在 Apifox、Java DTO 或自动化测试中统一表达 Path、Query、Header 参数，**不是发送给服务端的 HTTP Body**。

```json
{
  "pathParameters": {
    "dppId": "urn:uuid:0198f87e-7c2a-7c58-91a3-3b83f6e91240"
  },
  "queryParameters": {
    "representation": "full"
  },
  "headers": {
    "Accept": "application/json",
    "Authorization": "Bearer <JWT>",
    "X-Correlation-ID": "01K3QBTB8F3CBTDPND6GHRFQAG"
  }
}
```

实际请求：

```http
GET /v1/dpps/urn%3Auuid%3A0198f87e-7c2a-7c58-91a3-3b83f6e91240?representation=full HTTP/1.1
Host: dpp.example.com
Accept: application/json
Authorization: Bearer <JWT>
X-Correlation-ID: 01K3QBTB8F3CBTDPND6GHRFQAG
```

## 6. Compressed 请求参数 JSON

```json
{
  "pathParameters": {
    "dppId": "urn:uuid:0198f87e-7c2a-7c58-91a3-3b83f6e91240"
  },
  "queryParameters": {
    "representation": "compressed"
  },
  "headers": {
    "Accept": "application/json",
    "Authorization": "Bearer <JWT>",
    "X-Correlation-ID": "01K3QBTB8F3CBTDPND6GHRFQAG"
  }
}
```

实际请求：

```http
GET /v1/dpps/urn%3Auuid%3A0198f87e-7c2a-7c58-91a3-3b83f6e91240?representation=compressed HTTP/1.1
Host: dpp.example.com
Accept: application/json
Authorization: Bearer <JWT>
X-Correlation-ID: 01K3QBTB8F3CBTDPND6GHRFQAG
```

## 7. Full 响应 JSON 示例

示例产品是一块 48 V、20 Ah 的 LMT 电动自行车电池。业务数据项来自法规清单，数值均为模拟值。`elementId` 和 `dictionaryReference` 是临时实施映射。

```json
{
  "statusCode": "Success",
  "payload": {
    "digitalProductPassportId": "urn:uuid:0198f87e-7c2a-7c58-91a3-3b83f6e91240",
    "uniqueProductIdentifier": "https://id.gs1.org/01/09506000134352/21/LMT26B00018427",
    "granularity": "item",
    "dppSchemaVersion": "company:battery-passport:0.9.0",
    "dppStatus": "active",
    "lastUpdated": "2026-08-27T08:42:17Z",
    "economicOperatorId": "iso6523-actorid-upis::0088:9506000134352",
    "facilityId": "iso6523-actorid-upis::0088:95060001343520001",
    "contentSpecificationIds": [
      "EU-2023-1542:Annex-VI",
      "EU-2023-1542:Annex-XIII",
      "EN-18223:2026"
    ],
    "elements": [
      {
        "elementId": "batteryIdentification",
        "objectType": "DataElementCollection",
        "dictionaryReference": "https://vocabulary.example.com/battery/identification",
        "elements": [
          {
            "elementId": "manufacturerName",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp03-manufacturer-name",
            "valueDataType": "xsd:string",
            "value": "Example Energy Systems GmbH"
          },
          {
            "elementId": "manufacturerPostalAddress",
            "objectType": "MultiLanguageDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp04-manufacturer-postal-address",
            "value": [
              {
                "language": "en",
                "value": "Industriestrasse 18, 70565 Stuttgart, Germany"
              },
              {
                "language": "de",
                "value": "Industriestraße 18, 70565 Stuttgart, Deutschland"
              }
            ]
          },
          {
            "elementId": "manufacturerWebsite",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp05-manufacturer-website",
            "valueDataType": "xsd:anyURI",
            "value": "https://manufacturer.example.com"
          },
          {
            "elementId": "batteryCategory",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp06-battery-category",
            "valueDataType": "xsd:string",
            "value": "LMT"
          },
          {
            "elementId": "modelIdentification",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp07-model-identification",
            "valueDataType": "xsd:string",
            "value": "EBP-48V-20AH-PRO"
          },
          {
            "elementId": "serialNumber",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp07-serial-number",
            "valueDataType": "xsd:string",
            "value": "LMT26B00018427"
          },
          {
            "elementId": "placeOfManufacture",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp08-place-of-manufacture",
            "valueDataType": "xsd:string",
            "value": "Stuttgart, Baden-Württemberg, Germany"
          },
          {
            "elementId": "dateOfManufacture",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp09-date-of-manufacture",
            "valueDataType": "xsd:string",
            "value": "2026-06"
          }
        ]
      },
      {
        "elementId": "technicalCharacteristics",
        "objectType": "DataElementCollection",
        "dictionaryReference": "https://vocabulary.example.com/battery/technical-characteristics",
        "elements": [
          {
            "elementId": "weight",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp10-weight-kg",
            "valueDataType": "xsd:decimal",
            "value": 5.84
          },
          {
            "elementId": "capacity",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp11-capacity-ah",
            "valueDataType": "xsd:decimal",
            "value": 20.0
          },
          {
            "elementId": "chemistry",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp12-chemistry",
            "valueDataType": "xsd:string",
            "value": "Lithium-ion NMC 811 / Graphite"
          },
          {
            "elementId": "minimalVoltage",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp26-minimal-voltage-v",
            "valueDataType": "xsd:decimal",
            "value": 39.0
          },
          {
            "elementId": "nominalVoltage",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp27-nominal-voltage-v",
            "valueDataType": "xsd:decimal",
            "value": 48.0
          },
          {
            "elementId": "maximumVoltage",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp28-maximum-voltage-v",
            "valueDataType": "xsd:decimal",
            "value": 54.6
          },
          {
            "elementId": "originalPowerCapability",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp29-original-power-capability-w",
            "valueDataType": "xsd:decimal",
            "value": 1000.0
          },
          {
            "elementId": "expectedBatteryLifetimeCycles",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp31-expected-lifetime-cycles",
            "valueDataType": "xsd:integer",
            "value": 1000
          },
          {
            "elementId": "storageTemperatureRange",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp34-storage-temperature-range",
            "valueDataType": "xsd:string",
            "value": "-20 °C to +45 °C"
          },
          {
            "elementId": "initialRoundTripEnergyEfficiency",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp36-initial-round-trip-efficiency-percent",
            "valueDataType": "xsd:decimal",
            "value": 94.2
          },
          {
            "elementId": "internalBatteryResistance",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp38-pack-resistance-ohm",
            "valueDataType": "xsd:decimal",
            "value": 0.1
          }
        ]
      },
      {
        "elementId": "materialComposition",
        "objectType": "DataElementCollection",
        "dictionaryReference": "https://vocabulary.example.com/battery/material-composition",
        "elements": [
          {
            "elementId": "hazardousSubstances",
            "objectType": "MultiValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp13-hazardous-substances",
            "valueDataType": "xsd:string",
            "value": [
              {
                "elementId": "hazardousSubstance01",
                "objectType": "SingleValuedDataElement",
                "valueDataType": "xsd:string",
                "value": "1-methyl-2-pyrrolidone (NMP), CAS 872-50-4"
              },
              {
                "elementId": "hazardousSubstance02",
                "objectType": "SingleValuedDataElement",
                "valueDataType": "xsd:string",
                "value": "Lithium hexafluorophosphate, CAS 21324-40-3"
              }
            ]
          },
          {
            "elementId": "criticalRawMaterials",
            "objectType": "MultiValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp15-critical-raw-materials",
            "valueDataType": "xsd:string",
            "value": [
              {
                "elementId": "criticalRawMaterial01",
                "objectType": "SingleValuedDataElement",
                "valueDataType": "xsd:string",
                "value": "Lithium"
              },
              {
                "elementId": "criticalRawMaterial02",
                "objectType": "SingleValuedDataElement",
                "valueDataType": "xsd:string",
                "value": "Cobalt"
              },
              {
                "elementId": "criticalRawMaterial03",
                "objectType": "SingleValuedDataElement",
                "valueDataType": "xsd:string",
                "value": "Natural graphite"
              }
            ]
          },
          {
            "elementId": "detailedComposition",
            "objectType": "DataElementCollection",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp45-detailed-composition",
            "elements": [
              {
                "elementId": "cathodeMaterial",
                "objectType": "SingleValuedDataElement",
                "valueDataType": "xsd:string",
                "value": "LiNi0.8Mn0.1Co0.1O2"
              },
              {
                "elementId": "anodeMaterial",
                "objectType": "SingleValuedDataElement",
                "valueDataType": "xsd:string",
                "value": "Graphite with 5% silicon composite"
              },
              {
                "elementId": "electrolyte",
                "objectType": "SingleValuedDataElement",
                "valueDataType": "xsd:string",
                "value": "LiPF6 in EC/EMC electrolyte"
              },
              {
                "elementId": "numberOfCells",
                "objectType": "SingleValuedDataElement",
                "valueDataType": "xsd:integer",
                "value": 52
              },
              {
                "elementId": "cellLayout",
                "objectType": "SingleValuedDataElement",
                "valueDataType": "xsd:string",
                "value": "13S4P"
              }
            ]
          }
        ]
      },
      {
        "elementId": "documentsAndSafety",
        "objectType": "DataElementCollection",
        "dictionaryReference": "https://vocabulary.example.com/battery/documents-and-safety",
        "elements": [
          {
            "elementId": "usableExtinguishingAgent",
            "objectType": "MultiLanguageDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp14-usable-extinguishing-agent",
            "value": [
              {
                "language": "en",
                "value": "Use large quantities of water for cooling; avoid enclosed-space exposure to vapours."
              },
              {
                "language": "de",
                "value": "Mit großen Mengen Wasser kühlen; Dämpfe in geschlossenen Räumen vermeiden."
              }
            ]
          },
          {
            "elementId": "euDeclarationOfConformity",
            "objectType": "RelatedResource",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp42-eu-declaration-of-conformity",
            "contentType": "application/pdf",
            "url": "https://documents.example.com/batteries/LMT26B00018427/eu-declaration.pdf",
            "language": "en",
            "resourceTitle": "EU Declaration of Conformity"
          },
          {
            "elementId": "dismantlingInformation",
            "objectType": "RelatedResource",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp48-dismantling-information",
            "contentType": "application/pdf",
            "url": "https://documents.example.com/batteries/LMT26B00018427/dismantling.pdf",
            "language": "en",
            "resourceTitle": "Battery pack dismantling instructions and exploded diagram"
          },
          {
            "elementId": "safetyMeasures",
            "objectType": "MultiLanguageDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp49-safety-measures",
            "value": [
              {
                "language": "en",
                "value": "Disconnect the battery before servicing. Do not crush, puncture, short-circuit or expose above 60 °C."
              },
              {
                "language": "de",
                "value": "Vor Wartungsarbeiten Batterie trennen. Nicht zerdrücken, durchstechen, kurzschließen oder über 60 °C erhitzen."
              }
            ]
          },
          {
            "elementId": "complianceTestReport",
            "objectType": "RelatedResource",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp50-test-reports",
            "contentType": "application/pdf",
            "url": "https://documents.example.com/batteries/LMT26B00018427/test-report.pdf",
            "language": "en",
            "resourceTitle": "Performance, durability and safety test report"
          }
        ]
      },
      {
        "elementId": "currentPerformanceAndStateOfHealth",
        "objectType": "DataElementCollection",
        "dictionaryReference": "https://vocabulary.example.com/battery/current-performance",
        "elements": [
          {
            "elementId": "ratedCapacity",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp51-rated-capacity-ah",
            "valueDataType": "xsd:decimal",
            "value": 18.6
          },
          {
            "elementId": "capacityFade",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp52-capacity-fade-percent",
            "valueDataType": "xsd:decimal",
            "value": 7.0
          },
          {
            "elementId": "power",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp53-power-w",
            "valueDataType": "xsd:decimal",
            "value": 925.0
          },
          {
            "elementId": "internalResistance",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp55-internal-resistance-ohm",
            "valueDataType": "xsd:decimal",
            "value": 0.118
          },
          {
            "elementId": "remainingCapacity",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp62-remaining-capacity-ah",
            "valueDataType": "xsd:decimal",
            "value": 18.6
          },
          {
            "elementId": "remainingPowerCapability",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp63-remaining-power-capability-w",
            "valueDataType": "xsd:decimal",
            "value": 925.0
          },
          {
            "elementId": "remainingRoundTripEfficiency",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp64-remaining-round-trip-efficiency-percent",
            "valueDataType": "xsd:decimal",
            "value": 91.3
          },
          {
            "elementId": "batteryStatus",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp67-battery-status",
            "valueDataType": "xsd:string",
            "value": "original"
          },
          {
            "elementId": "chargingDischargingCycleCount",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp68-cycle-count",
            "valueDataType": "xsd:integer",
            "value": 327
          },
          {
            "elementId": "stateOfCharge",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/dp71-state-of-charge-percent",
            "valueDataType": "xsd:decimal",
            "value": 76.4
          },
          {
            "elementId": "lastMeasurementTime",
            "objectType": "SingleValuedDataElement",
            "dictionaryReference": "https://vocabulary.example.com/battery/measurement-time",
            "valueDataType": "xsd:dateTime",
            "value": "2026-08-27T08:40:00Z"
          }
        ]
      }
    ]
  }
}
```

## 8. Compressed 响应 JSON 示例

Compressed 表示省略 `objectType`、`dictionaryReference` 和 `valueDataType` 等重复语义元数据，直接用 `elementId` 作为业务 JSON Key。字段定义、类型和单位由版本化数据字典解释。

```json
{
  "statusCode": "Success",
  "payload": {
    "digitalProductPassportId": "urn:uuid:0198f87e-7c2a-7c58-91a3-3b83f6e91240",
    "uniqueProductIdentifier": "https://id.gs1.org/01/09506000134352/21/LMT26B00018427",
    "granularity": "item",
    "dppSchemaVersion": "company:battery-passport:0.9.0",
    "dppStatus": "active",
    "lastUpdated": "2026-08-27T08:42:17Z",
    "economicOperatorId": "iso6523-actorid-upis::0088:9506000134352",
    "facilityId": "iso6523-actorid-upis::0088:95060001343520001",
    "contentSpecificationIds": [
      "EU-2023-1542:Annex-VI",
      "EU-2023-1542:Annex-XIII",
      "EN-18223:2026"
    ],
    "batteryIdentification": {
      "manufacturerName": "Example Energy Systems GmbH",
      "manufacturerPostalAddress": {
        "en": "Industriestrasse 18, 70565 Stuttgart, Germany",
        "de": "Industriestraße 18, 70565 Stuttgart, Deutschland"
      },
      "manufacturerWebsite": "https://manufacturer.example.com",
      "batteryCategory": "LMT",
      "modelIdentification": "EBP-48V-20AH-PRO",
      "serialNumber": "LMT26B00018427",
      "placeOfManufacture": "Stuttgart, Baden-Württemberg, Germany",
      "dateOfManufacture": "2026-06"
    },
    "technicalCharacteristics": {
      "weight": 5.84,
      "capacity": 20.0,
      "chemistry": "Lithium-ion NMC 811 / Graphite",
      "minimalVoltage": 39.0,
      "nominalVoltage": 48.0,
      "maximumVoltage": 54.6,
      "originalPowerCapability": 1000.0,
      "expectedBatteryLifetimeCycles": 1000,
      "storageTemperatureRange": "-20 °C to +45 °C",
      "initialRoundTripEnergyEfficiency": 94.2,
      "internalBatteryResistance": 0.1
    },
    "materialComposition": {
      "hazardousSubstances": [
        "1-methyl-2-pyrrolidone (NMP), CAS 872-50-4",
        "Lithium hexafluorophosphate, CAS 21324-40-3"
      ],
      "criticalRawMaterials": [
        "Lithium",
        "Cobalt",
        "Natural graphite"
      ],
      "detailedComposition": {
        "cathodeMaterial": "LiNi0.8Mn0.1Co0.1O2",
        "anodeMaterial": "Graphite with 5% silicon composite",
        "electrolyte": "LiPF6 in EC/EMC electrolyte",
        "numberOfCells": 52,
        "cellLayout": "13S4P"
      }
    },
    "documentsAndSafety": {
      "usableExtinguishingAgent": {
        "en": "Use large quantities of water for cooling; avoid enclosed-space exposure to vapours.",
        "de": "Mit großen Mengen Wasser kühlen; Dämpfe in geschlossenen Räumen vermeiden."
      },
      "euDeclarationOfConformity": {
        "contentType": "application/pdf",
        "url": "https://documents.example.com/batteries/LMT26B00018427/eu-declaration.pdf",
        "language": "en",
        "resourceTitle": "EU Declaration of Conformity"
      },
      "dismantlingInformation": {
        "contentType": "application/pdf",
        "url": "https://documents.example.com/batteries/LMT26B00018427/dismantling.pdf",
        "language": "en",
        "resourceTitle": "Battery pack dismantling instructions and exploded diagram"
      },
      "safetyMeasures": {
        "en": "Disconnect the battery before servicing. Do not crush, puncture, short-circuit or expose above 60 °C.",
        "de": "Vor Wartungsarbeiten Batterie trennen. Nicht zerdrücken, durchstechen, kurzschließen oder über 60 °C erhitzen."
      },
      "complianceTestReport": {
        "contentType": "application/pdf",
        "url": "https://documents.example.com/batteries/LMT26B00018427/test-report.pdf",
        "language": "en",
        "resourceTitle": "Performance, durability and safety test report"
      }
    },
    "currentPerformanceAndStateOfHealth": {
      "ratedCapacity": 18.6,
      "capacityFade": 7.0,
      "power": 925.0,
      "internalResistance": 0.118,
      "remainingCapacity": 18.6,
      "remainingPowerCapability": 925.0,
      "remainingRoundTripEfficiency": 91.3,
      "batteryStatus": "original",
      "chargingDischargingCycleCount": 327,
      "stateOfCharge": 76.4,
      "lastMeasurementTime": "2026-08-27T08:40:00Z"
    }
  }
}
```

## 9. Full 与 Compressed 的技术差异

| 对比项 | Full | Compressed |
|---|---|---|
| 结构名称 | Fully expanded DataElement representation | Compressed JSON representation |
| 业务字段表示 | DataElement 对象数组 | 普通 JSON Key/Value |
| 是否携带 `objectType` | 是 | 通常不携带 |
| 是否携带 `dictionaryReference` | 是 | 通常从外部数据字典解析 |
| 是否携带 `valueDataType` | 是 | 由 Schema 或数据字典解析 |
| 数据量 | 较大 | 较小 |
| 可读性 | 适合通用语义交换、审计、跨系统解释 | 更符合传统 REST 接口和业务开发习惯 |
| 推荐用途 | 通用 DPP 服务、语义交换、数据治理 | App、前端、企业内部服务及高频读取 |

建议服务端同时支持两种读取格式，共享同一个领域模型和访问控制逻辑，只在序列化层切换表示。默认返回 `compressed`，调用方明确指定 `representation=full` 时返回 full。

## 10. 字段命名和数据字典实施建议

### 10.1 分离法规数据点与机器字段

内部数据字典至少保存：

```json
{
  "regulatoryDataPointNo": 62,
  "legalLabel": "Remaining capacity",
  "elementId": "remainingCapacity",
  "dictionaryReference": "https://vocabulary.example.com/battery/dp62-remaining-capacity-ah",
  "valueDataType": "xsd:decimal",
  "javaType": "java.math.BigDecimal",
  "unit": "Ah",
  "applicableBatteryCategories": ["LMT", "INDUSTRIAL_IF_APPLICABLE"],
  "legalSource": "Regulation (EU) 2023/1542, Annex XIII 4(b)",
  "mappingStatus": "PROVISIONAL"
}
```

### 10.2 不要把临时 URI 伪装成欧盟 URI

- 开发阶段使用企业控制的域名或 `urn:company:` 命名空间。
- `.example.com` 仅用于文档示例，生产环境不得使用。
- 欧盟正式语义目录发布后，通过配置或版本化迁移表替换，不改变业务主键和历史数据。

### 10.3 数值类型

- `xsd:decimal` → Java `BigDecimal`
- `xsd:integer` → Java `BigInteger` 或按范围选择 `Integer`/`Long`
- `xsd:boolean` → Java `Boolean`
- `xsd:date` → Java `LocalDate`
- `xsd:dateTime` → Java `OffsetDateTime` 或 `Instant`
- `xsd:anyURI` → Java `URI`
- `xsd:base64Binary` → Java `byte[]`

### 10.4 访问控制

`ReadDPPById` 在序列化 full 或 compressed 之前都必须执行访问控制和字段级过滤。消费者、维修商、再制造商、回收商、市场监管机构可能看到不同字段；不能先返回完整 JSON 再依赖前端隐藏。

## 11. 当前成熟度说明

欧委会已给出 71 个法规业务数据点，但其 2026-08 指南明确说明，未来更新仍可能补充单个数据点的定义、计量单位和报告格式。因此目前可以完成：

- API 路径、方法、参数、认证和错误响应；
- EN 18223 full/compressed 通用结构；
- 71 个法规数据点的内部字段映射；
- Java DTO、数据库模型和访问控制框架。

仍需保持可配置、等待官方目录进一步确定的内容包括：

- 欧盟官方 `elementId`；
- 正式 `dictionaryReference` URI；
- 部分字段的标准单位、精度和格式；
- 部分枚举及代码表；
- 产品组官方机器可读 Schema 及版本；
- 电池角色访问权限实施法案中的最终字段可见性。
