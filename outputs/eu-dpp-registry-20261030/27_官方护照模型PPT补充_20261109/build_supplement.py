from pathlib import Path
from copy import deepcopy
from lxml import etree as E
from PIL import ImageFont
import json
import shutil

BASE = Path(__file__).resolve().parent
WS = BASE / 'ppt_workspace'
SVG = WS / 'authoring-svg-flat'
NS = 'http://www.w3.org/2000/svg'
XML = 'http://www.w3.org/XML/1998/namespace'
Q = lambda tag: '{' + NS + '}' + tag
BLUE = '#4472C4'
INK = '#202A37'
MUTED = '#556477'
FONT = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
measurements = []

def el(parent, tag, **attrs):
    return E.SubElement(parent, Q(tag), {k.replace('_', '-'): str(v) for k, v in attrs.items()})

def group(parent, identity, bounds, **attrs):
    return el(parent, 'g', id=identity, **{'data-pptx-bounds': ' '.join(map(str, bounds))}, **attrs)

def rect(parent, x, y, w, h, fill, **attrs):
    return el(parent, 'rect', x=x, y=y, width=w, height=h, fill=fill, **attrs)

def text(parent, x, y, lines, size=21, color=INK, bold=False, leading=29, anchor='start', width=None):
    if isinstance(lines, str):
        lines = [lines]
    t = el(parent, 'text', x=x, y=y, font_family='Microsoft YaHei', font_size=size, fill=color,
           font_weight='bold' if bold else 'normal', text_anchor=anchor)
    t.set('{' + XML + '}space', 'preserve')
    t.text = lines[0]
    for line in lines[1:]:
        span = el(t, 'tspan', x=x, dy=leading)
        span.text = line
    if width:
        f = ImageFont.truetype(FONT, round(size))
        max_width = max(f.getlength(line) for line in lines)
        measurements.append({'lines': lines, 'size': size, 'actual_width': round(max_width, 2), 'slot_width': width})
        assert max_width <= width + 1, (lines, max_width, width)
    return t

def new_page(title, subtitle):
    root = deepcopy(E.parse(str(SVG / 'slide_09.svg')).getroot())
    # Preserve the original background and immutable inherited grey separator.
    for child in list(root):
        if child.get('data-pptx-source-ref', '').startswith('slide:'):
            root.remove(child)
    head = group(root, 'supplement-title', (20, 10, 1240, 49))
    text(head, 21.47, 45.27, title, 32, '#000000', True, width=1230)
    sub = group(root, 'supplement-subtitle', (38, 86, 1204, 43))
    text(sub, 38, 113, subtitle, 22, MUTED, width=1200)
    return root

def footer(root, lines):
    g = group(root, 'supplement-sources', (38, 675, 1204, 40), **{'data-pptx-role': 'footer'})
    text(g, 38, 690, lines, 12.5, MUTED, leading=18, width=1204)

def write_page(root, name):
    (SVG / name).write_bytes(E.tostring(root, xml_declaration=True, encoding='utf-8', pretty_print=True))

image_source = BASE.parent / '20_群文档评估_20261107/文档图片/fc-proof-10.png'
image_target = WS / 'images/eu_semantic_unavailable_20261106.png'
image_target.parent.mkdir(exist_ok=True)
shutil.copy2(image_source, image_target)

# Output page 9: released specifications and the dated integration evidence.
p = new_page('官方规范已发布，完整联调条件仍待确认', '开发依据已具备，完整联调仍待确认。')
left = group(p, 'released-development-resources', (38, 146, 572, 438))
rect(left, 38, 146, 572, 42, BLUE)
text(left, 56, 175, '已发布的开发依据｜2026.10.30', 23, '#FFFFFF', True, width=535)
text(left, 56, 229, '接口文档与 OpenAPI', 23, INK, True, width=535)
text(left, 56, 264, ['认证与Token、注册提交、幂等重试，', '以及任务状态和逐项结果查询。'], 21, width=535)
text(left, 56, 354, '语义模型 1.0.0', 23, INK, True, width=535)
text(left, 56, 389, ['覆盖EV、LMT、IND的字段结构、类型、', '必填、枚举及公开／受限访问视图。'], 21, width=535)
text(left, 56, 479, '校验资源与测试样例', 23, INK, True, width=535)
text(left, 56, 514, ['JSON Schema、SHACL、JSON-LD，', '可用于构建本地校验与回归用例。'], 21, width=535)

right = group(p, 'dated-integration-evidence', (634, 146, 608, 438))
rect(right, 634, 146, 608, 42, '#B45309')
text(right, 652, 175, '内部测试记录｜2026.11.06', 23, '#FFFFFF', True, width=572)
text(right, 652, 224, '鉴权链路尚待完成验证', 23, INK, True, width=572)
text(right, 652, 256, ['Token地址GET探测返回404；', '正式POST取Token待服务账号与凭据具备后验证。'], 20, leading=28, width=572)
text(right, 652, 323, '在线语义校验当次不可用', 23, '#9A3412', True, width=572)
el(right, 'image', x=648, y=339, width=580, height=213.42,
   href='../images/eu_semantic_unavailable_20261106.png', preserveAspectRatio='xMidYMid meet')
text(right, 652, 577, '正式环境：接收0条、拒绝1条；未完成登记。', 16.5, MUTED, width=572)

takeaway = group(p, 'integration-readiness-conclusion', (38, 600, 1204, 63))
rect(takeaway, 38, 600, 1204, 63, '#EAF0F9')
text(takeaway, 56, 639, '当前完整端到端联调尚未完成，具体可联调时间以官方后续通知为准。',
     22, '#1F4E79', True, width=1167)
footer(p, ['来源：欧盟官方资源页（2026.10.30）；刘勇辉《成果报告V1.3》《注册认证指南V1.2》，2026.11.06测试。',
           '官方资料：single-market-economy.ec.europa.eu/single-market/digital-product-passport/resources-documentation_en'])
write_page(p, 'official_registry_readiness.svg')

# Output page 10: one editable PowerPoint-native table, matching the source deck.
p = new_page('上汽当前可推进：完善系统，完成联调前准备', '以官方接口与语义规范为基线，先完成本地开发和验证，再进入欧盟端到端联调。')
table_x, table_y, table_w, table_h = 38, 145, 1204, 440
widths = [200, 680, 324]
heights = [44, 99, 99, 99, 99]
headers = ['工作方向', '当前可开展工作', '准备成果']
body = [
    [ ['模型与系统适配'],
      ['对齐字段、类型、必填、枚举、单位、多语言及权限；', '完善录入、动态更新规则和业务数据接入。'],
      ['工程字段映射与模型；', '上汽→护照平台接入接口'] ],
    [ ['欧盟接口开发'],
      ['开发私钥签名、Token申请、注册提交及幂等重试；', '实现任务查询，通过Mock验证调用与异常处理。'],
      ['欧盟接口适配模块；', '配置及联调用例'] ],
    [ ['本地模型校验'],
      ['执行JSON Schema结构校验与SHACL语义约束校验；', '覆盖三类电池的正向样例和负向校验用例。'],
      ['校验功能、样例及报告'] ],
    [ ['UPI与试点数据'],
      ['准备HTTPS护照服务、匿名公开及授权受限视图；', '实现欧盟回读令牌验签与授权，准备单条EV数据。'],
      ['UPI与访问控制；', '单条EV试点数据包'] ],
]
table = group(p, 'saic-preintegration-work', (table_x, table_y, table_w, table_h), **{'data-pptx-replace-with': 'table'})
payload = {
    'schema': 'ppt-master.semantic-table.v2', 'name': 'saic-preintegration-work',
    'x': table_x, 'y': table_y, 'width': table_w, 'height': table_h,
    'strict_grid': True, 'header_rows': 1, 'column_widths': widths, 'row_heights': heights,
    'style': {'font_family': 'Microsoft YaHei', 'font_size': 20, 'header_font_size': 22,
              'header_fill': BLUE, 'header_text': '#FFFFFF', 'body_text': INK,
              'body_fill': '#FFFFFF', 'band_row': False,
              'border_color': '#CED7E3', 'border_width': 1, 'lang': 'zh-CN'},
    'columns': [], 'rows': []
}

all_rows = [[ [h] for h in headers ]] + body
y = table_y
for ri, row in enumerate(all_rows):
    h = heights[ri]
    x = table_x
    payload_row = []
    for ci, lines in enumerate(row):
        header = ri == 0
        bg = BLUE if header else ('#FFFFFF' if ri % 2 else '#F3F6FB')
        fg = '#FFFFFF' if header else ('#1F4E79' if ci == 0 else INK)
        size = 22 if header else 20
        bold = header or ci == 0
        align = 'ctr' if header else 'l'
        rect(table, x, y, widths[ci], h, bg, stroke='#CED7E3', stroke_width=1)
        tx = x + widths[ci] / 2 if header else x + 15
        ty = y + 29 if header else y + 30
        text(table, tx, ty, lines, size, fg, bold, leading=27,
             anchor='middle' if header else 'start', width=widths[ci] - 30)
        payload_row.append({'text': ''.join(lines), 'fill': bg, 'color': fg,
                            'font_size': size, 'bold': bold, 'align': align,
                            'valign': 'middle' if header else 'top',
                            'padding': {'left': 15, 'right': 15, 'top': 9, 'bottom': 8}})
        x += widths[ci]
    if ri == 0:
        payload['columns'] = payload_row
    else:
        payload['rows'].append(payload_row)
    y += h
metadata = E.Element(Q('metadata'), type='application/json')
metadata.text = json.dumps(payload, ensure_ascii=False)
table.insert(0, metadata)

readiness = group(p, 'saic-live-integration-entry-conditions', (38, 604, 1204, 62))
rect(readiness, 38, 604, 1204, 62, '#EAF0F9')
text(readiness, 56, 630, ['联调启动条件：服务账号及凭据、Token POST、UPI回读及官方在线语义校验具备。',
                        '先完成单条EV护照端到端验证，再扩展类别与批量注册。'], 21, '#1F4E79', True, leading=27, width=1167)
footer(p, ['依据：官方API、Service Account、Self-Hosted Authentication及Semantic Data指南，结合上汽当前准备任务整理。',
           '范围：上汽业务系统→护照平台数据接入，与护照平台→欧盟注册中心接口开发分别推进。'])
write_page(p, 'saic_preintegration_work.svg')

plan = {'schema': 'ppt-master.roundtrip-page-plan.v1',
        'pages': ([{'source_slide': i} for i in range(1, 9)] +
                  [{'source_slide': 9, 'svg': 'official_registry_readiness.svg'},
                   {'source_slide': 9, 'svg': 'saic_preintegration_work.svg'},
                   {'source_slide': 9}])}
(WS / 'page_plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
(WS / 'validation/handcrafted-text-capacity.json').write_text(json.dumps(measurements, ensure_ascii=False, indent=2))
print(json.dumps({'pages': len(plan['pages']), 'new_pages': [9, 10], 'original_final_page': 11,
                  'measured_text_frames': len(measurements)}, ensure_ascii=False))
