# RNGenie.py
# A Discord bot for managing turn-based loot distribution in voice channels.
# Optimized with: unified Skip/Undo UX, faster refresh, smarter dropdown handling,
# stricter manager-only protections, per-session locks, and automatic timeout cleanup.

import os
import traceback
import random
import re
import asyncio
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands

# ===================================================================================================
# BOT SETUP & GLOBAL STATE
# ===================================================================================================

intents = nextcord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(intents=intents)

# Sessions keyed by control panel message ID
loot_sessions = {}
# Per-session locks to avoid race conditions
session_locks = {}

# Inactivity timeout: 10 minutes
SESSION_TIMEOUT_SECONDS = 600  # 10 minutes

NUMBER_EMOJIS = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟",
    11: "1️⃣1️⃣", 12: "1️⃣2️⃣", 13: "1️⃣3️⃣", 14: "1️⃣4️⃣", 15: "1️⃣5️⃣",
    16: "1️⃣6️⃣", 17: "1️⃣7️⃣", 18: "1️⃣8️⃣", 19: "1️⃣9️⃣", 20: "2️⃣0️⃣"
}

# ANSI color codes used in code blocks for visual separation
ANSI_RESET = "\u001b[0m"
ANSI_HEADER = "\u001b[0;33m"
ANSI_USER = "\u001b[0;34m"

TURN_NOT_STARTED = -1

# ===================================================================================================
# HELPERS
# ===================================================================================================

def _are_items_left(session):
    return any(not item["assigned_to"] for item in session["items"])

def _advance_turn_snake(session):
    """Advance the snake draft turn; sets just_reversed when reversal happens."""
    session["just_reversed"] = False
    if not _are_items_left(session):
        session["current_turn"] = len(session["rolls"])  # marker for done
        return

    num_rollers = len(session["rolls"])
    if num_rollers == 0:
        return

    if session["current_turn"] == TURN_NOT_STARTED:
        session["current_turn"] = 0
        return

    potential_next_turn = session["current_turn"] + session["direction"]
    if 0 <= potential_next_turn < num_rollers:
        session["current_turn"] = potential_next_turn
    else:
        session["direction"] *= -1
        session["round"] += 1
        session["just_reversed"] = True

def _build_roll_display(rolls):
    """Return text lines for the roll order; if ties exist, include tiebreaker where applicable."""
    roll_counts = {}
    for r in rolls:
        roll_counts.setdefault(r["roll"], 0)
        roll_counts[r["roll"]] += 1
    show_tiebreak = {rv: (count > 1) for rv, count in roll_counts.items()}

    lines = []
    for i, roll_info in enumerate(rolls):
        emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        name = roll_info["member"].display_name
        base = f"{emoji} {ANSI_USER}{name}{ANSI_RESET} ({roll_info['roll']})"
        if show_tiebreak.get(roll_info["roll"], False):
            tb = roll_info.get("tiebreak")
            tb_text = f"/TB:{tb}" if tb is not None else "/TB:—"
            base += f" {tb_text}"
        lines.append(base)
    return "\n".join(lines)

# ===================================================================================================
# UNDO HELPER
# ===================================================================================================

async def _undo_last_action(session, interaction):
    last_action = session.get("last_action")
    if not last_action:
        # Let caller decide how to handle (we still inform user here).
        await interaction.response.send_message("❌ There is nothing to undo.", ephemeral=True)
        return False

    for idx in last_action.get("assigned_indices", []):
        if 0 <= idx < len(session["items"]):
            session["items"][idx]["assigned_to"] = None

    session["current_turn"] = last_action["turn"]
    session["round"] = last_action["round"]
    session["direction"] = last_action["direction"]
    session["just_reversed"] = last_action.get("just_reversed", False)

    session["last_action"] = None
    session["selected_items"] = None
    return True

# ===================================================================================================
# MESSAGE BUILDERS
# ===================================================================================================

def build_loot_list_message(session):
    header = "**(1/2)**\n"
    remaining_items = [item for item in session["items"] if not item["assigned_to"]]
    if remaining_items:
        remaining_header = f"```ansi\n{ANSI_HEADER}❌ Remaining Loot Items ❌{ANSI_RESET}\n==================================\n"
        remaining_body = ""
        for item in session["items"]:
            if not item["assigned_to"]:
                remaining_body += f"{item['display_number']}. {item['name']}\n"
        remaining_footer = "```"
        return f"{header}{remaining_header}{remaining_body}{remaining_footer}"

    return f"{header}```ansi\n{ANSI_HEADER}✅ All Items Assigned ✅{ANSI_RESET}\n==================================\nAll items have been distributed.\n```"

def build_control_panel_message(session):
    """Content for control panel (status + assigned items + turn indicator). Minimal footer/footers removed."""
    invoker = session["invoker"]
    rolls = session["rolls"]

    header = f"**(2/2)**\n\n✍️ **Loot Manager:** {invoker.mention}\n\n"

    # Roll order
    roll_order_section = f"```ansi\n{ANSI_HEADER}🎲 Roll Order 🎲{ANSI_RESET}\n==================================\n"
    roll_order_section += _build_roll_display(rolls)
    roll_order_section += "\n```"

    # Assigned items
    assigned_items_header = f"```ansi\n{ANSI_HEADER}✅ Assigned Items ✅{ANSI_RESET}\n==================================\n"
    assigned_items_map = {r["member"].id: [] for r in rolls}
    for item in session["items"]:
        if item["assigned_to"]:
            assigned_items_map[item["assigned_to"]].append(item["name"])

    assigned_items_body = ""
    for i, roll_info in enumerate(rolls):
        member = roll_info["member"]
        emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        if i > 0:
            assigned_items_body += "\n"
        assigned_items_body += f"{emoji} {ANSI_USER}{member.display_name}{ANSI_RESET}\n"
        if assigned_items_map[member.id]:
            for nm in assigned_items_map[member.id]:
                assigned_items_body += f"- {nm}\n"
        else:
            assigned_items_body += "- N/A\n"

    assigned_items_section = assigned_items_header + assigned_items_body + "```"

    # Turn indicator
    indicator = ""
    if session["current_turn"] >= 0 and session["current_turn"] < len(rolls):
        direction_text = "Normal" if session["direction"] == 1 else "Reverse"
        indicator = f"\n🔔 **Round {session['round'] + 1}** ({direction_text})\n\n"
    else:
        indicator = f"\n🎁 **Loot distribution is ready!**\n\n✍️ **Loot Manager {invoker.mention} can remove participants or click below to begin.**"

    return f"{header}{roll_order_section}\n{assigned_items_section}{indicator}"

def build_final_summary_message(session, timed_out=False):
    rolls = session["rolls"]
    header = "⌛ **The loot session has timed out:**\n\n" if timed_out else "✅ **All Items Have Been Assigned:**\n\n"

    roll_order_section = f"```ansi\n{ANSI_HEADER}🎲 Roll Order 🎲{ANSI_RESET}\n==================================\n"
    roll_order_section += _build_roll_display(rolls)
    roll_order_section += "\n```"

    assigned_items_header = f"```ansi\n{ANSI_HEADER}✅ Assigned Items ✅{ANSI_RESET}\n=================================="
    assigned_items_map = {r["member"].id: [] for r in rolls}
    for item in session["items"]:
        if item["assigned_to"]:
            assigned_items_map[item["assigned_to"]].append(item["name"])

    assigned_items_body = ""
    for i, r in enumerate(rolls):
        emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        assigned_items_body += f"\n{emoji} {ANSI_USER}{r['member'].display_name}{ANSI_RESET}\n"
        if assigned_items_map[r["member"].id]:
            for nm in assigned_items_map[r["member"].id]:
                assigned_items_body += f"- {nm}\n"
        else:
            assigned_items_body += "- N/A\n"

    assigned_items_section = assigned_items_header + assigned_items_body + "```"

    # Unclaimed items
    unclaimed_items = [item for item in session["items"] if not item["assigned_to"]]
    unclaimed_section = ""
    if unclaimed_items:
        unclaimed_section = f"```ansi\n{ANSI_HEADER}❌ Unclaimed Items ❌{ANSI_RESET}\n==================================\n"
        for it in unclaimed_items:
            unclaimed_section += f"{it['display_number']}. {it['name']}\n"
        unclaimed_section += "```"

    return f"{header}{roll_order_section}\n{assigned_items_section}\n{unclaimed_section}"

# ===================================================================================================
# ITEM DROPDOWN VIEW (third message)
# ===================================================================================================

class ItemDropdownView(nextcord.ui.View):
    """View attached to the 3rd message that contains item-selects + assign/skip/undo actions.
       This view is recreated when the turn advances, but selection updates edit the existing message in-place."""
    def __init__(self, session_id):
        # no view timeout (session-level timeout is handled separately)
        super().__init__(timeout=None)
        self.session_id = session_id
        # populate so any view attached immediately has the components
        self.populate()

    def populate(self):
        # Note: populate rebuilds self.children fresh
        self.clear_items()
        session = loot_sessions.get(self.session_id)
        if not session:
            return

        # If no items left, nothing to add
        if not _are_items_left(session):
            return

        # Only add selects when it's a picker's active turn
        if 0 <= session["current_turn"] < len(session["rolls"]):
            available_items = [(idx, it) for idx, it in enumerate(session["items"]) if not it["assigned_to"]]
            if not available_items:
                return

            item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]
            selected_values = set(session.get("selected_items") or [])
            for i, chunk in enumerate(item_chunks):
                options = []
                for orig_index, item_dict in chunk:
                    label_text = f"{item_dict['display_number']}. {item_dict['name']}"
                    truncated_label = (label_text[:97] + '...') if len(label_text) > 100 else label_text
                    is_selected = str(orig_index) in selected_values
                    options.append(nextcord.SelectOption(label=truncated_label, value=str(orig_index), default=is_selected))
                placeholder = "Choose one or more items to claim..."
                if len(item_chunks) > 1:
                    start_num, end_num = chunk[0][1]['display_number'], chunk[-1][1]['display_number']
                    placeholder = f"Choose items ({start_num}-{end_num})..."
                # Note: max_values=len(options) allows multi-select up to chunk size
                self.add_item(nextcord.ui.Select(placeholder=placeholder, options=options, custom_id=f"item_select_{i}", min_values=0, max_values=len(options)))

            assign_disabled = not session.get("selected_items")
            # use ButtonStyle.success (green equivalent)
            self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.success, emoji="✅", custom_id="assign_button", disabled=assign_disabled))

        # Skip Turn is shown regardless
        self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button"))

        # Undo is present next to Skip Turn (only one Undo, here)
        undo_disabled = not session.get("last_action")
        self.add_item(nextcord.ui.Button(label="Undo", style=nextcord.ButtonStyle.secondary, emoji="↩️", custom_id="undo_button", disabled=undo_disabled))

        # assign callbacks (dynamic assignment keeps the code centralized)
        for child in self.children:
            if hasattr(child, "custom_id"):
                if child.custom_id == "assign_button":
                    child.callback = self.on_assign
                if child.custom_id == "skip_button":
                    child.callback = self.on_skip
                if child.custom_id == "undo_button":
                    child.callback = self.on_undo
                if hasattr(child, "options") and "item_select" in child.custom_id:
                    child.callback = self.on_item_select

    async def on_item_select(self, interaction: nextcord.Interaction):
        """
        Called when a user interacts with any of the Select components.
        Behavior:
         - validate the dropdown id / values
         - update session['selected_items'] under the session lock
         - acknowledge the component with defer_update() (we no longer attempt to force-collapse the client's dropdown)
         - refresh control/loot list messages (non-interaction path)
         - reset inactivity timeout
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return

        dropdown_id = interaction.data.get("custom_id")
        if dropdown_id is None:
            await interaction.response.send_message("Invalid interaction.", ephemeral=True)
            return
        try:
            dropdown_index = int(dropdown_id.split("_")[-1])
        except Exception:
            await interaction.response.send_message("Invalid selection (malformed dropdown id).", ephemeral=True)
            return

        available_items = [(index, item) for index, item in enumerate(session["items"]) if not item["assigned_to"]]
        item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]

        if dropdown_index >= len(item_chunks):
            await interaction.response.send_message("Invalid selection (stale dropdown).", ephemeral=True)
            return

        possible_values = {str(index) for index, _ in item_chunks[dropdown_index]}
        newly_selected = set(interaction.data.get("values", []))

        # Update session selected_items under the session lock to avoid races
        lock = session_locks.setdefault(self.session_id, asyncio.Lock())
        async with lock:
            current_master = set(session.get("selected_items") or [])
            # replace values belonging to only this dropdown chunk
            current_master -= possible_values
            current_master |= newly_selected
            session["selected_items"] = list(current_master)

        # Acknowledge the interaction without trying to collapse the client's dropdown.
        # This prevents "This interaction failed" client messages and lets us update the bot UI separately.
        try:
            await interaction.response.defer_update()
        except Exception:
            # ignore ack failures; we will continue to refresh messages
            pass

        # Refresh session timeout (activity)
        await _reset_session_timeout(session_id=self.session_id)

        # Now refresh other messages (control panel / loot list).
        await _refresh_all_messages(self.session_id, interaction=None, delete_item=False)

    async def on_assign(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return

        # permission: only the current picker or invoker can trigger assign (invoker allowed earlier)
        if session["current_turn"] < 0 or session["current_turn"] >= len(session["rolls"]):
            await interaction.response.send_message("It's not an active picking turn.", ephemeral=True)
            return

        selected_indices = session.get("selected_items") or []
        current_picker_id = session["rolls"][session["current_turn"]]["member"].id

        # record last action for undo
        session["last_action"] = {
            "turn": session["current_turn"],
            "round": session["round"],
            "direction": session["direction"],
            "just_reversed": session.get("just_reversed", False),
            "assigned_indices": [int(i) for i in selected_indices] if selected_indices else []
        }

        if selected_indices:
            for idx_str in selected_indices:
                idx = int(idx_str)
                if 0 <= idx < len(session["items"]):
                    session["items"][idx]["assigned_to"] = current_picker_id

        session["selected_items"] = None
        _advance_turn_snake(session)

        # refresh session timeout (activity)
        await _reset_session_timeout(session_id=self.session_id)

        # assignment is a turn-advance: allow delete+recreate
        await _refresh_all_messages(self.session_id, interaction, delete_item=True)

    async def on_skip(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return

        # record last action for undo if currently in a pick
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

        # refresh session timeout (activity)
        await _reset_session_timeout(session_id=self.session_id)

        # skipping advances turn: delete+recreate the item message
        await _refresh_all_messages(self.session_id, interaction, delete_item=True)

    async def on_undo(self, interaction: nextcord.Interaction):
        """Undo button placed next to Skip Turn. Only Loot Manager (invoker) allowed."""
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return

        if interaction.user.id != session["invoker_id"]:
            await interaction.response.send_message("🛡️ Only the Loot Manager can use Undo.", ephemeral=True)
            return

        if not await _undo_last_action(session, interaction):
            return

        # refresh session timeout (activity)
        await _reset_session_timeout(session_id=self.session_id)

        # Undo changes the session state: delete+recreate the item message to reflect restored turn.
        await _refresh_all_messages(self.session_id, interaction, delete_item=True)

# ===================================================================================================
# CONTROL PANEL VIEW (status and manager controls)
# ===================================================================================================

class ControlPanelView(nextcord.ui.View):
    """View for the control panel (message 2/2). Contains participant remove select + manager actions.
       Note: Undo button has been removed from this control panel; it exists only next to Skip Turn."""
    def __init__(self, session_id):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.populate()

    def populate(self):
        self.clear_items()
        session = loot_sessions.get(self.session_id)
        if not session:
            return

        # Pre-start: manage participants
        if session["current_turn"] == TURN_NOT_STARTED:
            selected_values = session.get("members_to_remove") or []
            member_options = []
            invoker_id = session["invoker_id"]
            for r in session["rolls"]:
                if r["member"].id != invoker_id:
                    is_selected = str(r['member'].id) in selected_values
                    member_options.append(nextcord.SelectOption(label=r['member'].display_name, value=str(r['member'].id), default=is_selected))
            if member_options:
                self.add_item(nextcord.ui.Select(placeholder="Select participants to remove...", options=member_options, custom_id="remove_select", min_values=0, max_values=len(member_options)))
            remove_disabled = not session.get("members_to_remove")
            self.add_item(nextcord.ui.Button(label="Remove Selected", style=nextcord.ButtonStyle.danger, emoji="✖️", custom_id="remove_confirm_button", disabled=remove_disabled))
            self.add_item(nextcord.ui.Button(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="start_button"))
        else:
            # Post-start: no Undo here (Undo is next to Skip Turn in the item dropdown message)
            # keep a terse control hint in the panel
            pass

        # attach callbacks
        for child in self.children:
            if hasattr(child, "custom_id"):
                if child.custom_id == "remove_select":
                    child.callback = self.on_remove_select
                if child.custom_id == "remove_confirm_button":
                    child.callback = self.on_remove_confirm
                if child.custom_id == "start_button":
                    child.callback = self.on_start

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False

        # Invoker always allowed to use control-panel actions
        if interaction.user.id == session["invoker_id"]:
            return True

        # During picking, the current picker may use the dropdown controls (which are on the third message),
        # but for control-panel interactions only allow invoker
        await interaction.response.send_message(f"🛡️ Only {session['invoker'].mention} can use control-panel buttons.", ephemeral=True)
        return False

    async def on_remove_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return
        session["members_to_remove"] = interaction.data.get("values")
        self.populate()
        await interaction.response.edit_message(view=self)

    async def on_remove_confirm(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return

        # Defensive handling: session["members_to_remove"] may be None
        vals = session.get("members_to_remove") or []
        ids_to_remove = set()
        for x in vals:
            try:
                ids_to_remove.add(int(x))
            except Exception:
                # ignore malformed entries
                continue

        if ids_to_remove:
            # remove participants
            session["rolls"] = [r for r in session["rolls"] if r["member"].id not in ids_to_remove]
            session["members_to_remove"] = None

            # If no rollers remain, tidy up and remove session
            if not session["rolls"]:
                # delete associated messages (best-effort) and cancel timeout task
                ch = bot.get_channel(session["channel_id"])
                if ch:
                    try:
                        if session.get("loot_list_message_id"):
                            await ch.get_partial_message(session["loot_list_message_id"]).delete()
                    except Exception:
                        pass
                    try:
                        if session.get("item_dropdown_message_id"):
                            await ch.get_partial_message(session["item_dropdown_message_id"]).delete()
                    except Exception:
                        pass
                    try:
                        await ch.get_partial_message(interaction.message.id).edit(content="⚠️ The loot session was cancelled — no participants remain.", view=None)
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
                return

            # Adjust current_turn if out-of-range after removal
            if session["current_turn"] != TURN_NOT_STARTED:
                if session["current_turn"] >= len(session["rolls"]):
                    # clamp to last index
                    session["current_turn"] = max(0, len(session["rolls"]) - 1)

        # refresh session timeout (activity)
        await _reset_session_timeout(session_id=self.session_id)

        await _refresh_all_messages(self.session_id, interaction)

    async def on_start(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return
        # starting sets current_turn to first picker
        session["members_to_remove"] = None
        session["selected_items"] = None
        session["last_action"] = None
        _advance_turn_snake(session)

        # refresh session timeout (activity)
        await _reset_session_timeout(session_id=self.session_id)

        await _refresh_all_messages(self.session_id, interaction)

# ===================================================================================================
# MESSAGE REFRESH / LIFECYCLE
# ====================================================================================================

async def _reset_session_timeout(session_id: int):
    """
    Cancel existing timeout task for session and start a fresh one.
    Called on user activity so timeout is inactivity-based.
    """
    session = loot_sessions.get(session_id)
    if not session:
        return
    old_task = session.get("timeout_task")
    if old_task:
        try:
            old_task.cancel()
        except Exception:
            pass
    # create new one
    task = asyncio.create_task(_schedule_session_timeout(session_id))
    session["timeout_task"] = task

async def _refresh_all_messages(session_id, interaction=None, delete_item=True):
    """
    Centralized message update: control panel, loot list, and item dropdown.
    Optimizations:
      - Skip edits if content hasn't changed (reduces API calls).
      - Use partial message edits where possible.
      - Reset inactivity timeout on activity.
    """
    session = loot_sessions.get(session_id)
    if not session:
        if interaction and not interaction.is_expired():
            await interaction.response.send_message("❌ Session missing or expired.", ephemeral=True)
        return

    lock = session_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        channel = bot.get_channel(session["channel_id"])
        if not channel:
            # cleanup
            loot_sessions.pop(session_id, None)
            t = session.get("timeout_task")
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
            session_locks.pop(session_id, None)
            return

        control_panel_msg = channel.get_partial_message(session_id)
        loot_list_msg = None
        loot_list_id = session.get("loot_list_message_id")
        if loot_list_id:
            loot_list_msg = channel.get_partial_message(loot_list_id)

        old_item_msg_id = session.get("item_dropdown_message_id")
        if delete_item and old_item_msg_id:
            try:
                await channel.get_partial_message(old_item_msg_id).delete()
            except (nextcord.NotFound, nextcord.Forbidden):
                pass
            session["item_dropdown_message_id"] = None

        if not _are_items_left(session) and session["current_turn"] != TURN_NOT_STARTED:
            final_content = build_final_summary_message(session, timed_out=False)
            try:
                await control_panel_msg.edit(content=final_content, view=None)
            except (nextcord.NotFound, nextcord.Forbidden):
                pass
            if loot_list_msg:
                try:
                    await loot_list_msg.delete()
                except (nextcord.NotFound, nextcord.Forbidden):
                    pass
            # cleanup
            t = session.get("timeout_task")
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
            loot_sessions.pop(session_id, None)
            session_locks.pop(session_id, None)
            return

        # Build contents
        loot_list_content = build_loot_list_message(session)
        control_panel_content = build_control_panel_message(session)

        # Edit only if content changed (reduces API calls)
        last_control = session.get("last_control_content")
        last_loot = session.get("last_loot_content")

        async def update_control():
            nonlocal last_control
            if control_panel_content != last_control:
                try:
                    await control_panel_msg.edit(content=control_panel_content, view=ControlPanelView(session_id))
                    session["last_control_content"] = control_panel_content
                except (nextcord.NotFound, nextcord.Forbidden):
                    pass

        async def update_loot():
            nonlocal last_loot
            if loot_list_msg and loot_list_content != last_loot:
                try:
                    await loot_list_msg.edit(content=loot_list_content)
                    session["last_loot_content"] = loot_list_content
                except (nextcord.NotFound, nextcord.Forbidden):
                    pass

        # Run edits concurrently
        await asyncio.gather(update_control(), update_loot())

        # refresh session timeout (activity)
        await _reset_session_timeout(session_id=session_id)

        # ---- Only create item-dropdown if active
        is_active_pick = (0 <= session["current_turn"] < len(session["rolls"])) and _are_items_left(session)
        if not is_active_pick:
            if delete_item:
                session["item_dropdown_message_id"] = None
            return

        picker = session["rolls"][session["current_turn"]]["member"]
        picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
        turn_text = "turn!" if not session.get("just_reversed", False) else "turn (direction reversed)!"

        item_message_content = (
            f"**{picker_emoji} {picker.mention}'s {turn_text}**\n\n"
            "Choose items below..."
        )
        # Create the view only when we are going to attach/send it
        item_view = ItemDropdownView(session_id)

        existing_id = session.get("item_dropdown_message_id")
        if not delete_item and existing_id:
            try:
                existing_msg = channel.get_partial_message(existing_id)
                await existing_msg.edit(content=item_message_content, view=item_view)
                return
            except (nextcord.NotFound, nextcord.Forbidden):
                session["item_dropdown_message_id"] = None

        # create new dropdown message (fast path)
        item_msg = await channel.send(item_message_content, view=item_view)
        session["item_dropdown_message_id"] = item_msg.id

# ===================================================================================================
# TIMEOUT CLEANUP TASK
# ===================================================================================================

async def _schedule_session_timeout(session_id: int):
    # Sleep then expire the session (one-shot). This will automatically produce a final summary in the control message.
    try:
        await asyncio.sleep(SESSION_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return

    # At timeout, remove session and post final summary
    session = loot_sessions.pop(session_id, None)
    # remove lock and cancel any remaining task
    session_locks.pop(session_id, None)
    if not session:
        return

    channel = bot.get_channel(session["channel_id"])
    if not channel:
        return

    # Clean up loot list (1/2)
    loot_list_id = session.get("loot_list_message_id")
    if loot_list_id:
        try:
            await channel.get_partial_message(loot_list_id).delete()
        except (nextcord.NotFound, nextcord.Forbidden):
            pass

    # Clean up item dropdown (3/3)
    item_msg_id = session.get("item_dropdown_message_id")
    if item_msg_id:
        try:
            await channel.get_partial_message(item_msg_id).delete()
        except (nextcord.NotFound, nextcord.Forbidden):
            pass

    # Edit control panel (2/2) into the final summary
    try:
        control_msg = await channel.fetch_message(session_id)
    except (nextcord.NotFound, nextcord.Forbidden):
        return

    final_content = build_final_summary_message(session, timed_out=True)
    try:
        await control_msg.edit(content=final_content, view=None)
    except (nextcord.NotFound, nextcord.Forbidden):
        pass

# ===================================================================================================
# MODAL & SLASH COMMAND
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
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
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You must be in a voice channel to set up a loot roll.", ephemeral=True)
            return

        members_in_channel = interaction.user.voice.channel.members
        if len(members_in_channel) > 20:
            await interaction.followup.send(f"❌ Too many users in the voice channel ({len(members_in_channel)})! The maximum is 20.", ephemeral=True)
            return
        if not members_in_channel:
            await interaction.followup.send("❌ I could not find anyone in your voice channel.", ephemeral=True)
            return

        # Primary roll
        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members_in_channel]

        # Detect duplicates and assign tiebreakers only for duplicated primary rolls
        roll_to_members = {}
        for r in rolls:
            roll_to_members.setdefault(r["roll"], []).append(r)
        for roll_val, group in roll_to_members.items():
            if len(group) > 1:
                # assign a tiebreak number to each member in the tie
                for r in group:
                    r["tiebreak"] = random.randint(1, 100)

        # Sort by primary roll desc, then tiebreak desc (None treated as -1)
        def _sort_key(r):
            tb = r.get("tiebreak")
            tb_sort = tb if tb is not None else -1
            return (r["roll"], tb_sort)
        rolls.sort(key=_sort_key, reverse=True)

        # Parse items (handle Nx syntax)
        item_names = []
        raw_lines = self.loot_items.value.splitlines()
        for line in raw_lines:
            stripped_line = line.strip()
            if not stripped_line:
                continue
            match = re.match(r"(\d+)[xX]\s*(.*)", stripped_line)
            if match:
                try:
                    count = int(match.group(1))
                    name = match.group(2).strip()
                    if name:
                        item_names.extend([name] * count)
                except Exception:
                    item_names.append(stripped_line)
            else:
                item_names.append(stripped_line)

        items_data = [{"name": nm, "assigned_to": None, "display_number": i} for i, nm in enumerate(item_names, 1)]
        if not items_data:
            await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)
            return

        # Send placeholder messages to get IDs
        loot_list_message = await interaction.followup.send("`Initializing Loot List (1/2)...`", wait=True)
        control_panel_message = await interaction.channel.send("`Initializing Control Panel (2/2)...`")

        session_id = control_panel_message.id
        session = {
            "rolls": rolls,
            "items": items_data,
            "current_turn": TURN_NOT_STARTED,
            "invoker_id": interaction.user.id,
            "invoker": interaction.user,
            "selected_items": None,
            "round": 0,
            "direction": 1,
            "just_reversed": False,
            "members_to_remove": None,
            "channel_id": control_panel_message.channel.id,
            "loot_list_message_id": loot_list_message.id,
            "item_dropdown_message_id": None,
            "last_action": None,
            "last_control_content": None,
            "last_loot_content": None,
            "timeout_task": None
        }
        loot_sessions[session_id] = session

        # schedule session timeout cleanup (one-shot)
        await _reset_session_timeout(session_id)

        # Build initial messages and views
        loot_list_content = build_loot_list_message(session)
        control_panel_content = build_control_panel_message(session)
        await loot_list_message.edit(content=loot_list_content)
        await control_panel_message.edit(content=control_panel_content, view=ControlPanelView(session_id))
        session["last_control_content"] = control_panel_content
        session["last_loot_content"] = loot_list_content

        # Create initial item dropdown message (will be recreated on updates)
        await _refresh_all_messages(session_id, interaction)

@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You need to be in a voice channel to start a loot roll!", ephemeral=True)
        return
    await interaction.response.send_modal(LootModal())

# ===================================================================================================
# BOT EVENTS
# ===================================================================================================

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    print('RNGenie is ready.')
    print('------')

@bot.event
async def on_application_command_error(interaction: nextcord.Interaction, error: Exception):
    print(f"\n--- Unhandled exception in interaction ---")
    traceback.print_exception(type(error), error, error.__traceback__)
    print("--- End of exception report ---\n")
    if not interaction.is_expired():
        try:
            message = "❌ An unexpected error occurred. See console for details."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except nextcord.HTTPException:
            pass

# ===================================================================================================
# RUN SCRIPT
# ===================================================================================================

if __name__ == "__main__":
    load_dotenv()
    bot.run(os.getenv("DISCORD_TOKEN"))
