# AOC License

This repository ships two tiers of one codebase. Which terms apply to you
depends on whether you have activated a Pro license key (Settings ->
LICENSE) -- see monitor.py's own "License (Free/Pro)" section for how
that's verified.

*This is a plain-language draft, not legal advice -- have it reviewed by
a lawyer before relying on it for real commercial sales.*

## 1. Free tier

Free of charge, for any use, including commercial use inside your own
organization. You may:

- Run it on any number of your own machines.
- Modify it for your own use.
- Redistribute unmodified copies of this repository, including this
  license file.

You may **not**:

- Remove or alter the license-verification code (the `_is_pro`/
  `_verify_license` functions and their call sites in monitor.py) and
  then distribute the result as if it were an unmodified copy.
- Sell, sublicense, or otherwise redistribute a Pro license key you did
  not purchase, or represent an unlicensed copy as licensed.

## 2. Pro tier

Activating a valid Pro license key unlocks additional features (Remote
Machines, the remote-access tunnel, webhook delivery, per-project
monthly budgets, CSV exports) in the same monitor.py file. A Pro license
key is:

- **Perpetual** -- once issued, it does not expire, and is not tied to a
  subscription unless explicitly sold as one.
- **Single-purchaser** -- licensed to the email address it was issued to.
  You may use it on any number of machines *you personally* use, but you
  may not share, resell, or publish a working key for others to use.
- **Non-transferable** without the seller's written consent.
- Sold **as-is**, with the disclaimer of warranty in Section 4 below.
  Pricing and any bundled support/update terms are as stated on the page
  where you purchased it, not restated here.

A key that is forged, shared publicly, or used outside these terms may
be revoked; monitor.py's Ed25519 verification (see its own comments for
why the scheme is asymmetric) means a revoked or never-issued key cannot
be worked around by guessing -- only by patching out the check entirely,
which Section 1 above already prohibits doing and then redistributing.

## 3. saas-backend/

Not included in the public release of this repository. If you have
separately been granted access to it (e.g. as a contractor or
acquirer), its terms are whatever separate agreement granted that
access, not this file.

## 4. Disclaimer of warranty

Provided "as is", without warranty of any kind, express or implied,
including but not limited to warranties of merchantability, fitness for
a particular purpose, and non-infringement. In no event shall the
authors be liable for any claim, damages, or other liability arising
from use of this software -- including, without limitation, Force Stop
(monitor.py's `/kill_session`, which terminates a real OS process) or
Remote Machines' remote-token-authenticated access between your own
machines. Use at your own risk.
