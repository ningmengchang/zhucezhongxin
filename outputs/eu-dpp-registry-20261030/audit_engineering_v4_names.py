"""Audit all engineering names against the immutable semantic snapshot.

Generate a separate reviewed workbook; retain the previously delivered file.
"""
from collections import Counter, defaultdict
from copy import copy
from datetime import datetime
import csv
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from build_engineering_v4 import (
    ROOT, OUT, URL, Source, Builder, build_workbook, field_name,
    make_sheet, MAIN_HEADERS, MAIN_WIDTHS, join_unique, sha,
)

SEMANTIC = ROOT / '03_语义资源'
OUTPUT = OUT / '电池护照_工程化字段表_v4.0_字段来源核对版.xlsx'
AUDIT_TITLE = '字段来源核对'


class Registry:
    def __init__(self):
        self.schemas = {}
        self.properties = defaultdict(list)
        self.notations = defaultdict(list)
        for path in sorted(SEMANTIC.rglob('*.schema.json')):
            rel = str(path.relative_to(SEMANTIC))
            obj = json.loads(path.read_text())
            self.schemas[rel] = obj
            self.walk(obj, rel, '')
        for path in sorted(SEMANTIC.rglob('*.ttl')):
            for name in re.findall(r'skos:notation\s+"([^"]+)"', path.read_text()):
                self.notations[name].append(str(path.relative_to(SEMANTIC)))
        assert len(self.schemas) == 18

    def walk(self, node, file, pointer):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == 'properties':
                    for name in value:
                        token = name.replace('~', '~0').replace('/', '~1')
                        self.properties[name].append(file + '#' + pointer + '/properties/' + token)
                token = key.replace('~', '~0').replace('/', '~1')
                self.walk(value, file, pointer + '/' + token)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                self.walk(value, file, pointer + '/' + str(index))

    def resolve(self, location):
        file, pointer = location.split('#', 1)
        node = self.schemas[file]
        for part in pointer.lstrip('/').split('/') if pointer else []:
            part = part.replace('~1', '/').replace('~0', '~')
            node = node[int(part)] if isinstance(node, list) else node[part]
        return node

    def evidence(self, entry, context, business):
        locations = list(dict.fromkeys([
            *context['node'].get('x-source-locations', []),
            *entry['node'].get('x-source-locations', []),
        ]))
        for loc in locations:
            self.resolve(loc)
        # Record a literal property definition even for array-item contexts.
        exact = [loc for loc in locations if '/properties/' + business in loc]
        assert business in self.properties, ('not an official property', business)
        return join_unique([*exact, *(locations if exact else self.properties[business])])


def normalize(b, registry):
    legacy_by_row = {r['row']: r for r in b.source.legacy}
    audits = []
    for r in b.records:
        old_code = r['code']
        old = legacy_by_row.get(r['oldrow'])
        source_code = old['old_code'] if old else ''
        generated = not source_code or source_code != old_code
        notes = []
        source = '—（本快照未确认官方对应）'
        evidence = ''
        if r['entry']:
            context = b.source.context(r['entry'])
            business = field_name(context['path'])
            source = business
            evidence = registry.evidence(r['entry'], context, business)
            is_range = r['path'].endswith('.valueRange')
            if is_range:
                notes.append('对应官方' + business + '的valueRange子对象；以路径表示，不能作为新的顶层JSON键。')
            if is_range and not old:
                code = business + '.valueRange'
                category = '结构拆分命名'
                origin = '本稿按官方结构拆分'
                decision = '保留为路径表达；不是独立顶层语义字段'
            else:
                code = business + '.valueRange' if is_range else business
                category = '别名统一官方名称' if old_code != code else '名称与官方一致'
                decision = '已改用官方名称' if old_code != code else '名称与官方定义一致'
                if old:
                    if not source_code:
                        origin = '本稿补名，采用官方名称'
                    elif old_code != code:
                        origin = '3.4原有别名，现统一官方名称'
                    else:
                        origin = '3.4名称与官方一致'
                else:
                    origin = '本稿工程别名，现统一官方名称' if old_code != code else '官方语义名称'
                if r['path'].endswith('.value'):
                    notes.append('工程行表示官方计量对象的value；unit仍按官方结构输出。')
                if old_code != code:
                    notes.append('修订前工程名' + old_code + '统一为官方名称' + code + '；旧名仅作历史追溯。')
            r['code'] = code
            if old_code != code:
                r['remarks'] = r['remarks'].replace('保留工程编码' + old_code + '；',
                    '3.4工程编码' + old_code + '现统一为' + code + '；')
                r['remarks'] = r['remarks'].replace('保留旧工程编码，',
                    '修订前沿用旧工程编码，现统一为' + code + '，')
                r['chinese'] = r['chinese'].replace('保留工程编码' + old_code + '；',
                    '3.4工程编码' + old_code + '现统一为' + code + '；')
                r['chinese'] = r['chinese'].replace('保留旧工程编码，',
                    '修订前沿用旧工程编码，现统一为' + code + '，')
            if old:
                old['code'] = code
        elif r['required'] == 'N/A':
            code = old_code
            business = r['path'][2:].split('.')[0]
            assert business in registry.properties
            source = business
            evidence = join_unique(registry.properties[business])
            category = '本类别不适用'
            origin = '3.4原有命名；本类别不适用'
            decision = '字段存在，但当前电池类别不定义该路径'
            notes.append('不得把其他电池类别中的同名字段填入本类别模型。')
        else:
            code = old_code
            assert code not in registry.properties, ('unreviewed exact match', code)
            assert code not in registry.notations, ('unreviewed vocabulary match', code)
            category = '未确认语义对应'
            decision = '保留旧工程项；候选关系不是已确认映射'
            if not source_code:
                origin = '本稿拟名，待确认'
                notes.append('3.4只有业务名称，没有机器字段名；该camelCase名称由本稿拟定，不是官方语义字段。')
            elif source_code != code:
                origin = '本稿更名，保留3.4业务项'
                notes.append('3.4原名' + source_code + '由本稿更名为' + code + '，仍属旧业务扩展，不能当作官方字段。')
            else:
                origin = '3.4原有工程名；未确认语义对应'
                notes.append('名称来自3.4业务表；在本地语义快照的JSON属性和SKOS notation中未找到同名定义。')
            if code == 'internalDppStatus':
                notes.append('旧五种生命周期状态与官方dppStatus的有效／无效不是同一含义。')
            if r['path'].startswith('候选：'):
                notes.append(r['path'] + '；需要人工确认转换规则，不能直接重命名或无损替换。')
        if old_code == 'ratedCapacity' and r['path'] == '$.batteryCapacity.value':
            notes.append('重要：旧工程名与RES3官方ratedCapacity发生同名异义；此公开行改为batteryCapacity，RES3行改用ratedCapacity。')
        if old_code == 'stateOfHealthRatedCapacity':
            notes.append('stateOfHealthRatedCapacity是本稿为避免旧命名冲突拟定的别名；现采用RES3官方名ratedCapacity。')
        r['remarks'] = join_unique([r['remarks'], *notes])
        audits.append(dict(
            id=r['id'], row=r['excel_row'], previous=old_code, current=code,
            title=r['title'], groups=' / '.join(r['groups']), official=source,
            origin=origin, category=category, conclusion=decision,
            original_code=source_code or '—（3.4未提供）', original_row=r['oldrow'],
            previous_is_official=old_code in registry.properties,
            previous_is_notation=old_code in registry.notations,
            generated=generated, path=r['path'], evidence=evidence,
            notes=join_unique(notes), semantic_rule_changed=False,
        ))
    return audits


def add_audit_columns(wb, b, audits):
    old = wb['工程化v4.0']
    rows = []
    headers = MAIN_HEADERS[:5] + ['官方语义字段', '命名来源', '语义对应状态', '修订前工程名'] + MAIN_HEADERS[5:] + ['官方定义出处', '3.4源行']
    widths = MAIN_WIDTHS[:5] + [36, 32, 38, 40] + MAIN_WIDTHS[5:] + [105, 13]
    for a in audits:
        values = [c.value for c in old[a['row']]]
        rows.append(values[:5] + [a['official'], a['origin'], a['category'], a['previous']] + values[5:] + [a['evidence'], a['original_row']])
    wb.remove(old)
    main = make_sheet(wb, '工程化v4.0', headers, rows, widths, 'EngineeringV40', 'F2')
    wb._sheets.remove(main)
    wb._sheets.insert(0, main)
    def translate(col):
        return col if col <= 5 else col + 4
    for row in old:
        for src in row:
            dst = main.cell(src.row, translate(src.column))
            dst._style = copy(src._style)
            dst.comment = copy(src.comment)
            if src.hyperlink:
                link = copy(src.hyperlink)
                link.ref = dst.coordinate
                dst._hyperlink = link
    for i in old.row_dimensions:
        main.row_dimensions[i].height = old.row_dimensions[i].height
    main.column_dimensions['I'].hidden = True
    main.column_dimensions.group('V', 'AC', hidden=True)
    main['A1'].comment = Comment('字段来源核对版。F列为官方语义业务字段，G/H列说明名称来源与对应关系；旧工程名在I列。点G列跳转完整核对记录。保留原199行及121条旧业务来源。', '使用说明')
    main['F1'].comment = Comment('计量行列出所属业务字段名称，如batteryCapacity；实际成员路径$.batteryCapacity.value见T列。*.valueRange为官方结构子项，不是额外顶层字段。未确认对应时不填官方字段名。', '核对说明')
    main['G1'].comment = Comment('明确区分3.4已有名称、本稿补名、本稿更名及官方语义名称；点击数据行可进入字段来源核对。', '核对说明')
    main['I1'].comment = Comment('本次来源核对之前的工程化字段名称，供追溯；本列默认隐藏。', '核对说明')
    for a in audits:
        i = a['row']
        cell = main.cell(i, 7)
        cell.hyperlink = f"#'{AUDIT_TITLE}'!A{i}"
        cell.font = Font(name='Noto Sans CJK SC', size=10, color='176E91', underline='single')
        main.cell(i, 5).comment = Comment(join_unique([a['origin'], a['conclusion'], a['notes'], a['evidence']]), '字段来源核对')
        color = {'未确认语义对应':'FFF1D4', '别名统一官方名称':'DEF1EE', '结构拆分命名':'E2EFFA', '本类别不适用':'E8E8E8'}.get(a['category'])
        if color:
            for col in [5, 6, 8]:
                main.cell(i, col).fill = PatternFill('solid', fgColor=color)
    for col, options in [('K', 'M,C,V,N/A,待确认'), ('Q', 'Static,Dynamic,待确认')]:
        dv = DataValidation(type='list', formula1='"' + options + '"')
        main.add_data_validation(dv)
        dv.add(col + '2:' + col + str(main.max_row))
    return main


def add_audit_sheet(wb, audits):
    headers = ['字段ID','数据项名称','修订前工程名','核对后工程名','官方语义字段','核对分类','命名来源','电池分类',
               '3.4原机器字段名','3.4源行','原名是否为官方JSON属性','原名是否为SKOS notation','是否本稿补名或更名',
               '官方JSON路径／候选','官方定义出处','结论与处理','核对说明','主表位置']
    rows = [[a['id'],a['title'],a['previous'],a['current'],a['official'],a['category'],a['origin'],a['groups'],
             a['original_code'],a['original_row'],'是' if a['previous_is_official'] else '否',
             '是' if a['previous_is_notation'] else '否','是' if a['generated'] else '否',
             a['path'],a['evidence'],a['conclusion'],a['notes'],'第'+str(a['row'])+'行'] for a in audits]
    widths = [12,28,40,40,36,26,38,18,45,13,22,22,24,75,110,65,110,18]
    ws = make_sheet(wb, AUDIT_TITLE, headers, rows, widths, 'EngineeringNameAuditV40', 'E2')
    ws['A1'].comment = Comment('逐行核对199行。官方名称依据03_语义资源的18个原始JSON Schema，并交叉检索TTL的SKOS notation。未匹配只代表当前本地快照未确认，不能据此删除旧业务项。', '核对范围')
    for a in audits:
        cell = ws.cell(a['row'],18)
        cell.hyperlink = f"#'工程化v4.0'!E{a['row']}"
        cell.font = Font(name='Noto Sans CJK SC',size=10,color='176E91',underline='single')
        if a['category'] == '未确认语义对应':
            for col in [4,5,6,7]:
                ws.cell(a['row'],col).fill = PatternFill('solid',fgColor='FFF1D4')
    return ws


def verify(output, audits, b, source_wb, registry):
    wb = load_workbook(output)
    assert wb.sheetnames == ['工程化v4.0','枚举字典','规则与映射','v3.4变更对照',AUDIT_TITLE]
    assert wb['工程化v4.0'].max_row == 200 and wb[AUDIT_TITLE].max_row == 200
    assert {a['original_row'] for a in audits if a['original_row']} == set(range(2,123))
    assert len(b.rule_ids) == 642
    main = wb['工程化v4.0']
    original = source_wb['工程化v4.0']
    name_keys = set()
    # All classification, obligation, permissions, formats, units, and enum
    # values retain the previously delivered rules; only names/provenance change.
    for a in audits:
        for group in a['groups'].split(' / '):
            key = (a['current'], group)
            assert key not in name_keys, ('duplicate name for battery category', key)
            name_keys.add(key)
        for col in range(6,14):
            assert main.cell(a['row'],col+4).value == original.cell(a['row'],col).value, (a['id'],col)
        assert main.cell(a['row'],20).value == a['path']
        assert main.cell(a['row'],5).value == a['current']
    for sheet in wb:
        assert sheet.auto_filter.ref and sheet.freeze_panes
        for row in sheet:
            for c in row:
                assert c.data_type not in {'f','e'}
                assert len(str(c.value or '')) <= 32767
    for code, path in [('batteryCapacity','$.batteryCapacity.value'),('ratedCapacity','$.ratedCapacity.value'),
                       ('lastUpdate','$.lastUpdate'),('hazardousSubstances','$.hazardousSubstances')]:
        assert any(a['current'] == code and a['path'] == path for a in audits)
    assert not any(a['current'] in {'stateOfHealthRatedCapacity','lastUpdated','hazardousSubstanceNames'} for a in audits)
    assert all(a['official'] in registry.properties for a in audits if a['category'] != '未确认语义对应')
    return wb


def main():
    original_path = OUT / '电池护照_工程化字段表_v4.0_评审稿.xlsx'
    original_sha = sha(original_path)
    original = load_workbook(original_path)
    source = Source()
    b = Builder(source)
    b.build_records()
    assert [(r['id'], r['code']) for r in b.records] == [(original['工程化v4.0'].cell(i,1).value,original['工程化v4.0'].cell(i,5).value) for i in range(2,201)]
    registry = Registry()
    audits = normalize(b, registry)
    wb, metrics = build_workbook(b)
    add_audit_columns(wb, b, audits)
    add_audit_sheet(wb, audits)
    # Update the existing guidance so the newly visible source columns agree.
    rules = wb['规则与映射']
    for row in rules:
        if row[2].value == '版本':
            row[7].value = 'v4.0字段来源核对版；' + datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    wb.properties.title = '电池护照工程化字段表 v4.0 字段来源核对版'
    wb.properties.description = '199行名称逐一核对；官方名称与旧业务扩展分列，保留3.4的121条来源记录和642个语义结构节点。'
    wb.active = 0
    wb.save(OUTPUT)
    verify(OUTPUT, audits, b, original, registry)
    categories = Counter(a['category'] for a in audits)
    unique = {k:len({a['previous'] for a in audits if a['category']==k}) for k in categories}
    report = dict(version='4.0-name-audit.1',referenceUrl=URL,sourceSemanticVersion='2026-10-30',
        mainRows=len(audits),uniquePreviousNames=len({a['previous'] for a in audits}),
        categoryRows=dict(categories),categoryDistinctPreviousNames=unique,
        originalBusinessRowsPreserved=121,semanticPathsPreserved=len(b.rule_ids),
        rawSchemaFilesChecked=len(registry.schemas),officialPropertyNames=len(registry.properties),
        skosNotationsChecked=len(registry.notations),
        inputSha256=original_sha,previousFilePreserved=sha(original_path)==original_sha,
        outputSha256=sha(OUTPUT),verification='passed',
        semanticConstraintsChanged=False,onlineOriginalModified=False,
        sourceSha256={file:sha(SEMANTIC/file) for file in registry.schemas})
    (OUT/'字段来源核对结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (OUT/'字段来源核对明细.json').write_text(json.dumps(audits,ensure_ascii=False,indent=2)+'\n')
    with (OUT/'字段来源核对明细.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.writer(file)
        writer.writerow(['字段ID','数据项名称','修订前工程名','核对后工程名','官方语义字段','分类','命名来源','电池分类','3.4原名','3.4源行','官方路径或候选','核对说明','官方定义出处'])
        for a in audits:
            writer.writerow([a[k] for k in ['id','title','previous','current','official','category','origin','groups','original_code','original_row','path','notes','evidence']])
    (OUT/'字段来源核对说明.txt').write_text(
        '请打开 电池护照_工程化字段表_v4.0_字段来源核对版.xlsx。\n'
        '原评审稿保留。主表F=官方语义字段，G=命名来源（可点击），H=对应状态，I=修订前工程名（默认隐藏），T=实际JSON路径。\n'
        '新增“字段来源核对”sheet，逐行提供名称来源、原表机器名和官方定义出处。\n'
        f'核对199行、171种修订前工程名：{unique.get("别名统一官方名称",0)}种别名统一官方名，{unique.get("结构拆分命名",0)}种结构拆分名改为路径表达，{unique.get("未确认语义对应",0)}种旧工程项未确认语义对应。\n'
        '公开batteryCapacity与RES3 ratedCapacity分别使用官方名称，不再使用旧表ratedCapacity指代公开字段。\n'
        '未确认项保留业务需求，但不能当作官方JSON字段；只提供候选关系的项目没有强行重命名。\n'
        '核对范围为本地2026-10-30快照的18个原始JSON Schema及TTL的SKOS notation；缺少同名定义不等于该业务项可以删除。\n'
        '本次核对不改变必填、权限、数据类型、单位及枚举规则。\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='sourceSha256'},ensure_ascii=False,indent=2))
    print(OUTPUT)


if __name__ == '__main__':
    main()
