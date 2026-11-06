供应商SN接口说明 v1.0 | 2026-11-04

服务域名：https://battery.chdpp.com
鉴权：POST /open-api/dpp-platform/login，Header apikey；业务请求Header token。
上传：POST /open-api/dpp-platform/supplier/sn/upload；根对象snList 1–100条，每项直接为完整EV模型。
SN路径：snList[].batteryIdentification.serialNumber，string必填且恰好24字符。
电池分类：只接受EV完整URI。SN完整国标编码合法性仍需业务校验。

04号JSON覆盖63个顶层字段；05号JSON只保留52个顶层必填字段及必要子字段。
两者均已通过附件的Draft 2020-12请求Schema及format检查。
示例SN TEST20261104000000000001 为虚构的结构测试码，不代表已登记国标码；/demo资源URL为演示。
语义枚举/规范URI维持欧盟域名；未使用真实凭证、未调用线上接口。

字段字典覆盖206个显式节点，18组不同枚举完整列出。
指定源文件SHA-256：abeaff08cd571a158d4062a2d12a31a6504742c810a02ddc24fc0390e8ea2345
结构、语义、业务三类约束的边界请查看Word/PDF与校验报告。
JSON Schema通过不等于完整SHACL或国标合规验证。
本版是设计/联调文档，新增结构及幂等规则需要服务端实现。
