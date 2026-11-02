"""Export the fixed semantic package as annotated, standalone OpenAPI models.

Run with Python 3.12 and rdflib, PyYAML, jsonschema, referencing, openpyxl.
This reads the existing field reader without running its Excel-writing entry point.
JSON Schema required/anyOf/dependentRequired rules are preserved. Vocabulary enums
are explicitly derived from the local TTL snapshot; full SHACL execution is not
claimed. All source files remain unchanged.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import types
import zipfile

import yaml
from jsonschema import Draft202012Validator
from rdflib import Graph, Namespace, RDF, URIRef

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / '03_语义资源'
OUT = ROOT / '08_Apifox语义模型'
SH = Namespace('http://www.w3.org/ns/shacl#')
SKOS = Namespace('http://www.w3.org/2004/02/skos/core#')
BASE = 'https://repository.product-passport.ec.europa.eu/'
ENUM_LABELS = {
    'common-error-code': '通用语义校验消息代码',
    'core-error-code': '核心语义校验消息代码',
    'battery-error-code': '电池语义校验消息代码',
    'common-vocabulary': '通用结构术语目录',
    'core-vocabulary': '核心护照术语目录',
    'battery-vocabulary': '电池字段术语目录',
    'critical-raw-material': '关键原材料',
    'granularity-level': '护照粒度',
    'information-type': '核心信息类型',
    'instance-status': '护照资源状态',
    'measurement-unit': '计量单位',
    'observed-characteristic': '被观测特征',
    'battery-category': '电池类别',
    'battery-chemical-symbol': '化学物质标识符号',
    'battery-chemistry': '电池化学体系',
    'battery-information-type': '电池信息类型',
    'battery-status': '电池生命周期状态',
}
CONSTRAINTS = (
    'format', 'pattern', 'minLength', 'maxLength', 'minimum', 'maximum',
    'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'minItems',
    'maxItems', 'uniqueItems', 'minProperties', 'maxProperties', 'const',
    'additionalProperties', 'unevaluatedProperties', 'propertyNames',
)


def jdump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def unique(values):
    return list(dict.fromkeys(values))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_reader():
    path = ROOT / 'build_schema_excel.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    assert isinstance(tree.body[-2], ast.ImportFrom)
    assert tree.body[-2].module == 'schema_excel_output'
    assert isinstance(tree.body[-1], ast.Expr)
    assert isinstance(tree.body[-1].value, ast.Call)
    assert tree.body[-1].value.func.id == 'build'
    tree.body = tree.body[:-2]
    module = types.ModuleType('dpp_semantic_source_reader')
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


def vocabulary_rows():
    concepts, by_namespace, by_uri = [], defaultdict(list), {}
    for file in sorted(ASSETS.rglob('*.ttl')):
        if '/vocabs/' not in file.as_posix():
            continue
        graph = Graph().parse(file, format='turtle')

        def texts(subject, predicate):
            values = list(graph.objects(subject, predicate))
            english = [v for v in values if getattr(v, 'language', None) in {None, 'en'}]
            return '\n'.join(sorted(map(str, english or values)))

        for subject in sorted(set(graph.subjects(RDF.type, SKOS.Concept)), key=str):
            uri = str(subject)
            row = {
                'uri': uri,
                'code': texts(subject, SKOS.notation) or uri.rsplit('#', 1)[-1],
                'label': texts(subject, SKOS.prefLabel),
                'definition': texts(subject, SKOS.definition),
                'scope': texts(subject, SKOS.scopeNote),
                'alt': texts(subject, SKOS.altLabel),
                'file': file.relative_to(ASSETS).as_posix(),
                'vocabulary': file.stem,
            }
            assert row['label'] and uri not in by_uri
            concepts.append(row)
            by_uri[uri] = row
            by_namespace[uri.rsplit('#', 1)[0] + '#'].append(row)
    return concepts, by_namespace, by_uri


def enum_detail(row, value=None):
    description = '\n'.join(filter(None, [
        '原始代码：' + row['code'],
        '完整概念 URI：' + row['uri'],
        '官方英文定义：' + row['definition'] if row['definition'] else '',
        '官方适用说明：' + row['scope'] if row['scope'] else '',
        '别名／符号：' + row['alt'] if row['alt'] else '',
        '来源：' + row['file'],
    ]))
    return {'value': row['uri'] if value is None else value,
            'name': row['label'], 'description': description}


def write_csv(path, headers, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)


class Exporter:
    def __init__(self, reader):
        from schema_field_details import ShapeIndex, enrich, HEADERS
        self.r = reader
        self.shapes = ShapeIndex(ASSETS)
        self.rich = enrich(ASSETS, reader.PROFILE_ROWS, reader.COMMON_ROWS,
                           reader.FIELD_META, reader.MEANINGS, reader.VOCABS,
                           reader.PROFILE_NAMES, reader.VIEWS)
        self.h = HEADERS
        self.concepts, self.by_namespace, self.by_uri = vocabulary_rows()
        assert len(self.concepts) == len(reader.VOCABS) == 294
        self.schemas = {}
        self.field_rows = []
        self.extra_rules = []
        self.enum_mismatches = []
        self.covered_sources = set()
        self.models = []
        self.sample_checks = []
        self.mutation_checks = []

    def normalise(self, parts):
        """Inline references and allOf while rejecting ambiguous constraint merges."""
        effective = self.r.expanded(parts)
        result = {}
        ignored = {'$ref', '$id', '$schema', '$defs', 'allOf', 'properties',
                   'items', 'required', 'title', 'description', 'message'}
        for part in effective:
            for key, value in part.node.items():
                if key in ignored:
                    continue
                if key == '$comment' and key in result:
                    result[key] = '\n'.join(unique([result[key], value]))
                elif key in result:
                    assert result[key] == value, (part.uri, part.ptr, key, result[key], value)
                else:
                    result[key] = deepcopy(value)
        props, required = self.r.properties(parts)
        if props:
            result['properties'] = {name: self.normalise(child) for name, child in props.items()}
        if required:
            result['required'] = [name for name in props if name in required]
            assert set(result['required']) == required
        items = [self.r.Part(p.uri, self.r.ptr_add(p.ptr, 'items'), p.node['items'])
                 for p in effective if isinstance(p.node.get('items'), dict)]
        if items:
            result['items'] = self.normalise(items)
        if props and 'type' not in result:
            result['type'] = 'object'
            result['x-derived-type'] = '源 Schema 通过 properties 定义对象结构；导入模型显式标注 object。'
        locations = unique(self.source_location(p) for p in effective)
        result['x-source-locations'] = locations
        self.covered_sources.update((p.uri, p.ptr) for p in effective)
        codes = unique(p.node['message']['type'] for p in effective
                       if isinstance(p.node.get('message'), dict) and 'type' in p.node['message'])
        if codes:
            result['x-semantic-message-codes'] = codes
        original_descriptions = unique(p.node['description'] for p in effective if p.node.get('description'))
        if original_descriptions:
            result['x-source-descriptions'] = original_descriptions
        return result

    def source_location(self, part):
        return self.r.DOCS[part.uri][0].relative_to(ASSETS).as_posix() + '#' + part.ptr

    def shape_index(self, files):
        roots = []
        for relative in files:
            graph = self.shapes.graphs[relative]
            roots.extend((root, relative) for root in sorted(set(graph.subjects(SH.targetClass, None)), key=str))
        return self.shapes.extract(roots)

    @staticmethod
    def facts_at(index, path):
        return index.get(path.replace('[]', ''), [])

    def inject_enum(self, node, path, facts):
        if 'const' in node:
            value = node['const']
            node['enum'] = [value]
            node['x-apifox-enum'] = [{'value': value, 'name': '固定值 ' + jdump(value),
                                       'description': '固定值由原始 JSON Schema const 定义。'}]
            node['x-enum-basis'] = '原始 JSON Schema const'
            return
        if node.get('type') != 'string':
            return
        text = '\n'.join([node.get('$comment', ''), node.get('pattern', '')]).replace('\\.', '.')
        namespaces = unique(re.findall(r'https://repository\.product-passport\.ec\.europa\.eu/[A-Za-z0-9_/-]+#', text))
        basis = '原始 Schema 的受控词表引用＋本地 TTL 词表快照（2026-10-30）；enum 为本导入版显式展开。'
        if not namespaces and path.rsplit('.', 1)[-1] == 'observedCharacteristic':
            namespaces = [BASE + 'core/vocabs/observed-characteristic#']
            basis = 'common-error-code.ttl 的 SEM-COM-MSG-001 指明 observed-characteristic 代码表；本地 TTL 快照展开为 enum。源 Schema 仅声明 URI。'
        rows = [c for ns in namespaces for c in self.by_namespace.get(ns, [])]
        fixed = unique(f.value for f in facts if f.predicate == 'hasValue'
                       and 'not分支' not in f.branch and 'qualified值子集' not in f.branch
                       and f.value in self.by_uri)
        if fixed:
            rows = [self.by_uri[uri] for uri in fixed]
            basis += '\n当前路径的 SHACL hasValue／or 分支进一步限制了可用值。'
        if not rows:
            return
        rows = sorted({row['uri']: row for row in rows}.values(), key=lambda row: row['uri'])
        values = []
        for row in rows:
            if 'pattern' in node and not re.search(node['pattern'], row['uri']):
                self.enum_mismatches.append({'path': path, 'uri': row['uri'], 'pattern': node['pattern']})
            else:
                values.append(row)
        assert values, ('empty derived enum', path)
        node['enum'] = [row['uri'] for row in values]
        node['x-apifox-enum'] = [enum_detail(row) for row in values]
        node['x-enum-basis'] = basis

    def annotate(self, node, model, path, index, details, required=None, parent=None):
        facts = self.facts_at(index, path)
        for name, child in node.get('properties', {}).items():
            self.annotate(child, model, path + '.' + name, index, details,
                          name in node.get('required', []), node)
        if isinstance(node.get('items'), dict):
            self.annotate(node['items'], model, path + '[]', index, details, None, node)
        self.inject_enum(node, path, facts)
        if 'properties' in node:
            node['x-apifox-orders'] = list(node['properties'])
        if path == '$':
            return
        detail = details.get(path, {})
        field_name = path.rstrip('[]').rsplit('.', 1)[-1]
        label = self.r.LABELS.get(field_name, field_name)
        if path.endswith('[]'):
            label += '（数组元素）'
        dtype = node.get('type', '未显式限定（源 JSON Schema）')
        conditional = bool(parent and any(field_name in b.get('required', [])
                          for kind in ('anyOf', 'oneOf') for b in parent.get(kind, [])))
        conditional |= bool(parent and (field_name in parent.get('dependentRequired', {}) or
                            any(field_name in v for v in parent.get('dependentRequired', {}).values())))
        schema_required = ('数组元素；不适用属性 required 标记' if required is None
                           else '是（所在父对象／数组元素存在时）' if required
                           else '条件必填（组合／依赖）；非独立 required' if conditional
                           else '否（未列入源 Schema required；业务适用条件另见下文）')
        semantic_required = detail.get(self.h[3], schema_required)
        type_note = dtype
        language_object = any('#/$defs/multiLanguage' in s for s in node.get('x-source-locations', []))
        if language_object:
            type_note += '；动态语言键对应的值未被源 Schema 限定类型，SHACL 要求语言文本（rdf:langString）。'
        constraint_text = '\n'.join(k + '=' + jdump(node[k]) for k in CONSTRAINTS if k in node)
        enum_node = node if 'enum' in node else node.get('items', {})
        enum_lines = []
        if 'enum' in enum_node:
            enum_lines = [jdump(v['value']) + ' — ' + v['name'] for v in enum_node['x-apifox-enum']]
            enum_text = ('数组元素枚举' if enum_node is not node else '枚举') + '（' + str(len(enum_lines)) + ' 项）：\n' + '\n'.join(enum_lines)
        else:
            enum_text = '非枚举；按字段类型、格式和业务约束填写。'
        shacl_text = '\n'.join(unique(('[' + f.branch + '] ' if f.branch else '') + f.predicate + '=' + f.value
                                   for f in facts if f.predicate != 'sparql'))
        source_text = '\n'.join(node.get('x-source-locations', []))
        meaning = self.r.MEANINGS.get(field_name, {})
        lines = [label, 'JSON 结构是否必填：' + schema_required,
                 '综合／语义必填说明：' + semantic_required,
                 '数据类型：' + type_note, enum_text]
        additions = [
            ('业务适用条件', detail.get(self.h[4], meaning.get('scope', ''))),
            ('填写格式', detail.get(self.h[8], '')),
            ('单位／口径', detail.get(self.h[5], '')),
            ('单位填写方式', detail.get(self.h[6], '')),
            ('Schema 约束', constraint_text),
            ('组合与依赖', '\n'.join(k + '=' + jdump(node[k]) for k in ('anyOf', 'oneOf', 'dependentRequired') if k in node)),
            ('父级组合与语义规则', detail.get(self.h[14], '')),
            ('SHACL 规则摘录', shacl_text),
            ('规则差异／边界', detail.get(self.h[16], '')),
            ('官方英文定义', detail.get(self.h[17], meaning.get('definition', ''))),
            ('官方适用说明原文', detail.get(self.h[18], meaning.get('scope', ''))),
            ('来源及 JSON Pointer', source_text),
            ('SHACL／词表来源', detail.get(self.h[20], '')),
            ('枚举展开依据', enum_node.get('x-enum-basis', '')),
        ]
        lines.extend(key + '：\n' + value for key, value in additions if value)
        node['title'] = label
        node['description'] = '\n\n'.join(lines)
        node['x-source-required'] = required
        node['x-semantic-required-note'] = semantic_required
        if facts:
            node['x-shacl-rules'] = [dict(path=f.path, predicate=f.predicate, value=f.value,
                                         branch=f.branch, source=f.source, shape=f.shape) for f in facts]
        if language_object:
            node['additionalProperties'] = {
                'description': '动态语言键的值：源 JSON Schema 未限定 JSON 类型；语义层要求 rdf:langString 语言文本，实际填写字符串。',
                'x-semantic-json-type': 'string',
            }
        self.field_rows.append([model, path, label, schema_required, semantic_required, type_note,
                                jdump(enum_node.get('enum', [])), '\n'.join(enum_lines) or '非枚举',
                                constraint_text, detail.get(self.h[4], meaning.get('scope', '')),
                                shacl_text, detail.get(self.h[16], ''), source_text])
        if language_object:
            self.field_rows.append([model, path + '.{languageTag}', '动态语言键／文本值',
                                    '对象至少一个语言键（minProperties=1）；键格式 xx-XX',
                                    'SHACL 需要带语言标签的文本；不代表最多一个语言键',
                                    '值的 JSON 类型源 Schema 未限定；语义要求 string（rdf:langString）',
                                    '[]', '非枚举', 'propertyNames=' + jdump(node['propertyNames']),
                                    '语言标签如 en-IE、fr-FR；并非只允许这些示例', '', '', source_text])

    def add_model(self, name, title, uri, index, details=None, fragment='', group='', view=''):
        doc = self.r.DOCS[uri][1]
        part = self.r.Part(uri, fragment, self.r.pointer(doc, fragment))
        schema = self.normalise([part])
        details = details or {}
        self.annotate(schema, name, '$', index, details)
        schema['title'] = title
        schema['description'] = '\n\n'.join([
            title,
            '字段逐层展开；required／anyOf／dependentRequired 来自源 JSON Schema。条件必填、SHACL 与词表差异见各字段说明。',
            '枚举按本地 2026-10-30 TTL 快照展开，提交完整 URI；具体 SHACL 固定值／允许单位见当前路径。此文件不能替代完整 SHACL／SPARQL 语义校验。',
            '这是护照语义数据模型；POST /api/v1/dpp/submission 的请求体仍以注册接口 OpenAPI 为准。',
            '来源：' + self.source_location(part),
            *schema.get('x-source-descriptions', []),
        ])
        if fragment.endswith('/multiLanguage'):
            schema['description'] += '\n\n多语言对象：至少一个 xx-XX 语言键。源 Schema 未限定动态值的 JSON 类型；SHACL 要求 rdf:langString，填写文本。'
            schema['additionalProperties'] = {'description': '源 Schema 未限定 JSON 类型；语义要求语言文本（字符串）。',
                                              'x-semantic-json-type': 'string'}
            self.field_rows.append([name, '$.{languageTag}', '动态语言键／文本值',
                                    '至少一个语言键；minProperties=1', 'SHACL 要求语言文本',
                                    '源 Schema 未限定；语义 string（rdf:langString）', '[]', '非枚举',
                                    'propertyNames=' + jdump(schema['propertyNames']), '', '', '', self.source_location(part)])
        schema['x-model-category'] = '访问视图' if group else '基础结构'
        if group:
            schema['x-battery-subgroup'] = group
            schema['x-access-view'] = view
        present_paths = {row[1].replace('[]', '') for row in self.field_rows if row[0] == name}
        extras = []
        for path, facts in sorted(index.items()):
            if path == '$' or path in present_paths:
                continue
            rule = '\n'.join(unique(('[' + f.branch + '] ' if f.branch else '') + f.predicate + '=' + f.value for f in facts))
            sources = '\n'.join(sorted(set(f.source for f in facts)))
            note = ('禁止出现（SHACL maxCount=0）' if any(f.predicate == 'maxCount' and not f.branch and f.value == '0' for f in facts)
                    else '该字段未在当前视图 JSON Schema properties 中定义；SHACL 另有数量／类型要求，不能据 Schema 未列 required 判为业务可选。')
            dtype = ('array<string>；完整结构参见 DppCore.contentSpecificationIds' if path == '$.contentSpecificationIds'
                     else '不适用（本访问视图禁止填写）')
            enum = ('非封闭枚举；规范 URI 的版本号按 pattern 校验' if path == '$.contentSpecificationIds'
                    else '不适用（本访问视图禁止填写）')
            extras.append(path + '：' + note + '\n数据类型：' + dtype + '\n枚举：' + enum + '\n' + rule)
            self.extra_rules.append([name, path, note, dtype, enum, rule, sources])
        if extras:
            schema['description'] += '\n\n当前 Schema 字段列表之外的 SHACL 补充规则：\n' + '\n\n'.join(extras)
        self.schemas[name] = schema
        self.models.append([name, title, group, view, len(schema.get('properties', {})),
                            jdump(schema.get('required', [])), self.source_location(part)])

    def build(self):
        common_uri = next(uri for uri in self.r.DOCS if uri.endswith('/common.schema.json'))
        common_titles = {'multiLanguage': '多语言文本', 'relatedResource': '关联资源',
                         'unitValue': '单位与数值', 'unitValueWithRange': '带条件范围的单位与数值',
                         'valueRange': '条件／观测范围', 'histogram': '直方图'}
        for key in self.r.DOCS[common_uri][1]['$defs']:
            detail = {path.replace('$defs.' + key, '$', 1): row
                      for (group, view, path), row in self.rich['details'].items()
                      if group is None and view == '$defs/' + key}
            self.add_model('DppCommon_' + key, '通用结构｜' + common_titles[key], common_uri,
                           self.shapes.common(key), detail, '/$defs/' + key)
        core_uri = next(uri for uri in self.r.DOCS if uri.endswith('/core.schema.json'))
        core_file = 'core/1.0.0/shapes/core-shapes.ttl'
        self.add_model('DppCore', '核心护照字段', core_uri, self.shape_index([core_file]))
        for view, label in self.r.VIEWS.items():
            uri = next(uri for uri in self.r.DOCS if uri.endswith('/' + view + '-batteries.schema.json'))
            shape_files = [f'product/batteries/1.0.0/shapes/{view}-batteries-shapes.ttl']
            if view == 'pub':
                shape_files.insert(0, core_file)
            self.add_model('DppBattery_' + view.upper(), '电池组基础｜' + label, uri, self.shape_index(shape_files))
        for group, group_label in self.r.PROFILE_NAMES.items():
            for view, label in self.r.VIEWS.items():
                uri = next(uri for uri in self.r.DOCS if uri.endswith('/' + view + '-' + group + '.schema.json'))
                detail = {path: row for (subgroup, access, path), row in self.rich['details'].items()
                          if subgroup == group and access == label}
                model_name = 'Dpp' + {'evb': 'EV', 'lmt': 'LMT', 'ind': 'IND'}[group] + '_' + view.upper()
                self.add_model(model_name, group_label + '电池｜' + label, uri,
                               self.shapes.profile(group, view), detail, group=group, view=view)
                sample_path = ASSETS / f'product/batteries/subgroup/{group}/1.0.0/tests/conforms/{view}-batteries-{group}.json'
                sample = json.loads(sample_path.read_text())
                validator = Draft202012Validator(self.schemas[model_name], format_checker=Draft202012Validator.FORMAT_CHECKER)
                errors = list(validator.iter_errors(sample))
                assert not errors, (model_name, [(list(e.path), e.message) for e in errors])
                self.sample_checks.append({'model': model_name, 'sample': sample_path.relative_to(ASSETS).as_posix(), 'passed': True})
                self.check_mutations(model_name, sample, validator)
        assert len(self.schemas) == 23
        by_file = defaultdict(list)
        for row in self.concepts:
            by_file[row['vocabulary']].append(row)
        for vocabulary, rows in by_file.items():
            name = 'DppEnum_' + ''.join(word.title() for word in vocabulary.split('-'))
            is_error_code = vocabulary.endswith('-error-code')
            values = [row['code'] if is_error_code else row['uri'] for row in rows]
            note = ('语义校验消息使用原始代码字符串；每项说明同时列出完整概念 URI。' if is_error_code else
                    '枚举值为完整概念 URI，不提交 URI 末尾的短代码。')
            if vocabulary.endswith('-vocabulary'):
                note += '这是字段术语目录的参考模型，不是要求在护照中新增的业务字段。'
            schema = {'type': 'string', 'title': '词表｜' + ENUM_LABELS[vocabulary],
                      'description': note + '\n来源：' + rows[0]['file'] + '\n本地 2026-10-30 词表快照；覆盖全部 ' + str(len(rows)) + ' 项。',
                      'enum': values, 'x-apifox-enum': [enum_detail(row, value) for row, value in zip(rows, values)],
                      'x-model-category': '词表枚举', 'x-source-vocabulary': rows[0]['file']}
            self.schemas[name] = schema
            self.models.append([name, schema['title'], '', '', 0, '不适用（枚举模型）', rows[0]['file']])
        assert len(self.schemas) == 40
        assert self.r.raw_locations <= self.covered_sources, self.r.raw_locations - self.covered_sources
        assert not self.enum_mismatches, self.enum_mismatches
        for schema in self.schemas.values():
            Draft202012Validator.check_schema(schema)
        return self.document()

    def check_mutations(self, name, sample, validator):
        for required in self.schemas[name].get('required', []):
            changed = deepcopy(sample)
            del changed[required]
            assert not validator.is_valid(changed), (name, 'missing required', required)
            self.mutation_checks.append([name, '删除根必填字段 ' + required, '正确拒绝'])
        if not name.endswith('_PUB'):
            return
        bad_values = [('manufacturerIdentification', {}), ('manufacturerContactDetails', {}),
                      ('batteryIdentification', {'modelIdentifier': 'M1'}),
                      ('batteryIdentification', {'batchNumber': 'B1'}),
                      ('manufacturingPlace', {'manufacturingCountry': 'IRL'}),
                      ('batteryCategory', 'INVALID'), ('batteryWeight', {'unit': BASE + 'core/vocabs/measurement-unit#volt', 'value': 1}),
                      ('separateCollectionSymbol', False), ('commercialWarrantyPeriod', -1),
                      ('commercialWarrantyPeriod', '24')]
        for key, value in bad_values:
            changed = deepcopy(sample)
            changed[key] = value
            assert not validator.is_valid(changed), (name, key, value)
            self.mutation_checks.append([name, key + '=' + jdump(value), '正确拒绝'])
        for key, value in [('batteryIdentification', {'serialNumber': 'S1'}),
                           ('manufacturerIdentification', {'manufacturerName': 'Example manufacturer'})]:
            changed = deepcopy(sample)
            changed[key] = value
            validator.validate(changed)
            self.mutation_checks.append([name, key + '=' + jdump(value), '正确接受合法组合'])
        changed = deepcopy(sample)
        changed.pop('commercialWarrantyPeriod', None)
        validator.validate(changed)
        self.mutation_checks.append([name, '省略可选 commercialWarrantyPeriod', '正确接受'])

    def document(self):
        return {
            'openapi': '3.1.0',
            'info': {
                'title': '欧盟 DPP 语义数据模型｜字段必填、类型与枚举说明',
                'version': '2026-10-30.local.1',
                'license': {'name': 'European Union Public Licence v. 1.2', 'identifier': 'EUPL-1.2'},
                'description': '由 03_语义资源 本地生成的 Apifox 数据模型导入版。包含 EV、LMT、工业电池的四种访问视图、核心及通用结构和完整词表。仅导入数据模型，paths 为空。必填勾选保留原 JSON Schema required；组合／业务适用条件、SHACL 数量规则和源规则差异写入说明。受控词表显式展开为本地快照 enum，并附 x-apifox-enum 名称和说明。所有结构引用已内置。完整语义合规仍需要原始上下文、SHACL／SPARQL 和业务条件判断。',
            },
            'jsonSchemaDialect': 'https://json-schema.org/draft/2020-12/schema',
            'paths': {},
            'components': {'schemas': self.schemas},
            'x-derived-document': {
                'sourceDirectory': '03_语义资源', 'sourcePublicationDate': '2026-10-30',
                'officialDocument': False, 'sourceSchemaCount': len(self.r.DOCS),
                'sourceVocabularyConcepts': len(self.concepts),
                'requiredPolicy': '保留 JSON Schema required、anyOf、dependentRequired；SHACL 补充要求单列，未冒充为原始 required。',
                'enumPolicy': 'TTL 受控词表快照＋可达单值 SHACL hasValue 约束展开；原 Schema 多为 pattern，并未原生声明这些 enum。',
                'typePolicy': '对象 properties 的类型显式标注 object；未限制的多语言动态值保持不限，另说明语义层要求文本。',
                'shaclValidationExecuted': False, 'apifoxUiImportTested': False,
            },
        }


def main():
    before = {p.relative_to(ASSETS).as_posix(): sha256(p) for p in ASSETS.rglob('*') if p.is_file()}
    reader = load_reader()
    exporter = Exporter(reader)
    document = exporter.build()
    OUT.mkdir(exist_ok=True)

    class ExportDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    def represent_string(dumper, value):
        return dumper.represent_scalar('tag:yaml.org,2002:str', value, style='|' if '\n' in value else None)

    ExportDumper.add_representer(str, represent_string)
    yaml_path = OUT / 'dpp-semantic-models-apifox.yaml'
    yaml_path.write_text('# 本地语义模型导入版；官方原件保留在 03_语义资源。\n' +
                         yaml.dump(document, Dumper=ExportDumper, allow_unicode=True, sort_keys=False, width=110), encoding='utf-8')
    json_path = OUT / 'dpp-semantic-models-apifox.json'
    json_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    assert yaml.safe_load(yaml_path.read_text()) == json.loads(json_path.read_text()) == document
    assert '"$ref"' not in json_path.read_text(), 'Unresolved structural reference'
    write_csv(OUT / '字段严格说明.csv',
              ['模型', '完整字段路径', '中文名称', 'JSON结构是否必填', '综合与语义必填说明', '数据类型',
               '完整枚举值(JSON)', '枚举项与名称', '格式与范围约束', '业务适用条件', 'SHACL规则', '规则差异', '字段来源'],
              sorted(exporter.field_rows, key=lambda row: (row[0], row[1])))
    write_csv(OUT / '枚举项明细.csv',
              ['词表', '代码', '完整概念URI', '官方名称', '别名或符号', '官方英文定义', '官方适用说明', '来源'],
              [[row[k] for k in ['vocabulary', 'code', 'uri', 'label', 'alt', 'definition', 'scope', 'file']]
               for row in exporter.concepts])
    write_csv(OUT / '模型清单.csv', ['模型ID', '中文模型名', '电池类别', '访问视图', '顶层字段数', '根required', '来源'], exporter.models)
    write_csv(OUT / 'Schema之外的SHACL补充规则.csv', ['模型', '字段路径', '语义必填或禁止要求', '数据类型', '枚举要求', '规则摘录', '来源'], exporter.extra_rules)
    write_csv(OUT / '边界与反例验证.csv', ['模型', '验证输入变更', '结果'], exporter.mutation_checks)
    after = {p.relative_to(ASSETS).as_posix(): sha256(p) for p in ASSETS.rglob('*') if p.is_file()}
    assert before == after
    report = {
        'sourceSchemaCount': len(reader.DOCS), 'sourceFilesUnmodified': before == after,
        'sourceFilesSha256': before, 'allSourcePropertyDefinitionsCovered': len(reader.raw_locations),
        'modelCount': len(exporter.schemas), 'structuralModels': 23, 'profileModels': 12,
        'vocabularyModels': 17, 'vocabularyConcepts': len(exporter.concepts),
        'fieldDescriptionRows': len(exporter.field_rows), 'shaclSupplementRows': len(exporter.extra_rules),
        'profileFieldDescriptionRows': sum(1 for row in exporter.field_rows if re.match(r'^Dpp(EV|LMT|IND)_', row[0])),
        'yamlJsonRoundTrip': 'passed', 'unresolvedStructuralReferences': 0,
        'allComponentJsonSchemasChecked': True, 'enumPatternMismatches': exporter.enum_mismatches,
        'officialProfileExampleChecks': exporter.sample_checks,
        'boundaryAndCounterExampleChecks': len(exporter.mutation_checks),
        'shaclValidationExecuted': False, 'apifoxUiImportTested': False,
        'requiredPolicy': document['x-derived-document']['requiredPolicy'],
        'enumPolicy': document['x-derived-document']['enumPolicy'],
        'artifactSha256': {yaml_path.name: sha256(yaml_path), json_path.name: sha256(json_path)},
    }
    (OUT / '生成与校验报告.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (OUT / '导入与口径说明.txt').write_text(
        'Apifox 导入：项目设置 → 导入数据 → OpenAPI/Swagger → 选择 dpp-semantic-models-apifox.yaml。\n'
        'JSON 文件与 YAML 内容相同，二选一即可。导入预览中选择数据模型；文件没有接口，导入后到“数据模型”中查看。\n'
        '推荐先看 DppEV_PUB、DppLMT_PUB、DppIND_PUB，再按需要查看同类别的 RES1／RES2／RES3。\n'
        '共 40 个模型：12 个电池类别／访问视图、5 个核心及电池组基础模型、6 个通用结构、17 个词表。\n'
        '\n必填：required 勾选只表示源 JSON Schema 的结构必填；嵌套字段仅在父对象存在时生效。\n'
        'manufacturerIdentification、batteryIdentification、manufacturingPlace 等组合规则完整保留，不把组合成员都设成必填。\n'
        '工业电池的可充电、循环应用、BMS 等条件在说明中列出；源模型未提供可直接用于 if/then 的判别字段，不虚构判断逻辑。\n'
        'SHACL 单独要求但当前 JSON Schema 未定义的字段（如受限视图 contentSpecificationIds）、maxCount=0 的禁止字段，列在模型说明和补充规则 CSV。\n'
        '\n类型：保留源字段类型。对象通过 properties 定义但未声明 type 时，导入版显式标注 object。\n'
        '多语言对象的键为 xx-XX，至少一个；源 JSON Schema 没有限定动态键的值类型，导入版保留该边界，说明中标出 SHACL 要求语言文本。\n'
        '\n枚举：受控词表按本地 2026-10-30 TTL 快照展开为标准 enum，附 x-apifox-enum 的名称与逐项说明。\n'
        '业务字段提交完整 URI；语义校验消息模型使用 SEM-... 原始代码。短代码仅用于解释，不能替代字段 URI。\n'
        '特定路径有 SHACL 固定值／允许单位时，按该路径收窄枚举。通用单位模型仍提供完整单位表。\n'
        'observedCharacteristic 的代码表依据是 common-error-code.ttl 中 SEM-COM-MSG-001；源 Schema 仅声明 URI。\n'
        '日期、国家代码外形、contentType、任意文本不依据示例生成封闭枚举。\n'
        '\n范围：这是护照语义数据模型；不是注册接口的完整请求体。注册请求体以 02_OpenAPI／04_单文件导入版 为准。\n'
        '完整 SHACL／SPARQL 校验还需加载原始上下文和词表；本次做本地 Schema、样例、必填、组合、类型及枚举边界核验，未在 Apifox 界面实测。\n'
        '\n复现：在资料包根目录运行 python build_semantic_apifox.py。\n'
        '依赖：rdflib 7.4.x、PyYAML、jsonschema、referencing、openpyxl；同时读取 build_schema_excel.py 与 schema_field_details.py。\n'
        '官方原始文件未修改。逐字段说明、完整枚举项、模型索引及校验记录都在本目录。\n'
        'Apifox 格式依据：https://docs.apifox.com/import-openapi-swagger\n'
        '枚举说明扩展依据：https://docs.apifox.com/x-apifox-enum\n', encoding='utf-8')
    archive = ROOT / 'DPP语义模型_Apifox导入包.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(OUT.iterdir()):
            if path.is_file():
                output.write(path, OUT.name + '/' + path.name)
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
    print(json.dumps({k: report[k] for k in ['modelCount', 'vocabularyConcepts', 'fieldDescriptionRows',
                     'allSourcePropertyDefinitionsCovered', 'boundaryAndCounterExampleChecks', 'sourceFilesUnmodified']}, ensure_ascii=False, indent=2))
    print('YAML:', yaml_path)
    print('ZIP:', archive)


if __name__ == '__main__':
    main()
