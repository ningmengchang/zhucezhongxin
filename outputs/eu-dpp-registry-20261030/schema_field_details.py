"""Cross-reference the fixed 2026-10-30 semantic package for its Excel reading copy.

Requires rdflib, openpyxl and the generator's existing JSON Schema dependencies.
SHACL is read as RDF, including references, lists, alternatives and property paths.
This module extracts evidence; it does not claim to execute a full SHACL validation.
"""
from collections import defaultdict
from dataclasses import dataclass
import json
import re

from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef

SH = Namespace('http://www.w3.org/ns/shacl#')
SKOS = Namespace('http://www.w3.org/2004/02/skos/core#')
UNIT_NS = 'https://repository.product-passport.ec.europa.eu/core/vocabs/measurement-unit#'

HEADERS = [
    '访问视图', '完整字段路径', '中文含义（内部解释）',
    '是否必填（综合阅读结论）', '业务适用条件（词表／组合）',
    '计量单位／统计口径', '单位提交方式及依据', 'JSON类型',
    '填写格式与说明（中文）', '格式／范围／长度约束（Schema）',
    'JSON Schema必填状态', 'SHACL数量要求（RDF值）',
    '完整路径的存在条件', '受控取值与词表参考',
    '结构与组合／依赖规则', '官方样例值', '核对结果／差异说明',
    '官方词汇解释（英文原文）', '业务适用说明scopeNote（原文）',
    '字段定义来源及JSON Pointer', 'SHACL与词表依据',
    '结构引用 $ref', '注意事项', '样例来源及路径', '语义校验消息代码',
]
WIDTHS = [18, 61, 31, 33, 54, 39, 70, 19, 74, 66, 42, 62, 64,
          78, 85, 75, 90, 83, 105, 95, 100, 95, 85, 95, 35]

UNIT_LABELS = {
    'kilogram': '千克（kg）', 'gram': '克（g）',
    'ampereHour': '安时（Ah）', 'milliampereHour': '毫安时（mAh）',
    'percent': '百分比（%）', 'volt': '伏特（V）',
    'degreeCelsius': '摄氏度（°C）', 'watt': '瓦特（W）',
    'ohm': '欧姆（Ω）', 'reciprocalHour': '每小时（h⁻¹，C 倍率）',
    'hour': '小时（h）',
}
SCALAR_UNITS = {
    'commercialWarrantyPeriod': ('月', '词表定义固定为 months；直接提交整数月数，不另传 unit。'),
    'expectedLifetimeCycles': ('循环次数（次）', '词表定义为以循环次数表达的寿命；直接提交整数。'),
    'expectedLifetimeCyclesRest': ('循环次数（次）', '参考条件下预期寿命的循环次数；直接提交整数。'),
    'expectedLifetimeYears': ('年', '词表定义固定为 years；直接提交整数年数，不另传 unit。'),
    'numberOfChargingCycles': ('充电循环次数（次）', '数组内每个整数表示次数；不另传 unit。'),
    'numberOfDischargingCycles': ('放电循环次数（次）', '数组内每个整数表示次数；不另传 unit。'),
}

NAME_MEMBERS = {'manufacturerName', 'manufacturerTradeName', 'manufacturerTrademark'}
ID_MEMBERS = {'modelIdentifier', 'batchNumber', 'serialNumber', 'productNumber', 'otherIdentificationElement'}
PLACE_MEMBERS = {'manufacturingCity', 'manufacturingRegion'}
CONTACT_MEMBERS = {'manufacturerWebsite', 'manufacturerEmail'}
IND_RECHARGEABLE = {
    'ratedCapacity', 'capacityFade', 'powerCapability', 'powerFade',
    'internalResistance', 'internalResistanceIncrease', 'expectedLifetimeYears',
}
IND_BMS = {
    'remainingCapacity', 'remainingPowerCapability', 'remainingRoundTripEfficiency',
    'selfDischargingRateSet', 'selfDischargingRate', 'ohmicResistance',
}
IND_SOME = {'initialRoundTripEnergyEfficiency', 'roundTripEfficiencyAtHalfCycleLife', 'cycleLifeChargeCRate'}
IND_CYCLES = {'expectedLifetimeCycles', 'cycleLifeReferenceTest'}
IN_USE = {'numberOfChargingCycles', 'numberOfDischargingCycles', 'negativeEvents',
          'environmentalConditionHistogramSet', 'environmentalConditionHistogram', 'stateOfChargeHistogram'}
GENERIC_NAMES = {'unit', 'value', 'valueRange', 'observedCharacteristic', 'lowerBound',
                 'upperBound', 'binRangeUnit', 'binValueUnit', 'bin', 'binValue',
                 'url', 'contentType', 'resourceTitle', 'language', 'recordedTime'}


def distinct(xs):
    return list(dict.fromkeys(xs))


def local(uri):
    return str(uri).rsplit('#', 1)[-1].rsplit('/', 1)[-1]


def norm(path):
    """JSON arrays become repeated RDF values; keep the original JSON path elsewhere."""
    return path.replace('[]', '')


def jdump(x):
    return json.dumps(x, ensure_ascii=False, separators=(',', ':'))


@dataclass(frozen=True)
class Fact:
    path: str
    predicate: str
    value: str
    branch: str
    source: str
    shape: str


class ShapeIndex:
    def __init__(self, assets):
        self.assets = assets
        self.graph = Graph()
        self.graphs = {}
        self.subject_sources = defaultdict(set)
        self.raw_rows = []
        self.raw_properties = 0
        self.cache = {}
        self.lists = {}
        for file in sorted(assets.rglob('*.ttl')):
            g = Graph().parse(file, format='turtle')
            if '/shapes/' not in file.as_posix():
                continue
            relative = file.relative_to(assets).as_posix()
            self.graphs[relative] = g
            for t in g:
                self.graph.add(t)
                self.subject_sources[t[0]].add(relative)
            # Use a stable source + owning named shape + ordinal instead of random BNode IDs.
            for owner in sorted(set(s for s in g.subjects() if isinstance(s, URIRef)), key=str):
                if not any(str(p).startswith(str(SH)) for p, _ in g.predicate_objects(owner)):
                    continue
                props = list(g.objects(owner, SH.property))
                if props:
                    root_only = Graph()
                    for triple in g:
                        if triple[0] == owner and triple[1] == SH.property:
                            continue
                        root_only.add(triple)
                    self.raw_rows.append([
                        relative, str(owner) + '\n[形状根规则，属性规则分行列出]', self.describe(owner, root_only),
                        'RDF解析摘录；每个property单独一行；完整执行需配合上下文与词表。',
                    ])
                    for prop in sorted(props, key=lambda n: self.describe(n, g)):
                        path = g.value(prop, SH.path)
                        self.raw_rows.append([
                            relative, str(owner) + '\n[property path=' + (str(path) if isinstance(path, URIRef) else self.describe(path, g)) + ']',
                            self.describe(prop, g),
                            'RDF属性值数量；不是JSON对象个数。or分支／qualified值子集须按原始结构解释。',
                        ])
                else:
                    self.raw_rows.append([
                        relative, str(owner), self.describe(owner, g),
                        '原始 SHACL 图结构摘录；RDF 数量不直接等于 JSON 数组长度；完整执行需配合上下文与词表。',
                    ])
            self.raw_properties += len(list(g.triples((None, SH.path, None))))

    def list_items(self, node, graph=None):
        g = graph or self.graph
        return list(g.items(node))

    def describe(self, node, graph=None, seen=()):
        g = graph or self.graph
        if isinstance(node, Literal):
            return node.n3()
        if isinstance(node, URIRef):
            if node in seen or not any(g.predicate_objects(node)):
                return str(node)
        if node in seen:
            return '(循环引用)'
        pairs = []
        for predicate, value in sorted(g.predicate_objects(node), key=lambda t: (str(t[0]), str(t[1]) if not isinstance(t[1], BNode) else '')):
            if predicate == RDF.type:
                pairs.append('a ' + str(value))
            elif predicate in {SH['and'], SH['or'], SH.xone, SH.alternativePath} or (isinstance(value, BNode) and (value, RDF.first, None) in g):
                pairs.append(local(predicate) + ' (' + ' ; '.join(self.describe(x, g, (*seen, node)) for x in g.items(value)) + ')')
            elif isinstance(value, BNode):
                pairs.append(local(predicate) + ' [' + self.describe(value, g, (*seen, node)) + ']')
            else:
                pairs.append(local(predicate) + ' ' + (value.n3() if isinstance(value, Literal) else str(value)))
        return '\n'.join(pairs)

    def path_options(self, node):
        if isinstance(node, URIRef):
            return [(local(node),)]
        if (node, RDF.first, None) in self.graph:
            elements = self.list_items(node)
            if all(isinstance(x, URIRef) for x in elements):
                return [tuple(local(x) for x in elements)]
        alt = self.graph.value(node, SH.alternativePath)
        if alt is not None:
            return [p for item in self.list_items(alt) for p in self.path_options(item)]
        return []

    def profile(self, subgroup, view):
        key = (subgroup, view)
        if key in self.cache:
            return self.cache[key]
        files = [f'product/batteries/1.0.0/shapes/{view}-batteries-shapes.ttl',
                 f'product/batteries/subgroup/{subgroup}/1.0.0/shapes/{view}-{subgroup}-shapes.ttl']
        if view == 'pub':
            files.insert(0, 'core/1.0.0/shapes/core-shapes.ttl')
        roots = []
        for file in files:
            g = self.graphs[file]
            roots.extend((root, file) for root in sorted(set(g.subjects(SH.targetClass, None)), key=str))
        result = self.extract(roots)
        self.cache[key] = result
        return result

    def common(self, def_name):
        names = {
            'relatedResource': 'resource-shapes', 'unitValue': None,
            'unitValueWithRange': None, 'valueRange': None, 'histogram': 'histogram-shape',
        }
        ns = 'https://repository.product-passport.ec.europa.eu/core/shapes/'
        roots = []
        if def_name == 'relatedResource':
            roots = [(URIRef(ns + names[def_name]), 'core/1.0.0/shapes/core-shapes.ttl')]
        elif def_name == 'histogram':
            roots = [(URIRef(ns + 'histogram-shape'), 'core/1.0.0/shapes/common-shapes.ttl')]
        elif def_name in {'unitValue', 'unitValueWithRange', 'valueRange'}:
            group = ['unit-shape', 'value-shape'] if def_name != 'valueRange' else ['observed-characteristic-shape', 'lower-bound-shape', 'upper-bound-shape', 'unit-shape']
            if def_name == 'unitValueWithRange':
                group += ['value-range-shape']
            roots = [(URIRef(ns + name), 'core/1.0.0/shapes/common-shapes.ttl') for name in group]
        return self.extract(roots)

    def extract(self, roots):
        facts = defaultdict(list)
        g = self.graph
        scalar_predicates = [SH.minCount, SH.maxCount, SH.datatype, SH.nodeKind,
                             SH.hasValue, SH.minInclusive, SH.maxInclusive, SH.pattern,
                             SH.qualifiedMinCount, SH.qualifiedMaxCount, SH.message]

        def walk(node, focus='$', branch='', trail=(), owner='', inherited_source=''):
            if node in trail:
                return
            source = '\n'.join(sorted(self.subject_sources.get(node, {inherited_source})))
            if isinstance(node, URIRef):
                owner = str(node)
            path_node = g.value(node, SH.path)
            paths = [focus]
            if path_node is not None:
                paths = [focus + ''.join('.' + name for name in p) for p in self.path_options(path_node)]
            for path in paths:
                for predicate in scalar_predicates:
                    for value in g.objects(node, predicate):
                        facts[path].append(Fact(path, local(predicate), str(value), branch, source, owner))
                for predicate in [SH['or'], SH.xone, SH['and']]:
                    for head in g.objects(node, predicate):
                        items = self.list_items(head)
                        if predicate != SH['and']:
                            facts[path].append(Fact(path, 'combination',
                                local(predicate) + ': ' + ' 或 '.join(self.describe(item) for item in items),
                                branch, source, owner))
                        for i, item in enumerate(items, 1):
                            mode = branch
                            if predicate != SH['and']:
                                mode = '/'.join(filter(None, [branch, f'{local(predicate)}分支{i}']))
                            walk(item, path, mode, (*trail, node), owner, source)
                for prop in g.objects(node, SH.property):
                    walk(prop, path, branch, (*trail, node), owner, source)
                for target in g.objects(node, SH.node):
                    walk(target, path, branch, (*trail, node), owner, source)
                for target in g.objects(node, SH.qualifiedValueShape):
                    walk(target, path, '/'.join(filter(None, [branch, 'qualified值子集'])), (*trail, node), owner, source)
                for target in g.objects(node, SH['not']):
                    walk(target, path, '/'.join(filter(None, [branch, 'not分支'])), (*trail, node), owner, source)
                for constraint in g.objects(node, SH.sparql):
                    for query in g.objects(constraint, SH.select):
                        facts[path].append(Fact(path, 'sparql', str(query), branch, source, owner))

        for root, file in roots:
            walk(root, inherited_source=file)
        return {k: distinct(v) for k, v in facts.items()}


def facts_at(index, path, predicate=None, unconditional=False):
    return [f for f in index.get(norm(path), []) if (predicate is None or f.predicate == predicate)
            and (not unconditional or not f.branch)]


def bound(index, path, predicate):
    values = [int(f.value) for f in facts_at(index, path, predicate, True)]
    return (max(values) if predicate in {'minCount', 'qualifiedMinCount'} else min(values)) if values else None


def scope_piece(scope, subgroup):
    label = {'evb': 'EV', 'lmt': 'LMT', 'ind': 'Industrial'}[subgroup]
    pattern = r'\b' + label + r':\s*(.*?)(?=,\s*(?:EV|LMT|Industrial):|$)'
    m = re.search(pattern, scope, flags=re.S)
    return m[1].strip().rstrip('.') if m else ''


def translate_scope(piece, name, subgroup):
    if name in NAME_MEMBERS:
        return '制造商名称／注册商号／注册商标至少一项；该子字段不逐项强制。'
    if name in ID_MEMBERS:
        return '“型号＋批次号”成对，或序列号／产品编号／其他识别要素至少一组；该子字段不逐项强制。'
    if name in PLACE_MEMBERS:
        return '制造城市／制造地区至少一项；国家代码另为必填。'
    if name == 'commercialWarrantyPeriod':
        return '有商业质保时适用；词表未将 0 定义为“无质保”或“终身质保”。'
    if name == 'chemicalSymbol':
        return '适用镉或铅标识要求时填写。'
    if name == 'manufacturerContactDetails':
        return '有资料时填写；对象可省略，提供对象时网站／邮箱至少一项。'
    if name in CONTACT_MEMBERS:
        return '父对象提供时，网站／邮箱至少一项；不要求两项同时提供。'
    if name in IN_USE:
        return '电池开始使用后适用；静态 Schema 未自动判断是否已使用。'
    if 'Not to be filled/displayed' in piece:
        return '本电池类别不填写／不展示。'
    if subgroup == 'ind':
        if name in IND_RECHARGEABLE:
            return '仅可充电工业电池适用；电池状态变化时可能需要更新。'
        if name == 'expectedLifetimeCyclesRest':
            return '仅可充电且属于循环应用的工业电池适用；电池状态变化时可能需要更新。'
        if name in IND_BMS:
            return '仅配有电池管理系统（BMS）的固定式电池储能系统适用；原定义注明“where possible”的指标还需结合是否可提供判断。'
        if name in IND_CYCLES:
            return '仅部分可用循环次数表达寿命的工业电池适用。'
        if name in IND_SOME:
            return '仅部分工业电池适用；本词表未进一步给出可程序化的判定条件。'
    if piece.startswith('Mandatory') or piece.startswith('Same as data point 11'):
        return '必填（该电池类别／当前访问视图）。'
    if piece.startswith('Optional'):
        return '可选；有相应资料时填写。'
    if 'If applicable' in piece or 'if applicable' in piece:
        if subgroup == 'ind':
            return '仅可充电工业电池适用，且需满足该指标的适用条件。'
        return '适用时填写；本词表未给出统一的适用条件判断算法。'
    return '官方词表未单列业务必填条件；按当前对象的 Schema、组合规则与 SHACL 判断。'


def numeric_constraints(parts):
    result = defaultdict(list)
    for p in parts:
        for key, value in p.node.items():
            if key in {'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'minItems', 'maxItems', 'minLength', 'maxLength', 'format', 'pattern', 'type', 'const', 'enum'}:
                result[key].append(value)
    return dict(result)


def human_format(row, meta, measure, index):
    name, path, typ = meta['name'], row[1], row[3]
    nodes = meta['parts']
    c = numeric_constraints(nodes)
    refs = row[12]
    result = []
    if '/$defs/multiLanguage' in refs:
        result += ['多语言 JSON 对象：语言标签作为键，文本作为值，例如 {"en-IE":"..."}。',
                   '至少一个语言键；键形如 xx-XX。Schema 未单独给动态键的值声明 string；SHACL 要求带语言标签的文本。']
    elif typ == 'integer':
        result.append('JSON 整数；直接填写数值。')
    elif typ == 'number':
        result.append('JSON 数值，允许小数；计量口径见单位列。')
    elif typ == 'boolean':
        result.append('JSON 布尔值 true／false。')
    elif typ == 'array':
        result.append('JSON 数组；逐元素结构已在 [] 子行展开。')
    elif typ == 'object':
        keys = distinct(k for part in nodes for k in part.node.get('properties', {}))
        result.append('JSON 对象；' + ('成员：' + '、'.join(keys) + '。' if keys else '结构见嵌套字段或动态语言键。'))
    else:
        result.append('JSON 字符串。')

    if name == 'commercialWarrantyPeriod':
        result += ['直接填写质保月数，如 24 表示 24 个月；8 年换算为 96。', '无商业质保而不适用时可省略；不是日期或时长字符串。']
    elif name == 'expectedLifetimeYears':
        result.append('直接填写年数；官方 Schema 采用整数，不支持分数年。')
    elif name == 'manufacturingDate':
        result.append('年－月字符串 YYYY-MM，如 "2026-10"；Schema 正则只约束外形，SHACL xsd:gYearMonth 再约束真实年月。')
    elif name in {'lastUpdate', 'recordedTime'}:
        result.append('date-time 字符串，包含日期、时间和时区；例如官方样例 "2025-08-22T03:12:00Z"。')
    elif name == 'manufacturingCountry':
        result.append('3 位大写字母国家／司法辖区代码；本版仅显式约束 ^[A-Z]{3}$，未附完整国家代码 enum。')
    elif name == 'manufacturerEmail':
        result.append('URI 字符串；官方样例采用 mailto: 前缀。')
    elif name in {'manufacturerWebsite', 'url'}:
        result.append('URI 字符串，Schema format=uri；实际可访问性和受控 HTTPS 策略需结合接口指南。')
    elif name == 'uniqueProductIdentifier':
        result.append('本语义 Schema 为 string；注册 OpenAPI 另要求完整 HTTPS UPI URL，可被注册中心读取。')
    elif name == 'digitalProductPassportId':
        result.append('DPP 实例标识字符串；官方样例为 URL；本语义 Schema 未单独增加 URI 格式关键字。')
    elif name in {'contentSpecificationIds', 'granularity', 'dppStatus', 'batteryCategory', 'batteryChemistry', 'criticalRawMaterials', 'chemicalSymbol', 'batteryStatus'}:
        result.append('受控值使用完整 URI／规范标识；参考允许值列，不能只传中文名称或 URI 末尾代码。')
    elif name in {'unit', 'binRangeUnit', 'binValueUnit'}:
        result.append('单位字段提交完整 measurement-unit 概念 URI；具体允许的单位见本行单位列。')
    elif name == 'contentType':
        result.append('媒体类型字符串；按实际资源填写，例如官方样例的 application/pdf。')
    elif name == 'language':
        result.append('资源语言标签字符串，形如 "en-IE"；本版正则要求两字母－两字母。')
    elif name == 'observedCharacteristic':
        result.append('被观测特征的完整 URI；温度范围的官方样例为 observed-characteristic#temperature。')
    elif name == 'separateCollectionSymbol':
        result.append('Schema 只声明 boolean；SHACL hasValue=true 要求该标志为 true。')
    elif name in {'selfDischargingRate', 'selfDischargingRateSet'}:
        result.append('词表口径为每年下降百分比（%/年）；JSON 单位 URI 为 percent，并以 recordedTime 记录时间。')
    if typ in {'integer', 'number'}:
        lower = c.get('minimum', c.get('exclusiveMinimum'))
        upper = c.get('maximum', c.get('exclusiveMaximum'))
        result.append(('数值下限：' + '、'.join(str(x) for x in lower) if lower else 'Schema 未设置数值下限') + '；' + ('上限：' + '、'.join(str(x) for x in upper) if upper else '未设置数值上限') + '。')
    if '百分比' in measure and typ in {'integer', 'number', 'object'}:
        result.append('百分比单位本身不代表 Schema 已规定 0～100；数值边界以实际约束列为准。')
    if not meta['required'] and meta['row_kind'] != '数组元素':
        result.append('是否可省略需结合适用条件与组合判断；提供字段时仍须满足所列类型，本版未声明接受 null。')
    return '\n'.join(result)


def unit_details(row, meta, index, sample_by_path):
    path, name, typ = norm(row[1]), meta['name'], row[3]
    source = []
    if name in SCALAR_UNITS:
        return (*SCALAR_UNITS[name], [])
    parent = path.rsplit('.', 1)[0]

    def allowed(key):
        fs = [f for f in facts_at(index, key, 'hasValue') if f.value.startswith(UNIT_NS) and 'not分支' not in f.branch]
        return distinct(f.value for f in fs), fs

    if name in {'unit', 'binRangeUnit', 'binValueUnit'}:
        unit_path = path
    elif name in {'value', 'lowerBound', 'upperBound'}:
        unit_path = parent + '.unit'
        if '.bin.' in path:
            unit_path = parent.rsplit('.bin', 1)[0] + '.binRangeUnit'
    elif name == 'binValue':
        unit_path = parent.rsplit('.bin', 1)[0] + '.binValueUnit'
    else:
        unit_path = path + '.unit'
    values, fs = allowed(unit_path)
    source += fs
    unit_labels = ' 或 '.join(UNIT_LABELS.get(local(v), local(v)) for v in values)
    if values:
        basis = 'SHACL ' + ('固定单位' if len(values) == 1 else '允许单位') + '；'
        basis += 'unit 提交完整 URI：\n' + '\n'.join(values)
        if name == 'selfDischargingRate' or '.selfDischargingRate.' in path:
            unit_labels += '；语义口径为每年下降百分比（%/年）'
            basis += '\n%/年口径来自词表定义，JSON 不另设 perYear 单位 URI。'
        range_units, range_fs = allowed(path + '.valueRange.unit')
        if range_units:
            unit_labels += '\n条件范围单位：' + ' 或 '.join(UNIT_LABELS.get(local(v), local(v)) for v in range_units)
            basis += '\nvalueRange.unit：\n' + '\n'.join(range_units)
            source += range_fs
        return unit_labels, basis, source
    if name == 'selfDischargingRateSet':
        return '自放电率：%/年；另带记录时间', '组合对象；单位写在 selfDischargingRate[].unit，recordedTime 为 date-time。', source
    if name in {'lowerBound', 'upperBound', 'binValue'} and '.bin.' in path:
        field = 'binValueUnit' if name == 'binValue' else 'binRangeUnit'
        sample = sample_by_path.get(unit_path)
        hint = '\n官方示例：' + UNIT_LABELS.get(local(sample), str(sample)) if isinstance(sample, str) else ''
        return '由所属直方图的 ' + field + ' 指定' + hint, '边界与统计值采用各自的兄弟单位字段；SHACL 未固定唯一单位。' + (('\n示例 URI：' + sample) if isinstance(sample, str) else ''), source
    if name in {'binRangeUnit', 'binValueUnit'}:
        sample = sample_by_path.get(path)
        label = UNIT_LABELS.get(local(sample), str(sample)) if isinstance(sample, str) else ''
        return '按实际统计口径选择' + ('；官方示例为' + label if label else ''), '提交完整单位 URI；通用词表有 11 种单位，未在此字段固定为某一种。' + (('\n示例 URI：' + sample) if isinstance(sample, str) else ''), source
    if '.valueRange' in path or name == 'notInUseTemperatureRange':
        if name in {'valueRange', 'notInUseTemperatureRange', 'unit', 'lowerBound', 'upperBound'}:
            if 'notInUseTemperatureRange' in path:
                return '摄氏度（°C，词表语义）', '词表规定耐受温度口径为 °C；SHACL 此处未固定 degreeCelsius，实际单位仍在 valueRange.unit 中提交。', source
            if name == 'valueRange' or name in {'unit', 'lowerBound', 'upperBound'}:
                return '由 valueRange.unit 指定', '范围上下界共用其 unit；通用结构未为具体指标固定计量单位。', source
    if name in {'stateOfChargeHistogram', 'environmentalConditionHistogram', 'environmentalConditionHistogramSet', 'bin'}:
        return '按 binRangeUnit 与 binValueUnit 分别确定', '直方图区间边界与统计值分别带单位；SOC 的词表口径为 %，官方示例的统计值单位为 hour；示例不构成通用固定单位。', source
    if name == 'unit':
        return '由具体业务指标决定（单位 URI）', '通用 unitValue／valueRange 本身只限定单位 URI 外形；具体单位须看引用方 SHACL。', source
    if name in {'value', 'lowerBound', 'upperBound', 'binValue'}:
        return '由所在结构的 unit 或相应单位字段指定', '数字本身不包含单位文字；通用定义没有固定单位。', source
    if typ in {'integer', 'number'}:
        return '官方未单列计量单位', '未从该字段词表或可达 SHACL 中找到固定单位；不依据字段名臆造。', source
    if name in {'manufacturingDate', 'lastUpdate', 'recordedTime'}:
        return '不适用（日期／时间）', '按日期／时间字符串格式填写；不是时间长度数值。', source
    if typ in {'object', 'array'}:
        return '组合／列表字段；详见子字段', '该行描述结构本身，子字段各自按对应单位或格式填写。', source
    return '不适用（非计量字段）', '按字符串、受控 URI、文本或布尔格式填写。', source


def quantity_text(index, path, typ, row_kind):
    fs = facts_at(index, path)
    if not fs:
        return '未找到该 JSON 路径对应的可达 SHACL 数量规则。'
    lo, hi = bound(index, path, 'minCount'), bound(index, path, 'maxCount')
    qlo, qhi = bound(index, path, 'qualifiedMinCount'), bound(index, path, 'qualifiedMaxCount')
    result = []
    if lo is not None or hi is not None:
        result.append(f'minCount={lo if lo is not None else "未声明"}；maxCount={hi if hi is not None else "未声明"}。')
    elif qlo is None:
        result.append('本路径未声明 minCount／maxCount。')
    if qlo is not None:
        result.append(f'qualifiedMinCount={qlo}；qualifiedMaxCount={qhi if qhi is not None else "未声明"}，仅约束满足特定模式的值子集。')
    if any(f.branch and f.predicate in {'minCount', 'maxCount'} for f in fs):
        result.append('另有组合分支中的数量条件；不将分支的 minCount 当作全局必填。')
    if typ == 'array' or row_kind == '数组元素':
        result.append('这里统计 RDF 属性值；JSON 数组长度另见 minItems／maxItems。')
    return '\n'.join(result)


def comprehensive_required(row, meta, scope, index):
    path = row[1]
    lo, hi = bound(index, path, 'minCount'), bound(index, path, 'maxCount')
    if meta['row_kind'] == '数组元素':
        return '数组元素结构；按父数组与 items 约束。'
    if hi == 0:
        return '不应出现（SHACL maxCount=0）'
    if meta['required']:
        if meta['parent_path'] == '$':
            return '必填（当前访问视图）'
        return '父对象／数组元素存在时必填'
    if meta['conditional']:
        return '条件必填（组合／依赖）'
    if lo is not None and lo >= 1:
        return 'SHACL要求出现；Schema未列必填'
    if meta['name'] == 'manufacturerContactDetails':
        return '可选对象；提供后内部至少填一项'
    if meta['name'] in GENERIC_NAMES:
        return '父对象存在时可选（Schema未列必填）'
    if '至少一项' in scope or '至少一组' in scope:
        return '组合内择项；不是逐项必填'
    if scope.startswith('必填'):
        return '词表称必填；Schema未列必填'
    if any(x in scope for x in ['适用', '开始使用后'] ) and not scope.startswith('官方词表未'):
        if '可选' in scope:
            return '可选（Schema未列必填）'
        return '适用时填写（Schema未列必填）'
    return '可选（Schema未列必填）'


def enrich(assets, profile_rows, common_rows, metadata, meanings, vocabs, profiles, views):
    shapes = ShapeIndex(assets)
    meanings = {k: dict(v) for k, v in meanings.items()}
    # Cross-check the old concept-block extractor against actual RDF parsing.
    total_concepts = 0
    for file in sorted(assets.rglob('*.ttl')):
        if '/vocabs/' not in file.as_posix():
            continue
        g = Graph().parse(file, format='turtle')
        total_concepts += len(set(g.subjects(RDF.type, SKOS.Concept)))
    assert total_concepts == len(vocabs), (total_concepts, len(vocabs))
    difference_rows = []
    units_rows = []
    unit_seen = set()
    all_details = {}
    all_indexes = {}

    def convert(rows, subgroup=None):
        sample_maps = defaultdict(dict)
        for row in rows:
            meta = metadata[id(row)]
            if not meta['sample_missing']:
                sample_maps[row[0]][norm(row[1])] = meta['sample']
        converted = []
        for row in rows:
            meta = metadata[id(row)]
            path, name, view = row[1], meta['name'], row[0]
            if subgroup:
                view_code = next(k for k, v in views.items() if v == view)
                index = shapes.profile(subgroup, view_code)
                profile_label = profiles[subgroup]
            else:
                index = shapes.common(view.split('/')[-1])
                # The common definitions are rooted at a descriptive $defs path.
                prefix = '$defs.' + view.split('/')[-1]
                index = {prefix + k[1:]: [Fact(prefix + f.path[1:], f.predicate, f.value, f.branch, f.source, f.shape) for f in fs] for k, fs in index.items()}
                profile_label = '通用'
            all_indexes[(subgroup, view)] = index
            meaning = meanings.get(name, {})
            scope = meaning.get('scope', '')
            effective_scope = scope_piece(scope, subgroup) if subgroup else ''
            business = translate_scope(effective_scope, name, subgroup) if subgroup else '通用结构成员；具体业务适用条件由引用该结构的电池字段决定。'
            if name in GENERIC_NAMES and subgroup:
                for ancestor in reversed(norm(path).split('.')[1:-1]):
                    ancestor_scope = meanings.get(ancestor, {}).get('scope', '')
                    if ancestor_scope:
                        parent_piece = scope_piece(ancestor_scope, subgroup)
                        business = '继承上层 ' + ancestor + '：' + translate_scope(parent_piece, ancestor, subgroup)
                        break
            measure, unit_basis, unit_facts = unit_details(row, meta, index, sample_maps[view])
            fs = facts_at(index, path)
            lo, hi = bound(index, path, 'minCount'), bound(index, path, 'maxCount')
            qlo = bound(index, path, 'qualifiedMinCount')
            schema_constraint = numeric_constraints(meta['parts'])
            notes = []
            categories = []

            def issue(kind, text):
                categories.append(kind)
                notes.append(text)

            if meta['row_kind'] != '数组元素':
                if meta['required'] and lo in {None, 0} and qlo is None and fs and row[3] != 'object' and not meta['conditional']:
                    if any(f.branch and f.predicate == 'minCount' and int(f.value) >= 1 for f in fs):
                        issue('条件结构表达', 'Schema在父结构存在时required此成员；SHACL在相应or分支中要求该成员出现。两者表达位置不同，不能把无全局minCount误读成成员可缺失。')
                    else:
                        issue('必填表达不同', 'Schema required 要求此属性；本路径 SHACL 未以 minCount≥1 要求出现。按 Schema 保留必填，不把 SHACL 未声明解释为允许省略。')
                if not meta['required'] and lo and lo >= 1 and not meta['conditional']:
                    issue('必填表达不同', 'Schema 未将该属性列入 required，而 SHACL minCount≥1 要求出现；两个层面的结果分别保留，完整校验需满足实际加载的规则。')
            if row[3] == 'array':
                array_min = max(schema_constraint.get('minItems', [0]))
                if lo and lo >= 1 and array_min == 0:
                    issue('数组非空要求', 'Schema 未声明 minItems≥1，属性存在时仍可能接受空数组；SHACL 要求至少一个 RDF 属性值，空数组不能据此认定完整校验通过。')
            if name in NAME_MEMBERS | ID_MEMBERS | PLACE_MEMBERS | CONTACT_MEMBERS:
                issue('组合必填口径', '词表或父结构的适用要求属于组合；本字段是否出现应按 anyOf／dependentRequired／SHACL分支判断，不能将组合成员全部设为独立必填。')
            if name == 'manufacturerContactDetails':
                issue('可选对象的内部条件', '词表称网站与邮箱可选；Schema 和 SHACL 在联系信息对象提供时要求网站／邮箱至少一项，空对象不符合。')
            if name in {'minimalVoltage', 'nominalVoltage', 'maximumVoltage'} or ('.valueRange' in path and norm(path).split('.')[1] in {'minimalVoltage', 'nominalVoltage', 'maximumVoltage'}):
                issue('词表与结构表述差异', '词表将温度范围称为 optional；本版具体电压 Schema 额外 required=["valueRange"]，SHACL unit-range-shape 也要求范围出现。开发按实际约束保留该差异。')
            if name == 'notInUseTemperatureRange' or 'notInUseTemperatureRange.valueRange' in path:
                issue('单位语义与校验范围', '词表定义耐受温度单位为 °C；此路径 SHACL 未用 hasValue 固定 degreeCelsius。应按语义表达摄氏度，不能把通用单位 pattern 当作业务单位清单。')
            if name in {'selfDischargingRate', 'selfDischargingRateSet'}:
                issue('单位口径补充', '词表是 decrease % per year；SHACL 单位只写 percent。按每年下降百分比解释数值，并保留 recordedTime；没有独立的 %/year 单位 URI。')
            if name == 'separateCollectionSymbol':
                issue('SHACL固定值', 'Schema 类型为 boolean；SHACL 另有 hasValue=true，不能只做布尔类型检查。')
            if name in {'stateOfChargeHistogram', 'binRangeUnit', 'binValueUnit'} and 'stateOfChargeHistogram' in path:
                issue('单位来源区分', 'SOC 词表口径为 %；可达 SHACL 只要求直方图单位字段存在，未固定其具体 URI。官方样例用 percent／hour，不能据样例认定所有直方图都固定为这两个单位。')
            if name == 'contentSpecificationIds':
                issue('限定子集数量', 'SHACL 分别要求核心、电池组、电池子类规范标识各一个匹配值；qualifiedMinCount／qualifiedMaxCount 不表示整个数组只能有一个元素。')
            if '/$defs/multiLanguage' in row[12]:
                issue('多语言格式', 'JSON Schema 动态语言键的值未单独声明 string；SHACL 要求 rdf:langString。RDF maxCount 针对语言文本值，不能直接理解为 JSON 对象最多一个键。')
            if subgroup == 'ind' and effective_scope and not meta['required'] and any(x in business for x in ['仅', '适用']):
                issue('业务条件未编码为required', '工业电池业务适用条件来自官方 scopeNote；静态 Schema 未通过 required 自动表达该条件，不能据此一概视为业务可选。')
            checks = '\n'.join(distinct(notes)) if notes else '已核对 Schema、可达 SHACL 与词表；本行未发现需要单独提示的口径差异。'
            source_fs = distinct([*fs, *unit_facts])
            sources = distinct(f.source + ('\nshape=' + f.shape if f.shape else '') for f in source_fs)
            if meaning:
                sources.append(str(meaning['file'].relative_to(assets)) + '\n概念=' + meaning['uri'])
            sources = distinct(sources)
            messages = distinct([p.node['message']['type'] for p in meta['parts'] if isinstance(p.node.get('message'), dict) and 'type' in p.node['message']] + [f.value for f in fs if f.predicate == 'message'])
            format_text = human_format(row, meta, measure, index)
            shacl_rules = []
            for f in fs:
                if f.predicate in {'datatype', 'nodeKind', 'hasValue', 'pattern', 'minInclusive', 'maxInclusive'}:
                    shacl_rules.append(('[' + f.branch + '] ' if f.branch else '') + f.predicate + '=' + f.value)
            details = {
                HEADERS[0]: view, HEADERS[1]: path, HEADERS[2]: row[2],
                HEADERS[3]: comprehensive_required(row, meta, business, index),
                HEADERS[4]: business, HEADERS[5]: measure, HEADERS[6]: unit_basis,
                HEADERS[7]: row[3], HEADERS[8]: format_text, HEADERS[9]: row[6] or '本字段未另设范围／长度／格式关键字；仍受类型、父结构及语义规则约束。',
                HEADERS[10]: row[4], HEADERS[11]: quantity_text(index, path, row[3], meta['row_kind']),
                HEADERS[12]: row[5], HEADERS[13]: row[7] or '未指定受控词表／enum。',
                HEADERS[14]: '\n'.join(filter(None, [row[8], 'SHACL本路径值约束：\n' + '\n'.join(distinct(shacl_rules)) if shacl_rules else ''])),
                HEADERS[15]: row[9], HEADERS[16]: checks, HEADERS[17]: row[10] or '该成员未在词表中单独定义；按引用结构解释。',
                HEADERS[18]: scope or '词表未给出独立 scopeNote。', HEADERS[19]: row[11],
                HEADERS[20]: '\n\n'.join(sources) or '本路径未单独匹配到 SHACL／词表条目。',
                HEADERS[21]: row[12] or '无直接 $ref。', HEADERS[22]: row[13] or '填写时同时满足父对象及当前访问视图的规则。',
                HEADERS[23]: row[14], HEADERS[24]: '\n'.join(messages) or '本路径未直接标注语义消息代码。',
            }
            new_row = [details[h] for h in HEADERS]
            converted.append(new_row)
            all_details[subgroup, view, path] = details
            if categories:
                difference_rows.append([profile_label, view, path, row[2], '；'.join(distinct(categories)), details[HEADERS[3]], row[4], details[HEADERS[11]], business, checks, row[11], details[HEADERS[20]]])
            # A compact reader for every scalar, quantity object, date and histogram.
            if measure not in {'不适用（非计量字段）', '组合／列表字段；详见子字段'} or row[3] in {'integer', 'number'}:
                unit_key = (view, path, measure, unit_basis, row[3], row[6], format_text)
                if unit_key not in unit_seen:
                    unit_seen.add(unit_key)
                    units_rows.append([profile_label, view, path, row[2], measure, unit_basis, row[3], format_text, row[6] or '未单列数值边界', row[9], details[HEADERS[20]]])
                else:
                    for existing in reversed(units_rows):
                        if existing[1:3] == [view, path] and existing[4:7] == [measure, unit_basis, row[3]] and existing[7] == format_text:
                            existing[0] = '／'.join(distinct(existing[0].split('／') + [profile_label]))
                            break
        return converted

    enriched_profiles = {s: convert(rows, s) for s, rows in profile_rows.items()}
    enriched_common = convert(common_rows)
    top = defaultdict(dict)
    for (subgroup, view, path), detail in all_details.items():
        if subgroup is not None and path.count('.') == 1 and '[]' not in path:
            top[view, path][subgroup] = detail
    compare_rows = []
    for (view, path), values in top.items():
        first = next(iter(values.values()))
        statuses, conditions = [], []
        for s in profiles:
            if s in values:
                statuses.append(values[s][HEADERS[3]])
                conditions.append(values[s][HEADERS[4]])
            else:
                index = shapes.profile(s, next(k for k, v in views.items() if v == view))
                excluded = bound(index, path, 'maxCount') == 0
                statuses.append('不填写／不展示（SHACL maxCount=0）' if excluded else '此子类Schema未定义；不能据此判定禁止。')
                concept = meanings.get(path[2:], {})
                piece = scope_piece(concept.get('scope', ''), s)
                conditions.append(translate_scope(piece, path[2:], s))
        compare_rows.append([view, path, first[HEADERS[2]], first[HEADERS[5]], first[HEADERS[7]],
                             *statuses, *conditions, first[HEADERS[8]], first[HEADERS[15]],
                             '\n\n'.join(distinct(profiles[s] + '：' + d[HEADERS[16]] for s, d in values.items() if not d[HEADERS[16]].startswith('已核对')))
                             or '三个类别分别核对；详细规则见对应类型工作表。'])
    metrics = {
        'sourceShapeFiles': len(shapes.graphs), 'sourceShapeTriples': len(shapes.graph),
        'sourceShapePathOccurrences': shapes.raw_properties,
        'vocabularyRdfConceptCount': total_concepts,
        'detailedProfileRows': sum(map(len, enriched_profiles.values())),
        'commonStructureRows': len(enriched_common),
        'unitFormatRows': len(units_rows), 'ruleReviewRows': len(difference_rows),
        'shaclValidationExecuted': False,
        'method': 'RDF解析SHACL，沿property/node/and/or/not/qualifiedValueShape与序列/替代路径读取；组合分支与无条件数量分开，Schema按$id展开。',
    }
    return {
        'profiles': enriched_profiles, 'common': enriched_common, 'compare': compare_rows,
        'differences': difference_rows, 'units': units_rows, 'shacl': shapes.raw_rows,
        'metrics': metrics, 'details': all_details, 'indexes': all_indexes,
    }
