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

# ... (The LootControlView class, LootModal class, /loot command, and on_ready event are IDENTICAL to your local version) ...
# (Paste the full classes and functions here)

# ===================================================================================================
# RUN SCRIPT
# ===================================================================================================

# Load environment variables (like the DISCORD_TOKEN) from a .env file.
load_dotenv()
# Start the keep-alive web server.
keep_alive()
# Start the bot.
bot.run(os.getenv("DISCORD_TOKEN"))
