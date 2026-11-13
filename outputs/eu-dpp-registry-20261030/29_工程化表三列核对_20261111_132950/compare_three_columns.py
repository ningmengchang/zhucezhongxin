from pathlib import Path
import json, re, unicodedata, collections, hashlib
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import range_boundaries, get_column_letter

ROOT = Path(__file__).resolve().parent
local = json.loads((ROOT / '本地快照.json').read_text())
raw = json.loads((ROOT / '在线表_最新读取快照.json').read_text())
meta = json.loads((ROOT / '在线表_结构.json').read_text())
assert raw['success'] and not raw['hasMore'] and not raw['truncationReasons']
assert raw['returnedRange'] == 'A1:I145'
assert '对应欧盟官方语义模型字段' in raw['cells'][0][4]['value']
assert '电池分类' in raw['cells'][0][6]['value']
assert '是否必填' in raw['cells'][0][7]['value']
assert '披露权限' in raw['cells'][0][8]['value']

def norm(x):
    return unicodedata.normalize('NFKC', str(x or '')).replace('\\r', '\r').replace('\\n', '\n').strip()

online = {}
for rn, cells in zip(raw['rowIndices'], raw['cells']):
    vals = [c.get('value', '') for c in cells]
    online[rn] = {'row': rn, 'name': vals[2], 'engineering': vals[3], 'mapping': vals[4],
                  'example': vals[5], 'category': vals[6], 'required': vals[7], 'permission': vals[8],
                  'inherited': {}, 'strike': any(c.get('cellStyles', {}).get('textLineThrough') is True for c in cells[2:5])}
# Inherit only cells inside actual merges; never forward-fill an unmerged blank.
keys = {3: 'name', 4: 'engineering', 5: 'mapping', 6: 'example', 7: 'category', 8: 'required', 9: 'permission'}
for merge in meta['mergedRanges']:
    c1, r1, c2, r2 = range_boundaries(merge)
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            if c in keys and r != r1:
                key = keys[c]
                online[r][key] = online[r1][key]
                online[r]['inherited'][key] = f'{get_column_letter(c1)}{r1} ({merge})'

def tokens(s):
    # Full identifiers with paths; punctuation or prose around a valid path is harmless.
    return set(re.findall(r'(?<![A-Za-z0-9_])(?:\$\.)?[A-Za-z_][A-Za-z0-9_]*(?:(?:\[\])?\.[A-Za-z_][A-Za-z0-9_]*)*', norm(s)))

def path(s):
    return norm(s).removeprefix('$.').replace('[]', '')

def aliases(l):
    # The local official-name column sometimes names a child's parent; it is
    # evidence for hierarchy, never an alias for an independent child row.
    a = {path(l['key']), path(l['path'])}
    # A measurement row representing .value also accepts its containing official object.
    if path(l['path']).endswith('.value'):
        a.add(path(l['path'])[:-6])
    return a - {''}

def categories(s):
    s = norm(s)
    if not s: return None
    found = set(re.findall(r'(?<![A-Za-z])(?:EV|LMT|IND)(?![A-Za-z])', s))
    extras = []
    if '无BMS的IND' in s: extras.append('无BMS的IND')
    return {'base': sorted(found), 'extras': extras, 'text': s}

def cat_compare(a, b):
    ca, cb = categories(a), categories(b)
    if cb is None: return '在线空缺', '', '在线分类单元格为空，且不属于合并单元格。'
    if ca['base'] != cb['base']:
        return '不一致', '/'.join(cb['base']), f"本地大类={'/'.join(ca['base'])}；在线大类={'/'.join(cb['base'])}。"
    if ca['extras'] != cb['extras']:
        return '大类一致·细分写法不同', '/'.join(cb['base']) + '; ' + '/'.join(cb['extras']), '在线另列“无BMS的IND”；保留该细分标注。EV/LMT/IND大类集合相同，不能据此认定BMS适用条件相同。'
    return '一致', '/'.join(cb['base']), '已统一分隔符、空白和顺序。'

def required(s, cats):
    s = re.sub(r'\s+', '', norm(s))
    base = (categories(cats) or {}).get('base', [])
    if not s: return None
    out = {c: 'N/A' for c in ['EV', 'LMT', 'IND'] if c not in base}
    if s in ['M', 'C', 'V']:
        out.update({c: s for c in base})
        return out
    chunks = re.findall(r'(EV(?:/LMT)?(?:/IND)?|LMT(?:/IND)?|IND):(N/A|M|C|V)', s)
    reverse = re.findall(r'(N/A|M|C|V)\((EV(?:/LMT)?(?:/IND)?|LMT(?:/IND)?|IND)\)', s)
    for cats, v in chunks:
        for c in cats.split('/'): out[c] = v
    for v, cats in reverse:
        for c in cats.split('/'): out[c] = v
    assert chunks or reverse, f'Unparsed mandatory: {s}'
    for c in base: out.setdefault(c, '?')
    return out

def req_compare(l, o):
    a, b = required(l['required'], l['category']), required(o['required'], o['category'])
    if b is None: return '在线空缺', '', '在线必填单元格为空，且不属于合并单元格。'
    diffs = [f'{c}: 本地{a.get(c, "?")} / 在线{b.get(c, "?")}' for c in ['EV', 'LMT', 'IND'] if a.get(c) != b.get(c)]
    # Missing EV due to a classification mismatch is visible as N/A here; do not hide it.
    return ('不一致' if diffs else '一致', '; '.join(f'{c}:{b.get(c, "?")}' for c in ['EV', 'LMT', 'IND']), '；'.join(diffs) if diffs else '按EV/LMT/IND展开后相同；仅比较必填代码，不代表C的具体条件已完全一致。')

PERMISSIONS = {
    'PUB': {'Public'},
    'RES1': {'PLI', 'NB', '市场监管机构', '委员会'},
    'RES2': {'NB', '市场监管机构', '委员会'},
    'RES3': {'PLI'},
}

def perm(s):
    s = norm(s)
    if not s: return None
    m = re.match(r'^(PUB|RES[123])(?:\b|\()', s)
    if m: return PERMISSIONS[m.group(1)]
    if s.lower() == 'public': return {'Public'}
    parts = set(s.split('/'))
    if parts <= {'PLI', 'NB', '市场监管机构', '委员会'}: return parts
    raise ValueError(f'Unparsed permissions {s}')

def perm_compare(a, b):
    pa, pb = perm(a), perm(b)
    if pb is None: return '在线空缺', '', '在线权限单元格为空，且不属于合并单元格。'
    detail = '按可见访问角色比较：Public↔公开、PLI↔合法权益人、NB↔公告机构、市场监管机构↔主管机关、委员会↔欧委会。在线未标RES代码及单个电池粒度，未认定访问视图/粒度完全一致。'
    if pa != pb:
        detail = '；'.join(([f"在线少了：{'/'.join(sorted(pa-pb))}"] if pa-pb else []) + ([f"在线多了：{'/'.join(sorted(pb-pa))}"] if pb-pa else [])) + '。' + detail
    return ('不一致' if pa != pb else '角色一致', '/'.join(sorted(pb)), detail)

results = []
for l in local['records']:
    aa = aliases(l)
    matches = [o for rn, o in online.items() if rn > 1 and not o['strike'] and aa & {path(t) for t in tokens(o['mapping'])}]
    kind = '字段直接对应'
    if not matches:
        pp = path(l['path'])
        # Match the longest verified ancestor only as reference, not an independent child row.
        parents = [pp.rsplit('.', i)[0] for i in range(1, pp.count('.') + 1)]
        for parent in parents:
            mm = [o for rn, o in online.items() if rn > 1 and not o['strike'] and parent in {path(t) for t in tokens(o['mapping'])}]
            if mm:
                matches, kind = mm, '仅父字段参考'
                break
    if not matches:
        results.append({'local': l, 'kind': '未找到语义映射', 'matches': [], 'category_status': '无法核对', 'required_status': '无法核对', 'permission_status': '无法核对'})
        continue
    rows = []
    for o in matches:
        cs, cn, cd = cat_compare(l['category'], o['category'])
        rs, rn, rd = req_compare(l, o)
        ps, pn, pd = perm_compare(l['permission'], o['permission'])
        rows.append({'online': o, 'category_status': cs, 'category_norm': cn, 'category_note': cd,
                     'required_status': rs, 'required_norm': rn, 'required_note': rd,
                     'permission_status': ps, 'permission_norm': pn, 'permission_note': pd})
    r = {'local': l, 'kind': kind, 'matches': rows}
    for column in ['category', 'required', 'permission']:
        vals = {x[column + '_status'] for x in rows}
        r[column + '_status'] = next(iter(vals)) if len(vals) == 1 else '粒度不同·需确认'
    results.append(r)

assert len(results) == 92
summary = {'total_fields': len(results), 'match_counts': dict(collections.Counter(r['kind'] for r in results)),
           'direct_columns': {c: dict(collections.Counter(r[c + '_status'] for r in results if r['kind'] == '字段直接对应')) for c in ['category', 'required', 'permission']},
           'parent_columns_reference_only': {c: dict(collections.Counter(r[c + '_status'] for r in results if r['kind'] == '仅父字段参考')) for c in ['category', 'required', 'permission']}}
(ROOT / '三列比对结果.json').write_text(json.dumps({'metadata': local['metadata'], 'online_metadata': meta, 'summary': summary, 'results': results}, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False, indent=2))
for r in results:
    bad = [c for c in ['category', 'required', 'permission'] if r[c + '_status'] in ['不一致', '在线空缺', '粒度不同·需确认', '无法核对']]
    if bad: print(json.dumps({'field': r['local']['key'], 'local_row': r['local']['row'], 'online_rows': [x['online']['row'] for x in r['matches']], 'kind': r['kind'], 'columns': bad, 'details': [{c: (x[c+'_status'], x[c+'_note']) for c in bad} for x in r['matches']]}, ensure_ascii=False))

# The deliverable keeps one row for each of the 92 local fields, while the
# detail sheets retain every contributing online row and the original text.
wb = openpyxl.Workbook()
ws = wb.active
ws.title = '核对结论'
notes = [
    ['项目', '结论／口径'],
    ['总体结论', '两张表的电池分类、是否必填、披露权限三列不能完全对齐。本报告只核对表值，不改变任何源表。'],
    ['读取时间', datetime.now().strftime('%Y-%m-%d %H:%M:%S Asia/Shanghai')],
    ['本地文件', local['metadata']['source']],
    ['本地工作表', '工程化v4.0；92个字段；F工程化字段名称，K电池分类，L是否必填，N披露权限'],
    ['本地文件SHA256', local['metadata']['sha256']],
    ['在线表', 'https://alidocs.dingtalk.com/i/nodes/Qnp9zOoBVBByzZaNClgbvO9xV1DK0g6l'],
    ['在线工作表', '工程化4.0（s2）；145行；E对应欧盟官方语义模型字段，G电池分类，H是否必填，I披露权限'],
    ['在线读取完整性', 'A1:I145；hasMore=false；truncationReasons=[]；已读取实际合并范围H19:H20、E65:E70'],
    ['字段匹配', '92项=84项字段直接对应+8项仅父字段/JSON示例覆盖；后8项不计入直接列值一致或差异数量'],
    ['电池分类（84项）', '26项一致；54项大类相同但在线额外标“无BMS的IND”；1项明确不一致；3项在线空缺'],
    ['是否必填（84项）', '63项一致；17项不一致；1项字段/业务行粒度不同，需确认；3项在线空缺'],
    ['披露权限（84项）', '76项可见角色一致；5项不一致；3项在线空缺。角色一致不等于RES视图和单个电池粒度均已写明'],
    ['明确差异数量', '23处字段-列差异（电池分类1、是否必填17、披露权限5），涉及21个字段'],
    ['空缺字段', 'manufacturingCountry（31行）、manufacturingCity（32行）、manufacturingRegion（33行）：三列均为空，不是合并单元格造成'],
    ['分类细分标注', '54项在线含“EV/LMT/IND/无BMS的IND”，本地为“EV / LMT / IND”。只判大类集合相同；保留无BMS细分，不推定具体BMS适用条件相同'],
    ['必填规范化', 'M/C/V/N/A保留；M(EV/LMT)+C(IND)与EV/LMT:M、IND:C按分类展开比较。C的业务条件保留在明细，不因代码相同而认定条件全文一致'],
    ['权限规范化', '仅按本次表内可见角色集合对照：Public/public↔PUB公开；PLI↔合法权益人；NB↔公告机构；市场监管机构↔主管机关；委员会↔欧委会'],
    ['权限粒度限制', 'PLI不是RES3代码的全局同义词。在线未显式给RES1/RES2/RES3代码及单个电池粒度，角色一致项仍保留该限制'],
    ['合并行特殊情况', 'detailedComposition对应E65:E70一个合并字段，业务行65/67/69必填M、66/68/70为V；不把三条CAS码业务行的V直接视为整个官方字段自愿'],
    ['父字段参考限制', '8个子字段只有父级映射，不能从父行直接确认子项三列；powerLimit.valueRange本地C与父行EV/LMT:M的差别只作参考，不计入17项明确必填差异'],
    ['当前映射修订', 'originalPowerCapability只按E111对应；E109/E110为空。selfDischargingRateSet父行是123，E122为空。batterySubstancesImpact已在E89正常匹配'],
    ['报告工作表', '三列逐字段：92项完整对照；明确差异：23项字段-列差异；待确认与空缺：12项；分类细分写法：54项；在线读取快照：原始A:I及真实合并'],
]
for row in notes: ws.append(row)

HEADERS = ['本地行', '字段ID', '工程化字段名称', '中文名称', '匹配方式', '在线行', '在线语义映射原文',
           '本地电池分类', '在线电池分类', '分类结论', '本地是否必填', '在线是否必填', '必填结论',
           '本地披露权限', '在线披露权限', '权限结论', '本地适用条件（辅助）', '说明', '本地官方JSON路径']

def joined(r, k):
    mm = r['matches']
    if not mm: return ''
    vals = list(dict.fromkeys(str(x['online'].get(k) or '') for x in mm))
    return vals[0] if len(vals) == 1 else '\n'.join(f"在线{x['online']['row']}行：{x['online'].get(k) or '（空）'}" for x in mm)

def report_row(r):
    l = r['local']; mm = r['matches']
    notice = []
    if r['kind'] == '仅父字段参考':
        notice.append('子字段未独立列行；以下在线值仅为父行参考，不能据此判定该子项一致或不一致。')
    for c, title in [('category', '分类'), ('required', '必填'), ('permission', '权限')]:
        if r[c + '_status'] in ['不一致', '在线空缺', '粒度不同·需确认']:
            notice.extend(f"{title}·在线{x['online']['row']}行：{x[c+'_note']}" for x in mm)
    if l['key'] == 'detailedComposition':
        notice.insert(0, '一个官方字段对应六条材料业务行；M/V反映不同业务子项，需先明确整字段规则与子项规则。')
    if r['permission_status'] == '角色一致':
        notice.append('权限仅判可见角色一致；在线未显式给RES代码／单个电池粒度。')
    if r['category_status'] == '大类一致·细分写法不同':
        notice.append('在线多列“无BMS的IND”，本地只列IND；保留细分，不推定具体BMS条件一致。')
    return [l['row'], l['id'], l['key'], l['name'], r['kind'], ', '.join(str(x['online']['row']) for x in mm), joined(r, 'mapping'),
            l['category'], joined(r, 'category'), ('仅父行参考：' if r['kind']=='仅父字段参考' else '')+r['category_status'],
            l['required'], joined(r, 'required'), ('仅父行参考：' if r['kind']=='仅父字段参考' else '')+r['required_status'],
            l['permission'], joined(r, 'permission'), ('仅父行参考：' if r['kind']=='仅父字段参考' else '')+r['permission_status'],
            l['condition'], '\n'.join(notice), l['path']]

allws = wb.create_sheet('三列逐字段')
allws.append(HEADERS)
for r in results: allws.append(report_row(r))

diffws = wb.create_sheet('明确差异')
diffws.append(['差异列', '工程化字段名称', '中文名称', '本地行', '在线行', '本地原值', '在线原值', '差异说明', '本地适用条件（辅助）'])
for r in results:
    if r['kind'] != '字段直接对应': continue
    for c, title in [('category', '电池分类'), ('required', '是否必填'), ('permission', '披露权限')]:
        if r[c+'_status'] != '不一致': continue
        diffws.append([title, r['local']['key'], r['local']['name'], r['local']['row'], ', '.join(str(x['online']['row']) for x in r['matches']),
                       r['local'][c], joined(r, c), '\n'.join(x[c+'_note'] for x in r['matches']), r['local']['condition']])

pendingws = wb.create_sheet('待确认与空缺')
pendingws.append(HEADERS)
for r in results:
    if r['kind'] != '字段直接对应' or any(r[c+'_status'] in ['在线空缺', '粒度不同·需确认'] for c in ['category', 'required', 'permission']):
        pendingws.append(report_row(r))

catws = wb.create_sheet('分类细分写法')
catws.append(['工程化字段名称', '本地行', '在线行', '本地分类', '在线分类', '说明'])
for r in results:
    if r['kind'] == '字段直接对应' and r['category_status'] == '大类一致·细分写法不同':
        catws.append([r['local']['key'], r['local']['row'], ', '.join(str(x['online']['row']) for x in r['matches']),
                      r['local']['category'], joined(r, 'category'), r['matches'][0]['category_note']])

snap = wb.create_sheet('在线读取快照')
for cells in raw['cells']:
    snap.append([c.get('value', '') for c in cells])
for merge in meta['mergedRanges']: snap.merge_cells(merge)

RED = 'FCE4E4'; YELLOW = 'FFF2CC'; GREEN = 'E2F0D9'; GRAY = 'EEEEEE'; BLUE = 'DDEBF7'
for sh in wb:
    sh.freeze_panes = 'A2'
    sh.sheet_view.showGridLines = False
    if sh != ws:
        sh.auto_filter.ref = sh.dimensions
    for cell in sh[1]:
        cell.fill = PatternFill('solid', fgColor='17365D')
        cell.font = Font(name='Microsoft YaHei', size=10, color='FFFFFF', bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    sh.row_dimensions[1].height = 32
    for row in sh.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name='Microsoft YaHei', size=10)
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            if cell.value in ['不一致']:
                cell.fill = PatternFill('solid', fgColor=RED)
            elif cell.value in ['在线空缺', '粒度不同·需确认'] or str(cell.value or '').startswith('仅父行参考'):
                cell.fill = PatternFill('solid', fgColor=YELLOW)
            elif cell.value in ['一致', '角色一致']:
                cell.fill = PatternFill('solid', fgColor=GREEN)
            elif cell.value == '大类一致·细分写法不同':
                cell.fill = PatternFill('solid', fgColor=BLUE)
    sh.sheet_properties.pageSetUpPr.fitToPage = True
    sh.page_setup.orientation = 'landscape'
    sh.page_setup.paperSize = sh.PAPERSIZE_A3
    sh.page_setup.fitToWidth = 1
    sh.page_setup.fitToHeight = 0
    sh.print_title_rows = '1:1'

ws.column_dimensions['A'].width = 25; ws.column_dimensions['B'].width = 115
for row in ws.iter_rows(min_row=2): ws.row_dimensions[row[0].row].height = 50 if len(str(row[1].value or '')) > 100 else 36
for sh in [allws, pendingws]:
    for i, width in enumerate([9, 14, 38, 30, 20, 15, 48, 22, 30, 24, 24, 24, 23, 34, 36, 26, 60, 80, 55], 1):
        sh.column_dimensions[get_column_letter(i)].width = width
    for n in range(2, sh.max_row + 1): sh.row_dimensions[n].height = 100
for sh, widths in [(diffws, [16, 40, 30, 10, 13, 38, 38, 95, 70]), (catws, [42, 10, 15, 25, 32, 90]), (snap, [30, 35, 40, 48, 55, 90, 30, 28, 40])]:
    for i, width in enumerate(widths, 1): sh.column_dimensions[get_column_letter(i)].width = width
    for n in range(2, sh.max_row + 1): sh.row_dimensions[n].height = 75 if sh != snap else 65

for n in range(2, allws.max_row + 1):
    k = allws.cell(n, 3).value
    allws.cell(n, 3).font = Font(name='Microsoft YaHei', size=10, bold=True)
    if allws.cell(n, 5).value == '仅父字段参考':
        allws.cell(n, 5).fill = PatternFill('solid', fgColor=YELLOW)

report = ROOT / '电池护照_本地与在线三列核对_20261111.xlsx'
wb.save(report)
check = openpyxl.load_workbook(report, read_only=False, data_only=True)
assert check['三列逐字段'].max_row == 93
assert check['明确差异'].max_row == 24
assert check['待确认与空缺'].max_row == 13
assert check['分类细分写法'].max_row == 55
assert check['在线读取快照'].max_row == 145
assert {str(x) for x in check['在线读取快照'].merged_cells.ranges} == {'E65:E70', 'H19:H20'}
assert hashlib.sha256(Path(local['metadata']['source']).read_bytes()).hexdigest() == local['metadata']['sha256'], 'Local source changed during comparison'
print(json.dumps({'report': str(report), 'rows': {sh.title: sh.max_row for sh in check}, 'unique_fields_with_explicit_differences': len({r[1].value for r in check['明确差异'].iter_rows(min_row=2)}), 'local_source_unchanged': True}, ensure_ascii=False))
