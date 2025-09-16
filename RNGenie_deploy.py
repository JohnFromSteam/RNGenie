# RNGenie.py
# A Discord bot for managing turn-based loot distribution in voice channels.
# Optimized: snappy item interactions, robust handling for voice-channel text chats,
# and fixed delete+recreate behavior for the 3rd (item-dropdown) message.

import os
import traceback
import random
import re
import asyncio
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands
from nextcord.errors import NotFound, Forbidden, HTTPException

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
SESSION_TIMEOUT_SECONDS = 600

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

async def _get_partial_message(channel, message_id):
    """
    Optimized helper to get a partial message object without a full API fetch.
    This is faster, especially in voice channel text chats.
    Returns None if message_id is invalid or channel is inaccessible.
    """
    if not channel or not message_id:
        return None
    try:
        return channel.get_partial_message(message_id)
    except (AttributeError, NotFound, Forbidden):
        # Fallback or failure cases are handled by the caller.
        return None

# ===================================================================================================
# UNDO HELPER
# ===================================================================================================

async def _undo_last_action(session, interaction):
    last_action = session.get("last_action")
    if not last_action:
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
    """Content for control panel (status + assigned items + turn indicator)."""
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

    assigned_items_header = f"```ansi\n{ANSI_HEADER}✅ Assigned Items ✅{ANSI_RESET}\n==================================\n"
    assigned_items_map = {r["member"].id: [] for r in rolls}
    for item in session["items"]:
        if item["assigned_to"]:
            assigned_items_map[item["assigned_to"]].append(item["name"])

    assigned_items_body = ""
    for i, r in enumerate(rolls):
        emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        if i > 0:
            assigned_items_body += "\n"
        assigned_items_body += f"{emoji} {ANSI_USER}{r['member'].display_name}{ANSI_RESET}\n"
        if assigned_items_map[r["member"].id]:
            for nm in assigned_items_map[r["member"].id]:
                assigned_items_body += f"- {nm}\n"
        else:
            assigned_items_body += "- N/A\n"

    assigned_items_section = assigned_items_header + assigned_items_body + "```"

    unclaimed_items = [item for item in session["items"] if not item["assigned_to"]]
    unclaimed_section = ""
    if unclaimed_items:
        unclaimed_section = f"```ansi\n{ANSI_HEADER}❌ Unclaimed Items ❌{ANSI_RESET}\n==================================\n"
        for it in unclaimed_items:
            unclaimed_section += f"{it['display_number']}. {it['name']}\n"
        unclaimed_section += "```"

    return f"{header}{roll_order_section}\n{assigned_items_section}\n{unclaimed_section}"

def _build_item_message_content_and_active(session):
    """Return (content_text, is_active_pick) for the item-dropdown message based on session state."""
    if not session:
        return ("Session expired.", False)
    if not _are_items_left(session) or session["current_turn"] == TURN_NOT_STARTED:
        return ("No active picks right now.", False)
    if not (0 <= session["current_turn"] < len(session["rolls"])):
        return ("No active picks right now.", False)
    picker = session["rolls"][session["current_turn"]]["member"]
    picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
    turn_text = "turn!" if not session.get("just_reversed", False) else "turn (direction reversed)!"
    item_message_content = f"**{picker_emoji} {picker.mention}'s {turn_text}**\n\nChoose items below..."
    return (item_message_content, True)

# ===================================================================================================
# ITEM DROPDOWN VIEW (third message)
# ===================================================================================================

class ItemDropdownView(nextcord.ui.View):
    """View attached to the 3rd message that contains item-selects + assign/skip/undo actions."""
    def __init__(self, session_id):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.populate()

    def populate(self):
        self.clear_items()
        session = loot_sessions.get(self.session_id)
        if not session:
            return

        if not _are_items_left(session):
            return

        if 0 <= session["current_turn"] < len(session["rolls"]):
            available_items = [(idx, it) for idx, it in enumerate(session["items"]) if not it["assigned_to"]]
            if not available_items:
                return

            item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]
            selected_values = set(session.get("selected_items") or [])
            for i, chunk in enumerate(item_chunks):
                options = [
                    nextcord.SelectOption(
                        label=(f"{item_dict['display_number']}. {item_dict['name']}"[:97] + '...') if len(f"{item_dict['display_number']}. {item_dict['name']}") > 100 else f"{item_dict['display_number']}. {item_dict['name']}",
                        value=str(orig_index),
                        default=str(orig_index) in selected_values
                    ) for orig_index, item_dict in chunk
                ]
                placeholder = "Choose one or more items to claim..."
                if len(item_chunks) > 1:
                    start_num, end_num = chunk[0][1]['display_number'], chunk[-1][1]['display_number']
                    placeholder = f"Choose items ({start_num}-{end_num})..."
                self.add_item(nextcord.ui.Select(placeholder=placeholder, options=options, custom_id=f"item_select_{i}", min_values=0, max_values=len(options), row=i))

            assign_disabled = not session.get("selected_items")
            self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.success, emoji="✅", custom_id="assign_button", disabled=assign_disabled, row=4))

        self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button", row=4))

        undo_disabled = not session.get("last_action")
        self.add_item(nextcord.ui.Button(label="Undo", style=nextcord.ButtonStyle.secondary, emoji="↩️", custom_id="undo_button", disabled=undo_disabled, row=4))

        for child in self.children:
            if hasattr(child, "custom_id"):
                if child.custom_id == "assign_button":
                    child.callback = self.on_assign
                elif child.custom_id == "skip_button":
                    child.callback = self.on_skip
                elif child.custom_id == "undo_button":
                    child.callback = self.on_undo
                elif "item_select" in child.custom_id:
                    child.callback = self.on_item_select

    async def _handle_interaction_response(self, interaction: nextcord.Interaction, content: str, view: nextcord.ui.View | None) -> bool:
        """
        Handles responding to an interaction in the fastest way possible.
        1. Tries to edit the message directly in the response.
        2. Falls back to deferring, then editing the original message.
        Returns True on success, False on failure.
        """
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(content=content, view=view)
                return True
        except HTTPException:
            pass

        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except HTTPException:
            return False

        try:
            await interaction.edit_original_message(content=content, view=view)
            return True
        except HTTPException:
            return False

    async def on_item_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.defer()
            return

        lock = session_locks.setdefault(self.session_id, asyncio.Lock())
        async with lock:
            all_selected_values = set(session.get("selected_items") or [])
            # Rebuild the full selection state from all dropdowns in the interaction payload
            for component in interaction.message.components:
                for child in component.children:
                    if "item_select" in child.custom_id:
                        # Get values for *this* dropdown from the interaction data
                        if child.custom_id == interaction.data['custom_id']:
                             current_dropdown_values = set(interaction.data.get("values", []))
                        else: # get values for other dropdowns from the session state
                             current_dropdown_values = {val for val in all_selected_values if any(opt.value == val for opt in child.options)}

                        # Remove old values belonging to any of the dropdowns and add the new ones
                        possible_values_in_this_dropdown = {opt.value for opt in child.options}
                        all_selected_values -= possible_values_in_this_dropdown
                        all_selected_values.update(current_dropdown_values)

            session["selected_items"] = list(all_selected_values)

        await _reset_session_timeout(session_id=self.session_id)

        # OPTIMIZATION: Immediately edit the view to show the selection.
        # This provides instant feedback to the user.
        self.populate()
        try:
            await interaction.response.edit_message(view=self)
        except HTTPException:
            # If the fast edit fails, defer and let a background task sync up.
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer()
            except HTTPException:
                pass
            asyncio.create_task(_refresh_all_messages(self.session_id, delete_item_dropdown=False))

    async def _execute_action(self, interaction: nextcord.Interaction, action: callable):
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except HTTPException: pass
            return

        current_picker = session["rolls"][session["current_turn"]]["member"] if 0 <= session["current_turn"] < len(session["rolls"]) else None
        is_picker = current_picker and interaction.user.id == current_picker.id
        is_invoker = interaction.user.id == session["invoker_id"]

        if not (is_picker or is_invoker):
            try:
                await interaction.response.send_message("🛡️ You are not authorized to perform this action.", ephemeral=True)
            except HTTPException: pass
            return

        action(session) # Perform the state change
        await _reset_session_timeout(session_id=self.session_id)

        new_content, is_active = _build_item_message_content_and_active(session)
        new_view = ItemDropdownView(self.session_id) if is_active else None

        edited_successfully = await self._handle_interaction_response(interaction, new_content, new_view)
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item_dropdown=not edited_successfully))

    async def on_assign(self, interaction: nextcord.Interaction):
        def action(session):
            selected_indices = session.get("selected_items") or []
            current_picker_id = session["rolls"][session["current_turn"]]["member"].id
            session["last_action"] = {
                "turn": session["current_turn"], "round": session["round"],
                "direction": session["direction"], "just_reversed": session.get("just_reversed", False),
                "assigned_indices": [int(i) for i in selected_indices if i.isdigit()]
            }
            for idx_str in selected_indices:
                if idx_str.isdigit() and 0 <= (idx := int(idx_str)) < len(session["items"]):
                    session["items"][idx]["assigned_to"] = current_picker_id
            session["selected_items"] = None
            _advance_turn_snake(session)
        await self._execute_action(interaction, action)

    async def on_skip(self, interaction: nextcord.Interaction):
        def action(session):
            if session["current_turn"] != TURN_NOT_STARTED:
                session["last_action"] = {
                    "turn": session["current_turn"], "round": session["round"],
                    "direction": session["direction"], "just_reversed": session.get("just_reversed", False),
                    "assigned_indices": []
                }
            session["selected_items"] = None
            _advance_turn_snake(session)
        await self._execute_action(interaction, action)

    async def on_undo(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session or interaction.user.id != session["invoker_id"]:
            try:
                await interaction.response.send_message("🛡️ Only the Loot Manager can use Undo.", ephemeral=True)
            except HTTPException: pass
            return

        ok = await _undo_last_action(session, interaction)
        if not ok: return

        await _reset_session_timeout(session_id=self.session_id)
        new_content, is_active = _build_item_message_content_and_active(session)
        new_view = ItemDropdownView(self.session_id) if is_active else None
        edited_successfully = await self._handle_interaction_response(interaction, new_content, new_view)
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item_dropdown=not edited_successfully))

# ===================================================================================================
# CONTROL PANEL VIEW (status and manager controls)
# ===================================================================================================

class ControlPanelView(nextcord.ui.View):
    """View for the control panel (message 2/2). Contains participant remove select + manager actions."""
    def __init__(self, session_id):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.populate()

    def populate(self):
        self.clear_items()
        session = loot_sessions.get(self.session_id)
        if not session: return

        if session["current_turn"] == TURN_NOT_STARTED:
            selected_values = session.get("members_to_remove") or []
            member_options = [
                nextcord.SelectOption(label=r['member'].display_name, value=str(r['member'].id), default=str(r['member'].id) in selected_values)
                for r in session["rolls"] if r["member"].id != session["invoker_id"]
            ]
            if member_options:
                self.add_item(nextcord.ui.Select(placeholder="Select participants to remove...", options=member_options, custom_id="remove_select", min_values=0, max_values=len(member_options)))
            self.add_item(nextcord.ui.Button(label="Remove Selected", style=nextcord.ButtonStyle.danger, emoji="✖️", custom_id="remove_confirm_button", disabled=not selected_values))
            self.add_item(nextcord.ui.Button(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="start_button"))

        for child in self.children:
            if hasattr(child, "custom_id"):
                if child.custom_id == "remove_select": child.callback = self.on_remove_select
                elif child.custom_id == "remove_confirm_button": child.callback = self.on_remove_confirm
                elif child.custom_id == "start_button": child.callback = self.on_start

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired.", ephemeral=True)
            return False
        if interaction.user.id == session["invoker_id"]:
            return True
        await interaction.response.send_message(f"🛡️ Only {session['invoker'].mention} can use control-panel buttons.", ephemeral=True)
        return False

    async def on_remove_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.defer()
            return
        session["members_to_remove"] = interaction.data.get("values")
        self.populate()
        try:
            await interaction.response.edit_message(view=self)
        except HTTPException:
            try:
                if not interaction.response.is_done(): await interaction.response.defer()
            except HTTPException: pass

    async def on_remove_confirm(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.defer()
            return

        ids_to_remove = {int(x) for x in session.get("members_to_remove", []) if x.isdigit()}
        if ids_to_remove:
            session["rolls"] = [r for r in session["rolls"] if r["member"].id not in ids_to_remove]
            session["members_to_remove"] = None

        await _reset_session_timeout(session_id=self.session_id)
        try:
            await interaction.response.defer()
        except HTTPException: pass
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item_dropdown=True))


    async def on_start(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.defer()
            return
        session["members_to_remove"] = None
        session["selected_items"] = None
        session["last_action"] = None
        _advance_turn_snake(session)

        await _reset_session_timeout(session_id=self.session_id)
        try:
            await interaction.response.defer()
        except HTTPException: pass
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item_dropdown=True))

# ===================================================================================================
# MESSAGE REFRESH / LIFECYCLE
# ===================================================================================================

async def _reset_session_timeout(session_id: int):
    session = loot_sessions.get(session_id)
    if not session: return
    if old_task := session.get("timeout_task"):
        old_task.cancel()
    session["timeout_task"] = asyncio.create_task(_schedule_session_timeout(session_id))

async def _cleanup_session(session_id: int, reason_message: str, view=None):
    """Centralized session cleanup logic."""
    session = loot_sessions.pop(session_id, None)
    session_locks.pop(session_id, None)
    if not session: return

    if t := session.get("timeout_task"): t.cancel()
    
    channel = bot.get_channel(session["channel_id"])
    if not channel: return

    # Clean up auxiliary messages
    for msg_key in ["loot_list_message_id", "item_dropdown_message_id"]:
        if msg_id := session.get(msg_key):
            try:
                msg = await _get_partial_message(channel, msg_id)
                if msg: await msg.delete()
            except (NotFound, Forbidden, HTTPException): pass

    # Edit the main control panel to be the final summary
    try:
        control_msg = await _get_partial_message(channel, session_id)
        if control_msg: await control_msg.edit(content=reason_message, view=view)
    except (NotFound, Forbidden, HTTPException): pass


async def _refresh_all_messages(session_id, delete_item_dropdown=False):
    session = loot_sessions.get(session_id)
    if not session: return

    lock = session_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        if not _are_items_left(session) and session["current_turn"] != TURN_NOT_STARTED:
            final_content = build_final_summary_message(session, timed_out=False)
            await _cleanup_session(session_id, final_content)
            return

        channel = bot.get_channel(session["channel_id"])
        if not channel:
            loot_sessions.pop(session_id, None)
            return

        control_panel_msg = await _get_partial_message(channel, session_id)
        loot_list_msg = await _get_partial_message(channel, session.get("loot_list_message_id"))

        # Update main messages concurrently if content has changed
        control_panel_content = build_control_panel_message(session)
        if control_panel_msg and session.get("last_control_content") != control_panel_content:
            try:
                await control_panel_msg.edit(content=control_panel_content, view=ControlPanelView(session_id))
                session["last_control_content"] = control_panel_content
            except (NotFound, Forbidden): pass

        loot_list_content = build_loot_list_message(session)
        if loot_list_msg and session.get("last_loot_content") != loot_list_content:
            try:
                await loot_list_msg.edit(content=loot_list_content)
                session["last_loot_content"] = loot_list_content
            except (NotFound, Forbidden): pass

        # === Handle the dynamic item dropdown message ===
        item_msg_id = session.get("item_dropdown_message_id")
        item_msg = await _get_partial_message(channel, item_msg_id) if item_msg_id else None

        content, is_active = _build_item_message_content_and_active(session)

        if is_active:
            if item_msg and delete_item_dropdown:
                try:
                    await item_msg.delete()
                except (NotFound, Forbidden): pass
                item_msg = None
            
            view = ItemDropdownView(session_id)
            if item_msg:
                try:
                    await item_msg.edit(content=content, view=view)
                except (NotFound, Forbidden):
                    item_msg = None # Message was deleted, recreate it
            
            if not item_msg:
                try:
                    new_msg = await channel.send(content, view=view)
                    session["item_dropdown_message_id"] = new_msg.id
                except (Forbidden, HTTPException): pass
        
        elif item_msg: # Not active, but message exists, so delete it
            try:
                await item_msg.delete()
            except (NotFound, Forbidden): pass
            session["item_dropdown_message_id"] = None
    
    await _reset_session_timeout(session_id=session_id)


# ===================================================================================================
# TIMEOUT CLEANUP TASK
# ===================================================================================================

async def _schedule_session_timeout(session_id: int):
    try:
        await asyncio.sleep(SESSION_TIMEOUT_SECONDS)
        session = loot_sessions.get(session_id)
        if session:
             final_content = build_final_summary_message(session, timed_out=True)
             await _cleanup_session(session_id, final_content)
    except asyncio.CancelledError:
        return

# ===================================================================================================
# MODAL & SLASH COMMAND
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__("RNGenie Loot Manager")
        self.loot_items = nextcord.ui.TextInput(
            label="List Items Below (One Per Line) Then Submit",
            placeholder="Example: Two-Handed Sword\n2x Health Potion",
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

        members = interaction.user.voice.channel.members
        if len(members) > 20 or not members:
            await interaction.followup.send(f"❌ A loot roll requires 1-20 members in the voice channel (found {len(members)}).", ephemeral=True)
            return

        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        roll_to_members = {}
        for r in rolls:
            roll_to_members.setdefault(r["roll"], []).append(r)
        for group in roll_to_members.values():
            if len(group) > 1:
                for r in group: r["tiebreak"] = random.randint(1, 100)
        
        rolls.sort(key=lambda r: (r["roll"], r.get("tiebreak", -1)), reverse=True)

        item_names = []
        for line in self.loot_items.value.splitlines():
            if not (stripped := line.strip()): continue
            if match := re.match(r"(\d+)[xX]\s*(.*)", stripped):
                count, name = int(match.group(1)), match.group(2).strip()
                if name: item_names.extend([name] * count)
            else:
                item_names.append(stripped)
        
        if not item_names:
            await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)
            return

        items_data = [{"name": nm, "assigned_to": None, "display_number": i} for i, nm in enumerate(item_names, 1)]
        
        loot_list_msg = await interaction.followup.send("`Initializing Loot List (1/2)...`", wait=True)
        control_panel_msg = await interaction.channel.send("`Initializing Control Panel (2/2)...`")

        session_id = control_panel_msg.id
        session = {
            "rolls": rolls, "items": items_data, "current_turn": TURN_NOT_STARTED,
            "invoker_id": interaction.user.id, "invoker": interaction.user, "selected_items": None,
            "round": 0, "direction": 1, "just_reversed": False, "members_to_remove": None,
            "channel_id": control_panel_msg.channel.id, "loot_list_message_id": loot_list_msg.id,
            "item_dropdown_message_id": None, "last_action": None, "last_control_content": None,
            "last_loot_content": None, "timeout_task": None
        }
        loot_sessions[session_id] = session

        await _reset_session_timeout(session_id)

        session["last_control_content"] = build_control_panel_message(session)
        session["last_loot_content"] = build_loot_list_message(session)
        
        await loot_list_msg.edit(content=session["last_loot_content"])
        await control_panel_msg.edit(content=session["last_control_content"], view=ControlPanelView(session_id))

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
    print(f"\n--- Unhandled exception in interaction ---\n{traceback.format_exc()}--- End of exception report ---\n")
    if not interaction.is_expired():
        message = "❌ An unexpected error occurred. See console for details."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except HTTPException:
            pass

# ===================================================================================================
# RUN SCRIPT
# ===================================================================================================

if __name__ == "__main__":
    load_dotenv()
    bot.run(os.getenv("DISCORD_TOKEN"))
