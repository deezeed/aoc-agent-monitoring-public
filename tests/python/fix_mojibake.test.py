"""Tests _fix_mojibake, extracted straight from monitor.py. Covers both
known corruption patterns (UTF-8 bytes misdecoded as latin-1, and as
cp1250/Central European -- this machine's system codepage, added as
defensive coverage alongside the original latin-1 case) plus the
"leave already-correct text alone" case."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_fix_mojibake"])
_fix_mojibake = ns["_fix_mojibake"]

c = Checker()

correct = "Pokračování vývoje Phantom AI"

# 1. latin-1 corruption (the original case this function was written for)
latin1_corrupted = correct.encode("utf-8").decode("latin-1")
c.check("latin-1 corruption repaired", _fix_mojibake(latin1_corrupted) == correct)

# 2. cp1250 corruption (confirmed live 2026-07-18)
cp1250_corrupted = correct.encode("utf-8").decode("cp1250")
c.check("cp1250 corruption repaired", _fix_mojibake(cp1250_corrupted) == correct)

# 3. already-correct text is left alone (not mangled by a spurious "fix")
c.check("correct text is unchanged", _fix_mojibake(correct) == correct)
c.check("plain ASCII text is unchanged", _fix_mojibake("AOC-Agent Monitoring") == "AOC-Agent Monitoring")

# 4. empty string doesn't crash
c.check("empty string handled", _fix_mojibake("") == "")

c.finish()
