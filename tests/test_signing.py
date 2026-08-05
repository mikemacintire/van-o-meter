"""Tests for the EcoFlow request-signing scheme.

Algorithm per developer.ecoflow.com General Information:
1. Flatten request params: nested objects -> dot notation (params.cmdSet),
   arrays -> bracket notation (quotas[0]).
2. Sort flattened key=value pairs by key, ASCII ascending, join with '&'.
3. Append accessKey, nonce, timestamp (in that order).
4. sign = lowercase hex HMAC-SHA256 of that string, keyed with secretKey.
"""

from ecoflow.signing import flatten_params, build_sign_string, hmac_sha256, build_headers

# Example structure from the official doc's General Information section.
ACCESS_KEY = "Fp4SvIprYSDPXtYJidEtUAd1o"
SECRET_KEY = "WIbFEKre0s6sLnh4ei7SPUeYnptHG6V"
NONCE = "345164"
TIMESTAMP = "1671171709428"
PARAMS = {"sn": "123456789", "params": {"cmdSet": 11, "id": 24, "eps": 0}}
EXPECTED_SIGN_STRING = (
    "params.cmdSet=11&params.eps=0&params.id=24&sn=123456789"
    "&accessKey=Fp4SvIprYSDPXtYJidEtUAd1o&nonce=345164&timestamp=1671171709428"
)


def test_flatten_nested_dict():
    assert flatten_params(PARAMS) == {
        "sn": "123456789",
        "params.cmdSet": 11,
        "params.id": 24,
        "params.eps": 0,
    }


def test_flatten_list():
    assert flatten_params({"params": {"quotas": ["bmsMaster.soc", "mppt.inWatts"]}}) == {
        "params.quotas[0]": "bmsMaster.soc",
        "params.quotas[1]": "mppt.inWatts",
    }


def test_sign_string_sorted_with_credentials_appended():
    assert (
        build_sign_string(PARAMS, ACCESS_KEY, NONCE, TIMESTAMP)
        == EXPECTED_SIGN_STRING
    )


def test_sign_string_no_params():
    assert build_sign_string(None, "AK", "123456", "1700000000000") == (
        "accessKey=AK&nonce=123456&timestamp=1700000000000"
    )


def test_hmac_hex_digest():
    # Locks the HMAC-SHA256 hex output for a fixed input (verified once
    # against Python's hmac stdlib at authoring time).
    assert hmac_sha256("abc", "key") == (
        "9c196e32dc0175f86f4b1cb89289d6619de6bee699e4c378e68309ed97a1a6ab"
    )


def test_build_headers():
    headers = build_headers(PARAMS, ACCESS_KEY, SECRET_KEY, nonce=NONCE, timestamp=TIMESTAMP)
    assert headers["accessKey"] == ACCESS_KEY
    assert headers["nonce"] == NONCE
    assert headers["timestamp"] == TIMESTAMP
    assert headers["sign"] == hmac_sha256(EXPECTED_SIGN_STRING, SECRET_KEY)


def test_build_headers_generates_nonce_and_timestamp():
    headers = build_headers(None, "AK", "SK")
    assert len(headers["nonce"]) == 6 and headers["nonce"].isdigit()
    assert headers["timestamp"].isdigit()
    assert int(headers["timestamp"]) > 1_700_000_000_000  # milliseconds
