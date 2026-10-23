"""Pure generated-contract validation; no domain or transport authority."""
from .errors import ContractError
from .profile import DIALECT, validate_bundle
from .validation import ABSENT, ContractBundle, parse_json

__all__ = ["ABSENT", "ContractBundle", "ContractError", "DIALECT", "parse_json", "validate_bundle"]
