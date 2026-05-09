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

# Names the bot manages on Discord. The bot creates these if missing
# and only ever adds/removes these specific roles — it never touches
# user-defined roles outside this set.
STUDENT_ROLE_NAME = "Student"
CAMP_ROLE_PREFIX  = "Camp · "  # camp-game roles get this prefix in Discord

# Mapping of camp-side role IDs (from the `roles` table) to a friendly
# Discord role name. The bot creates each as "Camp · <name>" if missing.
CAMP_ROLE_NAMES: Dict[str, str] = {
    "mazewiz":    "Maze Wizard",
    "money_tree": "Money Tree",
    "clicker":    "Clicker",
    "crane":      "Paper Crane",
}

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


bot = HGBot()


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
    return [CAMP_ROLE_PREFIX + v for v in CAMP_ROLE_NAMES.values()]


async def _sync_member(member: discord.Member, summary: Dict[str, Any]) -> None:
    """Reconcile a single member's nickname + roles against the camp
    summary returned by /api/bot/students."""
    full_name = (summary.get("fullName") or "").strip()
    desired_camp_roles = {
        CAMP_ROLE_PREFIX + CAMP_ROLE_NAMES[r]
        for r in (summary.get("roles") or [])
        if r in CAMP_ROLE_NAMES
    }
    desired_camp_roles.add(STUDENT_ROLE_NAME)

    # Build the union of roles we manage so we can untouch the rest.
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
                await ensure_role(guild, CAMP_ROLE_PREFIX + camp_role_name, color=discord.Color.gold())
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
                await _sync_member(member, summary)
        except Exception:  # noqa: BLE001
            log.exception("sync error in guild %s", getattr(guild, "id", "?"))


@sync_loop.before_loop
async def _before_sync() -> None:
    await bot.wait_until_ready()


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
    await _sync_member(member, summary)

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
def _admin_only(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and (perms.manage_roles or perms.administrator))


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
    if not _admin_only(interaction):
        await interaction.followup.send("This command is admin-only.", ephemeral=True)
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
    if not _admin_only(interaction):
        await interaction.followup.send("This command is admin-only.", ephemeral=True)
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
    if not _admin_only(interaction):
        await interaction.followup.send("This command is admin-only.", ephemeral=True)
        return
    await api.chest_delete(chest_id.strip())
    await interaction.followup.send(f"🗑 Chest `{chest_id}` deleted.", ephemeral=True)


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
