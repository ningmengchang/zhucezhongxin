"""Build LMT and industrial battery full and size-bounded single-model schemas."""

from copy import deepcopy
import csv
import json
from pathlib import Path
import zipfile

import yaml
from jsonschema import Draft202012Validator

from build_ev_single_model import access_note, compact_import_schema, constraints_only, field_rows, sha


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / '03_语义资源'
ACCESS = {
    'PUB': '公开信息',
    'RES1': '合法权益人／公告机构／成员国主管机关／欧委会',
    'RES2': '公告机构／成员国主管机关／欧委会',
    'RES3': '合法权益人（单个电池）',
}
PROFILES = [
    {'group': 'lmt', 'prefix': 'LMT', 'label': 'LMT', 'model': 'LMTBatteryPassport',
     'directory': '10_LMT电池单模型', 'required': 55},
    {'group': 'ind', 'prefix': 'IND', 'label': '工业', 'model': 'INDBatteryPassport',
     'directory': '11_工业电池单模型', 'required': 37},
]


def industrial_notes():
    notes = {}
    for name in ['ratedCapacity', 'capacityFade', 'powerCapability', 'powerFade',
                 'internalResistance', 'internalResistanceIncrease', 'expectedLifetimeYears']:
        notes['$.' + name] = '仅可充电工业电池适用。'
    for name in ['expectedLifetimeCycles', 'cycleLifeReferenceTest']:
        notes['$.' + name] = '仅适用以循环次数表达寿命的部分工业电池。'
    for name in ['initialRoundTripEnergyEfficiency', 'roundTripEfficiencyAtHalfCycleLife', 'cycleLifeChargeCRate']:
        notes['$.' + name] = '仅部分工业电池适用；源词表未提供统一判定字段。'
    notes['$.expectedLifetimeCyclesRest'] = '仅可充电且用于循环应用的工业电池适用。'
    for name in ['remainingCapacity', 'remainingPowerCapability', 'remainingRoundTripEfficiency',
                 'selfDischargingRateSet', 'ohmicResistance']:
        notes['$.' + name] = '仅配BMS的固定式电池储能适用；原文where possible指标按可提供性判断。'
    return notes


def dump_csv(path, headers, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)


def inspect_model(node, path='$', descriptions=None, titles=None, enums=None, enum_notes=None):
    descriptions = descriptions if descriptions is not None else []
    titles = titles if titles is not None else []
    enums = enums if enums is not None else {}
    enum_notes = enum_notes if enum_notes is not None else []
    assert '$ref' not in node, path
    if 'description' in node:
        descriptions.append((path, len(node['description'].encode('utf-8'))))
    if 'title' in node:
        titles.append((path, len(node['title'].encode('utf-8'))))
    if 'enum' in node:
        enums[path] = node['enum']
        for item in node.get('x-apifox-enum', []):
            enum_notes.append([path, node.get('title', ''), json.dumps(item['value'], ensure_ascii=False),
                               item['name'], item.get('description', '')])
    for name, child in node.get('properties', {}).items():
        inspect_model(child, path + '.' + name, descriptions, titles, enums, enum_notes)
    for key, suffix in [('items', '[]'), ('additionalProperties', '.{languageTag}')]:
        if isinstance(node.get(key), dict):
            inspect_model(node[key], path + suffix, descriptions, titles, enums, enum_notes)
    return descriptions, titles, enums, enum_notes


def verify_examples(profile, full, compact, sample):
    validators = [Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
                  for schema in [full, compact]]
    results = []
    for validator in validators:
        validator.validate(sample)
    for name in full['required']:
        changed = deepcopy(sample)
        del changed[name]
        assert all(not validator.is_valid(changed) for validator in validators), name
        results.append(['删除根必填字段', name, '两个版本均正确拒绝'])
    bad_values = [
        ('manufacturerIdentification', {}), ('manufacturerContactDetails', {}),
        ('batteryIdentification', {'modelIdentifier': 'M1'}),
        ('batteryIdentification', {'batchNumber': 'B1'}),
        ('manufacturingPlace', {'manufacturingCountry': 'ESP'}),
        ('batteryCategory', 'invalid-category'),
        ('batteryWeight', {'unit': 'https://repository.product-passport.ec.europa.eu/core/vocabs/measurement-unit#volt', 'value': 1}),
        ('remainingCapacity', {'unit': 'https://repository.product-passport.ec.europa.eu/core/vocabs/measurement-unit#watt', 'value': 1}),
        ('selfDischargingRateSet', {}), ('expectedLifetimeYears', 3.5),
        ('commercialWarrantyPeriod', '24'), ('separateCollectionSymbol', False),
    ]
    for name, value in bad_values:
        changed = deepcopy(sample)
        changed[name] = value
        assert all(not validator.is_valid(changed) for validator in validators), (name, value)
        results.append(['类型／枚举／组合反例', name + '=' + json.dumps(value, ensure_ascii=False), '两个版本均正确拒绝'])
    for name, value in [('batteryIdentification', {'serialNumber': 'S1'}),
                        ('manufacturerIdentification', {'manufacturerName': 'Example manufacturer'})]:
        changed = deepcopy(sample)
        changed[name] = value
        for validator in validators:
            validator.validate(changed)
        results.append(['合法择项组合', name, '两个版本均正确接受'])
    optional = set(full['properties']) - set(full['required'])
    for name in sorted(optional):
        changed = deepcopy(sample)
        changed.pop(name, None)
        for validator in validators:
            validator.validate(changed)
        results.append(['省略非独立required字段', name, '两个版本结构校验均允许；业务适用性另行判断'])
    if profile['group'] == 'ind':
        for name in ['ratedCapacity', 'remainingCapacity', 'expectedLifetimeCycles', 'selfDischargingRateSet']:
            assert name not in full['required']
            assert '适用' in compact['properties'][name]['description']
    else:
        for name in ['ratedCapacity', 'remainingCapacity', 'expectedLifetimeCycles', 'selfDischargingRateSet']:
            assert name in full['required']
    return results


def build_profile(profile, source):
    label, group = profile['label'], profile['group']
    out = ROOT / profile['directory']
    out.mkdir(exist_ok=True)
    properties, required, view_counts = {}, set(), {}
    for view, access in ACCESS.items():
        model = source['components']['schemas']['Dpp' + profile['prefix'] + '_' + view]
        required.update(model.get('required', []))
        view_counts[view] = len(model['properties'])
        for name, value in model['properties'].items():
            assert name not in properties, ('overlapping source property requires review', name)
            copied = deepcopy(value)
            access_note(copied, view, access)
            assert constraints_only(copied) == constraints_only(value)
            properties[name] = copied
    assert view_counts == {'PUB': 40, 'RES1': 4, 'RES2': 1, 'RES3': 21}
    assert len(properties) == 66 and len(required) == profile['required']
    assert not {'capacityExhaustionThreshold', 'stateOfCertifiedEnergy'} & set(properties)
    for name in ['remainingCapacity', 'remainingPowerCapability', 'remainingRoundTripEfficiency',
                 'selfDischargingRateSet', 'ohmicResistance']:
        assert name in properties
    sample_path = ASSETS / f'product/batteries/subgroup/{group}/1.0.0/tests/conforms/batteries-{group}.json'
    sample = json.loads(sample_path.read_text())
    description = (
        f'{label}电池全部语义字段的单一数据模型：PUB、RES1、RES2、RES3 合并为同一对象的 66 个顶层字段。'
        '嵌套结构、数组、字段类型、枚举及组合规则已内置。\n\n'
        f'完整对象 required 为本类别四个官方 JSON Schema required 的并集，共 {len(required)} 个顶层字段；'
        '嵌套 required 只在父对象存在时适用。每个字段说明标注访问级别及语义适用条件。\n\n'
        '受控词表按本地 2026-10-30 TTL 快照展开；完整枚举 URI、枚举名称及解释保留在全量版。'
        '多语言对象的动态值类型按原 Schema 保留不限，说明中列出语义层的文本要求。\n\n'
        'capacityExhaustionThreshold、stateOfCertifiedEnergy 不属于本类别的源 Schema 字段，相关 SHACL maxCount=0，未加入模型。'
        'contentSpecificationIds 在此完整对象中统一承载核心、电池组和对应子类的规范 URI。\n\n'
        '该模型描述护照语义对象；注册请求仍以原始注册 OpenAPI 为准。完整语义校验还需上下文、SHACL／SPARQL 与业务适用条件。'
    )
    if group == 'ind':
        description += ('\n\n工业电池的可充电、循环应用、配有BMS的固定式储能等适用条件，源静态 Schema '
                        '未通过统一 if/then 判别字段编码。因此未列 required 的字段不能一概认定业务可选，'
                        '应结合字段说明中的具体条件。')
    full = {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object', 'title': label + '电池全部语义字段', 'description': description,
        'properties': properties, 'required': [name for name in properties if name in required],
        'x-apifox-orders': list(properties), 'x-model-name': profile['model'],
        'x-battery-subgroup': group, 'x-source-view-field-counts': view_counts, 'examples': [sample],
    }
    full_path = out / (label + '电池全部语义字段.schema.json')
    full_path.write_text(json.dumps(full, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    notes = industrial_notes() if group == 'ind' else {}
    for compact_level in range(3):
        compact = compact_import_schema(full, battery_label=label, business_notes=notes, compact_level=compact_level)
        compact_text = json.dumps(compact, ensure_ascii=False, separators=(',', ':'))
        byte_count = len(compact_text.encode('utf-8'))
        quoted_bytes = len(json.dumps(compact_text, ensure_ascii=False).encode('utf-8'))
        if max(byte_count, quoted_bytes) < 65536:
            break
    else:
        raise AssertionError((profile['model'], 'compact size exceeds export budget', byte_count, quoted_bytes))
    assert constraints_only(full) == constraints_only(compact)
    for schema in [full, compact]:
        Draft202012Validator.check_schema(schema)
    full_description, _, full_enums, enum_rows = inspect_model(full)
    compact_description, compact_titles, compact_enums, _ = inspect_model(compact)
    assert full_enums == compact_enums
    assert max(size for _, size in compact_description) <= 240
    assert max(size for _, size in compact_titles) <= 64
    assert 'EV子类规范' not in compact_text and 'EV全量语义模型' not in compact_text
    compact_path = out / (profile['model'] + '_长度兼容版.schema.json')
    compact_path.write_text(compact_text + '\n', encoding='utf-8')
    assert json.loads(compact_path.read_text()) == compact
    results = verify_examples(profile, full, compact, sample)
    rows = field_rows(full)
    dump_csv(out / (label + '电池_全部字段说明.csv'),
             ['字段路径', '中文名称', '访问级别', 'JSON结构必填', '数据类型', '枚举值', '枚举名称', '完整说明'], rows)
    dump_csv(out / (label + '电池_枚举说明.csv'),
             ['字段路径', '字段名称', '完整枚举值(JSON)', '枚举名称', '枚举完整说明'], enum_rows)
    dump_csv(out / '边界与反例验证.csv', ['验证类别', '输入变更', '验证结果'], results)
    (out / (label + '电池_官方全量样例.json')).write_text(json.dumps(sample, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report = {
        'modelName': profile['model'], 'batterySubgroup': group, 'modelCountPerFile': 1,
        'topLevelFieldCount': len(properties), 'requiredTopLevelFieldCount': len(required),
        'accessViewFieldCounts': view_counts, 'fieldAndArrayItemDescriptionCount': len(rows),
        'allSourceViewFieldsCovered': True, 'validationConstraintsIdenticalBetweenVersions': True,
        'enumFields': len(full_enums), 'enumValueOccurrences': sum(map(len, full_enums.values())),
        'allEnumValuesPreserved': full_enums == compact_enums,
        'officialCompleteExamplePassedBothVersions': True, 'boundaryAndCounterexampleChecks': len(results),
        'compactDocumentationLevel': compact_level, 'compactUtf8Bytes': byte_count,
        'compactAsJsonStringUtf8Bytes': quoted_bytes,
        'maxCompactDescriptionUtf8Bytes': max(size for _, size in compact_description),
        'maxCompactTitleUtf8Bytes': max(size for _, size in compact_titles),
        'compactBudgetIsOfficialApifoxLimit': False,
        'unresolvedReferences': 0, 'apifoxUiSaveTested': False, 'shaclValidationExecuted': False,
        'officialExample': sample_path.relative_to(ASSETS).as_posix(),
        'files': {full_path.name: sha(full_path), compact_path.name: sha(compact_path)},
    }
    (out / '生成与校验结果.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / '导入说明.txt').write_text(
        f'{label}电池单模型：{profile["model"]}，共66个顶层字段，结构必填{len(required)}个。\n'
        f'全量版：{full_path.name}，含完整说明、规则依据和枚举含义，适合查阅与程序校验。\n'
        f'Apifox保存使用：{compact_path.name}。\n'
        '进入 接口管理 → ＋ → 新建数据模型，选择 通过JSON等生成 → JSON Schema。\n'
        '复制长度兼容版的全部内容，应用并保存；更新已有模型时在JSON Schema／编辑源码中全选替换。\n'
        '长度兼容版按与EV相同的导出预算检查：说明最多240个UTF-8字节，标题最多64字节，模型及JSON字符串转义后均小于64KiB。该预算不是Apifox官方限额。\n'
        '两个版本的字段、类型、required、组合约束和完整enum值一致。详细解释另在字段及枚举CSV中保留。\n'
        'required取本类别四个访问视图的并集，业务适用性、组合必填和SHACL补充约束见说明。\n'
        '未列enum的字段为非枚举，按对应type、format和其它约束填写；全部语义字段合并为一个根对象，没有视图包装层。\n'
        '工业电池的可充电、循环应用和BMS等条件按实际适用性判断，Schema层可省略不表示业务层一律可选。\n'
        '本地样例、边界、枚举和长度核验已通过；未在Apifox界面保存实测，未执行完整SHACL/SPARQL校验。\n', encoding='utf-8')
    return out, report


def main():
    source_path = ROOT / '08_Apifox语义模型/dpp-semantic-models-apifox.yaml'
    original_report = json.loads((ROOT / '08_Apifox语义模型/生成与校验报告.json').read_text())
    assert sha(source_path) == original_report['artifactSha256'][source_path.name]
    for name, digest in original_report['sourceFilesSha256'].items():
        assert sha(ASSETS / name) == digest
    source = yaml.safe_load(source_path.read_text())
    outputs = [build_profile(profile, source) for profile in PROFILES]
    for name, digest in original_report['sourceFilesSha256'].items():
        assert sha(ASSETS / name) == digest
    archive = ROOT / '其他电池_JSONSchema_全量及长度兼容版.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for directory, _ in outputs:
            for path in sorted(directory.iterdir()):
                if path.is_file():
                    package.write(path, directory.name + '/' + path.name)
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
    for directory, report in outputs:
        print(json.dumps({'directory': str(directory), **report}, ensure_ascii=False, indent=2))
    print('ZIP:', archive)


if __name__ == '__main__':
    main()
