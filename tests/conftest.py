# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Every test leaves the process the way it found it.

Twenty-odd test files build the service on a temporary database the same way:
point `VISTEST_ROOT` at a `tmp_path`, then `importlib.reload(vistest.api.main)`
so the module-level `db` is rebuilt against it. `monkeypatch` puts the
environment variable back afterwards — but nothing puts the module back.
`importlib.reload` rebuilds a module **in place**, so after such a test
`vistest.api.main.db` still points at that test's temporary database, for the
rest of the session.

That is invisible until something reads the service database lazily. It does:
`vistest.api.settings._guard` does `from .main import db` inside the function
and asks whether any users exist. A test that created an administrator on its
temporary database leaves `any_users(db)` true, and the variable-editor tests —
which are about IP addresses and have no users of their own — start failing
with «Sign in required», in a file that never touched authentication.

None of this shows up in the default run, because the default run happens to
order the files so the leak lands after the tests it would break. Change the
order — `-p xdist` splitting files across workers, `--reverse`, running one
file on its own — and it surfaces as a mystery failure somewhere else. That is
the worst shape a bug can have: real, order-dependent, and blamed on whatever
was being worked on at the time.

So the restoration is here, once, rather than in twenty fixtures:

* a service module that existed before the test gets its namespace put back;
* a service module that the test imported for the first time has no earlier
  state to be put back to, so it is reloaded once — by then `monkeypatch` has
  already restored the environment, so it rebuilds against the real root. That
  happens at most once per module per session: from the next test on, the
  module is one that existed before;
* the database connection cache is emptied either way — the connections it
  holds point at temporary files that are about to be deleted.

Every fixture that reloads is function-scoped (checked), so nothing depends on
a reload surviving into the next test.
"""

from __future__ import annotations

import importlib
import sys
import threading

import pytest

#  Modules the test suite rebuilds with `importlib.reload`. Each holds process
#  state — an open database, a built router, a licence read at import — that a
#  reload rebinds and never restores.
RELOADED_MODULES = (
    "vistest.api.main",
    "vistest.api.check",
    "vistest.api.baselines",
    "vistest.licensing",
)

#  Connection cache: thread-local sqlite handles. After a test its connections
#  point at a `tmp_path` database that pytest is about to delete, so it is
#  emptied rather than restored.
_DB_MODULE = "vistest.api.db"


@pytest.fixture(autouse=True)
def _service_modules_are_restored():
    """Snapshot the reloadable service modules, and put them back afterwards."""
    before: dict[str, dict | None] = {}
    for name in RELOADED_MODULES:
        module = sys.modules.get(name)
        before[name] = dict(vars(module)) if module is not None else None

    try:
        yield
    finally:
        for name, namespace in before.items():
            module = sys.modules.get(name)
            if module is None:
                continue
            if namespace is None:
                #  The test imported it for the first time, so there is no
                #  earlier namespace to put back — but leaving it as it is
                #  would leave it bound to the test's temporary root. Rebuild
                #  it instead. Dropping it from `sys.modules` would be simpler
                #  and is wrong: a test module that holds the module object
                #  from its own import line then fails in `importlib.reload`
                #  with «not in sys.modules».
                try:
                    importlib.reload(module)
                except Exception:       # pragma: no cover - best effort
                    pass
                continue
            module.__dict__.clear()
            module.__dict__.update(namespace)

        dbmod = sys.modules.get(_DB_MODULE)
        if dbmod is not None:
            dbmod._local = threading.local()
