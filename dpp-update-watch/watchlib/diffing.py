"""Deterministic, offline comparison of published DPP artifacts (Python stdlib only).

Risk labels are review hints inferred from textual/schema changes, never a claim
that a regulator accepts or rejects a passport. Nothing is extracted to disk.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath

MAX_MEMBERS = 2000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 80 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_CHANGES = 500
MAX_VALUE_CHARS = 1600
ANNOTATIONS = {"title", "description", "$comment", "examples", "example", "default", "$schema", "message"}
LOWER_BOUNDS = {"minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties"}
UPPER_BOUNDS = {"maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties"}
STRICT_RULES = {"type", "pattern", "format", "const", "$ref", "allOf", "anyOf", "oneOf", "not", "dependentRequired", "if", "then", "else", "propertyNames", "unevaluatedProperties"}


class ArtifactError(ValueError):
    """The resource cannot safely be used as a published model snapshot."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _reject_constant(value):
    raise ArtifactError("JSON 包含不符合规范的数值：" + value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactError("JSON 包含重复字段：" + key)
        result[key] = value
    return result


def _short(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else _dump(value)
    return value if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "…（截断，完整值见原文件）"


def _kind(path: str) -> str:
    low = path.lower()
    if low.endswith(".schema.json"):
        return "schema"
    if low.endswith((".jsonld", ".json-ld")):
        return "context"
    if low.endswith(".json"):
        return "json"
    if low.endswith(".ttl"):
        return "shacl" if "/shapes/" in low or "shapes" in low else "vocabulary"
    if low.endswith((".yaml", ".yml")):
        return "openapi"
    if low.endswith((".txt", ".md", ".csv", ".xml", ".xsd")):
        return "text"
    return "binary"


def _versions(path: str, text: str) -> list[str]:
    values = re.findall(r"(?:/|version(?:IRI|Info)?[\s\"':=<>]*|\bv)(\d+\.\d+\.\d+)(?=/|[\s\"'<>;,#]|$)", path + "\n" + text, flags=re.I)
    return sorted(set(values), key=lambda x: tuple(map(int, x.split("."))))


def _inspect(data: bytes, kind: str, title: str = ""):
    if not isinstance(data, bytes) or not data:
        raise ArtifactError("资源为空或不是二进制内容。")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArtifactError("资源超过 100MB 下载大小限制。")
    documents = {}
    schemas = {}
    files = {}
    versions = set()
    if "zip" not in kind.lower():
        if "pdf" in kind.lower():
            if not data.startswith(b"%PDF-"):
                raise ArtifactError("指南下载内容不是 PDF，可能是错误页。")
            name = title or "guide.pdf"
            result = {"sha256": _sha(data), "payload_sha256": _sha(data), "files": {name: {"sha256": _sha(data), "payload_sha256": _sha(data), "size": len(data), "kind": "pdf"}}, "schemas": {}, "versions": [], "kind": kind}
            return result, {name: data}
        raise ArtifactError("不支持的资源类型：" + kind)
    if not data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        raise ArtifactError("资源不是有效 ZIP，可能下载到了 HTML 错误页。")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS:
                raise ArtifactError("ZIP 条目数超过 2000。")
            total = 0
            paths = set()
            for member in members:
                path = member.filename
                posix = PurePosixPath(path)
                if "\\" in path or "\x00" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path) or ".." in posix.parts or not posix.parts:
                    raise ArtifactError("ZIP 含不安全路径：" + repr(path))
                normalized = posix.as_posix()
                if normalized in paths:
                    raise ArtifactError("ZIP 包含重复路径：" + normalized)
                paths.add(normalized)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ArtifactError("ZIP 含符号链接：" + normalized)
                if member.flag_bits & 1:
                    raise ArtifactError("不接受加密 ZIP 条目：" + normalized)
                if member.is_dir():
                    continue
                total += member.file_size
                if member.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                    raise ArtifactError("ZIP 解压后大小超出限制（单文件10MB／总计80MB）。")
                if member.file_size > 1024 * 1024 and member.file_size / max(1, member.compress_size) > 200:
                    raise ArtifactError("ZIP 压缩比异常：" + normalized)
                raw = archive.read(member)
                if len(raw) != member.file_size:
                    raise ArtifactError("ZIP 文件大小与声明不一致：" + normalized)
                file_kind = _kind(normalized)
                content = raw
                text = ""
                if file_kind in {"schema", "json", "context", "shacl", "vocabulary", "openapi", "text"}:
                    try:
                        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
                    except UnicodeDecodeError as exc:
                        raise ArtifactError("资源文本不是 UTF-8：" + normalized) from exc
                    content = text.encode("utf-8")
                    if file_kind in {"schema", "json", "context"}:
                        try:
                            parsed = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
                            content = _dump(parsed).encode("utf-8")
                        except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                            raise ArtifactError("JSON 无法解析：" + normalized + "：" + str(exc)[:160]) from exc
                        documents[normalized] = parsed
                        if file_kind == "schema":
                            if not isinstance(parsed, (dict, bool)):
                                raise ArtifactError("JSON Schema 根节点类型无效：" + normalized)
                            schemas[normalized] = parsed
                    else:
                        documents[normalized] = text
                    versions.update(_versions(normalized, text))
                else:
                    documents[normalized] = raw
                files[normalized] = {"sha256": _sha(raw), "payload_sha256": _sha(content), "size": len(raw), "kind": file_kind}
    except ArtifactError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError, RecursionError) as exc:
        raise ArtifactError("ZIP 无法完整读取：" + str(exc)[:180]) from exc
    if not files:
        raise ArtifactError("ZIP 中没有文件。")
    if not any(f["kind"] in {"schema", "json", "context", "shacl", "vocabulary", "openapi"} for f in files.values()):
        raise ArtifactError("ZIP 中未发现语义模型或 OpenAPI 资源。")
    payload = _dump([(path, files[path]["payload_sha256"]) for path in sorted(files)]).encode("utf-8")
    return {"sha256": _sha(data), "payload_sha256": _sha(payload), "files": files, "schemas": schemas, "versions": sorted(versions, key=lambda x: tuple(map(int, x.split(".")))), "kind": kind}, documents


def inspect_artifact(data: bytes, kind: str, title: str = "") -> dict:
    """Validate resource and return hashes, members, schema trees and found versions."""
    return _inspect(data, kind, title)[0]


def _pointer(path: str, part) -> str:
    return path + "/" + str(part).replace("~", "~0").replace("/", "~1")


def _assessment(pointer, before, after, mode):
    keyword = pointer.rsplit("/", 1)[-1]
    if any("/" + annotation + "/" in pointer or pointer.endswith("/" + annotation) for annotation in ANNOTATIONS):
        return "info", "说明、示例或提示信息变化；本条未据此判断数据兼容性。"
    if "/properties/" in pointer and keyword not in STRICT_RULES | LOWER_BOUNDS | UPPER_BOUNDS | {"required", "enum", "additionalProperties", "uniqueItems"}:
        # A whole property added/deleted (nested subfields are handled recursively).
        if pointer.split("/")[-2] == "properties":
            if mode == "removed":
                return "high", "字段定义被移除，可能影响已有数据或映射；需人工确认兼容性。"
            if mode == "added":
                return "medium", "新增字段定义；是否必填需同时查看所属对象 required 的变化。"
    if keyword == "required":
        prev, curr = set(before or []), set(after or [])
        if curr - prev:
            return "high", "新增必填成员，旧数据可能缺少这些字段（规则推断，需人工确认）。"
        return "medium", "必填成员减少或顺序调整；应核对业务适用条件。"
    if keyword == "enum":
        prev = {_dump(x) for x in before or []}
        curr = {_dump(x) for x in after or []}
        if prev - curr:
            return "high", "允许值减少，已有取值可能不再通过校验（规则推断）。"
        return "medium", "允许值集合扩展或重新排列。"
    if keyword in LOWER_BOUNDS | UPPER_BOUNDS:
        if mode == "added":
            return "high", "新增边界约束，可能使旧数据不再通过校验（规则推断）。"
        stricter = isinstance(before, (int, float)) and isinstance(after, (int, float)) and ((keyword in LOWER_BOUNDS and after > before) or (keyword in UPPER_BOUNDS and after < before))
        return ("high", "边界收紧，可能影响已有数据（规则推断）。") if stricter else ("medium", "数值、长度或数量边界调整；需核对具体数据。")
    if keyword == "additionalProperties":
        return ("high", "新增或收紧额外字段限制，旧扩展字段可能被拒绝（规则推断）。") if after is False else ("medium", "额外字段规则调整。")
    if keyword == "uniqueItems":
        return ("high", "新增去重要求，含重复值的旧数组可能不通过（规则推断）。") if after is True else ("medium", "数组去重规则调整。")
    if keyword in STRICT_RULES:
        return "high", "类型、模式、引用或组合约束变化，可能影响已有数据（规则推断，需人工确认）。"
    return "medium", "结构或取值发生变化；需结合上下级规则核对影响。"


def compare_artifacts(oldbytes: bytes, newbytes: bytes, kind: str, title: str = "") -> dict:
    """Compare logical payloads; raw-only ZIP repacking is packaging_only, not a model change."""
    old, olddocs = _inspect(oldbytes, kind, title)
    new, newdocs = _inspect(newbytes, kind, title)
    changed = old["payload_sha256"] != new["payload_sha256"]
    packaging_only = not changed and old["sha256"] != new["sha256"]
    counts = {"files_added": 0, "files_removed": 0, "files_modified": 0, "file_version_moves": 0, "schema_changes": 0, "text_changes": 0, "total_changes": 0, "truncated_changes": 0}
    changes = []

    def add(path, rule, before, after, impact, severity):
        counts["total_changes"] += 1
        if len(changes) < MAX_CHANGES:
            changes.append({"path": path, "rule": rule, "old": _short(before), "new": _short(after), "impact": impact, "severity": severity})

    def jsondiff(file, before, after, pointer="", schema=False):
        # Bool/int compare equal in Python; JSON distinguishes their types.
        if _dump(before) == _dump(after):
            return
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                ptr = _pointer(pointer, key)
                if key not in before:
                    severity, impact = _assessment(ptr, None, after[key], "added")
                    add(file + "#" + ptr, "新增规则／字段", None, after[key], impact, severity)
                    if schema:
                        counts["schema_changes"] += 1
                elif key not in after:
                    severity, impact = _assessment(ptr, before[key], None, "removed")
                    add(file + "#" + ptr, "移除规则／字段", before[key], None, impact, severity)
                    if schema:
                        counts["schema_changes"] += 1
                else:
                    jsondiff(file, before[key], after[key], ptr, schema)
        else:
            severity, impact = _assessment(pointer, before, after, "modified")
            add(file + "#" + (pointer or "/"), "规则／值变化", before, after, impact, severity)
            if schema:
                counts["schema_changes"] += 1

    if changed:
        # Published releases commonly move every file from /1.0.0/ to /1.1.0/.
        # Match unambiguous removed/added file families so rule differences remain
        # visible, while still recording the physical version-path movement.
        gone = set(old["files"]) - set(new["files"])
        added = set(new["files"]) - set(old["files"])
        family = lambda p: re.sub(r"(?<=/)\d+\.\d+\.\d+(?=/|$)", "{version}", p)
        old_families, new_families = {}, {}
        for collection, target in [(gone, old_families), (added, new_families)]:
            for p in collection:
                target.setdefault(family(p), []).append(p)
        aliases = {}
        for key in sorted(set(old_families) & set(new_families)):
            if len(old_families[key]) == 1 and len(new_families[key]) == 1:
                aliases[new_families[key][0]] = old_families[key][0]
        moved_from = set(aliases.values())
        paths = (set(old["files"]) | set(new["files"])) - moved_from
        for path in sorted(paths):
            before_path = aliases.get(path, path)
            if path in aliases:
                counts["file_version_moves"] += 1
                add(path, "版本目录变化", before_path, path, "同一文件家族升级到新版本目录；以下继续比较规则内容。", "info")
            elif path not in old["files"]:
                counts["files_added"] += 1
                add(path, "新增文件", None, new["files"][path], "新增发布资源；需查看内容及适用版本。", "medium")
                continue
            if path not in new["files"]:
                counts["files_removed"] += 1
                add(path, "移除文件", old["files"][path], None, "发布包移除了此文件，可能影响既有引用（规则推断）。", "high")
                continue
            if old["files"][before_path]["payload_sha256"] == new["files"][path]["payload_sha256"]:
                continue
            counts["files_modified"] += 1
            filekind = new["files"][path]["kind"]
            before, after = olddocs[before_path], newdocs[path]
            if filekind in {"json", "context", "schema"}:
                jsondiff(path, before, after, schema=filekind == "schema")
            elif isinstance(before, str) and isinstance(after, str):
                snippets = list(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile="原版本", tofile="新版本", lineterm="", n=2))
                excerpt = "\n".join(snippets[:40])
                if len(snippets) > 40:
                    excerpt += "\n…（差异超过40行，完整内容见原文件）"
                keywords = sorted(set(re.findall(r"(?:sh:(?:minCount|maxCount|datatype|hasValue|pattern|or|and|in)|skos:(?:definition|scopeNote)|owl:versionInfo|SEM-[A-Z-]+-\d+|required|enum|minimum|maximum)", excerpt)))
                severity = "high" if re.search(r"sh:(?:minCount|maxCount|datatype|hasValue|pattern)|\brequired\b", excerpt) else "medium"
                add(path, "文本规则变化" + ("：" + "、".join(keywords[:10]) if keywords else ""), None, excerpt, "文本差异摘录；未执行完整RDF推理或兼容性证明，需核对上下文。", severity)
                counts["text_changes"] += 1
            else:
                add(path, "文件内容变化", old["files"][before_path]["sha256"], new["files"][path]["sha256"], "文件字节变化；请打开新旧文件核对说明及版本。", "info")
    counts["truncated_changes"] = max(0, counts["total_changes"] - len(changes))
    severity = max((c["severity"] for c in changes), key={"info": 0, "medium": 1, "high": 2}.get, default="info")
    if changed:
        summary = f"资源内容变化：新增{counts['files_added']}个文件、移除{counts['files_removed']}个文件、修改{counts['files_modified']}个文件" + (f"、{counts['file_version_moves']}个文件升级版本目录" if counts['file_version_moves'] else "") + f"；共{counts['total_changes']}项差异。"
    elif packaging_only:
        summary = "下载文件字节变化，但模型内容相同（ZIP重新打包、JSON排版或换行变化）。"
    else:
        summary = "资源内容未变化。"
    return {"changed": changed, "packaging_only": packaging_only, "summary": summary, "severity": severity, "changes": changes, "versions_before": old["versions"], "versions_after": new["versions"], "counts": counts}
