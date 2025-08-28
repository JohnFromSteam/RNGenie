# RNGenie_deploy.py
# A Discord bot for managing turn-based loot distribution in voice channels.

import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands
import random
from threading import Thread
from flask import Flask

# ===================================================================================================
# KEEP-ALIVE WEB SERVER (FOR HOSTING PLATFORMS)
# ===================================================================================================

app = Flask('')

@app.route('/')
def home():
    return "RNGenie is alive!"

def run_web_server():
    # Use the PORT environment variable provided by the hosting service.
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    """Starts the Flask web server in a separate thread."""
    t = Thread(target=run_web_server)
    t.start()

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
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"
}

# ANSI color codes for direct color control in Discord 'ansi' code blocks.
ANSI_RESET = "\u001b[0m"
ANSI_HEADER = "\u001b[0;33m"      # Yellow/Orange
ANSI_USER = "\u001b[0;34m"        # Blue
ANSI_NOT_TAKEN = "\u001b[0;31m"  # Red
ANSI_ASSIGNED = "\u001b[0;32m"    # Green


# ===================================================================================================
# UNIFIED MESSAGE BUILDER
# ===================================================================================================

def build_dynamic_loot_message(session):
    """
    Constructs the entire dynamic message content, including roll order,
    live distribution, remaining items, and the current turn status.
    """
    invoker = session["invoker"]
    rolls = session["rolls"]

    # --- Part 1: Roll Order Header ---
    header = f"🎉 **Loot roll started by {invoker.mention}!**\n"
    roll_order_header = f"```ansi\n{ANSI_HEADER}# Roll Order #{ANSI_RESET}\n==================================\n"
    roll_order_body = ""
    for i, r in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"{i+1}.")
        roll_order_body += f"{num_emoji} {ANSI_USER}{r['member'].display_name}{ANSI_RESET} (Roll: {r['roll']})\n"
    roll_order_footer = "==================================\n```"
    roll_order_section = header + roll_order_header + roll_order_body + roll_order_footer

    # --- Part 2: Live Loot Distribution ---
    distribution_header = f"```ansi\n{ANSI_HEADER}# Loot Distribution #{ANSI_RESET}\n"
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
                distribution_body += f"{ANSI_ASSIGNED}[✅ Assigned]{ANSI_RESET} {item_name}\n"

    distribution_footer = "==================================\n```"
    distribution_section = distribution_header + distribution_body + distribution_footer

    # --- Part 3: Remaining Loot & Footer ---
    remaining_items = [item for item in session["items"] if not item["assigned_to"]]
    remaining_section = ""
    footer = ""

    if remaining_items:
        remaining_header = f"```ansi\n{ANSI_HEADER}# Remaining Loot Items #{ANSI_RESET}\n==================================\n"
        remaining_body = ""
        for item in remaining_items:
            remaining_body += f"{ANSI_NOT_TAKEN}[❌ Not Taken]{ANSI_RESET} {item['name']}\n"
        remaining_footer = "==================================\n```"
        remaining_section = remaining_header + remaining_body + remaining_footer

        if session["current_turn"] >= 0:
            picker = session["rolls"][session["current_turn"]]["member"]
            footer = f"📜 **It is now {picker.mention}'s turn to pick! (Round {session['round'] + 1})**"
        else:
            footer = "🎁 **Loot distribution is ready! Click 'Start Loot Assignment!' to begin.**"
    else:
        footer = "✅ **All items have been assigned! Looting has concluded!**"

    return f"{roll_order_section}\n{distribution_section}\n{remaining_section}\n{footer}"


# ===================================================================================================
# DYNAMIC UI VIEW (BUTTONS & DROPDOWNS)
# ===================================================================================================

class LootControlView(nextcord.ui.View):
    """
    Manages the interactive components (buttons, dropdowns) of the loot message.
    Only the original invoker (Loot Master) can interact with these components.
    """
    def __init__(self, session_id):
        super().__init__(timeout=None) 
        self.session_id = session_id
        self.update_components()

    def _are_items_left(self, session):
        """Helper to check if any items are still unassigned."""
        return any(not item["assigned_to"] for item in session["items"])

    def _advance_turn_snake(self, session):
        """
        Advances the turn in a "snake draft" order. When the order reaches an end,
        the person at the end gets a second consecutive turn before the order reverses.
        """
        if not self._are_items_left(session):
            session["current_turn"] = len(session["rolls"]) # End state
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

    def update_components(self):
        """Dynamically redraws the buttons and dropdown based on the current session state."""
        session = loot_sessions.get(self.session_id)
        self.clear_items()
        if not session or not self._are_items_left(session): return

        is_picking_turn = session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"])
        if is_picking_turn:
            available_items = [(index, item) for index, item in enumerate(session["items"]) if not item["assigned_to"]]
            if available_items:
                options = [nextcord.SelectOption(label=(item["name"][:97] + '...') if len(item["name"]) > 100 else item["name"], value=str(index)) for index, item in available_items]
                self.add_item(nextcord.ui.Select(placeholder="Choose one or more items to claim...", options=options, custom_id="item_select", min_values=1, max_values=len(available_items)))
            self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.green, emoji="✅", custom_id="assign_button"))
        
        if session["current_turn"] == -1:
            self.add_item(nextcord.ui.Button(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="skip_button"))
        else:
            self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.secondary, custom_id="skip_button"))
        
        for child in self.children:
            if hasattr(child, 'custom_id'):
                if child.custom_id == "assign_button": child.callback = self.on_assign
                if child.custom_id == "skip_button": child.callback = self.on_skip
                if child.custom_id == "item_select": child.callback = self.on_item_select

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        """Ensures only the original command invoker can use the controls."""
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
        """A central method to update the main loot message with new content and components."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        
        content = build_dynamic_loot_message(session)
        self.update_components()
        
        if not self._are_items_left(session):
            await interaction.message.edit(content=content, view=None)
            loot_sessions.pop(self.session_id, None)
        else:
            await interaction.message.edit(content=content, view=self)

    # --- UI Component Callbacks ---

    async def on_item_select(self, interaction: nextcord.Interaction):
        """Stores the user's selection from the dropdown menu."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        session["selected_items"] = interaction.data["values"]
        await interaction.response.defer()

    async def on_assign(self, interaction: nextcord.Interaction):
        """Assigns the selected item(s) to the current picker and advances the turn."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        selected_indices = session.get("selected_items")
        if not selected_indices:
            await interaction.response.send_message("🤔 You need to select an item from the dropdown first!", ephemeral=True)
            return
        
        current_picker_id = session["rolls"][session["current_turn"]]["member"].id
        for index_str in selected_indices:
            session["items"][int(index_str)]["assigned_to"] = current_picker_id
        
        session["selected_items"] = None
        self._advance_turn_snake(session)
        await self.update_message(interaction)

    async def on_skip(self, interaction: nextcord.Interaction):
        """Skips the current turn and advances to the next picker."""
        session = loot_sessions.get(self.session_id)
        if not session: return
        self._advance_turn_snake(session)
        await self.update_message(interaction)


# ===================================================================================================
# MODAL POP-UP
# ===================================================================================================

class LootModal(nextcord.ui.Modal):
    """
    A pop-up window that prompts the user to enter the list of loot items.
    """
    def __init__(self):
        super().__init__("Loot Distribution Setup")
        self.loot_items = nextcord.ui.TextInput(
            label="Loot Items (One Per Line)", 
            placeholder="Old Republic Jedi Master Cloak\nThunderfury, Blessed Blade of the Windseeker...", 
            required=True, 
            style=nextcord.TextInputStyle.paragraph
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        """
        This function is executed after the user submits the modal.
        It gathers all necessary data and creates the initial loot session message.
        """
        await interaction.response.defer()

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You must be in a voice channel to set up a loot roll.", ephemeral=True)
            return
        
        guild = interaction.guild
        voice_channel = guild.get_channel(interaction.user.voice.channel.id)
        members = [member for member in voice_channel.members]
        
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
            "selected_items": None, "round": 0, "direction": 1 
        }
        
        initial_content = build_dynamic_loot_message(session)
        loot_message = await interaction.followup.send(
            content=initial_content,
            view=LootControlView(0),
            wait=True
        )
        
        session_id = loot_message.id
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


# ===================================================================================================
# RUN SCRIPT
# ===================================================================================================

# Load environment variables (like the DISCORD_TOKEN) from a .env file.
load_dotenv()
# Start the keep-alive web server.
keep_alive()
# Start the bot.
bot.run(os.getenv("DISCORD_TOKEN"))
