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

def _role_ids(member) -> set:
    """All of the member's role IDs, robust to guild-cache misses. Combines the
    resolved Role objects with the RAW role-id list from the interaction payload
    (the latter never depends on the guild cache being populated)."""
    ids = set()
    for r in getattr(member, "roles", []) or []:
        rid = getattr(r, "id", None)
        if rid is not None:
            ids.add(int(rid))
    raw = getattr(member, "_roles", None)   # discord.py SnowflakeList of ids
    if raw is not None:
        try:
            ids.update(int(x) for x in raw)
        except Exception:
            pass
    return ids


def _is_admin(interaction: discord.Interaction) -> bool:
    """True if the user may control the queue: Manage Server/Administrator, or
    holds the configured CONTROL_ROLE_ID."""
    p = getattr(interaction.user, "guild_permissions", None)
    if p and (p.manage_guild or p.administrator):
        return True
    if CONTROL_ROLE_ID and CONTROL_ROLE_ID in _role_ids(interaction.user):
        return True
    return False


AUDIT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "interactions.log")


def track(interaction, action, detail=""):
    """Audit who did what: append the Discord id + name + action to a log file
    (and the journal). One tab-separated line per interaction."""
    user = getattr(interaction, "user", None)
    uid = getattr(user, "id", "?")
    line = f"{int(time.time())}\t{uid}\t{user}\t{action}\t{detail}"
    log.info("AUDIT %s", line)
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        log.exception("audit log write failed")


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
    if s.get("replies_pending"):
        lines.append(f"**Replies queued:** {s['replies_pending']} (sent first)")
    if s.get("state") == "resting" and s.get("resting_until"):
        secs = max(0, int(s["resting_until"] - time.time()))
        h, m = secs // 3600, (secs % 3600) // 60
        left = f"{h}h {m}m" if h else f"{m} min"
        reason = s.get("rest_reason") or f"between emails (batch of {s['batch_size']})"
        lines.append(f"**Resting:** ~{left} left — {reason}")
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
        e.set_footer(text="🔴 decline+reply · 🟢 accept (flags important) · "
                          "🚫 no reply · 🗑️ irrelevant (archive) · files → thread")
        return e

    async def _finalize(self, interaction, title, colour, note=""):
        self.handled = True
        for child in self.children:
            child.disabled = True
        e = self._embed()
        e.title = title
        e.colour = colour
        e.description = f"Handled by {interaction.user.mention}." + \
            (f"\n{note}" if note else "")
        if self.message:
            await self.message.edit(embed=e, view=self)

    @discord.ui.button(label="🔴 Rejected", style=discord.ButtonStyle.danger)
    async def rejected(self, interaction: discord.Interaction, _b: discord.ui.Button):
        # Opens the editable text box (pre-loaded with a varied draft). No pause
        # needed — sending just queues it.
        if self.handled:
            await interaction.response.send_message("Already handled.",
                                                    ephemeral=True)
            return
        track(interaction, "reply.rejected", self.reply.get("from_email", ""))
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
        track(interaction, "reply.accepted", self.reply.get("from_email", ""))
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
        await interaction.response.defer(ephemeral=True)
        if self.verdict == "accepted":
            await asyncio.to_thread(replies.mark_handled, r["message_id"])
            await asyncio.to_thread(replies.flag_important, r["message_id"])
            track(interaction, "reply.send.accepted", r.get("from_email", ""))
            await self._finalize(
                interaction, "🟢 Accepted — flagged important",
                discord.Colour.green(),
                note="No reply sent; the original email is flagged ⭐ **important** "
                     "in the inbox for manual follow-up.")
            await interaction.followup.send(
                "Marked accepted and flagged the email as important — "
                "follow up by hand.", ephemeral=True)
            return
        # Rejected: QUEUE the reply (sent first at the next slot — no pause).
        attachments = []
        if self.thread:
            try:
                async for m in self.thread.history(limit=50):
                    for a in m.attachments:
                        attachments.append((a.filename, await a.read()))
            except Exception:
                log.exception("reading thread attachments")
        emailer.enqueue_reply({
            "to_email": r["from_email"], "subject": r["subject"],
            "body": self.draft, "in_reply_to": r["message_id"],
            "references": r["references"], "attachments": attachments,
            "by": str(interaction.user),
            # Archive the original automatically once this reply actually sends.
            "archive_message_id": r["message_id"],
        })
        await asyncio.to_thread(replies.mark_handled, r["message_id"])
        track(interaction, "reply.send.queued", r.get("from_email", ""))
        extra = f" with {len(attachments)} attachment(s)" if attachments else ""
        await self._finalize(
            interaction, f"📨 Reply queued{extra}", discord.Colour.blurple(),
            note="Goes out at the **next send slot** (replies are prioritized) — "
                 "no need to pause. The original email is **archived** once it sends.")
        await interaction.followup.send(
            "Queued — it'll send at the next slot. ✅", ephemeral=True)

    @discord.ui.button(label="🚫 No reply", style=discord.ButtonStyle.secondary)
    async def no_reply(self, interaction: discord.Interaction, _b: discord.ui.Button):
        # Dismiss the message without sending anything — just mark it handled.
        if self.handled:
            await interaction.response.send_message("Already handled.",
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await asyncio.to_thread(replies.mark_handled, self.reply["message_id"])
        track(interaction, "reply.no_reply", self.reply.get("from_email", ""))
        await self._finalize(interaction, "🚫 No reply", discord.Colour.greyple(),
                             note="Marked handled — nothing was sent.")
        await interaction.followup.send("Done — no reply sent.", ephemeral=True)

    @discord.ui.button(label="🗑️ Irrelevant", style=discord.ButtonStyle.secondary)
    async def irrelevant(self, interaction: discord.Interaction, _b: discord.ui.Button):
        # Irrelevant info (auto-reply, spam, out-of-office) — archive it, no reply.
        if self.handled:
            await interaction.response.send_message("Already handled.",
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        moved = await asyncio.to_thread(replies.archive_message,
                                        self.reply["message_id"])
        track(interaction, "reply.irrelevant.archived",
              self.reply.get("from_email", ""))
        note = ("Archived to the **Archive** folder — no reply sent." if moved
                else "Marked handled (couldn't move it) — no reply sent.")
        await self._finalize(interaction, "🗑️ Irrelevant — archived",
                             discord.Colour.greyple(), note=note)
        await interaction.followup.send("Archived. 🗑️", ephemeral=True)


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


# ── Long-break announcer ─────────────────────────────────────────────
# Watches the queue and posts to the channel whenever it enters (or leaves) a
# LONG break — the shrinking batch cooldowns and the mandatory 12h break, which
# the emailer tags with a `rest_reason`. The ordinary 3-6 min gaps between
# emails carry no reason, so they're ignored and don't spam the channel.
_break_until = 0.0      # resting_until of the break we've already announced
_break_active = False   # are we currently in an announced long break?


@tasks.loop(seconds=30)
async def queue_break_announcer():
    global _break_until, _break_active
    if _bot is None or not REVIEW_CHANNEL_ID:
        return
    s = emailer.status()
    is_long = (s.get("state") == "resting" and bool(s.get("rest_reason"))
               and s.get("resting_until", 0) > time.time())
    channel = _bot.get_channel(REVIEW_CHANNEL_ID)
    if channel is None:
        return
    if is_long:
        if s["resting_until"] != _break_until:      # a new break we haven't posted
            _break_until, _break_active = s["resting_until"], True
            secs = max(0, int(s["resting_until"] - time.time()))
            h, m = secs // 3600, (secs % 3600) // 60
            left = f"{h}h {m}m" if h else f"{m} min"
            try:
                await channel.send(
                    f"⏸️ **Outreach queue is taking a break** — {s['rest_reason']}.\n"
                    f"Resuming in ~**{left}** · {s['sent']} sent so far "
                    f"({s['sent']}/{s['total']}).")
            except Exception:  # noqa: BLE001
                log.exception("break announce failed")
    elif _break_active:
        _break_active = False                       # the long break just ended
        try:
            await channel.send(
                f"▶️ **Outreach queue is back to sending.** "
                f"({s.get('sent', 0)}/{s.get('total', 0)} done.)")
        except Exception:  # noqa: BLE001
            log.exception("resume announce failed")


@queue_break_announcer.before_loop
async def _before_break_announcer():
    if _bot is not None:
        await _bot.wait_until_ready()


def start_review_loop() -> None:
    """Start the mailbox poll + break announcer. Call from setup_hook."""
    if REVIEW_CHANNEL_ID and not poll_replies.is_running():
        poll_replies.start()
        log.info("Reply review loop started (channel=%s, every %ss)",
                 REVIEW_CHANNEL_ID, REPLY_POLL_SECONDS)
    if REVIEW_CHANNEL_ID and not queue_break_announcer.is_running():
        queue_break_announcer.start()
        log.info("Queue break announcer started (channel=%s)", REVIEW_CHANNEL_ID)
    if not REVIEW_CHANNEL_ID:
        log.info("REVIEW_CHANNEL_ID not set — reply review + break alerts disabled.")


# ── setup ────────────────────────────────────────────────────────────

def setup(bot: discord.Client) -> None:
    global _bot
    _bot = bot
    tree = bot.tree

    async def _admin_only(interaction: discord.Interaction) -> bool:
        if _is_admin(interaction):
            return True
        log.warning("queue control DENIED: user=%s role_ids=%s control_role=%s",
                    getattr(interaction.user, "id", "?"),
                    sorted(_role_ids(interaction.user)), CONTROL_ROLE_ID)
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
        track(interaction, "queue.start", "ok" if ok else msg)
        await interaction.response.send_message(
            ("🚀 " if ok else "⚠️ ") + msg
            + (f"\n— started by {interaction.user.mention}" if ok else ""),
            ephemeral=not ok)   # public when it actually starts; private on error

    @tree.command(name="queue-pause", description="Pause all sending.")
    async def queue_pause(interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        emailer.pause()
        track(interaction, "queue.pause")
        await interaction.response.send_message(
            f"⏸ **Queue paused** by {interaction.user.mention} — sending halted. "
            f"Resume with `/queue-resume`.", ephemeral=False)

    @tree.command(name="queue-resume", description="Resume sending.")
    async def queue_resume(interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        emailer.resume()
        track(interaction, "queue.resume")
        await interaction.response.send_message(
            f"▶️ **Queue resumed** by {interaction.user.mention}.",
            ephemeral=False)

    @tree.command(name="queue-stop",
                  description="Stop the queue for this run.")
    async def queue_stop(interaction: discord.Interaction):
        if not await _admin_only(interaction):
            return
        emailer.request_stop()
        track(interaction, "queue.stop")
        await interaction.response.send_message(
            f"⏹ Queue stop requested by {interaction.user.mention}.",
            ephemeral=False)

    @tree.command(name="queue-status", description="Show the email queue status.")
    async def queue_status(interaction: discord.Interaction):
        track(interaction, "queue.status")
        await interaction.response.send_message(
            _fmt_status(emailer.status()), ephemeral=True)
