"""Explicit, opt-in content validation policy for trusted nonrect inputs.

This module is also copied into each derived runtime's materialization package
so Blender's flat worker imports and normal Python imports use identical code.
"""
import os

ENV = "NONRECT_CONTENT_FINGERPRINT_VALIDATION"
HASH_FIELDS = ("geometry_sha256", "mesh_data_sha256", "mesh_assembly_sha256",
               "material_sha256", "asset_assembly_sha256")


def mode():
    value = os.environ.get(ENV, "strict")
    if value not in {"strict", "off"}:
        raise ValueError("Invalid content_fingerprint_validation: " + value)
    return value


def skipped():
    return mode() == "off"


def receipt():
    return {"mode": "off", "status": "skipped_by_policy",
            "policy_id": "nonrect_trusted_content_off_v1",
            "scope": ["texture_pixels", "material_content", "geometry_content", "asset_assembly_content"],
            "integrity_limitation": "Content equivalence is not verified; identity, structure, transforms, bounds, renderability and file provenance remain checked."}


def validate_record(record):
    policy = record.get("content_fingerprint_validation")
    if skipped():
        if policy != receipt() or any(key in record for key in HASH_FIELDS):
            raise ValueError("Fast input requires explicit skipped_by_policy and absent content hashes")
    elif policy is not None:
        raise ValueError("Strict validation cannot consume skipped content validation")


def inspection_accepted(report):
    if not isinstance(report, dict):
        return False
    if not skipped():
        return report.get("status") == "passed" and "content_fingerprint_validation" not in report
    return (report.get("status") == "passed_structural_checks"
            and report.get("content_fingerprint_validation") == receipt())
