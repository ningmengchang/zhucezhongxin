"""One engineering row per shared field; retain category-specific rules."""
from collections import defaultdict
from copy import copy
import hashlib
import json
from math import ceil
from pathlib import Path
import re
from unicodedata import east_asian_width

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent
OUT = ROOT / '12_工程化字段表v4.0'
SOURCE = OUT / '电池护照_工程化字段表_v4.0_字段来源核对版_地址说明修订.xlsx'
TARGET = OUT / '电池护照_工程化字段表_v4.0_公共字段合并版.xlsx'
BASE = 'https://repository.product-passport.ec.europa.eu'
CLASSES = ['EV', 'LMT', 'IND']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ordered(values):
    return [x for x in CLASSES if x in values]


def categorized(rows, get, column):
    values = defaultdict(list)
    for i in rows:
        values[get(i, column) or '—'].extend(get(i, '电池分类').split(' / '))
    if len(values) == 1:
        return next(iter(values))
    return '\n\n'.join('/'.join(ordered(groups)) + '：' + str(text)
                        for text, groups in values.items())


def merge_fields(old, headers):
    get = lambda i, key: old.cell(i, headers[key]).value
    by_code = defaultdict(list)
    for i in range(2, old.max_row + 1):
        by_code[get(i, '工程化字段名称')].append(i)
    groups = []
    stable = ['工程化字段名称', '官方语义字段', '官方JSON路径', '数据格式', '单位', '枚举项', '披露权限', '动静态']
    for code, rows in by_code.items():
        active = [i for i in rows if get(i, '是否必填') != 'N/A'] or rows
        for key in stable:
            assert len({get(i, key) for i in active}) == 1, ('incompatible field', code, key)
        assert len({get(i, '官方JSON路径') for i in rows}) == 1
        seen = set()
        for i in rows:
            classes = set(get(i, '电池分类').split(' / '))
            assert not seen & classes, (code, 'overlapping category variants')
            seen |= classes
        groups.append(dict(code=code, rows=rows, active=active, first=rows[0], base=active[0]))
    return groups, get


def main():
    source_sha = digest(SOURCE)
    wb = load_workbook(SOURCE)
    old = wb['工程化v4.0']
    headers = {c.value: c.column for c in old[1]}
    groups, get = merge_fields(old, headers)
    assert old.max_row == 200 and len(groups) == 171
    merged = [x for x in groups if len(x['rows']) > 1]
    assert len(merged) == 25
    rowmap = {1: 1}
    for number, g in enumerate(groups, 2):
        g['new_row'] = number
        for i in g['rows']:
            rowmap[i] = number

    old.title = '原主表临时副本'
    ws = wb.create_sheet('工程化v4.0', 0)
    for attr in ['sheet_format', 'sheet_properties', 'page_setup', 'page_margins', 'print_options']:
        setattr(ws, attr, copy(getattr(old, attr)))
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = old.sheet_view.zoomScale
    ws.freeze_panes = old.freeze_panes
    for key, dim in old.column_dimensions.items():
        ws.column_dimensions[key] = copy(dim)
        ws.column_dimensions[key].parent = ws

    def copy_row(source_row, target_row):
        ws.row_dimensions[target_row].height = old.row_dimensions[source_row].height
        for cell in old[source_row]:
            dst = ws.cell(target_row, cell.column, cell.value)
            dst._style = copy(cell._style)
            dst.comment = copy(cell.comment)
            if cell.hyperlink:
                dst._hyperlink = copy(cell.hyperlink)
                dst._hyperlink.ref = dst.coordinate

    copy_row(1, 1)
    audit = []
    for g in groups:
        rows, active, new = g['rows'], g['active'], g['new_row']
        copy_row(g['base'], new)
        ws.cell(new, headers['字段ID']).value = get(g['first'], '字段ID')
        if len(rows) == 1:
            continue
        active_classes = ordered({c for i in active for c in get(i, '电池分类').split(' / ')})
        by_class = {c: dict(required=get(i, '是否必填'), condition=get(i, '适用条件'),
                            sourceRow=i, originalId=get(i, '字段ID'))
                    for i in rows for c in get(i, '电池分类').split(' / ')}
        changes = {
            '电池分类': ' / '.join(active_classes),
            '是否必填': categorized(rows, get, '是否必填'),
            '适用条件': categorized(rows, get, '适用条件'),
            '语义对应状态': categorized(rows, get, '语义对应状态'),
            '命名来源': categorized(rows, get, '命名来源'),
            '英文描述': categorized(active, get, '英文描述'),
            '中文描述': categorized(rows, get, '中文描述'),
            '示例': categorized(active, get, '示例'),
            '官方定义出处': '\n'.join(dict.fromkeys(str(get(i, '官方定义出处') or '') for i in rows)),
        }
        ids = [get(i, '字段ID') for i in rows]
        changes['工程与核对备注'] = (categorized(rows, get, '工程与核对备注') + '\n【公共字段合并】原记录' +
            ' / '.join(ids) + '合并；各类别的必填、适用条件与不适用状态分别保留。')
        changes['核对状态'] = categorized(active, get, '核对状态') + '；分类合并展示'
        mixed_required = len({get(i, '是否必填') for i in rows}) > 1
        if mixed_required:
            changes['适用条件'] = '分类必填规则：\n' + changes['是否必填'] + '\n\n' + changes['适用条件']
        if g['code'] == 'contentSpecificationIds':
            changes['适用条件'] = '三类均必填。核心规范、电池组规范、所属电池子类规范分别各含1个匹配标识；子类按EV→evb、LMT→lmt、IND→ind选择。'
            changes['中文描述'] = ('内容规范标识列表，类型array<string>，元素不可重复。\n'
                '核心规范、电池组规范、所属电池子类规范分别各有1个匹配值；每条护照填写对应电池类别的子类规范标识。\n'
                'EV使用/product/batteries/subgroup/evb/版本号；LMT使用/subgroup/lmt/版本号；IND使用/subgroup/ind/版本号。\n'
                '版本号按实际采用的规范填写；1.0.0是本快照样例，不是固定枚举。\n'
                'qualifiedMaxCount=1针对各匹配子集；源Schema没有整个数组的maxItems，不能理解为只能1项或恰好3项。')
            examples = []
            for category, part in [('EV', 'evb'), ('LMT', 'lmt'), ('IND', 'ind')]:
                values = [BASE + '/core/1.0.0', BASE + '/product/batteries/1.0.0',
                          BASE + '/product/batteries/subgroup/' + part + '/1.0.0']
                examples.append(category + '：' + json.dumps(values, ensure_ascii=False))
            changes['示例'] = '\n\n'.join(examples)
            changes['官方定义出处'] += '\n' + '\n'.join(
                'product/batteries/subgroup/' + part + '/1.0.0/shapes/pub-' + part +
                '-shapes.ttl；contentSpecificationIds的qualifiedValueShape' for part in ['evb', 'lmt', 'ind'])
        for key, value in changes.items():
            ws.cell(new, headers[key]).value = value
        estimated_lines = []
        for key in ['是否必填', '适用条件', '英文描述', '中文描述']:
            letter = get_column_letter(headers[key])
            width = max(8, ws.column_dimensions[letter].width - 2)
            lines = sum(max(1, ceil(sum(2 if east_asian_width(c) in {'W','F'} else 1
                                       for c in paragraph) / width))
                        for paragraph in str(ws.cell(new, headers[key]).value or '').split('\n'))
            estimated_lines.append(lines)
        ws.row_dimensions[new].height = min(409, max(165, max(estimated_lines) * 14 + 12))
        for key in ['是否必填', '适用条件']:
            ws.cell(new, headers[key]).fill = PatternFill('solid', fgColor='FFF0CA' if mixed_required else 'DEF1EE')
        ws.cell(new, headers['工程化字段名称']).comment = Comment(
            '合并记录：' + ' / '.join(ids) + '。\n' + changes['适用条件'] +
            '\n类型、单位、权限和JSON路径对适用类别一致；各类别原规则仍见规则与映射。', '公共字段合并说明')
        audit.append(dict(field=g['code'], oldRows=rows, newRow=new, oldIds=ids,
                          activeClasses=active_classes, perClass=by_class))

    ws['A1'].comment = Comment('公共字段合并版：171个字段，每个工程字段一行；25组分类记录已合并。分类间M/C/V/N/A差异写在同一行，N/A类别不计入适用电池分类。保留121条3.4业务来源及642个语义结构节点。I列保留旧工程名。', '使用说明')
    ws.cell(1, headers['是否必填']).comment = Comment('单值M/C/V/N/A适用于本行列明的类别；有分类差异时按“EV/LMT：M；IND：C”分行显示。未确认语义对应的旧工程项仍沿用3.4并标明来源。', '填写说明')
    ws.print_title_rows = '1:1'
    wb.remove(old)
    table = Table(displayName='EngineeringV40', ref=ws.dimensions)
    table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=False)
    ws.add_table(table)
    ws.auto_filter.ref = ws.dimensions
    dv = DataValidation(type='list', formula1='"M,C,V,N/A,待确认"')
    ws.add_data_validation(dv)
    for i in range(2, ws.max_row + 1):
        if ws.cell(i, headers['是否必填']).value in {'M', 'C', 'V', 'N/A', '待确认'}:
            dv.add(ws.cell(i, headers['是否必填']))
    dyn = DataValidation(type='list', formula1='"Static,Dynamic,待确认"')
    ws.add_data_validation(dyn)
    col = get_column_letter(headers['动静态'])
    dyn.add(col + '2:' + col + str(ws.max_row))

    for sheet in wb:
        for row in sheet:
            for cell in row:
                if cell.hyperlink:
                    m = re.fullmatch(r"#'工程化v4\.0'!([A-Z]+)(\d+)", cell.hyperlink.target or '')
                    if m:
                        cell.hyperlink.target = "#'工程化v4.0'!" + m[1] + str(rowmap[int(m[2])])
        sh = {c.value: c.column for c in sheet[1]}
        location = next((sh[k] for k in ['主表位置', '主表定位', 'v4主表定位'] if k in sh), None)
        if location:
            for i in range(2, sheet.max_row + 1):
                cell = sheet.cell(i, location)
                if isinstance(cell.value, str):
                    refs = re.findall(r'第(\d+)行', cell.value)
                    if refs:
                        cell.value = ', '.join(dict.fromkeys('第' + str(rowmap[int(n)]) + '行' for n in refs))
    wb['字段来源核对']['A1'].comment = Comment('保留199条历史分类来源核对记录，对应当前171行主表；同一工程字段不同类别的记录可指向主表同一行。', '合并说明')
    for row in wb['规则与映射']:
        if row[2].value == '主表粒度':
            row[7].value = '171个工程字段逐字段一行；同字段的分类差异分项写明。642个语义节点仍按类别逐项保留。'
        elif row[2].value == '必填':
            row[7].value = 'M/C/V/N/A/待确认；合并行有分类差异时按EV/LMT/IND分别标注，不能将工业条件必填一律改为M。JSON required与业务条件分别保留。'
    wb.properties.title = '电池护照工程化字段表 v4.0 公共字段合并版'
    wb.active = 0
    wb.save(TARGET)

    result = load_workbook(TARGET)
    main_sheet = result['工程化v4.0']
    codes = [main_sheet.cell(i, headers['工程化字段名称']).value for i in range(2, main_sheet.max_row + 1)]
    assert len(codes) == len(set(codes)) == 171
    for g in groups:
        new = g['new_row']
        for key in ['数据格式', '单位', '枚举项', '披露权限', '动静态', '官方JSON路径']:
            assert main_sheet.cell(new, headers[key]).value == get(g['base'], key)
        required = main_sheet.cell(new, headers['是否必填']).value
        parsed = {}
        if required not in {'M', 'C', 'V', 'N/A', '待确认'}:
            for line in required.splitlines():
                if '：' in line:
                    categories, state = line.split('：', 1)
                    for category in categories.split('/'):
                        parsed[category] = state
        for i in g['rows']:
            for category in get(i, '电池分类').split(' / '):
                assert parsed.get(category, required) == get(i, '是否必填'), (g['code'], category)
            if len(g['rows']) > 1 and g['code'] != 'contentSpecificationIds':
                assert get(i, '适用条件') in main_sheet.cell(new, headers['适用条件']).value
    assert {main_sheet.cell(i, headers['3.4源行']).value for i in range(2, main_sheet.max_row + 1)
            if main_sheet.cell(i, headers['3.4源行']).value} == set(range(2, 123))
    assert sum(row[0].value == '语义结构' for row in result['规则与映射'].iter_rows(min_row=2)) == 642
    for sheet in result:
        for row in sheet:
            for cell in row:
                assert cell.data_type not in {'f', 'e'}
                assert len(str(cell.value or '')) <= 32767
                if cell.hyperlink and (cell.hyperlink.target or '').startswith('#'):
                    m = re.fullmatch(r"#'([^']+)'!([A-Z]+\d+)", cell.hyperlink.target)
                    assert m and result[m[1]][m[2]].value is not None
    assert digest(SOURCE) == source_sha
    report = dict(source=SOURCE.name, target=TARGET.name, previousMainRows=199, currentMainRows=171,
                  mergedFieldGroups=25, reducedRows=28, mergedFields=audit,
                  rowMapping={str(k): v for k, v in rowmap.items()}, preservedLegacyBusinessRows=121,
                  preservedSemanticNodes=642, sourcePreserved=True, outputSha256=digest(TARGET), verification='passed')
    (OUT / '公共字段合并记录.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('Verified: 199 -> 171 rows; 25 shared fields merged; category obligations/conditions preserved; 121 legacy rows and 642 semantic nodes retained.')
    print(TARGET)


if __name__ == '__main__':
    main()
