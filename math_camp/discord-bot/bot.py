"""HigherGrade Tutoring — Discord bot.

Polls the camp API every 2 minutes and mirrors each linked student's
camp roles + display name into Discord. Also implements:

  /verify email password   → links a Discord user to their camp account
  /whoami                  → shows the linked student's stats
  /unlink                  → removes the link
  /unlock code             → grants the role hidden behind a chest

Admin-only (Manage Roles permission):

  /chest-create code role description
  /chest-list
  /chest-delete chest_id

Required environment variables:
  DISCORD_TOKEN     bot token from discord.com/developers
  CAMP_API_BASE     e.g. https://highergradetutoring.ca
  BOT_API_TOKEN     same secret you set in /etc/highergrade.env
  GUILD_ID          (optional) restrict slash commands to one server
                    for instant updates during development
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

# ── Config ───────────────────────────────────────────────────────────
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN") or ""
CAMP_API_BASE = (os.environ.get("CAMP_API_BASE") or "https://highergradetutoring.ca").rstrip("/")
BOT_API_TOKEN = os.environ.get("BOT_API_TOKEN") or ""
GUILD_ID = os.environ.get("GUILD_ID") or ""
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS") or 120)

# New-registration announcer: which channel to post in, and how often to check
# the live enrolled count (/api/stats/enrolled). If REGISTER_CHANNEL_ID is unset
# we fall back to a channel named "registrations"/"general" or the system channel.
REGISTER_CHANNEL_ID = os.environ.get("REGISTER_CHANNEL_ID") or ""
REGISTER_POLL_SECONDS = int(os.environ.get("REGISTER_POLL_SECONDS") or 60)

# Names the bot manages on Discord. The bot creates these if missing
# and only ever adds/removes these specific roles — it never touches
# user-defined roles outside this set.
STUDENT_ROLE_NAME = "Student"

# Mapping of camp-side role IDs (from the `roles` table) to a friendly
# Discord role name. Names match exactly so admins can also create roles
# directly on Discord with the same name and have them mirror to camp.
CAMP_ROLE_NAMES: Dict[str, str] = {
    "mazewiz":    "Maze Wizard",
    "money_tree": "Money Tree",
    "clicker":    "Clicker",
    "crane":      "Paper Crane",
}


def _normalize_role_name(name: str) -> str:
    """Mirror server-side normalization so blocklist matching is
    consistent across the boundary. Case + whitespace + 'Camp · '-prefix
    insensitive."""
    if not name:
        return ""
    n = name.lower().strip()
    for prefix in ("camp · ", "camp - ", "camp: ", "camp ", "camp·", "camp:"):
        if n.startswith(prefix):
            n = n[len(prefix):].strip()
            break
    return "".join(c for c in n if not c.isspace())

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hg-bot")


def _required(name: str, value: str) -> None:
    if not value:
        raise SystemExit(f"Missing required env var: {name}")


_required("DISCORD_TOKEN", DISCORD_TOKEN)
_required("BOT_API_TOKEN", BOT_API_TOKEN)


# ── Camp API client ──────────────────────────────────────────────────
class CampAPI:
    """Thin async wrapper around the bot endpoints on the camp server."""

    def __init__(self, base: str, token: str) -> None:
        self.base = base
        self.headers = {"Authorization": f"Bearer {token}"}
        self._session: Optional[aiohttp.ClientSession] = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        s = await self.session()
        async with s.post(self.base + path, json=body) as r:
            data = await r.json(content_type=None)
            data["_status"] = r.status
            return data

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        s = await self.session()
        async with s.get(self.base + path, params=params or {}) as r:
            data = await r.json(content_type=None)
            data["_status"] = r.status
            return data

    async def _delete(self, path: str) -> Dict[str, Any]:
        s = await self.session()
        async with s.delete(self.base + path) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:  # noqa: BLE001
                data = {"ok": r.status == 200}
            data["_status"] = r.status
            return data

    # — Linking —
    async def link(self, discord_id: str, guild_id: str, email: str, password: str) -> Dict[str, Any]:
        return await self._post("/api/bot/link", {
            "discordId": discord_id, "guildId": guild_id,
            "email": email, "password": password,
        })

    async def unlink(self, discord_id: str) -> Dict[str, Any]:
        return await self._post("/api/bot/unlink", {"discordId": discord_id})

    async def me(self, discord_id: str) -> Dict[str, Any]:
        return await self._get("/api/bot/me", {"discordId": discord_id})

    async def students(self, guild_id: str) -> Dict[str, Any]:
        return await self._get("/api/bot/students", {"guildId": guild_id})

    # — Open-call schedule (the /oncall sign-up board) —
    async def call_schedule(self) -> Dict[str, Any]:
        return await self._get("/api/bot/call-schedule")

    async def call_claim(self, date: str, name: str, number: str) -> Dict[str, Any]:
        return await self._post("/api/bot/call-schedule/claim",
                                {"date": date, "name": name, "number": number})

    # — Chests —
    async def chest_create(self, guild_id: str, code: str, role_id: str, role_name: str,
                           description: str, created_by: str,
                           image_url: Optional[str] = None) -> Dict[str, Any]:
        return await self._post("/api/bot/chests", {
            "guildId": guild_id, "code": code, "roleId": role_id, "roleName": role_name,
            "description": description, "createdBy": created_by,
            "imageUrl": image_url or "",
        })

    async def chest_set_message(self, chest_id: str, channel_id: str, message_id: str) -> Dict[str, Any]:
        return await self._post(f"/api/bot/chests/{chest_id}/message", {
            "channelId": channel_id, "messageId": message_id,
        })

    async def chest_list(self, guild_id: str) -> Dict[str, Any]:
        return await self._get("/api/bot/chests", {"guildId": guild_id})

    async def chest_delete(self, chest_id: str) -> Dict[str, Any]:
        return await self._delete(f"/api/bot/chests/{chest_id}")

    async def chest_claim(self, guild_id: str, discord_id: str, code: str,
                          chest_id: Optional[str] = None) -> Dict[str, Any]:
        body = {"guildId": guild_id, "discordId": discord_id, "code": code}
        if chest_id:
            body["chestId"] = chest_id
        return await self._post("/api/bot/chests/claim", body)

    # — Role-mirror blocklist + per-student push —
    async def role_mirror_list(self, guild_id: str) -> Dict[str, Any]:
        return await self._get("/api/bot/role-mirror/blocklist", {"guildId": guild_id})

    async def role_mirror_add(self, guild_id: str, role_id: str, role_name: str,
                              added_by: str) -> Dict[str, Any]:
        return await self._post("/api/bot/role-mirror/blocklist", {
            "guildId": guild_id, "roleId": role_id, "roleName": role_name,
            "addedBy": added_by,
        })

    async def role_mirror_remove(self, guild_id: str, role_id: str) -> Dict[str, Any]:
        return await self._post("/api/bot/role-mirror/blocklist/remove", {
            "guildId": guild_id, "roleId": role_id,
        })

    async def mirror_discord_roles(self, student_id: str, role_names: List[str]) -> Dict[str, Any]:
        return await self._post(
            f"/api/bot/students/{student_id}/mirror-discord-roles",
            {"roleNames": role_names},
        )

    # — Command-permission grants —
    async def perms_list(self, guild_id: str) -> Dict[str, Any]:
        return await self._get("/api/bot/perms", {"guildId": guild_id})

    async def perms_grant(self, guild_id: str, command: str, role_id: str,
                          role_name: str, created_by: str) -> Dict[str, Any]:
        return await self._post("/api/bot/perms", {
            "guildId": guild_id, "command": command, "roleId": role_id,
            "roleName": role_name, "createdBy": created_by,
        })

    async def perms_revoke(self, guild_id: str, command: str, role_id: str) -> Dict[str, Any]:
        return await self._post("/api/bot/perms/revoke", {
            "guildId": guild_id, "command": command, "roleId": role_id,
        })


api = CampAPI(CAMP_API_BASE, BOT_API_TOKEN)


# ── Discord client ───────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True   # PRIVILEGED — must be enabled in dev portal


class HGBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        # Persistent chest buttons — survive bot restarts because the
        # button's custom_id encodes the chest_id and discord.py
        # reconstructs the handler from the regex template.
        self.add_dynamic_items(ChestUnlockButton)
        # Sync slash commands. If GUILD_ID is set we sync to that guild
        # only (instant); otherwise the global sync that can take up
        # to an hour to propagate is used.
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        sync_loop.start()
        enrolled_announce_loop.start()   # @everyone ping on each new registration
        # Sponsor-campaign mailbox poll (queue commands were registered at import).
        try:
            campaign.start_review_loop()
        except Exception:
            log.exception("could not start campaign review loop")


bot = HGBot()

# Register the sponsor-campaign slash commands on the tree BEFORE it syncs.
try:
    import campaign
    campaign.setup(bot)
except Exception:
    log.exception("campaign setup failed — queue commands unavailable")


# ── Helpers ──────────────────────────────────────────────────────────
async def ensure_role(guild: discord.Guild, name: str, *, color: discord.Color = discord.Color.default(),
                      hoist: bool = False) -> discord.Role:
    """Look up a role by exact name, creating it if missing. The bot's
    own top role must be above any role it manages — Discord won't let
    us assign a role we can't reach."""
    existing = discord.utils.get(guild.roles, name=name)
    if existing:
        return existing
    return await guild.create_role(name=name, color=color, hoist=hoist, mentionable=False,
                                   reason="HigherGrade bot — auto-create managed role")


def _camp_discord_role_names() -> List[str]:
    """Discord role names that mirror the standard camp roles."""
    return list(CAMP_ROLE_NAMES.values())


async def _sync_member(
    member: discord.Member,
    summary: Dict[str, Any],
    blocklist_normalized: Optional[set] = None,
) -> None:
    """Reconcile a single member's nickname + roles against the camp
    summary returned by /api/bot/students. Also pushes the member's
    mirrorable Discord roles back to the camp so admin-granted Discord
    roles show up under the student's profile (additive — never
    removes camp roles)."""
    if blocklist_normalized is None:
        blocklist_normalized = set()
    full_name = (summary.get("fullName") or "").strip()

    # 1. Camp → Discord: ensure the user has a Discord role for every
    #    standard camp role (Maze Wizard, Money Tree, Clicker, Paper
    #    Crane), plus the Student verify marker.
    desired_camp_roles = {
        CAMP_ROLE_NAMES[r]
        for r in (summary.get("roles") or [])
        if r in CAMP_ROLE_NAMES
    }
    desired_camp_roles.add(STUDENT_ROLE_NAME)

    managed_names = set(_camp_discord_role_names()) | {STUDENT_ROLE_NAME}

    add: List[discord.Role] = []
    remove: List[discord.Role] = []
    for name in managed_names:
        role = discord.utils.get(member.guild.roles, name=name)
        if not role:
            continue
        has_it = role in member.roles
        wants_it = name in desired_camp_roles
        if wants_it and not has_it:
            add.append(role)
        elif not wants_it and has_it:
            remove.append(role)

    try:
        if add:
            await member.add_roles(*add, reason="HigherGrade sync")
        if remove:
            await member.remove_roles(*remove, reason="HigherGrade sync")
    except discord.Forbidden:
        log.warning("Missing permission to update roles on %s", member)

    # 2. Discord → Camp: push the union of mirrorable Discord role names
    #    back to the camp. Skip @everyone, the Student verify marker,
    #    Discord-managed roles (Boosters / integrations), and anything
    #    in the blocklist. Server merges additively + auto-creates
    #    matching camp roles when needed.
    student_id = summary.get("studentId")
    if student_id:
        mirror_names: List[str] = []
        seen_norm: set = set()
        for r in member.roles:
            if r.is_default():
                continue
            if r.managed:
                continue
            if r.name == STUDENT_ROLE_NAME:
                continue
            norm = _normalize_role_name(r.name)
            if not norm or norm in blocklist_normalized or norm in seen_norm:
                continue
            seen_norm.add(norm)
            mirror_names.append(r.name)
        try:
            await api.mirror_discord_roles(student_id, mirror_names)
        except Exception:  # noqa: BLE001
            log.exception("mirror_discord_roles failed for %s", student_id)

    # 3. Nickname.
    if full_name and member.display_name != full_name:
        try:
            await member.edit(nick=full_name[:32], reason="HigherGrade sync")
        except discord.Forbidden:
            # Server owner can't be renamed — silent skip.
            pass


# ── Polling ──────────────────────────────────────────────────────────
@tasks.loop(seconds=POLL_INTERVAL_SECONDS)
async def sync_loop() -> None:
    if not bot.is_ready():
        return
    for guild in bot.guilds:
        try:
            # Make sure the managed roles exist before we try to assign them.
            await ensure_role(guild, STUDENT_ROLE_NAME, color=discord.Color.blurple(), hoist=True)
            for camp_role_name in CAMP_ROLE_NAMES.values():
                await ensure_role(guild, camp_role_name, color=discord.Color.gold())
            # Fetch the blocklist once per guild so _sync_member doesn't
            # round-trip the API for every member.
            blocklist_normalized = await _fetch_blocklist_normalized(guild.id)
            data = await api.students(str(guild.id))
            if not data.get("ok"):
                log.warning("Skipping guild %s — students fetch failed: %s", guild.id, data)
                continue
            for summary in data.get("data") or []:
                discord_id = summary.get("discordId")
                if not discord_id:
                    continue
                member = guild.get_member(int(discord_id))
                if member is None:
                    try:
                        member = await guild.fetch_member(int(discord_id))
                    except discord.NotFound:
                        continue
                await _sync_member(member, summary, blocklist_normalized)
        except Exception:  # noqa: BLE001
            log.exception("sync error in guild %s", getattr(guild, "id", "?"))


async def _fetch_blocklist_normalized(guild_id: int) -> set:
    """Pull the role-mirror blocklist for a guild and return the set of
    normalized role names. Falls back to empty on API failure (so a
    transient outage doesn't silently start mirroring blocklisted roles
    — the rest of sync still runs)."""
    try:
        res = await api.role_mirror_list(str(guild_id))
        if not res.get("ok"):
            return set()
        return {
            _normalize_role_name((r.get("roleName") or ""))
            for r in (res.get("data") or [])
            if r.get("roleName")
        }
    except Exception:  # noqa: BLE001
        log.exception("blocklist fetch failed for guild %s", guild_id)
        return set()


@sync_loop.before_loop
async def _before_sync() -> None:
    await bot.wait_until_ready()


# ── New-registration announcer ───────────────────────────────────────
# Polls the same public counter the homepage uses (/api/stats/enrolled →
# {ok, enrolled, cap}). When the count goes UP, it @everyone-pings a channel.
# On the first poll we only record a baseline — we do NOT announce the campers
# who were already enrolled when the bot started.
_last_enrolled: Optional[int] = None


def _register_channel() -> Optional[discord.abc.Messageable]:
    """Resolve where to post registration announcements: REGISTER_CHANNEL_ID
    first, otherwise a channel named 'registrations'/'general' or the guild's
    system channel. None if nothing usable is found."""
    if REGISTER_CHANNEL_ID:
        ch = bot.get_channel(int(REGISTER_CHANNEL_ID))
        if ch is not None:
            return ch
        log.warning("REGISTER_CHANNEL_ID=%s not found — falling back", REGISTER_CHANNEL_ID)
    for guild in bot.guilds:
        ch = (discord.utils.get(guild.text_channels, name="registrations")
              or discord.utils.get(guild.text_channels, name="general")
              or guild.system_channel)
        if ch is not None:
            return ch
    return None


@tasks.loop(seconds=REGISTER_POLL_SECONDS)
async def enrolled_announce_loop() -> None:
    global _last_enrolled
    if not bot.is_ready():
        return
    try:
        data = await api._get("/api/stats/enrolled")
    except Exception:  # noqa: BLE001
        log.exception("enrolled-count poll failed")
        return
    if not data.get("ok"):
        return
    try:
        enrolled = int(data.get("enrolled"))
    except (TypeError, ValueError):
        return
    cap = data.get("cap")

    # First successful poll → set the baseline silently (don't ping for the
    # campers already enrolled when the bot booted).
    if _last_enrolled is None:
        _last_enrolled = enrolled
        log.info("registration baseline set at %s enrolled", enrolled)
        return

    if enrolled > _last_enrolled:
        gained = enrolled - _last_enrolled
        _last_enrolled = enrolled
        channel = _register_channel()
        if channel is None:
            log.warning("%s new registration(s) but no channel to announce in "
                        "— set REGISTER_CHANNEL_ID", gained)
            return
        noun = "camper" if gained == 1 else "campers"
        cap_str = f" / {cap}" if cap else ""
        msg = (f"@everyone 🎉 **{gained} new {noun} just registered!** "
               f"We're now at **{enrolled}{cap_str}** campers enrolled — "
               f"let's keep the momentum going! 🚀")
        try:
            await channel.send(
                msg, allowed_mentions=discord.AllowedMentions(everyone=True))
            log.info("announced %s new registration(s); now at %s", gained, enrolled)
        except discord.Forbidden:
            log.warning("can't post/@everyone in channel %s — check the bot's "
                        "permissions (needs Send Messages + Mention Everyone)",
                        getattr(channel, "id", "?"))
        except Exception:  # noqa: BLE001
            log.exception("failed to send registration announcement")
    elif enrolled < _last_enrolled:
        # Count dropped (un-enroll / frozen) — quietly re-baseline.
        _last_enrolled = enrolled


@enrolled_announce_loop.before_loop
async def _before_enrolled() -> None:
    await bot.wait_until_ready()


@bot.tree.command(name="enrolled",
                  description="How many campers are currently enrolled.")
async def enrolled_cmd(interaction: discord.Interaction) -> None:
    """Anyone can check the live enrolled count (it's public on the homepage
    anyway). Replies privately so it doesn't clutter the channel."""
    await interaction.response.defer(ephemeral=True)
    try:
        data = await api._get("/api/stats/enrolled")
    except Exception:  # noqa: BLE001
        log.exception("/enrolled fetch failed")
        await interaction.followup.send(
            "Couldn't reach the enrollment stats right now — try again shortly.",
            ephemeral=True)
        return
    if not data.get("ok"):
        await interaction.followup.send(
            "Enrollment stats are unavailable right now.", ephemeral=True)
        return
    enrolled = data.get("enrolled")
    cap = data.get("cap")
    cap_str = f" / {cap}" if cap else ""
    spots = ""
    try:
        if cap is not None:
            left = int(cap) - int(enrolled)
            spots = f"  ·  **{left}** spot(s) left" if left > 0 else "  ·  🎉 **full!**"
    except (TypeError, ValueError):
        pass
    await interaction.followup.send(
        f"🧮 **{enrolled}{cap_str}** campers enrolled.{spots}", ephemeral=True)


@bot.tree.command(name="campers",
                  description="Camper breakdown: total, paid, and registered-not-paid.")
async def campers_cmd(interaction: discord.Interaction) -> None:
    """Full camper picture in one command: total registered, how many have paid
    (account unfrozen), and how many registered but haven't paid yet."""
    await interaction.response.defer(ephemeral=True)
    try:
        data = await api._get("/api/stats/campers")
    except Exception:  # noqa: BLE001
        log.exception("/campers fetch failed")
        await interaction.followup.send(
            "Couldn't reach the camper stats right now — try again shortly.",
            ephemeral=True)
        return
    if not data.get("ok"):
        await interaction.followup.send(
            "Camper stats are unavailable right now.", ephemeral=True)
        return
    total = data.get("total", 0)
    paid = data.get("paid", 0)
    unpaid = data.get("unpaid", 0)
    cap = data.get("cap")
    cap_line = f" (cap {cap})" if cap else ""
    await interaction.followup.send(
        f"🏕️ **Camper breakdown**{cap_line}\n"
        f"• **Total registered:** {total}\n"
        f"• ✅ **Paid:** {paid}\n"
        f"• ⏳ **Registered, not yet paid:** {unpaid}",
        ephemeral=True)


# ── Open-call sign-up board (/oncall) ────────────────────────────────
def _oncall_embed(days: List[Dict[str, Any]]) -> discord.Embed:
    e = discord.Embed(
        title="📞 Open-call schedule — next 10 days",
        description="Pick a day from the menu to sign up. The number you enter "
                    "shows on the website's Contact page during that day's "
                    "5–8 PM call window.",
        colour=discord.Colour.blurple())
    for d in days:
        if d.get("name") or d.get("number"):
            val = f"✅ {d.get('name') or '—'}"
            if d.get("number"):
                val += f" · {d['number']}"
        else:
            val = "— open —"
        e.add_field(name=d["label"], value=val, inline=True)
    return e


class OnCallModal(discord.ui.Modal, title="Sign up for call duty"):
    def __init__(self, view: "OnCallView", date: str, cur: Dict[str, Any]):
        super().__init__()
        self.view_ref = view
        self.date = date
        self.cur = cur
        self.number = discord.ui.TextInput(
            label="Your phone number for this day",
            placeholder="e.g. 343-368-2005 — leave blank to remove yourself",
            default=cur.get("number", ""), required=False, max_length=40)
        self.add_item(self.number)

    async def on_submit(self, interaction: discord.Interaction):
        name = interaction.user.display_name
        number = str(self.number.value).strip()
        label = self.cur.get("label", self.date)
        if not number:
            cur_name = (self.cur.get("name") or "").strip().lower()
            if cur_name and cur_name == name.strip().lower():
                await api.call_claim(self.date, "", "")
                msg = f"🗑️ Removed you from **{label}**."
            else:
                await interaction.response.send_message(
                    "Enter a phone number to sign up. (You can only clear a day "
                    "you signed up for yourself.)", ephemeral=True)
                return
        else:
            await api.call_claim(self.date, name, number)
            msg = f"✅ You're on call for **{label}** — number **{number}**."
        # Refresh the board in place.
        try:
            data = await api.call_schedule()
            if data.get("ok") and self.view_ref.message:
                fresh = OnCallView(data["days"])
                fresh.message = self.view_ref.message
                await self.view_ref.message.edit(
                    embed=_oncall_embed(data["days"]), view=fresh)
        except Exception:  # noqa: BLE001
            log.exception("oncall board refresh failed")
        await interaction.response.send_message(msg, ephemeral=True)


class OnCallView(discord.ui.View):
    def __init__(self, days: List[Dict[str, Any]]):
        super().__init__(timeout=600)
        self.days = days
        self.message: Optional[discord.Message] = None
        opts = []
        for d in days:
            taken = d.get("name") or d.get("number")
            desc = (f"{d.get('name','')} · {d['number']}" if d.get("number")
                    else (d.get("name") or "open"))
            opts.append(discord.SelectOption(
                label=d["label"], value=d["date"], description=desc[:100],
                emoji="✅" if taken else "⬜"))
        self.picker = discord.ui.Select(
            placeholder="Pick a day to sign up for…", options=opts)
        self.picker.callback = self._on_pick
        self.add_item(self.picker)

    async def _on_pick(self, interaction: discord.Interaction):
        date = self.picker.values[0]
        cur = next((d for d in self.days if d["date"] == date), {"label": date})
        await interaction.response.send_modal(OnCallModal(self, date, cur))


@bot.tree.command(name="oncall",
                  description="See the call-window schedule and sign yourself up.")
async def oncall_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        data = await api.call_schedule()
    except Exception:  # noqa: BLE001
        log.exception("/oncall fetch failed")
        await interaction.followup.send(
            "Couldn't load the call schedule right now — try again shortly.",
            ephemeral=True)
        return
    if not data.get("ok"):
        await interaction.followup.send(
            "Call schedule is unavailable right now.", ephemeral=True)
        return
    view = OnCallView(data["days"])
    view.message = await interaction.followup.send(
        embed=_oncall_embed(data["days"]), view=view, ephemeral=True)


# ── Locked-chest interactive UI ──────────────────────────────────────
# Each chest gets posted as a public embed with a button. Clicking the
# button opens a modal asking for the passcode. Submitting the right
# code grants the role. Multi-claim is allowed — the chest message stays
# in the channel so anyone with the code can keep opening it.

class ChestUnlockModal(discord.ui.Modal, title="🔒 Locked chest"):
    code = discord.ui.TextInput(
        label="Passcode",
        placeholder="Enter the chest's secret code",
        min_length=1, max_length=128, required=True,
    )

    def __init__(self, chest_id: str) -> None:
        super().__init__()
        self.chest_id = chest_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild:
            await interaction.followup.send("Run this in a server.", ephemeral=True)
            return
        res = await api.chest_claim(
            str(interaction.guild.id), str(interaction.user.id),
            str(self.code.value).strip(), chest_id=self.chest_id,
        )
        if not res.get("ok"):
            await interaction.followup.send(
                f"🔒 {res.get('error') or 'Wrong code.'}", ephemeral=True,
            )
            return
        payload = res.get("data") or {}
        role_id = payload.get("roleId")
        description = payload.get("description") or "(no description set)"
        role = interaction.guild.get_role(int(role_id)) if role_id else None
        member = interaction.user if isinstance(interaction.user, discord.Member) else \
                 await interaction.guild.fetch_member(interaction.user.id)
        granted = False
        already = bool(payload.get("alreadyClaimed"))
        if role and member and role not in member.roles:
            try:
                await member.add_roles(role, reason="HigherGrade chest unlock")
                granted = True
            except discord.Forbidden:
                pass
        msg = f"🗝 **Chest opened!**\n\n{description}"
        if granted and role:
            msg += f"\n\n✅ Role granted: **{role.name}**"
        elif already and role:
            msg += f"\n\n(You already have **{role.name}** — opened previously.)"
        elif role:
            msg += "\n\n⚠️ Couldn't grant the role — ask an admin to put my role above it in Server Settings → Roles."
        await interaction.followup.send(msg, ephemeral=True)


class ChestUnlockButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"chest_unlock:(?P<chest_id>[A-Za-z0-9_\-]+)",
):
    """Persistent button. discord.py reconstructs the instance on every
    restart by matching the custom_id against the regex template — so old
    chest messages keep working even after a deploy."""

    def __init__(self, chest_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Open with code 🗝",
                style=discord.ButtonStyle.primary,
                custom_id=f"chest_unlock:{chest_id}",
            )
        )
        self.chest_id = chest_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: discord.ui.Button, match):
        return cls(match["chest_id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ChestUnlockModal(self.chest_id))


def _chest_view(chest_id: str) -> discord.ui.View:
    """Build a one-shot view containing a persistent button. Used at
    chest-creation time when we post the embed."""
    view = discord.ui.View(timeout=None)
    view.add_item(ChestUnlockButton(chest_id))
    return view


def _chest_embed(description: str, image_url: Optional[str] = None,
                 role_name: Optional[str] = None) -> discord.Embed:
    e = discord.Embed(
        title="🔒 Locked Chest",
        description=description or "*(no description)*",
        color=discord.Color.purple(),
    )
    if image_url:
        e.set_image(url=image_url)
    footer = "Tap the button below and enter the passcode to open."
    if role_name:
        footer += f" Unlocks: {role_name}."
    e.set_footer(text=footer)
    return e


# ── Slash commands ───────────────────────────────────────────────────
@bot.tree.command(name="verify", description="Link your Discord account to your camp account.")
@app_commands.describe(
    email="Your camp email (the one you registered with)",
    password="Your camp account password",
)
async def cmd_verify(interaction: discord.Interaction, email: str, password: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not interaction.guild:
        await interaction.followup.send("Run this in a server, not a DM.", ephemeral=True)
        return
    res = await api.link(str(interaction.user.id), str(interaction.guild.id), email.strip(), password)
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Verification failed.'}", ephemeral=True)
        return
    summary = res.get("data") or {}
    full_name = (summary.get("fullName") or "").strip()
    student_role = await ensure_role(interaction.guild, STUDENT_ROLE_NAME,
                                     color=discord.Color.blurple(), hoist=True)
    member = interaction.user if isinstance(interaction.user, discord.Member) else \
             await interaction.guild.fetch_member(interaction.user.id)
    role_warning = ""
    try:
        await member.add_roles(student_role, reason="HigherGrade verify")
    except discord.Forbidden:
        role_warning = "⚠️ Couldn't grant the **Student** role — ask an admin to put my role above it."

    # Also sync any camp-game roles the student already holds.
    blocklist_normalized = await _fetch_blocklist_normalized(interaction.guild.id)
    await _sync_member(member, summary, blocklist_normalized)

    # Force a nickname update directly here (even if _sync_member skipped
    # it) so we can give the user a clear pass/fail reason.
    nick_status = ""
    if full_name:
        if member.id == interaction.guild.owner_id:
            nick_status = (
                f"\n\nℹ️ Discord doesn't let bots rename the **server owner** — "
                f"please set your nickname to **{full_name}** manually."
            )
        elif member.top_role >= (interaction.guild.me.top_role if interaction.guild.me else member.top_role):
            nick_status = (
                f"\n\nℹ️ Couldn't update your nickname because your top role is at-or-above mine. "
                f"Set it to **{full_name}** manually, or ask an admin to move my role higher."
            )
        else:
            try:
                await member.edit(nick=full_name[:32], reason="HigherGrade verify")
                nick_status = f"\n\n📛 Nickname updated to **{full_name[:32]}**."
            except discord.Forbidden:
                nick_status = (
                    f"\n\nℹ️ Couldn't update your nickname — please set it to **{full_name}** manually."
                )

    msg = (
        f"✅ Linked to **{full_name or 'your camp account'}** · "
        f"{summary.get('privatePoints', 0)} pts. Welcome!"
    )
    if role_warning:
        msg += "\n\n" + role_warning
    msg += nick_status
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="whoami", description="Show your linked camp profile.")
async def cmd_whoami(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    res = await api.me(str(interaction.user.id))
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
        return
    s = res.get("data")
    if not s:
        await interaction.followup.send("You haven't linked a camp account yet — run /verify.", ephemeral=True)
        return
    e = discord.Embed(
        title=s.get("fullName") or "(no name)",
        description=s.get("className") or "Unassigned",
        color=discord.Color.blurple(),
    )
    e.add_field(name="Current pts",   value=str(s.get("privatePoints", 0)))
    e.add_field(name="Total earned",  value=str(s.get("totalPointsEarned", 0)))
    e.add_field(name="Camp roles",
                value=", ".join(s.get("roles") or []) or "—",
                inline=False)
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="unlink", description="Remove the link between your Discord and camp account.")
async def cmd_unlink(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    await api.unlink(str(interaction.user.id))
    if isinstance(interaction.user, discord.Member):
        managed = set(_camp_discord_role_names()) | {STUDENT_ROLE_NAME}
        to_remove = [r for r in interaction.user.roles if r.name in managed]
        try:
            if to_remove:
                await interaction.user.remove_roles(*to_remove, reason="HigherGrade unlink")
        except discord.Forbidden:
            pass
    await interaction.followup.send("Unlinked.", ephemeral=True)


@bot.tree.command(name="unlock", description="Open a locked chest with its passcode.")
@app_commands.describe(code="The chest's secret code")
async def cmd_unlock(interaction: discord.Interaction, code: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not interaction.guild:
        await interaction.followup.send("Run this in a server.", ephemeral=True)
        return
    res = await api.chest_claim(str(interaction.guild.id), str(interaction.user.id), code.strip())
    if not res.get("ok"):
        await interaction.followup.send(f"🔒 {res.get('error') or 'Wrong code.'}", ephemeral=True)
        return
    payload = res.get("data") or {}
    role_id = payload.get("roleId")
    description = payload.get("description") or "(no description set)"
    role = interaction.guild.get_role(int(role_id)) if role_id else None
    member = interaction.user if isinstance(interaction.user, discord.Member) else \
             await interaction.guild.fetch_member(interaction.user.id)
    granted = False
    if role and member:
        try:
            await member.add_roles(role, reason="HigherGrade chest unlock")
            granted = True
        except discord.Forbidden:
            pass
    msg = f"🗝 **Chest opened!** {description}"
    if granted:
        msg += f"\n\nRole granted: **{role.name}**"
    elif payload.get("alreadyClaimed"):
        msg += "\n\n(You'd already opened this one.)"
    else:
        msg += "\n\n⚠️ Couldn't grant the linked role — ask an admin to put me above it in the role list."
    await interaction.followup.send(msg, ephemeral=True)


# ── Admin commands ───────────────────────────────────────────────────
# Commands whose access can be opened up to specific roles via
# /perms-grant. Anything not in this list is implicitly admin/owner-only
# (or open, depending on the command).
RESTRICTABLE_COMMANDS: List[str] = ["chest-create", "chest-list", "chest-delete"]


def _is_server_admin(interaction: discord.Interaction) -> bool:
    """Server owner OR Administrator permission. Used for the meta
    permission commands (only owner/admin can grant access to others)."""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.id == interaction.guild.owner_id:
        return True
    return bool(interaction.user.guild_permissions.administrator)


async def _user_can_run(interaction: discord.Interaction, command: str) -> bool:
    """Return True if the interacting member is allowed to run the
    given command in this guild. Server owners and Administrators always
    pass. Otherwise the user must hold a role explicitly granted access
    via /perms-grant; if no grants exist yet, only owner/admin pass."""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    if _is_server_admin(interaction):
        return True
    res = await api.perms_list(str(interaction.guild.id))
    if not res.get("ok"):
        # Be conservative if the API is down — only owner/admin run.
        return False
    allowed = {
        p["roleId"]
        for p in (res.get("data") or [])
        if p.get("command") == command
    }
    if not allowed:
        return False
    user_role_ids = {str(r.id) for r in interaction.user.roles}
    return bool(allowed & user_role_ids)


@bot.tree.command(name="chest-create", description="Place a locked chest in this channel.")
@app_commands.describe(
    code="The passcode players must type to unlock",
    role="The role granted on unlock",
    description="Reveal text shown when the chest is opened",
    image="Optional image to embed in the chest message",
)
async def cmd_chest_create(
    interaction: discord.Interaction,
    code: str,
    role: discord.Role,
    description: str,
    image: Optional[discord.Attachment] = None,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not await _user_can_run(interaction, "chest-create"):
        await interaction.followup.send(
            "🚫 You don't have permission to run **chest-create** in this server. "
            "Ask a server admin to grant your role with `/perms-grant`.",
            ephemeral=True,
        )
        return
    me = interaction.guild.me if interaction.guild else None
    if me and role >= me.top_role:
        await interaction.followup.send(
            f"❌ I can't grant **{role.name}** — it's above my top role. "
            "Move my role above it in Server Settings → Roles.",
            ephemeral=True,
        )
        return
    if image is not None and not (image.content_type or "").startswith("image/"):
        await interaction.followup.send(
            "❌ The `image` attachment doesn't look like an image file.",
            ephemeral=True,
        )
        return
    image_url = image.url if image else None
    res = await api.chest_create(
        str(interaction.guild.id), code.strip(), str(role.id), role.name,
        description.strip(), str(interaction.user.id),
        image_url=image_url,
    )
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
        return
    chest_id = (res.get("data") or {}).get("id")
    if not chest_id:
        await interaction.followup.send("❌ Server didn't return a chest id.", ephemeral=True)
        return

    embed = _chest_embed(description.strip(), image_url=image_url, role_name=role.name)
    view = _chest_view(chest_id)

    # Post the public chest message into the same channel the admin ran
    # the command in. The button is persistent, so this message keeps
    # working forever (until the chest is deleted).
    posted = None
    try:
        posted = await interaction.channel.send(embed=embed, view=view)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I can't send messages in this channel — give me Send Messages + Embed Links permission and try again.",
            ephemeral=True,
        )
        # Roll back the chest record so we don't leave a phantom entry.
        await api.chest_delete(chest_id)
        return

    # Save the channel + message id back to the chest record so admins
    # can find / clean up old chests later.
    if posted:
        try:
            await api.chest_set_message(chest_id, str(posted.channel.id), str(posted.id))
        except Exception:  # noqa: BLE001
            log.exception("failed to save chest message id")

    await interaction.followup.send(
        f"📦 Chest placed in {posted.channel.mention if posted else 'this channel'}. "
        f"Code is **{code}** · unlocks **{role.name}**.",
        ephemeral=True,
    )


@bot.tree.command(name="chest-list", description="List every chest in this server.")
async def cmd_chest_list(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not await _user_can_run(interaction, "chest-list"):
        await interaction.followup.send(
            "🚫 You don't have permission to run **chest-list**. "
            "Ask a server admin to grant your role with `/perms-grant`.",
            ephemeral=True,
        )
        return
    res = await api.chest_list(str(interaction.guild.id))
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
        return
    chests = res.get("data") or []
    if not chests:
        await interaction.followup.send("No chests in this server yet.", ephemeral=True)
        return
    lines = []
    for c in chests:
        lines.append(
            f"• `{c['id']}` · code **{c['code']}** → <@&{c['roleId']}> "
            f"· {c.get('claimedCount', 0)} unlock(s) · {c.get('description') or '(no description)'}"
        )
    await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)


@bot.tree.command(name="chest-delete", description="Remove a chest by id.")
@app_commands.describe(chest_id="ID shown by /chest-list")
async def cmd_chest_delete(interaction: discord.Interaction, chest_id: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not await _user_can_run(interaction, "chest-delete"):
        await interaction.followup.send(
            "🚫 You don't have permission to run **chest-delete**. "
            "Ask a server admin to grant your role with `/perms-grant`.",
            ephemeral=True,
        )
        return
    await api.chest_delete(chest_id.strip())
    await interaction.followup.send(f"🗑 Chest `{chest_id}` deleted.", ephemeral=True)


# ── Permission management (admin/owner only) ────────────────────────

@bot.tree.command(name="perms-grant", description="Allow a role to run a restricted command.")
@app_commands.describe(
    command="The command to grant access to",
    role="Members of this role will be able to run the command",
)
@app_commands.choices(command=[
    app_commands.Choice(name=c, value=c) for c in RESTRICTABLE_COMMANDS
])
async def cmd_perms_grant(
    interaction: discord.Interaction,
    command: app_commands.Choice[str],
    role: discord.Role,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not _is_server_admin(interaction):
        await interaction.followup.send(
            "🚫 Only the server owner or members with **Administrator** can manage permissions.",
            ephemeral=True,
        )
        return
    res = await api.perms_grant(
        str(interaction.guild.id), command.value, str(role.id), role.name,
        str(interaction.user.id),
    )
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
        return
    already = (res.get("data") or {}).get("alreadyExisted")
    msg = (
        f"{'ℹ️ Already granted' if already else '✅ Granted'} "
        f"**{role.name}** access to `/{command.value}`."
    )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="perms-revoke", description="Remove a role's access to a restricted command.")
@app_commands.describe(
    command="The command to revoke access from",
    role="The role losing access",
)
@app_commands.choices(command=[
    app_commands.Choice(name=c, value=c) for c in RESTRICTABLE_COMMANDS
])
async def cmd_perms_revoke(
    interaction: discord.Interaction,
    command: app_commands.Choice[str],
    role: discord.Role,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not _is_server_admin(interaction):
        await interaction.followup.send(
            "🚫 Only the server owner or members with **Administrator** can manage permissions.",
            ephemeral=True,
        )
        return
    res = await api.perms_revoke(str(interaction.guild.id), command.value, str(role.id))
    removed = ((res.get("data") or {}).get("removed") or 0) if res.get("ok") else 0
    if removed:
        await interaction.followup.send(
            f"✅ Revoked **{role.name}**'s access to `/{command.value}`.",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            f"ℹ️ **{role.name}** didn't have access to `/{command.value}` in the first place.",
            ephemeral=True,
        )


@bot.tree.command(name="perms-list", description="Show which roles can run which commands.")
async def cmd_perms_list(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not _is_server_admin(interaction):
        await interaction.followup.send(
            "🚫 Only the server owner or Administrators can view the permission list.",
            ephemeral=True,
        )
        return
    res = await api.perms_list(str(interaction.guild.id))
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
        return
    perms = res.get("data") or []
    by_cmd: Dict[str, List[str]] = {c: [] for c in RESTRICTABLE_COMMANDS}
    for p in perms:
        cmd = p.get("command") or ""
        if cmd in by_cmd:
            by_cmd[cmd].append(f"<@&{p['roleId']}>")
    lines = ["**Command access (Server owner & Administrators always pass):**", ""]
    for cmd in RESTRICTABLE_COMMANDS:
        roles = by_cmd[cmd]
        if roles:
            lines.append(f"• `/{cmd}` → {', '.join(roles)}")
        else:
            lines.append(f"• `/{cmd}` → *(no roles granted — admin/owner only)*")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


# ── Role-mirror blocklist (admin/owner only) ────────────────────────

@bot.tree.command(name="role-mirror-block",
                  description="Stop a Discord role from mirroring to the camp website.")
@app_commands.describe(role="The role that should NOT mirror to the website")
async def cmd_role_mirror_block(interaction: discord.Interaction, role: discord.Role) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not _is_server_admin(interaction):
        await interaction.followup.send(
            "🚫 Only the server owner or Administrators can manage the mirror blocklist.",
            ephemeral=True,
        )
        return
    res = await api.role_mirror_add(
        str(interaction.guild.id), str(role.id), role.name,
        str(interaction.user.id),
    )
    already = (res.get("data") or {}).get("alreadyExisted")
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
        return
    msg = (
        f"{'ℹ️ Already blocked' if already else '✅ Blocked'} "
        f"**{role.name}** from mirroring to the website."
    )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="role-mirror-unblock",
                  description="Allow a Discord role to mirror to the website again.")
@app_commands.describe(role="The role to unblock from mirroring")
async def cmd_role_mirror_unblock(interaction: discord.Interaction, role: discord.Role) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not _is_server_admin(interaction):
        await interaction.followup.send(
            "🚫 Only the server owner or Administrators can manage the mirror blocklist.",
            ephemeral=True,
        )
        return
    res = await api.role_mirror_remove(str(interaction.guild.id), str(role.id))
    removed = ((res.get("data") or {}).get("removed") or 0) if res.get("ok") else 0
    if removed:
        await interaction.followup.send(
            f"✅ **{role.name}** will now mirror to the website on the next sync.",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            f"ℹ️ **{role.name}** wasn't on the blocklist.", ephemeral=True,
        )


@bot.tree.command(name="role-mirror-list",
                  description="Show every Discord role currently blocked from mirroring.")
async def cmd_role_mirror_list(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not _is_server_admin(interaction):
        await interaction.followup.send(
            "🚫 Only the server owner or Administrators can view the blocklist.",
            ephemeral=True,
        )
        return
    res = await api.role_mirror_list(str(interaction.guild.id))
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
        return
    rows = res.get("data") or []
    if not rows:
        await interaction.followup.send(
            "📭 No roles are blocked. Every Discord role you grant a verified student will mirror to the website.",
            ephemeral=True,
        )
        return
    lines = ["**Blocked roles (these won't mirror to the website):**", ""]
    for r in rows:
        lines.append(f"• <@&{r['roleId']}> — *{r.get('roleName') or '?'}*")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


# ── Lifecycle ────────────────────────────────────────────────────────
@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (id=%s) — in %d guild(s)", bot.user, bot.user.id, len(bot.guilds))


def main() -> None:
    try:
        bot.run(DISCORD_TOKEN, log_handler=None)
    finally:
        # discord.py manages its own session; close ours.
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(api.close())
        finally:
            loop.close()


if __name__ == "__main__":
    main()
