# RNGenie.py
# A Discord bot for managing turn-based loot distribution in voice channels.
# Optimized: Merged control/loot messages to reduce API calls for faster updates,
# especially in voice-channel text chats. Refined message handling for robustness.

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

# Sessions keyed by the main control panel message ID
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

async def _maybe_get_message(channel, message_id):
    """
    Robust helper to obtain a message-like object that supports .edit()/.delete()
    Works with channels that expose get_partial_message() (fast path) or fetch_message().
    Returns None if message can't be obtained.
    """
    if not channel or not message_id:
        return None
    try:
        # Fast path: Partial messages don't require an API call to get an object
        return channel.get_partial_message(message_id)
    except Exception:
        # Fallback: Fetch message if partial fails (e.g., not in cache)
        try:
            return await channel.fetch_message(message_id)
        except (nextcord.NotFound, nextcord.Forbidden):
            return None # Message is gone or we can't access it
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

def build_main_panel_message(session):
    """Content for the single main panel (Loot, Status, Assigned Items, Turn Indicator)."""
    invoker = session["invoker"]

    # 1. Remaining Items Section
    remaining_items = [item for item in session["items"] if not item["assigned_to"]]
    if remaining_items:
        remaining_header = f"```ansi\n{ANSI_HEADER}❌ Remaining Loot Items ❌{ANSI_RESET}\n==================================\n"
        remaining_body = ""
        for item in session["items"]:
            if not item["assigned_to"]:
                remaining_body += f"{item['display_number']}. {item['name']}\n"
        remaining_section = f"{remaining_header}{remaining_body}```"
    else:
        remaining_section = f"```ansi\n{ANSI_HEADER}✅ All Items Assigned ✅{ANSI_RESET}\n==================================\nAll items have been distributed.\n```"


    # 2. Roll Order Section
    roll_order_section = f"```ansi\n{ANSI_HEADER}🎲 Roll Order 🎲{ANSI_RESET}\n==================================\n"
    roll_order_section += _build_roll_display(session["rolls"])
    roll_order_section += "\n```"

    # 3. Assigned Items Section
    assigned_items_header = f"```ansi\n{ANSI_HEADER}✅ Assigned Items ✅{ANSI_RESET}\n==================================\n"
    assigned_items_map = {r["member"].id: [] for r in session["rolls"]}
    for item in session["items"]:
        if item["assigned_to"]:
            assigned_items_map[item["assigned_to"]].append(item["name"])

    assigned_items_body = ""
    for i, roll_info in enumerate(session["rolls"]):
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

    # 4. Turn Indicator / Status
    indicator = ""
    if session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"]):
        direction_text = "Normal" if session["direction"] == 1 else "Reverse"
        indicator = f"\n🔔 **Round {session['round'] + 1}** ({direction_text})"
    else:
        indicator = f"\n🎁 **Loot distribution is ready!**\n\n✍️ **Loot Manager {invoker.mention} can remove participants or click below to begin.**"

    header = f"✍️ **Loot Manager:** {invoker.mention}\n"
    return f"{header}\n{remaining_section}\n{roll_order_section}\n{assigned_items_section}{indicator}"

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
# ITEM DROPDOWN VIEW (second message)
# ===================================================================================================

class ItemDropdownView(nextcord.ui.View):
    """View attached to the second message that contains item-selects + assign/skip/undo actions."""
    def __init__(self, session_id):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.populate()

    def populate(self):
        self.clear_items()
        session = loot_sessions.get(self.session_id)
        if not session: return

        if not _are_items_left(session): return

        if 0 <= session["current_turn"] < len(session["rolls"]):
            available_items = [(idx, it) for idx, it in enumerate(session["items"]) if not it["assigned_to"]]
            if not available_items: return

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
                self.add_item(nextcord.ui.Select(placeholder=placeholder, options=options, custom_id=f"item_select_{i}", min_values=0, max_values=len(options)))

            assign_disabled = not session.get("selected_items")
            self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.success, emoji="✅", custom_id="assign_button", disabled=assign_disabled))

        self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button"))
        
        undo_disabled = not session.get("last_action")
        self.add_item(nextcord.ui.Button(label="Undo", style=nextcord.ButtonStyle.secondary, emoji="↩️", custom_id="undo_button", disabled=undo_disabled))

        for child in self.children:
            if hasattr(child, "custom_id"):
                if child.custom_id == "assign_button": child.callback = self.on_assign
                if child.custom_id == "skip_button": child.callback = self.on_skip
                if child.custom_id == "undo_button": child.callback = self.on_undo
                if "item_select" in child.custom_id: child.callback = self.on_item_select

    async def _fast_edit_item_message_response(self, interaction: nextcord.Interaction, content: str, view: nextcord.ui.View | None):
        """
        Attempt to immediately edit the message via interaction response for fast feedback.
        """
        try:
            await interaction.response.edit_message(content=content, view=view)
            return True
        except Exception:
            try:
                await interaction.response.defer_update()
            except Exception:
                pass # Acknowledgment failed, proceed to background update

            session = loot_sessions.get(self.session_id)
            if not session: return False
            ch = bot.get_channel(session["channel_id"])
            if not ch: return False
            try:
                existing_id = session.get("item_dropdown_message_id")
                if existing_id:
                    msg = await _maybe_get_message(ch, existing_id)
                    if msg:
                        await msg.edit(content=content, view=view)
                        return True
            except Exception:
                return False
        return False

    async def _ack_interaction_safely(self, interaction: nextcord.Interaction):
        """Acknowledge interaction without failing."""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer_update()
        except (nextcord.HTTPException, nextcord.NotFound):
            pass # Ignore if interaction is already gone

    async def on_item_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await self._ack_interaction_safely(interaction)
            return

        dropdown_id = interaction.data.get("custom_id", "")
        try:
            dropdown_index = int(dropdown_id.split("_")[-1])
        except (ValueError, IndexError):
            await self._ack_interaction_safely(interaction)
            return

        available_items = [(index, item) for index, item in enumerate(session["items"]) if not item["assigned_to"]]
        item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]

        if dropdown_index >= len(item_chunks):
            await self._ack_interaction_safely(interaction)
            return

        possible_values = {str(index) for index, _ in item_chunks[dropdown_index]}
        newly_selected = set(interaction.data.get("values", []))

        lock = session_locks.setdefault(self.session_id, asyncio.Lock())
        async with lock:
            current_master = set(session.get("selected_items") or [])
            current_master -= possible_values
            current_master |= newly_selected
            session["selected_items"] = list(current_master)
        
        await self._ack_interaction_safely(interaction)
        await _reset_session_timeout(session_id=self.session_id)
        asyncio.create_task(_refresh_messages(self.session_id, delete_item_msg=False))

    async def on_assign(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return await interaction.response.send_message("Session expired.", ephemeral=True, delete_after=10)

        if not (0 <= session["current_turn"] < len(session["rolls"])):
            return await interaction.response.send_message("It's not an active picking turn.", ephemeral=True, delete_after=10)

        current_picker = session["rolls"][session["current_turn"]]["member"]
        if interaction.user.id != current_picker.id and interaction.user.id != session["invoker_id"]:
            return await interaction.response.send_message("🛡️ Only the current picker or the Loot Manager can assign items.", ephemeral=True, delete_after=10)

        selected_indices = session.get("selected_items") or []
        session["last_action"] = {
            "turn": session["current_turn"], "round": session["round"], "direction": session["direction"],
            "just_reversed": session.get("just_reversed", False),
            "assigned_indices": [int(i) for i in selected_indices if i.isdigit()]
        }

        if selected_indices:
            for idx_str in selected_indices:
                if idx_str.isdigit() and 0 <= int(idx_str) < len(session["items"]):
                    session["items"][int(idx_str)]["assigned_to"] = current_picker.id
        
        session["selected_items"] = None
        _advance_turn_snake(session)
        await _reset_session_timeout(session_id=self.session_id)

        new_content, active = _build_item_message_content_and_active(session)
        new_view = ItemDropdownView(self.session_id) if active else None
        edited = await self._fast_edit_item_message_response(interaction, new_content, new_view)
        
        asyncio.create_task(_refresh_messages(self.session_id, delete_item_msg=not edited))

    async def on_skip(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return await interaction.response.send_message("Session expired.", ephemeral=True, delete_after=10)

        if 0 <= session["current_turn"] < len(session["rolls"]):
            current_picker = session["rolls"][session["current_turn"]]["member"]
            if interaction.user.id != current_picker.id and interaction.user.id != session["invoker_id"]:
                return await interaction.response.send_message("🛡️ Only the current picker or the Loot Manager can skip.", ephemeral=True, delete_after=10)

        if session["current_turn"] != TURN_NOT_STARTED:
            session["last_action"] = {
                "turn": session["current_turn"], "round": session["round"], "direction": session["direction"],
                "just_reversed": session.get("just_reversed", False), "assigned_indices": []
            }

        session["selected_items"] = None
        if session["current_turn"] == TURN_NOT_STARTED:
            session["members_to_remove"] = None
            session["last_action"] = None

        _advance_turn_snake(session)
        await _reset_session_timeout(session_id=self.session_id)

        new_content, active = _build_item_message_content_and_active(session)
        new_view = ItemDropdownView(self.session_id) if active else None
        edited = await self._fast_edit_item_message_response(interaction, new_content, new_view)
        
        asyncio.create_task(_refresh_messages(self.session_id, delete_item_msg=not edited))

    async def on_undo(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return await interaction.response.send_message("Session expired.", ephemeral=True, delete_after=10)

        if interaction.user.id != session["invoker_id"]:
            return await interaction.response.send_message("🛡️ Only the Loot Manager can use Undo.", ephemeral=True, delete_after=10)

        if not await _undo_last_action(session, interaction): return
        
        await _reset_session_timeout(session_id=self.session_id)
        
        new_content, active = _build_item_message_content_and_active(session)
        new_view = ItemDropdownView(self.session_id) if active else None
        edited = await self._fast_edit_item_message_response(interaction, new_content, new_view)
        
        asyncio.create_task(_refresh_messages(self.session_id, delete_item_msg=not edited))

# ===================================================================================================
# CONTROL PANEL VIEW (part of the main message)
# ===================================================================================================

class ControlPanelView(nextcord.ui.View):
    """View for the main panel message. Contains participant removal and manager actions."""
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
            
            remove_disabled = not session.get("members_to_remove")
            self.add_item(nextcord.ui.Button(label="Remove Selected", style=nextcord.ButtonStyle.danger, emoji="✖️", custom_id="remove_confirm_button", disabled=remove_disabled))
            self.add_item(nextcord.ui.Button(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="start_button"))
        
        for child in self.children:
            if hasattr(child, "custom_id"):
                if child.custom_id == "remove_select": child.callback = self.on_remove_select
                if child.custom_id == "remove_confirm_button": child.callback = self.on_remove_confirm
                if child.custom_id == "start_button": child.callback = self.on_start

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired.", ephemeral=True, delete_after=10)
            return False
        if interaction.user.id == session["invoker_id"]:
            return True
        await interaction.response.send_message(f"🛡️ Only {session['invoker'].mention} can use control-panel buttons.", ephemeral=True, delete_after=10)
        return False

    async def on_remove_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return await interaction.response.send_message("Session expired.", ephemeral=True, delete_after=10)
        session["members_to_remove"] = interaction.data.get("values")
        self.populate()
        await interaction.response.edit_message(view=self)

    async def on_remove_confirm(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return await interaction.response.send_message("Session expired.", ephemeral=True, delete_after=10)

        ids_to_remove = {int(x) for x in session.get("members_to_remove", []) if x.isdigit()}
        if ids_to_remove:
            session["rolls"] = [r for r in session["rolls"] if r["member"].id not in ids_to_remove]
            session["members_to_remove"] = None
            if not session["rolls"]:
                await interaction.response.defer(ephemeral=True)
                await _cleanup_session(self.session_id, interaction.channel, cancelled=True)
                return

        await _reset_session_timeout(session_id=self.session_id)
        await interaction.response.defer(ephemeral=True)
        asyncio.create_task(_refresh_messages(self.session_id, delete_item_msg=True))

    async def on_start(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return await interaction.response.send_message("Session expired.", ephemeral=True, delete_after=10)
        
        session["members_to_remove"] = None
        session["selected_items"] = None
        session["last_action"] = None
        _advance_turn_snake(session)
        
        await _reset_session_timeout(session_id=self.session_id)
        await interaction.response.defer(ephemeral=True)
        asyncio.create_task(_refresh_messages(self.session_id, delete_item_msg=True))

# ===================================================================================================
# MESSAGE REFRESH / LIFECYCLE
# ===================================================================================================

async def _reset_session_timeout(session_id: int):
    session = loot_sessions.get(session_id)
    if not session: return
    if session.get("timeout_task"):
        session["timeout_task"].cancel()
    session["timeout_task"] = asyncio.create_task(_schedule_session_timeout(session_id))

async def _refresh_messages(session_id, delete_item_msg=True):
    session = loot_sessions.get(session_id)
    if not session: return

    lock = session_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        channel = bot.get_channel(session["channel_id"])
        if not channel:
            return await _cleanup_session(session_id, None)

        main_panel_msg = await _maybe_get_message(channel, session_id)
        item_msg = None
        if session.get("item_dropdown_message_id"):
            item_msg = await _maybe_get_message(channel, session["item_dropdown_message_id"])

        if delete_item_msg and item_msg:
            try:
                await item_msg.delete()
            except (nextcord.NotFound, nextcord.Forbidden): pass
            session["item_dropdown_message_id"] = None
            item_msg = None
        
        if not _are_items_left(session) and session["current_turn"] != TURN_NOT_STARTED:
            return await _cleanup_session(session_id, channel, timed_out=False)

        main_panel_content = build_main_panel_message(session)
        if main_panel_msg and main_panel_content != session.get("last_main_content"):
            try:
                await main_panel_msg.edit(content=main_panel_content, view=ControlPanelView(session_id))
                session["last_main_content"] = main_panel_content
            except (nextcord.NotFound, nextcord.Forbidden): pass

        is_active_pick = (0 <= session["current_turn"] < len(session["rolls"])) and _are_items_left(session)

        if not is_active_pick:
            if item_msg:
                try: await item_msg.delete()
                except (nextcord.NotFound, nextcord.Forbidden): pass
                session["item_dropdown_message_id"] = None
            return

        item_message_content, _ = _build_item_message_content_and_active(session)
        item_view = ItemDropdownView(session_id)

        if item_msg:
            try:
                await item_msg.edit(content=item_message_content, view=item_view)
            except (nextcord.NotFound, nextcord.Forbidden):
                session["item_dropdown_message_id"] = None
                item_msg = None

        if not item_msg:
            try:
                new_item_msg = await channel.send(item_message_content, view=item_view)
                session["item_dropdown_message_id"] = new_item_msg.id
            except (nextcord.Forbidden):
                session["item_dropdown_message_id"] = None

# ===================================================================================================
# SESSION CLEANUP
# ===================================================================================================

async def _cleanup_session(session_id, channel, timed_out=False, cancelled=False):
    session = loot_sessions.pop(session_id, None)
    session_locks.pop(session_id, None)
    if not session: return

    if session.get("timeout_task"):
        session["timeout_task"].cancel()

    if not channel:
        channel = bot.get_channel(session["channel_id"])
        if not channel: return

    if session.get("item_dropdown_message_id"):
        try:
            msg = await _maybe_get_message(channel, session["item_dropdown_message_id"])
            if msg: await msg.delete()
        except (nextcord.NotFound, nextcord.Forbidden): pass

    main_panel_msg = await _maybe_get_message(channel, session_id)
    if main_panel_msg:
        try:
            if cancelled:
                content = "⚠️ The loot session was cancelled."
            else:
                content = build_final_summary_message(session, timed_out=timed_out)
            await main_panel_msg.edit(content=content, view=None)
        except (nextcord.NotFound, nextcord.Forbidden): pass

async def _schedule_session_timeout(session_id: int):
    try:
        await asyncio.sleep(SESSION_TIMEOUT_SECONDS)
        await _cleanup_session(session_id, None, timed_out=True)
    except asyncio.CancelledError:
        return

# ===================================================================================================
# MODAL & SLASH COMMAND
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__("RNGenie Loot Manager")
        self.loot_items = nextcord.ui.TextInput(
            label="List Items Below (One Per Line)",
            placeholder="Type your items here\nExample: 2x Health Potion",
            required=True, style=nextcord.TextInputStyle.paragraph, max_length=2000
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ You must be in a voice channel to start.", ephemeral=True)

        members = interaction.user.voice.channel.members
        if len(members) > 20:
            return await interaction.followup.send(f"❌ Too many users in VC ({len(members)}/20).", ephemeral=True)
        if not members:
            return await interaction.followup.send("❌ No one found in your voice channel.", ephemeral=True)

        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        roll_to_members = {}
        for r in rolls:
            roll_to_members.setdefault(r["roll"], []).append(r)
        for roll_val, group in roll_to_members.items():
            if len(group) > 1:
                for r in group:
                    r["tiebreak"] = random.randint(1, 100)
        rolls.sort(key=lambda r: (r["roll"], r.get("tiebreak", -1)), reverse=True)

        item_names = []
        for line in self.loot_items.value.splitlines():
            if not (stripped_line := line.strip()): continue
            match = re.match(r"(\d+)[xX]\s*(.*)", stripped_line)
            if match:
                try:
                    count = int(match.group(1))
                    name = match.group(2).strip()
                    if name: item_names.extend([name] * count)
                except ValueError: item_names.append(stripped_line)
            else:
                item_names.append(stripped_line)
        
        if not (items_data := [{"name": nm, "assigned_to": None, "display_number": i} for i, nm in enumerate(item_names, 1)]):
            return await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)

        # Create the main panel message first to get its ID for the session
        # This is sent to the channel where the command was used
        main_panel_message = await interaction.channel.send("`Initializing...`")
        session_id = main_panel_message.id

        session = {
            "rolls": rolls, "items": items_data, "current_turn": TURN_NOT_STARTED,
            "invoker_id": interaction.user.id, "invoker": interaction.user,
            "selected_items": None, "round": 0, "direction": 1,
            "just_reversed": False, "members_to_remove": None,
            "channel_id": interaction.channel.id, "item_dropdown_message_id": None,
            "last_action": None, "last_main_content": None, "timeout_task": None
        }
        loot_sessions[session_id] = session

        await _reset_session_timeout(session_id)

        # Now edit the panel with the full content and view
        main_panel_content = build_main_panel_message(session)
        await main_panel_message.edit(content=main_panel_content, view=ControlPanelView(session_id))
        session["last_main_content"] = main_panel_content
        
        await interaction.followup.send(f"✅ Loot session started! See {main_panel_message.jump_url}", ephemeral=True)
        
@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    if not interaction.user.voice:
        return await interaction.response.send_message("❌ You must be in a voice channel to start a loot roll!", ephemeral=True)
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
        message = "❌ An unexpected error occurred. Please check the console for details."
        try:
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
