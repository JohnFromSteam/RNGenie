# RNGenie: A Discord Loot Distribution Bot

RNGenie is a powerful yet easy-to-use Discord bot designed to manage turn-based loot distribution for games and events. It uses a fair "snake draft" system, a modern slash command (`/loot`), and a dynamic multi-message interface that keeps your chat clean and merges into a final summary upon completion.

The video below is a little outdated as it does not have all the current features and UI improvements showcased, but it does highlight how it works.
[![RNGenie: A Discord Loot Distribution Bot](https://img.youtube.com/vi/gKJX9DPIpS0/maxresdefault.jpg)](https://www.youtube.com/watch?v=gKJX9DPIpS0)

---

## Features

- **Slash Command**: `/loot` opens a modal where the Loot Manager pastes the item list (one item per line).
- **Multi-Message UI**:
  - **(1/2)** A dedicated message showing the remaining loot list, which updates live.
  - **(2/2)** The main control panel, showing roll order, assigned items, and Loot Manager controls.
  - A third, temporary message appears for the current picker, containing item selection dropdowns and action buttons (Assign / Skip / Undo).
- **Item Stacking**: `Nx` syntax is supported for quickly adding multiple copies of an item (e.g., `5x Health Potion`).
- **Auto-Detect Participants**: Automatically finds and includes all members in the Loot Manager’s voice channel (max **20** participants).
- **Randomized Roll Order + Tie-Breaker**: A primary roll (1–100) determines the initial order. Any ties are resolved by a second random tie-breaker roll, ensuring a fair and unique sequence.
- **Fair Snake Draft**: The pick order follows a snake pattern (1 → 2 → 3, then reverses 3 → 2 → 1) to ensure fairness across rounds.
- **Multi-Select & Explicit Assignment**: Pickers can select multiple items from dropdowns before clicking a single **Assign Selected** button to finalize their turn.
- **Skip & Undo**:
  - **Skip Turn** allows a user to pass on their pick.
  - **Undo**, available only to the Loot Manager, reverts the most recent assignment or skip, restoring the session to its previous state.
- **Per-Session Locks & Optimizations**: Uses `asyncio.Lock` to prevent race conditions during rapid actions and minimizes unnecessary message edits to respect Discord API limits.
- **Inactivity Timeout**: Sessions automatically expire after **10 minutes** of inactivity. On timeout, the bot posts a final summary and cleans up all temporary messages.

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
    A `requirements.txt` file should be created containing `nextcord` and `python-dotenv`.
    ```sh
    pip install nextcord python-dotenv
    ```

4.  **Create a Discord Bot Application:**
    -   Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a "New Application".
    -   Navigate to the "Bot" tab and click "Add Bot".
    -   Under **Privileged Gateway Intents**, enable the **Server Members Intent**. This is required for the bot to see who is in the voice channel.
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
    -   Copy the generated URL and paste it into your browser to invite the bot to your server.

7.  **Run the Bot:**
    ```sh
    python RNGenie.py
    ```
    You should see a message in your terminal confirming the bot is ready. It is now online and ready to use!

---

### 2. Deploying to a 24/7 Host (PaaS or VPS)

To run the bot continuously, you need to deploy it to a server.

#### Option A: PaaS (Platform as a Service) - Easiest Method
Platforms like **Railway** or **Fly.io** simplify deployment. They generally follow these steps:

1.  **Link Your GitHub Repository:** Connect your hosting account to the GitHub repository containing the bot's code.
2.  **Configure Build Settings:**
    -   **Build Command**: `pip install -r requirements.txt` (ensure you have this file).
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

1.  Join a voice channel with everyone who will participate in the loot roll.
2.  In a text channel (but not a voice-linked text chat), run `/loot`.
3.  Paste or type the items into the modal window (one per line). Use `Nx` (e.g., `3x Mana Potion`) for multiple copies of an item.
4.  Submit the modal.
5.  The bot posts the interface:
    *   **(1/2)** A message listing all remaining items.
    *   **(2/2)** The control panel showing the roll order and assigned items.
    *   A third message appears only when a pick is active, prompting the current user to choose.
6.  **Before starting**: The Loot Manager can use the dropdown on message (2/2) to remove participants if needed.
7.  Click **📜 Start Loot Assignment!** to begin the draft.
8.  For each pick:
    *   The current picker (or the Loot Manager) uses the dropdown(s) in the third message to select one or more items.
    *   Click **Assign Selected** to confirm the choice and advance the turn.
    *   Alternatively, click **Skip Turn** to pass.
    *   The Loot Manager may click **Undo** to revert the most recent assignment or skip.
9.  When all items are assigned, or if the session times out, the control panel is replaced with a final summary, and all other session messages are cleaned up.

---

## Customization

You can easily change the bot's color scheme by editing the ANSI color constants at the top of `RNGenie.py`. These variables control the colors used in the code-block-formatted messages.

1.  Open `RNGenie.py`.
2.  Find the ANSI color constants defined near the top of the file.
3.  Modify the variables to change the bot's appearance. Each variable controls a specific element:

    ```python
    # ANSI color constants used to produce colored code-block output in Discord messages.
    CSI = "\x1b["
    RESET = CSI + "0m"
    BOLD = CSI + "1m"
    RED = CSI + "31m"      # Headers for Remaining/Unclaimed loot
    GREEN = CSI + "32m"    # Headers for Assigned Items/Completion
    YELLOW = CSI + "33m"   # Header for the "Roll Order" block
    BLUE = CSI + "34m"     # Color for user display names
    MAGENTA = CSI + "35m"  # Not currently used
    CYAN = CSI + "36m"     # Not currently used
    ```

4.  You can change the number (`31`, `32`, etc.) to any of the following standard colors:
    -   `30`: Black
    -   `31`: Red
    -   `32`: Green
    -   `33`: Yellow
    -   `34`: Blue
    -   `35`: Magenta
    -   `36`: Cyan
    -   `37`: White
