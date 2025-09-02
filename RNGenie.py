# RNGenie.py
# A Discord bot for managing turn-based loot distribution in voice channels.

import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands
import random
import traceback

# ===================================================================================================
# BOT SETUP & GLOBAL STATE
# ===================================================================================================

# Define the specific intents required for the bot to function.
# - members: To read member information (like display names).
# - voice_states: To know who is in a voice channel.
intents = nextcord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(intents=intents)

# A dictionary to hold the state of active loot sessions, keyed by the message ID.
loot_sessions = {}

# Emojis for numbering lists in the Discord message for a clean UI.
NUMBER_EMOJIS = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟",
    11: "1️⃣1️⃣", 12: "1️⃣2️⃣", 13: "1️⃣3️⃣", 14: "1️⃣4️⃣", 15: "1️⃣5️⃣",
    16: "1️⃣6️⃣", 17: "1️⃣7️⃣", 18: "1️⃣8️⃣", 19: "1️⃣9️⃣", 20: "2️⃣0️⃣"
}

# ANSI color codes for formatting the text blocks in Discord messages.
ANSI_RESET = "\u001b[0m"
ANSI_HEADER = "\u001b[0;33m"
ANSI_USER = "\u001b[0;34m"

# ===================================================================================================
# UNIFIED MESSAGE BUILDER
# ===================================================================================================

def build_dynamic_loot_message(session, timed_out=False):
    """
    Constructs the complete Discord message content based on the current state of a loot session.
    This function generates the header, roll order, assigned items, and remaining items lists.
    """
    invoker = session["invoker"]
    rolls = session["rolls"]

    # --- Part 1: Header ---
    # The header changes based on the session's status (active, completed, or timed out).
    if timed_out:
        header = "⌛ **The loot session has timed out due to 30 minutes of inactivity!**\n\n"
    elif not any(not item["assigned_to"] for item in session["items"]):
        header = "✅ **All items have been assigned! Looting has concluded!**\n\n"
    else:
        header = f"🎉 **Loot roll started by {invoker.mention}!**\n\n"

    # --- Part 2: Roll Order ---
    # Displays the randomized roll order of participants.
    roll_order_header = f"```ansi\n{ANSI_HEADER}🔢 Roll Order 🔢{ANSI_RESET}\n==================================\n"
    roll_order_body = ""
    for i, r in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        roll_order_body += f"{num_emoji} {ANSI_USER}{r['member'].display_name}{ANSI_RESET} ({r['roll']})\n"
    roll_order_footer = "==================================\n```"
    roll_order_section = roll_order_header + roll_order_body + roll_order_footer

    # --- Part 3: Assigned Items ---
    # Groups the assigned items under the player who claimed them.
    assigned_header_text = "✅ Assigned Items ✅"
    if timed_out:
        assigned_header_text = "✅ Final Assigned Items ✅"
        
    distribution_header = f"```ansi\n{ANSI_HEADER}{assigned_header_text}{ANSI_RESET}\n"
    distribution_body = ""
    assigned_items_map = {roll_info["member"].id: [] for roll_info in rolls}
    for item in session["items"]:
        if item["assigned_to"]:
            assigned_items_map[item["assigned_to"]].append(item["name"])

    for i, roll_info in enumerate(rolls):
        member = roll_info["member"]
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        distribution_body += f"==================================\n[{num_emoji} {ANSI_USER}{member.display_name}{ANSI_RESET}]\n\n"
        if assigned_items_map[member.id]:
            for item_name in assigned_items_map[member.id]:
                distribution_body += f"{item_name}\n"
    distribution_footer = "==================================\n```"
    distribution_section = distribution_header + distribution_body + distribution_footer

    # --- Part 4: Remaining Items & Footer ---
    # Lists items still available and shows whose turn it is.
    remaining_items = [item for item in session["items"] if not item["assigned_to"]]
    remaining_section, footer = "", ""

    if remaining_items:
        header_text = "❌ Remaining Loot Items ❌" if not timed_out else "❌ Unclaimed Items ❌"
        remaining_header = f"```ansi\n{ANSI_HEADER}{header_text}{ANSI_RESET}\n==================================\n"
        remaining_body = ""
        # Iterate through all original items to display unassigned ones with their persistent numbers.
        for item in session["items"]:
            if not item["assigned_to"]:
                remaining_body += f"{item['display_number']}. {item['name']}\n"
        remaining_footer = "==================================\n```"
        remaining_section = remaining_header + remaining_body + remaining_footer
        
        # The footer displays turn-based information.
        if not timed_out:
            if session["current_turn"] >= 0:
                picker = session["rolls"][session["current_turn"]]["member"]
                direction_text = "Normal Order" if session["direction"] == 1 else "Reverse Order"
                picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
                turn_text = "turn again!" if session.get("just_reversed", False) else "turn!"
                footer = (
                    f"🔔 **Round {session['round'] + 1}** ({direction_text})\n\n"
                    f"**{picker_emoji} It is {picker.mention}'s {turn_text} **\n\n"
                    f"✍️ **{picker.mention} or {invoker.mention} must select items or skip.**"
                )
            else:
                footer = f"🎁 **Loot distribution is ready!**\n\n✍️ **{invoker.mention} must click below to begin.**"

    return f"{header}{roll_order_section}\n{distribution_section}\n{remaining_section}\n{footer}"


# ===================================================================================================
# DYNAMIC UI VIEW (BUTTONS & DROPDOWNS)
# ===================================================================================================

class LootControlView(nextcord.ui.View):
    """
    A persistent view that manages the interactive components (buttons, dropdowns)
    for a loot session. It handles user interactions and updates the session state.
    """
    def __init__(self, session_id):
        super().__init__(timeout=1800) # The view will time out after 30 minutes of inactivity.
        self.session_id = session_id
        self.update_components()

    def _are_items_left(self, session):
        """Checks if there are any unassigned items in the session."""
        return any(not item["assigned_to"] for item in session["items"])

    def _advance_turn_snake(self, session):
        """
        Calculates the next turn in a "snake draft" order.
        The order goes from 1 to N, then N back to 1, and repeats.
        """
        session["just_reversed"] = False
        if not self._are_items_left(session):
            session["current_turn"] = len(session["rolls"]) # End the session.
            return

        num_rollers = len(session["rolls"])
        if num_rollers == 0: return

        # This handles the initial "Start" button press.
        if session["current_turn"] == -1:
            session["current_turn"] = 0
            return

        potential_next_turn = session["current_turn"] + session["direction"]
        
        # If the next turn is within bounds, advance normally.
        if 0 <= potential_next_turn < num_rollers:
            session["current_turn"] = potential_next_turn
        # Otherwise, reverse direction (the "snake" part of the draft).
        else:
            session["direction"] *= -1
            session["round"] += 1
            session["just_reversed"] = True
            # The next turn will be the same as the current, but in the opposite direction.
            # No change to session["current_turn"] is needed here.

    def update_components(self):
        """
        Re-generates the UI components (buttons and dropdowns) based on the current session state.
        This is called after every interaction to ensure the UI is always up-to-date.
        """
        session = loot_sessions.get(self.session_id)
        self.clear_items()
        if not session: return

        # If the loot assignment has finished, do not add any components.
        if not self._are_items_left(session):
            return

        # ================== NEW: PRE-LOOT MANAGEMENT UI ==================
        # If the session hasn't started, show participant management tools.
        if session["current_turn"] == -1:
            if session["rolls"]: # Only show if there are members to remove.
                member_options = [
                    nextcord.SelectOption(label=r['member'].display_name, value=str(r['member'].id))
                    for r in session["rolls"]
                ]
                self.add_item(nextcord.ui.Select(
                    placeholder="Select participants to remove...",
                    options=member_options,
                    custom_id="remove_select",
                    min_values=0,
                    max_values=len(member_options)
                ))
            
            remove_button_disabled = not session.get("members_to_remove")
            self.add_item(nextcord.ui.Button(
                label="Remove Selected", style=nextcord.ButtonStyle.secondary, emoji="✖️", 
                custom_id="remove_confirm_button", disabled=remove_button_disabled
            ))
            
            self.add_item(nextcord.ui.Button(
                label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="skip_button"
            ))

        # ================== EXISTING: ITEM ASSIGNMENT UI ==================
        # If the session is active, show the item selection tools.
        else:
            is_picking_turn = session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"])
            if is_picking_turn:
                available_items = [(index, item) for index, item in enumerate(session["items"]) if not item["assigned_to"]]
                
                if available_items:
                    item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]
                    for i, chunk in enumerate(item_chunks):
                        options = []
                        selected_values = session.get("selected_items") or []
                        for original_index, item_dict in chunk:
                            is_selected = str(original_index) in selected_values
                            label_text = f"{item_dict['display_number']}. {item_dict['name']}"
                            truncated_label = (label_text[:97] + '...') if len(label_text) > 100 else label_text
                            options.append(nextcord.SelectOption(
                                label=truncated_label, value=str(original_index), default=is_selected
                            ))
                        
                        placeholder = "Choose one or more items to claim..."
                        if len(item_chunks) > 1:
                            start_num = chunk[0][1]['display_number']
                            end_num = chunk[-1][1]['display_number']
                            placeholder = f"Choose items ({start_num}-{end_num})..."

                        self.add_item(nextcord.ui.Select(
                            placeholder=placeholder, options=options, custom_id=f"item_select_{i}", 
                            min_values=0, max_values=len(options)
                        ))
                
                assign_button_disabled = not session.get("selected_items")
                self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.green, emoji="✅", custom_id="assign_button", disabled=assign_button_disabled))
            
            self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button"))
        
        # Dynamically assign callbacks to the newly created components.
        for child in self.children:
            if hasattr(child, 'custom_id'):
                if child.custom_id == "assign_button": child.callback = self.on_assign
                if child.custom_id == "skip_button": child.callback = self.on_skip
                if "item_select" in child.custom_id: child.callback = self.on_item_select
                if child.custom_id == "remove_select": child.callback = self.on_remove_select # NEW
                if child.custom_id == "remove_confirm_button": child.callback = self.on_remove_confirm # NEW


    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        """
        Checks if the interacting user is allowed to control the UI. Authorization is granted to
        the original command invoker (the Loot Master) or the user whose turn it is.
        """
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False

        # The invoker (Loot Master) always has control.
        if interaction.user.id == session["invoker_id"]:
            return True

        # Check if it's currently a player's turn to pick.
        is_picking_turn = session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"])
        if is_picking_turn:
            current_picker = session["rolls"][session["current_turn"]]["member"]
            if interaction.user.id == current_picker.id:
                return True
        
        invoker_mention = session["invoker"].mention
        if is_picking_turn:
            picker_mention = session["rolls"][session["current_turn"]]["member"].mention
            error_message = f"🛡️ It's not your turn! Only the Loot Master ({invoker_mention}) or the current picker ({picker_mention}) can interact."
        else:
            # This case handles the "Start" button and new removal buttons.
            error_message = f"🛡️ Only the Loot Master ({invoker_mention}) can manage participants or start the assignment."
        
        await interaction.response.send_message(error_message, ephemeral=True)
        return False

    async def update_message(self, interaction: nextcord.Interaction):
        """A central method to refresh the message and view after any state change."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        content = build_dynamic_loot_message(session)
        self.update_components()
        
        if not self._are_items_left(session) and session["current_turn"] != -1:
            await interaction.message.edit(content=content, view=None)
            loot_sessions.pop(self.session_id, None)
        else:
            await interaction.message.edit(content=content, view=self)

    async def on_timeout(self):
        """
        Handles the view timing out after 30 minutes of inactivity.
        It edits the message to a final state and cleans up the session data.
        """
        session = loot_sessions.get(self.session_id)
        if not session: return
        try:
            channel = bot.get_channel(session["channel_id"])
            if channel:
                message = await channel.fetch_message(self.session_id)
                final_content = build_dynamic_loot_message(session, timed_out=True)
                await message.edit(content=final_content, view=None)
        except (nextcord.NotFound, nextcord.Forbidden):
            pass
        finally:
            loot_sessions.pop(self.session_id, None)

    # ================== NEW: Participant Removal Callbacks ==================

    async def on_remove_select(self, interaction: nextcord.Interaction):
        """Callback for when the Loot Master selects users to remove."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        session["members_to_remove"] = interaction.data["values"]
        
        self.update_components()
        await interaction.response.edit_message(view=self)

    async def on_remove_confirm(self, interaction: nextcord.Interaction):
        """Callback for the 'Remove Selected' button."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        ids_to_remove = set(int(id_str) for id_str in session.get("members_to_remove", []))
        if not ids_to_remove:
            await interaction.response.defer()
            return
        
        # Filter the rolls list, keeping only members whose IDs are NOT in the removal set.
        session["rolls"] = [r for r in session["rolls"] if r["member"].id not in ids_to_remove]
        session["members_to_remove"] = None # Clear the selection.
        
        await self.update_message(interaction)

    # ================== EXISTING: Item Assignment Callbacks ==================

    async def on_item_select(self, interaction: nextcord.Interaction):
        """Callback for when a user selects one or more items from any dropdown."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        newly_selected_values = interaction.data["values"]
        dropdown_index = int(interaction.data["custom_id"].split("_")[-1])
        
        available_items = [(index, item) for index, item in enumerate(session["items"]) if not item["assigned_to"]]
        item_chunks = [available_items[i:i + 25] for i in range(0, len(available_items), 25)]
        possible_values_in_this_dropdown = {str(index) for index, item in item_chunks[dropdown_index]}

        current_master_selection = set(session.get("selected_items") or [])
        current_master_selection -= possible_values_in_this_dropdown
        current_master_selection.update(newly_selected_values)

        session["selected_items"] = list(current_master_selection)
        
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
        
        session["selected_items"] = None
        self._advance_turn_snake(session)
        await self.update_message(interaction)

    async def on_skip(self, interaction: nextcord.Interaction):
        """Callback for the 'Skip Turn' or 'Start Loot Assignment' button."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        # This button now serves two purposes. If the session hasn't started,
        # it will start it. If it has, it skips the current turn.
        session["selected_items"] = None 
        
        # If starting, we also clear any lingering removal selections.
        if session["current_turn"] == -1:
            session["members_to_remove"] = None
            
        self._advance_turn_snake(session)
        await self.update_message(interaction)


# ===================================================================================================
# MODAL POP-UP FOR LOOT SETUP
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
    """A pop-up form for the user to paste the list of loot items."""
    def __init__(self):
        super().__init__("RNGenie Loot Setup!")
        self.loot_items = nextcord.ui.TextInput(
            label="List Your Loot Items Below (One Per Line)", 
            placeholder="Type your items here\nEach new line is considered an item", 
            required=True, 
            style=nextcord.TextInputStyle.paragraph,
            max_length=1000
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        """This function is executed after the user submits the modal."""
        # Defer the response to prevent the interaction from timing out during processing.
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You must be in a voice channel to set up a loot roll.", ephemeral=True)
            return
        
        voice_channel = interaction.user.voice.channel
        members = voice_channel.members
        
        if len(members) > 20:
            await interaction.followup.send(f"❌ Too many users in the voice channel ({len(members)})! The maximum is 20.", ephemeral=True)
            return
        if not members:
            await interaction.followup.send("❌ I could not find anyone in your voice channel. This is likely a permissions issue.", ephemeral=True)
            return

        # Roll a 1-100 number for each member and sort them to create the turn order.
        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        rolls.sort(key=lambda x: x['roll'], reverse=True)
        
        # Create a list of item dictionaries. Each item gets a permanent display number.
        items_data = [
            {"name": line.strip(), "assigned_to": None, "display_number": i}
            for i, line in enumerate(self.loot_items.value.split('\n'), 1)
            if line.strip()
        ]
        if not items_data:
            await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)
            return
        
        # Construct the initial session state dictionary.
        session = {
            "rolls": rolls, "items": items_data, "current_turn": -1,
            "invoker_id": interaction.user.id, "invoker": interaction.user,
            "selected_items": None, "round": 0, "direction": 1,
            "just_reversed": False, "members_to_remove": None
        }
        
        # Send an initial message, waiting for it to be created to get its ID.
        loot_message = await interaction.followup.send("`Initializing Loot Session...`", wait=True)
        
        session_id = loot_message.id
        session["channel_id"] = loot_message.channel.id
        loot_sessions[session_id] = session
        
        # Now, edit the message with the full content and the interactive view.
        initial_content = build_dynamic_loot_message(session)
        final_view = LootControlView(session_id)
        await loot_message.edit(content=initial_content, view=final_view)


# ===================================================================================================
# SLASH COMMAND
# ===================================================================================================

@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    """The entry point command that users will type."""
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
    """Event that fires when the bot successfully connects to Discord."""
    print(f'Logged in as {bot.user}')
    print('RNGenie is ready.')
    print('------')

@bot.event
async def on_application_command_error(interaction: nextcord.Interaction, error: Exception):
    """A global error handler for all slash commands and UI interactions."""
    print(f"\n--- Unhandled exception in interaction for command '{interaction.application_command.name}' ---")
    traceback.print_exception(type(error), error, error.__traceback__)
    print("--- End of exception report ---\n")

    # Attempt to send a user-friendly error message if the interaction is still valid.
    if not interaction.is_expired():
        try:
            error_message = "❌ An unexpected error occurred. The developer has been notified via console logs."
            if interaction.response.is_done():
                await interaction.followup.send(error_message, ephemeral=True)
            else:
                await interaction.response.send_message(error_message, ephemeral=True)
        except nextcord.HTTPException:
            # This can happen if the original interaction is deleted or fails for other reasons.
            pass

# ===================================================================================================
# RUN SCRIPT
# ===================================================================================================

if __name__ == "__main__":
    load_dotenv()
    bot.run(os.getenv("DISCORD_TOKEN"))
