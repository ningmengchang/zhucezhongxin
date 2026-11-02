"""Combine the four EV semantic access views into one inline Apifox model."""

from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / '03_语义资源'
OUT = ROOT / '09_EV电池单模型'
MODEL = 'EVBatteryPassport'
VIEWS = [
    ('DppEV_PUB', 'PUB', '公开信息'),
    ('DppEV_RES1', 'RES1', '合法权益人／公告机构／成员国主管机关／欧委会'),
    ('DppEV_RES2', 'RES2', '公告机构／成员国主管机关／欧委会'),
    ('DppEV_RES3', 'RES3', '合法权益人（单个电池）'),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def constraints_only(node):
    """Remove documentation for an independent comparison of field constraints."""
    if isinstance(node, dict):
        return {k: constraints_only(v) for k, v in node.items()
                if k not in {'title', 'description', 'examples', '$comment'} and not k.startswith('x-')}
    if isinstance(node, list):
        return [constraints_only(v) for v in node]
    return node


def direct_import_schema(node):
    """Keep constraints and visible documentation, omitting source audit metadata."""
    if isinstance(node, dict):
        return {k: direct_import_schema(v) for k, v in node.items()
                if not k.startswith('x-') or k in {'x-apifox-enum', 'x-apifox-orders'}}
    if isinstance(node, list):
        return [direct_import_schema(v) for v in node]
    return node


def compact_import_schema(schema):
    """Bound documentation size without changing validation keywords or enum values.

    These are conservative export budgets, not a claim about Apifox's undocumented
    server limits. Complete field and enum descriptions remain in companion CSVs.
    """
    result = direct_import_schema(schema)
    special = {
        'manufacturerIdentification': '名称、注册商号、商标至少一项。',
        'manufacturerName': '名称、商号、商标至少一项。',
        'manufacturerTradeName': '名称、商号、商标至少一项。',
        'manufacturerTrademark': '名称、商号、商标至少一项。',
        'manufacturerContactDetails': '提供对象时，网站或邮箱至少一项。',
        'manufacturerWebsite': '网站或邮箱至少一项。',
        'manufacturerEmail': '网站或邮箱至少一项；邮箱填写mailto: URI。',
        'batteryIdentification': '型号和批次必须成对；或序列号、产品编号、其他识别要素至少一组。',
        'modelIdentifier': '填写型号时必须同时填写batchNumber。',
        'batchNumber': '填写批次时必须同时填写modelIdentifier。',
        'serialNumber': '属于电池识别择项组合。',
        'productNumber': '属于电池识别择项组合。',
        'otherIdentificationElement': '属于电池识别择项组合。',
        'manufacturingPlace': '国家必填；城市或地区至少一项。',
        'manufacturingCity': '城市或地区至少一项。',
        'manufacturingRegion': '城市或地区至少一项。',
        'manufacturingCountry': '三位大写国家代码。',
        'manufacturingDate': 'YYYY-MM年月格式。',
        'commercialWarrantyPeriod': '单位：月；商业质保适用时填写。',
        'expectedLifetimeCycles': '单位：循环次数。',
        'expectedLifetimeCyclesRest': '单位：循环次数。',
        'expectedLifetimeYears': '单位：年，填写整数。',
        'contentSpecificationIds': '核心、电池组、EV子类规范URI各一项；版本号按pattern填写。',
        'numberOfChargingCycles': '电池开始使用后适用；单位：次数。',
        'numberOfDischargingCycles': '电池开始使用后适用；单位：次数。',
        'negativeEvents': '电池开始使用后适用。',
        'environmentalConditionHistogramSet': '电池开始使用后适用。',
        'environmentalConditionHistogram': '电池开始使用后适用。',
        'stateOfChargeHistogram': '电池开始使用后适用。',
        'language': '语言标签，例如en-GB。',
    }

    def combine(parts):
        selected = []
        for part in parts:
            if part and len((' '.join([*selected, part])).encode('utf-8')) <= 240:
                selected.append(part)
        return ' '.join(selected)

    def visit(node, original, path='$', parent=None):
        for key in list(node):
            if key in {'examples', '$comment'} or key.startswith('x-'):
                del node[key]
        for name, child in node.get('properties', {}).items():
            visit(child, original['properties'][name], path + '.' + name, original)
        if isinstance(node.get('items'), dict):
            visit(node['items'], original['items'], path + '[]', original)
        if isinstance(node.get('additionalProperties'), dict):
            node['additionalProperties'] = {
                'description': '动态语言键的值：源Schema未限定JSON类型，语义层要求文本。'
            }
        if path == '$':
            node['description'] = 'EV全量语义模型：63个顶层字段，四视图必填规则取并集。字段详解及枚举含义见配套CSV。'
            return
        field = path.rstrip('[]').rsplit('.', 1)[-1]
        typ = original.get('type', '源Schema未限定')
        if typ == 'array':
            typ += '<' + original.get('items', {}).get('type', '未限定') + '>'
        conditional = parent and (
            any(field in branch.get('required', []) for key in ('anyOf', 'oneOf') for branch in parent.get(key, []))
            or field in parent.get('dependentRequired', {})
            or any(field in names for names in parent.get('dependentRequired', {}).values())
        )
        if path.endswith('[]'):
            status = '数组元素'
        elif field in (parent or {}).get('required', []):
            status = '必填' if path.count('.') == 1 else '父对象存在时必填'
        elif conditional:
            status = '条件必填'
        elif '适用' in original.get('x-semantic-required-note', ''):
            status = '适用时填写'
        else:
            status = '可选'
        values = node.get('enum', node.get('items', {}).get('enum', []))
        enum_text = ('枚举' + str(len(values)) + '项' + ('，使用完整URI。' if values and isinstance(values[0], str) and values[0].startswith('https://') else '。')) if values else '非枚举。'
        language_object = original.get('type') == 'object' and 'propertyNames' in original
        notes = ('至少一个xx-XX语言键；源Schema未限定值类型，语义要求文本。' if language_object
                 else special.get(field, ''))
        node['description'] = combine([
            original.get('x-access-view', '') + '；' + status + '；' + typ + '。',
            notes,
            enum_text,
            '格式：' + node['format'] + '。' if 'format' in node else '',
        ])

    visit(result, schema)
    return result


def access_note(node, view, label):
    if not isinstance(node, dict):
        return
    node['x-access-view'] = view
    node['x-access-view-label'] = label
    node['description'] = '访问级别：' + view + '（' + label + '）\n\n' + node.get('description', '')
    for child in node.get('properties', {}).values():
        access_note(child, view, label)
    if isinstance(node.get('items'), dict):
        access_note(node['items'], view, label)
    if isinstance(node.get('additionalProperties'), dict):
        access_note(node['additionalProperties'], view, label)


def field_rows(node, path='$', parent=None):
    rows = []
    if path != '$':
        items = node if 'enum' in node else node.get('items', {})
        enum = items.get('enum', [])
        required = ('数组元素' if path.endswith('[]') else
                    '是（父对象存在时）' if path.rsplit('.', 1)[-1] in (parent or {}).get('required', []) else
                    '见字段说明中的组合／适用条件；未列入独立 required')
        rows.append([path, node.get('title', ''), node['x-access-view'], required,
                     node.get('type', '源 Schema 未限定'), json.dumps(enum, ensure_ascii=False),
                     '\n'.join(str(v['value']) + ' — ' + v['name'] for v in items.get('x-apifox-enum', [])) or '非枚举',
                     node.get('description', '')])
    for name, child in node.get('properties', {}).items():
        rows.extend(field_rows(child, path + '.' + name, node))
    if isinstance(node.get('items'), dict):
        rows.extend(field_rows(node['items'], path + '[]', node))
    return rows


def main():
    source_file = ROOT / '08_Apifox语义模型/dpp-semantic-models-apifox.yaml'
    previous_report = json.loads((ROOT / '08_Apifox语义模型/生成与校验报告.json').read_text())
    assert sha(source_file) == previous_report['artifactSha256'][source_file.name]
    for name, digest in previous_report['sourceFilesSha256'].items():
        assert sha(ASSETS / name) == digest, ('source changed', name)
    source = yaml.safe_load(source_file.read_text())
    properties, required, access_counts = {}, set(), {}
    original_properties = {}
    for name, view, label in VIEWS:
        schema = source['components']['schemas'][name]
        access_counts[view] = len(schema['properties'])
        required.update(schema.get('required', []))
        for key, value in schema['properties'].items():
            assert key not in properties, ('review overlapping field before merging', key)
            original_properties[key] = value
            properties[key] = deepcopy(value)
            access_note(properties[key], view, label)
            assert constraints_only(properties[key]) == constraints_only(value), key
    assert len(properties) == 63
    assert access_counts == {'PUB': 41, 'RES1': 4, 'RES2': 1, 'RES3': 17}
    for view, _, _ in VIEWS:
        assert all(key in properties for key in source['components']['schemas'][view]['properties'])

    official_sample = ASSETS / 'product/batteries/subgroup/evb/1.0.0/tests/conforms/batteries-evb.json'
    sample = json.loads(official_sample.read_text())
    model = {
        'type': 'object',
        'title': 'EV电池全部语义字段',
        'description': (
            'EV 电池全部语义字段的单一数据模型。PUB、RES1、RES2、RES3 四个访问视图的字段直接合并在同一层；'
            '共 63 个顶层字段，不增加按视图分组的包装对象。嵌套结构、数组、字段类型、枚举和组合必填规则逐项保留。\n\n'
            '每个字段说明开头标出访问级别。required 是四份官方 EV JSON Schema required 的并集，'
            '表示一份完整 EV 语义数据对象的结构要求；只包含公开字段的数据对象应按原 PUB 视图校验。'
            '条件必填和 SHACL 差异仍见字段说明。\n\n'
            '词表枚举和单位值使用完整 URI；枚举名称与逐项解释内置在字段中。'
            '多语言对象动态键的值，源 JSON Schema 未限定类型，说明中保留语义层要求文本的边界。\n\n'
            'EV 视图不定义 remainingCapacity、remainingPowerCapability、remainingRoundTripEfficiency、'
            'selfDischargingRateSet、ohmicResistance；其 EV SHACL maxCount=0，这些字段未加入模型。\n\n'
            '本模型整理自本地 2026-10-30 官方语义资源；不是 POST /api/v1/dpp/submission 的完整请求体。'
            '完整语义校验仍需原始上下文与 SHACL／SPARQL。'
        ),
        'properties': properties,
        'required': [key for key in properties if key in required],
        'x-apifox-orders': list(properties),
        'x-source-views': [dict(model=name, view=view, access=label) for name, view, label in VIEWS],
        'x-derived-document': {'source': '03_语义资源', 'accessViewFieldCounts': access_counts,
                               'requiredPolicy': '四个 EV 视图 required 的并集；嵌套条件保持原样。'},
        'examples': [sample],
    }
    assert set(model['required']) == required
    Draft202012Validator.check_schema(model)
    validator = Draft202012Validator(model, format_checker=Draft202012Validator.FORMAT_CHECKER)
    validator.validate(sample)
    counterexamples = []
    for name in model['required']:
        changed = deepcopy(sample)
        del changed[name]
        assert not validator.is_valid(changed), name
        counterexamples.append('删除必填字段：' + name)
    for name, value in [
        ('manufacturerIdentification', {}),
        ('manufacturerContactDetails', {}),
        ('batteryIdentification', {'modelIdentifier': 'M1'}),
        ('manufacturingPlace', {'manufacturingCountry': 'ESP'}),
        ('batteryCategory', 'invalid-category'),
        ('batteryWeight', {'unit': 'https://repository.product-passport.ec.europa.eu/core/vocabs/measurement-unit#volt', 'value': 1}),
        ('commercialWarrantyPeriod', '24'),
        ('separateCollectionSymbol', False),
    ]:
        changed = deepcopy(sample)
        changed[name] = value
        assert not validator.is_valid(changed), (name, value)
        counterexamples.append('无效类型／组合／枚举：' + name)

    document = {
        'openapi': '3.1.0',
        'info': {'title': 'EV电池全部语义字段｜单一数据模型', 'version': '2026-10-30.ev.1',
                 'description': '仅定义 EVBatteryPassport 一个数据模型；EV 四个访问视图的全部字段直接合并，字段说明包含访问级别、必填条件、类型与枚举。',
                 'license': {'name': 'European Union Public Licence v. 1.2', 'identifier': 'EUPL-1.2'}},
        'jsonSchemaDialect': 'https://json-schema.org/draft/2020-12/schema',
        'paths': {},
        'components': {'schemas': {MODEL: model}},
    }
    assert len(document['components']['schemas']) == 1
    assert '"$ref"' not in json.dumps(document)
    OUT.mkdir(exist_ok=True)

    class Dumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    def represent_string(dumper, value):
        return dumper.represent_scalar('tag:yaml.org,2002:str', value, style='|' if '\n' in value else None)

    Dumper.add_representer(str, represent_string)
    target = OUT / 'openapi-ev-battery-single-model.yaml'
    target.write_text('# EV 全部语义字段；仅一个模型，所有字段结构与枚举内置。\n' +
                      yaml.dump(document, Dumper=Dumper, allow_unicode=True, sort_keys=False, width=110), encoding='utf-8')
    schema_document = {'$schema': 'https://json-schema.org/draft/2020-12/schema', **model}
    schema_path = OUT / 'EV电池全部语义字段.schema.json'
    schema_path.write_text(json.dumps(schema_document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    assert yaml.safe_load(target.read_text()) == document
    Draft202012Validator.check_schema(json.loads(schema_path.read_text()))
    direct_schema = compact_import_schema(schema_document)
    direct_path = OUT / 'EVBatteryPassport_Apifox直接建模.schema.json'
    direct_text = json.dumps(direct_schema, ensure_ascii=False, separators=(',', ':'))
    direct_path.write_text(direct_text + '\n', encoding='utf-8')
    compatible_path = OUT / 'EVBatteryPassport_长度兼容版.schema.json'
    compatible_path.write_text(direct_text + '\n', encoding='utf-8')
    assert constraints_only(direct_schema) == constraints_only(schema_document)
    Draft202012Validator.check_schema(direct_schema)
    direct_validator = Draft202012Validator(direct_schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    direct_validator.validate(sample)
    assert len(direct_schema['properties']) == 63
    for key in properties:
        assert constraints_only(direct_schema['properties'][key]) == constraints_only(properties[key])
    text_limits = []
    enum_rows = []

    def check_text_lengths(node, path='$'):
        if not isinstance(node, dict):
            return
        for key, limit in [('description', 240), ('title', 64)]:
            if key in node:
                actual = len(node[key].encode('utf-8'))
                assert actual <= limit, (path, key, actual, limit)
                text_limits.append((key, actual))
        for key, child in node.get('properties', {}).items():
            check_text_lengths(child, path + '.' + key)
        for key in ['items', 'additionalProperties']:
            if isinstance(node.get(key), dict):
                check_text_lengths(node[key], path + ( '[]' if key == 'items' else '.{languageTag}'))

    def enum_notes(node, path='$'):
        for item in node.get('x-apifox-enum', []):
            enum_rows.append([path, node.get('title', ''), json.dumps(item['value'], ensure_ascii=False),
                              item['name'], item.get('description', '')])
        for name, child in node.get('properties', {}).items():
            enum_notes(child, path + '.' + name)
        if isinstance(node.get('items'), dict):
            enum_notes(node['items'], path + '[]')

    check_text_lengths(direct_schema)
    enum_notes(schema_document)
    assert len(direct_text.encode('utf-8')) < 65536
    assert len(json.dumps(direct_text, ensure_ascii=False).encode('utf-8')) < 65536
    with (OUT / 'EV电池_枚举说明.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['字段路径', '字段名称', '完整枚举值(JSON)', '枚举名称', '枚举完整说明'])
        writer.writerows(enum_rows)
    (OUT / 'EV电池_官方全量样例.json').write_text(json.dumps(sample, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    rows = field_rows(model)
    with (OUT / 'EV电池_全部字段说明.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['字段路径', '中文名称', '访问级别', 'JSON结构必填', '数据类型', '枚举值', '枚举名称', '完整说明'])
        writer.writerows(rows)
    (OUT / '导入说明.txt').write_text(
        '推荐使用 EVBatteryPassport_长度兼容版.schema.json，从数据模型入口直接创建。\n'
        'EVBatteryPassport_Apifox直接建模.schema.json 也已更新为相同的长度兼容内容。\n'
        '操作：接口管理 → ＋ → 新建数据模型，名称填写 EVBatteryPassport。\n'
        '点击“通过 JSON 等生成”，选择 JSON Schema，将该文件的完整内容粘贴进去，应用并保存。\n'
        '模式必须选择 JSON Schema；JSON/XML 模式用于根据样例推断字段，可能将 type、properties 等 Schema 关键字识别为普通字段。\n'
        '也可在模型的 JSON Schema／编辑源码入口粘贴完整文件并保存。\n'
        '保存后在“接口管理 → 数据模型”中查找 EVBatteryPassport；数据模型与接口目录分开。\n'
        '该文件为单个模型本身，包含 63 个顶层字段，无 OpenAPI 外层包装，无辅助模型。\n'
        '字段类型、必填、组合与完整枚举值保持一致；模型内使用简明说明。\n'
        '兼容版按本次选定的保守预算控制：每段说明最多240个UTF-8字节，标题最多64字节，整个模型和作为JSON字符串转义后均小于64KiB。该预算不是对Apifox官方限额的声明。\n'
        '完整字段说明见 EV电池_全部字段说明.csv；逐项枚举名称与详细解释见 EV电池_枚举说明.csv。\n'
        'openapi-ev-battery-single-model.yaml 仍保留为 OpenAPI 归档；用户反馈该项目级导入路径成功后未看到模型，不能据解析成功确认模型已创建。\n'
        '模型内直接包含 63 个顶层字段：PUB 41、RES1 4、RES2 1、RES3 17。\n'
        '所有嵌套对象、数组、通用结构、枚举都内置于同一模型；不增加其他模型或视图包装层。\n'
        '完整 EV 对象的 required 为四个源视图 required 的并集；条件必填继续保留 anyOf／dependentRequired。\n'
        '每个字段说明开头标注访问级别，保留必填条件、数据类型、完整 URI 枚举及含义。\n'
        'EV电池全部语义字段.schema.json 是同一个模型的原生 JSON Schema，供单独的数据模型 Schema 导入／编辑入口使用。\n'
        '已校验官方 EV 全量样例、四视图字段覆盖、字段约束一致性及反例。未在 Apifox 界面实测。\n'
        '官方步骤：https://docs.apifox.com/create-data-schema\n'
        'JSON Schema 模式：https://docs.apifox.com/generate-from-json\n', encoding='utf-8')
    report = {
        'modelName': MODEL, 'modelCount': 1, 'topLevelFieldCount': len(properties),
        'requiredTopLevelFieldCount': len(required), 'accessViewFieldCounts': access_counts,
        'fieldAndArrayItemDescriptionCount': len(rows), 'allSourceViewFieldsCovered': True,
        'fieldConstraintsPreserved': all(constraints_only(properties[k]) == constraints_only(v) for k,v in original_properties.items()),
        'officialCompleteEvExamplePassed': True, 'counterexamplesRejected': len(counterexamples),
        'counterexamples': counterexamples, 'unresolvedReferences': 0, 'apifoxUiImportTested': False,
        'yamlSha256': sha(target), 'sourceArtifactsChecked': True,
        'directJsonSchemaFile': direct_path.name, 'directJsonSchemaSha256': sha(direct_path),
        'directJsonSchemaConstraintsPreserved': True, 'directJsonSchemaDescriptionsCompacted': True,
        'directJsonSchemaOfficialExamplePassed': True,
        'compatibleFile': compatible_path.name, 'compatibleUtf8Bytes': len(direct_text.encode('utf-8')),
        'compatibleAsJsonStringUtf8Bytes': len(json.dumps(direct_text, ensure_ascii=False).encode('utf-8')),
        'maxDescriptionUtf8Bytes': max(size for key,size in text_limits if key == 'description'),
        'maxTitleUtf8Bytes': max(size for key,size in text_limits if key == 'title'),
        'fullEnumExplanationRows': len(enum_rows),
    }
    (OUT / '生成与校验结果.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'counterexamples'}, ensure_ascii=False, indent=2))
    print(target)


if __name__ == '__main__':
    main()
