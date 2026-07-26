"""Generate Discord messages when a player's exotic loadout changes."""

WEAPON_SLOTS = {"Kinetic", "Energy", "Power"}


def diff_player(label: str, current: dict, previous: dict | None) -> list[str]:
    """Return announcement lines for one player. Empty if no change or first baseline."""
    if previous is None:
        return []

    prev_cid = previous.get("active_character_id")
    curr_cid = current.get("active_character_id")
    char_switched = (
        prev_cid is not None
        and curr_cid is not None
        and prev_cid != curr_cid
    )

    if char_switched:
        return _announce_all(label, current, mention_class=True)

    prev_exos = previous.get("exotics", {})
    curr_exos = current.get("exotics", {})
    if prev_exos != curr_exos:
        return _diff_same_character(label, prev_exos, curr_exos)
    return []


def format_loadout(label: str, snap: dict) -> str:
    """Human-readable current loadout for /status."""
    exos = snap.get("exotics", {})
    current_class = snap.get("active_class", "?")
    if not exos:
        return f"**{label}** — no exotics on {current_class}."

    lines = [f"**{label}** ({current_class})"]
    for slot, name in sorted(exos.items()):
        verb = "using" if slot in WEAPON_SLOTS else "wearing"
        lines.append(f"• {verb} **{name}** ({slot})")
    return "\n".join(lines)


def _announce_all(label: str, snap: dict, mention_class: bool = False) -> list[str]:
    exos = snap.get("exotics", {})
    current_class = snap.get("active_class", "")
    if not exos:
        return [f"{label} has no exotics equipped on {current_class}."]

    lines = []
    for slot, name in sorted(exos.items()):
        verb = "using" if slot in WEAPON_SLOTS else "wearing"
        if mention_class and current_class:
            lines.append(
                f"{label} is {verb} **{name}** in the {slot} slot on their {current_class}."
            )
        else:
            lines.append(f"{label} is {verb} **{name}** in the {slot} slot.")
    return lines


def _diff_same_character(label: str, old: dict, new: dict) -> list[str]:
    lines = []
    removed = [(slot, name) for slot, name in old.items() if slot not in new]
    added = [(slot, name) for slot, name in new.items() if slot not in old]

    if len(removed) == 1 and len(added) == 1:
        from_slot, from_name = removed[0]
        to_slot, to_name = added[0]
        lines.append(
            f"{label} switched from **{from_name}** (*{from_slot} slot*) "
            f"to **{to_name}** (*{to_slot} slot*)."
        )
        return lines

    all_slots = set(old) | set(new)
    for slot in sorted(all_slots):
        before = old.get(slot)
        after = new.get(slot)
        if before and after and before != after:
            lines.append(
                f"{label} switched from **{before}** (*{slot} slot*) "
                f"to **{after}** (*{slot} slot*)."
            )
        elif after and not before:
            lines.append(f"{label} switched to **{after}** in the *{slot} slot*.")
        elif before and not after:
            lines.append(f"{label} unequipped **{before}** from the *{slot} slot*.")
    return lines
