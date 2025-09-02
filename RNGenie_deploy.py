# RNGenie.py
# A Discord bot for managing turn-based loot distribution in voice channels.

import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands
import random
import traceback

# ===================================================================================================
# BOT SETUP
# ===================================================================================================

# Define the necessary intents for the bot to see members and their voice states.
intents = nextcord.Intents.default()
intents.members = True
intents.voice_states = True

# Initialize the bot and a dictionary to hold active loot sessions.
bot = commands.Bot(intents=intents)
loot_sessions = {}

# A dictionary to map numbers to their emoji equivalents for pretty formatting.
NUMBER_EMOJIS = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟",
    11: "1️⃣1️⃣", 12: "1️⃣2️⃣", 13: "1️⃣3️⃣", 14: "1️⃣4️⃣", 15: "1️⃣5️⃣",
    16: "1️⃣6️⃣", 17: "1️⃣7️⃣", 18: "1️⃣8️⃣", 19: "1️⃣9️⃣", 20: "2️⃣0️⃣"
}

# ANSI color codes for direct color control in Discord 'ansi' code blocks.
ANSI_RESET = "\u001b[0m"
ANSI_HEADER = "\u001b[0;33m"      # Yellow/Orange
ANSI_USER = "\u001b[0;34m"        # Blue
ANSI_NOT_TAKEN = "\u001b[0;31m"  # Red
ANSI_ASSIGNED = "\u001b[0;32m"    # Green


# ===================================================================================================
# MESSAGE BUILDERS
# ===================================================================================================

def build_main_panel(session, timed_out=False):
    """Builds the primary message with roll order, assigned items, and controls."""
    invoker = session["invoker"]
    rolls = session["rolls"]

    # --- Part 1: Header ---
    is_finished = not any(not item["assigned_to"] for item in session["items"])
    if timed_out:
        header = "⌛ **The loot session has timed out due to 30 minutes of inactivity!**\n\n"
    elif is_finished:
        header = "✅ **All items have been assigned! Looting has concluded!**\n\n"
    else:
        header = f"**(2/2)** 🎉 **Loot roll started by {invoker.mention}!**\n\n"

    # --- Part 2: Roll Order ---
    roll_order_header = f"```ansi\n{ANSI_HEADER}# Roll Order #{ANSI_RESET}\n==================================\n"
    roll_order_body = ""
    for i, r in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        roll_order_body += f"{num_emoji} {ANSI_USER}{r['member'].display_name}{ANSI_RESET} ({r['roll']})\n"
    roll_order_footer = "==================================\n```"
    roll_order_section = roll_order_header + roll_order_body + roll_order_footer

    # --- Part 3: Assigned Items ---
    assigned_header_text = "✅ Assigned Items ✅"
    if timed_out or is_finished:
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
    distribution_section = distribution_header + distribution_body + distribution_footer

    # --- Part 4: Footer and Unclaimed Items (on finish) ---
    footer = ""
    remaining_section = ""
    remaining_items = [item for item in session["items"] if not item["assigned_to"]]

    if is_finished or timed_out:
        # If the session is over, show unclaimed items in the main panel.
        if remaining_items:
            remaining_header = f"```ansi\n{ANSI_HEADER}❌ Unclaimed Items ❌{ANSI_RESET}\n==================================\n"
            remaining_body = ""
            # Correctly iterates through all original items to preserve numbering.
            for i, item in enumerate(session["items"], 1):
                if not item["assigned_to"]:
                    remaining_body += f"{i}. {item['name']}\n"
            remaining_footer = "==================================\n```"
            remaining_section = remaining_header + remaining_body + remaining_footer
    elif remaining_items:
        # If the session is active, build the dynamic footer.
        if session["current_turn"] >= 0:
            picker = session["rolls"][session["current_turn"]]["member"]
            direction_text = "Normal Order" if session["direction"] == 1 else "Reverse Order"
            picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
            turn_text = "turn again!" if session.get("just_reversed", False) else "turn!"
            footer = (
                f"🔔 **Round {session['round'] + 1}** ({direction_text})\n\n"
                f"**{picker_emoji} {picker.mention}'s {turn_text} **\n\n"
                f"✍️ **The Loot Master or the current picker can assign items.**"
            )
        else:
            footer = f"🎁 **Loot distribution is ready!**\n\n✍️**{invoker.mention} can remove participants or click 'Start Loot Assignment!' to begin.**"
    
    return f"{header}{roll_order_section}\n{distribution_section}\n{remaining_section}\n{footer}"

def build_remaining_items_panel(session):
    """Builds the separate message for the list of remaining items with static numbering."""
    remaining_items_exist = any(not item["assigned_to"] for item in session["items"])
    if not remaining_items_exist:
        return None

    header = f"**(1/2)**\n"
    remaining_header = f"```ansi\n{ANSI_HEADER}❌ Remaining Loot Items ❌{ANSI_RESET}\n==================================\n"
    remaining_body = ""
    # Iterate through the original item list to preserve original numbering.
    for i, item in enumerate(session["items"], 1):
        if not item["assigned_to"]:
            remaining_body += f"{i}. {item['name']}\n"
    remaining_footer = "==================================\n```"
    return header + remaining_header + remaining_body + remaining_footer


# ===================================================================================================
# DYNAMIC UI VIEW (BUTTONS & DROPDOWNS)
# ===================================================================================================

class LootControlView(nextcord.ui.View):
    def __init__(self, session_id):
        super().__init__(timeout=1800) 
        self.session_id = session_id
        self.update_components()

    def _are_items_left(self, session):
        return any(not item["assigned_to"] for item in session["items"])

    def _advance_turn_snake(self, session):
        session["just_reversed"] = False
        if not self._are_items_left(session):
            session["current_turn"] = len(session["rolls"])
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
        session = loot_sessions.get(self.session_id)
        self.clear_items()
        if not session or not self._are_items_left(session): return
        
        is_setup_phase = session["current_turn"] == -1
        is_picking_phase = not is_setup_phase

        if is_setup_phase:
            removable_members = [r for r in session["rolls"] if r["member"].id != session["invoker_id"]]
            if removable_members:
                member_options = []
                selected_to_remove = session.get("members_to_remove") or []
                for r in removable_members:
                    member_id_str = str(r['member'].id)
                    is_selected = member_id_str in selected_to_remove
                    label_text = f"{r['member'].display_name} (Roll: {r['roll']})"
                    truncated_label = (label_text[:97] + '...') if len(label_text) > 100 else label_text
                    member_options.append(nextcord.SelectOption(label=truncated_label, value=member_id_str, default=is_selected))

                placeholder = f"Select participants to remove ({len(removable_members)} total)..."
                self.add_item(nextcord.ui.Select(placeholder=placeholder, options=member_options, custom_id="remove_select", min_values=0, max_values=len(member_options)))
            
            remove_button_disabled = not session.get("members_to_remove")
            self.add_item(nextcord.ui.Button(label="Remove Selected", style=nextcord.ButtonStyle.danger, emoji="✖️", custom_id="remove_button", disabled=remove_button_disabled))
            self.add_item(nextcord.ui.Button(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="start_button"))
        
        elif is_picking_phase:
            available_items = [(index, item) for index, item in enumerate(session["items"]) if not item["assigned_to"]]
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
                    
                    start_num = chunk[0][0] + 1
                    end_num = chunk[-1][0] + 1
                    placeholder = f"Items {start_num}-{end_num}..." if len(item_chunks) > 1 else "Choose one or more items to claim..."
                    self.add_item(nextcord.ui.Select(placeholder=placeholder, options=options, custom_id=f"item_select_{i}", min_values=0, max_values=len(options)))
            
            assign_button_disabled = not session.get("selected_items")
            self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.green, emoji="✅", custom_id="assign_button", disabled=assign_button_disabled))
            self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button"))
        
        for child in self.children:
            if hasattr(child, 'custom_id'):
                if child.custom_id == "assign_button": child.callback = self.on_assign
                if child.custom_id == "skip_button": child.callback = self.on_skip
                if "item_select" in child.custom_id: child.callback = self.on_item_select
                if child.custom_id == "start_button": child.callback = self.on_start
                if child.custom_id == "remove_select": child.callback = self.on_remove_select
                if child.custom_id == "remove_button": child.callback = self.on_remove_confirm

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False
        
        if interaction.user.id == session["invoker_id"]:
            return True
        
        if session["current_turn"] >= 0:
            current_picker_id = session["rolls"][session["current_turn"]]["member"].id
            if interaction.user.id == current_picker_id:
                allowed_actions = ["assign_button"]
                if "item_select" in interaction.data.get("custom_id", ""):
                    return True
                if interaction.data.get("custom_id") in allowed_actions:
                    return True

        await interaction.response.send_message("🛡️ It is not your turn to perform this action.", ephemeral=True)
        return False

    async def update_message(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        main_panel_content = build_main_panel(session)
        remaining_items_content = build_remaining_items_panel(session)
        self.update_components()
        
        await interaction.message.edit(content=main_panel_content, view=self)

        remaining_message = session.get("remaining_message")
        if remaining_message:
            try:
                if remaining_items_content:
                    await remaining_message.edit(content=remaining_items_content)
                else:
                    await remaining_message.delete()
                    session["remaining_message"] = None
            except nextcord.NotFound:
                session["remaining_message"] = None

        if not self._are_items_left(session):
            final_content = build_main_panel(session)
            await interaction.message.edit(content=final_content, view=None)
            loot_sessions.pop(self.session_id, None)

    async def on_timeout(self):
        session = loot_sessions.get(self.session_id)
        if not session: return
        try:
            channel = bot.get_channel(session["channel_id"])
            if channel:
                main_message = await channel.fetch_message(self.session_id)
                final_content = build_main_panel(session, timed_out=True)
                await main_message.edit(content=final_content, view=None)

                if session.get("remaining_message"):
                    await session["remaining_message"].delete()
        except (nextcord.NotFound, nextcord.Forbidden):
            pass
        finally:
            loot_sessions.pop(self.session_id, None)

    # --- Callbacks ---

    async def on_remove_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return
        session["members_to_remove"] = interaction.data["values"]
        self.update_components()
        await interaction.response.edit_message(view=self)

    async def on_remove_confirm(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return
        members_to_remove_ids = [int(mid) for mid in session.get("members_to_remove", [])]
        session["rolls"] = [r for r in session["rolls"] if r["member"].id not in members_to_remove_ids]
        session["members_to_remove"] = None
        await self.update_message(interaction)

    async def on_start(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return
        self._advance_turn_snake(session)
        await self.update_message(interaction)

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
        for index_str in selected_indices:
            session["items"][int(index_str)]["assigned_to"] = current_picker_id
        session["selected_items"] = None
        self._advance_turn_snake(session)
        await self.update_message(interaction)

    async def on_skip(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return
        session["selected_items"] = None
        self._advance_turn_snake(session)
        await self.update_message(interaction)


# ===================================================================================================
# MODAL POP-UP
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__("RNGenie Loot Setup")
        self.loot_items = nextcord.ui.TextInput(
            label="List Your Loot Items Below (One Per Line)", 
            placeholder="Item One\nAnother Item\nThird Item...", 
            required=True, 
            style=nextcord.TextInputStyle.paragraph,
            max_length=1200
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You must be in a voice channel to set up a loot roll.", ephemeral=True)
            return
        
        guild = interaction.guild
        voice_channel = guild.get_channel(interaction.user.voice.channel.id)
        members = [member for member in voice_channel.members]
        
        if len(members) > 20:
            await interaction.followup.send(f"❌ Too many users in the voice channel ({len(members)})! The maximum is 20.", ephemeral=True)
            return
        if len(members) < 1:
            await interaction.followup.send("❌ I could not find anyone in your voice channel. This is likely a permissions issue.", ephemeral=True)
            return

        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        rolls.sort(key=lambda x: x['roll'], reverse=True)
        
        items_data = [{"name": line.strip(), "assigned_to": None} for line in self.loot_items.value.split('\n') if line.strip()]
        
        if len(items_data) > 100:
            await interaction.followup.send(f"❌ Too many loot items ({len(items_data)})! The maximum is 100.", ephemeral=True)
            return
            
        if not items_data:
            await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)
            return
        
        session = { 
            "rolls": rolls, "items": items_data, "current_turn": -1, 
            "invoker_id": interaction.user.id, "invoker": interaction.user,
            "selected_items": None, "members_to_remove": None, "round": 0, "direction": 1,
            "just_reversed": False, "remaining_message": None
        }
        
        remaining_message = await interaction.channel.send("`Loading Item List...`")
        main_message = await interaction.followup.send("`Initializing Main Panel...`", wait=True)
        
        panel_content = build_main_panel(session)
        remaining_content = build_remaining_items_panel(session)

        session_id = main_message.id
        session["channel_id"] = main_message.channel.id
        session["remaining_message"] = remaining_message
        loot_sessions[session_id] = session
        
        final_view = LootControlView(session_id)

        await main_message.edit(content=panel_content, view=final_view)
        if remaining_content and remaining_message:
            await remaining_message.edit(content=remaining_content)
        elif remaining_message:
            await remaining_message.delete()


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
    print(f'Logged in as {bot.user}')
    print('RNGenie is ready for local debugging.')
    print('------')

@bot.event
async def on_application_command_error(interaction: nextcord.Interaction, error: Exception):
    """A global error handler for all slash commands and UI interactions."""
    print(f"\n--- Unhandled exception in interaction for command '{interaction.application_command.name}' ---")
    traceback.print_exception(type(error), error, error.__traceback__)
    print("--- End of exception report ---\n")
    if not interaction.is_expired():
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ An unexpected error occurred. The developer has been notified via console logs.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ An unexpected error occurred. The developer has been notified via console logs.", ephemeral=True)
        except nextcord.HTTPException:
            pass


# ===================================================================================================
# RUN SCRIPT
# ===================================================================================================

load_dotenv()
bot.run(os.getenv("DISCORD_TOKEN"))
