"""People (server/18_users.py): invites, personal tokens, roles, who's here
-- and the Handler using them (source checks: the page never carries the
owner's token for someone else, every request is authorized)."""
import ast, hashlib, json, os, re, sys, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_source, SERVER_DIR
from lib.check import Checker
import secrets

c = Checker()
src = read_source(os.path.join(SERVER_DIR, "18_users.py"))
names = []
for node in ast.parse(src).body:
    if isinstance(node, ast.FunctionDef):
        names.append(node.name)
    elif isinstance(node, ast.Assign):
        names += [t.id for t in node.targets if isinstance(t, ast.Name)]
d = tempfile.mkdtemp()
U = exec_functions(names, {"os": os, "json": json, "re": re, "time": time, "threading": threading, "hashlib": hashlib,
                           "_secrets": secrets, "AOC_DATA_DIR": d,
                           "_load_json_file": lambda p, default=None: json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default})
P = os.path.join(d, "users.json")
MASTER = "master-token-123456"
owner = {"id": "owner", "name": "Owner", "role": "owner"}

# roles
ok = U["_user_allowed"]
c.check("owner: everything", ok("owner", "POST", "/tunnel/start") and ok("owner", "GET", "/auth/info") and ok("owner", "POST", "/users/invite"))
c.check("member: work yes (approve, runs, entities, kill)", all(ok("member", "POST", p) for p in
        ("/orch/approve", "/orch/create", "/orch/entity_run", "/permission/decide", "/kill_session", "/voice/ask")))
c.check("member: a route nobody listed is the owner's (allowlist, audit FIND-002)", not ok("member", "POST", "/some/new_route")
        and ok("owner", "POST", "/some/new_route"))
c.check("member: not the hooks' own posts (a member could plant prompts on the owner's dashboard), not typing into sessions",
        not any(ok("member", "POST", p) for p in ("/permission/request", "/reply/request", "/context_save/send", "/update")))
c.check("member: merge / discard / a gate / orchestration settings are the owner's (FIND-004)",
        not any(ok("member", "POST", p) for p in ("/orch/merge", "/orch/discard", "/orch/gate", "/orch/settings")))
c.check("member: worktrees and session changes are the owner's (the handler allowed them only from this PC anyway)",
        not any(ok("member", "POST", p) for p in ("/worktrees/launch", "/worktrees/merge", "/session_changes/discard", "/ab/start")))
c.check("DELETE: a member dismisses a closed session, nothing else; a viewer nothing",
        ok("member", "DELETE", "/session/abc") and not ok("member", "DELETE", "/license") and not ok("member", "DELETE", "/auth/token")
        and not ok("viewer", "DELETE", "/session/abc"))
c.check("viewer: its two POSTs aren't DELETEs", not ok("viewer", "DELETE", "/voice/ask"))
c.check("member: no people, no AOC settings, nothing on the owner's PC", not any(ok("member", "POST", p) for p in
        ("/users/invite", "/tunnel/start", "/license", "/restart", "/release/publish/confirm", "/hooks_health/repair", "/orch/settings",
         "/voice/settings", "/notify_settings", "/memory_file", "/fixes/apply", "/launch", "/focus_session", "/danger_guard",
         "/selfupdate/install", "/webhook_settings")))
c.check("member: may read, but not the owner's token", ok("member", "GET", "/status") and not ok("member", "GET", "/auth/info")
        and not ok("member", "GET", "/users") and ok("member", "GET", "/users/me"))
c.check("viewer: reads, may ask the voice assistant, changes nothing", ok("viewer", "GET", "/orch/runs") and ok("viewer", "POST", "/voice/ask")
        and not any(ok("viewer", m, p) for m, p in (("POST", "/orch/approve"), ("POST", "/permission/decide"), ("DELETE", "/remove"),
                                                    ("POST", "/notes"))))
SCHED_POSTS = ("/orch/start", "/orch/schedule_save", "/orch/schedule_pause", "/orch/schedule_delete", "/orch/schedule_run")
c.check("scheduled runs: members schedule / edit / start / run now / pause / delete; viewers only read the list and commits",
        all(ok("member", "POST", p) for p in SCHED_POSTS)
        and ok("viewer", "GET", "/orch/schedules") and ok("viewer", "GET", "/orch/commits")
        and not any(ok("viewer", "POST", p) for p in SCHED_POSTS))
c.check("unknown role: nothing", not ok("hacker", "POST", "/notes"))

# every POST route the handler has, with the least role that may use it. A new
# route (or one gone) fails this until someone decides who gets it -- the
# allowlist above makes an undecided route the owner's, this makes it visible.
ROUTES = {
    "viewer": ["/voice/ask", "/whats_new/dismiss"],
    "member": ["/ask", "/clear_done", "/danger_guard/test", "/history_tags", "/kill_session", "/notes", "/orch/agent_delete",
               "/orch/agent_save", "/orch/approve", "/orch/baseline", "/orch/cancel", "/orch/create", "/orch/entity_delete",
               "/orch/entity_forget", "/orch/entity_pause", "/orch/entity_run", "/orch/entity_save", "/orch/judge", "/orch/plan",
               "/orch/schedule_delete", "/orch/schedule_pause", "/orch/schedule_run", "/orch/schedule_save", "/orch/start",
               "/permission/decide", "/prompts", "/prompts/delete", "/prompts/used", "/push/subscribe", "/push/unsubscribe",
               "/queue/cancel", "/remove", "/reply/send", "/reset", "/session_note", "/voice/confirm"],
    "owner": ["/ab/discard", "/ab/estimate", "/ab/keep", "/ab/start", "/agents/background/checkpoint", "/agents/background/relaunch",
              "/auth/token", "/budget_caps", "/budget_caps/continue", "/clients", "/context_save", "/context_save/cancel",
              "/context_save/send", "/danger_guard", "/fixes/apply", "/fixes/preview", "/fixes/undo", "/focus_session",
              "/hooks_health/auto_repair", "/hooks_health/repair", "/launch", "/license", "/limit_stop", "/memory_archive",
              "/memory_file", "/memory_file/undo", "/notify_center/clear", "/notify_center/set", "/notify_center/test",
              "/notify_settings", "/orch/discard", "/orch/gate", "/orch/merge", "/orch/settings", "/permission/request",
              "/permission/settings", "/push/settings", "/push/test", "/queue", "/recap/settings", "/reply/request", "/restart",
              "/restore_backup", "/selfupdate/install", "/session_changes/discard", "/session_changes/seen",
              "/session_changes/stage", "/session_changes/undo", "/shared/start", "/shared/stop", "/snitch_ping_now",
              "/tunnel/ngrok_authtoken", "/tunnel/start", "/tunnel/stop", "/update", "/users/cancel_invites", "/users/invite",
              "/users/update", "/voice/settings", "/webhook_fire", "/webhook_settings", "/webhook_test", "/worklog/summary",
              "/worktrees/discard", "/worktrees/launch", "/worktrees/merge", "/worktrees/stranded/commit",
              "/worktrees/stranded/discard", "/worktrees/stranded/relaunch"],
}
_hsrc = read_source(os.path.join(SERVER_DIR, "20_handler.py"))
posts = set()
for fn in ast.walk(ast.parse(_hsrc)):
    if isinstance(fn, ast.FunctionDef) and fn.name == "do_POST":
        for n in ast.walk(fn):
            if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) and n.left.id == "path_no_qs":
                posts |= {x.value for x in ast.walk(n.comparators[0])
                          if isinstance(x, ast.Constant) and isinstance(x.value, str) and x.value.startswith("/")}
table = {p for ps in ROUTES.values() for p in ps}
if posts != table:
    print("  new, undecided:", sorted(posts - table), "| listed but gone:", sorted(table - posts))
c.check(f"route table: every POST route of the handler decided ({len(posts)} routes)", posts == table)
c.check("route table: the member allowlist has no route the handler doesn't have", set(U["_MEMBER_POST"]) <= posts)
LEVEL = {"viewer": 0, "member": 1, "owner": 2}
bad = [(role, p) for least, ps in ROUTES.items() for p in ps for role in LEVEL
       if ok(role, "POST", p) != (LEVEL[role] >= LEVEL[least])]
if bad:
    print("  role / route not as the table says:", bad[:10])
c.check("route table: each role gets exactly the routes at or below it (role x route x expectation)", not bad)
c.check("the private shell (/terminal/ws, a GET websocket) is the owner's only -- found in review",
        ok("owner", "GET", "/terminal/ws") and not ok("member", "GET", "/terminal/ws") and not ok("viewer", "GET", "/terminal/ws"))
c.check("a GET that writes isn't for viewers", not ok("viewer", "GET", "/history/save_current") and ok("member", "GET", "/history/save_current"))

# /diff: a non-owner sees tracked files only, never a secret-looking one (audit FIND-006)
dr = U["_diff_refusal"]
c.check("/diff: the owner sees anything (inside the project)", dr("owner", ".env", False) == "" and dr("owner", "x.txt", False) == "")
c.check("/diff: a member / viewer sees a tracked file", dr("viewer", "src/app.py", True) == "" and dr("member", "README.md", True) == "")
c.check("/diff: ... not an untracked one", "isn't tracked" in dr("viewer", "notes/local.txt", False))
c.check("/diff: ... nor a secret-looking one, even tracked", all("secrets" in dr("member", f, True) for f in
        (".env", "app/.env.local", "certs\\server.pem", "deploy.key", "id_rsa", "id_ed25519.pub", "config/client_secret.json",
         "aws-credentials", ".ENV.production")))
c.check("/diff: ... names merely containing those letters are fine", all(dr("viewer", f, True) == "" for f in
        ("environment.md", "src/keyboard.js", "monkey.py", "docs/envelope.txt")))

# invite -> join
inv = U["_user_invite"]({"role": "member", "name": "Marco"}, owner, "https://abc.trycloudflare.com/", path=P)
c.check("invite: one-time code, link through the tunnel, 7 days", inv["ok"] and inv["link"] == f"https://abc.trycloudflare.com/join?code={inv['code']}"
        and inv["expires"] - time.time() > 6.9 * 86400)
stored = json.load(open(P, encoding="utf-8"))
c.check("only a hash of the code is kept", inv["code"] not in json.dumps(stored) and stored["invites"][0]["code_sha256"])
c.check("no tunnel: a path to append", U["_user_invite"]({"role": "viewer"}, owner, "", path=P)["path"].startswith("/join?code="))
c.check("owner can't be invited", not U["_user_invite"]({"role": "owner"}, owner, "", path=P)["ok"])
c.check("join needs a name", "name" in U["_user_join"]({"code": inv["code"], "name": " x "}, path=P)["error"])
j = U["_user_join"]({"code": inv["code"], "name": "Marco  Rossi"}, path=P)
c.check("join: a person with their own token, the invite's role", j["ok"] and j["user"] == {"id": "marco-rossi", "name": "Marco Rossi", "role": "member"}
        and len(j["token"]) > 20)
c.check("the invite works once", "isn't valid" in U["_user_join"]({"code": inv["code"], "name": "Again"}, path=P)["error"])
stored = json.load(open(P, encoding="utf-8"))
c.check("only a hash of the person's token is kept", j["token"] not in json.dumps(stored))
inv2 = U["_user_invite"]({"role": "viewer"}, owner, "", path=P)
c.check("same name -> its own id", U["_user_join"]({"code": inv2["code"], "name": "Marco Rossi"}, path=P)["user"]["id"] == "marco-rossi-2")

# tokens -> people
D = U["_users_load"](P)
c.check("the main token = owner", U["_user_for_token"](MASTER, MASTER, D)["role"] == "owner")
c.check("a person's token = that person", U["_user_for_token"](j["token"], MASTER, D) == {"id": "marco-rossi", "name": "Marco Rossi", "role": "member"})
c.check("wrong / empty token = nobody", U["_user_for_token"]("nope", MASTER, D) is None and U["_user_for_token"]("", MASTER, D) is None)
U["_users_update"]({"id": "marco-rossi", "disabled": True}, path=P)
c.check("disabled -> their token stops working", U["_user_for_token"](j["token"], MASTER, U["_users_load"](P)) is None)
U["_users_update"]({"id": "marco-rossi", "disabled": False, "role": "viewer"}, path=P)
c.check("role changed", U["_user_for_token"](j["token"], MASTER, U["_users_load"](P))["role"] == "viewer")
c.check("can't make someone owner", U["_users_update"]({"id": "marco-rossi", "role": "owner"}, path=P)["ok"]
        and U["_users_load"](P)["users"][0]["role"] == "viewer")
U["_users_update"]({"owner_name": "Marek"}, path=P)
c.check("the owner's name", U["_owner_user"](U["_users_load"](P))["name"] == "Marek")
U["_users_update"]({"id": "marco-rossi-2", "remove": True}, path=P)
c.check("removed", [u["id"] for u in U["_users_load"](P)["users"]] == ["marco-rossi"])
U["_user_invite"]({"role": "member"}, owner, "", path=P)
c.check("open invites listed, then cancelled", len(U["_users_view"](path=P)["invites"]) == 2
        and U["_users_cancel_invites"](path=P)["ok"] and U["_users_view"](path=P)["invites"] == [])

# wrong codes are limited
for _ in range(20):
    U["_user_join"]({"code": "guess", "name": "Eve"}, path=P)
c.check("20 wrong codes an hour -> /join refuses", "too many" in U["_user_join"]({"code": "guess", "name": "Eve"}, path=P)["error"])

# who's here
U["_users_seen"].clear()
U["_user_seen"]({"id": "marco-rossi", "name": "Marco Rossi", "role": "viewer"}, "orch", now=1000)
U["_user_seen"]({"id": "owner", "name": "Marek", "role": "owner"}, now=1100)
U["_user_seen"]({"id": "owner", "name": "Owner", "role": "owner", "background": True}, now=1200)
pr = U["_users_present"](now=1110)
c.check("who's here: newest first, with what they look at; background work isn't a person",
        [(p["name"], p["ago_s"], p["view"]) for p in pr] == [("Marek", 10, ""), ("Marco Rossi", 110, "orch")])
c.check("gone after 2 minutes", [p["name"] for p in U["_users_present"](now=1150)] == ["Marek"]
        and U["_users_present"](now=1300) == [])
c.check("background = owner", U["_current_user"]()["role"] == "owner")

# the join page
jp = U["_join_page"]('x"</script><script>alert(1)')
c.check("join page: a code from the link can't break out of the script (found in review: json.dumps keeps '</')",
        "</script><script>alert" not in jp and "\u003c/script>" in jp)

# the Handler
h = read_source(os.path.join(SERVER_DIR, "20_handler.py"))
c.check("handler: every GET / POST / DELETE is authorized after the token check",
        h.count("self._authorize(\"GET\"") == 1 and h.count("self._authorize(\"POST\"") == 1 and h.count("self._authorize(\"DELETE\"") == 1)
c.check("handler: the page carries the requester's own token, the owner's only for the owner",
        'self._req_token or (_auth_token if self._user["role"] == "owner" else "")' in h)
c.check("handler: /join is open (the invite is the key), the rest needs a token",
        h.index('if self.path.split("?")[0] == "/join":') < h.index("if not self._check_auth():"))
c.check("handler: a member's orchestration action passes _orch_member_refusal first (FIND-003/004)",
        'why = "" if self._user["role"] == "owner" else _orch_member_refusal(act, data)' in h
        and h.index("_orch_member_refusal(act, data)") < h.index("reply = _orch_create(data)"))
c.check("handler: /diff asks _diff_refusal for a non-owner, with git's tracked answer",
        "_diff_refusal(role, rel, _run([\"git\", \"ls-files\", \"--error-unmatch\"" in h)
c.check("handler: orchestration for the owner / members, also through the tunnel",
        'self._is_direct_loopback() or self._user["role"] in ("owner", "member")' in h)
_orch_routes = h[h.index('elif path_no_qs in ("/orch/create"'):]
_orch_routes = _orch_routes[:_orch_routes.index("):")]
c.check("handler: run now on a schedule sits behind the same owner / member check as the other orchestration POSTs",
        all(p in _orch_routes for p in SCHED_POSTS))

# tokenless owner from this PC -- but not for a web page the owner happens to visit
t = lambda host="127.0.0.1:5151", origin=None, site=None, mode=None, method="GET", path="/status": \
    U["_tokenless_owner_ok"](host, origin, site, mode, method, path, 5151)
c.check("tokenless: hook / watchdog / curl (no browser headers) -> owner", t() and t(host=None) and t(method="POST", path="/update"))
c.check("tokenless: the dashboard itself (typed URL, its own fetches)",
        t(path="/", site="none", mode="navigate") and t(origin="http://localhost:5151", site="same-origin", method="POST", path="/reset")
        and t(host="localhost:5151", origin="http://127.0.0.1:5151", site="same-origin") and t(host="[::1]:5151"))
c.check("tokenless: DNS rebinding (attacker's Host) -> no", not t(host="evil.example:5151") and not t(host="127.0.0.1.evil.example"))
c.check("tokenless: another site's fetch / form -> no", not t(origin="https://evil.example", site="cross-site", method="POST", path="/update")
        and not t(origin="http://localhost:9999", method="POST") and not t(origin="null", method="POST"))
c.check("tokenless: <img src> / <script src> from another site (no Origin) -> no",
        not t(site="cross-site", mode="no-cors", path="/auth/info") and not t(site="same-site", mode="no-cors"))
c.check("tokenless: websocket from another site -> no", not t(origin="https://evil.example", site="cross-site", mode="websocket", path="/terminal/ws"))
c.check("tokenless: a link from another site may open the dashboard page, nothing else",
        t(path="/", site="cross-site", mode="navigate") and not t(path="/auth/info", site="cross-site", mode="navigate")
        and not t(path="/", site="cross-site", mode="navigate", method="POST"))
c.check("handler: tokenless owner needs a direct loopback request AND _tokenless_owner_ok",
        "if self._is_direct_loopback() and not tunnel_exposed and _tokenless_owner_ok(" in h)

# /join lockout: per address, plus a global cap
U["_join_fails"][:] = []
for i in range(U["_JOIN_FAIL_MAX"]):
    U["_user_join"]({"code": "bad", "name": "Eve"}, path=P, client="6.6.6.6")
c.check("join: one guesser is locked out", "too many" in U["_user_join"]({"code": "bad", "name": "Eve"}, path=P, client="6.6.6.6")["error"])
c.check("join: ... others are not", "too many" not in U["_user_join"]({"code": "bad", "name": "Ann"}, path=P, client="1.2.3.4")["error"])
U["_join_fails"][:] = [(f"10.0.0.{i % 250}", time.time()) for i in range(U["_JOIN_FAIL_ALL"])]
c.check("join: many addresses together hit the global cap", "too many" in U["_user_join"]({"code": "bad", "name": "Ann"}, path=P, client="1.2.3.4")["error"])
U["_join_fails"][:] = []
c.finish()
