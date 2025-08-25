import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands
import random
from keep_alive import keep_alive

# --- BOT SETUP ---
intents = nextcord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# This dictionary will store active loot sessions
loot_sessions = {}

# --- DYNAMIC UI VIEW FOR LOOT CONTROL ---
class LootControlView(nextcord.ui.View):
    def __init__(self, session_id):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.update_components()

    def _are_items_left(self, session):
        """Helper to check for unassigned items."""
        return any(not item["assigned_to"] for item in session["items"])

    def _advance_turn(self, session):
        """Advances the turn, looping if necessary."""
        if not self._are_items_left(session):
            session["current_turn"] = len(session["rolls"]) # Set to end state
            return

        num_rollers = len(session["rolls"])
        if num_rollers > 0:
            session["current_turn"] = (session["current_turn"] + 1) % num_rollers

    def update_components(self):
        """Dynamically adds/updates components based on session state."""
        session = loot_sessions.get(self.session_id)
        if not session: self.clear_items(); return

        self.clear_items()
        is_picking_turn = session["current_turn"] >= 0 and len(session["rolls"]) > session["current_turn"]
        
        if is_picking_turn:
            available_items = [item for item in session["items"] if not item["assigned_to"]]
            if available_items:
                # --- UPGRADE: Multi-select dropdown ---
                options = [nextcord.SelectOption(label=item["name"], value=item["name"]) for item in available_items]
                item_select = nextcord.ui.Select(
                    placeholder="Choose one or more items to claim...",
                    options=options,
                    custom_id="item_select",
                    min_values=1,
                    max_values=len(available_items) # Allow selecting all available items
                )
                item_select.callback = self.on_item_select
                self.add_item(item_select)

            assign_button = nextcord.ui.Button(label="Assign Selected Item(s)", style=nextcord.ButtonStyle.green, custom_id="assign_button")
            assign_button.callback = self.on_assign
            self.add_item(assign_button)
            
            skip_button = nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.grey, custom_id="skip_button")
            skip_button.callback = self.on_skip
            self.add_item(skip_button)

        button_label = "Next Turn"
        if is_picking_turn and self._are_items_left(session):
            if (session["current_turn"] == len(session["rolls"]) - 1):
                button_label = "Next Turn (Loop to #1)"

        next_turn_button = nextcord.ui.Button(label=button_label, style=nextcord.ButtonStyle.blurple, emoji="▶️", custom_id="next_turn_button")
        next_turn_button.callback = self.on_next_turn
        self.add_item(next_turn_button)

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session: return False
        
        invoker_id = session["invoker_id"]
        custom_id = interaction.data.get("custom_id")
        
        # Only the Loot Master can advance the turn
        if custom_id == "next_turn_button":
            if interaction.user.id != invoker_id:
                await interaction.response.send_message("Only the Loot Master can advance the turn!", ephemeral=True)
                return False
            return True
        
        # --- UPGRADE: Loot Master can use all other controls ---
        if session["current_turn"] >= 0 and len(session["rolls"]) > session["current_turn"]:
            current_picker_id = session["rolls"][session["current_turn"]]["member"].id
            # Allow action if user is the picker OR the loot master
            if interaction.user.id == current_picker_id or interaction.user.id == invoker_id:
                return True
        
        await interaction.response.send_message("It is not your turn to act!", ephemeral=True)
        return False

    async def update_message(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session: return

        if session["current_turn"] == -1:
            content_message = "**Loot distribution is ready!** The Loot Master can start by clicking 'Next Turn'."
        elif not self._are_items_left(session):
            content_message = "✅ **All items have been assigned! Looting has concluded!**"
            self.clear_items()
            loot_sessions.pop(self.session_id, None)
        else:
            picker = session["rolls"][session["current_turn"]]["member"]
            content_message = f"**It is now {picker.mention}'s turn to pick an item!**"
        
        description = ""
        for item in session["items"]:
            if item["assigned_to"]:
                member = bot.get_user(item["assigned_to"]) or await bot.fetch_user(item["assigned_to"])
                description += f"✅ **{item['name']}** — Assigned to {member.mention}\n"
            else:
                description += f"❌ **{item['name']}**\n"
        
        embed = interaction.message.embeds[0]
        embed.description = description
        
        self.update_components()
        await interaction.message.edit(content=content_message, embed=embed, view=self)

    # --- Callbacks ---
    async def on_item_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        # Now stores a list of selections
        session["selected_items"] = interaction.data["values"]
        await interaction.response.defer()

    async def on_assign(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        selected_list = session.get("selected_items")
        if not selected_list:
            await interaction.response.send_message("You need to select at least one item from the dropdown first!", ephemeral=True)
            return
        
        # IMPORTANT: Item is assigned to the current picker, regardless of who clicked
        current_picker_id = session["rolls"][session["current_turn"]]["member"].id
        for selected_name in selected_list:
            for item in session["items"]:
                if item["name"] == selected_name:
                    item["assigned_to"] = current_picker_id
        
        session["selected_items"] = None # Clear selection
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
        self.loot_items = nextcord.ui.TextInput(label="Loot Items (One Per Line)", placeholder="Sword of Valor...", required=True, style=nextcord.TextInputStyle.paragraph)
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        # Defer the interaction immediately to avoid a timeout.
        await interaction.response.defer()

        invoker = interaction.user
        if not invoker.voice or not invoker.voice.channel:
            # Since we deferred, we must use followup.send() for the first visible response.
            await interaction.followup.send("Error: You must be in a voice channel.", ephemeral=True); return

        members = invoker.voice.channel.members
        if not members:
            await interaction.followup.send("Error: There is nobody in your voice channel.", ephemeral=True); return

        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        rolls.sort(key=lambda x: x['roll'], reverse=True)

        # --- UPGRADE: Announce who started the loot roll ---
        await interaction.channel.send(f"🎉 **Loot roll started by {invoker.mention}!**")

        order_embed = nextcord.Embed(title="🎲 Loot Roll Order", color=nextcord.Color.gold())
        order_text, name_text, roll_text = "", "", ""
        for i, r in enumerate(rolls):
            order_text += f"**{i+1}.**\n"; name_text += f"{r['member'].mention}\n"; roll_text += f"**{r['roll']}**\n"
        order_embed.add_field(name="Order", value=order_text, inline=True); order_embed.add_field(name="Name", value=name_text, inline=True); order_embed.add_field(name="Roll (1-100)", value=roll_text, inline=True)
        await interaction.channel.send(embed=order_embed)

        item_list_raw = self.loot_items.value.split('\n')
        items_data = [{"name": line.strip(), "assigned_to": None} for line in item_list_raw if line.strip()]
        if not items_data: return

        initial_description = ""
        for item in items_data:
            initial_description += f"❌ **{item['name']}**\n"
        loot_embed = nextcord.Embed(title="🎁 Loot Distribution", description=initial_description, color=nextcord.Color.dark_green())
        initial_content = "**Loot distribution is ready!** The Loot Master can start by clicking 'Next Turn'."
        
        loot_message = await interaction.channel.send(content=initial_content, embed=loot_embed)
        
        session_id = loot_message.id
        loot_sessions[session_id] = {
            "rolls": rolls, "items": items_data, "current_turn": -1,
            "invoker_id": invoker.id, "selected_items": None
        }
        
        view = LootControlView(session_id)
        await loot_message.edit(view=view)

# --- SLASH COMMAND ---
@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("You need to be in a voice channel!", ephemeral=True)
        return
    await interaction.response.send_modal(LootModal())

# --- EVENT LISTENERS ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

# --- RUN ---
keep_alive()
load_dotenv() # Load the .env file
bot.run(os.getenv("DISCORD_TOKEN"))
