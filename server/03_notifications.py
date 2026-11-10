# AOC server, part 03: webhooks, quiet hours, notification center.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── Webhook settings (server-persisted, so delivery works with no browser
#    tab open at all -- see _webhook_notify_worker below) ─────────────────────
# The webhook URL/enabled-events used to live in localStorage only: the JS
# _fireWebhook() decided whether to fire and POSTed to /webhook_fire, which
# was a pure relay reading nothing stored server-side. If no tab was open,
# webhooks silently never fired. Modeled on TOKEN_FILE just below (a small
# standalone file, not status.json -- status.json gets wholesale-rebuilt by
# /reset, which already silently drops any key not explicitly carried into
# that handler's `idle = {...}` dict, e.g. session_note today).
WEBHOOK_SETTINGS_FILE = os.path.join(AOC_DIR, "aoc_webhook_settings.json")
_webhook_settings_lock = threading.Lock()
_webhook_settings = {"url": "", "events": {"done": True, "error": True, "stuck": False, "burn_spike": False, "weekly_digest": False, "waiting_nudge": False, "cost_spike": False, "budget_alert": False, "rate_limit": False}}

def _load_webhook_settings():
    global _webhook_settings
    data = _load_json_file(WEBHOOK_SETTINGS_FILE)
    try:
        if isinstance(data, dict) and "url" in data:
            # Same defensive backfill _load_notify_settings already does for
            # its own newer fields (e.g. digest_cadence) -- a settings file
            # written before burn_spike/weekly_digest/waiting_nudge existed
            # would otherwise load with those three keys simply absent
            # (harmless today since every .get() downstream already
            # defaults sensibly, but worth being consistent/explicit).
            data["events"] = _sanitize_webhook_events(data.get("events") or {})
            _webhook_settings = data
    except Exception:
        pass

def _sanitize_webhook_events(events: dict) -> dict:
    """Whitelist + coerce-to-bool every known webhook event key, with the
    same defaults used everywhere else these keys appear (the
    _webhook_settings module default, the JS _webhookEvents default
    string, _soundPrefs.events). Extracted specifically because this used
    to be inlined in the /webhook_settings POST handler with only
    done/error/stuck listed -- burn_spike/weekly_digest/waiting_nudge
    were silently dropped on every save despite the client always
    sending all seven (cost_spike added later) and every background
    worker already reading them (confirmed dead: aoc_webhook_settings.json
    had never once contained those three keys)."""
    out = {
        "done": bool(events.get("done", True)),
        "error": bool(events.get("error", True)),
        "stuck": bool(events.get("stuck", False)),
        "burn_spike": bool(events.get("burn_spike", False)),
        "weekly_digest": bool(events.get("weekly_digest", False)),
        "waiting_nudge": bool(events.get("waiting_nudge", False)),
        "cost_spike": bool(events.get("cost_spike", False)),
        "budget_alert": bool(events.get("budget_alert", False)),
        "rate_limit": bool(events.get("rate_limit", False)),
    }
    # Split toggles from the notification center: kept only once set, so a
    # missing one keeps following its older key (session_loop <- stuck,
    # budget_cap <- budget_alert; see _NOTIFY_TYPES "legacy").
    for k in ("session_loop", "budget_cap"):
        if k in events:
            out[k] = bool(events[k])
    return out

def _save_webhook_settings(data: dict):
    global _webhook_settings
    with _webhook_settings_lock:
        _webhook_settings = data
        try:
            with open(WEBHOOK_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

_load_webhook_settings()

def _clamp_backup_retention_days(value) -> int:
    """Coerce a user-supplied backup retention window to a sane int range
    (1-365 days) -- guards _backup_history_db's pruning loop against a
    malformed/absent value ever deleting everything (0 or negative) or a
    typo'd huge number effectively disabling pruning entirely."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 14
    return max(1, min(365, n))

def _clamp_agent_retention_hours(value) -> int:
    """Coerce a user-supplied agent-retention window to a sane int range.
    Capped at 23 (not 24+) because completed_at/started_at are stored as
    bare "HH:MM:SS" local-time strings with no date component (_now_ts) --
    _hms_elapsed_hours can only measure "hours since, assuming it was
    within the last day" (mod-24 arithmetic), so 24h+ is genuinely
    ambiguous with "just now". Floored at 1 so a malformed/zero value
    can't auto-clear every just-finished agent instantly."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 12
    return max(1, min(23, n))

# ── Notification quiet hours / per-project mute ────────────────────────────
# With toast + sound + webhook all now able to fire server-side with no tab
# open, notification fatigue became a real risk -- this is the one place
# that decides "should ANY channel notify about this agent right now",
# shared by both _headless_notify_worker (toast) and _webhook_notify_worker
# (webhook) below. Modeled on WEBHOOK_SETTINGS_FILE just above: its own
# small file, not status.json.
NOTIFY_SETTINGS_FILE = os.path.join(AOC_DIR, "aoc_notify_settings.json")
_notify_settings_lock = threading.Lock()
_notify_settings = {"quiet_start": "", "quiet_end": "", "muted_projects": [], "snitch_url": "", "digest_cadence": "weekly", "backup_retention_days": 14, "agent_retention_hours": 12, "project_budgets": {}}

def _sanitize_project_budgets(budgets) -> dict:
    """Whitelist + coerce a user-supplied {project: monthly $ budget} map --
    same reasoning as _sanitize_webhook_events: the client always sends
    whatever's currently in the Settings textarea, and this is the one
    place that gets trusted as project names / numeric thresholds
    _webhook_notify_worker's budget_alert check later compares against."""
    if not isinstance(budgets, dict):
        return {}
    out = {}
    for project, amount in budgets.items():
        project = str(project).strip()[:100]
        if not project:
            continue
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        out[project] = round(min(amount, 1_000_000), 2)
        if len(out) >= 50:
            break
    return out

def _load_notify_settings():
    global _notify_settings
    data = _load_json_file(NOTIFY_SETTINGS_FILE)
    try:
        if isinstance(data, dict) and "muted_projects" in data:
            data.setdefault("snitch_url", "")  # older settings files predate this field
            data.setdefault("digest_cadence", "weekly")  # ditto
            data.setdefault("backup_retention_days", 14)  # ditto
            data.setdefault("agent_retention_hours", 12)  # ditto
            data.setdefault("project_budgets", {})  # ditto
            _notify_settings = data
    except Exception:
        pass

def _save_notify_settings(data: dict):
    global _notify_settings
    with _notify_settings_lock:
        _notify_settings = data
        try:
            with open(NOTIFY_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

_load_notify_settings()

def _in_quiet_hours(quiet_start: str, quiet_end: str) -> bool:
    """True if the current local time falls in [quiet_start, quiet_end).
    A start > end is treated as an overnight window (e.g. 22:00-07:00)."""
    if not quiet_start or not quiet_end:
        return False
    try:
        now_str = datetime.now().strftime("%H:%M")
        if quiet_start <= quiet_end:
            return quiet_start <= now_str < quiet_end
        return now_str >= quiet_start or now_str < quiet_end
    except Exception:
        return False

def _notification_suppressed(project: str) -> bool:
    cfg = _notify_settings
    if project and project in (cfg.get("muted_projects") or []):
        return True
    return _in_quiet_hours(cfg.get("quiet_start", ""), cfg.get("quiet_end", ""))

# ── Notification center: every alert AOC sends, one row per type ──────────
# One entry per notification type. Adding a type = one row here plus a
# kind= on the call that fires it (_show_native_toast / _push_notify /
# _notify_webhook); Settings -> Notify lists, toggles and tests it from this
# table. Channels: toast (Windows), push (phones, see PHONE PUSH), webhook.
#   webhook: key in the webhook settings' "events" map; legacy: an older
#            key whose value a missing new key inherits (split toggles).
#   quiet:   quiet hours + muted projects apply. Off for things you asked
#            to be told about right away (held prompts) or that are
#            account-level housekeeping (updates, digest).
#   default: toast/push state until changed in Settings.
_NOTIFY_TYPES = [
    {"key": "done", "group": "Agents", "label": "Agent finished", "channels": ("toast", "push", "webhook"),
     "webhook": "done", "sample": ("✓ Sample agent", "Completed successfully")},
    {"key": "error", "group": "Agents", "label": "Agent error", "channels": ("toast", "push", "webhook"),
     "webhook": "error", "sample": ("✗ Sample agent", "Tool failed: example error")},
    {"key": "stuck", "group": "Agents", "label": "Agent stalled (no task progress 5 min)", "channels": ("toast", "push", "webhook"),
     "webhook": "stuck", "sample": ("⏸ Sample agent", "No task progress for 5+ min")},
    {"key": "session_loop", "group": "Sessions", "label": "Session looks stuck (going round in circles)", "channels": ("toast", "push", "webhook"),
     "webhook": "session_loop", "legacy": "stuck", "sample": ("⟳ Sample session looks stuck", "Same command failed 3 times")},
    {"key": "waiting_nudge", "group": "Sessions", "label": "Waiting on you for 2 h+", "channels": ("toast", "push", "webhook"),
     "webhook": "waiting_nudge", "sample": ("⏳ Sample session", "Waiting on you for 2h 5m")},
    {"key": "cache_expiry", "group": "Sessions", "label": "Prompt cache about to go cold", "channels": ("toast", "push"),
     "sample": ("❄ Sample session · cache cold in 8 min", "Reply now, or the next message costs ~$1.20 more")},
    {"key": "ctx_save", "group": "Sessions", "label": "Context almost full (save state)", "channels": ("toast", "push"),
     "sample": ("🧠 Sample session: context 90 % full", "Save its state to memory before it compacts -- one click in AOC.")},
    {"key": "permission", "group": "Sessions", "label": "Held permission prompt (Remote approve)", "channels": ("push",),
     "quiet": False, "sample": ("🔐 Sample session needs your OK", "Bash: npm test")},
    {"key": "reply", "group": "Sessions", "label": "Turn held for your reply (Reply from phone)", "channels": ("push",),
     "quiet": False, "sample": ("💬 Sample session asks", "Should I commit this?")},
    {"key": "queue", "group": "Sessions", "label": "Queued task started / failed", "channels": ("toast", "push"),
     "quiet": False, "sample": ("▶ Queued task started · project", "Run the tests and fix failures")},
    {"key": "burn_spike", "group": "Cost", "label": "Token burn spike", "channels": ("toast", "push", "webhook"),
     "webhook": "burn_spike", "sample": ("⚡ Sample session", "Burn spike: 42000 tok/min (avg 9000)")},
    {"key": "cost_spike", "group": "Cost", "label": "Session cost spike vs project average", "channels": ("toast", "push", "webhook"),
     "webhook": "cost_spike", "sample": ("💰 Sample session", "Cost spike: $12.40 (project avg $3.10)")},
    {"key": "budget_alert", "group": "Cost", "label": "Monthly project budget exceeded", "channels": ("toast", "push", "webhook"),
     "webhook": "budget_alert", "sample": ("💸 project", "Monthly budget exceeded: $52.00 of $50.00")},
    {"key": "budget_cap", "group": "Cost", "label": "Hard budget cap reached (tool calls blocked)", "channels": ("toast", "push", "webhook"),
     "webhook": "budget_cap", "legacy": "budget_alert", "quiet": False, "sample": ("⛔ Budget cap · Sample session", "$5.02 of $5.00 -- tool calls are blocked")},
    {"key": "rate_limit", "group": "Cost", "label": "Plan limit (5 h / weekly)", "channels": ("toast", "push", "webhook"),
     "webhook": "rate_limit", "sample": ("Plan limit · 5-hour at 80 %", "Resets in 1h 20m")},
    {"key": "limit_soon", "group": "Cost", "label": "Plan limit runs out within 30 min at this pace", "channels": ("toast", "push"),
     "sample": ("⏳ 5-hour limit in ~25 min at this pace", "Now 87 % · resets 21:00 · pause new agents or switch to a cheaper model")},
    {"key": "weekly_digest", "group": "Cost", "label": "Cost / activity digest", "channels": ("toast", "push", "webhook"),
     "webhook": "weekly_digest", "quiet": False, "sample": ("AOC Digest", "12 sessions, $48.20 (+5% vs last week), 1 errors this week")},
    {"key": "orchestration", "group": "Orchestration", "label": "Orchestration needs you (a plan, an OK for a sensitive task, results to merge)",
     "channels": ("toast", "push"), "sample": ("🤖 Plan ready for your OK", "Add CSV export to the reports page")},
    {"key": "update", "group": "AOC", "label": "AOC update available", "channels": ("toast", "push"),
     "quiet": False, "sample": ("AOC", "AOC 1.0.999 is available.")},
    {"key": "cc_version", "group": "AOC", "label": "New Claude Code version seen", "channels": ("toast", "push"),
     "quiet": False, "sample": ("AOC", "New Claude Code version detected: 9.9.9")},
    {"key": "backup", "group": "AOC", "label": "History backup failed its check", "channels": ("toast", "push"),
     "quiet": False, "sample": ("AOC Backup", "History DB backup failed integrity check")},
    {"key": "auto_repair", "group": "AOC", "label": "Automatic hook repair after an update didn't finish", "channels": ("toast", "push"),
     "quiet": False, "sample": ("AOC", "Automatic hook repair after the update didn't finish -- open Health -> Repair")},
]
_NOTIFY_BY_KEY = {t["key"]: t for t in _NOTIFY_TYPES}
_NOTIFY_CHANNELS = ("toast", "push", "webhook")


def _notify_type_on(kind, channel, notify_cfg, webhook_cfg) -> bool:
    """Pure: the user's toggle for (type, channel), defaults included --
    ignores quiet hours. Unknown types (a caller not migrated yet) keep the
    old behaviour: toast + push on, no webhook."""
    t = _NOTIFY_BY_KEY.get(kind)
    if t is None:
        return channel != "webhook"
    if channel not in t["channels"]:
        return False
    if channel == "webhook":
        ev = (webhook_cfg or {}).get("events") or {}
        if t["webhook"] in ev:
            return bool(ev[t["webhook"]])
        if t.get("legacy") in ev:
            return bool(ev[t["legacy"]])
        return t["webhook"] in ("done", "error")
    ch = ((notify_cfg or {}).get("channels") or {}).get(kind) or {}
    return bool(ch.get(channel, (t.get("default") or {}).get(channel, True)))


def _notify_gate(kind, channel, project="", notify_cfg=None, webhook_cfg=None, now_hm=None):
    """Pure-ish: (send?, reason) for one notification on one channel --
    the type's toggle, then muted project, then quiet hours (both skipped
    for types with quiet=False). Config defaults to the live settings."""
    ncfg = _notify_settings if notify_cfg is None else notify_cfg
    wcfg = _webhook_settings if webhook_cfg is None else webhook_cfg
    t = _NOTIFY_BY_KEY.get(kind) or {}
    if channel not in _NOTIFY_CHANNELS:
        return False, "unknown channel"
    if t and channel not in t["channels"]:
        return False, "not available"
    if not _notify_type_on(kind, channel, ncfg, wcfg):
        return False, "off"
    if channel == "webhook" and not (wcfg or {}).get("url"):
        return False, "no webhook url"
    if t.get("quiet", True):
        if project and project in (ncfg.get("muted_projects") or []):
            return False, "muted project"
        qs, qe = ncfg.get("quiet_start", ""), ncfg.get("quiet_end", "")
        if now_hm is not None:
            if qs and qe and ((qs <= now_hm < qe) if qs <= qe else (now_hm >= qs or now_hm < qe)):
                return False, "quiet hours"
        elif _in_quiet_hours(qs, qe):
            return False, "quiet hours"
    return True, ""


def _sanitize_notify_channels(channels) -> dict:
    """Whitelist {type: {toast|push: bool}} -- webhook toggles live in the
    webhook settings file, as before."""
    out = {}
    for kind, ch in (channels or {}).items() if isinstance(channels, dict) else ():
        t = _NOTIFY_BY_KEY.get(kind)
        if not t or not isinstance(ch, dict):
            continue
        row = {c: bool(ch[c]) for c in ("toast", "push") if c in ch and c in t["channels"]}
        if row:
            out[kind] = row
    return out


# Recent notifications, newest last: in memory + a small ring on disk so the
# list survives restarts.
NOTIFY_LOG_FILE = os.path.join(AOC_DATA_DIR, "notify_log.json")
_NOTIFY_LOG_MAX = 100
_notify_log_lock = threading.Lock()
_notify_log = []


def _notify_log_load():
    data = _load_json_file(NOTIFY_LOG_FILE)
    if isinstance(data, list):
        _notify_log[:] = [e for e in data if isinstance(e, dict)][-_NOTIFY_LOG_MAX:]


def _notify_log_add(kind, channel, title, body="", now=None, test=False, path=None):
    entry = {"t": round(now or time.time(), 1), "kind": str(kind or "other")[:40], "channel": channel,
             "title": str(title or "")[:120], "body": str(body or "")[:200]}
    if test:
        entry["test"] = True
    with _notify_log_lock:
        _notify_log.append(entry)
        del _notify_log[:-_NOTIFY_LOG_MAX]
        snap = list(_notify_log)
    try:
        path = path or NOTIFY_LOG_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass
    return entry


try:
    _notify_log_load()
except Exception:
    pass


def _notify_webhook(kind, payload, project="", url=None) -> bool:
    """Gate + deliver one webhook event (payload as before). True if sent."""
    ok, _why = _notify_gate(kind, "webhook", project)
    if not ok:
        return False
    _fire_webhook(url or _webhook_settings.get("url", ""), payload)
    _notify_log_add(kind, "webhook", payload.get("event", kind), "")
    return True


def _notify_center_view(notify_cfg, webhook_cfg, push_devices=0, now_hm=None) -> dict:
    """Pure: what Settings -> Notify shows -- every type with its channels'
    state and whether quiet hours apply right now."""
    qs, qe = notify_cfg.get("quiet_start", ""), notify_cfg.get("quiet_end", "")
    rows = []
    for t in _NOTIFY_TYPES:
        rows.append({"key": t["key"], "group": t["group"], "label": t["label"], "quiet": t.get("quiet", True),
                     "channels": {c: _notify_type_on(t["key"], c, notify_cfg, webhook_cfg) if c in t["channels"] else None
                                  for c in _NOTIFY_CHANNELS}})
    quiet_now = False
    if qs and qe and now_hm is not None:
        quiet_now = (qs <= now_hm < qe) if qs <= qe else (now_hm >= qs or now_hm < qe)
    return {"types": rows, "quiet_start": qs, "quiet_end": qe, "quiet_now": quiet_now,
            "muted_projects": list(notify_cfg.get("muted_projects") or []),
            "webhook_url": bool((webhook_cfg or {}).get("url")), "push_devices": push_devices}


def _notify_set(kind, channel, on, notify_cfg, webhook_cfg):
    """Pure: (new notify cfg, new webhook cfg) with one toggle changed, or
    raise ValueError."""
    t = _NOTIFY_BY_KEY.get(kind)
    if not t or channel not in t["channels"]:
        raise ValueError("unknown notification")
    ncfg, wcfg = dict(notify_cfg), dict(webhook_cfg)
    if channel == "webhook":
        ev = dict(_sanitize_webhook_events(wcfg.get("events") or {}))
        ev[t["webhook"]] = bool(on)
        wcfg["events"] = ev
    else:
        ch = {k: dict(v) for k, v in (ncfg.get("channels") or {}).items()}
        ch.setdefault(kind, {})[channel] = bool(on)
        ncfg["channels"] = _sanitize_notify_channels(ch)
    return ncfg, wcfg


_notify_test_last = [0.0]


def _notify_log_clear(path=None):
    with _notify_log_lock:
        del _notify_log[:]
    try:
        os.remove(path or NOTIFY_LOG_FILE)
    except OSError:
        pass


def _notify_center_payload(now=None) -> dict:
    """GET /notify_center: the toggle table plus the recent log, newest first."""
    try:
        devices = len(_push_subs())
    except Exception:
        devices = 0
    view = _notify_center_view(_notify_settings, _webhook_settings, devices,
                               datetime.fromtimestamp(now or time.time()).strftime("%H:%M"))
    with _notify_log_lock:
        view["log"] = list(reversed(_notify_log))
    view["pro"] = bool(_is_pro())
    return view


def _notify_test(kind, now=None, channel=None) -> dict:
    """Send the type's sample through every channel that is on for it, or
    just `channel` (on or off) -- quiet hours and the phone's away mode are
    ignored, you asked for it."""
    now = now or time.time()
    t = _NOTIFY_BY_KEY.get(kind)
    if not t:
        return {"ok": False, "error": "unknown notification"}
    if channel is not None and channel not in t["channels"]:
        return {"ok": False, "error": "not available for this notification"}
    if now - _notify_test_last[0] < 3:
        return {"ok": False, "error": "wait a few seconds between tests"}
    _notify_test_last[0] = now
    title, body = t["sample"]
    title = "[test] " + title
    sent, skipped = [], {}
    for ch in ((channel,) if channel else t["channels"]):
        if not channel and not _notify_type_on(kind, ch, _notify_settings, _webhook_settings):
            skipped[ch] = "off"
            continue
        if ch == "toast":
            _win_toast(title, body)
            _notify_log_add(kind, "toast", title, body, now=now, test=True)
            sent.append("toast")
        elif ch == "push":
            n = _push_notify(title, body, tag="test-" + kind, force=True)
            if n:
                _notify_log_add(kind, "push", title, body, now=now, test=True)
                sent.append("push")
            else:
                skipped["push"] = "no phone set up"
        elif ch == "webhook":
            url = _webhook_settings.get("url", "")
            if not url:
                skipped["webhook"] = "no webhook url"
                continue
            _fire_webhook(url, {"event": t["webhook"], "test": True, "title": title, "body": body})
            _notify_log_add(kind, "webhook", title, body, now=now, test=True)
            sent.append("webhook")
    return {"ok": True, "sent": sent, "skipped": skipped}

_SNITCH_INTERVAL_S = 600  # 10 min -- comfortably inside the grace period any
                          # ping-or-alert service (healthchecks.io, Cronitor,
                          # UptimeRobot heartbeats) would reasonably be set to

def _snitch_ping(url: str) -> None:
    """Fire a single heartbeat GET at the configured dead-man's-snitch URL.
    A missed ping is exactly the signal these services are built to alert
    on -- no retry/backoff here, same as every other best-effort background
    worker in this file; the NEXT interval's ping is the retry."""
    if not url:
        return
    try:
        import urllib.request
        urllib.request.urlopen(url, timeout=8)
    except Exception as e:
        _log_bg_error("_snitch_ping", e)

def _snitch_worker():
    """Runs in monitor.py itself, deliberately -- not sentinel.py/watchdog.py.
    The whole point is that if monitor.py dies, the pings stop, which is the
    correct trigger for the external service to fire. sentinel.py/watchdog.py
    already cover 'is AOC alive on this machine'; this covers 'is this
    machine even reachable at all', which nothing on the machine itself can
    ever detect once it's the thing that's down."""
    while True:
        time.sleep(_SNITCH_INTERVAL_S)
        _snitch_ping(_notify_settings.get("snitch_url", ""))

threading.Thread(target=_snitch_worker, daemon=True).start()

def _compute_burn_rates(history, now: float, recent_window_s: int = 180, min_history_s: int = 300):
    """Given a session's (epoch, cumulative_tokens) samples -- already
    trimmed to whatever window the caller tracks -- return (recent_rate,
    avg_rate) in tokens/minute, or None if there isn't yet enough history
    to judge a burn rate meaningfully (avoids noisy false positives on
    brand-new sessions). `recent_rate` covers the last `recent_window_s`
    seconds; `avg_rate` covers the whole observed history, requiring at
    least `min_history_s` seconds of it before returning anything at all."""
    if len(history) < 2:
        return None
    oldest_ts, oldest_tokens = history[0]
    newest_ts, newest_tokens = history[-1]
    observed_s = newest_ts - oldest_ts
    if observed_s < min_history_s:
        return None
    avg_rate = (newest_tokens - oldest_tokens) / (observed_s / 60.0)
    recent_cutoff = now - recent_window_s
    recent = [(t, tok) for t, tok in history if t >= recent_cutoff]
    if len(recent) < 2:
        return None
    r_oldest_ts, r_oldest_tokens = recent[0]
    r_newest_ts, r_newest_tokens = recent[-1]
    r_observed_s = r_newest_ts - r_oldest_ts
    if r_observed_s <= 0:
        return None
    recent_rate = (r_newest_tokens - r_oldest_tokens) / (r_observed_s / 60.0)
    return (recent_rate, avg_rate)

def _is_burn_spike(recent_rate: float, avg_rate: float, min_floor: float = 5000.0, multiplier: float = 3.0) -> bool:
    """A session's token burn rate is spiking if its recent short-window
    rate (tokens/min) is both a multiple of its own session-long average
    AND above an absolute floor -- the floor guards a low-volume session
    where the ratio alone would be noisy but the real numbers are trivial."""
    return avg_rate > 0 and recent_rate > multiplier * avg_rate and recent_rate > min_floor

def _is_cost_spike(session_cost: float, project_avg_cost: float, min_floor: float = 1.0, multiplier: float = 3.0) -> bool:
    """Session-level counterpart to _is_burn_spike -- distinct signal: this
    looks at a session's *total* cost so far against that project's
    historical per-session average (from /analytics' by_project rows), not
    the token *rate*. Catches a session that's simply run up an unusually
    large bill for its project (e.g. an expensive multi-hour deep-dive)
    even if it never burned tokens fast enough to trip the rate-based
    burn-spike check. Same floor+multiplier shape as _is_burn_spike so a
    project with a trivial historical average doesn't generate noise."""
    return project_avg_cost > 0 and session_cost > multiplier * project_avg_cost and session_cost > min_floor

def _project_avg_costs(by_project: list) -> dict:
    """Turn /analytics' by_project rows (each a sum of cost + a session
    count) into {project: average cost per historical session} for
    _is_cost_spike to compare a live session's running cost against.
    Skips any row with a zero/missing session count rather than dividing by
    zero (a brand-new project with no completed sessions yet has no
    baseline to compare against)."""
    out = {}
    for row in by_project or []:
        project = row.get("project")
        sessions = row.get("sessions") or 0
        if not project or sessions <= 0:
            continue
        out[project] = (row.get("cost") or 0) / sessions
    return out

def _project_month_to_date_costs() -> dict:
    """{project: cost} summed over the current calendar month only -- same
    aggregation shape as _db_analytics()'s by_project, just date-scoped to
    this month, for _webhook_notify_worker's budget_alert check against
    _notify_settings["project_budgets"]. A dedicated query rather than
    reusing by_day_project client-side-style (summing per-day rows in a
    loop): this needs one server-side per-project total, refreshed on the
    same 5-min cache cadence as _project_avg_cost_cache."""
    month_prefix = datetime.now().strftime("%Y-%m") + "%"
    with _db_lock:
        try:
            c = _db_conn()
            rows = c.execute("""
                SELECT project, SUM(cost) as cost
                FROM sessions WHERE project != '' AND date LIKE ?
                GROUP BY project
            """, (month_prefix,)).fetchall()
            c.close()
            return {r["project"]: (r["cost"] or 0) for r in rows}
        except Exception:
            return {}

def _fire_webhook(url: str, payload: dict):
    """Shared relay used by both /webhook_fire (client-triggered, tab must be
    open) and _webhook_notify_worker (server-triggered, works with no tab
    open) -- one place the actual HTTP delivery can drift, not two."""
    if AOC_PREVIEW:
        return
    if not url:
        return
    _log_alert_fired(payload)
    import urllib.request as _ur
    body = json.dumps({"source": "AOC", **payload}, ensure_ascii=False).encode()
    def _fire():
        try:
            req = _ur.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
            _ur.urlopen(req, timeout=8)
        except Exception as e:
            # _log_alert_fired above already recorded this as "fired" before
            # delivery was even attempted -- a failure here would otherwise
            # look identical to success in alert_audit.log.
            _log_bg_error("_fire_webhook", e)
    threading.Thread(target=_fire, daemon=True).start()
