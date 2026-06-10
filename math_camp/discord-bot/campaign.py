"""
Sponsor-campaign Discord layer: queue control + reply review.

Wired into the bot with a single call from bot.py:

    import campaign
    campaign.setup(bot)

Slash commands (require Manage Server):
    /queue-start /queue-pause /queue-resume /queue-stop /queue-status

Reply review:
    A background loop reads new mailbox replies. Each likely REJECTION is posted
    to REVIEW_CHANNEL_ID with Edit / Send / Mark-handled buttons and a thread for
    attachments. Editing or sending is BLOCKED unless the queue is paused. After
    a reply is sent, the post updates to prompt resuming the queue.

Extra environment variables:
    REVIEW_CHANNEL_ID     Discord channel id where rejections are posted
    REPLY_POLL_SECONDS    how often to check the mailbox (default 120)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import tasks

import emailer
import replies

log = logging.getLogger("hg-bot.campaign")

REVIEW_CHANNEL_ID = int(os.environ.get("REVIEW_CHANNEL_ID") or 0)
REPLY_POLL_SECONDS = int(os.environ.get("REPLY_POLL_SECONDS") or 120)
# Anyone with this Discord role (or Manage Server) may control the queue.
CONTROL_ROLE_ID = int(os.environ.get("CONTROL_ROLE_ID") or 0)

# Message-ids we've already posted this session (so the poll doesn't repost).
_posted_ids: set[str] = set()


# ── helpers ───────────────────────────────────────────────────────────

def _is_admin(interaction: discord.Interaction) -> bool:
    """True if the user may control the queue: Manage Server/Administrator, or
    holds the configured CONTROL_ROLE_ID."""
    p = getattr(interaction.user, "guild_permissions", None)
    if p and (p.manage_guild or p.administrator):
        return True
    if CONTROL_ROLE_ID:
        roles = getattr(interaction.user, "roles", []) or []
        return any(getattr(r, "id", None) == CONTROL_ROLE_ID for r in roles)
    return False


def _fmt_status(s: dict) -> str:
    if not s["running"] and s["state"] in ("idle",):
        return "**Queue:** idle (not started). Use `/queue-start`."
    state = "⏸ paused" if s["paused"] else f"● {s['state']}"
    lines = [
        f"**Queue:** {state}",
        f"**Progress:** {s['sent']} sent · {s['failed']} failed · "
        f"of {s['total']}",
    ]
    if s.get("current"):
        lines.append(f"**Current:** {s['current']}")
    if s.get("state") == "resting" and s.get("resting_until"):
        mins = max(0, int((s["resting_until"] - time.time()) / 60))
        lines.append(f"**Resting:** ~{mins} min left (batch of "
                     f"{s['batch_size']})")
    if s.get("message"):
        lines.append(f"*{s['message']}*")
    return "\n".join(lines)


# ── reply-review UI ──────────────────────────────────────────────────

class RejectDraftModal(discord.ui.Modal, title="Edit the rejection reply"):
    """Text box for the human to tweak the pre-loaded rejection reply."""

    def __init__(self, view: "ReviewView"):
        super().__init__()
        self.view_ref = view
        self.body = discord.ui.TextInput(
            label="Reply text (this gets sent to them)",
            style=discord.TextStyle.paragraph,
            default=view.draft or replies.draft_for_rejection(),
            max_length=3800, required=True)
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        self.view_ref.verdict = "rejected"
        self.view_ref.draft = str(self.body.value)
        if self.view_ref.message:
            await self.view_ref.message.edit(embed=self.view_ref._embed(),
                                             view=self.view_ref)
        await interaction.response.send_message(
            "🔴 Marked **Rejected** and draft saved. Press **📨 Send** to reply "
            "(attach files in the thread first if you want).", ephemeral=True)


class ReviewView(discord.ui.View):
    """Every reply gets posted with these buttons so a human decides the verdict
    (overriding the bot's guess) and sends a — possibly edited — reply."""

    def __init__(self, reply: dict):
        super().__init__(timeout=None)
        self.reply = reply
        self.verdict: str | None = None          # None | "rejected" | "accepted"
        self.draft = replies.draft_for_rejection()  # pre-loaded, varied
        self.message: discord.Message | None = None
        self.thread: discord.Thread | None = None
        self.handled = False

    def _embed(self) -> discord.Embed:
        r = self.reply
        if self.handled:
            colour = discord.Colour.greyple()
        elif self.verdict == "rejected":
            colour = discord.Colour.red()
        elif self.verdict == "accepted":
            colour = discord.Colour.green()
        else:
            colour = discord.Colour.blurple()
        guess = "🔴 a rejection" if r["is_rejection"] else "🟢 not a rejection"
        verdict_txt = {"rejected": "🔴 Rejected", "accepted": "🟢 Accepted"} \
            .get(self.verdict, "— you decide —")
        e = discord.Embed(title="📨 New reply — your call", colour=colour)
        e.add_field(name="From",
                    value=f"{r['from_name']} <{r['from_email']}>", inline=False)
        e.add_field(name="Subject", value=r["subject"] or "—", inline=False)
        e.add_field(name="Their message",
                    value=(r["preview"] or "—")[:1024], inline=False)
        e.add_field(name="🤖 Bot's guess",
                    value=f"{guess} ({int(r['confidence']*100)}%) — fix it below "
                          f"if it's wrong", inline=False)
        e.add_field(name="Verdict", value=verdict_txt, inline=True)
        if self.verdict == "rejected":
            e.add_field(name="Draft reply (tap 🔴 Rejected to edit)",
                        value=(self.draft[:1000] + "…") if len(self.draft) > 1000
                        else self.draft, inline=False)
        elif self.verdict == "accepted":
            e.add_field(name="Reply", value="*None — accepted, no auto-reply.*",
                        inline=False)
        e.set_footer(text="Pause the queue to send · attach files in the thread")
        return e

    async def _guard_paused(self, interaction: discord.Interaction) -> bool:
        if self.handled:
            await interaction.response.send_message("Already handled.",
                                                    ephemeral=True)
            return False
        if not emailer.is_paused():
            await interaction.response.send_message(
                "⏸ **Pause the queue first** with `/queue-pause`, then edit or "
                "send. Resume with `/queue-resume` when you're done.",
                ephemeral=True)
            return False
        return True

    async def _finalize(self, interaction, title, colour):
        self.handled = True
        for child in self.children:
            child.disabled = True
        e = self._embed()
        e.title = title
        e.colour = colour
        e.description = (f"Handled by {interaction.user.mention}. "
                         f"▶️ **Done? Resume the queue with `/queue-resume`.**")
        if self.message:
            await self.message.edit(embed=e, view=self)

    @discord.ui.button(label="🔴 Rejected", style=discord.ButtonStyle.danger)
    async def rejected(self, interaction: discord.Interaction, _b: discord.ui.Button):
        # Opens the editable text box (pre-loaded with a varied draft).
        if not await self._guard_paused(interaction):
            return
        await interaction.response.send_modal(RejectDraftModal(self))

    @discord.ui.button(label="🟢 Accepted", style=discord.ButtonStyle.success)
    async def accepted(self, interaction: discord.Interaction, _b: discord.ui.Button):
        # No reply is sent for an acceptance — just record the verdict.
        if self.handled:
            await interaction.response.send_message("Already handled.",
                                                    ephemeral=True)
            return
        self.verdict = "accepted"
        self.draft = ""
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="📨 Send", style=discord.ButtonStyle.primary)
    async def send(self, interaction: discord.Interaction, _b: discord.ui.Button):
        if self.handled:
            await interaction.response.send_message("Already handled.",
                                                    ephemeral=True)
            return
        if self.verdict is None:
            await interaction.response.send_message(
                "Pick **🔴 Rejected** or **🟢 Accepted** first.", ephemeral=True)
            return
        r = self.reply
        if self.verdict == "accepted":
            # Nothing to send — close it out.
            await interaction.response.defer(ephemeral=True)
            await asyncio.to_thread(replies.mark_handled, r["message_id"])
            await self._finalize(interaction, "🟢 Accepted — no reply sent",
                                 discord.Colour.green())
            await interaction.followup.send(
                "Marked accepted (no email sent) — follow up by hand.",
                ephemeral=True)
            return
        # Rejected: real outbound email -> require the queue to be paused.
        if not emailer.is_paused():
            await interaction.response.send_message(
                "⏸ **Pause the queue first** (`/queue-pause`) before sending a "
                "reply.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        attachments = []
        if self.thread:
            try:
                async for m in self.thread.history(limit=50):
                    for a in m.attachments:
                        attachments.append((a.filename, await a.read()))
            except Exception:
                log.exception("reading thread attachments")
        try:
            await asyncio.to_thread(
                replies.send_reply, r["from_email"], r["subject"], self.draft,
                r["message_id"], r["references"], attachments)
            await asyncio.to_thread(replies.mark_handled, r["message_id"])
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(f"❌ Send failed: {exc}",
                                            ephemeral=True)
            return
        extra = f" with {len(attachments)} attachment(s)" if attachments else ""
        await self._finalize(interaction, f"✅ Rejection reply sent{extra}",
                             discord.Colour.green())
        await interaction.followup.send("Sent. ✅", ephemeral=True)


# ── background mailbox poll (module-level so it can start in setup_hook) ──
_bot: discord.Client | None = None


@tasks.loop(seconds=REPLY_POLL_SECONDS)
async def poll_replies():
    if not REVIEW_CHANNEL_ID or _bot is None:
        return
    channel = _bot.get_channel(REVIEW_CHANNEL_ID)
    if channel is None:
        return
    try:
        new = await asyncio.to_thread(replies.fetch_new_replies, _posted_ids)
    except Exception:
        log.exception("fetch_new_replies failed")
        return
    for r in new:
        _posted_ids.add(r["message_id"])
        # EVERY reply is posted for a human to judge (the bot only suggests).
        view = ReviewView(r)
        try:
            msg = await channel.send(embed=view._embed(), view=view)
            view.message = msg
            try:
                view.thread = await msg.create_thread(
                    name=f"Reply · {r['from_name'][:60]}")
                await view.thread.send(
                    "Drop any files here to attach them, then press "
                    "**📨 Send** above. (Pause the queue first.)")
            except Exception:
                log.exception("creating attachment thread")
        except Exception:
            log.exception("posting reply review")


@poll_replies.before_loop
async def _before_poll():
    if _bot is not None:
        await _bot.wait_until_ready()


def start_review_loop() -> None:
    """Start the mailbox poll. Call from the bot's setup_hook (loop running)."""
    if REVIEW_CHANNEL_ID and not poll_replies.is_running():
        poll_replies.start()
        log.info("Reply review loop started (channel=%s, every %ss)",
                 REVIEW_CHANNEL_ID, REPLY_POLL_SECONDS)
    elif not REVIEW_CHANNEL_ID:
        log.info("REVIEW_CHANNEL_ID not set — reply review disabled.")


# ── setup ────────────────────────────────────────────────────────────

def setup(bot: discord.Client) -> None:
    global _bot
    _bot = bot
    tree = bot.tree

    async def _admin_only(interaction: discord.Interaction) -> bool:
        if _is_admin(interaction):
            return True
        await interaction.response.send_message(
            "You need **Manage Server** or the outreach role to control the "
            "email queue.", ephemeral=True)
        return False

    @tree.command(name="queue-start",
                  description="Start the sponsor email queue.")
    async def queue_start(interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        ok, msg = await asyncio.to_thread(emailer.start_queue)
        await interaction.response.send_message(
            ("🚀 " if ok else "⚠️ ") + msg, ephemeral=True)

    @tree.command(name="queue-pause",
                  description="Pause sending (so you can reply to emails).")
    async def queue_pause(interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        emailer.pause()
        await interaction.response.send_message(
            "⏸ **Queue paused.** You can now edit & send replies. "
            "Resume with `/queue-resume` when you're done.", ephemeral=False)

    @tree.command(name="queue-resume", description="Resume sending.")
    async def queue_resume(interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        emailer.resume()
        await interaction.response.send_message("▶️ **Queue resumed.**",
                                                ephemeral=False)

    @tree.command(name="queue-stop",
                  description="Stop the queue for this run.")
    async def queue_stop(interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        emailer.request_stop()
        await interaction.response.send_message("⏹ Stopping the queue.",
                                                ephemeral=True)

    @tree.command(name="queue-status", description="Show the email queue status.")
    async def queue_status(interaction: discord.Interaction):
        await interaction.response.send_message(
            _fmt_status(emailer.status()), ephemeral=True)
