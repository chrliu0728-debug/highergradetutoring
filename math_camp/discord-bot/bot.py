"""HigherGrade Tutoring — Discord bot.

Polls the camp API every 2 minutes and mirrors each linked student's
camp roles + display name into Discord. Also implements:

  /verify email password   → links a Discord user to their camp account
  /whoami                  → shows the linked student's stats
  /unlink                  → removes the link
  /unlock code             → grants the role hidden behind a chest
  /onboard                 → answer the onboarding questions

Onboarding: new members land in a single public #verify channel holding a
persistent "Verify me" panel. Verifying against the camp account is what
grants the Student role, and the Student/Staff roles are what open every
other channel (see /gate). Frozen accounts — registered but payment not
confirmed — are refused, and lose their roles again if re-frozen later.

Admin-only (Manage Roles permission):

  /chest-create code role description
  /chest-list
  /chest-delete chest_id

Admin-only (Administrator):

  /setup-verify            → post the Verify panel in the current channel
  /gate channel access     → set who can see a channel or category

Required environment variables:
  DISCORD_TOKEN     bot token from discord.com/developers
  CAMP_API_BASE     e.g. https://highergradetutoring.ca
  BOT_API_TOKEN     same secret you set in /etc/highergrade.env
  GUILD_ID          (optional) restrict slash commands to one server
                    for instant updates during development
  VERIFY_CHANNEL_ID  (optional) the public channel with the Verify panel
  ONBOARD_CHANNEL_ID (optional) staff-only channel for onboarding answers
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
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
# Staff channel (the email-review channel) where "new registration — watch for
# the e-Transfer" pings go. Same channel the reply review uses.
REVIEW_CHANNEL_ID = os.environ.get("REVIEW_CHANNEL_ID") or ""

# Onboarding. VERIFY_CHANNEL_ID is the one public channel new members can see
# — it holds the "Verify me" panel. ONBOARD_CHANNEL_ID is the staff-only
# channel the bot posts each member's onboarding answers into. Both optional:
# without them the bot falls back to channels named "verify"/"onboarding-answers".
VERIFY_CHANNEL_ID = os.environ.get("VERIFY_CHANNEL_ID") or ""
ONBOARD_CHANNEL_ID = os.environ.get("ONBOARD_CHANNEL_ID") or ""

# Where handed-in homework lands for staff to mark. Falls back to a channel
# named "marking-discussions"/"marking".
MARKING_CHANNEL_ID = os.environ.get("MARKING_CHANNEL_ID") or ""
# Discord's per-file upload ceiling on a non-boosted server is 25 MB.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES") or 25 * 1024 * 1024)

# Names the bot manages on Discord. The bot creates these if missing
# and only ever adds/removes these specific roles — it never touches
# user-defined roles outside this set.
STUDENT_ROLE_NAME = "Student"
# Staff is created (so /gate can reference it) but NEVER auto-assigned —
# there are no staff logins on the camp site, so an admin hands it out by
# hand in Server Settings → Members.
STAFF_ROLE_NAME = "Staff"

# Points a chest pays out on a first-time unlock, when the admin doesn't
# override it at creation time. 0 in the create modal disables the reward.
DEFAULT_CHEST_POINTS = int(os.environ.get("DEFAULT_CHEST_POINTS") or 50)

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
            try:
                data = await r.json(content_type=None)
            except Exception:  # noqa: BLE001
                data = None
            # A restarting API (or a Caddy 502) can return an empty/non-JSON
            # body — r.json() then yields None. Never index None; hand back a
            # well-formed dict so the caller just sees a failed request.
            if not isinstance(data, dict):
                data = {"ok": False, "raw": data}
            data["_status"] = r.status
            return data

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        s = await self.session()
        async with s.get(self.base + path, params=params or {}) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:  # noqa: BLE001
                data = None
            if not isinstance(data, dict):
                data = {"ok": False, "raw": data}
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

    async def call_claim(self, date: str, name: str, number: str,
                         discord_id: str = "") -> Dict[str, Any]:
        return await self._post("/api/bot/call-schedule/claim",
                                {"date": date, "name": name, "number": number,
                                 "discord_id": discord_id})

    # — Chests —
    async def chest_create(self, guild_id: str, code: str, role_id: str, role_name: str,
                           description: str, created_by: str,
                           image_url: Optional[str] = None,
                           points: int = DEFAULT_CHEST_POINTS,
                           max_claims: Optional[int] = None,
                           remove_role_id: str = "",
                           remove_role_name: str = "") -> Dict[str, Any]:
        return await self._post("/api/bot/chests", {
            "guildId": guild_id, "code": code, "roleId": role_id, "roleName": role_name,
            "description": description, "createdBy": created_by,
            "imageUrl": image_url or "",
            "points": points, "maxClaims": max_claims,
            "removeRoleId": remove_role_id or "",
            "removeRoleName": remove_role_name or "",
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

    # — Staff point awards —
    async def award(self, discord_id: str, amount: int, reason: str,
                    awarded_by: str) -> Dict[str, Any]:
        return await self._post("/api/bot/award", {
            "discordId": discord_id, "amount": amount,
            "reason": reason, "awardedBy": awarded_by,
        })

    # — Base stats (hand raises etc.) —
    async def base_stat_bump(self, discord_id: str, cat_id: str,
                             delta: int) -> Dict[str, Any]:
        return await self._post("/api/bot/base-stat", {
            "discordId": discord_id, "catId": cat_id, "delta": delta,
        })

    async def base_stats(self) -> Dict[str, Any]:
        return await self._get("/api/bot/base-stats")

    # — Attendance —
    async def attendance_mark(self, discord_id: str, status: str, date: str,
                              marked_by: str) -> Dict[str, Any]:
        return await self._post("/api/bot/attendance", {
            "discordId": discord_id, "status": status, "date": date,
            "markedBy": marked_by,
        })

    async def attendance_list(self, date: str = "") -> Dict[str, Any]:
        return await self._get("/api/bot/attendance", {"date": date} if date else None)

    # — Homework hand-in / marking —
    async def homework_create(self, guild_id: str, discord_id: str, title: str,
                              notes: str, attachments: List[Dict[str, Any]],
                              origin_channel_id: str = "") -> Dict[str, Any]:
        return await self._post("/api/bot/homework", {
            "guildId": guild_id, "discordId": discord_id, "title": title,
            "notes": notes, "attachments": attachments,
            "originChannelId": origin_channel_id,
        })

    async def homework_set_message(self, hid: str, channel_id: str,
                                   message_id: str) -> Dict[str, Any]:
        return await self._post(f"/api/bot/homework/{hid}/message", {
            "channelId": channel_id, "messageId": message_id,
        })

    async def homework_get(self, hid: str) -> Dict[str, Any]:
        return await self._get(f"/api/bot/homework/{hid}")

    async def homework_mark(self, hid: str, grade: str, feedback: str,
                            marked_by: str, marked_by_name: str) -> Dict[str, Any]:
        return await self._post(f"/api/bot/homework/{hid}/mark", {
            "grade": grade, "feedback": feedback,
            "markedBy": marked_by, "markedByName": marked_by_name,
        })

    async def homework_dm(self, hid: str, delivered: bool) -> Dict[str, Any]:
        return await self._post(f"/api/bot/homework/{hid}/dm", {"delivered": delivered})

    async def homework_list(self, guild_id: str, status: str = "") -> Dict[str, Any]:
        params = {"guildId": guild_id}
        if status:
            params["status"] = status
        return await self._get("/api/bot/homework", params)

    # — Per-role parameter locks —
    async def locks_list(self, guild_id: str) -> Dict[str, Any]:
        return await self._get("/api/bot/locks", {"guildId": guild_id})

    async def lock_set(self, guild_id: str, command: str, role_id: str, role_name: str,
                       field: str, value: str, created_by: str) -> Dict[str, Any]:
        return await self._post("/api/bot/locks", {
            "guildId": guild_id, "command": command, "roleId": role_id,
            "roleName": role_name, "field": field, "value": value,
            "createdBy": created_by,
        })

    async def lock_remove(self, guild_id: str, command: str, role_id: str,
                          field: str) -> Dict[str, Any]:
        return await self._post("/api/bot/locks/remove", {
            "guildId": guild_id, "command": command, "roleId": role_id, "field": field,
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
        self.add_dynamic_items(ChestUnlockButton, MarkButton)
        # Persistent onboarding views — fixed custom_ids, so the Verify
        # panel posted months ago still opens the modal after a redeploy.
        self.add_view(VerifyPanelView())
        self.add_view(OnboardingPromptView())
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
        enrolled_announce_loop.start()      # @everyone when a camper is confirmed/paid
        registration_announce_loop.start()  # staff ping on each new registration
        oncall_reminder_loop.start()        # DM the on-call person ~1h before shift
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
# ── Permission preflight ─────────────────────────────────────────────
# Every background task checks it can actually do the thing before it
# tries, and skips that task if not. The bot lives in more than one
# server (the camp server and the staff server) with deliberately
# different permissions in each, so "can't do X here" is a normal state
# to be skipped quietly — not an error to retry forever.
_WARNED: set = set()


def _warn_once(key: str, msg: str, *args: Any) -> None:
    """Log a permission gap once per process rather than every loop tick."""
    if key in _WARNED:
        return
    _WARNED.add(key)
    log.warning(msg, *args)


def _missing_guild_perms(guild: discord.Guild, *names: str) -> List[str]:
    """Which of the named guild-wide permissions the bot lacks here."""
    me = guild.me
    if me is None:
        return list(names)     # not cached yet — treat as "can't", don't warn
    perms = me.guild_permissions
    return [n for n in names if not getattr(perms, n, False)]


def _missing_channel_perms(channel: Any, *names: str) -> List[str]:
    """Which of the named permissions the bot lacks *in this channel*.
    Channel overwrites mean guild-wide perms aren't the whole story."""
    guild = getattr(channel, "guild", None)
    if guild is None:
        return []              # DM channel — nothing to check
    me = guild.me
    if me is None:
        return list(names)
    perms = channel.permissions_for(me)
    return [n for n in names if not getattr(perms, n, False)]


def _can_post(channel: Any, where: str, *, embeds: bool = False) -> bool:
    """Preflight for anything that posts a message. Warns once and returns
    False when the bot can't see or speak in the channel."""
    need = ["view_channel", "send_messages"] + (["embed_links"] if embeds else [])
    missing = _missing_channel_perms(channel, *need)
    if missing:
        _warn_once(f"post:{getattr(channel, 'id', '?')}:{','.join(missing)}",
                   "Skipping %s in channel %s — missing %s.",
                   where, getattr(channel, "id", "?"), ", ".join(missing))
        return False
    return True


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
    #
    #    A frozen account (payment not confirmed, or re-frozen by an admin
    #    after the fact) wants NO managed roles — the loop below then
    #    removes whatever it currently has, so re-freezing on the website
    #    revokes Discord access within one poll interval.
    if summary.get("frozen"):
        desired_camp_roles = set()
    else:
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
            if r.name in (STUDENT_ROLE_NAME, STAFF_ROLE_NAME):
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

    # 3. Nickname. Separate permission from role management, so check it on
    #    its own rather than letting a missing Manage Nicknames kill the
    #    role sync that already succeeded above.
    if full_name and member.display_name != full_name:
        if _missing_guild_perms(member.guild, "manage_nicknames"):
            _warn_once(f"nick:{member.guild.id}",
                       "Not syncing nicknames in '%s' (%s) — missing Manage Nicknames.",
                       member.guild.name, member.guild.id)
        else:
            try:
                await member.edit(nick=full_name[:32], reason="HigherGrade sync")
            except discord.Forbidden:
                # Server owner / higher-role member can't be renamed — silent skip.
                pass


# ── Polling ──────────────────────────────────────────────────────────
@tasks.loop(seconds=POLL_INTERVAL_SECONDS)
async def sync_loop() -> None:
    if not bot.is_ready():
        return
    for guild in bot.guilds:
        try:
            # Role syncing needs Manage Roles. A server where the bot is only
            # there to post (the staff server) legitimately won't have it —
            # skip the role work there and leave its posting tasks alone.
            missing = _missing_guild_perms(guild, "manage_roles")
            if missing:
                _warn_once(f"sync:{guild.id}",
                           "Skipping role sync in '%s' (%s) — missing %s. "
                           "Posting tasks in this server are unaffected.",
                           guild.name, guild.id, ", ".join(missing))
                continue
            # Make sure the managed roles exist before we try to assign them.
            await ensure_role(guild, STUDENT_ROLE_NAME, color=discord.Color.blurple(), hoist=True)
            # Never auto-assigned — created so /gate can grant it channel
            # access and so admins have a role to hand out by hand.
            await ensure_role(guild, STAFF_ROLE_NAME, color=discord.Color.green(), hoist=True)
            for camp_role_name in CAMP_ROLE_NAMES.values():
                await ensure_role(guild, camp_role_name, color=discord.Color.gold())
            # Fetch the blocklist once per guild so _sync_member doesn't
            # round-trip the API for every member.
            blocklist_normalized = await _fetch_blocklist_normalized(guild.id)
            # Keep the parameter-lock cache warm so /chest-create can
            # pre-fill locked fields without a round trip.
            await _refresh_locks(guild.id)
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
        if not _can_post(channel, "the enrolled announcement"):
            return
        # Missing Mention Everyone doesn't fail the send, it just silently
        # drops the ping — worth saying once, not worth skipping the post.
        if _missing_channel_perms(channel, "mention_everyone"):
            _warn_once(f"ping:{getattr(channel, 'id', '?')}",
                       "Channel %s: no Mention Everyone — announcements post "
                       "but won't actually ping.", getattr(channel, "id", "?"))
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


# ── New-registration ping — watch for the e-Transfer ─────────────────
# When the total registered count rises, ping the STAFF work channel so the team
# watches for that camper's payment. Distinct from the enrolled/paid milestone
# ping above (which celebrates confirmed campers).
_last_total_registered: Optional[int] = None


@tasks.loop(seconds=REGISTER_POLL_SECONDS)
async def registration_announce_loop() -> None:
    global _last_total_registered
    if not bot.is_ready():
        return
    try:
        data = await api._get("/api/stats/campers")
    except Exception:  # noqa: BLE001
        log.exception("registration poll failed")
        return
    if not data.get("ok"):
        return
    try:
        total = int(data.get("total"))
    except (TypeError, ValueError):
        return
    paid = data.get("paid", 0)
    unpaid = data.get("unpaid", 0)
    if _last_total_registered is None:          # baseline silently on first poll
        _last_total_registered = total
        log.info("registration baseline set at %s total", total)
        return
    if total > _last_total_registered:
        gained = total - _last_total_registered
        _last_total_registered = total
        if not REVIEW_CHANNEL_ID:
            log.warning("new registration(s) but REVIEW_CHANNEL_ID is unset")
            return
        channel = bot.get_channel(int(REVIEW_CHANNEL_ID))
        if channel is None:
            log.warning("registration ping: staff channel %s not found",
                        REVIEW_CHANNEL_ID)
            return
        if not _can_post(channel, "the registration ping"):
            return
        if _missing_channel_perms(channel, "mention_everyone"):
            _warn_once(f"ping:{getattr(channel, 'id', '?')}",
                       "Channel %s: no Mention Everyone — the registration ping "
                       "posts but won't ping.", getattr(channel, "id", "?"))
        noun = "registration" if gained == 1 else "registrations"
        try:
            await channel.send(
                f"@everyone 📝 **{gained} new {noun}!** Now **{total}** registered "
                f"({paid} paid · **{unpaid} awaiting payment**). 👀 Watch for the "
                f"e-Transfer so we can confirm and unfreeze them.",
                allowed_mentions=discord.AllowedMentions(everyone=True))
            log.info("registration ping: +%s (total %s)", gained, total)
        except discord.Forbidden:
            log.warning("registration ping: missing perms in %s", REVIEW_CHANNEL_ID)
        except Exception:  # noqa: BLE001
            log.exception("registration ping failed")
    elif total < _last_total_registered:
        _last_total_registered = total


@registration_announce_loop.before_loop
async def _before_registration() -> None:
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
            await api.call_claim(self.date, name, number,
                                 discord_id=str(interaction.user.id))
            msg = (f"✅ You're on call for **{label}** — number **{number}**.\n"
                   f"I'll **ping you ~1 hour before** your shift (around 4 PM "
                   f"that day).")
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


# Remind whoever's on call ~1 hour before their 5-8 PM shift (i.e. the 4 PM ET
# hour). We PING them with an @mention in a channel — a DM alone silently fails
# if the person has DMs off — and also try a DM as a bonus.
ONCALL_CHANNEL_ID = os.environ.get("ONCALL_CHANNEL_ID") or ""
_last_oncall_reminder = None


def _oncall_channel() -> Optional[discord.abc.Messageable]:
    """Where to post the on-call @mention: ONCALL_CHANNEL_ID, then the staff
    review channel, then the registration channel fallback."""
    for cid in (ONCALL_CHANNEL_ID, REVIEW_CHANNEL_ID):
        if cid:
            ch = bot.get_channel(int(cid))
            if ch is not None:
                return ch
    return _register_channel()


@tasks.loop(minutes=10)
async def oncall_reminder_loop() -> None:
    global _last_oncall_reminder
    if not bot.is_ready():
        return
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        now_e = datetime.now(ZoneInfo("America/Toronto"))
    except Exception:
        return
    if now_e.hour != 16:               # the 4 PM hour — 1 hour before the shift
        return
    today = now_e.strftime("%Y-%m-%d")
    if _last_oncall_reminder == today:
        return
    try:
        data = await api.call_schedule()
    except Exception:  # noqa: BLE001
        log.exception("on-call reminder fetch failed")
        return
    if not data.get("ok"):
        return
    _last_oncall_reminder = today      # mark today's 4 PM hour handled (once/day)
    entry = next((d for d in data.get("days", []) if d.get("date") == today), None)
    if not entry:
        return
    name = entry.get("name") or "the volunteer on call"
    num = entry.get("number") or "your number"
    did = entry.get("discord_id")

    # 1) Reliable: @mention them in a channel so it actually pings.
    channel = _oncall_channel()
    if channel is not None and not _can_post(channel, "the on-call reminder"):
        # _can_post already logged the specific permission that's missing;
        # blank it so we don't also print the misleading "set
        # ONCALL_CHANNEL_ID" advice below. The DM still goes out.
        channel = False
    if channel:
        who = f"<@{did}>" if did else f"**{name}**"
        msg = (f"📞 {who} — **call-window reminder!** You're on call today from "
               f"**5:00–8:00 PM**. Callers may reach **{num}** during that "
               f"window. Thanks for covering it! 🙌")
        try:
            await channel.send(msg, allowed_mentions=discord.AllowedMentions(
                users=True, everyone=False, roles=False))
            log.info("on-call ping posted for %s (%s)", name, today)
        except discord.Forbidden:
            log.warning("on-call ping: missing perms in the on-call channel")
        except Exception:  # noqa: BLE001
            log.exception("on-call channel ping failed")
    elif channel is None:
        log.warning("on-call reminder: no channel to ping in "
                    "(set ONCALL_CHANNEL_ID or REVIEW_CHANNEL_ID)")

    # 2) Bonus: also DM them (best-effort; silently skipped if DMs are off).
    if did:
        try:
            uid = int(did)
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            await user.send(
                "📞 **Call-window reminder** — you're on call today from "
                f"**5:00–8:00 PM**.\nCallers may reach **{num}** during that "
                "window. Thanks for covering it! 🙌")
        except Exception:  # noqa: BLE001
            log.info("on-call DM not delivered (DMs off?) — channel ping covers it")


@oncall_reminder_loop.before_loop
async def _before_oncall_reminder() -> None:
    await bot.wait_until_ready()


# ── Locked-chest interactive UI ──────────────────────────────────────
# Each chest gets posted as a public embed with a button. Clicking the
# button opens a modal asking for the passcode. Submitting the right
# code grants the role. Multi-claim is allowed — the chest message stays
# in the channel so anyone with the code can keep opening it.

async def _deliver_chest(interaction: discord.Interaction, payload: Dict[str, Any]) -> None:
    """Grant the chest's role, report the points payout, and reveal the
    description. Shared by the button and the /unlock command. The
    interaction must already be deferred (ephemeral).

    A description of any length is fine — it's split across as many
    follow-up messages as it takes."""
    guild = interaction.guild
    role_id = payload.get("roleId")
    description = payload.get("description") or "*(no description set)*"
    already = bool(payload.get("alreadyClaimed"))
    role = guild.get_role(int(role_id)) if (guild and role_id) else None
    member = interaction.user if isinstance(interaction.user, discord.Member) else \
             (await guild.fetch_member(interaction.user.id) if guild else None)

    granted = False
    if role and member and role not in member.roles:
        try:
            await member.add_roles(role, reason="HigherGrade chest unlock")
            granted = True
        except discord.Forbidden:
            pass

    # Optional role swap: strip the old role so they don't accumulate both.
    # Only on a first-time open — a repeat open is a no-op like everything
    # else about this chest.
    removed_name: Optional[str] = None
    remove_failed = False
    rm_id = payload.get("removeRoleId")
    if rm_id and member and guild and not already:
        rm_role = guild.get_role(int(rm_id))
        if rm_role and rm_role in member.roles:
            try:
                await member.remove_roles(
                    rm_role, reason="HigherGrade chest unlock — role swap")
                removed_name = rm_role.name
            except discord.Forbidden:
                remove_failed = True

    lines = ["🗝 **Chest opened!**" if not already else "🗝 **You've already opened this chest.**"]
    if granted and role:
        lines.append(f"✅ Role granted: **{role.name}**")
    elif already and role:
        lines.append(f"(You already have **{role.name}**.)")
    elif role and member and role in member.roles:
        lines.append(f"(You already have **{role.name}**.)")
    elif role:
        lines.append("⚠️ Couldn't grant the role — ask an admin to put my role above it "
                     "in Server Settings → Roles.")

    if removed_name:
        lines.append(f"🔄 Removed: **{removed_name}**")
    elif remove_failed:
        lines.append(f"⚠️ Couldn't remove **{payload.get('removeRoleName') or 'the old role'}** "
                     "— ask an admin to put my role above it.")

    awarded = int(payload.get("awarded") or 0)
    points = int(payload.get("points") or 0)
    skipped = payload.get("awardSkipped")
    if awarded > 0:
        lines.append(f"💰 **+{awarded} pts** added to your camp account.")
    elif already and points > 0:
        lines.append("💰 No points this time — a chest only pays out once per person.")
    elif skipped == "unverified":
        lines.append(f"💰 This chest pays **{points} pts**, but your Discord isn't linked "
                     f"to a camp account yet — verify and the next one will pay out.")
    elif skipped == "frozen":
        lines.append(f"💰 This chest pays **{points} pts**, but your camp account is "
                     f"pending payment confirmation, so it couldn't be credited.")

    max_claims = payload.get("maxClaims")
    if max_claims:
        left = max(0, int(max_claims) - int(payload.get("claimedCount") or 0))
        lines.append(f"📦 {left} of {int(max_claims)} opening(s) left.")

    for i, part in enumerate(_chunk("\n".join(lines) + "\n\n" + description, MSG_LIMIT)):
        await interaction.followup.send(part, ephemeral=True)
        if i >= 9:   # sanity stop — 10 follow-ups is already a wall of text
            break


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
        await _deliver_chest(interaction, res.get("data") or {})


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


# Discord's hard caps. Descriptions longer than this get split across
# continuation embeds/messages rather than being cut off.
EMBED_DESC_LIMIT = 4096
MSG_LIMIT = 2000


def _chunk(text: str, size: int) -> List[str]:
    """Split text into <=size pieces, preferring to break at a paragraph or
    line boundary so a long chest description doesn't get cut mid-sentence."""
    text = text or ""
    if len(text) <= size:
        return [text] if text else []
    out: List[str] = []
    while len(text) > size:
        window = text[:size]
        cut = max(window.rfind("\n\n"), window.rfind("\n"))
        if cut < size // 2:      # no sensible break point — hard split
            cut = size
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def _chest_embed(description: str, image_url: Optional[str] = None,
                 role_name: Optional[str] = None,
                 points: int = 0, max_claims: Optional[int] = None,
                 remove_role_name: Optional[str] = None) -> discord.Embed:
    """First (or only) embed of a chest message. Long descriptions are
    carried on by _chest_overflow_embeds."""
    body = _chunk(description, EMBED_DESC_LIMIT)
    e = discord.Embed(
        title="🔒 Locked Chest",
        description=(body[0] if body else "*(no description)*"),
        color=discord.Color.purple(),
    )
    if image_url:
        e.set_image(url=image_url)
    bits = ["Tap the button below and enter the passcode to open."]
    if role_name:
        bits.append(f"Unlocks: {role_name}.")
    if remove_role_name:
        bits.append(f"Replaces: {remove_role_name}.")
    if points > 0:
        bits.append(f"Reward: +{points} pts.")
    if max_claims:
        bits.append(f"Limited to {max_claims} opener(s).")
    else:
        bits.append("Unlimited openers — but only once each.")
    e.set_footer(text=" ".join(bits))
    return e


def _chest_overflow_embeds(description: str) -> List[discord.Embed]:
    """Continuation embeds for descriptions past the first 4096 characters."""
    return [discord.Embed(description=part, color=discord.Color.purple())
            for part in _chunk(description, EMBED_DESC_LIMIT)[1:]]


# ── Verification & onboarding ────────────────────────────────────────
async def _run_verify(interaction: discord.Interaction, email: str,
                      password: str) -> tuple[bool, str]:
    """Link a Discord user to their camp account and grant the roles that
    open up the private channels. Shared by the /verify command and the
    Verify-me button, so both paths behave identically.

    The interaction must already be deferred (ephemeral). Returns
    (succeeded, message-to-show)."""
    guild = interaction.guild
    if not guild:
        return False, "Run this in the server, not a DM."
    res = await api.link(str(interaction.user.id), str(guild.id), email.strip(), password)
    if not res.get("ok"):
        return False, f"❌ {res.get('error') or 'Verification failed.'}"

    summary = res.get("data") or {}
    full_name = (summary.get("fullName") or "").strip()
    student_role = await ensure_role(guild, STUDENT_ROLE_NAME,
                                     color=discord.Color.blurple(), hoist=True)
    member = interaction.user if isinstance(interaction.user, discord.Member) else \
             await guild.fetch_member(interaction.user.id)
    role_warning = ""
    try:
        await member.add_roles(student_role, reason="HigherGrade verify")
    except discord.Forbidden:
        role_warning = "⚠️ Couldn't grant the **Student** role — ask an admin to put my role above it."

    # Also sync any camp-game roles the student already holds.
    blocklist_normalized = await _fetch_blocklist_normalized(guild.id)
    await _sync_member(member, summary, blocklist_normalized)

    # Force a nickname update directly here (even if _sync_member skipped
    # it) so we can give the user a clear pass/fail reason.
    nick_status = ""
    if full_name:
        if member.id == guild.owner_id:
            nick_status = (
                f"\n\nℹ️ Discord doesn't let bots rename the **server owner** — "
                f"please set your nickname to **{full_name}** manually."
            )
        elif member.top_role >= (guild.me.top_role if guild.me else member.top_role):
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
        f"{summary.get('privatePoints', 0)} pts. The camper channels are open to you now — welcome!"
    )
    if role_warning:
        msg += "\n\n" + role_warning
    msg += nick_status
    return True, msg


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
    ok, msg = await _run_verify(interaction, email, password)
    if ok:
        # A modal can't be opened from a modal/command response, so the
        # onboarding questions hang off a follow-up button instead.
        await interaction.followup.send(msg, view=OnboardingPromptView(), ephemeral=True)
    else:
        await interaction.followup.send(msg, ephemeral=True)


class VerifyModal(discord.ui.Modal, title="Verify your camp account"):
    """Collects the camp login. A modal (rather than slash-command options)
    means the password is never rendered as command text in the channel."""

    email = discord.ui.TextInput(
        label="Camp email",
        placeholder="The email you registered with",
        max_length=200, required=True,
    )
    password = discord.ui.TextInput(
        label="Camp password",
        placeholder="Your password on highergradetutoring.ca",
        max_length=200, required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, msg = await _run_verify(interaction, str(self.email.value), str(self.password.value))
        if ok:
            await interaction.followup.send(msg, view=OnboardingPromptView(), ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)


class VerifyPanelView(discord.ui.View):
    """The persistent panel that lives in the public #verify channel. The
    fixed custom_id + timeout=None + bot.add_view() in setup_hook means the
    button keeps working across restarts and redeploys."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify me ✅", style=discord.ButtonStyle.success,
                       custom_id="hg_verify:panel")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(VerifyModal())


# ── Onboarding questions ─────────────────────────────────────────────
# Edit this list to change what new campers are asked. Discord allows at
# most 5 inputs per modal, and each label is capped at 45 characters.
ONBOARD_QUESTIONS: List[Dict[str, Any]] = [
    {"key": "What we should call you", "label": "What should we call you?",
     "placeholder": "Preferred name / nickname", "long": False, "required": True},
    {"key": "Grade this September", "label": "What grade are you going into?",
     "placeholder": "e.g. 10", "long": False, "required": True},
    {"key": "Hoping to get out of camp", "label": "What do you want out of camp?",
     "placeholder": "Contest prep, catching up, meeting people…", "long": True, "required": True},
    {"key": "Things we should know", "label": "Anything we should know?",
     "placeholder": "Allergies, access needs, anything else", "long": True, "required": False},
    {"key": "How they heard about us", "label": "How did you hear about us?",
     "placeholder": "Friend, teacher, Instagram…", "long": False, "required": False},
]


def _onboard_channel(guild: discord.Guild) -> Optional[discord.abc.Messageable]:
    """Where onboarding answers get posted: ONBOARD_CHANNEL_ID, else a
    channel named 'onboarding-answers'/'onboarding'. None if neither exists
    — the answers are then only echoed back to the member."""
    if ONBOARD_CHANNEL_ID:
        ch = bot.get_channel(int(ONBOARD_CHANNEL_ID))
        if ch is not None:
            return ch
        log.warning("ONBOARD_CHANNEL_ID=%s not found — falling back", ONBOARD_CHANNEL_ID)
    return (discord.utils.get(guild.text_channels, name="onboarding-answers")
            or discord.utils.get(guild.text_channels, name="onboarding"))


class OnboardingModal(discord.ui.Modal, title="A few quick questions"):
    """Built dynamically from ONBOARD_QUESTIONS so the questions are one
    edit away, rather than five hard-coded class attributes."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.inputs: List[discord.ui.TextInput] = []
        for q in ONBOARD_QUESTIONS[:5]:
            item = discord.ui.TextInput(
                label=str(q["label"])[:45],
                placeholder=q.get("placeholder") or None,
                style=discord.TextStyle.paragraph if q.get("long") else discord.TextStyle.short,
                required=bool(q.get("required")),
                max_length=1000 if q.get("long") else 200,
            )
            self.inputs.append(item)
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("Run this in the server, not a DM.", ephemeral=True)
            return

        # Pull the linked camp profile so staff see who the answers belong
        # to even if the member later changes their Discord nickname.
        camp_name = ""
        try:
            me = await api.me(str(interaction.user.id))
            camp_name = ((me.get("data") or {}).get("fullName") or "").strip()
        except Exception:  # noqa: BLE001
            log.exception("onboarding: /me lookup failed for %s", interaction.user.id)

        embed = discord.Embed(
            title="📝 Onboarding answers",
            description=f"{interaction.user.mention} · `{interaction.user}`"
                        + (f"\nCamp account: **{camp_name}**" if camp_name else ""),
            colour=0x22C55E,
        )
        for q, item in zip(ONBOARD_QUESTIONS, self.inputs):
            value = (str(item.value) or "").strip()
            embed.add_field(name=str(q["key"])[:256], value=(value or "*(skipped)*")[:1024],
                            inline=False)
        embed.set_footer(text=f"user id {interaction.user.id}")

        channel = _onboard_channel(guild)
        if channel is None:
            await interaction.followup.send(
                "✅ Thanks! (Heads up for staff: no onboarding channel is configured, "
                "so these answers weren't filed anywhere — set `ONBOARD_CHANNEL_ID`.)",
                ephemeral=True,
            )
            return
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(
                "✅ Thanks! (I couldn't post these to the staff channel — "
                "ask an admin to give me permission to post there.)",
                ephemeral=True,
            )
            return
        await interaction.followup.send("✅ Thanks — that's you all set up!", ephemeral=True)


class OnboardingPromptView(discord.ui.View):
    """Follow-up button shown right after a successful verify. Persistent so
    the button still works if the member comes back to it later."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Answer a few questions 📝", style=discord.ButtonStyle.primary,
                       custom_id="hg_onboard:start")
    async def onboard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(OnboardingModal())


@bot.tree.command(name="onboard",
                  description="Answer (or redo) the onboarding questions.")
async def cmd_onboard(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("Run this in the server, not a DM.", ephemeral=True)
        return
    await interaction.response.send_modal(OnboardingModal())


# ── Staff point awards ───────────────────────────────────────────────
@bot.tree.command(name="award", description="Give (or take) points from a camper, with a reason.")
@app_commands.describe(
    student="The camper — they must have verified their camp account",
    points="How many points. Use a negative number to take points away.",
    reason="Brief note on why. This shows on the transaction record.",
)
async def cmd_award(interaction: discord.Interaction, student: discord.Member,
                    points: int, reason: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not interaction.guild:
        await interaction.followup.send("Run this in the server.", ephemeral=True)
        return
    if not await _can_award(interaction):
        await interaction.followup.send(
            "🚫 Only staff can award points. Ask an admin for the **Staff** role, "
            "or to grant your role `award-points` with `/perms-grant`.",
            ephemeral=True)
        return
    if not reason.strip():
        await interaction.followup.send(
            "❌ Give a brief reason — it goes on the camper's transaction record.",
            ephemeral=True)
        return

    giver = _member_of(interaction)
    res = await api.award(str(student.id), points, reason.strip(),
                          giver.display_name if giver else str(interaction.user))
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Could not award points.'}",
                                        ephemeral=True)
        return
    data = res.get("data") or {}
    applied = int(data.get("applied") or 0)
    verb = "Gave" if applied >= 0 else "Took"
    prep = "to" if applied >= 0 else "from"

    # Tell the camper themselves — a point change with no explanation is
    # the thing people complain about.
    dm_note = ""
    try:
        e = discord.Embed(
            title=("🎉 You earned points!" if applied >= 0 else "📉 Points removed"),
            description=f"**{'+' if applied >= 0 else '−'}{abs(applied)} points**",
            colour=0x22C55E if applied >= 0 else 0xEF4444,
        )
        e.add_field(name="Why", value=reason.strip()[:1024], inline=False)
        e.add_field(name="Balance", value=f"{data.get('newPrivatePoints', 0)} pts", inline=True)
        e.set_footer(text=f"From {data.get('awardedBy') or 'HigherGrade staff'}")
        await student.send(embed=e)
    except (discord.Forbidden, discord.HTTPException):
        dm_note = "\n⚠️ Couldn't DM them (their DMs are closed) — tell them in person."

    msg = (f"✅ {verb} **{abs(applied)} pts** {prep} **{data.get('studentName') or student.display_name}**."
           f"\n• Reason: {reason.strip()}"
           f"\n• New balance: **{data.get('newPrivatePoints', 0)} pts**")
    if applied != int(data.get("requested") or 0):
        msg += (f"\n• They only had {abs(applied)} pts to take, so that's all that came off "
                f"— nobody goes negative.")
    if data.get("frozen"):
        msg += "\n• ℹ️ Their camp account is still pending payment confirmation."
    msg += dm_note
    await interaction.followup.send(msg, ephemeral=True)


# ── Class participation (base stats) ─────────────────────────────────
HAND_RAISED_STAT_ID = os.environ.get("HAND_RAISED_STAT_ID") or "hand_raised"


@bot.tree.command(name="handraise",
                  description="Credit a camper for raising their hand (2 pts each).")
@app_commands.describe(
    student="The camper — they must have verified their camp account",
    times="How many hand-raises to credit. Negative takes them back.",
)
async def cmd_handraise(interaction: discord.Interaction, student: discord.Member,
                        times: int = 1) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not interaction.guild:
        await interaction.followup.send("Run this in the server.", ephemeral=True)
        return
    if not await _can_award(interaction):
        await interaction.followup.send(
            "🚫 Only staff can credit participation. Ask an admin for the **Staff** "
            "role, or to grant your role `award-points` with `/perms-grant`.",
            ephemeral=True)
        return
    if times == 0:
        await interaction.followup.send("❌ That would do nothing — use a non-zero number.",
                                        ephemeral=True)
        return

    res = await api.base_stat_bump(str(student.id), HAND_RAISED_STAT_ID, times)
    if not res.get("ok"):
        err = res.get("error") or "Could not credit that."
        if "not found" in err.lower():
            err += (f"\n(The `{HAND_RAISED_STAT_ID}` stat is missing — recreate it on the "
                    f"**Base Stats** admin page, or set `HAND_RAISED_STAT_ID`.)")
        await interaction.followup.send(f"❌ {err}", ephemeral=True)
        return
    d = res.get("data") or {}
    pd = int(d.get("pointDelta") or 0)
    name = d.get("studentName") or student.display_name
    await interaction.followup.send(
        f"✋ **{name}** — {d.get('statName') or 'Hand Raised'} "
        f"{int(d.get('appliedDelta') or 0):+d} (now {d.get('newCount')}).\n"
        f"• {'+' if pd >= 0 else '−'}{abs(pd)} pts · balance **{d.get('newPrivatePoints', 0)} pts**",
        ephemeral=True,
    )


# ── Attendance ───────────────────────────────────────────────────────
@bot.tree.command(name="attendance", description="Mark a camper present, late, or absent.")
@app_commands.describe(
    student="The camper — they must have verified their camp account",
    status="Present (+250 pts), Late (−50 pts), or Absent (no change)",
    date="Defaults to today. Format YYYY-MM-DD.",
)
@app_commands.choices(status=[
    app_commands.Choice(name="Present  (+250 pts)", value="present"),
    app_commands.Choice(name="Late  (−50 pts)",     value="late"),
    app_commands.Choice(name="Absent  (no points)", value="absent"),
])
async def cmd_attendance(interaction: discord.Interaction, student: discord.Member,
                         status: app_commands.Choice[str],
                         date: Optional[str] = None) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not interaction.guild:
        await interaction.followup.send("Run this in the server.", ephemeral=True)
        return
    if not await _can_attend(interaction):
        await interaction.followup.send(
            "🚫 Only staff can take attendance. Ask an admin for the **Staff** role, "
            "or to grant your role `attendance` with `/perms-grant`.", ephemeral=True)
        return

    marker = _member_of(interaction)
    res = await api.attendance_mark(
        str(student.id), status.value, (date or "").strip(),
        marker.display_name if marker else str(interaction.user))
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Could not mark attendance.'}",
                                        ephemeral=True)
        return
    d = res.get("data") or {}
    name = d.get("studentName") or student.display_name

    if d.get("unchanged"):
        await interaction.followup.send(
            f"➖ **{name}** was already marked **{status.value}** for {d.get('date')} — "
            f"nothing changed, no points moved.", ephemeral=True)
        return

    applied = int(d.get("pointsApplied") or 0)
    bits = [f"✅ **{name}** marked **{status.value}** for {d.get('date')}."]
    if d.get("previousStatus"):
        bits.append(f"• Changed from **{d['previousStatus']}** — "
                    f"the earlier {abs(int(d.get('reversed') or 0))} pts were reversed first.")
    if applied:
        bits.append(f"• {'+' if applied > 0 else '−'}{abs(applied)} pts")
    else:
        bits.append("• No points moved" +
                    (" (they had none left to deduct)" if status.value == "late" else ""))
    bits.append(f"• Balance: **{d.get('newPrivatePoints', 0)} pts**")

    # Let the camper know, same as with awards.
    try:
        colour = {"present": 0x22C55E, "late": 0xF59E0B, "absent": 0x94A3B8}[status.value]
        e = discord.Embed(title=f"📋 Attendance — {d.get('date')}",
                          description=f"You were marked **{status.value}**.", colour=colour)
        if applied:
            e.add_field(name="Points", value=f"{'+' if applied > 0 else '−'}{abs(applied)}",
                        inline=True)
        e.add_field(name="Balance", value=f"{d.get('newPrivatePoints', 0)} pts", inline=True)
        await student.send(embed=e)
    except (discord.Forbidden, discord.HTTPException):
        bits.append("⚠️ Couldn't DM them — their DMs are closed.")

    await interaction.followup.send("\n".join(bits), ephemeral=True)


@bot.tree.command(name="attendance-today", description="Show who's been marked today.")
@app_commands.describe(date="Defaults to today. Format YYYY-MM-DD.")
async def cmd_attendance_today(interaction: discord.Interaction,
                               date: Optional[str] = None) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not await _can_attend(interaction):
        await interaction.followup.send("🚫 Staff only.", ephemeral=True)
        return
    res = await api.attendance_list((date or "").strip())
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
        return
    rows = res.get("data") or []
    day = res.get("date")
    if not rows:
        await interaction.followup.send(
            f"Nobody marked for **{day}** yet.", ephemeral=True)
        return
    icon = {"present": "✅", "late": "🟡", "absent": "⬜"}
    tally = {"present": 0, "late": 0, "absent": 0}
    lines = []
    for r in rows:
        tally[r["status"]] = tally.get(r["status"], 0) + 1
        pts = int(r.get("pointsApplied") or 0)
        lines.append(f"{icon.get(r['status'], '•')} **{r.get('studentName') or '?'}** "
                     f"— {r['status']}"
                     + (f" ({'+' if pts > 0 else '−'}{abs(pts)} pts)" if pts else ""))
    head = (f"**Attendance for {day}** — ✅ {tally['present']} present · "
            f"🟡 {tally['late']} late · ⬜ {tally['absent']} absent")
    await interaction.followup.send((head + "\n" + "\n".join(lines))[:1900], ephemeral=True)


# ── Homework hand-in & marking ───────────────────────────────────────
def _marking_channel(guild: discord.Guild) -> Optional[discord.abc.Messageable]:
    """Where handed-in work goes: MARKING_CHANNEL_ID, else a channel named
    'marking-discussions'/'marking'."""
    if MARKING_CHANNEL_ID:
        ch = bot.get_channel(int(MARKING_CHANNEL_ID))
        if ch is not None:
            return ch
        log.warning("MARKING_CHANNEL_ID=%s not found — falling back", MARKING_CHANNEL_ID)
    return (discord.utils.get(guild.text_channels, name="marking-discussions")
            or discord.utils.get(guild.text_channels, name="marking"))


async def _can_staff(interaction: discord.Interaction, command: str) -> bool:
    """Staff-tier gate: server admins, anyone holding Staff, or a role
    granted this specific command via /perms-grant."""
    if _is_server_admin(interaction):
        return True
    member = _member_of(interaction)
    if member and any(r.name == STAFF_ROLE_NAME for r in member.roles):
        return True
    return await _user_can_run(interaction, command)


async def _can_mark(interaction: discord.Interaction) -> bool:
    return await _can_staff(interaction, "mark-homework")


async def _can_award(interaction: discord.Interaction) -> bool:
    return await _can_staff(interaction, "award-points")


async def _can_attend(interaction: discord.Interaction) -> bool:
    return await _can_staff(interaction, "attendance")


def _submission_embed(data: Dict[str, Any], *, attachment_names: List[str]) -> discord.Embed:
    """The card staff see in the marking channel."""
    submitted = int(data.get("submittedAt") or time.time())
    e = discord.Embed(
        title=f"📥 {data.get('title') or 'Homework submission'}",
        colour=0x3B82F6,
    )
    e.add_field(name="Student",
                value=f"**{data.get('studentName') or 'Unknown'}**\n<@{data.get('discordId')}>",
                inline=True)
    # Both an absolute date and a relative one, each rendered in the
    # reader's own timezone by Discord.
    e.add_field(name="Handed in",
                value=f"<t:{submitted}:f>\n<t:{submitted}:R>", inline=True)
    if data.get("notes"):
        e.add_field(name="Student's note", value=str(data["notes"])[:1024], inline=False)
    if attachment_names:
        e.add_field(name="Files", value="\n".join(f"• {n}" for n in attachment_names)[:1024],
                    inline=False)
    e.set_footer(text=f"Submission {data.get('id')} · press Mark to send feedback")
    return e


def _marked_embed(base: discord.Embed, marked: Dict[str, Any], dm_ok: bool,
                  fallback_channel: Optional[int] = None) -> discord.Embed:
    """Re-render a submission card once it's been marked. Rebuilt from the
    original so re-marking replaces the result instead of stacking fields."""
    e = discord.Embed(title=base.title, colour=0x22C55E)
    for f in base.fields:
        if f.name in ("Grade", "Feedback", "Marked by"):
            continue
        e.add_field(name=f.name, value=f.value, inline=f.inline)
    if marked.get("grade"):
        e.add_field(name="Grade", value=str(marked["grade"])[:1024], inline=True)
    if dm_ok:
        delivery = "✅ DM delivered"
    elif fallback_channel:
        delivery = f"📢 DMs closed — posted in <#{fallback_channel}>"
    else:
        delivery = "⚠️ **Not delivered** — pass it on manually"
    e.add_field(name="Marked by",
                value=f"{marked.get('markedByName') or 'staff'}\n{delivery}",
                inline=True)
    e.add_field(name="Feedback", value=str(marked.get("feedback") or "")[:1024], inline=False)
    e.set_footer(text=base.footer.text or "")
    return e


async def _post_feedback_fallback(data: Dict[str, Any]) -> Optional[int]:
    """When a camper's DMs are shut, ping them with their feedback in the
    channel they ran /submit in. Returns the channel id used, or None.

    Note this makes the grade and feedback visible to everyone who can see
    that channel — it's the trade for reaching a camper who can't be DMed.
    """
    cid = data.get("originChannelId")
    if not cid:
        return None
    try:
        channel = bot.get_channel(int(cid)) or await bot.fetch_channel(int(cid))
    except (discord.NotFound, discord.Forbidden, ValueError, TypeError):
        return None
    except Exception:  # noqa: BLE001
        log.exception("couldn't resolve origin channel %s", cid)
        return None
    if channel is None or not _can_post(channel, "feedback fallback", embeds=True):
        return None

    e = discord.Embed(
        title="📝 Your homework has been marked",
        description=f"**{data.get('title') or 'Your submission'}**",
        colour=0x22C55E,
    )
    if data.get("grade"):
        e.add_field(name="Grade", value=str(data["grade"])[:1024], inline=False)
    e.add_field(name="Feedback", value=str(data.get("feedback") or "")[:1024], inline=False)
    e.set_footer(text=f"Marked by {data.get('markedByName') or 'HigherGrade staff'} · "
                      f"posted here because your DMs are closed")
    try:
        await channel.send(
            content=f"<@{data.get('discordId')}> — I couldn't DM you, so here it is:",
            embed=e,
            allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False),
        )
        rest = str(data.get("feedback") or "")[1024:]
        for part in _chunk(rest, MSG_LIMIT):
            await channel.send(part)
        return int(cid)
    except (discord.Forbidden, discord.HTTPException):
        log.info("feedback fallback post failed in channel %s", cid)
        return None


async def _dm_feedback(data: Dict[str, Any]) -> bool:
    """Send the student their feedback. Returns whether it landed."""
    try:
        uid = int(data.get("discordId"))
    except (TypeError, ValueError):
        return False
    e = discord.Embed(
        title="📝 Your homework has been marked",
        description=f"**{data.get('title') or 'Your submission'}**",
        colour=0x22C55E,
    )
    if data.get("grade"):
        e.add_field(name="Grade", value=str(data["grade"])[:1024], inline=False)
    e.add_field(name="Feedback", value=str(data.get("feedback") or "")[:1024], inline=False)
    e.set_footer(text=f"Marked by {data.get('markedByName') or 'HigherGrade staff'}")
    try:
        user = bot.get_user(uid) or await bot.fetch_user(uid)
        await user.send(embed=e)
        # Feedback longer than one embed field continues as plain messages.
        rest = str(data.get("feedback") or "")[1024:]
        for part in _chunk(rest, MSG_LIMIT):
            await user.send(part)
        return True
    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
        log.info("feedback DM not delivered to %s (DMs closed?)", uid)
        return False
    except Exception:  # noqa: BLE001
        log.exception("feedback DM failed for %s", uid)
        return False


class MarkModal(discord.ui.Modal, title="Mark this submission"):
    grade = discord.ui.TextInput(
        label="Grade / mark (optional)",
        placeholder="e.g. 8/10, B+, Complete",
        max_length=60, required=False,
    )
    feedback = discord.ui.TextInput(
        label="Feedback for the student",
        style=discord.TextStyle.paragraph,
        placeholder="This is DMed to them word for word.",
        max_length=4000, required=True,
    )

    def __init__(self, hid: str, source: Optional[discord.Message]) -> None:
        super().__init__()
        self.hid = hid
        self.source = source

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await _can_mark(interaction):
            await interaction.followup.send(
                "🚫 Only staff can mark work. Ask an admin for the **Staff** role, "
                "or to grant your role `mark-homework` with `/perms-grant`.",
                ephemeral=True)
            return
        marker = _member_of(interaction)
        res = await api.homework_mark(
            self.hid, str(self.grade.value or "").strip(),
            str(self.feedback.value).strip(), str(interaction.user.id),
            (marker.display_name if marker else str(interaction.user)),
        )
        if not res.get("ok"):
            await interaction.followup.send(f"❌ {res.get('error') or 'Could not save.'}",
                                            ephemeral=True)
            return
        data = res.get("data") or {}

        dm_ok = await _dm_feedback(data)
        fallback_channel: Optional[int] = None
        if not dm_ok:
            # DMs shut — reach them where they handed the work in instead.
            fallback_channel = await _post_feedback_fallback(data)
        try:
            # Delivered either way counts as delivered for the record.
            await api.homework_dm(self.hid, dm_ok or fallback_channel is not None)
        except Exception:  # noqa: BLE001
            log.exception("could not record DM status for %s", self.hid)

        # Re-render the card in the marking channel so the thread of record
        # shows the result without anyone having to re-open the modal.
        if self.source and self.source.embeds:
            try:
                await self.source.edit(
                    embed=_marked_embed(self.source.embeds[0], data, dm_ok,
                                        fallback_channel=fallback_channel),
                    view=_mark_view(self.hid))
            except (discord.Forbidden, discord.HTTPException):
                log.info("couldn't update submission message for %s", self.hid)

        who = data.get("studentName") or "the student"
        if dm_ok:
            await interaction.followup.send(
                f"✅ Marked. **{who}** has been DMed your feedback.", ephemeral=True)
        elif fallback_channel:
            await interaction.followup.send(
                f"✅ Marked. **{who}**'s DMs are closed, so I posted the feedback in "
                f"<#{fallback_channel}> and pinged them there — note that anyone who "
                f"can see that channel can read it.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"✅ Marked and saved — but I couldn't reach **{who}**: their DMs are "
                f"closed and I couldn't post in the channel they submitted from. "
                f"You'll need to pass the feedback on another way.", ephemeral=True)


class MarkButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"hw_mark:(?P<hid>[A-Za-z0-9_\-]+)",
):
    """Persistent per-submission button — the submission id lives in the
    custom_id, so old cards keep working after a redeploy."""

    def __init__(self, hid: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Mark & send feedback 📝",
                style=discord.ButtonStyle.success,
                custom_id=f"hw_mark:{hid}",
            )
        )
        self.hid = hid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: discord.ui.Button, match):
        return cls(match["hid"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _can_mark(interaction):
            await interaction.response.send_message(
                "🚫 Only staff can mark work.", ephemeral=True)
            return
        await interaction.response.send_modal(MarkModal(self.hid, interaction.message))


def _mark_view(hid: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(MarkButton(hid))
    return view


@bot.tree.command(name="submit", description="Hand in homework for marking.")
@app_commands.describe(
    title="What this is — e.g. 'Day 3 problem set'",
    file="Your work: photo, PDF, whatever",
    file2="Another file (optional)",
    file3="Another file (optional)",
    notes="Anything you want the marker to know (optional)",
)
async def cmd_submit(
    interaction: discord.Interaction,
    title: str,
    file: discord.Attachment,
    file2: Optional[discord.Attachment] = None,
    file3: Optional[discord.Attachment] = None,
    notes: Optional[str] = None,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not interaction.guild:
        await interaction.followup.send("Hand work in from the server, not a DM.",
                                        ephemeral=True)
        return

    attachments = [a for a in (file, file2, file3) if a is not None]
    too_big = [a.filename for a in attachments if a.size > MAX_UPLOAD_BYTES]
    if too_big:
        await interaction.followup.send(
            f"❌ Too large to re-upload (limit {MAX_UPLOAD_BYTES // (1024*1024)} MB): "
            + ", ".join(f"`{n}`" for n in too_big), ephemeral=True)
        return

    channel = _marking_channel(interaction.guild)
    if channel is None:
        await interaction.followup.send(
            "❌ There's no marking channel set up yet — ask an admin to create "
            "**#marking-discussions** or set `MARKING_CHANNEL_ID`.", ephemeral=True)
        return
    if not _can_post(channel, "a homework submission", embeds=True):
        await interaction.followup.send(
            "❌ I can't post in the marking channel — ask an admin to give me "
            "Send Messages, Embed Links, and Attach Files there.", ephemeral=True)
        return

    res = await api.homework_create(
        str(interaction.guild.id), str(interaction.user.id),
        title.strip(), (notes or "").strip(),
        [{"name": a.filename, "size": a.size} for a in attachments],
        origin_channel_id=str(interaction.channel_id or ""),
    )
    if not res.get("ok"):
        await interaction.followup.send(
            f"❌ {res.get('error') or 'Could not record your submission.'}", ephemeral=True)
        return
    data = dict(res.get("data") or {})
    hid = data.get("id")
    data.setdefault("title", title.strip())
    data.setdefault("notes", (notes or "").strip())
    data["discordId"] = str(interaction.user.id)

    # Re-upload the files onto the marking-channel message. Discord's own
    # attachment URLs are short-lived signed links, so pointing at them
    # would leave staff with dead links a day later.
    files: List[discord.File] = []
    for a in attachments:
        try:
            files.append(discord.File(io.BytesIO(await a.read()), filename=a.filename))
        except Exception:  # noqa: BLE001
            log.exception("could not re-upload %s", a.filename)

    embed = _submission_embed(data, attachment_names=[a.filename for a in attachments])
    try:
        posted = await channel.send(embed=embed, files=files, view=_mark_view(hid))
    except discord.HTTPException as exc:
        log.exception("submission post failed")
        await interaction.followup.send(
            f"❌ Couldn't post your work to the marking channel ({exc.status}). "
            "Nothing was lost — try again, or tell a staff member.", ephemeral=True)
        return

    try:
        await api.homework_set_message(hid, str(posted.channel.id), str(posted.id))
    except Exception:  # noqa: BLE001
        log.exception("could not save submission message id")

    await interaction.followup.send(
        f"✅ Handed in **{title.strip()}** with {len(attachments)} file(s).\n"
        f"Your work is with the markers now — you'll get a DM with feedback when "
        f"it's been looked at. Make sure your DMs are open for this server.",
        ephemeral=True,
    )


@bot.tree.command(name="submissions", description="List homework waiting to be marked.")
@app_commands.describe(show="Which submissions to list")
@app_commands.choices(show=[
    app_commands.Choice(name="Waiting to be marked", value="pending"),
    app_commands.Choice(name="Already marked", value="marked"),
    app_commands.Choice(name="Everything", value=""),
])
async def cmd_submissions(interaction: discord.Interaction,
                          show: Optional[app_commands.Choice[str]] = None) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not await _can_mark(interaction):
        await interaction.followup.send("🚫 Staff only.", ephemeral=True)
        return
    status = show.value if show else "pending"
    res = await api.homework_list(str(interaction.guild.id), status)
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
        return
    rows = res.get("data") or []
    if not rows:
        await interaction.followup.send(
            "Nothing here — everything's marked. 🎉" if status == "pending"
            else "No submissions match that.", ephemeral=True)
        return
    lines = []
    for r in rows[:25]:
        link = ""
        if r.get("channelId") and r.get("messageId"):
            link = (f" · [jump](https://discord.com/channels/"
                    f"{interaction.guild.id}/{r['channelId']}/{r['messageId']})")
        tick = "✅" if r.get("status") == "marked" else "🕒"
        lines.append(f"{tick} **{r.get('studentName') or '?'}** — {r.get('title') or 'untitled'} "
                     f"· <t:{int(r.get('submittedAt') or 0)}:R>{link}")
    await interaction.followup.send(
        f"**{len(rows)} submission(s)**\n" + "\n".join(lines)[:1800], ephemeral=True)


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


@bot.tree.command(name="help", description="List the commands you can use and what they do.")
async def cmd_help(interaction: discord.Interaction) -> None:
    """Role-aware command list: everyone sees the public commands; staff/admins
    additionally see the campaign and server-admin tiers they're allowed to run.
    The tiers below mirror the actual permission checks on each command."""
    import campaign  # already loaded via setup(); gives the queue tier + _is_admin

    def fmt(items):
        return "\n".join(f"`/{name}` — {desc}" for name, desc in items)

    everyone = [
        ("help", "Show this list of commands."),
        ("enrolled", "How many campers are currently enrolled."),
        ("campers", "Camper breakdown: total, paid, and registered-not-paid."),
        ("oncall", "See the call-window schedule and sign yourself up."),
        ("verify", "Link your Discord account to your camp account."),
        ("whoami", "Show your linked camp profile."),
        ("unlink", "Remove the link between your Discord and camp account."),
        ("unlock", "Open a locked chest with its passcode."),
        ("onboard", "Answer (or redo) the onboarding questions."),
        ("submit", "Hand in homework for marking."),
    ]
    staff_tools = [
        ("submissions", "List homework waiting to be marked."),
        ("award", "Give or take points from a camper, with a reason."),
        ("handraise", "Credit a camper for raising their hand (2 pts each)."),
        ("attendance", "Mark a camper present, late, or absent."),
        ("attendance-today", "Show who's been marked today."),
    ]
    chest_tools = [
        ("chest-create", "Place a locked chest in this channel."),
        ("chest-list", "List every chest in this server."),
        ("chest-delete", "Remove a chest by id."),
    ]
    campaign_cmds = [
        ("queue-start", "Start the sponsor email queue."),
        ("queue-pause", "Pause all sending."),
        ("queue-resume", "Resume sending."),
        ("queue-stop", "Stop the queue for this run."),
        ("queue-status", "Show the email queue status."),
        ("queue-next", "Preview, edit, or skip the upcoming outreach emails."),
    ]
    server_admin = [
        ("setup-verify", "Post the Verify panel in this channel."),
        ("gate", "Lock a channel or category to verified members."),
        ("perms-grant", "Allow a role to run a restricted command."),
        ("perms-revoke", "Remove a role's access to a restricted command."),
        ("perms-list", "Show which roles can run which commands."),
        ("perms-lock", "Pin a command's parameter to a fixed value for a role."),
        ("perms-unlock", "Remove a parameter lock from a role."),
        ("perms-locks", "Show every parameter lock in this server."),
        ("role-mirror-block", "Stop a Discord role from mirroring to the camp website."),
        ("role-mirror-unblock", "Allow a Discord role to mirror to the website again."),
        ("role-mirror-list", "Show every Discord role currently blocked from mirroring."),
    ]

    embed = discord.Embed(title="📖 HigherGrade Bot — commands you can use",
                          colour=0x5865F2)
    embed.add_field(name="Everyone", value=fmt(everyone), inline=False)

    # Chest tools — Administrators always; everyone else only for roles that were
    # opened up via /perms-grant (same rule as the commands' own _user_can_run).
    is_admin = _is_server_admin(interaction)
    visible_chest = []
    if is_admin:
        visible_chest = chest_tools
    elif interaction.guild:
        res = await api.perms_list(str(interaction.guild.id))
        if res.get("ok"):
            user_roles = {str(r.id) for r in getattr(interaction.user, "roles", [])}
            for name, desc in chest_tools:
                allowed = {p["roleId"] for p in (res.get("data") or [])
                           if p.get("command") == name}
                if allowed & user_roles:
                    visible_chest.append((name, desc))
    if visible_chest:
        embed.add_field(name="🔐 Chest tools", value=fmt(visible_chest), inline=False)

    # Marking — Staff role, admins, or a role granted `mark-homework`.
    if await _can_mark(interaction):
        embed.add_field(name="📝 Marking · Staff", value=fmt(staff_tools), inline=False)

    # Email campaign — needs Manage Server (or the configured control role).
    if campaign._is_admin(interaction):
        embed.add_field(name="📧 Email campaign · needs Manage Server",
                        value=fmt(campaign_cmds), inline=False)

    # Server-admin meta commands — Administrator / server owner only.
    if is_admin:
        embed.add_field(name="🛠️ Server admin · needs Administrator",
                        value=fmt(server_admin), inline=False)

    embed.set_footer(text="You only see commands your roles let you run — "
                          "ask a server admin if you need more access.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
    await _deliver_chest(interaction, res.get("data") or {})


# ── Admin commands ───────────────────────────────────────────────────
# Commands whose access can be opened up to specific roles via
# /perms-grant. Anything not in this list is implicitly admin/owner-only
# (or open, depending on the command).
RESTRICTABLE_COMMANDS: List[str] = ["chest-create", "chest-list", "chest-delete",
                                    "mark-homework", "award-points", "attendance"]


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


# ── Per-role parameter locks ─────────────────────────────────────────
# A lock pins one parameter of one command to a fixed value for holders of
# a role: they can still run the command, but that field is decided for
# them.
#
# Lockable fields are DISCOVERED from the live command tree rather than
# curated, so every command and every one of its parameters can be locked
# and a renamed parameter can't leave a stale entry behind.
#
# Fields collected in a modal rather than as a slash option are invisible
# to the tree, so they're declared here and enforced by the command itself.
EXTRA_LOCKABLE_FIELDS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "chest-create": {
        "points":    {"kind": "int",     "label": "Points awarded on unlock"},
        "maxClaims": {"kind": "int_opt", "label": "Max openers ('unlimited' for no cap)"},
    },
}

# discord option type -> how we validate and rebuild the value
_OPTION_KINDS: Dict[Any, str] = {
    discord.AppCommandOptionType.string:      "str",
    discord.AppCommandOptionType.integer:     "int",
    discord.AppCommandOptionType.number:      "float",
    discord.AppCommandOptionType.boolean:     "bool",
    discord.AppCommandOptionType.role:        "role",
    discord.AppCommandOptionType.channel:     "channel",
    discord.AppCommandOptionType.user:        "user",
    discord.AppCommandOptionType.mentionable: "user",
    discord.AppCommandOptionType.attachment:  "attachment",
}


def _lockable_for(command: str) -> Dict[str, Dict[str, Any]]:
    """Every lockable field of a command: its real slash parameters, read
    off the command tree, plus any modal-only fields declared above."""
    fields: Dict[str, Dict[str, Any]] = {}
    for cmd in bot.tree.walk_commands():
        if not isinstance(cmd, app_commands.Command) or cmd.qualified_name != command:
            continue
        for p in cmd.parameters:
            kind = _OPTION_KINDS.get(p.type, "str")
            spec: Dict[str, Any] = {"kind": kind, "label": (p.description or p.name)[:60]}
            if p.choices:
                spec["kind"] = "choice"
                spec["choices"] = [str(c.value) for c in p.choices]
            fields[p.name] = spec
        break
    for name, spec in EXTRA_LOCKABLE_FIELDS.get(command, {}).items():
        fields.setdefault(name, spec)
    return fields


def _all_lockable_commands() -> List[str]:
    names = {cmd.qualified_name for cmd in bot.tree.walk_commands()
             if isinstance(cmd, app_commands.Command)}
    names.update(EXTRA_LOCKABLE_FIELDS)
    return sorted(names)

# Last-known locks per guild. Refreshed by the sync loop and written
# through on every /perms-lock, so a modal can be pre-filled without
# paying for a round trip. Authoritative checks always re-fetch.
_LOCK_CACHE: Dict[str, List[Dict[str, Any]]] = {}


async def _refresh_locks(guild_id: Any) -> List[Dict[str, Any]]:
    try:
        res = await api.locks_list(str(guild_id))
        if res.get("ok"):
            _LOCK_CACHE[str(guild_id)] = res.get("data") or []
    except Exception:  # noqa: BLE001
        log.exception("lock fetch failed for guild %s", guild_id)
    return _LOCK_CACHE.get(str(guild_id), [])


def _resolve_locks(member: Optional[discord.Member], command: str,
                   locks: List[Dict[str, Any]]) -> Dict[str, str]:
    """field -> locked value for this member. When several of the member's
    roles lock the same field, the highest role wins — same intuition as
    Discord's own role hierarchy."""
    if member is None:
        return {}
    held = {str(r.id): r for r in member.roles}
    best: Dict[str, Any] = {}   # field -> (role position, value)
    for row in locks:
        if row.get("command") != command:
            continue
        role = held.get(str(row.get("roleId")))
        if role is None:
            continue
        field = row.get("field")
        if field not in best or role.position > best[field][0]:
            best[field] = (role.position, row.get("value"))
    return {f: v for f, (_pos, v) in best.items()}


def _member_of(interaction: discord.Interaction) -> Optional[discord.Member]:
    return interaction.user if isinstance(interaction.user, discord.Member) else None


async def _locks_for(interaction: discord.Interaction, command: str) -> Dict[str, str]:
    """Authoritative lock lookup — re-fetches before deciding. Server owners
    and Administrators are exempt: they're the only ones who can set locks,
    so subjecting them to their own would just be a trap."""
    if not interaction.guild or _is_server_admin(interaction):
        return {}
    locks = await _refresh_locks(interaction.guild.id)
    return _resolve_locks(_member_of(interaction), command, locks)


def _locks_cached(interaction: discord.Interaction, command: str) -> Dict[str, str]:
    """Zero-latency read of the last known locks. Used only to pre-fill a
    modal, where a network round trip would blow the 3-second interaction
    budget. Never trusted for enforcement."""
    if not interaction.guild or _is_server_admin(interaction):
        return {}
    return _resolve_locks(_member_of(interaction), command,
                          _LOCK_CACHE.get(str(interaction.guild.id), []))


def _validate_lock_value(guild: discord.Guild, command: str, field: str,
                         raw: str) -> tuple[bool, str, str]:
    """Check a proposed lock value against the field's kind.
    Returns (ok, stored_value, human_readable)."""
    spec = _lockable_for(command).get(field)
    if spec is None:
        known = ", ".join(f"`{f}`" for f in _lockable_for(command)) or "(none)"
        return False, "", (f"`/{command}` has no parameter called `{field}`. "
                           f"Its parameters: {known}")
    kind = spec["kind"]
    raw = (raw or "").strip()

    if kind == "attachment":
        return False, "", (f"`{field}` is a file upload — there's no fixed value to "
                           f"pin it to, so it can't be locked.")

    if kind == "str":
        return True, raw, raw or "(empty)"

    if kind == "float":
        try:
            n = float(raw)
        except ValueError:
            return False, "", f"`{field}` needs a number — got `{raw}`."
        return True, str(n), str(n)

    if kind == "channel":
        cid = raw.strip("<#>")
        ch = guild.get_channel(int(cid)) if cid.isdigit() else None
        if ch is None:
            ch = discord.utils.get(guild.channels, name=raw.lstrip("#"))
        if ch is None:
            return False, "", f"No channel matches `{raw}` — paste its ID or exact name."
        return True, str(ch.id), f"#{ch.name}"

    if kind == "user":
        uid = raw.strip("<@!>")
        if not uid.isdigit():
            return False, "", f"`{field}` needs a user ID — got `{raw}`."
        member = guild.get_member(int(uid))
        return True, uid, (f"@{member.display_name}" if member else uid)

    if kind == "int":
        try:
            n = int(raw)
        except ValueError:
            return False, "", f"`{field}` needs a whole number — got `{raw}`."
        if n < 0:
            return False, "", f"`{field}` can't be negative."
        return True, str(n), str(n)

    if kind == "int_opt":
        if raw.lower() in ("", "unlimited", "none", "blank"):
            return True, "", "unlimited"
        try:
            n = int(raw)
        except ValueError:
            return False, "", f"`{field}` needs a whole number or `unlimited` — got `{raw}`."
        if n < 1:
            return False, "", f"`{field}` must be at least 1, or `unlimited`."
        return True, str(n), str(n)

    if kind == "bool":
        if raw.lower() in ("true", "yes", "on", "1"):
            return True, "true", "true"
        if raw.lower() in ("false", "no", "off", "0"):
            return True, "false", "false"
        return False, "", f"`{field}` needs true or false — got `{raw}`."

    if kind == "choice":
        choices = spec.get("choices") or []
        if raw.lower() not in choices:
            return False, "", f"`{field}` must be one of: {', '.join(f'`{c}`' for c in choices)}."
        return True, raw.lower(), raw.lower()

    if kind == "role":
        rid = raw.strip("<@&>")
        role = None
        if rid.isdigit():
            role = guild.get_role(int(rid))
        if role is None:
            role = discord.utils.get(guild.roles, name=raw)
        if role is None:
            return False, "", f"No role matches `{raw}` — paste its ID or exact name."
        return True, str(role.id), f"@{role.name}"

    return False, "", f"Don't know how to validate `{field}`."


def _coerce_locked_value(guild: discord.Guild, kind: str, stored: str) -> Any:
    """Rebuild a stored lock value as the runtime object a command expects.
    Returns _UNSET when it can't be built, so the user's own value stands."""
    if kind in ("str", "choice"):
        return stored
    if kind in ("int", "int_opt"):
        return int(stored) if str(stored).strip() else None
    if kind == "float":
        return float(stored)
    if kind == "bool":
        return str(stored).lower() in ("true", "yes", "on", "1")
    if kind == "role":
        return guild.get_role(int(stored)) if str(stored).isdigit() else _UNSET
    if kind == "channel":
        return guild.get_channel(int(stored)) if str(stored).isdigit() else _UNSET
    if kind == "user":
        return guild.get_member(int(stored)) if str(stored).isdigit() else _UNSET
    return _UNSET


_UNSET = object()


async def _apply_locks_to_namespace(command: app_commands.Command,
                                    interaction: discord.Interaction,
                                    namespace: Any) -> None:
    """Overwrite locked slash parameters before a command runs.

    This is what makes a lock apply to *any* command rather than only the
    ones that were taught about locks. It edits the namespace that
    discord.py is about to unpack into the command's arguments, so the
    command sees the locked value and can't be written to bypass it.

    Fields collected in a modal aren't in the namespace; those are still
    enforced by the command itself (see _locks_for)."""
    guild = interaction.guild
    if guild is None or _is_server_admin(interaction):
        return
    locks = _resolve_locks(_member_of(interaction), command.qualified_name,
                           _LOCK_CACHE.get(str(guild.id), []))
    if not locks:
        return
    specs = _lockable_for(command.qualified_name)
    applied: List[str] = []
    for field, stored in locks.items():
        spec = specs.get(field)
        if spec is None or field not in namespace.__dict__:
            continue           # modal-only field, or a parameter since removed
        try:
            value = _coerce_locked_value(guild, spec["kind"], stored)
        except (ValueError, TypeError):
            continue
        if value is _UNSET:
            continue
        if namespace.__dict__.get(field) != value:
            applied.append(field)
        namespace.__dict__[field] = value
    if applied:
        # Left for the command to mention if it wants to; harmless if not.
        interaction.extras["hg_locked"] = applied


_orig_invoke_with_namespace = app_commands.Command._invoke_with_namespace


async def _invoke_with_locks(self, interaction, namespace):
    """Wrapper around discord.py's private invoke path. Locks have to be
    applied after the namespace is built (interaction_check runs before it
    exists) and before arguments are unpacked. Any failure here is
    swallowed — a broken lock must never stop a command from running."""
    try:
        await _apply_locks_to_namespace(self, interaction, namespace)
    except Exception:  # noqa: BLE001
        log.exception("lock injection failed for /%s", getattr(self, "qualified_name", "?"))
    return await _orig_invoke_with_namespace(self, interaction, namespace)


app_commands.Command._invoke_with_namespace = _invoke_with_locks


async def _lock_command_choices(interaction: discord.Interaction,
                                current: str) -> List[app_commands.Choice[str]]:
    return [app_commands.Choice(name=c, value=c)
            for c in _all_lockable_commands() if current.lower() in c][:25]


async def _lock_field_choices(interaction: discord.Interaction,
                              current: str) -> List[app_commands.Choice[str]]:
    cmd = getattr(interaction.namespace, "command", None) or ""
    fields = _lockable_for(cmd)
    return [app_commands.Choice(name=f"{f} — {spec['label']}"[:100], value=f)
            for f, spec in fields.items() if current.lower() in f][:25]


@bot.tree.command(name="perms-lock",
                  description="Pin a command's parameter to a fixed value for a role.")
@app_commands.describe(
    command="Which command to constrain",
    role="Members of this role get the locked value",
    field="Which parameter to pin",
    value="Leave blank to remove the lock and let them choose freely",
)
@app_commands.autocomplete(command=_lock_command_choices, field=_lock_field_choices)
async def cmd_perms_lock(interaction: discord.Interaction, command: str,
                         role: discord.Role, field: str,
                         value: Optional[str] = None) -> None:
    if not _is_server_admin(interaction) or not interaction.guild:
        await interaction.response.send_message("🚫 Server Administrators only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    command = command.strip().lstrip("/")
    field = field.strip()

    # Blank value means "don't pin this one" — releasing the field back to
    # whatever the person running the command chooses.
    if value is None or not value.strip():
        res = await api.lock_remove(str(interaction.guild.id), command, str(role.id), field)
        await _refresh_locks(interaction.guild.id)
        if (res.get("data") or {}).get("removed"):
            await interaction.followup.send(
                f"🔓 Left `{field}` free on `/{command}` — **{role.name}** picks their own.",
                ephemeral=True)
        else:
            await interaction.followup.send(
                f"Nothing changed — **{role.name}** had no lock on `{field}` for "
                f"`/{command}`, so it was already theirs to set.", ephemeral=True)
        return

    known = _lockable_for(command)
    if not known:
        await interaction.followup.send(
            f"❌ No command called `/{command}`. Available: "
            + ", ".join(f"`{c}`" for c in _all_lockable_commands())[:1500], ephemeral=True)
        return
    ok, stored, human = _validate_lock_value(interaction.guild, command, field, value)
    if not ok:
        await interaction.followup.send(f"❌ {human}", ephemeral=True)
        return
    res = await api.lock_set(str(interaction.guild.id), command, str(role.id), role.name,
                             field, stored, str(interaction.user.id))
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
        return
    await _refresh_locks(interaction.guild.id)
    await interaction.followup.send(
        f"🔒 **{role.name}** running `/{command}` now always gets **{field} = {human}**, "
        f"whatever they type.\n"
        f"Administrators and the server owner are never affected by locks.",
        ephemeral=True,
    )


@bot.tree.command(name="perms-unlock",
                  description="Remove a parameter lock from a role.")
@app_commands.describe(command="The command", role="The role", field="The parameter to release")
@app_commands.autocomplete(command=_lock_command_choices, field=_lock_field_choices)
async def cmd_perms_unlock(interaction: discord.Interaction, command: str,
                           role: discord.Role, field: str) -> None:
    if not _is_server_admin(interaction) or not interaction.guild:
        await interaction.response.send_message("🚫 Server Administrators only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    res = await api.lock_remove(str(interaction.guild.id), command.strip(),
                                str(role.id), field.strip())
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
        return
    await _refresh_locks(interaction.guild.id)
    removed = (res.get("data") or {}).get("removed", 0)
    if not removed:
        await interaction.followup.send(
            f"Nothing to remove — **{role.name}** had no lock on `{field}` for `/{command}`.",
            ephemeral=True)
        return
    await interaction.followup.send(
        f"🔓 **{role.name}** can set `{field}` on `/{command}` freely again.", ephemeral=True)


@bot.tree.command(name="perms-locks",
                  description="Show every parameter lock in this server.")
async def cmd_perms_locks(interaction: discord.Interaction) -> None:
    if not _is_server_admin(interaction) or not interaction.guild:
        await interaction.response.send_message("🚫 Server Administrators only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    locks = await _refresh_locks(interaction.guild.id)
    if not locks:
        await interaction.followup.send(
            "No parameter locks set. Use `/perms-lock` to pin a parameter for a role — "
            "any command, any of its parameters.\n\nCommands you can lock: "
            + ", ".join(f"`/{c}`" for c in _all_lockable_commands())[:1500],
            ephemeral=True)
        return
    by_cmd: Dict[str, List[str]] = {}
    for row in locks:
        shown = row.get("value")
        if row.get("field") == "role" and str(shown or "").isdigit():
            r = interaction.guild.get_role(int(shown))
            shown = f"@{r.name}" if r else f"(deleted role {shown})"
        elif row.get("field") == "maxClaims" and not shown:
            shown = "unlimited"
        by_cmd.setdefault(row.get("command") or "?", []).append(
            f"• <@&{row.get('roleId')}> → `{row.get('field')}` = **{shown}**")
    embed = discord.Embed(title="🔒 Parameter locks", colour=0xF59E0B)
    for cmd, rows in by_cmd.items():
        embed.add_field(name=f"/{cmd}", value="\n".join(rows)[:1024], inline=False)
    embed.set_footer(text="Highest role wins when a member has several locks on one field. "
                          "Administrators and the server owner are exempt.")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── Onboarding setup (admin) ─────────────────────────────────────────
@bot.tree.command(name="setup-verify",
                  description="Post the Verify panel in this channel.")
async def cmd_setup_verify(interaction: discord.Interaction) -> None:
    if not _is_server_admin(interaction) or not interaction.guild:
        await interaction.response.send_message(
            "🚫 Server Administrators only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    embed = discord.Embed(
        title="👋 Welcome to HigherGrade Tutoring",
        description=(
            "The rest of this server is for **campers and staff only**, so it's "
            "hidden until you prove you're one of us.\n\n"
            "Tap **Verify me** below and sign in with the email and password you "
            "use on [highergradetutoring.ca](https://highergradetutoring.ca). "
            "That unlocks the camper channels, sets your nickname to your real "
            "name, and carries over any camp roles you've already earned.\n\n"
            "**Staff:** you don't verify here — ping an admin and they'll hand "
            "you the Staff role directly."
        ),
        colour=0x5865F2,
    )
    embed.set_footer(text="Your password goes straight to the camp site over the "
                          "bot's private API — nobody in the server can see it.")
    try:
        await interaction.channel.send(embed=embed, view=VerifyPanelView())
    except discord.Forbidden:
        await interaction.followup.send(
            "🚫 I can't post in this channel — check my permissions here.", ephemeral=True)
        return
    await interaction.followup.send(
        "✅ Panel posted. Make sure this channel is the one channel `@everyone` "
        "can still see (`/gate` this channel as **public**).", ephemeral=True)


@bot.tree.command(name="gate",
                  description="Lock a channel or category to verified members.")
@app_commands.describe(
    target="The channel or category to change",
    access="Who should be able to see it",
    apply_to_children="For a category: also re-sync every channel inside it",
)
@app_commands.choices(access=[
    app_commands.Choice(name="Students + Staff (verified only)", value="members"),
    app_commands.Choice(name="Staff only", value="staff"),
    app_commands.Choice(name="Public (anyone, including unverified)", value="public"),
])
async def cmd_gate(
    interaction: discord.Interaction,
    target: discord.abc.GuildChannel,
    access: app_commands.Choice[str],
    apply_to_children: bool = True,
) -> None:
    """Sets the @everyone / Student / Staff view-channel overwrites so the
    server's privacy model lives in one command instead of a lot of
    hand-clicking. Everything else about the channel is left alone."""
    if not _is_server_admin(interaction) or not interaction.guild:
        await interaction.response.send_message(
            "🚫 Server Administrators only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild

    # `access` and `apply_to_children` are real slash parameters, so any
    # lock on them was already applied to the arguments this function
    # received. Nothing to enforce here — just say what was changed.
    overridden = list(interaction.extras.get("hg_locked") or [])

    everyone = guild.default_role
    student = await ensure_role(guild, STUDENT_ROLE_NAME,
                                color=discord.Color.blurple(), hoist=True)
    staff = await ensure_role(guild, STAFF_ROLE_NAME,
                              color=discord.Color.green(), hoist=True)

    # None clears the overwrite (falls back to inherited/default) rather
    # than writing an explicit allow — that's what "public" should mean.
    if access.value == "members":
        wanted = {everyone: False, student: True, staff: True}
    elif access.value == "staff":
        wanted = {everyone: False, student: False, staff: True}
    else:
        wanted = {everyone: None, student: None, staff: None}

    try:
        for role, view in wanted.items():
            await target.set_permissions(
                role, view_channel=view,
                reason=f"HigherGrade /gate → {access.value}")
    except discord.Forbidden:
        await interaction.followup.send(
            "🚫 I need **Manage Channels** (and my role above Student/Staff) "
            "to change permissions here.", ephemeral=True)
        return

    synced = 0
    if apply_to_children and isinstance(target, discord.CategoryChannel):
        for child in target.channels:
            try:
                await child.edit(sync_permissions=True,
                                 reason="HigherGrade /gate — inherit from category")
                synced += 1
            except discord.Forbidden:
                pass

    msg = f"✅ **{target.name}** is now **{access.name}**."
    if synced:
        msg += f" Re-synced {synced} channel(s) inside it."
    if access.value != "public" and VERIFY_CHANNEL_ID and str(target.id) == VERIFY_CHANNEL_ID:
        msg += ("\n\n⚠️ That's your verify channel — new members can no longer see it, "
                "so nobody can verify. Set it back to **public**.")
    if overridden:
        msg += "\n\n🔒 Locked by your role: " + ", ".join(overridden) + "."
    await interaction.followup.send(msg, ephemeral=True)


class ChestCreateModal(discord.ui.Modal, title="📦 Place a chest"):
    """The passcode, reveal text, reward and claim cap are collected in a
    modal rather than as slash-command options — the paragraph field takes
    far more text than the chat box will let you type into an option, which
    is what makes long chest descriptions practical."""

    code = discord.ui.TextInput(
        label="Passcode", placeholder="What players must type to open it",
        min_length=1, max_length=128, required=True,
    )
    description = discord.ui.TextInput(
        label="Reveal text (shown on the chest + on open)",
        style=discord.TextStyle.paragraph,
        placeholder="Anything you like — lore, a riddle, the next clue…",
        max_length=4000, required=True,
    )
    points = discord.ui.TextInput(
        label="Points awarded (0 for none)",
        default=str(DEFAULT_CHEST_POINTS),
        max_length=6, required=False,
    )
    max_claims = discord.ui.TextInput(
        label="Max openers (blank = unlimited)",
        placeholder="Leave blank so everyone with the code can open it",
        max_length=6, required=False,
    )

    def __init__(self, role: discord.Role, image_url: Optional[str],
                 locks: Optional[Dict[str, str]] = None,
                 remove_role: Optional[discord.Role] = None) -> None:
        super().__init__()
        self.role = role
        self.image_url = image_url
        self.remove_role = remove_role
        # Pre-fill and relabel any locked field so the creator can see the
        # value is not theirs to set. Enforcement still happens on submit
        # against a fresh fetch — this is presentation only.
        locks = locks or {}
        if "points" in locks:
            self.points.default = str(locks["points"])
            self.points.label = "Points awarded (locked by your role)"[:45]
        if "maxClaims" in locks:
            self.max_claims.default = str(locks["maxClaims"] or "")
            self.max_claims.label = "Max openers (locked by your role)"[:45]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Authoritative permission check. It lives here rather than before
        # send_modal because for a non-admin it costs a round-trip to the
        # camp API, and a modal must be the first response to an
        # interaction inside 3 seconds — no defer() is possible there.
        if not await _user_can_run(interaction, "chest-create"):
            await interaction.followup.send(
                "🚫 You don't have permission to run **chest-create** in this server. "
                "Ask a server admin to grant your role with `/perms-grant`.",
                ephemeral=True,
            )
            return

        # Authoritative lock lookup — whatever they typed into a locked
        # field is discarded here, including a hand-crafted submission that
        # never went through the pre-filled modal.
        locks = await _locks_for(interaction, "chest-create")
        overridden: List[str] = []

        raw_points = str(self.points.value or "").strip()
        if "points" in locks:
            pts = int(locks["points"])
            if raw_points and raw_points != str(pts):
                overridden.append(f"points → **{pts}**")
        else:
            try:
                pts = int(raw_points) if raw_points else DEFAULT_CHEST_POINTS
            except ValueError:
                await interaction.followup.send(
                    f"❌ Points must be a whole number — got `{raw_points}`.", ephemeral=True)
                return
            if pts < 0:
                await interaction.followup.send("❌ Points can't be negative.", ephemeral=True)
                return

        raw_max = str(self.max_claims.value or "").strip()
        cap: Optional[int] = None
        if "maxClaims" in locks:
            locked_max = str(locks["maxClaims"] or "").strip()
            cap = int(locked_max) if locked_max else None
            if raw_max != locked_max:
                overridden.append(f"max openers → **{cap if cap else 'unlimited'}**")
        elif raw_max:
            try:
                cap = int(raw_max)
            except ValueError:
                await interaction.followup.send(
                    f"❌ Max openers must be a whole number — got `{raw_max}`. "
                    "Leave it blank for unlimited.", ephemeral=True)
                return
            if cap < 1:
                await interaction.followup.send(
                    "❌ Max openers must be at least 1 — leave it blank for unlimited.",
                    ephemeral=True)
                return

        # The granted role is picked before the modal opens, so re-apply its
        # lock here too rather than trusting what came in.
        role = self.role
        if "role" in locks:
            locked_role = interaction.guild.get_role(int(locks["role"])) if str(
                locks["role"]).isdigit() else None
            if locked_role and locked_role.id != role.id:
                overridden.append(f"role → **{locked_role.name}**")
                role = locked_role

        desc = str(self.description.value).strip()
        res = await api.chest_create(
            str(interaction.guild.id), str(self.code.value).strip(),
            str(role.id), role.name, desc, str(interaction.user.id),
            image_url=self.image_url, points=pts, max_claims=cap,
            remove_role_id=str(self.remove_role.id) if self.remove_role else "",
            remove_role_name=self.remove_role.name if self.remove_role else "",
        )
        if not res.get("ok"):
            await interaction.followup.send(f"❌ {res.get('error') or 'Failed.'}", ephemeral=True)
            return
        chest_id = (res.get("data") or {}).get("id")
        if not chest_id:
            await interaction.followup.send("❌ Server didn't return a chest id.", ephemeral=True)
            return

        embeds = [_chest_embed(desc, image_url=self.image_url, role_name=role.name,
                               points=pts, max_claims=cap,
                               remove_role_name=self.remove_role.name if self.remove_role else None,
                               )] + _chest_overflow_embeds(desc)

        # Post the public chest message into the channel the command was run
        # in. The button is persistent, so this message keeps working forever
        # (until the chest is deleted). Discord caps a message at 6000 chars
        # across all its embeds, so overflow goes into follow-up messages.
        posted = None
        try:
            posted = await interaction.channel.send(embed=embeds[0], view=_chest_view(chest_id))
            for extra in embeds[1:]:
                await interaction.channel.send(embed=extra)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I can't send messages in this channel — give me Send Messages + "
                "Embed Links permission and try again.", ephemeral=True)
            # Roll back the chest record so we don't leave a phantom entry.
            await api.chest_delete(chest_id)
            return

        if posted:
            try:
                await api.chest_set_message(chest_id, str(posted.channel.id), str(posted.id))
            except Exception:  # noqa: BLE001
                log.exception("failed to save chest message id")
        # Drop the cached list so the new chest shows up in the delete
        # picker immediately rather than up to CHEST_CACHE_TTL later.
        _CHEST_CACHE.pop(str(interaction.guild.id), None)

        summary = (
            f"📦 Chest placed in {posted.channel.mention if posted else 'this channel'}.\n"
            f"• Code: **{str(self.code.value).strip()}**\n"
            f"• Unlocks: **{role.name}**\n"
            f"• Reward: **{pts} pts**" + (" (no points)" if pts == 0 else "") + "\n"
            f"• Openers: **{cap if cap else 'unlimited'}** — one open per person either way"
        )
        if self.remove_role:
            summary += f"\n• Removes: **{self.remove_role.name}** on open"
            # Camp-mirrored roles get re-asserted from the website, so a
            # chest stripping one only sticks until the next sync pass.
            if self.remove_role.name in _camp_discord_role_names():
                summary += (
                    f"\n\n⚠️ **{self.remove_role.name}** mirrors a camp role from the "
                    f"website. The sync loop re-adds it within 2 minutes for anyone who "
                    f"still holds it there — take it off their camp profile too if it "
                    f"should stay off.")
        if len(embeds) > 1:
            summary += f"\n• Description spans {len(embeds)} message blocks."
        if overridden:
            summary += ("\n\n🔒 Your role locks some of these — applied instead of what "
                        "you entered: " + ", ".join(overridden) + ".")
        await interaction.followup.send(summary, ephemeral=True)


@bot.tree.command(name="chest-create", description="Place a locked chest in this channel.")
@app_commands.describe(
    role="The role granted on unlock",
    image="Optional image to embed in the chest message",
    remove_role="Optional role taken away on unlock, so roles swap instead of stacking",
)
async def cmd_chest_create(
    interaction: discord.Interaction,
    role: discord.Role,
    image: Optional[discord.Attachment] = None,
    remove_role: Optional[discord.Role] = None,
) -> None:
    # No defer() here — a modal has to be the FIRST response to the
    # interaction, so only cheap local checks can run before we reply.
    # The permission check needs the network, so it runs in on_submit.
    if not interaction.guild:
        await interaction.response.send_message("Run this in a server.", ephemeral=True)
        return
    me = interaction.guild.me
    if me and role >= me.top_role:
        await interaction.response.send_message(
            f"❌ I can't grant **{role.name}** — it's above my top role. "
            "Move my role above it in Server Settings → Roles.",
            ephemeral=True,
        )
        return
    if image is not None and not (image.content_type or "").startswith("image/"):
        await interaction.response.send_message(
            "❌ The `image` attachment doesn't look like an image file.",
            ephemeral=True,
        )
        return
    if remove_role is not None:
        if me and remove_role >= me.top_role:
            await interaction.response.send_message(
                f"❌ I can't remove **{remove_role.name}** — it's above my top role. "
                "Move my role above it in Server Settings → Roles.",
                ephemeral=True,
            )
            return
        if remove_role.name == STUDENT_ROLE_NAME:
            await interaction.response.send_message(
                f"❌ **{STUDENT_ROLE_NAME}** is the role that verification grants — "
                "stripping it would lock the camper out of every channel. "
                "It also wouldn't stick: the sync loop re-adds it within 2 minutes.",
                ephemeral=True,
            )
            return
        if remove_role.id == role.id:
            await interaction.response.send_message(
                f"❌ The chest would grant and remove **{role.name}** at the same time. "
                "Pick a different role to remove.",
                ephemeral=True,
            )
            return
    # Cached locks only — there's no time for a fetch before a modal, and
    # these are used purely to pre-fill. on_submit re-checks for real.
    await interaction.response.send_modal(
        ChestCreateModal(role, image.url if image else None,
                         locks=_locks_cached(interaction, "chest-create"),
                         remove_role=remove_role))


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
    lines = ["**Chests in this server** — newest first. "
             "Use `/chest-delete` and pick from the dropdown; you don't need these IDs.\n"]
    for c in chests:
        cap = c.get("maxClaims")
        opens = f"{c.get('claimedCount', 0)}/{cap}" if cap else f"{c.get('claimedCount', 0)}/∞"
        pts = int(c.get("points") or 0)
        blurb = (c.get("description") or "(no description)").replace("\n", " ")
        # <t:unix:R> renders as "2 hours ago" in each viewer's own timezone.
        when = f" · placed <t:{int(c['createdAt'])}:R>" if c.get("createdAt") else ""
        swap = f" · removes <@&{c['removeRoleId']}>" if c.get("removeRoleId") else ""
        lines.append(
            f"• code **{c['code']}** → <@&{c['roleId']}>{swap} "
            f"· {opens} opens · {pts} pts{when}\n"
            f"  `{c['id']}` · {blurb[:70]}{'…' if len(blurb) > 70 else ''}"
        )
    await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)


# Autocomplete fires on every keystroke and has to answer within 3
# seconds, so the chest list is cached briefly rather than re-fetched per
# character. Invalidated whenever a chest is created or deleted.
_CHEST_CACHE: Dict[str, Any] = {}
CHEST_CACHE_TTL = 10.0


async def _chests_cached(guild_id: Any) -> List[Dict[str, Any]]:
    key = str(guild_id)
    now = time.time()
    hit = _CHEST_CACHE.get(key)
    if hit and (now - hit[0]) < CHEST_CACHE_TTL:
        return hit[1]
    try:
        res = await api.chest_list(key)
        if res.get("ok"):
            data = res.get("data") or []
            _CHEST_CACHE[key] = (now, data)
            return data
    except Exception:  # noqa: BLE001
        log.exception("chest list fetch failed for guild %s", guild_id)
    return hit[1] if hit else []


def _chest_label(c: Dict[str, Any]) -> str:
    """One-line description of a chest for the delete picker. Discord caps
    a choice name at 100 characters."""
    cap = c.get("maxClaims")
    opens = f"{c.get('claimedCount', 0)}/{cap}" if cap else f"{c.get('claimedCount', 0)}"
    when = ""
    ts = c.get("createdAt")
    if ts:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            when = " · " + datetime.fromtimestamp(int(ts),
                                                  ZoneInfo("America/Toronto")).strftime("%b %-d, %-I:%M %p")
        except Exception:  # noqa: BLE001
            when = ""
    label = f"{c.get('code')} → {c.get('roleName') or 'role'} · {opens} opens{when}"
    return label[:100]


async def _chest_delete_choices(interaction: discord.Interaction,
                                current: str) -> List[app_commands.Choice[str]]:
    """Newest chest first — the server already returns them createdAt DESC."""
    if not interaction.guild:
        return []
    chests = await _chests_cached(interaction.guild.id)
    cur = (current or "").lower()
    out = []
    for c in chests:
        hay = f"{c.get('code','')} {c.get('roleName','')} {c.get('id','')} {c.get('description','')}".lower()
        if cur and cur not in hay:
            continue
        out.append(app_commands.Choice(name=_chest_label(c), value=str(c.get("id"))))
        if len(out) >= 25:      # Discord's hard cap on autocomplete options
            break
    return out


@bot.tree.command(name="chest-delete", description="Remove a chest — pick it from the list.")
@app_commands.describe(chest="Newest first. Start typing to filter by code, role, or text.")
@app_commands.autocomplete(chest=_chest_delete_choices)
async def cmd_chest_delete(interaction: discord.Interaction, chest: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not await _user_can_run(interaction, "chest-delete"):
        await interaction.followup.send(
            "🚫 You don't have permission to run **chest-delete**. "
            "Ask a server admin to grant your role with `/perms-grant`.",
            ephemeral=True,
        )
        return
    chest_id = chest.strip()
    # Resolve what we're about to delete so the confirmation names it —
    # and so a typed-but-unpicked value fails loudly instead of silently
    # deleting nothing.
    chests = await _chests_cached(interaction.guild.id) if interaction.guild else []
    match = next((c for c in chests if str(c.get("id")) == chest_id), None)
    if match is None:
        match = next((c for c in chests if str(c.get("code", "")).lower() == chest_id.lower()), None)
    if match is None:
        await interaction.followup.send(
            f"❌ No chest matching `{chest_id}` in this server. Pick one from the "
            "dropdown, or run `/chest-list` to see what's there.", ephemeral=True)
        return

    res = await api.chest_delete(str(match["id"]))
    _CHEST_CACHE.pop(str(interaction.guild.id), None)
    if not res.get("ok"):
        await interaction.followup.send(f"❌ {res.get('error') or 'Delete failed.'}",
                                        ephemeral=True)
        return
    await interaction.followup.send(
        f"🗑 Deleted chest **{match.get('code')}** → **{match.get('roleName') or 'role'}** "
        f"· {match.get('claimedCount', 0)} open(s).\n"
        f"The chest message stays in the channel — its button now says the code "
        f"doesn't open anything. Delete the message yourself if you want it gone.",
        ephemeral=True,
    )


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
def _verify_channel(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
    """The public channel holding the Verify panel: VERIFY_CHANNEL_ID, else
    a channel named 'verify'/'start-here'/'welcome'."""
    if VERIFY_CHANNEL_ID:
        ch = bot.get_channel(int(VERIFY_CHANNEL_ID))
        if ch is not None:
            return ch
        log.warning("VERIFY_CHANNEL_ID=%s not found — falling back", VERIFY_CHANNEL_ID)
    for name in ("verify", "start-here", "welcome"):
        ch = discord.utils.get(guild.text_channels, name=name)
        if ch is not None:
            return ch
    return None


@bot.event
async def on_member_join(member: discord.Member) -> None:
    """Point new arrivals at the verify panel. DMs are best-effort — plenty
    of people have server DMs turned off, which is exactly why the panel
    also lives in a channel they can see."""
    if member.bot:
        return
    channel = _verify_channel(member.guild)
    where = channel.mention if channel else "the verify channel"
    try:
        await member.send(
            f"👋 Welcome to **{member.guild.name}**!\n\n"
            f"Most of the server is camper- and staff-only, so it'll look pretty empty "
            f"until you verify. Head to {where} and tap **Verify me**, then sign in with "
            f"your highergradetutoring.ca email and password.\n\n"
            f"Staff: ask an admin to give you the **Staff** role instead."
        )
    except (discord.Forbidden, discord.HTTPException):
        log.info("couldn't DM welcome to %s (DMs closed)", member)


_did_cmd_dedup = False


@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (id=%s) — in %d guild(s)", bot.user, bot.user.id, len(bot.guilds))
    global _did_cmd_dedup
    if not _did_cmd_dedup:
        _did_cmd_dedup = True
        # De-dupe: wipe any leftover GUILD-scoped command copies so only the
        # global set remains. They linger from a past run when GUILD_ID was set,
        # and Discord shows them *alongside* the globals (every command twice).
        for guild in bot.guilds:
            try:
                bot.tree.clear_commands(guild=guild)
                await bot.tree.sync(guild=guild)
                log.info("cleared stale guild commands for guild %s", guild.id)
            except Exception:  # noqa: BLE001
                log.exception("guild command cleanup failed for %s", guild.id)


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
