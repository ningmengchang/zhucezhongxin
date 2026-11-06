"""Apply the supplied Word template without changing interface fields or examples.

The template supplies the cover, margins, styles, table/cell properties and fonts.
The saved pre-formatting document supplies all interface content. No invisible
wrapping characters are inserted into domains, JSON or field names.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree as X

HERE = Path(__file__).resolve().parent
STEM = '供应商SN对接接口说明_EV全量语义_24位国标码_v1.0'
CONFIG = json.loads((HERE / '格式调整输入.json').read_text())
SOURCE = Path(CONFIG['backup_dir']) / (STEM + '.docx')
TEMPLATE = Path(CONFIG['template'])
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
INVISIBLE = '\u200b\ufeff\u2060\u00ad'


def tag(name):
    return '{' + W + '}' + name


def node(name, **attrs):
    return X.Element(tag(name), {tag(k): str(v) for k, v in attrs.items()})


def read_doc(path):
    with zipfile.ZipFile(path) as z:
        return X.fromstring(z.read('word/document.xml'))


def txt(element):
    return ''.join(element.xpath('.//w:t/text()', namespaces=NS))


def clean(value):
    return str(value).translate({ord(c): None for c in INVISIBLE})


def canonical(value):
    return re.sub(r'\s+', '', clean(value)).replace('➢', '')


def replace(parent, name, new):
    old = parent.find(tag(name))
    if old is not None:
        parent.remove(old)
    if new is not None:
        parent.insert(0, copy.deepcopy(new))


def prop(parent, name, **attrs):
    old = parent.find(tag(name))
    if old is not None:
        parent.remove(old)
    result = node(name, **attrs)
    parent.append(result)
    return result


REF = read_doc(TEMPLATE)
OLD = read_doc(SOURCE)
REF_BODY = REF.find(tag('body'))
OLD_BODY = OLD.find(tag('body'))
REF_TABLES = REF_BODY.findall(tag('tbl'))
REF_PARAS = REF_BODY.findall(tag('p'))
NORMAL_REF = next(p for p in REF_PARAS if txt(p).startswith('本文档用于说明'))
LABEL_REF = next(p for p in REF_PARAS if txt(p) == '➢  接口用途：')
FRONT_REF = next(p for p in REF_PARAS if txt(p) == '文档修订记录')
RULE_REF = next(p for p in REF_PARAS if p.find('./w:pPr/w:pBdr', NS) is not None)
CELL_HEADER = REF_TABLES[2].find('./w:tr/w:tc', NS)
CELL_BODY = REF_TABLES[2].findall('./w:tr', NS)[1].find(tag('tc'))
FIELD_HEADER = REF_TABLES[12].find('./w:tr/w:tc', NS)
FIELD_BODY = REF_TABLES[12].findall('./w:tr', NS)[1].find(tag('tc'))
CODE_REF = REF_TABLES[6]
CODE_CELL = CODE_REF.find('./w:tr/w:tc', NS)
BODY_SECTION = REF_BODY.find(tag('sectPr'))
HEAD_REFS = {str(level): next(p for p in REF_PARAS if p.find('./w:pPr/w:pStyle', NS) is not None
                             and p.find('./w:pPr/w:pStyle', NS).get(tag('val')) == str(level + 2))
             for level in (1, 2, 3)}
TOC_REFS = {1: next(p for p in REF_PARAS if txt(p) == '1.  前言描述4'),
            2: next(p for p in REF_PARAS if txt(p) == '2.1  鉴权接口设计5'),
            3: next(p for p in REF_PARAS if txt(p) == '2.1.1  请求参数及示例5')}


def style_id(p):
    style = p.find('./w:pPr/w:pStyle', NS)
    return style.get(tag('val')) if style is not None else ''


def run_format(p, size=11, bold=None, base=None):
    for r in p.findall('.//w:r', NS):
        previous = r.find(tag('rPr'))
        was_bold = previous.find(tag('b')) if previous is not None else None
        if base is not None:
            rp = copy.deepcopy(base)
        else:
            rp = copy.deepcopy(NORMAL_REF.find('./w:r/w:rPr', NS))
        for name in ['rFonts', 'color', 'sz', 'szCs', 'spacing', 'w', 'fitText', 'rStyle']:
            for old in rp.findall(tag(name)):
                rp.remove(old)
        rp.insert(0, node('rFonts', ascii='微软雅黑', hAnsi='微软雅黑', eastAsia='微软雅黑', cs='微软雅黑'))
        rp.append(node('color', val='000000'))
        rp.append(node('sz', val=round(size * 2)))
        rp.append(node('szCs', val=round(size * 2)))
        for old in rp.findall(tag('b')):
            rp.remove(old)
        if bold is not None:
            rp.append(node('b', val=1 if bold else 0))
        elif was_bold is not None:
            rp.append(copy.deepcopy(was_bold))
        replace(r, 'rPr', rp)
        for t in r.findall(tag('t')):
            t.text = clean(t.text or '')
    pp = p.find(tag('pPr'))
    if pp is not None:
        prop(pp, 'autoSpaceDE', val=0)
        prop(pp, 'autoSpaceDN', val=0)


def format_paragraph(p):
    old_pp = p.find(tag('pPr'))
    original_style = style_id(p)
    text = clean(txt(p))
    page_break = old_pp is not None and old_pp.find(tag('pageBreakBefore')) is not None
    section = old_pp.find(tag('sectPr')) if old_pp is not None else None
    if section is not None:
        replace(p, 'pPr', node('pPr'))
        p.find(tag('pPr')).append(copy.deepcopy(BODY_SECTION))
        return p
    if original_style.startswith('TOC'):
        level = int(original_style[-1])
        pp = copy.deepcopy(TOC_REFS[level].find(tag('pPr')))
        prop(pp, 'pStyle', val='SnTOC' + str(level))
        replace(p, 'pPr', pp)
        run_format(p, 11 if level == 1 else 10.5, bold=level == 1)
        return p
    if text in ('文档修订记录', '目    录'):
        pp = copy.deepcopy(FRONT_REF.find(tag('pPr')))
        prop(pp, 'pStyle', val='SnFrontHeading')
        if page_break:
            pp.append(node('pageBreakBefore'))
        replace(p, 'pPr', pp)
        run_format(p, 22, bold=True)
        return p
    if original_style.startswith('Heading'):
        level = original_style[-1]
        pp = copy.deepcopy(HEAD_REFS[level].find(tag('pPr')))
        if re.match(r'E\d\d\s*·', text):
            prop(pp, 'pStyle', val='SnEnumHeading')
            prop(pp, 'outlineLvl', val=9)
            prop(pp, 'keepNext')
            prop(pp, 'keepLines')
        if page_break:
            pp.append(node('pageBreakBefore'))
        replace(p, 'pPr', pp)
        run_format(p, {'1': 18, '2': 14, '3': 12}[level], bold=True)
        return p
    pp = copy.deepcopy(NORMAL_REF.find(tag('pPr')))
    if page_break:
        pp.append(node('pageBreakBefore'))
    if not text:
        prop(pp, 'spacing', before=0, after=0, line=40, lineRule='exact')
        prop(pp, 'ind', left=0, firstLine=0)
    elif text.startswith('（在 Word/WPS'):
        prop(pp, 'spacing', before=100, after=0, line=240, lineRule='auto')
        prop(pp, 'ind', left=0, firstLine=0)
    elif re.match(r'EV字段明细（续）', text):
        prop(pp, 'ind', left=0, firstLine=0)
        prop(pp, 'keepNext')
        prop(pp, 'spacing', before=100, after=60, line=240, lineRule='auto')
    if re.search(r'[A-Za-z]{4,}', text) or 'SHA-256' in text or text.startswith('适用字段：'):
        prop(pp, 'jc', val='left')
        if 'http' in text or 'SHA-256' in text or text.startswith('适用字段：'):
            prop(pp, 'ind', left=0, firstLine=0)
    if text.startswith('适用字段：'):
        prop(pp, 'keepNext')
    replace(p, 'pPr', pp)
    run_format(p, 9 if text.startswith(('参考文档：', '国家标准原文公开系统：', '第1号修改单：', '指定源文件SHA-256：', '适用字段：')) else 11)
    return p


def label(text):
    p = copy.deepcopy(LABEL_REF)
    for child in list(p):
        if child.tag != tag('pPr'):
            p.remove(child)
    r = node('r'); t = node('t'); t.text = text; r.append(t); p.append(r)
    run_format(p, 11, bold=True)
    return p


def format_table(table, is_revision=False):
    rows = table.findall(tag('tr'))
    cells = rows[0].findall(tag('tc'))
    titles = [clean(txt(c)) for c in cells]
    field = len(cells) == 6 and titles[0] == '序号' and titles[1].startswith('字段名称')
    count = len(cells)
    pr = copy.deepcopy(REF_TABLES[12 if field else 2].find(tag('tblPr')))
    prop(pr, 'tblW', w=9524, type='dxa')
    old_grid = table.find(tag('tblGrid'))
    if field:
        widths = [520, 2200, 1000, 800, 1120, 3884]
    elif count == 4 and titles[1] == '完整取值':
        widths = [600, 5500, 1900, 1524]
    else:
        widths = [int(x.get(tag('w'))) for x in old_grid] if old_grid is not None else [1] * count
        total = sum(widths)
        widths = [round(w * 9524 / total) for w in widths]
        widths[-1] += 9524 - sum(widths)
    replace(table, 'tblPr', pr)
    grid = node('tblGrid')
    for width in widths:
        grid.append(node('gridCol', w=width))
    if old_grid is not None:
        table.remove(old_grid)
    table.insert(1, grid)
    for row_index, row in enumerate(rows):
        rp = node('trPr'); rp.append(node('cantSplit'))
        if row_index == 0:
            rp.append(node('tblHeader'))
        replace(row, 'trPr', rp)
        for column, cell in enumerate(row.findall(tag('tc'))):
            ref = FIELD_HEADER if field and row_index == 0 else FIELD_BODY if field else CELL_HEADER if row_index == 0 else CELL_BODY
            cp = copy.deepcopy(ref.find(tag('tcPr')))
            prop(cp, 'tcW', w=widths[column], type='dxa')
            if is_revision and row_index == 0:
                prop(cp, 'shd', val='clear', color='auto', fill='FFFFFF')
            replace(cell, 'tcPr', cp)
            for p in cell.findall(tag('p')):
                pp = copy.deepcopy(ref.find('./w:p/w:pPr', NS))
                prop(pp, 'jc', val='center' if row_index == 0 else 'left')
                # Use layout-based wrapping; no extra characters enter field/URI text.
                prop(pp, 'wordWrap', val=0)
                prop(pp, 'keepNext', val=1 if row_index == 0 else 0)
                prop(pp, 'widowControl', val=0)
                replace(p, 'pPr', pp)
                run_format(p, 8 if field else 9, bold=row_index == 0)
    return table


def code_table(paragraphs):
    table = copy.deepcopy(CODE_REF)
    for child in list(table):
        if child.tag not in (tag('tblPr'), tag('tblGrid')):
            table.remove(child)
    prop(table.find(tag('tblPr')), 'tblW', w=9524, type='dxa')
    tr = node('tr')
    if len(paragraphs) < 50:
        trp = node('trPr'); trp.append(node('cantSplit')); tr.append(trp)
    tc = node('tc'); cp = copy.deepcopy(CODE_CELL.find(tag('tcPr')))
    prop(cp, 'tcW', w=9524, type='dxa'); tc.append(cp)
    for p in paragraphs:
        pp = copy.deepcopy(CODE_CELL.find('./w:p/w:pPr', NS))
        prop(pp, 'pStyle', val='SnCode')
        prop(pp, 'spacing', before=0, after=0, line=240, lineRule='auto')
        prop(pp, 'jc', val='left')
        prop(pp, 'ind', left=0, firstLine=0)
        prop(pp, 'wordWrap', val=0)
        prop(pp, 'keepNext', val=0)
        prop(pp, 'keepLines', val=0)
        prop(pp, 'widowControl', val=0)
        replace(p, 'pPr', pp)
        run_format(p, 8.5 if len(paragraphs) < 50 else 8, bold=False)
        tc.append(p)
    tr.append(tc); table.append(tr)
    return table


def after_cover(body):
    for i, element in enumerate(body):
        if element.find('./w:pPr/w:sectPr', NS) is not None:
            return i + 1
    raise AssertionError('Missing cover section break')


old_content = copy.deepcopy(list(OLD_BODY)[after_cover(OLD_BODY):])
new_body = copy.deepcopy(REF_BODY)
for child in list(new_body):
    new_body.remove(child)
# The exact template cover, including its double page border and borderless signature form.
for child in list(REF_BODY)[:after_cover(REF_BODY)]:
    c = copy.deepcopy(child)
    for t in c.findall('.//w:t', NS):
        if t.text == '2026 年 8 月':
            t.text = '2026 年 11 月'
    new_body.append(c)

old_main_paragraphs = []
old_tables = []
old_code_groups = []
new_code_groups = []
i = 0
while i < len(old_content):
    child = old_content[i]
    if child.tag == tag('sectPr'):
        new_body.append(copy.deepcopy(BODY_SECTION)); i += 1; continue
    if child.tag == tag('tbl'):
        old_tables.append(canonical(txt(child)))
        titles = [clean(txt(c)) for c in child.findall('./w:tr/w:tc', NS)]
        # Template uses bold arrow labels to introduce the design/response tables.
        if titles == ['项目', '说明']:
            new_body.append(label('➢  接口设计：'))
        elif titles[:2] == ['HTTP状态', '场景']:
            new_body.append(label('➢  HTTP状态说明：'))
        elif titles[:2] == ['业务码', '含义']:
            new_body.append(label('➢  单条SN业务处理结果码说明：'))
        new_body.append(format_table(child, is_revision=len(old_tables) == 1))
        i += 1; continue
    if child.tag != tag('p'):
        new_body.append(child); i += 1; continue
    if style_id(child) == 'Code':
        group = []
        while i < len(old_content) and old_content[i].tag == tag('p') and style_id(old_content[i]) == 'Code':
            group.append(old_content[i]); i += 1
        original_lines = [clean(txt(p)) for p in group]
        old_code_groups.append('\n'.join(original_lines))
        code = code_table(group)
        if len(new_body) and new_body[-1].tag == tag('p'):
            previous_pp = new_body[-1].find(tag('pPr'))
            if previous_pp is not None:
                prop(previous_pp, 'keepNext')
        new_code_groups.append('\n'.join(txt(p) for p in code.findall('.//w:tc/w:p', NS)))
        new_body.append(code)
        continue
    text = clean(txt(child))
    if text:
        old_main_paragraphs.append((style_id(child), canonical(text)))
    if text in ('文档修订记录', '目    录', '1. 前言描述', '2. 接口集成标准说明'):
        line = copy.deepcopy(RULE_REF)
        if child.find('./w:pPr/w:pageBreakBefore', NS) is not None:
            line.find(tag('pPr')).insert(0, node('pageBreakBefore'))
            child.find(tag('pPr')).remove(child.find('./w:pPr/w:pageBreakBefore', NS))
        new_body.append(line)
    if text.startswith('接口用途：'):
        new_body.append(label('➢  接口用途：'))
        text_nodes = child.findall('.//w:t', NS)
        assert len(text_nodes) == 1
        text_nodes[0].text = text[len('接口用途：'):]
    elif text in ('成功返回示例：', '认证失败示例：'):
        new_body.append(label('➢  ' + text)); i += 1; continue
    new_body.append(format_paragraph(child)); i += 1

# Keep the source/validation appendix compact enough to avoid a lone closing line.
in_sources = False
for child in new_body:
    if child.tag != tag('p'):
        continue
    if txt(child).startswith('附录C.'):
        in_sources = True
        continue
    if in_sources and txt(child):
        run_format(child, 9)

REF.replace(REF_BODY, new_body)
# Remove invisible wrapping characters from all text, including nested table content.
for t in REF.findall('.//w:t', NS):
    t.text = clean(t.text or '')

with zipfile.ZipFile(TEMPLATE) as z:
    styles = X.fromstring(z.read('word/styles.xml'))
    settings = X.fromstring(z.read('word/settings.xml'))
    package = {name: z.read(name) for name in z.namelist() if not name.endswith('/')}


def extra_style(identifier, display, size, outline=9):
    s = node('style', type='paragraph', styleId=identifier)
    s.append(node('name', val=display)); s.append(node('basedOn', val='1'))
    pp = node('pPr'); pp.append(node('outlineLvl', val=outline)); s.append(pp)
    rp = node('rPr'); rp.append(node('rFonts', ascii='微软雅黑', hAnsi='微软雅黑', eastAsia='微软雅黑', cs='微软雅黑'))
    rp.append(node('color', val='000000')); rp.append(node('sz', val=round(size * 2))); s.append(rp)
    styles.append(s)


extra_style('SnFrontHeading', 'SN Front Matter Heading', 22)
extra_style('SnEnumHeading', 'SN Enum Heading', 14)
extra_style('SnCode', 'SN Example', 8.5)
for level in (1, 2, 3):
    extra_style('SnTOC' + str(level), 'SN TOC ' + str(level), 11 if level == 1 else 10.5)
prop(settings, 'updateFields', val='true')

serialize = lambda root: X.tostring(root, encoding='UTF-8', xml_declaration=True, standalone=True)
package['word/document.xml'] = serialize(REF)
package['word/styles.xml'] = serialize(styles)
package['word/settings.xml'] = serialize(settings)

# Audit against authoritative pre-formatting body content, not just counts.
new_text = canonical(txt(new_body))
missing_paragraphs = [text for style, text in old_main_paragraphs if not style.startswith('TOC') and text not in new_text]
missing_tables = [i for i, value in enumerate(old_tables) if value not in new_text]
assert not missing_paragraphs, missing_paragraphs[:3]
assert not missing_tables, missing_tables
assert old_code_groups == new_code_groups
assert '\u200b' not in txt(REF)
assert all(s.find(tag('pgSz')).get(tag('w')) == '11906' for s in REF.findall('.//w:sectPr', NS))
expected_margins = dict(BODY_SECTION.find(tag('pgMar')).attrib)
assert all(dict(s.find(tag('pgMar')).attrib) == expected_margins for s in REF.findall('.//w:sectPr', NS))
assert all(c.get(tag('val')) == '000000' for c in new_body.findall('.//w:color', NS))
assert not new_body.findall('.//w:headerReference', NS) and not new_body.findall('.//w:footerReference', NS)
embedded = [json.loads(code) for code in new_code_groups if code.lstrip().startswith('{')]
full_request = next(value for value in embedded if 'snList' in value)
assert full_request == json.loads((HERE / '对接附件/04_SN上传_全量63字段请求.json').read_text())

output_paths = [HERE / (STEM + '.docx'), HERE / (STEM + '_模板格式修订版.docx')]
for path in output_paths:
    temporary = path.with_suffix('.tmp.docx')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in package.items():
            z.writestr(name, content)
    temporary.replace(path)

audit = {
    '原文件备份': str(SOURCE), '模板': str(TEMPLATE),
    '模板SHA256': hashlib.sha256(TEMPLATE.read_bytes()).hexdigest(),
    '修订版SHA256': hashlib.sha256(output_paths[-1].read_bytes()).hexdigest(),
    '删除零宽字符数量': sum(txt(OLD).count(c) for c in INVISIBLE),
    '修订后零宽字符数量': sum(txt(REF).count(c) for c in INVISIBLE),
    '模板属性': {'页幅': 'A4纵向', '页边距Twips': expected_margins,
                 '字体': '微软雅黑', '字体颜色': '000000', '表头填充': 'E7E6E6', '代码框填充': 'F7F7F7'},
    '一致性核对': {'原正文段落全部保留': not missing_paragraphs, '原字段表及其他表格内容全部保留': not missing_tables,
                   '原示例逐行一致': old_code_groups == new_code_groups, '全量示例与附件JSON一致': True,
                   '原正文表格数量': len(old_tables), '示例代码块数量': len(new_code_groups),
                   '全量请求EV顶层字段': len(full_request['snList'][0])},
    '输出文件': [str(p) for p in output_paths], '渲染验证': '待执行',
}
(HERE / '模板格式修订_核对报告.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2))
print(json.dumps(audit, ensure_ascii=False))
