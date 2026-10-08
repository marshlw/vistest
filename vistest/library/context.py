# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What one process knows about the run it is part of.

`expect_screenshot` is a plain function, deliberately: it has to be callable
from a test, from a fixture, from a helper three layers down, and from a script
that has nothing to do with pytest. So the things a run needs and a call site
should not have to repeat — where the baselines are, which platform this is,
whether we are updating, where the report goes — live here, installed once by
the pytest plugin.

**Without the plugin it still works.** A context is built from the environment
and the working directory, and a warning says the flags are not available. That
is the rule of this stage applied to its own machinery: an absent optional part
degrades quietly, a wrong setting does not. Which is why every environment
variable read here is read loudly.

**Under `pytest -n` there is one of these per worker**, each writing its own
files under names nobody else uses. Nothing in here is shared between
processes, there is no index to lock, and the report is assembled afterwards
from what the workers left behind.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..storage.base import SnapshotKey
from ..storage.file import FileStore, default_root
from .errors import VisTestWarning

if TYPE_CHECKING:  # pragma: no cover
    from ..config import VisTestConfig
    from ..storage.base import SnapshotStore
    from .targets import Capture

__all__ = ["LibraryContext", "current", "install", "uninstall"]

ENV_BASELINES = "VISTEST_BASELINES"
ENV_PLATFORM = "VISTEST_PLATFORM"
ENV_REPORT = "VISTEST_REPORT"
ENV_UPDATE = "VISTEST_UPDATE_BASELINES"

#: What `--vistest-update` rewrites. `missing` — only baselines that do not
#: exist yet; `changed` — those, and the ones whose check failed; `all` —
#: every baseline whose bytes differ from the new picture, passing or not.
UPDATE_MODES = ("missing", "changed", "all")
#: A bare `--vistest-update`, and `VISTEST_UPDATE_BASELINES=1`.
DEFAULT_UPDATE_MODE = "changed"

#: Where a check runs, for the words that tell how to accept it (review v1,
#: 4.11): under the pytest plugin, from `vistest check`, or from a script —
#: the library alone, the environment variables its only switches.
ORIGINS = ("pytest", "cli", "script")


def _origin(origin: str | None) -> str:
    """A context built by hand says nothing: under pytest it stands in for the
    plugin's (a test suite's own fixture), anywhere else it is a script's."""
    if origin:
        return origin
    import os

    return "pytest" if os.environ.get("PYTEST_CURRENT_TEST") else "script"

#: What the library reads of vistest.yaml: the keys its check uses, and the
#: sections it hands over whole (`plugins:` to the plugin runtime, `ai:` to the
#: AI pipeline). The engine reads the `diff:` keys listed. Everything else in
#: the file — `capture.full_page`, `matrix:`, `render:`, `service:`, the v1
#: tuning of `diff:` — is the server's, and changes nothing in a library run;
#: `unread_keys` names them. `tests/test_settings_review_b.py` holds this list
#: to the code that reads it.
LIBRARY_READS = frozenset({
    "capture.stable_timeout_ms", "capture.ready_timeout_ms", "capture.quiet_ms",
    "capture.track_requests", "capture.ignore_requests", "capture.retry_on_fail",
    "capture.keep_pointer", "capture.blur_focus", "capture.match_baseline_scroll",
    "capture.device_scale_factor",
    "diff.fail_severity", "diff.max_changed_area_pct", "diff.max_pixels",
    "diff.size_tolerance_px", "diff.ignore_kinds", "diff.fail_on_size_change",
    "diff.above_fold_px", "diff.above_fold_weight",
    "paths.root",
    "plugins", "ai",
})


def unread_keys(config) -> list[str]:
    """The keys `config`'s vistest.yaml sets and the library does not read."""
    return [k for k in getattr(config, "written", ())
            if k not in LIBRARY_READS and k.split(".", 1)[0] not in LIBRARY_READS]


def unread_text(config) -> str:
    """The one warning about them, or '' when every key is read."""
    keys = unread_keys(config)
    if not keys:
        return ""
    return (f"vistest: {config.source}: the library does not read "
            f"{', '.join(keys)} — they change nothing in this run (they are the "
            "server's settings)")


def update_mode(value: Any) -> str | None:
    """`update=` in any of the forms it arrives in -> a mode, or None.

    `True` is what the flag used to be and what the environment variable still
    is: it means the default mode. Anything that is not a mode is refused by
    name — a typo here decides what is written into somebody's repository.
    """
    if value is None or value is False or value == "":
        return None
    if value is True:
        return DEFAULT_UPDATE_MODE
    if isinstance(value, str) and value in UPDATE_MODES:
        return value
    raise ValueError(f"--vistest-update: {value!r} is not one of "
                     f"{', '.join(UPDATE_MODES)}")


@dataclass
class LibraryContext:
    """One process's answer to «where does all of this go»."""

    root: Path = field(default_factory=Path.cwd)
    baselines: Path | None = None
    artifacts_root: Path | None = None
    report: Path | None = None
    platform_override: str = ""
    #  None/False — compare only; True — the default mode; or one of
    #  UPDATE_MODES. Read through `update_mode`, which validates it.
    update: bool | str | None = False
    config_path: str | None = None
    #  `--vistest-fail-on`, folded over `plugins.fail_on` from the config.
    fail_on: str | None = None
    #  One of ORIGINS, or None: see `_origin`. `command` is the command line
    #  of `vistest check`.
    origin: str | None = None
    command: str = ""
    #  Filled on first use; both are process-wide and neither is cheap.
    _config: Any = None
    _store: Any = None

    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.baselines = Path(self.baselines) if self.baselines is not None \
            else default_root(self.root)
        if self.artifacts_root is None:
            self.artifacts_root = self.root / self.config.paths.root
        self.artifacts_root = Path(self.artifacts_root)
        if self.report is None:
            self.report = self.artifacts_root / "report" / "index.html"
        self.report = Path(self.report)

    # ------------------------------------------------------------------ #
    @property
    def update_mode(self) -> str | None:
        return update_mode(self.update)

    @property
    def accept_with(self) -> str:
        """How to accept the picture, where this check runs.

        `vistest check` without a baseline used to advise pytest; a script, a
        flag it does not have.
        """
        origin = _origin(self.origin)
        if origin == "pytest":
            return "pytest --vistest-update"
        if origin == "cli":
            return f"{self.command or 'vistest check NAME ACTUAL'} --update"
        return f"set {ENV_UPDATE}=1 and run it again"

    def updated_by(self, mode: str) -> str:
        """What wrote a baseline, for the report: '--vistest-update=changed'."""
        origin = _origin(self.origin)
        if origin == "pytest":
            return f"--vistest-update={mode}"
        if origin == "cli":
            return "vistest check --update"
        return f"{ENV_UPDATE}=1"

    @property
    def platform_how(self) -> str:
        """How to name the platform, where this check runs."""
        origin = _origin(self.origin)
        if origin == "pytest":
            return "pass --vistest-platform (or set vistest_platform in pyproject.toml)"
        if origin == "cli":
            return "pass --platform"
        return f"set {ENV_PLATFORM}"

    @property
    def config(self) -> VisTestConfig:
        if self._config is None:
            from ..config import VisTestConfig

            self._config = VisTestConfig.load(self.config_path)
        return self._config

    @property
    def plugins_config(self):
        """The `plugins:` section, with the command line's `fail_on` on top."""
        from dataclasses import replace

        plugins = self.config.plugins
        if self.fail_on:
            plugins = replace(plugins, fail_on=self.fail_on).validated(
                "--vistest-fail-on")
        return plugins

    @property
    def store(self) -> SnapshotStore:
        if self._store is None:
            self._store = FileStore(self.baselines)
        return self._store

    @property
    def parts_dir(self) -> Path:
        return self.artifacts_root / "report" / "parts"

    # ------------------------------------------------------------------ #
    def platform_for(self, shot: Capture) -> str:
        """Which directory this snapshot's baseline belongs in.

        Order: what the call or the flag said, then what the live page tells us,
        then nothing at all.

        The last one is the interesting case. A target that is a `bytes` object
        or a file carries no browser and no window size, and inventing
        «chromium» for it would be a guess written into a directory name in
        somebody's repository. So such a run keeps its baselines flat, directly
        under the baseline root, and the report says the platform is unknown.

        When the page does answer, the key is the same one the service uses —
        `platform_key()`, which includes the operating system. Font rendering
        differs between Linux and Windows physically, so a baseline captured on
        one is not comparable on the other; and keeping the same key is what
        makes `vistest baselines export` a way into a server installation
        rather than a re-approval of everything.

        The `1x` in that key is the scale **of the picture**, not of the
        screen: pixels per CSS pixel. With `scale="css"` — the default — that
        is 1 on every machine, which is the point of it. With
        `scale="device"` it is the page's own devicePixelRatio, so a 2x
        picture never lands next to a 1x one under the same name. Only when the
        ratio cannot be read does the config's `device_scale_factor` stand in.
        """
        if self.platform_override:
            return self.platform_override
        if not (shot.browser or shot.viewport):
            _warn_no_platform(self.platform_how)
            return ""
        from ..config import platform_key

        scale = shot.scale if shot.scale is not None \
            else self.config.capture.device_scale_factor
        return platform_key(shot.browser or "chromium", scale,
                            viewport=shot.viewport or None)

    def key(self, name: str, platform: str) -> SnapshotKey:
        #  No project prefix. `service.project` (and VISTEST_PROJECT, which a CI
        #  talking to a server sets) used to move every baseline into a folder
        #  of that name — so a CI variable meant for the server made the whole
        #  committed set «missing» (review v1, 2.4). The library reads nothing
        #  from the `service:` section.
        return SnapshotKey(name=name, platform=platform)

    def artifact_path(self, kind: str, key: SnapshotKey) -> Path:
        """`.vistest/actual/<platform>/<name>.png` and its siblings."""
        return self.artifacts_root / kind / Path(str(key.relpath))

    # ------------------------------------------------------------------ #
    @classmethod
    def from_env(cls, root: Path | None = None) -> LibraryContext:
        """A context for a process nobody configured. Loud about bad values."""
        from ..config import env_flag, env_text

        return cls(
            root=Path(root or Path.cwd()),
            baselines=(Path(value) if (value := env_text(ENV_BASELINES)) else None),
            report=(Path(value) if (value := env_text(ENV_REPORT)) else None),
            platform_override=env_text(ENV_PLATFORM),
            update=env_flag(ENV_UPDATE, default=False),
            origin="script",
        )


# --------------------------------------------------------------------------- #
#  The process-wide one
# --------------------------------------------------------------------------- #
_current: LibraryContext | None = None
_warned = False
_warned_no_platform = False
_warned_unread = False


def warn_unread_once(config, *, say: bool = True) -> str:
    """The warning about unread keys, once per process; returns its text.

    `say=False` only marks it as said — for the pytest plugin, which issues it
    itself, once per run, in the controller (not once per xdist worker).
    """
    global _warned_unread
    if _warned_unread:
        return ""
    _warned_unread = True
    text = unread_text(config)
    if text and say:
        warnings.warn(text, VisTestWarning, stacklevel=3)
    return text


def _warn_no_platform(how: str = f"set {ENV_PLATFORM}") -> None:
    """Said once per process when the baselines are going to the root.

    Not guessing a browser is the right call, but making it in silence is not.
    Somebody driving Selenium and handing us `bytes` gets one directory for
    every machine that ever runs the suite — and then a developer's macOS and a
    Linux CI compare against the same file. Font rendering differs between them
    physically, so the run goes red for a reason that has nothing to do with
    the page, and the directory layout gives no hint that this is what
    happened. It is the most expensive failure this tool has, and one line at
    the top of the log is the whole cost of preventing it.
    """
    global _warned_no_platform
    if _warned_no_platform:
        return
    _warned_no_platform = True
    warnings.warn(
        f"byte targets have no platform: baselines go to the root; {how} if they vary "
        "by machine", VisTestWarning, stacklevel=5)


def install(context: LibraryContext) -> LibraryContext:
    global _current
    _current = context
    return context


def uninstall() -> None:
    global _current
    _current = None


def current() -> LibraryContext:
    """The installed context, or one built from the environment — with a warning.

    The warning fires once per process and names what is not available, because
    the symptom otherwise is silent and confusing: `--vistest-update` appears
    to do nothing, and the report is written somewhere the person did not ask
    for. The usual cause is a suite that disabled plugin autoloading
    (`-p no:cacheprovider` style, or `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`).
    """
    global _warned
    if _current is not None:
        return _current
    if not _warned:
        _warned = True
        warnings.warn(
            "vistest: the pytest plugin is not active, so --vistest-update, "
            "--vistest-baselines, --vistest-platform and --vistest-report are "
            "not available. Falling back to the working directory and the "
            f"{ENV_BASELINES}/{ENV_PLATFORM}/{ENV_REPORT}/{ENV_UPDATE} "
            "environment variables.",
            VisTestWarning, stacklevel=3)
    context = install(LibraryContext.from_env())
    warn_unread_once(context.config)
    return context


def reset_warning() -> None:
    """Test hook: forget that the warnings were already issued."""
    global _warned, _warned_no_platform, _warned_unread
    _warned = False
    _warned_no_platform = False
    _warned_unread = False
