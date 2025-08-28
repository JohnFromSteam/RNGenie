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
    # Use the PORT environment variable Render provides.
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()
# -----------------------------------------

# --- BOT SETUP ---
intents = nextcord.Intents.default()
intents.members = True
intents.voice_states = True # Required for voice channel detection

bot = commands.Bot(intents=intents) # No command_prefix needed for a slash-command-only bot

# This dictionary will store active loot sessions
loot_sessions = {}

# --- DYNAMIC UI VIEW FOR LOOT CONTROL ---
class LootControlView(nextcord.ui.View):
    def __init__(self, session_id):
        super().__init__(timeout=None)
        self.session_id = session_id
        # No need to call update_components() here, the initial message has no components.
        
    def _are_items_left(self, session):
        return any(not item["assigned_to"] for item in session["items"])

    def _advance_turn(self, session):
        if not self._are_items_left(session):
            session["current_turn"] = len(session["rolls"])
            return
        num_rollers = len(session["rolls"])
        if num_rollers > 0:
            session["current_turn"] = (session["current_turn"] + 1) % num_rollers
            
    def update_components(self):
        session = loot_sessions.get(self.session_id)
        if not session:
            self.clear_items()
            return

        self.clear_items()
        is_picking_turn = session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"])
        
        # If all items are gone, show no components and stop.
        if not self._are_items_left(session):
            return

        # Row 1 & 2: Picker controls (Dropdown, Assign, Skip)
        if is_picking_turn:
            available_items = [(index, item) for index, item in enumerate(session["items"]) if not item["assigned_to"]]
            if available_items:
                options = []
                for index, item in available_items:
                    label_text = (item["name"][:97] + '...') if len(item["name"]) > 100 else item["name"]
                    options.append(nextcord.SelectOption(label=label_text, value=str(index), description=f"Claim {label_text}"))
                
                item_select = nextcord.ui.Select(
                    placeholder="Choose one or more items to claim...",
                    options=options, custom_id="item_select", min_values=1,
                    max_values=len(available_items)
                )
                item_select.callback = self.on_item_select
                self.add_item(item_select)
            
            # Put Assign and Skip on the same row
            assign_button = nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.green, custom_id="assign_button")
            assign_button.callback = self.on_assign
            self.add_item(assign_button)
            
            skip_button = nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.grey, custom_id="skip_button")
            skip_button.callback = self.on_skip
            self.add_item(skip_button)

        # Row 3: Loot Master control (Next Turn)
        button_label = "Next Turn"
        if is_picking_turn and (session["current_turn"] == len(session["rolls"]) - 1):
            button_label = "Next Turn (Loop to #1)"
        
        next_turn_button = nextcord.ui.Button(label=button_label, style=nextcord.ButtonStyle.blurple, emoji="▶️", custom_id="next_turn_button")
        next_turn_button.callback = self.on_next_turn
        self.add_item(next_turn_button)

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session: return False
        
        invoker_id = session["invoker_id"]
        custom_id = interaction.data.get("custom_id")
        
        if custom_id == "next_turn_button":
            if interaction.user.id != invoker_id:
                await interaction.response.send_message("🔒 Only the Loot Master can advance the turn!", ephemeral=True)
                return False
            return True
        
        if session["current_turn"] >= 0 and session["current_turn"] < len(session["rolls"]):
            current_picker_id = session["rolls"][session["current_turn"]]["member"].id
            if interaction.user.id == current_picker_id or interaction.user.id == invoker_id:
                return True
        
        await interaction.response.send_message("🛡️ It is not your turn to act!", ephemeral=True)
        return False

    async def update_message(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return

        # Determine message content
        if not self._are_items_left(session):
            content_message = "✅ **All items have been assigned! Looting has concluded!**"
            self.clear_items()
            loot_sessions.pop(self.session_id, None)
        elif session["current_turn"] == -1:
            content_message = "🎁 **Loot distribution is ready!** The Loot Master can start by clicking 'Next Turn'."
        else:
            picker = session["rolls"][session["current_turn"]]["member"]
            content_message = f"**It is now {picker.mention}'s turn to pick an item!**"
        
        # Build the pretty embed description
        description_lines = []
        for item in session["items"]:
            if item["assigned_to"]:
                # Fetch member object to get their mention string
                member = bot.get_user(item["assigned_to"]) or await bot.fetch_user(item["assigned_to"])
                description_lines.append(f"✅ **{item['name']}**\n> Assigned to {member.mention}")
            else:
                description_lines.append(f"❌ **{item['name']}**")
        
        embed = interaction.message.embeds[0]
        embed.description = "\n\n".join(description_lines) # Use double newline for spacing
        
        self.update_components()
        await interaction.message.edit(content=content_message, embed=embed, view=self)

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
            item_index = int(index_str)
            if 0 <= item_index < len(session["items"]):
                session["items"][item_index]["assigned_to"] = current_picker_id
        
        session["selected_items"] = None
        self._advance_turn(session)
        await self.update_message(interaction)

    async def on_skip(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        self._advance_turn(session)
        await self.update_message(interaction)

    async def on_next_turn(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        self._advance_turn(session)
        await self.update_message(interaction)

# --- MODAL ---
class LootModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__("Loot Distribution Setup")
        self.loot_items = nextcord.ui.TextInput(
            label="Loot Items (One Per Line)",
            placeholder="Thunderfury, Blessed Blade of the Windseeker\nOld Republic Jedi Master Cloak\n...",
            required=True,
            style=nextcord.TextInputStyle.paragraph
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        voice_channel = interaction.user.voice.channel
        if not voice_channel:
            await interaction.response.send_message("❌ You seem to have left the voice channel.", ephemeral=True)
            return

        await interaction.response.defer() # Acknowledge the interaction

        members = voice_channel.members
        if not members:
            await interaction.followup.send("Error: There is nobody in your voice channel.", ephemeral=True)
            return

        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        rolls.sort(key=lambda x: x['roll'], reverse=True)

        # Announce who started
        await interaction.channel.send(f"🎉 **Loot roll started by {interaction.user.mention}!**")

        # Build and send roll order embed
        order_embed = nextcord.Embed(title="🎲 Loot Roll Order", color=nextcord.Color.gold())
        order_text = "\n".join([f"**{i+1}.**" for i, r in enumerate(rolls)])
        name_text = "\n".join([r['member'].mention for r in rolls])
        roll_text = "\n".join([f"**{r['roll']}**" for r in rolls])
        order_embed.add_field(name="Order", value=order_text, inline=True)
        order_embed.add_field(name="Name", value=name_text, inline=True)
        order_embed.add_field(name="Roll (1-100)", value=roll_text, inline=True)
        await interaction.channel.send(embed=order_embed)
        
        items_data = [{"name": line.strip(), "assigned_to": None} for line in self.loot_items.value.split('\n') if line.strip()]
        if not items_data:
            await interaction.followup.send("⚠️ No valid items were entered.", ephemeral=True)
            return

        # Build and send initial loot panel
        initial_description = "\n\n".join([f"❌ **{item['name']}**" for item in items_data])
        loot_embed = nextcord.Embed(title="🎁 Loot Distribution", description=initial_description, color=0x228B22) # Forest Green
        loot_embed.set_footer(text="RNGenie | Turn-based Loot")
        
        view = LootControlView(interaction.id) # Use interaction ID as session ID
        view.update_components() # Manually update components for the first message
        
        loot_message = await interaction.followup.send(
            content="🎁 **Loot distribution is ready!** The Loot Master can start by clicking 'Next Turn'.",
            embed=loot_embed,
            view=view
        )
        
        # Use message ID as the key for the session view
        view.session_id = loot_message.id
        loot_sessions[loot_message.id] = {
            "rolls": rolls, "items": items_data, "current_turn": -1,
            "invoker_id": interaction.user.id, "selected_items": None
        }

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
keep_alive() # Start the web server
bot.run(os.getenv("DISCORD_TOKEN"))
