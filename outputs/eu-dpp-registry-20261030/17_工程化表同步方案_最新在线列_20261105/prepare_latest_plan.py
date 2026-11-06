"""Plan local 100-row projection onto the freshly restored 19-column online sheet.

No DWS write is performed. Legacy row anchors are verified against their original
field text, then exact names are matched only against unclaimed online rows.
"""
from pathlib import Path
from collections import Counter
import csv
import hashlib
import io
import json
import re

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
SOURCE = BASE / '12_工程化字段表v4.0/电池护照_工程化字段表_v4.0_公共字段合并版.xlsx'


def clean(value):
    return '' if value is None else str(value).replace('\r\n', '\n').replace('\r', '\n')


def read_snapshot(path):
    obj = json.loads(path.read_text())
    assert obj['success'] and not obj['hasMore'] and not obj['truncationReasons']
    return [[clean(v) for v in r] for r in csv.reader(io.StringIO(re.sub(r'(?m)^\[row=\d+\]\s*', '', obj['csv'])))]


def code(text):
    return next((v.strip() for v in clean(text).splitlines()
                 if re.fullmatch(r'[a-z][A-Za-z0-9_.]*', v.strip()) and v.strip() != 'field'), '')


online = read_snapshot(HERE / '在线表_最新读取快照.json')
legacy = read_snapshot(BASE / '12_工程化字段表v4.0/参考输入/工程化3.4_读取快照.json')
headers = online[0]
names = [v.splitlines()[0] for v in headers]
assert len(headers) == 19 and len(online) == 122
assert names[8] == 'batterPass数据格式' and names[18] == 'XXX型号'
source_bytes = SOURCE.read_bytes()
(HERE / '本地Excel_方案快照.xlsx').write_bytes(source_bytes)
source = load_workbook(io.BytesIO(source_bytes), data_only=False)['工程化v4.0']
hs = [cell.value for cell in source[1]]
source_rows = [{key: clean(cell.value) for key, cell in zip(hs, row)}
               for row in source.iter_rows(min_row=2) if row[hs.index('工程化字段名称')].value]
assert len(source_rows) == len({r['工程化字段名称'] for r in source_rows}) == 100
used = set()
matches = {}
match_notes = {}

# Explicit legacy mappings take precedence over name equality. In particular,
# old ratedCapacity belongs to today's public batteryCapacity, not RES3 ratedCapacity.
for row in source_rows:
    key = row['工程化字段名称']
    if row['3.4源行']:
        number = int(row['3.4源行'])
        assert 2 <= number <= len(online)
        reference = legacy[number - 1][3]
        current = online[number - 1][3]
        assert reference == current or (code(reference) and code(reference) == code(current)), (key, number, reference, current)
        assert number not in used
        matches[key] = number; used.add(number); match_notes[key] = '本地3.4映射，经原字段文本核对'
for row in source_rows:
    key = row['工程化字段名称']
    if key not in matches:
        candidates = [i for i in range(2, len(online) + 1) if i not in used and code(online[i - 1][3]) == key]
        assert len(candidates) <= 1, (key, candidates)
        if candidates:
            matches[key] = candidates[0]; used.add(candidates[0]); match_notes[key] = '工程字段英文名精确匹配'

special = {'工程化字段': '工程化字段名称', '是否采集': '采集方式',
           '是否录入多语言': '多语言', '责任部门/岗位': '责任部门'}
local_only = [k for k in hs if k not in {special.get(n, n) for n in names if n not in ('batterPass数据格式', 'XXX型号')}]
projected = []
matching_rows = []
new_fields = []
for target_row, source_row in enumerate(source_rows, 2):
    key = source_row['工程化字段名称']
    old_number = matches.get(key)
    row = []
    for column, name in enumerate(names):
        if name in ('batterPass数据格式', 'XXX型号'):
            value = online[old_number - 1][column] if old_number else ''
        else:
            value = source_row[special.get(name, name)]
            if name == '是否采集':
                assert value in ('手动填报', '系统获取', '0', '2', '待确认')
                value = {'手动填报': '0', '系统获取': '2'}.get(value, value)
        row.append(value)
    projected.append(row)
    matching_rows.append([target_row, key, source_row['数据项名称'],
                          old_number or '—', code(online[old_number - 1][3]) if old_number else '',
                          match_notes.get(key, '按本地主表补入'),
                          '沿用匹配字段的在线值' if old_number else '本地无对应列，保留空白'])
    if not old_number:
        new_fields.append(key)
removed = [[i, online[i - 1][2], code(online[i - 1][3]), online[i - 1][3]]
           for i in range(2, len(online) + 1) if i not in used]
assert len(matches) + len(new_fields) == 100
assert len(removed) + len(matches) == 121

padded = [*projected, *[[''] * 19 for _ in range(121 - len(projected))]]
changes = []
counts = Counter()
for index, (before, after) in enumerate(zip(online[1:], padded), 2):
    for column, (a, b) in enumerate(zip(before, after)):
        if a != b:
            changes.append([index, names[column], a, b]); counts[names[column]] += 1

with (HERE / '拟同步数据_A2-S122.csv').open('w', encoding='utf-8-sig', newline='') as fp:
    csv.writer(fp).writerows(padded)
(HERE / '拟同步矩阵.json').write_text(json.dumps([headers, *padded], ensure_ascii=False, indent=2))

wb = Workbook()
ws = wb.active; ws.title = '拟同步数据_最新19列'
ws.append(headers)
for row in projected: ws.append(row)
widths = [18, 21, 30, 36, 20, 24, 28, 20, 40, 24, 60, 20, 18, 18, 55, 100, 85, 26, 28]
for i, width in enumerate(widths, 1): ws.column_dimensions[ws.cell(1, i).column_letter].width = width
ws.row_dimensions[1].height = 90
for i in range(2, ws.max_row + 1): ws.row_dimensions[i].height = 70

mapping_sheet = wb.create_sheet('列映射')
mapping_sheet.append(['在线列序号', '在线列名', '本地来源', '处理规则'])
for i, name in enumerate(names, 1):
    if name in ('batterPass数据格式', 'XXX型号'):
        origin, rule = '在线现有值', '按匹配字段保留；本地补入字段留空'
    else:
        origin = special.get(name, name)
        rule = '手动填报→0；系统获取→2；待确认原样保留' if name == '是否采集' else '采用本地对应单元格'
    mapping_sheet.append([i, name, origin, rule])
matching = wb.create_sheet('字段匹配_100行')
matching.append(['拟同步行', '本地工程化字段', '本地数据项名称', '对应在线原行', '在线原工程名', '匹配依据', '两列在线专有信息'])
for row in matching_rows: matching.append(row)
retired = wb.create_sheet('移出在线的原条目')
retired.append(['在线原行', '原数据项名称', '原工程字段代码', '原工程字段完整内容'])
for row in removed: retired.append(row)
diffs = wb.create_sheet('单元格差异')
diffs.append(['在线行', '在线列', '在线当前值', '拟同步值'])
for row in changes: diffs.append(row)
structure = wb.create_sheet('结构调整')
structure.append(['调整对象', '当前结构', '拟处理', '原因'])
for address in ('F16:F17', 'F22:F23'):
    a, b = [int(re.search(r'\d+', x)[0]) for x in address.split(':')]
    structure.append([address, '数据区合并单元格', '解除合并，分别写入各字段必填值',
                      '重排后分别对应：' + '；'.join(projected[i - 2][3] + '=' + projected[i - 2][5] for i in (a, b))])
structure.append(['A102:S122', '原末尾21行内容', '清空旧内容，保留空白网格', '同步结果按本地主表保留100条有效记录'])

for sheet in wb:
    sheet.freeze_panes = 'A2'; sheet.auto_filter.ref = sheet.dimensions
    if sheet != ws:
        for column in sheet.columns: sheet.column_dimensions[column[0].column_letter].width = 65 if column[0].column > 2 else 32
    for cells in sheet:
        for cell in cells:
            cell.font = Font(name='等线', size=10, bold=cell.row == 1, color='FFFFFF' if cell.row == 1 else '000000')
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            if cell.row == 1: cell.fill = PatternFill('solid', fgColor='365F7D')
preview = HERE / '工程化4.0_最新19列_拟同步预览.xlsx'
wb.save(preview)

plan = {
    '版本': '按用户再次调整后的在线19列重新计算',
    '目标URL': 'https://alidocs.dingtalk.com/i/nodes/Qnp9zOoBVBByzZaNClgbvO9xV1DK0g6l',
    '工作表': '工程化4.0', 'sheetId': 's2',
    '本地文件': str(SOURCE), '本地主表': '工程化v4.0',
    '来源SHA256': hashlib.sha256(source_bytes).hexdigest(),
    '在线列数': 19, '在线原有效数据行': 121, '本地有效数据行': 100,
    '明确匹配原行': len(matches), '本地补入行': len(new_fields), '原条目移出数': len(removed),
    '净减少有效行数': 21, '补入字段': new_fields,
    '保留表头': headers, '冻结首行': True,
    '两列在线专有信息': {'列名': ['batterPass数据格式', 'XXX型号'],
                         '处理': '51个明确匹配字段沿用在线对应值；49个本地补入字段留空。',
                         '现有XXX型号非空单元格': sum(bool(r[18]) for r in online[1:]),
                         '拟保留batterPass格式单元格': sum(bool(r[8]) for r in projected)},
    '数据区解除合并': ['F16:F17', 'F22:F23'],
    '数据写入范围': 'A2:S122', '有效数据范围': 'A2:S101', '清空尾部': 'A102:S122',
    '值变化单元格数': len(changes), '按列变化单元格': dict(counts),
    '本地附加列': local_only,
    '中文描述': '采用本地中文描述原文，其他本地附加列留在Excel中。',
    '采集方式': dict(Counter(r[11] for r in projected)),
    '关键字段匹配': {k: {'在线原行': matches.get(k), '原工程名': code(online[matches[k] - 1][3]) if k in matches else None,
                              '本地数据项名称': next(r['数据项名称'] for r in source_rows if r['工程化字段名称'] == k)}
                    for k in ['batteryCapacity', 'ratedCapacity', 'dppStatus']},
    '执行状态': '仅生成方案及预览，未修改在线表格。',
    '预览文件': str(preview),
}
(HERE / '最新同步方案.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
print(json.dumps({k: v for k, v in plan.items() if k not in ('保留表头', '补入字段', '本地附加列', '按列变化单元格')}, ensure_ascii=False, indent=2))
