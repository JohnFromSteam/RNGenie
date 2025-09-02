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

def build_loot_list_message(session, timed_out=False):
    """Builds the content for the first message, which only lists remaining loot."""
    header = "**(1/2)**\n"
    remaining_items = [item for item in session["items"] if not item["assigned_to"]]
    
    # If there are items left to be looted
    if remaining_items:
        header_text = "❌ Remaining Loot Items ❌" if not timed_out else "❌ Unclaimed Items ❌"
        remaining_header = f"```ansi\n{ANSI_HEADER}{header_text}{ANSI_RESET}\n==================================\n"
        remaining_body = ""
        for item in session["items"]:
            if not item["assigned_to"]:
                remaining_body += f"{item['display_number']}. {item['name']}\n"
        remaining_footer = "==================================\n```"
        return f"{header}{remaining_header}{remaining_body}{remaining_footer}"
    
    # If all items have been assigned or the session timed out with no items left
    else:
        completion_text = "✅ All items have been assigned!"
        if timed_out and not any(item["assigned_to"] for item in session["items"]):
            completion_text = "⌛ Session timed out with no items assigned."
        
        return f"{header}```ansi\n{ANSI_HEADER}✅ Looting Complete ✅{ANSI_RESET}\n==================================\n{completion_text}\n==================================\n```"


def build_control_panel_message(session, timed_out=False):
    """Builds the content for the second message, which contains the roll order and assignments."""
    invoker = session["invoker"]
    rolls = session["rolls"]

    # --- Part 1: Header ---
    if timed_out:
        header = f"**(2/2)**\n⌛ **The loot session has timed out due to 30 minutes of inactivity!**\n\n"
    elif not any(not item["assigned_to"] for item in session["items"]):
        header = f"**(2/2)**\n✅ **All items have been assigned! Looting has concluded!**\n\n"
    else:
        header = f"**(2/2)**\n🎉 **Loot roll started by {invoker.mention}!**\n\n"

    # --- Part 2: Roll Order ---
    roll_order_header = f"```ansi\n{ANSI_HEADER}🔢 Roll Order 🔢{ANSI_RESET}\n==================================\n"
    roll_order_body = ""
    for i, r in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        roll_order_body += f"{num_emoji} {ANSI_USER}{r['member'].display_name}{ANSI_RESET} ({r['roll']})\n"
    roll_order_footer = "==================================\n```"
    roll_order_section = roll_order_header + roll_order_body + roll_order_footer

    # --- Part 3: Assigned Items ---
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

    # --- Part 4: Footer ---
    footer = ""
    if any(not item["assigned_to"] for item in session["items"]) and not timed_out:
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
            footer = f"🎁 **Loot distribution is ready!**\n\n✍️ **{invoker.mention} can remove participants or click below to begin.**"

    return f"{header}{roll_order_section}\n{distribution_section}\n{footer}"


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
        """
        Re-generates the UI components based on the current session state.
        This is called after every interaction to ensure the UI is always up-to-date.
        """
        session = loot_sessions.get(self.session_id)
        self.clear_items()
        if not session or not self._are_items_left(session): return

        if session["current_turn"] == -1:
            if session["rolls"]:
                selected_values = session.get("members_to_remove") or []
                member_options = []
                for r in session["rolls"]:
                    is_selected = str(r['member'].id) in selected_values
                    member_options.append(nextcord.SelectOption(
                        label=r['member'].display_name, value=str(r['member'].id), default=is_selected
                    ))

                self.add_item(nextcord.ui.Select(
                    placeholder="Select participants to remove...", options=member_options,
                    custom_id="remove_select", min_values=0, max_values=len(member_options)
                ))
            
            remove_button_disabled = not session.get("members_to_remove")
            self.add_item(nextcord.ui.Button(
                label="Remove Selected", style=nextcord.ButtonStyle.danger, emoji="✖️", # <-- STYLE IS NOW DANGER
                custom_id="remove_confirm_button", disabled=remove_button_disabled
            ))
            
            self.add_item(nextcord.ui.Button(
                label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="skip_button"
            ))

        else:
            is_picking_turn = 0 <= session["current_turn"] < len(session["rolls"])
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
        
        for child in self.children:
            if hasattr(child, 'custom_id'):
                if child.custom_id == "assign_button": child.callback = self.on_assign
                if child.custom_id == "skip_button": child.callback = self.on_skip
                if "item_select" in child.custom_id: child.callback = self.on_item_select
                if child.custom_id == "remove_select": child.callback = self.on_remove_select
                if child.custom_id == "remove_confirm_button": child.callback = self.on_remove_confirm

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False

        if interaction.user.id == session["invoker_id"]:
            return True

        is_picking_turn = 0 <= session["current_turn"] < len(session["rolls"])
        if is_picking_turn:
            current_picker = session["rolls"][session["current_turn"]]["member"]
            if interaction.user.id == current_picker.id:
                return True
        
        invoker_mention = session["invoker"].mention
        if is_picking_turn:
            picker_mention = session["rolls"][session["current_turn"]]["member"].mention
            error_message = f"🛡️ It's not your turn! Only the Loot Master ({invoker_mention}) or the current picker ({picker_mention}) can interact."
        else:
            error_message = f"🛡️ Only the Loot Master ({invoker_mention}) can manage participants or start the assignment."
        
        await interaction.response.send_message(error_message, ephemeral=True)
        return False

    async def update_messages(self, interaction: nextcord.Interaction):
        """A central method to refresh BOTH messages after any state change."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        try:
            # Re-fetch the message objects to ensure they are current
            channel = bot.get_channel(session["channel_id"])
            loot_list_msg = await channel.fetch_message(session["loot_list_message_id"])
            control_panel_msg = await channel.fetch_message(self.session_id)
        except (nextcord.NotFound, nextcord.Forbidden):
            loot_sessions.pop(self.session_id, None)
            return
        
        loot_list_content = build_loot_list_message(session)
        control_panel_content = build_control_panel_message(session)
        self.update_components()
        
        # If no items are left, end the session and remove the interactive components.
        if not self._are_items_left(session) and session["current_turn"] != -1:
            await loot_list_msg.edit(content=loot_list_content)
            await control_panel_msg.edit(content=control_panel_content, view=None)
            loot_sessions.pop(self.session_id, None)
        else:
            await loot_list_msg.edit(content=loot_list_content)
            await control_panel_msg.edit(content=control_panel_content, view=self)

    async def on_timeout(self):
        session = loot_sessions.get(self.session_id)
        if not session: return
        try:
            channel = bot.get_channel(session["channel_id"])
            if channel:
                loot_list_msg = await channel.fetch_message(session["loot_list_message_id"])
                control_panel_msg = await channel.fetch_message(self.session_id)
                
                final_loot_list = build_loot_list_message(session, timed_out=True)
                final_control_panel = build_control_panel_message(session, timed_out=True)

                await loot_list_msg.edit(content=final_loot_list)
                await control_panel_msg.edit(content=final_control_panel, view=None)
        except (nextcord.NotFound, nextcord.Forbidden):
            pass
        finally:
            loot_sessions.pop(self.session_id, None)

    async def on_remove_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return
        session["members_to_remove"] = interaction.data["values"]
        self.update_components()
        await interaction.response.edit_message(view=self)

    async def on_remove_confirm(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return
        ids_to_remove = set(int(id_str) for id_str in session.get("members_to_remove", []))
        if ids_to_remove:
            session["rolls"] = [r for r in session["rolls"] if r["member"].id not in ids_to_remove]
            session["members_to_remove"] = None
        await self.update_messages(interaction)

    async def on_item_select(self, interaction: nextcord.Interaction):
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
        session = loot_sessions.get(self.session_id)
        if not session: return
        selected_indices = session.get("selected_items")
        current_picker_id = session["rolls"][session["current_turn"]]["member"].id
        if selected_indices:
            for index_str in selected_indices:
                session["items"][int(index_str)]["assigned_to"] = current_picker_id
        session["selected_items"] = None
        self._advance_turn_snake(session)
        await self.update_messages(interaction)

    async def on_skip(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return
        session["selected_items"] = None
        if session["current_turn"] == -1:
            session["members_to_remove"] = None
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
            placeholder="Type your items here\nEach new line is considered an item", 
            required=True, 
            style=nextcord.TextInputStyle.paragraph,
            max_length=1000
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        """This function is executed after the user submits the modal."""
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

        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        rolls.sort(key=lambda x: x['roll'], reverse=True)
        
        items_data = [
            {"name": line.strip(), "assigned_to": None, "display_number": i}
            for i, line in enumerate(self.loot_items.value.split('\n'), 1)
            if line.strip()
        ]
        if not items_data:
            await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)
            return
        
        # Send two placeholder messages to get their IDs
        loot_list_message = await interaction.followup.send("`Initializing Loot List (1/2)...`", wait=True)
        control_panel_message = await interaction.channel.send("`Initializing Control Panel (2/2)...`")

        session_id = control_panel_message.id # The session is keyed by the control panel message ID

        session = { 
            "rolls": rolls, "items": items_data, "current_turn": -1, 
            "invoker_id": interaction.user.id, "invoker": interaction.user,
            "selected_items": None, "round": 0, "direction": 1,
            "just_reversed": False, "members_to_remove": None,
            "channel_id": interaction.channel.id,
            "loot_list_message_id": loot_list_message.id # Store the first message's ID
        }
        
        loot_sessions[session_id] = session
        
        # Now, edit the messages with their full initial content and the view
        loot_list_content = build_loot_list_message(session)
        control_panel_content = build_control_panel_message(session)
        
        final_view = LootControlView(session_id)
        
        await loot_list_message.edit(content=loot_list_content)
        await control_panel_message.edit(content=control_panel_content, view=final_view)


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
