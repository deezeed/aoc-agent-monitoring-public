# AOC server, part 20: HTTP Handler (all routes).
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # enables keep-alive, eliminates TIME_WAIT storm
    def log_message(self, fmt, *args):
        pass

    def _is_direct_loopback(self) -> bool:
        """A request from this machine itself, not relayed by a tunnel
        (cloudflared / ngrok always add forwarding headers)."""
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return False
        return not any(self.headers.get(h) for h in ("X-Forwarded-For", "Cf-Connecting-Ip", "X-Forwarded-Host",
                                                      "X-Real-Ip", "Forwarded", "Ngrok-Trace-Id", "Cf-Ray"))

    def _check_auth(self) -> bool:
        """Return True if request is authorized. Localhost always allowed, UNLESS a
        Cloudflare tunnel is active: cloudflared forwards public traffic to this
        server over a local connection, so every tunnel request also looks like it
        came from 127.0.0.1. The localhost bypass must not apply in that case, or
        the tunnel would grant unauthenticated internet access (incl. the terminal)."""
        tunnel_exposed = _tunnel_status[0] in ("starting", "ready")
        self._user, self._req_token = None, ""
        # _is_direct_loopback too: a tunnel AOC doesn't know about (its own one
        # failed to stop, or ngrok / VS Code port forwarding) adds forwarding headers
        if self._is_direct_loopback() and not tunnel_exposed and _tokenless_owner_ok(
                self.headers.get("Host"), self.headers.get("Origin"), self.headers.get("Sec-Fetch-Site"),
                self.headers.get("Sec-Fetch-Mode"), self.command, self.path.split("?")[0], PORT):
            self._user = _owner_user()
            return True
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        # the main token = the owner; a person's own token (People) = that
        # person -- compared with hmac.compare_digest (no timing side channel)
        for tok in (qs.get("token", [""])[0], self.headers.get("X-AOC-Token", "")):
            u = _user_for_token(tok, _auth_token)
            if u:
                self._user, self._req_token = u, tok
                return True
        return False

    def _authorize(self, method, path) -> bool:
        """After _check_auth: may this person do this? Runs the rest of the
        request as them (who approved / merged ... and who's here)."""
        user = getattr(self, "_user", None) or _owner_user()
        _req_ctx.user = user
        _user_seen(user)
        if _user_allowed(user["role"], method, path):
            return True
        self._serve(403, "application/json", json.dumps(
            {"ok": False, "error": f"your role ({user['role']}) can't do that -- ask the owner"}).encode())
        return False

    def _serve_csv_download(self, csv_text, filename):
        if not _is_pro():
            self._serve(403, "application/json",
                        json.dumps({"ok": False, "error": "CSV export is a Pro feature. Settings -> LICENSE to upgrade."}).encode())
            return
        csv_body = csv_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(csv_body))
        self.end_headers()
        self.wfile.write(csv_body)

    def _get_csv_date_range(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        return (qs.get("from") or [""])[0], (qs.get("to") or [""])[0]

    def _get_cache_stats(self):
        """Prompt-cache report (History -> CACHE) from transcript_index.db's
        usage table. ?days=7|30|0 (0 = everything indexed)."""
        from urllib.parse import urlparse, parse_qs
        try:
            days = max(0, min(int((parse_qs(urlparse(self.path).query).get("days") or ["30"])[0]), 3650))
        except ValueError:
            days = 30
        since = "0000-00-00" if days == 0 else time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))
        out = {"days": days, "index": dict(_transcript_index_progress), "stats": None}
        if os.path.exists(TRANSCRIPT_INDEX_DB):
            conn = None
            try:
                conn = _transcript_index_connect()
                out["stats"] = _cache_stats(conn, since)
            except Exception as e:
                _log_bg_error("_get_cache_stats", e)
                out["error"] = "cache stats failed"
            finally:
                if conn is not None:
                    conn.close()
        self._serve(200, "application/json", json.dumps(out, ensure_ascii=False).encode())

    def _get_clients(self, path):
        """History -> CLIENTS. /clients?month=YYYY-MM -> {config, report};
        /clients/export.csv?month&client (Pro, like every CSV export);
        /clients/print?month&client -> printable page (Save as PDF);
        /clients/report?month&client&lang -> the report for the client
        (worked time, what was done; no costs)."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        month = (qs.get("month") or [""])[0]
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            month = time.strftime("%Y-%m")
        client = (qs.get("client") or [None])[0]
        cfg = _load_clients()
        report, error, month_report = None, None, None
        if os.path.exists(TRANSCRIPT_INDEX_DB):
            conn = None
            try:
                conn = _transcript_index_connect()
                report = _client_report(conn, month, cfg, os.path.expanduser("~"))
                if path == "/clients/report":
                    note = next((c.get("note", "") for c in cfg["clients"] if c["name"] == client), "")
                    month_report = _client_month_report(conn, report, client or "", note)
            except Exception as e:
                _log_bg_error("_get_clients", e)
                error = "client report failed"
            finally:
                if conn is not None:
                    conn.close()
        if path == "/clients":
            out = {"month": month, "config": cfg, "report": report, "index": dict(_transcript_index_progress)}
            if error:
                out["error"] = error
            self._serve(200, "application/json", json.dumps(out, ensure_ascii=False).encode(), no_cache=True)
        elif report is None:
            self._serve(503, "text/plain", b"The transcript index is not ready yet.")
        elif path == "/clients/export.csv":
            self._serve_csv_download(_client_report_csv(report, client, bom=True),
                                     f"aoc-{_client_file_slug(client)}-{month}.csv")
        elif path == "/clients/report":
            if month_report is None:
                self._serve(404, "text/plain; charset=utf-8", "No such client.".encode())
            else:
                lang = (qs.get("lang") or [""])[0]
                self._serve(200, "text/html; charset=utf-8",
                            _client_month_report_html(month_report, lang).encode("utf-8"), no_cache=True)
        else:
            self._serve(200, "text/html; charset=utf-8", _client_report_print_html(report, client or "").encode("utf-8"),
                        no_cache=True)

    def _worklog_from_query(self, qs):
        """(report, error) for ?from&to&group from transcript_index.db."""
        d0, d1 = _worklog_range((qs.get("from") or [""])[0], (qs.get("to") or [""])[0], time.strftime("%Y-%m-%d"))
        group = "client" if (qs.get("group") or [""])[0] == "client" else "project"
        if not os.path.exists(TRANSCRIPT_INDEX_DB):
            return None, None
        conn = None
        try:
            conn = _transcript_index_connect()
            return _worklog_report(conn, d0, d1, group, _load_clients(), os.path.expanduser("~")), None
        except Exception as e:
            _log_bg_error("worklog", e)
            return None, "work log failed"
        finally:
            if conn is not None:
                conn.close()

    def _get_worklog(self, path):
        """History -> WORK LOG. /worklog?from&to&group=project|client ->
        {report, summaries: {standup, client}} (cached summaries only);
        /worklog/print?...&mode=standup|client -> printable page;
        /worklog/export.csv?... (Pro, like every CSV export)."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        report, error = self._worklog_from_query(qs)
        if path == "/worklog":
            out = {"report": report, "index": dict(_transcript_index_progress),
                   "summaries": _worklog_cached_summaries(report) if report else {}}
            if error:
                out["error"] = error
            self._serve(200, "application/json", json.dumps(out, ensure_ascii=False).encode(), no_cache=True)
        elif report is None:
            self._serve(503, "text/plain", b"The transcript index is not ready yet.")
        elif path == "/worklog/export.csv":
            self._serve_csv_download(_worklog_csv(report), f"aoc-worklog-{report['from']}-{report['to']}.csv")
        else:
            mode = (qs.get("mode") or [""])[0]
            summary = _worklog_cached_summaries(report).get(mode) if mode in _WORKLOG_MODES else None
            self._serve(200, "text/html; charset=utf-8", _worklog_print_html(report, summary).encode("utf-8"), no_cache=True)

    def _get_commit_costs(self):
        """Cost per commit (History -> COMMITS) from transcript_index.db.
        ?days=7|30|0 (0 = everything indexed)."""
        from urllib.parse import urlparse, parse_qs
        try:
            days = max(0, min(int((parse_qs(urlparse(self.path).query).get("days") or ["30"])[0]), 3650))
        except ValueError:
            days = 30
        since = "0000-00-00" if days == 0 else time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))
        out = {"days": days, "index": dict(_transcript_index_progress), "stats": None}
        if os.path.exists(TRANSCRIPT_INDEX_DB):
            conn = None
            try:
                conn = _transcript_index_connect()
                out["stats"] = _commit_stats(conn, since)
            except Exception as e:
                _log_bg_error("_get_commit_costs", e)
                out["error"] = "commit costs failed"
            finally:
                if conn is not None:
                    conn.close()
        self._serve(200, "application/json", json.dumps(out, ensure_ascii=False).encode())

    def _get_search_transcripts(self):
        """Full-text search over past conversations (transcript_index.db,
        kept current by _transcript_index_worker). Always returns the index
        progress so the UI can say 'still indexing' instead of 'no match'."""
        from urllib.parse import urlparse, parse_qs
        q = (parse_qs(urlparse(self.path).query).get("q") or [""])[0][:200]
        out = {"query": q, "sessions": [], "index": dict(_transcript_index_progress)}
        if q.strip() and os.path.exists(TRANSCRIPT_INDEX_DB):
            conn = None
            try:
                conn = _transcript_index_connect()
                out["sessions"] = _search_transcripts(conn, q)
            except Exception as e:
                _log_bg_error("_get_search_transcripts", e)
                out["error"] = "search failed"
            finally:
                if conn is not None:
                    conn.close()
        self._serve(200, "application/json", json.dumps(out, ensure_ascii=False).encode())

    def _get_auditlog(self):
        files = _log.list_logs()
        file_meta = []
        for fname in files:
            fpath = os.path.join(LOGS_DIR, fname)
            try:
                size = os.path.getsize(fpath)
            except Exception:
                size = 0
            file_meta.append({"name": fname, "size": size})
        payload = {
            "lines": _log.read_tail(200),
            "current": os.path.basename(_log.current_log_path() or ""),
            "files": files,
            "file_meta": file_meta,
        }
        self._serve(200, "application/json", json.dumps(payload, ensure_ascii=False).encode())

    def _get_diff(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        rel = qs.get("file", [""])[0]
        diff_text = ""
        if rel:
            try:
                base = _get_project_dir() or AOC_DIR
                # absolute path passed directly
                if os.path.isabs(rel):
                    full_path = rel
                    cwd = os.path.dirname(rel)
                    git_rel = os.path.basename(rel)
                else:
                    full_path = os.path.join(base, rel.replace("/", os.sep))
                    cwd = base
                    git_rel = rel
                # Require the resolved path to actually stay inside the current
                # project dir (or AOC's own dir as fallback) — an absolute `rel`
                # was otherwise accepted as-is, letting an authenticated request
                # read any file the process can (e.g. ?file=C:\Users\x\.ssh\id_rsa).
                base_real = os.path.realpath(base)
                full_real = os.path.realpath(full_path)
                if full_real != base_real and not full_real.startswith(base_real + os.sep):
                    diff_text = f"(access denied: {rel} is outside the project directory)"
                else:
                    # 1) unstaged changes
                    r = _run(["git", "diff", "HEAD", "--", git_rel], cwd=cwd, timeout=5)
                    if r.stdout.strip():
                        diff_text = r.stdout
                    else:
                        # 2) last commit changes for this file
                        r2 = _run(["git", "log", "-1", "--pretty=format:", "-p", "--", git_rel], cwd=cwd, timeout=5)
                        if r2.stdout.strip():
                            diff_text = r2.stdout
                    if not diff_text.strip():
                        # 3) fallback: show full file content
                        if os.path.exists(full_path):
                            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                                lines = f.readlines()
                            diff_text = f"--- /dev/null\n+++ {rel}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{l}" for l in lines)
                        else:
                            diff_text = f"(file not found: {rel})"
            except Exception as e:
                diff_text = f"Error: {e}"
        else:
            diff_text = "No file specified"
        self._serve(200, "application/json",
                    json.dumps({"diff": diff_text}, ensure_ascii=False).encode())

    def _get_git(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        cwd = qs.get('cwd', [AOC_DIR])[0]
        # cwd is a request param, not a trusted internal path -- the one
        # legitimate caller (_termUpdateGit) only ever passes a cwd
        # already sitting on a currently-tracked session (see that JS
        # function's own comment), so require it to actually be one of
        # those rather than running `git -C <anything>` against whatever
        # directory an authenticated request names, which would let it
        # probe branch/status for any directory this process can read.
        try:
            _known_cwds = {AOC_DIR} | {
                s.get("cwd") for s in _load_status().get("sessions", {}).values() if s.get("cwd")
            }
        except Exception:
            _known_cwds = {AOC_DIR}
        if cwd not in _known_cwds:
            self._serve(200, 'application/json', b'{"branch":null,"changes":0}')
            return
        try:
            branch = _run(['git','-C',cwd,'rev-parse','--abbrev-ref','HEAD'], timeout=5).stdout.strip()
            status_out = _run(['git','-C',cwd,'status','--short'], timeout=5).stdout.strip()
            changes = len([l for l in status_out.splitlines() if l.strip()])
            self._serve(200,'application/json',json.dumps(
                {'branch':branch,'changes':changes,'status':status_out[:500]},ensure_ascii=False).encode())
        except Exception:
            self._serve(200,'application/json',b'{"branch":null,"changes":0}')

    def _get_log_file(self):
        fname = os.path.basename(self.path[6:])
        fpath = os.path.join(LOGS_DIR, fname)
        if fname and os.path.exists(fpath) and fname.endswith(".log"):
            with open(fpath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        else:
            self._serve(404, "text/plain", b"Log not found")

    def do_GET(self):
        if self.path.split("?")[0] == "/restart_ok" and self._is_direct_loopback():
            # watchdog.py's code-reload probe (no token, like the hooks)
            body = {"pid": os.getpid(), "blockers": _restart_blockers(_perm_requests, _tunnel_status[0], _update_job.get("state"), _hooks_health["repairing"],
                                                                      _release_job.get("state"))}
            self._serve(200, "application/json", json.dumps(body).encode(), no_cache=True)
            return
        if self.path.split("?")[0] == "/join":
            # an invite link: no token yet, the code in it is the key
            from urllib.parse import urlparse, parse_qs
            code = (parse_qs(urlparse(self.path).query).get("code") or [""])[0]
            self._serve(200, "text/html; charset=utf-8", _join_page(code).encode("utf-8"), no_cache=True)
            return
        if not self._check_auth():
            self._serve(401, "text/plain", b"Unauthorized - token required")
            return
        path_no_qs = self.path.split("?")[0]
        if not self._authorize("GET", path_no_qs):
            return
        if path_no_qs in ("/", "/index.html"):
            # the page carries the token of whoever opened it -- never the owner's for someone else
            html = HTML.replace("__AOC_TOKEN_PLACEHOLDER__", self._req_token or (_auth_token if self._user["role"] == "owner" else "") or "") \
                       .replace("__AOC_IS_PRO_PLACEHOLDER__", "true" if _is_pro() else "false")
            self._serve(200, "text/html; charset=utf-8", html.encode())
        elif path_no_qs == "/me":
            self._serve(200, "application/json", json.dumps(dict(self._user, ok=True)).encode(), no_cache=True)
        elif path_no_qs == "/users":
            self._serve(200, "application/json", json.dumps(_users_view(), ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs in ("/icon.svg", "/favicon.ico"):
            self._serve(200, "image/svg+xml", _ICON_SVG.encode())
        elif path_no_qs == "/manifest.json":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            tok = (qs.get("token") or [""])[0]
            manifest = _build_manifest(tok)
            self._serve(200, "application/json", json.dumps(manifest).encode(), no_cache=True)
        elif path_no_qs == "/sw.js":
            # No offline caching (this is a live dashboard, a stale cached
            # snapshot would be actively misleading), just enough of a fetch
            # handler for PWA installability on Android/Chromium -- plus the
            # Web Push handlers (_push_notify).
            self._serve(200, "application/javascript", _SW_JS.encode(), no_cache=True)
        elif path_no_qs == "/prompts":
            self._serve(200, "application/json", json.dumps({"prompts": _prompts_list()}, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/push/info":
            subs = _push_subs()
            self._serve(200, "application/json", json.dumps({
                "public_key": _vapid_keys()[1], **_load_push_settings(),
                "devices": [{"endpoint": x["endpoint"], "label": x.get("label", ""), "created": x.get("created")} for x in subs],
            }).encode(), no_cache=True)
        elif path_no_qs == "/auth/info":
            self._serve(200, "application/json", json.dumps(_auth_info(), ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/license":
            self._serve(200, "application/json", json.dumps(_license_info(), ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/permission/poll":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            try:
                idle_s = float((qs.get("idle") or [""])[0])
            except ValueError:
                idle_s = None
            reply = _perm_poll((qs.get("id") or [""])[0], idle_s, _load_remote_approve(), time.time())
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/session_replay":
            from urllib.parse import urlparse, parse_qs
            sid = (parse_qs(urlparse(self.path).query).get("session_id") or [""])[0]
            try:
                reply = _session_replay(sid)
            except Exception as e:
                _log_bg_error("session_replay", e)
                reply = {"ok": False, "error": "could not read the transcript"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/budget_caps":
            with _budget_lock:
                st = {"today_spend": _budget_state["today_spend"], "day": _budget_state["day"]}
            cfg = _load_budget_caps()
            self._serve(200, "application/json", json.dumps({"session_usd": cfg["session_usd"], "day_usd": cfg["day_usd"], **st}).encode(), no_cache=True)
        elif path_no_qs == "/danger_guard":
            view = _danger_view(_load_danger_guard(), _fix_read_json(_fix_paths()["settings"], {}))
            view["log"] = _danger_log_read()
            self._serve(200, "application/json", json.dumps(view, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/reply/poll":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            try:
                idle_s = float((qs.get("idle") or [""])[0])
            except ValueError:
                idle_s = None
            reply = _reply_poll((qs.get("id") or [""])[0], idle_s, _load_remote_approve(), time.time())
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/permission/settings":
            self._serve(200, "application/json", json.dumps(_load_remote_approve()).encode(), no_cache=True)
        elif path_no_qs == "/recap":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            sid = (qs.get("session_id") or [""])[0]
            force = (qs.get("force") or ["0"])[0] == "1"
            if _load_recap_settings()["mode"] == "off" and not force:
                reply = {"ok": False, "error": "recaps are off"}
            else:
                reply = _recap_get(sid, force=force)
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/recap/settings":
            self._serve(200, "application/json", json.dumps(_load_recap_settings()).encode(), no_cache=True)
        elif path_no_qs == "/ask/history":
            self._serve(200, "application/json", json.dumps(_ask_history(), ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs in ("/session_changes", "/session_changes/diff"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            sid = (qs.get("session_id") or [""])[0]
            edited = _session_edited(sid)
            try:
                if path_no_qs == "/session_changes":
                    reply = {"ok": True, "edited": len(edited), **_session_changes(edited)}
                    seen = _change_seen_marks(sid, [f["path"] for r in reply["repos"] for f in r["files"]])
                    for r in reply["repos"]:
                        for f in r["files"]:
                            f["seen"] = bool(seen.get(f["path"]))
                            f["sig"] = _file_sig(f["path"])
                    reply["discards"] = [{k: m.get(k) for k in ("id", "path", "rel", "kind", "at")}
                                         for m in _change_backups(sid)]
                    # review actions write to the repo: only offered on this PC itself
                    reply["local"] = self._is_direct_loopback() and _tunnel_status[0] not in ("starting", "ready")
                    st = _own_status_payload() or {}
                    me = next((s for s in st.get("sessions_list") or [] if s.get("id") == sid), None)
                    reply["working"] = bool(me and me.get("session_active") is not False and not me.get("waiting_on_you"))
                else:
                    want = os.path.normcase(os.path.normpath((qs.get("file") or [""])[0]))
                    own = next((f for f in edited if os.path.normcase(os.path.normpath(f)) == want), None)
                    # only a file this session edited -- never an arbitrary path
                    reply = {"ok": True, "diff": _session_file_diff(own)} if own else \
                        {"ok": False, "error": "not a file this session edited"}
            except Exception as e:
                _log_bg_error("session_changes", e)
                reply = {"ok": False, "error": "failed"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/session_activity":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            try:
                hours = max(1, min(24, int((qs.get("hours") or ["24"])[0])))
            except ValueError:
                hours = 24
            reply = _sessions_activity(time.time() - hours * 3600)
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/status":
            try:
                data = _build_status_payload()
                self._serve(200, "application/json", json.dumps(data, ensure_ascii=False).encode(), no_cache=True)
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())
        elif path_no_qs == "/metrics":
            try:
                data = _build_status_payload()
                analytics = _db_analytics()
                body = _render_prometheus_metrics(data, analytics)
                self._serve(200, "text/plain", body.encode(), no_cache=True)
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())
        elif path_no_qs == "/events":
            # WebSocket push: replaces the old 500ms client-side poll loop.
            if self.headers.get('Upgrade', '').lower() == 'websocket':
                _handle_events_ws(self)
            else:
                self._serve(400, "text/plain", b"WebSocket required")
        elif path_no_qs == "/history":
            sessions = _db_get_sessions(100)
            self._serve(200, "application/json", json.dumps(sessions, ensure_ascii=False).encode())
        elif path_no_qs == "/history/search":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            query = (qs.get("q") or [""])[0]
            date_from = (qs.get("from") or [""])[0]
            date_to = (qs.get("to") or [""])[0]
            sessions = _db_search_sessions(query, date_from, date_to)
            self._serve(200, "application/json", json.dumps(sessions, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/history/save_current":
            try:
                current = _load_status()
                _db_save_session(current)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())
        elif path_no_qs.startswith("/history/"):
            sid = path_no_qs[len("/history/"):]
            detail = _db_get_session_detail(sid)
            self._serve(200, "application/json", json.dumps(detail, ensure_ascii=False).encode())
        elif path_no_qs == "/analytics":
            data = _db_analytics()
            self._serve(200, "application/json", json.dumps(data, ensure_ascii=False).encode())
        elif path_no_qs == "/export_costs.csv":
            date_from, date_to = self._get_csv_date_range()
            self._serve_csv_download(_export_costs_csv(date_from, date_to),
                                      f"aoc_costs_{date_from or 'all'}_{date_to or 'all'}.csv")
        elif path_no_qs == "/export_costs_by_model.csv":
            date_from, date_to = self._get_csv_date_range()
            self._serve_csv_download(_export_costs_by_model_csv(date_from, date_to),
                                      f"aoc_costs_by_model_{date_from or 'all'}_{date_to or 'all'}.csv")
        elif path_no_qs == "/export_costs_by_subagent_type.csv":
            date_from, date_to = self._get_csv_date_range()
            self._serve_csv_download(_export_costs_by_subagent_type_csv(date_from, date_to),
                                      f"aoc_costs_by_agent_type_{date_from or 'all'}_{date_to or 'all'}.csv")
        elif path_no_qs == "/update/status":
            self._serve(200, "application/json", json.dumps(_update_status_view(), ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/release/status":
            # the buttons only show where they work: this PC, a dev checkout
            view = _release_status_view()
            view["available"] = bool(view["available"] and self._is_direct_loopback())
            self._serve(200, "application/json", json.dumps(view, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/update/log":
            self._serve(200, "application/json", json.dumps(_install_log_tail(), ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/hooks_health":
            if "refresh=1" in self.path or _hooks_health["checks"] is None:
                _run_setup("--check")
            self._serve(200, "application/json", json.dumps(_hooks_health_view()).encode(), no_cache=True)
        elif path_no_qs == "/diag":
            data = _get_diag_info()
            self._serve(200, "application/json", json.dumps(data, ensure_ascii=False).encode())
        elif path_no_qs == "/backups":
            self._serve(200, "application/json", json.dumps(_list_backups(), ensure_ascii=False).encode())
        elif path_no_qs == "/webhook_settings":
            self._serve(200, "application/json", json.dumps(_webhook_settings, ensure_ascii=False).encode())
        elif path_no_qs == "/notify_settings":
            self._serve(200, "application/json", json.dumps(_notify_settings, ensure_ascii=False).encode())
        elif path_no_qs == "/notify_center":
            self._serve(200, "application/json", json.dumps(_notify_center_payload(), ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/whats_new":
            view = _whats_new_view(_read_installed_version(AOC_VERSION_FILE))
            self._serve(200, "application/json", json.dumps(view, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/context_save":
            self._serve(200, "application/json",
                        json.dumps({**_ctxsave_settings(), "default_prompt": _CTXSAVE_DEFAULT_PROMPT},
                                   ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/errors":
            data = _db_get_errors(100)
            self._serve(200, "application/json", json.dumps(data, ensure_ascii=False).encode())
        elif path_no_qs == "/auditlog":
            self._get_auditlog()
        elif path_no_qs == "/search_transcripts":
            self._get_search_transcripts()
        elif path_no_qs == "/launch/dirs":
            self._serve(200, "application/json", json.dumps({"dirs": _launch_known_dirs()}, ensure_ascii=False).encode(),
                        no_cache=True)
        elif path_no_qs == "/worktrees/stranded":
            try:
                reply = _wt_stranded_list((_own_status_payload() or {}).get("sessions_list") or [])
            except Exception as e:
                _log_bg_error("stranded worktrees", e)
                reply = {"ok": False, "error": "failed", "items": []}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/agents/background":
            reply = _bg_agents_snapshot(fresh="fresh=1" in self.path)
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/voice/settings":
            self._serve(200, "application/json", json.dumps(_voice_settings()).encode(), no_cache=True)
        elif path_no_qs in ("/orch/runs", "/orch/run", "/orch/agents", "/orch/catalog", "/orch/workspaces", "/orch/entities", "/orch/settings", "/orch/stats", "/orch/compare"):
            from urllib.parse import urlparse, parse_qs
            try:
                if path_no_qs == "/orch/agents":
                    reply = _orch_agents_list()
                elif path_no_qs == "/orch/stats":
                    reply = _orch_stats()
                elif path_no_qs == "/orch/compare":
                    reply = _orch_compare((parse_qs(urlparse(self.path).query).get("id") or [""])[0])
                elif path_no_qs == "/orch/settings":
                    reply = _orch_settings()
                elif path_no_qs == "/orch/entities":
                    reply = _orch_entities_list()
                elif path_no_qs == "/orch/workspaces":
                    reply = _orch_workspaces()
                elif path_no_qs == "/orch/catalog":
                    reply = _orch_catalog(fresh="fresh=1" in self.path)
                elif path_no_qs == "/orch/runs":
                    reply = _orch_list()
                else:
                    reply = _orch_get((parse_qs(urlparse(self.path).query).get("id") or [""])[0])
            except Exception as e:
                _log_bg_error("orchestration", e)
                reply = {"ok": False, "error": "failed"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs in ("/worktrees", "/worktrees/diff"):
            from urllib.parse import urlparse, parse_qs
            try:
                if path_no_qs == "/worktrees":
                    reply = _wt_list((_own_status_payload() or {}).get("sessions_list") or [])
                else:
                    reply = _wt_diff((parse_qs(urlparse(self.path).query).get("id") or [""])[0])
            except Exception as e:
                _log_bg_error("worktrees", e)
                reply = {"ok": False, "error": "failed", "items": []}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs in ("/ab", "/ab/get"):
            from urllib.parse import urlparse, parse_qs
            if path_no_qs == "/ab":
                reply = _ab_list()
            else:
                reply = _ab_get(str((parse_qs(urlparse(self.path).query).get("id") or [""])[0])) or {"error": "not found"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/model_advice":
            from urllib.parse import urlparse, parse_qs
            try:
                days = int((parse_qs(urlparse(self.path).query).get("days") or ["30"])[0])
            except ValueError:
                days = 30
            days = 30 if days not in (7, 30, 90) else days
            try:
                reply = _model_advice(days)
            except Exception as e:
                _log_bg_error("model_advice", e)
                reply = {"error": "model advice failed"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/fixes":
            applied = [{k: r.get(k) for k in ("id", "key", "title", "at")} for r in _fix_load_log()]
            self._serve(200, "application/json", json.dumps({"applied": applied}, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/failure_map":
            from urllib.parse import urlparse, parse_qs
            try:
                days = int((parse_qs(urlparse(self.path).query).get("days") or ["30"])[0])
            except ValueError:
                days = 30
            days = 30 if days not in (7, 30, 90) else days
            try:
                reply = _failure_map(days)
            except Exception as e:
                _log_bg_error("failure_map", e)
                reply = {"error": "failure map failed"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/sysinfo":
            try:
                reply = _sysinfo()
            except Exception as e:
                _log_bg_error("sysinfo", e)
                reply = {"error": "sysinfo failed"}
            self._serve(200, "application/json", json.dumps(reply).encode(), no_cache=True)
        elif path_no_qs == "/permission_advice":
            from urllib.parse import urlparse, parse_qs
            try:
                days = int((parse_qs(urlparse(self.path).query).get("days") or ["30"])[0])
            except ValueError:
                days = 30
            days = 30 if days not in (7, 30, 90) else days
            try:
                reply = _permission_advice(days)
            except Exception as e:
                _log_bg_error("permission_advice", e)
                reply = {"error": "permission advice failed"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/resume_projects":
            try:
                st = _own_status_payload() or {}
                open_ids = [s.get("id") for s in (st.get("sessions_list") or []) if s.get("session_active") is not False]
                reply = _resume_report(open_ids)
            except Exception as e:
                _log_bg_error("resume_projects", e)
                reply = {"error": "failed", "cards": []}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/memory_lint":
            try:
                reply = _memory_lint()
            except Exception as e:
                _log_bg_error("memory_lint", e)
                reply = {"error": "memory lint failed"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/memory_file":
            from urllib.parse import urlparse, parse_qs
            f = _mem_known((parse_qs(urlparse(self.path).query).get("path") or [""])[0])
            if not f:
                self._serve(404, "application/json", b'{"error":"unknown file"}'); return
            self._serve(200, "application/json", json.dumps({"path": f["path"], "kind": f["kind"], "text": f["text"],
                                                             "mtime": f["mtime"]}, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/memory_archive":
            from urllib.parse import urlparse, parse_qs
            try:
                reply = _memory_archive_preview((parse_qs(urlparse(self.path).query).get("path") or [""])[0])
            except Exception as e:
                _log_bg_error("memory_archive", e)
                reply = {"ok": False, "error": "Preparing the preview failed."}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/context_report":
            from urllib.parse import urlparse, parse_qs
            try:
                days = int((parse_qs(urlparse(self.path).query).get("days") or ["30"])[0])
            except ValueError:
                days = 30
            days = 30 if days not in (7, 30, 90) else days
            try:
                reply = _context_report(days)
            except Exception as e:
                _log_bg_error("context_report", e)
                reply = {"error": "context report failed"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/cache_stats":
            self._get_cache_stats()
        elif path_no_qs == "/commit_costs":
            self._get_commit_costs()
        elif path_no_qs in ("/clients", "/clients/export.csv", "/clients/print", "/clients/report"):
            self._get_clients(path_no_qs)
        elif path_no_qs in ("/worklog", "/worklog/print", "/worklog/export.csv"):
            self._get_worklog(path_no_qs)
        elif self.path.startswith("/diff"):
            self._get_diff()
        elif self.path.startswith("/git"):
            self._get_git()
        elif path_no_qs == "/terminal/ws":
            if self.headers.get('Upgrade','').lower() == 'websocket':
                _handle_terminal_ws(self)
            else:
                self._serve(400, "text/plain", b"WebSocket required")
        elif path_no_qs == "/shared":
            _shared_sweep()
            self._serve(200, "application/json", json.dumps(_shared_list(), ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/shared/ws":
            # everyone signed in may watch; who may type is the role's (_shared_input_allowed)
            from urllib.parse import urlparse, parse_qs
            if self.headers.get('Upgrade','').lower() == 'websocket':
                _handle_shared_ws(self, (parse_qs(urlparse(self.path).query).get("id") or [""])[0], self._user)
            else:
                self._serve(400, "text/plain", b"WebSocket required")
        elif self.path.startswith("/logs/"):
            self._get_log_file()
        else:
            self._serve(404, "text/plain", b"Not found")

    def do_DELETE(self):
        if not self._check_auth():
            self._serve(401, "text/plain", b"Unauthorized"); return
        if not self._authorize("DELETE", self.path.split("?")[0]):
            return
        # Stripped once here (mirroring do_GET's path_no_qs) rather than at
        # each elif -- a Remote Machine call always carries `?token=...`
        # (see README's Security model: the auth token travels as a query
        # param, not a header, for any cross-origin request), so matching
        # against the raw self.path used to either 404 on every remote
        # DELETE or, worse for /session/<id>, silently glue the query
        # string onto the extracted id and dismiss/create the wrong
        # session entry instead of failing loudly.
        path_no_qs = self.path.split("?")[0]
        if path_no_qs == "/auth/token":
            _delete_auth_token()
            self._serve(200, "application/json", json.dumps(_auth_info()).encode())
        elif path_no_qs == "/license":
            _delete_license()
            self._serve(200, "application/json", json.dumps(_license_info()).encode())
        elif path_no_qs.startswith("/session/"):
            sid = path_no_qs[len("/session/"):]
            try:
                from urllib.parse import unquote
                sid = unquote(sid)
                with _status_lock:
                    status = _load_status()
                    sessions = status.get("sessions", {})
                    if sid in sessions:
                        sessions[sid]["dismissed"] = True
                        sessions[sid]["session_active"] = False
                    else:
                        sessions[sid] = {"dismissed": True, "session_active": False}
                    status["sessions"] = sessions
                    _save_status(status)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())
        else:
            self._serve(404, "text/plain", b"Not found")

    def do_OPTIONS(self):
        """CORS preflight. `_serve()` already sends
        Access-Control-Allow-Origin on every real response, which is
        enough for a cross-origin GET (Remote Machines' own /status poll)
        or a bodyless POST -- browsers only send those straight through.
        DELETE and a POST with a JSON body are not "simple requests"
        though, so the browser sends this OPTIONS preflight first and
        withholds do_DELETE/do_POST entirely until it sees the right
        headers back here -- without this handler, BaseHTTPRequestHandler
        has no do_OPTIONS and falls back to a 501, which is exactly what
        silently made Force Stop/Dismiss no-ops against a merged-in
        Remote Machine (see README's Remote Machines section) despite
        those endpoints working fine called locally the whole time.
        No auth check here: a preflight carries no body and the actual
        token still travels as `?token=` on the real request that
        follows, checked by that request's own do_POST/do_DELETE exactly
        as before -- this only stops the browser from blocking that
        request before it's ever sent."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _post_update(self, body):
        try:
            update = json.loads(body)
            with _status_lock:
                status = _load_status()
                now = _now_ts()
                now_epoch = time.time()

                # ── multi-session tracking ──
                session_id = update.get("session_id", "default")
                cwd = update.get("cwd", "")
                sessions = status.setdefault("sessions", {})
                if session_id not in sessions:
                    sessions[session_id] = {}
                # Dismiss is meant for CLOSED sessions (the ✕ button only shows when
                # session_active===false). If a live heartbeat reports the session
                # active again, the user is back in that same CLI — un-hide it instead
                # of leaving it permanently gone.
                if sessions[session_id].get("dismissed") and update.get("session_active"):
                    sessions[session_id]["dismissed"] = False
                sessions[session_id]["last_seen"] = now
                sessions[session_id]["last_seen_epoch"] = now_epoch
                if cwd:
                    cwd = _fix_mojibake(cwd)
                    sessions[session_id]["cwd"] = cwd
                    cwd_name = os.path.basename(cwd.rstrip("/\\")) or cwd
                    if not sessions[session_id].get("project"):
                        sessions[session_id]["project"] = cwd_name
                if "project" in update and update["project"]:
                    sessions[session_id]["project"] = update["project"]
                if "session_active" in update:
                    sessions[session_id]["session_active"] = update["session_active"]
                if "display_name" in update:
                    sessions[session_id]["display_name"] = _fix_mojibake(update["display_name"])
                if update.get("host_pid"):
                    sessions[session_id]["host_pid"] = update["host_pid"]
                if "waiting_on_you" in update:
                    # Real-time signal from the Stop/UserPromptSubmit heartbeat hook:
                    # Stop means Claude just finished a turn and control is back with
                    # the human; UserPromptSubmit means the human just replied. Lower
                    # latency than the transcript scanner (which also computes this,
                    # as a fallback for sessions where the hook silently misses).
                    _accumulate_waiting_time(sessions[session_id], update["waiting_on_you"], now_epoch)
                    sessions[session_id]["waiting_on_you"] = update["waiting_on_you"]
                    # the transcript scanner only overrides this with lines newer
                    # than the hook's word (see _transcript_woy_applies)
                    sessions[session_id]["waiting_hook_epoch"] = now_epoch
                    if update["waiting_on_you"]:
                        kind = update.get("waiting_kind")
                        sessions[session_id]["waiting_kind"] = kind if kind in ("turn", "permission", "question") else "turn"
                        sessions[session_id]["waiting_message"] = _fix_mojibake(str(update.get("waiting_message") or ""))[:300]
                    else:
                        sessions[session_id]["waiting_kind"] = ""
                        sessions[session_id]["waiting_message"] = ""
                # mark session as having had agents (used for sessions_count)
                if update.get("agents"):
                    sessions[session_id]["has_agents"] = True

                # ── auto-expire sessions inactive >30min ──
                cutoff = now_epoch - 1800
                for sid in list(sessions.keys()):
                    if sessions[sid].get("dismissed"):
                        continue  # keep dismissed sessions so they stay hidden
                    if sessions[sid].get("last_seen_epoch", now_epoch) < cutoff:
                        del sessions[sid]
                        status["agents"] = [
                            a for a in status.get("agents", [])
                            if a.get("session_id") != sid
                        ]

                session_project = sessions.get(session_id, {}).get("project", "")

                # ── update session-level fields ──
                for key in ("session_active", "project", "started_at"):
                    if key in update:
                        status[key] = update[key]

                # ── upsert agents ──
                for au in update.get("agents", []):
                    _apply_agent_update(status, au, session_id, session_project, now)

                _save_status(status)
            self._serve(200, "application/json", b'{"ok":true}')
        except Exception as e:
            self._serve(500, "text/plain", str(e).encode())

    def _post_notify_settings(self, body):
        try:
            data = json.loads(body)
            quiet_start = str(data.get("quiet_start", ""))[:5]
            quiet_end = str(data.get("quiet_end", ""))[:5]
            muted = data.get("muted_projects") or []
            if not isinstance(muted, list):
                muted = []
            muted = [str(p)[:100] for p in muted if str(p).strip()][:50]
            snitch_url = str(data.get("snitch_url", ""))[:500]
            digest_cadence = data.get("digest_cadence", "weekly")
            if digest_cadence not in ("daily", "weekly", "off"):
                digest_cadence = "weekly"
            backup_retention_days = _clamp_backup_retention_days(data.get("backup_retention_days", 14))
            agent_retention_hours = _clamp_agent_retention_hours(data.get("agent_retention_hours", 12))
            project_budgets = _sanitize_project_budgets(data.get("project_budgets") or {})
            _save_notify_settings({
                "quiet_start": quiet_start,
                "quiet_end": quiet_end,
                "muted_projects": muted,
                "snitch_url": snitch_url,
                "digest_cadence": digest_cadence,
                "backup_retention_days": backup_retention_days,
                "agent_retention_hours": agent_retention_hours,
                "project_budgets": project_budgets,
                # per-type toast/push toggles: the notification center owns them
                "channels": _sanitize_notify_channels(data["channels"]) if "channels" in data
                            else (_notify_settings.get("channels") or {}),
            })
            self._serve(200, "application/json", b'{"ok":true}')
        except Exception as e:
            self._serve(500, "text/plain", str(e).encode())

    def _post_tunnel_start(self, body):
        if not _is_pro():
            self._serve(403, "application/json",
                        json.dumps({"ok": False, "error": "Remote access (tunnel) is a Pro feature. Settings -> LICENSE to upgrade."}).encode())
            return
        if _tunnel_status[0] in ("starting", "downloading", "ready"):
            self._serve(200, "application/json",
                        json.dumps({"ok": True, "status": _tunnel_status[0], "token": _auth_token}).encode())
        else:
            try:
                provider = json.loads(body or b"{}").get("provider", "cloudflare")
            except Exception:
                provider = "cloudflare"
            if provider not in ("cloudflare", "ngrok"):
                provider = "cloudflare"
            # A tunnel with no auth token would be wide open to the internet
            # (the localhost bypass is disabled once tunnel_exposed is true) —
            # always provision one first so exposure never happens unauthenticated.
            if not _auth_token:
                _save_auth_token(_secrets.token_urlsafe(24))
            _tunnel_provider[0] = provider
            if provider == "ngrok":
                _start_ngrok_worker_thread()
            else:
                _start_tunnel_worker_thread()
            self._serve(200, "application/json",
                        json.dumps({"ok": True, "status": "starting", "token": _auth_token}).encode())

    def _post_tunnel_ngrok_authtoken(self, body):
        try:
            authtoken = str(json.loads(body).get("authtoken", "")).strip()
            ng = _find_ngrok()
            if not ng:
                self._serve(200, "application/json", json.dumps({"ok": False, "error": "ngrok.exe not found"}).encode())
            elif not authtoken:
                self._serve(200, "application/json", json.dumps({"ok": False, "error": "empty authtoken"}).encode())
            else:
                import subprocess as _sp
                flags = _sp.CREATE_NO_WINDOW if os.name == "nt" else 0
                r = _sp.run([ng, "config", "add-authtoken", authtoken],
                            capture_output=True, timeout=10, creationflags=flags)
                if r.returncode == 0:
                    _ngrok_authtoken_check_now()  # refresh now so the next /status poll reflects it
                    self._serve(200, "application/json", b'{"ok":true}')
                else:
                    self._serve(200, "application/json",
                                 json.dumps({"ok": False, "error": r.stderr.decode("utf-8", "replace")[:300]}).encode())
        except Exception as e:
            self._serve(500, "text/plain", str(e).encode())

    def _post_reset(self):
        try:
            # Every other read-modify-write of status.json (see /update, the
            # transcript scanner) holds _status_lock across the whole
            # read...save span; this one didn't. A hook /update landing
            # concurrently with a user-triggered reset could read status
            # between this read and the _save_status(idle) below, save its
            # own update, and then have it silently wiped out by idle's
            # unconditional "agents": [] a moment later.
            with _status_lock:
                # snapshot current session to history DB before wiping
                current = _load_status()
                if current.get("session_active") or any(
                    a.get("status") in ("done","error")
                    for a in current.get("agents",[])
                    if not str(a.get("id","")).startswith("hook_")
                ):
                    _db_save_session(current)
                _log.session_reset()
                global _prev_session_active
                with _prev_session_active_lock:
                    _prev_session_active = False
                idle = {
                    "started_at": None,
                    "session_active": False,
                    "project": "",
                    "agents": [],
                    "sessions": current.get("sessions", {}),
                }
                _save_status(idle)
                _autosave_id[0] = None   # new session gets a fresh autosave ID
                _autosave_time[0] = 0
                try:
                    os.remove(AUTOSAVE_ID_FILE)
                except Exception:
                    pass
            self._serve(200, "application/json", b'{"ok":true}')
        except Exception as e:
            self._serve(500, "text/plain", str(e).encode())

    def _post_kill_session(self, body):
        try:
            data = json.loads(body)
            sid = str(data.get("session_id", ""))
            remote_addr = self.client_address[0] if self.client_address else ""
            outcome = _kill_session_core(sid, remote_addr)
            code, payload = _KILL_OUTCOME_RESPONSES[outcome]
            self._serve(code, "application/json", payload)
        except Exception as e:
            self._serve(500, "text/plain", str(e).encode())

    def do_POST(self):
        if self.path.split("?")[0] == "/hook/pretool" and self._is_direct_loopback():
            # Claude Code's HTTP PreToolUse hook (budget caps) can't carry the
            # token, so it is answered without one -- but only straight from
            # this machine, never through a tunnel (which adds forwarding
            # headers). Read-only: it returns allow or deny, nothing else.
            length = int(self.headers.get("Content-Length", 0))
            try:
                hook = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                hook = {}
            with _budget_lock:
                reply = _budget_hook_reply(_budget_state, hook)
            self._serve(200, "application/json", json.dumps(reply).encode())
            return
        if self.path.split("?")[0] == "/hook/danger" and self._is_direct_loopback():
            # The dangerous-command guard's PreToolUse hook (Bash/PowerShell).
            # Same no-token loopback rule as /hook/pretool. A harmless command
            # gets {} at once; a dangerous one holds this request until the
            # dashboard answers (or Claude Code hangs up).
            length = int(self.headers.get("Content-Length", 0))
            try:
                hook = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                hook = {}
            try:
                reply = _danger_hook(hook, gone_fn=lambda: _socket_hung_up(self.connection))
            except Exception as e:
                _log_bg_error("danger guard", e)
                reply = {}
            try:
                self._serve(200, "application/json", json.dumps(reply).encode())
            except Exception:
                pass
            return
        if self.path.split("?")[0] == "/join":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(min(length, 4096)) or b"{}")
            except ValueError:
                data = {}
            self._serve(200, "application/json", json.dumps(_user_join(data, client=self.headers.get("Cf-Connecting-Ip") or self.client_address[0]), ensure_ascii=False).encode())
            return
        if not self._check_auth():
            self._serve(401, "text/plain", b"Unauthorized"); return
        if not self._authorize("POST", self.path.split("?")[0]):
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        # Stripped once here (mirroring do_GET's path_no_qs) -- every route
        # below used to match against the raw self.path, which worked by
        # coincidence only because a same-origin call never carried a query
        # string. A Remote Machine call always does (the auth token travels
        # as `?token=...`, see README's Security model), so every one of
        # these routes 404'd for a remote caller until this was stripped.
        path_no_qs = self.path.split("?")[0]

        if path_no_qs == "/update":
            self._post_update(body)

        elif path_no_qs in ("/fixes/preview", "/fixes/apply", "/fixes/undo"):
            # changes Claude Code's own config files: only from this PC itself
            if not self._is_direct_loopback() or _tunnel_status[0] in ("starting", "ready"):
                self._serve(403, "application/json", b'{"ok":false,"error":"local only"}'); return
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                if path_no_qs == "/fixes/undo":
                    reply = _fix_undo(str(data.get("id") or ""))
                else:
                    fix = {"kind": str(data.get("kind") or ""), "target": str(data.get("target") or "")}
                    reply = _fix_preview(fix) if path_no_qs == "/fixes/preview" else _fix_apply(fix)
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("fixes", e)
                self._serve(500, "application/json", b'{"ok":false,"error":"failed"}')

        elif path_no_qs == "/worklog/summary":
            # runs `claude -p` (costs money): only from this PC itself
            if not self._is_direct_loopback() or _tunnel_status[0] in ("starting", "ready"):
                self._serve(403, "application/json", b'{"ok":false,"error":"local only"}'); return
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                report, error = self._worklog_from_query({k: [str(data.get(k) or "")] for k in ("from", "to", "group")})
                if report is None:
                    reply = {"ok": False, "error": error or "the transcript index is not ready yet"}
                else:
                    reply = _worklog_summary(report, str(data.get("mode") or "standup"), force=bool(data.get("force")))
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("worklog_summary", e)
                self._serve(500, "application/json", b'{"ok":false,"error":"failed"}')

        elif path_no_qs == "/agents/background/relaunch":
            # starts a Claude Code session (costs money): only from this PC itself
            if not self._is_direct_loopback() or _tunnel_status[0] in ("starting", "ready"):
                self._serve(403, "application/json", b'{"ok":false,"error":"agents can only be relaunched on this PC itself"}'); return
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                reply = _bg_agent_relaunch(str(data.get("id") or ""))
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("background agents", e)
                self._serve(400, "application/json", b'{"ok":false,"error":"bad request"}')
        elif path_no_qs == "/agents/background/checkpoint":
            # commits in an agent's worktree (+ may start a session): only from this PC itself
            if not self._is_direct_loopback() or _tunnel_status[0] in ("starting", "ready"):
                self._serve(403, "application/json", b'{"ok":false,"error":"agents can only be checkpointed on this PC itself"}'); return
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                reply = _bg_agent_checkpoint(str(data.get("id") or ""), relaunch=bool(data.get("relaunch")))
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("background agents", e)
                self._serve(400, "application/json", b'{"ok":false,"error":"bad request"}')
        elif path_no_qs in ("/worktrees/stranded/commit", "/worktrees/stranded/relaunch", "/worktrees/stranded/discard"):
            # commits in / starts sessions in / deletes git worktrees: only from this PC itself
            if not self._is_direct_loopback() or _tunnel_status[0] in ("starting", "ready"):
                self._serve(403, "application/json", b'{"ok":false,"error":"worktrees can only be managed on this PC itself"}'); return
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                reply = _wt_stranded_act(path_no_qs.rsplit("/", 1)[1], str(data.get("id") or ""),
                                         (_own_status_payload() or {}).get("sessions_list") or [])
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("stranded worktrees", e)
                self._serve(500, "application/json", b'{"ok":false,"error":"failed"}')

        elif path_no_qs in ("/shared/start", "/shared/stop"):
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                reply = _shared_start(data, self._user) if path_no_qs == "/shared/start" else _shared_stop(data)
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("shared sessions", e)
                self._serve(500, "application/json", b'{"ok":false,"error":"failed"}')
        elif path_no_qs in ("/users/invite", "/users/update", "/users/cancel_invites"):
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                if path_no_qs == "/users/invite":
                    base = _tunnel_url[0] if _tunnel_url[0] else ""
                    reply = _user_invite(data, self._user, base)
                elif path_no_qs == "/users/update":
                    reply = _users_update(data)
                else:
                    reply = _users_cancel_invites()
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("people", e)
                self._serve(500, "application/json", b'{"ok":false,"error":"failed"}')
        elif path_no_qs in ("/voice/ask", "/voice/confirm", "/voice/settings"):
            # the assistant itself decides what works from here: starting / merging work and PC actions
            # only from this PC (not relayed by a tunnel), like the same buttons
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                local = self._is_direct_loopback()
                role = self._user["role"]
                if path_no_qs == "/voice/ask":
                    reply = _voice_ask(data, local, role=role)
                elif path_no_qs == "/voice/confirm":
                    reply = _voice_confirm(data, local, role=role)
                else:
                    reply = _voice_settings_save(data)
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("voice", e)
                self._serve(500, "application/json", b'{"ok":false,"error":"failed"}')
        elif path_no_qs in ("/orch/create", "/orch/plan", "/orch/approve", "/orch/cancel", "/orch/merge", "/orch/discard", "/orch/gate",
                            "/orch/baseline", "/orch/judge",
                            "/orch/agent_save", "/orch/agent_delete", "/orch/entity_save", "/orch/entity_run",
                            "/orch/entity_pause", "/orch/entity_forget", "/orch/entity_delete", "/orch/settings"):
            # starts paid agents, creates/merges/deletes git branches: this PC, or the owner / a member
            # through the tunnel (their own token; viewers never get here -- _authorize)
            if not (self._is_direct_loopback() or self._user["role"] in ("owner", "member")):
                self._serve(403, "application/json", b'{"ok":false,"error":"orchestration needs the owner or a member"}'); return
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                act = path_no_qs.rsplit("/", 1)[1]
                if act == "create":
                    reply = _orch_create(data)
                elif act == "agent_save":
                    reply = _orch_agent_save(data)
                elif act == "agent_delete":
                    reply = _orch_agent_delete(data)
                elif act == "baseline":
                    reply = _orch_baseline(data)
                elif act == "judge":
                    reply = _orch_judge(data)
                elif act == "settings":
                    reply = _orch_settings_save(data)
                elif act == "entity_save":
                    reply = _orch_entity_save(data)
                elif act.startswith("entity_"):
                    reply = _orch_entity_action(act[len("entity_"):], data)
                else:
                    reply = _orch_action(act, data)
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("orchestration", e)
                self._serve(500, "application/json", b'{"ok":false,"error":"failed"}')
        elif path_no_qs in ("/worktrees/launch", "/worktrees/merge", "/worktrees/discard"):
            # creates/merges/deletes git branches and folders: only from this PC itself
            if not self._is_direct_loopback() or _tunnel_status[0] in ("starting", "ready"):
                self._serve(403, "application/json", b'{"ok":false,"error":"worktrees can only be managed on this PC itself"}'); return
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                sessions = (_own_status_payload() or {}).get("sessions_list") or []
                if path_no_qs == "/worktrees/launch":
                    reply = _wt_launch(data)
                elif path_no_qs == "/worktrees/merge":
                    reply = _wt_merge(str(data.get("id") or ""), sessions)
                else:
                    reply = _wt_discard(str(data.get("id") or ""), sessions, force=data.get("force") is True)
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("worktrees", e)
                self._serve(500, "application/json", b'{"ok":false,"error":"failed"}')

        elif path_no_qs in ("/session_changes/discard", "/session_changes/undo",
                            "/session_changes/stage", "/session_changes/seen"):
            # writes to the user's repos: only from this PC itself
            if not self._is_direct_loopback() or _tunnel_status[0] in ("starting", "ready"):
                self._serve(403, "application/json", b'{"ok":false,"error":"local only"}'); return
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                sid = str(data.get("session_id") or "")
                with _CHANGE_LOCK:
                    if path_no_qs == "/session_changes/undo":
                        meta = next((m for m in _change_backups() if m.get("id") == data.get("id")), None)
                        reply = _change_undo(str(data.get("id") or ""), force=bool(data.get("force"))) \
                            if meta and meta.get("sid") == sid else {"ok": False, "error": "not a discard of this session"}
                    else:
                        edited = _session_edited(sid)
                        if path_no_qs == "/session_changes/stage":
                            own = [_change_own(edited, p) for p in (data.get("paths") or [])[:300]]
                            reply = _change_stage([p for p in own if p]) if own and all(own) else \
                                {"ok": False, "error": "not a file this session edited"}
                        else:
                            own = _change_own(edited, data.get("path"))
                            if not own:
                                reply = {"ok": False, "error": "not a file this session edited"}
                            elif path_no_qs == "/session_changes/seen":
                                reply = _change_seen_set(sid, own, bool(data.get("seen", True)))
                            else:
                                sig = data.get("sig")
                                reply = _change_discard(own, sid, expect_sig=str(sig) if sig is not None else None)
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("session_changes", e)
                self._serve(500, "application/json", b'{"ok":false,"error":"failed"}')

        elif path_no_qs in ("/memory_file", "/memory_file/undo", "/memory_archive"):
            # writes Claude Code's instructions and memory: only from this PC itself
            if not self._is_direct_loopback() or _tunnel_status[0] in ("starting", "ready"):
                self._serve(403, "application/json", b'{"ok":false,"error":"local only"}'); return
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                with _FIX_LOCK:
                    if path_no_qs == "/memory_file/undo":
                        reply = _memory_undo(data.get("path"))
                    elif path_no_qs == "/memory_archive":
                        reply = _memory_archive_apply(data.get("path"), data.get("mtime"), data.get("archive_mtime"))
                    else:
                        reply = _memory_save(data.get("path"), data.get("text"), data.get("mtime"))
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("memory_file", e)
                self._serve(500, "application/json", b'{"ok":false,"error":"failed"}')

        elif path_no_qs == "/focus_session":
            # a desktop action: only from this machine itself, never through
            # a tunnel or a Remote Machines peer
            if self.client_address[0] not in ("127.0.0.1", "::1") or _tunnel_status[0] in ("starting", "ready"):
                self._serve(403, "application/json", b'{"ok":false,"error":"local only"}'); return
            try:
                sid = str((json.loads(body or b"{}") or {}).get("session_id") or "")
                code, payload = _focus_session_terminal(sid)
                self._serve(code, "application/json", json.dumps(payload).encode())
            except Exception as e:
                _log_bg_error("focus_session", e)
                self._serve(500, "application/json", b'{"ok":false,"error":"failed"}')

        elif path_no_qs in ("/budget_caps", "/budget_caps/continue"):
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                if path_no_qs == "/budget_caps":
                    cfg = _save_budget_caps({k: data[k] for k in ("session_usd", "day_usd") if k in data})
                    _budget_tick()
                    reply = {"ok": True, **cfg}
                else:
                    with _budget_lock:
                        state = dict(_budget_state)
                    caps, ok = _budget_continue(data, _load_budget_caps(), state, datetime.now().strftime("%Y-%m-%d"))
                    if ok:
                        _save_budget_caps(caps)
                        _budget_tick()
                    reply = {"ok": ok} if ok else {"ok": False, "error": "not over a cap"}
            except Exception as e:
                _log_bg_error("budget_caps", e)
                reply = {"ok": False, "error": "bad request"}
            self._serve(200, "application/json", json.dumps(reply).encode())

        elif path_no_qs == "/danger_guard":
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                reply = {"ok": True, **_save_danger_guard(data)}
            except Exception as e:
                _log_bg_error("danger_guard", e)
                reply = {"ok": False, "error": "bad request"}
            self._serve(200, "application/json", json.dumps(reply).encode())

        elif path_no_qs == "/danger_guard/test":
            # judges a command only -- nothing is held, run or logged
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                reply = {"ok": True, **_danger_test(str(data.get("command") or "")[:5000], data.get("shell"), _load_danger_guard())}
            except Exception:
                reply = {"ok": False, "error": "bad request"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())

        elif path_no_qs in ("/prompts", "/prompts/delete", "/prompts/used"):
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                if path_no_qs == "/prompts":
                    reply = _prompts_upsert(data)
                elif path_no_qs == "/prompts/delete":
                    reply = {"ok": _prompts_delete(str(data.get("id") or ""))}
                else:
                    reply = {"ok": _prompts_used(str(data.get("id") or ""))}
            except Exception as e:
                _log_bg_error("prompts", e)
                reply = {"ok": False, "error": "bad request"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())

        elif path_no_qs in ("/push/subscribe", "/push/unsubscribe", "/push/settings", "/push/test"):
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                if path_no_qs == "/push/subscribe":
                    reply = _push_subscribe(data)
                elif path_no_qs == "/push/unsubscribe":
                    reply = {"ok": _push_unsubscribe(str(data.get("endpoint") or ""), self._user["id"])}
                elif path_no_qs == "/push/settings":
                    reply = {"ok": True, **_save_push_settings(data)}
                else:
                    n = _push_notify("✅ AOC push works", "You'll get alerts here while you're away from the PC.",
                                     tag="test", force=True)
                    reply = {"ok": n > 0, "devices": n} if n else {"ok": False, "error": "no device has push enabled"}
            except Exception as e:
                _log_bg_error("push", e)
                reply = {"ok": False, "error": "bad request"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())

        elif path_no_qs in ("/queue", "/queue/cancel"):
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                if path_no_qs == "/queue":
                    reply = _queue_add(data)
                else:
                    reply = {"ok": _queue_cancel(str(data.get("id") or ""))}
            except Exception as e:
                _log_bg_error("queue", e)
                reply = {"ok": False, "error": "bad request"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())

        elif path_no_qs == "/ab/estimate":
            try:
                reply = _ab_quote(json.loads(body or b"{}"))
            except Exception:
                reply = {"runs": []}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())

        elif path_no_qs in ("/ab/start", "/ab/keep", "/ab/discard"):
            # runs Claude Code headless and merges into your repo: only from this PC itself
            if not self._is_direct_loopback() or _tunnel_status[0] in ("starting", "ready"):
                self._serve(403, "application/json", b'{"ok":false,"error":"local only"}'); return
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                if path_no_qs == "/ab/start":
                    reply = _ab_start(data)
                elif path_no_qs == "/ab/keep":
                    reply = _ab_keep(str(data.get("id") or ""), str(data.get("label") or ""))
                else:
                    reply = _ab_discard(str(data.get("id") or ""))
            except Exception as e:
                _log_bg_error("ab", e)
                reply = {"ok": False, "error": "failed"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())

        elif path_no_qs == "/limit_stop":
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                reply = _limit_stop_action(data)
            except Exception as e:
                _log_bg_error("limit_stop", e)
                reply = {"ok": False, "error": "bad request"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())

        elif path_no_qs == "/launch":
            try:
                reply = _launch_session(json.loads(body or b"{}"))
            except Exception as e:
                _log_bg_error("launch", e)
                reply = {"ok": False, "error": "could not start Claude Code"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())

        elif path_no_qs == "/clients":
            try:
                reply = _save_clients(json.loads(body or b"{}"))
                self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())
            except Exception as e:
                _log_bg_error("save clients", e)
                self._serve(400, "application/json", b'{"ok":false,"error":"bad request"}')

        elif path_no_qs == "/recap/settings":
            try:
                reply = _save_recap_settings(json.loads(body or b"{}"))
                self._serve(200, "application/json", json.dumps(reply).encode())
            except Exception:
                self._serve(400, "application/json", b'{"ok":false,"error":"bad request"}')

        elif path_no_qs == "/hooks_health/repair":
            # rewrites ~/.claude/settings.json + hook scripts: a click on this
            # PC only, never via the tunnel (the only automatic run is
            # _auto_repair_maybe: once per update, outdated parts only)
            reply = _repair_now(self._is_direct_loopback())
            self._serve(200, "application/json", json.dumps(reply).encode(), no_cache=True)

        elif path_no_qs == "/hooks_health/auto_repair":
            if not self._is_direct_loopback():
                self._serve(403, "application/json", b'{"ok":false,"error":"this PC only"}')
            else:
                try:
                    reply = _auto_repair_set(json.loads(body or b"{}").get("enabled", True))
                    self._serve(200, "application/json", json.dumps(reply).encode(), no_cache=True)
                except Exception:
                    self._serve(400, "application/json", b'{"ok":false,"error":"bad request"}')

        elif path_no_qs == "/restart":
            if not self._is_direct_loopback() or AOC_PREVIEW:
                reply = {"ok": False, "error": "restart can only be started on this PC" if not AOC_PREVIEW
                         else "a preview instance doesn't restart AOC"}
            else:
                try:
                    data = json.loads(body or b"{}")
                except ValueError:
                    data = {}
                try:
                    reply = _restart_aoc(force=bool(isinstance(data, dict) and data.get("force")))
                except Exception as e:
                    reply = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
            self._serve(200, "application/json", json.dumps(reply).encode(), no_cache=True)

        elif path_no_qs == "/selfupdate/install":
            # Runs an installer on this PC -- only from the PC itself, never
            # through the tunnel / phone.
            if not self._is_direct_loopback():
                reply = {"ok": False, "error": "updates can only be started on this PC"}
            else:
                reply = _update_start(_read_installed_version(AOC_VERSION_FILE), _latest_release[0])
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)

        elif path_no_qs.startswith("/release/"):
            # publishes a GitHub release / pushes the public mirror with this
            # PC's gh + git credentials: from the PC itself only, dev checkout only
            if not self._is_direct_loopback():
                reply = {"ok": False, "error": "releases can only be published on this PC"}
            elif not _release_tools_available():
                reply = {"ok": False, "error": "only in a git checkout of AOC, not an installed copy"}
            else:
                try:
                    data = json.loads(body or b"{}")
                except ValueError:
                    data = {}
                parts = path_no_qs.split("/")
                reply = (_release_action(parts[2], parts[3], data) if len(parts) == 4
                         else {"ok": False, "error": "unknown release action"})
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)

        elif path_no_qs == "/whats_new/dismiss":
            try:
                _whats_new_dismiss()
                reply = {"ok": True}
            except Exception as e:
                reply = {"ok": False, "error": str(e)[:200]}
            self._serve(200, "application/json", json.dumps(reply).encode(), no_cache=True)

        elif path_no_qs == "/ask":
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
            except Exception:
                self._serve(400, "application/json", b'{"ok":false,"error":"bad request"}')
                return
            reply = _ask(data.get("q"), force=bool(data.get("force")), prev=data.get("prev") or "",
                         model=data.get("model") or "haiku")
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode(), no_cache=True)

        elif path_no_qs in ("/permission/request", "/permission/decide", "/permission/settings"):
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                if path_no_qs == "/permission/request":
                    try:
                        _perm_log_add(data, time.time())   # every prompt shown (History -> PERMISSIONS)
                    except Exception as e:
                        _log_bg_error("permission log", e)
                    reply = _perm_open(data, _load_remote_approve(), time.time())
                elif path_no_qs == "/permission/decide":
                    ok = _perm_decide(str(data.get("id") or ""), data.get("decision"), data.get("message"))
                    reply = {"ok": ok} if ok else {"ok": False, "error": "no longer waiting"}
                else:
                    reply = _save_remote_approve(data)
                self._serve(200, "application/json", json.dumps(reply).encode())
            except Exception as e:
                _log_bg_error("permission", e)
                self._serve(400, "application/json", b'{"ok":false,"error":"bad request"}')

        elif path_no_qs in ("/reply/request", "/reply/send"):
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                if path_no_qs == "/reply/request":
                    reply = _reply_open(data, _load_remote_approve(), time.time())
                else:
                    ok, err = _reply_send(str(data.get("id") or ""), data.get("action"), data.get("text"))
                    reply = {"ok": True} if ok else {"ok": False, "error": err}
                self._serve(200, "application/json", json.dumps(reply).encode())
            except Exception as e:
                _log_bg_error("reply", e)
                self._serve(400, "application/json", b'{"ok":false,"error":"bad request"}')

        elif path_no_qs == "/remove":
            try:
                data = json.loads(body)
                aid = data.get("id")
                with _status_lock:
                    status = _load_status()
                    status["agents"] = [a for a in status.get("agents", []) if a["id"] != aid]
                    if not status["agents"]:
                        status["session_active"] = False
                    _save_status(status)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/clear_done":
            try:
                with _status_lock:
                    status = _load_status()
                    status["agents"] = [a for a in status.get("agents", []) if a.get("status") not in ("done",)]
                    if not any(a for a in status["agents"] if not str(a.get("id","")).startswith("hook_")):
                        status["session_active"] = False
                    _save_status(status)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/notes":
            try:
                data = json.loads(body)
                note = str(data.get("note", ""))[:2000]
                with _status_lock:
                    st = _load_status()
                    st["session_note"] = note
                    _save_status(st)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/session_note":
            # Per-session note, distinct from the single global "session_note"
            # above -- field name is "note" (not "session_note") specifically
            # to avoid confusion between the two.
            try:
                data = json.loads(body)
                session_id = data.get("session_id", "")
                note = data.get("note", "")
                with _status_lock:
                    st = _load_status()
                    if _set_session_note(st, session_id, note):
                        _save_status(st)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/history_tags":
            # Edits an already-saved History row directly (_db_set_session_tags),
            # not the live status dict -- see that function's own docstring
            # for why this differs from /session_note.
            try:
                data = json.loads(body)
                session_id = str(data.get("session_id", ""))
                ok = _db_set_session_tags(session_id, data.get("tags") or [])
                self._serve(200 if ok else 404, "application/json",
                            json.dumps({"ok": ok}).encode())
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/webhook_test":
            try:
                import urllib.request as _ur
                data = json.loads(body)
                url = str(data.get("url", ""))[:500]
                payload = json.dumps({"source": "AOC", "event": "test", "message": "AOC webhook test"}).encode()
                req = _ur.Request(url, data=payload, method="POST",
                                  headers={"Content-Type": "application/json"})
                _ur.urlopen(req, timeout=5)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(200, "application/json", json.dumps({"ok": False, "error": str(e)}).encode())

        elif path_no_qs == "/webhook_fire":
            try:
                data = json.loads(body)
                url = str(data.get("url", ""))[:500]
                _fire_webhook(url, data)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/webhook_settings":
            try:
                data = json.loads(body)
                url = str(data.get("url", ""))[:500]
                if url and not _is_pro():
                    self._serve(403, "application/json",
                                json.dumps({"ok": False, "error": "Webhook delivery is a Pro feature. Settings -> LICENSE to upgrade."}).encode())
                    return
                events = data.get("events") or {}
                # the Settings form doesn't know the split toggles -- keep them
                old_ev = _webhook_settings.get("events") or {}
                events = {**{k: old_ev[k] for k in ("session_loop", "budget_cap") if k in old_ev}, **events}
                _save_webhook_settings({
                    "url": url,
                    "events": _sanitize_webhook_events(events),
                })
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/notify_settings":
            self._post_notify_settings(body)

        elif path_no_qs in ("/context_save", "/context_save/send", "/context_save/cancel"):
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                if path_no_qs == "/context_save":
                    reply = {"ok": True, **_ctxsave_save_settings(data)}
                elif path_no_qs == "/context_save/send":
                    reply = _ctxsave_request(data.get("session_id"))
                else:
                    reply = {"ok": _ctxsave_cancel(data.get("session_id"))}
            except Exception as e:
                _log_bg_error("context save", e)
                reply = {"ok": False, "error": "bad request"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())

        elif path_no_qs in ("/notify_center/set", "/notify_center/test", "/notify_center/clear"):
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                if path_no_qs == "/notify_center/set":
                    kind, channel = str(data.get("kind") or ""), str(data.get("channel") or "")
                    if channel == "webhook" and data.get("on") and not _is_pro():
                        reply = {"ok": False, "error": "Webhook delivery is a Pro feature. Settings -> LICENSE to upgrade."}
                    else:
                        ncfg, wcfg = _notify_set(kind, channel, data.get("on"), _notify_settings, _webhook_settings)
                        if channel == "webhook":
                            _save_webhook_settings(wcfg)
                        else:
                            _save_notify_settings(ncfg)
                        reply = {"ok": True, **_notify_center_payload()}
                elif path_no_qs == "/notify_center/test":
                    reply = _notify_test(str(data.get("kind") or ""), channel=data.get("channel") or None)
                else:
                    _notify_log_clear()
                    reply = {"ok": True}
            except ValueError as e:
                reply = {"ok": False, "error": str(e)}
            except Exception as e:
                _log_bg_error("notify_center", e)
                reply = {"ok": False, "error": "bad request"}
            self._serve(200, "application/json", json.dumps(reply, ensure_ascii=False).encode())

        elif path_no_qs == "/snitch_ping_now":
            try:
                data = json.loads(body)
                url = str(data.get("url", "") or _notify_settings.get("snitch_url", ""))[:500]
                if not url:
                    self._serve(400, "application/json", b'{"ok":false,"error":"no URL configured"}')
                else:
                    _snitch_ping(url)
                    self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/restore_backup":
            try:
                data = json.loads(body)
                result = _restore_backup(str(data.get("filename", "")))
                self._serve(200 if result.get("ok") else 400, "application/json",
                            json.dumps(result, ensure_ascii=False).encode())
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/tunnel/start":
            self._post_tunnel_start(body)

        elif path_no_qs == "/tunnel/stop":
            _stop_tunnel()
            self._serve(200, "application/json", b'{"ok":true}')

        elif path_no_qs == "/tunnel/ngrok_authtoken":
            self._post_tunnel_ngrok_authtoken(body)

        elif path_no_qs == "/reset":
            self._post_reset()

        elif path_no_qs == "/kill_session":
            self._post_kill_session(body)

        elif path_no_qs == "/auth/token":
            try:
                new_token = _secrets.token_urlsafe(24)
                _save_auth_token(new_token)
                self._serve(200, "application/json", json.dumps(_auth_info(), ensure_ascii=False).encode())
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/license":
            try:
                data = json.loads(body)
                key = (data.get("key") or "").strip()
                payload = _verify_license(key) if key else None
                if not payload:
                    self._serve(400, "application/json", json.dumps({"ok": False, "error": "Invalid license key"}).encode())
                else:
                    _save_license(key)
                    self._serve(200, "application/json", json.dumps({"ok": True, **_license_info()}, ensure_ascii=False).encode())
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        else:
            self._serve(404, "text/plain", b"Not found")

    def _serve(self, code, ct, body, no_cache=False):
        self.send_response(code)
        if ct == "application/json":
            ct = "application/json; charset=utf-8"
        self.send_header("Content-Type", ct)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        if no_cache:
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)
