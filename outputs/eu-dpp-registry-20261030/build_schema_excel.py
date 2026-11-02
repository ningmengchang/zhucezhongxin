from pathlib import Path
from collections import defaultdict, Counter
from urllib.parse import urljoin, urldefrag, unquote
from dataclasses import dataclass
import hashlib
import json
import math
import re
import zipfile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / '03_语义资源'
OUT = ROOT / '05_Excel阅读版'
OUT.mkdir(exist_ok=True)
FILES = sorted(ASSETS.rglob('*.schema.json'))
DOCS = {json.loads(p.read_text())['$id']: (p, json.loads(p.read_text())) for p in FILES}
assert len(DOCS) == 18
BEFORE = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in ASSETS.rglob('*') if p.is_file()}
LABELS = dict(line.split('|', 1) for line in '''digitalProductPassportId|数字产品护照标识
uniqueProductIdentifier|唯一产品标识（UPI）
granularity|护照粒度
dppStatus|护照资源状态
lastUpdate|最后更新时间
contentSpecificationIds|内容规范标识列表
registrantIdentity|护照注册／负责主体身份信息
manufacturerIdentification|制造商名称标识组合
manufacturerName|制造商名称
manufacturerTradeName|制造商注册商号
manufacturerTrademark|制造商注册商标
manufacturerPostalAddress|制造商联系邮寄地址
manufacturerContactDetails|制造商网站及邮箱
manufacturerWebsite|制造商网站
manufacturerEmail|制造商邮箱 URI
batteryCategory|电池类别
batteryIdentification|电池识别信息组合
modelIdentifier|型号标识
batchNumber|批次号
serialNumber|序列号
productNumber|产品编号
otherIdentificationElement|其他识别要素
manufacturingPlace|制造地点
manufacturingCity|制造城市
manufacturingRegion|制造地区
manufacturingCountry|制造国家代码
manufacturingDate|制造年月
batteryWeight|电池重量
batteryCapacity|电池容量
batteryChemistry|电池化学体系
hazardousSubstances|有害物质列表
extinguishingAgent|可用灭火剂说明
criticalRawMaterials|关键原材料列表
renewableContentShare|可再生含量占比
minimalVoltage|最低电压
nominalVoltage|标称电压
maximumVoltage|最高电压
originalPowerCapability|初始功率能力
powerLimit|功率限值列表
notInUseTemperatureRange|非使用状态耐受温度范围及测试
notInUseTemperatureRangeTest|非使用状态温度范围的参考测试
commercialWarrantyPeriod|商业日历寿命质保期
internalCellResistance|电芯内阻
internalPackResistance|电池包内阻
cycleLifeDischargeCRate|循环寿命测试的放电 C 倍率
separateCollectionSymbol|分类收集标识
chemicalSymbol|化学物质标识符号
euDeclarationOfConformity|欧盟符合性声明资源
wasteBatteryPreventionManagementInfo|废电池预防与管理信息
endUserRoleInWastePrevention|最终用户在废物预防中的作用
endUserRoleInSeparateCollection|最终用户在分类收集中的作用
separateCollectionPoints|分类收集点信息
takeBackCollectionPoints|回收接收点信息
preparationForReuseFacilities|准备再使用的处理设施
preparationForRepurposingFacilities|准备改变用途的处理设施
wasteBatteryTreatmentFacilities|废电池处理设施
wasteBatterySafetyInstructions|废电池安全说明
batteryLabelSymbolMeaning|电池标签符号含义
batterySubstancesImpact|电池物质的影响
detailedComposition|详细组成信息
sparePartReplacementDetails|替换备件的编号及来源信息
dismantlingInformation|拆解信息资源
safetyMeasures|安全措施资源
complianceTestReportResults|合规测试报告结果资源
roundTripEnergyEfficiency|往返能量效率
roundTripEnergyEfficiencyFade|往返能量效率衰减
batteryStatus|电池生命周期状态
numberOfChargingCycles|充电循环次数列表
numberOfDischargingCycles|放电循环次数列表
negativeEvents|负面事件说明
environmentalConditionHistogramSet|运行环境条件直方图集合
environmentalConditionHistogram|运行环境条件直方图列表
stateOfChargeHistogram|荷电状态 SOC 直方图列表
expectedLifetimeCycles|预期循环寿命
cycleLifeReferenceTest|循环寿命参考测试
capacityExhaustionThreshold|容量耗尽阈值
initialRoundTripEnergyEfficiency|初始往返能量效率
roundTripEfficiencyAtHalfCycleLife|半循环寿命时的往返能量效率
cycleLifeChargeCRate|循环寿命测试的充电 C 倍率
ratedCapacity|额定容量
capacityFade|容量衰减
powerCapability|功率能力
powerFade|功率衰减
internalResistance|内阻
internalResistanceIncrease|内阻增加
expectedLifetimeCyclesRest|参考条件下预期寿命（循环次数）
expectedLifetimeYears|参考条件下预期寿命（年）
stateOfCertifiedEnergy|认证能量状态 SOCE
remainingCapacity|剩余容量
remainingPowerCapability|剩余功率能力
remainingRoundTripEfficiency|剩余往返能量效率
selfDischargingRateSet|自放电率及记录时间
selfDischargingRate|自放电率记录列表
recordedTime|记录时间
ohmicResistance|欧姆内阻
contentType|资源内容类型
url|资源 URL
resourceTitle|资源标题
language|资源语言标签
unit|计量单位 URI
value|数值
valueRange|取值／适用条件范围
observedCharacteristic|被观测特征 URI
lowerBound|区间下界
upperBound|区间上界
binRangeUnit|直方图区间单位
binValueUnit|直方图频数／数值单位
bin|直方图区间列表
binValue|区间统计值'''.splitlines())
VIEWS = {
    'pub': 'PUB 公开',
    'res1': 'RES1 合法权益人／公告机构／成员国主管机关／欧委会',
    'res2': 'RES2 公告机构／成员国主管机关／欧委会',
    'res3': 'RES3 合法权益人（单个电池）',
}
PROFILE_NAMES = {'evb': 'EV', 'lmt': 'LMT', 'ind': '工业'}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def unique(values):
    return list(dict.fromkeys(values))


def pointer(doc, fragment):
    if not fragment: return doc
    assert fragment.startswith('/')
    value = doc
    for part in fragment[1:].split('/'):
        part = unquote(part).replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def ptr_add(ptr, *parts):
    return ptr + ''.join('/' + str(p).replace('~', '~0').replace('/', '~1') for p in parts)


@dataclass
class Part:
    uri: str
    ptr: str
    node: dict


def reference(part, ref):
    uri, fragment = urldefrag(urljoin(part.uri, ref))
    assert uri in DOCS, ref
    return Part(uri, fragment, pointer(DOCS[uri][1], fragment))


def expanded(parts, ancestors=()):
    result = []
    seen = set()
    for part in parts:
        key = (part.uri, part.ptr)
        assert key not in ancestors, ('recursive reference', key)
        if '$ref' in part.node:
            result.extend(expanded([reference(part, part.node['$ref'])], (*ancestors, key)))
        for index, node in enumerate(part.node.get('allOf', [])):
            result.extend(expanded([Part(part.uri, ptr_add(part.ptr, 'allOf', index), node)], (*ancestors, key)))
        result.append(part)
    return [p for p in result if not ((p.uri, p.ptr) in seen or seen.add((p.uri, p.ptr)))]


def properties(parts):
    props = {}
    required = set()
    for part in expanded(parts):
        required.update(part.node.get('required', []))
        for name, node in part.node.get('properties', {}).items():
            props.setdefault(name, []).append(Part(part.uri, ptr_add(part.ptr, 'properties', name), node))
    return props, required


def literals(block, predicate):
    # 本包词表概念使用普通 Turtle 字符串；保留文字，不执行 RDF 推理。
    pattern = re.escape(predicate) + r'\s+("(?:[^"\\]|\\.)*")(?:@([A-Za-z-]+))?'
    values = []
    for m in re.finditer(pattern, block):
        if not m[2] or m[2].lower() == 'en': values.append(json.loads(m[1]))
    return values


VOCABS = []
for file in sorted(ASSETS.rglob('*.ttl')):
    if '/vocabs/' not in file.as_posix(): continue
    text = file.read_text()
    prefixes = dict(re.findall(r'@prefix\s+([^\s:]+):\s*<([^>]+)>\s*\.', text))
    starts = list(re.finditer(r'(?m)^(<[^>]+>|[A-Za-z_][\w-]*:[A-Za-z_][\w-]*)\s+(?=a\s)', text))
    found = 0
    for i, match in enumerate(starts):
        block = text[match.start(): starts[i+1].start() if i+1 < len(starts) else len(text)]
        if not re.search(r'\ba\s+skos:Concept\b', block): continue
        subject = match[1]
        uri = subject[1:-1] if subject.startswith('<') else prefixes[subject.split(':', 1)[0]] + subject.split(':', 1)[1]
        notation = literals(block, 'skos:notation')
        label = literals(block, 'skos:prefLabel')
        assert label, (file, subject)
        VOCABS.append({'file': file, 'uri': uri, 'code': notation[0] if notation else uri.rsplit('#', 1)[-1], 'label': '\n'.join(label), 'alt': '\n'.join(literals(block, 'skos:altLabel')), 'definition': '\n'.join(literals(block, 'skos:definition')), 'scope': '\n'.join(literals(block, 'skos:scopeNote'))})
        found += 1
    assert found == len(re.findall(r'\ba\s+skos:Concept\b', text)), file

VOCAB_BY_NS = defaultdict(list)
MEANINGS = {}
for concept in VOCABS:
    if '#' in concept['uri']: VOCAB_BY_NS[concept['uri'].rsplit('#', 1)[0] + '#'].append(concept)
    if concept['file'].name in {'core-vocabulary.ttl', 'common-vocabulary.ttl', 'battery-vocabulary.ttl'}:
        MEANINGS[concept['uri'].rsplit('#', 1)[-1]] = concept


def namespace_refs(parts):
    namespaces = []
    for part in expanded(parts):
        for text in [part.node.get('$comment', ''), part.node.get('pattern', '')]:
            text = text.replace('\\.', '.')
            namespaces.extend(re.findall(r'https://repository\.product-passport\.ec\.europa\.eu/[A-Za-z0-9_/-]+#', text))
    return unique(namespaces)


def type_text(parts):
    parts = expanded(parts)
    types = unique(dumps(p.node['type']) for p in parts if 'type' in p.node)
    return ' ∩ '.join(t.strip('"') for t in types) if types else '未显式限定类型（见结构约束）'


def structure_rules(parts):
    rules = []
    for part in expanded(parts):
        node = part.node
        for kind in ['anyOf', 'oneOf']:
            if kind in node:
                branches = [' + '.join(branch['required']) if set(branch) == {'required'} else dumps(branch) for branch in node[kind]]
                rules.append(('至少满足一组：' if kind == 'anyOf' else '恰好满足一组：') + ' 或 '.join('[' + branch + ']' for branch in branches))
        for key, dependents in node.get('dependentRequired', {}).items():
            rules.append('出现 ' + key + ' 时必须包含 ' + ', '.join(dependents))
        for key in ['additionalProperties', 'unevaluatedProperties', 'propertyNames']:
            if key in node: rules.append(key + '=' + dumps(node[key]))
    return '\n'.join(unique(rules))


def constraints(parts):
    keys = ['format', 'pattern', 'minLength', 'maxLength', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'minItems', 'maxItems', 'uniqueItems', 'minProperties', 'maxProperties', 'enum', 'const']
    values = []
    for part in expanded(parts):
        values.extend(key + '=' + dumps(part.node[key]) for key in keys if key in part.node)
    return '\n'.join(unique(values))


def combined_required_names(parts):
    names = set()
    for part in expanded(parts):
        for kind in ['anyOf', 'oneOf']:
            for branch in part.node.get(kind, []): names.update(branch.get('required', []))
        for key, dependencies in part.node.get('dependentRequired', {}).items():
            names.add(key)
            names.update(dependencies)
    return names


MISSING = object()
PROPERTY_LOCATIONS = set()
PROFILE_ROWS = {}
SAMPLE_RESULTS = []
FIELD_META = {}
registry = Registry()
for uri, (_, doc) in DOCS.items():
    Draft202012Validator.check_schema(doc)
    registry = registry.with_resource(uri, Resource.from_contents(doc, default_specification=DRAFT202012))


def readable_sample(value):
    if value is MISSING: return '官方对应样例未提供此字段值'
    text = dumps(value)
    return text if len(text) <= 750 else '对象／数组较长，完整值见下方样例来源（' + str(len(text)) + ' 字符）'


def walk(parts, view, path, name, parent_path, required, conditional, sample, sample_source, row_kind='字段', parent_rules='', trail=(), depth=0):
    assert depth < 20
    effective = expanded(parts)
    for part in parts:
        if '/properties/' in part.ptr: PROPERTY_LOCATIONS.add((part.uri, part.ptr))
    if row_kind == '数组元素':
        req = '数组元素；由 items 规则约束'
    elif required:
        req = '是（所在父对象存在时）'
    elif conditional:
        req = '未列 required；受组合／依赖条件约束'
    else:
        req = '未列 required（不等于法规可选）'
    condition = '在该访问视图对象内' if parent_path == '$' else '父结构 ' + parent_path + ' 存在时适用'
    if trail: condition += '；祖先条件：' + ' → '.join(trail)
    notes = []
    if name == 'manufacturerEmail': notes.append('原文 format=uri，官方样例使用 mailto:，不是 email 格式关键字。')
    if name == 'lastUpdate': notes.append('字段拼写为 lastUpdate。')
    if name == 'batteryIdentification': notes.append('型号与批次在本版 Schema 中成对依赖；不可仅按指南摘要设为任选一项。')
    if row_kind == '数组元素': notes.append('路径中的 [] 为本表表示数组元素的记法，不是实际 JSON 字段名。')
    ns = namespace_refs(parts)
    vocab_text = []
    for prefix in ns:
        concepts = VOCAB_BY_NS.get(prefix, [])
        vocab_text.append(prefix + '\n词表参考值：' + '；'.join(c['code'] for c in concepts))
        notes.append('词表参考值来自 TTL；pattern 只校验 URI 形状，不等同 Schema 中声明 enum。')
    own_rules = structure_rules(parts)
    rules = '\n'.join(filter(None, ['本字段结构：\n' + own_rules if own_rules else '', '父级组合：\n' + parent_rules if conditional else '']))
    locations = unique(str(DOCS[p.uri][0].relative_to(ASSETS)) + '#' + p.ptr for p in parts)
    refs = unique(p.node['$ref'] for p in parts if '$ref' in p.node)
    meaning = MEANINGS.get(name, {})
    row = [view, path, LABELS[name] + ('（数组元素）' if row_kind == '数组元素' else ''), type_text(parts), req, condition, constraints(parts), '\n\n'.join(vocab_text), rules, readable_sample(sample), meaning.get('definition', '') or meaning.get('label', ''), '\n'.join(locations), '\n'.join(refs), '\n'.join(unique(notes)), sample_source + '#' + path]
    FIELD_META[id(row)] = {
        'name': name, 'required': required, 'conditional': conditional,
        'row_kind': row_kind, 'trail': trail, 'parts': effective,
        'sample': sample, 'sample_missing': sample is MISSING,
        'parent_path': parent_path,
    }
    rows = [row]
    children, required_names = properties(parts)
    combo_names = combined_required_names(parts)
    next_trail = trail
    if row_kind != '数组元素' and not required: next_trail = (*trail, path + ' 非无条件 required')
    for key, child_parts in children.items():
        value = sample.get(key, MISSING) if isinstance(sample, dict) else MISSING
        rows.extend(walk(child_parts, view, path + '.' + key, key, path, key in required_names, key in combo_names, value, sample_source, parent_rules=own_rules, trail=next_trail, depth=depth+1))
    items = [Part(p.uri, ptr_add(p.ptr, 'items'), p.node['items']) for p in effective if isinstance(p.node.get('items'), dict)]
    if items:
        value = sample[0] if isinstance(sample, list) and sample else MISSING
        rows.extend(walk(items, view, path + '[]', name, path, False, False, value, sample_source, row_kind='数组元素', trail=next_trail, depth=depth+1))
    return rows


for subgroup in PROFILE_NAMES:
    all_rows = []
    for view, label in VIEWS.items():
        schema_file = ASSETS / f'product/batteries/subgroup/{subgroup}/1.0.0/schemas/{view}-{subgroup}.schema.json'
        doc = json.loads(schema_file.read_text())
        origin = Part(doc['$id'], '', doc)
        props, required = properties([origin])
        sample_file = ASSETS / f'product/batteries/subgroup/{subgroup}/1.0.0/tests/conforms/{view}-batteries-{subgroup}.json'
        sample = json.loads(sample_file.read_text())
        errors = list(Draft202012Validator(doc, registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER).iter_errors(sample))
        SAMPLE_RESULTS.append([PROFILE_NAMES[subgroup], label, str(sample_file.relative_to(ASSETS)), '通过' if not errors else '发现问题，原文保留', '\n'.join('$.' + '.'.join(map(str,e.absolute_path)) + ': ' + e.message for e in errors)])
        combo_names = combined_required_names([origin])
        parent_rules = structure_rules([origin])
        for key, parts in props.items():
            all_rows.extend(walk(parts, label, '$.' + key, key, '$', key in required, key in combo_names, sample.get(key, MISSING), str(sample_file.relative_to(ASSETS)), parent_rules=parent_rules))
    assert len({(r[0], r[1]) for r in all_rows}) == len(all_rows)
    PROFILE_ROWS[subgroup] = all_rows

common_uri = next(uri for uri in DOCS if uri.endswith('/common.schema.json'))
COMMON_ROWS = []
for key, definition in DOCS[common_uri][1]['$defs'].items():
    part = Part(common_uri, ptr_add('', '$defs', key), definition)
    props, required = properties([part])
    for name, parts in props.items():
        COMMON_ROWS.extend(walk(parts, '$defs/' + key, '$defs.' + key + '.' + name, name, '$defs.' + key, name in required, False, MISSING, 'core/1.0.0/schemas/common.schema.json', parent_rules=structure_rules([part])))

raw_locations = set()
constraint_rows = []
raw_refs = []
KEYWORDS = {'required', 'anyOf', 'oneOf', 'allOf', 'dependentRequired', 'additionalProperties', 'unevaluatedProperties', 'propertyNames', 'minItems', 'maxItems', 'minProperties'}
def scan(uri, node, ptr=''):
    if isinstance(node, dict):
        for name in node.get('properties', {}): raw_locations.add((uri, ptr_add(ptr, 'properties', name)))
        if '$ref' in node:
            raw_refs.append((uri, ptr, node['$ref']))
            reference(Part(uri, ptr, node), node['$ref'])
        for key, value in node.items():
            if key in KEYWORDS:
                constraint_rows.append([str(DOCS[uri][0].relative_to(ASSETS)), ptr or '/', key, dumps(value), '多个 allOf 约束同时生效；required 仅约束当前对象；组合与依赖条件须共同判断。'])
            scan(uri, value, ptr_add(ptr, key))
    elif isinstance(node, list):
        for index, value in enumerate(node): scan(uri, value, ptr_add(ptr, index))
for uri, (_, doc) in DOCS.items(): scan(uri, doc)
assert raw_locations <= PROPERTY_LOCATIONS, ('missing properties', raw_locations - PROPERTY_LOCATIONS)
assert all(name in LABELS for name in {ptr.rsplit('/',1)[-1] for _,ptr in raw_locations})


from schema_excel_output import build
build(globals())
