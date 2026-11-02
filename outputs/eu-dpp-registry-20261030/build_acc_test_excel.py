"""Build a sourced ACC test workbook and a credential-free Postman starter kit."""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter


ROOT = Path(__file__).resolve().parent
OUT = ROOT / '06_ACC测试指南'
OUT.mkdir(exist_ok=True)
KIT = OUT / 'Postman测试附件'
KIT.mkdir(exist_ok=True)
STAMP = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S %Z')
SOURCES = json.loads((ROOT / '来源与下载校验.json').read_text())
CHECKS = json.loads((OUT / 'ACC_匿名连通性检查.json').read_text())
DOCS = {f['title']: f for f in SOURCES['files']}
DOCS['Service Account Technical Integration Guide'] = DOCS['Service Account Integration Guide']
PUBLICATION = SOURCES['sourcePage']
SA = DOCS['Service Account Technical Integration Guide']['url']
API = DOCS['API Integration Guide']['url']
OPENAPI = DOCS['OpenAPI Specifications']['url']
SELFHOST = DOCS['Self-Hosted Authentication Guide']['url']
FAQ = 'https://single-market-economy.ec.europa.eu/single-market/digital-product-passport/eu-digital-product-passport-faq-batteries_en'
REGISTRY_PAGE = 'https://single-market-economy.ec.europa.eu/single-market/digital-product-passport/dpp-registry_en'
PORTAL = 'https://registry.acc.product-passport.ec.europa.eu/'
REG_BASE = 'https://reg.acc.product-passport.ec.europa.eu/api/v1'
ISSUER = 'https://auth.acc.product-passport.ec.europa.eu'
TOKEN = ISSUER + '/oauth2/token'
METADATA = ISSUER + '/.well-known/oauth-authorization-server'
JWKS = ISSUER + '/oauth2/jwks'
RESOURCE = ISSUER + '/resources/dpp/registry'
REG = yaml.safe_load((ROOT / '02_OpenAPI/openapi-dpp-registry-public.yml').read_text())
DASH = yaml.safe_load((ROOT / '02_OpenAPI/openapi-dpp-dashboard-public.yml').read_text())
SPEC_ORIGIN = next(s['url'] for s in REG['servers'] if s['description'] == 'ACC')
DASH_BASE = next(s['url'] for s in DASH['servers'] if s['description'] == 'ACC') + '/api/v1'
OUTPUT = OUT / '欧委会DPP_OpenAPI测试地址与操作指南_20261030.xlsx'


def page(url: str, number: int) -> str:
    return url + f'#page={number}'


wb = Workbook()
wb.remove(wb.active)
NAVY = '17344D'
TEAL = '087F8C'
ORANGE = 'FFF0CD'
WHITE = 'FFFFFF'
GRAY = '526374'


def sheet(name, title, note, headers, rows, widths, *, code_col=None):
    ws = wb.create_sheet(name)
    n = len(headers)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n)
    ws.cell(1, 1, title)
    ws.cell(1, 1).font = Font(name='微软雅黑', size=18, bold=True, color=WHITE)
    ws.cell(1, 1).fill = PatternFill('solid', fgColor=NAVY)
    ws.cell(1, 1).alignment = Alignment(vertical='center')
    ws.row_dimensions[1].height = 36
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n)
    ws.cell(2, 1, note)
    ws.cell(2, 1).font = Font(name='微软雅黑', size=11, color=GRAY)
    ws.cell(2, 1).alignment = Alignment(wrap_text=True, vertical='center')
    ws.row_dimensions[2].height = 48
    ws.cell(3, 1, '返回使用说明')
    ws.cell(3, 1).hyperlink = "#'使用说明'!A1"
    ws.cell(3, 1).font = Font(name='微软雅黑', color=TEAL, underline='single', size=10)
    for i, header in enumerate(headers, 1):
        c = ws.cell(4, i, header)
        c.font = Font(name='微软雅黑', bold=True, color=WHITE, size=11)
        c.fill = PatternFill('solid', fgColor=TEAL)
        c.alignment = Alignment(wrap_text=True, vertical='center')
    ws.row_dimensions[4].height = 28
    for ri, row in enumerate(rows, 5):
        heights = [2]
        for ci, value in enumerate(row, 1):
            text = '' if value is None else str(value)
            c = ws.cell(ri, ci, text)
            # All entries are text; never turn pasted strings into formulas.
            c.data_type = 's'
            c.font = Font(name='Consolas' if ci == code_col else '微软雅黑', size=10, color='233746')
            c.alignment = Alignment(wrap_text=True, vertical='top')
            c.border = Border(bottom=Side(style='hair', color='DCE4EA'))
            width = widths[ci - 1]
            weighted_lines = sum(max(1, math.ceil(sum(2 if ord(ch) > 255 else 1 for ch in line) / max(8, width - 3))) for line in text.split('\n'))
            heights.append(weighted_lines)
            if text.startswith('https://') and '\n' not in text and ' ' not in text:
                c.hyperlink = text
                c.font = Font(name='微软雅黑', size=10, color='096A9B', underline='single')
            if any(s in text for s in ['域名不一致', '未完成带账号', '缺少会话', '待核实', '未实测', '非 API 地址']):
                c.fill = PatternFill('solid', fgColor=ORANGE)
        ws.row_dimensions[ri].height = min(409, max(42, max(heights) * 14 + 10))
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = 'C5' if n > 4 else 'B5'
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 80 if n >= 7 else 90
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.sheet_properties.tabColor = TEAL
    ws.print_title_rows = '1:4'
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_options.horizontalCentered = True
    ws.oddFooter.center.text = 'DPP ACC 测试整理版 | 第 &P / &N 页'
    ws.oddFooter.right.text = '2026-10-31'
    ws.print_area = f'A1:{get_column_letter(n)}{ws.max_row}'
    if rows:
        table = Table(displayName=f'Table{len(wb.worksheets)}', ref=f'A4:{get_column_letter(n)}{ws.max_row}')
        table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=True)
        ws.add_table(table)
    return ws


summary_rows = [
    ('这份表是什么', '根据欧委会 2026-10-30 发布的原始指南、OpenAPI，以及 2026-10-31 本机匿名 GET 核验整理。是工作阅读版，官方原文链接见“官方来源”。', '官方来源'),
    ('先回答有没有地址', '有。服务账号指南第 20 页列出 ACC（Acceptance）环境的 Registry API 基础地址和 Token 地址；原始 OpenAPI 也声明 ACC server。', '测试地址'),
    ('当前能否直接联调', '未完成带账号的端到端联调。指南域名的产品分类接口返回 401，正文为 Unauthorized: missing session cookie；原始 OpenAPI 域名的同路径返回门户 HTML。不能据此判断 Bearer Token 接入已就绪。', '本机实测'),
    ('先核对哪件事', '官方指南用 reg.acc 域名，Registry/Dashboard OpenAPI 都用 registry.acc 域名，存在域名不一致。请确认两个 API 服务的实际 ACC base URL，以及网关是否有额外接入要求。不要自动把两个域名视为等价。', '报错与核实'),
    ('如何开始', '进入 ACC 门户，按官方要求使用单独的测试 EU Login；完成组织核验；创建带 Register DPPs 权限的 Service Account；上传公钥并取得 Client ID、kid。', '测试步骤'),
    ('最小联调顺序', '生成并签名 client assertion → POST Token → GET 产品分类 → 部署可被 Registry 访问的测试 DPP → POST 单条注册 → 查询任务 → 检查业务结果和逐条证据。', '测试步骤'),
    ('如何算成功', 'Token 成功签发且受保护接口返回预期 JSON；注册接口 202 仅为受理。任务应达到 COMPLETED，且 outcome.outcomeResult 为 SUCCEEDED，并核对逐条结果。', '接口清单'),
    ('配置怎么填', 'REGISTRY_BASE_URL 参考服务账号指南；DASHBOARD_BASE_URL 暂保留 Dashboard OpenAPI 原文值。两者都需要根据实际部署核实后填写。其余身份和令牌变量仅在本机配置。', 'JWT与参数'),
    ('复制请求到哪里', '“请求示例”可复制到 Bash/curl；另附 Postman Collection 与 Environment。先填自己的测试配置和新签名 assertion，再按编号逐个执行。', '请求示例'),
    ('本次核验边界', '只有匿名 GET；没有登录、没有申请 Token、没有提交注册、没有真实公私钥或访问令牌。Token URL 本次未实测。', '本机实测'),
    ('护照内容放哪里', '注册请求传 UPI 和分类等登记信息；Registry 会访问 EO/DPP 服务提供方托管的护照并校验。localhost、example.com 占位 URL 无法完成有效注册。', '测试步骤'),
    ('官方支持', '涉及域名、session cookie、元数据 401、JWKS 404 的疑问，见“报错与核实”可直接复制的问题和官方 Help Desk 联系方式。', '报错与核实'),
    ('资料时间', '官方资料发布日期：2026-10-30。地址出现在这批文档中，不代表测试环境首次于该日开放。', '官方来源'),
    ('整理时间', STAMP + '（北京时间）', '本机实测'),
]
cover = sheet('使用说明', '欧委会 DPP OpenAPI · ACC 测试地址与操作指南',
              '先看当前访问结果，再按步骤准备账号与凭证。黄色单元格表示有差异、尚未验证或需要核实。',
              ['你想了解什么', '说明', '查看工作表'], summary_rows, [24, 100, 24])
for ri, row in enumerate(summary_rows, 5):
    cover.cell(ri, 3).hyperlink = f"#'{row[2]}'!A1"
    cover.cell(ri, 3).font = Font(name='微软雅黑', color=TEAL, underline='single', size=11)

address_rows = [
    ('A01', '测试管理门户', '浏览器 / GET', PORTAL, '登录测试 EU Login，完成组织和服务账号配置。门户是网页入口。', '服务账号指南第 20 页；官方 Registry 网页 Testing Environment', '200 / text/html，标题 Digital Product Passport Registry；只证明门户网页响应。', page(SA, 20)),
    ('A02', 'Registry API base（指南）', 'API 基础地址', REG_BASE, '仅基础地址；追加 /dpp/product-groups 或 /dpp/submission，不要再重复追加 /api/v1。', '服务账号指南第 20 页 Registry API Base', '与原始 OpenAPI 存在域名不一致；查询分类匿名返回 401 / missing session cookie。', page(SA, 20)),
    ('A03', 'Registry/Dashboard server（OpenAPI）', 'OpenAPI server origin', SPEC_ORIGIN, 'OpenAPI servers 原文值，不含 /api/v1；paths 本身含 /api/v1。', 'openapi-dpp-registry-public.yml 和 openapi-dpp-dashboard-public.yml 的 servers[ACC]', '与指南存在域名不一致；分类路径本机返回门户 HTML，未返回 API JSON。', OPENAPI),
    ('A04', 'Token 端点', 'POST（表单）', TOKEN, 'OAuth2 client_credentials + private_key_jwt；填写 client_id、client_assertion_type、client_assertion、resource。scope 按已有授权选填。', '服务账号指南第 9–11、20 页', '本次未实测：没有服务账号和签名 assertion，不发送占位凭证请求。', page(SA, 9)),
    ('A05', '授权服务器元数据', 'GET', METADATA, '可用时读取 issuer、token_endpoint 等，以核对环境配置；本机当前不能匿名读取。', '服务账号指南第 9、20 页', '本机匿名 GET 为 401，无 JSON 元数据；需核实网关或环境开放情况。', page(SA, 9)),
    ('A06', 'DPP access-token JWKS', 'GET', JWKS, '令牌签名公钥集合地址；用于校验由授权服务签发的访问令牌，不用于上传服务账号的公钥。', '服务账号指南第 9 页', '本机匿名 GET 为 404 / application/json；需核实当前发布路由。', page(SA, 9)),
    ('A07', 'OAuth resource 标识', '标识值；不是请求接口', RESOURCE, '填写 Token 表单的 resource；不要换成 Registry API base。', '服务账号指南第 9、12 页', '非 API 地址，不把浏览器打开结果当接口测试。', page(SA, 12)),
    ('A08', '授权服务器 issuer', 'JWT audience 标识', ISSUER, 'client assertion 的 aud 必须精确等于此值；不要使用 Token URL，也不要使用 EU Login URL。', '服务账号指南第 9–10 页', '不是 Registry API base；aud 使用完整 HTTPS issuer，末尾不额外加斜杠。', page(SA, 10)),
]
sheet('测试地址', 'ACC 地址清单 · 每个地址分别做什么',
      '只列 ACC。Token、API、门户和 OAuth 标识用途不同；这里保留官方原文差异，没有替官方修正域名。',
      ['编号', '用途', '调用方式', '官方 ACC 地址', '怎样使用', '出处位置', '当前结果 / 注意事项', '官方原文'],
      address_rows, [9, 25, 22, 61, 54, 45, 59, 42])

api_rows = []
op_ids = {}
for spec, kind in [(REG, 'Registry'), (DASH, 'Dashboard')]:
    for path, entry in spec['paths'].items():
        for method, operation in entry.items():
            if method not in {'get', 'post'}:
                continue
            code = f'{"R" if kind == "Registry" else "D"}{1 + sum(r[1] == kind for r in api_rows):02}'
            op_ids[(method, path)] = code
            if 'product-groups' in path:
                name, args, expect = '查询产品分类', 'Authorization: Bearer；Accept: application/json', '200 + JSON 产品分类。使用返回的 code / productSubGroups，不自行猜分类。'
            elif 'submission' in path:
                name, args, expect = '提交护照注册', 'Authorization: Bearer；Content-Type: application/json；Idempotency-Key 必填；可选 X-Correlation-Id。JSON submissionItems 为 1–100 项，首测只传 1 项。', '202 + taskId：只表示受理异步任务；保存 taskId、correlationId 和 estimatedPeriodCompletionTime。'
            elif path.endswith('/items'):
                name, args, expect = '查询逐条处理结果', 'Authorization: Bearer；taskId；page=0、size=25（最大 100）；Accept 可为 application/json 或 text/csv。', '200 + 分页结果。检查逐条 outcomeMessage / outcomeDetails；CSV 也是当前页，需要翻页。'
            elif '{taskId}' in path:
                name, args, expect = '查询任务详情', 'Authorization: Bearer；taskId 使用注册返回值。', '200 + JSON。COMPLETED 后查看 outcome.outcomeResult；SUCCEEDED 才是业务成功，FAILED 是业务失败；ERROR 是技术处理失败。'
            else:
                name, args, expect = '查询注册任务列表', 'Authorization: Bearer；taskType=DPP_REGISTRATION；extended=true；page=0、size=25（最大 100）。', '200 + 分页列表；extended=true 才在任务行中返回可用的终态 outcome。'
            guide_url = REG_BASE + path.removeprefix('/api/v1') if kind == 'Registry' else '指南第 20 页仅命名 Registry API Base；Dashboard 实际 base 待核实。'
            api_rows.append((code, kind, name, method.upper(), path, SPEC_ORIGIN + path, guide_url, args, expect, OPENAPI))
sheet('接口清单', '五个业务接口 · 原文 URL 与成功标准',
      'OpenAPI 原文 URL 按 servers + path 生成；指南拼接 URL 仅列 Registry。Dashboard 路由需要向官方确认，不擅自套用 reg 域名。',
      ['编号', '所属服务', '用途', '方法', '原始 path', 'OpenAPI 原文 ACC 完整 URL', '服务账号指南对应地址', '请求要点', '怎样判断结果', '官方 OpenAPI 下载'],
      api_rows, [9, 15, 25, 10, 43, 63, 63, 62, 66, 42])

steps = [
    ('01', '确认 ACC 入口和实际 API 路由', '在浏览器打开 ACC 门户；先看“本机实测”。如 API 返回 session cookie、门户 HTML 或路由错误，向官方确认 Registry 和 Dashboard base URL 及网络/会话接入要求。', '不要把 200 HTML 或 401 当成带 Bearer 的接口调用通过。', '访问环境；研发', '当前域名差异和网关条件尚未解决。', REGISTRY_PAGE),
    ('02', '完成测试身份和组织核验', '官方页面要求使用单独的测试 EU Login。进入 ACC 门户完成组织入驻和验证，达到 VERIFIED。不要把生产环境已验证身份默认当成 ACC 已配置。', 'ACC 中可看到正确组织且状态为 VERIFIED。', 'EO 管理员', '需要组织测试账号和核验材料；本次未代办。', page(SA, 6)),
    ('03', '创建 Service Account 并授权', '由 EO Administrator 创建服务账号，授予 Register DPPs 角色；取得 Registry 生成的 Client ID。', '服务账号存在，角色允许注册 DPP。', 'EO 管理员', '共享 client_secret 不受支持，不能套普通 client secret 模式。', page(API, 7)),
    ('04', '登记公钥凭证', '在本机密钥库/HSM 生成 EC 密钥；支持 P-256/ES256、P-384/ES384、P-521/ES512。只登记公钥，记录 ACTIVE credential 的 kid。测试软件密钥可参考官方 OpenSSL 示例。', '有 ACTIVE 公钥凭证、对应 kid；本机安全持有匹配的私钥。', '研发；EO 管理员', 'Excel、Postman 导出文件和群聊里不放私钥、口令、assertion 或 access_token。', page(SA, 7)),
    ('05', '填写环境配置', '导入附带 Postman Collection 与 Environment；选择 ACC 环境。核实 registry_api_base 和 dashboard_api_base；填写 client_id、kid 等配置，暂不填写或保存真实令牌到分享文件。', '请求 URL 没有重复 /api/v1；所有 host 均属于 ACC。', '研发', 'scope 没有明确可用值时先不发送；填写任意 scope 不能获得额外权限。', page(SA, 9)),
    ('06', '签名 client assertion', '用与 kid 对应的私钥和 JOSE/JWT 库签名。“JWT与参数”有 header/claims。typ=client-authentication+jwt；iss=sub=client_id；aud=ACC issuer；exp 建议 iat+60–120 秒；每次使用新 jti。', '得到短期有效的签名 JWT，将其仅填入本机 Postman 的 client_assertion。', '研发', '不能用未签名的 header/payload 代替；每次 Token 请求都要重新签名，不能重用 assertion。', page(SA, 10)),
    ('07', '获取 Token', 'POST /oauth2/token，Body 使用 x-www-form-urlencoded。参数见“JWT与参数”或 Postman 的 01 请求。resource 用 ACC 的逻辑资源标识；scope 按已有授权选填。', '成功响应含 access_token、token_type=Bearer、expires_in。Postman 会保留在本机环境变量供后续请求使用。', '研发', 'Access Token 最长 15 分钟；有效期内复用，快到期再用新 assertion 申请。', page(SA, 11)),
    ('08', '先调用只读接口', 'GET {registry_api_base}/dpp/product-groups，携带 Authorization: Bearer {access_token}，Accept: application/json。从返回数据选择真实的 productGroup / productSubGroup。', '200 + 正确 JSON 产品分类；不是门户 HTML。先证明身份和 API 路由可用。', '研发', '若仍返回 missing session cookie，先核实网关；不能只凭匿名 401 判断补一个 Token 就一定可用。', page(API, 7)),
    ('09', '准备一条可访问的测试护照', '将测试 DPP 部署到欧委会校验端可解析和读取的 HTTPS UPI。按产品/子组采用官方 JSON Schema、上下文和语义资源；如保护 DPP 访问，按 Self-Hosted Authentication Guide 配置。', 'UPI 解析到正确护照资源，数据结构与分类对应；Registry 校验端能实际获取内容。', 'DPP 服务研发', 'example.com 仅为文档示意，localhost 不能作为有效远程测试地址。登记请求本身不是完整护照内容。', page(API, 6)),
    ('10', '提交单条注册', 'POST {registry_api_base}/dpp/submission，JSON 首测只传 1 条 submissionItems。携带 Bearer、Content-Type 和 Idempotency-Key；productGroup / productSubGroup 采用上一步分类值，UPI 替换为实际测试资源。', '202 响应返回 taskId。记录 correlationId 和建议等待时间；此时登记尚未判定成功。', '研发', '同一逻辑请求重试沿用相同幂等键；修改请求或发起新请求要换新键。', page(API, 9)),
    ('11', '查询异步任务', '先按 estimatedPeriodCompletionTime 等待，再 GET {dashboard_api_base}/tasks/{taskId}。PENDING/PROCESSING 时按 1、1、2、3、5、8…秒退避，上限 60 秒；Retry-After 优先。', '任务达到 COMPLETED 或 ERROR；COMPLETED 时读取 outcome.outcomeResult。', '研发', 'Dashboard base 需核实；原始 OpenAPI 的 ACC host 为 registry.acc，不能默认为 Registry 的 reg.acc。', page(API, 10)),
    ('12', '检查业务结果和逐条证据', 'COMPLETED + SUCCEEDED 才是正向业务结果；若 FAILED，读取 {taskId}/items 并核对非合规条目。ERROR 按技术故障处理。CSV 导出也按 page/size 翻页。', '确认总结果、submittedItems/compliantItems/nonCompliantItems 和逐条证据一致；保留 taskId 与 correlationId 排障。', '研发；测试', '注册是原子处理；一条不合规则整个提交不注册。COMPLETED 不代表全部注册成功。', page(API, 13)),
]
sheet('测试步骤', '从账号准备到一次注册验证',
      '建议先打通 Token + GET 分类，再做单条提交。以下是依据官方合同整理的操作步骤，本次未实际申请账号或执行注册。',
      ['顺序', '要做什么', '具体操作', '预期结果 / 检查点', '由谁准备', '当前限制 / 提醒', '官方出处'],
      steps, [9, 31, 100, 64, 22, 67, 42])

params = [
    ('Postman 环境', 'registry_api_base', '接口 base', REG_BASE, '来自服务账号指南第 20 页；当前仍有网关访问限制，实际部署待确认。', page(SA, 20)),
    ('Postman 环境', 'dashboard_api_base', '接口 base', DASH_BASE, '来自 Dashboard 原始 OpenAPI server + /api/v1；域名差异待核实。', OPENAPI),
    ('签名 header', 'typ', '必填', 'client-authentication+jwt', '准确匹配官方要求。', page(SA, 9)),
    ('签名 header', 'alg', '必填', 'ES256 / ES384 / ES512', '必须匹配已登记公钥曲线，不是任选一个。', page(SA, 7)),
    ('签名 header', 'kid', '必填', '<ACTIVE credential 的 kid>', '从 ACC 组织的服务账号凭证获取，不猜测。', page(SA, 8)),
    ('JWT claims', 'iss、sub', '必填', '<Registry 生成的 Client ID>', '两者均精确等于 client_id。不是组织 ID。', page(SA, 10)),
    ('JWT claims', 'aud', '必填', ISSUER, '是 ACC 授权服务器 issuer；不是 token endpoint，也不是 API base。', page(SA, 10)),
    ('JWT claims', 'iat、exp', '必填', '当前 Unix 秒；exp=iat+60–120 秒', '最大有效窗口 5 分钟；本机时钟同步。JSON 中用数值。', page(SA, 10)),
    ('JWT claims', 'jti', '必填', '<每次新生成的唯一值，例如 UUID>', '每次申请 Token 都用新 assertion 和新 jti；旧 assertion 不重放。', page(SA, 10)),
    ('Token 表单', 'grant_type', '必填', 'client_credentials', 'Body 编码：application/x-www-form-urlencoded。', page(SA, 10)),
    ('Token 表单', 'client_id', '必填', '<Registry 生成的 Client ID>', '与签名 claims 的 iss/sub 相同。', page(SA, 10)),
    ('Token 表单', 'client_assertion_type', '必填', 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer', '按原文填写，不用 client_secret 参数替代。', page(SA, 10)),
    ('Token 表单', 'client_assertion', '必填', '<新签名的短期 JWT>', 'JOSE/JWT 库签名；只在本机临时使用，不贴进 Excel 或共享材料。', page(SA, 10)),
    ('Token 表单', 'resource', '必填', RESOURCE, '逻辑 OAuth 标识，不作为普通 GET API 测试地址。', page(SA, 12)),
    ('Token 表单', 'scope', '按授权选填', '<服务账号已允许的 resource-specific scope>', '官方未在这里给通用固定 scope。不要编造；Postman 默认不发送本项。', page(SA, 11)),
    ('API header', 'Authorization', '受保护接口', 'Bearer <access_token>', '有效期内复用 access token；不是直接发送 client assertion。', page(SA, 12)),
    ('提交 header', 'Idempotency-Key', '必填', '<一次逻辑请求的唯一值，最长 128 字符>', '同逻辑重试保持；新请求或变更请求换新值。Postman 首次为空时生成一个 UUID，后续复用。', page(API, 9)),
    ('提交 JSON', 'productGroup / productSubGroup', '电池必填', '例如 BATTERIES / EV（以 GET 分类实际返回为准）', '同一次 submission 的分类一致；不要拿语义资源的目录名直接替代接口 code。', page(API, 7)),
    ('提交 JSON', 'submissionItems', '必填', '1–100 条；每条 uniqueProductIdentifier、granularity 必填', 'granularity 是 item/batch/model；batchIdentifier/modelIdentifier 按粒度规则填写。首测用一条 item。', OPENAPI),
    ('任务查询', 'taskId / page / size', '路径或查询', 'taskId 来自 202；page 从 0 开始，size 默认 25、最大 100', '逐条 CSV 是同一分页结果的替代表示，仍需翻页。', page(API, 14)),
]
sheet('JWT与参数', '认证与请求参数 · 避免填错环境或凭证',
      '本表所有身份和敏感值均为说明占位；实际值由你们的 ACC 测试账号和密钥库提供，不需要交给我。',
      ['配置位置', '参数', '是否必填', '填写值 / 格式', '说明', '官方出处'],
      params, [20, 35, 23, 77, 86, 42])

submission = REG['paths']['/api/v1/dpp/submission']['post']['requestBody']['content']['application/json']['example']
request_body = json.loads(json.dumps(submission))
request_body['productGroup'] = '{{product_group}}'
request_body['productSubGroup'] = '{{product_subgroup}}'
request_body['submissionItems'][0]['uniqueProductIdentifier'] = '{{unique_product_identifier}}'
request_body['submissionItems'][0]['batchIdentifier'] = '{{batch_identifier}}'
request_body['submissionItems'][0]['modelIdentifier'] = '{{model_identifier}}'
json_body = json.dumps(request_body, ensure_ascii=False, indent=2)

curl_config = '''# 使用 Bash。占位值须替换，敏感值只在本机临时配置。
export DPP_REGISTRY_BASE_URL='https://reg.acc.product-passport.ec.europa.eu/api/v1'
export DPP_DASHBOARD_BASE_URL='https://registry.acc.product-passport.ec.europa.eu/api/v1'
export DPP_CLIENT_ID='<实际 Client ID>'
# 在本机安全取得这些变量：DPP_CLIENT_ASSERTION、DPP_ACCESS_TOKEN
# DPP_IDEMPOTENCY_KEY 为一次逻辑提交的 UUID；同一请求重试不要更换。
# DPP_TASK_ID 从提交响应 taskId 取得。
# 两个 base URL 都须先核对实际 ACC 路由。'''
curl_token = '''curl --include --request POST \\
  'https://auth.acc.product-passport.ec.europa.eu/oauth2/token' \\
  --header 'Content-Type: application/x-www-form-urlencoded' \\
  --data-urlencode 'grant_type=client_credentials' \\
  --data-urlencode "client_id=$DPP_CLIENT_ID" \\
  --data-urlencode 'client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer' \\
  --data-urlencode "client_assertion=$DPP_CLIENT_ASSERTION" \\
  --data-urlencode 'resource=https://auth.acc.product-passport.ec.europa.eu/resources/dpp/registry'
# 如需 scope，按服务账号实际授权增加；不猜 scope。'''
curl_groups = '''curl --include "$DPP_REGISTRY_BASE_URL/dpp/product-groups" \\
  --header "Authorization: Bearer $DPP_ACCESS_TOKEN" \\
  --header 'Accept: application/json' '''
curl_submit = '''curl --include --request POST "$DPP_REGISTRY_BASE_URL/dpp/submission" \\
  --header "Authorization: Bearer $DPP_ACCESS_TOKEN" \\
  --header 'Content-Type: application/json' \\
  --header "Idempotency-Key: $DPP_IDEMPOTENCY_KEY" \\
  --data-binary @submission.json
# submission.json 中的 UPI 和分类须替换为真实测试值。'''
curl_task = '''curl --include "$DPP_DASHBOARD_BASE_URL/tasks/$DPP_TASK_ID" \\
  --header "Authorization: Bearer $DPP_ACCESS_TOKEN" \\
  --header 'Accept: application/json' '''
curl_items = '''curl --include "$DPP_DASHBOARD_BASE_URL/tasks/$DPP_TASK_ID/items?page=0&size=25" \\
  --header "Authorization: Bearer $DPP_ACCESS_TOKEN" \\
  --header 'Accept: application/json'
# 导出 CSV 时改 Accept 为 text/csv，并逐页拉取。'''
header_example = json.dumps({'typ': 'client-authentication+jwt', 'alg': 'ES256', 'kid': '<ACTIVE credential 的 kid>'}, ensure_ascii=False, indent=2)
claims_example = '''{
  "iss": "<clientId>",
  "sub": "<clientId>",
  "aud": "https://auth.acc.product-passport.ec.europa.eu",
  "iat": <当前 Unix 秒，数值>,
  "exp": <iat + 120，数值>,
  "jti": "<本次新生成的 UUID>"
}'''
example_rows = [
    ('00', '配置环境（Bash）', curl_config, '两个 API base URL 均有待官方确认，示例仅按各自原文保留。不要把真实私钥或令牌写入分享文件。', page(SA, 20)),
    ('01A', 'client assertion header', header_example, '这里只展示结构；alg 必须对应已注册曲线。kid 使用实际 ACTIVE 凭证值。', page(SA, 9)),
    ('01B', 'client assertion claims（结构示意）', claims_example, '尖括号是说明占位，不能当有效 JSON/JWT 发送。用 JOSE/JWT 库将 header+claims 与对应私钥签名，得到 client_assertion。', page(SA, 10)),
    ('01C', '签名实现参考', '官方服务账号指南：Java / Nimbus 示例在第 13 页；TypeScript / jose 示例在第 14 页。\n把生产示例中的 issuer 换成 ACC issuer，并从本机安全密钥库加载匹配私钥。', '不手写 ECDSA JWT 编码；本包不附密钥或可复用凭证。', page(SA, 13)),
    ('02', '申请 Token', curl_token, '获取新签名 assertion 后立即执行。成功响应中的 access_token 留在本机后续使用；不要复制整个响应到群里。', page(SA, 10)),
    ('03', '查询产品分类', curl_groups, '预期 200 + application/json。HTML、缺少 session cookie 都需要先核对路由和访问条件。', page(API, 7)),
    ('04A', 'submission.json（可替换模板）', json_body, '分类与 UPI 都是占位。granularity=item，batch/model 采用测试标识；按适用规则处理可选字段。', page(API, 9)),
    ('04B', '提交单条注册', curl_submit, '预期 202，保存 taskId；同一逻辑请求重试沿用同一个 Idempotency-Key。', page(API, 9)),
    ('05', '查询任务详情', curl_task, '先按响应建议等待；PENDING/PROCESSING 退避查询；COMPLETED 后还要检查 outcome.outcomeResult。', page(API, 10)),
    ('06', '查询逐条结果 / CSV', curl_items, 'JSON 和 CSV 都按分页规则处理。DPP 注册结果需要同时核对任务总结果与逐条证据。', page(API, 14)),
    ('07', 'Postman 操作', 'Import 两个 JSON → 选择 ACC 环境 → 核实 API base → 填 client_id 和本次 client_assertion → 依编号执行。\n成功获取 Token、提交注册后，Collection 会分别写入本机的 access_token、task_id。\n同一提交重试保留 idempotency_key；修改请求/发起新提交前清空或更换。', 'scope 默认禁用；只有掌握真实可用 scope 时才启用。不要直接 Run 整个 Collection，任务需要等待异步完成。', OPENAPI),
]
sheet('请求示例', '可复制的请求模板 · curl 与 Postman',
      '请求示例是根据官方合同整理的模板，必须替换测试身份、UPI 等配置。没有真实 Token 时无法完成受保护接口验证。',
      ['顺序', '用途', '示例 / 操作', '执行前与响应检查', '官方依据'],
      example_rows, [10, 33, 118, 71, 42], code_col=3)

observations = []
for check in CHECKS['结果']:
    code = check.get('状态码')
    name = check['名称']
    if name == '测试管理门户':
        observed, interpretation = '网页标题：' + check.get('页面标题', ''), '门户网页可响应；未登录、未核验组织。'
    elif name == '指南中的 API 域名':
        observed, interpretation = check.get('正文摘要', ''), '响应涉及 session cookie，可能来自网关或会话层；不能直接认定是标准 Bearer 鉴权失败。需官方核实机器调用接入条件。'
    elif name == 'OpenAPI 中的 API 域名':
        observed, interpretation = '返回门户 HTML；标题：' + check.get('页面标题', ''), '未取得产品分类 JSON；HTTP 200 本身不表示业务接口可用。'
    elif name == '访问令牌公钥 JWKS':
        observed, interpretation = 'JSON 顶层字段：' + ', '.join(check.get('JSON顶层字段', [])), '未取得预期 JWKS；需确认当前环境 /oauth2/jwks 路由与开放条件。'
    else:
        observed, interpretation = check.get('正文摘要', '') or '未取得 JSON 元数据；正文为空。', '本机匿名访问失败；不能据此确认 Token 端点实际可用情况。'
    observations.append((name, check['时间'], check['方法'], check['地址'], code if code else check.get('异常类型', ''), check.get('Content-Type', ''), observed, interpretation))
observations.append(('Token 与注册联调', STAMP, '未执行', TOKEN, '未实测', '—', '本次没有有效服务账号、签名 assertion 和 access_token，未 POST Token 或注册请求。', '未完成带账号的端到端验证；本表不把匿名检查称为联调成功。'))
sheet('本机实测', '2026-10-31 本机检查 · 如实记录响应',
      '北京时间。只有 GET 匿名读取；没有访问令牌、没有登录 Cookie、没有绕过网关，也没有注册写操作。HTTP 响应可能来自网关。',
      ['检查对象', '时间（北京时间）', '方法', '请求地址', 'HTTP / 状态', 'Content-Type', '观察到的内容', '能说明什么'],
      observations, [26, 29, 13, 70, 17, 23, 62, 74])

issues = [
    ('地址待核实', '指南 reg.acc 与 OpenAPI registry.acc 不一致', '请官方确认 Registry API 和 Activity Dashboard API 当前 ACC base URL；是否是版本、网关或文档差异。不要通过替换所有域名来假装已验证。', '当前原文差异 + 本机两个产品分类 URL 的不同响应。', REGISTRY_PAGE),
    ('访问待核实', 'Unauthorized: missing session cookie', '提供发生时间、脱敏请求 URL 和响应正文给 Help Desk，询问面向 Service Account 的机器调用是否有额外网络或会话前置条件。不要复制浏览器 Cookie 冒充正式机器鉴权方案。', 'reg.acc 的 GET 分类匿名响应为 401；不是完整 Bearer 联调结论。', REGISTRY_PAGE),
    ('路由待核实', 'metadata 401；JWKS 404', '请确认 ACC 授权服务的元数据和 JWKS 路由是否已开放，以及机器接入是否受环境限制。Token 端点还未实测，不能由此推断其已成功或失败。', '本机 GET；详见“本机实测”。', page(SA, 9)),
    ('Token 报错', 'invalid_request', '检查 Content-Type、必填表单项、client_assertion_type，以及只发送正确的一个 resource。', '服务账号指南第 18 页。', page(SA, 18)),
    ('Token 报错', 'invalid_client', '检查 client_id=iss=sub；typ；ACTIVE kid；alg/曲线；aud=ACC issuer；签名；iat/exp；新 jti；组织/服务账号资格。每次重试生成新 assertion。', '服务账号指南第 18 页。', page(SA, 18)),
    ('Token 报错', 'invalid_scope / invalid_target', 'scope 只能申请已允许的权限；resource 必须是 ACC registry resource 标识，不能写成 API base。', '服务账号指南第 18 页。', page(SA, 18)),
    ('API 报错', '401 / 403', '拿到 Token 后检查有效期、audience、权限和组织 VERIFIED；同时看响应正文、Content-Type，区分 API 的鉴权错误与网关 session 错误。', '服务账号指南第 18 页；API 指南第 11 页。', page(SA, 18)),
    ('提交报错', '400 / 409 / 413 / 422', '400 改正请求；409 查看当前状态/幂等冲突；413 最大 10 MB；422 区分数据无效与临时校验不可用。修改逻辑请求换新幂等键；相同请求重试沿用原键。', 'API 指南第 11 页。', page(API, 11)),
    ('频率 / 暂时故障', '429 / 503 / 技术故障', '尊重 Retry-After 并有限退避；有效 Token 复用；Token 重试每次重新签名 assertion；同一提交重试复用 Idempotency-Key。', '服务账号指南第 18 页；API 指南第 10–11 页。', page(SA, 18)),
    ('业务失败', 'UPI_NOT_RESOLVABLE / 非合规', '检查 Registry 是否能从实际 UPI 获取护照；采用对应产品/子组语义资源并检查受限访问配置。查看 {taskId}/items 的 outcomeMessage 和 outcomeDetails。', 'API 指南第 6、13–15 页。', page(API, 15)),
    ('成功判断', '202 / COMPLETED', '202 是异步受理；COMPLETED 是处理结束；还需 outcome.outcomeResult=SUCCEEDED。FAILED 时整次提交不注册，不能按 compliantItems 计算部分成功。', 'API 指南第 9–10、13 页。', page(API, 13)),
    ('官方支持', 'Help Desk', '邮箱 EC-HELPDESK-DPP@ec.europa.eu；电话 +32 2 296 04 31；官方页面列示服务时间 08:00–20:00 CET。仅列联系方式，本次未联系。', 'Registry 官方网页 Help Desk；测试入口也在该页。', REGISTRY_PAGE),
    ('可复制给官方的问题', 'ACC API 接入与文档一致性', 'We are integrating the APIs published on 30 October 2026. Could you confirm the ACC base URLs for both Registry and Activity Dashboard APIs? The Service Account Guide lists reg.acc, while both OpenAPI files list registry.acc. An anonymous GET to reg.acc /api/v1/dpp/product-groups returned 401 with "Unauthorized: missing session cookie"; the registry.acc path returned the portal HTML. The documented metadata endpoint returned 401 and /oauth2/jwks returned 404. Are additional onboarding, network or session prerequisites required for private_key_jwt machine-to-machine access? We have not yet performed an authenticated token or registration test.', '询问模板；发出前补充自己账号情况及复现时间，附脱敏响应，不附私钥、Cookie、assertion 或 Token。', REGISTRY_PAGE),
]
sheet('报错与核实', '当前需要核实的事项与排障顺序',
      '文档已公布不等于本机已完成联调。先核对实际部署和访问条件，再定位身份、数据或异步处理问题。',
      ['类别', '现象 / 错误', '如何处理 / 可复制内容', '依据或当前证据', '出处 / 官方支持'],
      issues, [22, 41, 126, 67, 42])

source_rows = [
    ('S01', '欧委会 DG GROW 官方资源发布页', '2026-10-30（六份技术资源条目）', '确认 API 指南、OpenAPI、语义资源及认证指南的官方来源和发布日期。', PUBLICATION, '网页；2026-10-31 复核'),
    ('S02', 'EU Digital Product Passport FAQ for Batteries', '动态网页；不推定发布日期', 'FAQ 1.2 给测试环境和 Help Desk 支持入口。', FAQ, '网页；2026-10-31 复核'),
    ('S03', 'The DPP Registry', '动态网页；不推定发布日期', 'Testing Environment：单独的测试 EU Login；Help Desk 联系方式。', REGISTRY_PAGE, '网页；2026-10-31 复核'),
]
for index, title in enumerate(['Service Account Technical Integration Guide', 'API Integration Guide', 'OpenAPI Specifications', 'Semantic Data Guide', 'Semantic Data Artifacts', 'Self-Hosted Authentication Guide'], 4):
    f = DOCS[title]
    file_path = ROOT / f['localPath']
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    assert digest == f['sha256'], f'Original source changed: {file_path}'
    source_rows.append((f'S{index:02}', f['filename'], '2026-10-30', {
        'Service Account Technical Integration Guide': '第 6 页前置条件；第 7–8 页密钥；第 9–12 页认证；第 18 页排障；第 20 页环境地址。',
        'API Integration Guide': '第 6–8 页访问条件；第 9–11 页注册；第 12–15 页任务和业务结果。',
        'OpenAPI Specifications': '三个 YAML：Registry 两个 operation；Dashboard 三个 operation；Common 为共用定义。ACC servers 均为 registry.acc。',
        'Semantic Data Guide': '护照内容的语义结构、校验资源和互操作说明。',
        'Semantic Data Artifacts': '部署测试护照时使用的 schemas、contexts、shapes、词表和示例。',
        'Self-Hosted Authentication Guide': 'DPP 服务由 EO/供应商托管时的令牌校验、JWKS 和授权配置。',
    }[title], f['url'], digest))
sheet('官方来源', '官方出处与原文校验',
      '页码使用 PDF 文件页码。官方文件保持原样；本 Excel 和 Postman 均是为阅读、测试整理的派生材料。',
      ['编号', '官方文件 / 网页', '发布日期', '本表引用位置 / 用途', '官方链接', 'SHA-256 / 网页核验'],
      source_rows, [9, 68, 35, 86, 79, 74])

wb.properties.creator = 'Futris 工作整理'
wb.properties.title = '欧委会 DPP OpenAPI ACC 测试地址与操作指南'
wb.properties.subject = '2026-10-30 official sources; ACC endpoints, authentication and testing workflow'
wb.properties.description = '官方资料阅读整理版；包含未解决的域名差异与匿名连通性证据；无真实凭证。'
wb.active = 0
wb.save(OUTPUT)

# A manually run, local-only Postman collection. No real credentials, signing
# keys, tokens, or environment cookies are included.
def js_event(kind, source):
    return {'listen': kind, 'script': {'type': 'text/javascript', 'exec': source.strip().splitlines()}}


def req(name, method, url, *, headers=None, auth=True, body=None, description='', events=None):
    result = {'name': name, 'request': {
        'method': method,
        'header': [{'key': k, 'value': v} for k, v in (headers or {}).items()],
        'url': url,
        'description': description,
    }}
    if not auth:
        result['request']['auth'] = {'type': 'noauth'}
    if body:
        result['request']['body'] = body
    if events:
        result['event'] = events
    return result


require_token = js_event('prerequest', '''
if (!pm.environment.get('access_token')) {
  throw new Error('先成功获取 access_token；匿名检查不等于鉴权联调。');
}
''')
json_test = js_event('test', '''
pm.test('返回 JSON，而非门户 HTML', function () {
  pm.expect(pm.response.headers.get('Content-Type') || '').to.include('application/json');
  pm.response.json();
});
''')
token_request = req('01 获取 Token（每次使用新签名 assertion）', 'POST', TOKEN, auth=False,
    headers={'Content-Type': 'application/x-www-form-urlencoded'},
    body={'mode': 'urlencoded', 'urlencoded': [
        {'key': 'grant_type', 'value': 'client_credentials', 'type': 'text'},
        {'key': 'client_id', 'value': '{{client_id}}', 'type': 'text'},
        {'key': 'client_assertion_type', 'value': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer', 'type': 'text'},
        {'key': 'client_assertion', 'value': '{{client_assertion}}', 'type': 'text'},
        {'key': 'resource', 'value': RESOURCE, 'type': 'text'},
        {'key': 'scope', 'value': '{{scope}}', 'type': 'text', 'disabled': True, 'description': '按已有授权选填；不猜 scope。'},
    ]},
    description='私钥在本机 JOSE/JWT 库/HSM 中签名。不要把真实 assertion/token 导出到分享文件。此请求是否在当前网关可用仍需身份联调验证。',
    events=[js_event('prerequest', '''
pm.environment.unset('access_token');
if (!pm.environment.get('client_id') || !pm.environment.get('client_assertion')) {
  throw new Error('请在本机填写真实 client_id 和本次新签名的 client_assertion。');
}
'''), js_event('test', '''
pm.test('Token 返回 200 JSON', function () {
  pm.response.to.have.status(200);
  pm.expect(pm.response.headers.get('Content-Type') || '').to.include('application/json');
});
if (pm.response.code === 200) {
  const data = pm.response.json();
  pm.test('含 Bearer access_token', function () {
    pm.expect(data.access_token).to.be.a('string').and.not.empty;
    pm.expect(String(data.token_type).toLowerCase()).to.eql('bearer');
  });
  if (data.access_token && String(data.token_type).toLowerCase() === 'bearer') {
    pm.environment.set('access_token', data.access_token);
  }
}
pm.environment.unset('client_assertion');
''')])
submit_request = req('03 提交一条测试注册（实际写入 ACC）', 'POST', '{{registry_api_base}}/dpp/submission',
    headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'Idempotency-Key': '{{idempotency_key}}'},
    body={'mode': 'raw', 'raw': json_body, 'options': {'raw': {'language': 'json'}}},
    description='先用 GET 产品分类核对分类，部署 Registry 可访问的真实测试 UPI。首次幂等键为空会自动生成；相同逻辑提交重试保留，新/变更请求要清空或更换。202 仅为受理。',
    events=[require_token, js_event('prerequest', r'''
const upi = pm.environment.get('unique_product_identifier') || '';
if (!upi.startsWith('https://') || /^https:\/\/(localhost|127\.0\.0\.1|example\.(com|invalid))(\/|:|$)/i.test(upi)) {
  throw new Error('先填写 Registry 可以实际访问的 HTTPS 测试护照 UPI。');
}
if (!pm.environment.get('product_group') || !pm.environment.get('product_subgroup')) {
  throw new Error('先根据 GET 分类结果填写 product_group / product_subgroup。');
}
if (!pm.environment.get('idempotency_key')) {
  pm.environment.set('idempotency_key', pm.variables.replaceIn('{{$guid}}'));
}
if (pm.environment.get('idempotency_key').length > 128) {
  throw new Error('Idempotency-Key 最长 128 字符。');
}
pm.environment.unset('task_id');
'''), js_event('test', '''
pm.test('202 仅表示异步受理', function () {
  pm.response.to.have.status(202);
});
if (pm.response.code === 202) {
  const data = pm.response.json();
  pm.test('返回 taskId', function () { pm.expect(data.taskId).to.be.a('string').and.not.empty; });
  if (data.taskId) pm.environment.set('task_id', data.taskId);
  if (data.correlationId) pm.environment.set('correlation_id', data.correlationId);
  if (data.estimatedPeriodCompletionTime) pm.environment.set('estimated_period', data.estimatedPeriodCompletionTime);
}
''')])
task_guard = js_event('prerequest', '''
if (!pm.environment.get('task_id')) throw new Error('先取得真实 task_id。');
''')
task_request = req('04 查询任务详情（先等待，按结果退避）', 'GET', '{{dashboard_api_base}}/tasks/{{task_id}}',
    headers={'Accept': 'application/json'},
    description='Dashboard host 来自原始 OpenAPI，需确认实际路由。PENDING/PROCESSING 继续等待；COMPLETED 后检查 outcome.outcomeResult=SUCCEEDED；ERROR 为技术失败。',
    events=[require_token, task_guard, json_test, js_event('test', '''
pm.test('任务查询返回 200', function () { pm.response.to.have.status(200); });
if (pm.response.code === 200 && (pm.response.headers.get('Content-Type') || '').includes('application/json')) {
  const data = pm.response.json();
  pm.test('任务状态合法', function () {
    pm.expect(['PENDING', 'PROCESSING', 'COMPLETED', 'ERROR']).to.include(data.status);
  });
  if (data.status === 'COMPLETED') {
    pm.test('注册业务成功：outcomeResult 为 SUCCEEDED', function () {
      pm.expect(data.outcome && data.outcome.outcomeResult).to.eql('SUCCEEDED');
    });
  } else if (data.status === 'ERROR') {
    pm.test('任务未发生技术失败', function () { throw new Error('任务为 ERROR，请检查逐条证据与 correlationId。'); });
  }
}
''')])
collection = {
    'info': {'_postman_id': str(uuid.uuid4()), 'name': '欧委会 DPP ACC · 20261030 OpenAPI 测试模板',
             'schema': 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json',
             'description': '工作整理版，官方出处见配套 Excel。Registry base 取服务账号指南 reg.acc；Dashboard base 暂保留原始 OpenAPI registry.acc，两者均有路由/访问条件待核实。无真实凭证。逐个手动执行，不直接 Run 全部。'},
    'auth': {'type': 'bearer', 'bearer': [{'key': 'token', 'value': '{{access_token}}', 'type': 'string'}]},
    'item': [
        {'name': '00 匿名查看（当前可能受环境限制）', 'item': [
            req('授权服务器元数据', 'GET', METADATA, auth=False),
            req('访问令牌 JWKS', 'GET', JWKS, auth=False),
        ]},
        token_request,
        req('02 查询产品分类（先验证 Token 与路由）', 'GET', '{{registry_api_base}}/dpp/product-groups',
            headers={'Accept': 'application/json'}, events=[require_token, json_test, js_event('test', "pm.test('分类返回 200', function () { pm.response.to.have.status(200); });")]),
        submit_request,
        task_request,
        req('05 查询逐条结果（JSON）', 'GET', '{{dashboard_api_base}}/tasks/{{task_id}}/items?page=0&size=25',
            headers={'Accept': 'application/json'}, events=[require_token, task_guard, json_test]),
        req('06 查询逐条结果（CSV 当前页）', 'GET', '{{dashboard_api_base}}/tasks/{{task_id}}/items?page=0&size=25',
            headers={'Accept': 'text/csv'}, description='仍为分页，需手工递增 page。', events=[require_token, task_guard]),
        req('07 列出注册任务', 'GET', '{{dashboard_api_base}}/tasks?taskType=DPP_REGISTRATION&extended=true&page=0&size=25',
            headers={'Accept': 'application/json'}, events=[require_token, json_test]),
    ],
}
values = {
    'registry_api_base': REG_BASE,
    'dashboard_api_base': DASH_BASE,
    'client_id': '', 'kid': '', 'algorithm': 'ES256', 'scope': '',
    'client_assertion': '', 'access_token': '',
    'product_group': 'BATTERIES', 'product_subgroup': 'EV',
    'unique_product_identifier': '', 'batch_identifier': 'TEST-BATCH-001',
    'model_identifier': 'TEST-MODEL-001', 'idempotency_key': '', 'task_id': '',
    'correlation_id': '', 'estimated_period': '',
}
environment = {
    'id': str(uuid.uuid4()), 'name': 'DPP ACC（地址需核实；凭证仅在本机填写）',
    'values': [{'key': k, 'value': v, 'type': 'secret' if k in {'client_assertion', 'access_token'} else 'default', 'enabled': True} for k, v in values.items()],
    '_postman_variable_scope': 'environment', '_postman_exported_using': 'Futris DPP sourced working template',
}
for name, value in [('DPP_ACC.postman_collection.json', collection), ('DPP_ACC.postman_environment.json', environment)]:
    (KIT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
(KIT / 'submission_占位模板.json').write_text(json_body + '\n')
(KIT / 'ACC_匿名连通性检查.json').write_text(json.dumps(CHECKS, ensure_ascii=False, indent=2) + '\n')
readme = f'''欧委会 DPP ACC 测试附件（资料日期 2026-10-30）
整理时间：{STAMP}

配合《{OUTPUT.name}》使用。附件是整理模板，不是欧委会原始发布文件。

先了解当前情况：
1. 官方文档提供 ACC 地址，但本次没有有效服务账号，未申请 Token、未提交注册。
2. 指南的 reg.acc 分类接口返回 401，正文为 Unauthorized: missing session cookie。
3. 原始 OpenAPI 的 registry.acc 分类接口返回门户 HTML；metadata 返回 401，JWKS 返回 404。
4. 请先向官方确认 Registry/Dashboard 实际 base 以及机器接入条件；不要把匿名响应当成联调成功。

准备：
- 单独的测试 EU Login、ACC VERIFIED 组织、带 Register DPPs 角色的 Service Account。
- ACTIVE 公钥凭证、Client ID、kid，匹配私钥保留在本机密钥库/HSM。
- 支持对应算法的 JOSE/JWT 库；服务账号指南第 13–14 页有 Java/TypeScript 签名示例。
- 公钥、私钥、访问令牌的用途不同；只上传公钥，不能把私钥放进 Postman 或本分享包。

Postman 操作：
1. Import Collection 和 Environment 两个 JSON，选择 ACC 环境。
2. 核实 registry_api_base / dashboard_api_base。当前分别保留指南和 Dashboard OpenAPI 的原文值。
3. 填 client_id。kid、algorithm 用于你们本机的 JWT 签名器；在环境中填写它们不会自动产生签名。
4. 生成新 client assertion：typ=client-authentication+jwt；iss=sub=client_id；aud={ISSUER}；
   iat=当前 Unix 秒；exp=iat+60–120；jti 每次唯一；用对应私钥签名。具体见 Excel。
5. 仅在本机填 client_assertion，立即发送 01 获取 Token。scope 默认禁用，只在已知授权值时开启。
   成功时脚本在本机环境保存 access_token；请求后移除 client_assertion，下一次申请须重新签名。
6. 发送 02 GET 分类。须收到 200 JSON；HTML 不是成功。按实际返回填写 product_group / product_subgroup。
7. 部署校验端能够访问的测试护照，并填写 unique_product_identifier。示例中的 example.com/localhost 不能用于有效注册。
8. 发送 03 单条提交。首次幂等键为空时会生成一个 UUID；相同请求重试沿用；修改逻辑请求前清空或换新。
9. 202 只表示受理，脚本保存 task_id。按 estimatedPeriodCompletionTime 等待，再手工执行 04。
   若还在 PENDING/PROCESSING，按 1、1、2、3、5、8…秒退避，上限 60 秒，Retry-After 优先。
10. COMPLETED 还需 outcome.outcomeResult=SUCCEEDED。FAILED 或 ERROR 时读 05 逐条证据。
    CSV 只是当前页；需要翻页。不要直接 Run 全部请求来替代等待异步任务。

补充：
- Collection 没有自动签 JWT；header/payload 的示例文本不是有效的签名凭证。
- 不在分享材料、日志或群聊中保存真实 assertion、Token、Cookie、私钥和口令。
- 有效 access_token 在到期前复用，最长寿命 15 分钟；新 Token 请求每次用新 assertion。
- Excel 中提供 curl 请求、来源页码、官方支持方式和可复制询问模板。

官方出处：
发布页：{PUBLICATION}
服务账号指南：{SA}
API 指南：{API}
原始 OpenAPI：{OPENAPI}
官方技术支持：{REGISTRY_PAGE}
'''
(KIT / '先读我_如何测试.txt').write_text(readme)

# Verify the deliverable is readable and that its endpoint inventory matches
# the originals. These checks validate artifacts, not remote API functionality.
book = load_workbook(OUTPUT)
assert book.sheetnames == ['使用说明', '测试地址', '接口清单', '测试步骤', 'JWT与参数', '请求示例', '本机实测', '报错与核实', '官方来源']
assert len(api_rows) == 5
assert {(r[3].lower(), r[4]) for r in api_rows} == {
    (m, p) for spec in (REG, DASH) for p, operations in spec['paths'].items() for m in operations if m in {'get', 'post'}
}
assert all(c.data_type != 'f' for ws in book for row in ws for c in row)
for ws in book:
    for row in ws:
        for cell in row:
            assert not isinstance(cell.value, str) or len(cell.value) < 32767
            if cell.hyperlink and cell.hyperlink.target.startswith('#'):
                target = cell.hyperlink.target[1:].split('!')[0].strip("'")
                assert target in book.sheetnames
for f in KIT.glob('*.json'):
    json.loads(f.read_text())
assert not values['client_assertion'] and not values['access_token'] and not values['unique_product_identifier']
source_text = subprocess.check_output(['pdftotext', '-raw', str(ROOT / DOCS['Service Account Technical Integration Guide']['localPath']), '-'], text=True)
normalized = re.sub(r'\s+', '', source_text).replace('\u00ad', '')
for endpoint in [REG_BASE, TOKEN, METADATA, JWKS, RESOURCE, ISSUER]:
    assert endpoint in normalized, f'Endpoint missing from source: {endpoint}'

zip_path = OUT / '欧委会DPP_ACC_Postman测试附件_20261030.zip'
with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for f in sorted(KIT.iterdir()):
        archive.write(f, f.name)
with zipfile.ZipFile(zip_path) as archive:
    assert archive.testzip() is None

report = {
    '生成时间': STAMP, '资料发布日期': '2026-10-30', 'Excel': OUTPUT.name,
    '工作表': [{'名称': ws.title, '数据行数': ws.max_row - 4} for ws in book],
    '地址条目': len(address_rows), '原始业务operations': len(api_rows), '操作步骤': len(steps),
    '本机匿名GET记录': len(CHECKS['结果']), '认证联调': '未执行', '写接口': '未执行',
    '凭证': '模板不含真实凭证', '文件校验': 'XLSX 可读、5 个 operation 对应原文、内部跳转有效、无公式、JSON 可解析、ZIP 完整、官方原文 SHA256 未变',
    '交付文件': [],
}
for p in [OUTPUT, zip_path]:
    report['交付文件'].append({'文件名': p.name, 'sizeBytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'uid': p.stat().st_uid, 'gid': p.stat().st_gid})
(OUT / '生成与核验.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
