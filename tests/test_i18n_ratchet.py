# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The English-only ratchet, checked the way everything else here is.

`scripts/check_i18n.py` runs as its own CI job, and that is where it matters.
This file exists so that the ratchet also fails in a local `pytest` run — the
place a person actually looks before pushing — and so that the script's own
counting rules are pinned rather than assumed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_i18n.py"

#  The Russian word for "comment", spelled in escapes. A test of a Russian-text
#  detector needs Russian text, and writing it literally would put this file
#  into the debt list it exists to guard — the one exemption nobody would
#  notice being abused later.
RU = "\u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0439"

sys.path.insert(0, str(ROOT / "scripts"))
import check_i18n  # noqa: E402


def test_the_repository_has_not_acquired_new_russian():
    """The whole point, asserted directly.

    Growth fails; shrinking does not — a file that has just been translated is
    progress, and demanding that the debt file be regenerated in the same
    commit would make the check an obstacle rather than a ratchet.
    """
    current = check_i18n.scan()
    debt = check_i18n.read_debt()

    appeared = sorted(set(current) - set(debt))
    grown = sorted(f"{path}: {debt[path]} -> {current[path]}"
                   for path in current
                   if path in debt and current[path] > debt[path])

    assert not appeared, (
        f"Russian text appeared in files that had none: {appeared}. Write it "
        "in English, or record the debt with "
        "`python scripts/check_i18n.py --update`.")
    assert not grown, f"files grew past their recorded debt: {grown}"


def test_a_russian_line_is_counted_and_an_english_one_is_not(tmp_path: Path):
    english = tmp_path / "a.py"
    english.write_text("# a comment\nx = 1\n", "utf-8")
    assert check_i18n.count_lines(english) == 0

    mixed = tmp_path / "b.py"
    mixed.write_text(f"# a comment\n# {RU}\nx = 1  # {RU}\n", "utf-8")
    assert check_i18n.count_lines(mixed) == 2


def test_binaries_and_baselines_are_not_read_for_language(tmp_path: Path):
    png = tmp_path / "baseline.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + RU.encode())
    assert check_i18n.count_lines(png) == 0

    blob = tmp_path / "model.bin"
    blob.write_bytes(b"\x00\x01" + RU.encode())
    assert check_i18n.count_lines(blob) == 0


def test_a_russian_document_is_not_debt():
    """`README.ru.md` is the Russian version, not a file waiting to be fixed."""
    assert check_i18n.is_translation("README.ru.md")
    assert check_i18n.is_translation("docs/ARCHITECTURE.ru.md")
    assert not check_i18n.is_translation("README.md")
    assert "README.ru.md" not in check_i18n.scan()


def test_the_script_exits_zero_on_the_current_state():
    """Also proves it runs as a script, which is how CI calls it."""
    done = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "none of them new" in done.stdout
