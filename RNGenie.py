# RNGenie.py - Discord loot distribution bot

import os
import random
import re
import asyncio
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
    Advance the current_turn index using snake draft logic.
    If the end is reached, reverse direction and increment the round.
    If no items remain, mark the session as complete by setting current_turn
    beyond the last index (uses len(session['rolls'])).
    """
    session["just_reversed"] = False
    if not _are_items_left(session):
        session["current_turn"] = len(session["rolls"])
        return

    num = len(session["rolls"])
    if num == 0:
        return
    if session["current_turn"] == TURN_NOT_STARTED:
        session["current_turn"] = 0
        return

    next_turn = session["current_turn"] + session["direction"]
    if 0 <= next_turn < num:
        session["current_turn"] = next_turn
    else:
        # reverse direction and step once in the new direction
        session["direction"] *= -1
        session["round"] += 1
        session["just_reversed"] = True
        # if there's only one roller, ensure index stays valid
        if num == 1:
            session["current_turn"] = 0

def _build_roll_lines(rolls: list) -> str:
    """
    Build the text block that shows roll order and any tie-break values.
    Returns a newline-separated string suitable for insertion into an ANSI code block.
    """
    roll_counts = {}
    for r in rolls:
        roll_counts.setdefault(r["roll"], 0)
        roll_counts[r["roll"]] += 1
    parts = []
    for idx, r in enumerate(rolls):
        emoji = NUMBER_EMOJIS.get(idx + 1, f"#{idx+1}")
        name = r["member"].display_name
        base = f"{emoji} {BLUE}{name}{RESET} ({r['roll']})"
        if roll_counts.get(r["roll"], 0) > 1:
            tb = r.get("tiebreak")
            base += f" /TB:{tb if tb is not None else '—'}"
        parts.append(base)
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

# ---------- Message builders (use ANSI for colored output) ----------
def build_loot_list_message(session: dict) -> str:
    """
    Build the left-hand 'loot list' message (1/2).
    Shows remaining items or a completion block.
    """
    header = f"**(1/2)**\n"
    remaining = [it for it in session["items"] if it["assigned_to"] is None]
    if remaining:
        body = (
            "```ansi\n"
            f"{RED}{BOLD}❌ Remaining Loot Items ❌{RESET}\n"
            "==================================\n"
        )
        for it in remaining:
            body += f"{RED}{it['display_number']}.{RESET} {it['name']}\n"
        body += "```"
        return f"{header}{body}"
    return (
        f"{header}"
        "```ansi\n"
        f"{GREEN}{BOLD}✅ All Items Assigned ✅{RESET}\n"
        "==================================\n"
        "All items have been distributed.\n"
        "```"
    )

def build_control_panel_message(session: dict) -> str:
    """
    Build the control panel message (2/2) containing roll order + assigned items
    and a short indicator about current round/direction or readiness.
    """
    header = f"**(2/2)**\n\n✍️ **Loot Manager:** {session['invoker'].mention}\n\n"
    roll_block = (
        "```ansi\n"
        f"{YELLOW}{BOLD}🎲 Roll Order 🎲{RESET}\n"
        "==================================\n"
        f"{_build_roll_lines(session['rolls'])}\n"
        "```"
    )

    # Map member id -> list of assigned item names for display
    assigned_map = {r["member"].id: [] for r in session["rolls"]}
    for it in session["items"]:
        if it["assigned_to"]:
            assigned_map.setdefault(it["assigned_to"], []).append(it["name"])
    assigned_block = (
        "```ansi\n"
        f"{GREEN}{BOLD}✅ Assigned Items ✅{RESET}\n"
        "==================================\n"
    )
    # Show each roller and their assigned items. Add a blank line after each person
    for i, r in enumerate(session["rolls"]):
        emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        assigned_block += f"{BLUE}{emoji} {r['member'].display_name}{RESET}\n"
        items = assigned_map.get(r["member"].id, [])
        if items:
            for nm in items:
                assigned_block += f"- {nm}\n"
        else:
            assigned_block += "- N/A\n"
        assigned_block += "\n"
    assigned_block += "```"

    indicator = ""
    if 0 <= session["current_turn"] < len(session["rolls"]):
        direction = "Normal" if session["direction"] == 1 else "Reverse"
        indicator = f"\n🔔 **Round {session['round'] + 1}** ({direction})\n\n"
    else:
        indicator = f"\n🎁 **Loot distribution is ready!**\n\n✍️ **Loot Manager can remove participants or click below to begin.**"
    return f"{header}{roll_block}\n{assigned_block}{indicator}"

def build_final_summary_message(session: dict, timed_out: bool=False) -> str:
    """
    Build final summary that is shown either when timed out or all items assigned.
    Includes roll order, final assigned lists, and any unclaimed items.
    """
    header = (f"⌛ {RED}{BOLD}The loot session has timed out:{RESET}\n\n" if timed_out 
              else f"{GREEN}{BOLD}✅ All Items Have Been Assigned:{RESET}\n\n")
    roll_block = (
        "```ansi\n"
        f"{YELLOW}{BOLD}🎲 Roll Order 🎲{RESET}\n"
        "==================================\n"
        f"{_build_roll_lines(session['rolls'])}\n"
        "```"
    )

    assigned_map = {r["member"].id: [] for r in session["rolls"]}
    for it in session["items"]:
        if it["assigned_to"]:
            assigned_map.setdefault(it["assigned_to"], []).append(it["name"])
    assigned_block = (
        "```ansi\n"
        f"{GREEN}{BOLD}✅ Assigned Items ✅{RESET}\n"
        "==================================\n"
    )
    # same formatting as control panel; blank line after each person's items for readability
    for i, r in enumerate(session["rolls"]):
        emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        assigned_block += f"{BLUE}{emoji} {r['member'].display_name}{RESET}\n"
        items = assigned_map.get(r["member"].id, [])
        if items:
            for nm in items:
                assigned_block += f"- {nm}\n"
        else:
            assigned_block += "- N/A\n"
        assigned_block += "\n"
    assigned_block += "```"

    unclaimed = [it for it in session["items"] if it["assigned_to"] is None]
    unclaimed_block = ""
    if unclaimed:
        unclaimed_block = (
            "```ansi\n"
            f"{RED}{BOLD}❌ Unclaimed Items ❌{RESET}\n"
            "==================================\n"
        )
        for it in unclaimed:
            unclaimed_block += f"{RED}{it['display_number']}.{RESET} {it['name']}\n"
        unclaimed_block += "```"
    return f"{header}{roll_block}\n{assigned_block}\n{unclaimed_block}"

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
    return (f"**{emoji} {picker.mention}'s {turn_text}**\n\nChoose items below...", True)

# ---------- UI Views: Item dropdown view and Control panel view ----------
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
        Uses session["selected_items"] to mark previously selected options as default.
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

        # split into 25-option chunks to respect Discord limit
        chunks = [available[i:i+25] for i in range(0, len(available), 25)]
        selected = set(session.get("selected_items") or [])
        for ci, chunk in enumerate(chunks):
            opts = []
            for idx, item in chunk:
                label = f"{item['display_number']}. {item['name']}"
                truncated = (label[:97] + "...") if len(label) > 100 else label
                default = str(idx) in selected
                opts.append(nextcord.SelectOption(label=truncated, value=str(idx), default=default))
            placeholder = "Choose items..." if len(chunks) == 1 else f"Choose items ({chunk[0][1]['display_number']}-{chunk[-1][1]['display_number']})..."
            self.add_item(nextcord.ui.Select(placeholder=placeholder, options=opts, custom_id=f"item_select_{ci}", min_values=0, max_values=len(opts)))

        # Buttons: Assign Selected (enabled only if something selected), Skip Turn, Undo (if available)
        assign_disabled = not session.get("selected_items")
        self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.success, emoji="✅", custom_id="assign_button", disabled=assign_disabled))
        self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button"))
        undo_disabled = not session.get("last_action")
        self.add_item(nextcord.ui.Button(label="Undo", style=nextcord.ButtonStyle.secondary, emoji="↩️", custom_id="undo_button", disabled=undo_disabled))

        # wire callbacks for each element
        for child in self.children:
            if getattr(child, "custom_id", None) == "assign_button":
                child.callback = self.on_assign
            if getattr(child, "custom_id", None) == "skip_button":
                child.callback = self.on_skip
            if getattr(child, "custom_id", None) == "undo_button":
                child.callback = self.on_undo
            if getattr(child, "custom_id", "").startswith("item_select_"):
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
                await interaction.response.defer_update()
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
            await interaction.response.defer_update()
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
            # remove any selections from this chunk (they will be replaced by new)
            current -= possible
            current |= newly
            session["selected_items"] = list(current)

        await self._ack(interaction)
        await _reset_session_timeout(self.session_id)
        # refresh messages without forcing item deletion (preserve dropdown when possible)
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=False))

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

        session["selected_items"] = None
        _advance_turn_snake(session)
        await _reset_session_timeout(self.session_id)

        new_text, active = _item_message_text_and_active(session)
        new_view = ItemDropdownView(self.session_id) if active else None

        edited = await self._fast_edit(interaction, new_text, new_view)
        # after assignment, force delete+recreate of the item message to ensure a fresh state
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=True))

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
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=True))

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

        session["current_turn"] = last["turn"]
        session["round"] = last["round"]
        session["direction"] = last["direction"]
        session["just_reversed"] = last.get("just_reversed", False)
        session["last_action"] = None
        session["selected_items"] = None

        await _reset_session_timeout(self.session_id)
        new_text, active = _item_message_text_and_active(session)
        new_view = ItemDropdownView(self.session_id) if active else None
        await self._fast_edit(interaction, new_text, new_view)
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=True))

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
                    lm = await _get_msg(ch, session.get("loot_list_message_id"))
                    if lm:
                        await lm.delete()
                except Exception:
                    pass
                try:
                    it = await _get_msg(ch, session.get("item_dropdown_message_id"))
                    if it:
                        await it.delete()
                except Exception:
                    pass
                try:
                    ctrl = await _get_msg(ch, self.session_id)
                    if ctrl:
                        await ctrl.edit(content="⚠️ The loot session was cancelled — no participants remain.", view=None)
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
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=True))

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
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=True))

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

async def _refresh_all_messages(session_id: int, delete_item: bool = True):
    """
    Synchronize the three messages for the session:
      - loot list (left)
      - control panel (right)
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

        control_msg = await _get_msg(ch, session_id)
        loot_msg = await _get_msg(ch, session.get("loot_list_message_id"))
        existing_item_msg = None
        existing_item_id = session.get("item_dropdown_message_id")
        if existing_item_id:
            existing_item_msg = await _get_msg(ch, existing_item_id)

        # Optionally delete the item message to force a clean recreate.
        if delete_item and existing_item_msg:
            try:
                await existing_item_msg.delete()
            except Exception:
                pass
            session["item_dropdown_message_id"] = None
            existing_item_msg = None
            existing_item_id = None

        # If distribution complete, show final summary and cleanup session.
        if not _are_items_left(session) and session["current_turn"] != TURN_NOT_STARTED:
            final = build_final_summary_message(session, timed_out=False)
            try:
                if control_msg:
                    await control_msg.edit(content=final, view=None)
                else:
                    fallback = await _get_msg(ch, session_id)
                    if fallback:
                        await fallback.edit(content=final, view=None)
            except Exception:
                pass
            if loot_msg:
                try:
                    await loot_msg.delete()
                except Exception:
                    pass
            try:
                existing = session.get("item_dropdown_message_id")
                if existing:
                    maybe = await _get_msg(ch, existing)
                    if maybe:
                        await maybe.delete()
            except Exception:
                pass
            t = session.get("timeout_task")
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
            loot_sessions.pop(session_id, None)
            session_locks.pop(session_id, None)
            return

        # Build current contents and only edit messages if changed to reduce API calls.
        loot_content = build_loot_list_message(session)
        control_content = build_control_panel_message(session)

        if loot_content != session.get("last_loot_content") and loot_msg:
            try:
                await loot_msg.edit(content=loot_content)
                session["last_loot_content"] = loot_content
            except Exception:
                pass

        if control_content != session.get("last_control_content") and control_msg:
            try:
                await control_msg.edit(content=control_content, view=ControlPanelView(session_id))
                session["last_control_content"] = control_content
            except Exception:
                pass

        await _reset_session_timeout(session_id)

        # Manage item-picking message: create if active, delete/skip if not
        is_active = (0 <= session["current_turn"] < len(session["rolls"])) and _are_items_left(session)
        if not is_active:
            if not delete_item and existing_item_msg:
                try:
                    await existing_item_msg.delete()
                except Exception:
                    pass
                session["item_dropdown_message_id"] = None
            return

        picker = session["rolls"][session["current_turn"]]["member"]
        emoji = NUMBER_EMOJIS.get(session["current_turn"] + 1, "👉")
        turn_text = "turn!" if not session.get("just_reversed", False) else "turn (direction reversed)!"
        item_text = f"**{emoji} {picker.mention}'s {turn_text}**\n\nChoose items below..."

        view = ItemDropdownView(session_id)

        # Either edit the existing item message (if allowed) or send a fresh one.
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
        except Exception:
            session["item_dropdown_message_id"] = None

async def _schedule_session_timeout(session_id: int):
    """
    Sleep for SESSION_TIMEOUT_SECONDS and then expire/cleanup the session.
    This removes the temporary messages and edits the control message with a timed-out summary.
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
    try:
        lm = await _get_msg(ch, session.get("loot_list_message_id"))
        if lm:
            await lm.delete()
    except Exception:
        pass
    try:
        im = await _get_msg(ch, session.get("item_dropdown_message_id"))
        if im:
            await im.delete()
    except Exception:
        pass
    final = build_final_summary_message(session, timed_out=True)
    try:
        ctrl = await _get_msg(ch, session_id)
        if ctrl:
            await ctrl.edit(content=final, view=None)
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
        It also creates the three messages (loot list, control panel, and item dropdown).
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

        # Roll generation with random tie-breakers for equal primary rolls
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

        # Parse the modal input for items; support Nx syntax
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

        # send placeholders and then initialize session state
        loot_msg = await interaction.followup.send("`Initializing Loot List (1/2)...`", wait=True)
        control_msg = await interaction.channel.send("`Initializing Control Panel (2/2)...`")

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
            "members_to_remove": None,  # stored as list[str] matching SelectOption.value
            "channel_id": control_msg.channel.id,
            "loot_list_message_id": loot_msg.id,
            "item_dropdown_message_id": None,
            "last_action": None,
            "last_control_content": None,
            "last_loot_content": None,
            "timeout_task": None
        }
        loot_sessions[session_id] = session
        await _reset_session_timeout(session_id)

        await loot_msg.edit(content=build_loot_list_message(session))
        await control_msg.edit(content=build_control_panel_message(session), view=ControlPanelView(session_id))
        session["last_control_content"] = build_control_panel_message(session)
        session["last_loot_content"] = build_loot_list_message(session)

        # create item dropdown via refresh task (delete/create behavior handled there)
        asyncio.create_task(_refresh_all_messages(session_id, delete_item=True))

@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    """
    Slash command to open the Loot modal. Performs a pre-modal check to ensure the
    invoker is in a voice channel (better UX: prevents showing a modal they cannot use).
    """
    # disallow invoking the command in voice-linked text chats
    ch_type = getattr(interaction.channel, "type", None)
    if ch_type in (nextcord.ChannelType.voice, nextcord.ChannelType.stage_voice):
        await interaction.response.send_message(
            "❌ Please run `/loot` in a regular text channel (not a voice-linked text chat).",
            ephemeral=True
        )
        return

    # require the invoking user to be connected to a voice channel before showing modal
    user_voice = getattr(interaction.user, "voice", None)
    user_voice_chan = getattr(user_voice, "channel", None)
    if not user_voice_chan:
        await interaction.response.send_message(
            "❌ You must be in a voice channel to start a loot roll. Join a voice channel and try again.",
            ephemeral=True
        )
        return

    # show the modal (modal callback does additional defensive checks)
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
