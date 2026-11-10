# AOC server, part 02: SQLite history DB.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── SQLite History DB ──────────────────────────────────────────────────────────

_db_lock = threading.Lock()

def _db_conn():
    c = sqlite3.connect(DB_FILE, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def _init_db():
    with _db_lock:
        c = _db_conn()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                project     TEXT,
                orchestrator TEXT, -- reserved, never populated or read (checked during a
                                   -- coverage/cleanup pass) -- left in place rather than
                                   -- DROP COLUMN'd since this codebase has no established
                                   -- column-removal migration precedent (only ADD COLUMN
                                   -- guards elsewhere in this function) and an empty TEXT
                                   -- column costs nothing to leave. Not a bug that it's
                                   -- always blank.
                date        TEXT,
                started_at  TEXT,
                ended_at    TEXT,
                duration_s  INTEGER DEFAULT 0,
                agents      INTEGER DEFAULT 0,
                done        INTEGER DEFAULT 0,
                errors      INTEGER DEFAULT 0,
                tokens      INTEGER DEFAULT 0,
                cost        REAL    DEFAULT 0,
                task_done   INTEGER DEFAULT 0,
                task_total  INTEGER DEFAULT 0,
                file_count  INTEGER DEFAULT 0,
                snapshot    TEXT
            );
            CREATE TABLE IF NOT EXISTS agents (
                rowid_      INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT,
                agent_id    TEXT,
                name        TEXT,
                unit        TEXT,
                status      TEXT,
                started_at  TEXT,
                completed_at TEXT,
                duration_s  INTEGER DEFAULT 0,
                tokens      INTEGER DEFAULT 0,
                cost        REAL    DEFAULT 0,
                task_done   INTEGER DEFAULT 0,
                task_total  INTEGER DEFAULT 0,
                file_count  INTEGER DEFAULT 0,
                error_msg   TEXT,
                UNIQUE(session_id, agent_id)
            );
            CREATE TABLE IF NOT EXISTS file_changes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT,
                agent_id    TEXT,
                path        TEXT,
                type        TEXT,
                lines       INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_agents_session ON agents(session_id);
            CREATE INDEX IF NOT EXISTS idx_files_session  ON file_changes(session_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_date  ON sessions(date);
        """)
        # Schema migration: detected_via didn't exist when this table was first
        # created, and CREATE TABLE IF NOT EXISTS above doesn't add columns to
        # an already-existing table -- ALTER TABLE has no "IF NOT EXISTS" in
        # SQLite, so just swallow the "duplicate column" error on every run
        # after the first.
        try:
            c.execute("ALTER TABLE agents ADD COLUMN detected_via TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE agents ADD COLUMN concurrent_sessions INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE agents ADD COLUMN model TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE agents ADD COLUMN subagent_type TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE agents ADD COLUMN tool_use_count INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE agents ADD COLUMN parent_id TEXT")
        except sqlite3.OperationalError:
            pass
        # cc_version was tracked live (per-CLI-session, via the transcript
        # scanner backfilling it from the transcript's own metadata -- see
        # _check_new_cc_version) but never persisted, so Session Compare/
        # export could show it for the CURRENT session only; once a session
        # was saved to history or reset, its cc_version was gone for good.
        try:
            c.execute("ALTER TABLE sessions ADD COLUMN cc_version TEXT")
        except sqlite3.OperationalError:
            pass
        # Cumulative seconds this session spent with waiting_on_you=True,
        # for the WAITING ON YOU history trend -- see _accumulate_waiting_time.
        # Like cc_version above, this was only ever live state before now.
        try:
            c.execute("ALTER TABLE sessions ADD COLUMN waiting_on_you_s INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # User-assigned session tags for organizing History beyond project
        # name -- comma-separated (never contains a literal comma per-tag,
        # see _sanitize_tags), edited directly on an already-saved History
        # row rather than going through the live status dict the way
        # session_note does, since tagging is inherently a look-back
        # activity.
        try:
            c.execute("ALTER TABLE sessions ADD COLUMN tags TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # The session's own name (user-set display name or Claude's
        # ai-title), so History can say "Phantom AI" instead of the cwd
        # folder every session started from the home directory shares.
        try:
            c.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
        except sqlite3.OperationalError:
            pass
        c.commit()
        c.close()

_HISTORY_RETENTION_DAYS = 90

def _db_prune_old(days: int = _HISTORY_RETENTION_DAYS):
    """Drop sessions (and their agents/file_changes rows) older than `days`.
    Nothing enforced this before — history.db and agents/file_changes only ever
    grew, one row per session forever. Run once at startup rather than on every
    autosave; a handful of DELETEs is cheap even against a large table."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _db_lock:
        try:
            c = _db_conn()
            old_ids = [r[0] for r in c.execute(
                "SELECT id FROM sessions WHERE date < ?", (cutoff,)).fetchall()]
            if old_ids:
                c.executemany("DELETE FROM agents WHERE session_id=?", [(i,) for i in old_ids])
                c.executemany("DELETE FROM file_changes WHERE session_id=?", [(i,) for i in old_ids])
                c.execute("DELETE FROM sessions WHERE date < ?", (cutoff,))
                c.commit()
            c.close()
        except Exception as e:
            _log_bg_error("_db_prune_old", e)

def _prune_old_logs(days: int = _HISTORY_RETENTION_DAYS):
    """Same retention window as _db_prune_old, applied to logs/session_*.log —
    those also only ever accumulated, one file per session forever."""
    cutoff_epoch = time.time() - days * 86400
    try:
        for p in glob.glob(os.path.join(LOGS_DIR, "session_*.log")):
            try:
                if os.path.getmtime(p) < cutoff_epoch:
                    os.remove(p)
            except Exception:
                pass
    except Exception as e:
        _log_bg_error("_prune_old_logs", e)

# started_at/completed_at are bare "HH:MM:SS" strings with no date component
# (see _clamp_agent_retention_hours' own docstring on the same ambiguity).
# The "midnight crossover" correction below (raw diff negative -> +86400)
# assumes a negative diff always means "wrapped past midnight within the
# same ~24h window" -- but two timestamps that are actually multiple
# calendar days apart with a merely SIMILAR time-of-day (e.g. an autosave
# picking up a stale started_at, or a long-idle session resumed days
# later) also produce a small negative raw diff, and the correction then
# reports a bogus ~86400s duration for what was really days apart, not a
# same-night handful of hours. Real agent runs are bounded by context
# windows and essentially never span this long -- the slowest_agents
# leaderboard (History -> Analytics) surfaced exactly this artifact live
# the first time anything actually looked at individual durations instead
# of only ever averaging them. Any post-correction duration past this
# ceiling is treated as unreliable (stored as 0/unknown) rather than a
# real measurement, rather than trying to guess the true elapsed time.
_MAX_PLAUSIBLE_DURATION_S = 12 * 3600  # 12h -- generous for even an unusually long agent run

def _correct_hms_diff(raw_diff: int) -> int:
    """Apply the midnight-crossover correction (negative raw HH:MM:SS diff
    -> +86400) then cap the result at _MAX_PLAUSIBLE_DURATION_S, treating
    anything past that ceiling as an unreliable multi-day-apart artifact
    rather than a real same-night measurement (see that constant's own
    comment for why). Shared by _db_save_session's session-level and
    per-agent duration computation, which otherwise each inlined this
    identical correction independently."""
    if raw_diff < 0:
        raw_diff += 86400
    if raw_diff > _MAX_PLAUSIBLE_DURATION_S:
        return 0
    return raw_diff

def _db_save_session(status: dict, sid: str = None):
    """Snapshot the current session into history DB. Called on reset or auto-save.
    If sid is provided, INSERT OR REPLACE updates the same record (idempotent auto-save)."""
    with _db_lock:
        try:
            agents = [a for a in status.get("agents", [])
                      if not str(a.get("id", "")).startswith("hook_")]
            if not agents:
                return
            if sid is None:
                sid = datetime.now().strftime("%Y%m%d_%H%M%S")
            project = status.get("project", "")
            started_at = status.get("started_at", "")
            ended_at = _now_ts()
            date = datetime.now().strftime("%Y-%m-%d")
            # cc_version has no top-level status[] mirror the way project/
            # started_at do (see the "update session-level fields" loop in
            # the /update handler) -- it's only ever set per-CLI-session, by
            # the transcript scanner backfilling status["sessions"][sid].
            # Same simplification as project/started_at already make for a
            # multi-session snapshot: picks whichever session has one set,
            # rather than trying to attribute it to one specific session.
            cc_version = next(
                (s.get("cc_version") for s in status.get("sessions", {}).values() if s.get("cc_version")),
                "")
            # Unlike cc_version above (picks whichever session has one),
            # waiting time sums across every session in this snapshot -- the
            # same "attribute it to the whole multi-session snapshot"
            # treatment tokens/cost already get. A still-open waiting period
            # (waiting_on_you currently True) has its elapsed-so-far folded
            # in too, so saving mid-wait doesn't silently drop that tail --
            # see _accumulate_waiting_time, which only commits to
            # waiting_on_you_accum_s on the *next* transition back to False.
            _save_now_epoch = time.time()
            waiting_on_you_s = 0
            for s in status.get("sessions", {}).values():
                waiting_on_you_s += s.get("waiting_on_you_accum_s", 0) or 0
                if s.get("waiting_on_you") and s.get("waiting_on_you_since"):
                    waiting_on_you_s += max(0, _save_now_epoch - s["waiting_on_you_since"])
            waiting_on_you_s = int(waiting_on_you_s)

            all_tasks    = [t for a in agents for t in (a.get("tasks") or [])]
            task_done    = sum(1 for t in all_tasks if t.get("done"))
            file_count   = len({f["path"] for a in agents for f in (a.get("files_changed") or [])})

            dur_s = 0
            if started_at:
                try:
                    h, m, s = (int(x) for x in started_at.split(":"))
                    h2, m2, s2 = (int(x) for x in ended_at.split(":"))
                    dur_s = _correct_hms_diff((h2*3600+m2*60+s2) - (h*3600+m*60+s))
                except Exception:
                    pass

            snapshot = _pack_snapshot(status)

            c = _db_conn()

            # Auto-save re-runs every 5 min against the same sid (see docstring).
            # file_changes has no unique constraint, so a plain INSERT below would
            # re-append the same files on every cycle; clear this session's rows
            # first so each save reflects the current file list exactly once.
            c.execute("DELETE FROM file_changes WHERE session_id=?", (sid,))

            for a in agents:
                tok = a.get("tokens_used", 0) or 0
                # Prefer the real per-model cost (set by /update when the hook sent
                # granular usage) over the flat-rate guess, so History stays
                # consistent with whatever was actually shown live for this agent.
                a_cost = a["estimated_cost"] if a.get("estimated_cost") is not None else tok/1_000_000*9
                a_tasks = a.get("tasks") or []
                a_done  = sum(1 for t in a_tasks if t.get("done"))
                a_files = a.get("files_changed") or []
                a_dur   = 0
                if a.get("started_at") and a.get("completed_at"):
                    try:
                        h,m,s = (int(x) for x in a["started_at"].split(":"))
                        h2,m2,s2 = (int(x) for x in a["completed_at"].split(":"))
                        a_dur = _correct_hms_diff((h2*3600+m2*60+s2)-(h*3600+m*60+s))
                    except Exception:
                        pass
                # ON CONFLICT DO UPDATE (not INSERT OR IGNORE / OR REPLACE): auto-save
                # reruns against the same (session_id, agent_id) every 5 min while the
                # agent may still be running, so a stale first snapshot must be refreshed
                # in place — OR IGNORE froze it at first-seen status forever, and OR REPLACE
                # would churn rowid_ (and the session-detail ORDER BY rowid_) on every save.
                c.execute("""
                    INSERT INTO agents
                    (session_id, agent_id, name, unit, status, started_at, completed_at,
                     duration_s, tokens, cost, task_done, task_total, file_count, error_msg,
                     detected_via, concurrent_sessions, model, subagent_type, tool_use_count, parent_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(session_id, agent_id) DO UPDATE SET
                        name=excluded.name, unit=excluded.unit, status=excluded.status,
                        started_at=excluded.started_at, completed_at=excluded.completed_at,
                        duration_s=excluded.duration_s, tokens=excluded.tokens, cost=excluded.cost,
                        task_done=excluded.task_done, task_total=excluded.task_total,
                        file_count=excluded.file_count, error_msg=excluded.error_msg,
                        detected_via=COALESCE(agents.detected_via, excluded.detected_via),
                        concurrent_sessions=COALESCE(agents.concurrent_sessions, excluded.concurrent_sessions),
                        model=COALESCE(agents.model, excluded.model),
                        subagent_type=COALESCE(agents.subagent_type, excluded.subagent_type),
                        tool_use_count=COALESCE(agents.tool_use_count, excluded.tool_use_count),
                        parent_id=COALESCE(agents.parent_id, excluded.parent_id)
                """, (sid, a.get("id",""), a.get("name",""), a.get("unit",""),
                      a.get("status",""), a.get("started_at",""), a.get("completed_at",""),
                      a_dur, tok, a_cost, a_done, len(a_tasks), len(a_files),
                      a.get("error_message",""), a.get("detected_via"), a.get("concurrent_sessions"),
                      a.get("model"), a.get("subagent_type"), a.get("tool_use_count"), a.get("parent_id")))
                for f in a_files:
                    c.execute("""
                        INSERT INTO file_changes (session_id, agent_id, path, type, lines)
                        VALUES (?,?,?,?,?)
                    """, (sid, a.get("id",""), f.get("path",""), f.get("type","changed"),
                          f.get("lines", 0) or 0))

            # Derived from the agents table *after* the upsert loop above, not
            # from the in-memory `agents` list this function started with --
            # that list only holds what _agent_retention_worker hasn't pruned
            # yet (old done/error agents get cleared from live status every
            # ~15min so TIMELINE/SUMMARY/etc. don't grow forever), while each
            # row upserted into the agents table stays there for this
            # session's lifetime (only _db_prune_old's whole-session cascade
            # delete ever removes it). Using SUM(agents.cost) also means this
            # total inherits the real per-model cost each row already carries
            # (see a_cost above) instead of re-deriving one flat-rate guess
            # from the session's total tokens regardless of model mix.
            totals = c.execute("""
                SELECT COUNT(*) as agents,
                       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors,
                       SUM(tokens) as tokens, SUM(cost) as cost
                FROM agents WHERE session_id=?
            """, (sid,)).fetchone()

            c.execute("""
                INSERT INTO sessions
                (id, project, date, started_at, ended_at, duration_s,
                 agents, done, errors, tokens, cost, task_done, task_total, file_count, snapshot, cc_version,
                 waiting_on_you_s)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                -- Upsert, not INSERT OR REPLACE: REPLACE deletes the row
                -- first, so every auto-save of a live session wiped the
                -- tags (and title) a user had already put on it.
                ON CONFLICT(id) DO UPDATE SET
                    project=excluded.project, date=excluded.date, started_at=excluded.started_at,
                    ended_at=excluded.ended_at, duration_s=excluded.duration_s,
                    agents=excluded.agents, done=excluded.done, errors=excluded.errors,
                    tokens=excluded.tokens, cost=excluded.cost, task_done=excluded.task_done,
                    task_total=excluded.task_total, file_count=excluded.file_count,
                    snapshot=excluded.snapshot, cc_version=excluded.cc_version,
                    waiting_on_you_s=excluded.waiting_on_you_s
            """, (sid, project, date, started_at, ended_at, dur_s,
                  totals["agents"], totals["done"] or 0, totals["errors"] or 0,
                  totals["tokens"] or 0, totals["cost"] or 0.0,
                  task_done, len(all_tasks), file_count, snapshot, cc_version, waiting_on_you_s))

            c.commit()
            c.close()
        except Exception as e:
            # never crash the server over DB -- but this used to mean a
            # failure here (from /history/save_current or /reset) was
            # completely invisible: the request handler gets a normal
            # response, the client sees success, and the session's history
            # just silently never gets persisted.
            _log_bg_error("_db_save_session", e)

def _tags_list(raw: str) -> list:
    """Turn the comma-separated tags column back into a list for the
    client -- the inverse of _sanitize_tags' ",".join(tags)."""
    return [t for t in (raw or "").split(",") if t]

def _sanitize_tags(tags) -> list:
    """Whitelist + normalize a user-supplied tag list: strip, drop blanks,
    dedupe case-insensitively (keeping first-seen casing), cap length per
    tag and total count -- same defensive shape _sanitize_project_budgets
    already uses for structured user input. No tag may contain a comma
    (the column's own separator) since that would silently merge two tags
    back into one on the next read."""
    if not isinstance(tags, list):
        return []
    out = []
    seen = set()
    for t in tags:
        t = str(t).replace(",", "").strip()[:30]
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= 10:
            break
    return out

def _db_set_session_tags(sid: str, tags) -> bool:
    """Persist a saved session's tags straight to history.db -- unlike
    session_note (live status-dict state, only snapshotted into the DB at
    save time), tags are edited on an ALREADY-saved History row, so this
    writes the DB directly rather than going through _db_save_session.
    Returns False (no row updated) if sid doesn't exist, same "can't
    attach to a session AOC never saved" guard _set_session_note has."""
    tags_str = ",".join(_sanitize_tags(tags))
    with _db_lock:
        try:
            c = _db_conn()
            cur = c.execute("UPDATE sessions SET tags=? WHERE id=?", (tags_str, sid))
            c.commit()
            ok = cur.rowcount > 0
            c.close()
            return ok
        except Exception as e:
            _log_bg_error("_db_set_session_tags", e)
            return False

def _db_get_sessions(limit=100):
    """Return list of past sessions newest-first."""
    with _db_lock:
        try:
            c = _db_conn()
            rows = c.execute("""
                SELECT id, project, date, started_at, ended_at, duration_s,
                       agents, done, errors, tokens, cost, task_done, task_total, file_count, cc_version, tags, title
                FROM sessions ORDER BY rowid DESC LIMIT ?
            """, (limit,)).fetchall()
            c.close()
            out = [dict(r) for r in rows]
            for r in out:
                r["tags"] = _tags_list(r.get("tags"))
            return out
        except Exception:
            return []

def _db_search_sessions(query: str = "", date_from: str = "", date_to: str = "", limit: int = 200):
    """Search past sessions by project name / session id / tag and/or date
    range, uncapped by _db_get_sessions' fixed 100-row window -- the
    /history endpoint only ever returns the 100 most recent sessions, so
    anything older is otherwise unreachable from the client. Empty
    query/date_from/date_to mean "no filter on that dimension", matching
    _export_costs_csv's existing convention for its own date params.
    Uses idx_sessions_date for the date-range half."""
    with _db_lock:
        try:
            c = _db_conn()
            like = f"%{query}%"
            rows = c.execute("""
                SELECT id, project, date, started_at, ended_at, duration_s,
                       agents, done, errors, tokens, cost, task_done, task_total, file_count, cc_version, tags, title
                FROM sessions
                WHERE (? = '' OR project LIKE ? OR id LIKE ? OR tags LIKE ? OR title LIKE ?)
                  AND (? = '' OR date >= ?)
                  AND (? = '' OR date <= ?)
                ORDER BY rowid DESC LIMIT ?
            """, (query, like, like, like, like, date_from, date_from, date_to, date_to, limit)).fetchall()
            c.close()
            out = [dict(r) for r in rows]
            for r in out:
                r["tags"] = _tags_list(r.get("tags"))
            return out
        except Exception:
            return []

def _db_get_errors(limit=100):
    """Return past agent errors newest-first, with session/project context —
    backs the ERRORS tab. A per-agent error box already exists in the live
    UI but disappears ~15s after the agent errors (scheduleRemove); this is
    the persistent view across sessions."""
    with _db_lock:
        try:
            c = _db_conn()
            rows = c.execute("""
                SELECT a.session_id, a.agent_id, a.name, a.unit, a.completed_at,
                       a.error_msg, a.model, a.detected_via, a.concurrent_sessions,
                       s.project, s.date
                FROM agents a JOIN sessions s ON s.id = a.session_id
                WHERE a.status = 'error' AND a.error_msg != ''
                ORDER BY a.rowid_ DESC LIMIT ?
            """, (limit,)).fetchall()
            c.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

def _pack_snapshot(status) -> bytes:
    """A session's saved dashboard snapshot, zlib-compressed. Snapshots were
    ~98% of history.db (123 MB of 125 MB -- mostly agents' log arrays) and
    compress ~10x; that size is what made every hourly backup, its integrity
    check and the OneDrive upload of it heavy enough to stall the monitor.
    Stored as a BLOB in the same TEXT-declared column (SQLite is dynamically
    typed), so old rows and old backups with plain JSON text still read."""
    import zlib
    return sqlite3.Binary(zlib.compress(json.dumps(status, ensure_ascii=False).encode("utf-8"), 6))

def _unpack_snapshot(value):
    """Inverse of _pack_snapshot; also reads legacy plain-JSON text. None for
    an empty value."""
    if not value:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        import zlib
        return json.loads(zlib.decompress(bytes(value)).decode("utf-8"))
    return json.loads(value)

def _db_compress_snapshots(batch=25):
    """One pass at startup: compress every legacy plain-text snapshot in
    place (a batch per lock hold, so requests aren't blocked for the whole
    pass), then VACUUM once so the file actually shrinks. Returns the number
    of rows converted; a no-op once everything is compressed."""
    import zlib
    converted = 0
    try:
        while True:
            with _db_lock:
                c = _db_conn()
                rows = c.execute("SELECT id, snapshot FROM sessions WHERE typeof(snapshot) = 'text' "
                                 "AND snapshot != '' LIMIT ?", (batch,)).fetchall()
                for r in rows:
                    # compress the stored text as-is (no parse): identical to
                    # _pack_snapshot's output for valid JSON, and a malformed
                    # legacy row can't wedge the pass on the same row forever
                    c.execute("UPDATE sessions SET snapshot = ? WHERE id = ?",
                              (sqlite3.Binary(zlib.compress(r[1].encode("utf-8"), 6)), r[0]))
                c.commit()
                c.close()
            converted += len(rows)
            if len(rows) < batch:
                break
            time.sleep(0.05)  # let queued DB users in between batches
        if converted:
            with _db_lock:
                c = _db_conn()
                c.execute("VACUUM")
                c.close()
    except Exception as e:
        _log_bg_error("_db_compress_snapshots", e)
    return converted

def _db_get_session_detail(sid: str):
    """Return snapshot JSON for a session."""
    with _db_lock:
        try:
            c = _db_conn()
            row = c.execute("SELECT snapshot, cc_version, tags FROM sessions WHERE id=?", (sid,)).fetchone()
            agents = c.execute("""
                SELECT agent_id, name, unit, status, started_at, completed_at,
                       duration_s, tokens, cost, task_done, task_total, file_count, error_msg, model,
                       subagent_type, tool_use_count, detected_via, concurrent_sessions, parent_id
                FROM agents WHERE session_id=? ORDER BY rowid_
            """, (sid,)).fetchall()
            files = c.execute("""
                SELECT agent_id, path, type, lines FROM file_changes WHERE session_id=?
            """, (sid,)).fetchall()
            c.close()
            return {
                "snapshot": _unpack_snapshot(row["snapshot"]) if row else None,
                "cc_version": (row["cc_version"] if row else None) or "",
                "tags": _tags_list(row["tags"] if row else None),
                "agents": [dict(a) for a in agents],
                "files":  [dict(f) for f in files],
            }
        except Exception:
            return {}

def _db_analytics():
    """Return aggregate stats for the analytics view."""
    import datetime as _dt
    today_str = _dt.date.today().isoformat()
    with _db_lock:
        try:
            c = _db_conn()
            # agents/done/errors/tokens/cost come from the agents table
            # directly (COUNT(*)/SUM(...) over every row ever upserted for
            # these sessions) rather than from sessions.agents/done/errors/
            # tokens/cost -- those columns get written by _db_save_session
            # from whatever's currently in the live in-memory agent list at
            # save time, which _agent_retention_worker periodically prunes
            # (old done/error agents cleared every ~15min so TIMELINE/
            # SUMMARY/etc. don't grow forever). A session that's since been
            # closed never gets re-saved to self-heal that, so trusting the
            # sessions columns here would keep understating lifetime totals
            # (and, for cost, ignoring each agent's own real per-model price
            # in favor of a flat-rate guess) even after _db_save_session's
            # own write path was fixed to stop introducing new drift.
            total_sessions = c.execute("""
                SELECT COUNT(*) as sessions, SUM(file_count) as files FROM sessions
            """).fetchone()
            total_agents = c.execute("""
                SELECT COUNT(*) as agents,
                       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors,
                       SUM(tokens) as tokens, SUM(cost) as cost
                FROM agents
            """).fetchone()
            # CLI sessions with no subagents have no agents rows at all: their
            # tokens/cost (the main conversation, from the transcript scanner)
            # live only in sessions.*. Without them every agents-table sum
            # below missed most of the spend -- days of plain CLI work showed
            # $0 (and the header's week-over-week cost read "-100 %").
            cli_only = c.execute("""
                SELECT date, project, COALESCE(tokens, 0) AS tokens, COALESCE(cost, 0) AS cost
                FROM sessions s WHERE NOT EXISTS (SELECT 1 FROM agents a WHERE a.session_id = s.id)
            """).fetchall()
            total = {
                "sessions": total_sessions["sessions"], "files": total_sessions["files"],
                "agents": total_agents["agents"], "done": total_agents["done"],
                "errors": total_agents["errors"],
                "tokens": (total_agents["tokens"] or 0) + sum(r["tokens"] for r in cli_only),
                "cost": (total_agents["cost"] or 0.0) + sum(r["cost"] for r in cli_only),
            }
            today_sessions = c.execute("""
                SELECT COUNT(*) as sessions, AVG(duration_s) as avg_duration_s
                FROM sessions WHERE date=?
            """, (today_str,)).fetchone()
            today_agents = c.execute("""
                SELECT COUNT(*) as agents,
                       SUM(CASE WHEN a.status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN a.status='error' THEN 1 ELSE 0 END) as errors,
                       SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE s.date=?
            """, (today_str,)).fetchone()
            today = {
                "sessions": today_sessions["sessions"], "avg_duration_s": today_sessions["avg_duration_s"],
                "agents": today_agents["agents"], "done": today_agents["done"],
                "errors": today_agents["errors"],
                "tokens": (today_agents["tokens"] or 0) + sum(r["tokens"] for r in cli_only if r["date"] == today_str),
                "cost": (today_agents["cost"] or 0.0) + sum(r["cost"] for r in cli_only if r["date"] == today_str),
            }
            # Same agents-table-is-authoritative fix as total/today above,
            # applied to the day/project breakdowns -- tokens/cost/agents/
            # done/errors are joined in from the agents table (grouped by
            # the owning session's date/project) rather than trusted from
            # sessions.*, which stays the source only for fields the agents
            # table has no equivalent for (session count, task_done/
            # task_total, waiting_on_you_s, avg_duration_s). Two queries
            # merged in Python rather than one JOIN...GROUP BY, so a date/
            # project with sessions but zero agents rows still shows up
            # (an INNER JOIN would silently drop it, same bug this is fixing
            # just relocated) with agents/tokens/cost defaulting to 0.
            by_day_sessions = c.execute("""
                SELECT date, COUNT(*) as sessions, AVG(duration_s) as avg_duration_s,
                       SUM(task_done) as task_done, SUM(task_total) as task_total,
                       SUM(waiting_on_you_s) as waiting_on_you_s
                FROM sessions GROUP BY date ORDER BY date DESC LIMIT 30
            """).fetchall()
            by_day_agents = {r["date"]: r for r in c.execute("""
                SELECT s.date as date, SUM(a.tokens) as tokens, SUM(a.cost) as cost,
                       COUNT(*) as agents,
                       SUM(CASE WHEN a.status='error' THEN 1 ELSE 0 END) as errors
                FROM agents a JOIN sessions s ON a.session_id = s.id
                GROUP BY s.date
            """).fetchall()}
            by_day = []
            for r in by_day_sessions:
                ag = by_day_agents.get(r["date"])
                cli_tok = sum(x["tokens"] for x in cli_only if x["date"] == r["date"])
                cli_cost = sum(x["cost"] for x in cli_only if x["date"] == r["date"])
                by_day.append({
                    "date": r["date"], "sessions": r["sessions"],
                    "tokens": ((ag["tokens"] or 0) if ag else 0) + cli_tok,
                    "cost": ((ag["cost"] or 0.0) if ag else 0.0) + cli_cost,
                    "agents": ag["agents"] if ag else 0, "errors": ag["errors"] if ag else 0,
                    "avg_duration_s": r["avg_duration_s"],
                    "task_done": r["task_done"], "task_total": r["task_total"],
                    "waiting_on_you_s": r["waiting_on_you_s"],
                })
            by_project_sessions = c.execute("""
                SELECT project, COUNT(*) as sessions,
                       SUM(task_done) as task_done, SUM(task_total) as task_total,
                       SUM(waiting_on_you_s) as waiting_on_you_s
                FROM sessions WHERE project != '' GROUP BY project
            """).fetchall()
            by_project_agents = {r["project"]: r for r in c.execute("""
                SELECT s.project as project, SUM(a.tokens) as tokens, SUM(a.cost) as cost,
                       COUNT(*) as agents,
                       SUM(CASE WHEN a.status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN a.status='error' THEN 1 ELSE 0 END) as errors
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE s.project != ''
                GROUP BY s.project
            """).fetchall()}
            by_project_all = []
            for r in by_project_sessions:
                ag = by_project_agents.get(r["project"])
                cli_tok = sum(x["tokens"] for x in cli_only if x["project"] == r["project"])
                cli_cost = sum(x["cost"] for x in cli_only if x["project"] == r["project"])
                by_project_all.append({
                    "project": r["project"], "sessions": r["sessions"],
                    "tokens": ((ag["tokens"] or 0) if ag else 0) + cli_tok,
                    "cost": ((ag["cost"] or 0.0) if ag else 0.0) + cli_cost,
                    "agents": ag["agents"] if ag else 0, "done": ag["done"] if ag else 0,
                    "errors": ag["errors"] if ag else 0,
                    "task_done": r["task_done"], "task_total": r["task_total"],
                    "waiting_on_you_s": r["waiting_on_you_s"],
                })
            by_project = sorted(by_project_all, key=lambda x: -(x["cost"] or 0))[:20]
            # Total agents whose *start* was only ever caught by the transcript
            # fallback scanner, never by Claude Code's own PreToolUse hook --
            # quantifies how often that hook dispatch actually drops an event
            # (previously invisible: the fallback silently fixed it with no
            # persistent count anywhere).
            hook_miss_row = c.execute(
                "SELECT COUNT(*) as n FROM agents WHERE detected_via='transcript'"
            ).fetchone()
            hook_misses = hook_miss_row["n"] if hook_miss_row else 0
            # Tests the hypothesis that the hook dispatch drops PreToolUse more
            # often when multiple CLI sessions are concurrently active (their
            # hook events competing/racing) rather than being purely random.
            concurrency_rows = c.execute("""
                SELECT detected_via='transcript' as is_hook_miss,
                       AVG(concurrent_sessions) as avg_concurrent, COUNT(*) as n
                FROM agents WHERE concurrent_sessions IS NOT NULL
                GROUP BY is_hook_miss
            """).fetchall()
            hook_miss_concurrency = {
                ("hook_miss" if r["is_hook_miss"] else "normal"): {
                    "avg_concurrent_sessions": round(r["avg_concurrent"], 2) if r["avg_concurrent"] is not None else None,
                    "n": r["n"],
                } for r in concurrency_rows
            }
            by_day_project = c.execute("""
                SELECT s.date as date, s.project as project,
                       SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE s.project != ''
                GROUP BY s.date, s.project ORDER BY s.date ASC
            """).fetchall()
            _bdp = {(r["date"], r["project"]): {"date": r["date"], "project": r["project"],
                                                "tokens": r["tokens"] or 0, "cost": r["cost"] or 0.0}
                    for r in by_day_project}
            for x in cli_only:
                if x["project"]:
                    d = _bdp.setdefault((x["date"], x["project"]),
                                        {"date": x["date"], "project": x["project"], "tokens": 0, "cost": 0.0})
                    d["tokens"] += x["tokens"]
                    d["cost"] += x["cost"]
            by_day_project = sorted(_bdp.values(), key=lambda d: d["date"] or "")
            # Cost/token breakdown by model (Sonnet vs Opus vs Haiku, etc.) --
            # only ever populated for agents saved since the `model` column
            # was added, so old rows are simply absent rather than showing a
            # blank/garbage bucket.
            # avg_duration_s excludes implausible/zero durations the same way
            # slowest_agents' WHERE clause does (see that query's own comment) --
            # this is a per-model average, so a single ~86400s midnight-crossover
            # artifact would otherwise skew it far worse than one bad row in a
            # 20-row leaderboard.
            by_model = c.execute("""
                SELECT model, COUNT(*) as agents, SUM(tokens) as tokens, SUM(cost) as cost,
                       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors,
                       SUM(tool_use_count) as tool_use_count,
                       AVG(CASE WHEN duration_s > 0 AND duration_s <= ? THEN duration_s END) as avg_duration_s
                FROM agents WHERE model IS NOT NULL AND model != ''
                GROUP BY model ORDER BY SUM(cost) DESC
            """, (_MAX_PLAUSIBLE_DURATION_S,)).fetchall()
            # Day-level counterpart to by_model, same idea as by_day_project --
            # agents has no date of its own (only time-of-day started_at/
            # completed_at), so this needs the session it belongs to for the
            # date, unlike by_model/by_project which both aggregate a single
            # table directly.
            by_day_model = c.execute("""
                SELECT s.date as date, a.model as model, SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE a.model IS NOT NULL AND a.model != ''
                GROUP BY s.date, a.model ORDER BY s.date ASC
            """).fetchall()
            # Cost/token breakdown by subagent_type (Explore vs Plan vs
            # general-purpose, etc.) -- same shape as by_model, just grouped
            # by the other identifying field an agent already carries.
            # subagent_type was already shown per-agent (card badge, Compare,
            # Markdown export) but never aggregated across history before.
            by_subagent_type = c.execute("""
                SELECT subagent_type, COUNT(*) as agents, SUM(tokens) as tokens, SUM(cost) as cost,
                       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors,
                       SUM(tool_use_count) as tool_use_count
                FROM agents WHERE subagent_type IS NOT NULL AND subagent_type != ''
                GROUP BY subagent_type ORDER BY SUM(cost) DESC
            """).fetchall()
            # Day-level counterpart, same agents-JOIN-sessions shape
            # by_day_model already established (agents carry no date of
            # their own).
            by_day_subagent_type = c.execute("""
                SELECT s.date as date, a.subagent_type as subagent_type, SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE a.subagent_type IS NOT NULL AND a.subagent_type != ''
                GROUP BY s.date, a.subagent_type ORDER BY s.date ASC
            """).fetchall()
            # Day-level counterpart to hook_misses/hook_miss_concurrency above --
            # those only ever surface an all-time total, with no way to see
            # whether the hook's reliability is trending better or worse over
            # time. Same sessions join as by_day_model, for the same reason
            # (agents carries no date of its own).
            by_day_hook_reliability = c.execute("""
                SELECT s.date as date,
                       SUM(CASE WHEN a.detected_via='transcript' THEN 1 ELSE 0 END) as misses,
                       COUNT(*) as total
                FROM agents a JOIN sessions s ON a.session_id = s.id
                GROUP BY s.date ORDER BY s.date ASC
            """).fetchall()
            # Most-frequently-changed files across all history -- file_changes
            # has every file any agent ever touched (path/type/lines, one row
            # per change) but was only ever queried per-session (the FILES
            # tab) before this, never aggregated. changes counts every touch
            # (a file edited by 3 different agents in 3 different sessions
            # counts 3), sessions is the distinct-session version of the same
            # thing -- the gap between the two says whether a file's edits
            # cluster in one session or are spread out over time.
            file_hotspots = c.execute("""
                SELECT path, COUNT(*) as changes, COUNT(DISTINCT session_id) as sessions,
                       SUM(lines) as total_lines
                FROM file_changes WHERE path != ''
                GROUP BY path ORDER BY COUNT(*) DESC LIMIT 20
            """).fetchall()
            # Day-level counterpart, same agents-JOIN-sessions shape by_day_model
            # already established -- scoped to just the top-20 paths above
            # (not every file ever touched) so this stays a small, bounded
            # query instead of a date x every-distinct-path cross product.
            top_paths = [r["path"] for r in file_hotspots]
            if top_paths:
                placeholders = ",".join("?" * len(top_paths))
                by_day_file_hotspots = c.execute(f"""
                    SELECT s.date as date, fc.path as path, COUNT(*) as changes
                    FROM file_changes fc JOIN sessions s ON fc.session_id = s.id
                    WHERE fc.path IN ({placeholders})
                    GROUP BY s.date, fc.path ORDER BY s.date ASC
                """, top_paths).fetchall()
            else:
                by_day_file_hotspots = []
            # File-type breakdown -- same file_changes source as file_hotspots
            # above, grouped by extension instead of path (which KIND of work
            # generates the most changes: .py vs .js vs .md, etc.). SQLite has
            # no clean "text after the last dot" builtin without a recursive
            # CTE, so this aggregates in Python instead of SQL -- file_changes
            # is a personal dev tool's history, not a scale where that matters.
            _ext_agg = {}
            for r in c.execute("SELECT path, lines, session_id FROM file_changes WHERE path != ''").fetchall():
                name = r["path"].replace("\\", "/").rsplit("/", 1)[-1]
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else "(no extension)"
                bucket = _ext_agg.setdefault(ext, {"ext": ext, "changes": 0, "total_lines": 0, "_sessions": set()})
                bucket["changes"] += 1
                bucket["total_lines"] += r["lines"] or 0
                bucket["_sessions"].add(r["session_id"])
            by_file_type = sorted(
                ({"ext": b["ext"], "changes": b["changes"], "sessions": len(b["_sessions"]), "total_lines": b["total_lines"]}
                 for b in _ext_agg.values()),
                key=lambda x: -x["changes"])[:20]
            # Tag cloud -- every distinct tag used across history, ranked by
            # how many sessions carry it. Same reason as by_file_type above:
            # tags live in one comma-separated column (_sanitize_tags), so
            # this aggregates in Python rather than needing SQL to explode a
            # delimited string. Without this, the only way to discover what
            # tags you'd already used was to remember them or browse
            # individual sessions one at a time.
            _tag_counts = {}
            for r in c.execute("SELECT tags FROM sessions WHERE tags != ''").fetchall():
                for t in _tags_list(r["tags"]):
                    _tag_counts[t] = _tag_counts.get(t, 0) + 1
            tag_cloud = sorted(
                ({"tag": t, "count": n} for t, n in _tag_counts.items()),
                key=lambda x: -x["count"])[:30]
            # Cost by tag -- same Python aggregation as tag_cloud above (one
            # comma-separated column), but attributing a session's full
            # cost/tokens/done/errors to EVERY tag it carries rather than
            # just counting participation. A session with 2 tags contributes
            # its full total to both -- the same double-counting any tag
            # cloud has, not a strict partition of cost across tags.
            _tag_cost_agg = {}
            for r in c.execute("SELECT tags, cost, tokens, done, errors, agents FROM sessions WHERE tags != ''").fetchall():
                for t in _tags_list(r["tags"]):
                    bucket = _tag_cost_agg.setdefault(t, {"tag": t, "sessions": 0, "cost": 0.0, "tokens": 0, "done": 0, "errors": 0, "agents": 0})
                    bucket["sessions"] += 1
                    bucket["cost"] += r["cost"] or 0
                    bucket["tokens"] += r["tokens"] or 0
                    bucket["done"] += r["done"] or 0
                    bucket["errors"] += r["errors"] or 0
                    bucket["agents"] += r["agents"] or 0
            by_tag = sorted(_tag_cost_agg.values(), key=lambda x: -x["cost"])[:20]
            # Retry detection -- an agent named the same as an earlier,
            # errored one, run again in that SAME session. Exact-name match
            # only (no fuzzy matching): Claude Code doesn't rename a retried
            # Task call, so this is exactly what "you re-ran the same thing
            # after it failed" looks like, the same reasoning common_errors'
            # exact-text grouping already uses. Grouped and walked in Python
            # (ordered by rowid_ within each session+name group, i.e.
            # insertion order) since this needs an ordered same-group
            # comparison SQL doesn't do cheaply.
            _retry_groups = {}
            for r in c.execute("""
                SELECT session_id, name, status, error_msg FROM agents
                WHERE name IS NOT NULL AND name != ''
                ORDER BY session_id, name, rowid_
            """).fetchall():
                _retry_groups.setdefault((r["session_id"], r["name"]), []).append((r["status"], r["error_msg"]))
            _retry_agg = {}
            for (sid, name), attempts in _retry_groups.items():
                if len(attempts) < 2:
                    continue
                for i in range(len(attempts) - 1):
                    status_i, err_i = attempts[i]
                    if status_i == "error":
                        bucket = _retry_agg.setdefault(name, {"name": name, "retries": 0, "_sessions": set(), "last_error": ""})
                        bucket["retries"] += 1
                        bucket["_sessions"].add(sid)
                        # "last" here means last processed, not strictly
                        # chronological (rows are ordered per session+name
                        # group, not globally) -- good enough to answer "why
                        # does this keep failing" without a second query.
                        if err_i:
                            bucket["last_error"] = err_i
            retry_count = sum(b["retries"] for b in _retry_agg.values())
            retry_patterns = sorted(
                ({"name": b["name"], "retries": b["retries"], "sessions": len(b["_sessions"]), "last_error": b["last_error"]}
                 for b in _retry_agg.values()),
                key=lambda x: -x["retries"])[:20]
            # Longest-running individual agents -- duration_s has always been
            # stored per agent but never surfaced as a ranked list, only ever
            # averaged into today's session-level avg_duration_s. A leaderboard
            # rather than a GROUP BY aggregate (unlike everything else in this
            # function): each row is one specific agent run, not a summary
            # bucket, so it can be joined against subagent_type/model to spot
            # which combination tends to run longest.
            # duration_s > _MAX_PLAUSIBLE_DURATION_S excluded defensively, not
            # just relying on the write-side cap in _db_save_session -- rows
            # already saved before that cap existed can still carry a bogus
            # ~86400s "midnight crossover" artifact (see that function's own
            # comment on why), and this leaderboard is exactly the place that
            # would otherwise surface them as if they were real.
            slowest_agents = c.execute("""
                SELECT a.agent_id as agent_id, a.name as name, a.subagent_type as subagent_type,
                       a.model as model, a.session_id as session_id, s.project as project,
                       a.duration_s as duration_s, a.status as status, a.cost as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE a.duration_s > 0 AND a.duration_s <= ?
                ORDER BY a.duration_s DESC LIMIT 20
            """, (_MAX_PLAUSIBLE_DURATION_S,)).fetchall()
            # Most frequently recurring error messages -- error_msg has
            # always been persisted per errored agent and shown in the
            # ERRORS panel, but only ever as a flat chronological list,
            # never grouped to answer "what keeps breaking". Exact-text
            # grouping (not fuzzy matching, which SQL can't do cheaply) --
            # a genuinely recurring failure (the same exception, the same
            # timeout) produces identical text every time it fires, so this
            # still catches the case that matters even though a message
            # containing something unique per-occurrence (a file path, a
            # timestamp) won't cluster. The two correlated subqueries pull
            # the most recent occurrence's session/project for click-through,
            # since GROUP BY alone can't give "the rest of that one row".
            common_errors = c.execute("""
                SELECT a1.error_msg as error_msg, COUNT(*) as occurrences,
                       (SELECT a2.session_id FROM agents a2
                        WHERE a2.error_msg = a1.error_msg AND a2.status = 'error'
                        ORDER BY a2.rowid_ DESC LIMIT 1) as last_session_id,
                       (SELECT s2.project FROM agents a2 JOIN sessions s2 ON a2.session_id = s2.id
                        WHERE a2.error_msg = a1.error_msg AND a2.status = 'error'
                        ORDER BY a2.rowid_ DESC LIMIT 1) as project
                FROM agents a1
                WHERE a1.status = 'error' AND a1.error_msg IS NOT NULL AND a1.error_msg != ''
                GROUP BY a1.error_msg ORDER BY COUNT(*) DESC LIMIT 15
            """).fetchall()
            c.close()
            return {
                "total": dict(total) if total else {},
                "today": dict(today) if today else {},
                "by_day": [dict(r) for r in by_day],
                "by_project": [dict(r) for r in by_project],
                "by_day_project": [dict(r) for r in by_day_project],
                "by_model": [dict(r) for r in by_model],
                "by_day_model": [dict(r) for r in by_day_model],
                "by_subagent_type": [dict(r) for r in by_subagent_type],
                "by_day_subagent_type": [dict(r) for r in by_day_subagent_type],
                "by_day_hook_reliability": [dict(r) for r in by_day_hook_reliability],
                "file_hotspots": [dict(r) for r in file_hotspots],
                "by_day_file_hotspots": [dict(r) for r in by_day_file_hotspots],
                "by_file_type": by_file_type,
                "tag_cloud": tag_cloud,
                "by_tag": by_tag,
                "retry_count": retry_count,
                "retry_patterns": retry_patterns,
                "slowest_agents": [dict(r) for r in slowest_agents],
                "common_errors": [dict(r) for r in common_errors],
                "hook_misses": hook_misses,
                "hook_miss_concurrency": hook_miss_concurrency,
            }
        except Exception:
            return {"total": {}, "today": {}, "by_day": [], "by_project": [], "by_day_project": [],
                    "by_model": [], "by_day_model": [], "by_subagent_type": [], "by_day_subagent_type": [],
                    "by_day_hook_reliability": [], "file_hotspots": [], "by_day_file_hotspots": [], "by_file_type": [],
                    "tag_cloud": [], "by_tag": [],
                    "retry_count": 0, "retry_patterns": [],
                    "slowest_agents": [],
                    "common_errors": [],
                    "hook_misses": 0, "hook_miss_concurrency": {}}

def _db_by_day_excluding_projects(excluded_projects, limit: int = 30) -> list:
    """Same shape as _db_analytics()'s by_day, but excludes any session
    whose project is in `excluded_projects` -- used by the weekly digest
    so a muted (noise-suppressed) project's cost/sessions/errors don't
    silently inflate a summary that's itself a notification. Muting a
    project already suppresses its individual toast/webhook events; the
    digest ignoring that was the one place mute didn't actually mean
    quiet. Empty `excluded_projects` behaves identically to the
    unfiltered by_day query (no project ever equals a value not in an
    empty exclusion, so every row passes)."""
    excluded_projects = [p for p in (excluded_projects or []) if p]
    with _db_lock:
        try:
            c = _db_conn()
            if excluded_projects:
                placeholders = ",".join("?" * len(excluded_projects))
                where = f"project NOT IN ({placeholders})"
                params = list(excluded_projects)
            else:
                where = "1=1"
                params = []
            # sessions.tokens/cost: for a session with agents they are its
            # agents' sum (_db_save_session), for a CLI-only session the main
            # conversation -- so the plain SUM covers both kinds.
            rows = c.execute(f"""
                SELECT date, COUNT(*) as sessions, SUM(tokens) as tokens, SUM(cost) as cost,
                       SUM(errors) as errors
                FROM sessions WHERE {where}
                GROUP BY date ORDER BY date DESC LIMIT ?
            """, params + [limit]).fetchall()
            c.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

def _prom_escape(s) -> str:
    """Escape a Prometheus label value per the text exposition format spec
    (backslash, double-quote, newline)."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

def _render_prometheus_metrics(data: dict, analytics: dict) -> str:
    """Render a subset of AOC's live status + DB analytics in Prometheus
    text exposition format -- same underlying numbers /status already
    returns, just re-rendered for a scraper instead of a browser, so
    AOC can plug into any existing monitoring/alerting stack rather than
    staying siloed."""
    lines = []
    lines.append("# HELP aoc_sessions_active Number of currently active CLI sessions")
    lines.append("# TYPE aoc_sessions_active gauge")
    lines.append(f'aoc_sessions_active {data.get("sessions_count", 0)}')

    status_counts = {}
    for a in data.get("agents", []):
        st = a.get("status", "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1
    lines.append("# HELP aoc_agents_total Number of tracked agents by status")
    lines.append("# TYPE aoc_agents_total gauge")
    for st in sorted(status_counts):
        lines.append(f'aoc_agents_total{{status="{_prom_escape(st)}"}} {status_counts[st]}')

    lines.append("# HELP aoc_session_cost_dollars Estimated cost per session")
    lines.append("# TYPE aoc_session_cost_dollars gauge")
    for sess in data.get("sessions_list", []):
        sid = _prom_escape(sess.get("id", ""))
        project = _prom_escape(sess.get("project", "") or "none")
        cost = sess.get("estimated_cost", 0.0) or 0.0
        lines.append(f'aoc_session_cost_dollars{{session_id="{sid}",project="{project}"}} {cost}')

    lines.append("# HELP aoc_hook_misses_total Agent events detected via transcript fallback")
    lines.append("# TYPE aoc_hook_misses_total counter")
    lines.append(f'aoc_hook_misses_total {analytics.get("hook_misses", 0)}')

    return "\n".join(lines) + "\n"

def _export_costs_csv(date_from: str = "", date_to: str = "") -> str:
    """CSV cost report grouped by project, for a date range (inclusive on
    both ends; empty means unbounded on that side). Cost tracking already
    has global/per-project budgets and a forecast, but no way to get the
    numbers out for expensing or handing to someone else -- this is that
    escape hatch. Backs GET /export_costs.csv."""
    import csv, io
    with _db_lock:
        try:
            c = _db_conn()
            where, params = [], []
            if date_from:
                where.append("date >= ?")
                params.append(date_from)
            if date_to:
                where.append("date <= ?")
                params.append(date_to)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            rows = c.execute(f"""
                SELECT COALESCE(NULLIF(project,''),'(none)') as project,
                       COUNT(*) as sessions, SUM(tokens) as tokens, SUM(cost) as cost
                FROM sessions {clause}
                GROUP BY project ORDER BY SUM(cost) DESC
            """, params).fetchall()
            c.close()
        except Exception:
            rows = []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["project", "sessions", "tokens", "cost_usd"])
    total_sessions = total_tokens = 0
    total_cost = 0.0
    for r in rows:
        cost = r["cost"] or 0
        tokens = r["tokens"] or 0
        w.writerow([r["project"], r["sessions"], tokens, round(cost, 4)])
        total_sessions += r["sessions"] or 0
        total_tokens += tokens
        total_cost += cost
    w.writerow(["TOTAL", total_sessions, total_tokens, round(total_cost, 4)])
    return buf.getvalue()

def _export_costs_by_model_csv(date_from: str = "", date_to: str = "") -> str:
    """CSV cost report grouped by model, mirroring _export_costs_csv's
    project-based report exactly -- COST BY PROJECT has had a CSV escape
    hatch since the Analytics tab's THIS MONTH/ALL TIME buttons, COST BY
    MODEL never did. Sourced from agents (joined against sessions for the
    date, since agents carry no date of their own -- the same
    agents-JOIN-sessions shape by_day_model already established for the
    trend chart). Backs GET /export_costs_by_model.csv."""
    import csv, io
    with _db_lock:
        try:
            c = _db_conn()
            where, params = ["a.model IS NOT NULL AND a.model != ''"], []
            if date_from:
                where.append("s.date >= ?")
                params.append(date_from)
            if date_to:
                where.append("s.date <= ?")
                params.append(date_to)
            clause = "WHERE " + " AND ".join(where)
            rows = c.execute(f"""
                SELECT a.model as model, COUNT(*) as agents, SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                {clause}
                GROUP BY a.model ORDER BY SUM(a.cost) DESC
            """, params).fetchall()
            c.close()
        except Exception:
            rows = []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["model", "agents", "tokens", "cost_usd"])
    total_agents = total_tokens = 0
    total_cost = 0.0
    for r in rows:
        cost = r["cost"] or 0
        tokens = r["tokens"] or 0
        w.writerow([r["model"], r["agents"], tokens, round(cost, 4)])
        total_agents += r["agents"] or 0
        total_tokens += tokens
        total_cost += cost
    w.writerow(["TOTAL", total_agents, total_tokens, round(total_cost, 4)])
    return buf.getvalue()

def _export_costs_by_subagent_type_csv(date_from: str = "", date_to: str = "") -> str:
    """CSV cost report grouped by subagent_type, mirroring
    _export_costs_by_model_csv exactly -- same agents-JOIN-sessions shape,
    just the other identifying column. Backs GET
    /export_costs_by_subagent_type.csv."""
    import csv, io
    with _db_lock:
        try:
            c = _db_conn()
            where, params = ["a.subagent_type IS NOT NULL AND a.subagent_type != ''"], []
            if date_from:
                where.append("s.date >= ?")
                params.append(date_from)
            if date_to:
                where.append("s.date <= ?")
                params.append(date_to)
            clause = "WHERE " + " AND ".join(where)
            rows = c.execute(f"""
                SELECT a.subagent_type as subagent_type, COUNT(*) as agents, SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                {clause}
                GROUP BY a.subagent_type ORDER BY SUM(a.cost) DESC
            """, params).fetchall()
            c.close()
        except Exception:
            rows = []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["subagent_type", "agents", "tokens", "cost_usd"])
    total_agents = total_tokens = 0
    total_cost = 0.0
    for r in rows:
        cost = r["cost"] or 0
        tokens = r["tokens"] or 0
        w.writerow([r["subagent_type"], r["agents"], tokens, round(cost, 4)])
        total_agents += r["agents"] or 0
        total_tokens += tokens
        total_cost += cost
    w.writerow(["TOTAL", total_agents, total_tokens, round(total_cost, 4)])
    return buf.getvalue()


_init_db()
_db_prune_old()
_prune_old_logs()

_BACKUP_RETENTION_DAYS = 14

def _backup_history_db():
    """history.db moved off OneDrive into %LOCALAPPDATA% (2026-07-16, to fix
    the OneDrive-lock freeze bug) and lost the automatic cloud backup it used
    to get for free as a side effect of living there. Copy it back into
    AOC_DIR (still OneDrive-synced) on a schedule instead -- a periodic,
    out-of-band copy doesn't reintroduce the original bug, which was
    specifically about every single request synchronously touching a
    OneDrive-locked file on the hot path; this runs on its own timer, never
    blocking a request. Uses sqlite3's own .backup() API rather than a raw
    file copy, since a raw copy mid-write could produce a torn/inconsistent
    snapshot -- .backup() is the documented safe way to copy a live DB."""
    try:
        backup_dir = os.path.join(AOC_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"history_{datetime.now().strftime('%Y-%m-%d')}.db")
        with _db_lock:
            src = _db_conn()
            dst = sqlite3.connect(backup_path)
            src.backup(dst)
            dst.close()
            src.close()
        # A completed .backup() call only means the copy operation didn't
        # raise -- it doesn't guarantee the result is actually restorable.
        # Verify with SQLite's own integrity_check rather than trusting a
        # silently-corrupt backup nobody would notice until they needed it.
        try:
            check_conn = sqlite3.connect(backup_path)
            result = check_conn.execute("PRAGMA integrity_check").fetchall()
            check_conn.close()
            if result != [("ok",)]:
                corrupt_path = backup_path[:-3] + "_CORRUPT.db"
                try:
                    os.replace(backup_path, corrupt_path)
                except Exception:
                    pass
                # Deliberate exception to "toasts are for agent status only"
                # (the only other caller is _headless_notify_worker) -- a
                # trusted-but-corrupt backup is worse than no backup, and
                # this is exactly the rare/important class of event native
                # toasts exist for. If _show_native_toast isn't defined yet
                # (module still loading when this runs at startup, extremely
                # unlikely given how rare an actual corruption is), the outer
                # except below absorbs it same as any other failure here.
                _show_native_toast("AOC Backup", "History DB backup failed integrity check -- see backups/ folder", kind="backup")
        except Exception:
            pass
        # globals().get(...) rather than a direct _notify_settings reference:
        # _backup_worker's thread (started right below, at module load) can
        # reach this line before _notify_settings is even defined further
        # down the file -- unlike _webhook_notify_worker/_digest_worker,
        # whose own thread-starts both come after that point. Falls back to
        # the plain constant either way, so this is never a hard dependency.
        # Inlined rather than calling _clamp_backup_retention_days: that
        # function is defined further down the file (after
        # _load_webhook_settings()), and this whole function can run from
        # _backup_worker's thread before that point at module load --
        # same reasoning as the globals().get() above, just one level
        # further to avoid a stray NameError silently skipping the entire
        # backup (this whole function body is one big try/except).
        try:
            retention_days = int(globals().get("_notify_settings", {}).get("backup_retention_days", _BACKUP_RETENTION_DAYS))
        except (TypeError, ValueError):
            retention_days = _BACKUP_RETENTION_DAYS
        retention_days = max(1, min(365, retention_days))
        cutoff = time.time() - retention_days * 86400
        # -journal: left behind when a backup was interrupted mid-copy; the
        # .db pattern alone never matched them, so they piled up forever.
        for p in (glob.glob(os.path.join(backup_dir, "history_*.db"))
                  + glob.glob(os.path.join(backup_dir, "history_*.db-journal"))):
            if "_CORRUPT.db" in p:
                continue  # keep corrupt backups for inspection, don't auto-delete the evidence
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except Exception:
                pass
    except Exception as e:
        _log_bg_error("_backup_history_db", e)

# Heavy, non-urgent DB work (full backup, _db_analytics) waits this long after
# monitor.py starts: the first ~3 min after a boot/restart the machine is
# already saturated and /status took 1-15 s (status_stalls.log 2026-09-30).
_STARTUP_GRACE_S = 180
_BACKUP_INTERVAL_S = 3600

def _first_backup_delay(now: float, today_backup_mtime) -> float:
    """Seconds _backup_worker waits before its first backup: at least the
    startup grace, and if today's snapshot is already fresh (a restart
    within the hour), the rest of its interval -- restarts used to re-copy
    the whole DB every time."""
    delay = _STARTUP_GRACE_S
    if today_backup_mtime is not None:
        delay = max(delay, _BACKUP_INTERVAL_S - (now - today_backup_mtime))
    return min(delay, _BACKUP_INTERVAL_S)

def _backup_worker():
    today_path = os.path.join(AOC_DIR, "backups", f"history_{datetime.now().strftime('%Y-%m-%d')}.db")
    try:
        mtime = os.path.getmtime(today_path)
    except OSError:
        mtime = None
    time.sleep(_first_backup_delay(time.time(), mtime))
    while True:
        _backup_history_db()
        time.sleep(_BACKUP_INTERVAL_S)  # hourly; dated filename means only today's snapshot actually changes

threading.Thread(target=_backup_worker, daemon=True).start()

def _compress_snapshots_worker():
    time.sleep(60)  # after startup settles; the backup worker waits longer still
    _db_compress_snapshots()

threading.Thread(target=_compress_snapshots_worker, daemon=True, name="compress-snapshots").start()


def _list_backups() -> list:
    """Full backup listing for the DIAG view's restore picker -- distinct
    from _get_diag_info's single 'latest' summary below, this is what a
    user actually picks a restore target from."""
    backup_dir = os.path.join(AOC_DIR, "backups")
    out = []
    try:
        for p in sorted(glob.glob(os.path.join(backup_dir, "history_*.db")), reverse=True):
            name = os.path.basename(p)
            out.append({
                "filename": name,
                "size_bytes": os.path.getsize(p),
                "mtime": os.path.getmtime(p),
                "corrupt": name.endswith("_CORRUPT.db"),
            })
    except Exception:
        pass
    return out


def _restore_backup(filename: str) -> dict:
    """Restore history.db from a chosen backups/ snapshot. This is itself a
    destructive action -- whatever's live right now is gone the moment it's
    overwritten -- so it takes its own pre-restore safety copy first, the
    same way _backup_history_db already guarantees recoverability for every
    other point in time. Never previously built or tested; backups existed
    but restoring from one had no code path at all."""
    import shutil
    backup_dir = os.path.join(AOC_DIR, "backups")
    # filename is a POST body value, not a trusted internal path -- reject
    # anything that isn't a bare filename already sitting in backups/.
    if os.path.basename(filename) != filename or not filename.startswith("history_"):
        return {"ok": False, "error": "invalid filename"}
    if filename.endswith("_CORRUPT.db"):
        return {"ok": False, "error": "refusing to restore a backup that failed its own integrity check"}
    src_path = os.path.join(backup_dir, filename)
    if not os.path.isfile(src_path):
        return {"ok": False, "error": "backup file not found"}

    with _db_lock:
        try:
            pre_restore_name = f"history_PRE_RESTORE_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.db"
            pre_restore_path = os.path.join(backup_dir, pre_restore_name)
            if os.path.exists(DB_FILE):
                pre_conn = sqlite3.connect(DB_FILE)
                pre_dst = sqlite3.connect(pre_restore_path)
                pre_conn.backup(pre_dst)
                pre_dst.close()
                pre_conn.close()

            shutil.copy2(src_path, DB_FILE)

            check_conn = sqlite3.connect(DB_FILE)
            result = check_conn.execute("PRAGMA integrity_check").fetchall()
            check_conn.close()
            if result != [("ok",)]:
                return {"ok": False, "error": "restored file failed integrity check",
                         "pre_restore_backup": pre_restore_name}
            return {"ok": True, "pre_restore_backup": pre_restore_name}
        except Exception as e:
            return {"ok": False, "error": str(e)}

def _get_diag_info() -> dict:
    """Self-diagnostics for AOC itself -- uptime/DB size/thread count/memory/
    backup status. Nothing tracked any of this before; useful for debugging
    AOC the way today's OneDrive-freeze investigation needed (mtime checks,
    manual process inspection) without ad-hoc tooling next time."""
    info = {
        "uptime_s": time.time() - _PROCESS_START,
        "thread_count": threading.active_count(),
        "db_size_bytes": None,
        "memory_rss_bytes": None,
        "backups_count": 0,
        "backups_latest": None,
        "backups_latest_size_bytes": None,
    }
    try:
        info["db_size_bytes"] = os.path.getsize(DB_FILE)
    except Exception:
        pass
    try:
        import psutil
        info["memory_rss_bytes"] = psutil.Process(os.getpid()).memory_info().rss
    except Exception:
        pass
    try:
        backup_dir = os.path.join(AOC_DIR, "backups")
        files = sorted(glob.glob(os.path.join(backup_dir, "history_*.db")))
        info["backups_count"] = len(files)
        if files:
            latest = files[-1]
            info["backups_latest"] = os.path.basename(latest)
            info["backups_latest_size_bytes"] = os.path.getsize(latest)
    except Exception:
        pass
    return info
