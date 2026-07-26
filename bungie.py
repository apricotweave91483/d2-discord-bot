"""Bungie API helpers for Destiny 2 exotic loadout snapshots."""

import json
import os
from functools import lru_cache
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BUNGIE_API_KEY")
BASE_URL = "https://www.bungie.net/Platform"
SCRIPT_DIR = Path(__file__).parent
MANIFEST_PATH = SCRIPT_DIR / "d2_manifest_items.json"

WEAPON_ARMOR_BUCKETS = {
    3448274439: "Helmet",
    3551918588: "Gauntlets",
    14239492: "Chest",
    20886954: "Legs",
    1585787867: "Class Item",
    1498876634: "Kinetic",
    2465295065: "Energy",
    953998645: "Power",
}

CLASS_NAMES = {0: "Titan", 1: "Hunter", 2: "Warlock"}


def _headers() -> dict:
    if not API_KEY:
        raise RuntimeError("Missing BUNGIE_API_KEY — add it to .env")
    return {"X-API-Key": API_KEY}


@lru_cache(maxsize=1)
def get_manifest() -> dict:
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    print("Downloading manifest (one-time, ~20MB)...")
    resp = requests.get(f"{BASE_URL}/Destiny2/Manifest/", headers=_headers())
    resp.raise_for_status()
    m = resp.json()["Response"]
    url = m["jsonWorldComponentContentPaths"]["en"]["DestinyInventoryItemDefinition"]
    r = requests.get(f"https://www.bungie.net{url}", headers=_headers())
    r.raise_for_status()
    data = r.json()
    with open(MANIFEST_PATH, "w") as f:
        json.dump(data, f)
    print("Manifest cached.\n")
    return data


def item_bucket_hash(item_hash: int) -> int:
    entry = get_manifest().get(str(item_hash), {})
    return entry.get("inventory", {}).get("bucketTypeHash", 0)


def item_bucket(item_hash: int) -> str | None:
    return WEAPON_ARMOR_BUCKETS.get(item_bucket_hash(item_hash))


def item_name(item_hash: int) -> str:
    entry = get_manifest().get(str(item_hash), {})
    return entry.get("displayProperties", {}).get("name", f"?({item_hash})")


def is_exotic(item_hash: int) -> bool:
    entry = get_manifest().get(str(item_hash), {})
    return entry.get("inventory", {}).get("tierType", 0) == 6


def parse_bungie_name(full: str) -> tuple[str, int] | None:
    if "#" not in full:
        return None
    name, code_str = full.rsplit("#", 1)
    try:
        return name.strip(), int(code_str.strip())
    except ValueError:
        return None


def search_player(name: str, code: int) -> dict | None:
    payload = {"displayName": name, "displayNameCode": code}
    resp = requests.post(
        f"{BASE_URL}/Destiny2/SearchDestinyPlayerByBungieName/-1/",
        headers=_headers(),
        json=payload,
    )
    resp.raise_for_status()
    players = resp.json().get("Response", [])
    return players[0] if players else None


def get_profile(mtype: int, mid: str) -> dict:
    resp = requests.get(
        f"{BASE_URL}/Destiny2/{mtype}/Profile/{mid}/?components=200,205,300",
        headers=_headers(),
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("ErrorCode") != 1:
        raise RuntimeError(body.get("Message", "Unknown Bungie error"))
    return body["Response"]


def pull_snapshot(mtype: int, mid: str) -> dict:
    profile = get_profile(mtype, mid)
    chars = profile.get("characters", {}).get("data", {})
    equipment = profile.get("characterEquipment", {}).get("data", {})

    if not chars:
        return {}

    active_cid = None
    active_class = None
    latest = ""
    for cid, c in chars.items():
        last = c.get("dateLastPlayed", "")
        if last > latest:
            latest = last
            active_cid = cid
            active_class = CLASS_NAMES.get(int(c.get("classType", 0)), "?")

    exotics: dict[str, str] = {}

    if active_cid and active_cid in equipment:
        for it in equipment[active_cid].get("items", []):
            ihash = it.get("itemHash", 0)
            if not is_exotic(ihash):
                continue
            bucket = item_bucket(ihash)
            if bucket is None:
                continue
            exotics[bucket] = item_name(ihash)

    return {
        "active_class": active_class,
        "active_character_id": active_cid,
        "exotics": exotics,
    }
