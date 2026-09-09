# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`NamingProfile` — reading somebody else's directory of PNGs.

This is the part of a suite profile the comparison core keeps, and the whole
non-Python path rests on it: connect a Playwright or a Cypress suite and every
answer about it — how many snapshots there are, which picture is the fresh one,
which baseline it pairs with — comes out of these four methods. Get the naming
wrong and the run ends with «no pairs found» and nothing to look at, which is
indistinguishable from «your tests are broken».

The tests are grouped by method, and `with_overrides` gets the most of them on
purpose. It is the escape hatch a project uses when its layout was not
predicted, so it is edited by people who are already confused, and every one of
its rules needs stating: an unknown key is refused, an empty value means «not
specified», a bare string becomes a one-element tuple, and `search_dirs` adds
rather than replaces. None of that is guessable from the signature.

The asymmetry between the first two is the interesting part and is tested from
both sides. A typo in a key is a mistake nobody can see afterwards — the
built-in patterns keep working and the correction is simply absent — so it
fails at once. An empty value is what the connection form sends for every
field a person left alone, so it has to keep meaning «leave this as it is».
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from vistest.core.naming import (
    ACTUAL,
    DIFF,
    EXPECTED,
    OTHER,
    SKIP_DIRS,
    NamingError,
    NamingProfile,
    Snapshot,
)

#  A profile in the shape the JS ecosystem actually uses: suffixes, not
#  prefixes. Recognising only prefixes is the bug this class was extracted to
#  keep fixed.
SUFFIXED = NamingProfile(
    actual=(r"(?P<name>.+)-actual\.png",),
    expected=(r"(?P<name>.+)-expected\.png",),
    diff=(r"(?P<name>.+)-diff\.png",),
)


# --------------------------------------------------------------------------- #
#  classify
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("filename, kind", [
    ("login-actual.png", ACTUAL),
    ("login-expected.png", EXPECTED),
    ("login-diff.png", DIFF),
    ("login.png", OTHER),
    ("notes.txt", OTHER),
])
def test_each_kind_is_recognised(filename, kind):
    assert SUFFIXED.classify(Path("/x") / filename).kind == kind


def test_the_name_is_the_group_plus_png():
    got = SUFFIXED.classify(Path("/x/checkout-actual.png"))
    assert got.name == "checkout.png"


def test_the_png_stays_on_the_name():
    """It has always been part of the name, in the store and in the history.

    Dropping it would make every existing snapshot look like a new one under a
    new name — the same picture twice in the list, with the history split.
    """
    assert SUFFIXED.classify(Path("/x/a-actual.png")).name.endswith(".png")


def test_diff_is_tried_before_actual():
    """A loose `actual` pattern would otherwise swallow the diff image.

    Accepting a diff as a result is worse than accepting nothing: the engine
    would compare a picture of red rectangles against the baseline and report a
    regression that is not there.
    """
    greedy = NamingProfile(actual=(r"(?P<name>.+)\.png",),
                           diff=(r"(?P<name>.+)-diff\.png",))
    assert greedy.classify(Path("/x/login-diff.png")).kind == DIFF
    assert greedy.classify(Path("/x/login.png")).kind == ACTUAL


def test_classification_is_case_insensitive():
    assert SUFFIXED.classify(Path("/x/Login-ACTUAL.PNG")).kind == ACTUAL


def test_a_pattern_without_a_name_group_falls_back_to_the_stem():
    profile = NamingProfile(actual=(r".+-shot\.png",))
    assert profile.classify(Path("/x/login-shot.png")).name == "login-shot.png"


def test_strip_removes_what_is_ours_to_decide():
    """Playwright bakes the browser and the OS into the baseline file name.

    Both are ours — the browser is a run property, the platform is part of the
    storage key — so leaving them in would turn one snapshot into three that
    never meet.
    """
    profile = NamingProfile(
        actual=(r"(?P<name>.+)\.png",),
        strip=(r"-(?:chromium|firefox|webkit)(?:-(?:linux|darwin|win32))?$",))
    assert profile.classify(Path("/x/login-chromium-linux.png")).name == "login.png"


def test_a_name_stripped_down_to_nothing_still_has_one():
    profile = NamingProfile(actual=(r"(?P<name>.+)\.png",), strip=(r".*",))
    assert profile.classify(Path("/x/login.png")).name == "snapshot.png"


def test_an_unmatched_file_carries_no_name():
    got = SUFFIXED.classify(Path("/x/readme.png"))
    assert got.kind == OTHER and got.name == ""
    assert got.path == Path("/x/readme.png")


def test_is_actual_answers_for_the_actual_only():
    assert SUFFIXED.classify(Path("/x/a-actual.png")).is_actual
    assert not SUFFIXED.classify(Path("/x/a-expected.png")).is_actual


# --------------------------------------------------------------------------- #
#  classify: grouping and keep_dir
# --------------------------------------------------------------------------- #
def test_the_three_pictures_of_one_comparison_share_a_group():
    kinds = [SUFFIXED.classify(Path("/x/login-" + k + ".png"))
             for k in ("actual", "expected", "diff")]
    assert len({s.group for s in kinds}) == 1


def test_the_same_name_in_two_directories_is_two_groups():
    a = SUFFIXED.classify(Path("/x/one/login-actual.png"))
    b = SUFFIXED.classify(Path("/x/two/login-actual.png"))
    assert a.group != b.group


def test_without_keep_dir_two_specs_collapse_into_one_name():
    a = SUFFIXED.classify(Path("/root/one/login-actual.png"), relative_to=Path("/root"))
    b = SUFFIXED.classify(Path("/root/two/login-actual.png"), relative_to=Path("/root"))
    assert a.name == b.name == "login.png"


def test_keep_dir_makes_them_different(tmp_path):
    """Two specs may each hold a `login.png`; flattening loses the second."""
    profile = SUFFIXED.with_overrides(keep_dir=True)
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    a = profile.classify(tmp_path / "one" / "login-actual.png", relative_to=tmp_path)
    b = profile.classify(tmp_path / "two" / "login-actual.png", relative_to=tmp_path)
    assert (a.name, b.name) == ("one/login.png", "two/login.png")


def test_keep_dir_uses_posix_separators(tmp_path):
    """The name is a storage key, and a key must not depend on the OS."""
    profile = SUFFIXED.with_overrides(keep_dir=True)
    (tmp_path / "a" / "b").mkdir(parents=True)
    got = profile.classify(tmp_path / "a" / "b" / "login-actual.png",
                           relative_to=tmp_path)
    assert got.name == "a/b/login.png" and "\\" not in got.name


def test_keep_dir_without_a_root_keeps_the_bare_name():
    profile = SUFFIXED.with_overrides(keep_dir=True)
    assert profile.classify(Path("/x/one/login-actual.png")).name == "login.png"


def test_keep_dir_outside_the_root_falls_back_to_the_bare_name(tmp_path):
    """A file that is not under `relative_to` has no relative path to keep."""
    profile = SUFFIXED.with_overrides(keep_dir=True)
    outside = tmp_path.parent / "elsewhere" / "login-actual.png"
    assert profile.classify(outside, relative_to=tmp_path).name == "login.png"


# --------------------------------------------------------------------------- #
#  with_overrides
# --------------------------------------------------------------------------- #
def test_overriding_one_field_leaves_the_others_alone():
    got = SUFFIXED.with_overrides(naming={"actual": r"a-(?P<name>.+)\.png"})
    assert got.actual == (r"a-(?P<name>.+)\.png",)
    assert got.expected == SUFFIXED.expected
    assert got.diff == SUFFIXED.diff


def test_overriding_several_fields_at_once():
    got = SUFFIXED.with_overrides(naming={
        "actual": r"A(?P<name>.+)",
        "expected": r"E(?P<name>.+)",
        "diff": r"D(?P<name>.+)",
        "strip": r"-x$",
    })
    assert (got.actual, got.expected, got.diff, got.strip) == (
        (r"A(?P<name>.+)",), (r"E(?P<name>.+)",), (r"D(?P<name>.+)",), (r"-x$",))


def test_a_bare_string_becomes_a_one_element_tuple():
    """The form sends one pattern as a string; the field is a tuple of them."""
    got = SUFFIXED.with_overrides(naming={"actual": r"(?P<name>.+)\.png"})
    assert got.actual == (r"(?P<name>.+)\.png",)


def test_a_list_becomes_a_tuple():
    got = SUFFIXED.with_overrides(naming={"actual": ["a", "b"]})
    assert got.actual == ("a", "b")


def test_an_override_replaces_the_defaults_rather_than_adding_to_them():
    """Patterns replace; only `search_dirs` accumulates. The asymmetry is real.

    A project that corrects a pattern is saying the built-in one is wrong for
    it, and keeping the old one alongside would let the wrong rule keep
    matching. A project that adds a directory is saying «also look here».
    """
    got = SUFFIXED.with_overrides(naming={"actual": "only"})
    assert got.actual == ("only",)
    assert SUFFIXED.actual[0] not in got.actual


def test_an_unknown_key_is_refused_by_name():
    """A dropped key is indistinguishable from a key that works.

    This is the failure the library mode cannot afford: the correction is
    written into somebody else's `vistest.yaml`, the patterns quietly stay the
    built-in ones, and the only symptom is that the setting «does nothing».
    """
    with pytest.raises(NamingError) as e:
        SUFFIXED.with_overrides(naming={"nonsense": "x"})
    assert "nonsense" in str(e.value)
    assert "actual" in str(e.value)          # the known keys are listed


def test_the_naming_patch_itself_must_be_an_object():
    with pytest.raises(NamingError):
        SUFFIXED.with_overrides(naming="actual=login")


@pytest.mark.parametrize("empty", ["", [], (), None, 0, False])
def test_an_empty_value_does_not_erase_the_default(empty):
    """“Leave it as it is” and “match nothing” must not be the same input.

    The form sends an empty string for a field the person did not fill in. If
    that erased the pattern, opening the connection dialog and pressing save
    would silently stop every snapshot from being recognised.
    """
    got = SUFFIXED.with_overrides(naming={"actual": empty})
    assert got.actual == SUFFIXED.actual


def test_search_dirs_are_added_not_replaced():
    profile = NamingProfile(search_dirs=("out",))
    assert profile.with_overrides(search_dirs=("extra",)).search_dirs == ("out", "extra")


def test_search_dirs_are_deduplicated_keeping_order():
    profile = NamingProfile(search_dirs=("out", "keep"))
    got = profile.with_overrides(search_dirs=("keep", "out", "new"))
    assert got.search_dirs == ("out", "keep", "new")


def test_empty_search_dirs_change_nothing():
    profile = NamingProfile(search_dirs=("out",))
    assert profile.with_overrides(search_dirs=()) is profile


@pytest.mark.parametrize("value, expected", [(True, True), (False, False)])
def test_keep_dir_is_applied_in_both_directions(value, expected):
    """`False` has to be distinguishable from «not specified», which is `None`."""
    assert SUFFIXED.with_overrides(keep_dir=value).keep_dir is expected


def test_keep_dir_none_means_not_specified():
    assert SUFFIXED.with_overrides(keep_dir=None) is SUFFIXED


def test_with_no_arguments_the_same_object_comes_back():
    """Cheap, and it means a profile with no corrections is shared, not copied."""
    assert SUFFIXED.with_overrides() is SUFFIXED
    assert SUFFIXED.with_overrides(naming={}) is SUFFIXED


def test_the_original_is_not_modified():
    before = SUFFIXED.actual
    SUFFIXED.with_overrides(naming={"actual": "other"})
    assert SUFFIXED.actual == before


def test_a_profile_cannot_be_mutated_in_place():
    """Profiles are shared between projects; one of them editing is a bug."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        SUFFIXED.actual = ()


def test_overrides_of_a_subclass_return_that_subclass():
    """`SuiteProfile` extends this, and an override must not downgrade it."""
    from vistest.suites.base import SuiteProfile

    profile = SuiteProfile(id="x", title="X", actual=("a",))
    got = profile.with_overrides(naming={"actual": "b"})
    assert isinstance(got, SuiteProfile)
    assert got.id == "x" and got.actual == ("b",)


def test_a_pattern_that_does_not_compile_is_refused_where_it_is_written():
    """The regex is compiled while the profile is built, not while it is used.

    It used to survive until `classify`, which is the worst place available for
    it to surface: a bare `re.error` in the middle of a walk, with a file name
    in the traceback and no mention of the setting that is wrong. In the
    library mode that traceback lands in somebody else's CI.
    """
    with pytest.raises(NamingError) as e:
        SUFFIXED.with_overrides(naming={"actual": "(unclosed"})
    assert "actual" in str(e.value) and "unclosed" in str(e.value)


def test_a_broken_pattern_is_refused_on_the_profile_itself_too():
    """Not only through `with_overrides`: the constructor is a way in as well."""
    with pytest.raises(NamingError):
        NamingProfile(actual=("(unclosed",))


def test_a_bare_string_field_is_normalised_rather_than_iterated():
    """`actual="x"` used to be read as one pattern per character, in silence."""
    assert NamingProfile(actual=r"(?P<name>.+)\.png").actual == \
        (r"(?P<name>.+)\.png",)


# --------------------------------------------------------------------------- #
#  roots
# --------------------------------------------------------------------------- #
def test_roots_returns_only_directories_that_exist(tmp_path):
    (tmp_path / "there").mkdir()
    profile = NamingProfile(search_dirs=("there", "missing"))
    assert profile.roots(tmp_path) == [(tmp_path / "there").resolve()]


def test_roots_puts_the_extra_directories_first(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    profile = NamingProfile(search_dirs=("a",))
    got = profile.roots(tmp_path, extra=(tmp_path / "b",))
    assert got == [(tmp_path / "b").resolve(), (tmp_path / "a").resolve()]


def test_roots_deduplicates_the_same_directory_reached_twice(tmp_path):
    (tmp_path / "a").mkdir()
    profile = NamingProfile(search_dirs=("a",))
    assert profile.roots(tmp_path, extra=(tmp_path / "a",)) == \
        [(tmp_path / "a").resolve()]


def test_roots_of_nothing_is_empty(tmp_path):
    assert NamingProfile().roots(tmp_path) == []


# --------------------------------------------------------------------------- #
#  walk
# --------------------------------------------------------------------------- #
def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really a png")


def test_walk_finds_and_classifies_every_png(tmp_path):
    _png(tmp_path / "login-actual.png")
    _png(tmp_path / "nested" / "cart-expected.png")
    got = {s.path.name: s.kind for s in SUFFIXED.walk([tmp_path])}
    assert got == {"login-actual.png": ACTUAL, "cart-expected.png": EXPECTED}


def test_walk_ignores_everything_that_is_not_a_png(tmp_path):
    _png(tmp_path / "a-actual.png")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert [s.path.name for s in SUFFIXED.walk([tmp_path])] == ["a-actual.png"]


def test_walk_does_not_descend_into_junk(tmp_path):
    """`os.walk` and not `rglob` for exactly this: `rglob` cannot prune.

    On a Node project the difference is a hundred files against a hundred
    thousand, and it is paid on every render of the project card.
    """
    _png(tmp_path / "wanted-actual.png")
    for junk in ("node_modules", ".git", "__pycache__"):
        _png(tmp_path / junk / "unwanted-actual.png")
    assert [s.path.name for s in SUFFIXED.walk([tmp_path])] == ["wanted-actual.png"]


def test_walk_skips_every_hidden_directory(tmp_path):
    """Not only the listed ones: a dot directory is somebody's tooling."""
    _png(tmp_path / "wanted-actual.png")
    _png(tmp_path / ".anything" / "unwanted-actual.png")
    assert [s.path.name for s in SUFFIXED.walk([tmp_path])] == ["wanted-actual.png"]


def test_the_skip_list_covers_the_ecosystems_we_connect():
    assert {"node_modules", ".git", ".venv", "__pycache__", ".gradle",
            "vendor", ".vistest"} <= SKIP_DIRS


def test_walk_stops_at_the_limit(tmp_path):
    for i in range(10):
        _png(tmp_path / f"s{i}-actual.png")
    assert len(list(SUFFIXED.walk([tmp_path], limit=4))) == 4


def test_walk_covers_several_roots(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    _png(tmp_path / "one" / "a-actual.png")
    _png(tmp_path / "two" / "b-actual.png")
    got = sorted(s.path.name
                 for s in SUFFIXED.walk([tmp_path / "one", tmp_path / "two"]))
    assert got == ["a-actual.png", "b-actual.png"]


def test_walk_passes_relative_to_through_to_the_name(tmp_path):
    profile = SUFFIXED.with_overrides(keep_dir=True)
    _png(tmp_path / "spec" / "login-actual.png")
    got = list(profile.walk([tmp_path], relative_to=tmp_path))
    assert [s.name for s in got] == ["spec/login.png"]


def test_walk_yields_snapshots(tmp_path):
    _png(tmp_path / "a-actual.png")
    assert all(isinstance(s, Snapshot) for s in SUFFIXED.walk([tmp_path]))
