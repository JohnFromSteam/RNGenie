# RNGenie.py
# A Discord bot for managing turn-based loot distribution in voice channels.
# Updated: adds 3rd message for item dropdown (auto-deletes+repopulates), tiebreaker on ties,
# and tidies up footers / view separation.

import os
import traceback
import random
import re
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

# session keyed by control panel message ID
loot_sessions = {}

SESSION_TIMEOUT_SECONDS = 1800  # 30 minutes

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


# ===================================================================================================
# SHARED HELPERS
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

    if session["current_turn"] == -1:
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
    # detect duplicates
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
            # If tiebreak wasn't generated for some reason, show "TB:—"
            tb = roll_info.get("tiebreak")
            tb_text = f"/TB:{tb}" if tb is not None else "/TB:—"
            base += f" {tb_text}"
        lines.append(base)
    return "\n".join(lines)


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

    header = f"**(2/2)**\n\n🎉 **Loot roll** — started by {invoker.mention}\n\n"

    # Roll order
    roll_order_section = f"```ansi\n{ANSI_HEADER}🔢 Roll Order 🔢{ANSI_RESET}\n==================================\n"
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
        picker = rolls[session["current_turn"]]["member"]
        direction_text = "Normal" if session["direction"] == 1 else "Reverse"
        picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
        turn_text = "turn!" if not session.get("just_reversed", False) else "turn (direction reversed)!"
        indicator = (
            f"\n🔔 **Round {session['round'] + 1}** ({direction_text})\n\n"
        )
    else:
        indicator = f"\n🎁 **Loot distribution is ready!**\n\n✍️ **Loot Manager {invoker.mention} can remove participants or click below to begin.**"

    return f"{header}{roll_order_section}\n{assigned_items_section}{indicator}"


def build_final_summary_message(session, timed_out=False):
    rolls = session["rolls"]
    header = "⌛ **The loot session has timed out — final summary:**\n\n" if timed_out else "✅ **Final Summary — all items assigned:**\n\n"

    roll_order_section = f"```ansi\n{ANSI_HEADER}🔢 Final Roll Order 🔢{ANSI_RESET}\n==================================\n"
    roll_order_section += _build_roll_display(rolls)
    roll_order_section += "\n```"

    assigned_items_header = f"```ansi\n{ANSI_HEADER}✅ Final Assigned Items ✅{ANSI_RESET}\n==================================\n"
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
    """View attached to the 3rd message that contains item-selects + assign/skip actions.
       This view is **recreated** and the message replaced whenever the turn advances (per your request)."""
    def __init__(self, session_id):
        super().__init__(timeout=SESSION_TIMEOUT_SECONDS)
        self.session_id = session_id
        self.populate()

def populate(self):
    self.clear_items()
    session = loot_sessions.get(self.session_id)
    if not session:
        return

    # Pre-start: manage participants
    if session["current_turn"] == -1:
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
            if child.custom_id == "undo_button":
                child.callback = self.on_undo

    async def on_item_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return

        dropdown_id = interaction.data["custom_id"]
        dropdown_index = int(dropdown_id.split("_")[-1])
        available_items = [(index, item) for index, item in enumerate(session["items"]) if not item["assigned_to"]]
        item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]

        if dropdown_index >= len(item_chunks):
            await interaction.response.send_message("Invalid selection (stale dropdown).", ephemeral=True)
            return

        possible_values = {str(index) for index, _ in item_chunks[dropdown_index]}
        newly_selected = set(interaction.data.get("values", []))
        current_master = set(session.get("selected_items") or [])
        # replace values belonging to this dropdown
        current_master -= possible_values
        current_master |= newly_selected
        session["selected_items"] = list(current_master)

        # Do NOT delete/recreate the third message on mere selection; just edit it in-place.
        await _refresh_all_messages(self.session_id, interaction)

    async def on_assign(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return

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
        await _refresh_all_messages(self.session_id, interaction)

    async def on_skip(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return

        if session["current_turn"] != -1:
            session["last_action"] = {
                "turn": session["current_turn"],
                "round": session["round"],
                "direction": session["direction"],
                "just_reversed": session.get("just_reversed", False),
                "assigned_indices": []
            }

        session["selected_items"] = None
        if session["current_turn"] == -1:
            session["members_to_remove"] = None
            session["last_action"] = None

        _advance_turn_snake(session)
        await _refresh_all_messages(self.session_id, interaction)

    async def on_undo(self, interaction: nextcord.Interaction):
        """Undo button placed next to Skip Turn. Only Loot Manager (invoker) allowed."""
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return

        # permission check
        if interaction.user.id != session["invoker_id"]:
            await interaction.response.send_message("🛡️ Only the Loot Manager can use Undo.", ephemeral=True)
            return

        last_action = session.get("last_action")
        if not last_action:
            await interaction.response.send_message("❌ There is nothing to undo.", ephemeral=True)
            return

        indices_to_unassign = last_action.get("assigned_indices", [])
        for idx in indices_to_unassign:
            if 0 <= idx < len(session["items"]):
                session["items"][idx]["assigned_to"] = None

        session["current_turn"] = last_action["turn"]
        session["round"] = last_action["round"]
        session["direction"] = last_action["direction"]
        session["just_reversed"] = last_action.get("just_reversed", False)

        session["last_action"] = None
        session["selected_items"] = None

        await _refresh_all_messages(self.session_id, interaction)


# ===================================================================================================
# CONTROL PANEL VIEW (status and manager controls)
# ===================================================================================================

class ControlPanelView(nextcord.ui.View):
    """View for the control panel (message 2/2). Contains participant remove select + manager actions + undo."""
    def __init__(self, session_id):
        super().__init__(timeout=SESSION_TIMEOUT_SECONDS)
        self.session_id = session_id
        self.populate()

    def populate(self):
        self.clear_items()
        session = loot_sessions.get(self.session_id)
        if not session:
            return

        # Pre-start: manage participants
        if session["current_turn"] == -1:
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
            # Post-start: allow undo (invoker only) and a terse control hint in the panel.
            undo_disabled = not session.get("last_action")
            self.add_item(nextcord.ui.Button(label="Undo", style=nextcord.ButtonStyle.secondary, emoji="↩️", custom_id="undo_button", disabled=undo_disabled))

        # attach callbacks
        for child in self.children:
            if hasattr(child, "custom_id"):
                if child.custom_id == "remove_select":
                    child.callback = self.on_remove_select
                if child.custom_id == "remove_confirm_button":
                    child.callback = self.on_remove_confirm
                if child.custom_id == "start_button":
                    child.callback = self.on_start
                if child.custom_id == "undo_button":
                    child.callback = self.on_undo

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False

        # Undo restricted to invoker
        if interaction.data.get("custom_id") == "undo_button":
            if interaction.user.id == session["invoker_id"]:
                return True
            else:
                await interaction.response.send_message("🛡️ Only the Loot Manager can use Undo.", ephemeral=True)
                return False

        # Invoker always allowed
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
        ids_to_remove = set(int(x) for x in session.get("members_to_remove", []))
        if ids_to_remove:
            session["rolls"] = [r for r in session["rolls"] if r["member"].id not in ids_to_remove]
            session["members_to_remove"] = None
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
        await _refresh_all_messages(self.session_id, interaction)

    async def on_undo(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("Session expired.", ephemeral=True)
            return

        last_action = session.get("last_action")
        if not last_action:
            await interaction.response.send_message("❌ There is nothing to undo.", ephemeral=True)
            return

        indices_to_unassign = last_action.get("assigned_indices", [])
        for idx in indices_to_unassign:
            if 0 <= idx < len(session["items"]):
                session["items"][idx]["assigned_to"] = None

        session["current_turn"] = last_action["turn"]
        session["round"] = last_action["round"]
        session["direction"] = last_action["direction"]
        session["just_reversed"] = last_action.get("just_reversed", False)

        session["last_action"] = None
        session["selected_items"] = None

        await _refresh_all_messages(self.session_id, interaction)


# ===================================================================================================
# MESSAGE REFRESH / LIFECYCLE
# ===================================================================================================

async def _refresh_all_messages(session_id, interaction=None, delete_item=True):
    """Centralized message update: control panel, loot list, and item dropdown.
       Only creates/deletes the item-dropdown message when delete_item=True.
       If delete_item is False, attempt to edit the existing third message in-place
       (keeps user selections smooth when they just clicked a dropdown)."""
    session = loot_sessions.get(session_id)
    if not session:
        if interaction and not interaction.is_expired():
            await interaction.response.send_message("Session missing or expired.", ephemeral=True)
        return

    channel = bot.get_channel(session["channel_id"])
    if not channel:
        loot_sessions.pop(session_id, None)
        return

    # fetch control panel (session_id is control panel message id)
    try:
        control_panel_msg = await channel.fetch_message(session_id)
    except (nextcord.NotFound, nextcord.Forbidden):
        loot_sessions.pop(session_id, None)
        return

    # loot list message (may be None if deleted)
    try:
        loot_list_msg = await channel.fetch_message(session["loot_list_message_id"])
    except (nextcord.NotFound, nextcord.Forbidden):
        loot_list_msg = None

    # delete existing item-dropdown message only if delete_item is True
    old_item_msg_id = session.get("item_dropdown_message_id")
    if delete_item and old_item_msg_id:
        try:
            old_item_msg = await channel.fetch_message(old_item_msg_id)
            await old_item_msg.delete()
        except (nextcord.NotFound, nextcord.Forbidden):
            pass
        session["item_dropdown_message_id"] = None

    # If all items assigned -> finalize and cleanup
    if not _are_items_left(session) and session["current_turn"] != -1:
        final_content = build_final_summary_message(session, timed_out=False)
        await control_panel_msg.edit(content=final_content, view=None)
        if loot_list_msg:
            try:
                await loot_list_msg.delete()
            except (nextcord.NotFound, nextcord.Forbidden):
                pass
        loot_sessions.pop(session_id, None)
        return

    # Normal update: update loot list and control panel content
    loot_list_content = build_loot_list_message(session)
    control_panel_content = build_control_panel_message(session)
    control_view = ControlPanelView(session_id)
    await control_panel_msg.edit(content=control_panel_content, view=control_view)
    if loot_list_msg:
        await loot_list_msg.edit(content=loot_list_content)

    # ---- ONLY create the third (item-dropdown) message if session is active (not pre-start)
    is_active_pick = (0 <= session["current_turn"] < len(session["rolls"])) and _are_items_left(session)
    if not is_active_pick:
        # We're in pre-start or no active picker; do NOT create the item-dropdown message.
        # If delete_item was False we still might want to clear item id to be safe.
        if delete_item:
            session["item_dropdown_message_id"] = None
        return

    # Create the item-dropdown message appropriate for an active picker's turn:
    invoker = session["invoker"]
    picker = session["rolls"][session["current_turn"]]["member"]

    # small helpers used elsewhere — keep wording consistent with the control-panel
    picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
    turn_text = "turn!" if not session.get("just_reversed", False) else "turn (direction reversed)!"

    item_message_content = (
        f"**{picker_emoji} {picker.mention}'s {turn_text}**\n\n"
        "Choose items below..."
    )


    item_view = ItemDropdownView(session_id)

    # If delete_item is False, attempt to edit the existing item message in-place.
    existing_id = session.get("item_dropdown_message_id")
    if not delete_item and existing_id:
        try:
            existing_msg = await channel.fetch_message(existing_id)
            await existing_msg.edit(content=item_message_content, view=item_view)
            return
        except (nextcord.NotFound, nextcord.Forbidden):
            # If fetch/edit fails, fall through and create a new message.
            session["item_dropdown_message_id"] = None

    # Otherwise (delete_item True or no existing message), create a fresh message.
    item_msg = await channel.send(item_message_content, view=item_view)
    session["item_dropdown_message_id"] = item_msg.id


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
            else:
                # leave tiebreak unset for non-ties
                pass

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
            "current_turn": -1,
            "invoker_id": interaction.user.id,
            "invoker": interaction.user,
            "selected_items": None,
            "round": 0,
            "direction": 1,
            "just_reversed": False,
            "members_to_remove": None,
            "channel_id": interaction.channel.id,
            "loot_list_message_id": loot_list_message.id,
            "item_dropdown_message_id": None,
            "last_action": None
        }
        loot_sessions[session_id] = session

        # Build initial messages and views
        loot_list_content = build_loot_list_message(session)
        control_panel_content = build_control_panel_message(session)
        await loot_list_message.edit(content=loot_list_content)
        await control_panel_message.edit(content=control_panel_content, view=ControlPanelView(session_id))

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
