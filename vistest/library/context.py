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
from ..storage.file import DEFAULT_BASELINE_ROOT, FileStore
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


@dataclass
class LibraryContext:
    """One process's answer to «where does all of this go»."""

    root: Path = field(default_factory=Path.cwd)
    baselines: Path | None = None
    artifacts_root: Path | None = None
    report: Path | None = None
    platform_override: str = ""
    update: bool = False
    config_path: str | None = None
    preset: str | None = None
    #  Filled on first use; both are process-wide and neither is cheap.
    _config: Any = None
    _store: Any = None

    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.baselines = Path(self.baselines) if self.baselines is not None \
            else self.root / DEFAULT_BASELINE_ROOT
        if self.artifacts_root is None:
            self.artifacts_root = self.root / self.config.paths.root
        self.artifacts_root = Path(self.artifacts_root)
        if self.report is None:
            self.report = self.artifacts_root / "report" / "index.html"
        self.report = Path(self.report)

    # ------------------------------------------------------------------ #
    @property
    def config(self) -> VisTestConfig:
        if self._config is None:
            from ..config import VisTestConfig

            self._config = (VisTestConfig.preset_of(self.preset) if self.preset
                            else VisTestConfig.load(self.config_path))
        return self._config

    @property
    def store(self) -> SnapshotStore:
        if self._store is None:
            self._store = FileStore(self.baselines)
        return self._store

    @property
    def parts_dir(self) -> Path:
        return self.artifacts_root / "report" / "parts"

    @property
    def project(self) -> str:
        """The name prefix for a repository that holds more than one app."""
        name = str(self.config.service.project or "").strip("/")
        return "" if name in ("", "default") else name

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
        """
        if self.platform_override:
            return self.platform_override
        if not (shot.browser or shot.viewport):
            _warn_no_platform()
            return ""
        from ..config import platform_key

        return platform_key(shot.browser or "chromium",
                            self.config.capture.device_scale_factor,
                            viewport=shot.viewport or None)

    def key(self, name: str, platform: str) -> SnapshotKey:
        return SnapshotKey(name=name, platform=platform, project=self.project)

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
        )


# --------------------------------------------------------------------------- #
#  The process-wide one
# --------------------------------------------------------------------------- #
_current: LibraryContext | None = None
_warned = False
_warned_no_platform = False


def _warn_no_platform() -> None:
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
        "byte targets have no platform; baselines go to the root — set "
        "vistest_platform if these images depend on the environment",
        VisTestWarning, stacklevel=4)


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
    return install(LibraryContext.from_env())


def reset_warning() -> None:
    """Test hook: forget that the warnings were already issued."""
    global _warned, _warned_no_platform
    _warned = False
    _warned_no_platform = False
