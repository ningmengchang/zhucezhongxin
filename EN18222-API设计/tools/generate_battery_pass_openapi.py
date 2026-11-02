#!/usr/bin/env python3
"""Generate the EV February-2027 BatteryPass-Ready OpenAPI profile.

The source ``openapi.yaml`` is read-only input. The generated profile is written
to a separate file so the initial EN 18222 implementation profile is retained.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import openpyxl
import yaml


BASE_DIR = Path(__file__).resolve().parents[1]
SOURCE_OPENAPI = BASE_DIR / "openapi.yaml"
OUTPUT_OPENAPI = BASE_DIR / "openapi-battery-pass-ready-v2.0-ev-feb27.yaml"
REFERENCE_DIR = BASE_DIR / "参考资料" / "BatteryPass-Ready-v2.0"
SOURCE_SCHEMA = (
    REFERENCE_DIR
    / "BatteryPassDataModel"
    / "BatteryPass-Ready"
    / "JSONSchemaFiles"
    / "v2.0"
    / "EV_Feb27_2.0.json"
)
SOURCE_LONGLIST = REFERENCE_DIR / "2026_BatteryPass-Ready_DataAttributeLongList_v2.0.xlsx"
MODEL_COMMIT = "9ff729f6a917f4f4aa7830891902d3cb2dba6d0d"
DICTIONARY_BASE = "https://example.com/dictionaries/battery-pass-ready/2.0/ev-feb27"

PUBLIC_HEADER_FIELDS = (
    "digitalProductPassportId",
    "uniqueProductIdentifier",
    "granularity",
    "dppSchemaVersion",
    "dppStatus",
    "lastUpdated",
    "economicOperatorId",
    "facilityId",
    "contentSpecificationIds",
)
PUBLIC_REQUIRED_HEADER_FIELDS = (
    "digitalProductPassportId",
    "uniqueProductIdentifier",
    "granularity",
    "dppSchemaVersion",
    "dppStatus",
    "lastUpdated",
    "economicOperatorId",
)


ATTRIBUTE_KEY_ALIASES = {
    "materialsUsedinCathodeAnodeAndElectrolyte": "materialsUsedInCathodeAnodeAndElectrolyte",
    "stateOfCertifiedEnergy": "stateOfCertifiedEnergySOCE",
    "stateOfCharge": "stateOfChargeSoC",
    "internalResistanceIncreaseOfPackCell AndModuleRecommended": (
        "internalResistanceIncreaseOfPackCellAndModuleRecommended"
    ),
    "expectedLifetimeNumberofChargeDischargeCycles": (
        "expectedLifetimeNumberOfChargeDischargeCycles"
    ),
}


class OpenApiDumper(yaml.SafeDumper):
    """Readable YAML without anchors introduced by shared Python objects."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _represent_string(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


OpenApiDumper.add_representer(str, _represent_string)


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def component_name(definition_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", " ", definition_name).title().replace(" ", "")
    if not safe:
        raise ValueError(f"Cannot build component name from {definition_name!r}")
    return "Bp" + safe[0].upper() + safe[1:]


def load_attribute_records() -> list[dict[str, Any]]:
    workbook = openpyxl.load_workbook(SOURCE_LONGLIST, read_only=True, data_only=True)
    longlist = workbook["Data attribute longlist_v2.0"]
    technical = workbook["Technical references"]

    main_by_number: dict[int, dict[str, Any]] = {}
    for row in range(8, longlist.max_row + 1):
        number = longlist.cell(row, 2).value
        if not isinstance(number, (int, float)):
            continue
        main_by_number[int(number)] = {
            "number": int(number),
            "evApplicability": clean(longlist.cell(row, 4).value),
            "mandatoryFromFeb2027": clean(longlist.cell(row, 10).value).lower(),
            "category": clean(longlist.cell(row, 11).value),
            "subCategory": clean(longlist.cell(row, 12).value),
            "attribute": clean(longlist.cell(row, 13).value),
            "shortDefinition": clean(longlist.cell(row, 17).value),
            "regulationReference": clean(longlist.cell(row, 20).value),
            "unit": clean(longlist.cell(row, 21).value),
            "dataFormat": clean(longlist.cell(row, 22).value),
            "accessRights": clean(longlist.cell(row, 23).value),
            "behaviour": clean(longlist.cell(row, 24).value),
            "updateRequirement": clean(longlist.cell(row, 25).value),
            "granularity": clean(longlist.cell(row, 26).value),
        }

    records: list[dict[str, Any]] = []
    for row in range(10, technical.max_row + 1):
        number = technical.cell(row, 2).value
        if not isinstance(number, (int, float)):
            continue
        number = int(number)
        if number not in main_by_number:
            continue
        record = dict(main_by_number[number])
        technical_name = clean(technical.cell(row, 7).value)
        record.update(
            {
                "technicalName": technical_name,
                "schemaProperty": ATTRIBUTE_KEY_ALIASES.get(technical_name, technical_name),
                "jsonKeys": clean(technical.cell(row, 8).value),
                "jsonKeyDescription": clean(technical.cell(row, 9).value),
                "jsonSchemaTypes": clean(technical.cell(row, 12).value),
            }
        )
        records.append(record)
    if len(records) != 92:
        raise RuntimeError(f"Expected 92 longlist records, found {len(records)}")
    return records


def iter_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_nodes(value)


def normalize_access_rights_and_enums(schema: dict[str, Any]) -> None:
    for node in iter_nodes(schema):
        if "enum" in node and isinstance(node["enum"], list):
            node["x-apifox-enum"] = [
                {"value": value, "name": str(value), "description": ""}
                for value in node["enum"]
            ]
        for source_key in ("x-AccessRights", "x-Access-Rights"):
            raw = node.get(source_key)
            if raw is None:
                continue
            parsed: Any = raw
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = {"raw": raw}
            node["x-battery-pass-access-rights"] = parsed


def enrich_schema_from_longlist(
    schema: dict[str, Any], records: list[dict[str, Any]]
) -> tuple[int, dict[str, int]]:
    applicable = {
        record["schemaProperty"]: record
        for record in records
        if record["mandatoryFromFeb2027"] == "yes"
        and record["evApplicability"] not in ("", "-")
    }
    if len(applicable) != 68:
        raise RuntimeError(f"Expected 68 EV Feb27 attribute mappings, found {len(applicable)}")

    # Only these six object definitions contain the 68 longlist attributes.
    # Nested value objects can reuse an attribute name (for example the outer
    # ``extinguishingAgent`` attribute and its inner string value), so a global
    # name-only tree walk would attach regulatory metadata to the wrong node.
    attribute_definitions = (
        "dppHeader",
        "symbolsLabelsAndDocumentationOfConformity",
        "performanceAndDurability",
        "identifiersAndProductData",
        "circularityAndResourceEfficiency",
        "batteryMaterialsAndComposition",
    )
    definitions = schema.get("$defs", {})
    hits: dict[str, int] = {key: 0 for key in applicable}
    for definition_name in attribute_definitions:
        properties = definitions.get(definition_name, {}).get("properties", {})
        if not isinstance(properties, dict):
            raise RuntimeError(f"Missing properties for definition {definition_name}")
        for property_name, property_schema in properties.items():
            record = applicable.get(property_name)
            if not record or not isinstance(property_schema, dict):
                continue
            hits[property_name] += 1
            property_schema.setdefault("title", record["attribute"])
            if record["shortDefinition"]:
                property_schema.setdefault("description", record["shortDefinition"])
            property_schema["x-battery-pass-ready"] = {
                "attributeNumber": record["number"],
                "category": record["category"],
                "subCategory": record["subCategory"],
                "attribute": record["attribute"],
                "technicalNameInLonglist": record["technicalName"],
                "jsonKeys": record["jsonKeys"],
                "jsonKeyDescription": record["jsonKeyDescription"],
                "evApplicability": record["evApplicability"],
                "mandatoryFromFeb2027": record["mandatoryFromFeb2027"],
                "unit": record["unit"],
                "dataFormat": record["dataFormat"],
                "jsonSchemaTypes": record["jsonSchemaTypes"],
                "accessRights": record["accessRights"],
                "behaviour": record["behaviour"],
                "updateRequirement": record["updateRequirement"],
                "granularity": record["granularity"],
                "regulationReference": record["regulationReference"],
            }
            if record["evApplicability"] == "C":
                property_schema["x-conditionally-required"] = True
    missing = [key for key, count in hits.items() if count == 0]
    duplicates = {key: count for key, count in hits.items() if count > 1}
    if missing or duplicates:
        raise RuntimeError(f"Attribute mapping mismatch; missing={missing}, duplicates={duplicates}")
    return len(applicable), hits


def transform_local_refs(node: Any, component_names: dict[str, str]) -> Any:
    if isinstance(node, list):
        return [transform_local_refs(value, component_names) for value in node]
    if not isinstance(node, dict):
        return node
    transformed: dict[str, Any] = {}
    for key, value in node.items():
        if key == "$ref" and isinstance(value, str) and value.startswith("#/$defs/"):
            definition_name = value.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
            transformed[key] = f"#/components/schemas/{component_names[definition_name]}"
        else:
            transformed[key] = transform_local_refs(value, component_names)
    return transformed


def resolve_schema(node: Any, definitions: dict[str, Any], stack: tuple[str, ...] = ()) -> Any:
    if isinstance(node, list):
        return [resolve_schema(value, definitions, stack) for value in node]
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    if isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/$defs/"):
        name = node["$ref"].removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
        if name in stack:
            raise RuntimeError("Circular schema reference: " + " -> ".join((*stack, name)))
        base = resolve_schema(definitions[name], definitions, (*stack, name))
        siblings = {
            key: resolve_schema(value, definitions, stack)
            for key, value in node.items()
            if key != "$ref"
        }
        return {**base, **siblings}
    return {
        key: resolve_schema(value, definitions, stack)
        for key, value in node.items()
        if key != "$defs"
    }


def allow_null(schema: Any) -> dict[str, Any]:
    if schema is False:
        return {"type": "null"}
    if isinstance(schema, dict):
        if schema.get("type") == "null":
            return schema
        for keyword in ("anyOf", "oneOf"):
            if isinstance(schema.get(keyword), list) and any(
                isinstance(option, dict) and option.get("type") == "null"
                for option in schema[keyword]
            ):
                return schema
    return {"anyOf": [schema, {"type": "null"}]}


def make_merge_patch_schema(node: Any, inside_array: bool = False) -> Any:
    if isinstance(node, list):
        return [make_merge_patch_schema(value, inside_array) for value in node]
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    if inside_array:
        return copy.deepcopy(node)

    result = copy.deepcopy(node)
    properties = node.get("properties")
    if isinstance(properties, dict):
        original_required = set(node.get("required", []))
        result.pop("required", None)
        result["properties"] = {}
        for name, property_schema in properties.items():
            if isinstance(property_schema, dict) and property_schema.get("type") == "array":
                patched = copy.deepcopy(property_schema)
            else:
                patched = make_merge_patch_schema(property_schema)
            if name not in original_required:
                patched = allow_null(patched)
            result["properties"][name] = patched
    if "items" in node:
        result["items"] = make_merge_patch_schema(node["items"], inside_array=True)
    return result


STRING_EXAMPLES = {
    "dppSchemaVersion": "BatteryPass-Ready:2.0:EV:Feb2027",
    "contentSpecificationIds": "BatteryPass-Ready:2.0:EV:Feb2027",
    "digitalProductPassportId": "https://passport.example.com/dpp/ev-battery-000001",
    "uniqueProductIdentifier": "https://id.example.com/batteries/ev-battery-000001",
    "economicOperatorId": "https://id.example.com/operators/economic-operator-001",
    "facilityId": "https://id.example.com/facilities/shanghai-battery-plant-01",
    "thirdPartyServiceProvider": "Example DPP Service Provider",
    "batteryModelIdentifier": "EV-NMC-PACK-2027-001",
    "batterySerialNumber": "EVBAT-SN-000001",
    "uniqueManufacturerIdentifier": "https://id.example.com/operators/battery-manufacturer-001",
    "manufacturingPlace": "Shanghai, China",
    "name": "Example Battery Manufacturer",
    "registeredTradeNameOrRegisteredTrademark": "Example Battery",
    "postalAddress": "100 Example Road, Shanghai, China",
    "resourceTitle": "Battery passport supporting document",
    "contentType": "application/pdf",
    # The upstream 2.0 schema's RelatedResource regex double-escapes ``\\s``;
    # this URI remains both realistic and valid against that published pattern.
    "url": "https://example.com/battery-document.pdf",
    "webAddress": "https://www.example-battery.com",
    "eMailAddress": "dpp@example-battery.com",
    "substanceName": "Lithium nickel manganese cobalt oxide",
    "identifier": "CAS No 182442-95-1",
    "cycleLifeReferenceTest": "IEC 62660-1 cycle-life test",
}


NUMBER_EXAMPLES = {
    "amperehourMiliamperehourValue": 180,
    "kilowattHourValue": 75.6,
    "percentageValue": 80,
    "gramKilogramValue": 480,
    "voltValue": 400,
    "wattValue": 250000,
    "wattValueAt80PercentStateOfCharge": 240000,
    "wattValueAt20PercentStateOfCharge": 180000,
    "amperePerAmpereHourValue": 1,
    "ohmValueForBatteryModule": 2,
    "ohmValueForBatteryPack": 4,
    "ohmValueForBatteryCell": 1,
    "temperatureRangeUpperBoundaryValue": 45,
    "temperatureRangeLowerBoundaryValue": -20,
    "celsiusValue": 25,
}


def value_within_bounds(value: int | float, schema: dict[str, Any]) -> int | float:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def sample_value(schema: Any, definitions: dict[str, Any], path: tuple[str, ...] = ()) -> Any:
    schema = resolve_schema(schema, definitions)
    if not isinstance(schema, dict):
        return None
    name = path[-1] if path else "root"
    if "const" in schema:
        return copy.deepcopy(schema["const"])
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        if name == "chemicalCodeValue" and "Li-ion NMC" in schema["enum"]:
            return "Li-ion NMC"
        if name == "dppStatusValue" and "active" in schema["enum"]:
            return "active"
        if name == "language" and "en-GB" in schema["enum"]:
            return "en-GB"
        return copy.deepcopy(schema["enum"][0])
    for keyword in ("oneOf", "anyOf"):
        if isinstance(schema.get(keyword), list) and schema[keyword]:
            return sample_value(schema[keyword][0], definitions, path)

    schema_type = schema.get("type")
    if schema_type == "object" or isinstance(schema.get("properties"), dict):
        return {
            property_name: sample_value(property_schema, definitions, (*path, property_name))
            for property_name, property_schema in schema.get("properties", {}).items()
        }
    if schema_type == "array":
        item_schema = schema.get("items", {})
        count = max(1, int(schema.get("minItems", 1)))
        return [sample_value(item_schema, definitions, (*path, f"item{index + 1}")) for index in range(count)]
    if schema_type == "string":
        if name in STRING_EXAMPLES:
            return STRING_EXAMPLES[name]
        fmt = schema.get("format")
        if fmt == "uri":
            return f"https://example.com/resources/{re.sub(r'[^A-Za-z0-9-]+', '-', name).lower()}"
        if fmt == "date-time":
            return "2027-02-18T08:00:00Z"
        if fmt == "date":
            return "2027-02-18"
        if fmt == "duration":
            return "P8Y"
        minimum = int(schema.get("minLength", 1))
        value = "example"
        return value if len(value) >= minimum else value + "x" * (minimum - len(value))
    if schema_type in ("integer", "number"):
        default = NUMBER_EXAMPLES.get(name, 1)
        value = value_within_bounds(default, schema)
        return int(value) if schema_type == "integer" else float(value)
    if schema_type == "boolean":
        return True
    if schema_type == "null":
        return None
    return "example"


def schema_annotations(schema: dict[str, Any]) -> dict[str, Any]:
    """Return documentation/vendor annotations without the raw value constraint."""

    return {
        key: copy.deepcopy(value)
        for key, value in schema.items()
        if key in ("title", "description", "deprecated", "readOnly", "writeOnly")
        or key.startswith("x-")
    }


def public_header_properties(
    source_header: dict[str, Any],
    definitions: dict[str, Any],
    component_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Adapt the BatteryPass header to EN 18223's flat JSON serialization.

    BatteryPass-Ready 2.0 wraps granularity/status in value objects and models
    ``contentSpecificationIds`` as one string. EN 18223 serializes the header
    attributes directly, with scalar granularity/status and a 0..* identifier
    list. When ``component_names`` is supplied, OpenAPI component refs are used;
    otherwise a fully resolved JSON Schema is returned for merge-patch creation.
    """

    source_properties = source_header["properties"]
    adapted: dict[str, Any] = {}
    for name in PUBLIC_HEADER_FIELDS:
        source_property = source_properties[name]
        annotations = schema_annotations(source_property)
        if name == "granularity":
            constraint = (
                {"$ref": "#/components/schemas/Granularity"}
                if component_names is not None
                else resolve_schema(definitions["dppGranularityCodes"], definitions)
            )
            adapted[name] = {**constraint, **annotations}
        elif name == "dppStatus":
            constraint = (
                {"$ref": "#/components/schemas/DppStatus"}
                if component_names is not None
                else resolve_schema(definitions["dppStatusCodes"], definitions)
            )
            adapted[name] = {**constraint, **annotations}
        elif name == "contentSpecificationIds":
            adapted[name] = {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
                **annotations,
                "description": (
                    "References to horizontal or product-type content specifications. "
                    "EN 18223 cardinality is 0..*; the public API therefore uses an array."
                ),
            }
        elif component_names is not None:
            adapted[name] = transform_local_refs(source_property, component_names)
        else:
            adapted[name] = resolve_schema(source_property, definitions)
        adapted[name]["x-battery-pass-ready-source-path"] = (
            "dppHeader.granularity.dppGranularityValue"
            if name == "granularity"
            else "dppHeader.dppStatus.dppStatusValue"
            if name == "dppStatus"
            else "dppHeader.contentSpecificationIds"
            if name == "contentSpecificationIds"
            else f"dppHeader.{name}"
        )
    return adapted


def to_public_compressed(raw_document: dict[str, Any]) -> dict[str, Any]:
    """Map the raw BatteryPass-Ready 2.0 envelope to the public EN 18223 view."""

    header = raw_document["dppHeader"]
    public_document: dict[str, Any] = {
        "digitalProductPassportId": header["digitalProductPassportId"],
        "uniqueProductIdentifier": header["uniqueProductIdentifier"],
        "granularity": header["granularity"]["dppGranularityValue"],
        "dppSchemaVersion": header["dppSchemaVersion"],
        "dppStatus": header["dppStatus"]["dppStatusValue"],
        "lastUpdated": header["lastUpdated"],
        "economicOperatorId": header["economicOperatorId"],
    }
    if "facilityId" in header:
        public_document["facilityId"] = header["facilityId"]
    if "contentSpecificationIds" in header:
        content_ids = header["contentSpecificationIds"]
        public_document["contentSpecificationIds"] = (
            content_ids if isinstance(content_ids, list) else [content_ids]
        )
    public_document["batteryPassportData"] = copy.deepcopy(raw_document["batteryPassportData"])
    return public_document


def infer_xsd_type(value: Any) -> str:
    if isinstance(value, bool):
        return "xsd:boolean"
    if isinstance(value, int):
        return "xsd:integer"
    if isinstance(value, float):
        return "xsd:decimal"
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*Z", value):
            return "xsd:dateTime"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return "xsd:date"
        if value.startswith(("https://", "http://", "urn:")):
            return "xsd:anyURI"
    return "xsd:string"


def dictionary_reference(path: tuple[str, ...]) -> str:
    safe_parts = [re.sub(r"[^A-Za-z0-9._~-]+", "-", part) for part in path]
    return DICTIONARY_BASE + "/" + "/".join(safe_parts)


def to_full_element(element_id: str, value: Any, path: tuple[str, ...]) -> dict[str, Any]:
    base = {
        "elementId": element_id,
        "dictionaryReference": dictionary_reference(path),
    }
    if isinstance(value, dict):
        if "contentType" in value and "url" in value:
            return {
                **base,
                "objectType": "RelatedResource",
                "contentType": value["contentType"],
                "url": value["url"],
                **({"language": value["language"]} if value.get("language") else {}),
                **({"resourceTitle": value["resourceTitle"]} if value.get("resourceTitle") else {}),
            }
        return {
            **base,
            "objectType": "DataElementCollection",
            "elements": [
                to_full_element(child_name, child_value, (*path, child_name))
                for child_name, child_value in value.items()
            ],
        }
    if isinstance(value, list):
        return {
            **base,
            "objectType": "MultiValuedDataElement",
            "value": [
                to_full_element(f"item-{index + 1}", item, (*path, f"item-{index + 1}"))
                for index, item in enumerate(value)
            ],
        }
    return {
        **base,
        "objectType": "SingleValuedDataElement",
        "valueDataType": infer_xsd_type(value),
        "value": value,
    }


def remove_parameter(operation: dict[str, Any], component: str) -> None:
    operation["parameters"] = [
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("$ref") != f"#/components/parameters/{component}"
    ]
    if not operation["parameters"]:
        operation.pop("parameters", None)


def clone_dpp_response(
    original_response: dict[str, Any], schema_ref: str, example: dict[str, Any], description: str
) -> dict[str, Any]:
    response = copy.deepcopy(original_response)
    response["description"] = description
    content = response["content"]["application/json"]
    content["schema"] = {"$ref": schema_ref}
    content["examples"] = {"compressed": {"summary": "BatteryPass-Ready compressed DPP", "value": example}}
    return response


def build_openapi() -> dict[str, Any]:
    source_hash = sha256(SOURCE_OPENAPI)
    document = yaml.safe_load(SOURCE_OPENAPI.read_text(encoding="utf-8"))
    source_schema = json.loads(SOURCE_SCHEMA.read_text(encoding="utf-8"))
    records = load_attribute_records()

    attribute_count, _ = enrich_schema_from_longlist(source_schema, records)
    normalize_access_rights_and_enums(source_schema)
    definitions = source_schema["$defs"]
    component_names = {name: component_name(name) for name in definitions}

    bp_components = {
        component_names[name]: transform_local_refs(schema, component_names)
        for name, schema in definitions.items()
    }
    raw_root_schema = {
        key: value
        for key, value in source_schema.items()
        if key not in ("$schema", "$defs")
    }
    raw_root_schema = transform_local_refs(raw_root_schema, component_names)
    raw_root_schema.update(
        {
            "title": "Raw BatteryPass-Ready EV February 2027 v2.0 envelope",
            "description": (
                "Unmodified structural envelope from EV_Feb27_2.0.json. It retains the BatteryPass-Ready "
                "dppHeader + batteryPassportData split and is an internal validation/mapping model, not the "
                "EN 18222 public response representation."
            ),
            "x-visibility": "internal",
            "x-battery-pass-ready-model-version": "2.0",
            "x-battery-category": "EV",
            "x-requirement-profile": "February 2027",
            "x-source-schema": "EV_Feb27_2.0.json",
            "x-source-model-commit": MODEL_COMMIT,
            "x-attribute-count": attribute_count,
        }
    )

    resolved_root = resolve_schema(source_schema, definitions)
    resolved_root.pop("$schema", None)
    public_properties = public_header_properties(
        source_schema["$defs"]["dppHeader"], definitions, component_names
    )
    public_properties["batteryPassportData"] = {
        "$ref": f"#/components/schemas/{component_names['batteryPassportMaster']}"
    }
    public_root_schema = {
        "type": "object",
        "properties": public_properties,
        "required": [*PUBLIC_REQUIRED_HEADER_FIELDS, "batteryPassportData"],
        "title": "EN 18223 public DPP — BatteryPass-Ready EV February 2027",
        "description": (
            "Public compressed DPP representation. EN 18223 header attributes are serialized directly at "
            "the DPP root; batteryPassportData retains the BatteryPass-Ready 2.0 EV February-2027 business "
            "model. It contains 68 attribute-level properties. Conditional applicability must also be "
            "evaluated against the Data Attribute Longlist v2.0."
        ),
        "x-battery-pass-ready-model-version": "2.0",
        "x-battery-category": "EV",
        "x-requirement-profile": "February 2027",
        "x-source-schema": "EV_Feb27_2.0.json",
        "x-source-model-commit": MODEL_COMMIT,
        "x-source-envelope-component": "BatteryPassReadyRawEvFeb27",
        "x-public-header-serialization": "EN 18223 flat",
        "x-attribute-count": attribute_count,
    }

    resolved_public_properties = public_header_properties(
        source_schema["$defs"]["dppHeader"], definitions
    )
    resolved_public_properties["batteryPassportData"] = copy.deepcopy(
        resolved_root["properties"]["batteryPassportData"]
    )
    resolved_public_root = {
        "type": "object",
        "properties": resolved_public_properties,
        "required": [*PUBLIC_REQUIRED_HEADER_FIELDS, "batteryPassportData"],
    }
    patch_schema = make_merge_patch_schema(resolved_public_root)
    patch_schema["title"] = "EN 18223 public BatteryPass EV February 2027 merge patch"
    patch_schema["description"] = (
        "Compressed RFC 7396 merge patch for the public flat-header representation. All properties are "
        "optional in the patch; optional source properties may be removed with null. The merged complete "
        "DPP must pass BatteryPassReadyEvFeb27."
    )
    patch_schema["minProperties"] = 1
    patch_schema["properties"]["digitalProductPassportId"] = False
    patch_schema["properties"]["uniqueProductIdentifier"] = False

    raw_example = sample_value(source_schema, definitions)
    compressed_example = to_public_compressed(raw_example)
    full_header = {
        name: copy.deepcopy(compressed_example[name])
        for name in PUBLIC_HEADER_FIELDS
        if name in compressed_example
    }
    full_example = {
        **full_header,
        "elements": [
            to_full_element(group_name, group_value, ("batteryPassportData", group_name))
            for group_name, group_value in compressed_example["batteryPassportData"].items()
        ],
    }
    remaining_capacity = compressed_example["batteryPassportData"]["PerformanceAndDurability"][
        "remainingCapacity"
    ]
    remaining_capacity_full = to_full_element(
        "remainingCapacity",
        remaining_capacity,
        ("batteryPassportData", "PerformanceAndDurability", "remainingCapacity"),
    )

    info = document["info"]
    info["title"] = "EN 18222:2026 DPP API — BatteryPass-Ready EV Feb 2027 profile"
    info["version"] = "1.2.0-battery-pass-ready-2.0-ev-feb27"
    info["description"] = (
        "面向开发和联调的 EN 18222:2026 REST/HTTPS API 配置文件，集成 BatteryPass-Ready "
        "Data Model 2.0 的 EV February 2027 产品数据模型。\n\n"
        "- EN 18222 路径、状态码和生命周期语义继承自初始实施配置文件。\n"
        "- 公共 DPP 按 EN 18223 将头部字段直接序列化在根对象；不暴露 dppHeader 包装层。\n"
        "- batteryPassportData 保留 BatteryPass-Ready 业务结构；原始双根对象模型仅作为内部映射组件。\n"
        "- 创建和更新只接受公共 flat-header compressed JSON。\n"
        "- 读取支持 representation=compressed 或 representation=full；full 是从同一 compressed "
        "规范数据生成的 EN 18223 DataElement 表示。\n"
        "- BatteryPass-Ready 模型仍为 Work in progress，条件必填必须结合 Data Attribute Longlist v2.0。\n"
        "- full 示例中的 dictionaryReference 使用 example.com 占位 URI，不代表欧盟官方数据字典。"
    )
    document["x-derived-from-openapi-sha256"] = source_hash
    document["x-battery-pass-ready"] = {
        "modelVersion": "2.0",
        "batteryCategory": "EV",
        "requirementProfile": "February 2027",
        "sourceSchema": "EV_Feb27_2.0.json",
        "sourceCommit": MODEL_COMMIT,
        "writeRepresentation": "compressed",
        "readRepresentations": ["compressed", "full"],
        "publicHeaderSerialization": "EN 18223 flat",
        "rawSourceEnvelope": "dppHeader + batteryPassportData (internal only)",
        "dictionaryReferenceStatus": "implementation placeholder",
    }

    schemas = document["components"]["schemas"]
    schemas.update(bp_components)
    schemas["Granularity"] = {
        **copy.deepcopy(bp_components[component_names["dppGranularityCodes"]]),
        "description": "EN 18223 scalar granularity; the EV February-2027 profile permits item only.",
    }
    schemas["DppStatus"] = {
        **copy.deepcopy(bp_components[component_names["dppStatusCodes"]]),
        "description": "EN 18223 scalar DPP digital-resource status using the BatteryPass-Ready 2.0 code list.",
    }
    schemas["BatteryPassReadyRawEvFeb27"] = raw_root_schema
    schemas["BatteryPassReadyEvFeb27"] = public_root_schema
    schemas["BatteryPassReadyEvFeb27MergePatch"] = patch_schema
    schemas["DppHeader"] = {
        "type": "object",
        "properties": copy.deepcopy(public_properties),
        "required": list(PUBLIC_REQUIRED_HEADER_FIELDS),
        "description": (
            "EN 18223 public DPP header attributes serialized directly at the DigitalProductPassport root. "
            "This is distinct from the internal BatteryPass-Ready dppHeader envelope."
        ),
    }
    schemas["DppHeader"]["properties"].pop("batteryPassportData")
    schemas["CompressedDigitalProductPassport"] = {
        "allOf": [{"$ref": "#/components/schemas/BatteryPassReadyEvFeb27"}],
        "description": (
            "Canonical writable public representation: flat EN 18223 header plus BatteryPass-Ready "
            "batteryPassportData."
        ),
    }
    schemas["FullDigitalProductPassport"] = {
        "allOf": [
            {"$ref": "#/components/schemas/DppHeader"},
            {
                "type": "object",
                "required": ["elements"],
                "properties": {
                    "elements": {
                        "type": "array",
                        "minItems": 1,
                        "description": "Fully expanded DataElement representation of batteryPassportData.",
                        "items": {"$ref": "#/components/schemas/ExpandedDataElement"},
                    }
                },
            },
        ],
        "description": (
            "Read-only expanded representation. EN 18223 header attributes remain directly at the root; "
            "only BatteryPass business data is expanded into DataElement objects."
        ),
    }
    schemas["DigitalProductPassport"] = {
        "description": "Read response selected by representation=compressed|full.",
        "oneOf": [
            {"$ref": "#/components/schemas/CompressedDigitalProductPassport"},
            {"$ref": "#/components/schemas/FullDigitalProductPassport"},
        ],
    }
    schemas["PartialDigitalProductPassport"] = {
        "allOf": [{"$ref": "#/components/schemas/BatteryPassReadyEvFeb27MergePatch"}],
        "description": "Backward component name for the compressed BatteryPass merge-patch model.",
    }
    schemas["CompressedDppResponse"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["statusCode", "payload"],
        "properties": {
            "statusCode": {"$ref": "#/components/schemas/StatusCode"},
            "payload": {"$ref": "#/components/schemas/CompressedDigitalProductPassport"},
        },
    }

    parameters = document["components"]["parameters"]
    parameters["Representation"]["description"] = (
        "读取表示方式：compressed 返回 EN 18223 根级头部 + BatteryPass-Ready 业务 JSON；"
        "full 保持同一根级头部并返回 DataElement 展开表示。写接口不接受该参数。"
    )

    element_path = parameters["ElementIdPath"]
    element_path["description"] = (
        "BatteryPass-Ready compressed JSON 的 RFC 9535 绝对规范化路径，只能定位一个元素。"
    )
    element_path["example"] = "$.batteryPassportData.PerformanceAndDurability.remainingCapacity"

    compressed_response_example = {"statusCode": "Success", "payload": compressed_example}
    full_response_example = {"statusCode": "Success", "payload": full_example}
    responses = document["components"]["responses"]
    dpp_ok_content = responses["DppOk"]["content"]["application/json"]
    dpp_ok_content["examples"] = {
        "compressed": {
            "summary": "EN 18223 flat-header compressed EV February 2027 DPP",
            "value": compressed_response_example,
        },
        "full": {
            "summary": "EN 18223 full view derived from the same BatteryPass data",
            "value": full_response_example,
        },
    }
    responses["CompressedDppOk"] = clone_dpp_response(
        responses["DppOk"],
        "#/components/schemas/CompressedDppResponse",
        compressed_response_example,
        "Compressed BatteryPass-Ready DPP update succeeded.",
    )

    document["components"]["examples"]["CompressedBatteryDpp"] = {
        "summary": "Public EN 18223 flat-header BatteryPass EV February 2027 DPP",
        "value": compressed_example,
    }
    create = document["paths"]["/v1/dpps"]["post"]
    remove_parameter(create, "Representation")
    create["description"] = (
        "创建 EV February 2027 DPP。请求体只接受 EN 18223 根级头部 + BatteryPass-Ready "
        "batteryPassportData 的 compressed JSON；不接受 dppHeader 包装层；"
        "完整请求必须通过 BatteryPassReadyEvFeb27 校验。"
    )
    create_content = create["requestBody"]["content"]["application/json"]
    create_content["schema"] = {"$ref": "#/components/schemas/CompressedDigitalProductPassport"}
    create_content["examples"] = {
        "evFeb27": {"$ref": "#/components/examples/CompressedBatteryDpp"}
    }

    update = document["paths"]["/v1/dpps/{dppId}"]["patch"]
    remove_parameter(update, "Representation")
    update["description"] = (
        "只接受公共 flat-header compressed RFC 7396 merge patch。先将补丁合并到当前完整 DPP，"
        "再按 BatteryPassReadyEvFeb27 校验并原子提交；digitalProductPassportId 和 "
        "uniqueProductIdentifier 不可修改。"
    )
    update_content = update["requestBody"]["content"]["application/merge-patch+json"]
    update_content["schema"] = {"$ref": "#/components/schemas/BatteryPassReadyEvFeb27MergePatch"}
    update_content["examples"] = {
        "remainingCapacity": {
            "summary": "Update remaining capacity in Ah",
            "value": {
                "batteryPassportData": {
                    "PerformanceAndDurability": {
                        "remainingCapacity": {
                            "amperehourMiliamperehourValue": 175,
                            "ampereHourMiliamperehourUnit": "Ah",
                        }
                    }
                }
            },
        },
        "status": {
            "summary": "Update the DPP digital-resource status",
            "value": {"dppStatus": "inactive"},
        },
    }
    update["responses"]["200"] = {"$ref": "#/components/responses/CompressedDppOk"}

    element_get = document["paths"]["/v1/dpps/{dppId}/elements/{elementIdPath}"]["get"]
    element_examples = element_get["responses"]["200"]["content"]["application/json"]["examples"]
    element_examples["compressed"] = {
        "summary": "Compressed remainingCapacity object",
        "value": {"statusCode": "Success", "payload": remaining_capacity},
    }
    element_examples["full"] = {
        "summary": "Full remainingCapacity DataElement collection",
        "value": {"statusCode": "Success", "payload": remaining_capacity_full},
    }

    element_patch = document["paths"]["/v1/dpps/{dppId}/elements/{elementIdPath}"]["patch"]
    remove_parameter(element_patch, "Representation")
    element_patch["description"] = (
        "只接受目标 BatteryPass-Ready compressed 字段值。目标路径决定实际 Schema；服务端更新后必须"
        "校验完整 DPP。JSON null 仅可移除源 Schema 中非 required 的属性。"
    )
    element_patch_content = element_patch["requestBody"]["content"]["application/merge-patch+json"]
    element_patch_content["schema"] = {
        "description": "Compressed target value or null; validated against the Schema at elementIdPath."
    }
    element_patch_content["examples"] = {
        "replaceRemainingCapacity": {"value": {"amperehourMiliamperehourValue": 175, "ampereHourMiliamperehourUnit": "Ah"}},
        "removeOptionalElement": {"value": None},
    }
    element_patch_ok = element_patch["responses"]["200"]
    element_patch_ok["description"] = (
        "Data element updated successfully; the response payload is the resulting compressed value."
    )
    element_patch_ok["content"]["application/json"]["examples"] = {
        "remainingCapacity": {
            "value": {
                "statusCode": "Success",
                "payload": {
                    "amperehourMiliamperehourValue": 175,
                    "ampereHourMiliamperehourUnit": "Ah",
                },
            }
        }
    }

    return document


def main() -> None:
    document = build_openapi()
    OUTPUT_OPENAPI.write_text(
        yaml.dump(
            document,
            Dumper=OpenApiDumper,
            sort_keys=False,
            allow_unicode=True,
            width=120,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    print(OUTPUT_OPENAPI)


if __name__ == "__main__":
    main()
