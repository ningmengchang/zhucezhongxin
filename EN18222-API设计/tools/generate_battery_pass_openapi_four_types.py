#!/usr/bin/env python3
"""Generate a four-battery-type EN 18222/BatteryPass-Ready OpenAPI profile.

The existing EV-only OpenAPI is never written by this generator. The new API
uses the native BatteryPass-Ready 2.0 envelope on every DPP operation:
``dppHeader`` and ``batteryPassportData`` are the only two root properties.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import openpyxl
import yaml

import generate_battery_pass_openapi as ev_generator


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_OPENAPI = BASE_DIR / "openapi-battery-pass-ready-v2.0-4-battery-types-feb27.yaml"
REFERENCE_DIR = BASE_DIR / "参考资料" / "BatteryPass-Ready-v2.0"
SCHEMA_DIR = (
    REFERENCE_DIR
    / "BatteryPassDataModel"
    / "BatteryPass-Ready"
    / "JSONSchemaFiles"
    / "v2.0"
)
SOURCE_LONGLIST = REFERENCE_DIR / "2026_BatteryPass-Ready_DataAttributeLongList_v2.0.xlsx"
MODEL_COMMIT = ev_generator.MODEL_COMMIT
EV_ONLY_OPENAPI_SHA256 = "c87082ec8e8a2170ec8cade8e440665ce80d004ff8de2308ec3f3348394773fb"

ATTRIBUTE_DEFINITIONS = (
    "dppHeader",
    "symbolsLabelsAndDocumentationOfConformity",
    "performanceAndDurability",
    "identifiersAndProductData",
    "circularityAndResourceEfficiency",
    "batteryMaterialsAndComposition",
)

PROFILES: tuple[dict[str, Any], ...] = (
    {
        "key": "ev",
        "prefix": "Ev",
        "title": "Electric vehicle battery",
        "source": "EV_Feb27_2.0.json",
        "applicabilityColumn": 4,
        "component": "BatteryPassReadyEvFeb27",
        "patchComponent": "BatteryPassReadyEvFeb27MergePatch",
        "categoryValue": "electric vehicle battery",
        "idSlug": "ev-battery",
    },
    {
        "key": "lmt",
        "prefix": "Lmt",
        "title": "Light means of transport battery",
        "source": "LMT_Feb_27_2.0.json",
        "applicabilityColumn": 5,
        "component": "BatteryPassReadyLmtFeb27",
        "patchComponent": "BatteryPassReadyLmtFeb27MergePatch",
        "categoryValue": "LMT battery",
        "idSlug": "lmt-battery",
    },
    {
        "key": "otherIndustrial",
        "prefix": "OtherIndustrial",
        "title": "Other industrial battery above 2 kWh",
        "source": "Other_Industrial_Above_2kWh_Feb27_2.0.json",
        "applicabilityColumn": 6,
        "component": "BatteryPassReadyOtherIndustrialAbove2kWhFeb27",
        "patchComponent": "BatteryPassReadyOtherIndustrialAbove2kWhFeb27MergePatch",
        "categoryValue": "industrial/non-stationary battery",
        "idSlug": "other-industrial-battery",
    },
    {
        "key": "stationary",
        "prefix": "StationaryIndustrial",
        "title": "Stationary industrial battery above 2 kWh",
        "source": "Stationary_Industrial_Above_2kWh_Feb27_2.0.json",
        "applicabilityColumn": 7,
        "component": "BatteryPassReadyStationaryIndustrialAbove2kWhFeb27",
        "patchComponent": "BatteryPassReadyStationaryIndustrialAbove2kWhFeb27MergePatch",
        "categoryValue": "industrial/stationary battery",
        "idSlug": "stationary-industrial-battery",
    },
)


def prefixed_component_name(prefix: str, definition_name: str) -> str:
    return "Bp" + prefix + ev_generator.component_name(definition_name).removeprefix("Bp")


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
            "applicability": {
                "ev": ev_generator.clean(longlist.cell(row, 4).value),
                "lmt": ev_generator.clean(longlist.cell(row, 5).value),
                "otherIndustrial": ev_generator.clean(longlist.cell(row, 6).value),
                "stationary": ev_generator.clean(longlist.cell(row, 7).value),
                "industrialWithoutBms": ev_generator.clean(longlist.cell(row, 8).value),
            },
            "mandatoryFromFeb2027": ev_generator.clean(longlist.cell(row, 10).value).lower(),
            "category": ev_generator.clean(longlist.cell(row, 11).value),
            "subCategory": ev_generator.clean(longlist.cell(row, 12).value),
            "attribute": ev_generator.clean(longlist.cell(row, 13).value),
            "shortDefinition": ev_generator.clean(longlist.cell(row, 17).value),
            "regulationReference": ev_generator.clean(longlist.cell(row, 20).value),
            "unit": ev_generator.clean(longlist.cell(row, 21).value),
            "dataFormat": ev_generator.clean(longlist.cell(row, 22).value),
            "accessRights": ev_generator.clean(longlist.cell(row, 23).value),
            "behaviour": ev_generator.clean(longlist.cell(row, 24).value),
            "updateRequirement": ev_generator.clean(longlist.cell(row, 25).value),
            "granularity": ev_generator.clean(longlist.cell(row, 26).value),
        }

    records: list[dict[str, Any]] = []
    for row in range(10, technical.max_row + 1):
        number = technical.cell(row, 2).value
        if not isinstance(number, (int, float)) or int(number) not in main_by_number:
            continue
        record = dict(main_by_number[int(number)])
        technical_name = ev_generator.clean(technical.cell(row, 7).value)
        record.update(
            {
                "technicalName": technical_name,
                "schemaProperty": ev_generator.ATTRIBUTE_KEY_ALIASES.get(
                    technical_name, technical_name
                ),
                "jsonKeys": ev_generator.clean(technical.cell(row, 8).value),
                "jsonKeyDescription": ev_generator.clean(technical.cell(row, 9).value),
                "jsonSchemaTypes": ev_generator.clean(technical.cell(row, 12).value),
            }
        )
        records.append(record)
    if len(records) != 92:
        raise RuntimeError(f"Expected 92 longlist records, found {len(records)}")
    return records


def enrich_profile_schema(
    schema: dict[str, Any], records: list[dict[str, Any]], profile: dict[str, Any]
) -> int:
    applicable = {
        record["schemaProperty"]: record
        for record in records
        if record["mandatoryFromFeb2027"] == "yes"
        and record["applicability"][profile["key"]] not in ("", "-")
    }
    schema_properties = {
        name
        for definition_name in ATTRIBUTE_DEFINITIONS
        for name in schema["$defs"][definition_name]["properties"]
    }
    if set(applicable) != schema_properties:
        raise RuntimeError(
            f"{profile['key']} attribute mapping mismatch; "
            f"missing={sorted(schema_properties - set(applicable))}, "
            f"extra={sorted(set(applicable) - schema_properties)}"
        )

    for definition_name in ATTRIBUTE_DEFINITIONS:
        for property_name, property_schema in schema["$defs"][definition_name][
            "properties"
        ].items():
            record = applicable[property_name]
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
                "profile": profile["key"],
                "profileApplicability": record["applicability"][profile["key"]],
                "allBatteryTypeApplicability": record["applicability"],
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
            if record["applicability"][profile["key"]] == "C":
                property_schema["x-conditionally-required"] = True
    return len(schema_properties)


def customize_raw_example(raw: dict[str, Any], profile: dict[str, Any]) -> None:
    slug = profile["idSlug"]
    header = raw["dppHeader"]
    header["digitalProductPassportId"] = f"https://passport.example.com/dpp/{slug}-000001"
    header["uniqueProductIdentifier"] = f"https://id.example.com/batteries/{slug}-000001"
    header["dppSchemaVersion"] = f"BatteryPass-Ready:2.0:{profile['key']}:Feb2027"
    header["contentSpecificationIds"] = (
        f"BatteryPass-Ready:2.0:{profile['key']}:Feb2027"
    )
    identifiers = raw["batteryPassportData"]["IdentifiersAndProductData"]
    if "batteryModelIdentifier" in identifiers:
        identifiers["batteryModelIdentifier"] = f"{slug.upper()}-MODEL-2027-001"
    if "batterySerialNumber" in identifiers:
        identifiers["batterySerialNumber"] = f"{slug.upper()}-SN-000001"


def dictionary_reference(profile: dict[str, Any], path: tuple[str, ...]) -> str:
    safe_parts = [re.sub(r"[^A-Za-z0-9._~-]+", "-", part) for part in path]
    base = (
        "https://example.com/dictionaries/battery-pass-ready/2.0/"
        f"{profile['idSlug']}/feb27"
    )
    return base + "/" + "/".join(safe_parts)


def to_full_element(
    profile: dict[str, Any], element_id: str, value: Any, path: tuple[str, ...]
) -> dict[str, Any]:
    base = {
        "elementId": element_id,
        "dictionaryReference": dictionary_reference(profile, path),
    }
    if isinstance(value, dict):
        if "contentType" in value and "url" in value:
            return {
                **base,
                "objectType": "RelatedResource",
                "contentType": value["contentType"],
                "url": value["url"],
                **({"language": value["language"]} if value.get("language") else {}),
                **(
                    {"resourceTitle": value["resourceTitle"]}
                    if value.get("resourceTitle")
                    else {}
                ),
            }
        return {
            **base,
            "objectType": "DataElementCollection",
            "elements": [
                to_full_element(profile, child_name, child_value, (*path, child_name))
                for child_name, child_value in value.items()
            ],
        }
    if isinstance(value, list):
        return {
            **base,
            "objectType": "MultiValuedDataElement",
            "value": [
                to_full_element(
                    profile, f"item-{index + 1}", item, (*path, f"item-{index + 1}")
                )
                for index, item in enumerate(value)
            ],
        }
    return {
        **base,
        "objectType": "SingleValuedDataElement",
        "valueDataType": ev_generator.infer_xsd_type(value),
        "value": value,
    }


def to_full_document(
    profile: dict[str, Any], compressed: dict[str, Any], group_names: list[str]
) -> dict[str, Any]:
    battery_data = compressed["batteryPassportData"]
    return {
        "dppHeader": copy.deepcopy(compressed["dppHeader"]),
        "batteryPassportData": {
            group_name: to_full_element(
                profile,
                group_name,
                battery_data[group_name],
                ("batteryPassportData", group_name),
            )
            for group_name in group_names
        },
    }


def build_profile(
    profile: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    source_schema = json.loads((SCHEMA_DIR / profile["source"]).read_text(encoding="utf-8"))
    attribute_count = enrich_profile_schema(source_schema, records, profile)
    ev_generator.normalize_access_rights_and_enums(source_schema)
    definitions = source_schema["$defs"]
    component_names = {
        name: prefixed_component_name(profile["prefix"], name) for name in definitions
    }
    components = {
        component_names[name]: ev_generator.transform_local_refs(schema, component_names)
        for name, schema in definitions.items()
    }

    wrapper_schema = {
        key: value
        for key, value in source_schema.items()
        if key not in ("$schema", "$defs")
    }
    wrapper_schema = ev_generator.transform_local_refs(wrapper_schema, component_names)
    wrapper_schema["additionalProperties"] = False
    wrapper_schema.update(
        {
            "title": f"BatteryPass-Ready {profile['title']} February 2027 envelope",
            "description": (
                f"Public compressed representation derived from {profile['source']}. The root uses the "
                "native BatteryPass-Ready 2.0 dppHeader + batteryPassportData envelope; flat root-level "
                "header or business attributes are not accepted."
            ),
            "x-battery-type": profile["key"],
            "x-battery-category-value": profile["categoryValue"],
            "x-source-schema": profile["source"],
            "x-source-model-commit": MODEL_COMMIT,
            "x-attribute-count": attribute_count,
            "x-root-serialization": "BatteryPass-Ready native wrapper",
        }
    )

    master = source_schema["$defs"]["batteryPassportMaster"]
    group_names = list(master["properties"])

    resolved_root = ev_generator.resolve_schema(source_schema, definitions)
    resolved_root.pop("$schema", None)
    resolved_root["additionalProperties"] = False
    patch_schema = ev_generator.make_merge_patch_schema(resolved_root)
    patch_schema["title"] = f"{profile['title']} February 2027 merge patch"
    patch_schema["description"] = (
        "RFC 7396 merge patch using the native dppHeader + batteryPassportData envelope. Validate the "
        f"merged complete DPP against {profile['component']}. The two identifiers and battery category "
        "are immutable."
    )
    patch_schema["minProperties"] = 1
    header_patch = patch_schema["properties"]["dppHeader"]["properties"]
    header_patch["digitalProductPassportId"] = False
    header_patch["uniqueProductIdentifier"] = False
    identifiers_patch = patch_schema["properties"]["batteryPassportData"]["properties"][
        "IdentifiersAndProductData"
    ]["properties"]
    identifiers_patch["batteryCategory"] = False
    patch_schema["x-battery-type"] = profile["key"]

    compressed_example = ev_generator.sample_value(source_schema, definitions)
    customize_raw_example(compressed_example, profile)
    full_example = to_full_document(profile, compressed_example, group_names)

    components[profile["component"]] = wrapper_schema
    components[profile["patchComponent"]] = patch_schema
    return {
        "profile": profile,
        "sourceSchema": source_schema,
        "componentNames": component_names,
        "components": components,
        "groupNames": group_names,
        "compressedExample": compressed_example,
        "fullExample": full_example,
        "attributeCount": attribute_count,
    }


def remove_ev_profile_components(schemas: dict[str, Any]) -> None:
    explicit = {
        "Granularity",
        "DppStatus",
        "DppHeader",
        "BatteryPassReadyRawEvFeb27",
        "BatteryPassReadyEvFeb27",
        "BatteryPassReadyEvFeb27MergePatch",
        "CompressedDigitalProductPassport",
        "FullDigitalProductPassport",
        "DigitalProductPassport",
        "PartialDigitalProductPassport",
        "CompressedDppResponse",
    }
    for name in list(schemas):
        if name.startswith("Bp") or name in explicit:
            schemas.pop(name)


def build_openapi() -> dict[str, Any]:
    document = ev_generator.build_openapi()
    records = load_attribute_records()
    built_profiles = [build_profile(profile, records) for profile in PROFILES]
    first = built_profiles[0]
    group_names = first["groupNames"]
    if any(built["groupNames"] != group_names for built in built_profiles[1:]):
        raise RuntimeError("The four public profiles do not share the same top-level groups")

    info = document["info"]
    info["title"] = "EN 18222:2026 DPP API — BatteryPass-Ready 2.0 four battery types"
    info["version"] = "2.0.0-battery-pass-ready-2.0-four-types-feb27"
    info["description"] = (
        "EN 18222 lifecycle API integrated with four BatteryPass-Ready 2.0 February-2027 models: "
        "EV, LMT, Other Industrial >2 kWh and Stationary Industrial >2 kWh. All DPP payloads use the "
        "native BatteryPass-Ready wrapper: dppHeader and batteryPassportData are the two root properties. "
        "The compressed representation preserves the published JSON Schema shape; the full representation "
        "keeps the same wrapper and expands the five business groups as EN 18223 DataElement objects. "
        "Industrial without BMS is a separate special profile and is not included in this contract."
    )
    document["x-battery-pass-ready"] = {
        "modelVersion": "2.0",
        "requirementProfile": "February 2027",
        "sourceCommit": MODEL_COMMIT,
        "includedBatteryTypes": [profile["key"] for profile in PROFILES],
        "includedSourceSchemas": [profile["source"] for profile in PROFILES],
        "excludedSpecialProfile": "Industrial_Without_BMS_Feb27_2.0.json",
        "rootSerialization": "BatteryPass-Ready native wrapper",
        "rootProperties": ["dppHeader", "batteryPassportData"],
        "headerSerialization": "dppHeader object",
        "businessSerialization": "five groups inside batteryPassportData",
        "writeRepresentation": "compressed",
        "readRepresentations": ["compressed", "full"],
        "dictionaryReferenceStatus": "implementation placeholder",
    }

    schemas = document["components"]["schemas"]
    remove_ev_profile_components(schemas)
    for built in built_profiles:
        schemas.update(built["components"])

    wrapper_refs = [{"$ref": f"#/components/schemas/{p['component']}"} for p in PROFILES]
    schemas["BatteryPassReadyFourTypesFeb27"] = {
        "oneOf": wrapper_refs,
        "title": "BatteryPass-Ready four battery types — February 2027",
        "description": (
            "Exactly one battery-type profile selected by "
            "$.batteryPassportData.IdentifiersAndProductData.batteryCategory.batteryCategoryValue."
        ),
        "x-discriminator-path": (
            "$.batteryPassportData.IdentifiersAndProductData.batteryCategory.batteryCategoryValue"
        ),
        "x-discriminator-mapping": {
            p["categoryValue"]: f"#/components/schemas/{p['component']}" for p in PROFILES
        },
    }
    schemas["BatteryPassReadyFourTypesFeb27MergePatch"] = {
        "anyOf": [
            {"$ref": f"#/components/schemas/{p['patchComponent']}"} for p in PROFILES
        ],
        "title": "Four-type BatteryPass merge patch",
        "description": (
            "The existing DPP determines the battery type. A partial patch can match several profiles; "
            "after merging, the complete document must validate against its existing type schema."
        ),
    }
    schemas["CompressedDigitalProductPassport"] = {
        "$ref": "#/components/schemas/BatteryPassReadyFourTypesFeb27",
        "description": (
            "Writable compressed representation for one of the four battery types, with dppHeader "
            "and batteryPassportData at the root."
        ),
    }

    full_battery_data = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            group_name: {"$ref": "#/components/schemas/ExpandedDataElement"}
            for group_name in group_names
        },
        "required": group_names,
        "description": "Five BatteryPass business groups expanded as EN 18223 DataElement objects.",
    }
    schemas["FullDigitalProductPassport"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "dppHeader": {
                "$ref": (
                    f"#/components/schemas/{first['componentNames']['dppHeader']}"
                )
            },
            "batteryPassportData": full_battery_data,
        },
        "required": ["dppHeader", "batteryPassportData"],
        "description": (
            "Read-only expanded view. The BatteryPass-Ready dppHeader + batteryPassportData wrapper "
            "is retained; only the five business groups are expanded."
        ),
    }
    schemas["DigitalProductPassport"] = {
        "oneOf": [
            {"$ref": "#/components/schemas/CompressedDigitalProductPassport"},
            {"$ref": "#/components/schemas/FullDigitalProductPassport"},
        ],
        "description": "Read response selected by representation=compressed|full.",
    }
    schemas["PartialDigitalProductPassport"] = {
        "$ref": "#/components/schemas/BatteryPassReadyFourTypesFeb27MergePatch"
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
        "compressed returns the native dppHeader + batteryPassportData JSON; full retains that wrapper "
        "and expands the five groups inside batteryPassportData as DataElement objects. Write operations "
        "do not accept this parameter."
    )
    parameters["ElementIdPath"]["description"] = (
        "RFC 9535 absolute normalized path in the wrapped BatteryPass DPP. The path must identify exactly "
        "one element."
    )
    parameters["ElementIdPath"]["example"] = (
        "$.batteryPassportData.PerformanceAndDurability.remainingCapacity"
    )

    compressed_response_examples: dict[str, Any] = {}
    for built in built_profiles:
        profile = built["profile"]
        compressed_response_examples[f"{profile['key']}Compressed"] = {
            "summary": f"{profile['title']} compressed",
            "value": {"statusCode": "Success", "payload": built["compressedExample"]},
        }
        compressed_response_examples[f"{profile['key']}Full"] = {
            "summary": f"{profile['title']} full",
            "value": {"statusCode": "Success", "payload": built["fullExample"]},
        }
    responses = document["components"]["responses"]
    responses["DppOk"]["content"]["application/json"]["examples"] = (
        compressed_response_examples
    )
    ev_response = {
        "statusCode": "Success",
        "payload": first["compressedExample"],
    }
    responses["CompressedDppOk"] = ev_generator.clone_dpp_response(
        responses["DppOk"],
        "#/components/schemas/CompressedDppResponse",
        ev_response,
        "Compressed wrapped BatteryPass-Ready DPP update succeeded.",
    )

    examples = document["components"]["examples"]
    examples.pop("CompressedBatteryDpp", None)
    for built in built_profiles:
        profile = built["profile"]
        examples[f"{profile['prefix']}CompressedDpp"] = {
            "summary": f"{profile['title']} February-2027 compressed DPP",
            "value": built["compressedExample"],
        }

    create = document["paths"]["/v1/dpps"]["post"]
    ev_generator.remove_parameter(create, "Representation")
    create["description"] = (
        "Create one EV, LMT, Other Industrial >2 kWh or Stationary Industrial >2 kWh DPP. "
        "The request body must contain the native BatteryPass-Ready dppHeader and batteryPassportData "
        "wrapper. Flat root-level header or business fields are rejected."
    )
    create_content = create["requestBody"]["content"]["application/json"]
    create_content["schema"] = {
        "$ref": "#/components/schemas/CompressedDigitalProductPassport"
    }
    create_content["examples"] = {
        built["profile"]["key"]: {
            "$ref": f"#/components/examples/{built['profile']['prefix']}CompressedDpp"
        }
        for built in built_profiles
    }

    update = document["paths"]["/v1/dpps/{dppId}"]["patch"]
    ev_generator.remove_parameter(update, "Representation")
    update["description"] = (
        "Apply an RFC 7396 merge patch using the native BatteryPass-Ready wrapper. The existing resource "
        "fixes the battery type; batteryCategory, digitalProductPassportId and uniqueProductIdentifier "
        "cannot be changed. Validate the merged complete document against that type's February-2027 schema."
    )
    update_content = update["requestBody"]["content"]["application/merge-patch+json"]
    update_content["schema"] = {
        "$ref": "#/components/schemas/BatteryPassReadyFourTypesFeb27MergePatch"
    }
    update_examples: dict[str, Any] = {
        "status": {
            "summary": "Update the DPP status",
            "value": {"dppHeader": {"dppStatus": {"dppStatusValue": "inactive"}}},
        }
    }
    for built in built_profiles:
        profile = built["profile"]
        capacity = built["compressedExample"]["batteryPassportData"][
            "PerformanceAndDurability"
        ]["remainingCapacity"]
        update_examples[f"{profile['key']}RemainingCapacity"] = {
            "summary": f"Update {profile['title']} remaining capacity",
            "value": {
                "batteryPassportData": {
                    "PerformanceAndDurability": {"remainingCapacity": capacity}
                }
            },
        }
    update_content["examples"] = update_examples
    update["responses"]["200"] = {"$ref": "#/components/responses/CompressedDppOk"}

    remaining_capacity = first["compressedExample"]["batteryPassportData"][
        "PerformanceAndDurability"
    ]["remainingCapacity"]
    remaining_capacity_full = to_full_element(
        first["profile"],
        "remainingCapacity",
        remaining_capacity,
        ("batteryPassportData", "PerformanceAndDurability", "remainingCapacity"),
    )
    element_get = document["paths"]["/v1/dpps/{dppId}/elements/{elementIdPath}"]["get"]
    element_get["responses"]["200"]["content"]["application/json"]["examples"] = {
        "compressed": {
            "summary": "Compressed remainingCapacity",
            "value": {"statusCode": "Success", "payload": remaining_capacity},
        },
        "full": {
            "summary": "Full remainingCapacity DataElement",
            "value": {"statusCode": "Success", "payload": remaining_capacity_full},
        },
    }
    element_patch = document["paths"]["/v1/dpps/{dppId}/elements/{elementIdPath}"]["patch"]
    ev_generator.remove_parameter(element_patch, "Representation")
    element_patch["description"] = (
        "Update the compressed value at a wrapped BatteryPass JSONPath. Validate the resulting complete "
        "DPP against its existing battery-type schema."
    )

    return document


def main() -> None:
    current_ev_hash = ev_generator.sha256(ev_generator.OUTPUT_OPENAPI)
    if current_ev_hash != EV_ONLY_OPENAPI_SHA256:
        raise RuntimeError(
            "Existing EV-only OpenAPI changed before generation: "
            f"expected {EV_ONLY_OPENAPI_SHA256}, got {current_ev_hash}"
        )
    document = build_openapi()
    OUTPUT_OPENAPI.write_text(
        yaml.dump(
            document,
            Dumper=ev_generator.OpenApiDumper,
            sort_keys=False,
            allow_unicode=True,
            width=120,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    if ev_generator.sha256(ev_generator.OUTPUT_OPENAPI) != EV_ONLY_OPENAPI_SHA256:
        raise RuntimeError("Existing EV-only OpenAPI changed during four-type generation")
    print(OUTPUT_OPENAPI)


if __name__ == "__main__":
    main()
