from pathlib import Path
from datetime import datetime
import json, re, unicodedata, collections, hashlib
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter, range_boundaries

ROOT = Path(__file__).resolve().parent

def nf(s):
    return unicodedata.normalize('NFKC', str(s or '')).strip()

def compact(s):
    return re.sub(r'\s+', '', nf(s)).lower()

def chinese_name(s):
    # Keep embedded Latin business notation, e.g. C率 and ID, while removing
    # the English translation appended on the same line.
    lines = [x.strip() for x in nf(s).splitlines() if x.strip()]
    if not lines: return ''
    first = lines[0]
    if not re.search('[\u4e00-\u9fff]', first): return ''
    first = re.split(r'\s+(?=[A-Za-z])|(?<=[\u4e00-\u9fff])(?=[A-Z][a-z])', first, maxsplit=1)[0]
    return re.sub(r'\s+', '', first)

def english_name(s):
    cn = chinese_name(s)
    if not cn: return compact(s)
    full = compact(s)
    return full[len(compact(cn)):] if full.startswith(compact(cn)) else ''

def load(label, name_col, cat_col, req_col, perm_col, first, expected_range):
    raw = json.loads((ROOT / f'{label}_快照.json').read_text())
    meta = json.loads((ROOT / f'{label}_结构.json').read_text())
    assert raw['success'] and not raw['hasMore'] and not raw['truncationReasons']
    assert raw['returnedRange'] == expected_range
    rr = []
    for rn, cells in zip(raw['rowIndices'], raw['cells']):
        if rn < first: continue
        values = [c.get('value', '') for c in cells]
        name = values[name_col-1]
        rr.append({'sheet': meta['name'], 'row': rn, 'name': name, 'cn': chinese_name(name), 'en': english_name(name),
                   'category': values[cat_col-1], 'required': values[req_col-1], 'permission': values[perm_col-1], 'inherited': {}})
    colkeys = {name_col: 'name', cat_col: 'category', req_col: 'required', perm_col: 'permission'}
    byrow = {r['row']: r for r in rr}
    # Only inherit values where the server reports a real merged cell.
    for merge in meta['mergedRanges']:
        c1, r1, c2, r2 = range_boundaries(merge)
        for c, key in colkeys.items():
            if c1 <= c <= c2 and r1 in byrow:
                for rn in range(r1+1, r2+1):
                    if rn in byrow:
                        byrow[rn][key] = byrow[r1][key]
                        byrow[rn]['inherited'][key] = merge
    return rr, raw, meta

fc, fc_raw, fc_meta = load('FCV4', 4, 5, 6, 12, 3, 'A1:L98')
eng, eng_raw, eng_meta = load('工程化V4', 3, 7, 8, 9, 2, 'A1:I145')
assert '数据项名称' in fc_raw['cells'][1][3]['value']
assert '数据项名称' in eng_raw['cells'][0][2]['value']
assert '披露权限' in fc_raw['cells'][1][11]['value']
assert '披露权限' in eng_raw['cells'][0][8]['value']

def group(rr):
    out = collections.defaultdict(list)
    for r in rr:
        if r['cn']: out[r['cn']].append(r)
    return dict(out)

fg, eg = group(fc), group(eng)
common = [n for n in fg if n in eg]
single = [n for n in common if len(fg[n]) == len(eg[n]) == 1]
multiple = [n for n in common if n not in single]
unfc = [r for r in fc if r['cn'] not in eg]
uneng = [r for r in eng if r['cn'] and r['cn'] not in fg]
blank = [r for r in eng if not r['cn']]
assert len(fc) == 96 and len(eng) == 144
assert len(common) == 77 and len(single) == 70 and len(multiple) == 7 and len(unfc) == 16

def cat(s):
    text = nf(s)
    if not text: return None
    return set(re.findall(r'(?<![A-Za-z])(?:EV|LMT|IND)(?![A-Za-z])', text))

def cat_compare(a,b):
    if cat(a) != cat(b): return '不一致', f'FC大类={a}；工程化大类={b}。'
    if ('无BMS的IND' in nf(a)) != ('无BMS的IND' in nf(b)):
        return '大类一致·细分写法不同', '工程化额外标注“无BMS的IND”；保留细分，不据此认定BMS具体适用条件一致。'
    return '一致', '分类代码集合相同；仅统一空白、分隔符与顺序。'

def req(s, category):
    s = re.sub(r'\s+', '', nf(s))
    if not s: return None
    cats = cat(category) or set()
    if s in ['M', 'C', 'V']:
        return {c: s for c in cats}
    # Conditions are deliberately retained instead of flattening to plain M.
    if any(x in s for x in ['如果', 'IND-BESS', '仅适用', 'withBMS']): return {'condition': s}
    out = {}
    pairs = re.findall(r'(EV(?:/LMT)?(?:/IND)?|LMT(?:/IND)?|IND):(M|C|V|N/A)', s)
    reverse = re.findall(r'(M|C|V|N/A)\((EV(?:/LMT)?(?:/IND)?|LMT(?:/IND)?|IND)\)', s)
    for cs, value in pairs:
        for c in cs.split('/'): out[c] = value
    for value, cs in reverse:
        for c in cs.split('/'): out[c] = value
    if not out: return {'condition': s}
    return out

def req_compare(a,b):
    x,y=req(a['required'],a['category']),req(b['required'],b['category'])
    if x is None or y is None: return '空缺·需确认', '至少一侧的必填单元格为空。'
    if 'condition' in x or 'condition' in y:
        if compact(a['required']) == compact(b['required']): return '一致', '条件文字相同。'
        note = f"FC：{a['required']}；工程化：{b['required']}。保留条件原文，不能将带条件的M直接当作无条件M。"
        if 'EV/IND-BESS' in nf(a['required']) and cat(a['category']) == {'LMT','IND'}:
            note += ' FC分类仅列LMT/IND，但条件写EV/IND-BESS，存在表内适用范围疑点。'
        if '商业保修' in nf(a['required']):
            note += ' 两侧均可表达条件必填，但工程化C单元格未明确写出商业保修条件。'
        return '条件表达不同·需确认', note
    diff = [f"{c}：FC={x.get(c,'未列')}，工程化={y.get(c,'未列')}" for c in ['EV','LMT','IND'] if x.get(c) != y.get(c)]
    return ('不一致' if diff else '一致', '；'.join(diff) if diff else '按分类展开后M/C/V规则相同。')

ROLE = {'Public': {'Public'}, 'R1': {'PLI','NB','市场监管机构','委员会'}, 'R2': {'NB','市场监管机构','委员会'}, 'R3': {'PLI'}}

def perm(s):
    s = nf(s)
    if not s: return None
    if s.lower() == 'public': return ROLE['Public']
    m = re.search(r'Restricted\s*([123])', s, re.I)
    if m: return ROLE['R'+m.group(1)]
    parts = set(s.split('/'))
    assert parts <= {'PLI','NB','市场监管机构','委员会'}, s
    return parts

def perm_compare(a,b):
    x,y=perm(a),perm(b)
    if x is None or y is None: return '空缺·需确认', '至少一侧权限单元格为空。'
    if x == y:
        return '角色一致', '可见角色集合一致。工程化PLI未显式写Restricted 3/单块电池粒度，不能据此确认访问视图与粒度完全相同。' if 'PLI' in x else '可见角色集合一致；Public/public仅大小写不同。'
    diff=[]
    if x-y: diff.append('工程化少了：'+'/'.join(sorted(x-y)))
    if y-x: diff.append('工程化多了：'+'/'.join(sorted(y-x)))
    return '不一致', '；'.join(diff)+'。只比较表内可见角色，不审定法规权限。'

results=[]
for name in single:
    a,b=fg[name][0],eg[name][0]
    cs,cn=cat_compare(a['category'],b['category'])
    rs,rn=req_compare(a,b)
    ps,pn=perm_compare(a['permission'],b['permission'])
    results.append({'anchor':name,'fc':a,'engineering':b,'category_status':cs,'category_note':cn,'required_status':rs,'required_note':rn,'permission_status':ps,'permission_note':pn,
                    'full_name_same':compact(a['name'])==compact(b['name'])})

multi_notes = {
 '生产地点': 'FC13对应工程化30—33四个同名行。已纳入核对期间的更新：工程化31—33分类均已填EV/LMT/IND、必填均为M（H32:H33为实际合并）；31—33权限仍为空。只继承真实合并值，不能从30行下填权限。',
 '检测报告': 'FC25为M；工程化45/46/47/50为M，48/49为V。先区分整项与拆分子项，再决定必填规则，不把六行当六个独立FC字段。',
 '零部件编号及备件来源信息': 'FC40为Restricted 1；工程化79为PLI，82为PLI/NB/市场监管机构/委员会，权限角色范围不同，需明确两行各自业务范围。',
 '额定容量': 'FC56(The capacity)与57(Rated capacity)中文同名，权限分别Public与Restricted 3；工程化100(batteryCapacity)为Public。不能只按中文名选择其中一个。工程化99的中文名为“容量”，不强行归入本组。',
 '初始功率': 'FC65(Original power capability)为Public，66(powerCapability)为Restricted 3；工程化109/111均为Original power capability且Public。FC65有完整名称对应，FC66中文同名但含义有歧义；工程化112中文名为“功率”，本报告不按语义字段改配。',
 '初始内阻': 'FC78(Initial internal resistance...)为Public，79(Internal resistance)为Restricted 3；工程化124完整名称对应FC78且Public。FC79不能仅因中文同名而认定也对应124。',
 '当前完全循环次数': 'FC85为V；工程化134为M、136为C，两个同名行规则不同，需先确定对应关系。',
}

summary = {'fc_data_rows':len(fc),'fc_unique_names':len(fg),'engineering_data_rows':len(eng),'engineering_named_rows':sum(bool(r['cn']) for r in eng),'engineering_unique_names':len(eg),
           'common_name_groups':len(common),'one_to_one_groups':len(single),'multiple_name_groups':len(multiple),'fc_unmatched_names':len(unfc),'engineering_unmatched_named_rows':len(uneng),'engineering_blank_name_rows':len(blank),
           'one_to_one_columns':{c:dict(collections.Counter(r[c+'_status'] for r in results)) for c in ['category','required','permission']}}
differences=[(r,c) for r in results for c in ['category','required','permission'] if r[c+'_status']=='不一致']
summary['explicit_field_column_differences']=len(differences)
summary['unique_names_with_explicit_differences']=len({r['anchor'] for r,c in differences})
data={'created_at':datetime.now().isoformat(),'source_url':'https://alidocs.dingtalk.com/i/nodes/Qnp9zOoBVBByzZaNClgbvO9xV1DK0g6l','summary':summary,'one_to_one':results,
      'multiple_names':[{'anchor':n,'fc':fg[n],'engineering':eg[n],'note':multi_notes[n]} for n in multiple], 'fc_unmatched':unfc,'engineering_unmatched':uneng,'engineering_blank_names':blank,
      'snapshot_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ['FCV4_快照.json','工程化V4_快照.json']}}
(ROOT/'FC与工程化_比对结果.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False,indent=2))
for r,c in differences:
    print(json.dumps({'name':r['anchor'],'column':c,'FC_row':r['fc']['row'],'engineering_row':r['engineering']['row'],'FC':r['fc'][c],'engineering':r['engineering'][c]},ensure_ascii=False))

wb=openpyxl.Workbook(); overview=wb.active;overview.title='核对结论'
overview_rows=[
 ['项目','结论／口径'],
 ['总体结论','FC V4.0与工程化4.0的电池分类、是否必填、披露权限三列不能完全对齐。未修改原表。'],
 ['读取时间',datetime.now().strftime('%Y-%m-%d %H:%M:%S Asia/Shanghai')],
 ['在线工作簿',data['source_url']],
 ['FC工作表','FC V4.0；第2行表头；D数据项名称、E电池分类、F是否必填、L披露权限；96条数据行'],
 ['工程化工作表','工程化4.0；第1行表头；C数据项名称、G电池分类、H是否必填、I披露权限；144条数据行'],
 ['读取完整性','FC A1:L98，工程化A1:I145；均success=true、hasMore=false、truncationReasons=[]。'],
 ['名称锚定','按中文数据项名称精确相同：只统一Unicode全半角、空白和换行；完整中英文名称用于核验。不同中文名不按近义、英文或语义字段自动合并。'],
 ['匹配覆盖','77组共同名称=70组严格一对一+7组同名多行；FC另有16个名称未匹配；工程化27行数据项名称为空。'],
 ['电池分类（70组一对一）','22项一致；47项大类相同但工程化额外列“无BMS的IND”；1项明确差异：当前电阻FC81=LMT/IND，工程化129=EV/LMT/IND。'],
 ['是否必填（70组一对一）','50项一致；15项明确M/C/V规则差异；5项条件表达不同，单列待确认。'],
 ['披露权限（70组一对一）','67项可见角色一致；3项明确角色范围不同：拆卸指南、往返能效、初始自放电率。'],
 ['明确差异合计',f"{len(differences)}处字段—列差异，涉及{summary['unique_names_with_explicit_differences']}个名称；不包含同名多行、5项条件表达与47项分类细分写法差异。"],
 ['7组同名多行','生产地点、检测报告、零部件编号及备件来源信息、额定容量、初始功率、初始内阻、当前完全循环次数。逐行原值见同名多行工作表。'],
 ['同名多行规则','不任意选第一行，不做笛卡尔组合统计；全名可支持部分候选，但整体对应关系仍单列确认。'],
 ['必填条件','保留M(如果提供商业保修)、M(EV/IND-BESS)、M(LMT/IND-BESS with BMS)等原文；不能都简化成普通M。'],
 ['分类细分限制','无BMS的IND保留为细分标注；仅判EV/LMT/IND大类集合相同，不认定具体BMS适用条件一致。'],
 ['权限规范化','按本次表内可见角色比较：Public/public统一；Restricted 1含PLI/NB/市场监管机构/委员会；Restricted 2不含PLI；Restricted 3可见角色为PLI。'],
 ['权限粒度限制','工程化PLI没有显式写Restricted 3及单块电池粒度。角色一致不等于访问视图、粒度完全一致；未重新审定法规正确性。'],
 ['空名称处理','工程化C列没有实际合并区域。27个空名称行不向上继承，也不用D工程化字段或E语义字段猜测对应关系。'],
 ['未匹配不等于缺字段','16个FC名称未匹配只表示本次名称锚定不成立，可能已改名、拆分或被父级覆盖。英文相同的候选仅作参考，不计入主比对。'],
 ['报告工作表','一对一核对、明确差异、必填条件待确认、同名多行、FC未匹配名称、工程化未匹配与空名、分类细分写法、两表读取快照。'],
]
for row in overview_rows:overview.append(row)

headers=['数据项名称锚点','FC行','工程化行','FC名称原文','工程化名称原文','完整中英文名称相同',
         'FC电池分类','工程化电池分类','分类结论','FC是否必填','工程化是否必填','必填结论','FC披露权限','工程化披露权限','权限结论','说明']

def result_row(r):
    a,b=r['fc'],r['engineering']
    return [r['anchor'],a['row'],b['row'],a['name'],b['name'],'是' if r['full_name_same'] else '否；中文名称精确相同',
            a['category'],b['category'],r['category_status'],a['required'],b['required'],r['required_status'],a['permission'],b['permission'],r['permission_status'],
            '\n'.join(r[c+'_note'] for c in ['category','required','permission'])]

allws=wb.create_sheet('一对一核对');allws.append(headers)
for r in results:allws.append(result_row(r))
diffws=wb.create_sheet('明确差异');diffws.append(['数据项名称','差异列','FC行','工程化行','FC原值','工程化原值','差异说明'])
titles={'category':'电池分类','required':'是否必填','permission':'披露权限'}
for r,c in differences:diffws.append([r['anchor'],titles[c],r['fc']['row'],r['engineering']['row'],r['fc'][c],r['engineering'][c],r[c+'_note']])
conditionws=wb.create_sheet('必填条件待确认');conditionws.append(headers)
for r in results:
    if r['required_status']=='条件表达不同·需确认':conditionws.append(result_row(r))

multiws=wb.create_sheet('同名多行');multiws.append(['数据项名称锚点','来源工作表','源行号','名称原文','电池分类原文','是否必填原文','披露权限原文','核对说明'])
for n in multiple:
    for r in fg[n]+eg[n]:
        note=multi_notes[n]
        if r['inherited']:note += ' 本行合并继承：'+'；'.join(f'{titles.get(k,k)}取自{v}' for k,v in r['inherited'].items())+'。'
        multiws.append([n,r['sheet'],r['row'],r['name'],r['category'],r['required'],r['permission'],note])

ufws=wb.create_sheet('FC未匹配名称');ufws.append(['FC行','FC名称原文','FC电池分类','FC是否必填','FC披露权限','仅英文相同的工程化候选','说明'])
for r in unfc:
    candidates=[b for b in eng if b['en'] and b['en']==r['en']]
    ufws.append([r['row'],r['name'],r['category'],r['required'],r['permission'],
                 '\n'.join(f"工程化{b['row']}行：{b['name']}" for b in candidates),
                 '未找到中文同名项；不按语义或近义自动匹配。' + ('英文相同但中文名不同，仅供确认。' if candidates else '')])

uews=wb.create_sheet('工程化未匹配与空名');uews.append(['类型','工程化行','名称原文','电池分类','是否必填','披露权限','说明'])
for r in uneng+blank:uews.append(['中文名称未在FC匹配' if r['cn'] else '数据项名称为空',r['row'],r['name'],r['category'],r['required'],r['permission'],
                                '未按语义字段或近义补配。' if r['cn'] else 'C列无真实合并，不能自动继承上行名称。'])

catsws=wb.create_sheet('分类细分写法');catsws.append(['数据项名称','FC行','工程化行','FC分类','工程化分类','说明'])
for r in results:
    if r['category_status']=='大类一致·细分写法不同':catsws.append([r['anchor'],r['fc']['row'],r['engineering']['row'],r['fc']['category'],r['engineering']['category'],r['category_note']])

for title,raw,meta in [('FC读取快照',fc_raw,fc_meta),('工程化读取快照',eng_raw,eng_meta)]:
    sh=wb.create_sheet(title)
    for cells in raw['cells']:sh.append([c.get('value','') for c in cells])
    for merge in meta['mergedRanges']:
        c1,r1,c2,r2=range_boundaries(merge)
        if c2<=len(raw['colIndices']):sh.merge_cells(merge)

for sh in wb:
    headrow=2 if sh.title=='FC读取快照' else 1
    sh.freeze_panes=f'A{headrow+1}';sh.sheet_view.showGridLines=False
    if sh!=overview:sh.auto_filter.ref=f'A{headrow}:{get_column_letter(sh.max_column)}{sh.max_row}'
    for row in sh:
        for cell in row:
            cell.font=Font(name='Microsoft YaHei',size=10)
            cell.alignment=Alignment(wrap_text=True,vertical='top')
            if cell.value=='不一致':cell.fill=PatternFill('solid',fgColor='FCE4E4')
            elif cell.value=='条件表达不同·需确认':cell.fill=PatternFill('solid',fgColor='FFF2CC')
            elif cell.value=='大类一致·细分写法不同':cell.fill=PatternFill('solid',fgColor='DDEBF7')
            elif cell.value in ['一致','角色一致']:cell.fill=PatternFill('solid',fgColor='E2F0D9')
    for cell in sh[headrow]:
        cell.fill=PatternFill('solid',fgColor='17365D');cell.font=Font(name='Microsoft YaHei',size=10,bold=True,color='FFFFFF')
    sh.row_dimensions[headrow].height=34
    for rn in range(headrow+1,sh.max_row+1):sh.row_dimensions[rn].height=85
    sh.print_title_rows=f'1:{headrow}';sh.sheet_properties.pageSetUpPr.fitToPage=True
    sh.page_setup.orientation='landscape';sh.page_setup.fitToWidth=1;sh.page_setup.fitToHeight=0
    for col in range(1,sh.max_column+1):sh.column_dimensions[get_column_letter(col)].width=30
overview.column_dimensions['A'].width=26;overview.column_dimensions['B'].width=115
for rn in range(2,overview.max_row+1):overview.row_dimensions[rn].height=48
for sh in [allws,conditionws]:
    for i,width in enumerate([36,10,12,45,45,28,24,32,25,30,30,25,65,42,22,90],1):sh.column_dimensions[get_column_letter(i)].width=width
for sh,widths in [(diffws,[38,16,10,12,60,45,95]),(multiws,[38,20,10,55,32,34,68,100]),(ufws,[10,65,25,35,70,70,65]),(uews,[25,12,60,32,35,45,75]),(catsws,[38,10,12,28,35,90])]:
    for i,width in enumerate(widths,1):sh.column_dimensions[get_column_letter(i)].width=width

report=ROOT/'FCV4.0与工程化V4.0_按数据项名称_三列核对_20261111.xlsx'
wb.save(report)
check=openpyxl.load_workbook(report,data_only=True)
assert check['一对一核对'].max_row==71
assert check['明确差异'].max_row==len(differences)+1
assert check['必填条件待确认'].max_row==6
assert check['FC未匹配名称'].max_row==17
assert check['分类细分写法'].max_row==48
assert check['FC读取快照'].max_row==98 and check['工程化读取快照'].max_row==145
assert len({r['anchor'] for r in data['multiple_names']})==7
print(json.dumps({'report':str(report),'sheets':{s.title:s.max_row for s in check},'verified':True},ensure_ascii=False))
