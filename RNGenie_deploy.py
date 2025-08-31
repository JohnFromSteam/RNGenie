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


# ===================================================================================================
# EMBED MESSAGE BUILDERS
# ===================================================================================================

def build_main_panel_embed(session, timed_out=False):
    """Builds the primary message with roll order, assigned items, and controls."""
    invoker = session["invoker"]
    rolls = session["rolls"]

    # --- Part 1: Main Embed & Description ---
    if timed_out:
        description = f"⌛ **The loot session has timed out due to 30 minutes of inactivity!**"
    elif not any(not item["assigned_to"] for item in session["items"]):
        description = f"✅ **All items have been assigned! Looting has concluded!**"
    else:
        description = f"🎉 **Loot roll started by {invoker.mention}!**"

    embed = nextcord.Embed(description=description, color=nextcord.Color.dark_gold())

    # --- Part 2: Roll Order Field ---
    roll_order_body = ""
    for i, r in enumerate(rolls):
        num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        roll_order_body += f"{num_emoji} {r['member'].display_name} (Roll: {r['roll']})\n"
    embed.add_field(name="🔢 Roll Order 🔢", value=roll_order_body, inline=False)

    # --- Part 3: Assigned Items Field ---
    assigned_header_text = "✅ Assigned Items ✅"
    if timed_out:
        assigned_header_text = "✅ Final Assigned Items ✅"
    
    assigned_items_body = ""
    assigned_items = {}
    for item in session["items"]:
        if item["assigned_to"]:
            assignee_id = item["assigned_to"]
            if assignee_id not in assigned_items: assigned_items[assignee_id] = []
            assigned_items[assignee_id].append(item["name"])

    if not assigned_items:
        assigned_items_body = "No items assigned yet."
    else:
        for i, roll_info in enumerate(rolls):
            member = roll_info["member"]
            if member.id in assigned_items:
                num_emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
                assigned_items_body += f"**{num_emoji} {member.display_name}**\n"
                for item_name in assigned_items[member.id]:
                    assigned_items_body += f"└ {item_name}\n"
    embed.add_field(name=assigned_header_text, value=assigned_items_body, inline=False)

    # --- Part 4: Unclaimed Items (on timeout/finish) ---
    if timed_out or not any(not item["assigned_to"] for item in session["items"]):
        remaining_items = [item for item in session["items"] if not item["assigned_to"]]
        if remaining_items:
            header_text = "❌ Unclaimed Items ❌"
            
            item_fields = []
            current_field = ""
            for item in remaining_items:
                line = f"{item['name']}\n"
                if len(current_field) + len(line) > 1024:
                    item_fields.append(current_field)
                    current_field = ""
                current_field += line
            item_fields.append(current_field)

            for i, field_content in enumerate(item_fields):
                field_name = header_text
                if len(item_fields) > 1:
                    field_name += f" ({i+1}/{len(item_fields)})"
                embed.add_field(name=field_name, value=field_content, inline=False)

    # --- Part 5: Footer ---
    if not timed_out and any(not item["assigned_to"] for item in session["items"]):
        if session["current_turn"] >= 0:
            picker = session["rolls"][session["current_turn"]]["member"]
            direction_text = "Normal Order" if session["direction"] == 1 else "Reverse Order"
            picker_emoji = NUMBER_EMOJIS.get(session['current_turn'] + 1, "👉")
            turn_text = "turn again!" if session.get("just_reversed", False) else "turn!"
            footer_text = (
                f"Round {session['round'] + 1} ({direction_text}) | {picker_emoji} It is now {picker.mention}'s {turn_text}\n"
                f"Loot Master {invoker.display_name} must select or skip."
            )
            embed.set_footer(text=footer_text)
        else:
            embed.set_footer(text=f"Loot is ready! {invoker.display_name} must click 'Start Loot Assignment!' to begin.")
            
    return embed

def build_remaining_items_embed(session):
    """Builds the separate embed for the list of remaining items."""
    remaining_items = [item for item in session["items"] if not item["assigned_to"]]
    if not remaining_items:
        return None

    embed = nextcord.Embed(color=nextcord.Color.dark_grey())
    header_text = "❌ Remaining Loot Items ❌"
    
    item_fields = []
    current_field = ""
    for item in remaining_items:
        line = f"{item['name']}\n"
        if len(current_field) + len(line) > 1024:
            item_fields.append(current_field)
            current_field = ""
        current_field += line
    item_fields.append(current_field)

    for i, field_content in enumerate(item_fields):
        field_name = header_text
        if len(item_fields) > 1:
            field_name += f" ({i+1}/{len(item_fields)})"
        embed.add_field(name=field_name, value=field_content, inline=False)

    return embed


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
        
        main_panel_embed = build_main_panel_embed(session)
        remaining_items_embed = build_remaining_items_embed(session)
        self.update_components()
        
        await interaction.message.edit(embed=main_panel_embed, view=self)

        remaining_message = session.get("remaining_message")
        if remaining_message:
            try:
                if remaining_items_embed:
                    await remaining_message.edit(embed=remaining_items_embed)
                else:
                    await remaining_message.delete()
                    session["remaining_message"] = None
            except nextcord.NotFound:
                session["remaining_message"] = None

        if not self._are_items_left(session):
            final_embed = build_main_panel_embed(session)
            await interaction.message.edit(embed=final_embed, view=None)
            loot_sessions.pop(self.session_id, None)

    async def on_timeout(self):
        session = loot_sessions.get(self.session_id)
        if not session: return
        try:
            channel = bot.get_channel(session["channel_id"])
            if channel:
                main_message = await channel.fetch_message(self.session_id)
                final_embed = build_main_panel_embed(session, timed_out=True)
                await main_message.edit(embed=final_embed, view=None)

                if session.get("remaining_message"):
                    await session["remaining_message"].delete()
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
            "just_reversed": False, "remaining_message": None
        }
        
        panel_embed = build_main_panel_embed(session)
        main_message = await interaction.followup.send(embed=panel_embed, view=LootControlView(0), wait=True)
        
        remaining_embed = build_remaining_items_embed(session)
        remaining_message = await interaction.channel.send(embed=remaining_embed) if remaining_embed else None
        
        session_id = main_message.id
        session["channel_id"] = main_message.channel.id
        session["remaining_message"] = remaining_message
        loot_sessions[session_id] = session
        
        final_view = LootControlView(session_id)
        await main_message.edit(view=final_view)


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
