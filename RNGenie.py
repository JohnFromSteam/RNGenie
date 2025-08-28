import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands
import random
from threading import Thread
from flask import Flask

# --- Keep-Alive Web Server for Render ---
app = Flask('')

@app.route('/')
def home():
    return "RNGenie is alive!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()
# -----------------------------------------

# --- BOT SETUP ---
intents = nextcord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(intents=intents)
loot_sessions = {}

# --- Helper Functions for Text Formatting ---

def build_roll_order_message(invoker, rolls):
    header = f"🎉 **Loot roll started by {invoker.mention}!**\n"
    table_header = "```md\n# Roll Order #\n==================================\n"
    table_body = ""
    for i, r in enumerate(rolls):
        # Format: 1. JohnFromSteam (Roll: 98)
        table_body += f"{i+1}. {r['member'].display_name} (Roll: {r['roll']})\n"
    table_footer = "==================================\n```"
    return header + table_header + table_body + table_footer

def build_loot_panel_message(session):
    # Determine header message
    if not any(not item["assigned_to"] for item in session["items"]):
        header = "✅ **All items have been assigned! Looting has concluded!**"
    elif session["current_turn"] == -1:
        header = "🎁 **Loot distribution is ready!** The Loot Master can start by clicking 'Skip Turn'."
    else:
        picker = session["rolls"][session["current_turn"]]["member"]
        header = f"**It is now {picker.mention}'s turn to pick! (Round {session['round'] + 1})**"

    # Build the item list
    item_list_header = "```md\n# Loot Items #\n==================================\n"
    item_list_body = ""
    for item in session["items"]:
        if item["assigned_to"]:
            member = bot.get_user(item["assigned_to"])
            item_list_body += f"[Taken] {item['name']}\n> Assigned to: {member.display_name}\n\n"
        else:
            item_list_body += f"[Available] {item['name']}\n\n"
    item_list_footer = "==================================\n```"
    
    return header + "\n" + item_list_header + item_list_body.strip() + "\n" + item_list_footer

# --- DYNAMIC UI VIEW FOR LOOT CONTROL ---
class LootControlView(nextcord.ui.View):
    def __init__(self, session_id):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.update_components()

    def _are_items_left(self, session):
        return any(not item["assigned_to"] for item in session["items"])

    def _advance_turn_snake(self, session):
        """Advances the turn using snake draft logic."""
        if not self._are_items_left(session):
            session["current_turn"] = len(session["rolls"]) # End state
            return

        num_rollers = len(session["rolls"])
        if num_rollers == 0: return

        # If starting for the first time
        if session["current_turn"] == -1:
            session["current_turn"] = 0
            return

        direction = session["direction"]
        new_turn = session["current_turn"] + direction

        # Check if we are within bounds
        if 0 <= new_turn < num_rollers:
            session["current_turn"] = new_turn
        else:
            # We've hit an edge, so we reverse direction and increment the round
            session["direction"] *= -1
            session["round"] += 1
            # The next turn is the one we just landed on
            session["current_turn"] = session["current_turn"] + session["direction"]


    def update_components(self):
        session = loot_sessions.get(self.session_id)
        if not session or not self._are_items_left(session):
            self.clear_items()
            return

        self.clear_items()
        
        # Add the 'Skip Turn' button first, as it's always present
        skip_button = nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.blurple, emoji="▶️", custom_id="skip_button")
        skip_button.callback = self.on_skip
        self.add_item(skip_button)

        is_picking_turn = session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"])
        if is_picking_turn:
            available_items = [(index, item) for index, item in enumerate(session["items"]) if not item["assigned_to"]]
            if available_items:
                options = [nextcord.SelectOption(label=(item["name"][:97] + '...') if len(item["name"]) > 100 else item["name"], value=str(index)) for index, item in available_items]
                
                item_select = nextcord.ui.Select(
                    placeholder="Choose one or more items to claim...", options=options,
                    custom_id="item_select", min_values=1, max_values=len(available_items)
                )
                item_select.callback = self.on_item_select
                self.add_item(item_select)
            
            assign_button = nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.green, custom_id="assign_button")
            assign_button.callback = self.on_assign
            self.add_item(assign_button)
            
    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session: return False
        
        # Anyone can interact if it's not a picking turn (i.e., to start the first turn)
        if session["current_turn"] == -1 and interaction.data.get("custom_id") == "skip_button":
            return interaction.user.id == session["invoker_id"]

        if session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"]):
            current_picker_id = session["rolls"][session["current_turn"]]["member"].id
            if interaction.user.id == current_picker_id or interaction.user.id == session["invoker_id"]:
                return True
        
        await interaction.response.send_message("🛡️ It is not your turn to act!", ephemeral=True)
        return False

    async def update_message(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return

        message_content = build_loot_panel_message(session)
        self.update_components()

        # If all items are gone, remove the view entirely
        view = self if self._are_items_left(session) else None
        await interaction.message.edit(content=message_content, view=view)
        
        if not self._are_items_left(session):
            loot_sessions.pop(self.session_id, None)

    async def on_item_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        session["selected_items"] = interaction.data["values"]
        await interaction.response.defer()

    async def on_assign(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
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
        session = loot_sessions.get(self.session_id)
        self._advance_turn_snake(session)
        await self.update_message(interaction)

# --- MODAL ---
class LootModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__("Loot Distribution Setup")
        self.loot_items = nextcord.ui.TextInput(label="Loot Items (One Per Line)", placeholder="Thunderfury, Blessed Blade of the Windseeker\nOld Republic Jedi Master Cloak\n...", required=True, style=nextcord.TextInputStyle.paragraph)
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        voice_channel = interaction.user.voice.channel
        if not voice_channel:
            await interaction.response.send_message("❌ You seem to have left the voice channel.", ephemeral=True)
            return

        await interaction.response.defer()

        members = [m for m in voice_channel.members if not m.bot]
        if not members:
            await interaction.followup.send("Error: There are no users in your voice channel.", ephemeral=True); return

        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        rolls.sort(key=lambda x: x['roll'], reverse=True)

        await interaction.channel.send(build_roll_order_message(interaction.user, rolls))
        
        items_data = [{"name": line.strip(), "assigned_to": None} for line in self.loot_items.value.split('\n') if line.strip()]
        if not items_data:
            await interaction.followup.send("⚠️ No valid items were entered.", ephemeral=True)
            return

        session = {
            "rolls": rolls, "items": items_data, "current_turn": -1,
            "invoker_id": interaction.user.id, "selected_items": None,
            "round": 0, "direction": 1 # New variables for snake draft
        }
        
        view = LootControlView(interaction.id)
        message_content = build_loot_panel_message(session)
        
        loot_message = await interaction.followup.send(content=message_content, view=view)
        
        view.session_id = loot_message.id
        loot_sessions[loot_message.id] = session

# --- SLASH COMMAND ---
@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You need to be in a voice channel to start a loot roll!", ephemeral=True)
        return
    await interaction.response.send_modal(LootModal())

# --- EVENT LISTENERS ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

# --- RUN ---
load_dotenv()
keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))
