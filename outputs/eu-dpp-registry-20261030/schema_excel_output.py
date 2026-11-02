"""Write and verify the refined Excel, preserving the original file name."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import math

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from schema_field_details import HEADERS, WIDTHS, enrich

DISPLAY_ORDER = [0, 1, 2, 5, 3, 4, *range(6, len(HEADERS))]
DISPLAY_HEADERS = [HEADERS[i] for i in DISPLAY_ORDER]
DISPLAY_WIDTHS = [16, 52, 28, 32, 31, 52, *WIDTHS[6:]]


def display_rows(rows):
    return [[row[i] for i in DISPLAY_ORDER] for row in rows]


def build(context):
    root, assets, out = context['ROOT'], context['ASSETS'], context['OUT']
    rows, common, views, profiles = (context[k] for k in ['PROFILE_ROWS', 'COMMON_ROWS', 'VIEWS', 'PROFILE_NAMES'])
    result = enrich(assets, rows, common, context['FIELD_META'], context['MEANINGS'], context['VOCABS'], profiles, views)
    now = datetime.now().isoformat(timespec='seconds')
    overview = [
        ('文件性质', '根据欧委会 2026-10-30 官方 Semantic Data Artifacts 生成的内部 Excel 阅读版，中文为内部解释；本 Excel 由我们整理，非欧委会官方 Excel。'),
        ('本次细化', '所有字段补充计量单位或“不适用”的原因、单位提交方式、中文填写格式、Schema 必填状态、SHACL数量要求及业务适用条件；嵌套字段全部保留。'),
        ('官方资源包', 'DPP Registry – Semantic Data Artifacts.zip，资源版本 1.0.0，18份 JSON Schema（draft 2020-12）。'),
        ('官方来源页面', 'https://single-market-economy.ec.europa.eu/single-market/digital-product-passport/resources-documentation_en'),
        ('官方资源包下载', 'https://single-market-economy.ec.europa.eu/document/download/5fad8a3a-9c18-486d-be02-01eb540e91e8_en'),
        ('版本与日期', f'字段字典细化版 v1.1；官方资源仍为 2026-10-30 发布版；本次核对时间 {now}。'),
        ('阅读顺序', '先看“字段对照”，按 EV／LMT／工业比较；“EV字段／LMT字段／工业字段”查看全部嵌套细节；“单位与格式速查”可快速定位计量字段。'),
        ('质保期示例', 'commercialWarrantyPeriod：商业日历寿命质保时长，固定单位为月，JSON integer，minimum=0，无maximum；Schema 非必填，SHACL 0..1，词表注明有商业质保时适用。官方示例24；8年应换算为96。'),
        ('必填列怎么读', '“是否必填”给出阅读结论；同时保留 JSON Schema required、SHACL 数量与词表 scopeNote。不能将“未列 required”直接解释为所有业务场景均可省略。'),
        ('条件与父对象', '父对象可省略时，其中 required 子字段仅在父对象出现后必填；数组元素的 required 只约束已存在的每个元素。anyOf／dependentRequired 单独保留。'),
        ('省略与空值', 'Schema未列required表示字段未被无条件要求出现；并不自动允许null、空字符串或空数组。提供字段后仍须满足类型、长度、元素数量、组合与业务适用规则。'),
        ('SHACL计数单位', 'SHACL minCount／maxCount 统计 RDF 属性值；不是 JSON 对象个数。数组、语言对象和 qualified 子集的数量必须结合 JSON-LD 上下文解释。'),
        ('单位从哪里来', '优先标明 SHACL 固定或允许单位；词表定义中的 months／years／% per year 另行明确。示例单位单独标成示例，不升级成官方唯一单位。'),
        ('单位提交方式', '商业质保期、预期寿命等整数直接填月数／年数／次数；unitValue 结构提交 {"unit":"完整单位URI","value":数值}；范围和直方图各自携带单位。'),
        ('单位数值边界', 'percent 表示百分比计量口径，不自动意味着 Schema 已规定0～100；没有声明的最小值／最大值不擅自补写为官方强制约束。'),
        ('字段对照范围', f'{len(result["compare"])} 个访问视图＋顶层字段组合；EV、LMT、工业的嵌套细节分别为 {len(result["profiles"]["evb"])}、{len(result["profiles"]["lmt"])}、{len(result["profiles"]["ind"])} 行。'),
        ('PUB／RES1／RES2／RES3', 'PUB为公开；RES1为合法权益人、公告机构、成员国主管机关、欧委会；RES2为公告机构、成员国主管机关、欧委会；RES3为合法权益人对单个电池的数据视图。具体授权还需结合实际访问主体与产品范围。'),
        ('差异核对', '“必填与规则核对”集中列出条件、组合口径及源文件表达差异；这张表保留证据，不将差异一律称为官方错误，也不替官方选择放宽某层规则。'),
        ('类型不提供的字段', '“字段对照”中标注类别不适用；例如EV的剩余容量、LMT／工业的SOCE、LMT／工业的容量耗尽阈值，均核对对应 SHACL maxCount=0。类别明细表只展开该类别Schema实际定义的字段。'),
        ('动态语言键', '多语言对象里的 en-IE 等动态键不是固定业务字段。“动态键格式”另列格式与语义要求，避免遗漏。'),
        ('引用解析', '按原始 $id 解析全部 $ref 与 allOf，保留引用的兄弟约束、实际 ZIP 文件路径和 JSON Pointer。'),
        ('SHACL核对方法', result['metrics']['method'] + '本次执行了JSON Schema样例校验，未进行实际注册或完整SHACL运行。'),
        ('保留依据', '中文解释与规则结论均附原始定义、scopeNote、Schema路径、SHACL形状与样例路径；右侧原文证据列可通过列分组展开。'),
        ('原文件保护', '此次只更新阅读版工作簿及生成脚本；细化前Excel与核验记录已存于同目录“历史备份”；官方ZIP、PDF和解压资源保持原样。'),
    ]
    issues = [
        ('必填与法规边界', 'Schema required、SHACL数量、词表业务适用性分别记录。词表有“Mandatory”的组合成员不能全部改成逐项必填；条件数据也不能因Schema未列required就视为永久可选。'),
        ('商业质保期', '固定单位为月，直接用非负整数；24表示24个月。Schema未设maximum；0虽通过数值下限校验，官方并未约定0表示无质保或终身质保。不适用时可省略。'),
        ('单位允许值', '重量SHACL允许kilogram或gram；容量允许ampereHour或milliampereHour；不能只按词表标题kg／Ah删除允许的另一单位。完整URI见具体字段。'),
        ('电压温度范围', '词表把温度范围称为optional，但minimalVoltage／nominalVoltage／maximumVoltage的具体Schema另有required=["valueRange"]，SHACL也要求范围出现。表内如实记录三者。'),
        ('耐受温度范围', '词表定义°C；对应SHACL只引用通用范围结构，未固定degreeCelsius。语义单位与校验器目前能检查的范围分别说明。'),
        ('自放电率', '词表定义每年下降百分比；SHACL单位URI为percent；JSON结构另带recordedTime。本阅读版保留%/年口径，不发明新URI。'),
        ('百分比范围', '不根据百分比单位默认增加0～100。每个具体字段实际最小值／最大值见Schema约束；未声明时明确写出。'),
        ('数组为空', '若Schema没有minItems，属性已提供也可能是空数组；某些SHACL仍要求至少一个RDF属性值。表内单独提示。'),
        ('SHACL子集', 'qualifiedMinCount和qualifiedMaxCount约束满足qualifiedValueShape的值子集；contentSpecificationIds中的核心、产品组、子类标识分别计数。'),
        ('多语言对象', 'Schema只要求至少一个xx-XX形式的键，没有逐值string声明；SHACL要求带语言标签的文本。RDF数量不是JSON语言键数量的直接替代。'),
        ('模型与批次', 'modelIdentifier与batchNumber在本版Schema成对依赖；另外可用serialNumber、productNumber、otherIdentificationElement中的一个分支满足组合。'),
        ('制造地点', 'manufacturingCountry为必填；manufacturingCity与manufacturingRegion至少一项。国家代码仅显式检查三位大写字母，不能把未随包提供的代码表写成Schema enum。'),
        ('工业条件', '可充电、循环应用、固定式储能、是否配BMS等条件来自scopeNote；静态Schema和SHACL未逐一编码所有判定条件，平台应另记录适用性。'),
        ('示例与允许列表', '示例列直接取自官方tests/conforms；数组元素行显示第一个元素样例。示例不是全部允许值，也不应被理解为推荐的真实业务参数。'),
        ('受控URI', '词表pattern检查URI形状；部分SHACL另检查SKOS词表成员，或固定hasValue。保留这两层，不把整个TTL表直接称为JSON Schema enum。'),
        ('UPI格式补充', '语义Schema中的uniqueProductIdentifier为string；10月30日注册OpenAPI另要求完整HTTPS URL。该说明属于接口指南补充，不冒充本字段Schema的format关键字。'),
        ('校验范围', '本次核对18份JSON Schema、18份SHACL形状文件、词表与官方样例；完整SHACL需结合JSON-LD转换、上下文和加载的词表执行。本表没有宣称已完成端到端注册测试。'),
    ]
    dynamic_rows = [
        ['multiLanguage.{languageTag}', '多语言文本的动态语言键', '不是固定的schema properties字段；每个语言键形如xx-XX，至少一个。',
         '语言键：^[A-Za-z]{2}-[A-Za-z]{2}$；minProperties=1。', 'Schema没有为动态键的值单独声明string；SHACL要求rdf:langString。',
         '{"en-IE":"Example text"}', 'core/1.0.0/schemas/common.schema.json#/$defs/multiLanguage；各字段SHACL语言文本与SEM-COM-MSG-012。'],
        ['histogram.bin[]', '直方图区间元素', '已在各类别字段及通用结构的[]行逐层展开；每个已存在元素要求lowerBound、upperBound、binValue。',
         'lowerBound／upperBound为number；binValue为number且minimum=0。', '上下界单位由binRangeUnit指定，统计值单位由binValueUnit指定。',
         '官方示例见各类别stateOfChargeHistogram或environmentalConditionHistogram。', 'core/1.0.0/schemas/common.schema.json#/$defs/histogram/properties/bin/items'],
    ]

    wb = Workbook()
    wb.remove(wb.active)
    table_index = 0

    def display_units(text):
        return sum(2 if ord(char) > 255 else 1 for char in str(text))

    def sheet(name, headers, data, widths, freeze='D2', evidence_start=None):
        nonlocal table_index
        table_index += 1
        ws = wb.create_sheet(name)
        ws.append(headers)
        for row in data:
            assert len(row) == len(headers), (name, len(row), len(headers))
            ws.append(row)
        ws.freeze_panes = freeze
        ws.sheet_view.zoomScale = 85
        ws.sheet_view.showGridLines = False
        for i, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
        for cell in ws[1]:
            cell.fill = PatternFill('solid', fgColor='193F50')
            if '单位' in str(cell.value):
                cell.fill = PatternFill('solid', fgColor='24736C')
            elif '必填' in str(cell.value) or '适用' in str(cell.value):
                cell.fill = PatternFill('solid', fgColor='345F8C')
            cell.font = Font(name='Noto Sans CJK SC', size=11, color='FFFFFF', bold=True)
            cell.alignment = Alignment(wrap_text=True, vertical='center')
        ws.row_dimensions[1].height = 48
        for cells in ws.iter_rows(min_row=2):
            line_count = 1
            for i, cell in enumerate(cells):
                assert cell.value is None or not isinstance(cell.value, str) or len(cell.value) <= 32767, (name, cell.coordinate)
                cell.alignment = Alignment(wrap_text=True, vertical='top')
                cell.font = Font(name='Noto Sans CJK SC', size=10, color='23343D')
                if cell.row % 2 == 0:
                    cell.fill = PatternFill('solid', fgColor='F1F6F8')
                if cell.value is not None and (evidence_start is None or i + 1 < evidence_start):
                    line_count = max(line_count, sum(max(1, math.ceil(display_units(line) / max(8, widths[i] - 4))) for line in str(cell.value).split('\n')))
                if '是否必填' in headers[i] or headers[i].endswith('必填结论'):
                    fill = 'FFF1CF' if any(x in str(cell.value) for x in ['条件', '适用', '未列', '组合']) else 'E1F1EC'
                    cell.fill = PatternFill('solid', fgColor=fill)
                if headers[i] == '核对结果／差异说明' and not str(cell.value).startswith('已核对'):
                    cell.fill = PatternFill('solid', fgColor='FFF4DB')
            ws.row_dimensions[cells[0].row].height = min(409, max(42, 15 * line_count + 12))
        tab = Table(displayName=f'SemanticDetail{table_index}', ref=ws.dimensions)
        tab.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=False)
        ws.add_table(tab)
        ws.auto_filter.ref = ws.dimensions
        if evidence_start:
            ws.column_dimensions.group(get_column_letter(evidence_start), get_column_letter(len(headers)), hidden=True)
            ws.sheet_properties.outlinePr.summaryRight = False
            # Each row retains the complete evidence; a comment explains how to expose it.
            ws.cell(1, evidence_start - 1).comment = Comment('右侧为英文原文、Schema与SHACL来源及校验消息代码，已按列分组收起；点击上方“+”可展开。', 'Codex')
        ws.print_title_rows = '1:1'
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.paperSize = ws.PAPERSIZE_A3
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.oddFooter.center.text = '欧委会2026-10-30语义资源 · 内部字段字典细化版 · &P / &N'
        return ws

    intro = sheet('阅读说明', ['项目', '说明'], overview, [27, 125], 'B2')
    for cells in intro.iter_rows(min_row=2):
        if str(cells[1].value).startswith('https://'):
            cells[1].hyperlink = str(cells[1].value)
            cells[1].font = Font(name='Noto Sans CJK SC', size=10, color='1A678D', underline='single')
    compare_headers = ['访问视图', '顶层字段路径', '中文含义（内部解释）', '计量单位／统计口径', 'JSON类型',
                       'EV必填结论', 'LMT必填结论', '工业必填结论',
                       'EV业务适用条件', 'LMT业务适用条件', '工业业务适用条件',
                       '填写格式与说明', '官方样例值', '核对结果／差异摘要']
    sheet('字段对照', compare_headers, result['compare'], [13, 42, 28, 32, 13, 29, 29, 34, 60, 60, 70, 85, 80, 110])
    for subgroup, label in profiles.items():
        sheet(label + '字段', DISPLAY_HEADERS, display_rows(result['profiles'][subgroup]), DISPLAY_WIDTHS, evidence_start=18)
    sheet('通用结构', DISPLAY_HEADERS, display_rows(result['common']), DISPLAY_WIDTHS, evidence_start=18)
    sheet('单位与格式速查', ['适用类别', '访问视图', '字段路径', '中文含义', '计量单位／统计口径', '单位提交方式及依据', 'JSON类型', '填写格式（中文）', 'Schema边界／格式', '官方样例值', '来源依据'],
          result['units'], [20, 20, 65, 34, 47, 88, 20, 92, 70, 90, 110], evidence_start=11)
    sheet('必填与规则核对', ['电池类别', '访问视图', '字段路径', '中文含义', '核对类型', '综合结论', 'JSON Schema必填状态', 'SHACL数量要求', '业务适用条件', '核对说明／处理口径', 'Schema来源', 'SHACL与词表依据'],
          result['differences'], [17, 20, 70, 32, 42, 42, 47, 78, 80, 105, 110, 115], evidence_start=11)
    vocab_rows = [[c['file'].name, c['code'], c['label'], c['alt'], c['uri'], c['definition'], c['scope'], str(c['file'].relative_to(assets))] for c in context['VOCABS']]
    sheet('词表参考', ['词表文件', '原始代码', '英文名称', '别名／符号', '完整概念URI', '英文定义', 'scopeNote（原文）', '源文件'], vocab_rows, [34, 35, 55, 44, 105, 120, 130, 88], 'C2')
    sheet('组合与结构约束', ['Schema源文件', 'JSON Pointer', '关键字', '原文约束JSON', '解读边界'], context['constraint_rows'], [90, 95, 27, 135, 85], 'C2')
    sheet('SHACL规则索引', ['SHACL源文件', '完整形状URI', '形状约束原文（RDF摘录）', '读取边界'], result['shacl'], [100, 120, 155, 90], 'C2')
    sheet('动态键格式', ['结构／路径', '含义', '必填条件', 'Schema格式／范围', 'SHACL或单位口径', '示意／例子', '来源'], dynamic_rows, [48, 35, 90, 100, 105, 100, 120], 'C2')
    schema_rows = [[str(p.relative_to(assets)), doc['$id'], doc.get('title', ''), doc['$schema'], len(doc.get('properties', {})), context['dumps'](doc.get('required', [])), '\n'.join(v.get('$ref', '') for v in doc.get('allOf', [])), context['BEFORE'][p]] for p, doc in context['DOCS'].values()]
    sheet('Schema文件索引', ['实际源文件路径', '原始$id', '标题', 'JSON Schema版本', '直接属性数', '根required', '直接allOf引用', 'SHA256'], schema_rows, [100, 120, 110, 75, 20, 130, 120, 80], 'B2')
    sheet('注意事项', ['主题', '说明'], issues, [33, 140], 'B2')
    sheet('样例校验', ['电池类型', '访问视图', '官方样例文件', '本地JSON Schema校验', '问题详情'], context['SAMPLE_RESULTS'], [18, 40, 110, 43, 135], 'C2')
    # Internal links allow the compact comparison table to jump to the expanded field.
    locations = {(s, row[0], row[1]): i for s, rs in result['profiles'].items() for i, row in enumerate(rs, 2)}
    for i, row in enumerate(result['compare'], 2):
        s = next(s for s in profiles if (s, row[0], row[1]) in locations)
        cell = wb['字段对照'].cell(i, 2)
        cell.hyperlink = f"#'{profiles[s]}字段'!B{locations[s, row[0], row[1]]}"
        cell.font = Font(name='Noto Sans CJK SC', size=10, color='1A678D', underline='single')
    wb.properties.title = '欧委会电池护照 JSON Schema 字段字典（单位与必填细化版 v1.1）'
    wb.properties.creator = 'Codex'
    wb.properties.description = '基于固定2026-10-30官方资源；逐字段核对JSON Schema、SHACL、单位、业务适用条件及官方样例；内部阅读版。'
    output = out / '欧委会电池护照_JSONSchema字段字典_20261030.xlsx'
    staged = out / '.字段字典_细化版_待校验.xlsx'
    wb.save(staged)
    check = load_workbook(staged, data_only=False)
    for subgroup, label in profiles.items():
        assert check[label + '字段'].max_row == len(rows[subgroup]) + 1
        data = {(r[0], r[1]): dict(zip(DISPLAY_HEADERS, r)) for r in check[label + '字段'].iter_rows(min_row=2, values_only=True)}
        warranty = data[views['pub'], '$.commercialWarrantyPeriod']
        assert warranty[HEADERS[5]] == '月'
        assert warranty[HEADERS[7]] == 'integer'
        assert 'minimum=0' in warranty[HEADERS[9]]
        assert '未列' in warranty[HEADERS[10]]
        assert 'minCount=0' in warranty[HEADERS[11]] and 'maxCount=1' in warranty[HEADERS[11]]
        assert '有商业质保时适用' in warranty[HEADERS[4]]
        assert 'kilogram' in data[views['pub'], '$.batteryWeight'][HEADERS[6]]
        assert 'gram' in data[views['pub'], '$.batteryWeight'][HEADERS[6]]
        assert 'milliampereHour' in data[views['pub'], '$.batteryCapacity'][HEADERS[6]]
        assert 'volt' in data[views['pub'], '$.minimalVoltage.unit'][HEADERS[6]]
        assert 'degreeCelsius' in data[views['pub'], '$.minimalVoltage.valueRange.unit'][HEADERS[6]]
        assert '型号' in data[views['pub'], '$.batteryIdentification.modelIdentifier'][HEADERS[4]]
        assert all(r[HEADERS[4]] and r[HEADERS[5]] and r[HEADERS[6]] and r[HEADERS[8]] and r[HEADERS[10]] and r[HEADERS[11]] for r in data.values())
    assert all(len(ws.tables) == 1 and ws.freeze_panes and ws.auto_filter.ref for ws in check)
    assert not any(ws.merged_cells.ranges for ws in check)
    assert len(context['raw_locations']) == len(context['raw_locations'] & context['PROPERTY_LOCATIONS'])
    assert len(context['SAMPLE_RESULTS']) == 12 and all(r[3] == '通过' for r in context['SAMPLE_RESULTS'])
    assert not any(cell.data_type == 'f' for ws in check for row in ws for cell in row)
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in context['BEFORE'].items())
    backup_dir = max((out / '历史备份').glob('细化前_*'), key=lambda p: p.name)
    original_manifest = json.loads((backup_dir / '官方资源SHA256.json').read_text())
    assert all(hashlib.sha256((root / p).read_bytes()).hexdigest() == digest for p, digest in original_manifest.items())
    check.close()
    staged.replace(output)
    report = {
        'workbook': output.name, 'revision': '1.1', 'generatedAt': now,
        'officialExcel': False, 'sourcePublicationDate': '2026-10-30',
        'sourceSchemaCount': len(context['DOCS']), 'allSourcePropertyDefinitionsCovered': len(context['raw_locations']),
        'sourceReferencesResolved': len(context['raw_refs']),
        'profileFieldRows': {profiles[s]: len(rs) for s, rs in rows.items()},
        'topLevelViewFieldCombinations': len(result['compare']),
        'vocabularyConcepts': len(context['VOCABS']), **result['metrics'],
        'exampleChecks': {'total': 12, 'passed': 12, 'withIssues': 0, 'formatCheckerEnabled': True},
        'allProfileRowsHaveUnitFormatAndRequiredExplanation': True,
        'unitAndRequiredSpotChecks': ['三类别commercialWarrantyPeriod月/integer/minimum=0/非required/SHACL0..1/商业质保适用', '重量kg或g', '容量Ah或mAh', '电压主值V与温度范围°C分开', '组合必填条件'],
        'originalFilesUnmodified': True, 'officialFilesSha256Checked': len(original_manifest),
        'backup': str(backup_dir.relative_to(root)), 'worksheets': wb.sheetnames,
        'outputSha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'generator': [str(p.relative_to(root)) for p in [root/'build_schema_excel.py', root/'schema_field_details.py', root/'schema_excel_output.py']],
    }
    report_path = out / '字段字典_生成与核验.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (out / '字段字典_复现说明.txt').write_text(
        '生成入口：../build_schema_excel.py\n'
        '新增细化模块：../schema_field_details.py、../schema_excel_output.py\n'
        '依赖：openpyxl、jsonschema、referencing、rdflib（本次使用7.4.0）。\n'
        '执行：在依赖齐备的Python环境运行 python build_schema_excel.py。\n'
        '输入为固定的03_语义资源目录；不联网修改官方文件。\n'
        '输出文件仍使用原名；细化前版本在历史备份目录。\n'
        '完整核验结果见 字段字典_生成与核验.json。\n'
    )
    assert all(p.stat().st_uid == 1000 and p.stat().st_gid == 1000 for p in [output, out, report_path])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report
