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

# Emojis for numbering lists
NUMBER_EMOJIS = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"
}

# --- Helper Functions for Text Formatting ---

def build_roll_order_message(invoker, rolls):
    header = f"🎉 **Loot roll started by {invoker.mention}!**\n"
    table_header = "```md\n# Roll Order #\n==================================\n"
    table_body = ""
    for i, r in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"{i+1}.")
        table_body += f"{num_emoji} {r['member'].display_name} (Roll: {r['roll']})\n"
    table_footer = "==================================\n```"
    return header + table_header + table_body + table_footer

def build_final_summary_message(session):
    header = "✅ **All items have been assigned! Looting has concluded!**\n"
    body = "```md\n# Final Loot Distribution #\n"
    assigned_items = {}
    for item in session["items"]:
        assignee_id = item["assigned_to"]
        if assignee_id not in assigned_items: assigned_items[assignee_id] = []
        assigned_items[assignee_id].append(item["name"])
    for i, roll_info in enumerate(session["rolls"]):
        member = roll_info["member"]
        if member.id in assigned_items:
            num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
            body += f"==================================\n{num_emoji} {member.display_name}\n==================================\n"
            for item_name in assigned_items[member.id]:
                body += f"[✅ Taken] {item_name}\n"
            body += "\n"
    body += "```"
    return header + body

def build_loot_panel_message(session):
    if not any(not item["assigned_to"] for item in session["items"]):
        return build_final_summary_message(session)
    if session["current_turn"] == -1:
        header = "🎁 **Loot distribution is ready!** The Loot Master can start the roll."
    else:
        picker = session["rolls"][session["current_turn"]]["member"]
        header = f"**It is now {picker.mention}'s turn to pick! (Round {session['round'] + 1})**"
    item_list_header = "```md\n# Loot Items #\n==================================\n"
    item_list_body = ""
    for item in session["items"]:
        if item["assigned_to"]:
            member = bot.get_user(item["assigned_to"])
            member_name = member.display_name if member else "Unknown User"
            item_list_body += f"[✅ Taken] {item['name']}\n> Assigned to: {member_name}\n\n"
        else:
            item_list_body += f"[❌ Not Taken] {item['name']}\n\n"
    item_list_footer = "==================================\n```"
    return header + "\n" + item_list_header + item_list_body.strip() + "\n" + item_list_footer

# --- DYNAMIC UI VIEW FOR LOOT CONTROL ---
class LootControlView(nextcord.ui.View):
    def __init__(self, session_id):
        # View is persistent but relies on in-memory session data.
        # This can fail if the bot restarts.
        super().__init__(timeout=None) 
        self.session_id = session_id
        
    def _are_items_left(self, session):
        return any(not item["assigned_to"] for item in session["items"])

    def _advance_turn_snake(self, session):
        if not self._are_items_left(session):
            session["current_turn"] = len(session["rolls"])
            return
        num_rollers = len(session["rolls"])
        if num_rollers == 0: return
        if session["current_turn"] == -1:
            session["current_turn"] = 0
            return
        direction = session["direction"]
        new_turn = session["current_turn"] + direction
        if 0 <= new_turn < num_rollers:
            session["current_turn"] = new_turn
        else:
            session["direction"] *= -1
            session["round"] += 1
            session["current_turn"] = session["current_turn"] + session["direction"]

    def update_components(self):
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
        
        start_label = "📜 Start Loot Assignment!" if session["current_turn"] == -1 else "Skip Turn"
        self.add_item(nextcord.ui.Button(label=start_label, style=nextcord.ButtonStyle.blurple, custom_id="skip_button"))
        
        for child in self.children:
            if child.custom_id == "assign_button": child.callback = self.on_assign
            if child.custom_id == "skip_button": child.callback = self.on_skip
            if child.custom_id == "item_select": child.callback = self.on_item_select

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ This loot session has expired or could not be found.", ephemeral=True)
            return False
        
        if interaction.data.get("custom_id") == "skip_button" and interaction.user.id == session["invoker_id"]:
            return True

        if session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"]):
            current_picker_id = session["rolls"][session["current_turn"]]["member"].id
            if interaction.user.id == current_picker_id:
                return True

        await interaction.response.send_message("🛡️ It is not your turn to act, or only the Loot Master can start the roll.", ephemeral=True)
        return False

    async def update_message(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return
        message_content = build_loot_panel_message(session)
        self.update_components()
        view = self if self._are_items_left(session) else None
        await interaction.message.edit(content=message_content, view=view)
        if not self._are_items_left(session):
            loot_sessions.pop(self.session_id, None)

    async def on_item_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return # Check is handled by interaction_check
        session["selected_items"] = interaction.data["values"]
        await interaction.response.defer()

    async def on_assign(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return # Check is handled by interaction_check
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
        if not session: return # Check is handled by interaction_check
        self._advance_turn_snake(session)
        await self.update_message(interaction)

# --- VIEW AND MODAL FOR INITIAL SETUP ---

class StartLootView(nextcord.ui.View):
    def __init__(self):
        super().__init__(timeout=60) # Button will be disabled after 60 seconds

    @nextcord.ui.button(label="Setup Loot Items", style=nextcord.ButtonStyle.green)
    async def setup_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await interaction.response.send_modal(LootModal())
        # Disable the button and stop the view after it's clicked
        button.disabled = True
        await interaction.message.edit(view=self)
        self.stop()

class LootModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__("Loot Distribution Setup")
        self.loot_items = nextcord.ui.TextInput(label="Loot Items (One Per Line)", placeholder="Thunderfury, Blessed Blade of the Windseeker\nOld Republic Jedi Master Cloak\n...", required=True, style=nextcord.TextInputStyle.paragraph)
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You seem to have left the voice channel.", ephemeral=True)
            return
        voice_channel = interaction.user.voice.channel
        await interaction.response.defer()

        members = [
            member for member in interaction.guild.members 
            if not member.bot and member.voice and member.voice.channel == voice_channel
        ]
        
        if len(members) < 1:
            await interaction.followup.send("Error: Could not find any users in your voice channel.", ephemeral=True)
            return

        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        rolls.sort(key=lambda x: x['roll'], reverse=True)
        # Send the roll order to the public channel
        await interaction.channel.send(build_roll_order_message(interaction.user, rolls))
        
        items_data = [{"name": line.strip(), "assigned_to": None} for line in self.loot_items.value.split('\n') if line.strip()]
        if not items_data:
            await interaction.followup.send("⚠️ No valid items were entered.", ephemeral=True)
            return

        session = { "rolls": rolls, "items": items_data, "current_turn": -1, "invoker_id": interaction.user.id, "selected_items": None, "round": 0, "direction": 1 }
        
        # This interaction was for the modal, we need a new ID for the session message
        # We will use the followup message's ID as the session ID
        
        # Temporarily store session with the interaction ID to build the message
        temp_session_id = interaction.id
        loot_sessions[temp_session_id] = session
        
        view = LootControlView(temp_session_id)
        message_content = build_loot_panel_message(session)
        view.update_components()
        
        # Send the main loot panel message
        loot_message = await interaction.followup.send(content=message_content, view=view)
        
        # Now, update the session ID to the actual message ID for future interactions
        view.session_id = loot_message.id
        loot_sessions[loot_message.id] = session
        del loot_sessions[temp_session_id]

# --- SLASH COMMAND ---
@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You need to be in a voice channel to start a loot roll!", ephemeral=True)
        return
    
    # Immediately defer to avoid the 3-second timeout.
    await interaction.response.defer(ephemeral=True) 

    # Now send a follow-up message with the button that opens the modal.
    view = StartLootView()
    await interaction.followup.send(
        "Click the button below to add the loot items and start the roll.", 
        view=view, 
        ephemeral=True
    )

# --- EVENT LISTENERS ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

# --- RUN ---
load_dotenv()
keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))
