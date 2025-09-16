# RNGenie.py
# Rewritten RNGenie: optimized/cleaner structure, minimal logging, force-delete/recreate of 3rd message after each turn,
# and disallow invoking /loot from voice-linked text channels. Preserves original text outputs and behavior.

import os
import random
import re
import asyncio
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands

# ---------------------------
# Basic setup
# ---------------------------
intents = nextcord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(intents=intents)

# sessions keyed by control-panel message id
loot_sessions: dict[int, dict] = {}
session_locks: dict[int, asyncio.Lock] = {}

# constants
SESSION_TIMEOUT_SECONDS = 600  # 10 minutes inactivity
TURN_NOT_STARTED = -1

NUMBER_EMOJIS = {
    1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
    6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"
}
# extend placeholder for numbers beyond 10
for i in range(11, 21):
    NUMBER_EMOJIS.setdefault(i, f"#{i}")

# ---------------------------
# Small helpers
# ---------------------------
def _are_items_left(session: dict) -> bool:
    return any(it.get("assigned_to") is None for it in session["items"])

def _advance_turn_snake(session: dict) -> None:
    """Advance snake draft order. If at ends, reverse direction and increment round."""
    session["just_reversed"] = False
    if not _are_items_left(session):
        session["current_turn"] = len(session["rolls"])  # marker for done
        return

    num = len(session["rolls"])
    if num == 0:
        return
    if session["current_turn"] == TURN_NOT_STARTED:
        session["current_turn"] = 0
        return

    next_turn = session["current_turn"] + session["direction"]
    if 0 <= next_turn < num:
        session["current_turn"] = next_turn
    else:
        session["direction"] *= -1
        session["round"] += 1
        session["just_reversed"] = True
        # after reversing, advance one to the new direction
        session["current_turn"] = max(0, min(num - 1, session["current_turn"] + session["direction"]))

def _build_roll_lines(rolls: list) -> str:
    # include tiebreak numbers if present for duplicate rolls
    roll_counts = {}
    for r in rolls:
        roll_counts.setdefault(r["roll"], 0)
        roll_counts[r["roll"]] += 1
    parts = []
    for idx, r in enumerate(rolls):
        emoji = NUMBER_EMOJIS.get(idx + 1, f"#{idx+1}")
        name = r["member"].display_name
        base = f"{emoji} {name} ({r['roll']})"
        if roll_counts.get(r["roll"], 0) > 1:
            tb = r.get("tiebreak")
            base += f" /TB:{tb if tb is not None else '—'}"
        parts.append(base)
    return "\n".join(parts)

async def _get_msg(channel: nextcord.abc.GuildChannel | nextcord.TextChannel | None, msg_id: int):
    if not channel or not msg_id:
        return None
    try:
        partial = getattr(channel, "get_partial_message", None)
        if callable(partial):
            return partial(msg_id)
    except Exception:
        pass
    try:
        fetch = getattr(channel, "fetch_message", None)
        if callable(fetch):
            return await channel.fetch_message(msg_id)
    except Exception:
        pass
    return None

# ---------------------------
# Message builders
# ---------------------------
def build_loot_list_message(session: dict) -> str:
    header = "**(1/2)**\n"
    remaining = [it for it in session["items"] if it["assigned_to"] is None]
    if remaining:
        body = "```ansi\n❌ Remaining Loot Items ❌\n==================================\n"
        for it in remaining:
            body += f"{it['display_number']}. {it['name']}\n"
        body += "```"
        return f"{header}{body}"
    return f"{header}```ansi\n✅ All Items Assigned ✅\n==================================\nAll items have been distributed.\n```"

def build_control_panel_message(session: dict) -> str:
    header = f"**(2/2)**\n\n✍️ **Loot Manager:** {session['invoker'].mention}\n\n"
    roll_block = "```ansi\n🎲 Roll Order 🎲\n==================================\n" + _build_roll_lines(session["rolls"]) + "\n```"

    # assigned items per roller
    assigned_map = {r["member"].id: [] for r in session["rolls"]}
    for it in session["items"]:
        if it["assigned_to"]:
            assigned_map.setdefault(it["assigned_to"], []).append(it["name"])
    assigned_block = "```ansi\n✅ Assigned Items ✅\n==================================\n"
    for i, r in enumerate(session["rolls"]):
        emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        assigned_block += f"{emoji} {r['member'].display_name}\n"
        items = assigned_map.get(r["member"].id, [])
        if items:
            for nm in items:
                assigned_block += f"- {nm}\n"
        else:
            assigned_block += "- N/A\n"
    assigned_block += "```"

    indicator = ""
    if 0 <= session["current_turn"] < len(session["rolls"]):
        direction = "Normal" if session["direction"] == 1 else "Reverse"
        indicator = f"\n🔔 **Round {session['round'] + 1}** ({direction})\n\n"
    else:
        indicator = f"\n🎁 **Loot distribution is ready!**\n\n✍️ **Loot Manager {session['invoker'].mention} can remove participants or click below to begin.**"
    return f"{header}{roll_block}\n{assigned_block}{indicator}"

def build_final_summary_message(session: dict, timed_out: bool=False) -> str:
    header = "⌛ **The loot session has timed out:**\n\n" if timed_out else "✅ **All Items Have Been Assigned:**\n\n"
    roll_block = "```ansi\n🎲 Roll Order 🎲\n==================================\n" + _build_roll_lines(session["rolls"]) + "\n```"

    assigned_map = {r["member"].id: [] for r in session["rolls"]}
    for it in session["items"]:
        if it["assigned_to"]:
            assigned_map.setdefault(it["assigned_to"], []).append(it["name"])
    assigned_block = "```ansi\n✅ Assigned Items ✅\n==================================\n"
    for i, r in enumerate(session["rolls"]):
        emoji = NUMBER_EMOJIS.get(i + 1, f"#{i+1}")
        assigned_block += f"{emoji} {r['member'].display_name}\n"
        items = assigned_map.get(r["member"].id, [])
        if items:
            for nm in items:
                assigned_block += f"- {nm}\n"
        else:
            assigned_block += "- N/A\n"
    assigned_block += "```"

    unclaimed = [it for it in session["items"] if it["assigned_to"] is None]
    unclaimed_block = ""
    if unclaimed:
        unclaimed_block = "```ansi\n❌ Unclaimed Items ❌\n==================================\n"
        for it in unclaimed:
            unclaimed_block += f"{it['display_number']}. {it['name']}\n"
        unclaimed_block += "```"
    return f"{header}{roll_block}\n{assigned_block}\n{unclaimed_block}"

def _item_message_text_and_active(session: dict) -> tuple[str, bool]:
    if not _are_items_left(session) or session["current_turn"] == TURN_NOT_STARTED:
        return ("No active picks right now.", False)
    if not (0 <= session["current_turn"] < len(session["rolls"])):
        return ("No active picks right now.", False)
    picker = session["rolls"][session["current_turn"]]["member"]
    emoji = NUMBER_EMOJIS.get(session["current_turn"] + 1, "👉")
    turn_text = "turn!" if not session.get("just_reversed", False) else "turn (direction reversed)!"
    return (f"**{emoji} {picker.mention}'s {turn_text}**\n\nChoose items below...", True)

# ---------------------------
# Views
# ---------------------------
class ItemDropdownView(nextcord.ui.View):
    def __init__(self, session_id: int):
        super().__init__(timeout=None)
        self.session_id = session_id
        self._populate()

    def _populate(self):
        self.clear_items()
        session = loot_sessions.get(self.session_id)
        if not session:
            return
        if not _are_items_left(session):
            return
        if not (0 <= session["current_turn"] < len(session["rolls"])):
            return

        available = [(i, it) for i, it in enumerate(session["items"]) if it["assigned_to"] is None]
        if not available:
            return

        # chunk into up to 25 options per select
        chunks = [available[i:i+25] for i in range(0, len(available), 25)]
        selected = set(session.get("selected_items") or [])
        for ci, chunk in enumerate(chunks):
            opts = []
            for idx, item in chunk:
                label = f"{item['display_number']}. {item['name']}"
                truncated = (label[:97] + "...") if len(label) > 100 else label
                opts.append(nextcord.SelectOption(label=truncated, value=str(idx), default=str(idx) in selected))
            placeholder = "Choose items..." if len(chunks) == 1 else f"Choose items ({chunk[0][1]['display_number']}-{chunk[-1][1]['display_number']})..."
            self.add_item(nextcord.ui.Select(placeholder=placeholder, options=opts, custom_id=f"item_select_{ci}", min_values=0, max_values=len(opts)))

        assign_disabled = not session.get("selected_items")
        self.add_item(nextcord.ui.Button(label="Assign Selected", style=nextcord.ButtonStyle.success, emoji="✅", custom_id="assign_button", disabled=assign_disabled))
        self.add_item(nextcord.ui.Button(label="Skip Turn", style=nextcord.ButtonStyle.danger, custom_id="skip_button"))
        undo_disabled = not session.get("last_action")
        self.add_item(nextcord.ui.Button(label="Undo", style=nextcord.ButtonStyle.secondary, emoji="↩️", custom_id="undo_button", disabled=undo_disabled))

        # assign callbacks
        for child in self.children:
            if getattr(child, "custom_id", None) == "assign_button":
                child.callback = self.on_assign
            if getattr(child, "custom_id", None) == "skip_button":
                child.callback = self.on_skip
            if getattr(child, "custom_id", None) == "undo_button":
                child.callback = self.on_undo
            if getattr(child, "custom_id", "").startswith("item_select_"):
                child.callback = self.on_item_select

    async def _fast_edit(self, interaction: nextcord.Interaction, content: str, view: nextcord.ui.View | None) -> bool:
        # try editing as response for snappy UX; fall back to edit via stored id, or send new message.
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return False

        try:
            await interaction.response.edit_message(content=content, view=view)
            return True
        except Exception:
            try:
                await interaction.response.defer_update()
            except Exception:
                try:
                    await interaction.response.send_message("Processing...", ephemeral=True)
                except Exception:
                    pass

            ch = bot.get_channel(session["channel_id"])
            if not ch:
                return False
            existing_id = session.get("item_dropdown_message_id")
            if existing_id:
                try:
                    msg = await _get_msg(ch, existing_id)
                    if msg:
                        await msg.edit(content=content, view=view)
                        return True
                except Exception:
                    pass
            try:
                msg = await ch.send(content, view=view)
                session["item_dropdown_message_id"] = msg.id
                return True
            except Exception:
                return False

    async def _ack(self, interaction: nextcord.Interaction):
        try:
            await interaction.response.defer_update()
        except Exception:
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message("Processing...", ephemeral=True)
                except Exception:
                    pass

    async def on_item_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            await self._ack(interaction)
            try:
                await interaction.followup.send("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        cid = interaction.data.get("custom_id")
        if not cid:
            await self._ack(interaction)
            try:
                await interaction.followup.send("Invalid selection.", ephemeral=True)
            except Exception:
                pass
            return
        try:
            idx = int(cid.split("_")[-1])
        except Exception:
            await self._ack(interaction)
            try:
                await interaction.followup.send("Malformed dropdown id.", ephemeral=True)
            except Exception:
                pass
            return

        available = [(i, it) for i, it in enumerate(session["items"]) if it["assigned_to"] is None]
        chunks = [available[i:i+25] for i in range(0, len(available), 25)]
        if idx >= len(chunks):
            await self._ack(interaction)
            try:
                await interaction.followup.send("Stale dropdown.", ephemeral=True)
            except Exception:
                pass
            return

        possible = {str(i) for i, _ in chunks[idx]}
        newly = set(interaction.data.get("values", []))
        lock = session_locks.setdefault(self.session_id, asyncio.Lock())
        async with lock:
            current = set(session.get("selected_items") or [])
            current -= possible
            current |= newly
            session["selected_items"] = list(current)

        await self._ack(interaction)
        await _reset_session_timeout(self.session_id)
        # background refresh (non-blocking)
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=False))

    async def on_assign(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        if session["current_turn"] < 0 or session["current_turn"] >= len(session["rolls"]):
            try:
                await interaction.response.send_message("It's not an active picking turn.", ephemeral=True)
            except Exception:
                pass
            return

        picker = session["rolls"][session["current_turn"]]["member"]
        if interaction.user.id not in (picker.id, session["invoker_id"]):
            try:
                await interaction.response.send_message("🛡️ Only the current picker or the Loot Manager can assign items.", ephemeral=True)
            except Exception:
                pass
            return

        selected = session.get("selected_items") or []
        session["last_action"] = {
            "turn": session["current_turn"],
            "round": session["round"],
            "direction": session["direction"],
            "just_reversed": session.get("just_reversed", False),
            "assigned_indices": [int(i) for i in selected] if selected else []
        }

        for s in selected:
            try:
                idx = int(s)
            except Exception:
                continue
            if 0 <= idx < len(session["items"]):
                session["items"][idx]["assigned_to"] = picker.id

        session["selected_items"] = None
        _advance_turn_snake(session)
        await _reset_session_timeout(self.session_id)

        new_text, active = _item_message_text_and_active(session)
        new_view = ItemDropdownView(self.session_id) if active else None

        # Force delete+recreate after each turn for the third message as requested:
        edited = await self._fast_edit(interaction, new_text, new_view)
        # Always refresh with delete_item=True to ensure recreate
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=True))

    async def on_skip(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        if 0 <= session["current_turn"] < len(session["rolls"]):
            picker = session["rolls"][session["current_turn"]]["member"]
            if interaction.user.id not in (picker.id, session["invoker_id"]):
                try:
                    await interaction.response.send_message("🛡️ Only the current picker or the Loot Manager can skip the turn.", ephemeral=True)
                except Exception:
                    pass
                return

        # record undo state
        if session["current_turn"] != TURN_NOT_STARTED:
            session["last_action"] = {
                "turn": session["current_turn"],
                "round": session["round"],
                "direction": session["direction"],
                "just_reversed": session.get("just_reversed", False),
                "assigned_indices": []
            }

        session["selected_items"] = None
        if session["current_turn"] == TURN_NOT_STARTED:
            session["members_to_remove"] = None
            session["last_action"] = None

        _advance_turn_snake(session)
        await _reset_session_timeout(self.session_id)

        new_text, active = _item_message_text_and_active(session)
        new_view = ItemDropdownView(self.session_id) if active else None

        await self._fast_edit(interaction, new_text, new_view)
        # ensure delete+recreate after each turn
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=True))

    async def on_undo(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return

        if interaction.user.id != session["invoker_id"]:
            try:
                await interaction.response.send_message("🛡️ Only the Loot Manager can use Undo.", ephemeral=True)
            except Exception:
                pass
            return

        last = session.get("last_action")
        if not last:
            try:
                await interaction.response.send_message("❌ There is nothing to undo.", ephemeral=True)
            except Exception:
                pass
            return

        for idx in last.get("assigned_indices", []):
            if 0 <= idx < len(session["items"]):
                session["items"][idx]["assigned_to"] = None

        session["current_turn"] = last["turn"]
        session["round"] = last["round"]
        session["direction"] = last["direction"]
        session["just_reversed"] = last.get("just_reversed", False)
        session["last_action"] = None
        session["selected_items"] = None

        await _reset_session_timeout(self.session_id)
        new_text, active = _item_message_text_and_active(session)
        new_view = ItemDropdownView(self.session_id) if active else None
        await self._fast_edit(interaction, new_text, new_view)
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=True))

class ControlPanelView(nextcord.ui.View):
    def __init__(self, session_id: int):
        super().__init__(timeout=None)
        self.session_id = session_id
        self._populate()

    def _populate(self):
        self.clear_items()
        session = loot_sessions.get(self.session_id)
        if not session:
            return

        if session["current_turn"] == TURN_NOT_STARTED:
            # removal select
            options = []
            inv = session["invoker_id"]
            for r in session["rolls"]:
                if r["member"].id != inv:
                    options.append(nextcord.SelectOption(label=r["member"].display_name, value=str(r["member"].id)))
            if options:
                self.add_item(nextcord.ui.Select(placeholder="Select participants to remove...", options=options, custom_id="remove_select", min_values=0, max_values=len(options)))
            self.add_item(nextcord.ui.Button(label="Remove Selected", style=nextcord.ButtonStyle.danger, emoji="✖️", custom_id="remove_confirm_button"))
            self.add_item(nextcord.ui.Button(label="📜 Start Loot Assignment!", style=nextcord.ButtonStyle.success, custom_id="start_button"))
        # assign callbacks
        for child in self.children:
            if getattr(child, "custom_id", "") == "remove_select":
                child.callback = self.on_remove_select
            if getattr(child, "custom_id", "") == "remove_confirm_button":
                child.callback = self.on_remove_confirm
            if getattr(child, "custom_id", "") == "start_button":
                child.callback = self.on_start

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("❌ Session expired or not found.", ephemeral=True)
            except Exception:
                pass
            return False
        if interaction.user.id == session["invoker_id"]:
            return True
        try:
            await interaction.response.send_message(f"🛡️ Only {session['invoker'].mention} can use control-panel buttons.", ephemeral=True)
        except Exception:
            pass
        return False

    async def on_remove_select(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return
        session["members_to_remove"] = interaction.data.get("values")
        self._populate()
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

    async def on_remove_confirm(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return
        vals = session.get("members_to_remove") or []
        to_remove = set()
        for v in vals:
            try:
                to_remove.add(int(v))
            except Exception:
                continue
        if to_remove:
            session["rolls"] = [r for r in session["rolls"] if r["member"].id not in to_remove]
            session["members_to_remove"] = None
            if not session["rolls"]:
                ch = bot.get_channel(session["channel_id"])
                # tidy messages if possible
                try:
                    lm = await _get_msg(ch, session.get("loot_list_message_id"))
                    if lm:
                        await lm.delete()
                except Exception:
                    pass
                try:
                    it = await _get_msg(ch, session.get("item_dropdown_message_id"))
                    if it:
                        await it.delete()
                except Exception:
                    pass
                try:
                    ctrl = await _get_msg(ch, self.session_id)
                    if ctrl:
                        await ctrl.edit(content="⚠️ The loot session was cancelled — no participants remain.", view=None)
                except Exception:
                    pass
                t = session.get("timeout_task")
                if t:
                    try:
                        t.cancel()
                    except Exception:
                        pass
                loot_sessions.pop(self.session_id, None)
                session_locks.pop(self.session_id, None)
                try:
                    await interaction.response.send_message("Session cancelled — no participants remain.", ephemeral=True)
                except Exception:
                    pass
                return
            # keep current_turn in bounds
            if session["current_turn"] != TURN_NOT_STARTED and session["current_turn"] >= len(session["rolls"]):
                session["current_turn"] = max(0, len(session["rolls"]) - 1)

        await _reset_session_timeout(self.session_id)
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        # force delete+recreate of item message when participants change
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=True))

    async def on_start(self, interaction: nextcord.Interaction):
        session = loot_sessions.get(self.session_id)
        if not session:
            try:
                await interaction.response.send_message("Session expired.", ephemeral=True)
            except Exception:
                pass
            return
        session["members_to_remove"] = None
        session["selected_items"] = None
        session["last_action"] = None
        _advance_turn_snake(session)
        await _reset_session_timeout(self.session_id)
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        # force delete+recreate of the item message when starting
        asyncio.create_task(_refresh_all_messages(self.session_id, delete_item=True))

# ---------------------------
# Message lifecycle / refresh / timeout
# ---------------------------
async def _reset_session_timeout(session_id: int):
    session = loot_sessions.get(session_id)
    if not session:
        return
    task = session.get("timeout_task")
    if task:
        try:
            task.cancel()
        except Exception:
            pass
    session["timeout_task"] = asyncio.create_task(_schedule_session_timeout(session_id))

async def _refresh_all_messages(session_id: int, delete_item: bool = True):
    session = loot_sessions.get(session_id)
    if not session:
        return
    lock = session_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        ch = bot.get_channel(session["channel_id"])
        if not ch:
            # cleanup
            t = session.get("timeout_task")
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
            loot_sessions.pop(session_id, None)
            session_locks.pop(session_id, None)
            return

        # try to load existing msgs
        control_msg = await _get_msg(ch, session_id)
        loot_msg = await _get_msg(ch, session.get("loot_list_message_id"))
        existing_item_msg = None
        existing_item_id = session.get("item_dropdown_message_id")
        if existing_item_id:
            existing_item_msg = await _get_msg(ch, existing_item_id)

        # If delete_item requested, attempt to delete current item dropdown before recreating
        if delete_item and existing_item_msg:
            try:
                await existing_item_msg.delete()
            except Exception:
                pass
            session["item_dropdown_message_id"] = None
            existing_item_msg = None
            existing_item_id = None

        # If session completed, post final summary and cleanup
        if not _are_items_left(session) and session["current_turn"] != TURN_NOT_STARTED:
            final = build_final_summary_message(session, timed_out=False)
            try:
                if control_msg:
                    await control_msg.edit(content=final, view=None)
                else:
                    # fallback if control msg not available
                    fallback = await _get_msg(ch, session_id)
                    if fallback:
                        await fallback.edit(content=final, view=None)
            except Exception:
                pass
            if loot_msg:
                try:
                    await loot_msg.delete()
                except Exception:
                    pass
            # remove any remaining item msg
            try:
                existing = session.get("item_dropdown_message_id")
                if existing:
                    maybe = await _get_msg(ch, existing)
                    if maybe:
                        await maybe.delete()
            except Exception:
                pass
            t = session.get("timeout_task")
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
            loot_sessions.pop(session_id, None)
            session_locks.pop(session_id, None)
            return

        # build latest content
        loot_content = build_loot_list_message(session)
        control_content = build_control_panel_message(session)

        # reduce edits by comparing last stored
        if loot_content != session.get("last_loot_content") and loot_msg:
            try:
                await loot_msg.edit(content=loot_content)
                session["last_loot_content"] = loot_content
            except Exception:
                pass

        if control_content != session.get("last_control_content") and control_msg:
            try:
                await control_msg.edit(content=control_content, view=ControlPanelView(session_id))
                session["last_control_content"] = control_content
            except Exception:
                pass

        await _reset_session_timeout(session_id)

        # manage item dropdown message (only when there's an active pick)
        is_active = (0 <= session["current_turn"] < len(session["rolls"])) and _are_items_left(session)
        if not is_active:
            # ensure deleted if present
            if not delete_item and existing_item_msg:
                try:
                    await existing_item_msg.delete()
                except Exception:
                    pass
                session["item_dropdown_message_id"] = None
            return

        picker = session["rolls"][session["current_turn"]]["member"]
        emoji = NUMBER_EMOJIS.get(session["current_turn"] + 1, "👉")
        turn_text = "turn!" if not session.get("just_reversed", False) else "turn (direction reversed)!"
        item_text = f"**{emoji} {picker.mention}'s {turn_text}**\n\nChoose items below..."

        view = ItemDropdownView(session_id)

        # prefer editing existing item message only if delete_item is False
        if existing_item_msg and not delete_item:
            try:
                await existing_item_msg.edit(content=item_text, view=view)
                session["item_dropdown_message_id"] = existing_item_id
                return
            except Exception:
                session["item_dropdown_message_id"] = None
                existing_item_msg = None

        # create new item message
        try:
            new_msg = await ch.send(item_text, view=view)
            session["item_dropdown_message_id"] = new_msg.id
        except Exception:
            session["item_dropdown_message_id"] = None

async def _schedule_session_timeout(session_id: int):
    try:
        await asyncio.sleep(SESSION_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return

    session = loot_sessions.pop(session_id, None)
    session_locks.pop(session_id, None)
    if not session:
        return
    ch = bot.get_channel(session["channel_id"])
    if not ch:
        return
    # delete loot list and item messages if possible
    try:
        lm = await _get_msg(ch, session.get("loot_list_message_id"))
        if lm:
            await lm.delete()
    except Exception:
        pass
    try:
        im = await _get_msg(ch, session.get("item_dropdown_message_id"))
        if im:
            await im.delete()
    except Exception:
        pass
    final = build_final_summary_message(session, timed_out=True)
    try:
        ctrl = await _get_msg(ch, session_id)
        if ctrl:
            await ctrl.edit(content=final, view=None)
    except Exception:
        pass

# ---------------------------
# Modal & slash command
# ---------------------------
class LootModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__("RNGenie Loot Manager")
        self.loot_items = nextcord.ui.TextInput(
            label="List Items Below (One Per Line) Then Submit",
            placeholder="Type your items here\nExample: 2x Health Potion",
            required=True,
            style=nextcord.TextInputStyle.paragraph,
            max_length=2000
        )
        self.add_item(self.loot_items)

    async def callback(self, interaction: nextcord.Interaction):
        # require that the command be invoked in a regular text channel (not voice-linked text)
        channel_type = getattr(interaction.channel, "type", None)
        if channel_type in (nextcord.ChannelType.voice, nextcord.ChannelType.stage_voice):
            await interaction.response.send_message("❌ Please run `/loot` in a regular text channel (not a voice-linked text chat).", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # user must be in a voice channel to reference its members
        if not (interaction.user and interaction.user.voice and interaction.user.voice.channel):
            await interaction.followup.send("❌ You must be in a voice channel to set up a loot roll.", ephemeral=True)
            return

        members = interaction.user.voice.channel.members
        if not members:
            await interaction.followup.send("❌ I could not find anyone in your voice channel.", ephemeral=True)
            return
        if len(members) > 20:
            await interaction.followup.send(f"❌ Too many users in the voice channel ({len(members)})! The maximum is 20.", ephemeral=True)
            return

        # generate primary rolls and tiebreakers
        rolls = [{"member": m, "roll": random.randint(1, 100)} for m in members]
        by_roll = {}
        for r in rolls:
            by_roll.setdefault(r["roll"], []).append(r)
        for val, group in by_roll.items():
            if len(group) > 1:
                for r in group:
                    r["tiebreak"] = random.randint(1, 100)

        def _sort_key(r):
            return (r["roll"], r.get("tiebreak", -1))
        rolls.sort(key=_sort_key, reverse=True)

        # parse item lines (support Nx syntax)
        lines = self.loot_items.value.splitlines()
        names = []
        for l in lines:
            s = l.strip()
            if not s:
                continue
            m = re.match(r"(\d+)[xX]\s*(.*)", s)
            if m:
                try:
                    c = int(m.group(1))
                    nm = m.group(2).strip()
                    if nm:
                        names.extend([nm] * c)
                    else:
                        names.append(s)
                except Exception:
                    names.append(s)
            else:
                names.append(s)

        items = [{"name": n, "assigned_to": None, "display_number": i} for i, n in enumerate(names, 1)]
        if not items:
            await interaction.followup.send("⚠️ You must enter at least one item.", ephemeral=True)
            return

        # send placeholder messages to obtain ids
        loot_msg = await interaction.followup.send("`Initializing Loot List (1/2)...`", wait=True)
        control_msg = await interaction.channel.send("`Initializing Control Panel (2/2)...`")

        session_id = control_msg.id
        session = {
            "rolls": rolls,
            "items": items,
            "current_turn": TURN_NOT_STARTED,
            "invoker_id": interaction.user.id,
            "invoker": interaction.user,
            "selected_items": None,
            "round": 0,
            "direction": 1,
            "just_reversed": False,
            "members_to_remove": None,
            "channel_id": control_msg.channel.id,
            "loot_list_message_id": loot_msg.id,
            "item_dropdown_message_id": None,
            "last_action": None,
            "last_control_content": None,
            "last_loot_content": None,
            "timeout_task": None
        }
        loot_sessions[session_id] = session
        await _reset_session_timeout(session_id)

        # build and set initial message content
        await loot_msg.edit(content=build_loot_list_message(session))
        await control_msg.edit(content=build_control_panel_message(session), view=ControlPanelView(session_id))
        session["last_control_content"] = build_control_panel_message(session)
        session["last_loot_content"] = build_loot_list_message(session)

        # create initial item dropdown (force delete/recreate behavior on each change handled elsewhere)
        asyncio.create_task(_refresh_all_messages(session_id, delete_item=True))

@bot.slash_command(name="loot", description="Starts a turn-based loot roll for your voice channel.")
async def loot(interaction: nextcord.Interaction):
    # disallow invoking the command in voice-linked text chats
    ch_type = getattr(interaction.channel, "type", None)
    if ch_type in (nextcord.ChannelType.voice, nextcord.ChannelType.stage_voice):
        await interaction.response.send_message("❌ Please run `/loot` in a regular text channel (not a voice-linked text chat).", ephemeral=True)
        return
    await interaction.response.send_modal(LootModal())

# ---------------------------
# Events
# ---------------------------
@bot.event
async def on_ready():
    # minimal output
    print(f"RNGenie ready as {bot.user}")

@bot.event
async def on_application_command_error(interaction: nextcord.Interaction, error: Exception):
    # minimal error response to user
    try:
        if not interaction.is_expired():
            if interaction.response.is_done():
                await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)
    except Exception:
        pass

# ---------------------------
# Run
# ---------------------------
if __name__ == "__main__":
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN environment variable required.")
    bot.run(token)
