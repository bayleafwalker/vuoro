"""Pure, stdlib-only JSON-Schema and operation-spec builders."""

from .catalog import (
    CatalogRegistry,
    SCHEMA_DIALECT,
    SCHEMA_FEATURES,
    build_object_schema,
    build_operation_spec,
    object_schema,
    operation_spec,
)

__all__ = [
    "CatalogRegistry",
    "SCHEMA_DIALECT",
    "SCHEMA_FEATURES",
    "build_object_schema",
    "build_operation_spec",
    "object_schema",
    "operation_spec",
]

from .catalog_v2 import build_operation_spec_v2, operation_spec_v2
from .admission import (
    ARGUMENT_BYTES_LIMIT, CATALOG_REQUIRED_PROFILE, TRUSTED_REVIEW_INGRESS_PROFILE,
    AdmissionIdentity, AdmissionResponse, OwnerAdmission, OwnerAdmissionContext,
    OwnerAdmissionRequest, PrincipalIdentity, TrustedIngressProvenance,
)

__all__ += [
    "ARGUMENT_BYTES_LIMIT", "CATALOG_REQUIRED_PROFILE", "TRUSTED_REVIEW_INGRESS_PROFILE",
    "AdmissionIdentity", "AdmissionResponse", "OwnerAdmission", "OwnerAdmissionContext",
    "OwnerAdmissionRequest", "PrincipalIdentity", "TrustedIngressProvenance",
    "build_operation_spec_v2", "operation_spec_v2",
]

__version__ = "0.2.0"
