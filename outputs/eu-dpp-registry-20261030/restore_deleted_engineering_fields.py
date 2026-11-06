"""Restore deleted legacy records without reverting the user's retained rows."""
from collections import defaultdict
from copy import copy, deepcopy
from datetime import datetime
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from zoneinfo import ZoneInfo

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.datavalidation import DataValidationList
from openpyxl.worksheet.dimensions import RowDimension

ROOT = Path(__file__).resolve().parent
OUT = ROOT / '12_工程化字段表v4.0'
TARGET = OUT / '电池护照_工程化字段表_v4.0_公共字段合并版.xlsx'
RESTORED = OUT / '电池护照_工程化字段表_v4.0_公共字段合并版_旧字段恢复版.xlsx'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blank(value):
    return '' if value is None else value


def baseline_path():
    cached = Path('/tmp/dpp-field-recovery-state.json')
    if cached.exists():
        state = json.loads(cached.read_text())
        if Path(state['baseline']).exists():
            return Path(state['baseline'])
    import merge_engineering_common_rows as merger
    merger.OUT = Path(tempfile.mkdtemp(prefix='dpp-field-recovery-'))
    merger.TARGET = merger.OUT / 'baseline-full-merged.xlsx'
    merger.main()
    return merger.TARGET


def headers(sheet):
    result = {c.value:c.column for c in sheet[1] if c.value}
    assert len(result) == sheet.max_column
    return result


def field_rows(sheet):
    h = headers(sheet)
    result = {}
    for i in range(2, sheet.max_row + 1):
        code = sheet.cell(i, h['工程化字段名称']).value
        if code:
            assert code not in result, ('duplicate engineering name', code)
            result[code] = i
    return result, h


def merge_order(original_order, current_order):
    """Insert missing records at original neighbours; keep current relative order."""
    result = list(current_order)
    available = set(result)
    for i, key in enumerate(original_order):
        if key in available:
            continue
        predecessor = next((x for x in reversed(original_order[:i]) if x in available), None)
        successor = next((x for x in original_order[i+1:] if x in available), None)
        if predecessor is not None:
            position = result.index(predecessor) + 1
        elif successor is not None:
            position = result.index(successor)
        else:
            position = len(result)
        result.insert(position, key)
        available.add(key)
    assert [x for x in result if x in set(current_order)] == list(current_order)
    return result


def copy_cell(source, target):
    target.value = source.value
    for name in ['font', 'fill', 'border', 'alignment', 'protection']:
        setattr(target, name, copy(getattr(source, name)))
    target.number_format = source.number_format
    target.quotePrefix = source.quotePrefix
    target.comment = copy(source.comment)
    if source.hyperlink:
        target._hyperlink = copy(source.hyperlink)
        target._hyperlink.ref = target.coordinate


def copy_dimension(source, sheet, index):
    target = RowDimension(sheet, index=index)
    for attr in ['height', 'hidden', 'outlineLevel', 'collapsed', 'thickTop', 'thickBot']:
        setattr(target, attr, getattr(source, attr))
    if source.has_style:
        for name in ['font', 'fill', 'border', 'alignment', 'protection']:
            setattr(target, name, copy(getattr(source, name)))
        target.number_format = source.number_format
    return target


def insert_missing(sheet, base_sheet, final_order, current_rows, base_rows, current_headers, base_headers):
    dimensions = {i:copy(d) for i,d in sheet.row_dimensions.items()}
    original_max = sheet.max_row
    final_rows = {key:i for i,key in enumerate(final_order,2)}
    restored = []
    for key in final_order:
        if key in current_rows:
            continue
        position = final_rows[key]
        sheet.insert_rows(position)
        for title, col in current_headers.items():
            copy_cell(base_sheet.cell(base_rows[key],base_headers[title]), sheet.cell(position,col))
        restored.append(key)
    sheet.row_dimensions.clear()
    for key, old_row in current_rows.items():
        if old_row in dimensions:
            sheet.row_dimensions[final_rows[key]] = copy_dimension(dimensions[old_row],sheet,final_rows[key])
    if 1 in dimensions:
        sheet.row_dimensions[1] = copy_dimension(dimensions[1],sheet,1)
    for key in restored:
        row = final_rows[key]
        sheet.row_dimensions[row] = copy_dimension(base_sheet.row_dimensions[base_rows[key]],sheet,row)
    for i, dimension in dimensions.items():
        if i > original_max:
            new = i + len(restored)
            sheet.row_dimensions[new] = copy_dimension(dimension,sheet,new)
    for row in sheet:
        for cell in row:
            if cell.hyperlink:
                cell.hyperlink.ref = cell.coordinate
    sheet.auto_filter.ref = sheet.calculate_dimension()
    for table in sheet.tables.values():
        table.ref = sheet.calculate_dimension()
        if table.autoFilter:
            table.autoFilter.ref = table.ref
    return final_rows, restored


def enum_records(sheet):
    h = headers(sheet)
    records = {}
    for i in range(2,sheet.max_row+1):
        gid = sheet.cell(i,h['字典编号']).value
        if not gid:
            continue
        # URI identifies official concepts. Historical entries have a stable
        # dictionary id plus their original reference text.
        if str(gid).startswith('LEGACY-'):
            key = (gid, blank(sheet.cell(i,h['官方英文定义／原文']).value))
        else:
            key = (gid, blank(sheet.cell(i,h['实际提交值／完整URI']).value))
        assert key not in records, ('duplicate dictionary entry',key)
        records[key] = i
    return records,h


def remap_validation(original_validations, base_validations, sheet, current_map, restored_base_map, extra_rows):
    sheet.data_validations = DataValidationList()
    for validations,mapping,is_current in [(original_validations,current_map,True),(base_validations,restored_base_map,False)]:
        for source in validations:
            target = deepcopy(source)
            target.sqref = ''
            for area in source.sqref.ranges:
                assert area.max_row <= 10000, 'unexpected full-column validation'
                for old_row in range(area.min_row,area.max_row+1):
                    if old_row in mapping:
                        new_row = mapping[old_row]
                    elif is_current and old_row > max(current_map):
                        new_row = old_row + extra_rows
                    else:
                        continue
                    lo,hi = get_column_letter(area.min_col),get_column_letter(area.max_col)
                    target.add(f'{lo}{new_row}:{hi}{new_row}')
            if target.sqref:
                sheet.add_data_validation(target)


def main():
    original_bytes = TARGET.read_bytes()
    original_sha = sha(original_bytes)
    baseline = baseline_path()
    wb = load_workbook(BytesIO(original_bytes))
    before = load_workbook(BytesIO(original_bytes))
    base = load_workbook(baseline)
    sheet = wb['工程化v4.0']
    bs = base['工程化v4.0']
    current, ch = field_rows(sheet)
    original, bh = field_rows(bs)
    assert set(ch) == set(bh)
    assert len(original) == 171
    # The supplied file contains the same stable codes, with old items removed.
    # A rename needs matching by id before treating it as a deleted field.
    extra = set(current)-set(original)
    assert not extra, ('new or renamed fields require an id-aware comparison',sorted(extra))
    missing = [key for key in original if key not in current]
    if not missing:
        print('All 171 engineering fields are already present; nothing changed.')
        return
    assert all('待确认' in str(bs.cell(original[k],bh['语义对应状态']).value) or
               '未确认' in str(bs.cell(original[k],bh['语义对应状态']).value) for k in missing)
    current_order = list(current)
    order = merge_order(list(original),current_order)
    original_validations = deepcopy(sheet.data_validations.dataValidation)
    assert not sheet.merged_cells.ranges and not sheet.conditional_formatting
    assert not any(c.data_type=='f' for row in sheet for c in row)
    final_rows,restored = insert_missing(sheet,bs,order,current,original,ch,bh)
    current_to_final = {1:1,**{old:final_rows[key] for key,old in current.items()}}
    base_to_final = {1:1,**{old:final_rows[key] for key,old in original.items()}}
    remap_validation(original_validations,bs.data_validations.dataValidation,sheet,current_to_final,
                     {original[k]:final_rows[k] for k in restored},len(restored))

    enums = wb['枚举字典']
    base_enums = base['枚举字典']
    current_enum,eh = enum_records(enums)
    original_enum,beh = enum_records(base_enums)
    enum_order = merge_order(list(original_enum),list(current_enum))
    final_enum,restored_enum = insert_missing(enums,base_enums,enum_order,current_enum,original_enum,eh,beh)
    current_enum_map = {1:1,**{i:final_enum[k] for k,i in current_enum.items()}}
    base_enum_map = {1:1,**{i:final_enum[k] for k,i in original_enum.items()}}

    # The three provenance sheets retained their complete pre-deletion data.
    # Their existing locations refer to baseline main-table rows, so map those
    # stable baseline references to the restored table (which keeps user order).
    changed_refs = 0
    for name in ['规则与映射','v3.4变更对照','字段来源核对']:
        target = wb[name]
        reference = base[name]
        th,rh = headers(target),headers(reference)
        assert th == rh and target.max_row == reference.max_row
        location = next(th[k] for k in ['主表位置','主表定位','v4主表定位'] if k in th)
        for i in range(2,target.max_row+1):
            raw = reference.cell(i,location).value
            if isinstance(raw,str) and re.search(r'第\d+行',raw):
                value = ', '.join(dict.fromkeys('第'+str(base_to_final[int(n)])+'行'
                        for n in re.findall(r'第(\d+)行',raw)))
                cell = target.cell(i,location)
                changed_refs += cell.value != value
                cell.value = value
        for row in target:
            for cell in row:
                if not cell.hyperlink:
                    continue
                ref = reference[cell.coordinate]
                raw = ref.hyperlink.target if ref.hyperlink else cell.hyperlink.target
                match = re.fullmatch(r"#'工程化v4\.0'!([A-Z]+)(\d+)",raw or '')
                if match:
                    cell.hyperlink.target = "#'工程化v4.0'!"+match[1]+str(base_to_final[int(match[2])])
    for key,row in final_rows.items():
        for cell in sheet[row]:
            if cell.hyperlink:
                m = re.fullmatch(r"#'枚举字典'!([A-Z]+)(\d+)",cell.hyperlink.target or '')
                if m:
                    title = sheet.cell(1,cell.column).value
                    baseline_link = bs.cell(original[key],bh[title]).hyperlink
                    was_baseline_link = baseline_link and baseline_link.target == cell.hyperlink.target
                    mapping = base_enum_map if key not in current or was_baseline_link else current_enum_map
                    old_index = int(m[2])
                    if old_index not in mapping:
                        mapping = base_enum_map
                    cell.hyperlink.target = "#'枚举字典'!"+m[1]+str(mapping[old_index])
    old_comment = sheet['A1'].comment.text if sheet['A1'].comment else ''
    sheet['A1'].comment = Comment(old_comment+'\n2026-11-04：恢复被删除的71条旧工程字段，保留现有字段的内容与相对顺序，并恢复相关历史枚举及跳转位置。','恢复说明')
    wb.save(RESTORED)

    # Verify retained user edits and recovered records separately.
    result = load_workbook(RESTORED)
    rs = result['工程化v4.0']
    rr,rh = field_rows(rs)
    assert set(rr) == set(original) and len(rr) == 171
    assert [k for k in rr if k in current] == current_order
    for key,old_row in current.items():
        for title,column in ch.items():
            a,b = before['工程化v4.0'].cell(old_row,column),rs.cell(rr[key],rh[title])
            assert blank(a.value) == blank(b.value), ('retained user content changed',key,title)
            for attr in ['font','fill','border','alignment','protection']:
                assert copy(getattr(a,attr)) == copy(getattr(b,attr)), ('retained style changed',key,title,attr)
            assert a.number_format == b.number_format
    for key in restored:
        for title,column in bh.items():
            assert blank(bs.cell(original[key],column).value) == blank(rs.cell(rr[key],rh[title]).value), ('restored source mismatch',key,title)
    after_enum,ah = enum_records(result['枚举字典'])
    assert set(after_enum) == set(original_enum)|set(current_enum)
    for key,old_row in current_enum.items():
        for title,column in eh.items():
            assert blank(before['枚举字典'].cell(old_row,column).value) == blank(result['枚举字典'].cell(after_enum[key],ah[title]).value)
    for name in ['规则与映射','v3.4变更对照','字段来源核对']:
        left,right = before[name],result[name]
        hh=headers(left)
        location=next(hh[k] for k in ['主表位置','主表定位','v4主表定位'] if k in hh)
        for row in left:
            for cell in row:
                if cell.column!=location:
                    assert blank(cell.value)==blank(right[cell.coordinate].value),(name,cell.coordinate)
    links = 0
    for ss in result:
        for row in ss:
            for cell in row:
                if cell.hyperlink and (cell.hyperlink.target or '').startswith('#'):
                    m=re.fullmatch(r"#'([^']+)'!([A-Z]+\d+)",cell.hyperlink.target)
                    assert m and result[m[1]][m[2]].value is not None,(ss.title,cell.coordinate,cell.hyperlink.target)
                    links+=1
    assert {rs.cell(i,rh['3.4源行']).value for i in rr.values() if rs.cell(i,rh['3.4源行']).value} == set(range(2,123))
    assert sha(TARGET.read_bytes()) == original_sha, 'source file changed during restoration; recovered copy kept'

    # Keep exact pre-recovery bytes and a separate recovered copy before the
    # atomic replacement, since this workbook is currently open in an editor.
    stamp=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')
    backups=OUT/'历史备份'
    backups.mkdir(exist_ok=True)
    backup=backups/(TARGET.stem+'_恢复前_'+stamp+'.xlsx')
    backup.write_bytes(original_bytes)
    assert sha(backup.read_bytes())==original_sha
    data=RESTORED.read_bytes()
    assert sha(TARGET.read_bytes())==original_sha
    handle=tempfile.NamedTemporaryFile(dir=OUT,prefix='.dpp-restore-',suffix='.xlsx',delete=False)
    temporary=Path(handle.name)
    try:
        handle.write(data)
        handle.close()
        os.chmod(temporary,stat.S_IMODE(TARGET.stat().st_mode))
        os.replace(temporary,TARGET)
    finally:
        handle.close()
        temporary.unlink(missing_ok=True)
    assert sha(TARGET.read_bytes())==sha(data)
    report=dict(target=str(TARGET),recoveredCopy=str(RESTORED),backup=str(backup),
        beforeFields=len(current),afterFields=len(rr),restoredFields=len(restored),
        restoredEnumEntries=len(restored_enum),existingContentsAndStylesPreserved=True,
        existingRelativeOrderPreserved=True,sourceBusinessRowsPreserved=121,
        repairedLocationCells=changed_refs,internalLinksVerified=links,
        beforeSha256=original_sha,afterSha256=sha(data),verification='passed',
        restored=[dict(field=key,title=bs.cell(original[key],bh['数据项名称']).value,
                       fieldId=bs.cell(original[key],bh['字段ID']).value,row=rr[key]) for key in restored])
    (OUT/'旧字段恢复记录.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='restored'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
