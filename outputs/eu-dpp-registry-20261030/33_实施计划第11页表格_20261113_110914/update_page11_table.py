from pathlib import Path
from lxml import etree as E
import json

ROOT=Path(__file__).resolve().parent
W=ROOT/'ppt_workspace'
P=W/'authoring-svg-flat/slide_11.svg'
S='http://www.w3.org/2000/svg'
def q(s):return f'{{{S}}}{s}'
def add(p,tag,**attrs):return E.SubElement(p,q(tag),{k.replace('_','-'):str(v) for k,v in attrs.items()})
def text(p,x,y,value,size=20,color='#202A37',bold=False,anchor='start'):
    n=add(p,'text',x=x,y=y,font_size=size,fill=color,text_anchor=anchor)
    if bold:n.set('font-weight','bold')
    n.text=value
    return n

tasks=[
    ('1  法规4.0输出','形成法规模型及字段要求','孔瑞霞','11.10'),
    ('2  法规4.0用户宣贯','对齐字段含义、适用规则与使用方式','待确认','待确认'),
    ('3  工程化4.0输出','形成工程字段、数据结构与实施规则','宁蒙昌','11.12'),
    ('4  系统影响面评估','实体、API、护照编辑器、二维码落地页改动','待确认','待确认'),
    ('5  PRD输出','明确功能需求、交互流程与改造范围','待确认','待确认'),
    ('6  功能上线','完成相关功能开发、验证与发布','待确认','待确认'),
    ('7  数据割接','手动补数据、系统割接','待确认','待确认'),
]
root=E.parse(str(P)).getroot()
for child in list(root):
    ref=child.get('data-pptx-source-ref','')
    keep=ref.startswith(('layout:','master:')) or ref=='slide:3' or (E.QName(child).localname=='rect' and child.get('x')=='0' and child.get('width')=='1280')
    if not keep:root.remove(child)

subtitle=add(root,'g',id='implementation-plan-subtitle',**{'data-pptx-bounds':'38 89 1204 36'})
text(subtitle,38,113,'实施任务、责任分工与预期时间',21,'#556477')

x,y=38,138
widths=[820,180,204]
heights=[48,66,66,66,80,66,66,80]
H=sum(heights)
g=add(root,'g',id='implementation-plan-table',**{'data-pptx-replace-with':'table','data-pptx-bounds':f'{x} {y} {sum(widths)} {H}'})
borders={k:{'style':'solid','color':'#CED7E3','width':1} for k in ['left','right','top','bottom']}
rows=[[{'text':s,'fill':'#4472C4','color':'#FFFFFF','font_size':23,'bold':True,'align':'ctr','valign':'middle'} for s in ['任务项','责任人','预期时间']]]
for idx,(title,detail,owner,date) in enumerate(tasks):
    fill='#FFFFFF' if idx%2==0 else '#F3F6FB'
    task={'paragraphs':[{'runs':[{'text':title,'bold':True,'font_size':21,'color':'#1F4E79'}]},
                        {'runs':[{'text':detail,'font_size':19,'color':'#34465C'}]}],
          'fill':fill,'valign':'middle','align':'l'}
    who={'text':owner,'fill':fill,'align':'ctr','valign':'middle','font_size':22,'color':'#556477' if owner=='待确认' else '#202A37'}
    when={'text':date,'fill':fill,'align':'ctr','valign':'middle','font_size':22,'color':'#556477' if date=='待确认' else '#202A37'}
    rows.append([task,who,when])
payload={'schema':'ppt-master.semantic-table.v2','name':'implementation-plan-table','x':x,'y':y,'width':sum(widths),'height':H,
         'strict_grid':True,'header_rows':1,'column_widths':widths,'row_heights':heights,
         'defaults':{'cell':{'fill':'#FFFFFF','valign':'middle','align':'l','borders':borders,
                             'padding_left':18,'padding_right':18,'padding_top':5,'padding_bottom':5},
                     'run':{'font_family':'Microsoft YaHei','font_size':20,'color':'#202A37','lang':'zh-CN'}},'rows':rows}
meta=add(g,'metadata',type='application/json');meta.text=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
# SVG fallback and native payload are authored together, with identical cells,
# fills, geometry and typography. The exporter will emit a native a:tbl.
cy=y
for rn,(cells,height) in enumerate(zip(rows,heights)):
    cx=x
    for col,(cell,width) in enumerate(zip(cells,widths)):
        cg=add(g,'g')
        add(cg,'rect',x=cx,y=cy,width=width,height=height,fill=cell['fill'],stroke='#CED7E3',stroke_width=1)
        if rn==0:
            text(cg,cx+width/2,cy+height/2+8,cell['text'],23,'#FFFFFF',True,'middle')
        elif col==0:
            title,detail=tasks[rn-1][:2]
            text(cg,cx+18,cy+height/2-3,title,21,'#1F4E79',True)
            text(cg,cx+18,cy+height/2+22,detail,19,'#34465C')
        else:
            text(cg,cx+width/2,cy+height/2+8,cell['text'],22,cell['color'],False,'middle')
        cx+=width
    cy+=height
# Keep each fallback primitive in an atomic group, matching the round-trip
# materializer's shape transport so its structural normalization is idempotent.
for cell_group in list(g):
    if E.QName(cell_group).localname!='g':continue
    index=list(g).index(cell_group)
    atoms=list(cell_group)
    g.remove(cell_group)
    for offset,atom in enumerate(atoms):
        atomic=E.Element(q('g'));atomic.append(atom);g.insert(index+offset,atomic)
footer=add(root,'g',id='implementation-plan-note',**{'data-pptx-bounds':'38 682 1204 30'})
text(footer,38,703,'注：11.10、11.12沿用原稿计划；其他责任人与预期时间待确认。',16,'#556477')
P.write_bytes(E.tostring(root,xml_declaration=True,encoding='UTF-8',pretty_print=True))

plan={'authorization':'用户直接指定第11页改为任务项、责任人、预期时间三列表格，延续已确认的七步内容。',
      'source':str((W/'sources/source.pptx').resolve()),'source_pages':13,'output_pages':13,
      'roster':[{'page':n,'mode':'edited' if n==11 else 'referenced'} for n in range(1,14)],
      'table_columns':['任务项','责任人','预期时间'],'tasks':tasks,
      'source_mapping':{'法规4.0输出':'原第13页护照模型V4.0输出：孔瑞霞/11.10；沿用原计划，不代表完成状态或新承诺。',
                        '工程化4.0输出':'原第13页工程化护照模型V4.0输出：宁蒙昌/11.12。',
                        'other_rows':'无同任务明确责任和时间，标注待确认。'},
      'enhancements':{'native_table':True,'notes':'preserve','motion':'preserve','new_audio':False}}
(ROOT/'第11页表格修改方案.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
print(json.dumps({'page':11,'table_rows_including_header':8,'table_cols':3,'bounds':[x,y,sum(widths),H],'other_pages':'referenced'},ensure_ascii=False))
