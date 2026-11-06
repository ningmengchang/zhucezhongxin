"""Read-only planning: project local rows onto the current online columns."""
from pathlib import Path
import csv
import io
import json
import re
import hashlib
from collections import Counter
from copy import copy

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / '12_工程化字段表v4.0/电池护照_工程化字段表_v4.0_公共字段合并版.xlsx'
snapshot = json.loads((HERE / '在线表_当前读取快照.json').read_text())
assert snapshot['success'] and not snapshot['hasMore'] and not snapshot['truncationReasons']
online = list(csv.reader(io.StringIO(re.sub(r'(?m)^\[row=\d+\]\s*', '', snapshot['csv']))))
headers = online[0]
names = [v.splitlines()[0] for v in headers]
assert len(names) == 17
online_rows = [row for row in online[1:] if any(v for v in row)]
assert all(len(row) == 17 and row[3] for row in online_rows)
old = {row[3]: row for row in online_rows}
assert len(old) == len(online_rows)

source_bytes = SOURCE.read_bytes()
(HERE / '本地Excel_方案快照.xlsx').write_bytes(source_bytes)
workbook = load_workbook(io.BytesIO(source_bytes), data_only=False)
sheet = workbook['工程化v4.0']
local_headers = [cell.value for cell in sheet[1]]
local_index = {name: i for i, name in enumerate(local_headers)}
mapping = {
    '工程化字段': '工程化字段名称', '是否采集': '采集方式',
    '是否录入多语言': '多语言', '责任部门/岗位': '责任部门',
}
clean = lambda v: '' if v is None else str(v).replace('\r\n', '\n').replace('\r', '\n')
new_rows = []
source_extras = sorted(set(local_headers) - {mapping.get(name, name) for name in names})
for cells in sheet.iter_rows(min_row=2):
    values = [cell.value for cell in cells]
    code = clean(values[local_index['工程化字段名称']])
    if not code:
        continue
    row = []
    for name in names:
        source_name = mapping.get(name, name)
        cell = cells[local_index[source_name]]
        assert cell.data_type != 'f', f'Formula requires review: {cell.coordinate}'
        value = clean(cell.value)
        if name == '是否采集':
            assert value in ('手动填报', '系统获取', '0', '2', '待确认'), (code, value)
            value = {'手动填报': '0', '系统获取': '2'}.get(value, value)
        row.append(value)
    new_rows.append(row)
new = {row[3]: row for row in new_rows}
assert len(new) == len(new_rows) == 100
new_keys = list(new)
old_keys = list(old)
additions = [key for key in new_keys if key not in old]
removals = [key for key in old_keys if key not in new]
cell_changes = []
row_changes = []
per_column = Counter()
for index, row in enumerate(new_rows, 2):
    key = row[3]
    changes = []
    if key in old:
        for column, (before, after) in enumerate(zip(old[key], row)):
            if clean(before) != clean(after):
                changes.append(names[column]); per_column[names[column]] += 1
                cell_changes.append([index, key, names[column], before, after])
        kind = '内容更新' if changes else '保持一致'
    else:
        kind = '新增'
    row_changes.append([index, key, kind, '、'.join(changes)])
for key in removals:
    row_changes.append(['—', key, '移除', '本地主表已无此字段'])

wb = Workbook()
preview = wb.active
preview.title = '拟同步数据_17列'
preview.append(headers)
for row in new_rows:
    preview.append(row)
preview.freeze_panes = 'A2'
preview.auto_filter.ref = preview.dimensions
widths = [18, 21, 30, 36, 20, 23, 27, 17, 23, 65, 18, 18, 18, 55, 100, 85, 26]
for i, width in enumerate(widths, 1):
    preview.column_dimensions[preview.cell(1, i).column_letter].width = width
preview.row_dimensions[1].height = 90
for index in range(2, preview.max_row + 1):
    preview.row_dimensions[index].height = 66

mapping_sheet = wb.create_sheet('列映射')
mapping_sheet.append(['在线列', '在线列名', '本地列名', '转换规则'])
for i, name in enumerate(names, 1):
    mapping_sheet.append([i, name, mapping.get(name, name),
                          '手动填报→0；系统获取→2；待确认原样保留' if name == '是否采集' else '本地对应值直接写入'])
row_sheet = wb.create_sheet('字段级差异')
row_sheet.append(['拟写入在线行', '工程化字段', '处理方式', '变化列'])
for row in row_changes:
    row_sheet.append(row)
cell_sheet = wb.create_sheet('单元格差异')
cell_sheet.append(['拟写入在线行', '工程化字段', '在线列名', '在线当前值', '本地拟写入值'])
for row in cell_changes:
    cell_sheet.append(row)
for ws in wb:
    if ws != preview:
        ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 80 if col[0].column > 2 else 35
    for cells in ws:
        for cell in cells:
            cell.font = Font(name='等线', size=10, bold=cell.row == 1, color='FFFFFF' if cell.row == 1 else '000000')
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            if cell.row == 1:
                cell.fill = PatternFill('solid', fgColor='365F7D')
wb.save(HERE / '工程化4.0_在线17列_拟同步预览.xlsx')
with (HERE / '拟同步数据_A2-Q101.csv').open('w', encoding='utf-8-sig', newline='') as fp:
    csv.writer(fp).writerows(new_rows)
with (HERE / '拟更新中文描述_O2-O101.csv').open('w', encoding='utf-8-sig', newline='') as fp:
    csv.writer(fp).writerows([[row[names.index('中文描述')]] for row in new_rows])
name_changes = [{'range': f'C{r[0]}', 'field': r[1], 'before': r[3], 'after': r[4]}
                for r in cell_changes if r[2] == '数据项名称']
(HERE / '拟更新数据项名称.json').write_text(json.dumps(name_changes, ensure_ascii=False, indent=2))
(HERE / '拟同步矩阵.json').write_text(json.dumps([headers, *new_rows], ensure_ascii=False, indent=2))

summary = {
    '目标地址': 'https://alidocs.dingtalk.com/i/nodes/Qnp9zOoBVBByzZaNClgbvO9xV1DK0g6l',
    '工作表': '工程化4.0', 'sheetId': 's2', '本地主表': '工程化v4.0',
    '来源文件': str(SOURCE), '来源SHA256': hashlib.sha256(source_bytes).hexdigest(),
    '在线列数': len(names), '本地列数': len(local_headers),
    '在线数据行': len(online_rows), '拟同步数据行': len(new_rows),
    '新增字段': additions, '移除字段': removals,
    '内容更新行': sum(r[2] == '内容更新' for r in row_changes),
    '保持一致行': sum(r[2] == '保持一致' for r in row_changes),
    '变化单元格数': len(cell_changes), '变化列统计': dict(per_column),
    '数据项名称变化': name_changes,
    '实际修改范围': ['O2:O101', *[r['range'] for r in name_changes]],
    '行顺序是否一致': old_keys == new_keys,
    '列名与顺序': names, '特殊列映射': mapping,
    '保留在本地的附加列': source_extras,
    '中文描述处理': '直接采用本地中文描述列，不追加其他列内容。',
    '示例处理': '按文本保留JSON引号、前导零和日期字符串。',
    '采集方式分布': dict(Counter(row[names.index('是否采集')] for row in new_rows)),
    '数据核对范围': 'A2:Q101', '表头范围': 'A1:Q1',
    '执行状态': '仅生成同步方案和本地预览，未写入在线表格。',
}
(HERE / '同步方案.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False, indent=2))
