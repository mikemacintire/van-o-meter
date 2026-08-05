"""BLE proof-of-concept: read both Delta Pros over the local radio.

Run when a BLE adapter is within range of the truck (see docs/ble-plan.md):
    .venv\\Scripts\\python.exe scripts\\ble_poc.py

Needs ECOFLOW_USER_ID in .env (fetch it once with scripts/ble_login.py).
Read-only: connects, authenticates, prints a snapshot next to the cloud's
view of the same unit, disconnects. Sends no commands.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from ecoflow.ble_client import BleClient, BleError
from ecoflow.client import EcoFlowClient

UNITS = {"A": os.environ["ECOFLOW_SN_A"], "B": os.environ["ECOFLOW_SN_B"]}
SHOW = ["ems.f32LcdShowSoc", "mppt.inWatts", "pd.wattsOutSum",
        "inv.cfgAcEnabled", "ems.maxChargeSoc", "ems.minDsgSoc"]


def main():
    ble = BleClient(os.environ.get("ECOFLOW_USER_ID"), UNITS)
    cloud = EcoFlowClient(os.environ["ECOFLOW_ACCESS_KEY"],
                          os.environ["ECOFLOW_SECRET_KEY"])
    for unit, sn in UNITS.items():
        print(f"\n=== Unit {unit} ({sn}) ===")
        try:
            b = ble.get_quota_all(sn)
            print("BLE snapshot OK")
        except BleError as e:
            print(f"BLE: {e}")
            continue
        try:
            c = cloud.get_quota_all(sn)
        except Exception as e:
            c = {}
            print(f"(cloud unavailable for comparison: {e})")
        print(f"{'field':24} {'BLE':>12} {'cloud':>12}")
        for k in SHOW:
            print(f"{k:24} {str(b.get(k, '—')):>12} {str(c.get(k, '—')):>12}")


if __name__ == "__main__":
    main()
