from pathlib import Path
from collections import Counter
from datetime import datetime
import hashlib
import json
import re
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

BASE = Path(__file__).resolve().parent
SOURCE = BASE.parent / '12_工程化字段表v4.0/电池护照_工程化字段表_v4.0_公共字段合并版.xlsx'
SNAPSHOT = BASE / '本地Excel_本次比对快照.xlsx'
ONLINE = BASE / '在线表_最新读取快照.json'
URL = 'https://alidocs.dingtalk.com/i/nodes/Qnp9zOoBVBByzZaNClgbvO9xV1DK0g6l'

def normal(value):
    return re.sub(r'^\$\.', '', str(value or '').strip())

raw = json.loads(ONLINE.read_text())
assert raw['success'] and raw['hasMore'] is False and not raw['truncationReasons']
assert raw['returnedRange'] == 'A1:H145'
assert raw['rowIndices'] == list(range(1, 146))
online_rows = {n: [cell.get('value', '') for cell in row]
               for n, row in zip(raw['rowIndices'], raw['cells'])}
assert online_rows[1][4] == '对应欧盟官方语义模型字段'
index = {}
for number, values in online_rows.items():
    if number == 1:
        continue
    for token in re.split(r'[\r\n,，;；]+', str(values[4])):
        key = normal(token)
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*', key):
            index.setdefault(key, []).append(number)

mapping_blank = {
    'detailedComposition': ([65, 66, 67, 68, 69, 70], '在线已有正极、负极、电解液组成的业务行；E65:E70为合并单元格，内容是固定文案格式，未填写detailedComposition。'),
    'originalPowerCapability': ([109, 110], '在线已有20%和80% SOC初始功率业务行，但E列均为空；本次仅记录映射缺口，不替官方决定单值SOC口径。'),
    'batterySubstancesImpact': ([76], '在线D76已有“有害物质影响”，E76为空；并非完全缺少业务行。'),
}
children = {
    'maximumVoltage.valueRange': (108, 'maximumVoltage', 'valueRange'),
    'minimalVoltage.valueRange': (108, 'minimalVoltage', 'valueRange'),
    'nominalVoltage.valueRange': (107, 'nominalVoltage', 'valueRange'),
    'notInUseTemperatureRange.valueRange': (142, 'notInUseTemperatureRange', 'valueRange'),
    'notInUseTemperatureRangeTest': (142, 'notInUseTemperatureRange', 'notInUseTemperatureRangeTest'),
    'powerLimit.valueRange': (114, 'powerLimit', 'valueRange'),
    'recordedTime': (122, 'selfDischargingRateSet', 'recordedTime'),
    'selfDischargingRate': (122, 'selfDischargingRateSet', 'selfDischargingRate'),
}
missing_notes = {
    'batchNumber': '在线仅列型号和序列号，未找到批次号业务行或语义映射。',
    'otherIdentificationElement': '未找到其他识别要素业务行或语义映射。',
    'productNumber': '未找到产品编号业务行或语义映射。',
    'expectedLifetimeCyclesRest': '在线125行为expectedLifetimeCycles，不能代替expectedLifetimeCyclesRest。',
    'ohmicResistance': '在线122行为internalResistance，不能代替ohmicResistance。',
    'powerCapability': '在线已有remainingPowerCapability及powerFade，未找到powerCapability。',
    'roundTripEnergyEfficiency': '在线已有初始、半寿命、剩余往返能效及衰减率，未找到roundTripEnergyEfficiency。',
}

book = load_workbook(SNAPSHOT, data_only=True, read_only=True)
sheet = book['工程化v4.0']
headers = [c.value for c in sheet[1]]
records = []
for number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
    row = dict(zip(headers, values))
    key = normal(row['工程化字段名称'])
    if not key:
        continue
    path = normal(row['官方JSON路径'])
    matches = index.get(key)
    note = ''
    if matches:
        status = '直接匹配'
        note = '去除首尾空白和$.前缀；同格多个字段按换行或逗号拆开。'
    elif path in index:
        status = '路径写法不同但已对应'
        matches = index[path]
        note = '本地工程名为简名；本地官方JSON路径与在线E列路径一致。'
    elif key in mapping_blank:
        status = '已有业务行但映射未填'
        matches, note = mapping_blank[key]
    elif key in children:
        status = '子字段未独立列行'
        online_row, parent, child = children[key]
        matches = [online_row]
        evidence = online_rows[online_row]
        assert parent in [normal(t) for t in re.split(r'[\r\n,，;；]+', str(evidence[4]))]
        assert re.search(r'"' + re.escape(child) + r'"\s*:', evidence[5])
        note = f'在线E{online_row}只列父字段{parent}；F{online_row}示例含{child}，没有独立子字段行。'
    else:
        assert key in missing_notes, f'未分类字段：{key}'
        status = '整项缺失'
        matches = []
        note = missing_notes[key]
    records.append({
        '状态': status, '本地行号': number, '字段ID': row['字段ID'],
        '数据项名称': row['数据项名称'], '工程化字段名称': key,
        '电池分类（本地）': row['电池分类'], '是否必填（本地）': row['是否必填'],
        '本地官方JSON路径': row['官方JSON路径'],
        '在线相关行号': ', '.join(map(str, matches)),
        '在线E列原文': '\n'.join(str(online_rows[n][4]) for n in matches if online_rows[n][4]),
        '核对说明': note,
    })

counts = Counter(r['状态'] for r in records)
assert len(records) == len({r['工程化字段名称'] for r in records}) == 92
assert counts == Counter({'直接匹配': 63, '路径写法不同但已对应': 20,
                          '整项缺失': 0, '已有业务行但映射未填': 1,
                          '子字段未独立列行': 8})
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert digest(SOURCE) == digest(SNAPSHOT), '比对期间本地原表已变更，请更新快照后重新比对。'

result = {
    '核对时间': datetime.now().astimezone().isoformat(),
    '本地文件': str(SOURCE), '本地SHA256': digest(SOURCE),
    '本地工作表': '工程化v4.0', '本地匹配列': 'F 工程化字段名称',
    '在线表': URL, '在线工作表': '工程化4.0 (s2)',
    '在线匹配列': 'E 对应欧盟官方语义模型字段',
    '在线读取范围': raw['returnedRange'], '在线读取完整': True,
    '在线完整范围（info）': 'A1:S145',
    '在线合并范围（info）': ['H19:H20', 'E65:E70'],
    '本地字段数': len(records), '分类计数': dict(counts),
    '仅按工程名与E列规范化直接比较未命中': 29,
    '整项缺失数': 0, '缺独立子字段行数': 8, '已有业务行但映射未填数': 1,
    '范围说明': '只比对本地主表中现有行；不从历史对照、规则映射或来源核对页补入字段；不修改任何源表。',
    '明细': records,
}
(BASE / '字段缺行比对结果.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))

out = Workbook()
summary = out.active
summary.title = '核对汇总'
summary.append(['项目', '结果'])
for key, value in result.items():
    if key != '明细':
        summary.append([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
columns = list(records[0])
for title, status in [('整项缺失0项', '整项缺失'), ('子字段未独立列行8项', '子字段未独立列行'),
                      ('已有业务行但映射未填1项', '已有业务行但映射未填'),
                      ('路径已对应20项', '路径写法不同但已对应'), ('全部92项', None)]:
    ws = out.create_sheet(title)
    ws.append(columns)
    for record in records:
        if status is None or record['状态'] == status:
            ws.append([record[k] for k in columns])
for ws in out:
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.fill = PatternFill('solid', fgColor='1F4E78')
        cell.font = Font(name='微软雅黑', bold=True, color='FFFFFF')
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name='微软雅黑', size=10)
            cell.alignment = Alignment(vertical='top', wrap_text=True)
    widths = [35, 100] if ws == summary else [25, 12, 15, 28, 46, 22, 22, 65, 20, 65, 90]
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for i in range(2, ws.max_row + 1):
        ws.row_dimensions[i].height = 58 if ws != summary else 32
target = BASE / '本地与在线_字段缺行复核_20261108_175902.xlsx'
out.save(target)
check = load_workbook(target, read_only=True)
assert check['全部92项'].max_row == 93
assert check['整项缺失0项'].max_row == 1
print(json.dumps({'counts': dict(counts), 'missing': [r for r in records if r['状态'] == '整项缺失'], 'report': str(target)}, ensure_ascii=False, indent=2))
