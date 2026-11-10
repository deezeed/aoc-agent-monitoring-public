# AOC server, part 04: plan rate limits, context window per session.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── Plan rate limits (Pro/Max 5-hour + weekly windows) ──────────────────────
# Claude Code hands its statusline command the real utilization Anthropic
# reports (rate_limits.five_hour/seven_day: used_percentage 0-100, resets_at
# epoch s). hooks/aoc_statusline.py writes that to RATE_LIMITS_FILE; hooks
# never see it. Second source: when a limit is actually hit, the transcript
# line carries quotaLimits {status: "rejected", rateLimitType, resetsAt} --
# the transcript scanner records those via _record_rate_limit_hit, so
# "limit reached, resets 13:20" shows even without the statusline installed.
RATE_LIMITS_FILE = os.path.join(AOC_DATA_DIR, "rate_limits.json")
_RATE_LIMIT_KINDS = (("five_hour", "5h"), ("seven_day", "weekly"))
_RATE_LIMIT_WARN_PCT = 80.0
_RATE_LIMIT_SOON_S = 1800        # "runs out within 30 min at this pace"
_RATE_LIMIT_KEY_TOL_S = 120      # resets_at readings this close = one window
_rate_limit_lock = threading.Lock()
# samples: (kind, resets_at) -> [(epoch, pct)] -- one list per window, so a
# reset never mixes the old window's 95 % into the new window's pace.
# Persisted to RATE_LIMIT_SAMPLES_FILE: the code reload restarts the monitor
# often, and a forecast that needs 5 min of fresh history after every
# restart is mostly missing.
_rate_limit_state = {"mtime": None, "snap": None, "samples": {}, "hits": {}}
RATE_LIMIT_SAMPLES_FILE = os.path.join(AOC_DATA_DIR, "rate_limit_samples.json")
_RATE_LIMIT_SAVE_EVERY_S = 60


def _rate_limit_pace(samples, now, span_s=1800):
    """Pure: percentage points per hour over the last span_s seconds of one
    window's (epoch, pct) samples -- a least-squares slope over the running
    maximum (usage can't go down inside a window, so a lower reading is
    rounding noise, and one late burst no longer decides the whole pace).
    None when there is too little history (< 2 samples or < 5 min between
    first and last) to say anything."""
    recent = sorted((t, p) for t, p in samples if now - t <= span_s)
    if len(recent) < 2 or recent[-1][0] - recent[0][0] < 300:
        return None
    pts, hi = [], None
    for t, p in recent:
        hi = p if hi is None else max(hi, p)
        pts.append((t, hi))
    mt = sum(t for t, _ in pts) / len(pts)
    mp = sum(p for _, p in pts) / len(pts)
    den = sum((t - mt) ** 2 for t, _ in pts)
    slope = sum((t - mt) * (p - mp) for t, p in pts) / den
    return max(0.0, slope * 3600)


def _rate_limit_sample_key(keys, kind, resets, tol=_RATE_LIMIT_KEY_TOL_S):
    """Pure: the samples key for a (kind, resets_at) reading -- the existing
    key of the same kind whose resets_at is within tol seconds, so a reset
    time that wobbles by a few seconds between readings doesn't split one
    window's history into many single-sample lists."""
    best = None
    for k in keys:
        if k[0] == kind and abs(k[1] - resets) <= tol and (best is None or abs(k[1] - resets) < abs(best[1] - resets)):
            best = k
    return best or (kind, resets)


def _rate_limit_samples_load(data, now):
    """Pure: RATE_LIMIT_SAMPLES_FILE content -> samples dict; windows that
    already reset and malformed rows are dropped."""
    out = {}
    for row in (data if isinstance(data, list) else []):
        try:
            kind, resets, pts = str(row["kind"]), float(row["resets_at"]), row["samples"]
            if resets <= now:
                continue
            lst = sorted((float(t), float(p)) for t, p in pts)[-200:]
        except Exception:
            continue
        if lst:
            out[(kind, resets)] = lst
    return out


def _rate_limit_samples_dump(samples):
    return [{"kind": k[0], "resets_at": k[1], "samples": [list(x) for x in v]} for k, v in sorted(samples.items())]


def _rate_limit_view(snap, hits, samples, now):
    """Pure: statusline snapshot + transcript limit hits + pace samples ->
    the /status "rate_limits" block, or None when there's nothing to show."""
    snap = snap or {}
    updated_at = snap.get("updated_at")
    windows = []
    for kind, label in _RATE_LIMIT_KINDS:
        w = snap.get(kind) or {}
        pct, resets = w.get("used_percentage"), w.get("resets_at")
        source = "statusline"
        hit = hits.get(kind)
        if hit and hit["resets_at"] > now and (resets is None or hit["seen_at"] >= (updated_at or 0)):
            pct, resets, source = 100.0, hit["resets_at"], "limit_message"
        if not isinstance(pct, (int, float)) or not isinstance(resets, (int, float)):
            continue
        if resets <= now:
            windows.append({"kind": kind, "label": label, "pct": None, "resets_at": resets,
                            "status": "reset", "source": source})
            continue
        pace = _rate_limit_pace(samples.get(_rate_limit_sample_key(samples.keys(), kind, resets), []), now)
        projected = eta_full = None
        if pace is not None:
            projected = round(pct + pace * (resets - now) / 3600, 1)
            if pace > 0 and pct < 100:
                eta = now + (100 - pct) / pace * 3600
                if eta < resets:
                    eta_full = int(eta)
        status = "hit" if pct >= 100 else "warn" if (pct >= _RATE_LIMIT_WARN_PCT or eta_full) else "ok"
        windows.append({
            "kind": kind, "label": label, "pct": round(float(pct), 1),
            "resets_at": int(resets), "resets_in_s": int(resets - now),
            "pace_pct_per_h": round(pace, 1) if pace is not None else None,
            "projected_pct": projected, "eta_full_at": eta_full,
            "eta_full_in_s": int(eta_full - now) if eta_full else None,
            "soon": bool(eta_full and eta_full - now <= _RATE_LIMIT_SOON_S),
            "status": status, "source": source,
        })
    if not windows:
        return None
    return {"updated_at": updated_at,
            "age_s": int(now - updated_at) if isinstance(updated_at, (int, float)) else None,
            "windows": windows}


def _fmt_reset_time(epoch, now):
    """'13:20' when it's within a day, else 'Tue 13:20'."""
    dt = datetime.fromtimestamp(epoch)
    return dt.strftime("%H:%M") if epoch - now < 86400 else dt.strftime("%a %H:%M")


def _rate_limit_alerts(view, notified, now):
    """Pure: which rate-limit notifications are due. Returns a list of
    (key, toast_title, toast_body, webhook_payload); the caller adds each
    key to `notified` after firing. One warning and one hit per window
    (keyed on resets_at), so a new window re-arms both."""
    out = []
    for w in (view or {}).get("windows", []):
        if w.get("status") not in ("warn", "hit"):
            continue
        key = (w["kind"], w["resets_at"], w["status"])
        if key in notified:
            continue
        name = "5-hour" if w["kind"] == "five_hour" else "weekly"
        resets = _fmt_reset_time(w["resets_at"], now)
        if w["status"] == "hit":
            title, body = f"⛔ {name.capitalize()} limit reached", f"Resets {resets}"
        elif w.get("eta_full_at") and w["pct"] < _RATE_LIMIT_WARN_PCT:
            title = f"⚠ On pace to hit the {name} limit at {_fmt_reset_time(w['eta_full_at'], now)}"
            body = f"Now {w['pct']:.0f}% · resets {resets}"
        else:
            title, body = f"⚠ {name.capitalize()} limit at {w['pct']:.0f}%", f"Resets {resets}"
        payload = {"event": "rate_limit", "level": "hit" if w["status"] == "hit" else "warning",
                   "window": w["kind"], "pct": w["pct"], "resets_at": w["resets_at"],
                   "eta_full_at": w.get("eta_full_at")}
        out.append((key, title, body, payload))
    return out


def _fmt_in(secs):
    """'~25 min' / '~1 h 10 min' / '~2 h' (rounded up to the minute)."""
    m = max(1, int((secs + 59) // 60))
    if m < 60:
        return f"~{m} min"
    return f"~{m // 60} h {m % 60} min" if m % 60 else f"~{m // 60} h"


def _rate_limit_soon_alerts(view, notified, now):
    """Pure: the "runs out within 30 min at this pace" alert, once per
    window. Returns [(keys, toast_title, toast_body)]: keys = the soon key
    and that window's ordinary warn key -- the caller marks both notified
    before _rate_limit_alerts runs, so a window never toasts twice in a row
    about the same thing."""
    out = []
    for w in (view or {}).get("windows", []):
        if not w.get("soon") or w.get("status") == "hit" or not w.get("eta_full_at"):
            continue
        key = (w["kind"], w["resets_at"], "soon")
        if key in notified:
            continue
        name = "5-hour" if w["kind"] == "five_hour" else "weekly"
        title = f"⏳ {name.capitalize()} limit in {_fmt_in(w['eta_full_at'] - now)} at this pace"
        body = (f"Now {w['pct']:.0f} % · resets {_fmt_reset_time(w['resets_at'], now)} · "
                "pause new agents or switch to a cheaper model")
        out.append(([key, (w["kind"], w["resets_at"], "warn")], title, body))
    return out


RATE_LIMIT_NOTIFIED_FILE = os.path.join(AOC_DATA_DIR, "rate_limit_notified.json")


def _load_rate_limit_notified(now):
    """Already-fired (kind, resets_at, status) keys, persisted so a monitor
    restart mid-window doesn't toast the same warning again. Expired windows
    are dropped on load."""
    data = _load_json_file(RATE_LIMIT_NOTIFIED_FILE)
    out = set()
    for k in (data if isinstance(data, list) else []):
        if isinstance(k, list) and len(k) == 3 and isinstance(k[1], (int, float)) and k[1] > now:
            out.add((str(k[0]), k[1], str(k[2])))
    return out


def _save_rate_limit_notified(keys):
    try:
        os.makedirs(AOC_DATA_DIR, exist_ok=True)
        tmp = RATE_LIMIT_NOTIFIED_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted([list(k) for k in keys]), f)
        os.replace(tmp, RATE_LIMIT_NOTIFIED_FILE)
    except Exception as e:
        _log_bg_error("_save_rate_limit_notified", e)


def _record_rate_limit_hit(kind, resets_at, seen_at):
    """Called by the transcript scanner for a quotaLimits 'rejected' line."""
    if kind not in dict(_RATE_LIMIT_KINDS) or not isinstance(resets_at, (int, float)):
        return
    with _rate_limit_lock:
        prev = _rate_limit_state["hits"].get(kind)
        if not prev or seen_at >= prev["seen_at"]:
            _rate_limit_state["hits"][kind] = {"resets_at": resets_at, "seen_at": seen_at}


def _rate_limits_snapshot():
    """The /status 'rate_limits' block. Re-reads RATE_LIMITS_FILE only when
    its mtime changes (it's in %LOCALAPPDATA%, not OneDrive, so the stat is
    cheap) and appends each new reading to that window's pace samples."""
    now = time.time()
    with _rate_limit_lock:
        st = _rate_limit_state
        if not st.get("loaded"):
            st["loaded"] = True
            for k, v in _rate_limit_samples_load(_load_json_file(RATE_LIMIT_SAMPLES_FILE), now).items():
                st["samples"].setdefault(k, v)
        try:
            mtime = os.stat(RATE_LIMITS_FILE).st_mtime
        except OSError:
            mtime = None
        if mtime is not None and mtime != st["mtime"]:
            snap = _load_json_file(RATE_LIMITS_FILE)
            if isinstance(snap, dict):
                st["mtime"], st["snap"] = mtime, snap
                t = snap.get("updated_at") or now
                for kind, _label in _RATE_LIMIT_KINDS:
                    w = snap.get(kind) or {}
                    if isinstance(w.get("used_percentage"), (int, float)) and isinstance(w.get("resets_at"), (int, float)):
                        key = _rate_limit_sample_key(st["samples"].keys(), kind, w["resets_at"])
                        lst = st["samples"].setdefault(key, [])
                        if not lst or t > lst[-1][0]:
                            lst.append((t, float(w["used_percentage"])))
                            del lst[:-200]
                            st["dirty"] = True
                for k in [k for k in st["samples"] if k[1] < now]:
                    del st["samples"][k]
                    st["dirty"] = True
            if st.get("dirty") and now - st.get("saved_at", 0) >= _RATE_LIMIT_SAVE_EVERY_S:
                st["dirty"], st["saved_at"] = False, now
                try:
                    os.makedirs(AOC_DATA_DIR, exist_ok=True)
                    tmp = RATE_LIMIT_SAMPLES_FILE + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(_rate_limit_samples_dump(st["samples"]), f)
                    os.replace(tmp, RATE_LIMIT_SAMPLES_FILE)
                except Exception as e:
                    _log_bg_error("_rate_limit_samples_save", e)
        return _rate_limit_view(st["snap"], dict(st["hits"]),
                                {k: list(v) for k, v in st["samples"].items()}, now)


# ── Context window per CLI session ──────────────────────────────────────────
# hooks/aoc_statusline.py writes CONTEXT_DIR\<session_id>.json from Claude
# Code's statusline input: context_window fill (used_percentage, window
# 200k/1M, input_tokens) + prompt_cache expiry. Without the statusline there
# is no file and the card simply shows no meter.
CONTEXT_DIR = os.path.join(AOC_DATA_DIR, "context")
_CONTEXT_PRUNE_AFTER_S = 3 * 86400
_context_lock = threading.Lock()
_context_state = {"files": {}, "pruned_at": 0.0}  # sid -> (mtime, dict)


def _context_view(raw):
    """Pure: a saved context file -> the /status 'context' dict, or None if
    it isn't usable. level: ok < 70 % <= warn < 85 % <= high (auto-compact
    territory; Claude Code compacts on its own close to the limit)."""
    if not isinstance(raw, dict):
        return None
    pct, window = raw.get("used_percentage"), raw.get("window")
    if not isinstance(pct, (int, float)) or not isinstance(window, (int, float)) or window <= 0:
        return None
    pct = max(0.0, min(100.0, float(pct)))
    exp = raw.get("cache_expires_at")
    rec = raw.get("recache_tokens")
    return {
        "pct": round(pct, 1),
        "window": int(window),
        "tokens": int(raw.get("input_tokens") or 0),
        "level": "high" if pct >= 85 else "warn" if pct >= 70 else "ok",
        "cache_expires_at": exp if isinstance(exp, (int, float)) else None,
        "cache_ttl": raw.get("cache_ttl") if raw.get("cache_ttl") in ("5m", "1h") else None,
        "recache_tokens": int(rec) if isinstance(rec, (int, float)) else None,
        "updated_at": raw.get("updated_at"),
    }


def _context_snapshot(session_ids):
    """{session_id: context view} for the given sessions. Files live in
    %LOCALAPPDATA% (not OneDrive), so a stat per session is cheap; a file is
    re-read only when its mtime changes. Files untouched for 3 days are
    deleted, at most once an hour."""
    now = time.time()
    out = {}
    with _context_lock:
        cache = _context_state["files"]
        for sid in session_ids:
            if not sid or not all(ch.isalnum() or ch in "-_" for ch in sid):
                continue
            path = os.path.join(CONTEXT_DIR, sid + ".json")
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                cache.pop(sid, None)
                continue
            hit = cache.get(sid)
            if not hit or hit[0] != mtime:
                hit = (mtime, _context_view(_load_json_file(path)))
                cache[sid] = hit
            if hit[1]:
                out[sid] = hit[1]
        if now - _context_state["pruned_at"] >= 3600:
            _context_state["pruned_at"] = now
            try:
                for name in os.listdir(CONTEXT_DIR):
                    p = os.path.join(CONTEXT_DIR, name)
                    try:
                        if now - os.stat(p).st_mtime > _CONTEXT_PRUNE_AFTER_S:
                            os.remove(p)
                    except OSError:
                        pass
            except OSError:
                pass
    return out


# Set at the very bottom of the module, once every function the /status
# builder needs is defined. The notify workers below start long before that.
_module_loaded = threading.Event()

def _own_status_payload():
    """The /status payload for in-process consumers (notify/webhook/tray
    workers), or None while the module is still loading. They used to GET
    our own /status over HTTP every 3 s with a 2 s timeout: during OS-level
    stalls the socket round trip timed out and logged a background error per
    worker per tick, although the payload is right here in memory. This is
    the same cached object GET /status serializes -- callers must treat it
    as read-only."""
    if not _module_loaded.is_set():
        return None
    return _build_status_payload()

def _webhook_notify_worker():
    """Server-side counterpart to the client-only _fireWebhook -- mirrors
    _headless_notify_worker's own "poll /status every 3s, diff against a
    _prev dict, act on running->done/error transitions" shape exactly, just
    calling _fire_webhook with the persisted config instead of
    _show_native_toast. This is what makes webhooks actually fire when no
    browser tab is open at all.

    Also tracks stuck agents server-side (mirrors the client's own
    _lastProgressAt/_stuckNotified logic in the JS, which only fires
    _fireWebhook('stuck', ...) while a browser tab is open -- without this,
    'stuck' was the one event type that still silently required a tab).

    Used to bail out entirely with `if not url: continue` -- meaning the
    stuck/burn-spike/cost-spike/waiting-nudge detection below never ran at
    all without a webhook URL configured, and none of those four ever had a
    native toast either (unlike done/error, which get one from the
    completely separate _headless_notify_worker/_tray_monitor loops
    regardless of webhook config). That made them silently invisible in
    --headless mode with no webhook set up and no browser tab open --
    exactly the "nobody's watching" scenario a stuck-agent or
    forgotten-session alert exists for. Detection now always runs;
    _fire_webhook(url, ...) already no-ops safely on an empty url, and a
    toast is fired alongside it gated only on quiet-hours/muted-project
    suppression, mirroring the done/error toast precedent exactly."""
    import urllib.request
    _prev = {}
    _last_progress_at = {}
    _stuck_notified = set()
    _token_history = {}  # session_id -> [(epoch, cumulative_tokens), ...], trimmed to last 10 min
    _burn_notified = set()
    _waiting_notified = set()
    _loop_notified = {}  # session_id -> loop kind already alerted
    _cost_spike_notified = set()
    _project_avg_cost_cache = {}
    # first _db_analytics() refresh (below, every 300 s) lands once the
    # startup grace is over, not on the very first tick
    _project_avg_cost_cache_at = time.time() - 300 + _STARTUP_GRACE_S
    _budget_cost_cache = {}
    _budget_cost_cache_at = 0.0
    _budget_notified = set()  # projects already alerted this calendar month
    _budget_notified_month = [datetime.now().strftime("%Y-%m")]
    _rate_limit_notified = _load_rate_limit_notified(time.time())
    _cache_alert_notified = set()  # (session id, cache expiry) already alerted
    while True:
        try:
            time.sleep(3)
            cfg = _webhook_settings
            url = cfg.get("url", "")
            events = cfg.get("events", {})
            d = _own_status_payload()
            if d is None:
                continue
            agents = {a["id"]: a for a in (d.get("agents") or []) if not str(a.get("id", "")).startswith("hook_")}
            for aid, a in agents.items():
                old = _prev.get(aid, {})
                suppressed = _notification_suppressed(a.get("session_project", ""))
                if old.get("status") == "running":
                    new_status = a.get("status")
                    event = new_status if new_status in ("done", "error") else None
                    if event and not suppressed:
                        # project/model were missing here even though both are
                        # sitting right on `a` -- an external webhook consumer
                        # (Slack, Discord, a custom bot) had no way to tell
                        # which project/model an event was even about without
                        # opening the dashboard, and an `error` notification in
                        # particular carried no error text at all, defeating
                        # much of the point of getting notified without having
                        # to go look.
                        agent_payload = {
                            "id": a.get("id"), "name": a.get("name"), "status": new_status,
                            "cost": round(a.get("estimated_cost") or 0, 4),
                            "project": a.get("session_project") or "",
                            "model": a.get("model") or "",
                        }
                        if new_status == "error" and a.get("error_message"):
                            agent_payload["error_message"] = a["error_message"][:500]
                        _notify_webhook(event, {"event": event, "agent": agent_payload}, agent_payload["project"], url)
                prev_done = len([t for t in (old.get("tasks") or []) if t.get("done")])
                cur_done = len([t for t in (a.get("tasks") or []) if t.get("done")])
                if a.get("status") == "running":
                    if cur_done > prev_done or aid not in _last_progress_at:
                        _last_progress_at[aid] = time.time()
                    if aid not in _stuck_notified and time.time() - _last_progress_at[aid] > 300:
                        _stuck_notified.add(aid)
                        if not suppressed:
                            _notify_webhook("stuck", {
                                "event": "stuck",
                                "agent": {
                                    "id": a.get("id"), "name": a.get("name"), "status": "running",
                                    "cost": round(a.get("estimated_cost") or 0, 4),
                                    "project": a.get("session_project") or "",
                                    "model": a.get("model") or "",
                                },
                            }, a.get("session_project") or "", url)
                            try:
                                _show_native_toast(f"⏸ {a.get('name','Agent')[:40]}", "No task progress for 5+ min",
                                                   kind="stuck", project=a.get("session_project") or "")
                            except Exception:
                                pass
                else:
                    _stuck_notified.discard(aid)
                    _last_progress_at.pop(aid, None)
            _prev = agents

            # Refresh the per-project historical-average-cost cache every
            # 5min (not every 3s tick) -- it's a DB aggregate query and only
            # needs to track slow-moving history, not live session state.
            # No longer gated on events.get("cost_spike") -- that toggle only
            # controls the webhook, but the toast fires independently of it
            # now, and without this cache project_avg stays permanently 0,
            # which would silently make _is_cost_spike() never trigger.
            if time.time() - _project_avg_cost_cache_at > 300:
                try:
                    _project_avg_cost_cache = _project_avg_costs(_db_analytics().get("by_project"))
                except Exception:
                    pass
                _project_avg_cost_cache_at = time.time()

            # Month-to-date per-project cost for budget_alert below, same
            # 5-min refresh cadence as _project_avg_cost_cache above (a DB
            # aggregate, not something that needs per-tick freshness). Also
            # the natural place to detect a new calendar month starting and
            # re-arm _budget_notified -- month-to-date spend only ever goes
            # up within a month, so "the month rolled over" is the one
            # condition that should let an already-alerted project fire
            # again, mirroring how other *_notified sets re-arm on their
            # own condition clearing (e.g. _stuck_notified.discard above).
            if time.time() - _budget_cost_cache_at > 300:
                try:
                    _budget_cost_cache = _project_month_to_date_costs()
                except Exception:
                    pass
                _budget_cost_cache_at = time.time()
                this_month = datetime.now().strftime("%Y-%m")
                if this_month != _budget_notified_month[0]:
                    _budget_notified.clear()
                    _budget_notified_month[0] = this_month

            # Per-project monthly budget: fires once per project per
            # calendar month when month-to-date spend crosses the budget
            # configured in Settings (_notify_settings["project_budgets"]).
            # Consolidates two previously-disconnected client-only signals
            # (a browser-Notification check in renderKpi, tab-open only;
            # and a purely-visual Settings-panel summary that never fired
            # anything) into the same server-side worker every other alert
            # type already goes through, so it works headless too.
            for project, budget in ((_notify_settings.get("project_budgets") or {}).items() if _is_pro() else ()):
                spend = _budget_cost_cache.get(project, 0)
                if spend >= budget and project not in _budget_notified:
                    _budget_notified.add(project)
                    if not _notification_suppressed(project):
                        _notify_webhook("budget_alert", {
                            "event": "budget_alert",
                            "project": project,
                            "spend": round(spend, 2),
                            "budget": round(budget, 2),
                        }, project, url)
                        try:
                            _show_native_toast(
                                f"💸 {project}",
                                f"Monthly budget exceeded: ${spend:.2f} of ${budget:.2f}",
                                kind="budget_alert", project=project,
                            )
                        except Exception:
                            pass

            # Plan rate limits (5-hour / weekly): warn at 80 % or when the
            # current pace reaches 100 % before the reset, and again on a hit.
            # Account-wide, so only quiet hours apply (no project to mute).
            # Escalation: on pace to run out within 30 min -- its own Notify
            # toggle (limit_soon); it stands in for that window's warn toast
            # (not its webhook) when the limit_soon toast or push is on.
            rl_view, rl_now = d.get("rate_limits"), time.time()
            rl_due = _rate_limit_alerts(rl_view, _rate_limit_notified, rl_now)
            rl_covered = set()
            for keys, title, body in _rate_limit_soon_alerts(rl_view, _rate_limit_notified, rl_now):
                _rate_limit_notified.update(keys)
                _save_rate_limit_notified({k for k in _rate_limit_notified if k[1] > time.time()})
                if not _notification_suppressed(""):
                    if _notify_gate("limit_soon", "toast")[0] or _notify_gate("limit_soon", "push")[0]:
                        rl_covered.update(keys)
                    try:
                        _show_native_toast(title, body, kind="limit_soon")
                    except Exception:
                        pass
            for key, title, body, payload in rl_due:
                _rate_limit_notified.add(key)
                _save_rate_limit_notified({k for k in _rate_limit_notified if k[1] > time.time()})
                if not _notification_suppressed(""):
                    _notify_webhook("rate_limit", payload, "", url)
                    if key not in rl_covered:
                        try:
                            _show_native_toast(title, body, kind="rate_limit")
                        except Exception:
                            pass

            # Prompt cache about to expire on a session waiting on you: reply
            # now for cache-read prices, or pay to write the context again.
            now = time.time()
            for key in [k for k in _cache_alert_notified if k[1] <= now]:
                _cache_alert_notified.discard(key)
            sess_by_id = {s.get("id"): s for s in (d.get("sessions_list") or [])}
            for key, title, body in _cache_expiry_alerts(d.get("sessions_list"), _cache_alert_notified, now):
                _cache_alert_notified.add(key)
                proj = (sess_by_id.get(key[0]) or {}).get("project", "")
                if not _notification_suppressed(proj):
                    try:
                        _show_native_toast(title, body, kind="cache_expiry", project=proj)
                    except Exception:
                        pass

            # Context almost full: offer "Save state" (or send it, mode auto)
            # once per crossing, before Claude Code compacts the session.
            try:
                _cs_cfg = _ctxsave_settings()
                for sess, ev in _ctxsave_tick(d.get("sessions_list"), _cs_cfg, now):
                    if ev != "crossed" or _cs_cfg["mode"] == "off":
                        continue
                    if _cs_cfg["mode"] == "auto":
                        _ctxsave_request(sess["id"], now, _cs_cfg["prompt"])
                    proj = sess.get("project", "")
                    if not _notification_suppressed(proj):
                        title, body = _ctxsave_alert(sess, _cs_cfg)
                        _show_native_toast(title, body, kind="ctx_save", project=proj)
            except Exception as e:
                _log_bg_error("context save", e)

            # Token-burn anomaly: a session whose recent tokens/min rate is a
            # multiple of its own session-long average -- catches a runaway
            # loop or retry storm. Live in-memory check, no persisted
            # baseline (see _compute_burn_rates/_is_burn_spike docstrings).
            now = time.time()
            for sess in (d.get("sessions_list") or []):
                sid = sess.get("id")
                if not sid or not sess.get("session_active"):
                    _token_history.pop(sid, None)
                    _burn_notified.discard(sid)
                    _waiting_notified.discard(sid)
                    _cost_spike_notified.discard(sid)
                    _loop_notified.pop(sid, None)
                    continue
                suppressed = _notification_suppressed(sess.get("project", ""))

                # Waiting-too-long nudge: the inverse of the burn-spike check
                # below -- flags a session that's been sitting in
                # waiting_on_you state for hours, so a forgotten session gets
                # surfaced instead of just showing a static WAITING pill
                # forever. waiting_secs is precomputed server-side in
                # _build_status_payload (see _compute_waiting_secs). Kept
                # independent of the burn-rate history-sufficiency check
                # below (which uses `continue` and would otherwise silently
                # skip this too for a session with under 5min of samples).
                if (sess.get("waiting_secs") or 0) > 7200:
                    if sid not in _waiting_notified:
                        _waiting_notified.add(sid)
                        if not suppressed:
                            _notify_webhook("waiting_nudge", {
                                "event": "waiting_nudge",
                                "session": {
                                    "id": sid, "project": sess.get("project"),
                                    "display_name": sess.get("display_name"),
                                    "waiting_secs": sess.get("waiting_secs"),
                                },
                            }, sess.get("project") or "", url)
                            try:
                                ws = sess.get("waiting_secs") or 0
                                wh, wm = int(ws // 3600), int((ws % 3600) // 60)
                                _show_native_toast(
                                    f"⏳ {sess.get('display_name') or sess.get('project') or 'Session'}",
                                    f"Waiting on you for {wh}h {wm}m",
                                    kind="waiting_nudge", project=sess.get("project") or "",
                                )
                            except Exception:
                                pass
                else:
                    _waiting_notified.discard(sid)

                # Loop: the session's recent tool calls look like it's going
                # round in circles (_loop_signal). Once per session and kind,
                # re-armed when the signal clears. Shares the "stuck" toggle.
                loop = sess.get("loop")
                if loop and _loop_notified.get(sid) != loop.get("kind"):
                    _loop_notified[sid] = loop.get("kind")
                    if not suppressed:
                        name = sess.get("display_name") or sess.get("project") or "Session"
                        _notify_webhook("session_loop", {
                            "event": "session_loop",
                            "session": {"id": sid, "project": sess.get("project"), "display_name": name,
                                        "kind": loop.get("kind"), "detail": loop.get("text"),
                                        "cost": round(sess.get("estimated_cost") or 0, 4)},
                        }, sess.get("project") or "", url)
                        try:
                            _show_native_toast(f"⟳ {name[:40]} looks stuck", (loop.get("text") or "")[:120],
                                               kind="session_loop", project=sess.get("project") or "")
                        except Exception:
                            pass
                elif not loop:
                    _loop_notified.pop(sid, None)

                # Cost-spike: a session's *total* cost so far vs. its
                # project's historical per-session average (see
                # _is_cost_spike) -- distinct from the token-*rate*
                # burn-spike below, and kept independent of that check's
                # history-sufficiency `continue` for the same reason
                # waiting_nudge above is.
                project_avg = _project_avg_cost_cache.get(sess.get("project", ""), 0)
                session_cost = sess.get("estimated_cost") or 0
                if _is_cost_spike(session_cost, project_avg):
                    if sid not in _cost_spike_notified:
                        _cost_spike_notified.add(sid)
                        if not suppressed:
                            _notify_webhook("cost_spike", {
                                "event": "cost_spike",
                                "session": {
                                    "id": sid, "project": sess.get("project"),
                                    "display_name": sess.get("display_name"),
                                    "cost": round(session_cost, 4),
                                    "project_avg_cost": round(project_avg, 4),
                                },
                            }, sess.get("project") or "", url)
                            try:
                                _show_native_toast(
                                    f"💰 {sess.get('display_name') or sess.get('project') or 'Session'}",
                                    f"Cost spike: ${session_cost:.2f} (project avg ${project_avg:.2f})",
                                    kind="cost_spike", project=sess.get("project") or "",
                                )
                            except Exception:
                                pass
                else:
                    _cost_spike_notified.discard(sid)

                total = (sess.get("input_tokens") or 0) + (sess.get("output_tokens") or 0)
                hist = _token_history.setdefault(sid, [])
                hist.append((now, total))
                cutoff = now - 600
                while hist and hist[0][0] < cutoff:
                    hist.pop(0)
                rates = _compute_burn_rates(hist, now)
                if not rates:
                    continue
                recent_rate, avg_rate = rates
                if _is_burn_spike(recent_rate, avg_rate):
                    if sid not in _burn_notified:
                        _burn_notified.add(sid)
                        if not suppressed:
                            _notify_webhook("burn_spike", {
                                "event": "burn_spike",
                                "session": {
                                    "id": sid, "project": sess.get("project"),
                                    "display_name": sess.get("display_name"),
                                    "recent_rate": round(recent_rate, 1),
                                    "avg_rate": round(avg_rate, 1),
                                },
                            }, sess.get("project") or "", url)
                            try:
                                _show_native_toast(
                                    f"⚡ {sess.get('display_name') or sess.get('project') or 'Session'}",
                                    f"Burn spike: {recent_rate:.0f} tok/min (avg {avg_rate:.0f})",
                                    kind="burn_spike", project=sess.get("project") or "",
                                )
                            except Exception:
                                pass
                else:
                    _burn_notified.discard(sid)
        except Exception as e:
            _log_bg_error("_webhook_notify_worker", e)

threading.Thread(target=_webhook_notify_worker, daemon=True).start()
