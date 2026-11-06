"""Build editable Word documentation and validated EV SN integration attachments."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import shutil
import sys
import zipfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET

sys.path.insert(0, '/tmp/sn-api-doc-deps')
from jsonschema import Draft202012Validator, FormatChecker
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ATT = HERE / '对接附件'
ATT.mkdir(exist_ok=True)
SOURCE = BASE / '09_EV电池单模型/EVBatteryPassport_Apifox直接建模.schema.json'
MODEL = json.loads(SOURCE.read_text())
ENRICHED = json.loads((SOURCE.parent / 'EV电池全部语义字段.schema.json').read_text())
OFFICIAL_SAMPLE = BASE / '03_语义资源/product/batteries/subgroup/evb/1.0.0/tests/conforms/batteries-evb.json'
HOST = 'https://battery.chdpp.com'
LOGIN_PATH = '/open-api/dpp-platform/login'
UPLOAD_PATH = '/open-api/dpp-platform/supplier/sn/upload'
EV = 'https://repository.product-passport.ec.europa.eu/product/batteries/vocabs/battery-category#electricVehicleBattery'
UNIT = 'https://repository.product-passport.ec.europa.eu/core/vocabs/measurement-unit#'
SN = 'TEST20261104000000000001'
STEM = '供应商SN对接接口说明_EV全量语义_24位国标码_v1.0'
STAMP = '2026-11-04'
HASH = hashlib.sha256(SOURCE.read_bytes()).hexdigest()


def save_json(name, data):
    (ATT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def walk(schema, prefix='', parent=None, name=None):
    if prefix:
        yield prefix, schema, parent, name
    for key, value in schema.get('properties', {}).items():
        yield from walk(value, f'{prefix}.{key}' if prefix else key, schema, key)
    if isinstance(schema.get('items'), dict):
        yield from walk(schema['items'], prefix + '[]', schema, '[]')


NODES = list(walk(MODEL))
RICH_NODES = {p: n for p, n, _, _ in walk(ENRICHED)}
ENUMS = OrderedDict()
for path, node, _, _ in NODES:
    if 'enum' in node:
        key = json.dumps(node['enum'], ensure_ascii=False)
        if key not in ENUMS:
            ENUMS[key] = {'id': f'E{len(ENUMS)+1:02}', 'values': node['enum'], 'paths': []}
        ENUMS[key]['paths'].append(path)

VOCAB = {}
with (BASE / '08_Apifox语义模型/枚举项明细.csv').open(encoding='utf-8-sig') as fp:
    for row in csv.DictReader(fp):
        VOCAB[row['完整概念URI']] = row

# Keep the requested source untouched. New platform constraints are a separate allOf layer.
SN_CONSTRAINT = {
    'type': 'object',
    'properties': {
        'batteryIdentification': {
            'type': 'object',
            'properties': {'serialNumber': {
                'type': 'string', 'minLength': 24, 'maxLength': 24,
                'description': '平台约束：必填且恰好24个字符；完整国标编码合法性另作业务校验。',
            }},
            'required': ['serialNumber'],
        },
        'batteryCategory': {'const': EV},
    },
}
REQUEST_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    'title': '供应商上传SN请求_EV全量语义_24位国标码',
    'description': 'snList为平台批量封装；每项直接采用EV模型。平台补充SN必填24位、EV分类和顶层字段白名单。',
    'type': 'object', 'additionalProperties': False,
    'properties': {'snList': {
        'type': 'array', 'minItems': 1, 'maxItems': 100,
        'items': {
            'allOf': [{'$ref': '#/$defs/EVBatteryPassport'}, SN_CONSTRAINT],
            'unevaluatedProperties': False,
        },
    }},
    'required': ['snList'], '$defs': {'EVBatteryPassport': copy.deepcopy(MODEL)},
}
LOGIN_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    'title': '鉴权请求', 'type': 'object', 'additionalProperties': False,
    'properties': {
        'userAccount': {'type': 'string', 'minLength': 1, 'maxLength': 50},
        'password': {'type': 'string', 'minLength': 1, 'maxLength': 20},
    }, 'required': ['userAccount', 'password'],
}
save_json('SnUploadRequest_EV_24位国标码.schema.json', REQUEST_SCHEMA)
save_json('LoginRequest.schema.json', LOGIN_SCHEMA)
shutil.copyfile(SOURCE, ATT / SOURCE.name)


def demo_links(value):
    if isinstance(value, dict):
        return {k: demo_links(v) for k, v in value.items()}
    if isinstance(value, list):
        return [demo_links(v) for v in value]
    if isinstance(value, str) and value.startswith(('https://example.', 'https://www.example.')):
        u = urlsplit(value)
        return urlunsplit(('https', 'battery.chdpp.com', '/demo' + u.path, u.query, u.fragment))
    return value


EXAMPLE_ITEM = demo_links(json.loads(OFFICIAL_SAMPLE.read_text()))
EXAMPLE_ITEM.update({
    'digitalProductPassportId': HOST + '/demo/passports/' + SN,
    'uniqueProductIdentifier': HOST + '/demo/products/' + SN,
    'lastUpdate': '2026-11-04T08:00:00+08:00',
    'registrantIdentity': '示例电池护照责任主体（演示）',
    'manufacturerIdentification': {'manufacturerName': '示例新能源电池有限公司（演示）'},
    'manufacturerPostalAddress': '中国上海市嘉定区示例路100号，邮编201800（演示地址）',
    'manufacturerContactDetails': {
        'manufacturerWebsite': HOST + '/demo/manufacturer',
        'manufacturerEmail': 'mailto:supplier@example.com',
    },
    'batteryIdentification': {'modelIdentifier': 'EV-DEMO-75', 'batchNumber': 'DEMO-20261104', 'serialNumber': SN},
    'manufacturingPlace': {'manufacturingCity': '上海市', 'manufacturingRegion': '上海市', 'manufacturingCountry': 'CHN'},
    'manufacturingDate': '2026-11',
    'cycleLifeDischargeCRate': {'unit': UNIT + 'reciprocalHour', 'value': 1.0},
})
# Keep fields in the supplied model's order, including every optional EV field.
EXAMPLE_ITEM = {k: EXAMPLE_ITEM[k] for k in MODEL['properties']}
FULL_REQUEST = {'snList': [EXAMPLE_ITEM]}
MIN_ITEM = {k: copy.deepcopy(EXAMPLE_ITEM[k]) for k in MODEL['required']}
MIN_ITEM['batteryIdentification'] = {'serialNumber': SN}
MIN_ITEM['manufacturingPlace'] = {'manufacturingCity': '上海市', 'manufacturingCountry': 'CHN'}
MIN_REQUEST = {'snList': [MIN_ITEM]}
LOGIN_EXAMPLE = {'userAccount': 'supplier_demo', 'password': '<PASSWORD>'}
AUTH_OK = {'code': '200', 'success': True, 'message': '业务处理成功', 'body': {
    'token': '<TOKEN>', 'userAccount': 'supplier_demo', 'userDomain': 'supplier_demo', 'realName': '示例供应商',
}}
AUTH_FAIL = {'code': -7003, 'success': False, 'message': '没有通过权限验证', 'body': None}
UPLOAD_OK = {'code': '0', 'message': 'success', 'data': None}
UPLOAD_PARTIAL = {'code': '200', 'message': 'success', 'data': [
    {'sn': SN, 'code': '0000', 'message': None},
    {'sn': SN[:-1], 'code': '1001', 'message': 'batteryIdentification.serialNumber必须为24位国标码；当前长度为23'},
]}
for name, value in [
    ('01_鉴权请求.json', LOGIN_EXAMPLE), ('02_鉴权成功响应.json', AUTH_OK),
    ('03_鉴权失败响应.json', AUTH_FAIL), ('04_SN上传_全量63字段请求.json', FULL_REQUEST),
    ('05_SN上传_最小必填请求.json', MIN_REQUEST), ('06_SN上传_全部成功响应.json', UPLOAD_OK),
    ('07_SN上传_存在失败响应.json', UPLOAD_PARTIAL),
]:
    save_json(name, value)


def fmt_type(node):
    typ = node.get('type', '未限定')
    if typ == 'array':
        return f"array<{node.get('items', {}).get('type', 'any')}>"
    return typ if isinstance(typ, str) else '|'.join(typ)


def required(path, node, parent, name):
    if path == 'batteryIdentification.serialNumber':
        return '是（平台约束）'
    if path in ('batteryIdentification.productNumber', 'batteryIdentification.otherIdentificationElement'):
        return '否'
    if path in ('batteryIdentification.modelIdentifier', 'batteryIdentification.batchNumber'):
        return '条件必填（成对）'
    if name == '[]':
        return '数组元素约束'
    if name in parent.get('required', []):
        return '是' if parent is MODEL else '是（父对象存在时）'
    if any(name in branch.get('required', []) for branch in parent.get('anyOf', [])):
        return '条件必填（至少一项）'
    if '适用时填写' in node.get('description', ''):
        return '否（适用时填写）'
    return '否'


def length(path, node):
    if path == 'batteryIdentification.serialNumber':
        return '恰好24字符'
    if path == 'manufacturingDate':
        return '7字符'
    if path == 'manufacturingPlace.manufacturingCountry':
        return '3字符'
    if node.get('type') == 'string':
        lo, hi = node.get('minLength'), node.get('maxLength')
        if lo is not None or hi is not None:
            return (f'{lo}–{hi}字符' if lo is not None and hi is not None else f'≥{lo}字符' if lo is not None else f'≤{hi}字符')
        return '未限定'
    if node.get('type') == 'array':
        lo, hi = node.get('minItems'), node.get('maxItems')
        if lo is not None or hi is not None:
            return f'{lo if lo is not None else 0}–{hi if hi is not None else "不限"}项'
        return '未限定项数'
    return '—'


def facts(path, node):
    parts = []
    if 'enum' in node:
        group = ENUMS[json.dumps(node['enum'], ensure_ascii=False)]
        parts.append(f'枚举{group["id"]}（{len(group["values"])}项，见附录A）')
    for k, text in [('format', '格式'), ('pattern', '正则'), ('minimum', '最小值'), ('maximum', '最大值'),
                    ('exclusiveMinimum', '须大于'), ('exclusiveMaximum', '须小于'), ('multipleOf', '步长'),
                    ('minProperties', '最少键数'), ('maxProperties', '最多键数')]:
        if k in node:
            parts.append(f'{text}：{node[k]}')
    if 'const' in node:
        parts.append('固定值：' + json.dumps(node['const'], ensure_ascii=False))
    if node.get('uniqueItems'):
        parts.append('数组元素不得重复')
    if node.get('additionalProperties') is False or node.get('unevaluatedProperties') is False:
        parts.append('不接收该层未声明的字段')
    if 'anyOf' in node:
        choices = ['+'.join(branch.get('required', [])) for branch in node['anyOf']]
        parts.append('至少满足一组：' + '；或 '.join(choices))
    if 'dependentRequired' in node:
        parts.append('联动必填：' + '；'.join(k + '→' + ','.join(v) for k, v in node['dependentRequired'].items()))
    if 'propertyNames' in node:
        parts.append('动态语言键正则：' + node['propertyNames'].get('pattern', ''))
        parts.append('填写文本；源Schema未限定语言值的JSON类型')
    if path == 'batteryCategory':
        parts.append('本接口仅取electricVehicleBattery完整URI；源枚举另含LMT和IND')
    if path == 'batteryIdentification':
        parts.append('本接口另要求serialNumber必填且恰好24字符')
    if path == 'batteryIdentification.serialNumber':
        parts.append('原模型仅string且minLength=1；国标分段/登记合法性另作业务校验')
    if path == 'contentSpecificationIds':
        parts.append('语义要求core、电池组、EV子组规范各1个；源Schema未设minItems=3')
    if path.endswith('.contentType'):
        parts.append('无封闭枚举；填写资源实际MIME类型，例如application/pdf')
    if path == 'manufacturerPostalAddress':
        parts.append('普通地址字符串；模型未分中英文子字段，填写完整可投递地址')
    if path == 'manufacturingDate':
        parts.append('YYYY-MM；不填写时间戳或YYYY-MM-DD；正则不验证月份实际范围')
    if path == 'manufacturerContactDetails.manufacturerEmail':
        parts.append('按URI填写，例如mailto:supplier@example.com')
    return '；'.join(parts)


def english(path):
    description = RICH_NODES.get(path, {}).get('description', '')
    match = re.search(r'官方英文定义：\s*(.*?)(?:\n\n|$)', description, re.S)
    return match.group(1).strip() if match else ''


FIELD_ROWS = []
for i, (path, node, parent, name) in enumerate(NODES, 1):
    source_constraints = {k: v for k, v in node.items() if k not in ('title', 'description', 'properties', 'items')}
    FIELD_ROWS.append({
        '序号': i, '字段路径（相对snList[]）': path, '中文名称': node.get('title', ''),
        '数据类型': fmt_type(node), '长度/数量': length(path, node), '是否必填（本接口）': required(path, node, parent, name),
        '披露视图': node.get('description', '').split('；')[0],
        '约束与填写说明': facts(path, node), '官方英文定义（可追溯项）': english(path),
        '源Schema约束JSON': json.dumps(source_constraints, ensure_ascii=False),
        '原模型字段说明': node.get('description', ''),
        '来源': json.dumps(RICH_NODES.get(path, {}).get('x-source-locations', []), ensure_ascii=False),
    })
with (ATT / 'EV字段字典_206节点.csv').open('w', encoding='utf-8-sig', newline='') as fp:
    writer = csv.DictWriter(fp, fieldnames=list(FIELD_ROWS[0]))
    writer.writeheader()
    writer.writerows(FIELD_ROWS)
with (ATT / '枚举字典_完整取值.csv').open('w', encoding='utf-8-sig', newline='') as fp:
    writer = csv.writer(fp)
    writer.writerow(['枚举编号', '适用字段（相对snList[]）', '取值', '官方名称', '符号'])
    for group in ENUMS.values():
        for val in group['values']:
            meta = VOCAB.get(val, {}) if isinstance(val, str) else {}
            writer.writerow([group['id'], '\n'.join(group['paths']), json.dumps(val, ensure_ascii=False) if not isinstance(val, str) else val,
                             meta.get('官方名称', ''), meta.get('别名或符号', '')])

# Excel companion is generated from the same rows as Word, without hand-entered aliases.
wb = Workbook()
ws = wb.active
ws.title = 'EV完整字段206节点'
ws.append(list(FIELD_ROWS[0]))
for row in FIELD_ROWS:
    ws.append(list(row.values()))
ws.freeze_panes = 'C2'
ws.auto_filter.ref = ws.dimensions
for col, width in {'A': 7, 'B': 56, 'C': 30, 'D': 19, 'E': 19, 'F': 28, 'G': 14, 'H': 95, 'I': 65, 'J': 80, 'K': 65, 'L': 65}.items():
    ws.column_dimensions[col].width = width
for row in ws:
    for cell in row:
        cell.alignment = Alignment(vertical='top', wrap_text=True)
        cell.font = Font(name='等线', size=10, bold=cell.row == 1, color='FFFFFF' if cell.row == 1 else '172B4D')
        if cell.row == 1:
            cell.fill = PatternFill('solid', fgColor='244C70')
    ws.row_dimensions[row[0].row].height = 32 if row[0].row == 1 else 72
enum_ws = wb.create_sheet('完整枚举')
with (ATT / '枚举字典_完整取值.csv').open(encoding='utf-8-sig') as fp:
    for row in csv.reader(fp):
        enum_ws.append(row)
enum_ws.freeze_panes = 'A2'
enum_ws.auto_filter.ref = enum_ws.dimensions
for col, width in {'A': 12, 'B': 70, 'C': 120, 'D': 35, 'E': 20}.items():
    enum_ws.column_dimensions[col].width = width
for row in enum_ws:
    for cell in row:
        cell.alignment = Alignment(vertical='top', wrap_text=True)
        if cell.row == 1:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='244C70')
wb.save(ATT / 'EV字段字典及枚举_206节点.xlsx')

# Meaningful positive/negative validation. These are document fixture checks, not service tests.
validator = Draft202012Validator(REQUEST_SCHEMA, format_checker=FormatChecker())
assert 'date-time' in validator.format_checker.checkers and 'uri' in validator.format_checker.checkers
Draft202012Validator.check_schema(REQUEST_SCHEMA)
Draft202012Validator.check_schema(LOGIN_SCHEMA)
REPORT = []


def check(label, request, expected, used=validator):
    errors = sorted(used.iter_errors(request), key=lambda e: '/'.join(map(str, e.absolute_path)))
    actual = not errors
    REPORT.append({'用例': label, '预期通过': expected, '实际通过': actual,
                   '测试结果': 'PASS' if actual == expected else 'FAIL',
                   '首个错误': ('/'.join(map(str, errors[0].absolute_path)) + ': ' + errors[0].message) if errors else None})
    if actual != expected:
        raise AssertionError(label + ': ' + str(errors[:1]))


def changed(path, value=None, remove=False, source=FULL_REQUEST):
    request = copy.deepcopy(source)
    target = request['snList'][0]
    keys = path.split('.')
    for key in keys[:-1]:
        target = target[key]
    if remove:
        target.pop(keys[-1], None)
    else:
        target[keys[-1]] = value
    return request


check('完整EV请求：全部63顶层字段', FULL_REQUEST, True)
check('最小必填请求：52顶层必填及平台SN', MIN_REQUEST, True)
for length_ in [23, 25]:
    check(f'SN长度{length_}须拒绝', changed('batteryIdentification.serialNumber', 'A' * length_), False)
check('SN缺失须拒绝', changed('batteryIdentification.serialNumber', remove=True), False)
check('SN数字类型须拒绝', changed('batteryIdentification.serialNumber', 123456789012345678901234), False)
check('非EV分类须拒绝', changed('batteryCategory', MODEL['properties']['batteryCategory']['enum'][1]), False)
check('modelIdentifier缺少配对batchNumber须拒绝', changed('batteryIdentification.batchNumber', remove=True), False)
check('制造商三个识别字段全缺失须拒绝', changed('manufacturerIdentification', {}), False)
check('制造城市和地区同时缺失须拒绝', changed('manufacturingPlace', {'manufacturingCountry': 'CHN'}), False)
check('两位制造国家码须拒绝', changed('manufacturingPlace.manufacturingCountry', 'CN'), False)
check('制造时间填写日期须拒绝', changed('manufacturingDate', '2026-11-04'), False)
check('lastUpdate不是date-time须拒绝', changed('lastUpdate', '2026-11'), False)
check('单位缩写替代完整URI须拒绝', changed('batteryWeight.unit', 'kg'), False)
check('separateCollectionSymbol=false须拒绝', changed('separateCollectionSymbol', False), False)
check('多语言en键不符合源正则须拒绝', changed('extinguishingAgent', {'en': 'Example'}), False)
check('空多语言对象须拒绝', changed('extinguishingAgent', {}), False)
check('资源缺少contentType须拒绝', changed('euDeclarationOfConformity.contentType', remove=True), False)
check('资源url不是URI须拒绝', changed('euDeclarationOfConformity.url', 'not a uri'), False)
check('直方图负频数须拒绝', changed('stateOfChargeHistogram', [{
    'binRangeUnit': UNIT + 'percent', 'binValueUnit': UNIT + 'hour',
    'bin': [{'lowerBound': 0, 'upperBound': 20, 'binValue': -1}],
}]), False)
check('旧版sn别名不属于EV字段须拒绝', changed('sn', SN), False)
check('嵌套对象未声明字段须拒绝', changed('batteryIdentification.extra', 'x'), False)
check('0条批次须拒绝', {'snList': []}, False)
check('101条批次须拒绝', {'snList': [MIN_ITEM] * 101}, False)
check('根封装多余字段须拒绝', {'snList': [MIN_ITEM], 'extra': True}, False)
check('鉴权请求示例', LOGIN_EXAMPLE, True, Draft202012Validator(LOGIN_SCHEMA))
check('鉴权密码21字符须拒绝', {'userAccount': 'demo', 'password': 'a' * 21}, False, Draft202012Validator(LOGIN_SCHEMA))
# Explicitly demonstrate limits of the supplied model. These passing cases are not recommendations.
check('边界记录：月份13仅按源正则仍通过结构校验', changed('manufacturingDate', '2026-13'), True)
check('边界记录：多语言数字值源Schema未限制类型', changed('extinguishingAgent', {'en-GB': 123}), True)
check('边界记录：重复SN需业务层判定，Schema不跨条去重', {'snList': [MIN_ITEM, MIN_ITEM]}, True)
check('边界记录：24个任意字符不代表国标编码合规', changed('batteryIdentification.serialNumber', '字' * 24), True)
check('边界记录：部分源数组必填但未设minItems', changed('hazardousSubstances', []), True)
check('原模型接受官方原始示例', json.loads(OFFICIAL_SAMPLE.read_text()), True,
      Draft202012Validator(MODEL, format_checker=FormatChecker()))
assert len(NODES) == 206 and len(MODEL['properties']) == 63 and len(MODEL['required']) == 52
assert REQUEST_SCHEMA['$defs']['EVBatteryPassport'] == MODEL
assert SOURCE.read_bytes() == (ATT / SOURCE.name).read_bytes()
save_json('校验报告.json', {
    '日期': STAMP, '来源文件': SOURCE.name, '来源SHA256': HASH,
    '统计': {'顶层字段': 63, '顶层必填': 52, '显式字段及数组元素节点': 206, '枚举集合': len(ENUMS), '校验用例': len(REPORT)},
    '校验方式': 'JSON Schema draft 2020-12 + FormatChecker；未调用真实接口、未做完整SHACL及国标编码合规验证。',
    '结果': REPORT,
})

# Minimal, native OOXML writer. All text, tables, headings and code remain editable in Word/WPS.
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
ET.register_namespace('w', W)
ET.register_namespace('r', R)


def el(name, attrs=None, text=None):
    node = ET.Element(f'{{{W}}}{name}', {f'{{{W}}}{k}': str(v) for k, v in (attrs or {}).items()})
    if text is not None:
        node.text = text
    return node


DOC = el('document')
BODY = ET.SubElement(DOC, f'{{{W}}}body')
BOOKMARKS = []
ORIENTATION = 'portrait'


def run(parent, text, *, bold=False, size=None, color=None, font=None):
    r = el('r')
    rp = el('rPr')
    if bold:
        rp.append(el('b'))
    if size:
        rp.append(el('sz', {'val': int(size * 2)}))
    if color:
        rp.append(el('color', {'val': color}))
    if font:
        rp.append(el('rFonts', {'ascii': font, 'hAnsi': font, 'eastAsia': 'Noto Sans CJK SC'}))
    if len(rp):
        r.append(rp)
    for i, line in enumerate(str(text).split('\n')):
        if i:
            r.append(el('br'))
        t = el('t', text=line)
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        r.append(t)
    parent.append(r)
    return r


def paragraph(text='', style=None, *, parent=None, align=None, before=0, after=100, page_break=False, **kw):
    parent = BODY if parent is None else parent
    p = el('p')
    pp = el('pPr')
    if style:
        pp.append(el('pStyle', {'val': style}))
    if align:
        pp.append(el('jc', {'val': align}))
    pp.append(el('spacing', {'before': before, 'after': after, 'line': 300, 'lineRule': 'auto'}))
    if page_break:
        pp.append(el('pageBreakBefore'))
    if style not in ('Heading1', 'Heading2', 'Heading3'):
        pp.append(el('widowControl', {'val': 0}))
    p.append(pp)
    if text:
        run(p, text, **kw)
    parent.append(p)
    return p


def heading(text, level=1, page_break=False):
    p = paragraph(text, f'Heading{level}', before=180 if level > 1 else 220, after=120, page_break=page_break)
    idx = len(BOOKMARKS) + 1
    bookmark = '_sn_' + str(idx)
    p.insert(1, el('bookmarkStart', {'id': idx, 'name': bookmark}))
    p.append(el('bookmarkEnd', {'id': idx}))
    BOOKMARKS.append((level, text, bookmark))
    return p


def breakable(value):
    # Only presentation tables get zero-width break opportunities. JSON examples stay byte-faithful.
    return re.sub(r'([./#_\-])', lambda m: m[0] + '\u200b', str(value))


def table(headers, rows, widths=None, size=9):
    total = 14800 if ORIENTATION == 'landscape' else 9740
    if widths is None:
        widths = [1] * len(headers)
    cols = [round(total * w / sum(widths)) for w in widths]
    cols[-1] += total - sum(cols)
    t = el('tbl')
    pr = el('tblPr')
    pr.append(el('tblW', {'w': total, 'type': 'dxa'}))
    pr.append(el('tblLayout', {'type': 'fixed'}))
    borders = el('tblBorders')
    for key in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
        borders.append(el(key, {'val': 'single', 'sz': 4, 'color': 'B6C4D0'}))
    pr.append(borders)
    margins = el('tblCellMar')
    for key in ['top', 'left', 'bottom', 'right']:
        margins.append(el(key, {'w': 65 if key in ('top', 'bottom') else 85, 'type': 'dxa'}))
    pr.append(margins)
    t.append(pr)
    grid = el('tblGrid')
    for width in cols:
        grid.append(el('gridCol', {'w': width}))
    t.append(grid)
    for index, data in enumerate([headers] + list(rows)):
        tr = el('tr')
        trp = el('trPr')
        trp.append(el('cantSplit'))
        if index == 0:
            trp.append(el('tblHeader'))
        tr.append(trp)
        for j, value in enumerate(data):
            tc = el('tc')
            tcp = el('tcPr')
            tcp.append(el('tcW', {'w': cols[j], 'type': 'dxa'}))
            tcp.append(el('vAlign', {'val': 'top'}))
            if index == 0:
                tcp.append(el('shd', {'fill': 'E6EDF4'}))
            tc.append(tcp)
            p = paragraph(breakable(value), parent=tc, after=0, size=size, bold=index == 0)
            p.find(f'{{{W}}}pPr/{{{W}}}spacing').set(f'{{{W}}}line', '255')
            tr.append(tc)
        t.append(tr)
    BODY.append(t)
    paragraph('', after=30)
    return t


def code(value):
    text = json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value
    for line in text.splitlines():
        p = paragraph(line or ' ', 'Code', after=0, font='DejaVu Sans Mono', size=8)
        pp = p.find(f'{{{W}}}pPr')
        pp.find(f'{{{W}}}spacing').set(f'{{{W}}}line', '240')
        pp.append(el('shd', {'fill': 'F4F6F8'}))


def section_props(orientation='portrait', *, cover=False):
    sp = el('sectPr')
    sp.append(ET.Element(f'{{{W}}}headerReference', {f'{{{W}}}type': 'default', f'{{{R}}}id': 'rHeader'}))
    sp.append(ET.Element(f'{{{W}}}footerReference', {f'{{{W}}}type': 'default', f'{{{R}}}id': 'rFooter'}))
    sp.append(el('type', {'val': 'nextPage'}))
    sp.append(el('pgSz', {'w': 16838 if orientation == 'landscape' else 11906,
                           'h': 11906 if orientation == 'landscape' else 16838,
                           'orient': orientation}))
    sp.append(el('pgMar', {'top': 1020, 'right': 1020, 'bottom': 1020, 'left': 1020,
                           'header': 430, 'footer': 430, 'gutter': 0}))
    if cover:
        sp.append(el('titlePg'))
    return sp


def section(orientation, *, cover=False):
    global ORIENTATION
    p = paragraph('', after=0)
    p.find(f'{{{W}}}pPr').append(section_props(ORIENTATION, cover=cover))
    ORIENTATION = orientation


# Cover, revision record and template-style table of contents.
paragraph('电池护照（DPP）平台', 'Title', align='center', before=1700, after=280, size=26, bold=True)
paragraph('接口对接标准说明', 'Title', align='center', after=220, size=24, bold=True)
paragraph('供应商上传 SN 接口', 'Subtitle', align='center', after=120, size=20, bold=True)
paragraph('EV 电池全量语义字段版 · 24 位国标码', align='center', after=500, size=15)
table(['文档项目', '内容'], [
    ['文档版本', 'v1.0（评审稿）'], ['文档日期', STAMP],
    ['文档编号', '待填写'], ['编撰 / 审核 / 批准', '项目组 / 待填写 / 待填写'],
    ['服务域名', HOST], ['适用电池分类', 'EV（电动汽车电池）'],
], [2, 5], 11)
paragraph('2026 年 11 月', align='center', before=350, size=14)
section('portrait', cover=True)
heading('文档修订记录', 1)
table(['序号', '版本', '修改日期', '修改人', '批准人', '备注'], [[
    '1', 'v1.0', STAMP, '项目组', '待填写', '按参考文档结构编写；采用完整EV模型；SN必填且恰好24位；统一域名。',
]], [0.6, 0.8, 1.2, 0.9, 0.9, 3.2])
paragraph('本文是接口设计及对接说明，供评审与联调使用。新增EV请求结构及平台业务规则需要服务端按本版实现；本文不表示线上接口已完成改造或验证。')
paragraph('字段校验以指定的EV JSON Schema为基础，平台附加规则单列说明；不得由字段名称推断或另行增加必填字段。')
heading('目    录', 1, page_break=True)
TOC_INSERT = len(BODY)
paragraph('（在 Word/WPS 中更新目录可刷新页码。）', color='68788A', size=9)

heading('1. 前言描述', page_break=True)
paragraph('本文说明供应商系统向电池护照（DPP）平台上传EV电池SN及护照完整语义数据的接口规范，包括鉴权、请求字段、填写方式、返回协议和异常处理。')
paragraph('请求字段来自 EVBatteryPassport_Apifox直接建模.schema.json；参考《供应商接口对接标准说明-20260818》的章节、参数表及鉴权逻辑。供应商上传时，每条数据直接使用EV对象，SN统一填写在 batteryIdentification.serialNumber。')
heading('1.1 文档范围', 2)
table(['接口', '用途', '请求地址'], [
    ['鉴权接口', '使用平台分配的账号、密码及apikey获取token', HOST + LOGIN_PATH],
    ['供应商上传SN接口', '批量提交完整EV语义对象，1–100条/次', HOST + UPLOAD_PATH],
], [1.5, 2.2, 4.3])
heading('1.2 通用调用约定', 2)
table(['项目', '约定'], [
    ['传输协议 / 字符编码', 'HTTPS / UTF-8'], ['方法 / 数据格式', 'POST / application/json'],
    ['业务鉴权', '登录时Header传apikey；业务接口Header传token，值直接取body.token'],
    ['时间格式', 'manufacturingDate：YYYY-MM；lastUpdate：date-time，例如2026-11-04T08:00:00+08:00。其他字段按各自约束填写。'],
    ['单次条数', 'snList必填，1–100条；每条为完整EV对象。'],
    ['数值 / 布尔', 'number、integer及boolean使用JSON原生类型；不得以字符串替代。'],
    ['计量单位', 'unit使用模型给出的完整URI；例如电压使用measurement-unit#volt，不直接填写V。'],
    ['枚举', '有enum时取模型列出的完整值；无enum的字段不自行补造封闭枚举。'],
    ['披露视图', '字段保留PUB/RES1/RES2/RES3来源标识。全量上传不改变这些字段的对外披露权限。'],
], [1.6, 6.4])
heading('1.3 字段来源与平台约束', 2)
table(['来源', '本版采用的规则'], [
    ['指定EV模型', '63个顶层字段，其中52个顶层必填；展开为206个显式字段及数组元素节点。嵌套必填、枚举、类型、格式、anyOf及联动条件按原文件保留。'],
    ['平台批量封装', '新增根对象snList，1–100项。snList不属于欧盟语义字段。'],
    ['平台SN要求（用户确认）', 'batteryIdentification.serialNumber必填，string，恰好24字符；须为符合国标的电池编码。源模型仅要求其在存在时为非空字符串。'],
    ['平台接口范围', 'batteryCategory限定为electricVehicleBattery完整URI。源模型还保留LMT、工业电池两个枚举，见附录A。'],
    ['平台字段边界', '批量根对象仅接收snList；每条对象仅接收EV模型声明的63个顶层字段。嵌套层的未知字段处理沿用源模型。'],
], [1.7, 6.3])
paragraph('本版“24位”在结构校验中指字符串恰好24个字符，不是数值类型。GB/T 34014—2017《汽车动力蓄电池编码规则》及现行第1号修改单的字符、分段及生产企业代码等合法性须另作业务校验；长度正确不代表完整国标校验通过。附件SN为虚构的24字符测试占位码，不代表已登记国标编码。')

heading('2. 接口集成标准说明', page_break=True)
heading('2.1 鉴权接口设计', 2)
heading('2.1.1 请求参数及示例', 3)
paragraph('接口用途：通过平台分配的账号、密码和apikey取得访问令牌，供后续业务接口使用。')
table(['项目', '说明'], [
    ['接口编码 / 名称', 'NO1 / 鉴权接口'], ['提供方 / 调用方向', 'DPP平台 / 供应商系统 → DPP平台'],
    ['请求方法 / 调用频率', 'POST / 60次每分钟（沿用参考文档）'], ['请求地址', HOST + LOGIN_PATH],
    ['Content-Type', 'application/json; charset=UTF-8'], ['apikey', '<API_KEY>，平台分配，Header必填'],
], [1.8, 6.2])
table(['序号', '字段名称', '类型', '长度', '是否必填', '说明'], [
    ['1', 'userAccount', 'string', '1–50字符', '是', '平台分配的供应商登录账号。'],
    ['2', 'password', 'string', '1–20字符', '是', '平台分配的登录密码。'],
], [0.5, 1.4, 0.8, 1, 0.8, 3.5])
paragraph('账号、密码在JSON请求体中传递，apikey放入Header。以下均为占位值，不能直接用于生产认证。')
code('POST ' + HOST + LOGIN_PATH + '\nContent-Type: application/json; charset=UTF-8\napikey: <API_KEY>')
code(LOGIN_EXAMPLE)
heading('2.1.2 返回参数及鉴权逻辑', 3)
table(['字段名称', '类型', '是否必返', '说明'], [
    ['code', 'string | integer', '是', '兼容参考文档字符串200与数值-7003的示例；比较时先转为字符串。'],
    ['success', 'boolean', '是', '认证是否成功；仅code转字符串为200且success=true时取得令牌。'],
    ['message', 'string', '否', '结果说明。'], ['body', 'object | null', '是', '成功时为账号及令牌对象；失败可为null。'],
    ['body.token', 'string', '成功时是', '后续业务请求凭证；使用原值，不附加Bearer前缀。'],
    ['body.userAccount', 'string', '否', '账号。'], ['body.userDomain', 'string', '否', '账号所属域。'], ['body.realName', 'string', '否', '账号名称。'],
], [1.5, 1.1, 1, 4.4])
paragraph('成功返回示例：')
code(AUTH_OK)
paragraph('认证失败示例：')
code(AUTH_FAIL)
paragraph('调用顺序：先鉴权；成功后从body.token取值；上传接口Header添加token: <TOKEN>。参考文档没有给出token固定有效期、刷新接口或自动续期协议，本版不虚构这些字段。')
paragraph('HTTP 401时重新鉴权并最多重试一次；认证业务码-7003时核对账号、密码、apikey。403时检查应用授权。缓存token并控制并发重复登录，账号、密码、apikey及完整token不写入日志。')
paragraph('参考文档的body类型表与示例不一致，本版依据示例及鉴权逻辑统一为object|null，token路径为body.token。')

heading('2.2 供应商上传SN接口设计', 2, page_break=True)
heading('2.2.1 接口用途及设计', 3)
paragraph('接口用途：供应商按一条EV电池一条记录，上传24位国标SN以及相应护照语义信息。平台校验账号权限、字段结构与SN业务规则，逐条处理并返回结果。')
table(['项目', '说明'], [
    ['接口名称 / 提供方', '供应商上传SN接口 / DPP平台'], ['请求地址', HOST + UPLOAD_PATH],
    ['请求方法 / 数据格式', 'POST / application/json; charset=UTF-8'], ['业务请求头token', '<TOKEN>，必填；取鉴权响应body.token原值'],
    ['请求封装', 'snList数组，1–100项，每项直接为EVBatteryPassport对象'],
    ['SN字段路径', 'snList[].batteryIdentification.serialNumber'], ['批次处理方式', '批次内逐条校验、逐条处理；存在失败时返回逐条结果。'],
], [1.8, 6.2])
code('POST ' + HOST + UPLOAD_PATH + '\nContent-Type: application/json; charset=UTF-8\ntoken: <TOKEN>')
paragraph('请求结构示意（省略号仅用于说明结构，不可直接发送）：')
code('snList\n  └─ EVBatteryPassport 对象\n       ├─ batteryIdentification.serialNumber：24位国标码\n       ├─ batteryCategory：electricVehicleBattery完整URI\n       └─ 其他字段：完整EV模型；见2.2.2及附录B完整JSON')
paragraph('完整可解析、可通过结构校验的请求见附录B及附件04_SN上传_全量63字段请求.json；最小必填请求见附件05_SN上传_最小必填请求.json。示例的业务标识、制造商、测试数据和资源链接均用于演示。')

section('landscape')
heading('2.2.2 请求字段说明', 3)
paragraph('字段路径相对每一个snList数组元素；拼接snList[].即为完整请求路径。“是（父对象存在时）”表示字段所在对象/数组元素已提交时必须提供；不能理解为可选父对象也必须提交。“数组元素约束”说明元素类型及内部结构，不单独决定数组是否必填或是否允许为空。')
paragraph('本接口SN已必填，因此batteryIdentification中的productNumber、otherIdentificationElement保持可选；modelIdentifier与batchNumber仍须同时提供或同时省略。制造商名称/商号/商标至少一项，制造城市/地区至少一项，制造国家始终必填。')
table(['序号', '字段名称', '类型', '长度/数量', '是否必填', '说明'], [
    ['A', 'snList', 'array<object>', '1–100项', '是（平台封装）', '上传记录数组。'],
    ['A.1', 'snList[]', 'object', '—', '数组元素约束', '每条为完整EV模型；分类限EV；SN必填且24字符。'],
], [0.55, 3.5, 1.1, 1.05, 1.4, 5.4], 9)
for offset in range(0, len(FIELD_ROWS), 40):
    chunk = FIELD_ROWS[offset:offset+40]
    if offset:
        paragraph(f'EV字段明细（续）：第{offset+1}–{offset+len(chunk)}项', bold=True, before=100)
    rows = []
    for row in chunk:
        description = row['中文名称'] + '。' + row['原模型字段说明'].split('。', 1)[-1].strip()
        # Drop duplicate enum/format prose; the generated constraint text below is exact.
        description = re.sub(r'非枚举。|枚举\d+项，使用完整URI。|格式：[^。]+。', '', description).strip()
        description += ('\n' if row['约束与填写说明'] else '') + row['约束与填写说明']
        rows.append([str(row['序号']), row['字段路径（相对snList[]）'], row['数据类型'], row['长度/数量'], row['是否必填（本接口）'], description])
    table(['序号', '字段名称（相对snList[]）', '类型', '长度/数量', '是否必填', '说明'], rows,
          [0.55, 3.5, 1.1, 1.05, 1.4, 5.4], 8.5)
paragraph('字典覆盖全部206个显式字段/数组元素节点；语言对象的动态键不计入固定字段数量，其键名及值的边界见下节。附件Excel、CSV与此表由同一模型生成。')

section('portrait')
heading('2.2.3 填写规则与业务处理说明', 3)
table(['场景', '填写或处理约定'], [
    ['SN编码', 'string，必填，恰好24字符，保留前导0。须使用供应商真实国标码。长度校验之后还须执行国标字符/分段/登记等业务检查。'],
    ['制造年月 / 国家', 'manufacturingDate为YYYY-MM，例2026-11；manufacturingCountry为3位大写国家/辖区代码，例CHN。模型正则不校验真实日历月份或完整代码清单。'],
    ['多语言对象', '至少一个形如xx-XX的键，例zh-CN或en-IE，值按语义填写文本。源Schema键正则只接收两位字母-两位字母，并未为语言值声明JSON类型；语言键不是无限BCP47标签集合。'],
    ['普通字符串', 'manufacturerPostalAddress为普通字符串，填写完整地址；不改造成{zh_CN,en}对象。digitalProductPassportId/uniqueProductIdentifier依源模型为string，源文件未设URI format。'],
    ['资源对象', 'contentType填写实际MIME类型，url为资源URI；resourceTitle、language按模型可选。资源通过url提供，不在此接口中上传二进制内容。'],
    ['contentType示例', 'application/pdf、application/json、image/png、image/jpeg、application/vnd.openxmlformats-officedocument.wordprocessingml.document等仅为格式示例；源模型没有封闭枚举。'],
    ['数量和范围', '数量对象保留unit与value。百分比不自动推定0–100；小数精度、上下界关系仅在模型声明或业务明确规定时校验。powerLimit保持数组结构。'],
    ['必填与空值', '必填指字段必须存在；能否为空字符串/空数组/空对象由具体minLength/minItems/minProperties等决定。无null类型的字段不得用null代替省略。'],
    ['未适用的动态信息', 'numberOfChargingCycles、负面事件、直方图等源模型可选字段在实际适用时填写。已在required中的动态参数仍按源模型必填，不填造虚假数据。'],
    ['批次内重复SN', '同批相同SN的记录均返回1002，不以数组元素整体不同作为允许重复的理由。'],
    ['重复上传', '本版设计约定：同一账号/主体、同一SN、相同内容的未激活记录允许幂等重试；已激活SN返回1003；未激活但内容冲突返回1008并说明原因，不自动覆盖。服务端需按此实现。'],
    ['权限与主体', '供应商身份及授权主体由登录账号配置关联，不额外增加supplierCode/customerCompanyCode等非EV字段。受限视图字段的读取权限由平台另行控制。'],
], [1.45, 6.55], 9)
paragraph('源模型和完整语义验证存在边界：部分数组未设minItems，多语言值未设string类型，部分SHACL基数/条件和国标码分段未在JSON Schema中表达。附件校验报告明确记录这些边界；Schema通过只代表结构约束通过，不代表完成全部语义或国标认证。')
paragraph('contentSpecificationIds须按语义提供core、电池组、EV子组规范各一个；示例均使用本批资源版本1.0.0。完整URI保持欧盟资源域名，不改写为平台服务域名。')
heading('2.2.4 返回协议及异常处理', 3)
paragraph('全部成功：HTTP 200，业务code="0"，data=null。')
code(UPLOAD_OK)
paragraph('存在任一条失败：HTTP 200，业务code="200"，data按输入顺序给出本批所有记录的处理结果。此处200是业务码，不代表所有记录成功，也不同于鉴权接口的成功业务码200。')
code(UPLOAD_PARTIAL)
table(['字段名称', '类型', '说明'], [
    ['code', 'string', '0=全部处理成功；200=批次处理完成但至少一条失败。'],
    ['message', 'string', '批次结果描述，必须结合code及data判断。'],
    ['data', 'array<object> | null', '全部成功可为null；存在失败时返回逐条结果数组。'],
    ['data[].sn', 'string | null', '回显batteryIdentification.serialNumber；缺失/非字符串时为null，依数组顺序对应输入记录。'],
    ['data[].code', 'string', '逐条结果码，0000为成功。'],
    ['data[].message', 'string | null', '逐条错误说明；成功可为null。尽量给出字段路径与原因。'],
], [1.5, 1.5, 5])
table(['HTTP状态', '场景', '处理建议'], [
    ['200', '批次已处理', '读取业务code及每条结果，不能只判断HTTP200。'],
    ['400', 'JSON无法解析或根封装无效，如snList缺失、非数组、0条或超过100条', '修正请求体后重传；不进入逐条业务处理。'],
    ['401', 'token缺失、无效或已过期', '重新鉴权后最多重试一次。'],
    ['403', '账号无接口权限或无相应主体权限', '核对平台授权配置。'],
    ['500', '服务端未完成处理或发生异常', '核对处理状态，使用同一SN及相同内容受控重试，避免更改数据后盲目重传。'],
], [0.8, 3.1, 4.1])
table(['业务码', '含义', '适用与处理建议'], [
    ['0000', '本条成功', '无需重传。'], ['1001', '字段格式或编码校验失败', '检查字段路径、类型、必填、枚举以及24位国标码完整规则。'],
    ['1002', '本批SN重复', '对SN去重后再提交；不是对整个EV对象去重。'],
    ['1003', 'SN对应护照已激活', '不得重复上传，按平台已激活流程处理。'],
    ['1004', '参考文档保留：passcode校验错误', '当前EV请求没有passcode/passCodes字段。本版不增加对应字段；后端若仍返回此码，需核对兼容映射。'],
    ['1005', '公司配置不存在', '核对账号关联的公司主体配置；本版不新增customerCompanyCode请求字段。'],
    ['1006', '供应商配置不存在', '核对账号关联的供应商配置；本版不新增supplierCode请求字段。'],
    ['1007', '参考文档保留：工厂标识格式错误', '当前EV模型没有factoryIdentityCode字段。本版不新增对应字段；若后端仍返回，需核对兼容映射。'],
    ['1008', '其他业务错误或未激活记录内容冲突', '依据message处理；幂等重试不得覆盖不一致内容。'],
], [0.7, 2, 5.3], 9)
paragraph('本版逐条参数失败以1001返回；凭证/权限或整个封装错误按对应HTTP状态返回。网络超时无法直接断定服务端未入库，应保留原始SN和请求内容，核对状态后重试。')
paragraph('参考文档的data类型表写为object，但失败示例为数组，本版统一为array|null；成功码与鉴权码分别按各自协议解释。')

section('landscape')
heading('附录A. 完整枚举字典')
paragraph('以下18组枚举完整列出指定源模型的全部取值。相同枚举集合合并展示，并列出适用字段。URI取值必须提交完整字符串；末尾代码只用于阅读。batteryCategory同时展示源模型的3项，但本SN接口仅接受EV项。')
for group in ENUMS.values():
    heading(f'{group["id"]} · {len(group["values"])}项', 2)
    paragraph('适用字段：' + '；'.join(group['paths']), size=9)
    rows = []
    for index, val in enumerate(group['values'], 1):
        meta = VOCAB.get(val, {}) if isinstance(val, str) else {}
        display = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
        rows.append([str(index), display, meta.get('官方名称', '固定为true' if val is True else ''), meta.get('别名或符号', '')])
    table(['序号', '完整取值', '官方名称', '符号'], rows, [0.5, 8.5, 2.5, 1], 8.5)
heading('附录B. 完整请求示例', page_break=True)
paragraph('本示例包含全部63个EV顶层字段，可通过附件请求Schema及format校验。它由官方conforms示例扩充缺少的可选字段后生成，并替换演示标识、制造商及业务资源链接。数值仅演示结构，不代表某个真实电池的检测结果或认证数据。')
paragraph(f'SN占位码：{SN}（24字符，仅用于结构测试，不代表已登记国标码）。所有/demo链接均为演示链接，不承诺存在真实资源；欧盟枚举/规范URI维持原始域名。')
code(FULL_REQUEST)

section('portrait')
heading('附录C. 来源、附件与验证说明')
table(['资料', '来源与用途'], [
    ['文档格式与鉴权', '供应商接口对接标准说明-20260818.docx；参考链接见下。沿用登录路径、账号密码及apikey登录、业务Header传token、批量条数和主要响应习惯。'],
    ['字段依据', SOURCE.name + '；63顶层字段/52顶层必填/206展开节点。附带原文件副本及独立请求封装Schema。'],
    ['官方示例', '03_语义资源/product/batteries/subgroup/evb/1.0.0/tests/conforms/batteries-evb.json；用于结构示例的基础。'],
    ['补充字段释义', 'EV电池全部语义字段.schema.json及官方词表；仅补充来源、英文释义与枚举标签，不覆盖指定源模型的结构约束。'],
    ['国标参考', 'GB/T 34014—2017《汽车动力蓄电池编码规则》及第1号修改单。官方平台显示标准现行；修改单于2026-08-01实施。24字符长度与完整编码合法性分层校验。'],
], [1.5, 6.5], 9)
paragraph('参考文档：https://alidocs.dingtalk.com/i/nodes/P7QG4Yx2JpAON7qZiqjoBb6K89dEq3XD', size=9)
paragraph('国家标准原文公开系统：https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=60E981B01DE41613119F9C788AE3EAE4', size=9)
paragraph('第1号修改单：https://std.samr.gov.cn/gb/search/gbDetailedCNF?id=3B33CCA4D7FDAEEBE06397BE0A0AFC50', size=9)
paragraph('指定源文件SHA-256：' + HASH, font='DejaVu Sans Mono', size=8)
table(['附件', '用途'], [
    ['SnUploadRequest_EV_24位国标码.schema.json', '完整批量请求Schema：原模型不改动，allOf附加SN与EV接口约束。'],
    [SOURCE.name, '指定源模型的逐字节副本，用于追溯。'],
    ['LoginRequest.schema.json', '鉴权请求结构约束。'],
    ['01–07号JSON示例', '鉴权请求/响应、全量/最小SN请求、成功/存在失败的响应。'],
    ['EV字段字典及枚举_206节点.xlsx', '完整可筛选字段表、原模型约束JSON及完整枚举。'],
    ['EV字段字典_206节点.csv / 枚举字典_完整取值.csv', '与Word/Excel一致的机器可读字典。'],
    ['校验报告.json', f'{len(REPORT)}个正反例及边界检查结果；含原文件hash。'],
], [3.5, 4.5], 9)
paragraph(f'验证完成：{len(REPORT)}个用例全部符合预期；完整/最小请求、SN长度与必填、EV范围、条件必填、枚举URI、日期类型、布尔值、语言键、资源及直方图约束等已检查。批量重复SN、真实国标分段与登记、SHACL语义完整性及实际接口行为仍属于业务或联调验证。')
paragraph('原模型存在的宽松边界已记录在报告中：例如制造年月正则允许13月、语言值未声明string、部分必填数组允许空数组、跨记录重复SN不能由当前Schema判定。供应商按真实语义填报，平台需补充相应业务校验。')
paragraph('本次未使用真实凭证，未调用登录或上传接口，未修改任何线上接口或原始语义文件。')

# A native TOC field with cached entries and dynamic page references.
toc_page_file = HERE / '目录页码.json'
toc_pages = json.loads(toc_page_file.read_text()) if toc_page_file.exists() else {}
toc_nodes = []
p = el('p')
run(p, '')
r = el('r'); r.append(el('fldChar', {'fldCharType': 'begin'})); p.append(r)
r = el('r'); instr = el('instrText', text=' TOC \\o "1-3" \\h \\z \\u '); instr.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve'); r.append(instr); p.append(r)
r = el('r'); r.append(el('fldChar', {'fldCharType': 'separate'})); p.append(r)
toc_nodes.append(p)
for level, text, bookmark in BOOKMARKS:
    if text in ('文档修订记录', '目    录') or re.match(r'E\d{2}', text):
        continue
    p = el('p'); pp = el('pPr'); pp.append(el('pStyle', {'val': 'TOC' + str(level)}))
    pp.append(el('spacing', {'after': 70})); tabs = el('tabs'); tabs.append(el('tab', {'val': 'right', 'leader': 'dot', 'pos': 9600})); pp.append(tabs); p.append(pp)
    run(p, text)
    r = el('r'); r.append(el('tab')); p.append(r)
    field = el('fldSimple', {'instr': ' PAGEREF ' + bookmark + ' \\h '}); run(field, str(toc_pages.get(text, 1))); p.append(field)
    toc_nodes.append(p)
p = el('p'); r = el('r'); r.append(el('fldChar', {'fldCharType': 'end'})); p.append(r); toc_nodes.append(p)
for i, p in enumerate(toc_nodes):
    BODY.insert(TOC_INSERT + i, p)
BODY.append(section_props(ORIENTATION))


def make_styles():
    styles = el('styles')
    defaults = el('docDefaults'); rd = el('rPrDefault'); rp = el('rPr')
    rp.append(el('rFonts', {'ascii': 'Calibri', 'hAnsi': 'Calibri', 'eastAsia': 'Noto Sans CJK SC'}))
    rp.append(el('sz', {'val': 21})); rp.append(el('lang', {'val': 'en-GB', 'eastAsia': 'zh-CN'})); rd.append(rp); defaults.append(rd)
    pd = el('pPrDefault'); pp = el('pPr'); pp.append(el('spacing', {'after': 100, 'line': 300, 'lineRule': 'auto'})); pd.append(pp); defaults.append(pd); styles.append(defaults)
    for name, display, size in [('Normal', 'Normal', 10.5), ('Title', 'Title', 24), ('Subtitle', 'Subtitle', 18),
                                 ('Heading1', 'heading 1', 16), ('Heading2', 'heading 2', 13), ('Heading3', 'heading 3', 11),
                                 ('Code', 'Source Code', 8), ('TOC1', 'toc 1', 10.5), ('TOC2', 'toc 2', 10), ('TOC3', 'toc 3', 9.5)]:
        st = el('style', {'type': 'paragraph', 'styleId': name})
        if name == 'Normal':
            st.set(f'{{{W}}}default', '1')
        st.append(el('name', {'val': display}))
        if name != 'Normal':
            st.append(el('basedOn', {'val': 'Normal'}))
        st.append(el('next', {'val': 'Normal'})); pr = el('pPr'); rp = el('rPr'); rp.append(el('sz', {'val': int(size * 2)}))
        if name.startswith('Heading'):
            pr.append(el('keepNext')); pr.append(el('keepLines')); pr.append(el('outlineLvl', {'val': int(name[-1])-1}))
            rp.append(el('b')); rp.append(el('color', {'val': '244C70'}))
        if name.startswith('TOC'):
            pr.append(el('ind', {'left': (int(name[-1])-1)*260}))
        st.append(pr); st.append(rp); styles.append(st)
    return styles


HEADER = el('hdr')
paragraph('电池护照（DPP）平台  |  供应商上传SN接口  |  EV', parent=HEADER, size=8, color='68788A', after=0)
FOOTER = el('ftr')
p = paragraph('v1.0  ·  ' + STAMP + '    |    第 ', parent=FOOTER, align='center', size=8, after=0)
field = el('fldSimple', {'instr': ' PAGE '}); run(field, '1', size=8); p.append(field); run(p, ' 页', size=8)
SETTINGS = el('settings'); SETTINGS.append(el('updateFields', {'val': 'true'})); SETTINGS.append(el('defaultTabStop', {'val': '420'}))


def xml_bytes(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


RELNS = 'http://schemas.openxmlformats.org/package/2006/relationships'
def rels(rows):
    root = ET.Element('Relationships', xmlns=RELNS)
    for identifier, kind, target in rows:
        ET.SubElement(root, 'Relationship', Id=identifier, Type=R + '/' + kind, Target=target)
    return xml_bytes(root)


types = ET.Element('Types', xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
for ext, typ in [('rels', 'application/vnd.openxmlformats-package.relationships+xml'), ('xml', 'application/xml')]:
    ET.SubElement(types, 'Default', Extension=ext, ContentType=typ)
for part, kind in [('document', 'document.main'), ('styles', 'styles'), ('settings', 'settings'), ('header1', 'header'), ('footer1', 'footer')]:
    ET.SubElement(types, 'Override', PartName=f'/word/{part}.xml', ContentType=f'application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml')
with zipfile.ZipFile(HERE / (STEM + '.docx'), 'w', zipfile.ZIP_DEFLATED) as z:
    for name, data in {
        '[Content_Types].xml': xml_bytes(types),
        '_rels/.rels': rels([('rDoc', 'officeDocument', 'word/document.xml')]),
        'word/document.xml': xml_bytes(DOC), 'word/styles.xml': xml_bytes(make_styles()),
        'word/settings.xml': xml_bytes(SETTINGS), 'word/header1.xml': xml_bytes(HEADER), 'word/footer1.xml': xml_bytes(FOOTER),
        'word/_rels/document.xml.rels': rels([
            ('rStyles', 'styles', 'styles.xml'), ('rSettings', 'settings', 'settings.xml'),
            ('rHeader', 'header', 'header1.xml'), ('rFooter', 'footer', 'footer1.xml'),
        ]),
    }.items():
        z.writestr(name, data)

readme = f'''供应商SN接口说明 v1.0 | {STAMP}

服务域名：{HOST}
鉴权：POST {LOGIN_PATH}，Header apikey；业务请求Header token。
上传：POST {UPLOAD_PATH}；根对象snList 1–100条，每项直接为完整EV模型。
SN路径：snList[].batteryIdentification.serialNumber，string必填且恰好24字符。
电池分类：只接受EV完整URI。SN完整国标编码合法性仍需业务校验。

04号JSON覆盖63个顶层字段；05号JSON只保留52个顶层必填字段及必要子字段。
两者均已通过附件的Draft 2020-12请求Schema及format检查。
示例SN {SN} 为虚构的结构测试码，不代表已登记国标码；/demo资源URL为演示。
语义枚举/规范URI维持欧盟域名；未使用真实凭证、未调用线上接口。

字段字典覆盖206个显式节点，18组不同枚举完整列出。
指定源文件SHA-256：{HASH}
结构、语义、业务三类约束的边界请查看Word/PDF与校验报告。
JSON Schema通过不等于完整SHACL或国标合规验证。
本版是设计/联调文档，新增结构及幂等规则需要服务端实现。
'''
(ATT / 'README.txt').write_text(readme)
with zipfile.ZipFile(HERE / 'SN接口对接附件_EV全量_24位国标码.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for path in sorted(ATT.iterdir()):
        z.write(path, 'SN接口对接附件/' + path.name)
print(json.dumps({'docx': str(HERE / (STEM + '.docx')), 'top_fields': 63, 'nodes': len(NODES),
                  'enum_sets': len(ENUMS), 'tests': len(REPORT), 'validation': 'PASS'}, ensure_ascii=False))
