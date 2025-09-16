# RNGenie.py
# A Discord bot for managing turn-based loot distribution in voice channels.

import os
import traceback
import random
import re
import asyncio
from dotenv import load_dotenv
from itertools import groupby
import nextcord
from nextcord.ext import commands

# ===================================================================================================
# BOT SETUP & GLOBAL STATE
# ===================================================================================================

# Define the specific intents required for the bot to function.
intents = nextcord.Intents.default()
intents.members = True      # Required to read member properties like display names.
intents.voice_states = True # Required to see who is in a voice channel.

bot = commands.Bot(intents=intents)

# A dictionary to hold the state of active loot sessions, keyed by a persistent message ID.
loot_sessions = {}

# --- Constants ---
SESSION_TIMEOUT_SECONDS = 1800  # 30 minutes

# Emojis for numbering lists in the Discord message for a clean UI.
NUMBER_EMOJIS = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟",
    11: "1️⃣1️⃣", 12: "1️⃣2️⃣", 13: "1️⃣3️⃣", 14: "1️⃣4️⃣", 15: "1️⃣5️⃣", 16: "1️⃣6️⃣", 17: "1️⃣7️⃣", 18: "1️⃣8️⃣", 19: "1️⃣9️⃣", 20: "2️⃣0️⃣"
}

# ANSI color codes for formatting the text blocks in Discord messages.
ANSI_RESET = "\u001b[0m"
ANSI_HEADER = "\u001b[0;33m"
ANSI_USER = "\u001b[0;34m"


# ===================================================================================================
# MESSAGE BUILDER FUNCTIONS
# ===================================================================================================

def build_loot_list_message(session):
    """Builds the content for the first message (1/3), which lists remaining loot."""
    header = "**(1/3)**\n"
    if any(not item["assigned_to"] for item in session["items"]):
        remaining_body = "\n".join(
            f"{item['display_number']}. {item['name']}"
            for item in session["items"] if not item["assigned_to"]
        )
        return (
            f"{header}```ansi\n{ANSI_HEADER}❌ Remaining Loot Items ❌{ANSI_RESET}\n"
            f"==================================\n{remaining_body}\n```"
        )
    return (
        f"{header}```ansi\n{ANSI_HEADER}✅ All Items Assigned ✅{ANSI_RESET}\n"
        f"==================================\nAll items have been distributed.\n```"
    )


def build_control_panel_message(session):
    """Builds the content for the second message (2/3), the main info panel."""
    invoker = session["invoker"]
    header = f"**(2/3)**\n\n🎉 **Loot roll started by {invoker.mention}!**\n\n"

    # --- Roll Order Section ---
    roll_order_body = "\n".join(
        f"{NUMBER_EMOJIS.get(i, f'#{i}')} {ANSI_USER}{r['member'].display_name}{ANSI_RESET} ({r['roll']})"
        f"{f' (TIE BREAKER: {r['tiebreaker_roll']})' if 'tiebreaker_roll' in r else ''}"
        for i, r in enumerate(session["rolls"], 1)
    )
    roll_order_section = (
        f"```ansi\n{ANSI_HEADER}🔢 Roll Order 🔢{ANSI_RESET}\n==================================\n{roll_order_body}\n```"
    )

    # --- Assigned Items Section ---
    assigned_items_map = {roll_info["member"].id: [] for roll_info in session["rolls"]}
    for item in session["items"]:
        if item["assigned_to"]:
            assigned_items_map[item["assigned_to"]].append(item["name"])

    assigned_items_body = ""
    for i, roll_info in enumerate(session["rolls"], 1):
        if i > 1: assigned_items_body += "\n"
        assigned_items_body += f"{NUMBER_EMOJIS.get(i, f'#{i}')} {ANSI_USER}{roll_info['member'].display_name}{ANSI_RESET}\n"
        if assigned_items_map[roll_info["member"].id]:
            assigned_items_body += "".join(f"   - {name}\n" for name in assigned_items_map[roll_info["member"].id])
        
    assigned_items_section = (
        f"```ansi\n{ANSI_HEADER}✅ Assigned Items ✅{ANSI_RESET}\n==================================\n{assigned_items_body}```"
    )
    return f"{header}{roll_order_section}\n{assigned_items_section}"


def build_final_summary_message(session, timed_out=False):
    """Builds the single, merged message shown when the session ends."""
    header = "⌛ **The loot session has timed out!**\n\n" if timed_out else "✅ **All items have been assigned!**\n\n"

    # --- Final Roll Order Section ---
    roll_order_body = "\n".join(
        f"{NUMBER_EMOJIS.get(i, f'#{i}')} {ANSI_USER}{r['member'].display_name}{ANSI_RESET} ({r['roll']})"
        f"{f' (TIE BREAKER: {r['tiebreaker_roll']})' if 'tiebreaker_roll' in r else ''}"
        for i, r in enumerate(session["rolls"], 1)
    )
    roll_order_section = (
        f"```ansi\n{ANSI_HEADER}🔢 Final Roll Order 🔢{ANSI_RESET}\n==================================\n{roll_order_body}\n```"
    )

    # --- Final Assigned Items Section ---
    assigned_items_map = {roll_info["member"].id: [] for roll_info in session["rolls"]}
    for item in session["items"]:
        if item["assigned_to"]:
            assigned_items_map[item["assigned_to"]].append(item["name"])
    
    assigned_items_body = ""
    for i, roll_info in enumerate(session["rolls"], 1):
        if i > 1: assigned_items_body += "\n"
        assigned_items_body += f"{NUMBER_EMOJIS.get(i, f'#{i}')} {ANSI_USER}{roll_info['member'].display_name}{ANSI_RESET}\n"
        if assigned_items_map[roll_info["member"].id]:
            assigned_items_body += "".join(f"   - {name}\n" for name in assigned_items_map[roll_info["member"].id])
        else:
            assigned_items_body += "   - N/A\n"
            
    assigned_items_section = (
        f"```ansi\n{ANSI_HEADER}✅ Final Assigned Items ✅{ANSI_RESET}\n==================================\n{assigned_items_body}```"
    )
    
    # --- Unclaimed Items Section ---
    unclaimed_section = ""
    unclaimed_items = [item for item in session["items"] if not item["assigned_to"]]
    if unclaimed_items:
        unclaimed_body = "\n".join(f"{item['display_number']}. {item['name']}" for item in unclaimed_items)
        unclaimed_section = (
            f"```ansi\n{ANSI_HEADER}❌ Unclaimed Items ❌{ANSI_RESET}\n==================================\n{unclaimed_body}\n```"
        )

    return f"{header}{roll_order_section}\n{assigned_items_section}\n{unclaimed_section}"


# ===================================================================================================
# DYNAMIC UI VIEWS
# ===================================================================================================

class ControlPanelView(nextcord.ui.View):
    """A persistent view for the control panel (2/3) with pre-start actions."""
    def __init__(self, session_id):
        super().__init__(timeout=None) # Timeout is handled by the session logic.
        self.session_id = session_id
        self._add_components()

    def _add_components(self):
        """Adds components for the pre-start phase."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        member_options = [
            nextcord.SelectOption(label=r['member'].display_name, value=str(r['member'].id),
                                  default=str(r['member'].id) in (session.get("members_to_remove") or []))
            for r in session["rolls"] if r['member'].id != session["invoker_id"]
        ]
        if member_options:
            self.add_item(self.RemoveSelect(member_options))
        
        self.add_item(self.RemoveConfirmButton(disabled=not session.get("members_to_remove")))
        self.add_item(self.StartButton())

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if session and interaction.user.id == session["invoker_id"]:
            return True
        await interaction.response.send_message("🛡️ Only the Loot Manager can use these controls.", ephemeral=True)
        return False

    # --- Component Classes and Callbacks for the Control Panel ---

    class RemoveSelect(nextcord.ui.Select):
        def __init__(self, options):
            super().__init__(placeholder="Select participants to remove...", options=options, row=0,
                             custom_id="remove_select", min_values=0, max_values=len(options))
        async def callback(self, interaction: nextcord.Interaction):
            session = loot_sessions.get(self.view.session_id)
            session["members_to_remove"] = self.values
            self.view.clear_items()
            self.view._add_components()
            await interaction.response.edit_message(view=self.view)

    class RemoveConfirmButton(nextcord.ui.Button):
        def __init__(self, disabled):
            super().__init__(label="Remove Selected", style=nextcord.ButtonStyle.danger, emoji="✖️",
                             custom_id="remove_confirm_button", disabled=disabled, row=1)
        async def callback(self, interaction: nextcord.Interaction):
            await interaction.response.defer()
            session = loot_sessions.get(self.view.session_id)
            ids_to_remove = {int(id_str) for id_str in (session.get("members_to_remove") or [])}
            if ids_to_remove:
                session["rolls"] = [r for r in session["rolls"] if r["member"].id not in ids_to_remove]
                session["members_to_remove"] = None
            await _update_all_messages(self.view.session_id, interaction)

    class StartButton(nextcord.ui.Button):
        def __init__(self):
            super().__init__(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success,
                             custom_id="start_button", row=1)
        async def callback(self, interaction: nextcord.Interaction):
            await interaction.response.defer()
            session = loot_sessions.get(self.view.session_id)
            session.update({"members_to_remove": None, "last_action": None, "selected_items": None, "current_turn": 0})
            await _update_all_messages(self.view.session_id, interaction)


class LootControlView(nextcord.ui.View):
    """A temporary view for an active looting turn, created for the UI message (3/3)."""
    def __init__(self, session_id):
        super().__init__(timeout=SESSION_TIMEOUT_SECONDS)
        self.session_id = session_id
        self._add_components()

    def _add_components(self):
        """Adds the necessary components for a loot turn, assigning explicit rows."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        next_row = 0
        available_items = [(i, item) for i, item in enumerate(session["items"]) if not item["assigned_to"]]
        if available_items:
            for i, chunk in enumerate(available_items[i:i + 25] for i in range(0, len(available_items), 25)):
                if chunk:
                    self.add_item(self.ItemSelect(chunk, i, session.get("selected_items") or [], row=next_row))
                    next_row += 1
        
        self.add_item(self.AssignButton(disabled=not session.get("selected_items"), row=next_row))
        self.add_item(self.SkipButton(row=next_row))
        self.add_item(self.UndoButton(disabled=not session.get("last_action"), row=next_row))

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False

        if interaction.user.id == session["invoker_id"]:
            return True

        if "undo_button" in interaction.data.get("custom_id", ""):
            await interaction.response.send_message("🛡️ Only the Loot Manager can use the Undo button.", ephemeral=True)
            return False

        is_picking_turn = 0 <= session["current_turn"] < len(session["rolls"])
        if is_picking_turn and interaction.user.id == session["rolls"][session["current_turn"]]["member"].id:
            return True
        
        if is_picking_turn:
            picker_mention = session["rolls"][session["current_turn"]]["member"].mention
            error_message = f"🛡️ It's not your turn! Only {session['invoker'].mention} or {picker_mention} can interact."
        else:
            error_message = f"🛡️ Only {session['invoker'].mention} can manage the session."
        await interaction.response.send_message(error_message, ephemeral=True)
        return False

    # --- Component Classes and Callbacks for the Loot Control View ---

    class ItemSelect(nextcord.ui.Select):
        def __init__(self, chunk, chunk_index, selected_values, row=0):
            options = [
                nextcord.SelectOption(
                    label=(f"{item['display_number']}. {item['name']}"[:97] + '...') if len(f"{item['display_number']}. {item['name']}") > 100 else f"{item['display_number']}. {item['name']}",
                    value=str(original_index), default=str(original_index) in selected_values
                ) for original_index, item in chunk
            ]
            placeholder = "Choose one or more items to claim..."
            if len(options) > 1 and (chunk[0][1]['display_number'] != chunk[-1][1]['display_number']):
                placeholder = f"Choose items ({chunk[0][1]['display_number']}-{chunk[-1][1]['display_number']})..."
            
            super().__init__(placeholder=placeholder, options=options, custom_id=f"item_select_{chunk_index}", min_values=0, max_values=len(options), row=row)
        
        async def callback(self, interaction: nextcord.Interaction):
            session = loot_sessions.get(self.view.session_id)
            dropdown_index = int(self.custom_id.split("_")[-1])
            available_items = [(i, item) for i, item in enumerate(session["items"]) if not item["assigned_to"]]
            item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]
            
            possible_values = {str(index) for index, item in item_chunks[dropdown_index]}
            current_selection = set(session.get("selected_items") or [])
            current_selection -= possible_values
            current_selection.update(self.values)
            session["selected_items"] = list(current_selection)
            
            self.view.clear_items()
            self.view._add_components()
            await interaction.response.edit_message(view=self.view)

    class AssignButton(nextcord.ui.Button):
        def __init__(self, disabled, row=0):
            super().__init__(label="Assign Selected", style=nextcord.ButtonStyle.green, emoji="✅", custom_id="assign_button", disabled=disabled, row=row)
        async def callback(self, interaction: nextcord.Interaction):
            await interaction.response.defer()
            session = loot_sessions.get(self.view.session_id)
            selected_indices = session.get("selected_items")
            
            session["last_action"] = {
                "turn": session["current_turn"], "round": session["round"], "direction": session["direction"],
                "assigned_indices": [int(i) for i in selected_indices] if selected_indices else []
            }
            
            if selected_indices:
                picker_id = session["rolls"][session["current_turn"]]["member"].id
                for index_str in selected_indices:
                    session["items"][int(index_str)]["assigned_to"] = picker_id
            
            session["selected_items"] = None
            _advance_turn_snake(session)
            await _update_all_messages(self.view.session_id, interaction)

    class SkipButton(nextcord.ui.Button):
        def __init__(self, row=0):
            super().__init__(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button", row=row)
        async def callback(self, interaction: nextcord.Interaction):
            await interaction.response.defer()
            session = loot_sessions.get(self.view.session_id)
            session["last_action"] = {
                "turn": session["current_turn"], "round": session["round"], "direction": session["direction"],
                "assigned_indices": []
            }
            session["selected_items"] = None
            _advance_turn_snake(session)
            await _update_all_messages(self.view.session_id, interaction)

    class UndoButton(nextcord.ui.Button):
        def __init__(self, disabled, row=0):
            super().__init__(label="Undo", style=nextcord.ButtonStyle.secondary, emoji="↩️", custom_id="undo_button", disabled=disabled, row=row)
        async def callback(self, interaction: nextcord.Interaction):
            await interaction.response.defer()
            session = loot_sessions.get(self.view.session_id)
            last_action = session.get("last_action")
            if not last_action:
                return await interaction.followup.send("❌ There is nothing to undo.", ephemeral=True)

            for index in last_action.get("assigned_indices", []):
                if index < len(session["items"]):
                    session["items"][index]["assigned_to"] = None
            
            session.update({k: v for k, v in last_action.items() if k != "assigned_indices"})
            session.update({"last_action": None, "selected_items": None})
            await _update_all_messages(self.view.session_id, interaction)


# ===================================================================================================
# CORE LOGIC & HELPER FUNCTIONS
# ===================================================================================================

def _advance_turn_snake(session):
    """Calculates the next turn in a "snake draft" order (1->N, N->1)."""
    if not any(not item["assigned_to"] for item in session["items"]):
        session["current_turn"] = len(session["rolls"]) # Signal the end of the session.
        return

    potential_next_turn = session["current_turn"] + session["direction"]
    if 0 <= potential_next_turn < len(session["rolls"]):
        session["current_turn"] = potential_next_turn
    else:
        # Reverse direction at the end of a row.
        session["direction"] *= -1
        session["round"] += 1
        session["current_turn"] += session["direction"]


async def _update_all_messages(session_id: int, interaction: nextcord.Interaction):
    """
    Central hub for refreshing messages. This function is heavily optimized for speed.
    It creates the new UI message FIRST, then concurrently updates all other messages in the background.
    """
    session = loot_sessions.get(session_id)
    if not session: return
    
    channel = bot.get_channel(session["channel_id"])
    
    # --- Create the new UI message FIRST for a snappy user experience ---
    new_ui_msg = None
    if not any(not item["assigned_to"] for item in session["items"]):
        pass # All items are gone, so we don't need a new UI.
    else:
        picker = session["rolls"][session["current_turn"]]["member"]
        content = f"🎁 **It is {picker.mention}'s turn to pick!**"
        view = LootControlView(session_id)
        new_ui_msg = await channel.send(content, view=view)

    # --- Prepare and run all background tasks concurrently ---
    tasks = []
    old_ui_message_id = session.get("ui_message_id")
    
    try:
        loot_list_msg = await channel.fetch_message(session["loot_list_message_id"])
        control_panel_msg = await channel.fetch_message(session["control_panel_message_id"])
        
        tasks.append(loot_list_msg.edit(content=build_loot_list_message(session)))
        tasks.append(control_panel_msg.edit(content=build_control_panel_message(session), view=ControlPanelView(session_id)))
        
        if old_ui_message_id:
            try:
                old_ui_msg = await channel.fetch_message(old_ui_message_id)
                tasks.append(old_ui_msg.delete())
            except (nextcord.NotFound, nextcord.Forbidden):
                pass

    except (nextcord.NotFound, nextcord.Forbidden):
        return loot_sessions.pop(session_id, None)

    await asyncio.gather(*tasks, return_exceptions=True)

    # Now that background tasks are done, update the session with the new UI message ID.
    if new_ui_msg:
        session["ui_message_id"] = new_ui_msg.id

    # --- Handle Session Completion ---
    if not any(not item["assigned_to"] for item in session["items"]):
        final_summary = build_final_summary_message(session, timed_out=False)
        await control_panel_msg.edit(content=final_summary, view=None)
        try: 
            await loot_list_msg.delete()
        except (nextcord.NotFound, nextcord.Forbidden): pass
        return loot_sessions.pop(session_id, None)


# ===================================================================================================
# MODAL POP-UP FOR LOOT SETUP
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
    """A pop-up form for the user to paste the list of loot items."""
    def __init__(self):
        super().__init__("RNGenie Loot Manager")
        self.loot_items = nextcord.ui.TextInput(
            label="List Items Below (One Per Line)", 
            placeholder="Type your items here\nExample: 2x Health Potion", 
            required=True, 
            style=nextcord.TextInputStyle.paragraph,
            max_length=1000
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        """This function is executed after the user submits the modal."""
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ You must be in a voice channel to set up a loot roll.", ephemeral=True)
        
        members_in_channel = list(interaction.user.voice.channel.members)
        
        if not members_in_channel:
            return await interaction.followup.send("❌ I could not find anyone in your voice channel.", ephemeral=True)
        if len(members_in_channel) > 20:
            return await interaction.followup.send(f"❌ Too many users in VC ({len(members_in_channel)})! Max is 20.", ephemeral=True)

        # --- Tie-Breaker Rolling Logic ---
        random.shuffle(members_in_channel)
        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members_in_channel]

        rolls.sort(key=lambda x: x['roll'])
        processed_rolls = []
        for _, group in groupby(rolls, key=lambda x: x['roll']):
            group_list = list(group)
            if len(group_list) > 1:
                tie_rolls = list(range(1, len(group_list) + 1))
                random.shuffle(tie_rolls)
                for i, member_dict in enumerate(group_list):
                    member_dict['tiebreaker_roll'] = tie_rolls[i]
            processed_rolls.extend(group_list)

        processed_rolls.sort(key=lambda x: x.get('tiebreaker_roll', 0))
        processed_rolls.sort(key=lambda x: x['roll'], reverse=True)
        rolls = processed_rolls
        
        # --- Item Parsing Logic ---
        item_names = []
        for line in self.loot_items.value.split('\n'):
            stripped_line = line.strip()
            if not stripped_line: continue

            match = re.match(r"(\d+)[xX]\s*(.*)", stripped_line)
            if match:
                try:
                    count = int(match.group(1))
                    name = match.group(2).strip()
                    if name: item_names.extend([name] * count)
                except (ValueError, IndexError):
                    item_names.append(stripped_line)
            else:
                item_names.append(stripped_line)
        
        items_data = [{"name": name, "assigned_to": None, "display_number": i} for i, name in enumerate(item_names, 1)]
        if not items_data:
            return await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)
        
        # --- Message & Session Creation ---
        loot_list_message = await interaction.followup.send("`Initializing Loot List (1/3)...`", wait=True)
        control_panel_message = await interaction.channel.send("`Initializing Control Panel (2/3)...`")
        
        session_id = control_panel_message.id
        session = { 
            "rolls": rolls, "items": items_data, "current_turn": -1, "invoker_id": interaction.user.id,
            "invoker": interaction.user, "selected_items": None, "round": 0, "direction": 1,
            "channel_id": interaction.channel.id, "loot_list_message_id": loot_list_message.id,
            "control_panel_message_id": control_panel_message.id, "ui_message_id": None, "last_action": None
        }
        loot_sessions[session_id] = session
        
        start_content = f"🎁 **Loot distribution is ready!**\n\n✍️ **{interaction.user.mention} can remove participants or click below to begin.**"
        start_view = ControlPanelView(session_id)
        ui_message = await interaction.channel.send(start_content, view=start_view)
        session["ui_message_id"] = ui_message.id

        await asyncio.gather(
            loot_list_message.edit(content=build_loot_list_message(session)),
            control_panel_message.edit(content=build_control_panel_message(session))
        )


# ===================================================================================================
# SLASH COMMAND
# ===================================================================================================

@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    """The entry point command that users will type."""
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You need to be in a voice channel to start a loot roll!", ephemeral=True)
        return
    await interaction.response.send_modal(LootModal())


# ===================================================================================================
# BOT EVENTS
# ===================================================================================================

@bot.event
async def on_ready():
    """Event that fires when the bot successfully connects to Discord."""
    print(f'Logged in as {bot.user}')
    print('RNGenie is ready.')
    print('------')

@bot.event
async def on_application_command_error(interaction: nextcord.Interaction, error: Exception):
    """A global error handler for all slash commands and UI interactions."""
    print(f"\n--- Unhandled exception in interaction for command '{getattr(interaction.application_command, 'name', 'unknown')}' ---")
    traceback.print_exception(type(error), error, error.__traceback__)
    print("--- End of exception report ---\n")

    if not interaction.is_expired():
        try:
            error_message = "❌ An unexpected error occurred. The developer has been notified."
            if interaction.response.is_done():
                await interaction.followup.send(error_message, ephemeral=True)
            else:
                await interaction.response.send_message(error_message, ephemeral=True)
        except nextcord.HTTPException:
            pass

# ===================================================================================================
# RUN SCRIPT
# ===================================================================================================

if __name__ == "__main__":
    load_dotenv()
    bot.run(os.getenv("DISCORD_TOKEN"))
