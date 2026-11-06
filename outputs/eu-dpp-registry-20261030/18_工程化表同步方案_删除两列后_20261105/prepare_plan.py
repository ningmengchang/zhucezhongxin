"""Prepare the final 17-column plan after the user removed two online columns.

Only local artifacts are written. Every target value has a local source column.
"""
from pathlib import Path
from collections import Counter
import csv
import io
import json
import re
import hashlib

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / '12_工程化字段表v4.0/电池护照_工程化字段表_v4.0_公共字段合并版.xlsx'
readback = json.loads((HERE / '在线表_删除两列后快照.json').read_text())
assert readback['success'] and not readback['hasMore'] and not readback['truncationReasons']
clean = lambda v: '' if v is None else str(v).replace('\r\n', '\n').replace('\r', '\n')
online = [[clean(v) for v in r] for r in csv.reader(io.StringIO(re.sub(r'(?m)^\[row=\d+\]\s*', '', readback['csv'])))]
headers = online[0]
names = [v.splitlines()[0] for v in headers]
assert len(online) == 122 and len(names) == 17
assert 'batterPass数据格式' not in names and 'XXX型号' not in names
source_bytes = SOURCE.read_bytes()
(HERE / '本地Excel_方案快照.xlsx').write_bytes(source_bytes)
ws = load_workbook(io.BytesIO(source_bytes), data_only=False)['工程化v4.0']
source_headers = [c.value for c in ws[1]]
source_index = {v: i for i, v in enumerate(source_headers)}
mapping = {'工程化字段': '工程化字段名称', '是否采集': '采集方式',
           '是否录入多语言': '多语言', '责任部门/岗位': '责任部门'}
data = []
for cells in ws.iter_rows(min_row=2):
    if not cells[source_index['工程化字段名称']].value:
        continue
    row = []
    for name in names:
        cell = cells[source_index[mapping.get(name, name)]]
        assert cell.data_type != 'f', cell.coordinate
        value = clean(cell.value)
        if name == '是否采集':
            assert value in ('手动填报', '系统获取', '待确认', '0', '2')
            value = {'手动填报': '0', '系统获取': '2'}.get(value, value)
        row.append(value)
    data.append(row)
assert len(data) == len({r[3] for r in data}) == 100
tail_count = len(online) - len(data) - 1
padded = [*data, *[[''] * len(names) for _ in range(tail_count)]]
diffs = []
counts = Counter()
for i, (before, after) in enumerate(zip(online[1:], padded), 2):
    for j, (a, b) in enumerate(zip(before, after)):
        if a != b:
            diffs.append([i, names[j], a, b]); counts[names[j]] += 1
with (HERE / '拟同步数据_A2-Q122.csv').open('w', encoding='utf-8-sig', newline='') as f:
    csv.writer(f).writerows(padded)
(HERE / '拟同步矩阵.json').write_text(json.dumps([headers, *padded], ensure_ascii=False, indent=2))

book = Workbook()
preview = book.active; preview.title = '拟同步数据_最新17列'
preview.append(headers)
for row in data: preview.append(row)
widths = [18, 21, 30, 36, 20, 24, 28, 20, 24, 65, 20, 18, 18, 55, 100, 85, 26]
for i, width in enumerate(widths, 1): preview.column_dimensions[preview.cell(1, i).column_letter].width = width
preview.row_dimensions[1].height = 90
for i in range(2, 102): preview.row_dimensions[i].height = 70
current = book.create_sheet('在线当前数据_121行')
for row in online: current.append(row)
mapping_sheet = book.create_sheet('列映射')
mapping_sheet.append(['在线列', '在线列名', '本地来源列', '处理'])
for i, name in enumerate(names, 1):
    mapping_sheet.append([i, name, mapping.get(name, name),
                          '手动填报→0；系统获取→2；待确认原样保留' if name == '是否采集' else '本地原文'])
difference_sheet = book.create_sheet('单元格差异')
difference_sheet.append(['在线行', '在线列', '当前值', '拟写入值'])
for row in diffs: difference_sheet.append(row)
structure = book.create_sheet('结构调整')
structure.append(['范围', '拟处理', '说明'])
for span in ['F16:F17', 'F22:F23']:
    numbers = [int(re.search(r'\d+', s)[0]) for s in span.split(':')]
    structure.append([span, '解除数据区合并', '；'.join(data[i - 2][3] + '=' + data[i - 2][5] for i in numbers)])
structure.append(['A102:Q122', '清空原尾部21行内容', '有效数据按本地主表保留100行，表头和空白网格保留'])
for sheet in book:
    sheet.freeze_panes = 'A2'; sheet.auto_filter.ref = sheet.dimensions
    if sheet != preview:
        for col in sheet.columns: sheet.column_dimensions[col[0].column_letter].width = 70 if col[0].column > 2 else 32
    for row in sheet:
        for c in row:
            c.font = Font(name='等线', size=10, bold=c.row == 1, color='FFFFFF' if c.row == 1 else '000000')
            c.alignment = Alignment(vertical='top', wrap_text=True)
            if c.row == 1: c.fill = PatternFill('solid', fgColor='365F7D')
path = HERE / '工程化4.0_最新17列_同步方案预览.xlsx'
book.save(path)
plan = {
    '版本': '用户删除两列后重新读取并计算',
    '目标URL': 'https://alidocs.dingtalk.com/i/nodes/Qnp9zOoBVBByzZaNClgbvO9xV1DK0g6l',
    '目标工作表': '工程化4.0', 'sheetId': 's2', '在线列数': 17,
    '在线原数据行': 121, '本地数据行': 100, '清空尾部旧内容行数': 21,
    '来源文件': str(SOURCE), '来源工作表': '工程化v4.0',
    '来源SHA256': hashlib.sha256(source_bytes).hexdigest(),
    '保留表头': headers, '值写入范围': 'A2:Q122', '有效数据范围': 'A2:Q101',
    '清空尾部': 'A102:Q122', '先解除数据区合并': ['F16:F17', 'F22:F23'],
    '按列名映射': mapping,
    '中文描述规则': '采用本地中文描述列；本地其余12列信息保留在本地文件中。',
    '示例规则': '保留JSON引号、前导零、日期字符串和大小写，按文本原样写入。',
    '采集方式': dict(Counter(r[10] for r in data)),
    '值变化单元格数': len(diffs), '按列变化': dict(counts),
    '预览文件': str(path), '当前状态': '仅方案和本地预览，未修改在线表格。',
}
(HERE / '最新同步方案.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
print(json.dumps({k: v for k, v in plan.items() if k not in ('保留表头', '按列变化')}, ensure_ascii=False, indent=2))
