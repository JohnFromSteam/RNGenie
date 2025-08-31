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

intents = nextcord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(intents=intents)
loot_sessions = {}

NUMBER_EMOJIS = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟",
    11: "1️⃣1️⃣", 12: "1️⃣2️⃣", 13: "1️⃣3️⃣", 14: "1️⃣4️⃣", 15: "1️⃣5️⃣",
    16: "1️⃣6️⃣", 17: "1️⃣7️⃣", 18: "1️⃣8️⃣", 19: "1️⃣9️⃣", 20: "2️⃣0️⃣"
}

# ANSI color codes
ANSI_RESET = "\u001b[0m"
ANSI_HEADER = "\u001b[0;33m"
ANSI_USER = "\u001b[0;34m"
ANSI_NOT_TAKEN = "\u001b[0;31m"
ANSI_ASSIGNED = "\u001b[0;32m"


# ===================================================================================================
# UNIFIED MESSAGE BUILDERS
# ===================================================================================================

def build_roll_order_message(invoker, rolls):
    """Builds the static, one-time roll order message."""
    header = f"🎉 **Loot roll started by {invoker.mention}!**\n\n"
    roll_order_header = f"```ansi\n{ANSI_HEADER}🔢 Roll Order 🔢{ANSI_RESET}\n==================================\n"
    roll_order_body = ""
    for i, r in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        roll_order_body += f"{num_emoji} {ANSI_USER}{r['member'].display_name}{ANSI_RESET} ({r['roll']})\n"
    roll_order_footer = "==================================\n```"
    return header + roll_order_header + roll_order_body + roll_order_footer

def build_interactive_loot_panel(session):
    """Builds the main, interactive loot message that gets updated."""
    invoker = session["invoker"]
    
    # --- Part 1: Live Loot Distribution ---
    distribution_header = f"```ansi\n{ANSI_HEADER}✅ Assigned Items ✅{ANSI_RESET}\n"
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
        distribution_body += f"==================================\n[{num_emoji} {ANSI_USER}{member.display_name}{ANSI_RESET}]\n\n"
        if member.id in assigned_items:
            for item_name in assigned_items[member.id]:
                distribution_body += f"{item_name}\n"
    distribution_footer = "==================================\n```"
    distribution_section = distribution_header + distribution_body + distribution_footer

    # --- Part 2: Remaining Loot & Footer ---
    remaining_items = [item for item in session["items"] if not item["assigned_to"]]
    remaining_section, footer = "", ""

    if remaining_items:
        remaining_header = f"```ansi\n{ANSI_HEADER}❌ Remaining Loot Items ❌{ANSI_RESET}\n==================================\n"
        remaining_body = ""
        for item in remaining_items:
            remaining_body += f"{item['name']}\n"
        remaining_footer = "==================================\n```"
        remaining_section = remaining_header + remaining_body + remaining_footer
        
        if session["current_turn"] >= 0:
            picker = session["rolls"][session["current_turn"]]["member"]
            direction_text = "Normal Order" if session["direction"] == 1 else "Reverse Order"
            picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
            turn_text = "turn again!" if session.get("just_reversed", False) else "turn!"
            
            footer = (
                f"🔔 **Round {session['round'] + 1}** ({direction_text})\n\n"
                f"**{picker_emoji} {picker.mention}'s {turn_text} **\n\n"
                f"✍️ **{invoker.mention} must select\nor skip for {picker.mention}**"
            )
        else:
            footer = f"🎁 **Loot distribution is ready!\n\n✍️{invoker.mention} must click below to begin.\n**"
    else:
        footer = "✅ **All items have been assigned! Looting has concluded!**"
    return f"{distribution_section}\n{remaining_section}\n{footer}"

def build_timeout_message(session):
    """Builds the summary message for when a loot session times out."""
    header = "⌛ **The loot session has timed out due to 30 minutes of inactivity!**\n"
    rolls = session["rolls"]
    
    # --- Final Loot Distribution Section ---
    distribution_header = f"```ansi\n{ANSI_HEADER}✅ Final Assigned Items ✅{ANSI_RESET}\n"
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

    # --- Unclaimed Items Section ---
    remaining_items = [item for item in session["items"] if not item["assigned_to"]]
    remaining_section = ""
    if remaining_items:
        remaining_header = f"```ansi\n{ANSI_HEADER}❌ Unclaimed Items ❌{ANSI_RESET}\n==================================\n"
        remaining_body = ""
        for item in remaining_items:
            remaining_body += f"{item['name']}\n"
        remaining_footer = "==================================\n```"
        remaining_section = remaining_header + remaining_body + remaining_footer

    return f"{header}\n{distribution_section}\n{remaining_section}"


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
        is_picking_turn = session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"])
        if is_picking_turn:
            available_items = [(index, item) for index, item in enumerate(session["items"]) if not item["assigned_to"]]
            if available_items:
                options = []
                selected_values = session.get("selected_items") or []
                for index, item in available_items:
                    is_selected = str(index) in selected_values
                    options.append(nextcord.SelectOption(label=(item["name"][:97] + '...') if len(item["name"]) > 100 else item["name"], value=str(index), default=is_selected))
                self.add_item(nextcord.ui.Select(placeholder="Choose one or more items to claim...", options=options, custom_id="item_select", min_values=0, max_values=len(available_items)))
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
                if child.custom_id == "item_select": child.callback = self.on_item_select

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False
        if interaction.user.id == session["invoker_id"]:
            return True
        else:
            await interaction.response.send_message("🛡️ Only the Loot Master who started the roll can assign items or skip turns.", ephemeral=True)
            return False

    async def update_message(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return
        content = build_interactive_loot_panel(session)
        self.update_components()
        if not self._are_items_left(session):
            await interaction.message.edit(content=content, view=None)
            loot_sessions.pop(self.session_id, None)
        else:
            await interaction.message.edit(content=content, view=self)

    async def on_timeout(self):
        session = loot_sessions.get(self.session_id)
        if not session: return
        try:
            channel = bot.get_channel(session["channel_id"])
            if channel:
                message = await channel.fetch_message(self.session_id)
                timeout_content = build_timeout_message(session)
                await message.edit(content=timeout_content, view=None)
        except (nextcord.NotFound, nextcord.Forbidden):
            pass
        finally:
            loot_sessions.pop(self.session_id, None)

    async def on_item_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return
        session["selected_items"] = interaction.data["values"]
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
    """A pop-up window that prompts the user to enter the list of loot items."""
    def __init__(self):
        super().__init__("RNGenie Loot Setup!")
        self.loot_items = nextcord.ui.TextInput(
            label="List Your Loot Items Below (One Per Line)", 
            placeholder="Old Republic Jedi Master Cloak\nThunderfury, Blessed Blade of the Windseeker...", 
            required=True, 
            style=nextcord.TextInputStyle.paragraph
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        """Executed after modal submission. Gathers data and creates the initial loot session message."""
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
        if not items_data:
            await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)
            return
        
        session = { 
            "rolls": rolls, "items": items_data, "current_turn": -1, 
            "invoker_id": interaction.user.id, "invoker": interaction.user,
            "selected_items": None, "round": 0, "direction": 1,
            "just_reversed": False
        }
        
        # Send the static roll order message first.
        roll_order_content = build_roll_order_message(interaction.user, rolls)
        await interaction.channel.send(roll_order_content)

        # Then, send the interactive panel as the followup.
        panel_content = build_interactive_loot_panel(session)
        loot_message = await interaction.followup.send(
            content=panel_content,
            view=LootControlView(0),
            wait=True
        )
        
        session_id = loot_message.id
        session["channel_id"] = loot_message.channel.id
        loot_sessions[session_id] = session
        
        final_view = LootControlView(session_id)
        await loot_message.edit(view=final_view)


# ===================================================================================================
# SLASH COMMAND
# ===================================================================================================

@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    """The entry point for the loot command."""
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You need to be in a voice channel to start a loot roll!", ephemeral=True)
        return
    
    await interaction.response.send_modal(LootModal())


# ===================================================================================================
# BOT EVENTS
# ===================================================================================================

@bot.event
async def on_ready():
    """Event that fires when the bot successfully logs in."""
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
            await interaction.followup.send(
                "❌ An unexpected error occurred. The developer has been notified via console logs.", 
                ephemeral=True
            )
        except nextcord.HTTPException:
            pass


# ===================================================================================================
# RUN SCRIPT
# ===================================================================================================

load_dotenv()
bot.run(os.getenv("DISCORD_TOKEN"))
