# HigherGrade Tutoring — Discord bot

A small `discord.py` bot that mirrors camp roles + display names into a
Discord server, lets students self-link via `/verify`, and provides
admin-placed "locked chests" that grant a Discord role when unlocked
with the right passcode.

The bot polls the camp API every 2 minutes — there is no webhook, so
expect a couple-minute lag for role/nickname changes to show up in
Discord.

---

## 1) Create the bot in the Discord developer portal

1. Sign in to <https://discord.com/developers/applications> → **New
   Application** → name it `HigherGrade Tutoring` (or whatever).
2. Sidebar → **Bot** → **Add Bot**.
3. Under **Privileged Gateway Intents**, turn on **Server Members
   Intent**. (This is required for the polling loop to read members
   and update nicknames.) The other two privileged intents stay off.
4. Click **Reset Token**, copy the long token string. This is your
   `DISCORD_TOKEN` — keep it secret.
5. Sidebar → **OAuth2** → **URL Generator**:
   - **Scopes:** `bot`, `applications.commands`
   - **Bot permissions:** `Manage Roles`, `Manage Nicknames`,
     `Send Messages`, `Use Slash Commands`
   - Copy the generated URL and open it in a browser to invite the bot
     to your server.
6. After the bot joins, **drag its role above** the roles it should
   manage in Server Settings → Roles. Discord won't let it grant or
   revoke any role positioned above its own top role.

## 2) Get a bot-API token from the camp server

The bot authenticates to the camp API with a shared secret. Generate
one and put it on the VM:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
# copy the output, then on the VM:
sudo nano /etc/highergrade.env
#   add: HIGHERGRADE_BOT_TOKEN=<paste>
sudo systemctl restart highergrade-api
```

You'll use the same value as `BOT_API_TOKEN` in the bot's environment.

## 3) Run the bot locally (test)

```bash
cd math_camp/discord-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then edit
python bot.py
```

`.env` contents:

```
DISCORD_TOKEN=<from step 1.4>
BOT_API_TOKEN=<from step 2>
CAMP_API_BASE=https://highergradetutoring.ca
GUILD_ID=<your server's id, optional>
POLL_INTERVAL_SECONDS=120
```

`GUILD_ID` is optional but recommended during setup — when set, slash
commands appear instantly in that one server. Without it Discord can
take up to an hour to register global commands.

To find a server's ID: enable Developer Mode in Discord (Settings →
Advanced → Developer Mode), right-click the server icon → Copy ID.

## 4) Run the bot on the production VM

After confirming it works locally, ship it alongside the camp API:

```bash
ssh ubuntu@40.233.122.40
cd /var/www/highergrade/math_camp/discord-bot
sudo -u www-data python3 -m venv .venv
sudo -u www-data .venv/bin/pip install -r requirements.txt

# Bot env file — separate from the API's so you can rotate independently.
sudo tee /etc/highergrade-bot.env >/dev/null <<'EOF'
DISCORD_TOKEN=<your bot token>
BOT_API_TOKEN=<the same token you put in /etc/highergrade.env>
CAMP_API_BASE=https://highergradetutoring.ca
GUILD_ID=<your server id>
POLL_INTERVAL_SECONDS=120
EOF
sudo chmod 600 /etc/highergrade-bot.env

sudo cp highergrade-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now highergrade-bot
sudo systemctl status highergrade-bot --no-pager
journalctl -u highergrade-bot -f      # tail the logs
```

## 5) Slash-command reference

Run from any text channel the bot can see. All replies are ephemeral
(only the user who ran the command sees them).

| Command | Who | What it does |
| --- | --- | --- |
| `/verify email:<…> password:<…>` | Anyone | Links your Discord user to your camp account. On success you get the **Student** role and your nickname is set to your camp name. |
| `/whoami` | Anyone | Shows your linked profile + current points. |
| `/unlink` | Anyone | Removes your link and revokes the bot-managed roles. |
| `/unlock code:<passcode>` | Anyone | Opens the chest with that code and grants its hidden role. |
| `/chest-create code:<…> role:<@role> description:<…>` | Manage Roles | Places a chest. The code unlocks the role; the description shows when claimed. |
| `/chest-list` | Manage Roles | Lists every chest in the server with its code, role, and claim count. |
| `/chest-delete chest_id:<id>` | Manage Roles | Deletes a chest by ID (from `/chest-list`). |

## 6) About verification

Discord's "verified" badge is **only required once a bot is in 100 or
more servers**. Below that threshold, no verification is needed —
just invite it and run.

When you cross 100 guilds, Discord prompts you to apply at
<https://support.discord.com/hc/en-us/requests/new?ticket_form_id=360000629171>.
You'll need:

- A photo ID for the bot owner
- A privacy policy + terms of service URL (you can host these as plain
  text on the camp site, e.g. `/bot-privacy.html`)
- A short description of what the bot does and why it needs the
  privileged Members intent (answer: nickname + role mirroring for a
  student leaderboard)

For a 40-student summer camp this isn't relevant — you'll have one
guild and the bot will just work.

## 7) Roles the bot manages

The bot creates and **only ever modifies** these roles. Anything else
in your server is left alone.

- `Student` — granted on `/verify`, removed on `/unlink`.
- `Camp · Maze Wizard`, `Camp · Money Tree`, `Camp · Clicker`,
  `Camp · Paper Crane` — mirrored from the camp's role table.
- Any role you wire into a chest via `/chest-create`. The bot grants
  it on `/unlock` but doesn't remove it.

If you rename a managed role in Discord, the bot will create a fresh
one with the original name on the next poll. Either rename the camp
role at the same time (in the source code constants) or live with the
duplicate.

## 8) Troubleshooting

- **Slash commands don't appear** → set `GUILD_ID` in the bot env, restart
  the bot. Without it, global sync can take up to an hour.
- **"I can't grant that role"** → in Server Settings → Roles, drag the
  bot's role above the one it's trying to assign.
- **Members intent error on startup** → re-open dev portal → Bot →
  enable Server Members Intent.
- **Bot is online but `/verify` says "Bot integration is disabled"** →
  the API can't see `HIGHERGRADE_BOT_TOKEN`. Check `/etc/highergrade.env`
  on the VM and `sudo systemctl restart highergrade-api`.
