"""Produce neutrally named SN documentation and synchronized examples."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from lxml import etree as X
from openpyxl import load_workbook

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / '名称调整输入.json').read_text())
BACKUP = Path(CONFIG['backup_dir'])
OLD_STEM = '供应商SN对接接口说明_EV全量语义_24位国标码_v1.0'
STEM = 'SN对接接口说明_EV全量语义_24位国标码_v1.0'
parser = argparse.ArgumentParser()
parser.add_argument('--remove-route-segment', action='store_true')
args = parser.parse_args()
REPLACEMENTS = [
    ('供应商上传 SN 接口', 'SN 数据上传接口'),
    ('供应商上传SN接口', 'SN数据上传接口'),
    ('供应商SN对接接口说明', 'SN对接接口说明'),
    ('供应商SN接口说明', 'SN接口说明'),
    ('供应商接口对接标准说明', 'SN接口对接标准说明'),
    ('供应商上传SN请求', 'SN数据上传请求'),
    ('供应商系统', '对接系统'),
    ('供应商按', '调用方按'),
    ('供应商身份', '接入主体身份'),
    ('供应商配置', '接入主体配置'),
    ('供应商登录账号', '对接登录账号'),
    ('示例供应商', '示例对接方'),
    ('供应商', '对接方'),
    ('supplier_demo', 'client_demo'),
    ('supplier@example.com', 'integration@example.com'),
]
if args.remove_route_segment:
    REPLACEMENTS.append(('/open-api/dpp-platform/supplier/sn/upload', '/open-api/dpp-platform/sn/upload'))


def neutral(value):
    for old, new in REPLACEMENTS:
        value = value.replace(old, new)
    return value


with zipfile.ZipFile(BACKUP / (OLD_STEM + '_对接规范修订版.docx')) as z:
    package = {p: z.read(p) for p in z.namelist()}
part_counts = {}
for name in package:
    if name.endswith(('.xml', '.rels')):
        text = package[name].decode('utf-8')
        part_counts[name] = text.count('供应商')
        package[name] = neutral(text).encode('utf-8')
        X.fromstring(package[name])
assert not any('供应商'.encode() in data for data in package.values())
with zipfile.ZipFile(HERE / (STEM + '.docx'), 'w', zipfile.ZIP_DEFLATED) as z:
    for name, data in package.items():
        z.writestr(name, data)

for source in (BACKUP / '对接附件').iterdir():
    target = HERE / '对接附件' / source.name
    if source.suffix in ('.json', '.csv', '.txt'):
        target.write_bytes(neutral(source.read_text(encoding='utf-8-sig')).encode('utf-8-sig' if source.suffix == '.csv' else 'utf-8'))
    elif source.suffix == '.xlsx':
        workbook = load_workbook(source)
        for sheet in workbook:
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.value = neutral(cell.value)
        for prop in ('title', 'subject', 'creator', 'description', 'keywords', 'category'):
            value = getattr(workbook.properties, prop, None)
            if isinstance(value, str):
                setattr(workbook.properties, prop, neutral(value))
        workbook.save(target)

# The API schemas have the same validation constraints; only the request title changes.
request_name = 'SnUploadRequest_EV_24位国标码.schema.json'
before = json.loads((BACKUP / '对接附件' / request_name).read_text())
after = json.loads((HERE / '对接附件' / request_name).read_text())
before.pop('title'); after.pop('title')
assert before == after
for schema_name in ('EVBatteryPassport_Apifox直接建模.schema.json', 'LoginRequest.schema.json'):
    assert json.loads((BACKUP / '对接附件' / schema_name).read_text()) == json.loads((HERE / '对接附件' / schema_name).read_text())

with zipfile.ZipFile(HERE / 'SN接口对接附件_EV全量_24位国标码.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for path in sorted((HERE / '对接附件').iterdir()):
        z.write(path, 'SN接口对接附件/' + path.name)

report = {
    '新文件名': STEM + '.docx',
    '名称规则': '文件名、正文及示例采用SN接口、对接方及client_demo等中性表述。',
    '调用路径': '/open-api/dpp-platform/sn/upload' if args.remove_route_segment else '/open-api/dpp-platform/supplier/sn/upload',
    '路径处理': '删除角色路径段' if args.remove_route_segment else '沿用已约定的调用地址',
    '清理XML部件': {k: v for k, v in part_counts.items() if v},
    '请求Schema验证约束保持一致': True,
    '原EV结构定义保持一致': True,
    '核对状态': '待渲染',
}
(HERE / 'SN接口命名调整_核对报告.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False))
