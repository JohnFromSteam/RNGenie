# RNGenie.py
# A Discord bot for managing turn-based loot distribution in voice channels.

import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands
import random
import traceback

# ===================================================================================================
# BOT SETUP & CONSTANTS
# ===================================================================================================

intents = nextcord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(intents=intents)
loot_sessions = {}

# Emojis used for numbering the roll order list.
NUMBER_EMOJIS = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟",
    11: "1️⃣1️⃣", 12: "1️⃣2️⃣", 13: "1️⃣3️⃣", 14: "1️⃣4️⃣", 15: "1️⃣5️⃣",
    16: "1️⃣6️⃣", 17: "1️⃣7️⃣", 18: "1️⃣8️⃣", 19: "1️⃣9️⃣", 20: "2️⃣0️⃣"
}

# ANSI color codes for formatting text in code blocks.
ANSI_RESET = "\u001b[0m"
ANSI_HEADER = "\u001b[0;33m"
ANSI_USER = "\u001b[0;34m"


# ===================================================================================================
# UNIFIED MESSAGE BUILDER HELPERS
# ===================================================================================================

def _build_roll_order_section(session):
    """Builds the 'Roll Order' part of the message."""
    rolls = session["rolls"]
    header = f"```ansi\n{ANSI_HEADER}🔢 Roll Order 🔢{ANSI_RESET}\n==================================\n"
    body = ""
    for i, r in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        body += f"{num_emoji} {ANSI_USER}{r['member'].display_name}{ANSI_RESET} ({r['roll']})\n"
    footer = "==================================\n```"
    return header + body + footer

def _build_distribution_section(session, timed_out=False):
    """Builds the 'Assigned Items' part of the message."""
    rolls = session["rolls"]
    header_text = "✅ Assigned Items ✅"
    if timed_out:
        header_text = "✅ Final Assigned Items ✅"
        
    header = f"```ansi\n{ANSI_HEADER}{header_text}{ANSI_RESET}\n"
    body = ""
    assigned_items = {}
    for item in session["items"]:
        if item["assigned_to"]:
            assignee_id = item["assigned_to"]
            if assignee_id not in assigned_items: assigned_items[assignee_id] = []
            assigned_items[assignee_id].append(item["name"])

    for i, roll_info in enumerate(rolls):
        member = roll_info["member"]
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        body += f"==================================\n{num_emoji} {ANSI_USER}{member.display_name}{ANSI_RESET}\n\n"
        if member.id in assigned_items:
            for item_name in assigned_items[member.id]:
                body += f"{item_name}\n"
    footer = "==================================\n```"
    return header + body + footer

def _build_remaining_items_section(session, timed_out=False):
    """Builds the 'Remaining Loot' part of the message, keeping original item numbers."""
    remaining_items_exist = any(not item["assigned_to"] for item in session["items"])
    if not remaining_items_exist:
        return ""

    header_text = "❌ Remaining Loot Items ❌" if not timed_out else "❌ Unclaimed Items ❌"
    header = f"```ansi\n{ANSI_HEADER}{header_text}{ANSI_RESET}\n==================================\n"
    body = ""
    # Iterate through the original items list to maintain the original numbering.
    for i, item in enumerate(session["items"], 1):
        if not item["assigned_to"]:
            body += f"{i}. {item['name']}\n"
    footer = "==================================\n```"
    return header + body + footer

def _build_footer_section(session, timed_out=False):
    """Builds the footer section indicating whose turn it is."""
    if timed_out or not any(not item["assigned_to"] for item in session["items"]):
        return ""
    
    invoker = session["invoker"]
    if session["current_turn"] >= 0:
        picker = session["rolls"][session["current_turn"]]["member"]
        direction_text = "Normal Order" if session["direction"] == 1 else "Reverse Order"
        picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
        turn_text = "turn again!" if session.get("just_reversed", False) else "turn!"
        
        # Clarify who can control the UI.
        control_text = f"✍️ **{picker.mention} or {invoker.mention} must select or skip.**"
        
        return (
            f"🔔 **Round {session['round'] + 1}** ({direction_text})\n\n"
            f"**{picker_emoji} It is {picker.mention}'s {turn_text}**\n\n"
            f"{control_text}"
        )
    else:
        return (
            f"🎁 **Loot distribution is ready!**\n\n"
            f"✍️ **{invoker.mention} must click below to begin.**"
        )

def build_dynamic_loot_message(session, timed_out=False):
    """Constructs the complete loot session message from component parts."""
    invoker = session["invoker"]

    # --- Part 1: Main Header ---
    if timed_out:
        header = "⌛ **The loot session has timed out due to 30 minutes of inactivity!**\n\n"
    elif not any(not item["assigned_to"] for item in session["items"]):
        header = "✅ **All items have been assigned! Looting has concluded!**\n\n"
    else:
        header = f"🎉 **Loot roll started by {invoker.mention}!**\n\n"

    # --- Part 2: Assemble Sections ---
    roll_order_section = _build_roll_order_section(session)
    distribution_section = _build_distribution_section(session, timed_out)
    remaining_section = _build_remaining_items_section(session, timed_out)
    footer = _build_footer_section(session, timed_out)

    return f"{header}{roll_order_section}\n{distribution_section}\n{remaining_section}\n{footer}"


# ===================================================================================================
# DYNAMIC UI VIEW (BUTTONS & DROPDOWNS)
# ===================================================================================================

class LootControlView(nextcord.ui.View):
    """
    Manages the user interface for a loot session, including buttons and item dropdowns.
    This view handles turn progression, item selection, and assignment.
    It automatically times out after 30 minutes of inactivity.
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
        The turn order proceeds normally (1 -> N), then reverses (N -> 1) at the end of each round.
        """
        session["just_reversed"] = False
        if not self._are_items_left(session):
            session["current_turn"] = len(session["rolls"]) # End the session
            return

        num_rollers = len(session["rolls"])
        if num_rollers == 0: return

        # This is the initial state before the "Start" button is clicked.
        if session["current_turn"] == -1:
            session["current_turn"] = 0
            return

        potential_next_turn = session["current_turn"] + session["direction"]

        # If the next turn is within the bounds of the roller list, advance normally.
        if 0 <= potential_next_turn < num_rollers:
            session["current_turn"] = potential_next_turn
        # Otherwise, reverse direction and start a new round.
        else:
            session["direction"] *= -1
            session["round"] += 1
            session["just_reversed"] = True
            # The next turn will be the same player again.
            session["current_turn"] += session["direction"]


    def update_components(self):
        """
        Clears and rebuilds the UI components (buttons, dropdowns) based on the current session state.
        """
        session = loot_sessions.get(self.session_id)
        self.clear_items()
        if not session or not self._are_items_left(session): return

        is_picking_turn = session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"])
        if is_picking_turn:
            # Get a list of items that are not yet assigned, preserving their original index.
            available_items = [(idx, item) for idx, item in enumerate(session["items"]) if not item["assigned_to"]]
            
            if available_items:
                # Split available items into chunks of 25 for multiple dropdowns if necessary.
                item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]
                
                for i, chunk in enumerate(item_chunks):
                    options = []
                    selected_values = session.get("selected_items") or []
                    
                    for original_index, item_dict in chunk:
                        is_selected = str(original_index) in selected_values
                        # The label uses the original item number (index + 1).
                        label_text = f"{original_index + 1}. {item_dict['name']}"
                        truncated_label = (label_text[:97] + '...') if len(label_text) > 100 else label_text
                        options.append(nextcord.SelectOption(label=truncated_label, value=str(original_index), default=is_selected))
                    
                    placeholder = "Choose one or more items to claim..."
                    if len(item_chunks) > 1:
                        # Use original item numbers in the placeholder for clarity.
                        start_num = chunk[0][0] + 1
                        end_num = chunk[-1][0] + 1
                        placeholder = f"Choose items ({start_num}-{end_num})..."

                    self.add_item(nextcord.ui.Select(placeholder=placeholder, options=options, custom_id=f"item_select_{i}", min_values=0, max_values=len(options)))
            
            assign_button_disabled = not session.get("selected_items")
            self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.green, emoji="✅", custom_id="assign_button", disabled=assign_button_disabled))
        
        # Add the appropriate action button: "Start" for the first turn, "Skip" for subsequent turns.
        if session["current_turn"] == -1:
            self.add_item(nextcord.ui.Button(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="skip_button"))
        else:
            self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button"))
        
        # Dynamically assign callbacks to the created components.
        for child in self.children:
            if hasattr(child, 'custom_id'):
                if child.custom_id == "assign_button": child.callback = self.on_assign
                if child.custom_id == "skip_button": child.callback = self.on_skip
                if "item_select" in child.custom_id: child.callback = self.on_item_select

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        """
        Determines who is allowed to interact with the UI components.
        Allows the original invoker (Loot Master) and the person whose turn it currently is.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False

        # Determine the ID of the person whose turn it is.
        current_picker_id = None
        turn_index = session.get("current_turn", -1)
        if 0 <= turn_index < len(session["rolls"]):
            current_picker_id = session["rolls"][turn_index]["member"].id

        # Check if the interacting user is either the invoker or the current picker.
        if interaction.user.id == session["invoker_id"] or interaction.user.id == current_picker_id:
            return True
        else:
            await interaction.response.send_message("🛡️ Only the Loot Master or the person whose turn it is can interact.", ephemeral=True)
            return False

    async def update_message(self, interaction: nextcord.Interaction):
        """A centralized method to refresh the message content and UI components after an action."""
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
        """Handles the session ending due to inactivity."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        try:
            channel = bot.get_channel(session["channel_id"])
            if channel:
                message = await channel.fetch_message(self.session_id)
                final_content = build_dynamic_loot_message(session, timed_out=True)
                await message.edit(content=final_content, view=None)
        except (nextcord.NotFound, nextcord.Forbidden):
            # Ignore errors if the message or channel was deleted.
            pass
        finally:
            loot_sessions.pop(self.session_id, None)

    async def on_item_select(self, interaction: nextcord.Interaction):
        """Callback for when a user selects an item from a dropdown."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        newly_selected_values = interaction.data["values"]
        dropdown_id = interaction.data["custom_id"]

        # This logic correctly handles multiple dropdowns by rebuilding the master selection list.
        # It removes all possible options from this specific dropdown, then adds back the new selections.
        all_possible_values_in_this_dropdown = {opt.value for opt in interaction.to_message_components()['components'][0]['components'] if opt.type == 3} # 3 is SelectMenu
        
        current_master_selection = set(session.get("selected_items") or [])
        current_master_selection -= all_possible_values_in_this_dropdown
        current_master_selection.update(newly_selected_values)
        
        session["selected_items"] = list(current_master_selection)
        
        # Re-render the components to enable/disable the "Assign" button.
        self.update_components()
        await interaction.response.edit_message(view=self)

    async def on_assign(self, interaction: nextcord.Interaction):
        """Callback for the 'Assign Selected' button."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        selected_indices = session.get("selected_items")
        current_picker_id = session["rolls"][session["current_turn"]]["member"].id

        if selected_indices:
            for index_str in selected_indices:
                session["items"][int(index_str)]["assigned_to"] = current_picker_id
        
        session["selected_items"] = None # Clear selection after assigning
        self._advance_turn_snake(session)
        await self.update_message(interaction)

    async def on_skip(self, interaction: nextcord.Interaction):
        """Callback for the 'Skip Turn' or 'Start Loot Assignment' button."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        session["selected_items"] = None # Clear any lingering selections
        self._advance_turn_snake(session)
        await self.update_message(interaction)


# ===================================================================================================
# MODAL POP-UP FOR LOOT ENTRY
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__("RNGenie Loot Setup!")
        self.loot_items = nextcord.ui.TextInput(
            label="List Your Loot Items Below (One Per Line)", 
            placeholder="Ancient Dragon Scale\nPattern: Robe of the Archmage\n...", 
            required=True, 
            style=nextcord.TextInputStyle.paragraph
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # --- Initial validation checks ---
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You must be in a voice channel to set up a loot roll.", ephemeral=True)
            return
        
        voice_channel = interaction.user.voice.channel
        members = voice_channel.members
        
        if len(members) > 20:
            await interaction.followup.send(f"❌ Too many users in the voice channel ({len(members)})! The maximum is 20.", ephemeral=True)
            return
        if not members:
            await interaction.followup.send("❌ I could not find anyone in your voice channel. This may be a permissions issue.", ephemeral=True)
            return
        
        items_data = [{"name": line.strip(), "assigned_to": None} for line in self.loot_items.value.split('\n') if line.strip()]
        if not items_data:
            await interaction.followup.send("⚠️ You must enter at least one item to be looted.", ephemeral=True)
            return

        # --- Session creation ---
        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        rolls.sort(key=lambda x: x['roll'], reverse=True)
        
        session = { 
            "rolls": rolls, 
            "items": items_data, 
            "current_turn": -1, # -1 indicates the session hasn't started yet
            "invoker_id": interaction.user.id, 
            "invoker": interaction.user,
            "selected_items": [], 
            "round": 0, 
            "direction": 1, # 1 for forward, -1 for reverse
            "just_reversed": False
        }
        
        # --- Send the initial message ---
        loot_message = await interaction.followup.send("`Initializing Loot Session...`", wait=True)
        
        session_id = loot_message.id
        session["channel_id"] = loot_message.channel.id
        loot_sessions[session_id] = session
        
        initial_content = build_dynamic_loot_message(session)
        final_view = LootControlView(session_id)

        await loot_message.edit(content=initial_content, view=final_view)


# ===================================================================================================
# SLASH COMMAND
# ===================================================================================================

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
    """Event that fires when the bot is logged in and ready."""
    print(f'Logged in as {bot.user}')
    print('RNGenie is ready.')
    print('------')

@bot.event
async def on_application_command_error(interaction: nextcord.Interaction, error: Exception):
    """A global error handler for all slash commands and UI interactions."""
    print(f"\n--- Unhandled exception in interaction for command '{interaction.application_command.name}' ---")
    traceback.print_exception(type(error), error, error.__traceback__)
    print("--- End of exception report ---\n")

    # Try to send an ephemeral error message to the user if the interaction is still valid.
    if not interaction.is_expired():
        try:
            error_message = "❌ An unexpected error occurred. The developer has been notified via console logs."
            if interaction.response.is_done():
                await interaction.followup.send(error_message, ephemeral=True)
            else:
                await interaction.response.send_message(error_message, ephemeral=True)
        except nextcord.HTTPException:
            # If sending a message fails, there's not much else we can do.
            pass


# ===================================================================================================
# RUN SCRIPT
# ===================================================================================================

load_dotenv()
bot.run(os.getenv("DISCORD_TOKEN"))
