"""Cache-expiry alert (monitor.py): _recache_cost prices the next message cold
(1.25x input for a 5-minute entry, 2x for a 1-hour one) vs warm (cache read),
and _cache_expiry_alerts picks open sessions waiting on you whose cache
expires within the lead time and would cost enough more to re-cache --
once per (session, expiry)."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ns = exec_functions(["_MODEL_PRICING", "_CACHE_WRITE_1H_MULT", "_model_pricing", "_recache_cost",
                     "_CACHE_ALERT_LEAD_S", "_CACHE_ALERT_MIN_USD", "_cache_expiry_alerts"], {})
cost, alerts = ns["_recache_cost"], ns["_cache_expiry_alerts"]
near = lambda a, b: abs(a - b) < 1e-9

cold, warm = cost(100_000, "1h", "claude-opus-5-5")
c.check("1h on Opus 5.5 ($4 in, $0.20 read): 2x input to write", near(cold, 0.8) and near(warm, 0.02))
cold5, _ = cost(100_000, "5m", "claude-opus-5-5")
c.check("5m: the 1.25x write price", near(cold5, 0.5))
c.check("unknown model / no tokens don't crash", cost(None, "1h", None) == (0.0, 0.0) and cost(1000, "1h", "x")[0] > 0)

NOW = 1_800_000_000
def sess(sid="s1", left=300, ttl="1h", tokens=125_000, **kw):
    return {"id": sid, "session_active": True, "waiting_on_you": True, "model": "claude-opus-5-5",
            "display_name": "AOC work", "project": "aoc",
            "context": {"cache_expires_at": NOW + left, "cache_ttl": ttl, "recache_tokens": tokens, "tokens": tokens}, **kw}

out = alerts([sess()], set(), NOW)
c.check("one alert, keyed by session + expiry", len(out) == 1 and out[0][0] == ("s1", NOW + 300))
c.check("title says when, body says both prices",
        "5 min" in out[0][1] and "AOC work" in out[0][1] and "$0.03" in out[0][2] and "$1.00" in out[0][2] and "125k" in out[0][2])
c.check("already alerted for this expiry -> nothing", alerts([sess()], {("s1", NOW + 300)}, NOW) == [])
c.check("a new expiry (you replied) re-arms", len(alerts([sess(left=200)], {("s1", NOW + 300)}, NOW)) == 1)
c.check("1h cache: not before 10 min left", alerts([sess(left=601)], set(), NOW) == [] and len(alerts([sess(left=600)], set(), NOW)) == 1)
c.check("5m cache: only in the last 2 min", alerts([sess(left=121, ttl="5m")], set(), NOW) == []
        and len(alerts([sess(left=90, ttl="5m")], set(), NOW)) == 1)
c.check("already expired -> no alert", alerts([sess(left=0)], set(), NOW) == [])
c.check("not waiting / closed session -> no alert",
        alerts([sess(waiting_on_you=False), sess(sid="s2", session_active=False)], set(), NOW) == [])
c.check("small context not worth it (< $0.25 extra)", alerts([sess(tokens=20_000)], set(), NOW) == [])
c.check("no context / unknown ttl -> skipped",
        alerts([{**sess(), "context": None}, sess(sid="s3", ttl="2h")], set(), NOW) == [])
c.check("falls back to context tokens when recache_tokens is missing",
        len(alerts([{**sess(), "context": {**sess()["context"], "recache_tokens": None}}], set(), NOW)) == 1)
c.check("under a minute left still says 1 min", "1 min" in alerts([sess(left=30)], set(), NOW)[0][1])

c.finish()
