"""
D2 Exotics Bot — watch players listed in friends.json and post loadout changes.

Run:  python bot.py

friends.json format:
  {"BungieName#1234": "DisplayName", ...}
Edit this file locally (Mac or SSH on Pi) to add/remove/rename watches.

Env (.env):
  DISCORD_BOT_TOKEN    — Discord bot token
  BUNGIE_API_KEY       — Bungie API key
  DISCORD_CHANNEL_ID   — channel where updates are posted
  ALLOWED_GUILD_IDS    — optional comma-separated server IDs
  POLL_SECONDS         — default 120
"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

import bungie
import db
from loadout_diff import diff_player, format_loadout

load_dotenv()

SCRIPT_DIR = Path(__file__).parent
FRIENDS_PATH = SCRIPT_DIR / "friends.json"

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
if not TOKEN:
    sys.exit("Missing DISCORD_BOT_TOKEN in .env")
if not CHANNEL_ID:
    sys.exit("Missing DISCORD_CHANNEL_ID in .env — right-click your channel → Copy Channel ID")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "120"))

_raw_guilds = os.getenv("ALLOWED_GUILD_IDS", "").strip()
ALLOWED_GUILD_IDS: set[int] | None = None
if _raw_guilds:
    ALLOWED_GUILD_IDS = {int(x.strip()) for x in _raw_guilds.split(",") if x.strip()}


@dataclass
class TrackedPlayer:
    label: str
    bungie_name: str
    bungie_code: int
    membership_id: str
    membership_type: int


# BungieName#1234 -> TrackedPlayer (rebuilt when friends.json changes)
_players: dict[str, TrackedPlayer] = {}
_friends_mtime: float | None = None


def guild_allowed(guild_id: int) -> bool:
    if ALLOWED_GUILD_IDS is None:
        return True
    return guild_id in ALLOWED_GUILD_IDS


def load_friends_file() -> dict[str, str]:
    if not FRIENDS_PATH.exists():
        return {}
    with open(FRIENDS_PATH) as f:
        return json.load(f)


def refresh_players(force: bool = False) -> list[TrackedPlayer]:
    """Reload friends.json when the file changes; resolve new Bungie names."""
    global _friends_mtime, _players

    try:
        mtime = FRIENDS_PATH.stat().st_mtime
    except FileNotFoundError:
        _players = {}
        _friends_mtime = None
        return []

    if not force and _friends_mtime == mtime:
        return list(_players.values())

    _friends_mtime = mtime
    friends = load_friends_file()
    updated: dict[str, TrackedPlayer] = {}

    for full_name, label in friends.items():
        parsed = bungie.parse_bungie_name(full_name)
        if parsed is None:
            print(f"[friends] Skipping '{full_name}' — use Name#1234 format")
            continue

        bungie_name, bungie_code = parsed
        if full_name in _players:
            old = _players[full_name]
            updated[full_name] = TrackedPlayer(
                label=label,
                bungie_name=old.bungie_name,
                bungie_code=old.bungie_code,
                membership_id=old.membership_id,
                membership_type=old.membership_type,
            )
            continue

        try:
            player = bungie.search_player(bungie_name, bungie_code)
        except Exception as e:
            print(f"[friends] Bungie error for {full_name}: {e}")
            continue
        if player is None:
            print(f"[friends] Player not found: {full_name}")
            continue

        updated[full_name] = TrackedPlayer(
            label=label,
            bungie_name=bungie_name,
            bungie_code=bungie_code,
            membership_id=player["membershipId"],
            membership_type=player["membershipType"],
        )
        print(f"[friends] Tracking {full_name} → {label}")

    removed = set(_players) - set(updated)
    for name in removed:
        print(f"[friends] Stopped tracking {name}")

    _players = updated
    return list(_players.values())


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def require_guild(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and guild_allowed(interaction.guild.id)


@tree.command(name="list", description="List players from friends.json")
async def list_cmd(interaction: discord.Interaction):
    if not require_guild(interaction):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    players = refresh_players(force=True)
    if not players:
        await interaction.response.send_message(
            "`friends.json` is empty or missing. Edit it on the host machine.",
            ephemeral=True,
        )
        return

    lines = [
        f"• **{p.label}** — `{p.bungie_name}#{p.bungie_code}`"
        for p in sorted(players, key=lambda x: x.label.lower())
    ]
    await interaction.response.send_message(
        "**Tracked players** (from `friends.json`):\n" + "\n".join(lines),
        ephemeral=True,
    )


@tree.command(name="status", description="Show current exotic loadouts")
@app_commands.describe(label="Display name from friends.json (omit for all)")
async def status_cmd(interaction: discord.Interaction, label: str | None = None):
    if not require_guild(interaction):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    players = refresh_players(force=True)
    if label:
        players = [p for p in players if p.label.lower() == label.strip().lower()]
    if not players:
        await interaction.response.send_message("No matching player in friends.json.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    parts = []
    for p in players:
        try:
            snap = bungie.pull_snapshot(p.membership_type, p.membership_id)
            parts.append(format_loadout(p.label, snap))
        except Exception as e:
            parts.append(f"**{p.label}** — error: {e}")

    await interaction.followup.send("\n\n".join(parts), ephemeral=True)


@tasks.loop(seconds=POLL_SECONDS)
async def poll_loop():
    players = refresh_players()
    if not players:
        return

    channel = client.get_channel(int(CHANNEL_ID))
    if channel is None:
        print(f"[poll] Channel {CHANNEL_ID} not found")
        return

    for player in players:
        previous = db.get_messenger_state(player.membership_id)

        try:
            current = bungie.pull_snapshot(player.membership_type, player.membership_id)
        except Exception as e:
            print(f"[poll] Error pulling {player.label}: {e}")
            continue

        lines = diff_player(player.label, current, previous)
        if lines:
            try:
                await channel.send("\n".join(lines))
            except discord.DiscordException as e:
                print(f"[poll] Failed to send: {e}")

        # Persist after a successful pull so new snapshot fields get baselined.
        db.set_messenger_state(player.membership_id, current)


@poll_loop.before_loop
async def before_poll():
    await client.wait_until_ready()


@client.event
async def on_ready():
    db.init_db()
    bungie.get_manifest()
    refresh_players(force=True)

    channel = client.get_channel(int(CHANNEL_ID))
    ch_name = f"#{channel.name}" if channel else CHANNEL_ID

    mode = (
        f"whitelist: {ALLOWED_GUILD_IDS}"
        if ALLOWED_GUILD_IDS
        else "DEV (all guilds — set ALLOWED_GUILD_IDS for production)"
    )
    print(f"Logged in as {client.user} ({client.user.id})")
    print(f"Posting to channel: {ch_name} ({CHANNEL_ID})")
    print(f"Friends file: {FRIENDS_PATH}")
    print(f"Guild mode: {mode}")
    print(f"Polling every {POLL_SECONDS}s")

    for guild in client.guilds:
        print(f"  Server: {guild.name} (id={guild.id})")

    synced = await tree.sync()
    print(f"Synced {len(synced)} slash command(s)")

    if not poll_loop.is_running():
        poll_loop.start()


@client.event
async def on_guild_join(guild: discord.Guild):
    if ALLOWED_GUILD_IDS is not None and guild.id not in ALLOWED_GUILD_IDS:
        print(f"Leaving unauthorized guild: {guild.name} ({guild.id})")
        await guild.leave()


def main():
    client.run(TOKEN)


if __name__ == "__main__":
    main()
