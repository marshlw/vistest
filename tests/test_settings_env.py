# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Проверка редактора переменных среды.

Файл содержит пароли к стенду, поэтому тесты фиксируют не только разбор
формата, но и границы: что нельзя записать и кому нельзя отвечать.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi", reason="редактор живёт в сервисе")

from vistest.api import settings as st  # noqa: E402


# --------------------------------------------------------------------------- #
#  Разбор формата
# --------------------------------------------------------------------------- #
def test_parse_skips_comments_and_blanks():
    items = st.parse_env(
        "# комментарий\n\nVISTEST_USER=qa\n   \nVISTEST_PASSWORD=secret\n")
    assert [i["name"] for i in items] == ["VISTEST_USER", "VISTEST_PASSWORD"]
    assert items[0]["value"] == "qa"


def test_parse_strips_quotes():
    assert st.parse_env('A="in quotes"')[0]["value"] == "in quotes"
    assert st.parse_env("B='single'")[0]["value"] == "single"


def test_parse_keeps_equals_inside_value():
    """Пароли и токены часто содержат `=` — резать по первому, не по всем."""
    assert st.parse_env("TOKEN=abc=def==")[0]["value"] == "abc=def=="


def test_secret_flag_is_set_by_name():
    items = {i["name"]: i["secret"] for i in st.parse_env(
        "VISTEST_USER=qa\nVISTEST_PASSWORD=x\nAPI_TOKEN=y\nBASE_URL=z\n")}
    assert items["VISTEST_PASSWORD"] and items["API_TOKEN"]
    assert not items["VISTEST_USER"] and not items["BASE_URL"]


def test_roundtrip_preserves_values():
    original = [{"name": "VISTEST_USER", "value": "qa"},
                {"name": "VISTEST_PASSWORD", "value": "p@ss w=rd"}]
    parsed = st.parse_env(st.render_env(original))
    assert [(i["name"], i["value"]) for i in parsed] == \
           [(i["name"], i["value"]) for i in original]


def test_rendered_file_warns_about_git():
    text = st.render_env([{"name": "A", "value": "1"}])
    assert ".gitignore" in text


# --------------------------------------------------------------------------- #
#  Что записать нельзя
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["2FA", "with space", "a-b", "поле", "A=B"])
def test_invalid_names_are_rejected(name):
    with pytest.raises(fastapi.HTTPException):
        st.render_env([{"name": name, "value": "x"}])


def test_empty_name_is_skipped_not_rejected():
    """Пустая строка в форме — не ошибка пользователя, а просто пустая строка."""
    text = st.render_env([{"name": "", "value": "x"},
                          {"name": "A", "value": "1"}])
    assert st.parse_env(text) == [{"name": "A", "value": "1", "secret": False}]


def test_newline_in_value_is_rejected():
    """Перенос строки разорвал бы файл на две переменные, одна из них мусорная."""
    with pytest.raises(fastapi.HTTPException):
        st.render_env([{"name": "A", "value": "one\ntwo"}])


# --------------------------------------------------------------------------- #
#  Доступ
# --------------------------------------------------------------------------- #
class _Req:
    def __init__(self, host, cookies=None):
        self.client = type("_C", (), {"host": host})()
        # `_guard` сначала проверяет роль по сессионной куке — без этого поля
        # заглушка падала с AttributeError раньше, чем доходила до проверки
        # адреса, ради которой тест и написан.
        self.cookies = dict(cookies or {})


def test_remote_access_is_denied_by_default(monkeypatch):
    monkeypatch.delenv("VISTEST_SECRETS_UI", raising=False)
    with pytest.raises(fastapi.HTTPException) as e:
        st._guard(_Req("10.0.0.5"))
    assert e.value.status_code == 403


def test_loopback_is_allowed(monkeypatch):
    monkeypatch.delenv("VISTEST_SECRETS_UI", raising=False)
    for host in ("127.0.0.1", "::1", "localhost"):
        st._guard(_Req(host))          # не должно бросать


def test_can_be_opened_explicitly(monkeypatch):
    monkeypatch.setenv("VISTEST_SECRETS_UI", "all")
    st._guard(_Req("10.0.0.5"))


def test_can_be_disabled_entirely(monkeypatch):
    monkeypatch.setenv("VISTEST_SECRETS_UI", "off")
    with pytest.raises(fastapi.HTTPException):
        st._guard(_Req("127.0.0.1"))


# --------------------------------------------------------------------------- #
#  Подсказка «чего не хватает»
# --------------------------------------------------------------------------- #
def test_missing_vars_are_discovered_from_config(tmp_path, monkeypatch):
    root = tmp_path / ".vistest"
    (root / "baselines" / "login").mkdir(parents=True)
    (root / "baselines" / "login" / "meta.json").write_text(
        '{"steps": [{"action": "fill", "value": "${VISTEST_PASSWORD}"}]}',
        encoding="utf-8")
    (tmp_path / "vistest.yaml").write_text(
        'flows:\n  login:\n    - {action: fill, value: "${VISTEST_USER}"}\n',
        encoding="utf-8")

    monkeypatch.setenv("VISTEST_ROOT", str(root))
    found = st._referenced_names()

    assert {"VISTEST_USER", "VISTEST_PASSWORD"} <= found


def test_write_applies_values_to_process(monkeypatch):
    monkeypatch.delenv("VISTEST_DEMO_VAR", raising=False)
    st._apply_to_process("VISTEST_DEMO_VAR=hello\n")
    import os

    assert os.environ["VISTEST_DEMO_VAR"] == "hello"

    # Перезапись, а не setdefault: человек только что исправил значение.
    st._apply_to_process("VISTEST_DEMO_VAR=updated\n")
    assert os.environ["VISTEST_DEMO_VAR"] == "updated"
