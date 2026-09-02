# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Матрица браузеров и разрешений.

Главное, что здесь проверяется, — не раскрытие списков, а **обратная
совместимость ключей хранения**. Просто добавить размер окна в ключ значит в
день обновления обесценить все уже снятые эталоны разом: они лежат под
`linux-chromium-1x`, а прогон пойдёт искать `linux-chromium-1x-1440x900` и
запишет всё заново как «новые». Человек получит сотню новых эталонов, ни одного
сравнения — и будет прав, посчитав это поломкой.

Поэтому базовый размер остаётся под прежним ключом, и на это здесь тест.
"""

from __future__ import annotations

import pytest

from vistest.config import VisTestConfig, platform_key
from vistest.matrix import (
    MatrixError,
    Variant,
    expand,
    from_config,
    is_matrix,
    normalize_viewport,
    parse_viewport,
    variant_of_platform,
)


# --------------------------------------------------------------------------- #
#  Разбор размеров
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("1440x900", (1440, 900)),
    ("1440X900", (1440, 900)),
    ("1440×900", (1440, 900)),      # знак умножения — его печатают чаще, чем думают
    (" 390x844 ", (390, 844)),
])
def test_a_viewport_is_read_the_way_people_write_it(text, expected):
    assert parse_viewport(text) == expected


@pytest.mark.parametrize("text", ["1440*900", "1440", "abc", "", "0x0", "99999x100"])
def test_a_broken_viewport_is_refused_by_name(text):
    """Опечатка обязана падать здесь, а не через минуту ожидания браузера."""
    with pytest.raises(MatrixError):
        parse_viewport(text)


def test_the_same_size_written_differently_is_one_variant():
    """«1440X900» и «1440×900» — один набор эталонов, а не три.

    Как ключи каталога они разошлись бы молча, и человек получил бы три набора
    под одну и ту же вёрстку, каждый со своей историей решений.
    """
    assert normalize_viewport("1440X900") == normalize_viewport("1440×900") \
        == normalize_viewport(" 1440x900 ") == "1440x900"


# --------------------------------------------------------------------------- #
#  Ключи хранения — то, ради чего этот файл существует
# --------------------------------------------------------------------------- #
def test_without_a_matrix_the_key_is_exactly_what_it_always_was():
    assert platform_key("chromium", 1.0) == platform_key("chromium", 1.0, None)
    assert "x-" not in platform_key("chromium", 1.0).split("-", 2)[2]


def test_the_base_size_keeps_the_old_key_and_the_rest_get_a_suffix():
    """Единственное правило, от которого зависит, переживёт ли обновление набор."""
    variants = expand(["chromium"], ["1440x900", "390x844"])
    base, other = variants
    assert base.base is True
    assert base.platform == platform_key("chromium", 1.0), \
        "эталоны базового размера обязаны остаться там, где лежали"
    assert other.platform == platform_key("chromium", 1.0) + "-390x844"


def test_the_base_size_can_be_named_explicitly():
    """Иначе перестановка строк в конфиге переносит набор на диске.

    Выглядит такая правка как форматирование, а стоит как потеря эталонов.
    """
    variants = expand(["chromium"], ["1440x900", "390x844"],
                      base_viewport="390x844")
    by_size = {v.viewport: v for v in variants}
    assert by_size["390x844"].platform == platform_key("chromium", 1.0)
    assert by_size["1440x900"].platform.endswith("-1440x900")


def test_a_base_size_outside_the_matrix_is_refused():
    with pytest.raises(MatrixError):
        expand(["chromium"], ["1440x900"], base_viewport="800x600")


def test_a_browser_is_already_part_of_the_key():
    """Так было до всякой матрицы: отрисовка шрифтов в chromium и firefox разная."""
    a, b = expand(["chromium", "firefox"], [])
    assert a.platform != b.platform


def test_an_explicit_key_wins_over_a_computed_one():
    """Эталоны docker при сервисе на Windows — обычный случай, а не экзотика.

    Пересборка ключа из полей дала бы `win-...` вместо `docker-...`, увела бы
    пересъёмку в пустой набор и записала бы там «новый эталон».
    """
    v = Variant(browser="chromium", viewport="390x844", scale=1.0,
                key="docker-chromium-1x-390x844")
    assert v.platform == "docker-chromium-1x-390x844"


# --------------------------------------------------------------------------- #
#  Раскрытие
# --------------------------------------------------------------------------- #
def test_the_order_of_variants_is_stable():
    """Порядок — это порядок, в котором человек увидит варианты в разборе."""
    variants = expand(["chromium", "firefox"], ["1440x900", "390x844"])
    assert [v.label for v in variants] == [
        "chromium · 1440×900", "chromium · 390×844",
        "firefox · 1440×900", "firefox · 390×844",
    ]


def test_duplicates_collapse_instead_of_failing():
    """Повтор в списке из шести строк — невнимательность, а не ошибка описания."""
    variants = expand(["chromium", "chromium"], ["1440x900", "1440x900"])
    assert len(variants) == 1


def test_an_empty_matrix_still_gives_one_variant():
    """Ноль вариантов означал бы прогон, который молча проверил ноль снимков.

    В историю он попал бы зелёным, и это худший из возможных ответов.
    """
    variants = expand([], [])
    assert len(variants) == 1 and variants[0].browser == "chromium"
    assert not is_matrix(variants)


def test_browsers_only_matrix_keeps_the_size_from_capture():
    variants = expand(["chromium", "webkit"], [])
    assert [v.viewport for v in variants] == [None, None]
    assert all(v.base for v in variants)


def test_a_config_without_a_matrix_behaves_as_before():
    cfg = VisTestConfig()
    variants = from_config(cfg)
    assert len(variants) == 1
    assert variants[0].platform == platform_key(
        "chromium", cfg.capture.device_scale_factor)


def test_the_config_matrix_is_expanded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "vistest.yaml").write_text(
        "matrix:\n"
        "  browsers: [chromium, firefox]\n"
        '  viewports: ["1440x900", "390x844"]\n'
        '  base_viewport: "1440x900"\n', encoding="utf-8")
    cfg = VisTestConfig.load()
    assert len(from_config(cfg)) == 4


def test_a_broken_matrix_fails_at_load_not_at_run(tmp_path, monkeypatch):
    """Опечатка в размере иначе всплывёт максимально далеко от строки с ней."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "vistest.yaml").write_text(
        'matrix:\n  viewports: ["1440*900"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="matrix"):
        VisTestConfig.load()


# --------------------------------------------------------------------------- #
#  Обратный разбор — для подписей в интерфейсе
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key,browser,viewport", [
    ("linux-chromium-1x", "chromium", None),
    ("docker-firefox-2x-390x844", "firefox", "390x844"),
    ("win-webkit-1.5x-1440x900", "webkit", "1440x900"),
])
def test_a_key_reads_back_into_a_variant(key, browser, viewport):
    info = variant_of_platform(key)
    assert info["browser"] == browser and info["viewport"] == viewport


def test_an_unknown_key_is_shown_as_it_is():
    """Отвечать «не знаю» на собственные данные — худшее из поведений.

    Ключ мог быть записан версией, которая знала другой набор браузеров.
    """
    info = variant_of_platform("что-то-совсем-другое")
    assert info["label"] == "что-то-совсем-другое"


def test_every_variant_of_a_run_gets_its_own_artifact_directory():
    """Иначе второй вариант затирает картинки первого.

    Каталог артефактов берётся по имени снимка, а имя у вариантов общее — и
    разбор показал бы кадр firefox под подписью chromium.
    """
    variants = expand(["chromium", "firefox"], ["1440x900", "390x844"])
    assert len({v.slug for v in variants}) == len(variants)
