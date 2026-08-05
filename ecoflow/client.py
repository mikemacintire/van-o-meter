"""Thin client for the EcoFlow Developer REST API."""

import requests

from .signing import build_headers

BASE_URL = "https://api.ecoflow.com"


class EcoFlowApiError(Exception):
    pass


class EcoFlowClient:
    def __init__(self, access_key, secret_key, base_url=BASE_URL, session=None):
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = base_url
        self.session = session or requests.Session()

    def _request(self, method, path, params=None, body=None):
        # Sign whichever param set the request carries (query for GET, JSON body for PUT).
        headers = build_headers(body or params, self.access_key, self.secret_key)
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = self.session.request(
            method, self.base_url + path,
            headers=headers, params=params, json=body, timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code")) != "0":
            raise EcoFlowApiError(
                f"API code {payload.get('code')}: {payload.get('message')}"
            )
        return payload.get("data")

    def get_device_list(self):
        return self._request("GET", "/iot-open/sign/device/list")

    def get_quota_all(self, sn):
        return self._request("GET", "/iot-open/sign/device/quota/all", params={"sn": sn})

    def set_quota(self, sn, cmd_id, **params):
        body = {"sn": sn, "params": {"cmdSet": 32, "id": cmd_id, **params}}
        return self._request("PUT", "/iot-open/sign/device/quota", body=body)
