import base64
import json
from dataclasses import asdict, replace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vuoro_service.recipient_admission import (
    AUDIENCE, OPERATION, AdmissionBinding, AdmissionVerificationError,
    verify_recipient_admission,
)

NOW = 1000
BINDING = AdmissionBinding(
    operation=OPERATION, caller="caller", request="request", resolution="resolution",
    work="work", binding="binding", recipient_principal="gateway:subject:1",
    recipient_instance="instance", workspace="workspace", repo="repo",
    gateway_issuer="gateway", subject="subject", grant="grant", client="client",
    epoch=1, resource="resource", scopes=("vuoro:work.read", "vuoro:evidence.record"),
    effective_scope="work:read",
)


@pytest.fixture
def setup():
    private = Ed25519PrivateKey.generate()
    claims = dict(asdict(BINDING), iss="issuer", aud=AUDIENCE, jti="unique-id",
                  iat=995, exp=1025, authority_read_start=990, authority_read_end=994)
    def sign(data=claims, key=private):
        return jwt.encode(data, key, algorithm="EdDSA", headers={"kid": "key", "typ": "JWT"})
    def verify(token, **kwargs):
        args = dict(keys={"key": private.public_key()}, issuer="issuer", audience=AUDIENCE,
                    expected=BINDING, now=NOW)
        args.update(kwargs)
        return verify_recipient_admission(token, **args)
    return claims, sign, verify


def test_valid_and_immutable(setup):
    claims, sign, verify = setup
    result = verify(sign())
    assert result.binding == BINDING and result.jti == "unique-id"
    assert result.authority_read_start == 990
    with pytest.raises(AttributeError):
        result.jti = "changed"
    claims["scopes"] = list(reversed(claims["scopes"]))
    assert set(verify(sign()).binding.scopes) == set(BINDING.scopes)


@pytest.mark.parametrize("field", [f for f in asdict(BINDING) if f not in ("epoch", "scopes")])
def test_every_text_binding_is_exact(setup, field):
    claims, sign, verify = setup
    claims[field] = "different"
    with pytest.raises(AdmissionVerificationError, match="^recipient admission assertion invalid$"):
        verify(sign())


@pytest.mark.parametrize("field,value", [
    ("epoch", 2), ("epoch", True), ("scopes", ["vuoro:work.read"]),
    ("scopes", ["vuoro:work.read", "vuoro:evidence.record", "extra"]),
    ("scopes", ["vuoro:work.read", "vuoro:evidence.record", "vuoro:work.read"]),
    ("effective_scope", "work:write"), ("iss", "other"), ("aud", "other"),
    ("aud", [AUDIENCE]), ("jti", ""),
    ("iat", True), ("iat", 996.0), ("exp", 1026), ("exp", NOW),
    ("iat", 1003), ("authority_read_start", 969),
    ("authority_read_start", 1003), ("authority_read_end", 1003),
    ("authority_read_end", 989), ("iat", 991),
])
def test_reject_bad_claims(setup, field, value):
    claims, sign, verify = setup
    claims[field] = value
    with pytest.raises(AdmissionVerificationError):
        verify(sign())


def test_time_boundaries(setup):
    claims, sign, verify = setup
    claims.update(iat=1002, exp=1032, authority_read_start=970, authority_read_end=1002)
    assert verify(sign()).issued_at == 1002
    claims.update(iat=998, exp=1028, authority_read_start=998, authority_read_end=1000)
    assert verify(sign()).authority_read_end == 1000


def test_trust_inputs_and_old_assertions_fail(setup):
    claims, sign, verify = setup
    token = sign()
    for kwargs in (dict(keys={"key": Ed25519PrivateKey.generate().public_key()}),
                   dict(issuer="other"), dict(audience="gateway-audience"),
                   dict(expected=replace(BINDING, repo="other")), dict(now=float("nan"))):
        with pytest.raises(AdmissionVerificationError):
            verify(token, **kwargs)
    with pytest.raises(AdmissionVerificationError):
        verify(sign(key=Ed25519PrivateKey.generate()))
    for change in (lambda d: d.pop("authority_read_start"), lambda d: d.update(extra="x"),
                   lambda d: d.update(operation="recipient-admission/v0")):
        data = claims.copy()
        change(data)
        with pytest.raises(AdmissionVerificationError):
            verify(sign(data))


@pytest.mark.parametrize("field,value", [
    ("gateway_issuer", "other-gateway"),
    ("subject", "other-subject"),
    ("epoch", 2),
    ("recipient_principal", "gateway:subject:2"),
])
def test_signed_and_expected_bindings_must_compose_recipient(setup, field, value):
    claims, sign, verify = setup
    claims[field] = value
    token = sign()
    # A matching trusted tuple must not legitimize an internally inconsistent
    # signed assertion; a valid trusted tuple must also reject the assertion.
    for expected in (replace(BINDING, **{field: value}), BINDING):
        with pytest.raises(AdmissionVerificationError):
            verify(token, expected=expected)
    # The trusted binding is independently required to be internally valid.
    with pytest.raises(AdmissionVerificationError):
        verify(sign(dict(asdict(BINDING), iss="issuer", aud=AUDIENCE, jti="unique-id",
                         iat=995, exp=1025, authority_read_start=990, authority_read_end=994)),
               expected=replace(BINDING, **{field: value}))


def test_duplicate_json_claims_and_headers_and_malformed_token(setup):
    claims, sign, verify = setup
    token = sign()
    header, payload, signature = token.split(".")
    def encode(data):
        return base64.urlsafe_b64encode(data).decode().rstrip("=")
    duplicate_payload = encode(json.dumps(claims).encode()[:-1] + b',"jti":"second"}')
    duplicate_header = encode(b'{"alg":"EdDSA","typ":"JWT","kid":"key","kid":"key"}')
    for candidate in ("bad", token + "!", f"{header}.{duplicate_payload}.{signature}",
                      f"{duplicate_header}.{payload}.{signature}"):
        with pytest.raises(AdmissionVerificationError):
            verify(candidate)


@pytest.mark.parametrize("non_utf8_part", ["header", "payload"])
def test_signed_non_utf8_json_refuses(non_utf8_part):
    private = Ed25519PrivateKey.generate()
    header = json.dumps({"alg": "EdDSA", "typ": "JWT", "kid": "key"})
    claims = dict(asdict(BINDING), iss="issuer", aud=AUDIENCE, jti="id",
                  iat=995, exp=1025, authority_read_start=990, authority_read_end=994)
    payload = json.dumps(claims)
    def encode(data):
        return base64.urlsafe_b64encode(data).decode().rstrip("=")
    h = encode(header.encode("utf-16" if non_utf8_part == "header" else "utf-8"))
    p = encode(payload.encode("utf-16" if non_utf8_part == "payload" else "utf-8"))
    signed = f"{h}.{p}"
    token = f"{signed}.{encode(private.sign(signed.encode('ascii')))}"
    with pytest.raises(AdmissionVerificationError):
        verify_recipient_admission(token, keys={"key": private.public_key()},
            issuer="issuer", audience=AUDIENCE, expected=BINDING, now=NOW)


def test_cloud_scope_set_and_internal_ceiling_are_distinct(setup):
    claims, sign, verify = setup
    for scopes in (["work:read", "work:evidence"],
                   ["vuoro:work.read"],
                   ["vuoro:evidence.record"],
                   ["vuoro:work.read", "vuoro:evidence.record", "vuoro:effect.apply"]):
        claims["scopes"] = scopes
        with pytest.raises(AdmissionVerificationError):
            verify(sign(), expected=replace(BINDING, scopes=tuple(scopes)))
    scopes = ("vuoro:work.read", "vuoro:evidence.record", "vuoro:effect.read", "vuoro:work.claim")
    claims["scopes"] = list(scopes)
    assert verify(sign(), expected=replace(BINDING, scopes=scopes)).binding.effective_scope == "work:read"


def test_read_must_end_before_assertion_expiry(setup):
    claims, sign, verify = setup
    claims.update(iat=999, exp=1001, authority_read_start=999, authority_read_end=1001)
    with pytest.raises(AdmissionVerificationError):
        verify(sign())


def test_gateway_text_is_not_artificially_truncated(setup):
    claims, sign, verify = setup
    claims["subject"] = "a" * 513
    claims["recipient_principal"] = f"gateway:{claims['subject']}:1"
    expected = replace(BINDING, subject=claims["subject"], recipient_principal=claims["recipient_principal"])
    assert verify(sign(), expected=expected).binding.subject == claims["subject"]
