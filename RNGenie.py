# RNGenie.py
# A Discord bot for managing turn-based loot distribution in voice channels.

import os
import traceback
import random
from dotenv import load_dotenv
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
# MESSAGE BUILDER FUNCTIONS
# ===================================================================================================

def build_loot_list_message(session):
    """Builds the content for the first message (1/2), which only lists remaining loot."""
    header = "**(1/2)**\n"
    remaining_items = [item for item in session["items"] if not item["assigned_to"]]
    
    if remaining_items:
        header_text = "❌ Remaining Loot Items ❌"
        remaining_header = f"```ansi\n{ANSI_HEADER}{header_text}{ANSI_RESET}\n==================================\n"
        remaining_body = ""
        for item in session["items"]:
            if not item["assigned_to"]:
                remaining_body += f"{item['display_number']}. {item['name']}\n"
        remaining_footer = "```"
        return f"{header}{remaining_header}{remaining_body}{remaining_footer}"
    
    # This state should rarely be seen, as the message is deleted upon completion,
    # but serves as a fallback.
    return f"{header}```ansi\n{ANSI_HEADER}✅ All Items Assigned ✅{ANSI_RESET}\n==================================\nAll items have been distributed.\n==================================\n```"

def build_control_panel_message(session):
    """Builds the content for the second message (2/2), the main control panel."""
    invoker = session["invoker"]
    rolls = session["rolls"]

    header = f"**(2/2)**\n\n🎉 **Loot roll started by {invoker.mention}!**\n\n"

    # --- Part 1: Roll Order ---
    roll_order_header = f"```ansi\n{ANSI_HEADER}🔢 Roll Order 🔢{ANSI_RESET}\n==================================\n"
    roll_order_body = ""
    for i, roll_info in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        roll_order_body += f"{num_emoji} {ANSI_USER}{roll_info['member'].display_name}{ANSI_RESET} ({roll_info['roll']})\n"
    roll_order_footer = "```"
    roll_order_section = roll_order_header + roll_order_body + roll_order_footer

    # --- Part 2: Assigned Items (MODIFIED) ---
    assigned_items_header = f"```ansi\n{ANSI_HEADER}✅ Assigned Items ✅{ANSI_RESET}\n==================================\n"
    assigned_items_body = ""
    assigned_items_map = {roll_info["member"].id: [] for roll_info in rolls}
    for item in session["items"]:
        if item["assigned_to"]:
            assigned_items_map[item["assigned_to"]].append(item["name"])

    # This loop no longer contains the "===" separator.
    for i, roll_info in enumerate(rolls):
        member = roll_info["member"]
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        # Add a newline for spacing between users, but not before the first one.
        if i > 0:
            assigned_items_body += "\n"
        assigned_items_body += f"{num_emoji} {ANSI_USER}{member.display_name}{ANSI_RESET}\n"
        if assigned_items_map[member.id]:
            for item_name in assigned_items_map[member.id]:
                assigned_items_body += f"- {item_name}\n"
    
    assigned_items_footer = "```"
    assigned_items_section = assigned_items_header + assigned_items_body + assigned_items_footer

    # --- Part 3: Footer (Turn Indicator) ---
    footer = ""
    if session["current_turn"] >= 0:
        picker = session["rolls"][session["current_turn"]]["member"]
        direction_text = "Normal Order" if session["direction"] == 1 else "Reverse Order"
        picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
        turn_text = "turn again!" if session.get("just_reversed", False) else "turn!"
        footer = (
            f"🔔 **Round {session['round'] + 1}** ({direction_text})\n\n"
            f"**{picker_emoji} It is {picker.mention}'s {turn_text} **\n\n"
            f"✍️ **Loot Manager {invoker.mention}\nor {picker.mention} must select items or skip.**"
        )
    else:
        footer = f"🎁 **Loot distribution is ready!**\n\n✍️ **Loot Manager {invoker.mention} can remove participants or click below to begin.**"

    return f"{header}{roll_order_section}\n{assigned_items_section}\n{footer}"

def build_final_summary_message(session, timed_out=False):
    """Builds the single, merged message shown when the session ends."""
    rolls = session["rolls"]

    # --- Part 1: Final Header ---
    if timed_out:
        header = "⌛ **The loot session has timed out! Here is the final summary:**\n\n"
    else:
        header = "✅ **All items have been assigned! Here is the final summary:**\n\n"

    # --- Part 2: Roll Order (same as control panel) ---
    roll_order_header = f"```ansi\n{ANSI_HEADER}🔢 Final Roll Order 🔢{ANSI_RESET}\n==================================\n"
    roll_order_body = ""
    for i, roll_info in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        roll_order_body += f"{num_emoji} {ANSI_USER}{roll_info['member'].display_name}{ANSI_RESET} ({roll_info['roll']})\n"
    roll_order_footer = "```"
    roll_order_section = roll_order_header + roll_order_body + roll_order_footer

    # --- Part 3: Final Assigned Items (MODIFIED) ---
    assigned_items_header = f"```ansi\n{ANSI_HEADER}✅ Final Assigned Items ✅{ANSI_RESET}\n==================================\n"
    assigned_items_body = ""
    assigned_items_map = {roll_info["member"].id: [] for roll_info in rolls}
    for item in session["items"]:
        if item["assigned_to"]:
            assigned_items_map[item["assigned_to"]].append(item["name"])

    # This loop no longer contains the "===" separator.
    for i, roll_info in enumerate(rolls):
        member = roll_info["member"]
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        # Add a newline for spacing between users, but not before the first one.
        if i > 0:
            assigned_items_body += "\n"
        assigned_items_body += f"{num_emoji} {ANSI_USER}{member.display_name}{ANSI_RESET}\n"
        if assigned_items_map[member.id]:
            for item_name in assigned_items_map[member.id]:
                assigned_items_body += f"- {item_name}\n"
        else:
            assigned_items_body += "- N/A\n"
            
    assigned_items_footer = "```"
    assigned_items_section = assigned_items_header + assigned_items_body + assigned_items_footer
    
    # --- Part 4: Unclaimed Items (the merged part) ---
    unclaimed_section = ""
    unclaimed_items = [item for item in session["items"] if not item["assigned_to"]]
    if unclaimed_items:
        unclaimed_header = f"```ansi\n{ANSI_HEADER}❌ Unclaimed Items ❌{ANSI_RESET}\n==================================\n"
        unclaimed_body = ""
        for item in unclaimed_items:
            unclaimed_body += f"{item['display_number']}. {item['name']}\n"
        unclaimed_footer = "```"
        unclaimed_section = unclaimed_header + unclaimed_body + unclaimed_footer

    return f"{header}{roll_order_section}\n{assigned_items_section}\n{unclaimed_section}"


# ===================================================================================================
# DYNAMIC UI VIEW (BUTTONS & DROPDOWNS)
# ===================================================================================================

class LootControlView(nextcord.ui.View):
    """
    Manages the interactive components (buttons, dropdowns) for a loot session.
    It handles user interactions, updates the session state, and refreshes the messages.
    """
    def __init__(self, session_id):
        super().__init__(timeout=SESSION_TIMEOUT_SECONDS)
        self.session_id = session_id
        self.update_components()

    def _are_items_left(self, session):
        """Checks if there are any unassigned items in the session."""
        return any(not item["assigned_to"] for item in session["items"])

    def _advance_turn_snake(self, session):
        """
        Calculates the next turn in a "snake draft" order (1->N, N->1).
        """
        session["just_reversed"] = False
        if not self._are_items_left(session):
            session["current_turn"] = len(session["rolls"]) # Signal to end the session.
            return

        num_rollers = len(session["rolls"])
        if num_rollers == 0: return

        # This handles the initial "Start" button press.
        if session["current_turn"] == -1:
            session["current_turn"] = 0
            return

        potential_next_turn = session["current_turn"] + session["direction"]
        
        if 0 <= potential_next_turn < num_rollers:
            session["current_turn"] = potential_next_turn
        else:
            # Reverse direction (the "snake" part of the draft).
            session["direction"] *= -1
            session["round"] += 1
            session["just_reversed"] = True

    def update_components(self):
        """Re-generates UI components based on the current session state."""
        session = loot_sessions.get(self.session_id)
        self.clear_items()
        if not session or not self._are_items_left(session): return

        # --- UI State 1: Pre-Loot (Participant Management) ---
        if session["current_turn"] == -1:
            selected_values = session.get("members_to_remove") or []
            member_options = []
            invoker_id = session["invoker_id"]
            
            for roll_info in session["rolls"]:
                if roll_info['member'].id != invoker_id:
                    is_selected = str(roll_info['member'].id) in selected_values
                    member_options.append(nextcord.SelectOption(
                        label=roll_info['member'].display_name, value=str(roll_info['member'].id), default=is_selected
                    ))

            if member_options:
                self.add_item(nextcord.ui.Select(
                    placeholder="Select participants to remove...", options=member_options,
                    custom_id="remove_select", min_values=0, max_values=len(member_options)
                ))
            
            remove_button_disabled = not session.get("members_to_remove")
            self.add_item(nextcord.ui.Button(
                label="Remove Selected", style=nextcord.ButtonStyle.danger, emoji="✖️",
                custom_id="remove_confirm_button", disabled=remove_button_disabled
            ))
            
            self.add_item(nextcord.ui.Button(
                label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="skip_button"
            ))

        # --- UI State 2: Active Looting (Item Assignment) ---
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
                            start_num, end_num = chunk[0][1]['display_number'], chunk[-1][1]['display_number']
                            placeholder = f"Choose items ({start_num}-{end_num})..."

                        self.add_item(nextcord.ui.Select(
                            placeholder=placeholder, options=options, custom_id=f"item_select_{i}", 
                            min_values=0, max_values=len(options)
                        ))
                
                assign_button_disabled = not session.get("selected_items")
                self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.green, emoji="✅", custom_id="assign_button", disabled=assign_button_disabled))
            
            self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button"))
            
            # NEW: Add the Undo button
            undo_disabled = not session.get("last_action")
            self.add_item(nextcord.ui.Button(label="Undo", style=nextcord.ButtonStyle.secondary, emoji="↩️", custom_id="undo_button", disabled=undo_disabled))
        
        # Dynamically assign callbacks to the newly created components.
        for child in self.children:
            if hasattr(child, 'custom_id'):
                if child.custom_id == "assign_button": child.callback = self.on_assign
                if child.custom_id == "skip_button": child.callback = self.on_skip
                if "item_select" in child.custom_id: child.callback = self.on_item_select
                if child.custom_id == "remove_select": child.callback = self.on_remove_select
                if child.custom_id == "remove_confirm_button": child.callback = self.on_remove_confirm
                if child.custom_id == "undo_button": child.callback = self.on_undo # NEW

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        """Checks if the interacting user is allowed to control the UI."""
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False

        # NEW: Check for the Undo button first, as it has special permissions.
        if interaction.data.get("custom_id") == "undo_button":
            if interaction.user.id == session["invoker_id"]:
                return True
            else:
                await interaction.response.send_message("🛡️ Only the Loot Manager can use the Undo button.", ephemeral=True)
                return False

        # The invoker (Loot Master) and the person whose turn it is have control for other buttons.
        if interaction.user.id == session["invoker_id"]:
            return True

        is_picking_turn = 0 <= session["current_turn"] < len(session["rolls"])
        if is_picking_turn:
            current_picker = session["rolls"][session["current_turn"]]["member"]
            if interaction.user.id == current_picker.id:
                return True
        
        # If unauthorized, send a helpful ephemeral message.
        invoker_mention = session["invoker"].mention
        if is_picking_turn:
            picker_mention = session["rolls"][session["current_turn"]]["member"].mention
            error_message = f"🛡️ It's not your turn! Only {invoker_mention} or {picker_mention} can interact."
        else:
            error_message = f"🛡️ Only {invoker_mention} can manage participants or start the assignment."
        
        await interaction.response.send_message(error_message, ephemeral=True)
        return False

    async def update_messages(self, interaction: nextcord.Interaction):
        """Refreshes both messages after a state change or merges them if the session is over."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        try:
            channel = bot.get_channel(session["channel_id"])
            loot_list_msg = await channel.fetch_message(session["loot_list_message_id"])
            control_panel_msg = await channel.fetch_message(self.session_id)
        except (nextcord.NotFound, nextcord.Forbidden):
            loot_sessions.pop(self.session_id, None)
            return

        # --- Handle Session Completion ---
        if not self._are_items_left(session) and session["current_turn"] != -1:
            final_content = build_final_summary_message(session, timed_out=False)
            await control_panel_msg.edit(content=final_content, view=None)
            try:
                await loot_list_msg.delete()
            except (nextcord.NotFound, nextcord.Forbidden):
                pass
            loot_sessions.pop(self.session_id, None)

        # --- Handle Normal Update ---
        else:
            loot_list_content = build_loot_list_message(session)
            control_panel_content = build_control_panel_message(session)
            self.update_components()
            await loot_list_msg.edit(content=loot_list_content)
            await control_panel_msg.edit(content=control_panel_content, view=self)

    async def on_timeout(self):
        """Handles the view timing out, merging messages into a final summary."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        try:
            channel = bot.get_channel(session["channel_id"])
            if channel:
                loot_list_msg = await channel.fetch_message(session["loot_list_message_id"])
                control_panel_msg = await channel.fetch_message(self.session_id)
                final_summary = build_final_summary_message(session, timed_out=True)
                await control_panel_msg.edit(content=final_summary, view=None)
                try:
                    await loot_list_msg.delete()
                except (nextcord.NotFound, nextcord.Forbidden):
                    pass
        except (nextcord.NotFound, nextcord.Forbidden):
            pass
        finally:
            loot_sessions.pop(self.session_id, None)

    async def on_remove_select(self, interaction: nextcord.Interaction):
        """Callback when the Loot Master selects users to remove."""
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
        if ids_to_remove:
            session["rolls"] = [r for r in session["rolls"] if r["member"].id not in ids_to_remove]
            session["members_to_remove"] = None
        await self.update_messages(interaction)

    async def on_item_select(self, interaction: nextcord.Interaction):
        """Callback when a user selects one or more items from any dropdown."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        newly_selected_values = interaction.data["values"]
        dropdown_id = interaction.data["custom_id"]
        dropdown_index = int(dropdown_id.split("_")[-1])
        
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
        
        # MODIFIED: Record the state BEFORE changing it.
        session["last_action"] = {
            "turn": session["current_turn"],
            "round": session["round"],
            "direction": session["direction"],
            "just_reversed": session.get("just_reversed", False),
            "assigned_indices": [int(i) for i in selected_indices] if selected_indices else []
        }
        
        if selected_indices:
            for index_str in selected_indices:
                session["items"][int(index_str)]["assigned_to"] = current_picker_id
        
        session["selected_items"] = None
        self._advance_turn_snake(session)
        await self.update_messages(interaction)

    async def on_skip(self, interaction: nextcord.Interaction):
        """Callback for the 'Skip Turn' or 'Start Loot Assignment' button."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        # MODIFIED: Record the state BEFORE changing it.
        # Only record state if the session is active (not the initial "start" press).
        if session["current_turn"] != -1:
            session["last_action"] = {
                "turn": session["current_turn"],
                "round": session["round"],
                "direction": session["direction"],
                "just_reversed": session.get("just_reversed", False),
                "assigned_indices": [] # A skip assigns no items.
            }

        session["selected_items"] = None
        if session["current_turn"] == -1:
            session["members_to_remove"] = None
            session["last_action"] = None # No "last action" before the first turn.
            
        self._advance_turn_snake(session)
        await self.update_messages(interaction)

    async def on_undo(self, interaction: nextcord.Interaction):
        """Callback for the 'Undo' button. Reverts the last assignment or skip."""
        session = loot_sessions.get(self.session_id)
        if not session: return

        last_action = session.get("last_action")
        if not last_action:
            # This should not happen if the button is disabled, but as a safeguard:
            await interaction.response.send_message("❌ There is nothing to undo.", ephemeral=True)
            return

        # Un-assign any items from the last action
        indices_to_unassign = last_action.get("assigned_indices", [])
        for index in indices_to_unassign:
            # Check if the item still exists and is assigned to the correct person
            if index < len(session["items"]):
                session["items"][index]["assigned_to"] = None

        # Restore the previous turn state
        session["current_turn"] = last_action["turn"]
        session["round"] = last_action["round"]
        session["direction"] = last_action["direction"]
        session["just_reversed"] = last_action["just_reversed"]
        
        # Clear the last action so you can't undo the same thing twice
        session["last_action"] = None
        session["selected_items"] = None # Clear any selections
        
        await self.update_messages(interaction)


# ===================================================================================================
# MODAL POP-UP FOR LOOT SETUP
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
    """A pop-up form for the user to paste the list of loot items."""
    def __init__(self):
        super().__init__("RNGenie Loot Manager")
        self.loot_items = nextcord.ui.TextInput(
            label="List Items Below (One Per Line) Then Submit", 
            placeholder="Type your items here\nEach line is considered an item", 
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
        
        members_in_channel = interaction.user.voice.channel.members
        
        if len(members_in_channel) > 20:
            await interaction.followup.send(f"❌ Too many users in the voice channel ({len(members_in_channel)})! The maximum is 20.", ephemeral=True)
            return
        if not members_in_channel:
            await interaction.followup.send("❌ I could not find anyone in your voice channel. This is likely a permissions issue.", ephemeral=True)
            return

        # Roll a 1-100 number for each member and sort them to create the turn order.
        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members_in_channel]
        rolls.sort(key=lambda x: x['roll'], reverse=True)
        
        # First, filter out empty lines to get a clean list of item names.
        item_names = [line.strip() for line in self.loot_items.value.split('\n') if line.strip()]
        
        # Then, enumerate the clean list to create the final data structure with sequential numbers.
        items_data = [
            {"name": name, "assigned_to": None, "display_number": i}
            for i, name in enumerate(item_names, 1)
        ]
        if not items_data:
            await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)
            return
        
        # Send two placeholder messages to get their IDs.
        loot_list_message = await interaction.followup.send("`Initializing Loot List (1/2)...`", wait=True)
        control_panel_message = await interaction.channel.send("`Initializing Control Panel (2/2)...`")

        session_id = control_panel_message.id # The session is keyed by the control panel message ID.

        # Construct the initial session state dictionary.
        session = { 
            "rolls": rolls,                     # List of {"member", "roll"} sorted by roll.
            "items": items_data,                # List of {"name", "assigned_to", "display_number"}.
            "current_turn": -1,                 # Index of the current picker in the rolls list. -1 is pre-start.
            "invoker_id": interaction.user.id,  # The Loot Master's ID.
            "invoker": interaction.user,        # The Loot Master's member object.
            "selected_items": None,             # A list of original item indices selected in the dropdown.
            "round": 0,                         # The current loot round.
            "direction": 1,                     # The direction of the snake draft (1 or -1).
            "just_reversed": False,             # Flag for messaging if the turn order just reversed.
            "members_to_remove": None,          # A list of member IDs selected for removal.
            "channel_id": interaction.channel.id, # The channel where the session is active.
            "loot_list_message_id": loot_list_message.id, # The ID of the separate loot list message.
            "last_action": None                 # NEW: Stores the state before the last action for the undo feature.
        }
        loot_sessions[session_id] = session
        
        # Now, edit the messages with their full initial content and the interactive view.
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
