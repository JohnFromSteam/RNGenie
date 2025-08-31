# RNGenie: A Discord Loot Distribution Bot

RNGenie is a powerful yet easy-to-use Discord bot designed to manage turn-based loot distribution for games and events. It uses a fair "snake draft" system, a modern slash command (`/loot`), and a dynamic, single-message interface to keep your chat clean and the process organized.

[![RNGenie: A Discord Loot Distribution Bot](https://img.youtube.com/vi/u4bAoasJTRQ/maxresdefault.jpg)](https://www.youtube.com/watch?v=u4bAoasJTRQ)

---

## Features

-   **Slash Command Integration**: Simply type `/loot` to start a new session.
-   **Automatic Member Detection**: Instantly finds all members (including other bots) in your current voice channel.
-   **Randomized Roll Order**: Assigns a random roll (1-100) to each member and sorts them from highest to lowest.
-   **Fair Snake Draft System**: The turn order is a "snake draft" (e.g., 1 -> 2 -> 3, then 3 -> 2 -> 1) to ensure fairness. The player at the end of a round gets a consecutive "double pick" before the order reverses.
-   **Live Updating UI**: A single, clean message is created that updates in place as loot is assigned, preventing chat spam.
-   **Loot Master Control**: Only the person who initiated the `/loot` command can assign items or skip turns, giving them full control.
-   **Automatic Timeout**: If a loot session is inactive for 30 minutes, it will automatically time out, displaying a final summary of assigned and unclaimed items.
-   **Fully Customizable Colors**: Easily change the color of headers, usernames, and status tags using ANSI color codes at the top of the script.

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
    -   Under **Privileged Gateway Intents**, enable **SERVER MEMBERS INTENT** and **MESSAGE CONTENT INTENT**. This is crucial for the bot to function correctly.
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
    Use the `RNGenie.py` file for local development.
    ```sh
    python RNGenie.py
    ```
    You will see a "Logged in as..." message in your terminal. The bot is now online and ready to use!

---

### 2. Deploying to a 24/7 Host (PaaS or VPS)

To run the bot continuously, you need to deploy it to a server. This requires a different approach than running it locally, as the script must run persistently in the background and restart automatically if it crashes or the server reboots.

#### Option A: PaaS (Platform as a Service) - Easiest Method
Platforms like **Heroku**, **Railway**, or **Fly.io** simplify deployment. They generally follow these steps:

1.  **Link Your GitHub Repository:** Connect your hosting account to the GitHub repository containing the bot's code.
2.  **Configure Build Settings:**
    -   **Build Command**: `pip install -r requirements.txt`
    -   **Start Command**: `python RNGenie_deploy.py`
3.  **Set Environment Variables:** In your host's dashboard, find the "Environment Variables" or "Secrets" section and add your bot's token.
    -   **Variable Name**: `DISCORD_TOKEN`
    -   **Value**: `YOUR_BOT_TOKEN_HERE`
4.  **Deploy:** The platform will automatically build and run your bot. Many PaaS providers (especially on free tiers) require the keep-alive server included in `RNGenie_deploy.py`. You may need to use a service like UptimeRobot to ping your service's URL to prevent it from sleeping.

#### Option B: VPS (Virtual Private Server) - More Control
A VPS from providers like **DigitalOcean**, **Linode**, or **Vultr** gives you a full Linux server. This method provides the most stability.

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
    To ensure the bot runs 24/7 and restarts on its own, we will use `systemd`, the standard process manager for modern Linux.

    -   Create a service file:
        ```sh
        sudo nano /etc/systemd/system/rngenie.service
        ```
    -   Paste the following configuration into the file. **Remember to replace `/path/to/your/RNGenie` with the actual path** where you cloned the repository.
        ```ini
        [Unit]
        Description=RNGenie Discord Bot
        After=network.target

        [Service]
        User=your_username # Replace with your linux username (e.g., root, ubuntu)
        Group=your_group   # Replace with your linux group (e.g., root, ubuntu)
        WorkingDirectory=/path/to/your/RNGenie 
        ExecStart=/path/to/your/RNGenie/venv/bin/python RNGenie_deploy.py
        Restart=always
        RestartSec=3

        [Install]
        WantedBy=multi-user.target
        ```
    -   Save the file (`Ctrl+X`, then `Y`, then `Enter`).

7.  **Enable and Start the Service:**
    -   Reload `systemd` to recognize the new file:
        ```sh
        sudo systemctl daemon-reload
        ```
    -   Enable the service to start automatically on boot:
        ```sh
        sudo systemctl enable rngenie.service
        ```
    -   Start the bot immediately:
        ```sh
        sudo systemctl start rngenie.service
        ```
    -   You can check the bot's status and logs with:
        ```sh
        sudo systemctl status rngenie.service
        journalctl -u rngenie -f
        ```
Your bot is now running persistently on the server!

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
    -   `31`: Red
    -   `32`: Green
    -   `33`: Yellow/Orange
    -   `34`: Blue
    -   `35`: Magenta/Pink
    -   `36`: Cyan
    -   `37`: White/Light Grey
