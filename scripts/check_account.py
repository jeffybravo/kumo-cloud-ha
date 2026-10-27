"""Log in to Kumo Cloud and print each unit, without Home Assistant.

    pip install aiohttp
    python scripts/check_account.py you@example.com
"""

import asyncio
import getpass
import importlib.util
import json
import sys
from pathlib import Path

import aiohttp

# Load api.py directly so Home Assistant doesn't need to be installed.
_spec = importlib.util.spec_from_file_location(
    "kumo_api", Path(__file__).parent.parent / "custom_components/kumo_cloud/api.py"
)
api_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api_mod)

KEYS = ("operationMode", "roomTemp", "spHeat", "spCool", "spAuto", "fanSpeed",
        "airDirection", "humidity", "connected")


async def main(username: str) -> None:
    password = getpass.getpass("Kumo Cloud password: ")
    async with aiohttp.ClientSession() as session:
        api = api_mod.KumoCloudApi(session, username, password)
        await api.login()
        print("Login OK")
        for site in await api.get_sites():
            print(f"\nSite: {site.get('name')}")
            for zone in await api.get_zones(site["id"]):
                serial = (zone.get("adapter") or {}).get("deviceSerial")
                if not serial:
                    continue
                device = await api.get_device(serial)
                print(f"  {zone['name']} ({serial}): "
                      + json.dumps({k: device.get(k) for k in KEYS}))
                print("    profile: " + json.dumps(await api.get_profile(serial)))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else input("Email: ")))
