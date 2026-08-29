# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Откуда фикстура берёт значение — разбор чужого conftest без его выполнения."""

from __future__ import annotations

import textwrap

import pytest

from vistest.introspect import find_fixture_sources


def _project(tmp_path, **files):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("body, kind, key", [
    ('return os.getenv("USER_PASSWORD")', "env", "USER_PASSWORD"),
    ('return os.environ.get("USER_PASSWORD")', "env", "USER_PASSWORD"),
    ('return os.environ["USER_PASSWORD"]', "env", "USER_PASSWORD"),
    ('return os.getenv("USER_PASSWORD", "")', "env", "USER_PASSWORD"),
    ('return request.config.getoption("--user-pass")', "option", "--user-pass"),
    ('return pytestconfig.getoption("user_pass")', "option", "--user_pass"),
])
def test_value_source_is_recognized(tmp_path, body, kind, key):
    _project(tmp_path, **{"conftest.py": f'''
        import os
        import pytest

        @pytest.fixture(scope="session")
        def user_pass(request, pytestconfig):
            {body}
    '''})

    found = find_fixture_sources(tmp_path, ["user_pass"])
    assert found["user_pass"].kind == kind
    assert found["user_pass"].key == key
    assert found["user_pass"].file == "conftest.py"
    assert found["user_pass"].line > 0


def test_module_level_constant_is_unwrapped(tmp_path):
    """`PASSWORD = os.getenv(...)` наверху, `return PASSWORD` в фикстуре."""
    _project(tmp_path, **{"conftest.py": '''
        import os
        import pytest

        PASSWORD = os.getenv("STAND_PASSWORD")

        @pytest.fixture
        def user_pass():
            return PASSWORD
    '''})

    found = find_fixture_sources(tmp_path, ["user_pass"])
    assert (found["user_pass"].kind, found["user_pass"].key) == ("env", "STAND_PASSWORD")


def test_unknown_source_still_points_at_the_file(tmp_path):
    """Не угадали — честно отдаём файл и строку, а не выдуманную переменную."""
    _project(tmp_path, **{"conftest.py": '''
        import pytest

        @pytest.fixture
        def user_pass(vault):
            return vault.secret("stand")
    '''})

    src = find_fixture_sources(tmp_path, ["user_pass"])["user_pass"]
    assert src.kind == "unknown"
    assert src.key == ""
    assert "conftest.py" in src.describe()
    assert "open that file" in src.advice()


def test_only_fixtures_are_considered(tmp_path):
    """Обычная функция с тем же именем — не фикстура и в ответ не попадает."""
    _project(tmp_path, **{"conftest.py": '''
        import os

        def user_pass():
            return os.getenv("NOT_A_FIXTURE")
    '''})

    assert find_fixture_sources(tmp_path, ["user_pass"]) == {}


def test_conftest_wins_over_other_modules(tmp_path):
    """Фикстуры почти всегда в conftest — его и смотрим первым."""
    _project(
        tmp_path,
        **{"conftest.py": '''
            import os
            import pytest

            @pytest.fixture
            def user_pass():
                return os.getenv("RIGHT")
        ''',
           "UiTests/helpers.py": '''
            import os
            import pytest

            @pytest.fixture
            def user_pass():
                return os.getenv("WRONG")
        '''})

    assert find_fixture_sources(tmp_path, ["user_pass"])["user_pass"].key == "RIGHT"


def test_virtualenv_is_not_walked(tmp_path):
    """В чужом .venv лежат тысячи файлов и чужие фикстуры — туда не ходим."""
    _project(tmp_path, **{".venv/lib/site.py": '''
        import os
        import pytest

        @pytest.fixture
        def user_pass():
            return os.getenv("FROM_VENV")
    '''})

    assert find_fixture_sources(tmp_path, ["user_pass"]) == {}


def test_broken_file_does_not_stop_the_search(tmp_path):
    _project(
        tmp_path,
        **{"UiTests/broken.py": "def (((\n",
           "conftest.py": '''
            import os
            import pytest

            @pytest.fixture
            def user_pass():
                return os.getenv("STILL_FOUND")
        '''})

    assert find_fixture_sources(tmp_path, ["user_pass"])["user_pass"].key == "STILL_FOUND"


def test_nothing_asked_nothing_read(tmp_path):
    assert find_fixture_sources(tmp_path, []) == {}
