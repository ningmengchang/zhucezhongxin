import json
from pathlib import Path

from app.retrieval import query_variants


def test_query_variants_do_not_translate_or_expand_agent_query():
    chinese = "任意中文法规术语"
    english = "arbitrary regulatory term"
    assert query_variants(chinese) == [chinese]
    assert query_variants(english) == [english]


def test_retrieval_source_has_no_domain_translation_tables():
    source = (Path(__file__).parents[1] / "app" / "retrieval.py").read_text(encoding="utf-8")
    assert "ZH_EN_GLOSSARY" not in source
    assert "CRITICAL_PHRASES" not in source
    assert "authorised representative" not in source.lower()
    assert "授权代表" not in source


def test_markdown_output_schema_requires_every_root_property():
    schema_path = Path(__file__).parents[1] / "schemas" / "markdown_answer.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert set(schema["required"]) == set(schema["properties"])
    web_item = schema["properties"]["webSources"]["items"]
    assert set(web_item["required"]) == set(web_item["properties"])
