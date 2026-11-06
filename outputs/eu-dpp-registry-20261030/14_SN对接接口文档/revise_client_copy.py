"""Rewrite client-facing prose while preserving the approved EV request contract."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import shutil
import zipfile
from collections import OrderedDict
from pathlib import Path

from lxml import etree as X
from openpyxl import load_workbook

HERE = Path(__file__).resolve().parent
STEM = '供应商SN对接接口说明_EV全量语义_24位国标码_v1.0'
BACKUP = Path(json.loads((HERE / '文案调整输入.json').read_text())['backup_dir'])
SOURCE = BACKUP / (STEM + '_模板格式修订版.docx')
ATT = HERE / '对接附件'
MODEL_PATH = HERE.parent / '09_EV电池单模型/EVBatteryPassport_Apifox直接建模.schema.json'
MODEL = json.loads(MODEL_PATH.read_text())
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
EV = 'https://repository.product-passport.ec.europa.eu/product/batteries/vocabs/battery-category#electricVehicleBattery'
BRAND = re.compile(r'上汽(?:大乘用车|乘用车)?|上海汽车乘用车有限公司|SAIC|saicmotortest', re.I)
CHANGES = []


def walk(n, p='', parent=None, name=None):
    if p:
        yield p, n, parent, name
    for k, v in n.get('properties', {}).items():
        yield from walk(v, p + '.' + k if p else k, n, k)
    if isinstance(n.get('items'), dict):
        yield from walk(n['items'], p + '[]', n, '[]')


NODES = list(walk(MODEL))
BY_PATH = {p: n for p, n, _, _ in NODES}
ENUMS = OrderedDict()
for p, n, _, _ in NODES:
    if 'enum' in n:
        key = json.dumps(n['enum'], ensure_ascii=False)
        if key not in ENUMS:
            ENUMS[key] = {'id': f'E{len(ENUMS)+1:02}', 'values': n['enum']}


def tag(n):
    return '{' + W + '}' + n


def txt(n):
    return ''.join(n.xpath('.//w:t/text()', namespaces=NS))


def set_p(p, value):
    old = txt(p)
    if old == value:
        return
    first_run = p.find('.//w:r', NS)
    rp = copy.deepcopy(first_run.find('w:rPr', NS)) if first_run is not None and first_run.find('w:rPr', NS) is not None else None
    for child in list(p):
        if child.tag not in (tag('pPr'), tag('bookmarkStart'), tag('bookmarkEnd')):
            p.remove(child)
    r = X.Element(tag('r'))
    if rp is not None:
        r.append(rp)
    for i, line in enumerate(value.split('\n')):
        if i:
            r.append(X.Element(tag('br')))
        t = X.Element(tag('t')); t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve'); t.text = line; r.append(t)
    p.append(r)
    CHANGES.append({'原文': old, '修订': value})


def set_cell(cell, value):
    p = cell.find('w:p', NS)
    set_p(p, value)
    for extra in cell.findall('w:p', NS)[1:]:
        cell.remove(extra)


def title(path):
    return BY_PATH[path].get('title', path).replace('（数组元素）', '')


TOP_TEXT = {
    'digitalProductPassportId': '数字产品护照标识，用于标识当前护照记录。',
    'uniqueProductIdentifier': '唯一产品标识（UPI），用于标识当前护照对应的单个EV电池。',
    'granularity': '护照粒度，固定为单个电池（item）的完整URI。',
    'dppStatus': '护照实例状态，提交枚举E02中的完整URI。',
    'lastUpdate': '本条护照数据的最后更新时间，使用包含时区的date-time字符串。示例：2026-11-04T08:00:00+08:00。',
    'contentSpecificationIds': '内容规范标识数组，包含core、电池组、EV子组的规范URI，各一项；版本号采用三段数字格式。',
    'registrantIdentity': '注册该护照或承担护照责任的主体身份信息，使用字符串填写。',
    'manufacturerIdentification': '制造商标识对象。manufacturerName、manufacturerTradeName、manufacturerTrademark至少提供一项。',
    'manufacturerPostalAddress': '制造商可接收书面联系的完整邮寄地址，使用单个字符串填写。',
    'manufacturerContactDetails': '制造商联系方式。提交本对象时，manufacturerWebsite、manufacturerEmail至少提供一项。',
    'batteryCategory': '电池分类，固定为electricVehicleBattery的完整URI。',
    'batteryIdentification': '电池识别信息。serialNumber必填且恰好24字符；modelIdentifier与batchNumber同时填写或同时省略；productNumber与otherIdentificationElement为选填标识。',
    'manufacturingPlace': '制造地点。manufacturingCountry必填；manufacturingCity、manufacturingRegion至少提供一项。',
    'manufacturingDate': '电池制造年月，格式为YYYY-MM。示例：2026-11；月份取01至12。',
    'batteryChemistry': '电池化学体系列表，每项填写枚举E06中的完整URI。',
    'hazardousSubstances': '电池中汞、镉、铅以外的有害物质名称列表，每项为字符串。',
    'criticalRawMaterials': '电池所含关键原材料列表，每项填写枚举E07中的完整URI。',
    'commercialWarrantyPeriod': '商业日历寿命质保期，单位为月，填写非负整数；不提供商业质保时省略本字段。',
    'chemicalSymbol': '电池标签上的化学符号列表，每项填写枚举E17中的完整URI。',
    'separateCollectionSymbol': '电池使用分类收集标识的确认值，固定填写JSON布尔值true。',
    'batteryStatus': '电池生命周期状态，填写枚举E18中的完整URI。',
    'numberOfChargingCycles': '电池累计充电循环次数列表，每项为非负整数，单位为次；电池投入使用前省略本字段。',
    'numberOfDischargingCycles': '电池累计放电循环次数列表，每项为非负整数，单位为次；电池投入使用前省略本字段。',
    'negativeEvents': '电池生命周期内发生的负面事件说明，使用多语言文本对象填写。',
    'environmentalConditionHistogramSet': '电池运行环境条件的直方图集合，用于记录投入使用后的环境条件分布；电池投入使用前省略本字段。',
    'stateOfChargeHistogram': '荷电状态（SOC）分布直方图列表，用于记录投入使用后的SOC分布；电池投入使用前省略本字段。',
    'expectedLifetimeCycles': '预期循环寿命，填写整数，单位为循环次数。',
    'expectedLifetimeCyclesRest': '参考测试条件下的预期循环寿命，填写整数，单位为循环次数。',
    'expectedLifetimeYears': '参考测试条件下的预期使用年限，填写整数，单位为年。',
}

SPECIFIC = {
    'manufacturerIdentification.manufacturerName': '制造商名称。manufacturerTradeName和manufacturerTrademark均未提供时，本字段必填。',
    'manufacturerIdentification.manufacturerTradeName': '制造商注册商号。manufacturerName和manufacturerTrademark均未提供时，本字段必填。',
    'manufacturerIdentification.manufacturerTrademark': '制造商注册商标。manufacturerName和manufacturerTradeName均未提供时，本字段必填。',
    'manufacturerContactDetails.manufacturerWebsite': '制造商网站URI。提交manufacturerContactDetails且未提供manufacturerEmail时，本字段必填。',
    'manufacturerContactDetails.manufacturerEmail': '制造商邮箱URI，格式为mailto:邮箱地址。提交manufacturerContactDetails且未提供manufacturerWebsite时，本字段必填。',
    'batteryIdentification.serialNumber': '电池SN，必填字符串，恰好24字符，保留前导0；编码须符合GB/T 34014—2017及第1号修改单。',
    'batteryIdentification.modelIdentifier': '电池型号标识。提交batchNumber时，本字段必填。',
    'batteryIdentification.batchNumber': '电池生产批次号。提交modelIdentifier时，本字段必填。',
    'batteryIdentification.productNumber': '电池产品编号，作为SN之外的补充识别信息。',
    'batteryIdentification.otherIdentificationElement': '电池的其他补充识别信息。',
    'manufacturingPlace.manufacturingCity': '制造城市。未提供manufacturingRegion时，本字段必填。',
    'manufacturingPlace.manufacturingRegion': '制造地区。未提供manufacturingCity时，本字段必填。',
    'manufacturingPlace.manufacturingCountry': '制造国家或辖区代码，填写3位大写字母。示例：CHN。',
}


def field_description(path, n):
    leaf = path.rsplit('.', 1)[-1]
    parent_path = path.rsplit('.', 1)[0] if '.' in path else ''
    if path in SPECIFIC:
        description = SPECIFIC[path]
    elif path in TOP_TEXT:
        description = TOP_TEXT[path]
    elif path.endswith('[]'):
        scalar_items = {
            'contentSpecificationIds[]': '单个内容规范URI，填写core、电池组或EV子组的完整规范标识。',
            'batteryChemistry[]': '单个电池化学体系URI。',
            'hazardousSubstances[]': '单个有害物质名称，填写汞、镉、铅以外的有害物质名称字符串。',
            'criticalRawMaterials[]': '单个关键原材料URI。',
            'chemicalSymbol[]': '单个电池标签化学符号URI。',
            'numberOfChargingCycles[]': '单个充电循环次数值，填写非负整数，单位为次。',
            'numberOfDischargingCycles[]': '单个放电循环次数值，填写非负整数，单位为次。',
            'powerLimit[]': '单组功率限值及其测试条件，unit和value必填，valueRange选填。',
            'stateOfChargeHistogram[]': '单个荷电状态（SOC）分布直方图。',
        }
        if path in scalar_items:
            description = scalar_items[path]
        elif path.endswith('.bin[]'):
            description = '单个直方图区间统计记录，包含区间下界、上界及统计值。'
        else:
            description = '单个' + title(path).replace('列表', '').replace('集合', '') + '记录。'
    elif leaf == 'value':
        description = title(parent_path) + '的数值，填写JSON number；计量单位由同级unit指定。'
    elif leaf == 'unit':
        description = title(parent_path) + '的计量单位，提交完整单位URI。'
    elif leaf in ('lowerBound', 'upperBound'):
        boundary = '下界' if leaf == 'lowerBound' else '上界'
        description = ('直方图区间的' + boundary + '，单位由所在直方图的binRangeUnit指定。') if '.bin[]' in path else ('取值或测试条件区间的' + boundary + '，单位由同级unit指定。')
    elif leaf == 'binValue':
        description = '该区间的统计值，单位由所在直方图的binValueUnit指定。'
    elif leaf == 'binRangeUnit':
        description = '直方图区间上下界的计量单位，提交完整单位URI。'
    elif leaf == 'binValueUnit':
        description = '直方图各区间统计值的计量单位，提交完整单位URI。'
    elif leaf == 'observedCharacteristic':
        description = '取值范围或直方图所针对的观测特征，提交完整特征URI。'
    elif leaf == 'contentType':
        description = '资源的MIME媒体类型字符串，与url指向资源的实际类型一致。示例：application/pdf。'
    elif leaf == 'url':
        description = title(parent_path) + '的访问URI，填写完整资源地址。'
    elif leaf == 'resourceTitle':
        description = title(parent_path) + '的标题，使用字符串填写。'
    elif leaf == 'language':
        description = '资源内容的语言标签，格式为“语言-地区”。示例：en-GB。'
    elif leaf == 'valueRange':
        description = title(parent_path) + '对应的取值或测试条件区间。'
    else:
        description = title(path) + '。'

    rules = []
    if 'propertyNames' in n:
        rules.append('至少提供1个语言键；键格式为两位字母、半角连字符、两位字母，如zh-CN、en-IE；每个键的值填写文本字符串')
    if n.get('type') == 'array':
        rules.append('每项类型为' + n['items'].get('type', 'JSON值'))
    if 'enum' in n:
        group = ENUMS[json.dumps(n['enum'], ensure_ascii=False)]
        count = 1 if path == 'batteryCategory' else len(n['enum'])
        rules.append(f'枚举{group["id"]}，共{count}项，完整取值见附录A')
    if 'const' in n:
        rules.append('固定值：' + json.dumps(n['const'], ensure_ascii=False))
    if 'format' in n:
        rules.append('格式：' + n['format'])
    if 'pattern' in n:
        rules.append('正则：' + n['pattern'])
    for key, label in [('minimum', '最小值'), ('maximum', '最大值'), ('exclusiveMinimum', '必须大于'), ('exclusiveMaximum', '必须小于'), ('multipleOf', '数值步长')]:
        if key in n:
            rules.append(f'{label}：{n[key]}')
    if n.get('uniqueItems'):
        rules.append('数组元素不得重复')
    if n.get('additionalProperties') is False or n.get('unevaluatedProperties') is False:
        rules.append('仅接收本表列出的成员字段')
    if n.get('required') and path not in ('batteryIdentification', 'manufacturingPlace'):
        rules.append('必填成员：' + '、'.join(n['required']))
    return description + ('\n' + '；'.join(rules) + '。' if rules else '')


def required_text(path, old):
    if path == 'batteryIdentification.serialNumber':
        return '是'
    if old.startswith('否'):
        return '否'
    if old == '数组元素约束':
        return '—'
    if old.startswith('条件必填'):
        return '条件必填'
    if old == '是（父对象存在时）':
        return '父对象提交时必填'
    return old


def length_text(value):
    if value in ('未限定', '未限定项数'):
        return '不限'
    return value


P_REPLACE = {
    '本文是接口设计及对接说明，供评审与联调使用。新增EV请求结构及平台业务规则需要服务端按本版实现；本文不表示线上接口已完成改造或验证。': '本文规定供应商系统与电池护照（DPP）平台的鉴权方式、EV电池数据结构、字段填写要求及业务处理协议。',
    '字段校验以指定的EV JSON Schema为基础，平台附加规则单列说明；不得由字段名称推断或另行增加必填字段。': '供应商按本规范构造请求。平台执行结构校验、语义内容校验与业务校验，并按本规范返回处理结果。',
    '请求字段来自 EVBatteryPassport_Apifox直接建模.schema.json；参考《供应商接口对接标准说明-20260818》的章节、参数表及鉴权逻辑。供应商上传时，每条数据直接使用EV对象，SN统一填写在 batteryIdentification.serialNumber。': '请求体使用snList数组批量提交EV电池数据，每条记录为一个完整的EV电池对象。电池SN填写在batteryIdentification.serialNumber，类型为string，长度固定为24字符。',
    '本版“24位”在结构校验中指字符串恰好24个字符，不是数值类型。GB/T 34014—2017《汽车动力蓄电池编码规则》及现行第1号修改单的字符、分段及生产企业代码等合法性须另作业务校验；长度正确不代表完整国标校验通过。附件SN为虚构的24字符测试占位码，不代表已登记国标编码。': 'SN使用24字符的字符串，保留前导0。平台先校验类型与长度，再按GB/T 34014—2017《汽车动力蓄电池编码规则》及第1号修改单校验编码字符、分段及生产企业登记信息。示例SN为格式演示值，正式报送须替换为真实国标码。',
    '调用顺序：先鉴权；成功后从body.token取值；上传接口Header添加token: <TOKEN>。参考文档没有给出token固定有效期、刷新接口或自动续期协议，本版不虚构这些字段。': '调用顺序：先调用鉴权接口；code转为字符串后等于200且success为true时，读取并缓存body.token；上传接口Header传入token原值。接口返回401时重新鉴权，并最多重试一次。',
    '字段路径相对每一个snList数组元素；拼接snList[].即为完整请求路径。“是（父对象存在时）”表示字段所在对象/数组元素已提交时必须提供；不能理解为可选父对象也必须提交。“数组元素约束”说明元素类型及内部结构，不单独决定数组是否必填或是否允许为空。': '字段路径以snList[]为根。“是”表示必填；“否”表示选填；“父对象提交时必填”表示提交所属对象时必须同时提交该字段；“条件必填”的触发条件在说明中列明。“—”用于数组元素节点，其规则对每个元素分别生效。“不限”表示该列未设置字符数或数组项数上限。',
    '本接口SN已必填，因此batteryIdentification中的productNumber、otherIdentificationElement保持可选；modelIdentifier与batchNumber仍须同时提供或同时省略。制造商名称/商号/商标至少一项，制造城市/地区至少一项，制造国家始终必填。': 'batteryIdentification.serialNumber必填；modelIdentifier与batchNumber同时填写或同时省略。制造商名称、商号、商标至少提供一项；制造城市、地区至少提供一项；制造国家代码必填。',
    '字典覆盖全部206个显式字段/数组元素节点；语言对象的动态键不计入固定字段数量，其键名及值的边界见下节。附件Excel、CSV与此表由同一模型生成。': '字段表包含206个字段及数组元素节点。多语言对象以语言标签作为动态键，键格式及文本填写规则见2.2.3。附件Excel和CSV提供相同的字段路径及填写要求。',
    '源模型和完整语义验证存在边界：部分数组未设minItems，多语言值未设string类型，部分SHACL基数/条件和国标码分段未在JSON Schema中表达。附件校验报告明确记录这些边界；Schema通过只代表结构约束通过，不代表完成全部语义或国标认证。': '结构校验检查字段类型、必填、长度、数组数量、枚举及格式。语义内容校验检查语言文本、制造年月及内容规范标识组合。业务校验检查国标编码、账号权限、批次内SN唯一性、护照状态及幂等要求。每条记录须通过三类校验后才能成功入库。',
    'contentSpecificationIds须按语义提供core、电池组、EV子组规范各一个；示例均使用本批资源版本1.0.0。完整URI保持欧盟资源域名，不改写为平台服务域名。': 'contentSpecificationIds包含core、电池组、EV子组规范URI，各一项。示例版本为1.0.0。规范URI和枚举URI使用附录中的完整取值；鉴权及SN上传请求使用battery.chdpp.com服务域名。',
    '以下18组枚举完整列出指定源模型的全部取值。相同枚举集合合并展示，并列出适用字段。URI取值必须提交完整字符串；末尾代码只用于阅读。batteryCategory同时展示源模型的3项，但本SN接口仅接受EV项。': '本附录列出本接口使用的18组枚举及对应字段。URI枚举提交完整字符串。E03仅包含EV电池分类electricVehicleBattery；其他组按表中取值提交。',
    '本示例包含全部63个EV顶层字段，可通过附件请求Schema及format校验。它由官方conforms示例扩充缺少的可选字段后生成，并替换演示标识、制造商及业务资源链接。数值仅演示结构，不代表某个真实电池的检测结果或认证数据。': '以下示例包含全部63个EV顶层字段，展示完整请求结构。接入时替换SN、主体信息、检测数值及资源地址，填报与当前电池一致的真实数据。',
    'SN占位码：TEST20261104000000000001（24字符，仅用于结构测试，不代表已登记国标码）。所有/demo链接均为演示链接，不承诺存在真实资源；欧盟枚举/规范URI维持原始域名。': '示例SN：TEST20261104000000000001，长度为24字符。该值与/demo资源地址用于演示；正式报送使用真实国标码与可访问的资源地址。',
    '验证完成：33个用例全部符合预期；完整/最小请求、SN长度与必填、EV范围、条件必填、枚举URI、日期类型、布尔值、语言键、资源及直方图约束等已检查。批量重复SN、真实国标分段与登记、SHACL语义完整性及实际接口行为仍属于业务或联调验证。': '字段与编码校验失败返回1001；批次内SN重复返回1002；护照已激活返回1003；主体配置错误返回1005或1006；记录内容冲突返回1008。HTTP状态与逐条返回结构见2.2.4。',
    '本版逐条参数失败以1001返回；凭证/权限或整个封装错误按对应HTTP状态返回。网络超时无法直接断定服务端未入库，应保留原始SN和请求内容，核对状态后重试。': '逐条参数校验失败返回1001；凭证、权限及根封装错误按对应HTTP状态返回。请求超时时，保留原始SN和请求内容，核对处理状态后按幂等规则重试。',
}
P_DELETE = [
    '参考文档的body类型表与示例不一致，本版依据示例及鉴权逻辑统一为object|null，token路径为body.token。',
    '参考文档的data类型表写为object，但失败示例为数组，本版统一为array|null；成功码与鉴权码分别按各自协议解释。',
    '原模型存在的宽松边界已记录在报告中：例如制造年月正则允许13月、语言值未声明string、部分必填数组允许空数组、跨记录重复SN不能由当前Schema判定。供应商按真实语义填报，平台需补充相应业务校验。',
    '本次未使用真实凭证，未调用登录或上传接口，未修改任何线上接口或原始语义文件。',
]
HEAD_REPLACE = {'1.3 字段来源与平台约束': '1.3 接口约束', '附录C. 来源、附件与验证说明': '附录C. 附件与校验规则'}

with zipfile.ZipFile(SOURCE) as z:
    package = {name: z.read(name) for name in z.namelist()}
root = X.fromstring(package['word/document.xml'])
body = root.find('w:body', NS)
old_code = [txt(p) for p in root.findall('.//w:p', NS) if (p.find('./w:pPr/w:pStyle', NS) is not None and p.find('./w:pPr/w:pStyle', NS).get(tag('val')) == 'SnCode')]

for p in list(body.findall('w:p', NS)):
    value = txt(p)
    if BRAND.search(value) or value in P_DELETE or value.startswith(('参考文档：https://alidocs.', '指定源文件SHA-256：')):
        CHANGES.append({'原文': value, '修订': '删除'}); body.remove(p); continue
    if value in P_REPLACE:
        set_p(p, P_REPLACE[value])
    # Update heading text in both the document and native TOC while retaining PAGEREF fields.
    for t in p.findall('.//w:t', NS):
        for old, new in HEAD_REPLACE.items():
            if old in (t.text or ''):
                CHANGES.append({'原文': t.text, '修订': t.text.replace(old, new)})
                t.text = t.text.replace(old, new)

with (BACKUP / '对接附件/EV字段字典_206节点.csv').open(encoding='utf-8-sig') as fp:
    fields = list(csv.DictReader(fp))
field_by_path = {r['字段路径（相对snList[]）']: r for r in fields}
for path, n, _, _ in NODES:
    row = field_by_path[path]
    row['长度/数量'] = length_text(row['长度/数量'])
    row['是否必填（本接口）'] = required_text(path, row['是否必填（本接口）'])
    row['约束与填写说明'] = field_description(path, n)

SOURCE_RULES = [
    ['数据范围', '每条记录包含63个EV顶层字段，其中52个顶层字段必填；字段表展开为206个字段及数组元素节点。'],
    ['请求封装', '根对象为snList，数组长度为1至100项；每项为一个EV电池对象。'],
    ['SN要求', 'batteryIdentification.serialNumber为必填字符串，长度固定为24字符，编码符合GB/T 34014—2017及第1号修改单。'],
    ['电池分类', 'batteryCategory固定为electricVehicleBattery的完整URI，见枚举E03。'],
    ['字段范围', '根对象只接收snList；每条记录只接收本规范列出的63个顶层字段。嵌套对象的成员范围和必填条件按2.2.2执行。'],
]
FILL_RULES = [
    ['SN编码', '填写24字符国标码字符串，保留前导0。平台依次校验类型、长度、编码字符、分段规则及生产企业登记信息。'],
    ['制造年月 / 国家', 'manufacturingDate采用YYYY-MM格式，月份为01至12；manufacturingCountry填写3位大写国家或辖区代码。示例：2026-11、CHN。'],
    ['多语言对象', '至少提供1个语言键，键格式为两位字母、半角连字符、两位字母；每个值为文本字符串。示例：{"zh-CN":"说明文本"}、{"en-IE":"Description"}。'],
    ['普通字符串', 'manufacturerPostalAddress使用单个字符串填写完整邮寄地址；digitalProductPassportId和uniqueProductIdentifier使用字符串填写护照标识和产品标识。'],
    ['资源对象', 'contentType与url必填；resourceTitle和language选填。contentType填写实际MIME媒体类型，url填写完整资源URI。资源文件通过url提供。'],
    ['资源媒体类型', 'contentType为开放字符串，按实际资源填写MIME类型。格式示例：application/pdf、application/json、image/png、image/jpeg、application/vnd.openxmlformats-officedocument.wordprocessingml.document。'],
    ['数值与计量单位', '计量对象同时提交unit和value。unit提交完整URI，value提交JSON数字；数值上下限按字段表执行，数值精度不设固定小数位数。powerLimit按数组提交。'],
    ['必填与空值', '必填字段须出现在请求中；字符串、数组及对象分别按字段表的长度、项数和成员要求填写。选填字段不提交时直接省略，不能使用null代替。'],
    ['运行数据', 'numberOfChargingCycles、numberOfDischargingCycles、environmentalConditionHistogramSet和stateOfChargeHistogram记录投入使用后的数据；电池投入使用前省略。其余字段的必填要求按2.2.2执行。'],
    ['批次内重复SN', '按batteryIdentification.serialNumber判断重复。同批出现相同SN的记录均返回1002。'],
    ['重复上传', '同一账号主体下，未激活SN重复提交相同内容时按幂等请求处理；未激活SN内容冲突时返回1008；已激活SN返回1003。'],
    ['权限与主体', '平台依据登录账号关联供应商身份与公司主体，按账号授权控制字段读取权限。'],
]
VALIDATION_RULES = [
    ['结构校验', '校验字段名、JSON类型、必填、长度、数组数量、枚举、URI及date-time格式。'],
    ['语义内容校验', '校验多语言值为文本、制造月份为01至12，以及contentSpecificationIds的core、电池组、EV子组规范标识组合。'],
    ['SN编码校验', '按GB/T 34014—2017及第1号修改单校验24字符国标码的字符、分段及生产企业登记信息。'],
    ['身份与权限校验', '校验apikey、登录账号、token及账号关联的供应商和公司主体授权。'],
    ['状态与幂等校验', '校验批次内SN唯一性、护照激活状态、重复请求的一致性及记录内容冲突。'],
]

field_count = 0
for table in list(body.findall('w:tbl', NS)):
    rows = table.findall('w:tr', NS)
    values = lambda row: [txt(c) for c in row.findall('w:tc', NS)]
    headers = values(rows[0])
    if len(headers) == 6 and '相对snList[]' in headers[1]:
        for tr in rows[1:]:
            cells = tr.findall('w:tc', NS); path = txt(cells[1]); row = field_by_path[path]
            set_cell(cells[3], row['长度/数量']); set_cell(cells[4], row['是否必填（本接口）']); set_cell(cells[5], row['约束与填写说明']); field_count += 1
    elif headers == ['来源', '本版采用的规则']:
        for cell, text in zip(rows[0].findall('w:tc', NS), ['约束项', '接口要求']):
            set_cell(cell, text)
        for tr, content in zip(rows[1:], SOURCE_RULES):
            for cell, text in zip(tr.findall('w:tc', NS), content):
                set_cell(cell, text)
    elif headers == ['场景', '填写或处理约定']:
        for tr, content in zip(rows[1:], FILL_RULES):
            for cell, text in zip(tr.findall('w:tc', NS), content):
                set_cell(cell, text)
    elif headers == ['资料', '来源与用途']:
        for cell, text in zip(rows[0].findall('w:tc', NS), ['校验类别', '校验规则']):
            set_cell(cell, text)
        for tr, content in zip(rows[1:], VALIDATION_RULES):
            for cell, text in zip(tr.findall('w:tc', NS), content):
                set_cell(cell, text)
    elif headers == ['序号', '完整取值', '官方名称', '符号']:
        if any(txt(c) == EV for tr in rows[1:] for c in tr.findall('w:tc', NS)):
            for tr in rows[1:]:
                if txt(tr.findall('w:tc', NS)[1]) != EV:
                    CHANGES.append({'原文': txt(tr), '修订': '删除非EV分类展示项'}); table.remove(tr)
    elif headers == ['业务码', '含义', '适用与处理建议']:
        rules = {
            '1004': ['保留码', '本接口不使用此码。'],
            '1005': ['公司主体配置不存在', '核对登录账号关联的公司主体配置。'],
            '1006': ['供应商配置不存在', '核对登录账号关联的供应商配置。'],
            '1007': ['保留码', '本接口不使用此码。'],
        }
        for tr in rows[1:]:
            cells = tr.findall('w:tc', NS)
            if txt(cells[0]) in rules:
                for cell, text in zip(cells[1:], rules[txt(cells[0])]):
                    set_cell(cell, text)
    elif headers == ['附件', '用途']:
        descriptions = {
            'SnUploadRequest_EV_24位国标码.schema.json': '批量请求的结构校验Schema，包含snList封装、EV字段、24字符SN及EV分类约束。',
            'EVBatteryPassport_Apifox直接建模.schema.json': 'EV电池护照数据结构定义。',
            'EV字段字典及枚举_206节点.xlsx': '206个字段及数组元素节点的字段字典，以及完整枚举取值。',
            '校验报告.json': '请求Schema与JSON示例的结构、格式及边界测试记录。',
        }
        for tr in rows[1:]:
            cells = tr.findall('w:tc', NS)
            if txt(cells[0]) in descriptions:
                set_cell(cells[1], descriptions[txt(cells[0])])

CELL_REPLACE = {
    '按参考文档结构编写；采用完整EV模型；SN必填且恰好24位；统一域名。': 'EV电池全量字段；SN必填且固定24字符；统一鉴权及上传服务域名。',
    'unit使用模型给出的完整URI；例如电压使用measurement-unit#volt，不直接填写V。': 'unit使用附录A列出的完整单位URI；电压单位提交measurement-unit#volt对应的完整URI。',
    '有enum时取模型列出的完整值；无enum的字段不自行补造封闭枚举。': '枚举字段严格使用附录A中的取值；字符串字段按字段表的内容与格式要求填写。',
    '字段保留PUB/RES1/RES2/RES3来源标识。全量上传不改变这些字段的对外披露权限。': 'PUB为公开信息；RES1面向合法权益人、公告机构及监管机构；RES2面向公告机构及监管机构；RES3面向对单个电池享有合法权益的主体。平台按授权控制披露。',
    'POST / 60次每分钟（沿用参考文档）': 'POST / 60次每分钟',
    '兼容参考文档字符串200与数值-7003的示例；比较时先转为字符串。': '业务状态码，类型为string或integer；调用方统一转为字符串判断。200表示鉴权成功，-7003表示鉴权失败。',
    '成功时为账号及令牌对象；失败可为null。': '认证成功时返回账号及令牌对象；认证失败时返回null。',
    '全部成功可为null；存在失败时返回逐条结果数组。': '全部成功时返回null；存在失败时返回本批全部记录的逐条结果数组。',
    '逐条错误说明；成功可为null。尽量给出字段路径与原因。': '成功时为null；失败时返回具体字段路径、错误原因及处理要求。',
    '每条为完整EV模型；分类限EV；SN必填且24字符。': '每条为一个EV电池对象；batteryCategory固定为EV；serialNumber必填且恰好24字符。',
    '数组元素约束': '—',
}
for p in root.findall('.//w:p', NS):
    value = txt(p)
    if value in CELL_REPLACE:
        set_p(p, CELL_REPLACE[value])
    for t in p.findall('.//w:t', NS):
        # Retain the three-item source vocabulary in the immutable input model;
        # client-facing E03 displays only the EV value accepted by this API.
        if (t.text or '') == 'E03 · 3项':
            t.text = 'E03 · 1项'

assert field_count == 206
new_text = txt(root)
banned = ['源Schema', '源模型', '原模型', '参考文档', '适用时填写', '含糊', '需核对兼容', '本版不虚构', '尽量', '尚未', '待确认']
assert not BRAND.search(new_text)
assert not [s for s in banned if s in new_text], [s for s in banned if s in new_text]
assert not any(c in new_text for c in ['\u200b', '\ufeff', '\u2060', '\u00ad'])
new_code = [txt(p) for p in root.findall('.//w:p', NS) if (p.find('./w:pPr/w:pStyle', NS) is not None and p.find('./w:pPr/w:pStyle', NS).get(tag('val')) == 'SnCode')]
assert old_code == new_code

# Companion dictionaries use the same definitive field wording as the Word table.
with (ATT / 'EV字段字典_206节点.csv').open('w', encoding='utf-8-sig', newline='') as fp:
    writer = csv.DictWriter(fp, fieldnames=list(fields[0])); writer.writeheader(); writer.writerows(fields)
with (BACKUP / '对接附件/枚举字典_完整取值.csv').open(encoding='utf-8-sig') as fp:
    enum_rows = list(csv.DictReader(fp))
enum_rows = [r for r in enum_rows if r['枚举编号'] != 'E03' or r['取值'] == EV]
with (ATT / '枚举字典_完整取值.csv').open('w', encoding='utf-8-sig', newline='') as fp:
    writer = csv.DictWriter(fp, fieldnames=list(enum_rows[0])); writer.writeheader(); writer.writerows(enum_rows)
workbook = load_workbook(BACKUP / '对接附件/EV字段字典及枚举_206节点.xlsx')
sheet = workbook['EV完整字段206节点']
for i, row in enumerate(fields, 2):
    for j, value in enumerate(row.values(), 1):
        sheet.cell(i, j, int(value) if j == 1 else value)
enum_sheet = workbook['完整枚举']
enum_sheet.delete_rows(2, enum_sheet.max_row)
for row in enum_rows:
    enum_sheet.append(list(row.values()))
enum_sheet.auto_filter.ref = enum_sheet.dimensions
workbook.save(ATT / 'EV字段字典及枚举_206节点.xlsx')

package['word/document.xml'] = X.tostring(root, encoding='UTF-8', xml_declaration=True, standalone=True)
# Drop the template's cached cover thumbnail, which still depicts its former company name.
for part in list(package):
    if 'thumbnail' in part.lower():
        del package[part]
relationships = X.fromstring(package['_rels/.rels'])
for relationship in list(relationships):
    if 'thumbnail' in relationship.get('Type', '').lower():
        relationships.remove(relationship)
package['_rels/.rels'] = X.tostring(relationships, encoding='UTF-8', xml_declaration=True, standalone=True)
for name in [STEM, STEM + '_模板格式修订版', STEM + '_对接规范修订版']:
    destination = HERE / (name + '.docx')
    with zipfile.ZipFile(destination.with_suffix('.tmp.docx'), 'w', zipfile.ZIP_DEFLATED) as z:
        for part, value in package.items():
            z.writestr(part, value)
    destination.with_suffix('.tmp.docx').replace(destination)
with zipfile.ZipFile(HERE / 'SN接口对接附件_EV全量_24位国标码.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for p in sorted(ATT.iterdir()):
        z.write(p, 'SN接口对接附件/' + p.name)

json_unchanged = {}
for p in ATT.glob('*.json'):
    assert p.read_bytes() == (BACKUP / '对接附件' / p.name).read_bytes(), p.name
    json_unchanged[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
report = {
    '输入文档': str(SOURCE), '输出文档': str(HERE / (STEM + '_对接规范修订版.docx')),
    '修订范围': ['移除封面企业名称', '删除来源对照与生成过程说明', '将206个字段说明改为填写规则', '明确条件必填与运行数据要求', 'E03仅展示本接口接受的EV项', '错误码1004和1007明确为本接口保留码', '同步Excel与CSV字段说明'],
    '核对': {'字段及数组节点': 206, '顶层字段': 63, '顶层必填': 52, '原JSON示例逐行一致': old_code == new_code,
             '企业名称残留': bool(BRAND.search(new_text)), '模糊来源措辞残留': [s for s in banned if s in new_text],
             '原始EV模型及请求Schema和JSON文件未改动': json_unchanged},
    '文案变更': CHANGES, '渲染核对': '待执行',
}
(HERE / '对接规范修订_核对报告.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({'fields': field_count, 'wording_changes': len(CHANGES), 'brand_removed': True,
                  'json_files_unchanged': len(json_unchanged), 'output': STEM + '_对接规范修订版.docx'}, ensure_ascii=False))
