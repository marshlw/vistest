# Security

VisTest is self-hosted. It runs inside your perimeter, on your machine, with
your data — which means the security of an installation is a shared job: some
of it is done in this code, and some of it can only be done by whoever deploys
it. This file says plainly which is which.

## Reporting a vulnerability

<!-- Впишите сюда свой адрес или ссылку на приватный advisory GitHub. -->
Write to **<security contact — fill this in>** with a description and, if you
have one, a way to reproduce it. Please do not open a public issue for
something exploitable.

You will get a first answer within 5 working days. If the report is confirmed,
we will agree a disclosure date with you — normally after a fix is released. We
do not run a bug bounty, and we will credit you in the release notes unless you
would rather we did not.

## Supported versions

Only the latest release gets fixes. VisTest is a young product; backporting to
older versions would mean promising something we cannot keep.

## The threat model, said out loud

**An administrator can run code on the machine where the service runs.** This
is not a defect, it is what the product does: connecting a project starts its
test suite, and a test suite is arbitrary code. `runner: "command"` runs an
arbitrary argv; the mouse recorder starts a browser; the variable editor writes
`secrets.env`, which those processes then read.

Two things follow, and both matter more than any hardening below:

1. **The `admin` role is equivalent to shell access.** Give it to the people
   you would give an SSH key to, and to no one else.
2. Those actions are limited to the service machine by default
   (`VISTEST_PROJECTS_UI`, `VISTEST_TESTS_UI`, `VISTEST_SECRETS_UI`,
   `VISTEST_RECORD_UI` — all `local`). Setting any of them to `all` moves a
   shell to whoever holds an admin session. It is a legitimate choice for a
   team behind a VPN; it is a deliberate one.

**A CI token can write history, not read the disk.** A token issued for a
project can push runs and upload artifacts for that project. It cannot read
files outside the data volume, and it cannot approve baselines.

**Everyone who can reach the port can read, until a user exists.** On a fresh
installation there is no administrator yet, so the first person to open the
interface becomes one. Inside a perimeter that is fine. On an open network it
is not — set `VISTEST_SETUP_TOKEN` before starting the service, and the setup
form will ask for it.

**An external identity never takes over a local account.** With directory
sign-in (LDAP/AD, or any `AuthProvider`) on, two accounts can share a login: the
one created here and the one the directory knows. They stay two. A local
account (`source = local`) is opened by its local password only: when that
password fails, the directory is not asked — a password typed for a local
account does not travel to another system — and the attempt is one ordinary
`login.failed` row whose details say `reason: local_account`,
`directory: not_asked`; the account row is left as it was. No sign-in, from any
source, writes an account's `source`, `active` or `status`: those change only
when an administrator changes them. So an account switched off here stays off
whatever the directory answers — that attempt, the directory saying yes to an
account disabled here, is the one recorded as `auth.external_refused` — and
whoever controls an entry called `admin` in the directory is not the
administrator of this installation. The reverse holds too: an account that came
from the directory cannot be opened with a local password.

**The price of that, in response time.** With directory sign-in turned on, a
sign-in under a login that has a local account is answered without a trip to
the directory; every other login — a directory account, or one that exists nowhere
— waits for the directory's answer. So the time a failed sign-in takes tells an
outsider which logins have a local account here. This is deliberate: the
alternative is to send a password typed for a local account to another system,
and to let a directory entry answer for a local login. What bounds the guessing
that follows is the sign-in throttle, counted from the audit log over a
15-minute window: 8 failures for one login from one address, 40 for one login
from all addresses, 30 from one address across all logins; each count starts
afresh after a successful sign-in. With no directory to ask, the response
time is flat: a login that does not exist is checked against a dummy hash of the same
cost, so timing does not tell existing logins from missing ones. Either way,
do not give the local administrator an obvious name — `admin`, `root`,
`administrator` are the first ones guessed.

**Roles from the directory.** A directory account's role is recalculated from
group membership at every sign-in, down as well as up, and each change is
audited as `user.role` with the provider as its author. A role an administrator
set by hand in VisTest is pinned: no sign-in moves it until it is handed back
with `{"role": "directory"}`. The last active administrator is never demoted by
a sign-in (`auth.role_kept`), so a broken group mapping cannot leave the
installation without anyone able to fix it.

**Disabling someone in the directory stops new sign-ins, not open sessions.**
The directory is asked only when someone signs in; nothing polls it. A session
opened before the account was disabled in the directory keeps working until it
expires, which is 14 days after it was opened. To cut a person off at once,
disable the account in VisTest as well (**Settings → Team**): that deletes every
session the account has. The same holds for a role lowered in the directory —
it takes effect at the next sign-in.

## What VisTest does

- Passwords: PBKDF2-HMAC-SHA256, 480 000 iterations, per-password salt, the
  iteration count stored inside the hash so it can be raised without breaking
  old passwords.
- Sessions: server-side, so signing out and revoking access actually work.
  Changing a password closes every other session of that user.
- CI tokens: only a SHA-256 hash is stored, alongside a short prefix for
  lookup and display. A token is bound to a project and can be revoked on its
  own.
- Sign-in throttling: counted from the audit log, so a restart does not clear
  it. The tight limit is on the pair «login + address» — the loose one on a
  login alone, deliberately, so that knowing someone's login is not enough to
  lock them out of their own service.
- Access requests: throttled per address, and the pending queue has a ceiling.
- Every route requires at least the `viewer` role, except a named list of
  public ones (sign-in, health, the setup flow, the stabilisation script).
- Artifacts are served only from the data volume, only with an allow-listed
  extension, never from the subtrees holding the database and secrets, always
  with `nosniff` and a sandboxing CSP.
- The interface is served with a CSP, `X-Frame-Options: DENY` and
  `Referrer-Policy: same-origin`.
- A state-changing request carrying an `Origin` of another site is refused.
- Uploads have ceilings: image size, number of frames, decoded pixels, archive
  size, unpacked size and entry count.
- The audit log records who approved, rejected, ignored, ingested and signed
  in, and is kept for a year by default.

## What VisTest does not do, and you must

**TLS.** VisTest speaks plain HTTP. Put nginx, Caddy or Traefik in front of it.
Without that, passwords and session cookies cross the network in the clear.

**Trusting a proxy.** Name your reverse proxy in `VISTEST_TRUSTED_PROXIES`.
Unset, VisTest ignores `X-Forwarded-*` entirely — which is safe but means the
audit log records the proxy's address, and the session cookie only gets
`Secure` over real HTTPS (or with `VISTEST_COOKIE_SECURE=1`).

Do **not** start the service with uvicorn's `--proxy-headers
--forwarded-allow-ips *`. In that mode the client address is taken from the
first element of `X-Forwarded-For`, which the caller writes themselves — and
that address is what «only from the service machine» is checked against. The
shipped images do not use it.

**Backups.** `python -m vistest.cli backup` packs the baselines and the
database. Baselines are accumulated human decisions; nothing recreates them.

**The data volume is sensitive.** `.vistest/` holds password hashes, live
session tokens, `secrets.env`, and baselines that contain the content of the
pages you capture. If you test an internal system, treat that directory as data
of the same sensitivity — including in backups, and including where you push
it.

**Network egress.** VisTest reaches out in exactly one place: the notification
webhook, to the address an administrator configured. Nothing else phones home.
There is no telemetry.

## Hardening checklist for a shared installation

```bash
VISTEST_SETUP_TOKEN=…            # before the first start, on an open network
VISTEST_TRUSTED_PROXIES=10.0.0.5 # the address of your reverse proxy
VISTEST_COOKIE_SECURE=1          # TLS is terminated in front
VISTEST_SECRETS_UI=off           # unless the team really edits variables in the UI
VISTEST_PROJECTS_UI=off          # connect projects from the service machine
VISTEST_TESTS_UI=off
VISTEST_RECORD_UI=off
VISTEST_DOCS=                    # leave unset: the API schema stays closed
VISTEST_METRICS_TOKEN=…          # if Prometheus scrapes /metrics
```

Plus: run behind TLS, give `admin` to as few people as possible, take backups,
and keep the data volume off any public repository.
