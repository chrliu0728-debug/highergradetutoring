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
| `/chest-create [role:<@role>] [image:<file>] [remove_role:<@role>]` | Manage Roles | Opens a form for the passcode, reveal text, points reward, and opener cap. `role` is optional — skip it for a points-only chest. |
| `/chest-list` | Manage Roles | Lists every chest with its code, role, opens/cap, points, and description. |
| `/chest-delete chest:<pick>` | Manage Roles | Pick a chest from a dropdown, newest first. No IDs to copy. |
| `/onboard` | Anyone | Re-opens the onboarding questions. |
| `/submit title:<…> file:<…>` | Verified campers | Hands homework in for marking. |
| `/submissions [show:<…>]` | Staff | Lists homework waiting to be marked. |
| `/award student:<@…> points:<n> reason:<…>` | Staff | Gives or takes points, with a reason that lands on the transaction. |
| `/handraise student:<@…> [times:<n>]` | Staff | Credits hand-raises — 2 pts each, via the Hand Raised base stat. |
| `/attendance student:<@…> status:<…> [date]` | Staff | Present (+250), Late (−50), or Absent (0). |
| `/attendance-today [date]` | Staff | Who's been marked today, with a tally. |
| `/setup-verify` | Administrator | Posts the "Verify me" panel in the current channel. |
| `/gate target:<#channel> access:<…>` | Administrator | Sets who can see a channel or category. |
| `/perms-lock command:<…> role:<@role> field:<…> value:<…>` | Administrator | Pins a command's parameter to a fixed value for a role. |
| `/perms-unlock command:<…> role:<@role> field:<…>` | Administrator | Removes a parameter lock. |
| `/perms-locks` | Administrator | Shows every parameter lock in the server. |

## 5b) Onboarding — locking the server to campers and staff

The gate is: **verifying against a camp account is what grants the
`Student` role, and `Student`/`Staff` are what make the channels
visible.** Nobody who can't log in to highergradetutoring.ca gets past
the front door.

**One-time setup:**

1. Invite the bot with **Manage Channels** in addition to the
   permissions in step 1 (or tick it in Server Settings → Roles). `/gate`
   can't edit permissions without it.
2. Make a public landing channel — `#verify` is the conventional name so
   the fallback finds it. Run `/gate target:#verify access:Public`.
3. Run `/setup-verify` in that channel. It posts the panel with the
   **Verify me** button. The button is persistent — it survives bot
   restarts and redeploys, so you post it once and never again.
4. Make a **staff-only** `#onboarding-answers` channel and
   `/gate target:#onboarding-answers access:Staff only`.
5. For every other category: `/gate target:<category>
   access:Students + Staff`. With `apply_to_children` left on (the
   default) every channel inside re-syncs to the category, so you do one
   command per category rather than one per channel.
6. Put the channel IDs in the bot env (`VERIFY_CHANNEL_ID`,
   `ONBOARD_CHANNEL_ID`) and restart. Not strictly required — the bot
   falls back to channel *names* — but the IDs are what survive a rename.

**What a new member experiences:**

1. They join. Every channel but `#verify` is invisible. The bot DMs them
   a pointer to `#verify` (best-effort — many people have DMs off, which
   is why the panel is also in a channel they can see).
2. They tap **Verify me** and enter their camp email + password in a
   modal. The password goes to `/api/bot/link` over the bot's private
   API — it is never rendered as text in the server, which is why this
   is better than the `/verify` slash command.
3. On success: `Student` role, nickname set to their real camp name, and
   any camp-game roles they'd already earned. The channels appear.
4. They then get an **Answer a few questions** button. Their answers get
   posted as an embed into `#onboarding-answers`, tagged with their
   mention, their camp account name, and their user ID. They can redo it
   any time with `/onboard`.

**Staff** don't verify — there are no staff logins on the camp site (the
`staff` table is bios and photos only). An admin hands out the `Staff`
role by hand in Server Settings → Members. The bot creates that role but
never assigns or removes it, and never mirrors it back to the camp site.

**Frozen accounts** — registered but the payment isn't confirmed — are
refused at `/api/bot/link` with an explanation, and get no roles. If you
re-freeze somebody on the website later, the 2-minute sync loop strips
their `Student` and camp roles, so the channels close again on their
own.

To change the onboarding questions, edit `ONBOARD_QUESTIONS` near the top
of the verification section in `bot.py`. Discord allows at most 5
questions per modal and caps each label at 45 characters.

## 5c) Chests

`/chest-create role:<@role>` opens a form with four fields. Only the first
two are required:

| Field | Default | Notes |
| --- | --- | --- |
| Passcode | — | Must be unique per server. |
| Reveal text | — | Up to 4000 characters. Shown on the chest message *and* on unlock. |
| Points awarded | `50` | Paid on a first-time open. `0` disables the reward. |
| Max openers | *blank* | Blank = unlimited. Any number caps total distinct openers. |

The `role`, `image`, and `remove_role` options stay as slash-command
options because Discord forms can't hold a role picker or a file upload.
**All three are optional.** Leave `role` blank for a **points-only
chest** that pays out but grants nothing — picking a role literally
named `N/A` (or `NA`/`None`) counts as blank too. A chest with neither
a role nor points is refused, since it would do nothing on unlock.

**Swapping roles instead of stacking them.** `remove_role` takes a role
*away* on unlock, so a chest can promote someone rather than piling roles
on top of each other:

```
/chest-create role:@Level 2 remove_role:@Level 1
```

Opening it grants Level 2 and strips Level 1 in one step. It only fires
on a **first-time** open, and only if the person actually holds the role
— no wasted API call otherwise. The chest message footer says
*"Replaces: Level 1"* so it's visible before anyone opens it.

Three things it refuses at creation time: a role above the bot's own top
role (it couldn't remove it), the same role it's granting, and the
`Student` role — stripping that would lock the camper out of every
channel, and the sync loop would put it back within 2 minutes anyway.

⚠️ Removing one of the four **camp-mirrored** roles (Maze Wizard, Money
Tree, Clicker, Paper Crane) only sticks until the next sync pass, because
those are re-asserted from the student's profile on the website. The bot
warns you about this when you create such a chest. Take the role off
their camp profile too if it should stay off.

**The rules, by default:** anyone who knows the passcode can open a chest,
there's no limit on how many different people open it, and **each person
can only open a given chest once**. A repeat open is a no-op — it re-shows
the reveal text but grants no second role and, importantly, pays no second
reward. Set *Max openers* to make a chest first-come-first-served.

**Points** land in the camp account linked to that Discord user and show
up in the transaction log as `🗝 Chest unlocked`. Two cases where the role
is still granted but the points aren't:

- **Not verified** — no camp account to pay into. They're told to verify.
- **Frozen** — payment not confirmed, so the account can't be credited.

**Reveal text length.** The form takes 4000 characters, which is roughly
700 words. The bot splits anything past Discord's 4096-character embed
limit across continuation embeds when it posts the chest, and across
multiple ephemeral follow-ups when someone opens it. Splits prefer
paragraph then line boundaries, so text never breaks mid-sentence.

Both `/unlock code:<passcode>` and the button on the chest message run the
same code path, so they behave identically.

**Deleting a chest.** `/chest-delete` gives you a dropdown of every chest
in the server, **newest first**, each shown as
`CODE → Role · 4/10 opens · Aug 3, 1:12 AM`. Start typing to filter by
code, role name, or description text. You never need to handle a chest ID
— `/chest-list` still prints them, but only as a fallback.

Deleting a chest removes the record, not the message. The posted embed
stays in the channel and its button will report that the code doesn't
open anything; delete the message yourself if you want it gone.

## 5c-ii) Points, participation, and attendance

All three move points through the **same ledger** as everything else, so
every change is auditable on the admin Transactions page, and all three
are staff-gated (Administrator, the **Staff** role, or a role granted the
matching command via `/perms-grant`).

**Awards** — `/award student points reason`. The reason is required and is
written onto the transaction; a negative amount deducts. Nobody goes below
zero: a deduction larger than the balance takes what's there and the
ledger notes the shortfall. The camper gets a DM with the amount, the
reason, and their new balance.

**Hand raises** — `/handraise student [times]`. This is not new machinery:
it bumps the **Hand Raised base stat**, which carries `pointsPerUnit: 2`,
so each raise is worth 2 points automatically. Change the value on the
Base Stats admin page and the command follows it. A negative `times`
takes raises back, which is how you deal with anyone gaming it.

**Attendance** — `/attendance student status [date]`, or the
**Attendance** page in the admin nav for marking a whole room quickly.

| Status | Points |
| --- | --- |
| Present | **+250** |
| Late | **−50** |
| Absent | 0 |

One record per camper per day. Re-marking a day **reverses the previous
mark's points first**, using what was actually applied rather than the
nominal value — so correcting a mistake never double-charges anyone, and a
deduction that got clipped by an empty balance is undone by the same
clipped amount. Marking the same status twice is a no-op. Dates are
camp-local (America/Toronto), so an evening session doesn't roll onto
tomorrow.

## 5d) Locking parameters per role

`/perms-grant` decides *who can run* a command. Parameter locks decide
*what they're allowed to put in it*. A lock pins one parameter of one
command to a fixed value for holders of one role — they can still run the
command, but that field is decided for them.

```
/perms-lock command:chest-create role:@Counsellor field:points value:50
```

Counsellors can now place chests, but every chest they place awards
exactly 50 points no matter what they type. The field shows up pre-filled
and relabelled *"(locked by your role)"*, and anything they change is
discarded on submit.

**Any command, any parameter.** The lockable fields are read off the live
command tree, so every command and every one of its parameters can be
locked — and a renamed parameter can't leave a stale rule behind. Both
`command` and `field` autocomplete from what actually exists.

- `/perms-lock command role field value` — set a lock
- `/perms-lock command role field` with **`value` left blank** — removes
  the lock, leaving that parameter free for the role to set
- `/perms-unlock command role field` — same thing, explicitly
- `/perms-locks` — show every lock in the server

Because blank means "leave it free", locking max openers to *unlimited*
takes the literal word `unlimited`.

Values are checked against the parameter's real type when you set the
lock, so you can't pin an integer parameter to `abc`, a choice parameter
to a value that isn't offered, or a role parameter to a role that doesn't
exist. File-upload parameters can't be locked — there's no fixed value to
pin them to — and `/perms-lock` says so rather than storing a dead rule.

All three are **server owner / Administrator only**, and admins are never
subject to locks themselves — they're the only ones who can change them,
so enforcing them there would just be a way to lock yourself out.

If a member has several roles that lock the same field, the **highest
role wins**, matching Discord's own hierarchy intuition.

**How enforcement works.** Locked slash parameters are rewritten in the
argument namespace *before* the command function is called, so a command
sees only the locked value and can't be written to bypass it — no
per-command code needed. This hooks a private discord.py method
(`Command._invoke_with_namespace`), because locks have to be applied after
the argument namespace exists and before arguments are unpacked, and
`interaction_check` runs too early. It's wrapped in a catch-all: a broken
lock logs and is skipped, never blocking a command. Worth re-testing after
a discord.py major upgrade.

Fields collected in a **modal** rather than as a slash option (the chest
form's `points` and `maxClaims`) aren't in that namespace, so they're
declared in `EXTRA_LOCKABLE_FIELDS` and enforced by the command itself,
which re-fetches the lock at submit time.

## 5e) Homework hand-in and marking

Campers hand work in with `/submit`; it lands in a staff-only marking
channel; staff press a button to write feedback; the bot DMs the feedback
to the camper.

**Camper side:**

```
/submit title:"Day 3 problem set" file:<photo> [file2] [file3] [notes]
```

Only **verified** campers can submit — the whole thing keys off the linked
camp account, which is where the real name comes from and who the feedback
gets sent to. An unverified user is told to verify first.

**Staff side.** Each submission posts a card into the marking channel with
the camper's real name, their Discord mention, the hand-in time (absolute
*and* relative, in each reader's own timezone), their note, and the files
themselves. Press **Mark & send feedback 📝**, fill in a grade (optional)
and feedback (required), and the bot DMs it to the camper immediately.

The card then re-renders green with the grade, the marker's name, the
feedback, and **where the feedback actually went**. You can press the
button again to re-mark; the card replaces the previous result rather than
stacking a second one.

**If the camper's DMs are closed**, the bot falls back to pinging them
with the feedback in the channel they ran `/submit` in. The card shows
📢 *DMs closed — posted in #channel* so you know it happened.

> ⚠️ The fallback makes that camper's grade and feedback **visible to
> everyone who can see that channel**. That's the trade for reaching
> someone who can't be DMed. If you'd rather it stay private, either tell
> campers to submit in a channel only they and staff can see, or ask for
> the fallback to be changed to a bare "come see a staff member" ping.

If the bot can't post there either (no permission, or an older submission
from before the origin channel was recorded), the card shows ⚠️ *Not
delivered* and you'll need to pass it on yourself.

`/submissions` lists what's outstanding, newest first, with a jump link to
each card. It defaults to unmarked work; `show:` switches to marked or
everything.

**Who can mark:** server Administrators, anyone holding **Staff**, or a
role granted `mark-homework` via `/perms-grant`.

**Files.** The bot re-uploads submitted files onto the marking-channel
message rather than linking to them. Discord's own attachment URLs are
short-lived signed links, so linking would leave staff with dead files a
day later. Cap is `MAX_UPLOAD_BYTES` (25 MB, Discord's non-boosted limit);
anything larger is refused up front with a clear message. Up to 3 files
per submission.

**Storage and privacy.** `homework_submissions` records the submission and
is the audit trail; the files live on the Discord message. The camper's
name is deliberately **not** duplicated into this table — it stores
`studentId` and resolves the name from `students`, where it's already
encrypted at rest. The student's note and the feedback are free text
written about a minor, so both are encrypted with the same
`HIGHERGRADE_ENC_KEY` as the rest of the PII.

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

- `Student` — granted on `/verify` or the Verify panel, removed on
  `/unlink` or when the camp account is frozen.
- `Staff` — created so `/gate` can grant it channel access, but **never
  assigned or removed by the bot**. Hand it out yourself.
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

### Running in more than one server

The bot is in both the camp server and the staff server, on purpose and
with different permissions in each. The staff server only grants View
Channel + Send Messages, because the only thing the bot does there is
post (`REVIEW_CHANNEL_ID` — the email-review channel — lives there).

Every background task **checks it has the permissions for that specific
job before attempting it**, and skips just that job where it doesn't:

| Task | Needs | Where it's skipped |
| --- | --- | --- |
| Role sync | Manage Roles | staff server |
| Nickname sync | Manage Nicknames | staff server |
| Announcements / pings | View Channel + Send Messages *per channel* | anywhere it can't speak |

So role syncing is skipped in the staff server while its posting tasks
keep running normally. Skips are logged **once per process**, not once
per loop — a permanent, intentional permission gap shouldn't produce a
warning every two minutes and bury real problems.

Missing **Mention Everyone** is treated separately: the announcement
still posts, it just won't ping, so it warns rather than skipping.

If you *want* the bot managing roles in a second server, grant it Manage
Roles there — it'll start creating `Student`, `Staff`, and the camp-game
roles on the next poll, and renaming members to their camp names.
