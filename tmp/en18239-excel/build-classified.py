import json
import re
import subprocess
from pathlib import Path
from collections import Counter
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter

ROOT = Path('/home/ningmengchang/Desktop/注册中心')
OUT = ROOT / 'outputs/en18239-20261026/EN18239_章节解读_DPP系统要求与法规边界.xlsx'
SOURCE = ROOT / 'BS_EN_18239_2026_中英双语版.pdf'
text = (ROOT / 'tmp/en18239-excel/build.mjs').read_text()
literal = text.split('const rows = ', 1)[1].split('\n\nconst workspace', 1)[0].rstrip().rstrip(';')
rows = json.loads(subprocess.check_output(['node', '-e', 'const vm=require("vm");process.stdout.write(JSON.stringify(vm.runInNewContext(process.argv[1])))', literal], text=True))

chapters = [
 ('前言','European Foreword','欧洲前言','出版与标准化背景','说明标准编制及国家采纳安排；不是平台功能清单。'),
 ('0.1','Background','背景','背景说明','说明DPP及相关标准分工，具体产品内容由行业规则确定。'),
 ('0.2','Overview','概述','背景说明','概述身份、权限、保密、安全及责任转移框架；不能据此推定已有具体交接API。'),
 ('1','Scope','范围','适用说明','访问管理、安全、数据保护及责任转移；公开读取与受控访问分开。'),
 ('2','Normative references','规范性引用文件','规范性引用','本文件列出EN 18219:2026，标识细则应结合该标准核对。'),
 ('3','Terms and definitions','术语和定义','定义','统一术语含义，定义本身不直接给出某字段的访问资格。'),
 ('4','Business transactions and responsibilities related to the DPP','DPP相关业务交易与责任','章节总览','业务阶段与参与主体构成访问管理基础。'),
 ('4.1','Business aspects of the DPP life cycle as basis of access management','DPP生命周期的业务方面作为访问管理的基础','标准要求','只考虑适用阶段，并补充产品组特有阶段。'),
 ('4.2','Actors along the DPP life cycle','DPP生命周期中的主体','角色说明','明确EO、维修商、监管方、海关及主/备服务商等主体。'),
 ('5','Requirements for the protection of business confidentiality and the management of access rights','商业保密保护与访问权限管理要求','章节总览','正文含义按完整章节标题整理；源文件简目录标题有缩写。'),
 ('5.1','General','概述','适用说明','第5章基于生命周期及第4.2节业务角色。'),
 ('5.2','Requirements for the protection of business confidentiality','商业保密保护要求','24个编号项','主体身份、匿名公开读取、认证、授权、委托、撤销、字段控制和审计。'),
 ('6','Requirements on system and service resilience','系统与服务韧性要求','章节总览','访问管理、撤销、运营韧性和安全管理。'),
 ('6.1','General requirements','通用要求','要求与建议混合','公开免认证；受控认证；适用时主体及组件都要认证。'),
 ('6.2','Access management','访问管理','职责与访问方案','EO责任范围；公开匿名只读与受控角色访问。'),
 ('6.3','Access revoking policy','访问撤销策略','标准要求','系统及服务必须建立访问撤销策略。'),
 ('6.4','Digital operational resilience','数字运营韧性','章节总览','目标、业务连续性及持续改进。'),
 ('6.4.1','General','概述','目标说明','关键ICT供应链抵御破坏并恢复服务。'),
 ('6.4.2','Business continuity','业务连续性','建议与法律提示','参考连续性标准，同时核对实际适用法律。'),
 ('6.4.3','Continuous improvement','持续改进','建议','持续调整安全措施和策略。'),
 ('6.5','Security management','安全管理','章节总览','安全设计、事件响应、恢复及ISMS。'),
 ('6.5.1','Service availability','服务可用性','法规承接','可用性与及时访问的具体条件由适用法律进一步明确。'),
 ('6.5.2','Security by design','安全设计','要求与建议混合','生命周期措施和评估为shall；设计方法与供应商合同为should。'),
 ('6.5.3','Incident detection and response','事件检测与响应','建议','预防、发现、识别、遏制、分析、响应。'),
 ('6.5.4','Recovery capability','恢复能力','建议','冗余、备份、防DoS与恢复。'),
 ('6.5.5','Continuous improvement','持续改进','要求与建议混合','ISMS与PDCA主要为should/may；政策执行为shall。'),
 ('附录A','Authentication assumptions','认证假设','资料性附录','11项认证假设，不能整体升级为所有接口的无条件强制项。'),
 ('附录ZA','Relationship with Regulation (EU) 2024/1781','与欧盟法规2024/1781的关系','资料性附录','自愿符合路径及有限范围映射；原表需核对官方版本。'),
 ('参考文献','Bibliography','参考文献','资料性参考','引用来源不等于全部要求均自动强制，也不等于要求全部认证。'),
]
chapter_lookup = {r[0]: r for r in chapters}
definitions = [
 ('3.1','受控DPP数据','按用户访问权控制访问的护照信息。','对数据分类，而不是将整本护照一律公开或一律受限。'),
 ('3.2','系统韧性','从安全泄露或攻击中恢复的能力。','需要恢复验证，不只检查服务启动。'),
 ('3.3','系统可用性','经授权主体可按需访问和使用的属性。','可用性应覆盖真实业务请求。'),
 ('3.4','网络韧性','不利条件或攻击下仍能保持业务连续性。','关键依赖故障也要纳入设计。'),
 ('3.5','主体','履行某角色的组织或个人。','组织、个人账号与角色分开记录。'),
 ('3.6','notified actor','由授权认可机构或主管机关赋予相应资格、履行DPP生命周期角色的组织或个人。','不是“收到系统通知的用户”，资格不能仅凭用户自选。'),
 ('3.7','数字产品护照','产品生命周期内产品特征的数字记录。','定义不等于要求每个字段都有完整历史版本接口。'),
 ('3.8','抗抵赖性','防止参与主体否认参与某次操作的能力。','需要可靠身份绑定与可验证操作证据。'),
 ('3.9','ICT','收集、存储、检索、处理、分析和传输信息的技术。','安全范围包括基础设施及依赖。'),
 ('3.10','身份核验','获取并验证足够信息，将实体识别到指定或公认保证水平的过程。','身份核验、登录认证与业务授权不能混用。'),
]

# Correct interpretative overstatements in the earlier, separate workbook.
for r in rows:
 if r[0]=='5.2 第6项':
  r[3]='EO可分配权限；主体资格、产品组相关规则和适用法律仍构成边界。标识本身不等于资格证明。'
  r[6]='notified actor按3.6理解，不是收到消息的人；产品组不是平台随意勾选产品编码的集合。本条未规定必须按型号授权。'
 if r[0]=='5.2 第17项':
  r[4]='具备成文的授予、撤销、修改流程，规定触发条件、职责及生效处理；标准未强制自助申请或人工审批这一特定入口。'
 if r[0]=='6.3':
  r[6]+=' 本文未明确要求撤销后删除接收方已取得的副本；后续平台访问与外部副本处置应分开。'

def section(locator):
 if locator.startswith('附录A '): return '附录A'
 if locator.startswith('附录ZA'): return '附录ZA'
 return re.match(r'[0-9.]+', locator)[0]

def category(locator):
 if locator.startswith('附录'): return '资料性说明/指南'
 if locator in ['5.2 第6项','5.2 第7项','5.2 第13项','5.2 第20项','5.2 第22项','5.2 第23项','6.5.1']:
  return '标准要求＋法规承接'
 if locator.startswith('4.2') or locator=='5.1' or locator=='6.4.1': return '角色/适用范围说明'
 if locator.startswith(('6.4.2','6.4.3','6.5.2','6.5.3','6.5.4','6.5.5','5.2 第21项')):
  return '标准要求/安全治理建议'
 return '标准中的DPP系统要求'

def law_boundary(locator):
 if locator in ['5.2 第6项','5.2 第7项','5.2 第23项']:
  return '角色及字段资格由适用法律确定；电池补充核对77(2)、77(9)、78(f)、附录XIII。不是EN给出固定电池权限表。'
 if locator in ['5.2 第13项','5.2 第22项']:
  return '标准要求认证/授权，具体法律认证条件须另查；不得自行推定所有访问都需实名或特定电子签名。'
 if locator=='5.2 第20项': return '受托角色法律义务及数据责任关系需核对；电池代办另参77(4)，不等于共享账号。'
 if locator=='6.5.1': return '本条没有给出数值SLA；须查适用法案，不把平台自定99.99%写成EN强制值。'
 if locator.startswith('附录ZA'): return '仅按提供的译本说明；不是重新核定的官方符合性映射，未确认公报引用状态。'
 if locator=='6.4.2': return '正文提示可能适用其他地方法律，未列出针对所有平台的固定法律义务。'
 return '本条来源为EN标准，不自动等同独立法定义务；是否有同主题法律规定须另行核对。'

def responsibility(locator):
 if locator.startswith('附录ZA'): return '合规/法务'
 if locator.startswith('附录A'): return '身份认证/安全治理（参考）'
 if locator.startswith('4.2') or locator in ['5.2 第6项','5.2 第7项','6.2（EO的访问管理）']: return 'EO业务/合规＋平台配置'
 if locator.startswith(('6.4','6.5')) or locator=='5.2 第21项': return '平台安全/运维＋组织治理'
 return 'DPP系统＋授权管理人员'

wb=Workbook(); wb.remove(wb.active)
def add_sheet(name, headers, data, widths):
 ws=wb.create_sheet(name); ws.append(headers)
 for record in data: ws.append(record)
 ws.freeze_panes='D2' if len(headers)>6 else 'B2'
 ws.sheet_view.zoomScale=85
 for c in ws[1]: c.fill=PatternFill('solid',fgColor='17365D');c.font=Font(name='Microsoft YaHei',size=11,bold=True,color='FFFFFF');c.alignment=Alignment(wrap_text=True,vertical='center')
 ws.row_dimensions[1].height=34
 for i,w in enumerate(widths,1):ws.column_dimensions[get_column_letter(i)].width=w
 for row in ws.iter_rows(min_row=2):
  max_lines=1
  for c in row:
   c.font=Font(name='Microsoft YaHei',size=10,color='243247');c.alignment=Alignment(wrap_text=True,vertical='top')
   if c.row%2==0:c.fill=PatternFill('solid',fgColor='EDF3F8')
   val=str(c.value or '');width=widths[c.column-1]
   units=sum(2 if ord(ch)>127 else 1 for ch in val)
   max_lines=max(max_lines,(units//max(int(width)-2,1))+val.count('\n')+1)
   if val.startswith('https://'): c.hyperlink=val;c.font=Font(name='Microsoft YaHei',size=10,color='0563C1',underline='single')
  ws.row_dimensions[row[0].row].height=max(46,min(240,max_lines*16+12))
 if data:
  tab=Table(displayName='T'+str(len(wb.worksheets)),ref=f'A1:{get_column_letter(len(headers))}{ws.max_row}')
  tab.tableStyleInfo=TableStyleInfo(name='TableStyleMedium2',showRowStripes=True);ws.add_table(tab)
 ws.sheet_properties.pageSetUpPr.fitToPage=True
 ws.page_setup.orientation='landscape';ws.page_setup.paperSize=ws.PAPERSIZE_A3;ws.page_setup.fitToWidth=1;ws.page_setup.fitToHeight=0
 ws.print_title_rows='1:1'
 return ws

notes=[
 ('目的','按EN18239章节说明具体含义，区分标准系统要求、组织治理、法规承接与资料性内容。'),
 ('阅读顺序','先看02章节目录，再按目录号筛选03逐条要求；法律来源另看04法规对照。'),
 ('不能二选一','“DPP系统要求”是落实位置，“法规要求”是依据来源；同一主题可能同时属于两者。'),
 ('文档依据',str(SOURCE)),
 ('原始文档性质','用户提供的内部参考中英双语译本；未与出版社正式原版逐字核验，不能作为认证或正式法律结论。'),
 ('范围','覆盖前言、引言、1—6章、3.1—3.10术语、5.2全部24项、附录A全部11项、附录ZA及参考文献。不是全文逐字翻译。'),
 ('章节定位','以正文标题为准；简目录部分标题有省略。5.2编号项不冒写为5.2.1；附录A编号项不冒写为A.1。原PDF目录页码与物理页不一致，采用章节定位。'),
 ('强度','shall=标准要求；should=建议；may=允许/可选。混合句按具体要求拆分，不依据中文“应”一律判定强制。'),
 ('法规标识','03中的法规列是适用边界提示；04为独立补充法律对照，不是声称EN原文逐条引用了电池法规。'),
 ('法律核验范围','2026-10-26核对EUR-Lex对应条文检索结果；页面正文有反爬限制。未穷尽后续实施/授权法案，未确认EN18239官方公报引用状态。'),
 ('产品组与型号','产品组是适用法律中的产品范围概念；EN未规定等于EV/LMT，也未规定等于型号。按A1型号授权是平台设计选择。'),
 ('撤销和副本','撤销后不能凭该授权再次从平台访问受限历史数据；本文未明确规定统一删除接收方已取得的副本。保留/使用/删除另查法律和有效协议。'),
 ('实现列','“平台落实建议”和“验证重点”均为分析建议；具体菜单、API、状态、技术选型不是EN原文规定。'),
 ('已知待核验','notified actor翻译；ISO27000 A.5/A.9引用；ZA.1映射；若干术语来源引用。正式合规使用应核对官方原版。'),
 ('旧文件','本次另建文件，未覆盖2026-10-13版本。未自动发送钉钉。'),
]
add_sheet('01使用说明',['项目','说明'],notes,[25,120])
add_sheet('02章节目录',['章节编号','英文标题（正文）','中文标题','内容性质','这一章节讲什么'],chapters,[16,68,49,28,85])
detail=[]
for key in ['前言','0.1','0.2','1','2']:
 ch=chapter_lookup[key];detail.append([key,ch[2],key,'背景/范围/引用','说明，不是独立功能义务',ch[4],ch[4],'不单独据此新增强制功能。','见使用说明，按适用法律另核对。','合规/产品','核验适用性及来源。','未规定额外API或认证。','用户提供PDF：'+key])
for code,title,meaning,imp in definitions:
 detail.append(['3','术语和定义',code,'术语定义','定义，非独立功能要求',title+'：'+meaning,imp,'用于统一数据、安全和授权模型。','不直接确定电池字段的法定访问人。','产品/架构/合规',imp,'不把定义解释为额外固定API。','用户提供PDF：'+code])
for r in rows:
 loc,strength,content,meaning,req,impl,focus=r;sec=section(loc);ch=chapter_lookup[sec]
 detail.append([sec,ch[2],loc,category(loc),strength,content,meaning,req,law_boundary(loc),responsibility(loc),impl,focus,'用户提供PDF：'+loc])
detail.append(['参考文献','参考文献','Bibliography','资料性参考','参考来源','参考资料涉及信息安全、连续性、供应链及身份保证等。','提供进一步方法来源，不等于所有引用文件均成为全部强制要求。','根据实际风险和引用性质选择核对。','法律及认证适用性应独立确认。','安全/合规','建立引用版本与适用范围清单。','不把参考书目变成强制认证清单。','用户提供PDF：Bibliography'])
add_sheet('03逐条要求',['章节编号','章节中文标题','条款/编号项','要求来源分类','条款强度/性质','文档内容（中文概括）','具体含义','DPP系统/管理要求','法规要求及边界','主要落实方','平台落实建议（非原文）','验证重点及限制','来源定位'],detail,[15,34,28,29,31,64,63,65,66,30,72,76,34])

espr='https://eur-lex.europa.eu/eli/reg/2024/1781/oj'
battery='https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32023R1542'
laws=[
 ('L01','ESPR 2024/1781','10(1)(g)','访问遵守产品组相关授权法案。','5.2第6、7、23项','法律定义资格；平台执行策略。','不是EN规定固定产品组枚举。',espr),
 ('L02','ESPR 2024/1781','11(a)','DPP具备技术、语义和组织互操作性。','4.2；附录ZA表ZA.1','身份与责任关系可跨参与方理解。','ZA表映射仅按内部译本；须核对官方原表。',espr),
 ('L03','电池法规2023/1542','77(2)、附录XIII','区分公开信息及不同受限信息。','5.2第2、7、22、23项','按法定访问资格落实到字段。','维修角色不自动取得所有字段。',battery),
 ('L04','电池法规2023/1542','77(4)','负责EO可书面授权其他主体代办。','5.2第18、20项；6.2','记录责任EO及代办依据。','与普通查看授权不是同一概念。',battery),
 ('L05','电池法规2023/1542','77(9)','具体合法利益主体及下载、分享、发布、再利用范围由实施法案明确。','5.2第7项；6.3的外部副本问题','规则应可更新。','本表未穷尽实施法案；不能推定撤销即必须删除所有副本。',battery),
 ('L06','电池法规2023/1542','78(b)、78(f)','按权利免费访问；读取、新增、修改及更新受权限约束。','5.2第22、23项；6.2','读写分别控制。','此为补充法律对照，不是EN官方逐条映射。',battery),
]
add_sheet('04法规对照',['对照ID','法律文件','条文','法律要求（概括）','相关EN章节（分析关联）','平台落实含义','适用限制','官方来源'],laws,[13,32,27,69,40,51,67,78])

checks=[
 ('5.2全覆盖','24个原编号项，不新增虚构子章节','通过'),
 ('附录A全覆盖','11个资料性认证假设','通过'),
 ('术语覆盖','3.1—3.10共10个定义','通过'),
 ('要求分层','标准要求、建议、允许、定义、资料性内容已区分','通过'),
 ('法规边界','法规对照单独列示，不声称所有shall均为直接法定义务','通过'),
 ('撤销条款','5.2第17、19项及6.3；区分后续访问和外部副本','通过'),
 ('原版核对','内部译本与官方原版逐字比对','待核对'),
 ('协调标准状态','EN18239实际公报引用和范围','未核查'),
 ('实施法案','产品组及电池访问实施规则是否新增/适用','需持续核对'),
 ('合法权益范围','整型号全部SN访问必要性、角色及证据','上线前核验，不由EN直接保证'),
]
add_sheet('05覆盖与待核验',['检查项','检查内容','结果/状态'],checks,[31,105,50])

OUT.parent.mkdir(parents=True,exist_ok=True)
wb.save(OUT)
reopened=load_workbook(OUT)
assert len(reopened.sheetnames)==5
assert len([r for r in rows if re.fullmatch(r'5\.2 第\d+项',r[0])])==24
assert len([r for r in rows if r[0].startswith('附录A 第')])==11
assert len(detail)==82
for ws in reopened:
 assert ws.freeze_panes and len(ws.tables)==1
 for row in ws:
  for cell in row:
   assert cell.value is not None,(ws.title,cell.coordinate)
   assert cell.data_type!='f'
   assert not (cell.data_type=='e')
print(json.dumps({'output':str(OUT),'bytes':OUT.stat().st_size,'detail_rows':len(detail),'sheets':{ws.title:ws.max_row-1 for ws in reopened},'categories':dict(Counter(r[3] for r in detail))},ensure_ascii=False,indent=2))
