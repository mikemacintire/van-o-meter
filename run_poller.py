"""Read-only poller: log SOC/solar/power for both Delta Pros to CSV.

Usage:
    .venv\\Scripts\\python.exe run_poller.py [--interval 30] [--once]

Requires .env with ECOFLOW_ACCESS_KEY, ECOFLOW_SECRET_KEY, ECOFLOW_SN_A,
ECOFLOW_SN_B. If the SNs are missing, prints the device list and exits so
you can copy them in.
"""

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from ecoflow.client import EcoFlowClient
from ecoflow.poller import extract_sample, append_sample, migrate
from ecoflow import single_instance

CSV_PATH = Path(__file__).parent / "data" / "samples.csv"
INSTANCE_LOCK_PORT = 8644   # two pollers double the API load for zero data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=30, help="seconds between samples")
    parser.add_argument("--once", action="store_true", help="take one sample and exit")
    args = parser.parse_args()

    # Held for process lifetime. The scheduled task's logon + keepalive
    # triggers raced past IgnoreNew on 2026-07-27 and left a zombie twin.
    instance_lock = single_instance.acquire(INSTANCE_LOCK_PORT)
    if instance_lock is None:
        raise SystemExit("another poller instance is already running — exiting")

    load_dotenv(Path(__file__).parent / ".env")
    access_key = os.environ.get("ECOFLOW_ACCESS_KEY")
    secret_key = os.environ.get("ECOFLOW_SECRET_KEY")
    if not access_key or not secret_key:
        raise SystemExit("Missing ECOFLOW_ACCESS_KEY / ECOFLOW_SECRET_KEY in .env")

    client = EcoFlowClient(access_key, secret_key)

    units = {"A": os.environ.get("ECOFLOW_SN_A"), "B": os.environ.get("ECOFLOW_SN_B")}
    if not all(units.values()):
        print("ECOFLOW_SN_A/ECOFLOW_SN_B not set. Devices on this account:")
        for device in client.get_device_list():
            print(f"  {device}")
        raise SystemExit("Copy the serial numbers into .env and rerun.")

    CSV_PATH.parent.mkdir(exist_ok=True)
    migrate(CSV_PATH)
    print(f"Polling every {args.interval}s -> {CSV_PATH} (Ctrl+C to stop)")
    while True:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for unit, sn in units.items():
            try:
                sample = extract_sample(client.get_quota_all(sn))
                append_sample(CSV_PATH, now, unit, sample)
                print(f"{now} {unit}: soc={sample['soc']}% solar={sample['solar_w']}W "
                      f"in={sample['watts_in']}W out={sample['watts_out']}W "
                      f"ac_chg={sample['ac_charge_w']}W chg_state={sample['chg_state']}")
            except Exception as e:  # keep polling through transient API errors
                print(f"{now} {unit}: ERROR {e}")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
