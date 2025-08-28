# RNGenie: A Discord Loot Distribution Bot

RNGenie is a powerful yet easy-to-use Discord bot designed to manage turn-based loot distribution for games and events. It uses a fair "snake draft" system, a modern slash command (`/loot`), and a dynamic, single-message interface to keep your chat clean and the process organized.

## Features

-   **Slash Command Integration**: Simply type `/loot` to start a new session.
-   **Automatic Member Detection**: Instantly finds all members (including other bots) in your current voice channel.
-   **Randomized Roll Order**: Assigns a random roll (1-100) to each member and sorts them from highest to lowest.
-   **Fair Snake Draft System**: The turn order is a "snake draft" (e.g., 1 -> 2 -> 3, then 3 -> 2 -> 1, then 1 -> 2 -> 3, etc.) to ensure fairness, giving the players at the end of the order a double-pick.
-   **Live Updating UI**: A single, clean message is created that updates in place as loot is assigned, preventing chat spam.
-   **Loot Master Control**: Only the person who initiated the `/loot` command can assign items or skip turns, giving them full control.
-   **Fully Customizable Colors**: Easily change the color of headers, usernames, and tags using ANSI color codes at the top of the script.

---

## Setup and Installation

You can run RNGenie locally for testing or deploy it to a 24/7 hosting provider like [Render](https://render.com/).

### 1. Running Locally (Recommended for Testing)

**Prerequisites:**
-   Python 3.8 or newer
-   Git

**Steps:**

1.  **Clone the Repository:**
    ```sh
    git clone https://github.com/JohnFromSteam/RNGenie.git
    cd RNGenie
    ```

2.  **Create a Virtual Environment (Recommended):**
    ```sh
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```

3.  **Install Dependencies:**
    A `requirements.txt` file is included with all necessary libraries.
    ```sh
    pip install -r requirements.txt
    ```

4.  **Create a Discord Bot Application:**
    -   Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a "New Application".
    -   Go to the "Bot" tab and click "Add Bot".
    -   Under "Privileged Gateway Intents", enable **SERVER MEMBERS INTENT** and **VOICE STATE INTENT**. This is crucial for the bot to see members in voice channels.
    -   Click "Reset Token" to reveal your bot's token. Keep this safe!

5.  **Create a `.env` File:**
    -   In the project folder, create a new file named `.env`.
    -   Add your bot token to this file:
      ```
      DISCORD_TOKEN=YOUR_BOT_TOKEN_HERE
      ```

6.  **Invite the Bot to Your Server:**
    -   In the Developer Portal, go to "OAuth2" -> "URL Generator".
    -   Select the scopes: `bot` and `applications.commands`.
    -   Under "Bot Permissions", select the following:
        -   `View Channels`
        -   `Send Messages`
        -   `Read Message History`
        -   `Connect`
    -   Copy the generated URL and paste it into your browser to invite the bot to your server.

7.  **Run the Bot:**
    Use the `RNGenie.py` file for local development.
    ```sh
    python RNGenie.py
    ```
    You will see a "Logged in as..." message in your terminal. The bot is now online and ready to use!
    Type `/loot` in a text channel and enjoy!

---

### 2. Deploying to a Server (Render.com)

To run the bot 24/7, you can deploy it to a free hosting service like Render. This setup uses a small web server to keep the bot alive on free plans.

**Steps:**

1.  **Fork this Repository** on GitHub.
2.  **Create a Render Account** and connect it to your GitHub account.
3.  **Create a New "Web Service"** on the Render dashboard.
4.  **Connect Your Repository**: Select the repository you just forked.
5.  **Configure the Service:**
    -   **Name**: Give your service a name (e.g., `rngenie-bot`).
    -   **Environment**: `Python 3`
    -   **Build Command**: `pip install -r requirements.txt`
    -   **Start Command**: `python RNGenie_deploy.py` (Make sure to use the `_deploy` version!)
6.  **Add Your Secret Token:**
    -   Go to the "Environment" tab for your new service.
    -   Under "Secret Files", click "Add Secret File".
    -   **Filename**: `.env`
    -   **Contents**: `DISCORD_TOKEN=YOUR_BOT_TOKEN_HERE`
    -   Click "Save Changes".
7.  **Create and Deploy:** Click "Create Web Service". Render will automatically build and deploy your bot.
8.  **(Optional but Recommended) Keep-Alive Service:**
    -   Render's free web services "sleep" after 15 minutes of inactivity. To keep your bot online, use a free service like [UptimeRobot](https://uptimerobot.com/).
    -   Create a new "HTTP(s)" monitor in UptimeRobot.
    -   For the URL, use the URL of your Render web service (e.g., `https://rngenie-bot.onrender.com`).
    -   Set the monitoring interval to 5-10 minutes. This will "ping" your bot and prevent it from sleeping.

---

## Usage

1.  Join a voice channel with the members who will be part of the loot roll.
2.  In any text channel, type the slash command `/loot`.
3.  A modal window will pop up. Enter the list of items to be distributed, one item per line.
4.  Click "Submit".
5.  The bot will post a message showing the randomized roll order and the loot interface.
6.  As the Loot Master, you can now use the buttons and dropdown menu to assign items to the person whose turn it is. The message will update live for everyone to see.

---

## Customization

You can easily change the bot's color scheme to match your server's theme.

1.  Open `RNGenie.py` or `RNGenie_deploy.py`.
2.  Find the `BOT SETUP` section at the top of the file.
3.  Modify the ANSI color code variables:
    ```python
    # ANSI color codes for direct color control
    ANSI_HEADER = "\u001b[0;33m"      # Yellow/Orange
    ANSI_USER = "\u001b[0;34m"        # Blue
    ANSI_NOT_TAKEN = "\u001b[0;31m"  # Red
    ANSI_ASSIGNED = "\u001b[0;32m"    # Green
    ```
4.  You can change the number (`33`, `34`, etc.) to any of the following:
    -   **31**: Red
    -   **32**: Green
    -   **33**: Yellow/Orange
    -   **34**: Blue
    -   **35**: Magenta/Pink
    -   **36**: Cyan
    -   **37**: White/Light Grey
