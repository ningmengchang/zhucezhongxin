from pathlib import Path
import json,csv,re,hashlib
from datetime import datetime
import openpyxl
from openpyxl.styles import Font,PatternFill,Alignment
from openpyxl.utils import get_column_letter

ROOT=Path(__file__).resolve().parent
context=json.loads((ROOT/'字段上下文.json').read_text())
draft=[]
for file in ['draft_rows_2_82.json','draft_rows_83_150.json']:
    draft.extend(json.loads((ROOT/file).read_text()))
assert len(draft)==149
assert sorted(d['row'] for d in draft)==list(range(2,151))
byrow={d['row']:d for d in draft}
overrides_file=ROOT/'最终修订.json'
overrides=json.loads(overrides_file.read_text()) if overrides_file.exists() else []
for override in overrides: byrow[override['row']].update(override)

plan=[]
for r in context:
    d=byrow[r['row']]
    description=d['description'].strip()
    assert description and not re.search('[\u4e00-\u9fff\r\n]',description),d
    assert not description.startswith(('=','+','-','@')),d
    assert len(description)<=200,(r['row'],description)
    plan.append({**r,'new_en':description,'source_key':d.get('source_key',''),'notes':d.get('notes',''),'changed':r['en']!=description})

with (ROOT/'英文描述写入.csv').open('w',newline='',encoding='utf-8') as f:
    writer=csv.writer(f,lineterminator='\n')
    writer.writerows([[r['new_en']] for r in plan])
with (ROOT/'英文描述还原.csv').open('w',newline='',encoding='utf-8') as f:
    writer=csv.writer(f,lineterminator='\n')
    writer.writerows([[r['en']] for r in plan])
(ROOT/'更新计划.json').write_text(json.dumps({'created_at':datetime.now().isoformat(),'node':'XPwkYGxZV3BLXRKECrYDrYw5WAgozOKL','sheet_id':'st-e85d417d-79819','sheet_name':'工程化v4.0','range':'J2:J150','rows':plan,
                                           'original_snapshot_sha256':hashlib.sha256((ROOT/'修改前完整快照.json').read_bytes()).hexdigest()},ensure_ascii=False,indent=2))

wb=openpyxl.Workbook();ws=wb.active;ws.title='英文描述修改对照'
ws.append(['在线行号','数据项名称','工程化数据项','原英文描述','新英文描述','语义依据/父字段','内部核对备注','是否有值变化'])
for r in plan:ws.append([r['row'],r['name'],r['engineering'],r['en'],r['new_en'],r['source_key'],r['notes'],'是' if r['changed'] else '否'])
ws.freeze_panes='D2';ws.auto_filter.ref=ws.dimensions;ws.sheet_view.showGridLines=False
for c in ws[1]:
    c.font=Font(name='Microsoft YaHei',size=10,bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='17365D');c.alignment=Alignment(wrap_text=True,vertical='center')
for row in ws.iter_rows(min_row=2):
    for c in row:c.font=Font(name='Microsoft YaHei',size=10);c.alignment=Alignment(wrap_text=True,vertical='top')
    if not row[3].value:row[4].fill=PatternFill('solid',fgColor='E2F0D9')
for i,w in enumerate([12,40,44,75,72,40,85,16],1):ws.column_dimensions[get_column_letter(i)].width=w
ws.row_dimensions[1].height=32
for rn in range(2,151):ws.row_dimensions[rn].height=70
wb.save(ROOT/'英文描述修改对照_20261113.xlsx')
print(json.dumps({'range':'J2:J150','cells':len(plan),'changed_cells':sum(r['changed'] for r in plan),'previously_empty':sum(not str(r['en'] or '').strip() for r in plan),'max_words':max(len(r['new_en'].split()) for r in plan),'max_chars':max(len(r['new_en']) for r in plan)},ensure_ascii=False))
