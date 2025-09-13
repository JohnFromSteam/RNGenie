# RNGenie.py
# A Discord bot for managing turn-based loot distribution in voice channels.

import os
import traceback
import random
import re
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

# A dictionary to hold the state of active loot sessions, keyed by the control panel message ID.
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
    """Builds the content for the first message (1/2), which lists remaining loot."""
    header = "**(1/2)**\n"
    if any(not item["assigned_to"] for item in session["items"]):
        remaining_body = "\n".join(
            f"{item['display_number']}. {item['name']}"
            for item in session["items"] if not item["assigned_to"]
        )
        return (
            f"{header}```ansi\n{ANSI_HEADER}❌ Remaining Loot Items ❌{ANSI_RESET}\n"
            f"==================================\n{remaining_body}\n```"
        )
    # Fallback message for when all items are gone.
    return (
        f"{header}```ansi\n{ANSI_HEADER}✅ All Items Assigned ✅{ANSI_RESET}\n"
        f"==================================\nAll items have been distributed.\n```"
    )


def build_control_panel_message(session):
    """Builds the content for the second message (2/2), the main control panel."""
    invoker = session["invoker"]
    rolls = session["rolls"]
    header = f"**(2/2)**\n\n🎉 **Loot roll started by {invoker.mention}!**\n\n"

    # --- Roll Order Section ---
    roll_order_body = ""
    for i, roll_info in enumerate(rolls, 1):
        roll_str = f"({roll_info['roll']})"
        if 'tiebreaker_roll' in roll_info:
            roll_str += f" (TIE BREAKER: {roll_info['tiebreaker_roll']})"
        roll_order_body += f"{NUMBER_EMOJIS.get(i, f'#{i}')} {ANSI_USER}{roll_info['member'].display_name}{ANSI_RESET} {roll_str}\n"
    
    roll_order_section = (
        f"```ansi\n{ANSI_HEADER}🔢 Roll Order 🔢{ANSI_RESET}\n==================================\n{roll_order_body}```"
    )

    # --- Assigned Items Section ---
    assigned_items_body = ""
    assigned_items_map = {roll_info["member"].id: [] for roll_info in rolls}
    for item in session["items"]:
        if item["assigned_to"]:
            assigned_items_map[item["assigned_to"]].append(item["name"])

    for i, roll_info in enumerate(rolls, 1):
        if i > 1: assigned_items_body += "\n"
        assigned_items_body += f"{NUMBER_EMOJIS.get(i, f'#{i}')} {ANSI_USER}{roll_info['member'].display_name}{ANSI_RESET}\n"
        if assigned_items_map[roll_info["member"].id]:
            assigned_items_body += "".join(f"   - {name}\n" for name in assigned_items_map[roll_info["member"].id])
        
    assigned_items_section = (
        f"```ansi\n{ANSI_HEADER}✅ Assigned Items ✅{ANSI_RESET}\n==================================\n{assigned_items_body}```"
    )

    # --- Footer (Turn Indicator) ---
    footer = ""
    if session["current_turn"] >= 0:
        picker = session["rolls"][session["current_turn"]]["member"]
        direction = "Normal Order" if session["direction"] == 1 else "Reverse Order"
        turn_text = "turn again!" if session.get("just_reversed") else "turn!"
        footer = (
            f"🔔 **Round {session['round'] + 1}** ({direction})\n\n"
            f"**{NUMBER_EMOJIS.get(session['current_turn'] + 1, '👉')} It is {picker.mention}'s {turn_text} **\n\n"
            f"✍️ **{picker.mention} or {invoker.mention} must select items or skip.**"
        )
    else:
        footer = f"🎁 **Loot distribution is ready!**\n\n✍️ **{invoker.mention} can remove participants or click below to begin.**"

    return f"{header}{roll_order_section}\n{assigned_items_section}\n{footer}"


def build_final_summary_message(session, timed_out=False):
    """Builds the single, merged message shown when the session ends."""
    header = "⌛ **The loot session has timed out!**\n\n" if timed_out else "✅ **All items have been assigned!**\n\n"

    # --- Final Roll Order Section ---
    roll_order_body = ""
    for i, roll_info in enumerate(session["rolls"], 1):
        roll_str = f"({roll_info['roll']})"
        if 'tiebreaker_roll' in roll_info:
            roll_str += f" (TIE BREAKER: {roll_info['tiebreaker_roll']})"
        roll_order_body += f"{NUMBER_EMOJIS.get(i, f'#{i}')} {ANSI_USER}{roll_info['member'].display_name}{ANSI_RESET} {roll_str}\n"
        
    roll_order_section = (
        f"```ansi\n{ANSI_HEADER}🔢 Final Roll Order 🔢{ANSI_RESET}\n==================================\n{roll_order_body}```"
    )

    # --- Final Assigned Items Section ---
    assigned_items_body = ""
    assigned_items_map = {roll_info["member"].id: [] for roll_info in session["rolls"]}
    for item in session["items"]:
        if item["assigned_to"]:
            assigned_items_map[item["assigned_to"]].append(item["name"])

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
# DYNAMIC UI VIEW
# ===================================================================================================

class LootControlView(nextcord.ui.View):
    """
    Manages the interactive components for a loot session using UI Kit decorators.
    """
    def __init__(self, session_id):
        super().__init__(timeout=SESSION_TIMEOUT_SECONDS)
        self.session_id = session_id
        self.update_dynamic_components()

    def update_dynamic_components(self):
        """Adds the correct UI components to the view based on the session's state."""
        session = loot_sessions.get(self.session_id)
        self.clear_items()
        if not session or not any(not item["assigned_to"] for item in session["items"]): return

        if session["current_turn"] == -1:
            # --- Pre-Loot Phase: Add participant management components ---
            member_options = [
                nextcord.SelectOption(label=r['member'].display_name, value=str(r['member'].id),
                                      default=str(r['member'].id) in (session.get("members_to_remove") or []))
                for r in session["rolls"] if r['member'].id != session["invoker_id"]
            ]
            if member_options:
                self.add_item(self.RemoveSelect(member_options))
            
            self.add_item(self.RemoveConfirmButton(disabled=not session.get("members_to_remove")))
            self.add_item(self.StartButton())
        else:
            # --- Active Looting Phase: Add item assignment components ---
            available_items = [(i, item) for i, item in enumerate(session["items"]) if not item["assigned_to"]]
            if available_items:
                # Chunk items into groups of 25 for multiple dropdowns if necessary.
                for i, chunk in enumerate(available_items[i:i + 25] for i in range(0, len(available_items), 25)):
                    self.add_item(self.ItemSelect(chunk, i, session.get("selected_items") or []))
            
            self.add_item(self.AssignButton(disabled=not session.get("selected_items")))
            self.add_item(self.SkipButton())
            self.add_item(self.UndoButton(disabled=not session.get("last_action")))

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        """Checks if the interacting user is allowed to control the UI."""
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False

        # The Loot Manager has universal control.
        if interaction.user.id == session["invoker_id"]:
            return True
            
        # The Undo button is exclusively for the Loot Manager.
        if interaction.data.get("custom_id") == "undo_button":
            await interaction.response.send_message("🛡️ Only the Loot Manager can use the Undo button.", ephemeral=True)
            return False

        # The current picker also has control during their turn.
        is_picking_turn = 0 <= session["current_turn"] < len(session["rolls"])
        if is_picking_turn and interaction.user.id == session["rolls"][session["current_turn"]]["member"].id:
            return True
        
        # If none of the above, deny permission with a helpful message.
        if is_picking_turn:
            picker_mention = session["rolls"][session["current_turn"]]["member"].mention
            error_message = f"🛡️ It's not your turn! Only {session['invoker'].mention} or {picker_mention} can interact."
        else:
            error_message = f"🛡️ Only {session['invoker'].mention} can manage participants or start the assignment."
        await interaction.response.send_message(error_message, ephemeral=True)
        return False

    async def _update_all_messages(self, interaction: nextcord.Interaction):
        """Central hub for refreshing messages, handling session completion, and sending notifications."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        try:
            channel = bot.get_channel(session["channel_id"])
            loot_list_msg = await channel.fetch_message(session["loot_list_message_id"])
            control_panel_msg = await channel.fetch_message(self.session_id)
        except (nextcord.NotFound, nextcord.Forbidden):
            return loot_sessions.pop(self.session_id, None)

        # --- Handle Session Completion ---
        if not any(not item["assigned_to"] for item in session["items"]) and session["current_turn"] != -1:
            final_content = build_final_summary_message(session, timed_out=False)
            await control_panel_msg.edit(content=final_content, view=None)
            try: await loot_list_msg.delete()
            except (nextcord.NotFound, nextcord.Forbidden): pass
            return loot_sessions.pop(self.session_id, None)

        # --- Handle Normal Update ---
        self.update_dynamic_components()
        await loot_list_msg.edit(content=build_loot_list_message(session))
        await control_panel_msg.edit(content=build_control_panel_message(session), view=self)

        # --- Send DM Notification ---
        is_active_turn = 0 <= session["current_turn"] < len(session["rolls"])
        if is_active_turn:
            picker = session["rolls"][session["current_turn"]]["member"]
            notification_content = (
                f"🔔 **It's your turn to pick in the loot session!**\n\n"
                f"Click here to jump back to the channel: {control_panel_msg.jump_url}\n\n"
                "*This message will self-destruct in 30 seconds.*"
            )
            try:
                await picker.send(notification_content, delete_after=30.0)
            except (nextcord.Forbidden, nextcord.HTTPException) as e:
                print(f"Could not send DM to {picker.display_name}. Reason: {e}")
            
    async def on_timeout(self):
        """Handles the view timing out by posting a final summary."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        try:
            channel = bot.get_channel(session["channel_id"])
            if channel:
                loot_list_msg = await channel.fetch_message(session["loot_list_message_id"])
                control_panel_msg = await channel.fetch_message(self.session_id)
                final_summary = build_final_summary_message(session, timed_out=True)
                await control_panel_msg.edit(content=final_summary, view=None)
                try: await loot_list_msg.delete()
                except (nextcord.NotFound, nextcord.Forbidden): pass
        except (nextcord.NotFound, nextcord.Forbidden): pass
        finally:
            loot_sessions.pop(self.session_id, None)

    # --- Component Callbacks defined with Decorators ---

    # Note: For dropdowns created dynamically, we can't use a decorator.
    # So we create a static class for it and add it dynamically.
    class RemoveSelect(nextcord.ui.Select):
        def __init__(self, options):
            super().__init__(placeholder="Select participants to remove...", options=options,
                             custom_id="remove_select", min_values=0, max_values=len(options))
        async def callback(self, interaction: nextcord.Interaction):
            session = loot_sessions.get(self.view.session_id)
            session["members_to_remove"] = self.values
            self.view.update_dynamic_components()
            await interaction.response.edit_message(view=self.view)

    class ItemSelect(nextcord.ui.Select):
        def __init__(self, chunk, chunk_index, selected_values):
            options = [
                nextcord.SelectOption(
                    label=(f"{item['display_number']}. {item['name']}"[:97] + '...') if len(f"{item['display_number']}. {item['name']}") > 100 else f"{item['display_number']}. {item['name']}",
                    value=str(original_index), default=str(original_index) in selected_values
                ) for original_index, item in chunk
            ]
            placeholder = "Choose items..."
            if len(options) > 1 and (chunk[0][1]['display_number'] != chunk[-1][1]['display_number']):
                placeholder = f"Choose items ({chunk[0][1]['display_number']}-{chunk[-1][1]['display_number']})..."
            
            super().__init__(placeholder=placeholder, options=options, custom_id=f"item_select_{chunk_index}", min_values=0, max_values=len(options))
        
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
            
            self.view.update_dynamic_components()
            await interaction.response.edit_message(view=self.view)

    # Note: For buttons that change state (e.g., Start/Skip), we also define them as classes.
    class RemoveConfirmButton(nextcord.ui.Button):
        def __init__(self, disabled):
            super().__init__(label="Remove Selected", style=nextcord.ButtonStyle.danger, emoji="✖️",
                             custom_id="remove_confirm_button", disabled=disabled)
        async def callback(self, interaction: nextcord.Interaction):
            await interaction.response.defer()
            session = loot_sessions.get(self.view.session_id)
            ids_to_remove = {int(id_str) for id_str in (session.get("members_to_remove") or [])}
            if ids_to_remove:
                session["rolls"] = [r for r in session["rolls"] if r["member"].id not in ids_to_remove]
                session["members_to_remove"] = None
            await self.view._update_all_messages(interaction)

    class AssignButton(nextcord.ui.Button):
        def __init__(self, disabled):
            super().__init__(label="Assign Selected", style=nextcord.ButtonStyle.green, emoji="✅",
                             custom_id="assign_button", disabled=disabled)
        async def callback(self, interaction: nextcord.Interaction):
            await interaction.response.defer()
            session = loot_sessions.get(self.view.session_id)
            selected_indices = session.get("selected_items")
            
            session["last_action"] = {
                "turn": session["current_turn"], "round": session["round"],
                "direction": session["direction"], "just_reversed": session.get("just_reversed", False),
                "assigned_indices": [int(i) for i in selected_indices] if selected_indices else []
            }
            
            if selected_indices:
                picker_id = session["rolls"][session["current_turn"]]["member"].id
                for index_str in selected_indices:
                    session["items"][int(index_str)]["assigned_to"] = picker_id
            
            session["selected_items"] = None
            self.view._advance_turn_snake(session)
            await self.view._update_all_messages(interaction)

    class StartButton(nextcord.ui.Button):
        def __init__(self):
            super().__init__(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="skip_button")
        async def callback(self, interaction: nextcord.Interaction):
            await interaction.response.defer()
            session = loot_sessions.get(self.view.session_id)
            session.update({"members_to_remove": None, "last_action": None, "selected_items": None})
            self.view._advance_turn_snake(session)
            await self.view._update_all_messages(interaction)

    class SkipButton(nextcord.ui.Button):
        def __init__(self):
            super().__init__(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button")
        async def callback(self, interaction: nextcord.Interaction):
            await interaction.response.defer()
            session = loot_sessions.get(self.view.session_id)
            session["last_action"] = {
                "turn": session["current_turn"], "round": session["round"],
                "direction": session["direction"], "just_reversed": session.get("just_reversed", False),
                "assigned_indices": []
            }
            session["selected_items"] = None
            self.view._advance_turn_snake(session)
            await self.view._update_all_messages(interaction)

    class UndoButton(nextcord.ui.Button):
        def __init__(self, disabled):
            super().__init__(label="Undo", style=nextcord.ButtonStyle.secondary, emoji="↩️",
                             custom_id="undo_button", disabled=disabled)
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
            await self.view._update_all_messages(interaction)


# ===================================================================================================
# MODAL POP-UP FOR LOOT SETUP
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
    """A pop-up form for the user to paste the list of loot items."""
    def __init__(self):
        super().__init__("RNGenie Loot Manager")
        self.loot_items = nextcord.ui.TextInput(
            label="List Items Below (One Per Line) Then Submit", 
            placeholder="Type your items here\nEach line is considered an item\nExample: 2x Health Potion or x2 Health Potion", 
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
            return await interaction.followup.send("❌ I could not find anyone in your voice channel. This is likely a permissions issue.", ephemeral=True)
        if len(members_in_channel) > 20:
            return await interaction.followup.send(f"❌ Too many users in the voice channel ({len(members_in_channel)})! The maximum is 20.", ephemeral=True)

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

            match = re.match(r"(?i)(?:(\d+)x|x(\d+))\s*(.*)", stripped_line)
            if match:
                try:
                    count_str = match.group(1) or match.group(2)
                    name = match.group(3).strip()
                    if name: item_names.extend([name] * int(count_str))
                except (ValueError, IndexError):
                    item_names.append(stripped_line)
            else:
                item_names.append(stripped_line)
        
        items_data = [{"name": name, "assigned_to": None, "display_number": i} for i, name in enumerate(item_names, 1)]
        if not items_data:
            return await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)
        
        # --- Message & Session Creation ---
        loot_list_message = await interaction.followup.send("`Initializing Loot List (1/2)...`", wait=True)
        control_panel_message = await interaction.channel.send("`Initializing Control Panel (2/2)...`")

        session = { 
            "rolls": rolls, "items": items_data, "current_turn": -1, "invoker_id": interaction.user.id,
            "invoker": interaction.user, "selected_items": None, "round": 0, "direction": 1,
            "just_reversed": False, "members_to_remove": None, "channel_id": interaction.channel.id,
            "loot_list_message_id": loot_list_message.id, "last_action": None
        }
        loot_sessions[control_panel_message.id] = session
        
        view = LootControlView(control_panel_message.id)
        await loot_list_message.edit(content=build_loot_list_message(session))
        await control_panel_message.edit(content=build_control_panel_message(session), view=view)


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
