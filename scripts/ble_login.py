"""One-time: fetch your EcoFlow User ID for the BLE auth handshake.

Run interactively:  .venv\\Scripts\\python.exe scripts\\ble_login.py
Prompts for your EcoFlow app email + password locally (nothing is stored;
the password goes only to EcoFlow's login endpoint via the vendored eflib).
On success, prints the ECOFLOW_USER_ID line to add to .env.
"""
import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp

from ecoflow.ble.eflib.login import EcoFlowLogin, Region


async def main():
    email = input("EcoFlow account email: ").strip()
    password = getpass.getpass("EcoFlow account password: ")
    async with aiohttp.ClientSession() as session:
        result = await EcoFlowLogin(session).login(email, password, Region.AUTO)
    if result.error or not result.user_id:
        print(f"\nLogin failed: {result.error or 'no user id returned'}")
        sys.exit(1)
    print("\nLogin OK. Add this line to .env:")
    print(f"ECOFLOW_USER_ID={result.user_id}")


if __name__ == "__main__":
    asyncio.run(main())
