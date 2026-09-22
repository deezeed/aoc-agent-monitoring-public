"""Tests _resolve_autosave_id, extracted straight from monitor.py. This is
the restart-survival decision logic for _autosave_worker's sid: before
this fix, _autosave_id was in-memory only, so a watchdog restart (or a
manual one) mid-session forgot it and the next autosave tick minted a
brand-new sid for the same still-running live session -- splitting one
session's agents across two separate history.db rows instead of
continuing the one that was already there. Confirmed live: the same
agent_id showed up under two different auto_YYYYMMDD_HHMMSS session ids
~11 minutes apart, exactly matching a watchdog restart logged at that
same timestamp."""
import sys, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_resolve_autosave_id"], {"datetime": datetime})
_resolve_autosave_id = ns["_resolve_autosave_id"]

c = Checker()

# 1. No live agents -> always None, regardless of any in-memory or persisted id
c.check("no agents, no current id, no persisted id -> None",
        _resolve_autosave_id(None, [], None) is None)
c.check("no agents clears an existing in-memory id (session actually ended)",
        _resolve_autosave_id("auto_20260101_100000", [], None) is None)
c.check("no agents ignores a persisted id too",
        _resolve_autosave_id(None, [], "auto_20260101_100000") is None)

# 2. Already have an in-memory id -> keep it (the normal steady-state case,
# every tick after the first one for a given session)
c.check("existing in-memory id is kept as-is when agents are present",
        _resolve_autosave_id("auto_20260101_100000", [{"id": "a1"}], None) == "auto_20260101_100000")
c.check("existing in-memory id wins even if a different id is persisted",
        _resolve_autosave_id("auto_20260101_100000", [{"id": "a1"}], "auto_20260101_090000") == "auto_20260101_100000")

# 3. THE FIX: no in-memory id, agents present, a persisted id exists -> resume it
# (this is the exact restart scenario -- a prior process's sid for a
# session that's still actually live gets reattached instead of forked)
c.check("no in-memory id but agents present + a persisted id -> resumes the persisted id",
        _resolve_autosave_id(None, [{"id": "a1"}], "auto_20260101_090000") == "auto_20260101_090000")

# 4. No in-memory id, agents present, nothing persisted -> genuinely the
# first save of a new session, mint a fresh one (not testing the exact
# timestamp value, just that it's a fresh auto_-prefixed id, not None and
# not confused with a persisted value that doesn't exist)
fresh = _resolve_autosave_id(None, [{"id": "a1"}], None)
c.check("no in-memory id, no persisted id -> mints a fresh auto_-prefixed id",
        fresh is not None and fresh.startswith("auto_"))

c.finish()
