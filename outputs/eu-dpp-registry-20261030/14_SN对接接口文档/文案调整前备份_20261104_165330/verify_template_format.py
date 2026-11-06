"""Cache actual TOC pages, then audit Word content and the final rendered PDF."""
import argparse
import csv
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

import fitz
from lxml import etree as X
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
STEM = '供应商SN对接接口说明_EV全量语义_24位国标码_v1.0'
DOCX = HERE / (STEM + '_模板格式修订版.docx')
PDF = DOCX.with_suffix('.pdf')
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
normalize = lambda s: re.sub(r'[\s\u200b\ufeff\u2060\u00ad]', '', s)
txt = lambda n: ''.join(n.xpath('.//w:t/text()', namespaces=NS))
parser = argparse.ArgumentParser()
parser.add_argument('--update-toc', action='store_true')
args = parser.parse_args()

with zipfile.ZipFile(DOCX) as z:
    package = {name: z.read(name) for name in z.namelist()}
root = X.fromstring(package['word/document.xml'])
pdf = fitz.open(PDF)
page_texts = [normalize(p.get_text()) for p in pdf]
pages = {}
for p in root.findall('./w:body/w:p', NS):
    bookmark = p.find('w:bookmarkStart', NS)
    if bookmark is not None:
        text = txt(p)
        for i in range(1, len(pdf)):
            if i == 2:  # The TOC itself is not the location of a heading.
                continue
            if normalize(text) and normalize(text) in page_texts[i]:
                pages[bookmark.get('{' + W + '}name')] = {'title': text, 'page': i + 1}
                break
fields = root.findall('.//w:fldSimple', NS)
toc_fields = [f for f in fields if 'PAGEREF' in f.get('{' + W + '}instr', '')]
missing = []
for field in toc_fields:
    match = re.search(r'PAGEREF\s+(\S+)', field.get('{' + W + '}instr'))
    name = match[1]
    assert name in pages, name
    expected = str(pages[name]['page'])
    if args.update_toc:
        field.find('.//w:t', NS).text = expected
    elif txt(field) != expected:
        missing.append({'heading': pages[name]['title'], 'cached': txt(field), 'actual': expected})

if args.update_toc:
    package['word/document.xml'] = X.tostring(root, encoding='UTF-8', xml_declaration=True, standalone=True)
    for destination in [DOCX, HERE / (STEM + '.docx')]:
        temporary = destination.with_suffix('.tmp.docx')
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, content in package.items():
                z.writestr(name, content)
        temporary.replace(destination)
    (HERE / '模板格式修订_目录页码.json').write_text(json.dumps(pages, ensure_ascii=False, indent=2))
    print(json.dumps({'updated_toc_entries': len(toc_fields), 'pages': len(pdf)}, ensure_ascii=False))
    raise SystemExit(0)

assert not missing, missing
all_text = txt(root)
assert not any(c in all_text for c in ['\u200b', '\ufeff', '\u2060', '\u00ad'])
urls = ['https://battery.chdpp.com/open-api/dpp-platform/login',
        'https://battery.chdpp.com/open-api/dpp-platform/supplier/sn/upload']
assert all(url in all_text for url in urls)
paths = []
enum_values = []
for table in root.findall('./w:body/w:tbl', NS):
    rows = table.findall('w:tr', NS)
    cells = rows[0].findall('w:tc', NS)
    titles = [txt(c) for c in cells]
    if len(titles) == 6 and '相对snList[]' in titles[1]:
        for row in rows[1:]:
            values = [txt(c) for c in row.findall('w:tc', NS)]
            if values[0].isdecimal():
                paths.append((int(values[0]), values[1]))
    if titles == ['序号', '完整取值', '官方名称', '符号']:
        enum_values.extend(txt(row.findall('w:tc', NS)[1]) for row in rows[1:])
with (HERE / '对接附件/EV字段字典_206节点.csv').open(encoding='utf-8-sig') as fp:
    expected_paths = [(int(r['序号']), r['字段路径（相对snList[]）']) for r in csv.DictReader(fp)]
with (HERE / '对接附件/枚举字典_完整取值.csv').open(encoding='utf-8-sig') as fp:
    expected_enums = [r['取值'] for r in csv.DictReader(fp)]
assert paths == expected_paths and len(paths) == 206
assert enum_values == expected_enums
assert (HERE / (STEM + '.docx')).read_bytes() == DOCX.read_bytes()
text_run_fonts = {r.find('w:rPr/w:rFonts', NS).get('{' + W + '}eastAsia')
                  for r in root.findall('.//w:r', NS) if r.find('w:t', NS) is not None and txt(r)}
assert text_run_fonts == {'微软雅黑'}, text_run_fonts
violations = []
colors = set()
for i, page in enumerate(pdf):
    assert abs(page.rect.width - 595.3) < 1 and abs(page.rect.height - 841.9) < 1
    for word in page.get_text('words'):
        if word[0] < 55 or word[2] > page.rect.width - 50 or word[1] < 30 or word[3] > page.rect.height - 35:
            violations.append({'page': i + 1, 'text': word[4], 'box': word[:4]})
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            colors.update(span['color'] for span in line['spans'])
assert colors == {0}, colors
assert not violations, violations[:5]
assert len(pdf[-1].get_text().strip()) > 200
shutil.copyfile(PDF, HERE / (STEM + '.pdf'))
headings = ['2.1.1请求参数及示例', '2.2.2请求字段说明', 'E07·34项', '附录B.完整请求示例', '附录C.来源、附件与验证说明']
selected = [0, 2]
for heading in headings:
    found = next(i for i in range(3, len(pdf)) if normalize(heading) in page_texts[i])
    selected.append(found)
thumbs = []
for i in selected:
    pix = pdf[i].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
    im = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    im.thumbnail((580, 810))
    canvas = Image.new('RGB', (610, 845), '#e1e1e1')
    canvas.paste(im, ((610 - im.width) // 2, 28))
    ImageDraw.Draw(canvas).text((12, 8), f'Revised p{i + 1}', fill='black')
    thumbs.append(canvas)
sheet = Image.new('RGB', (1830, 845 * ((len(thumbs) + 2) // 3)), 'white')
for i, im in enumerate(thumbs):
    sheet.paste(im, ((i % 3) * 610, (i // 3) * 845))
sheet.save(HERE / '模板格式修订_排版预览.jpg')
report_file = HERE / '模板格式修订_核对报告.json'
report = json.loads(report_file.read_text())
report.update({'修订版SHA256': hashlib.sha256(DOCX.read_bytes()).hexdigest(),
               '渲染验证': {'PDF页数': len(pdf), '正文颜色': '全部黑色', '页边距外文字': violations,
                        '全部206个字段路径逐项一致': True, '全部枚举值逐项一致': True,
                        '域名和接口地址无附加字符': True, '目录页码逐项核对': len(toc_fields),
                        '示例代码块的内容逐行一致': True, '无单句尾页': True,
                        '视觉检查页码': [i + 1 for i in selected]}})
report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report['渲染验证'], ensure_ascii=False))
