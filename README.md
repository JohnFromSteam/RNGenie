# RNGenie - The Fair Loot Distribution Discord Bot

RNGenie is a powerful and intuitive Discord bot designed to manage turn-based loot distribution for games like World of Warcraft, Final Fantasy XIV, or any other activity where items need to be divided fairly among a group.

It automates the entire process from rolling for turn order to selecting items, ensuring a smooth and transparent experience for everyone in your voice channel.


*(Feel free to replace this GIF with one of your own bot in action!)*

## ✨ Key Features

*   **Slash Command Integration:** Easy to start with a simple `/loot` command.
*   **Modal Item Entry:** A popup window lets you paste a list of all loot items at once.
*   **Automatic Voice Channel Rolling:** The bot automatically includes everyone in your current voice channel in a `/roll 1-100`.
*   **Sorted Turn Order:** Displays a clear, embedded message showing the roll results and the final picking order.
*   **Fully Interactive UI:** Uses modern Discord UI components like buttons and multi-select dropdowns for a seamless user experience.
*   **Turn-Based Selection:** The bot enforces the turn order, only allowing the current person to select an item.
*   **Multi-Item Selection:** Users can claim multiple items in a single turn if desired.
*   **Loot Master & User Controls:** The person whose turn it is can select items or skip. The person who started the session (the "Loot Master") can also skip turns for users or advance to the next turn.
*   **Real-Time Updates:** The main loot message updates instantly as items are claimed, showing who got what.
*   **Session Management:** The bot cleanly concludes the session once all items are assigned.

## 🚀 How It Works (User Guide)

1.  **Join a Voice Channel:** Gather all participants in a single voice channel.
2.  **Start the Loot Roll:** One person (the "Loot Master") types the `/loot` command in a text channel.
    
3.  **Enter Loot Items:** A modal window will pop up. Paste your list of loot items, with each item on a new line, and click "Submit".
    4.  **Roll for Order:** The bot announces who started the roll and then automatically rolls for every member in the voice channel. It posts an embed with the sorted turn order.
    
5.  **Distribute Loot:** A final control panel message is created. The Loot Master clicks the "▶️ Next Turn" button to begin with the #1 roller.
6.  **Pick an Item:** The person whose turn it is can now use the dropdown to select one or more items and click "Assign Selected Item(s)". They can also choose to "Skip Turn".
    *   If the picker is unavailable, the Loot Master can also use the controls on their behalf.
7.  **Process Repeats:** The process continues in order until all items have been assigned. The bot will then announce that the session is complete.
    ![Loot Complete](https.i.imgur.com/8Qp4wYf.png)

## 🔧 Setup and Installation (For Self-Hosting)

Follow these steps to get your own instance of RNGenie running.

### Prerequisites

*   Python 3.8 or newer.
*   A Discord Bot Application.

### 1. Create a Discord Bot

1.  Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a "New Application".
2.  Navigate to the "Bot" tab and click "Add Bot".
3.  Under the bot's settings, enable the **SERVER MEMBERS INTENT** and **MESSAGE CONTENT INTENT** under "Privileged Gateway Intents". This is **required** for the bot to see who is in a voice channel as well as message content.
4.  Click "Reset Token" to reveal your bot's token. **Keep this secret and secure!**

### 2. Clone the Repository

```bash
git clone https://github.com/JohnFromSteam/RNGenie.git
cd RNGenie
```

### 3. Install Dependencies

Create a file named `requirements.txt` with the following content:

```
nextcord
python-dotenv
```

Then, run the following command in your terminal to install the necessary Python libraries:

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

1.  Create a file in the main directory named `.env`.
2.  Add the following line to the file, replacing `YOUR_BOT_TOKEN_HERE` with the token you got from the Discord Developer Portal.

```
DISCORD_TOKEN=YOUR_BOT_TOKEN_HERE
```

### 5. Invite the Bot to Your Server

1.  In the Developer Portal, go to the "OAuth2" -> "URL Generator" tab.
2.  Select the scopes `bot` and `applications.commands`.
3.  In the "Bot Permissions" section, select the following permissions:
    *   Send Messages
    *   Embed Links
    *   Read Message History (to find the message to update)
4.  Copy the generated URL at the bottom and paste it into your browser to invite the bot to your server.

### 6. Run the Bot

Finally, run the Python script from your terminal:

```bash
python RNGenie.py
```

Your bot should now be online and ready to use the `/loot` command! You can do this manually for your server so long as your script is running, or use a server provider and configure through that end.

## 📜 Code Overview

*   **`RNGenie.py`**: The main file containing all the bot's logic.
    *   **`loot_sessions`**: A global dictionary that acts as a simple database, storing the state of all active loot rolls using the message ID as the key.
    *   **`LootModal`**: A `nextcord.ui.Modal` class that captures the initial list of items from the user.
    *   **`LootControlView`**: A `nextcord.ui.View` class that dynamically generates and manages the buttons and dropdown menu for the main loot panel. This is the heart of the interactive component.
    *   **`@bot.slash_command(name="loot")`**: The entry point that initiates the entire process.
*   **`keep_alive.py`**: A simple Flask web server used to keep the bot running 24/7 on hosting platforms like [Replit](https://replit.com/).

## 📄 License

This project is licensed under the MIT License. See the `LICENSE` file for details.
