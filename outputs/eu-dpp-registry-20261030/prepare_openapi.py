from pathlib import Path
from copy import deepcopy
from collections import defaultdict
from urllib.parse import unquote
import csv
import hashlib
import json
import zipfile

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


ROOT = Path(__file__).resolve().parent
ORIGINALS = ROOT / '02_OpenAPI'
OUT = ROOT / '04_单文件导入版'
OUT.mkdir(exist_ok=True)
COMMON = 'openapi-dpp-common-public.yml'
REGISTRY = 'openapi-dpp-registry-public.yml'
DASHBOARD = 'openapi-dpp-dashboard-public.yml'
SOURCES = {p.name: yaml.safe_load(p.read_text()) for p in ORIGINALS.glob('*.yml')}
PREFIX = {COMMON: 'Common', REGISTRY: 'Registry', DASHBOARD: 'Dashboard'}
MANIFEST = json.loads((ROOT / '来源与下载校验.json').read_text())
assert len(MANIFEST['files']) == 6 and not MANIFEST['failures']


def pointer(doc, fragment):
    assert fragment.startswith('/')
    result = doc
    for part in fragment[1:].split('/'):
        key = unquote(part).replace('~1', '/').replace('~0', '~')
        result = result[int(key)] if isinstance(result, list) else result[key]
    return result


def refs(node):
    if isinstance(node, dict):
        if '$ref' in node: yield node['$ref']
        for key, value in node.items():
            if key == 'discriminator' and isinstance(value, dict):
                yield from value.get('mapping', {}).values()
            yield from refs(value)
    elif isinstance(node, list):
        for value in node: yield from refs(value)


def validate_original_refs():
    checked = 0
    for filename, doc in SOURCES.items():
        for ref in refs(doc):
            filepart, fragment = ref.split('#', 1)
            target = Path(filepart).name if filepart else filename
            assert target in SOURCES
            pointer(SOURCES[target], fragment)
            checked += 1
    return checked


def make_bundle(filenames, title, output):
    filenames = [*filenames, COMMON]
    buckets = defaultdict(list)
    for filename in filenames:
        for kind, values in SOURCES[filename].get('components', {}).items():
            for name, value in values.items(): buckets[kind, name].append((filename, value))

    renames = {}
    for (kind, name), items in buckets.items():
        equal = all(value == items[0][1] for _, value in items)
        for filename, _ in items:
            renames[filename, kind, name] = name if equal else PREFIX[filename] + name

    def rewrite_ref(ref, origin):
        filepart, fragment = ref.split('#', 1)
        filename = Path(filepart).name if filepart else origin
        assert filename in filenames
        parts = fragment.split('/')
        assert len(parts) == 4 and parts[1] == 'components', ref
        _, _, kind, name = parts
        return '#/components/' + kind + '/' + renames[filename, kind, name]

    def rewrite(node, origin):
        if isinstance(node, list): return [rewrite(value, origin) for value in node]
        if not isinstance(node, dict): return deepcopy(node)
        result = {}
        for key, value in node.items():
            if key == '$ref': result[key] = rewrite_ref(value, origin)
            elif key == 'discriminator' and isinstance(value, dict):
                result[key] = rewrite(value, origin)
                if 'mapping' in value:
                    result[key]['mapping'] = {tag: rewrite_ref(ref, origin) for tag, ref in value['mapping'].items()}
            else: result[key] = rewrite(value, origin)
        return result

    base = SOURCES[filenames[0]]
    combined = {
        'openapi': base['openapi'],
        'info': deepcopy(base['info']),
        'servers': deepcopy(base['servers']),
        'security': deepcopy(base['security']),
        'paths': {},
        'components': {},
        'x-derived-document': {
            'notice': 'Convenience bundle prepared locally from official European Commission files. Original files are retained in 02_OpenAPI. Shared references are localised; conflicting component names are qualified. No API operation, payload field, required field, response or security requirement has been intentionally changed.',
            'sourceFiles': filenames,
            'publicationDate': '2026-10-30',
            'sourcePage': MANIFEST['sourcePage'],
        },
    }
    combined['info']['title'] = title
    combined['info']['description'] = 'Locally prepared single-file import bundle. Consult original source contracts and accompanying integration guides.\n\n' + combined['info'].get('description', '')
    tags = {}
    for filename in filenames:
        doc = SOURCES[filename]
        if doc.get('paths'):
            assert doc.get('servers') == combined['servers']
            assert doc.get('security') == combined['security']
        for tag in doc.get('tags', []): tags[tag['name']] = deepcopy(tag)
        for path, operation in doc.get('paths', {}).items():
            assert path not in combined['paths']
            combined['paths'][path] = rewrite(operation, filename)
        for kind, values in doc.get('components', {}).items():
            destination = combined['components'].setdefault(kind, {})
            for name, value in values.items():
                target = renames[filename, kind, name]
                rewritten = rewrite(value, filename)
                if target in destination: assert destination[target] == rewritten
                else: destination[target] = rewritten
    if tags: combined['tags'] = list(tags.values())
    for ref in refs(combined):
        assert ref.startswith('#/'), ref
        pointer(combined, ref[1:])
    for schema in combined['components'].get('schemas', {}).values():
        Draft202012Validator.check_schema(schema)
    text = '# Locally prepared import bundle; official unmodified specifications are in 02_OpenAPI.\n'
    text += yaml.safe_dump(combined, allow_unicode=True, sort_keys=False, width=110)
    path = OUT / output
    path.write_text(text)
    parsed = yaml.safe_load(path.read_text())
    assert parsed == combined
    return path, combined


original_refs = validate_original_refs()
bundles = []
for files, title, output in [
    ([REGISTRY], 'EU DPP Registry API — single-file import bundle', 'openapi-dpp-registry-bundled.yaml'),
    ([DASHBOARD], 'EU DPP Activity Dashboard API — single-file import bundle', 'openapi-dpp-dashboard-bundled.yaml'),
    ([REGISTRY, DASHBOARD], 'EU DPP Registry and Activity Dashboard APIs — combined import bundle', 'openapi-dpp-all-bundled.yaml'),
]:
    path, document = make_bundle(files, title, output)
    bundles.append({'path': str(path.relative_to(ROOT)), 'operations': sum(method in {'get','post','put','patch','delete','head','options','trace'} for item in document['paths'].values() for method in item), 'localReferencesChecked': len(list(refs(document))), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})

combined = yaml.safe_load((OUT / 'openapi-dpp-all-bundled.yaml').read_text())
assert bundles[-1]['operations'] == 5
resource = Resource.from_contents(combined, default_specification=DRAFT202012)
registry = Registry().with_resource('urn:ec:dpp:bundle', resource)
schema = {'$ref': 'urn:ec:dpp:bundle#/components/schemas/DPPRegistrationSubmissionRequest'}
validator = Draft202012Validator(schema, registry=registry)
sample = SOURCES[REGISTRY]['paths']['/api/v1/dpp/submission']['post']['requestBody']['content']['application/json']['example']
validator.validate(sample)

rows = [
    ('GET', '/api/v1/dpp/product-groups', '查询产品组及子组', 'Bearer JWT；无请求体', '200：产品组及子组列表', '先获取合法分类代码。'),
    ('POST', '/api/v1/dpp/submission', '提交注册请求', '必需 Idempotency-Key；JSON 含 productGroup、productSubGroup（电池必需）、submissionItems；每项至少含 uniqueProductIdentifier、granularity', '202：taskId、taskType、submittedAt、correlationId 等', '每次 1—100 项；202 表示受理。使用任务接口判断最终结果。'),
    ('GET', '/api/v1/tasks', '查询任务列表', '可选 status、taskType、page、size、sort、submittedFrom、submittedTo、extended', '200：分页任务列表', '默认 extended=false；业务结果需启用扩展或查看详情。'),
    ('GET', '/api/v1/tasks/{taskId}', '查询任务详情与业务结果', 'taskId；Bearer JWT', '200：任务状态、outcome、specificTaskData', 'COMPLETED 后还需检查 outcome.outcomeResult，处理结束不等于注册成功。'),
    ('GET', '/api/v1/tasks/{taskId}/items', '查询逐项处理结果', 'taskId；可选 page、size；Accept: application/json 或 text/csv', '200：分页逐项结果；也可获取该页 CSV', 'CSV 同样遵循分页；用于定位具体 UPI 的成功／失败及原因。'),
]
with (ROOT / '接口速览.csv').open('w', encoding='utf-8-sig', newline='') as file:
    writer = csv.writer(file)
    writer.writerow(['方法', '路径', '中文用途', '入参要点（本地摘要）', '成功出参要点（本地摘要）', '注意事项'])
    writer.writerows(rows)
with (ROOT / '文件清单.csv').open('w', encoding='utf-8-sig', newline='') as file:
    writer = csv.writer(file)
    writer.writerow(['名称', '官网发布日期', '本地路径', '大小(bytes)', '页数', '官方URL', 'SHA256'])
    for entry in MANIFEST['files']:
        writer.writerow([entry['title'], MANIFEST['publicationDate'], entry['localPath'], entry['sizeBytes'], entry.get('pages', ''), entry['url'], entry['sha256']])

verification = {
    'officialDownloads': 6,
    'downloadFailures': MANIFEST['failures'],
    'officialOpenApiFiles': 3,
    'officialOpenApiVersion': '3.1.0',
    'officialContractVersion': '1.0.0',
    'originalReferencesChecked': original_refs,
    'bundles': bundles,
    'registrationExampleSchemaCheck': 'passed',
    'scope': '本地检查 YAML 解析、引用解析、组件 Schema 与注册请求示例。未调用注册服务，也未在 Apifox 中实测导入。',
}
# 确认下载原文件和解包 YAML 均保持原样。
for entry in MANIFEST['files']:
    assert hashlib.sha256((ROOT / entry['localPath']).read_bytes()).hexdigest() == entry['sha256']
api_zip = next(ROOT / entry['localPath'] for entry in MANIFEST['files'] if entry['title'] == 'OpenAPI Specifications')
with zipfile.ZipFile(api_zip) as original:
    for filename in SOURCES: assert original.read(filename) == (ORIGINALS / filename).read_bytes()
verification['originalFilesUnmodified'] = True
(ROOT / 'OpenAPI_本地验证结果.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2))

archive = ROOT.parent / '欧委会DPP注册中心_20261030_资料包.zip'
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipout:
    for file in sorted(ROOT.rglob('*')):
        if file.is_file(): zipout.write(file, Path(ROOT.name) / file.relative_to(ROOT))
with zipfile.ZipFile(archive) as check: assert check.testzip() is None
assert all(path.stat().st_uid == 1000 and path.stat().st_gid == 1000 for path in [ROOT, archive, *ROOT.rglob('*')])
print(json.dumps(verification, ensure_ascii=False, indent=2))
print('资料包：', archive)
print('资料包大小：', archive.stat().st_size)
