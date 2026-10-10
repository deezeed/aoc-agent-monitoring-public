"""'While you were away' recaps (monitor.py): _human_text (what counts as
you writing), _activity_track / _activity_view (counters since your last
message), _recap_digest (the text Haiku gets), _recap_needs_refresh, and
_recap_get end to end on a real transcript file with a fake summarizer
(caching, refresh only after new activity, busy guard, failures)."""
import sys, os, json, tempfile, threading, time, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
data = tempfile.mkdtemp(prefix="aoc_recap_")
ns = exec_functions(
    ["_load_json_file", "_iso_to_epoch", "_assistant_text", "_LOOP_EDIT_TOOLS", "_loop_tool_key", "_GIT_COMMIT_RE", "_is_commit_call",
     "RECAP_SETTINGS_FILE", "RECAP_CACHE_FILE", "_RECAP_MODES", "_RECAP_MIN_AWAY_S", "_RECAP_MIN_TOOLS",
     "_RECAP_REFRESH_S", "_RECAP_DIGEST_MAX", "_recap_lock", "_recap_busy", "_human_text", "_activity_track",
     "_activity_view", "_recap_digest", "_RECAP_INSTRUCTION", "_load_recap_settings", "_save_recap_settings",
     "_recap_cached", "_recap_store", "_recap_needs_refresh", "_recap_get"],
    {"os": os, "json": json, "re": __import__("re"), "time": time, "threading": threading, "datetime": __import__("datetime").datetime,
     "calendar": __import__("calendar"), "AOC_DATA_DIR": data,
     "_transcript_cursors": {}, "_transcript_cursors_lock": threading.Lock(),
     "_log_bg_error": lambda *a: None})

T0 = 1_800_000_000
ts = lambda sec: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(T0 + sec))
n = [0]


def you(text, sec, **kw):
    return {"type": "user", "timestamp": ts(sec), "message": {"role": "user", "content": text}, **kw}


def say(text, sec):
    return {"type": "assistant", "timestamp": ts(sec), "message": {"content": [{"type": "text", "text": text}]}}


def tool(name, inp, err, sec, out="ok"):
    n[0] += 1
    tid = f"t{n[0]}"
    return [{"type": "assistant", "timestamp": ts(sec), "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}},
            {"type": "user", "timestamp": ts(sec + 1), "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": err, "content": out}]}}]


# ── _human_text ──
h = ns["_human_text"]
c.check("string prompt is you", h(you("fix it", 0)) == "fix it")
c.check("text-block prompt is you", h({"type": "user", "message": {"content": [{"type": "text", "text": "a"}, {"type": "image"}]}}) == "a")
c.check("tool result is not you", h(tool("Bash", {}, False, 0)[1]) is None)
c.check("meta / sidechain / assistant are not you",
        h(you("x", 0, isMeta=True)) is None and h(you("x", 0, isSidechain=True)) is None and h(say("x", 0)) is None)

# ── activity counters ──
lines = [you("old ask", 0), *tool("Bash", {"command": "ls"}, False, 5), you("Prosím oprav testy a commitni", 100)]
lines += tool("Read", {"file_path": "C:/p/a.py"}, False, 110)
lines += tool("Edit", {"file_path": "C:/p/a.py"}, False, 120)
lines += tool("Bash", {"command": "pytest"}, True, 130, out="2 failed: test_x, test_y")
lines += tool("Edit", {"file_path": "C:/p/b.py"}, False, 140)
lines += tool("Bash", {"command": "pytest"}, False, 150)
lines += tool("Bash", {"command": 'git commit -m "fix"'}, False, 160)
lines += tool("Bash", {"command": "git commit --amend"}, True, 170)
lines += [say("Opravené, testy prechádzajú a je to commitnuté. Pushnúť?", 180)]
st = {}
for o in lines:
    ns["_activity_track"](st, o, T0 + 999)
act = st["activity"]
c.check("counts since YOUR last message only", act["since"] == T0 + 100 and act["tools"] == 7)
c.check("errors and successful commits counted", act["errors"] == 2 and act["commits"] == 1)
c.check("edited files, not read ones", act["files"] == ["C:/p/a.py", "C:/p/b.py"])
view = ns["_activity_view"]
c.check("too soon after your message -> no recap", view(act, T0 + 100 + 60) is None)
v = view(act, T0 + 100 + 16 * 60)
c.check("15+ min later -> view", v == {"since": T0 + 100, "tools": 7, "errors": 2, "commits": 1, "files": 2})
c.check("too little done -> no recap", view({**act, "tools": 2}, T0 + 99999) is None)
c.check("nothing yet -> None", view(None, 1) is None and view({}, 1) is None)
ns["_activity_track"](st, you("ok push", 200), T0 + 999)
c.check("your next message resets", st["activity"]["tools"] == 0 and st["activity"]["since"] == T0 + 200)

# ── digest ──
raw = [json.dumps(o, ensure_ascii=False) for o in lines]
d = ns["_recap_digest"](raw)
c.check("digest starts at your last message", d.startswith("YOU: Prosím oprav testy") and "old ask" not in d)
c.check("tool calls listed", "-> Bash: pytest" in d and "-> Edit: C:/p/a.py" in d)
c.check("failures carry their output", "FAILED: 2 failed: test_x, test_y" in d)
c.check("Claude's last message at the end", d.rstrip().endswith("CLAUDE'S LAST MESSAGE: Opravené, testy prechádzajú a je to commitnuté. Pushnúť?"))
c.check("no message from you -> empty", ns["_recap_digest"]([json.dumps(say("hi", 0))]) == "")
c.check("garbage lines skipped", ns["_recap_digest"](["{bad", *raw]) == d)
big = [json.dumps(you("go", 0))] + [json.dumps(o) for i in range(400) for o in tool("Bash", {"command": f"step {i} " + "x" * 50}, False, i)]
bd = ns["_recap_digest"](big, max_chars=3000)
c.check("long digest cut in the middle, keeps start and end", len(bd) <= 3100 and bd.startswith("YOU: go") and "middle cut" in bd and "step 399" in bd)

# ── refresh rule ──
need = ns["_recap_needs_refresh"]
cached = {"since": 5, "size": 100, "generated_at": 1000}
c.check("no cache -> summarize", need(None, 5, 100, 1001, False))
c.check("you wrote again -> summarize", need(cached, 6, 100, 1001, False))
c.check("nothing new -> cached, even when forced", not need(cached, 5, 100, 99999, True))
c.check("new activity but recent -> cached", not need(cached, 5, 200, 1000 + 60, False))
c.check("new activity + forced -> summarize", need(cached, 5, 200, 1000 + 60, True))
c.check("new activity after 10 min -> summarize", need(cached, 5, 200, 1000 + 601, False))

# ── settings ──
c.check("recap mode default auto", ns["_load_recap_settings"]() == {"mode": "auto"})
ns["_save_recap_settings"]({"mode": "click"})
c.check("mode saved", ns["_load_recap_settings"]() == {"mode": "click"})
c.check("bad mode -> auto", ns["_save_recap_settings"]({"mode": "x"}) == {"mode": "auto"})

# ── _recap_get end to end ──
tdir = tempfile.mkdtemp(prefix="aoc_recap_t_")
tpath = os.path.join(tdir, "sess.jsonl")
with open(tpath, "w", encoding="utf-8") as f:
    f.write("\n".join(raw) + "\n")
ns["_transcript_cursors"]["sess"] = {"path": tpath, "stats": {"activity": {"since": T0 + 100}}}
calls = []


def fake(prompt, digest):
    calls.append((prompt, digest))
    return "- Fixed the tests\n- Committed", 0.0123


get = ns["_recap_get"]
r = get("sess", run=fake, now=5000)
c.check("first ask summarizes", r["ok"] and r["fresh"] and r["text"] == "- Fixed the tests\n- Committed" and r["cost"] == 0.0123)
c.check("summarizer got instruction + digest", calls[0][0] == ns["_RECAP_INSTRUCTION"] and calls[0][1].startswith("YOU: Prosím"))
r = get("sess", run=fake, now=5010)
c.check("asked again, nothing new -> cache, no call", r["ok"] and not r["fresh"] and len(calls) == 1)
c.check("cache survives (file)", ns["_recap_cached"]("sess")["text"].startswith("- Fixed"))
with open(tpath, "a", encoding="utf-8") as f:
    f.write(json.dumps(say("Also updated the README.", 300)) + "\n")
r = get("sess", run=fake, now=5020)
c.check("new activity, too soon -> still cached", not r["fresh"] and len(calls) == 1)
r = get("sess", force=True, run=fake, now=5030)
c.check("Refresh button -> new summary", r["fresh"] and len(calls) == 2 and "README" in calls[1][1])
c.check("unknown session -> error", not get("nope", run=fake)["ok"])
with open(tpath, "a", encoding="utf-8") as f:
    f.write(json.dumps(say("Then ran lint.", 350)) + "\n")
ns["_recap_busy"].add("sess")
r = get("sess", force=True, run=fake, now=99999)
ns["_recap_busy"].discard("sess")
c.check("already running -> busy, no second call", r.get("busy") and len(calls) == 2)


def boom(prompt, digest):
    raise RuntimeError("Claude Code (claude) not found")


with open(tpath, "a", encoding="utf-8") as f:
    f.write(json.dumps(say("more", 400)) + "\n")
r = get("sess", force=True, run=boom, now=99999)
c.check("summarizer failure -> error text, cache kept", not r["ok"] and "not found" in r["error"]
        and ns["_recap_cached"]("sess")["text"].startswith("- Fixed"))
c.check("busy flag cleared after failure", "sess" not in ns["_recap_busy"])

# ── _run_haiku against a fake `claude` (Windows: a .cmd running python) ──
if os.name == "nt":
    import subprocess
    fake_py = os.path.join(tdir, "fake_claude.py")
    with open(fake_py, "w", encoding="utf-8") as f:
        f.write("import sys, json, time, os\n"
                "args = sys.argv[1:]\n"
                "data = sys.stdin.buffer.read().decode('utf-8')\n"
                "mode = os.environ.get('FAKE_MODE', 'ok')\n"
                "if mode == 'slow': time.sleep(1.5)\n"
                "if mode == 'garbage': print('not json'); sys.stderr.write('auth failed'); sys.exit(1)\n"
                "ok = args[:4] == ['-p', '--model', 'haiku', '--no-session-persistence'] and 'disableAllHooks' in ' '.join(args)\n"
                "print(json.dumps({'result': ('- got ' + str(len(data)) + ' chars') if ok else 'bad args ' + repr(args),"
                " 'total_cost_usd': 0.0042, 'is_error': not ok}))\n")
    fake_cmd = os.path.join(tdir, "claude.cmd")
    with open(fake_cmd, "w", encoding="utf-8") as f:
        f.write(f'@"{sys.executable}" "{fake_py}" %*\n')
    pids = set()
    rh = exec_functions(["_run_haiku"], {"os": os, "json": json, "subprocess": subprocess, "AOC_DATA_DIR": data,
                                         "_find_claude_exe": lambda: fake_cmd, "_recap_run_lock": threading.Lock(),
                                         "_own_claude_pids": pids})["_run_haiku"]
    os.environ["FAKE_MODE"] = "slow"
    seen = []
    th = threading.Thread(target=lambda: seen.append(rh("Summarize", "héllo wörld")))
    th.start()
    time.sleep(0.6)
    during = set(pids)
    th.join(30)
    c.check("run: stdin passed (utf-8), args right, cost parsed", seen and seen[0] == ("- got 11 chars", 0.0042))
    c.check("run: its pid is excluded from the CLI count while it runs, then dropped", len(during) == 1 and not pids)
    os.environ["FAKE_MODE"] = "garbage"
    try:
        rh("x", "y")
        c.check("run: bad output raises", False)
    except RuntimeError as e:
        c.check("run: bad output raises with stderr text", "auth failed" in str(e))
    os.environ["FAKE_MODE"] = "slow"
    try:
        rh("x", "y", timeout=0.3)
        c.check("run: timeout raises", False)
    except RuntimeError as e:
        c.check("run: timeout raises, pid dropped", "too long" in str(e) and not pids)
    os.environ.pop("FAKE_MODE", None)
    no = exec_functions(["_run_haiku"], {"os": os, "json": json, "subprocess": subprocess, "AOC_DATA_DIR": data,
                                         "_find_claude_exe": lambda: None, "_recap_run_lock": threading.Lock(),
                                         "_own_claude_pids": set()})["_run_haiku"]
    try:
        no("x", "y")
        c.check("run: no claude -> clear error", False)
    except RuntimeError as e:
        c.check("run: no claude -> clear error", "not found" in str(e))

shutil.rmtree(tdir, ignore_errors=True)
shutil.rmtree(data, ignore_errors=True)
c.finish()
