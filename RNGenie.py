# RNGenie.py - Discord loot distribution bot

import os
import random
import re
import asyncio
import time
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands

# ANSI color constants used to produce colored code-block output in Discord messages.
CSI = "\x1b["
RESET = CSI + "0m"
BOLD = CSI + "1m"
RED = CSI + "31m"
GREEN = CSI + "32m"
YELLOW = CSI + "33m"
BLUE = CSI + "34m"
MAGENTA = CSI + "35m"
CYAN = CSI + "36m"

# Setup bot intents and create the bot object.
intents = nextcord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(intents=intents)

# In-memory session store and locks:
# - loot_sessions: maps control-panel message id -> session dict
# - session_locks: per-session asyncio.Lock to avoid race conditions
loot_sessions: dict[int, dict] = {}
session_locks: dict[int, asyncio.Lock] = {}

# Configuration constants
SESSION_TIMEOUT_SECONDS = 600  # seconds of inactivity before session times out
TURN_NOT_STARTED = -1  # sentinel for "no turn has begun yet"

# Discord hard-caps message content at 2000 characters. We stay under that with
# margin so a code-block's ```ansi fences/newlines never push us over.
DISCORD_MSG_LIMIT = 2000
SAFE_CHUNK_LIMIT = 1900

# emoji mapping for numbered players (1..10) + fallback for higher counts
NUMBER_EMOJIS = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"
}
for i in range(11, 21):
    NUMBER_EMOJIS.setdefault(i, f"#{i}")

# ---------- Helper functions ----------
def _are_items_left(session: dict) -> bool:
    """Return True if any item has not yet been assigned."""
    return any(it.get("assigned_to") is None for it in session["items"])

def _advance_turn_snake(session: dict) -> None:
    """
    Advance the current_turn index using snake draft logic, respecting 'skipped' status.
    If the end is reached, reverse direction and increment the round.
    If no items remain or all users skipped, mark session complete.
    """
    session["just_reversed"] = False
    if not _are_items_left(session):
        session["current_turn"] = len(session["rolls"])
        return

    rolls = session["rolls"]
    num = len(rolls)
    if num == 0:
        return

    # Check if anyone is active (not skipped)
    if all(r.get("skipped") for r in rolls):
        session["current_turn"] = num
        return

    if session["current_turn"] == TURN_NOT_STARTED:
        # Start at the first non-skipped user
        for i in range(num):
            if not rolls[i].get("skipped"):
                session["current_turn"] = i
                return
        # Fallback if everyone is skipped (caught by all() check above usually)
        session["current_turn"] = num
        return

    curr = session["current_turn"]
    direction = session["direction"]

    # We need to find the next active user.
    # Simulate the snake walk until a non-skipped user is found or we exhaust reasonable attempts.
    # Limit iterations to avoid infinite loops.
    for _ in range(num * 4):
        next_idx = curr + direction

        if 0 <= next_idx < num:
            curr = next_idx
            # If user is not skipped, they are the next turn
            if not rolls[curr].get("skipped"):
                session["current_turn"] = curr
                session["direction"] = direction
                return
            # If skipped, loop continues with updated curr in same direction
        else:
            # Reverse direction
            direction *= -1
            session["round"] += 1
            session["just_reversed"] = True
            session["direction"] = direction

            # In snake draft, hitting the edge often means the edge player goes again (or first in next round).
            # Check the edge player (curr) again with the new direction logic implications.
            # If the edge player is not skipped, they take the turn.
            if not rolls[curr].get("skipped"):
                session["current_turn"] = curr
                return
            # If edge is skipped, next iteration will apply new direction from curr

    # If we fall through, assume done
    session["current_turn"] = num

def _get_next_active_index(session: dict) -> int:
    """
    Determine the index of the *next* player who will take a turn, without modifying session state.
    Returns -1 if no next player exists.
    """
    if not _are_items_left(session):
        return -1
    rolls = session["rolls"]
    num = len(rolls)
    if num == 0:
        return -1
    if session["current_turn"] < 0 or session["current_turn"] >= num:
        return -1

    curr = session["current_turn"]
    direction = session["direction"]

    # Simulate one successful 'advance' step
    for _ in range(num * 4):
        next_idx = curr + direction
        if 0 <= next_idx < num:
            curr = next_idx
            if not rolls[curr].get("skipped"):
                return curr
        else:
            direction *= -1
            if not rolls[curr].get("skipped"):
                return curr

    return -1

def _build_roll_lines(session: dict) -> str:
    """
    Build the text block that shows roll order, tie-breaks, and status emojis.
    Returns a newline-separated string suitable for insertion into an ANSI code block.
    """
    rolls = session["rolls"]
    roll_counts = {}
    for r in rolls:
        roll_counts.setdefault(r["roll"], 0)
        roll_counts[r["roll"]] += 1

    current_idx = session["current_turn"]
    # Only calculate next if session is active
    is_active = (0 <= current_idx < len(rolls)) and _are_items_left(session)
    next_idx = _get_next_active_index(session) if is_active else -1

    parts = []
    for idx, r in enumerate(rolls):
        emoji = NUMBER_EMOJIS.get(idx + 1, f"#{idx+1}")
        name = r["member"].display_name
        base = f"{emoji} {BLUE}{name}{RESET} ({r['roll']})"
        if roll_counts.get(r["roll"], 0) > 1:
            tb = r.get("tiebreak")
            base += f" /TB:{tb if tb is not None else '—'}"

        # Add status emoji
        status = ""
        if r.get("skipped"):
            # User has opted out of remaining loot.
            pass
        elif is_active:
            if idx == current_idx:
                status = " ▶️"
            elif idx == next_idx:
                status = " 🔜"
            else:
                status = " ⏳"

        parts.append(base + status)
    return "\n".join(parts)

async def _get_msg(channel: nextcord.abc.GuildChannel | nextcord.TextChannel | None, msg_id: int):
    """
    Robust fetch of a message by id from a channel.
    Tries partial-message helper first (if available) then falls back to fetch_message.
    Returns the message object or None.
    """
    if not channel or not msg_id:
        return None
    try:
        partial = getattr(channel, "get_partial_message", None)
        if callable(partial):
            return partial(msg_id)
    except Exception:
        pass
    try:
        fetch = getattr(channel, "fetch_message", None)
        if callable(fetch):
            return await channel.fetch_message(msg_id)
    except Exception:
        pass
    return None

# ---------- Pagination helpers ----------
def _paginate_ansi_blocks(header_lines: list[str], body_lines: list[str],
                           limit: int = SAFE_CHUNK_LIMIT) -> list[str]:
    """
    Split body_lines across one or more ```ansi code-block messages, each kept under
    `limit` characters (including the ```ansi fences, header, and trailing ```), so
    Discord's hard 2000-char message cap is never hit no matter how many items/players
    there are. header_lines are repeated at the top of every resulting chunk so each
    message is self-contained and readable on its own.

    Returns a list of complete message strings (each already wrapped in ```ansi ... ```).
    If body_lines is empty, returns a single chunk containing just the header.
    """
    fence_open = "```ansi\n"
    fence_close = "```"
    header_block = "\n".join(header_lines)

    def _wrap(lines: list[str]) -> str:
        content = header_block
        if lines:
            content += "\n" + "\n".join(lines)
        return fence_open + content + "\n" + fence_close

    chunks: list[str] = []
    current: list[str] = []

    # Baseline overhead (header + fences) counted once per chunk.
    for line in body_lines:
        trial = _wrap(current + [line])
        if len(trial) > limit and current:
            # Current chunk is full; seal it and start a new one.
            chunks.append(_wrap(current))
            current = [line]
        else:
            current.append(line)

    # Always emit at least one chunk (even if body_lines was empty).
    chunks.append(_wrap(current))
    return chunks

def _paginate_plain_sections(sections: list[str], limit: int = SAFE_CHUNK_LIMIT) -> list[str]:
    """
    Greedily pack pre-built section strings (each itself a full ```ansi block or
    plain text block) into as few messages as possible, each under `limit` chars,
    joined with a single newline (not a blank line — code-block fences already get
    visual padding from Discord's own rendering, so an extra blank line on top of
    that reads as a doubled gap). Never splits a section internally — if a single
    section alone exceeds the limit, it is emitted on its own as an oversized chunk
    rather than being corrupted, since these sections are already safe-chunked
    upstream by _paginate_ansi_blocks.
    """
    messages: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for sec in sections:
        sec_len = len(sec)
        added_len = sec_len + (1 if current_parts else 0)  # "\n" join
        if current_parts and (current_len + added_len) > limit:
            messages.append("\n".join(current_parts))
            current_parts = [sec]
            current_len = sec_len
        else:
            current_parts.append(sec)
            current_len += added_len

    if current_parts:
        messages.append("\n".join(current_parts))
    if not messages:
        messages.append("")
    return messages

def _pack_lines_into_ansi_blocks(line_groups: list[list[str]], limit: int = SAFE_CHUNK_LIMIT,
                                  leading_header_lines: list[str] | None = None) -> list[str]:
    """
    Pack multiple pre-built "groups" of lines (e.g. one group per player, where
    each group's first line is a header like a name, followed by its item lines)
    into as FEW ```ansi blocks as possible, separating groups with a single blank
    line WITHIN the same code block instead of closing/reopening a fence per group.

    This is what keeps consecutive players visually tight (one blank line) instead
    of the large gap Discord renders around adjacent ```...``` fences.

    If leading_header_lines is given, those lines are baked directly into the very
    FIRST returned block (ahead of the first group, no blank-line gap after them,
    just a normal line break) instead of being a separate section — this is what
    keeps a section title like "✅ Assigned Items ✅" glued to the first player
    instead of floating in its own fenced box.

    Each returned string is a complete ```ansi ... ``` block, kept under `limit`
    characters. If a single group is so large it can't fit even in an empty block,
    it is split across multiple blocks (falling back to raw line packing) rather
    than dropped.
    """
    fence_open = "```ansi\n"
    fence_close = "```"
    overhead = len(fence_open) + len(fence_close) + 1  # +1 for the trailing newline before fence_close

    blocks: list[str] = []
    current_lines: list[str] = list(leading_header_lines) if leading_header_lines else []
    current_len = sum(len(l) + 1 for l in current_lines)
    has_group_in_current = False  # tracks real groups placed, so a header alone doesn't force a blank line

    def _flush():
        nonlocal current_lines, current_len, has_group_in_current
        if current_lines:
            blocks.append(fence_open + "\n".join(current_lines) + "\n" + fence_close)
        current_lines = []
        current_len = 0
        has_group_in_current = False

    for gi, group in enumerate(line_groups):
        if not group:
            continue
        group_text_len = sum(len(l) + 1 for l in group)  # +1 per newline
        separator_len = 1 if has_group_in_current else 0  # blank line only between two groups, not after a bare header

        # If the group fits in the current block, add it.
        if current_lines and (current_len + separator_len + group_text_len + overhead) <= limit:
            if has_group_in_current:
                current_lines.append("")  # blank separator line, only between groups
            current_lines.extend(group)
            current_len += separator_len + group_text_len
            has_group_in_current = True
            continue

        # If the group fits in a *fresh* block, flush and start a new one with it.
        if group_text_len + overhead <= limit:
            _flush()
            current_lines = list(group)
            current_len = group_text_len
            has_group_in_current = True
            continue

        # The group itself is too large even alone — split it line by line.
        _flush()
        sub: list[str] = []
        sub_len = 0
        for line in group:
            line_len = len(line) + 1
            if sub and (sub_len + line_len + overhead) > limit:
                blocks.append(fence_open + "\n".join(sub) + "\n" + fence_close)
                sub = [line]
                sub_len = line_len
            else:
                sub.append(line)
                sub_len += line_len
        if sub:
            current_lines = sub
            current_len = sub_len
            has_group_in_current = True

    _flush()
    if not blocks:
        blocks.append(fence_open + fence_close)
    return blocks

# ---------- Message builders (use ANSI for colored output) ----------
def build_loot_list_messages(session: dict) -> list[str]:
    """
    Build the 'loot list' message body/bodies (NOT including any page-number label —
    callers that assemble the full session message sequence apply unified numbering
    across ALL session messages, since this list's page count depends on item count
    and must be numbered relative to the control panel pages too).
    Returns a LIST of message strings because the remaining-items list can exceed
    Discord's 2000-char limit when there are many items; in that case it is split
    across multiple messages instead of being truncated.
    """
    remaining = [it for it in session["items"] if it["assigned_to"] is None]
    if remaining:
        header = [f"{RED}{BOLD}❌ Remaining Loot Items ❌{RESET}", "=================================="]
        lines = [f"{RED}{it['display_number']}.{RESET} {it['name']}" for it in remaining]
        blocks = _paginate_ansi_blocks(header, lines)
    else:
        blocks = [
            "```ansi\n"
            f"{GREEN}{BOLD}✅ All Items Assigned ✅{RESET}\n"
            "==================================\n"
            "All items have been distributed.\n"
            "```"
        ]
    return blocks

def build_last_assigned_messages(session: dict) -> list[str]:
    """
    Build 'Last Assigned Loot Items' view(s) showing the items assigned in the
    most recent action. Falls back to the normal loot list if no snapshot exists.
    Returns a list (paginated the same way as build_loot_list_messages).
    """
    last = session.get("last_action") or {}
    indices = last.get("assigned_indices") or []
    if not indices:
        return build_loot_list_messages(session)

    header = [f"{MAGENTA}{BOLD}📝 Last Assigned Loot Items 📝{RESET}", "=================================="]
    lines = []
    for idx in indices:
        if 0 <= idx < len(session["items"]):
            it = session["items"][idx]
            lines.append(f"{MAGENTA}{it['display_number']}.{RESET} {it['name']}")
    blocks = _paginate_ansi_blocks(header, lines)

    messages = []
    total = len(blocks)
    for i, block in enumerate(blocks):
        page_note = f" ({i+1}/{total})" if total > 1 else ""
        messages.append(f"**(1/2){page_note}**\n{block}")
    return messages

def _build_assigned_sections(session: dict, section_header_lines: list[str] | None = None) -> list[str]:
    """
    Build the assigned-items list as one or more ```ansi blocks, packing AS MANY
    players as possible into each block (separated by a single blank line) rather
    than giving every player their own code fence. This is what removes the large
    visual gaps between players that Discord renders around adjacent ``` fences.

    If section_header_lines is given (e.g. the "✅ Assigned Items ✅" title +
    divider), it's baked into the first returned block rather than becoming its
    own separate fenced section — otherwise the title would float in its own box
    with a gap before the first player, same problem as the player-to-player gaps.

    Still safely overflows a single oversized player into its own block(s) if
    needed. Returns a list of complete ```ansi ... ``` blocks ready to be packed
    into messages.
    """
    assigned_items = [it for it in session["items"] if it["assigned_to"]]
    assigned_items.sort(key=lambda x: x.get("assigned_order", 0))

    assigned_map = {r["member"].id: [] for r in session["rolls"]}
    for it in assigned_items:
        assigned_map.setdefault(it["assigned_to"], []).append(it["name"])

    line_groups: list[list[str]] = []
    for i, r in enumerate(session["rolls"]):
        emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        header_line = f"{BLUE}{emoji} {r['member'].display_name}{RESET}"
        items = assigned_map.get(r["member"].id, [])
        item_lines = [f"- {nm}" for nm in items] if items else ["- N/A"]
        line_groups.append([header_line] + item_lines)

    return _pack_lines_into_ansi_blocks(line_groups, leading_header_lines=section_header_lines)

def build_control_panel_messages(session: dict) -> list[str]:
    """
    Build the control panel message body/bodies: roll order + assigned items + status.
    Returns a LIST (NOT including any page-number label — see build_loot_list_messages
    for why numbering is applied later, across the whole session's messages).
    """
    roll_header = [f"{YELLOW}{BOLD}🎲 Roll Order 🎲{RESET}", "=================================="]
    roll_lines = _build_roll_lines(session).split("\n") if session["rolls"] else []
    roll_blocks = _paginate_ansi_blocks(roll_header, roll_lines)

    assigned_header_lines = [f"{GREEN}{BOLD}✅ Assigned Items ✅{RESET}", "=================================="]
    assigned_sections = _build_assigned_sections(session, section_header_lines=assigned_header_lines)

    indicator = ""
    if 0 <= session["current_turn"] < len(session["rolls"]):
        direction = "Normal" if session["direction"] == 1 else "Reverse"
        indicator = f"\n🔔 **Round {session['round'] + 1}** ({direction})\n\n"
    else:
        indicator = f"\n🎁 **Loot distribution is ready!**\n\n✍️ **Loot Manager can remove participants or click below to begin.**"
    expires = session.get("expires_at")
    if expires:
        try:
            ts = int(expires)
            indicator += f"\n⏳ Expires: <t:{ts}:R>\n"
        except Exception:
            pass

    # Pack: roll_blocks + assigned_sections (title baked into the first assigned
    # block) into as few messages as possible.
    all_sections = list(roll_blocks) + assigned_sections
    packed = _paginate_plain_sections(all_sections, limit=SAFE_CHUNK_LIMIT)

    messages = []
    for i, chunk in enumerate(packed):
        if i == 0:
            messages.append(f"✍️ **Loot Manager:** {session['invoker'].mention}\n\n{chunk}")
        else:
            messages.append(chunk)
    # Indicator goes on the last page only.
    messages[-1] = messages[-1] + indicator
    return messages

def build_final_summary_messages(session: dict, timed_out: bool = False) -> list[str]:
    """
    Build final summary message(s), shown either on timeout or completion.
    Returns a LIST for the same overflow-safety reasons as the other builders.
    """
    header = ("⌛ **The loot session has timed out!**\n\n" if timed_out
              else "✅ **All items have been assigned!**\n\n")

    roll_header = [f"{YELLOW}{BOLD}🎲 Roll Order 🎲{RESET}", "=================================="]
    roll_lines = _build_roll_lines(session).split("\n") if session["rolls"] else []
    roll_blocks = _paginate_ansi_blocks(roll_header, roll_lines)

    assigned_header_lines = [f"{GREEN}{BOLD}✅ Assigned Items ✅{RESET}", "=================================="]
    assigned_sections = _build_assigned_sections(session, section_header_lines=assigned_header_lines)

    unclaimed = [it for it in session["items"] if it["assigned_to"] is None]
    unclaimed_blocks = []
    if unclaimed:
        unclaimed_header = [f"{RED}{BOLD}❌ Unclaimed Items ❌{RESET}", "=================================="]
        unclaimed_lines = [f"{RED}{it['display_number']}.{RESET} {it['name']}" for it in unclaimed]
        unclaimed_blocks = _paginate_ansi_blocks(unclaimed_header, unclaimed_lines)

    all_sections = list(roll_blocks) + assigned_sections + unclaimed_blocks
    packed = _paginate_plain_sections(all_sections, limit=SAFE_CHUNK_LIMIT)

    messages = []
    total = len(packed)
    for i, chunk in enumerate(packed):
        if i == 0:
            messages.append(f"{header}{chunk}")
        else:
            messages.append(chunk)
    return messages

# Discord hard-caps a View to 5 action rows; two rows are reserved for buttons
# (Assign/Skip/Skip Remaining on one row, Undo/Add Item on the other), leaving 3
# rows for item Select menus. Each Select holds at most 25 options, so at most
# 75 items can be shown as choosable at once. Anything beyond that genuinely
# can't fit in this UI shape — flagged in the picker message rather than silently
# dropped, so the Loot Manager knows to use "Add Item" removal or split the pool.
MAX_DROPDOWN_ITEMS = 75

def _item_message_text_and_active(session: dict) -> tuple[str, bool]:
    """
    Returns tuple (message_text, is_active) for the 'item picker' message.
    is_active True means a picker should see a dropdown view created.
    """
    if not _are_items_left(session) or session["current_turn"] == TURN_NOT_STARTED:
        return ("No active picks right now.", False)
    if not (0 <= session["current_turn"] < len(session["rolls"])):
        return ("No active picks right now.", False)
    picker = session["rolls"][session["current_turn"]]["member"]
    emoji = NUMBER_EMOJIS.get(session["current_turn"] + 1, "👉")
    turn_text = "turn!" if not session.get("just_reversed", False) else "turn (direction reversed)!"
    text = f"**{emoji} {picker.mention}'s {turn_text}**\n\nChoose item(s) below:"

    available_count = sum(1 for it in session["items"] if it["assigned_to"] is None)
    if available_count > MAX_DROPDOWN_ITEMS:
        hidden = available_count - MAX_DROPDOWN_ITEMS
        text += (
            f"\n\n⚠️ *Showing the first {MAX_DROPDOWN_ITEMS} items — "
            f"{hidden} more are waiting and will appear once earlier items are assigned.*"
        )
    return (text, True)

def _assemble_session_messages(control_pages: list[str], loot_pages: list[str]) -> tuple[list[str], list[str]]:
    """
    Combine the raw control-panel page bodies and raw loot-list page bodies into
    their final, correctly-labeled text, with a SINGLE numbering scheme spanning
    the whole session (not two independent "(1/2)"/"(2/2)" labels).

    Ordering: control panel pages come first (this is the primary panel with the
    roll order and assignments), followed by loot-list pages, so that when extra
    pages exist they're appended in a stable, predictable position in the channel
    instead of the loot list's overflow pages landing between control panel pages
    or after the item-picker message.

    Returns (labeled_control_pages, labeled_loot_pages) — same lengths as the
    inputs, in the same relative order, just with the correct "(i/total)" label
    applied to each.
    """
    total = len(control_pages) + len(loot_pages)

    labeled_control = []
    for i, body in enumerate(control_pages):
        page_note = f" ({i+1}/{total})" if total > 1 else ""
        labeled_control.append(f"**(2/2){page_note}**\n\n{body}" if i == 0 else f"**(2/2){page_note}**\n{body}")

    labeled_loot = []
    offset = len(control_pages)
    for i, body in enumerate(loot_pages):
        n = offset + i
        page_note = f" ({n+1}/{total})" if total > 1 else ""
        labeled_loot.append(f"**(1/2){page_note}**\n{body}")

    return labeled_control, labeled_loot

# ---------- Helper to sync a list of target messages against a list of desired contents ----------
async def _sync_message_list(channel, existing_ids: list[int], desired_contents: list[str],
                              views: list | None = None) -> list[int]:
    """
    Ensures `channel` has exactly len(desired_contents) messages matching existing_ids
    (in order), editing in place where possible, sending new ones if desired_contents
    is longer than existing_ids, and deleting extras if it's shorter.

    `views` (optional) must be the same length as desired_contents; only applied to
    the LAST message in the list (views are only ever attached to the final page).

    Returns the updated list of message ids (same length as desired_contents), or
    fewer if sends failed (e.g. due to missing permissions) — callers should check
    the returned length against what they expected.

    Gracefully handles nextcord.Forbidden (missing access) by giving up on sending
    further pages rather than crashing the caller.
    """
    result_ids: list[int] = []
    n_existing = len(existing_ids)
    n_desired = len(desired_contents)

    for i, content in enumerate(desired_contents):
        view = None
        if views and i == len(desired_contents) - 1:
            view = views[i] if i < len(views) else None

        if i < n_existing:
            msg = await _get_msg(channel, existing_ids[i])
            if msg:
                try:
                    if view is not None:
                        await msg.edit(content=content, view=view)
                    else:
                        await msg.edit(content=content)
                    result_ids.append(msg.id)
                    continue
                except nextcord.Forbidden:
                    # Can't edit (unlikely) — try to continue with what we have.
                    result_ids.append(existing_ids[i])
                    continue
                except Exception:
                    result_ids.append(existing_ids[i])
                    continue
            # Existing id was stale/deleted; fall through to send a new one.

        try:
            if view is not None:
                sent = await channel.send(content, view=view)
            else:
                sent = await channel.send(content)
            result_ids.append(sent.id)
        except nextcord.Forbidden:
            # Bot lacks Send Messages / Embed Links etc. in this channel.
            # Stop trying further pages; return what we have so far.
            break
        except Exception:
            break

    # Delete any leftover messages beyond what we now need.
    if n_existing > len(result_ids):
        for extra_id in existing_ids[len(result_ids):n_existing]:
            msg = await _get_msg(channel, extra_id)
            if msg:
                try:
                    await msg.delete()
                except Exception:
                    pass

    return result_ids

async def _delete_message_list(channel, ids: list[int]):
    """Delete every message id in the list, best-effort."""
    for mid in ids or []:
        msg = await _get_msg(channel, mid)
        if msg:
            try:
                await msg.delete()
            except Exception:
                pass

# ---------- UI Views: Item dropdown view, Add Item Modal, and Control panel view ----------

class AddItemModal(nextcord.ui.Modal):
    """
    Modal for the Loot Master to add a new item dynamically during distribution.
    """
    def __init__(self, session_id: int):
        super().__init__("Add Loot Item")
        self.session_id = session_id
        self.item_input = nextcord.ui.TextInput(
            label="Item Name (supports '2x Item' syntax)",
            placeholder="e.g. 1x Mysterious Orb",
            required=True,
            style=nextcord.TextInputStyle.short,
            max_length=100
        )
        self.add_item(self.item_input)

    async def callback(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return

        # Parse input
        s = self.item_input.value.strip()
        names = []
        if s:
            m = re.match(r"(\d+)[xX]\s*(.*)", s)
            if m:
                try:
                    c = int(m.group(1))
                    nm = m.group(2).strip()
                    if nm:
                        names.extend([nm] * c)
                    else:
                        names.append(s)
                except Exception:
                    names.append(s)
            else:
                names.append(s)

        if not names:
            await interaction.response.send_message("Invalid item name.", ephemeral=True)
            return

        # Determine next display number
        current_max = 0
        for it in session["items"]:
            try:
                if it["display_number"] > current_max:
                    current_max = it["display_number"]
            except Exception:
                pass

        new_items = [{"name": n, "assigned_to": None, "display_number": current_max + i + 1} for i, n in enumerate(names)]
        session["items"].extend(new_items)

        await _reset_session_timeout(self.session_id)
        await interaction.response.defer(ephemeral=True)
        _schedule_refresh(self.session_id, delete_item=True)

class ItemDropdownView(nextcord.ui.View):
    """
    Dropdown view that shows all currently available items for the active picker.
    Supports multi-chunk selects (25 options per select) and buttons for assign/skip/undo.
    """
    def __init__(self, session_id: int):
        super().__init__(timeout=None)
        self.session_id = session_id
        self._populate()

    def _populate(self):
        """
        Build Select options and Buttons based on current session state.
        Fixes the '25+ items' crash by dynamically assigning Action Rows.
        """
        self.clear_items()
        session = loot_sessions.get(self.session_id)
        if not session:
            return
        if not _are_items_left(session):
            return
        if not (0 <= session["current_turn"] < len(session["rolls"])):
            return

        available = [(i, it) for i, it in enumerate(session["items"]) if it["assigned_to"] is None]
        if not available:
            return

        chunks = [available[i:i+25] for i in range(0, len(available), 25)]
        selected = set(session.get("selected_items") or [])

        max_dropdowns = MAX_DROPDOWN_ITEMS // 25  # 3 rows worth of Select menus (see MAX_DROPDOWN_ITEMS)
        dropdown_count = min(len(chunks), max_dropdowns)

        for ci in range(dropdown_count):
            chunk = chunks[ci]
            opts = []
            for idx, item in chunk:
                label = f"{item['display_number']}. {item['name']}"
                truncated = (label[:97] + "...") if len(label) > 100 else label
                is_selected = str(idx) in selected
                opts.append(nextcord.SelectOption(label=truncated, value=str(idx), default=is_selected))

            placeholder = "Choose item(s)..." if dropdown_count == 1 else f"Items {chunk[0][1]['display_number']} - {chunk[-1][1]['display_number']}"

            self.add_item(nextcord.ui.Select(
                placeholder=placeholder,
                options=opts,
                custom_id=f"item_select_{ci}",
                min_values=0,
                max_values=len(opts),
                row=ci
            ))

        btn_row_1 = dropdown_count
        btn_row_2 = dropdown_count + 1

        assign_disabled = not session.get("selected_items")
        self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.success, emoji="✅", custom_id="assign_button", disabled=assign_disabled, row=btn_row_1))
        self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button", row=btn_row_1))
        self.add_item(nextcord.ui.Button(label="Skip Remaining", style=nextcord.ButtonStyle.danger, custom_id="skip_remaining_button", row=btn_row_1))

        undo_disabled = not session.get("last_action")
        self.add_item(nextcord.ui.Button(label="Undo", style=nextcord.ButtonStyle.secondary, emoji="↩️", custom_id="undo_button", disabled=undo_disabled, row=btn_row_2))
        self.add_item(nextcord.ui.Button(label="Add Item", style=nextcord.ButtonStyle.primary, emoji="➕", custom_id="add_item_button", row=btn_row_2))

        for child in self.children:
            if isinstance(child, nextcord.ui.Button):
                if child.custom_id == "assign_button": child.callback = self.on_assign
                elif child.custom_id == "skip_button": child.callback = self.on_skip
                elif child.custom_id == "skip_remaining_button": child.callback = self.on_skip_remaining
                elif child.custom_id == "undo_button": child.callback = self.on_undo
                elif child.custom_id == "add_item_button": child.callback = self.on_add_item
            elif isinstance(child, nextcord.ui.Select):
                child.callback = self.on_item_select

    async def _fast_edit(self, interaction: nextcord.Interaction, content: str, view: nextcord.ui.View | None) -> bool:
        """
        Attempt quick edit via interaction.response.edit_message; fallback to fetching & editing
        the stored item-dropdown message id, or send a new message if needed.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return False

        try:
            await interaction.response.edit_message(content=content, view=view)
            return True
        except Exception:
            try:
                await interaction.response.defer()
            except Exception:
                try:
                    await interaction.response.send_message("Processing...", ephemeral=True)
                except Exception:
                    pass

            ch = bot.get_channel(session["channel_id"])
            if not ch:
                return False
            existing_id = session.get("item_dropdown_message_id")
            if existing_id:
                try:
                    msg = await _get_msg(ch, existing_id)
                    if msg:
                        await msg.edit(content=content, view=view)
                        return True
                except Exception:
                    pass
            try:
                msg = await ch.send(content, view=view)
                session["item_dropdown_message_id"] = msg.id
                return True
            except Exception:
                return False

    async def _ack(self, interaction: nextcord.Interaction):
        """Helper to acknowledge interactions gracefully."""
        try:
            await interaction.response.defer()
        except Exception:
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message("Processing...", ephemeral=True)
                except Exception:
                    pass

    async def on_item_select(self, interaction: nextcord.Interaction):
        """
        When user (re)selects items, persist selections into session['selected_items'].
        Uses set arithmetic to keep selections across chunked selects.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            await self._ack(interaction)
            try:
                await interaction.followup.send("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        cid = interaction.data.get("custom_id")
        if not cid:
            await self._ack(interaction)
            try:
                await interaction.followup.send("Invalid selection.", ephemeral=True)
            except Exception:
                pass
            return
        try:
            idx = int(cid.split("_")[-1])
        except Exception:
            await self._ack(interaction)
            try:
                await interaction.followup.send("Malformed dropdown id.", ephemeral=True)
            except Exception:
                pass
            return

        available = [(i, it) for i, it in enumerate(session["items"]) if it["assigned_to"] is None]
        chunks = [available[i:i+25] for i in range(0, len(available), 25)]
        if idx >= len(chunks):
            await self._ack(interaction)
            try:
                await interaction.followup.send("Stale dropdown.", ephemeral=True)
            except Exception:
                pass
            return

        possible = {str(i) for i, _ in chunks[idx]}
        newly = set(interaction.data.get("values", []))
        lock = session_locks.setdefault(self.session_id, asyncio.Lock())
        async with lock:
            current = set(session.get("selected_items") or [])
            current -= possible
            current |= newly
            session["selected_items"] = list(current)

        await self._ack(interaction)
        await _reset_session_timeout(self.session_id)
        _schedule_refresh(self.session_id, delete_item=False)

    async def on_assign(self, interaction: nextcord.Interaction):
        """
        Assign selected items to the current picker (or allow invoker to assign).
        Records an undo snapshot in session['last_action'].
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        if session["current_turn"] < 0 or session["current_turn"] >= len(session["rolls"]):
            try:
                await interaction.response.send_message("It's not an active picking turn.", ephemeral=True)
            except Exception:
                pass
            return

        picker = session["rolls"][session["current_turn"]]["member"]
        if interaction.user.id not in (picker.id, session["invoker_id"]):
            try:
                await interaction.response.send_message("🛡️ Only the current picker or the Loot Manager can assign items.", ephemeral=True)
            except Exception:
                pass
            return

        selected = session.get("selected_items") or []
        session["last_action"] = {
            "turn": session["current_turn"],
            "round": session["round"],
            "direction": session["direction"],
            "just_reversed": session.get("just_reversed", False),
            "assigned_indices": [int(i) for i in selected] if selected else []
        }

        for s in selected:
            try:
                idx = int(s)
            except Exception:
                continue
            if 0 <= idx < len(session["items"]):
                session["items"][idx]["assigned_to"] = picker.id
                session["items"][idx]["assigned_order"] = session["assignment_counter"]
                session["assignment_counter"] += 1

        session["selected_items"] = None
        _advance_turn_snake(session)
        await _reset_session_timeout(self.session_id)

        new_text, active = _item_message_text_and_active(session)
        new_view = ItemDropdownView(self.session_id) if active else None

        await self._fast_edit(interaction, new_text, new_view)
        _schedule_refresh(self.session_id, delete_item=True)

    async def on_skip(self, interaction: nextcord.Interaction):
        """
        Skip the current pick. Only the picker or invoker can skip.
        Records undo state if appropriate.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        if 0 <= session["current_turn"] < len(session["rolls"]):
            picker = session["rolls"][session["current_turn"]]["member"]
            if interaction.user.id not in (picker.id, session["invoker_id"]):
                try:
                    await interaction.response.send_message("🛡️ Only the current picker or the Loot Manager can skip the turn.", ephemeral=True)
                except Exception:
                    pass
                return

        if session["current_turn"] != TURN_NOT_STARTED:
            session["last_action"] = {
                "turn": session["current_turn"],
                "round": session["round"],
                "direction": session["direction"],
                "just_reversed": session.get("just_reversed", False),
                "assigned_indices": []
            }

        session["selected_items"] = None
        if session["current_turn"] == TURN_NOT_STARTED:
            session["members_to_remove"] = None
            session["last_action"] = None

        _advance_turn_snake(session)
        await _reset_session_timeout(self.session_id)

        new_text, active = _item_message_text_and_active(session)
        new_view = ItemDropdownView(self.session_id) if active else None

        await self._fast_edit(interaction, new_text, new_view)
        _schedule_refresh(self.session_id, delete_item=True)

    async def on_skip_remaining(self, interaction: nextcord.Interaction):
        """
        Marks the current picker as 'skipped' for the rest of the distribution.
        They remain in the lists but are skipped in turn order.
        Allowed for: Current picker or Loot Master.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        if not (0 <= session["current_turn"] < len(session["rolls"])):
            try:
                await interaction.response.send_message("No active turn.", ephemeral=True)
            except Exception:
                pass
            return

        current_roller = session["rolls"][session["current_turn"]]
        picker_member = current_roller["member"]

        if interaction.user.id not in (picker_member.id, session["invoker_id"]):
            try:
                await interaction.response.send_message("🛡️ Only the current picker or the Loot Manager can use this.", ephemeral=True)
            except Exception:
                pass
            return

        session["last_action"] = {
            "turn": session["current_turn"],
            "round": session["round"],
            "direction": session["direction"],
            "just_reversed": session.get("just_reversed", False),
            "assigned_indices": [],
            "skipped_turn_action": True
        }

        session["rolls"][session["current_turn"]]["skipped"] = True
        session["selected_items"] = None

        _advance_turn_snake(session)
        await _reset_session_timeout(self.session_id)

        try:
            await interaction.response.defer()
        except Exception:
            pass
        _schedule_refresh(self.session_id, delete_item=True)

    async def on_undo(self, interaction: nextcord.Interaction):
        """
        Undo the last assign/skip action. Only the Loot Manager (invoker) can undo.
        Restores assigned items referenced in last_action.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        if interaction.user.id != session["invoker_id"]:
            try:
                await interaction.response.send_message("🛡️ Only the Loot Manager can use Undo.", ephemeral=True)
            except Exception:
                pass
            return

        last = session.get("last_action")
        if not last:
            try:
                await interaction.response.send_message("❌ There is nothing to undo.", ephemeral=True)
            except Exception:
                pass
            return

        for idx in last.get("assigned_indices", []):
            if 0 <= idx < len(session["items"]):
                session["items"][idx]["assigned_to"] = None
                session["items"][idx]["assigned_order"] = -1

        session["current_turn"] = last["turn"]
        session["round"] = last["round"]
        session["direction"] = last["direction"]
        session["just_reversed"] = last.get("just_reversed", False)

        if last.get("skipped_turn_action"):
            if 0 <= last["turn"] < len(session["rolls"]):
                session["rolls"][last["turn"]]["skipped"] = False

        session["last_action"] = None
        session["selected_items"] = None

        await _reset_session_timeout(self.session_id)
        new_text, active = _item_message_text_and_active(session)
        new_view = ItemDropdownView(self.session_id) if active else None
        await self._fast_edit(interaction, new_text, new_view)
        _schedule_refresh(self.session_id, delete_item=True)

    async def on_add_item(self, interaction: nextcord.Interaction):
        """
        Open the modal to add an item. Only for Loot Master.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        if interaction.user.id != session["invoker_id"]:
            try:
                await interaction.response.send_message("🛡️ Only the Loot Manager can add items.", ephemeral=True)
            except Exception:
                pass
            return

        await interaction.response.send_modal(AddItemModal(self.session_id))

class ControlPanelView(nextcord.ui.View):
    """
    Control panel used by the Loot Manager to remove participants or start assignment.
    Only the invoker can interact with these controls (enforced by interaction_check).
    """
    def __init__(self, session_id: int):
        super().__init__(timeout=None)
        self.session_id = session_id
        self._populate()

    def _populate(self):
        """
        Populate remove-select and start button when the session hasn't started.
        Uses session['members_to_remove'] (list[str]) to keep defaults for the select.
        """
        self.clear_items()
        session = loot_sessions.get(self.session_id)
        if not session:
            return

        if session["current_turn"] == TURN_NOT_STARTED:
            options = []
            inv = session["invoker_id"]
            members_to_remove = set(session.get("members_to_remove") or [])
            for r in session["rolls"]:
                if r["member"].id != inv:
                    val = str(r["member"].id)
                    default_selected = val in members_to_remove
                    options.append(nextcord.SelectOption(label=r["member"].display_name, value=val, default=default_selected))
            if options:
                self.add_item(nextcord.ui.Select(placeholder="Select participants to remove...", options=options, custom_id="remove_select", min_values=0, max_values=len(options)))
            self.add_item(nextcord.ui.Button(label="Remove Selected", style=nextcord.ButtonStyle.danger, emoji="✖️", custom_id="remove_confirm_button"))
            self.add_item(nextcord.ui.Button(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="start_button"))
        for child in self.children:
            if getattr(child, "custom_id", "") == "remove_select":
                child.callback = self.on_remove_select
            if getattr(child, "custom_id", "") == "remove_confirm_button":
                child.callback = self.on_remove_confirm
            if getattr(child, "custom_id", "") == "start_button":
                child.callback = self.on_start

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        """
        Only the session invoker can interact with the control panel; others receive an ephemeral notice.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("❌ Session expired or not found.", ephemeral=True)
            except Exception:
                pass
            return False
        if interaction.user.id == session["invoker_id"]:
            return True
        try:
            await interaction.response.send_message(f"🛡️ Only {session['invoker'].mention} can use control-panel buttons.", ephemeral=True)
        except Exception:
            pass
        return False

    async def on_remove_select(self, interaction: nextcord.Interaction):
        """
        Persist removal selections into session['members_to_remove'] (list[str]) and re-render view.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return
        vals = interaction.data.get("values") or []
        session["members_to_remove"] = list(vals)
        self._populate()
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

    async def on_remove_confirm(self, interaction: nextcord.Interaction):
        """
        Remove chosen participants from session['rolls']. If no participants remain,
        cancel the session and clean up messages and tasks.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return
        vals = session.get("members_to_remove") or []
        to_remove = set()
        for v in vals:
            try:
                to_remove.add(int(v))
            except Exception:
                continue
        if to_remove:
            session["rolls"] = [r for r in session["rolls"] if r["member"].id not in to_remove]
            session["members_to_remove"] = None
            if not session["rolls"]:
                ch = bot.get_channel(session["channel_id"])
                try:
                    await _delete_message_list(ch, session.get("loot_list_message_ids") or [])
                except Exception:
                    pass
                try:
                    it = await _get_msg(ch, session.get("item_dropdown_message_id"))
                    if it:
                        await it.delete()
                except Exception:
                    pass
                try:
                    ctrl_ids = session.get("control_panel_message_ids") or [self.session_id]
                    for i, cid in enumerate(ctrl_ids):
                        ctrl = await _get_msg(ch, cid)
                        if ctrl:
                            if i == 0:
                                await ctrl.edit(content="⚠️ The loot session was cancelled — no participants remain.", view=None)
                            else:
                                await ctrl.delete()
                except Exception:
                    pass
                t = session.get("timeout_task")
                if t:
                    try:
                        t.cancel()
                    except Exception:
                        pass
                loot_sessions.pop(self.session_id, None)
                session_locks.pop(self.session_id, None)
                try:
                    await interaction.response.send_message("Session cancelled — no participants remain.", ephemeral=True)
                except Exception:
                    pass
                return
            if session["current_turn"] != TURN_NOT_STARTED and session["current_turn"] >= len(session["rolls"]):
                session["current_turn"] = max(0, len(session["rolls"]) - 1)

        await _reset_session_timeout(self.session_id)
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        _schedule_refresh(self.session_id, delete_item=True)

    async def on_start(self, interaction: nextcord.Interaction):
        """
        Start the assignment process by advancing to the first turn.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return
        session["members_to_remove"] = None
        session["selected_items"] = None
        session["last_action"] = None
        _advance_turn_snake(session)
        await _reset_session_timeout(self.session_id)
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        _schedule_refresh(self.session_id, delete_item=True)


class FinalizeView(nextcord.ui.View):
    """
    View shown when all items have been assigned. Only the invoker can interact.
    Provides two buttons:
      - 📝 Finish Loot Distribution: merge messages and finish session
      - ↩️ Undo: undo the last assigned items and continue the rounds
    """
    def __init__(self, session_id: int):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.add_item(nextcord.ui.Button(label="📝 Finish Loot Distribution", style=nextcord.ButtonStyle.success, custom_id="finalize_finish"))
        self.add_item(nextcord.ui.Button(label="↩️ Undo", style=nextcord.ButtonStyle.secondary, custom_id="finalize_undo"))
        for child in self.children:
            if getattr(child, "custom_id", "") == "finalize_finish":
                child.callback = self.on_finish
            if getattr(child, "custom_id", "") == "finalize_undo":
                child.callback = self.on_undo

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return False
        if interaction.user.id == session["invoker_id"]:
            return True
        try:
            await interaction.response.send_message(f"🛡️ Only {session['invoker'].mention} can use these controls.", ephemeral=True)
        except Exception:
            pass
        return False

    async def on_finish(self, interaction: nextcord.Interaction):
        """Merge messages and finish the loot distribution (same as prior finalization)."""
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        try:
            await interaction.response.defer()
        except Exception:
            try:
                await interaction.response.send_message("Finishing...", ephemeral=True)
            except Exception:
                pass

        ch = bot.get_channel(session["channel_id"])
        final_pages = build_final_summary_messages(session, timed_out=False)

        ctrl_ids = session.get("control_panel_message_ids") or [self.session_id]
        try:
            await _sync_message_list(ch, ctrl_ids, final_pages, views=None)
        except Exception:
            pass

        try:
            await _delete_message_list(ch, session.get("loot_list_message_ids") or [])
        except Exception:
            pass

        try:
            existing = session.get("item_dropdown_message_id")
            if existing:
                maybe = await _get_msg(ch, existing)
                if maybe:
                    try:
                        await maybe.delete()
                    except Exception:
                        pass
        except Exception:
            pass

        t = session.get("timeout_task")
        if t:
            try:
                t.cancel()
            except Exception:
                pass
        session.pop("finalize_shown", None)
        loot_sessions.pop(self.session_id, None)
        session_locks.pop(self.session_id, None)

    async def on_undo(self, interaction: nextcord.Interaction):
        """Undo the last assign/skip action (invoker only) and resume the rounds."""
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        if interaction.user.id != session["invoker_id"]:
            try:
                await interaction.response.send_message("🛡️ Only the Loot Manager can use Undo.", ephemeral=True)
            except Exception:
                pass
            return

        last = session.get("last_action")
        if not last:
            try:
                await interaction.response.send_message("❌ There is nothing to undo.", ephemeral=True)
            except Exception:
                pass
            return

        for idx in last.get("assigned_indices", []):
            if 0 <= idx < len(session["items"]):
                session["items"][idx]["assigned_to"] = None
                session["items"][idx]["assigned_order"] = -1

        session["current_turn"] = last["turn"]
        session["round"] = last["round"]
        session["direction"] = last["direction"]
        session["just_reversed"] = last.get("just_reversed", False)

        if last.get("skipped_turn_action"):
            if 0 <= last["turn"] < len(session["rolls"]):
                session["rolls"][last["turn"]]["skipped"] = False

        session["last_action"] = None
        session["selected_items"] = None

        await _reset_session_timeout(self.session_id)

        ch = bot.get_channel(session["channel_id"])
        try:
            existing = session.get("item_dropdown_message_id")
            if existing:
                maybe = await _get_msg(ch, existing)
                if maybe:
                    try:
                        await maybe.delete()
                    except Exception:
                        pass
        except Exception:
            pass
        session["item_dropdown_message_id"] = None
        session.pop("finalize_shown", None)

        _schedule_refresh(self.session_id, delete_item=True)

# ---------- Message lifecycle, refresh, and timeout ----------
async def _reset_session_timeout(session_id: int):
    """
    Cancel any existing timeout task and schedule a fresh timeout for the session.
    """
    session = loot_sessions.get(session_id)
    if not session:
        return
    task = session.get("timeout_task")
    if task:
        try:
            task.cancel()
        except Exception:
            pass
    session["timeout_task"] = asyncio.create_task(_schedule_session_timeout(session_id))
    try:
        session["expires_at"] = int(time.time() + SESSION_TIMEOUT_SECONDS)
    except Exception:
        session["expires_at"] = None

async def _refresh_all_messages(session_id: int, delete_item: bool = True):
    """
    Synchronize all messages for the session:
      - loot list (left) — now potentially MULTIPLE messages
      - control panel (right) — now potentially MULTIPLE messages
      - item dropdown (third, recreated as requested)
    The delete_item flag controls whether the third message is forcibly deleted
    and recreated (used to force a fresh view).
    """
    session = loot_sessions.get(session_id)
    if not session:
        return
    lock = session_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        ch = bot.get_channel(session["channel_id"])
        if not ch:
            t = session.get("timeout_task")
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
            loot_sessions.pop(session_id, None)
            session_locks.pop(session_id, None)
            return

        ctrl_ids = session.get("control_panel_message_ids") or [session_id]
        loot_ids = session.get("loot_list_message_ids") or []
        existing_item_msg = None
        existing_item_id = session.get("item_dropdown_message_id")
        if existing_item_id:
            existing_item_msg = await _get_msg(ch, existing_item_id)

        if delete_item and existing_item_msg:
            try:
                await existing_item_msg.delete()
            except Exception:
                pass
            session["item_dropdown_message_id"] = None
            existing_item_msg = None
            existing_item_id = None

        # If distribution complete, show final summary and present a finalize view.
        if not _are_items_left(session) and session["current_turn"] != TURN_NOT_STARTED:
            await _reset_session_timeout(session_id)

            raw_final_ctrl_pages = build_control_panel_messages(session)
            final_ctrl_pages, _empty_loot = _assemble_session_messages(raw_final_ctrl_pages, [])
            try:
                new_ctrl_ids = await _sync_message_list(ch, ctrl_ids, final_ctrl_pages, views=None)
                session["control_panel_message_ids"] = new_ctrl_ids
            except Exception:
                pass

            # No remaining-items page is relevant once distribution is complete —
            # remove any leftover loot-list pages from the channel.
            try:
                await _delete_message_list(ch, loot_ids)
                session["loot_list_message_ids"] = []
                session["loot_list_message_id"] = None
            except Exception:
                pass

            finalize_text = f"✍️ {session['invoker'].mention}\n\nClick an action below to finish or undo the last assignment."
            finalize_view = FinalizeView(session_id)

            if existing_item_msg:
                try:
                    await existing_item_msg.delete()
                except Exception:
                    pass

            try:
                sent = await ch.send(finalize_text, view=finalize_view)
                session["item_dropdown_message_id"] = sent.id
                session["finalize_shown"] = True
            except nextcord.Forbidden:
                session["item_dropdown_message_id"] = None
            except Exception:
                pass

            return

        # Build current contents (each is now a LIST of raw page bodies), apply
        # unified numbering across both lists, then sync CONTROL PANEL FIRST and
        # LOOT LIST SECOND so any overflow pages land in a stable, predictable
        # order in the channel: control panel page(s) -> loot list page(s) ->
        # item picker (sent/edited below).
        raw_loot_pages = build_loot_list_messages(session)
        raw_control_pages = build_control_panel_messages(session)
        control_pages, loot_pages = _assemble_session_messages(raw_control_pages, raw_loot_pages)

        try:
            new_ctrl_ids = await _sync_message_list(ch, ctrl_ids, control_pages, views=[ControlPanelView(session_id)] * len(control_pages))
            session["control_panel_message_ids"] = new_ctrl_ids
        except Exception:
            pass

        try:
            new_loot_ids = await _sync_message_list(ch, loot_ids, loot_pages, views=None)
            session["loot_list_message_ids"] = new_loot_ids
            # Keep legacy single-id field pointing at the first page for any external reference.
            session["loot_list_message_id"] = new_loot_ids[0] if new_loot_ids else None
        except Exception:
            pass

        await _reset_session_timeout(session_id)

        is_active = (0 <= session["current_turn"] < len(session["rolls"])) and _are_items_left(session)
        if not is_active:
            if not delete_item and existing_item_msg:
                try:
                    await existing_item_msg.delete()
                except Exception:
                    pass
                session["item_dropdown_message_id"] = None
            return

        item_text, _active = _item_message_text_and_active(session)
        view = ItemDropdownView(session_id)

        if existing_item_msg and not delete_item:
            try:
                await existing_item_msg.edit(content=item_text, view=view)
                session["item_dropdown_message_id"] = existing_item_id
                return
            except Exception:
                session["item_dropdown_message_id"] = None
                existing_item_msg = None

        try:
            new_msg = await ch.send(item_text, view=view)
            session["item_dropdown_message_id"] = new_msg.id
        except nextcord.Forbidden:
            session["item_dropdown_message_id"] = None
        except Exception:
            session["item_dropdown_message_id"] = None


def _schedule_refresh(session_id: int, delete_item: bool = True) -> asyncio.Task | None:
    """
    Schedule a stored refresh task for a session. This helper cancels any
    previously scheduled refresh task on the session and stores the new task
    in session['refresh_task'] so it isn't garbage-collected prematurely.
    Returns the scheduled Task or None if scheduling failed.
    """
    session = loot_sessions.get(session_id)
    if not session:
        return None
    prev = session.get("refresh_task")
    if prev and not prev.done():
        try:
            prev.cancel()
        except Exception:
            pass
    try:
        t = asyncio.create_task(_refresh_all_messages(session_id, delete_item=delete_item))
        session["refresh_task"] = t
        return t
    except Exception:
        session["refresh_task"] = None
        return None

async def _schedule_session_timeout(session_id: int):
    """
    Sleep for SESSION_TIMEOUT_SECONDS and then expire/cleanup the session.
    This removes the temporary messages and edits the control message(s) with a timed-out summary.
    """
    try:
        await asyncio.sleep(SESSION_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return

    session = loot_sessions.pop(session_id, None)
    session_locks.pop(session_id, None)
    if not session:
        return
    ch = bot.get_channel(session["channel_id"])
    if not ch:
        return

    loot_ids = session.get("loot_list_message_ids") or []
    try:
        if session.get("finalize_shown"):
            # Merge: put the timed-out final summary in place of the loot-list message(s).
            merged_pages = build_final_summary_messages(session, timed_out=True)
            try:
                await _sync_message_list(ch, loot_ids, merged_pages, views=None)
            except Exception:
                await _delete_message_list(ch, loot_ids)
        else:
            await _delete_message_list(ch, loot_ids)
    except Exception:
        pass

    try:
        im = await _get_msg(ch, session.get("item_dropdown_message_id"))
        if im:
            await im.delete()
    except Exception:
        pass

    final_pages = build_final_summary_messages(session, timed_out=True)
    ctrl_ids = session.get("control_panel_message_ids") or [session_id]
    try:
        await _sync_message_list(ch, ctrl_ids, final_pages, views=None)
    except Exception:
        pass

# ---------- Modal and command logic ----------
class LootModal(nextcord.ui.Modal):
    """
    Modal requesting an item list from the invoker.
    Format supports simple 'Nx ItemName' lines (e.g., '2x Health Potion').
    """
    def __init__(self):
        super().__init__("RNGenie Loot Manager")
        self.loot_items = nextcord.ui.TextInput(
            label="List Items Below (One Per Line) Then Submit",
            placeholder="Type your items here\nExample: 2x Health Potion",
            required=True,
            style=nextcord.TextInputStyle.paragraph,
            max_length=2000
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        """
        Modal callback performs safety checks and initializes the session.
        It also creates the messages (loot list, control panel, and item dropdown),
        gracefully handling missing-permission errors instead of crashing.
        """
        channel_type = getattr(interaction.channel, "type", None)
        if channel_type in (nextcord.ChannelType.voice, nextcord.ChannelType.stage_voice):
            await interaction.response.send_message("❌ Please run `/loot` in a regular text channel (not a voice-linked text chat).", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if not (interaction.user and interaction.user.voice and interaction.user.voice.channel):
            await interaction.followup.send("❌ You must be in a voice channel to set up a loot roll.", ephemeral=True)
            return

        members = interaction.user.voice.channel.members
        if not members:
            await interaction.followup.send("❌ I could not find anyone in your voice channel.", ephemeral=True)
            return
        if len(members) > 20:
            await interaction.followup.send(f"❌ Too many users in the voice channel ({len(members)})! The maximum is 20.", ephemeral=True)
            return

        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        by_roll = {}
        for r in rolls:
            by_roll.setdefault(r["roll"], []).append(r)
        for val, group in by_roll.items():
            if len(group) > 1:
                for r in group:
                    r["tiebreak"] = random.randint(1, 100)

        def _sort_key(r):
            return (r["roll"], r.get("tiebreak", -1))
        rolls.sort(key=_sort_key, reverse=True)

        lines = self.loot_items.value.splitlines()
        names = []
        for l in lines:
            s = l.strip()
            if not s:
                continue
            m = re.match(r"(\d+)[xX]\s*(.*)", s)
            if m:
                try:
                    c = int(m.group(1))
                    nm = m.group(2).strip()
                    if nm:
                        names.extend([nm] * c)
                    else:
                        names.append(s)
                except Exception:
                    names.append(s)
            else:
                names.append(s)

        items = [{"name": n, "assigned_to": None, "display_number": i} for i, n in enumerate(names, 1)]
        if not items:
            await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)
            return

        # Check permissions up front so we can give a clear error instead of a raw traceback.
        me = interaction.channel.guild.me if getattr(interaction.channel, "guild", None) else None
        perms = interaction.channel.permissions_for(me) if me else None
        if perms is not None and not (perms.send_messages and perms.view_channel):
            await interaction.followup.send(
                "❌ I don't have permission to send messages in this channel. "
                "Please ask a server admin to grant me **View Channel** and **Send Messages** here, then try again.",
                ephemeral=True
            )
            return

        try:
            loot_msg = await interaction.followup.send("`Initializing Loot List...`", wait=True)
            control_msg = await interaction.channel.send("`Initializing Control Panel...`")
        except nextcord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to send messages in this channel. "
                "Please ask a server admin to grant me **Send Messages** here, then try again.",
                ephemeral=True
            )
            return

        session_id = control_msg.id
        session = {
            "rolls": rolls,
            "items": items,
            "current_turn": TURN_NOT_STARTED,
            "invoker_id": interaction.user.id,
            "invoker": interaction.user,
            "selected_items": None,
            "round": 0,
            "direction": 1,
            "just_reversed": False,
            "members_to_remove": None,
            "channel_id": control_msg.channel.id,
            "loot_list_message_ids": [loot_msg.id],
            "loot_list_message_id": loot_msg.id,  # legacy single-id reference
            "control_panel_message_ids": [control_msg.id],
            "item_dropdown_message_id": None,
            "last_action": None,
            "timeout_task": None,
            "assignment_counter": 0
        }
        loot_sessions[session_id] = session
        await _reset_session_timeout(session_id)

        # Build raw page bodies, then apply unified numbering across BOTH lists so
        # labels reflect real total message count, not a hardcoded "1/2"/"2/2".
        raw_loot_pages = build_loot_list_messages(session)
        raw_control_pages = build_control_panel_messages(session)
        control_pages, loot_pages = _assemble_session_messages(raw_control_pages, raw_loot_pages)

        # Sync the control panel FIRST, then the loot list, so any overflow pages
        # from either one land in a stable, predictable order in the channel:
        # control panel page(s) -> loot list page(s) -> item picker (sent later).
        try:
            new_ctrl_ids = await _sync_message_list(
                control_msg.channel, [control_msg.id], control_pages,
                views=[ControlPanelView(session_id)] * len(control_pages)
            )
            session["control_panel_message_ids"] = new_ctrl_ids
        except Exception:
            pass

        try:
            new_loot_ids = await _sync_message_list(control_msg.channel, [loot_msg.id], loot_pages, views=None)
            session["loot_list_message_ids"] = new_loot_ids
            session["loot_list_message_id"] = new_loot_ids[0] if new_loot_ids else None
        except Exception:
            pass

        _schedule_refresh(session_id, delete_item=True)

@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    """
    Slash command to open the Loot modal. Performs a pre-modal check to ensure the
    invoker is in a voice channel (better UX: prevents showing a modal they cannot use).
    """
    ch_type = getattr(interaction.channel, "type", None)
    if ch_type in (nextcord.ChannelType.voice, nextcord.ChannelType.stage_voice):
        await interaction.response.send_message(
            "❌ Please run `/loot` in a regular text channel (not a voice-linked text chat).",
            ephemeral=True
        )
        return

    user_voice = getattr(interaction.user, "voice", None)
    user_voice_chan = getattr(user_voice, "channel", None)
    if not user_voice_chan:
        await interaction.response.send_message(
            "❌ You must be in a voice channel to start a loot roll. Join a voice channel and try again.",
            ephemeral=True
        )
        return

    await interaction.response.send_modal(LootModal())

# ---------- Events and run ----------
@bot.event
async def on_ready():
    """Log a minimal ready message when the bot connects."""
    print(f"RNGenie ready as {bot.user}")

@bot.event
async def on_application_command_error(interaction: nextcord.Interaction, error: Exception):
    """
    Generic application command error handler that sends a simple ephemeral notice.
    Keeps behavior minimal to avoid noisy logging.
    """
    try:
        if not interaction.is_expired():
            if interaction.response.is_done():
                await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)
    except Exception:
        pass

if __name__ == "__main__":
    # Load token from .env and run the bot
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN environment variable required.")
    bot.run(token)
