# RNGenie.py
# A Discord bot for managing turn-based "snake draft" style loot distribution.
# This version uses a two-message system: one for the interactive management panel
# and a second dedicated message for the list of remaining loot.

import os
import random
import traceback
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands

# ===================================================================================================
# BOT SETUP & GLOBAL VARIABLES
# ===================================================================================================

# Define necessary intents for member presence and voice state tracking.
intents = nextcord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(intents=intents)

# In-memory dictionary to store active loot session data.
# The key is the ID of the "management" message.
loot_sessions = {}

# --- UI & Formatting Constants ---

# Emojis used for numbering the roll order list.
NUMBER_EMOJIS = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟",
    11: "1️⃣1️⃣", 12: "1️⃣2️⃣", 13: "1️⃣3️⃣", 14: "1️⃣4️⃣", 15: "1️⃣5️⃣",
    16: "1️⃣6️⃣", 17: "1️⃣7️⃣", 18: "1️⃣8️⃣", 19: "1️⃣9️⃣", 20: "2️⃣0️⃣"
}

# ANSI color codes for formatting text within code blocks.
ANSI_RESET = "\u001b[0m"
ANSI_HEADER = "\u001b[0;33m"
ANSI_USER = "\u001b[0;34m"


# ===================================================================================================
# MESSAGE BUILDER FUNCTIONS
# ===================================================================================================

def build_loot_list_message(session):
    """Constructs the content for the message that only displays remaining items."""
    remaining_items = [item for item in session["items"] if not item["assigned_to"]]

    if not remaining_items:
        return "✅ **All items have been claimed!**"

    header_text = "🎁 Remaining Loot Items 🎁"
    remaining_header = f"```ansi\n{ANSI_HEADER}{header_text}{ANSI_RESET}\n==================================\n"
    remaining_body = ""
    # Iterate through all original items to maintain consistent numbering.
    for i, item in enumerate(session["items"], 1):
        if not item["assigned_to"]:
            remaining_body += f"{i}. {item['name']}\n"
    remaining_footer = "==================================\n```"
    return remaining_header + remaining_body + remaining_footer


def build_management_message(session):
    """Constructs the content for the main message with controls and session info."""
    invoker = session["invoker"]
    rolls = session["rolls"]
    message_parts = []

    # --- Header ---
    header = f"🎉 **Loot roll started by {invoker.mention}!**\n\n"
    message_parts.append(header)

    # --- Roll Order ---
    roll_order_header = f"```ansi\n{ANSI_HEADER}🔢 Roll Order 🔢{ANSI_RESET}\n==================================\n"
    roll_order_body = ""
    for i, roll_info in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        roll_order_body += f"{num_emoji} {ANSI_USER}{roll_info['member'].display_name}{ANSI_RESET} ({roll_info['roll']})\n"
    roll_order_footer = "==================================\n```"
    message_parts.append(roll_order_header + roll_order_body + roll_order_footer)

    # --- Assigned Items Log ---
    distribution_header = f"```ansi\n{ANSI_HEADER}✅ Assigned Items Log ✅{ANSI_RESET}\n"
    distribution_body = ""
    assigned_items = {}
    for item in session["items"]:
        if item["assigned_to"]:
            assignee_id = item["assigned_to"]
            if assignee_id not in assigned_items:
                assigned_items[assignee_id] = []
            assigned_items[assignee_id].append(item["name"])

    for i, roll_info in enumerate(rolls):
        member = roll_info["member"]
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        distribution_body += f"==================================\n{num_emoji} {ANSI_USER}{member.display_name}{ANSI_RESET}\n\n"
        if member.id in assigned_items:
            for item_name in assigned_items[member.id]:
                distribution_body += f"{item_name}\n"
    distribution_footer = "==================================\n```"
    message_parts.append(distribution_header + distribution_body + distribution_footer)

    # --- Turn Indicator / Footer ---
    footer = ""
    if session["current_turn"] >= 0:
        picker = session["rolls"][session["current_turn"]]["member"]
        direction_text = "Normal Order" if session["direction"] == 1 else "Reverse Order"
        picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
        turn_text = "turn again!" if session.get("just_reversed") else "turn!"
        footer = (
            f"🔔 **Round {session['round'] + 1}** ({direction_text})\n\n"
            f"**{picker_emoji} It is {picker.mention}'s {turn_text} **\n\n"
            f"✍️ **{invoker.mention} or**\n\n"
            f"**{picker_emoji} {picker.mention} can assign the item or skip.**"
        )
    else:
        footer = (
            f"🎁 **Loot distribution is ready!**\n\n"
            f"✍️ **{invoker.mention} must click below to begin.**"
        )
    message_parts.append(footer)

    return "\n".join(message_parts)


def build_final_summary_message(session, timed_out=False):
    """Constructs the single, final message when the session has concluded."""
    message_parts = []

    # --- Header ---
    if timed_out:
        header = "⌛ **The loot session has timed out! Here is the final summary:**\n\n"
    else:
        header = "✅ **All items have been assigned! Here is the final summary:**\n\n"
    message_parts.append(header)

    # --- Roll Order ---
    roll_order_header = f"```ansi\n{ANSI_HEADER}🔢 Roll Order 🔢{ANSI_RESET}\n==================================\n"
    roll_order_body = ""
    for i, r in enumerate(session["rolls"]):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        roll_order_body += f"{num_emoji} {ANSI_USER}{r['member'].display_name}{ANSI_RESET} ({r['roll']})\n"
    roll_order_footer = "==================================\n```"
    message_parts.append(roll_order_header + roll_order_body + roll_order_footer)

    # --- Final Item Distribution ---
    distribution_header = f"```ansi\n{ANSI_HEADER}✅ Final Item Distribution ✅{ANSI_RESET}\n"
    distribution_body = ""
    assigned_items = {}
    for item in session["items"]:
        if item["assigned_to"]:
            assignee_id = item["assigned_to"]
            if assignee_id not in assigned_items: assigned_items[assignee_id] = []
            assigned_items[assignee_id].append(item["name"])

    for i, roll_info in enumerate(session["rolls"]):
        member = roll_info["member"]
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        distribution_body += f"==================================\n{num_emoji} {ANSI_USER}{member.display_name}{ANSI_RESET}\n\n"
        if member.id in assigned_items:
            for item_name in assigned_items[member.id]:
                distribution_body += f"{item_name}\n"
    distribution_footer = "==================================\n```"
    message_parts.append(distribution_header + distribution_body + distribution_footer)

    # --- Unclaimed Items (if any) ---
    unclaimed_items = [item for item in session["items"] if not item["assigned_to"]]
    if unclaimed_items:
        unclaimed_header = f"```ansi\n{ANSI_HEADER}❌ Unclaimed Items ❌{ANSI_RESET}\n==================================\n"
        unclaimed_body = ""
        for i, item in enumerate(session["items"], 1):
            if not item["assigned_to"]:
                unclaimed_body += f"{i}. {item['name']}\n"
        unclaimed_footer = "==================================\n```"
        message_parts.append(unclaimed_header + unclaimed_body + unclaimed_footer)

    return "\n".join(message_parts)


# ===================================================================================================
# DYNAMIC UI VIEW (BUTTONS & DROPDOWNS)
# ===================================================================================================

class LootControlView(nextcord.ui.View):
    """
    Manages the interactive UI for a loot session.
    This view is attached to the "management" message.
    """
    def __init__(self, session_id):
        super().__init__(timeout=1800)
        self.session_id = session_id
        self.update_components()

    def _are_items_left(self, session):
        """Checks if there are any unassigned items in the session."""
        return any(not item["assigned_to"] for item in session["items"])

    def _advance_turn_snake(self, session):
        """Calculates the next turn in a "snake draft" order."""
        session["just_reversed"] = False
        if not self._are_items_left(session):
            session["current_turn"] = len(session["rolls"]) # End the turns
            return

        num_rollers = len(session["rolls"])
        if num_rollers == 0: return

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

    def update_components(self):
        """Re-draws all UI components based on the current session state."""
        session = loot_sessions.get(self.session_id)
        self.clear_items()
        if not session or not self._are_items_left(session):
            return

        is_picking_turn = 0 <= session["current_turn"] < len(session["rolls"])

        if is_picking_turn:
            available_items = [(idx, item) for idx, item in enumerate(session["items"]) if not item["assigned_to"]]
            if available_items:
                item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]
                for i, chunk in enumerate(item_chunks):
                    options = []
                    selected_values = session.get("selected_items") or []
                    for original_index, item_dict in chunk:
                        is_selected = str(original_index) in selected_values
                        label_text = f"{original_index + 1}. {item_dict['name']}"
                        truncated_label = (label_text[:97] + '...') if len(label_text) > 100 else label_text
                        options.append(nextcord.SelectOption(label=truncated_label, value=str(original_index), default=is_selected))
                    
                    placeholder = "Choose one or more items to claim..."
                    if len(item_chunks) > 1:
                        start_num = chunk[0][0] + 1
                        end_num = chunk[-1][0] + 1
                        placeholder = f"Choose items ({start_num}-{end_num})..."

                    self.add_item(nextcord.ui.Select(placeholder=placeholder, options=options, custom_id=f"item_select_{i}", min_values=0, max_values=len(options)))
            
            assign_button_disabled = not session.get("selected_items")
            self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.green, emoji="✅", custom_id="assign_button", disabled=assign_button_disabled))
        
        if session["current_turn"] == -1:
            self.add_item(nextcord.ui.Button(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="skip_button"))
        else:
            self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button"))
        
        for child in self.children:
            if hasattr(child, 'custom_id'):
                if child.custom_id == "assign_button": child.callback = self.on_assign
                if child.custom_id == "skip_button": child.callback = self.on_skip
                if "item_select" in child.custom_id: child.callback = self.on_item_select

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        """
        Determines who can interact with the UI components.
        - The invoker (Loot Master) can use all components.
        - The person whose turn it is can use the item dropdowns and the skip button.
        - All others are denied.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False
        if interaction.user.id == session["invoker_id"]:
            return True

        is_picking_turn = 0 <= session["current_turn"] < len(session["rolls"])
        if is_picking_turn:
            picker = session["rolls"][session["current_turn"]]["member"]
            
            if interaction.user.id == picker.id:
                custom_id = interaction.data.get('custom_id', '')
                if custom_id.startswith('item_select') or custom_id == 'skip_button':
                    return True
                
        await interaction.response.send_message(
            "🛡️ Only the Loot Master may assign items. The current player may select items or skip their turn.", 
            ephemeral=True
        )
        return False

    async def update_messages(self, interaction: nextcord.Interaction):
        """Centralized method to update both the management and loot list messages."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        channel = bot.get_channel(session["channel_id"])
        if not channel:
            loot_sessions.pop(self.session_id, None)
            return

        # If no items are left, execute the session conclusion logic.
        if not self._are_items_left(session):
            await self.conclude_session(channel, session)
            return

        # Otherwise, perform a standard update of both messages.
        self.update_components()
        management_content = build_management_message(session)
        loot_list_content = build_loot_list_message(session)

        try:
            management_message = await channel.fetch_message(session["management_message_id"])
            loot_list_message = await channel.fetch_message(session["loot_list_message_id"])

            await management_message.edit(content=management_content, view=self)
            await loot_list_message.edit(content=loot_list_content)
        except (nextcord.NotFound, nextcord.Forbidden) as e:
            print(f"Error updating messages for session {self.session_id}: {e}")
            loot_sessions.pop(self.session_id, None)

    async def conclude_session(self, channel, session, timed_out=False):
        """Consolidates the two messages into one final summary and ends the session."""
        # Delete the separate loot list message.
        try:
            loot_list_message = await channel.fetch_message(session["loot_list_message_id"])
            await loot_list_message.delete()
        except (nextcord.NotFound, nextcord.Forbidden):
            pass # Message might already be gone, which is fine.

        # Edit the main management message into the final summary.
        try:
            management_message = await channel.fetch_message(session["management_message_id"])
            final_content = build_final_summary_message(session, timed_out=timed_out)
            await management_message.edit(content=final_content, view=None)
        except (nextcord.NotFound, nextcord.Forbidden) as e:
            print(f"Error concluding session {self.session_id}: {e}")
        finally:
            loot_sessions.pop(self.session_id, None)
    
    async def on_timeout(self):
        """Handles the session ending due to 30 minutes of inactivity."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        channel = bot.get_channel(session["channel_id"])
        if channel:
            await self.conclude_session(channel, session, timed_out=True)

    async def on_item_select(self, interaction: nextcord.Interaction):
        """Callback for when a user selects an item from any dropdown."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        newly_selected_values = interaction.data["values"]
        dropdown_id_parts = interaction.data["custom_id"].split("_")
        dropdown_index = int(dropdown_id_parts[-1]) if len(dropdown_id_parts) > 1 else 0
        
        available_items = [(idx, item) for idx, item in enumerate(session["items"]) if not item["assigned_to"]]
        item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]
        possible_values_in_this_dropdown = {str(index) for index, item in item_chunks[dropdown_index]}

        current_master_selection = set(session.get("selected_items", []))
        current_master_selection -= possible_values_in_this_dropdown
        current_master_selection.update(newly_selected_values)

        session["selected_items"] = list(current_master_selection)
        
        self.update_components()
        await interaction.response.edit_message(view=self)

    async def on_assign(self, interaction: nextcord.Interaction):
        """Callback for the 'Assign Selected' button."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        selected_indices = session.get("selected_items") or []
        if not selected_indices:
            await interaction.response.defer()
            return

        current_picker_id = session["rolls"][session["current_turn"]]["member"].id
        for index_str in selected_indices:
            session["items"][int(index_str)]["assigned_to"] = current_picker_id
        
        session["selected_items"] = None
        self._advance_turn_snake(session)
        await self.update_messages(interaction)

    async def on_skip(self, interaction: nextcord.Interaction):
        """Callback for the 'Skip Turn' or 'Start' button."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        session["selected_items"] = None
        self._advance_turn_snake(session)
        await self.update_messages(interaction)


# ===================================================================================================
# MODAL POP-UP FOR LOOT SETUP
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
    """A pop-up form for the user to paste the list of loot items."""
    def __init__(self):
        super().__init__("RNGenie Loot Setup!")
        self.loot_items = nextcord.ui.TextInput(
            label="List Your Loot Items Below (One Per Line)", 
            placeholder="Glimmering Mithril Tunic\nBoots of the Shadow Flame...", 
            required=True, 
            style=nextcord.TextInputStyle.paragraph,
            max_length=1000
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        """Handles the submission of the loot modal and creation of the session."""
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You must be in a voice channel to start a loot roll.", ephemeral=True)
            return
        
        voice_channel = interaction.guild.get_channel(interaction.user.voice.channel.id)
        members = [member for member in voice_channel.members]
        
        if len(members) > 20:
            await interaction.followup.send(f"❌ Too many users in the voice channel ({len(members)})! The maximum is 20.", ephemeral=True)
            return
        if not members:
            await interaction.followup.send("❌ I could not find any other users in your voice channel.", ephemeral=True)
            return

        rolls = [{"member": member, "roll": random.randint(1, 100)} for member in members]
        rolls.sort(key=lambda x: x['roll'], reverse=True)
        
        items_data = [{"name": line.strip(), "assigned_to": None} for line in self.loot_items.value.split('\n') if line.strip()]
        if not items_data:
            await interaction.followup.send("⚠️ You must enter at least one loot item.", ephemeral=True)
            return
        
        session = { 
            "rolls": rolls, "items": items_data, "current_turn": -1,
            "invoker_id": interaction.user.id, "invoker": interaction.user,
            "selected_items": None, "round": 0, "direction": 1,
            "just_reversed": False, "channel_id": interaction.channel_id
        }
        
        # Send placeholder messages first.
        loot_list_message = await interaction.followup.send("`Loading loot list...`", wait=True)
        management_message = await interaction.followup.send("`Initializing Loot Session...`", wait=True)

        # Finalize session data with message IDs.
        session_id = management_message.id
        session["management_message_id"] = management_message.id
        session["loot_list_message_id"] = loot_list_message.id
        loot_sessions[session_id] = session
        
        # Build the initial content and view.
        loot_list_content = build_loot_list_message(session)
        management_content = build_management_message(session)
        view = LootControlView(session_id)
        
        # Edit the placeholder messages with the real content.
        await loot_list_message.edit(content=loot_list_content)
        await management_message.edit(content=management_content, view=view)


# ===================================================================================================
# SLASH COMMAND DEFINITION
# ===================================================================================================

@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    """The entry point for the loot rolling feature."""
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You need to be in a voice channel to start a loot roll!", ephemeral=True)
        return
    
    await interaction.response.send_modal(LootModal())


# ===================================================================================================
# BOT EVENTS
# ===================================================================================================

@bot.event
async def on_ready():
    """Event fired when the bot has successfully connected to Discord."""
    print(f'Logged in as {bot.user}')
    print('RNGenie is ready.')
    print('------')

@bot.event
async def on_application_command_error(interaction: nextcord.Interaction, error: Exception):
    """A global error handler for all slash commands and UI interactions."""
    print(f"\n--- Unhandled exception in interaction for command '{getattr(interaction.application_command, 'name', 'N/A')}' ---")
    traceback.print_exception(type(error), error, error.__traceback__)
    print("--- End of exception report ---\n")
    
    if not interaction.is_expired():
        try:
            error_message = "❌ An unexpected error occurred. Please report this to the bot developer."
            if interaction.response.is_done():
                await interaction.followup.send(error_message, ephemeral=True)
            else:
                await interaction.response.send_message(error_message, ephemeral=True)
        except nextcord.HTTPException:
            pass


# ===================================================================================================
# SCRIPT EXECUTION
# ===================================================================================================

if __name__ == "__main__":
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("ERROR: DISCORD_TOKEN not found in environment variables or .env file.")
    else:
        bot.run(token)
