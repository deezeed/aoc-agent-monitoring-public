# AOC — Agent Operations Center

A local, single-file monitoring dashboard for Claude Code CLI sessions and
the subagents ("agents") they spawn. Tracks live status, tokens, cost,
files changed, session history, and server-side notifications (native
Windows toasts, webhooks) — all from one Python process, no external
services required. Can also merge in the same view from other AOC
instances on your LAN (Settings → Remote Machines), so running it on
more than one machine doesn't mean more than one dashboard.

Dashboard: **http://localhost:5151**

Installable as a PWA (`/manifest.json`, an SVG icon, a minimal no-op
service worker — just enough for installability, no offline caching,
since a stale cached snapshot of a live dashboard would be actively
misleading). Works the same way through a Cloudflare tunnel or a Remote
Machine's LAN address: the manifest/icon URLs carry the same `?token=`
the page itself needed, so an installed home-screen icon stays
authenticated instead of 401ing the moment you open it.

## What it actually is

- `monitor.py` — the whole app. A single `ThreadingHTTPServer` process
  serving both the HTTP API and an embedded HTML/CSS/JS dashboard (no
  separate frontend build step).
- `watchdog.py` — a supervisor process that keeps `monitor.py` alive,
  restarting it (and firing a native toast) if it stops responding.
- `sentinel.py` — the watchdog's own watchdog. See **Two-tier watchdog**
  below.
- `setup.py` — one-command setup: deploys the hook scripts, wires
  `~/.claude/settings.json`, registers the two Task Scheduler tasks.
  Supports `--dry-run`.
- `install.ps1` — bootstrap script for setting AOC up on a second machine
  (clone/pull + `setup.py`, with a confirm step).
- `hooks/aoc_hook.py` + `hooks/run_hook.pyw` — the templates `setup.py`
  deploys to `~/.claude/hooks/`. The **deployed copies live outside this
  repo** (under your Claude Code hooks directory) — editing the repo
  copies doesn't affect an already-deployed hook, re-run `setup.py` to
  redeploy. This is how Claude Code tells AOC what's happening (session
  heartbeats, agent start/done/error).

AOC also has a fallback that doesn't depend on the hook at all: a
background thread tails each session's own Claude Code transcript JSONL
file and reconstructs agent activity directly from it. This exists because
Claude Code's hook dispatch has been observed to occasionally silently
drop a `PreToolUse` event — when that happens, the transcript scanner
still catches the agent. Cards created this way are marked with a
"⚠ HOOK MISS" badge so you can see how often it's actually happening
(also surfaced as an aggregate count in **History → Analytics**).

## Architecture

Everything above is one process, one file. There's no build step, no
frontend framework, no ORM — `monitor.py` is a `ThreadingHTTPServer`
subclass whose `Handler` class serves both the JSON API and a single
`HTML = r"""..."""` triple-quoted string holding the entire dashboard
(CSS in a `<style>` block, then vanilla JS in a `<script>` block —
everything from the theme system to the SVG dependency graph is plain
DOM manipulation, no React/Vue/build tooling of any kind). The Python
half above that string holds the DB layer, the hook-facing `/update`
endpoint, ~12 background daemon threads, and the webhook/notification
logic. `do_POST`/`do_GET` are thin dispatchers — each route with more
than a handful of lines of real logic (`/update`, `/reset`,
`/kill_session`, `/diff`, `/git`, ...) is its own named `_post_x`/`_get_x`
method, not inlined in the `if self.path == ...` chain; grep for the
route string to find the method, then grep the method name. Finding
anything else in the file is a `grep` for a function or section-comment
name, not a line number — the file grows by roughly a feature's worth of
lines every session, so line numbers in this README would go stale
within days.

**Live state.** There's exactly one in-memory `status` dict (the same
shape `/status` returns), guarded by `_status_lock` and persisted to
`%LOCALAPPDATA%\AOC\monitor-status.json` on every write via
`_save_status`/`_load_status`. A monotonic `_status_version` counter plus
a `_status_cond` condition variable is how the WebSocket push loop
(`_handle_events_ws`, serving `/events`) knows when to actually push a
new payload instead of polling — any code path that mutates `status`
bumps the version and notifies the condition, and every connected
WebSocket client wakes up, rebuilds the payload via
`_build_status_payload` (which has its own short-TTL cache — see
**Troubleshooting** below for why), and sends it. This is also why a
closed CLI session or a config change shows up in the dashboard within
about a second without a page reload: nothing in the frontend ever polls
on a timer for the main view, it just waits on this same WebSocket.

**Background workers.** Roughly a dozen daemon threads run for the
lifetime of the process, each on its own independent interval —
autosave (5 min), the dead man's snitch ping (10 min), backup rotation
(hourly), the GitHub PR-link check (5 min, via `gh`), the self-update
`git fetch` check (15 min), the weekly/daily digest, quiet-hours-aware
webhook + native-toast delivery (`stuck`/`burn spike`/`cost spike`/
`waiting too long`/`budget exceeded` detection — runs regardless of
whether a webhook URL is configured, so these five still surface as a
toast in `--headless` mode with no webhook set up and no browser tab
open), agent
auto-retention cleanup, a `claude.exe` process-count
poll (2s, existed because a naive 10s poll stacked with the WebSocket
heartbeat could take ~12s to notice a closed CLI), and the
transcript-scanner hook-miss fallback described above. None of them
share state directly — they all go through the same `status` dict under
the same lock, so a worker deciding to fire a webhook and a hook POSTing
`/update` at the same instant can never race each other.

**Two different datastores, two different lifetimes.** The live
`status` dict above is ephemeral and reflects only what's currently
running or was dismissed in the last 30 minutes. `history.db` (SQLite,
3 tables — `sessions`, `agents`, `file_changes`) is the durable,
queryable record everything in **History** reads from — written on
`/reset`, every 5-minute autosave tick, or an explicit **⊕ SAVE NOW**,
never on every single `/update` call (that would mean a DB write per
hook invocation, i.e. per subagent start/stop). `agents` rows use
`INSERT ... ON CONFLICT(session_id, agent_id) DO UPDATE` rather than
plain insert-or-replace specifically so a re-running autosave refreshes
an in-progress agent's row in place without resetting its `rowid_`
(which the session-detail view's `ORDER BY` depends on) or letting a
`COALESCE`-guarded column (`model`, `subagent_type`, `detected_via`,
etc.) that was already captured get overwritten with a later, blanker
value. The autosave `sid` that upsert is keyed against survives a
watchdog (or manual) restart mid-session too — it's mirrored to a small
`aoc_autosave_id.txt` marker file and resumed on the next tick, rather
than a restart forgetting the in-memory id and forking one still-live
session's agents across two separate `history.db` rows.

**Hook protocol.** `aoc_hook.py` (the deployed copy, templated by
`setup.py`) only ever POSTs to one endpoint, `/update`, and only cares
about four hook event names: `UserPromptSubmit` and `Stop` are the CLI
session heartbeat (they carry `session_id`/`cwd`/`project`/
`display_name` and flip `waiting_on_you` — `Stop` means Claude just
handed control back to you, `UserPromptSubmit` means you just replied);
`PreToolUse`/`PostToolUse` matched against `tool_name == "Agent"` are
the actual subagent lifecycle (`Pre` registers a `running` card parsed
out of the agent's own task description, `Post` marks it `done`/`error`
and — when the tool's own response happens to carry `usage` +
`resolvedModel` — attaches real per-invocation token/cost data, `model`,
`subagent_type`, `tool_use_count`, and `files_changed` (parsed from that
specific subagent's own transcript file, `<session>/subagents/
agent-<agentId>.jsonl` — not from the tool response, which only ever
carries aggregate stats, never file paths). Every hook invocation
— including ones that get filtered out early — writes one line to
`~/.claude/hooks/aoc_hook_debug.log` before anything else runs, which is
the actual ground truth for "did the hook fire at all" (see
**Troubleshooting**).

**API reference.** Every endpoint requires the same auth
(`_check_auth` — localhost always allowed unless a tunnel is active,
otherwise `?token=`/`X-AOC-Token` — see **Security model** below).

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | The dashboard itself (`HTML`, token injected) |
| GET | `/status` | Full live state — what the WebSocket push also sends |
| GET | `/events` | WebSocket upgrade — live `/status` push, see above |
| GET | `/metrics` | The same live numbers as Prometheus text exposition |
| GET | `/history` | Last 100 saved sessions (summary rows) |
| GET | `/history/search?q=&from=&to=` | Full `history.db` search, no 100-row cap |
| GET | `/history/<session_id>` | One session's full per-agent detail + files |
| GET | `/history/save_current` | Force-save the live session to `history.db` now |
| GET | `/analytics` | Aggregates behind History → Analytics (by day/project/model/...) |
| GET | `/export_costs.csv`, `/export_costs_by_model.csv`, `/export_costs_by_subagent_type.csv` | CSV cost reports (`?from=&to=`) |
| GET | `/errors` | Persistent cross-session error list (backs the ERRORS panel) |
| GET | `/diag` | Self-diagnostics — uptime, threads, memory, DB/backup size |
| GET | `/backups` | Backup file list |
| GET | `/auditlog` | Tail of the audit log |
| GET | `/diff?file=` | Git diff (or last-commit patch, or full content) for one file |
| GET | `/git?cwd=` | Current branch + uncommitted-change count for a directory |
| GET | `/webhook_settings`, `/notify_settings` | Current settings (also POST, below) |
| GET | `/terminal/ws` | WebSocket upgrade for the embedded terminal grid |
| GET | `/logs/<name>.log` | Download one log file |
| POST | `/update` | The hook's own endpoint — heartbeat or agent upsert, see above |
| POST | `/remove`, `/clear_done` | Dismiss one agent / sweep all done agents |
| POST | `/notes`, `/session_note` | Global session note / one CLI session's own note |
| POST | `/history_tags` | Set an already-saved History session's tags (replaces the full set) |
| POST | `/webhook_settings`, `/notify_settings` | Save settings |
| POST | `/webhook_test`, `/webhook_fire` | Test-fire a webhook / fire a real event |
| POST | `/restore_backup` | Restore `history.db` from a backup (safety-copies current first) |
| POST | `/tunnel/start`, `/tunnel/stop`, `/tunnel/ngrok_authtoken` | Cloudflare/ngrok tunnel control |
| POST | `/reset` | Snapshot current session to `history.db`, then clear the live view |
| POST | `/kill_session` | Force Stop — see the dedicated section below |
| POST | `/snitch_ping_now` | Immediate dead man's snitch ping |
| POST | `/auth/token` | Rotate the auth token |
| DELETE | `/auth/token` | Delete the auth token |
| DELETE | `/session/<id>` | Dismiss a closed CLI session |

## Requirements

- Windows (the process-management, tray, and notification code all use
  Windows-specific APIs — this isn't a cross-platform tool)
- Python 3.9 (this install uses
  `C:\Users\marek\AppData\Local\Programs\Python\Python39\`, referenced by
  full path in a few places — `watchdog.py`'s `PYTHONW`, the hook's
  configured command)
- `psutil` (for the `/diag` self-diagnostics view's memory figure — everything
  else degrades gracefully without it, this one field just shows `—`)

## Setup

### 1. One command
`python setup.py` does the whole thing: deploys `hooks/aoc_hook.py` +
`hooks/run_hook.pyw` (filling in this machine's Python/repo paths) to
`~/.claude/hooks/`, adds the 4 required hook entries to
`~/.claude/settings.json` (without touching anything else already there —
idempotent, safe to re-run), and registers both Task Scheduler tasks
(watchdog + sentinel, below). Run `python setup.py --dry-run` first if
you want to see exactly what it would change before it changes anything —
no files written, no tasks created/modified, only read-only
`schtasks /Query` calls to report whether a task already exists.

Setting up a **second machine**: `install.ps1` clones/pulls the repo,
installs `psutil`, runs `setup.py --dry-run` so you see the plan, then
asks to confirm before applying. Against the public release repo this is
a plain `git clone` — no credentials needed. Pointed at a private
checkout instead (e.g. pairing a second machine you own to this dev
repo), pass `-InstallDir` and it works the same way, just still needs
your own git credentials for that clone.

### 2. The hook (what `setup.py` wires up)
`~/.claude/settings.json` needs `UserPromptSubmit`, `Stop`, and
`PreToolUse`/`PostToolUse` (matcher `Agent`) hooks pointing at
`pythonw.exe ~/.claude/hooks/run_hook.pyw`, which reads the hook JSON off
stdin, writes it to a temp file, and invokes `aoc_hook.py --file <path>`
via a real (windowed, no-console) `pythonw.exe` subprocess — this
stdin-to-tempfile indirection exists because pythonw.exe's stdin handling
from Claude Code's hook invocation was unreliable directly.

### 3. Two-tier watchdog
`watchdog.py` runs continuously via a **Windows Task Scheduler task set
to trigger at login** (not a service) — no elevation, runs as the
logged-in user. It polls `/status` every 30s and restarts
`monitor.py --headless` after 2 consecutive failures, with exponential
backoff on repeated restart failures. Task Scheduler's own
"restart on failure" is also configured (3 attempts, 1 min apart) for if
`watchdog.py`'s *process* exits — but that only fires 3 times and never
catches a hang. `sentinel.py` is the second tier: its own periodic Task
Scheduler task (every 15 min, not just at login) just tries to relaunch
`watchdog.py` every time — cheap and safe when it's already alive
(`watchdog.py`'s own lock file makes the new attempt exit in well under a
second), and it's what actually revives a hung or fully-given-up
`watchdog.py`. Toasts and `sentinel.log` (next to `sentinel.py`) only get
written when it actually had to do something, not on every no-op tick.

### 4. Manual/alternate launch
`start_monitor.vbs` launches `monitor.py --app` directly (system tray icon
+ a native pywebview window) — a manual alternative to the headless
watchdog-supervised mode, useful for a visible desktop-app-style session
instead of "just open the URL in a browser." The two modes don't share a
notification path: `--app` mode uses `pystray`'s native tray notifications,
`--headless` mode (what the watchdog actually runs) uses the WinRT toast
path described below. Only one instance can hold port 5151 at a time
(a startup guard checks `/status` and refuses to bind if something's
already listening there), so running both isn't useful.

### 5. Native notifications (automatic, no setup)
On first `--headless`/CLI-mode startup, AOC registers its own
AppUserModelID via a Start Menu shortcut (COM interop, since
`WshShell.CreateShortcut` can't set that property directly) so toast
notifications show AOC's own name/icon. If that registration ever fails
for any reason, it falls back to borrowing `powershell.exe`'s own
already-registered AUMID rather than going silent — you'll just get a
generically-branded toast instead of a broken one.

## Data locations

Two different directories, for a reason:

- **`%LOCALAPPDATA%\AOC\`** — `monitor-status.json`, `history.db`,
  `logs/session_*.log`, `logs/kill_audit.log` (every `Force Stop` call,
  successful or refused — see **Using the dashboard** below), and
  `logs/alert_audit.log` (every webhook event AOC actually dispatched —
  `done`/`error`/`stuck`/`burn_spike`/`cost_spike`/`waiting_nudge`/
  `weekly_digest`/`budget_alert` — one JSON line per fire, written from `_fire_webhook`
  itself so both the server-side worker and the client-triggered
  `/webhook_fire` path are covered by the same single write site. The
  in-browser notification history panel is a plain in-memory array that
  resets on every reload; this is the only durable record of whether an
  alert actually fired), and `logs/background_errors.log` (every
  background/maintenance worker's outermost `except:pass` now writes here
  via `_log_bg_error` instead of failing completely silently — these
  threads all run headless under `pythonw.exe` with no console, so a
  disk-full/permissions/DB-lock failure previously had zero visibility.
  Throttled per call site to once every 5 minutes for the same message,
  so a persistently offline machine or a stuck lock doesn't flood the
  file). Hot,
  constantly-written data. Deliberately kept **outside** OneDrive sync:
  OneDrive can transiently lock a file mid-upload, and since every
  dashboard request used to touch these files synchronously, a single
  stuck lock could freeze the whole server long enough for the watchdog
  to kill and restart it — silently dropping any hook update that landed
  in that window. This was a real, previously-unexplained source of
  "agents randomly vanish from the dashboard."
- **This repo's own folder** (wherever you cloned it, e.g. under OneDrive)
  — the code itself, `aoc_token.txt` (remote-access auth token),
  `aoc_webhook_settings.json` (server-persisted webhook config),
  `aoc_notify_settings.json` (quiet hours, muted projects, dead man's
  snitch URL, digest cadence — see below), `aoc_last_digest.txt` (a
  single ISO-week or calendar-date marker, so a watchdog restart mid-week
  can't cause the digest to double-send), `sentinel.log` (next to
  `sentinel.py`),
  `cloudflared.exe` (downloaded on first tunnel use), and **`backups/`**
  — since `history.db` no longer lives somewhere OneDrive-synced, an hourly
  background job copies a verified-consistent snapshot (SQLite's own
  `.backup()` API, integrity-checked with `PRAGMA integrity_check` after
  every copy) back into `backups/history_YYYY-MM-DD.db` here, so it still
  gets the automatic offsite backup that living in OneDrive used to provide
  for free. A backup that fails its integrity check is renamed to
  `..._CORRUPT.db` and kept (not silently deleted) alongside a toast alert.
  Restorable from the **DIAG** view (below) — restoring always takes a
  safety copy of whatever's currently live first, so it's itself
  reversible. The retention window (default 14 days) is configurable in
  Settings → **INFRA**, alongside a live count/size summary of what's
  currently in `backups/`.

## Using the dashboard

Views (toolbar buttons, each with a single-letter hotkey once the page has
focus): **A**gents(**C**), **C**LI(**B**), **T**imeline, **S**ummary,
**G**raph, heat(**X**), health(**K**), tree(**W**), history(**V**),
term(**M**). AGENTS and CLI used to be a sub-tab toggle inside one CARDS
view; they're now two top-level views in their own right, each still
backed by the same card-rendering code (agent cards vs. CLI session cards)
that already existed. `R` resets the current session, `F` focuses the
agent search box, `L` toggles list/grid, `Z` fullscreen, `H` toggles the
theme, `Ctrl+K` opens the **command palette** — type to jump straight to
any session, agent, settings tab, or view, faster than clicking through
tabs once there's more than a handful of sessions running.

The settings/audit/right-panel/agent-detail tab groups are real
`role="tab"` controls now, not plain `<div onclick>`s — reachable with
`Tab`, activated with `Enter`/`Space`, roving-focus with `←`/`→` like any
ARIA tablist. `Escape` closes every overlay including Settings and Notes
(both had working open/close state already, they just weren't wired into
the Escape-priority handler every other overlay used). The three inputs
that had `outline:none` with no replacement focus style now get one, and
every modal announces `role="dialog"`.

A **right-hand panel** sits alongside every view (`P` collapses/expands
it) with its own **LOG** / **FILES** (`I`) / **AUDIT** (`U`) / **ERRORS**
sub-tabs: LOG is the running text log this whole session has produced,
FILES lists every file any agent has touched this session (with a
NEW/MOD badge, clickable through to a diff), AUDIT mirrors the same
content as the standalone audit log, and ERRORS is a persistent list of
every agent error across **all** sessions (not just the current one —
the same per-agent error box on a card disappears ~15s after the agent
errors, this is the durable version). Clicking an ERRORS row jumps
straight into that error's session in **History** (below) for full
context, instead of only ever showing the bare error text in isolation.
ERRORS also has its own search box now (case-insensitive substring match
across the error text, agent name, and project) — the one panel in the
app that never had text search, unlike the agent cards' own.

Each CLI session card has its own row of actions: 📝 add/edit a
per-session note (shown as a preview snippet on the card once set), ⇩
export that whole session — every agent it ran, plus its note and cost —
as one Markdown report (copied to the clipboard, reusing the same
per-agent formatting the single-agent export already had), ⊞ pick two
sessions to open **Session Compare** (cost/duration/tokens/agents/tasks
side by side, plus a **Models** row listing the distinct models across
that session's own agents — a session can mix models (an orchestrator on
one, subagents on another), so this is a set, not the single value
Agent Compare's own Model row shows — the session-level counterpart to
the existing per-agent **Agent Compare**; its Tokens row shows the same
Input/Output/Cache read/Cache write breakdown on hover the session card
itself has, and its branch line links to the open PR the same way the
card's own branch label does), ⟲ on a closed session copies
`claude --resume <id>` to the clipboard, and ⛔ Force Stop on an active
one. A session that's
finished its turn and is waiting on you shows **WAITING** with how long
it's been waiting (`WAITING · 2h 15m`) — past 2 hours this also fires
the `waiting_nudge` webhook/sound event, so a forgotten session actually
gets surfaced instead of sitting there silently. A session whose recent
token burn rate spikes well above its own average shows a **BURN SPIKE**
badge (and the matching webhook/sound event) — catches a runaway loop or
retry storm. Once there's more than one closed/idle session, a **SWEEP**
button appears in the CLI view to bulk-dismiss all of them at once
instead of clicking ✕ on each. If a session's tracked git branch has an
open GitHub PR (via the `gh` CLI, checked per-branch every 5 min), the
branch name on its card links straight to it.

If this machine's own checkout of AOC falls behind `origin/master`, a
small badge appears next to the tunnel badge (checked every 15 min via a
background `git fetch`) — a plain reminder to `git pull`, nothing
automatic.

A failed (`✗`) agent card gets a **copy context** button alongside the
existing bare-error-message copy — bundles project, task description,
subtask progress, the error, and the last 10 log lines into one
paste-ready block, instead of only the raw error text.

A **TOP TODAY** strip above the KPI bar shows the top 3 projects by cost
so far today, without needing to open History → Analytics — it reuses
the same `/analytics` fetch the KPI bar's own numbers already refresh
every 30s from, no extra request. The same strip also leads with a live
**7-day cost delta** (`▲12% vs prior wk` / `▼8% vs prior wk`) — the exact
last-7-vs-preceding-7-days comparison the weekly digest already computes
(`_build_digest_summary`), just computed client-side off the same
already-fetched data and shown continuously instead of only once a week
in a toast/webhook.

Completed/errored subagents (the entries TIMELINE/SUMMARY/GRAPH/HEAT/TREE
all visualize) auto-clear after a configurable window (Settings →
**INFRA**, default 12h, capped under 24h — see below) instead of only
ever shrinking via a manual **CLEAR DONE** sweep. If one of those 5 views
looks empty, it's because nothing's been dispatched via the Task/Agent
tool recently — they track subagent orchestration specifically, not
plain CLI session activity, and repopulate the moment a new subagent
runs.

The **TREE** view draws actual orchestrator→subagent hierarchy — when a
subagent itself spawns a child subagent (nested delegation), the child
renders under its real parent instead of everything showing up as an
unrelated flat root. This works by hashing a nested `tool_use` block's
own id the same way AOC hashes any Agent-tool call to get that agent's
id (Claude Code fires an independent hook chain for a subagent-spawned
subagent exactly like it does for a top-level session's own Agent calls)
— no change needed on Claude Code's side. A session with no nested
delegation at all still shows a flat "no nested subagent delegation in
this session" hint, which is the correct, expected state for a session
where nothing delegated further, not a broken one. Agent detail panel,
Markdown export, and History Detail all show a `↳ <parent name>`
indicator too, wherever an agent has one.

The **HEALTH** view (was called DIAG until it turned out that name gave
no hint it's about AOC itself, not your Claude Code sessions) shows AOC's
own health in two grouped sections — **PROCESS** (uptime, thread count,
memory RSS) and **DATABASE** (`history.db` size, backup count plus the
full backup list with a **RESTORE** button per entry). Restoring always
takes a safety copy of whatever's currently live first (it's itself
reversible) and refuses anything already marked `_CORRUPT`. The `/diag`
endpoint name is unchanged — only the view's label and layout are new.

Settings → **INFRA** reports whether `watchdog.py` is actually alive
(PID-checked, not just "the log file exists") and how many times it's
had to restart `monitor.py` recently, plus how long ago `sentinel.py`
last ran — turns "check watchdog.log/sentinel.log manually" into a
glance. A red topbar badge appears automatically if either looks
unhealthy. It also reports **hook reliability** — what fraction of agent
starts were only ever caught by the transcript-scanner fallback instead
of Claude Code's own `PreToolUse` hook, and whether that correlates with
more sessions running concurrently (previously only visible buried
inside History → Analytics) — plus the backup retention window and the
agent auto-clear window described above. **Alerts fired (7d)** and
**Force stops (7d)** read back `logs/alert_audit.log` and
`logs/kill_audit.log` respectively (both write-only until now — the only
way to know either had ever fired was opening the log file by hand),
grouped by event type/outcome so the counts are meaningful at a glance
instead of just a raw total.

**Force Stop** (⛔, on an active CLI session's card) kills that session's
actual `claude.exe` process directly. This is destructive and gated behind
a confirmation dialog — there is no undo. It only appears once AOC has
learned that session's OS process ID from a hook heartbeat, which can take
one turn after the session starts, and only ever for a session on **this**
machine (never a merged-in remote one — see below). Every call, successful
or refused, is appended to `logs/kill_audit.log`.

**Remote Machines** (Settings → Remote Machines) merges other AOC
instances on your LAN into this same dashboard, live view and
History/Analytics both. Add a name, that machine's `http://ip:5151` (or
tunnel URL), and its own auth token (it needs one generated even for pure
LAN access — non-localhost requests are always rejected without one).
Read-only for now: Force Stop and Dismiss only ever act on this machine's
own sessions, never a remote one's.

Settings → **COST** covers the cost rate, a global per-session budget
(shows a red KPI once a live session's own cost crosses it — a
tab-open-only visual cue, no alert fired), **per-project monthly
budgets** (JSON object, project name → $ limit for that calendar month —
no fallback to the global budget above; unlisted projects simply have no
monthly limit configured), and a **projected month-end spend** figure
(simple run-rate: cost so far ÷ days elapsed × days remaining — the same
number History → Analytics already computes, just also surfaced here).
Each configured project budget also gets its own line right under the
JSON textarea — cost so far this month vs. that project's own limit, plus
its own run-rate projection, colored amber near the limit and red once
the projection is set to exceed it. Persisted server-side (alongside
quiet hours/muted projects, below) rather than only in the browser, so
the actual alert — a native toast plus an optional `budget_alert` webhook
event (Settings → **NOTIFY**) — fires once per project per calendar month
the moment month-to-date spend crosses the configured limit, from the
same background worker that delivers every other webhook/toast event,
whether or not a browser tab is open at all.

The **history**(**V**) view has its own **SESSIONS** / **ANALYTICS**
sub-tabs. SESSIONS lists every session ever saved (autosaved every 5 min
plus on reset, or on demand via **⊕ SAVE NOW**), grouped by date, each
row showing done/agent counts, task completion, error count, cost, and
tokens at a glance — filterable by project/session-id substring and an
optional date range (`/history/search`, reaching every session in
`history.db`, not just the 100 most recent `/history` alone returns),
and merging in sessions from any configured **Remote Machine** the same
way the live view does. Clicking a row opens that session's own detail
view: every agent it ran (status, model, subagent type, tool call count,
cost, duration, task-completion chip, the `⚠ HOOK MISS` badge when it
applies) and every file any of them changed — the historical,
read-after-the-fact counterpart to the live agent detail panel, backed
by the same `model`/`subagent_type`/`tool_use_count`/`detected_via`/
`concurrent_sessions` columns persisted in `history.db`. The session
detail header also shows which **Claude Code version** ran that session
(`v2.x.x`), read from `sessions.cc_version` — CC version is tracked live
per-CLI-session (the transcript-scanner fallback backfills it from the
transcript's own metadata, and a first-time-seen version fires a toast so
a Claude Code update silently changing the transcript/hook format this
app depends on at least gets flagged), but previously wasn't persisted:
Session Compare and the Markdown export could only ever show it for the
*current* session, gone for good the moment a session was saved to
history or reset.

SESSIONS rows also carry a **⚡ OUTLIER** badge when a session's cost ran
3x+ its own project's historical average — the retrospective counterpart
to the live `cost_spike` webhook (below), which only ever fired a
one-time alert while the session was still running and left no lasting
record once it closed. And each session detail view has its own **tags**
(a small chip row under the header, `+ tag` to add, click a tag's ✕ to
remove) — organizing History beyond project name, persisted straight to
that saved row (`POST /history_tags`) rather than going through the live
session-note mechanism, since tagging is inherently something you do
after the fact. The existing History search box already reaches them for
free (`/history/search`'s query also matches against `tags`).

Settings → **NOTIFY** covers **quiet hours** and **muted projects**
(suppress sound/toast/webhook, either on a schedule or per-project
outright), a **dead man's snitch URL** (below), a **digest cadence**
(Daily / Weekly / Off — a native-toast-plus-optional-webhook summary of
the last 7 days' sessions/cost/errors with a week-over-week cost trend;
calendar-based scheduling, not a rolling timer, so a watchdog restart
can't cause a double-send or a skipped period), and per-event webhook
toggles: `done`, `error`, `stuck` (no task progress for 5+ min), `burn
spike` (token rate well above a session's own average), `cost spike`
(a session's *total* cost well above its project's own historical
per-session average — distinct from `burn spike`'s token-rate check,
catches an expensive session that never burned tokens fast enough to
trip that one), `weekly digest`, `waiting too long` (2+ hours in the
WAITING state), and `budget exceeded` (a project's month-to-date spend
crossed its configured monthly limit — see **per-project monthly
budgets** above). Webhook delivery happens two ways: client-side (needs a
browser tab open, fires instantly) and server-side (background workers
poll status independently — this is what makes webhooks, including
`stuck`/`burn spike`/`cost spike`/`waiting too long`/`budget exceeded`
detection, actually arrive when no dashboard tab is open at all). Those
same five also fire a native toast unconditionally (quiet hours/muted
projects aside) — independent of the webhook toggles above and of
whether a webhook URL is even configured, so they still surface in
`--headless` mode with no webhook set up and no browser tab open, the one
scenario they're most likely to matter in. **Analytics** tab has THIS
MONTH/ALL TIME buttons to download a cost-by-project CSV
(`/export_costs.csv`), with a matching
BY MODEL pair right next to them (`/export_costs_by_model.csv`, joining
`agents` against `sessions` for the date the same way `by_day_model`
does) — COST BY PROJECT had this CSV escape hatch, COST BY MODEL didn't
until now. Analytics' own search
box now reaches every session in `history.db` — project name/session id
plus an optional date range, via `/history/search` — not just the 100
most recent sessions the plain `/history` view alone can see. A **COST BY
MODEL** breakdown sits alongside COST BY PROJECT — since per-model
pricing was already tracked for the cost math itself, this is just that
same `model` field (now persisted to `history.db`, a column added
alongside `detected_via`/`concurrent_sessions`) grouped and summed, so a
Sonnet-vs-Opus-vs-Haiku spend question doesn't need a manual `history.db`
query. Only ever populated going forward — agents saved before this
column existed are simply excluded, not shown as a blank bucket. The
tokens-by-day and cost-by-day trend charts (hoverable, last 30 days) now
have matching **errors-by-day** and **avg-session-duration-by-day** trend
charts alongside them — the daily error count was already summed into
`by_day` for the KPI bar's own use, and per-session `duration_s` had
always been in `sessions` but only ever averaged for *today* — neither
had a historical trend view before. **COST BY PROJECT** rows are
clickable — the small-multiples "top projects" trend below only ever
covers the 3 biggest spenders, but `by_day_project` already has every
project's daily numbers with no cap, so clicking any row (even one
outside the top 3) pins open that project's own cost trend; clicking it
again closes it. **COST BY MODEL** rows got the same treatment once a
`by_day_model` breakdown existed to back it (a join against `sessions`
for the date, since `agents` only ever carried a time-of-day, not a date
of its own) — click Sonnet or Opus to see that model's own cost trend
day by day, e.g. after a mid-month model-preference change. A **HOOK
MISS RATE BY DAY** trend chart (same sessions join, grouped by date
instead of model) sits alongside the others — the INFRA settings tab's
hook-reliability line only ever showed an all-time total, with no way to
see whether the `PreToolUse` hook's reliability is trending better or
worse over time. **COST BY MODEL** rows also carry the same red error
badge **COST BY PROJECT** rows already had — `agents.status` supports
the identical `SUM(CASE WHEN status='error' ...)` aggregation `sessions`
already used for the project version, it just hadn't been added yet.

A **COST BY AGENT TYPE** breakdown sits alongside COST BY MODEL, same
shape (click a type for its own trend, its own CSV export pair) just
grouped by `subagent_type` (`Explore`/`Plan`/`general-purpose`/...)
instead of `model` — that field was already shown per-agent (card badge,
Agent Compare, Markdown export) but never aggregated across history
before. Both COST BY MODEL and COST BY AGENT TYPE rows also show a
**tokens-per-tool-call** figure in their tooltip — `tool_use_count` and
`tokens` were both already summed into those rows for their own stats,
but never divided against each other; a genuinely different signal from
raw token totals, since a model simply used on bigger tasks would win a
raw-tokens comparison either way. A **TOP FILES** section shows the
most-frequently-changed files across all of history (`file_changes` had
every file any agent ever touched, but was only ever queried per-session
before, never aggregated) — click a row for that file's own
changes-over-time trend (scoped to the top 20 hottest files, not every
file ever touched, so this stays a small, bounded query), or the small
⇄ icon to open the same `/diff` viewer the FILES tab already uses. A
**SLOWEST AGENTS** leaderboard ranks the top 20 individual agent runs by
duration — `duration_s` had always been stored per agent but only ever
averaged into today's session-level figure, never surfaced as its own
ranked list; click a row to jump straight to that agent's session in
History. A **COMMON ERRORS** section groups `error_msg` by exact text
match and ranks by occurrence count — a genuinely recurring failure (the
same exception, the same timeout) produces identical text every time, so
this catches "what keeps breaking" even though fuzzy matching isn't
attempted; click a row to jump to its most recent occurrence. All three
sections merge across configured **Remote Machines** the same way every
other breakdown does. **COST BY PROJECT** rows also gained a task
completion percentage in their tooltip, and the tokens-by-day/cost-by-day
trend cluster gained a matching **TASK COMPLETION RATE BY DAY** chart —
`task_done`/`task_total` were already summed per session for the
SESSIONS list's own completion chips, but never divided against each
other into a rate, whether per-project or over time.

**COST BY MODEL** rows now also carry a success-rate figure inline
(`done`/`errors` were already summed per model, just never turned into a
percentage) plus an average-duration figure in the tooltip — answers
"does this model actually finish more tasks, or does it just cost more"
instead of leaving cost as the only signal, the same MODEL PERFORMANCE
question COST BY MODEL didn't have the numbers to answer before. A
**FILE TYPES** section sits below TOP FILES — the same `file_changes`
source, grouped by extension instead of path (`.py` vs `.js` vs `.md`,
etc.) to show which *kind* of work generates the most changes; SQLite has
no clean "text after the last dot" built-in, so this one aggregate groups
in Python instead of SQL, unlike every other breakdown here. The trend
cluster also gained a **TIME CLAUDE WAITED ON YOU BY DAY** chart —
`waiting_on_you`/`waiting_secs` were always live-only (recomputed from
`last_seen_epoch` on every poll, nothing persisted how long a session had
spent waiting across its whole lifetime), so a small state machine now
tracks each session's cumulative waiting time as it happens and folds it
into a new `sessions.waiting_on_you_s` column the next time that session
gets saved.

An agent's `model` (tracked and costed per-model since COST BY MODEL was
added) previously only ever showed up in these historical aggregates —
opening a live agent's own detail panel or exporting it to Markdown gave
no way to tell which model actually ran it. All three now show it: a
small model-name badge next to the cost figure in the agent detail panel
header, a `**Model:**` line in the Markdown export right after
`**Tokens:**`, and a Model row in **Agent Compare**'s STATS
section — arguably the one place it matters most, since "same task, ran
with Sonnet vs. Opus" is exactly the comparison Agent Compare exists for.
The GRAPH/TREE views' own node hover tooltip (`_treeNodeHover` is a thin
wrapper around `_graphNodeHover`, so one change covers both) shows it too.

The same detail panel/export/Compare/tooltip quartet also carries an
agent's **`subagent_type`** (`general-purpose`/`Explore`/`Plan`/... — read
straight off the `Task`/`Agent` tool call's own `subagent_type` input,
previously invisible even though it's the one field that actually tells
two same-named agents apart) and its **`tool_use_count`** (total tool
calls made — a different signal from raw token count: a lot of tokens
with few tool calls behaves very differently from the reverse). Both are
also persisted to `history.db` alongside `model`, so **History**'s
per-session detail view shows them for past agents too, not just live
ones. A compact **`NN% ctx`** badge next to the cost figure (agent
detail panel header, plus a `**Context used:**` line in the Markdown
export) shows how close an agent's own `tokens_used` is to the
200k-token Claude context window — the same number the agent card's own
token progress bar has always drawn from, just not previously shown
anywhere else.

An agent whose start was only ever caught by the transcript-scanner
fallback (the `⚠ HOOK MISS` badge described above) now shows that
warning consistently everywhere its status is visible — the card,
SUMMARY/AGENTS-tab rows, **Agent Compare**, the GRAPH/TREE tooltip, and
**History**'s per-session detail view all read the same
`detected_via`/`concurrent_sessions` fields (the latter persisted to
`history.db` right alongside `model`).

**EXPORT/IMPORT** (bottom of the Settings panel, visible from every tab)
bundles every *configuration* field above — cost rate, budgets, webhook
URL/events, quiet hours, muted projects, snitch URL, digest cadence,
accent, density, sound preferences, and configured Remote Machines —
into one downloadable JSON, and can load it back. Deliberately excludes
transient UI state (current view, theme, which cards are collapsed —
that lives in a separate `aoc_prefs` localStorage key, not this bundle),
since that's session-local state, not configuration worth carrying to a
new machine. Importing reloads the page once it's done, the same "safe
and obvious over clever" choice this app already makes for backup
restores rather than trying to hot-patch every open panel and
background worker in place.

**Dead man's snitch**: `sentinel.py` catches a dead `watchdog.py`, but
nothing on the machine can ever notice if the whole machine is off or
unreachable — that fundamentally needs something external. Paste a
heartbeat URL from a ping-or-alert service (healthchecks.io, Cronitor,
UptimeRobot heartbeats, etc. — AOC doesn't create an account for you, it
just pings whatever URL you give it) into Settings, and `monitor.py`
GETs it every 10 minutes for as long as it's alive. Missed pings are
exactly the signal those services alert on. **PING NOW** sends one
immediately so you can confirm the URL is right without waiting.

## Integrations

- **`/metrics`** — the same live numbers `/status` already returns
  (active session count, per-status agent counts, per-session cost, the
  hook-miss counter) re-rendered as Prometheus text exposition format
  instead of JSON, for plugging AOC into an existing monitoring/alerting
  stack rather than it staying siloed. Same auth as every other endpoint
  (`?token=` when tunneled, nothing needed from localhost).
- **`/history/search?q=&from=&to=`** — project name/session id substring
  plus an optional date range, querying `history.db` directly rather
  than the 100-row window `/history` alone serves. Merges in results
  from configured Remote Machines the same way the unfiltered History
  view already does.

## Free / Pro

AOC is one codebase, license-key-gated — there's no separate Pro build.
Free tier is everything most people need for local, single- or
multi-machine monitoring: full dashboard, History/Analytics, native
toasts, the embedded terminal, Force Stop. Activating a Pro key
(Settings → LICENSE) additionally unlocks:

- **Remote Machines** — merging in other AOC instances' live state
- The **remote-access tunnel** (Cloudflare/ngrok)
- **Webhook delivery** (Slack/Discord/generic, burn-spike and
  weekly-digest alerts)
- **Per-project monthly budgets**
- **CSV exports**

A key is `AOC-PRO-<payload>.<sig>`, Ed25519-signed and verified fully
offline against a public key embedded in `monitor.py` — activating one
never phones home. See **Security model** below for why the scheme is
asymmetric rather than a simple unlock code. Full terms are in
[`LICENSE.md`](LICENSE.md); to buy one, see that file's purchase note or
whatever page pointed you here.

## Security model

Auth tokens (the one `Remote Access`/tunnel token, and each configured
Remote Machine's own token) travel as a `?token=` query-string parameter,
not an `X-AOC-Token` header, wherever the request is cross-origin — a
custom header would trigger a CORS preflight `OPTIONS` request, and this
server has no `do_OPTIONS` handler, so it would just 501. This is a
deliberate tradeoff, not an oversight, and it's narrower than it might
look: `Handler.log_message` is overridden to a no-op, so this server has
never written per-request access logs — a token riding in a URL has never
been at risk of landing in a log file on disk here. The real exposure is
scoped to whoever already has LAN or tunnel access to the machine, which
is the same trust boundary the token model has always assumed; adding a
Remote Machine widens that boundary to include whichever machines you've
configured, not the general internet.

License keys (see **Free / Pro** above) are verified the same
asymmetric way for a different reason: this ships as plain-text `.py`,
so nothing stops someone from deleting the `if not _is_pro()` checks —
that's never actually preventable. The point of Ed25519 over a simpler
HMAC is narrower: stopping a *valid-looking* key from being forged or
shared. A symmetric scheme can't do that, since verifying a key would
require embedding the same secret used to sign it. Verification only
needs the public key baked into `monitor.py`; minting a new key needs
the private half, which never ships in this repo.

Token comparison uses `hmac.compare_digest` rather than `==`, guarding
against a timing side-channel in principle (impractical to actually
exploit here given a 24-byte random token and real network jitter, but
free to do correctly). `/git?cwd=` — used by the embedded terminal's
toolbar to show the current branch — only runs `git -C <cwd>` against a
directory that's actually one of the currently-tracked sessions' own
`cwd` (or this app's own directory), not an arbitrary path an
authenticated request could name; `/diff?file=` has an equivalent
containment check keeping a resolved path inside the current project
directory.

## Testing

`tests/` holds a small, dependency-free regression suite (`tests/js/` via
`node`, `tests/python/` via `python` — no pytest/jest, matching this repo's
own "no `requirements.txt`/`package.json`" choice). Each test extracts the
real function it's checking straight out of `monitor.py` (via regex for JS,
via the `ast` module for Python) rather than a hand-copied re-implementation,
so a test can't silently drift from the shipped code. Currently 991
checks across 87 files (grows with every feature — extracting the pure logic out
of a DOM/HTTP-driven function specifically so it *can* be tested this way is
a deliberate, recurring design choice throughout this codebase, not just a
testing afterthought). Run everything with one command:
`powershell -File tests/run_all.ps1`.

## Troubleshooting

- **`watchdog.log`** (next to `watchdog.py`) — restart history. A healthy
  system shows long stretches (hours to days) with no entries at all;
  frequent "not responding" lines are the symptom the OneDrive-lock bug
  above used to cause. A second, since-fixed cause of the same symptom:
  `/status` used to be rebuilt from scratch (disk read + full JSON
  parse/re-serialize) on *every* call, including once per open `/events`
  WebSocket client every time anything changed — on a long session with
  many accumulated agents (the payload can reach several MB), a burst of
  those landing together could stack enough CPU time to make the
  watchdog's own poll miss twice in a row. `_build_status_payload` now
  caches its result (gated on the same version counter that already
  tracked staleness, plus a 1s TTL for the handful of fields that change
  without bumping it), so concurrent callers share one rebuild instead of
  each redoing it. Beyond restarts, it also now logs failure modes that
  used to be silent `except: pass`es — most importantly if `monitor.py`
  survives `terminate()`+`kill()`+`taskkill /F` entirely (previously the
  log just stopped after "Restarting monitor..." with no indication the
  restart never actually happened), plus PID-lock file and toast-display
  failures.
- **`sentinel.log`** (next to `sentinel.py`) — only gets a line when
  `sentinel.py` actually had to relaunch a genuinely-dead `watchdog.py`.
  Empty/short is healthy; a line here means `watchdog.py` hung or gave up
  between the periodic 15-min checks. Also now logs a failed toast
  display, the same silent-`except`-turned-loud treatment `watchdog.log`
  got.
- **`~/.claude/hooks/aoc_hook_debug.log`** — every hook invocation, one
  `TRACE` line per event *unconditionally*, logged before any filtering.
  If an agent never showed up and there's no `TRACE` line for that time at
  all, the hook dispatch itself silently never fired (a Claude-Code-side
  issue, not this codebase) — that's exactly the gap the transcript-scanner
  fallback above exists to paper over.
- **`logs/background_errors.log`** — a feature that should be firing
  silently isn't (a webhook, a backup, an autosave)? Check here first: it
  catches exactly the class of failure that's otherwise invisible in
  `--headless` mode (see **Data locations** above). Empty is healthy.
- **The HEALTH view** (`/diag`) — quick self-check of AOC's own health
  (uptime, threads, memory, DB/backup size) without needing to inspect
  processes manually.
- **Multiple `monitor.py`/`watchdog.py` instances** — Windows'
  `SO_REUSEADDR` semantics differ from POSIX and can let a second instance
  bind the same port instead of just tolerating `TIME_WAIT`. Both scripts
  guard against this (a live `/status` check before binding, and a PID
  lock file respectively) but if something looks inconsistent, check for
  duplicates with `Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'"`
  before assuming a code bug.
