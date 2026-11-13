from pathlib import Path
from lxml import etree as ET
from copy import deepcopy
from datetime import datetime
import json

ROOT=Path(__file__).resolve().parent
WORK=ROOT/'ppt_workspace'
AUTHOR=WORK/'authoring-svg-flat'
NS='http://www.w3.org/2000/svg'
def q(name):return f'{{{NS}}}{name}'
def element(parent,tag,**attrs):return ET.SubElement(parent,q(tag),{k.replace('_','-'):str(v) for k,v in attrs.items()})
def text(parent,x,y,content,size=20,color='#202A37',bold=False,anchor='start'):
    n=element(parent,'text',x=x,y=y,font_size=size,fill=color,text_anchor=anchor)
    if bold:n.set('font-weight','bold')
    n.text=content
    return n
def group(parent,id,x,y,w,h):
    return element(parent,'g',id=id,**{'data-pptx-bounds':f'{x} {y} {w} {h}'})
def box(parent,x,y,w,h,fill='#F3F6FB',stroke='#D7E1EF',rx=6):
    return element(parent,'rect',x=x,y=y,width=w,height=h,rx=rx,ry=rx,fill=fill,stroke=stroke,stroke_width=1)

def empty_copy(title,subtitle):
    root=ET.parse(str(AUTHOR/'slide_10.svg')).getroot()
    for child in list(root):
        ref=child.get('data-pptx-source-ref','')
        keep=ref.startswith(('layout:','master:')) or (ET.QName(child).localname=='rect' and child.get('x')=='0' and child.get('width')=='1280')
        if not keep:root.remove(child)
    root.set('data-pptx-page-role','content')
    header=group(root,'page-title',38,13,1204,46)
    text(header,38,46,title,32,'#000000',True)
    sub=group(root,'page-subtitle',38,91,1204,35)
    text(sub,38,115,subtitle,21,'#556477')
    return root

def marker(root):
    defs=element(root,'defs')
    m=element(defs,'marker',id='step-arrow',markerWidth=10,markerHeight=10,refX=8,refY=5,orient='auto',markerUnits='userSpaceOnUse')
    element(m,'polygon',points='0,0 9,5 0,10',fill='#7894BC')

def line(root,id,x1,y1,x2,y2,arrow=True):
    n=element(root,'line',id=id,x1=x1,y1=y1,x2=x2,y2=y2,stroke='#7894BC',stroke_width=2.5,**{'data-pptx-role':'decoration'})
    if arrow:n.set('marker-end','url(#step-arrow)')
    return n

def step(root,n,x,y,w,h,title,lines,emphasis=False):
    g=group(root,f'step-{n:02d}',x,y,w,h)
    box(g,x,y,w,h,'#EAF0F9' if emphasis else '#F5F7FB','#9BB3D8' if emphasis else '#D7E1EF')
    element(g,'line',x1=x+2.5,y1=y,x2=x+2.5,y2=y+h,stroke='#4472C4',stroke_width=5)
    text(g,x+23,y+42,f'{n:02d}',30,'#4472C4',True)
    text(g,x+23,y+82,title,23,'#1F4E79',True)
    t=element(g,'text',x=x+23,y=y+125,font_size=19,fill='#34465C')
    for i,s in enumerate(lines):
        if i==0:t.text=s
        else:
            child=element(t,'tspan',x=x+23,dy=28);child.text=s
    return g

def roadmap():
    root=empty_copy('模型4.0实施与落地','从法规要求、工程设计到功能上线与数据割接，推进七个实施步骤。')
    marker(root)
    top_x=[38,349.33,660.67,972]
    for i in range(3):line(root,f'flow-top-{i+1}',top_x[i]+270,265,top_x[i+1]-10,265)
    # Step 4 continues to step 5 through the clear gap between the rows.
    line(root,'flow-next-row-a',1107,365,1107,400,False)
    line(root,'flow-next-row-b',1107,400,221,400,False)
    line(root,'flow-next-row-c',221,400,221,430)
    line(root,'flow-bottom-1',404,530,446,530)
    line(root,'flow-bottom-2',823,530,865,530)
    step(root,1,top_x[0],165,270,200,'法规4.0输出',['形成法规模型','明确字段要求'])
    step(root,2,top_x[1],165,270,200,'法规4.0用户宣贯',['对齐字段含义、适用规则','与使用方式'])
    step(root,3,top_x[2],165,270,200,'工程化4.0输出',['形成工程字段、数据结构','与实施规则'])
    step(root,4,top_x[3],165,270,200,'系统影响面评估',['实体改动、API改动','护照编辑器改动','二维码落地页改动'],True)
    step(root,5,38,430,366,200,'PRD输出',['明确功能需求、交互流程','与改造范围'])
    step(root,6,457,430,366,200,'功能上线',['完成相关功能开发','验证与发布'])
    step(root,7,876,430,366,200,'数据割接',['手动补数据','系统割接'],True)
    foot=group(root,'roadmap-summary',38,658,1204,35)
    text(foot,38,682,'实施路径：法规要求对齐 → 工程化设计 → 系统改造 → 上线与数据割接',19,'#556477')
    return root

def layer(root,n,y,h,title,items,columns=2):
    g=group(root,f'implementation-layer-{n}',38,y,1204,h)
    box(g,38,y,1204,h,'#F3F6FB','#D7E1EF',0)
    label=element(g,'g')
    element(label,'rect',x=38,y=y,width=258,height=h,fill='#4472C4' if n<5 else '#1F4E79',stroke='none')
    text(label,60,y+h/2+9,f'{n:02d}',25,'#FFFFFF',True)
    text(label,108,y+h/2+8,title,22,'#FFFFFF',True)
    xs=[334,638,942] if columns==3 else [334,790]
    baselines=[y+38,y+78] if len(items)>columns else [y+h/2+8]
    for i,s in enumerate(items):
        x=xs[i%columns];yy=baselines[i//columns]
        item=element(g,'g')
        element(item,'circle',cx=x+2.5,cy=yy-10.5,r=2.5,fill='#4472C4',stroke='none')
        text(item,x+15,yy,s,21,'#202A37')
    return g

def layers():
    root=empty_copy('工程化4.0落地实施步骤','围绕数据录入、护照注册、数据展示、欧盟交互与数据割接，推进五个实施环节。')
    layer(root,1,145,112,'数据录入环节',['API改动','护照编辑器改动','实体改动','变更功能','护照状态机改动','期初数据导入'],3)
    layer(root,2,269,96,'护照注册',['注册模块','自动化逻辑','Token逻辑','EU Registry凭证管理'])
    layer(root,3,377,70,'数据展示环节',['二维码展示页改动'])
    layer(root,4,459,88,'欧盟注册中心交互',['语义模型1.0转换逻辑','JTC24接口改动'])
    layer(root,5,559,88,'数据割接策略',['手动补数据','系统割接方案'])
    return root

plan_path=ROOT/'新增两页内容与插入方案.json'
plan=json.loads(plan_path.read_text());plan['status']='confirmed';plan['confirmation']='用户回复“确认”后执行';plan['confirmed_at']=datetime.now().isoformat();plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2))
pages=[{'source_slide':i} for i in range(1,11)]+[{'source_slide':10,'svg':'implementation_roadmap.svg'},{'source_slide':10,'svg':'implementation_layers.svg'},{'source_slide':11}]
(WORK/'page_plan.json').write_text(json.dumps({'schema':'ppt-master.roundtrip-page-plan.v1','pages':pages},ensure_ascii=False,indent=2))
for name,build in [('implementation_roadmap.svg',roadmap),('implementation_layers.svg',layers)]:
    tree=build()
    (AUTHOR/name).write_bytes(ET.tostring(tree,xml_declaration=True,encoding='UTF-8',pretty_print=True))
print(json.dumps({'new_svgs':['implementation_roadmap.svg','implementation_layers.svg'],'output_pages':13,'original_pages_referenced':11},ensure_ascii=False))
