# RNGenie: A Discord Loot Distribution Bot

RNGenie is a powerful yet easy-to-use Discord bot designed to manage turn-based loot distribution for games and events. It uses a fair "snake draft" system, a modern slash command (`/loot`), and a dynamic two-message interface that keeps your chat clean and merges into a final summary.

[![RNGenie: A Discord Loot Distribution Bot](https://img.youtube.com/vi/gKJX9DPIpS0/maxresdefault.jpg)](https://www.youtube.com/watch?v=gKJX9DPIpS0)

---

## Features

- **Slash command**: `/loot` opens a modal where the Loot Manager pastes the item list (one item per line).
- **Two-message UI + item message**:  
  - **(1/2)** Remaining loot list (updates live)  
  - **(2/2)** Control panel (Loot Manager controls)  
  - **Third message** contains item selects and action buttons (Assign / Skip / Undo).
- **Item stacking**: `Nx` syntax supported (e.g., `5x Health Potion`).
- **Auto-detect participants**: finds members in the Loot Manager’s voice channel (max **20** participants).
- **Randomized roll order + tie-breaker**: primary roll (1–100); ties get a random tiebreaker; sorting by `(roll, tiebreak)` descending.
- **Fair snake draft**: order is snake (1 → 2 → 3, then 3 → 2 → 1). The bot tracks `round`, `direction`, and `just_reversed`.
- **Multi-select + explicit assign**: you can select multiple items and click **Assign Selected** to finalize. Selecting does **not** immediately assign — it updates session state so reopened selects show previous selections.
- **Skip & Undo**:  
  - **Skip Turn** advances the draft.  
  - **Undo** is available only **next to Skip Turn** in the item dropdown view. The control panel no longer shows a duplicate Undo.
  - Undo reverts the most recent assignment or skip. Only the Loot Manager (session invoker) can Undo.
- **Per-session locks & optimizations**: avoids race conditions and reduces unnecessary edits to Discord.
- **Inactivity timeout**: sessions expire after **10 minutes** (configurable). On timeout the bot posts a final summary and cleans up state.


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

1. Join a voice channel with everyone who will be included in the loot roll.
2. In a text channel, run `/loot`.
3. Paste/type the items in the modal (one per line). Use `Nx` (e.g., `3x Mana Potion`) to stack items.
4. Submit.
5. The bot posts:
    * **(1/2)** Remaining items (updates live)
    * **(2/2)** Control panel (invoker controls)
    * Third message: item selects + Assign Selected / Skip Turn / Undo
6. **Before starting**: Loot Manager may remove participants via the dropdown on message (2/2).
7. Click **Start Loot Assignment!** to begin the snake draft.
8. For each pick:
    * The current picker (or Loot Manager) opens the select(s), chooses one or more items, and clicks **Assign Selected** to confirm assignment. Selecting values updates session state but does not assign until **Assign Selected** is clicked.
    * Click **Skip Turn** to pass.
    * Loot Manager may click **Undo** (only next to Skip Turn) to revert the most recent assignment or skip.
9. When all items are assigned or inactivity timeout occurs, a final summary replaces the control panel and the session is cleaned up.

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
