import io
import json
from pathlib import Path
import stat
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from watchlib.diffing import ArtifactError, compare_artifacts, inspect_artifact


def archive(files, year=2026, compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as z:
        for path, value in files.items():
            item = zipfile.ZipInfo(path, date_time=(year, 1, 1, 0, 0, 0))
            item.compress_type = compression
            z.writestr(item, json.dumps(value) if isinstance(value, dict) else value)
    return buffer.getvalue()


class DiffingTests(unittest.TestCase):
    def test_repacked_zip_and_json_key_order_do_not_fake_model_change(self):
        old = archive({"v/1.0.0/a.schema.json": '{"type":"object","required":["a"]}', "v/shape.ttl": "x\ny\n"}, 2025)
        new = archive({"v/shape.ttl": "x\r\ny\r\n", "v/1.0.0/a.schema.json": '{ "required": ["a"], "type": "object" }'}, 2026, zipfile.ZIP_STORED)
        result = compare_artifacts(old, new, "semantic_zip")
        self.assertFalse(result["changed"])
        self.assertTrue(result["packaging_only"])
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["versions_after"], ["1.0.0"])

    def test_new_required_and_stricter_bounds_are_high_review_priority(self):
        old = archive({"a.schema.json": {"type": "object", "properties": {"a": {"type": "integer", "minimum": 0}}, "required": []}})
        new = archive({"a.schema.json": {"type": "object", "properties": {"a": {"type": "integer", "minimum": 1}}, "required": ["a"]}})
        result = compare_artifacts(old, new, "semantic_zip")
        self.assertTrue(result["changed"])
        self.assertEqual(result["severity"], "high")
        self.assertEqual({c["path"] for c in result["changes"]}, {"a.schema.json#/required", "a.schema.json#/properties/a/minimum"})
        self.assertEqual(result["counts"]["schema_changes"], 2)

    def test_removed_field_and_enum_value_are_high(self):
        old = archive({"a.schema.json": {"properties": {"gone": {"type": "string"}, "status": {"enum": ["a", "b"]}}}})
        new = archive({"a.schema.json": {"properties": {"status": {"enum": ["a"]}}}})
        result = compare_artifacts(old, new, "semantic_zip")
        self.assertEqual([x["severity"] for x in result["changes"]], ["high", "high"])

    def test_schema_pointer_escapes_slash_and_tilde(self):
        a = archive({"a.schema.json": {"properties": {"a/b~c": {"type": "string"}}}})
        b = archive({"a.schema.json": {"properties": {"a/b~c": {"type": "number"}}}})
        self.assertEqual(compare_artifacts(a, b, "semantic_zip")["changes"][0]["path"], "a.schema.json#/properties/a~1b~0c/type")

    def test_json_bool_and_number_have_distinct_types(self):
        a = archive({"a.schema.json": {"const": True}})
        b = archive({"a.schema.json": {"const": 1}})
        result = compare_artifacts(a, b, "semantic_zip")
        self.assertTrue(result["changed"])
        self.assertEqual(len(result["changes"]), 1)

    def test_malicious_paths_are_rejected_without_extracting(self):
        for path in ["../a.schema.json", "/a.schema.json", "C:/a.schema.json", "x\\a.schema.json", "x/../../a.schema.json"]:
            with self.subTest(path=path), self.assertRaises(ArtifactError):
                inspect_artifact(archive({path: {"type": "object"}}), "semantic_zip")

    def test_symlink_is_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            item = zipfile.ZipInfo("a.schema.json")
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(item, "/etc/passwd")
        with self.assertRaises(ArtifactError):
            inspect_artifact(buffer.getvalue(), "semantic_zip")

    def test_invalid_json_and_duplicate_keys_rejected(self):
        for payload in ['{"broken":', '{"a":1,"a":2}', '{"a":NaN}']:
            with self.subTest(payload=payload), self.assertRaises(ArtifactError):
                inspect_artifact(archive({"a.schema.json": payload}), "semantic_zip")

    def test_invalid_download_cannot_become_new_baseline(self):
        old = archive({"a.schema.json": {"type": "object"}})
        for new in [b"<html>gateway error</html>", b"PK\x03\x04truncated", b""]:
            with self.subTest(new=new), self.assertRaises(ArtifactError):
                compare_artifacts(old, new, "semantic_zip")
        self.assertEqual(inspect_artifact(old, "semantic_zip")["schemas"]["a.schema.json"], {"type": "object"})

    def test_archive_member_limit_rejected(self):
        with self.assertRaises(ArtifactError):
            inspect_artifact(archive({str(i) + ".json": {} for i in range(2001)}), "semantic_zip")

    def test_suspicious_compression_rejected(self):
        with self.assertRaises(ArtifactError):
            inspect_artifact(archive({"big.json": b" " * (2 * 1024 * 1024)}), "semantic_zip")

    def test_ttl_changes_keep_rule_evidence(self):
        old = archive({"shapes/a.ttl": '@prefix sh: <http://www.w3.org/ns/shacl#> .\nsh:minCount 0 .\n'})
        new = archive({"shapes/a.ttl": '@prefix sh: <http://www.w3.org/ns/shacl#> .\nsh:minCount 1 .\n'})
        result = compare_artifacts(old, new, "semantic_zip")
        self.assertEqual(result["severity"], "high")
        self.assertIn("sh:minCount", result["changes"][0]["new"])

    def test_diff_count_bounded_and_values_truncated(self):
        old = archive({"a.schema.json": {"properties": {}}})
        new = archive({"a.schema.json": {"properties": {"f" + str(i): {"description": "a" * 2000} for i in range(600)}}}, compression=zipfile.ZIP_STORED)
        result = compare_artifacts(old, new, "semantic_zip")
        self.assertEqual(len(result["changes"]), 500)
        self.assertEqual(result["counts"]["truncated_changes"], 100)
        self.assertLess(len(result["changes"][0]["new"]), 1700)

    def test_pdf_checks_magic_and_reports_bytes_change(self):
        with self.assertRaises(ArtifactError):
            inspect_artifact(b"<!doctype html>", "guide_pdf")
        result = compare_artifacts(b"%PDF-1.7\nold", b"%PDF-1.7\nnew", "guide_pdf", "guide.pdf")
        self.assertTrue(result["changed"])
        self.assertEqual(result["counts"]["files_modified"], 1)

    def test_optional_description_only_change_is_information(self):
        old = archive({"a.schema.json": {"description": "old"}})
        new = archive({"a.schema.json": {"description": "new"}})
        self.assertEqual(compare_artifacts(old, new, "semantic_zip")["severity"], "info")

    def test_version_directory_upgrade_still_reports_actual_rule_difference(self):
        old = archive({"product/1.0.0/a.schema.json": {"type": "integer", "minimum": 0}})
        new = archive({"product/1.1.0/a.schema.json": {"type": "integer", "minimum": 1}})
        result = compare_artifacts(old, new, "semantic_zip")
        self.assertEqual(result["counts"]["file_version_moves"], 1)
        self.assertEqual(result["counts"]["schema_changes"], 1)
        self.assertEqual(result["versions_after"], ["1.1.0"])
        self.assertIn("product/1.1.0/a.schema.json#/minimum", {c["path"] for c in result["changes"]})
        self.assertEqual(result["severity"], "high")


if __name__ == "__main__":
    unittest.main()
