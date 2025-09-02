# RNGenie.py
# A Discord bot for managing turn-based "snake draft" style loot distribution in voice channels.
# The user who initiates the command is the "Loot Master" and has primary control.
# The user whose turn it is can also interact with the item selection menus.

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
# The key is the message ID of the loot embed.
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
# UNIFIED MESSAGE BUILDER
# ===================================================================================================

def build_dynamic_loot_message(session, timed_out=False):
    """
    Constructs the complete Discord message content for a loot session.
    
    This function dynamically builds the message based on the current state of the session,
    including roll order, item distribution, and whose turn it is.

    Args:
        session (dict): The data dictionary for the current loot session.
        timed_out (bool): If True, displays a message indicating the session has ended due to inactivity.

    Returns:
        str: The fully formatted message content.
    """
    invoker = session["invoker"]
    rolls = session["rolls"]
    message_parts = []

    # --- Part 1: Header ---
    if timed_out:
        header = "⌛ **The loot session has timed out due to 30 minutes of inactivity!**\n\n"
    elif not any(not item["assigned_to"] for item in session["items"]):
        header = "✅ **All items have been assigned! Looting has concluded!**\n\n"
    else:
        header = f"🎉 **Loot roll started by {invoker.mention}!**\n\n"
    message_parts.append(header)

    # --- Part 2: Roll Order ---
    roll_order_header = f"```ansi\n{ANSI_HEADER}🔢 Roll Order 🔢{ANSI_RESET}\n==================================\n"
    roll_order_body = ""
    for i, r in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        roll_order_body += f"{num_emoji} {ANSI_USER}{r['member'].display_name}{ANSI_RESET} ({r['roll']})\n"
    roll_order_footer = "==================================\n```"
    message_parts.append(roll_order_header + roll_order_body + roll_order_footer)

    # --- Part 3: Assigned Items ---
    assigned_header_text = "✅ Assigned Items ✅"
    if timed_out:
        assigned_header_text = "✅ Final Assigned Items ✅"
        
    distribution_header = f"```ansi\n{ANSI_HEADER}{assigned_header_text}{ANSI_RESET}\n"
    distribution_body = ""
    assigned_items = {}
    for item in session["items"]:
        if item["assigned_to"]:
            assignee_id = item["assigned_to"]
            if assignee_id not in assigned_items: assigned_items[assignee_id] = []
            assigned_items[assignee_id].append(item["name"])

    for i, roll_info in enumerate(rolls):
        member = roll_info["member"]
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        distribution_body += f"==================================\n[{num_emoji} {ANSI_USER}{member.display_name}{ANSI_RESET}]\n\n"
        if member.id in assigned_items:
            for item_name in assigned_items[member.id]:
                distribution_body += f"{item_name}\n"
    distribution_footer = "==================================\n```"
    message_parts.append(distribution_header + distribution_body + distribution_footer)

    # --- Part 4: Remaining Items & Footer ---
    remaining_items_exist = any(not item["assigned_to"] for item in session["items"])
    if remaining_items_exist:
        header_text = "❌ Remaining Loot Items ❌" if not timed_out else "❌ Unclaimed Items ❌"
        remaining_header = f"```ansi\n{ANSI_HEADER}{header_text}{ANSI_RESET}\n==================================\n"
        remaining_body = ""
        # Iterate through all original items to maintain persistent numbering.
        for i, item in enumerate(session["items"], 1):
            if not item["assigned_to"]:
                remaining_body += f"{i}. {item['name']}\n"
        remaining_footer = "==================================\n```"
        message_parts.append(remaining_header + remaining_body + remaining_footer)
        
        # --- Part 5: Turn Indicator / Footer ---
        footer = ""
        if not timed_out:
            # Check if turns have started.
            if session["current_turn"] >= 0:
                picker = session["rolls"][session["current_turn"]]["member"]
                direction_text = "Normal Order" if session["direction"] == 1 else "Reverse Order"
                picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
                turn_text = "turn again!" if session.get("just_reversed", False) else "turn!"
                footer = (
                    f"🔔 **Round {session['round'] + 1}** ({direction_text})\n\n"
                    f"**{picker_emoji} It is {picker.mention}'s {turn_text} **\n\n"
                    f"✍️ **{invoker.mention} must click Assign or Skip.**\n"
                    f"**{picker.mention} may select items below.**"
                )
            # Display if the session is ready but not started.
            else:
                footer = (
                    f"🎁 **Loot distribution is ready!**\n\n"
                    f"✍️ **{invoker.mention} must click below to begin.**"
                )
            message_parts.append(footer)

    return "\n".join(message_parts)


# ===================================================================================================
# DYNAMIC UI VIEW (BUTTONS & DROPDOWNS)
# ===================================================================================================

class LootControlView(nextcord.ui.View):
    """
    Manages the interactive UI (buttons, dropdowns) for a loot session.
    This view handles all user interactions and updates the session state accordingly.
    The view times out after 30 minutes of inactivity.
    """
    def __init__(self, session_id):
        super().__init__(timeout=1800) 
        self.session_id = session_id
        self.update_components()

    def _are_items_left(self, session):
        """Checks if there are any unassigned items in the session."""
        return any(not item["assigned_to"] for item in session["items"])

    def _advance_turn_snake(self, session):
        """
        Calculates the next turn in a "snake draft" order.
        Order proceeds 1 -> N, then N -> 1, and repeats.
        """
        session["just_reversed"] = False
        if not self._are_items_left(session):
            session["current_turn"] = len(session["rolls"]) # End the turns
            return

        num_rollers = len(session["rolls"])
        if num_rollers == 0: return

        # Special case for the first turn starting from -1.
        if session["current_turn"] == -1:
            session["current_turn"] = 0
            return
        
        # Calculate the next position based on the current direction.
        potential_next_turn = session["current_turn"] + session["direction"]

        # If the next turn is within the bounds of the roller list, advance.
        if 0 <= potential_next_turn < num_rollers:
            session["current_turn"] = potential_next_turn
        # Otherwise, reverse direction, start a new round, and stay on the current player.
        else:
            session["direction"] *= -1
            session["round"] += 1
            session["just_reversed"] = True
            # The next turn will be the same as the current one, but with the new direction.
            # No change to session["current_turn"] is needed here.

    def update_components(self):
        """Re-draws all buttons and dropdowns based on the current session state."""
        session = loot_sessions.get(self.session_id)
        self.clear_items()
        if not session or not self._are_items_left(session):
            return

        is_picking_turn = session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"])

        # Add item selection dropdowns only during an active picking turn.
        if is_picking_turn:
            # Get a list of (original_index, item_dictionary) for unassigned items.
            available_items = [(idx, item) for idx, item in enumerate(session["items"]) if not item["assigned_to"]]
            
            if available_items:
                # Split items into chunks of 25 to fit in Discord's dropdown limit.
                item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]
                
                for i, chunk in enumerate(item_chunks):
                    options = []
                    selected_values = session.get("selected_items") or []

                    for original_index, item_dict in chunk:
                        is_selected = str(original_index) in selected_values
                        # Label uses original index+1 for persistent numbering. Value is the index itself.
                        label_text = f"{original_index + 1}. {item_dict['name']}"
                        truncated_label = (label_text[:97] + '...') if len(label_text) > 100 else label_text
                        options.append(nextcord.SelectOption(label=truncated_label, value=str(original_index), default=is_selected))
                    
                    placeholder = "Choose one or more items to claim..."
                    if len(item_chunks) > 1:
                        start_num = chunk[0][0] + 1
                        end_num = chunk[-1][0] + 1
                        placeholder = f"Choose items ({start_num}-{end_num})..."

                    self.add_item(nextcord.ui.Select(placeholder=placeholder, options=options, custom_id=f"item_select_{i}", min_values=0, max_values=len(options)))
            
            # Add the "Assign" button, disabled until items are selected.
            assign_button_disabled = not session.get("selected_items")
            self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.green, emoji="✅", custom_id="assign_button", disabled=assign_button_disabled))
        
        # Add a "Start" button before turns begin, or a "Skip" button during turns.
        if session["current_turn"] == -1:
            self.add_item(nextcord.ui.Button(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="skip_button"))
        else:
            self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button"))
        
        # Dynamically assign callbacks to the newly created components.
        for child in self.children:
            if hasattr(child, 'custom_id'):
                if child.custom_id == "assign_button": child.callback = self.on_assign
                if child.custom_id == "skip_button": child.callback = self.on_skip
                if "item_select" in child.custom_id: child.callback = self.on_item_select

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        """
        Determines who can interact with the UI components.
        - The invoker (Loot Master) can use all components.
        - The person whose turn it is can only use the item selection dropdowns.
        - All others are denied.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False

        # The invoker (Loot Master) always has full control.
        if interaction.user.id == session["invoker_id"]:
            return True

        # Check if it's currently a player's turn to pick.
        is_picking_turn = 0 <= session["current_turn"] < len(session["rolls"])
        if is_picking_turn:
            picker = session["rolls"][session["current_turn"]]["member"]
            # The current picker is allowed ONLY to use the item selection dropdowns.
            if interaction.user.id == picker.id and interaction.data.get('custom_id', '').startswith('item_select'):
                return True

        # If the check falls through, send an appropriate ephemeral message.
        await interaction.response.send_message(
            "🛡️ Only the Loot Master may assign/skip. The current player may select items.", 
            ephemeral=True
        )
        return False

    async def update_message(self, interaction: nextcord.Interaction):
        """A centralized method to rebuild the message and view, then send the edit."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        content = build_dynamic_loot_message(session)
        self.update_components()
        
        # If no items are left, end the session and remove the UI view.
        if not self._are_items_left(session):
            await interaction.message.edit(content=content, view=None)
            loot_sessions.pop(self.session_id, None)
        else:
            await interaction.message.edit(content=content, view=self)

    async def on_timeout(self):
        """Handles the session ending due to 30 minutes of inactivity."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        try:
            channel = bot.get_channel(session["channel_id"])
            if channel:
                message = await channel.fetch_message(self.session_id)
                final_content = build_dynamic_loot_message(session, timed_out=True)
                await message.edit(content=final_content, view=None)
        except (nextcord.NotFound, nextcord.Forbidden):
            # If message was deleted or we can't access it, just clean up internally.
            pass
        finally:
            loot_sessions.pop(self.session_id, None)

    async def on_item_select(self, interaction: nextcord.Interaction):
        """Callback for when a user selects an item from any dropdown."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        newly_selected_values = interaction.data["values"]
        custom_id_parts = interaction.data["custom_id"].split("_")
        dropdown_index = int(custom_id_parts[-1]) if len(custom_id_parts) > 1 else 0
        
        # Determine which item indices were possible to select in THIS specific dropdown.
        available_items = [(idx, item) for idx, item in enumerate(session["items"]) if not item["assigned_to"]]
        item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]
        possible_values_in_this_dropdown = {str(index) for index, item in item_chunks[dropdown_index]}

        # Update the master list of selected items for the session.
        # 1. Start with the existing selections.
        current_master_selection = set(session.get("selected_items") or [])
        # 2. Remove any values that could have been in this dropdown, effectively processing de-selections.
        current_master_selection -= possible_values_in_this_dropdown
        # 3. Add back the newly selected values from this interaction.
        current_master_selection.update(newly_selected_values)

        session["selected_items"] = list(current_master_selection)
        
        # Re-render the components to reflect the selection (e.g., enable 'Assign' button).
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
        
        session["selected_items"] = None # Clear selections after assigning.
        self._advance_turn_snake(session)
        await self.update_message(interaction)

    async def on_skip(self, interaction: nextcord.Interaction):
        """Callback for the 'Skip Turn' or 'Start Loot Assignment' button."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        session["selected_items"] = None # Clear any selections if a turn is skipped.
        self._advance_turn_snake(session)
        await self.update_message(interaction)


# ===================================================================================================
# MODAL POP-UP
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
    """A pop-up form for the user to paste the list of loot items."""
    def __init__(self):
        super().__init__("RNGenie Loot Setup!")
        self.loot_items = nextcord.ui.TextInput(
            label="List Your Loot Items Below (One Per Line)", 
            placeholder="Glimmering Mithril Tunic\nBoots of the Shadow Flame...", 
            required=True, 
            style=nextcord.TextInputStyle.paragraph
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        """Handles the submission of the loot modal."""
        await interaction.response.defer(ephemeral=True) # Acknowledge interaction privately

        # 1. Validate user is in a voice channel.
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You must be in a voice channel to set up a loot roll.", ephemeral=True)
            return
        
        # 2. Get all non-bot members in the user's current voice channel.
        voice_channel = interaction.guild.get_channel(interaction.user.voice.channel.id)
        members = [member for member in voice_channel.members if not member.bot]
        
        if len(members) > 20:
            await interaction.followup.send(f"❌ Too many users in the voice channel ({len(members)})! The maximum is 20.", ephemeral=True)
            return
        if not members:
            await interaction.followup.send("❌ I could not find any other users in your voice channel.", ephemeral=True)
            return

        # 3. Calculate rolls for all members and sort them descending.
        rolls = [{"member": member, "roll": random.randint(1, 100)} for member in members]
        rolls.sort(key=lambda x: x['roll'], reverse=True)
        
        # 4. Parse the loot items from the modal input.
        items_data = [{"name": line.strip(), "assigned_to": None} for line in self.loot_items.value.split('\n') if line.strip()]
        if not items_data:
            await interaction.followup.send("⚠️ You must enter at least one loot item.", ephemeral=True)
            return
        
        # 5. Create the initial session data dictionary.
        session = { 
            "rolls": rolls, 
            "items": items_data, 
            "current_turn": -1,  # -1 indicates the session hasn't started yet.
            "invoker_id": interaction.user.id, 
            "invoker": interaction.user,
            "selected_items": None, 
            "round": 0, 
            "direction": 1,      # 1 for forward, -1 for reverse.
            "just_reversed": False
        }
        
        # 6. Send an initial placeholder message.
        loot_message = await interaction.followup.send("`Initializing Loot Session...`", wait=True)
        
        # 7. Build the full message content and UI view.
        initial_content = build_dynamic_loot_message(session)
        session_id = loot_message.id
        session["channel_id"] = loot_message.channel.id
        loot_sessions[session_id] = session
        
        view = LootControlView(session_id)
        await loot_message.edit(content=initial_content, view=view)


# ===================================================================================================
# SLASH COMMAND
# ===================================================================================================

@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    """The entry point for the loot rolling feature."""
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You need to be in a voice channel to start a loot roll!", ephemeral=True)
        return
    
    # Open the modal for the user to input loot items.
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
    
    # Try to send an ephemeral error message to the user if the interaction is still valid.
    if not interaction.is_expired():
        try:
            error_message = "❌ An unexpected error occurred. Please report this to the bot developer."
            if interaction.response.is_done():
                await interaction.followup.send(error_message, ephemeral=True)
            else:
                await interaction.response.send_message(error_message, ephemeral=True)
        except nextcord.HTTPException:
            # If sending a message fails, there's nothing more we can do.
            pass


# ===================================================================================================
# RUN SCRIPT
# ===================================================================================================

if __name__ == "__main__":
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("ERROR: DISCORD_TOKEN not found in environment variables or .env file.")
    else:
        bot.run(token)
