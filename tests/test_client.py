"""Tests for the EcoFlow REST client (fake HTTP session, no live creds)."""

import pytest

from ecoflow.client import EcoFlowClient, EcoFlowApiError


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    """Records the last request and returns a canned payload."""

    def __init__(self, payload):
        self.payload = payload
        self.last = None

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.last = {
            "method": method, "url": url, "headers": headers,
            "params": params, "json": json,
        }
        return FakeResponse(self.payload)


def make_client(payload):
    session = FakeSession(payload)
    client = EcoFlowClient("AK", "SK", session=session)
    return client, session


def test_get_quota_all_builds_signed_get():
    client, session = make_client({"code": "0", "data": {"bmsMaster.soc": 87}})
    data = client.get_quota_all("DCABZ123")
    assert data == {"bmsMaster.soc": 87}
    assert session.last["method"] == "GET"
    assert session.last["url"] == "https://api.ecoflow.com/iot-open/sign/device/quota/all"
    assert session.last["params"] == {"sn": "DCABZ123"}
    headers = session.last["headers"]
    assert headers["accessKey"] == "AK"
    assert set(headers) >= {"accessKey", "nonce", "timestamp", "sign"}


def test_get_device_list():
    devices = [{"sn": "A1", "online": 1}, {"sn": "B2", "online": 1}]
    client, session = make_client({"code": "0", "data": devices})
    assert client.get_device_list() == devices
    assert session.last["url"] == "https://api.ecoflow.com/iot-open/sign/device/list"


def test_set_quota_builds_signed_put_with_cmdset32():
    client, session = make_client({"code": "0", "data": {}})
    client.set_quota("B2", cmd_id=66, enabled=1)
    assert session.last["method"] == "PUT"
    assert session.last["url"] == "https://api.ecoflow.com/iot-open/sign/device/quota"
    assert session.last["json"] == {
        "sn": "B2",
        "params": {"cmdSet": 32, "id": 66, "enabled": 1},
    }
    assert session.last["headers"]["Content-Type"] == "application/json"


def test_nonzero_code_raises():
    client, _ = make_client({"code": "6012", "message": "device offline"})
    with pytest.raises(EcoFlowApiError, match="6012"):
        client.get_quota_all("A1")
