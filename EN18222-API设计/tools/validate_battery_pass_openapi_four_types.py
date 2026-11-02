#!/usr/bin/env python3
"""Validate the four-type BatteryPass-Ready February-2027 OpenAPI profile."""

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

import generate_battery_pass_openapi_four_types as generator


EXPECTED_ORIGINAL_SHA256 = "7cb3f4bd326910e47089d0d7ec846fa2dc9f3264d0ea155c6a866e59fca43db4"
EXPECTED_PROFILE_COUNTS = {
    "ev": {"definitions": 42, "attributes": 68, "required": 56, "enums": 16},
    "lmt": {"definitions": 41, "attributes": 67, "required": 56, "enums": 15},
    "otherIndustrial": {
        "definitions": 40,
        "attributes": 64,
        "required": 47,
        "enums": 15,
    },
    "stationary": {"definitions": 41, "attributes": 67, "required": 56, "enums": 15},
}
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


def openapi_errors(document: dict[str, Any], schema_name: str, instance: Any):
    registry = Registry().with_resource(
        "urn:generated-openapi",
        Resource(contents=document, specification=DRAFT202012),
    )
    validator = Draft202012Validator(
        {"$ref": f"urn:generated-openapi#/components/schemas/{schema_name}"},
        registry=registry,
        format_checker=FormatChecker(),
    )
    return sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))


def validate_openapi_instance(
    document: dict[str, Any], schema_name: str, instance: Any, label: str
) -> None:
    errors = openapi_errors(document, schema_name, instance)
    if errors:
        details = "; ".join(
            f"/{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in errors[:10]
        )
        raise AssertionError(f"{label} failed {schema_name}: {details}")


def validate_source_instance(source_schema: dict[str, Any], instance: Any, label: str) -> None:
    errors = sorted(
        Draft202012Validator(
            source_schema, format_checker=FormatChecker()
        ).iter_errors(instance),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        details = "; ".join(
            f"/{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in errors[:10]
        )
        raise AssertionError(f"{label} failed official JSON Schema: {details}")


def parameter_names(document: dict[str, Any], path: str, method: str) -> set[str]:
    path_item = document["paths"][path]
    parameters = [*path_item.get("parameters", []), *path_item[method].get("parameters", [])]
    names: set[str] = set()
    for parameter in parameters:
        if "$ref" in parameter:
            parameter = json_pointer(document, parameter["$ref"])
        names.add(parameter["name"])
    return names


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
            declared: set[str] = set()
            parameters = [*path_item.get("parameters", []), *operation.get("parameters", [])]
            for parameter in parameters:
                if "$ref" in parameter:
                    parameter = json_pointer(document, parameter["$ref"])
                if parameter.get("in") == "path":
                    require(parameter.get("required") is True, f"Path parameter is optional: {path}")
                    declared.add(parameter["name"])
            require(
                declared == template_names,
                f"Path parameter mismatch for {method.upper()} {path}: {declared} != {template_names}",
            )
    require(len(operation_ids) == len(set(operation_ids)), "operationId values must be unique")


def profile_component_names(profile: dict[str, Any], source_schema: dict[str, Any]) -> set[str]:
    return {
        generator.prefixed_component_name(profile["prefix"], name)
        for name in source_schema["$defs"]
    }


def validate_profile(
    document: dict[str, Any], profile: dict[str, Any], response_examples: dict[str, Any]
) -> None:
    schemas = document["components"]["schemas"]
    examples = document["components"]["examples"]
    expected = EXPECTED_PROFILE_COUNTS[profile["key"]]
    source_path = generator.SCHEMA_DIR / profile["source"]
    source_schema = json.loads(source_path.read_text(encoding="utf-8"))
    component_names = profile_component_names(profile, source_schema)

    require(len(source_schema["$defs"]) == expected["definitions"], f"{profile['key']} definition count changed")
    required_count = sum(
        len(source_schema["$defs"][name].get("required", []))
        for name in generator.ATTRIBUTE_DEFINITIONS
    )
    attribute_names = {
        property_name
        for name in generator.ATTRIBUTE_DEFINITIONS
        for property_name in source_schema["$defs"][name]["properties"]
    }
    require(len(attribute_names) == expected["attributes"], f"{profile['key']} attribute count changed")
    require(required_count == expected["required"], f"{profile['key']} required count changed")

    generated_nodes = [
        node
        for component_name in component_names
        for node in iter_nodes(schemas[component_name])
    ]
    metadata = [node["x-battery-pass-ready"] for node in generated_nodes if "x-battery-pass-ready" in node]
    require(len(metadata) == expected["attributes"], f"{profile['key']} metadata count mismatch")
    require(
        len({item["attributeNumber"] for item in metadata}) == expected["attributes"],
        f"{profile['key']} attribute numbers are not unique",
    )
    require(
        all(item["profile"] == profile["key"] for item in metadata),
        f"{profile['key']} metadata profile mismatch",
    )

    source_enum_count = sum("enum" in node for node in iter_nodes(source_schema))
    generated_enums = [node for node in generated_nodes if "enum" in node]
    require(source_enum_count == expected["enums"], f"{profile['key']} source enum count changed")
    require(len(generated_enums) == expected["enums"], f"{profile['key']} generated enum count mismatch")
    for enum_node in generated_enums:
        require("x-apifox-enum" in enum_node, f"{profile['key']} enum lacks x-apifox-enum")
        require(
            [entry["value"] for entry in enum_node["x-apifox-enum"]] == enum_node["enum"],
            f"{profile['key']} x-apifox-enum differs from enum",
        )

    access_nodes = [node for node in generated_nodes if "x-battery-pass-access-rights" in node]
    require(len(access_nodes) == expected["attributes"], f"{profile['key']} access-right count mismatch")

    wrapper = schemas[profile["component"]]
    require(set(wrapper["properties"]) == {"dppHeader", "batteryPassportData"}, f"{profile['key']} wrapper properties changed")
    require(set(wrapper["required"]) == {"dppHeader", "batteryPassportData"}, f"{profile['key']} wrapper requirements changed")
    require(wrapper.get("additionalProperties") is False, f"{profile['key']} wrapper is not strict")
    require(wrapper["x-attribute-count"] == expected["attributes"], f"{profile['key']} root count mismatch")

    compressed = examples[f"{profile['prefix']}CompressedDpp"]["value"]
    require(set(compressed) == {"dppHeader", "batteryPassportData"}, f"{profile['key']} compressed example is flat")
    validate_source_instance(source_schema, compressed, profile["source"])
    validate_openapi_instance(document, profile["component"], compressed, f"{profile['key']} compressed example")
    validate_openapi_instance(
        document, "BatteryPassReadyFourTypesFeb27", compressed, f"{profile['key']} four-type union"
    )

    matches = [
        candidate["key"]
        for candidate in generator.PROFILES
        if not openapi_errors(document, candidate["component"], compressed)
    ]
    require(matches == [profile["key"]], f"{profile['key']} discriminator matched {matches}")
    category = compressed["batteryPassportData"]["IdentifiersAndProductData"]["batteryCategory"][
        "batteryCategoryValue"
    ]
    require(category == profile["categoryValue"], f"{profile['key']} category discriminator mismatch")
    require(
        compressed["dppHeader"]["dppSchemaVersion"]
        == f"BatteryPass-Ready:2.0:{profile['key']}:Feb2027",
        f"{profile['key']} dppSchemaVersion example mismatch",
    )

    flat = {**compressed["dppHeader"], **compressed["batteryPassportData"]}
    require(
        bool(openapi_errors(document, "BatteryPassReadyFourTypesFeb27", flat)),
        f"{profile['key']} flat payload was unexpectedly accepted",
    )

    full = response_examples[f"{profile['key']}Full"]["value"]["payload"]
    require(set(full) == {"dppHeader", "batteryPassportData"}, f"{profile['key']} full example is flat")
    require(full["dppHeader"] == compressed["dppHeader"], f"{profile['key']} full header changed")
    require(
        list(full["batteryPassportData"]) == list(compressed["batteryPassportData"]),
        f"{profile['key']} full group order changed",
    )
    validate_openapi_instance(document, "FullDigitalProductPassport", full, f"{profile['key']} full example")
    validate_openapi_instance(
        document,
        "DppResponse",
        response_examples[f"{profile['key']}Full"]["value"],
        f"{profile['key']} full response",
    )
    validate_openapi_instance(
        document,
        "DppResponse",
        response_examples[f"{profile['key']}Compressed"]["value"],
        f"{profile['key']} compressed response",
    )

    references = [
        node["dictionaryReference"]
        for node in iter_nodes(full["batteryPassportData"])
        if "dictionaryReference" in node
    ]
    require(len(references) >= expected["attributes"], f"{profile['key']} full example is under-expanded")
    require(len(references) == len(set(references)), f"{profile['key']} dictionary references repeat")
    expected_prefix = (
        "https://example.com/dictionaries/battery-pass-ready/2.0/"
        f"{profile['idSlug']}/feb27/batteryPassportData/"
    )
    require(
        all(reference.startswith(expected_prefix) for reference in references),
        f"{profile['key']} dictionary reference path is not wrapped",
    )

    patch = schemas[profile["patchComponent"]]
    header_patch = patch["properties"]["dppHeader"]["properties"]
    require(header_patch["digitalProductPassportId"] is False, f"{profile['key']} DPP ID is mutable")
    require(header_patch["uniqueProductIdentifier"] is False, f"{profile['key']} product ID is mutable")
    category_patch = patch["properties"]["batteryPassportData"]["properties"][
        "IdentifiersAndProductData"
    ]["properties"]["batteryCategory"]
    require(category_patch is False, f"{profile['key']} battery category is mutable")


def main() -> None:
    original = generator.ev_generator.SOURCE_OPENAPI
    ev_only = generator.ev_generator.OUTPUT_OPENAPI
    output = generator.OUTPUT_OPENAPI
    require(output.exists(), "Generated four-type OpenAPI is missing; run the generator first")
    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    ev_only_hash = hashlib.sha256(ev_only.read_bytes()).hexdigest()
    require(original_hash == EXPECTED_ORIGINAL_SHA256, f"Original openapi.yaml changed: {original_hash}")
    require(ev_only_hash == generator.EV_ONLY_OPENAPI_SHA256, f"Existing EV OpenAPI changed: {ev_only_hash}")

    document = yaml.safe_load(output.read_text(encoding="utf-8"))
    require(document.get("openapi") == "3.1.0", "Generated document is not OpenAPI 3.1.0")
    require(document.get("x-derived-from-openapi-sha256") == original_hash, "Source hash metadata mismatch")
    metadata = document["x-battery-pass-ready"]
    require(metadata["rootSerialization"] == "BatteryPass-Ready native wrapper", "Root serialization mismatch")
    require(metadata["rootProperties"] == ["dppHeader", "batteryPassportData"], "Root properties mismatch")
    require(metadata["writeRepresentation"] == "compressed", "Write representation mismatch")
    require(metadata["readRepresentations"] == ["compressed", "full"], "Read representations mismatch")
    require(
        metadata["excludedSpecialProfile"] == "Industrial_Without_BMS_Feb27_2.0.json",
        "Industrial-without-BMS exclusion is missing",
    )

    refs = [node["$ref"] for node in iter_nodes(document) if isinstance(node.get("$ref"), str)]
    require(refs, "No local refs found")
    for ref in refs:
        json_pointer(document, ref)
    require(not any("#/$defs/" in ref for ref in refs), "A source $defs ref remains")
    require(not any("$defs" in node for node in iter_nodes(document)), "A $defs object remains")

    schemas = document["components"]["schemas"]
    for schema_name, schema in schemas.items():
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise AssertionError(f"Invalid component schema {schema_name}: {exc}") from exc

    expected_bp_components: set[str] = set()
    for profile in generator.PROFILES:
        source_schema = json.loads((generator.SCHEMA_DIR / profile["source"]).read_text(encoding="utf-8"))
        expected_bp_components.update(profile_component_names(profile, source_schema))
    actual_bp_components = {name for name in schemas if name.startswith("Bp")}
    require(actual_bp_components == expected_bp_components, "Converted BatteryPass component set mismatch")
    require(not any(name.startswith("BatteryPassReadyRaw") for name in schemas), "Internal raw schema remains")

    response_examples = document["components"]["responses"]["DppOk"]["content"]["application/json"][
        "examples"
    ]
    expected_response_names = {
        f"{profile['key']}{suffix}"
        for profile in generator.PROFILES
        for suffix in ("Compressed", "Full")
    }
    require(set(response_examples) == expected_response_names, "DppOk does not contain eight four-type examples")
    for profile in generator.PROFILES:
        validate_profile(document, profile, response_examples)

    union = schemas["BatteryPassReadyFourTypesFeb27"]
    expected_discriminator = (
        "$.batteryPassportData.IdentifiersAndProductData.batteryCategory.batteryCategoryValue"
    )
    require(union["x-discriminator-path"] == expected_discriminator, "Discriminator path is not wrapped")

    for path, method in READ_OPERATIONS:
        require("representation" in parameter_names(document, path, method), f"Read lacks representation: {path}")
    for path, method in WRITE_OPERATIONS:
        require("representation" not in parameter_names(document, path, method), f"Write exposes representation: {path}")

    create_content = document["paths"]["/v1/dpps"]["post"]["requestBody"]["content"]["application/json"]
    require(
        create_content["schema"] == {"$ref": "#/components/schemas/CompressedDigitalProductPassport"},
        "CreateDPP is not compressed wrapper-only",
    )
    require(set(create_content["examples"]) == {p["key"] for p in generator.PROFILES}, "CreateDPP lacks four examples")

    update_content = document["paths"]["/v1/dpps/{dppId}"]["patch"]["requestBody"]["content"]
    require(set(update_content) == {"application/merge-patch+json"}, "UpdateDPP media type mismatch")
    update_media = update_content["application/merge-patch+json"]
    require(
        update_media["schema"] == {"$ref": "#/components/schemas/BatteryPassReadyFourTypesFeb27MergePatch"},
        "UpdateDPP does not use four-type merge patch",
    )
    for name, example in update_media["examples"].items():
        require(
            set(example["value"]).issubset({"dppHeader", "batteryPassportData"}),
            f"Update example {name} is not wrapped",
        )
        validate_openapi_instance(
            document,
            "BatteryPassReadyFourTypesFeb27MergePatch",
            example["value"],
            f"UpdateDPP {name}",
        )

    element_path = document["components"]["parameters"]["ElementIdPath"]["example"]
    require(element_path == "$.batteryPassportData.PerformanceAndDurability.remainingCapacity", "Element path is not wrapped")
    validate_path_parameters(document)

    regenerated = yaml.dump(
        generator.build_openapi(),
        Dumper=generator.ev_generator.OpenApiDumper,
        sort_keys=False,
        allow_unicode=True,
        width=120,
        default_flow_style=False,
    )
    require(regenerated == output.read_text(encoding="utf-8"), "Generator output is not deterministic/current")
    require(hashlib.sha256(ev_only.read_bytes()).hexdigest() == ev_only_hash, "EV-only OpenAPI changed during validation")

    print("PASS  original openapi.yaml and existing EV-only OpenAPI unchanged")
    print(f"PASS  OpenAPI 3.1 YAML, {len(schemas)} component schemas and all local refs")
    print("PASS  native root wrapper enforced: dppHeader + batteryPassportData")
    print("PASS  EV 68/56, LMT 67/56, Other Industrial 64/47, Stationary 67/56")
    print("PASS  four official JSON Schemas, four compressed examples and four full examples")
    print("PASS  nested discriminator, immutable IDs/category and wrapped merge patches")
    print("PASS  reads=compressed|full; writes=compressed; deterministic generation")


if __name__ == "__main__":
    main()
