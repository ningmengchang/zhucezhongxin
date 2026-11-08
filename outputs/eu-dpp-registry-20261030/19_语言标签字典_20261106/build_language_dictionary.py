"""Build language-label dictionaries without treating generated pairs as official enums."""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
import re
import string
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import babel
from babel import Locale
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = Path(__file__).resolve().parent
RAW = HERE / 'IANA_language-subtag-registry.txt'
REGISTRY_URL = 'https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry'
RFC_URL = 'https://www.rfc-editor.org/rfc/rfc5646.html'
raw = RAW.read_text()
file_date = raw.splitlines()[0].split(': ', 1)[1]
records = []
for block in raw.split('%%'):
    record = {}
    last = None
    for line in block.strip().splitlines():
        if line.startswith(' ') and last:
            record[last][-1] += ' ' + line.strip()
        elif ': ' in line:
            k, v = line.split(': ', 1)
            record.setdefault(k, []).append(v)
            last = k
    if 'Type' in record:
        records.append(record)

zh = Locale.parse('zh_CN')
val = lambda rec, key: '; '.join(rec.get(key, []))


def label(rec, kind):
    key = val(rec, 'Subtag')
    if val(rec, 'Description') == 'Private use':
        return '私用' + ('语言码' if kind == 'language' else '地区码')
    names = zh.languages if kind == 'language' else zh.territories
    return names.get(key) or val(rec, 'Description')


languages = {val(r, 'Subtag'): r for r in records
             if val(r, 'Type') == 'language' and re.fullmatch('[a-z]{2}', val(r, 'Subtag'))}
regions = {}
for record in records:
    if val(record, 'Type') != 'region':
        continue
    key = val(record, 'Subtag')
    if re.fullmatch('[A-Z]{2}', key):
        regions[key] = record
    elif re.fullmatch(r'[A-Z]{2}\.\.[A-Z]{2}', key):
        low, high = key.split('..')
        for a, b in itertools.product(string.ascii_uppercase, repeat=2):
            key_ = a + b
            if low <= key_ <= high:
                expanded = dict(record)
                expanded['Subtag'] = [key_]
                expanded['Range-Source'] = [key]
                regions[key_] = expanded
active_lang = {k: r for k, r in languages.items() if 'Deprecated' not in r}
public_region = {k: r for k, r in regions.items() if 'Deprecated' not in r and val(r, 'Description') != 'Private use'}


def status(lang, region):
    result = []
    if 'Deprecated' in lang:
        result.append('语言码已废弃')
    if 'Deprecated' in region:
        result.append('地区码已废弃')
    if val(region, 'Description') == 'Private use':
        result.append('私用地区')
    return '；'.join(result) or '现行注册组合'


headers = ['标签', '语言码', '语言名称（中文参考）', '语言名称（IANA英文）', '地区码',
           '地区名称（中文参考）', '地区名称（IANA英文）', '状态', '语言首选替代码', '地区首选替代码']


def make_row(language, region):
    l, r = languages[language], regions[region]
    return [language + '-' + region, language, label(l, 'language'), val(l, 'Description'), region,
            label(r, 'region'), val(r, 'Description'), status(l, r), val(l, 'Preferred-Value'), val(r, 'Preferred-Value')]


all_rows = [make_row(l, r) for l in sorted(languages) for r in sorted(regions)]
active_values = [l + '-' + r for l in sorted(active_lang) for r in sorted(public_region)]
tag_values = [row[0] for row in all_rows]

common = [
    ('zh-CN', '中文（中国大陆）', '地区标签不显式指定简繁体；需要脚本区分时BCP 47可用zh-Hans-CN，但当前模型不支持该长度。'),
    ('zh-TW', '中文（台湾地区）', '地区标签不显式指定书写脚本。'),
    ('zh-HK', '中文（香港）', '地区标签不显式指定书写脚本。'),
    ('zh-MO', '中文（澳门）', ''), ('zh-SG', '中文（新加坡）', ''),
    ('en-IE', '英语（爱尔兰）', 'IE为地区码；爱尔兰语的语言码是ga。'),
    ('en-GB', '英语（英国）', '英国地区码使用GB。'), ('en-US', '英语（美国）', ''),
    ('en-CA', '英语（加拿大）', ''), ('en-AU', '英语（澳大利亚）', ''), ('en-NZ', '英语（新西兰）', ''),
    ('en-IN', '英语（印度）', ''), ('en-SG', '英语（新加坡）', ''), ('en-ZA', '英语（南非）', ''),
    ('en-MT', '英语（马耳他）', ''), ('de-DE', '德语（德国）', ''), ('de-AT', '德语（奥地利）', ''),
    ('de-CH', '德语（瑞士）', ''), ('de-LU', '德语（卢森堡）', '卢森堡官方语言，也是欧盟官方语言。'),
    ('fr-FR', '法语（法国）', ''), ('fr-BE', '法语（比利时）', ''),
    ('fr-LU', '法语（卢森堡）', '卢森堡官方语言，也是欧盟官方语言。'),
    ('fr-CH', '法语（瑞士）', ''), ('fr-CA', '法语（加拿大）', ''), ('es-ES', '西班牙语（西班牙）', ''),
    ('es-MX', '西班牙语（墨西哥）', ''), ('es-AR', '西班牙语（阿根廷）', ''), ('it-IT', '意大利语（意大利）', ''),
    ('it-CH', '意大利语（瑞士）', ''), ('nl-NL', '荷兰语（荷兰）', ''), ('nl-BE', '荷兰语（比利时）', ''),
    ('pt-PT', '葡萄牙语（葡萄牙）', ''), ('pt-BR', '葡萄牙语（巴西）', ''), ('pl-PL', '波兰语（波兰）', ''),
    ('cs-CZ', '捷克语（捷克）', ''), ('sk-SK', '斯洛伐克语（斯洛伐克）', ''), ('hu-HU', '匈牙利语（匈牙利）', ''),
    ('ro-RO', '罗马尼亚语（罗马尼亚）', ''), ('bg-BG', '保加利亚语（保加利亚）', ''), ('el-GR', '希腊语（希腊）', ''),
    ('el-CY', '希腊语（塞浦路斯）', ''), ('da-DK', '丹麦语（丹麦）', ''), ('sv-SE', '瑞典语（瑞典）', ''),
    ('sv-FI', '瑞典语（芬兰）', ''), ('fi-FI', '芬兰语（芬兰）', ''), ('et-EE', '爱沙尼亚语（爱沙尼亚）', ''),
    ('lv-LV', '拉脱维亚语（拉脱维亚）', ''), ('lt-LT', '立陶宛语（立陶宛）', ''), ('sl-SI', '斯洛文尼亚语（斯洛文尼亚）', ''),
    ('hr-HR', '克罗地亚语（克罗地亚）', ''), ('ga-IE', '爱尔兰语（爱尔兰）', ''), ('mt-MT', '马耳他语（马耳他）', ''),
    ('lb-LU', '卢森堡语（卢森堡）', '卢森堡官方语言；未列入欧盟24种官方语言。'),
    ('nb-NO', '挪威博克马尔语（挪威）', ''), ('nn-NO', '挪威尼诺斯克语（挪威）', ''),
    ('is-IS', '冰岛语（冰岛）', ''), ('ru-RU', '俄语（俄罗斯）', ''), ('uk-UA', '乌克兰语（乌克兰）', ''),
    ('tr-TR', '土耳其语（土耳其）', ''), ('ja-JP', '日语（日本）', ''), ('ko-KR', '韩语（韩国）', ''),
    ('ar-SA', '阿拉伯语（沙特阿拉伯）', ''), ('ar-AE', '阿拉伯语（阿联酋）', ''),
    ('he-IL', '希伯来语（以色列）', '使用he，不使用已废弃的iw。'), ('hi-IN', '印地语（印度）', ''),
    ('th-TH', '泰语（泰国）', ''), ('vi-VN', '越南语（越南）', ''), ('id-ID', '印度尼西亚语（印度尼西亚）', '使用id，不使用已废弃的in。'),
    ('ms-MY', '马来语（马来西亚）', ''), ('bn-BD', '孟加拉语（孟加拉国）', ''), ('ta-IN', '泰米尔语（印度）', ''),
    ('ur-PK', '乌尔都语（巴基斯坦）', ''), ('fa-IR', '波斯语（伊朗）', ''),
]
for item, _, _ in common:
    assert item in active_values

with (HERE / 'xx-XX全部组合_含状态.csv').open('w', encoding='utf-8-sig', newline='') as fp:
    writer = csv.writer(fp); writer.writerow(headers); writer.writerows(all_rows)
with (HERE / '常用语言标签.csv').open('w', encoding='utf-8-sig', newline='') as fp:
    writer = csv.writer(fp); writer.writerow(['标签', '中文名称', '备注']); writer.writerows(common)
save_json = lambda name, value: (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
save_json('xx-XX_现行非私用组合.enum.json', active_values)
save_json('xx-XX_全部注册组合含废弃及私用.enum.json', tag_values)
save_json('常用语言标签.enum.json', [tag_ for tag_, _, _ in common])

# A write-only workbook keeps the full list practical without excessive memory use.
wb = Workbook(write_only=True)


def make_sheet(name, heading, rows, widths):
    from openpyxl.cell import WriteOnlyCell
    ws = wb.create_sheet(name)
    ws.freeze_panes = 'A2'
    ws.sheet_view.showGridLines = True
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    header = []
    for value in heading:
        cell = WriteOnlyCell(ws, value=value)
        cell.font = Font(name='等线', bold=True, size=10, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='365F7D')
        cell.alignment = Alignment(vertical='top', wrap_text=True)
        header.append(cell)
    ws.append(header)
    count = 0
    for row in rows:
        ws.append(row); count += 1
    ws.auto_filter.ref = f'A1:{get_column_letter(len(heading))}{count + 1}'
    return count


notes = [
    ['用途', '为EV护照多语言字段提供语言键字典及候选值；未修改任何业务Schema或在线表格。'],
    ['欧盟覆盖', '常用标签示例已覆盖欧盟27个成员国地区及24种欧盟官方语言；另收录卢森堡语。'],
    ['标准', 'BCP 47，标签结构由RFC 5646定义，子标签取自IANA Language Subtag Registry。'],
    ['当前模型格式', '^[A-Za-z]{2}-[A-Za-z]{2}$；仅支持二字母语言码-二字母地区码。'],
    ['大小写', '标准比较不区分大小写；本文件统一小写语言码、大写地区码，例如en-IE。'],
    ['是否官方枚举', '官方模型只有正则，没有enum；组合全集是由子标签生成的候选清单，不是欧盟发布的逐项标签枚举。'],
    ['全部组合范围', f'{len(languages)}个二字母语言码 × {len(regions)}个二字母地区码（包括历史、私用）= {len(all_rows)}项。'],
    ['现行非私用子集', f'{len(active_lang)}个现行二字母语言码 × {len(public_region)}个现行非私用二字母地区码 = {len(active_values)}项。'],
    ['组合语义', '组合表不证明某语言是当地官方语言或存在特定方言；选值应与实际提供的文本语言和地区变体一致。'],
    ['私用地区', 'AA、QM..QZ、XA..XZ、ZZ等没有固定公共地区语义，须由交换双方约定；不作为普通地区选项推荐。'],
    ['已废弃', '历史记录单独标记，并保留Preferred-Value；替代码可能不满足当前二字母模型。'],
    ['全BCP47边界', 'BCP47还允许三字母语言、脚本、数字地区、变体、扩展和私用标签，不能把本表称为全部BCP47标签。'],
    ['不兼容示例', 'en、zh-Hans-CN、zh-Hant-TW、es-419、fil-PH均可属于BCP47写法，但不满足当前字段的xx-XX正则。'],
    ['原始正则边界', 'aa-AA之类字符串只要符合字母长度即能通过正则；业务还应确认语言码、地区码及使用含义。'],
    ['字段值', '语言键对应说明文本，例如{"en-IE":"Description","zh-CN":"说明文本"}；地区标签不表示制造地。'],
    ['中文名称来源', f'本机Babel {babel.__version__}附带的CLDR中文标签，仅为阅读辅助，可能落后于注册表；IANA英文名称及代码为追溯依据。'],
    ['IANA文件日期', file_date], ['生成日期', '2026-11-06'],
    ['IANA来源', REGISTRY_URL], ['标签标准来源', RFC_URL],
    ['注册表SHA256', hashlib.sha256(RAW.read_bytes()).hexdigest()],
]
make_sheet('使用说明', ['项目', '说明'], notes, [28, 130])
make_sheet('常用标签示例', ['标签', '中文名称', '备注'], common, [18, 40, 110])
make_sheet('xx-XX全部组合', headers, all_rows, [18, 12, 30, 40, 12, 32, 45, 30, 20, 20])
lang_rows = [[key, label(rec, 'language'), val(rec, 'Description'), '已废弃' if 'Deprecated' in rec else '现行',
              val(rec, 'Deprecated'), val(rec, 'Preferred-Value'), val(rec, 'Scope'), val(rec, 'Suppress-Script')]
             for key, rec in sorted(languages.items())]
make_sheet('二字母语言码', ['语言码', '中文名称参考', 'IANA英文名称', '状态', '废弃日期', '首选替代码', '范围', '默认可省略脚本'],
           lang_rows, [15, 30, 50, 15, 18, 18, 22, 25])
region_rows = [[key, label(rec, 'region'), val(rec, 'Description'), '已废弃' if 'Deprecated' in rec else '私用' if val(rec, 'Description') == 'Private use' else '现行',
                val(rec, 'Deprecated'), val(rec, 'Preferred-Value'), val(rec, 'Range-Source')]
               for key, rec in sorted(regions.items())]
make_sheet('二字母地区码', ['地区码', '中文名称参考', 'IANA英文名称', '状态', '废弃日期', '首选替代码', '展开自私用范围'],
           region_rows, [15, 35, 55, 15, 18, 20, 25])
all_records = [[val(rec, 'Type'), val(rec, 'Subtag') or val(rec, 'Tag'), val(rec, 'Description'), val(rec, 'Added'),
                val(rec, 'Deprecated'), val(rec, 'Preferred-Value'), val(rec, 'Prefix'), val(rec, 'Suppress-Script'), val(rec, 'Scope')]
               for rec in records]
make_sheet('IANA完整注册条目', ['类型', '子标签或标签', 'IANA名称', '登记日期', '废弃日期', '首选替代码', '前缀限制', '默认可省略脚本', '范围'],
           all_records, [20, 35, 65, 20, 20, 30, 45, 25, 25])
output = HERE / 'BCP47语言标签_全部xx-XX组合及字典.xlsx'
wb.save(output)

# Independent evidence checks: known distinctions, complete products, source membership,
# deprecated replacements, XML package integrity, and workbook readback counts.
pattern = re.compile(r'^[A-Za-z]{2}-[A-Za-z]{2}$')
assert len(tag_values) == len(set(tag_values)) == len(languages) * len(regions)
assert len(active_values) == len(set(active_values)) == len(active_lang) * len(public_region)
assert all(pattern.fullmatch(value) for value in tag_values)
assert set(active_values) <= set(tag_values)
assert 'en-IE' in active_values and 'ga-IE' in active_values
assert 'en-AA' not in active_values and 'en-AA' in tag_values
assert 'iw-IL' not in active_values and 'iw-IL' in tag_values
assert val(languages['iw'], 'Preferred-Value') == 'he'
assert 'xx-XX' not in tag_values
assert not any(pattern.fullmatch(value) for value in ['en', 'zh-Hans-CN', 'zh-Hant-TW', 'es-419', 'fil-PH'])
with zipfile.ZipFile(output) as z:
    assert z.testzip() is None
check_wb = load_workbook(output, read_only=True, data_only=True)
count_rows = sum(1 for _ in check_wb['xx-XX全部组合'].iter_rows(min_row=2, values_only=True))
assert count_rows == len(all_rows)
registry_rows = sum(1 for _ in check_wb['IANA完整注册条目'].iter_rows(min_row=2, values_only=True))
assert registry_rows == len(records)
check_wb.close()
report = {
    '标准': 'BCP 47 / RFC 5646', 'IANA文件日期': file_date, '生成日期': '2026-11-06',
    'IANA来源': REGISTRY_URL, 'RFC来源': RFC_URL,
    '官方模型声明enum': False, '模型语言键正则': '^[A-Za-z]{2}-[A-Za-z]{2}$',
    '二字母语言码含废弃': len(languages), '现行二字母语言码': len(active_lang),
    '二字母地区码含废弃及展开私用': len(regions), '现行非私用二字母地区码': len(public_region),
    '二字母组合全集': len(all_rows), '现行非私用组合': len(active_values),
    '常用示例': len(common), 'IANA完整注册条目': len(records),
    '状态统计': dict(Counter(row[7] for row in all_rows)),
    '中文辅助标签来源': f'Babel {babel.__version__} / CLDR bundled data',
    '核对结果': 'PASS', '现行表定位': '注册子标签的生成候选组合，不等于地区官方语言清单或项目必填清单。',
    '原始注册表SHA256': hashlib.sha256(RAW.read_bytes()).hexdigest(),
}
save_json('生成与校验报告.json', report)
(HERE / 'README.txt').write_text('\n'.join(k + '：' + v for k, v in notes) + '\n')
with zipfile.ZipFile(HERE / 'BCP47语言标签_枚举与字典.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for path in sorted(HERE.iterdir()):
        if path.suffix in ('.csv', '.json', '.xlsx') or path.name == 'README.txt':
            z.write(path, 'BCP47语言标签字典/' + path.name)
print(json.dumps(report, ensure_ascii=False, indent=2))
