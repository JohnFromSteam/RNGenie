# RNGenie: A Discord Loot Distribution Bot

RNGenie is a powerful yet easy-to-use Discord bot designed to manage turn-based loot distribution for games and events. It uses a fair "snake draft" system, a modern slash command (`/loot`), and a dynamic two-message interface that keeps your chat clean and merges into a final summary.

[![RNGenie: A Discord Loot Distribution Bot](https://img.youtube.com/vi/gKJX9DPIpS0/maxresdefault.jpg)](https://www.youtube.com/watch?v=gKJX9DPIpS0)

---

## Features

-   **Slash Command Integration**: Simply type `/loot` to start a new session.
-   **Item Stacking**: Easily add multiple copies of the same item using `Nx` syntax (e.g., `5x Health Potion`).
-   **Automatic Member Detection**: Instantly finds all members in your current voice channel.
-   **Participant Management**: The Loot Master can remove participants from the roll order *before* the loot assignment begins.
-   **Randomized Roll Order**: Assigns a random roll (1-100) to each member and sorts them from highest to lowest, with fair, random tie-breaking.
-   **Fair Snake Draft System**: The turn order is a "snake draft" (e.g., 1 -> 2 -> 3, then 3 -> 2 -> 1) to ensure fairness.
-   **Clean Two-Message UI**: Separates the remaining loot list from the main control panel to reduce clutter. Both messages update live.
-   **Merged Final Summary**: When the session ends or times out, the two messages merge into a single, clean summary.
-   **Shared Control**: Both the Loot Master and the person whose turn it is can select and assign items or skip the turn.
-   **Undo Last Action**: The Loot Master can undo the most recent assignment or skip with a single click.
-   **Persistent Item Numbering**: Items in the loot list keep their original number throughout the session, preventing confusion.
-   **Automatic Timeout**: If a session is inactive for 30 minutes, it automatically times out and posts the final summary.

---

## Setup and Installation

You can run RNGenie locally for testing or deploy it to a 24/7 hosting provider.

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
    -   Under **Privileged Gateway Intents**, enable **Presence Intent**, **Server Members Intent**, and **Message Content Intent**.
    -   Click "Reset Token" to reveal your bot's token. **Keep this token private!**

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
        -   `Use Slash Commands`
        -   `Connect`
    -   Copy the generated URL and paste it into your browser to invite the bot to your server.

7.  **Run the Bot:**
    ```sh
    python RNGenie.py
    ```
    You will see a "Logged in as..." message in your terminal. The bot is now online and ready to use!

---

### 2. Deploying to a 24/7 Host (PaaS or VPS)

To run the bot continuously, you need to deploy it to a server.

#### Option A: PaaS (Platform as a Service) - Easiest Method
Platforms like **Railway** or **Fly.io** simplify deployment. They generally follow these steps:

1.  **Link Your GitHub Repository:** Connect your hosting account to the GitHub repository containing the bot's code.
2.  **Configure Build Settings:**
    -   **Build Command**: `pip install -r requirements.txt`
    -   **Start Command**: `python RNGenie.py`
3.  **Set Environment Variables:** In your host's dashboard, find the "Environment Variables" or "Secrets" section and add your bot's token.
    -   **Variable Name**: `DISCORD_TOKEN`
    -   **Value**: `YOUR_BOT_TOKEN_HERE`
4.  **Deploy:** The platform will automatically build and run your bot.

#### Option B: VPS (Virtual Private Server) - More Control
A VPS from providers like **DigitalOcean**, **Linode**, or **Vultr** gives you a full Linux server for maximum stability.

1.  **Get a VPS:** Provision a new server, typically running a modern OS like Ubuntu 22.04.
2.  **Connect via SSH:** Use a terminal to connect to your server's IP address.
3.  **Install Prerequisites:**
    ```sh
    sudo apt update
    sudo apt install python3 python3-pip python3-venv git -y
    ```
4.  **Clone Your Repository:**
    ```sh
    git clone https://github.com/JohnFromSteam/RNGenie.git
    cd RNGenie
    ```
5.  **Set Up Environment:**
    -   Install dependencies into a virtual environment as described in the "Running Locally" section.
    -   Create the `.env` file directly on the server with your `DISCORD_TOKEN`. **Do not commit your `.env` file to GitHub.**

6.  **Create a Service to Run the Bot Persistently:**
    We will use `systemd`, the standard process manager for modern Linux, to keep the bot online.

    -   Create a service file:
        ```sh
        sudo nano /etc/systemd/system/rngenie.service
        ```
    -   Paste the following configuration into the file. **Remember to replace `/path/to/your/RNGenie` and `your_username` with your actual details.**
        ```ini
        [Unit]
        Description=RNGenie Discord Bot
        After=network.target

        [Service]
        User=your_username # Replace with your linux username (e.g., root, ubuntu)
        Group=your_group   # Replace with your linux group (e.g., root, ubuntu)
        WorkingDirectory=/path/to/your/RNGenie 
        ExecStart=/path/to/your/RNGenie/venv/bin/python RNGenie.py
        Restart=always
        RestartSec=3

        [Install]
        WantedBy=multi-user.target
        ```
    -   Save the file (`Ctrl+X`, then `Y`, then `Enter`).

7.  **Enable and Start the Service:**
    -   Reload `systemd` to recognize the new file: `sudo systemctl daemon-reload`
    -   Enable the service to start automatically on boot: `sudo systemctl enable rngenie.service`
    -   Start the bot immediately: `sudo systemctl start rngenie.service`
    -   You can check the bot's status and logs with: `sudo systemctl status rngenie.service` and `journalctl -u rngenie -f`.

Your bot is now running persistently on the server!

---

## Usage

1.  Join a voice channel with everyone who will be part of the loot roll.
2.  In a text channel, type the slash command `/loot`.
3.  A modal window will pop up. Paste or type the list of items to be distributed, one item per line.
    -   **Tip:** Use the `Nx` prefix to add multiple copies of an item (e.g., `5x Health Potion`).
4.  Click "Submit".
5.  The bot posts two messages: **(1/2)** shows the list of remaining items, and **(2/2)** is the control panel.
6.  **Before starting:** As the Loot Master, you can select members from the dropdown on message (2/2) to remove them from the roll.
7.  **To start:** Click the "Start Loot Assignment!" button.
8.  The current picker or the Loot Master can now use the dropdowns and buttons to assign items or skip turns. The Loot Master also has access to an `Undo` button to revert the last action.

---

## Customization

You can easily change the bot's color scheme to match your server's theme.

1.  Open `RNGenie.py`.
2.  Find the `BOT SETUP` section at the top of the file.
3.  Modify the ANSI color code variables:
    ```python
    # ANSI color codes for formatting the text blocks in Discord messages.
    ANSI_RESET = "\u001b[0m"
    ANSI_HEADER = "\u001b[0;33m" # Color for titles like "Roll Order"
    ANSI_USER = "\u001b[0;34m"   # Color for user display names
    ```
4.  You can change the number (`33`, `34`, etc.) to any of the following standard colors:
    -   `31`: Red
    -   `32`: Green
    -   `33`: Yellow/Orange
    -   `34`: Blue
    -   `35`: Magenta/Pink
    -   `36`: Cyan
    -   `37`: White/Light Grey
