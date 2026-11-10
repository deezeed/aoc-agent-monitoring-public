# AOC server, part 09: Ask AOC.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── Ask AOC: questions in plain words over your own history ───────────────
# The model never touches the database. The question picks a period ("v
# utorok", "this month", "last 7 days") and topics (spend, failures,
# commits, cache, clients, models) by keywords; the matching facts are
# pulled from the transcript index and the existing reports into a short
# plain-text sheet (capped), and Haiku answers from that sheet only.
# Sessions in the sheet carry 8-char ids ([#1a2b3c4d]) that the UI turns
# into Replay links. Answers are cached per question and day.
# A follow-up ("a včera?", "which of those failed?") names the answer it
# continues (prev = its cache key): the last few Q/A pairs go along as
# context, their words widen the fact search, and a follow-up without its
# own period keeps the previous one. "Deeper" re-asks with Sonnet.
ASK_CACHE_FILE = os.path.join(AOC_DATA_DIR, "ask_cache.json")
_ASK_MAX_FACTS = 12000
_ASK_MAX_Q = 500
_ASK_KEEP = 60
_ASK_FOLLOW = 3                    # earlier Q/A pairs sent with a follow-up
_ASK_MODELS = {"haiku": "haiku", "sonnet": "claude-sonnet-5"}
_ask_lock = threading.Lock()
_ask_busy = [False]
_ASK_INSTRUCTION = (
    "You answer a question about the user's own Claude Code usage. Use ONLY the FACTS given with "
    "the question; use their exact numbers (money in $ with 2 decimals) and add them up yourself only "
    "when no total is given. If the facts don't hold the answer, say so in one sentence and name what "
    "is missing. When you mention a session, write its id exactly as in the facts, like [#1a2b3c4d]; "
    "the app shows the session's title in its place, so the sentence must still read naturally with a "
    "name there, and don't write the title next to it again. 'Agents' (agenti) in a question means the "
    "user's Claude Code sessions and their subagents, so answer it from the session facts. "
    "Be short: 1-5 sentences or a short list with '- '. Start with the answer itself: don't "
    "repeat the question, no filler, no closing offers. Plain text: no markdown headings, no bold, "
    "no backticks. Answer in the same language as the question, the way a native speaker would say it: "
    "natural word order, correct grammatical case and agreement, and don't translate the English labels "
    "in the facts word for word (PERIOD: today is just 'dnes' in Slovak). If EARLIER IN THIS CONVERSATION "
    "is given, the question may build on it ('a včera?', 'which of those?'): use it to understand what is "
    "asked, but take numbers only from the FACTS.")
_ASK_STOP = set("""
a aj ako ale alebo ani asi az bol bola bolo by bude co ci do este for from had has have how i in is it je
ja jej ich k kde ked kedy kolko kto ma mam me mi my na nad nie o od on ona pod pre pri s sa si sme so som
ste su sú t ta tak tam te ten the this to tom tu tych u uz v vo vy z za ze what which who why with you
your was were did do does my me on at of and or an are be by kolkokrat ktora ktore ktory ktorej najviac
najmenej most least celkovo spolu total kolko minul minula minuli stal stala stalo stoji cost spend spent
dnes vcera predvcerom today yesterday tyzden tyzdni tyzdna week mesiac mesiaci mesiaca month rok year
posledne poslednych poslednom last past den dni dna day days robil robila robili urobil pracoval work worked
session sessions sessionu sessiony relacia relacie claude code aoc som si sa mi ma
tento tomto tohto tuto tato toto this that these those minuly minulom minulu minuleho previous ever time all
predchadzajuci predchadzajucom much many cost costs costed ktory which kolko viac more less menej
""".split())
_ASK_TOPICS = (
    ("spend", r"minul|stal|stoj|cost|spend|spent|cen[ay]|\$|eur|dolar|peniaz|money|kolko|how much|drah|expens|lacn|cheap|rozpoc|budget|utrat"),
    ("failures", r"fail|chyb|error|zlyh|padl|padal|crash|broke|nefung|denied|zamiet|problem"),
    ("commits", r"commit|push|merge"),
    ("cache", r"cache|kes"),
    ("clients", r"klient|client|faktur|invoice|bill"),
    ("models", r"model|opus|sonnet|haiku|fable"),
)
_ASK_WEEKDAYS = (r"pondel|monday|\bmon\b", r"utor|tuesday|\btue\b", r"\bstred|wednesday", r"stvrt|thursday",
                 r"\bpiat|friday", r"sobot|saturday", r"nedel|sunday")
_ASK_MONTHS = (r"januar|january|\bjan\b", r"februar|february", r"\bmar(ec|ci|ca)\b|march", r"april",
               r"\bmaj(a|i)?\b|\bmay\b", r"\bjun(a|i|e)?\b|june", r"\bjul(a|i)?\b|july", r"august",
               r"septemb", r"oktob|october", r"novemb", r"decemb")


def _ask_norm(s):
    """Pure: lowercase, no diacritics, single spaces."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s.lower()).strip()


def _ask_period(question, today):
    """Pure: the days a question is about -> {from, to, label} (YYYY-MM-DD,
    both inclusive). today = datetime.date. Default: the last 30 days."""
    from datetime import date as _date, timedelta as _td
    t = _ask_norm(question)
    iso = lambda d: d.isoformat()

    def one(d, label):
        return {"from": iso(d), "to": iso(d), "label": label}

    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        try:
            return one(_date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(0))
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})\.\s?(\d{1,2})\.(?:\s?(\d{4}))?", t)
    if m:
        try:
            d = _date(int(m.group(3) or today.year), int(m.group(2)), int(m.group(1)))
            if d > today and not m.group(3):
                d = d.replace(year=d.year - 1)
            return one(d, iso(d))
        except ValueError:
            pass
    m = re.search(r"\b(?:posledn\w*|last|past)\s+(\d{1,3})\s*(dn|dni|day|tyzd|week|mesia|month)", t)
    if m:
        n = int(m.group(1)) * (7 if m.group(2) in ("tyzd", "week") else 30 if m.group(2) in ("mesia", "month") else 1)
        n = max(1, min(n, 3650))
        return {"from": iso(today - _td(days=n - 1)), "to": iso(today), "label": f"last {n} days"}
    if re.search(r"predvcer|day before yesterday", t):
        return one(today - _td(days=2), "day before yesterday")
    if re.search(r"\bvcer|yesterday", t):
        return one(today - _td(days=1), "yesterday")
    if re.search(r"\bdnes|today", t):
        return one(today, "today")
    monday = today - _td(days=today.weekday())
    if re.search(r"(minul\w*|predchadzajuc\w*|last|previous)\s+(tyzd|week)", t):
        return {"from": iso(monday - _td(days=7)), "to": iso(monday - _td(days=1)), "label": "last week"}
    if re.search(r"(tento|tomto|tohto|this)\s+(tyzd|week)", t):
        return {"from": iso(monday), "to": iso(today), "label": "this week"}
    first = today.replace(day=1)
    if re.search(r"(minul\w*|predchadzajuc\w*|last|previous)\s+(mesia|month)", t):
        prev_end = first - _td(days=1)
        return {"from": iso(prev_end.replace(day=1)), "to": iso(prev_end), "label": "last month"}
    if re.search(r"(tento|tomto|tohto|this)\s+(mesia|month)", t):
        return {"from": iso(first), "to": iso(today), "label": "this month"}
    if re.search(r"(tento|tomto|tohto|this)\s+(rok|year)|\bcelkovo\b|all time|\bever\b|odjakziva|\bvsetk\w* cas", t):
        return {"from": "2000-01-01", "to": iso(today), "label": "all time"}
    for i, pat in enumerate(_ASK_WEEKDAYS):
        if re.search(pat, t):
            d = today - _td(days=(today.weekday() - i) % 7)
            return one(d, d.strftime("%A"))
    for i, pat in enumerate(_ASK_MONTHS):
        if re.search(pat, t):
            y = today.year if i + 1 <= today.month else today.year - 1
            start = _date(y, i + 1, 1)
            end = (_date(y + (i == 11), (i + 1) % 12 + 1, 1) - _td(days=1))
            return {"from": iso(start), "to": iso(min(end, today)), "label": start.strftime("%B %Y")}
    return {"from": iso(today - _td(days=29)), "to": iso(today), "label": "last 30 days"}


def _ask_topics(question):
    """Pure: the report topics a question touches (set)."""
    t = _ask_norm(question)
    return {name for name, pat in _ASK_TOPICS if re.search(pat, t)}


def _ask_keywords(question, limit=5):
    """Pure: the words worth searching for (names, things), no stop words,
    no dates or topic words."""
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9_.\-]{2,}", _ask_norm(question)):
        w = w.strip(".-_")
        if len(w) < 3 or w in _ASK_STOP or re.fullmatch(r"[\d.\-_]+", w) or w in out:
            continue
        if any(re.fullmatch(pat, w) or re.match(pat, w) for _n, pat in _ASK_TOPICS):
            continue
        if any(re.search(p, w) for p in _ASK_WEEKDAYS + _ASK_MONTHS):
            continue
        out.append(w)
    return out[:limit]


def _ask_cap(blocks, max_chars=_ASK_MAX_FACTS):
    """Pure: join fact blocks in order until max_chars; the block that
    doesn't fit is cut at a line end and marked, later ones dropped."""
    out, used = [], 0
    for b in blocks:
        b = (b or "").strip()
        if not b:
            continue
        if used + len(b) + 2 <= max_chars:
            out.append(b)
            used += len(b) + 2
            continue
        room = max_chars - used - 30
        if room > 200:
            cut = b[:room].rsplit("\n", 1)[0]
            out.append(cut + "\n(... more not shown)")
        break
    return "\n\n".join(out)


def _ask_sessions(conn, d0, d1):
    """Sessions with spend in [d0, d1]: [{id, cost, tokens, first_day,
    last_day, title, project, cwd, first}] most expensive first."""
    rows = conn.execute("""SELECT session_id, SUM(cost), SUM(inp + out + cw + cr), MIN(day), MAX(day)
                           FROM usage WHERE day >= ? AND day <= ? GROUP BY session_id""", (d0, d1)).fetchall()
    sids = [r[0] for r in rows]
    meta, first = {}, {}
    for i in range(0, len(sids), 500):
        part = sids[i:i + 500]
        qs = ",".join("?" * len(part))
        for sid, project, cwd, title in conn.execute(
                f"SELECT session_id, project, cwd, title FROM sessions_meta WHERE session_id IN ({qs})", part):
            meta[sid] = (project or "", cwd or "", title or "")
        for sid, ts, text in conn.execute(
                f"SELECT session_id, ts, substr(text, 1, 160) FROM msgs WHERE role = 'user' AND session_id IN ({qs})", part):
            if sid not in first or (ts or "") < first[sid][0]:
                first[sid] = (ts or "", text or "")
    out = []
    for sid, cost, tokens, a, b in rows:
        project, cwd, title = meta.get(sid, ("", "", ""))
        out.append({"id": sid, "cost": round(cost or 0, 4), "tokens": int(tokens or 0), "first_day": a, "last_day": b,
                    "title": title, "project": project, "cwd": cwd,
                    "first": re.sub(r"\s+", " ", first.get(sid, ("", ""))[1]).strip()})
    out.sort(key=lambda s: -s["cost"])
    return out


def _ask_facts(conn, question, today, fail_fn=None, client_cfg=None, period=None):
    """-> (facts text, {id8: {id, title}}, period). conn = transcript index;
    fail_fn(days) = the failure map (only called when the question is about
    failures -- it scans transcripts). period overrides the question's own."""
    from datetime import date as _date
    period = period or _ask_period(question, today)
    topics = _ask_topics(question)
    words = _ask_keywords(question)
    d0, d1 = period["from"], period["to"]
    sess = _ask_sessions(conn, d0, d1)
    ids = {}

    def ref(s):
        k = s["id"][:8]
        ids[k] = {"id": s["id"], "title": s["title"] or s["project"] or k}
        return f"[#{k}]"

    money = lambda v: f"${(v or 0):.2f}"
    total = sum(s["cost"] for s in sess)
    n_commits = conn.execute("SELECT COUNT(*) FROM commits WHERE sha NOT LIKE 'tu:%' AND day >= ? AND day <= ?",
                             (d0, d1)).fetchone()[0]
    blocks = [f"PERIOD: {period['label']} ({d0} to {d1}); today is {today.isoformat()} ({today.strftime('%A')})\n"
              f"Total Claude Code spend: {money(total)} in {len(sess)} sessions; {n_commits} commits."]
    if words:
        lines = []
        for w in words:
            hit = [s for s in sess if w in _ask_norm(" ".join((s["title"], s["first"], s["cwd"], s["project"])))]
            if hit:
                lines.append(f"- '{w}': {money(sum(s['cost'] for s in hit))} in {len(hit)} sessions "
                             f"({', '.join(ref(s) for s in hit[:12])})")
        if lines:
            blocks.append("SESSIONS MENTIONING THE QUESTION'S WORDS (title, first message or folder), this period:\n"
                          + "\n".join(lines))
    try:
        days_n = (_date.fromisoformat(d1) - _date.fromisoformat(d0)).days + 1
    except ValueError:
        days_n = 31
    if 1 < days_n <= 62:
        by_day = conn.execute("SELECT day, SUM(cost) FROM usage WHERE day >= ? AND day <= ? GROUP BY day ORDER BY day",
                              (d0, d1)).fetchall()
        if by_day:
            blocks.append("SPEND PER DAY:\n" + "\n".join(f"{d} {_date.fromisoformat(d).strftime('%a')} {money(c)}"
                                                         for d, c in by_day))
    if sess:
        lines = []
        for s in sess[:40]:
            days = s["first_day"] if s["first_day"] == s["last_day"] else f"{s['first_day']}..{s['last_day']}"
            lines.append(f"{ref(s)} {days} {money(s['cost'])} \"{s['title'] or s['project']}\""
                         + (f" first msg: \"{s['first'][:110]}\"" if s["first"] else ""))
        more = f"\n(+{len(sess) - 40} cheaper sessions, {money(sum(s['cost'] for s in sess[40:]))})" if len(sess) > 40 else ""
        blocks.append("SESSIONS (most expensive first):\n" + "\n".join(lines) + more)
    if "models" in topics or "spend" in topics:
        rows = conn.execute("SELECT model, SUM(cost), SUM(n) FROM usage WHERE day >= ? AND day <= ? GROUP BY model "
                            "ORDER BY 2 DESC", (d0, d1)).fetchall()
        if rows:
            blocks.append("SPEND BY MODEL:\n" + "\n".join(f"{m or '?'}: {money(c)}, {n} API calls" for m, c, n in rows))
    if "commits" in topics or not topics:
        cs = [c for c in _commit_stats(conn, d0)["commits"]
              if not c.get("epoch") or datetime.fromtimestamp(c["epoch"]).strftime("%Y-%m-%d") <= d1]
        if cs:
            lines = []
            for c in cs[:30]:
                day = datetime.fromtimestamp(c["epoch"]).strftime("%Y-%m-%d %H:%M") if c.get("epoch") else ""
                k = c["session_id"][:8]
                ids.setdefault(k, {"id": c["session_id"], "title": c.get("title") or k})
                lines.append(f"{day} {c['repo']} {str(c['sha'])[:7]} \"{c['subject'][:90]}\" {money(c['cost'])} [#{k}]")
            blocks.append("COMMITS (newest first, cost = session spend since its previous commit):\n" + "\n".join(lines))
    if "cache" in topics:
        st = _cache_stats(conn, d0, top=5)["total"]
        blocks.append(f"PROMPT CACHE since {d0}: hit rate {round((st['hit_rate'] or 0) * 100)}%, saved {money(st['saved'])}, "
                      f"re-caches {st['cold_n']} costing {money(st['cold_extra'])} extra, cache reads {money(st['cost_read'])}, "
                      f"cache writes {money(st['cost_write'])}, output {money(st['cost_out'])}")
    if "clients" in topics and client_cfg is not None:
        rep = _client_report(conn, d0[:7], client_cfg)
        lines = [f"- {g['name'] or '(no client)'}: {money(g['cost'])} (billed {money(g['billed'])}), "
                 f"{len(g['sessions'])} sessions, {g['commits']} commits" for g in rep["clients"] if g["sessions"]]
        if lines:
            blocks.append(f"CLIENTS in {rep['month']}:\n" + "\n".join(lines))
    if "failures" in topics and fail_fn is not None:
        try:
            fdays = max(1, min(90, (today - _date.fromisoformat(d0)).days + 1))
            fm = fail_fn(fdays)
            lines = [f"Last {fdays} days: {fm['failed']} failed tool calls of {fm['calls']} ({fm['rate']}%), "
                     f"~{round(fm['secs'] / 60)} min and {money(fm['cost'])} lost, {fm['denied']} denied by you"]
            for g in (fm.get("top") or [])[:15]:
                lines.append(f"- {g['tool']} {g['target'][:60]} ({g['reason']}): {g['count']}x in {g['sessions']} sessions, "
                             f"{money(g['cost'])}; last [#{str(g['last_sid'])[:8]}]")
                ids.setdefault(str(g["last_sid"])[:8], {"id": g["last_sid"], "title": g.get("last_label") or ""})
            worst = {}
            for g in fm.get("top") or []:
                w = worst.setdefault(g["last_sid"], [0, g.get("last_label") or ""])
                w[0] += g["count"]
            if worst:
                lines.append("Sessions with the most failures (by their latest failing pattern): " + ", ".join(
                    f"[#{str(sid)[:8]}] {n}x" for sid, (n, _l) in sorted(worst.items(), key=lambda kv: -kv[1][0])[:6]))
            blocks.append("FAILURES:\n" + "\n".join(lines))
        except Exception as e:
            _log_bg_error("ask failures", e)
    if words:
        hits = _search_transcripts(conn, " ".join(words), limit_sessions=8, hits_per_session=2)
        if not hits and len(words) > 1:  # every word at once found nothing: each one alone
            seen = set()
            for w in words[:3]:
                for h in _search_transcripts(conn, w, limit_sessions=4, hits_per_session=2):
                    if h["session_id"] not in seen:
                        seen.add(h["session_id"])
                        hits.append(h)
        lines = []
        for h in hits:
            k = h["session_id"][:8]
            ids.setdefault(k, {"id": h["session_id"], "title": h.get("title") or h.get("project") or k})
            snips = " | ".join(re.sub(r"[\x02\x03]", "", x["snippet"] or "")[:140] for x in h["hits"])
            lines.append(f"[#{k}] {str(h.get('last_ts') or '')[:10]} \"{h.get('title') or h.get('project') or ''}\" "
                         f"({h['n_hits']} matches): {snips}")
        if lines:
            blocks.append("TEXT SEARCH IN CONVERSATIONS (any date) for " + ", ".join(words) + ":\n" + "\n".join(lines))
    facts = _ask_cap(blocks)
    used = {k: v for k, v in ids.items() if f"[#{k}]" in facts}
    return facts, used, period


def _ask_cache_key(question, today, model="haiku", prev=""):
    """Pure: per question + day; a non-default model or a follow-up gets its
    own key (a plain Haiku question keeps the key it always had)."""
    import hashlib
    s = _ask_norm(question) + "|" + today.isoformat()
    if model != "haiku":
        s += "|m:" + model
    if prev:
        s += "|p:" + prev
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:20]


def _ask_thread(cache, prev, limit=_ASK_FOLLOW):
    """Pure: the answers a follow-up continues, oldest first (at most `limit`,
    following each entry's own `prev`; a broken or looping chain just ends)."""
    out, seen = [], set()
    while prev and prev not in seen and len(out) < limit:
        seen.add(prev)
        e = cache.get(prev)
        if not isinstance(e, dict):
            break
        out.append(e)
        prev = e.get("prev") or ""
    return out[::-1]


def _ask_period_explicit(question, today):
    """Pure: does the question name its own period? (Otherwise a follow-up
    keeps the previous answer's period.)"""
    return (_ask_period(question, today)["label"] != "last 30 days"
            or bool(re.search(r"\b30\b", _ask_norm(question))))


def _ask_prompt(question, facts, thread=()):
    """Pure: the text sent with _ASK_INSTRUCTION. Earlier Q/A pairs of a
    follow-up go first; a plain question is just QUESTION + FACTS."""
    head = ""
    if thread:
        head = "EARLIER IN THIS CONVERSATION:\n" + "".join(
            f"Q: {e.get('q', '')}\nA: {str(e.get('answer', ''))[:1200]}\n" for e in thread) + "\n"
    return f"{head}QUESTION: {question}\n\nFACTS:\n{facts}\n"


def _ask_load():
    d = _load_json_file(ASK_CACHE_FILE, {})
    return d if isinstance(d, dict) else {}


def _ask_store(key, entry):
    d = _ask_load()
    d[key] = entry
    d = dict(sorted(d.items(), key=lambda kv: kv[1].get("at", 0))[-_ASK_KEEP:])
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    tmp = ASK_CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, ASK_CACHE_FILE)


def _ask_history(limit=12):
    """GET /ask/history: the newest answered questions."""
    items = sorted(_ask_load().values(), key=lambda e: -e.get("at", 0))[:limit]
    return {"items": items}


def _ask(question, force=False, run=None, now=None, conn_fn=None, fail_fn=None, prev="", model="haiku"):
    """POST /ask: {"ok", "q", "answer", "cost", "at", "period", "sessions",
    "model", "prev", "thread", "cached"}. prev = the cache key of the answer
    this follows up; model = haiku | sonnet. Blocking (a few seconds) unless
    cached for today."""
    from datetime import date as _date
    q = re.sub(r"\s+", " ", str(question or "")).strip()[:_ASK_MAX_Q]
    if len(q) < 3:
        return {"ok": False, "error": "Type a question first."}
    model = model if model in _ASK_MODELS else "haiku"
    prev = prev if isinstance(prev, str) and re.fullmatch(r"[0-9a-f]{20}", prev) else ""
    now = now or time.time()
    today = _date.fromtimestamp(now)
    cache = _ask_load()
    thread = _ask_thread(cache, prev)
    if prev and not thread:
        return {"ok": False, "error": "The earlier answer is gone -- ask it as a new question."}
    key = _ask_cache_key(q, today, model, prev)
    hit = cache.get(key)
    if hit and not force:
        return {"ok": True, **hit, "cached": True}
    if conn_fn is None and not os.path.exists(TRANSCRIPT_INDEX_DB):
        return {"ok": False, "error": "The history index isn't built yet -- try again in a minute."}
    with _ask_lock:
        if _ask_busy[0]:
            return {"ok": False, "busy": True, "error": "Still answering the previous question."}
        _ask_busy[0] = True
    try:
        # a follow-up searches with the earlier questions' words too ("a včera?"
        # alone names no topic) and keeps their period unless it names one
        scope = " ".join([e.get("q", "") for e in thread] + [q])
        period = None
        if thread and not _ask_period_explicit(q, today) and isinstance(thread[-1].get("period"), dict):
            period = thread[-1]["period"]
        conn = (conn_fn or _transcript_index_connect)()
        try:
            facts, sessions, period = _ask_facts(conn, scope, today, fail_fn=fail_fn if fail_fn is not None else _failure_map,
                                                 client_cfg=_load_clients(), period=period)
        finally:
            conn.close()
        text, cost = (run or _run_haiku)(_ASK_INSTRUCTION, _ask_prompt(q, facts, thread), model=_ASK_MODELS[model])
        text = text.strip()[:4000]
        entry = {"key": key, "q": q, "answer": text, "cost": round(cost, 4), "at": now, "model": model,
                 "period": period, "sessions": {k: v for k, v in sessions.items() if f"#{k}" in text}}
        if prev:
            entry["prev"] = prev
            entry["thread"] = [e.get("q", "") for e in thread]
        _ask_store(key, entry)
        return {"ok": True, **entry, "cached": False}
    except Exception as e:
        _log_bg_error("ask", e)
        return {"ok": False, "error": str(e)[:200] or "failed"}
    finally:
        with _ask_lock:
            _ask_busy[0] = False
