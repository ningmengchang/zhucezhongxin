#!/usr/bin/env python3
"""Validate the generated BatteryPass-Ready EV February-2027 OpenAPI profile."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

import generate_battery_pass_openapi as generator


EXPECTED_ORIGINAL_SHA256 = "7cb3f4bd326910e47089d0d7ec846fa2dc9f3264d0ea155c6a866e59fca43db4"
ATTRIBUTE_DEFINITIONS = (
    "dppHeader",
    "symbolsLabelsAndDocumentationOfConformity",
    "performanceAndDurability",
    "identifiersAndProductData",
    "circularityAndResourceEfficiency",
    "batteryMaterialsAndComposition",
)
READ_OPERATIONS = (
    ("/v1/dpps/{dppId}", "get"),
    ("/v1/dppsByProductId/{productId}", "get"),
    ("/v1/dppsByIdAndDate/{dppId}", "get"),
    ("/v1/dpps/{dppId}/elements/{elementIdPath}", "get"),
)
WRITE_OPERATIONS = (
    ("/v1/dpps", "post"),
    ("/v1/dpps/{dppId}", "patch"),
    ("/v1/dpps/{dppId}/elements/{elementIdPath}", "patch"),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def iter_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_nodes(value)


def json_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"Only local JSON Pointer refs are supported: {pointer}")
    current = document
    for token in pointer[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        else:
            require(isinstance(current, dict) and token in current, f"Unresolved ref: {pointer}")
            current = current[token]
    return current


def parameter_names(document: dict[str, Any], path: str, method: str) -> set[str]:
    path_item = document["paths"][path]
    parameters = [*path_item.get("parameters", []), *path_item[method].get("parameters", [])]
    result: set[str] = set()
    for parameter in parameters:
        if "$ref" in parameter:
            parameter = json_pointer(document, parameter["$ref"])
        result.add(parameter["name"])
    return result


def reachable_local_refs(document: dict[str, Any], root: Any) -> set[str]:
    """Follow local refs from an OpenAPI subtree, tolerating recursive schemas."""

    reached: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/") and ref not in reached:
                reached.add(ref)
                walk(json_pointer(document, ref))
            for key, value in node.items():
                if key != "$ref":
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(root)
    return reached


def validate_openapi_instance(
    document: dict[str, Any], schema_name: str, instance: Any, label: str
) -> None:
    registry = Registry().with_resource(
        "urn:generated-openapi",
        Resource(contents=document, specification=DRAFT202012),
    )
    validator = Draft202012Validator(
        {"$ref": f"urn:generated-openapi#/components/schemas/{schema_name}"},
        registry=registry,
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if errors:
        details = "; ".join(
            f"/{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in errors[:10]
        )
        raise AssertionError(f"{label} failed {schema_name}: {details}")


def validate_path_parameters(document: dict[str, Any]) -> None:
    operation_ids: list[str] = []
    for path, path_item in document["paths"].items():
        template_names = set(re.findall(r"\{([^{}]+)\}", path))
        for method in ("get", "post", "put", "patch", "delete", "options", "head", "trace"):
            operation = path_item.get(method)
            if not operation:
                continue
            operation_id = operation.get("operationId")
            require(bool(operation_id), f"Missing operationId: {method.upper()} {path}")
            operation_ids.append(operation_id)
            parameters = [*path_item.get("parameters", []), *operation.get("parameters", [])]
            declared_path_names: set[str] = set()
            for parameter in parameters:
                if "$ref" in parameter:
                    parameter = json_pointer(document, parameter["$ref"])
                if parameter.get("in") == "path":
                    declared_path_names.add(parameter["name"])
                    require(parameter.get("required") is True, f"Path parameter must be required: {path}")
            require(
                declared_path_names == template_names,
                f"Path parameter mismatch for {method.upper()} {path}: "
                f"template={template_names}, declared={declared_path_names}",
            )
    require(len(operation_ids) == len(set(operation_ids)), "operationId values must be unique")


def validate_full_dictionary_references(full_example: dict[str, Any]) -> None:
    references = [
        node["dictionaryReference"]
        for node in iter_nodes(full_example["elements"])
        if isinstance(node, dict) and "dictionaryReference" in node
    ]
    require(len(references) >= 68, "Full example does not contain the expected expanded elements")
    require(len(references) == len(set(references)), "Full example dictionaryReference values must be unique")
    require(
        all(reference.startswith(generator.DICTIONARY_BASE + "/") for reference in references),
        "Full example contains a non-placeholder dictionaryReference",
    )


def public_to_raw_battery_pass(public_document: dict[str, Any]) -> dict[str, Any]:
    """Independently reconstruct the upstream BatteryPass-Ready 2.0 envelope."""

    header: dict[str, Any] = {
        "granularity": {"dppGranularityValue": public_document["granularity"]},
        "economicOperatorId": public_document["economicOperatorId"],
        "lastUpdated": public_document["lastUpdated"],
        "dppStatus": {"dppStatusValue": public_document["dppStatus"]},
        "dppSchemaVersion": public_document["dppSchemaVersion"],
        "uniqueProductIdentifier": public_document["uniqueProductIdentifier"],
        "digitalProductPassportId": public_document["digitalProductPassportId"],
    }
    if "facilityId" in public_document:
        header["facilityId"] = public_document["facilityId"]
    if "contentSpecificationIds" in public_document:
        content_ids = public_document["contentSpecificationIds"]
        require(isinstance(content_ids, list) and content_ids, "contentSpecificationIds must be a non-empty array")
        # The upstream 2.0 source schema currently models this 0..* EN 18223
        # attribute as a single string. The public contract correctly uses an
        # array; one value is sufficient to validate this deterministic example.
        header["contentSpecificationIds"] = content_ids[0]
    return {
        "dppHeader": header,
        "batteryPassportData": public_document["batteryPassportData"],
    }


def main() -> None:
    require(generator.SOURCE_OPENAPI.exists(), "Original openapi.yaml is missing")
    require(generator.OUTPUT_OPENAPI.exists(), "Generated OpenAPI is missing; run the generator first")

    original_hash = hashlib.sha256(generator.SOURCE_OPENAPI.read_bytes()).hexdigest()
    require(
        original_hash == EXPECTED_ORIGINAL_SHA256,
        f"Original openapi.yaml changed: expected {EXPECTED_ORIGINAL_SHA256}, got {original_hash}",
    )

    document = yaml.safe_load(generator.OUTPUT_OPENAPI.read_text(encoding="utf-8"))
    source_schema = json.loads(generator.SOURCE_SCHEMA.read_text(encoding="utf-8"))
    require(document.get("openapi") == "3.1.0", "Generated document must use OpenAPI 3.1.0")
    require(document.get("x-derived-from-openapi-sha256") == original_hash, "Source hash metadata mismatch")
    require(document["x-battery-pass-ready"]["writeRepresentation"] == "compressed", "Write policy mismatch")
    require(
        document["x-battery-pass-ready"]["readRepresentations"] == ["compressed", "full"],
        "Read policy mismatch",
    )
    require(
        document["x-battery-pass-ready"]["publicHeaderSerialization"] == "EN 18223 flat",
        "Public header serialization metadata mismatch",
    )

    refs = [node["$ref"] for node in iter_nodes(document) if isinstance(node.get("$ref"), str)]
    require(refs, "No refs found in generated document")
    for ref in refs:
        json_pointer(document, ref)
    require(not any("#/$defs/" in ref for ref in refs), "A JSON Schema $defs ref remains")
    require(not any("$defs" in node for node in iter_nodes(document)), "A $defs object remains")

    for schema_name, schema in document["components"]["schemas"].items():
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # pragma: no cover - keeps the schema name in CLI diagnostics
            raise AssertionError(f"Invalid component schema {schema_name}: {exc}") from exc

    expected_component_names = {
        generator.component_name(name) for name in source_schema["$defs"]
    }
    actual_bp_names = {name for name in document["components"]["schemas"] if name.startswith("Bp")}
    require(len(source_schema["$defs"]) == 42, "Unexpected upstream definition count")
    require(actual_bp_names == expected_component_names, "BatteryPass component conversion mismatch")

    expected_attributes = {
        property_name
        for definition_name in ATTRIBUTE_DEFINITIONS
        for property_name in source_schema["$defs"][definition_name]["properties"]
    }
    required_attribute_count = sum(
        len(source_schema["$defs"][definition_name].get("required", []))
        for definition_name in ATTRIBUTE_DEFINITIONS
    )
    require(len(expected_attributes) == 68, "Upstream EV February-2027 attribute count is not 68")
    require(required_attribute_count == 56, "Upstream required attribute count is not 56")

    bp_nodes = [
        node
        for component_name in actual_bp_names
        for node in iter_nodes(document["components"]["schemas"][component_name])
    ]
    attribute_metadata = [node["x-battery-pass-ready"] for node in bp_nodes if "x-battery-pass-ready" in node]
    require(len(attribute_metadata) == 68, "Expected 68 enriched attribute metadata records")
    require(
        len({metadata["attributeNumber"] for metadata in attribute_metadata}) == 68,
        "Attribute metadata numbers are not unique",
    )
    require(
        all(metadata["mandatoryFromFeb2027"] == "yes" for metadata in attribute_metadata),
        "An enriched field is not in the February-2027 profile",
    )

    source_enum_count = sum("enum" in node for node in iter_nodes(source_schema))
    generated_enum_nodes = [node for node in bp_nodes if "enum" in node]
    require(source_enum_count == 16, "Unexpected upstream enum node count")
    require(len(generated_enum_nodes) == source_enum_count, "Enum conversion count mismatch")
    for enum_node in generated_enum_nodes:
        require("x-apifox-enum" in enum_node, "An enum is missing x-apifox-enum")
        require(
            [item["value"] for item in enum_node["x-apifox-enum"]] == enum_node["enum"],
            "x-apifox-enum values do not match enum",
        )

    access_nodes = [node for node in bp_nodes if "x-battery-pass-access-rights" in node]
    require(len(access_nodes) == 68, "Expected 68 normalized access-right records")
    require(
        document["components"]["schemas"]["BatteryPassReadyEvFeb27"]["x-attribute-count"] == 68,
        "Root schema attribute count metadata mismatch",
    )

    compressed_example = document["components"]["examples"]["CompressedBatteryDpp"]["value"]
    public_schema = document["components"]["schemas"]["BatteryPassReadyEvFeb27"]
    raw_schema = document["components"]["schemas"]["BatteryPassReadyRawEvFeb27"]
    require("dppHeader" not in public_schema["properties"], "Public schema exposes dppHeader")
    require(
        set(raw_schema["properties"]) == {"dppHeader", "batteryPassportData"},
        "Raw BatteryPass component no longer preserves the upstream envelope",
    )
    public_refs = reachable_local_refs(document, document["paths"])
    require(
        "#/components/schemas/BatteryPassReadyRawEvFeb27" not in public_refs,
        "A public operation exposes the raw BatteryPass envelope",
    )
    require(
        "#/components/schemas/BpDppheader" not in public_refs,
        "A public operation exposes the raw BatteryPass dppHeader component",
    )
    require("dppHeader" not in compressed_example, "Public compressed example exposes dppHeader")
    require(isinstance(compressed_example["granularity"], str), "Public granularity must be scalar")
    require(isinstance(compressed_example["dppStatus"], str), "Public dppStatus must be scalar")
    require(
        isinstance(compressed_example["contentSpecificationIds"], list),
        "Public contentSpecificationIds must be an array",
    )
    raw_example = public_to_raw_battery_pass(compressed_example)
    source_errors = sorted(
        Draft202012Validator(source_schema, format_checker=FormatChecker()).iter_errors(raw_example),
        key=lambda error: list(error.absolute_path),
    )
    if source_errors:
        raise AssertionError(
            "Public-to-raw example failed official EV_Feb27_2.0.json: "
            + "; ".join(error.message for error in source_errors[:10])
        )
    validate_openapi_instance(
        document, "BatteryPassReadyRawEvFeb27", raw_example, "Reconstructed raw BatteryPass example"
    )

    dpp_response_examples = document["components"]["responses"]["DppOk"]["content"]["application/json"]["examples"]
    full_example = dpp_response_examples["full"]["value"]["payload"]
    validate_openapi_instance(document, "CompressedDigitalProductPassport", compressed_example, "Compressed example")
    validate_openapi_instance(document, "FullDigitalProductPassport", full_example, "Full example")
    validate_full_dictionary_references(full_example)
    require("dppHeader" not in full_example, "Public full example exposes dppHeader")
    for header_name in generator.PUBLIC_HEADER_FIELDS:
        require(
            full_example.get(header_name) == compressed_example.get(header_name),
            f"Full/compressed header differs: {header_name}",
        )
    require(
        [element["elementId"] for element in full_example["elements"]]
        == list(compressed_example["batteryPassportData"]),
        "Full representation top-level groups differ from compressed data",
    )

    require(set(dpp_response_examples) == {"compressed", "full"}, "DppOk must include both representations")
    for name, example in dpp_response_examples.items():
        validate_openapi_instance(document, "DppResponse", example["value"], f"DppOk {name} example")

    for path, method in READ_OPERATIONS:
        require("representation" in parameter_names(document, path, method), f"Read operation lacks representation: {path}")
    for path, method in WRITE_OPERATIONS:
        require("representation" not in parameter_names(document, path, method), f"Write operation exposes representation: {path}")

    create_schema = document["paths"]["/v1/dpps"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    require(
        create_schema == {"$ref": "#/components/schemas/CompressedDigitalProductPassport"},
        "CreateDPP is not compressed-only",
    )
    update_operation = document["paths"]["/v1/dpps/{dppId}"]["patch"]
    update_media = update_operation["requestBody"]["content"]
    require(set(update_media) == {"application/merge-patch+json"}, "UpdateDPP media type mismatch")
    require(
        update_media["application/merge-patch+json"]["schema"]
        == {"$ref": "#/components/schemas/BatteryPassReadyEvFeb27MergePatch"},
        "UpdateDPP is not using the BatteryPass merge patch",
    )
    for name, example in update_media["application/merge-patch+json"]["examples"].items():
        validate_openapi_instance(
            document,
            "BatteryPassReadyEvFeb27MergePatch",
            example["value"],
            f"UpdateDPP {name} example",
        )

    merge_schema = document["components"]["schemas"]["BatteryPassReadyEvFeb27MergePatch"]
    require(
        merge_schema["properties"]["digitalProductPassportId"] is False,
        "digitalProductPassportId must be immutable",
    )
    require(
        merge_schema["properties"]["uniqueProductIdentifier"] is False,
        "uniqueProductIdentifier must be immutable",
    )

    validate_path_parameters(document)
    rendered = generator.OUTPUT_OPENAPI.read_text(encoding="utf-8")
    for obsolete_name in ("remainingCapacityKWh", "ratedCapacityKWh"):
        require(obsolete_name not in rendered, f"Obsolete example field remains: {obsolete_name}")

    print("PASS  original openapi.yaml unchanged:", original_hash)
    print(
        f"PASS  OpenAPI YAML, {len(document['components']['schemas'])} component schemas and all local refs"
    )
    print("PASS  BatteryPass source conversion: 42 $defs -> components.schemas")
    print("PASS  EV February-2027 fields: 68 total / 56 JSON-Schema required")
    print("PASS  enums: 16 schema enum nodes, all with x-apifox-enum")
    print("PASS  EN 18223 flat public header; raw dppHeader retained only as internal mapping schema")
    print("PASS  public -> raw source mapping and derived full example")
    print("PASS  writes=compressed; reads=compressed|full; immutable IDs enforced")


if __name__ == "__main__":
    main()
