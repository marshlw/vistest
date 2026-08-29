# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Сводка прогона для комментария в PR/MR.

Комментарий читают в ленте, между чужими сообщениями, и читают быстро. Отсюда
всё, что здесь проверяется: ответ в первой строке, падения свёрнуты по общей
причине, таблица обрезана, а не растёт до сотни строк, и — отдельно — что
комментарий не врёт про то, куда уедет решение «принять».

Раньше эту сводку собирал JavaScript внутри `.github/workflows/visual.yml`:
двадцать строк, разбиравших `run.json` руками, без единого теста. Поэтому
здесь особенно много проверок на форму — ровно то, что тихо ломалось при любом
переименовании поля.
"""

from __future__ import annotations

from vistest.report.comment import MAX_CLUSTERS, MAX_ROWS, render_comment

FAILED = {
    "name": "shop.example/login.png", "verdict": "fail",
    "metrics": {"max_severity": 44.0, "changed_area_pct": 0.4},
    "regions": [{"kind": "shift", "severity": 44.0, "selector": ".header"}],
}
PASSED = {"name": "shop.example/cart.png", "verdict": "pass",
          "metrics": {"max_severity": 0.0}}
ERRORED = {"name": "checkout.png", "verdict": "error",
           "error": "page did not answer"}
FRESH = {"name": "profile.png", "verdict": "new_baseline"}


def _first(body: str) -> str:
    return body.splitlines()[0]


# --------------------------------------------------------------------------- #
#  Первая строка — ответ
# --------------------------------------------------------------------------- #
def test_the_first_line_answers_instead_of_announcing_itself():
    """Заголовок «VisTest report» не сообщает ничего.

    Человек в ленте PR читает первую строку и по ней решает, открывать ли
    остальное. Если там название инструмента, решение принято вслепую.
    """
    head = _first(render_comment([FAILED, PASSED]))
    assert "1 of 2" in head
    assert "changed" in head


def test_a_clean_run_says_so_in_the_first_line():
    head = _first(render_comment([PASSED, PASSED]))
    assert "no visual changes" in head
    assert "2 snapshots" in head


def test_an_empty_run_is_not_reported_as_success():
    """Ноль снимков — это не «всё хорошо», это «проверки не было».

    Самый неприятный из возможных обманов: тесты не сняли ничего (сломался
    селектор, упал браузер), а комментарий зелёный.
    """
    head = _first(render_comment([]))
    assert "nothing was compared" in head
    assert "no visual changes" not in head


def test_errors_alone_do_not_read_as_a_clean_run():
    head = _first(render_comment([PASSED, ERRORED]))
    assert "could not be checked" in head


def test_failures_outrank_errors_in_the_headline():
    """Есть и падения, и ошибки — первой строкой идут падения.

    Ошибка съёмки это работа инструмента, регресс это работа человека.
    """
    head = _first(render_comment([FAILED, ERRORED]))
    assert "changed" in head
    assert "could not be checked" not in head


# --------------------------------------------------------------------------- #
#  Разобранное падение — не падение
# --------------------------------------------------------------------------- #
def test_an_accepted_failure_does_not_count_as_a_change():
    """Решение уже принято человеком; повторить его вопросом — шум."""
    accepted = dict(FAILED, review="approved", reviewed_by="cena")
    body = render_comment([accepted, PASSED])
    assert "no visual changes" in _first(body)
    assert "1 already accepted as normal by cena" in body


def test_new_baselines_are_mentioned_but_not_counted_as_changes():
    body = render_comment([FRESH, PASSED])
    assert "no visual changes" in _first(body)
    assert "1 new baseline" in body
    assert "nothing to compare against yet" in body


# --------------------------------------------------------------------------- #
#  Группировка по причине
# --------------------------------------------------------------------------- #
def test_one_cause_across_many_snapshots_is_one_question():
    """Двадцать красных снимков от одного отступа — один вопрос, не двадцать.

    Показать двадцать строк вместо одной значит заставить человека
    восстанавливать группировку самому.
    """
    many = [dict(FAILED, name=f"page{i}.png") for i in range(20)]
    clusters = [{"kind": "shift", "snapshot_count": 20, "max_severity": 44.0,
                 "description": "the header moved down by 8px",
                 "selectors": [".header"], "common": ".header",
                 "advice": ""}]
    body = render_comment(many, clusters=clusters)
    assert "1 question, not 20 snapshots" in body
    assert "the header moved down by 8px" in body


def test_a_cause_seen_in_a_single_snapshot_is_not_a_group():
    """Группа из одного — это обычная строка таблицы, а не отдельный раздел."""
    clusters = [{"kind": "shift", "snapshot_count": 1, "max_severity": 44.0,
                 "description": "the header moved", "selectors": [".header"]}]
    body = render_comment([FAILED], clusters=clusters)
    assert "Grouped by cause" not in body


def test_the_list_of_groups_is_cut_off():
    clusters = [{"kind": "shift", "snapshot_count": 3, "max_severity": 10.0,
                 "description": f"cause {i}", "selectors": []}
                for i in range(MAX_CLUSTERS + 4)]
    body = render_comment([FAILED], clusters=clusters)
    assert f"cause {MAX_CLUSTERS - 1}" in body
    assert f"cause {MAX_CLUSTERS}" not in body
    assert "and 4 more groups" in body


# --------------------------------------------------------------------------- #
#  Таблица
# --------------------------------------------------------------------------- #
def test_the_table_is_ordered_by_severity():
    """Первой строкой — то, что сильнее всего изменилось.

    Порядок «как пришло» означает порядок обхода файловой системы, то есть
    случайный: самое важное оказывается на двенадцатой строке или за обрезкой.
    """
    mild = dict(FAILED, name="mild.png", metrics={"max_severity": 5.0})
    bad = dict(FAILED, name="bad.png", metrics={"max_severity": 90.0})
    rows = [ln for ln in render_comment([mild, bad]).splitlines()
            if ln.startswith("| `")]
    assert "bad.png" in rows[0]
    assert "mild.png" in rows[1]


def test_the_table_is_cut_off_and_says_so():
    """Сто строк никто не пролистает, а молчаливая обрезка читается как «всё»."""
    many = [dict(FAILED, name=f"p{i}.png", metrics={"max_severity": 90 - i})
            for i in range(MAX_ROWS + 7)]
    body = render_comment(many)
    assert body.count("\n| `") == MAX_ROWS
    assert "and 7 more" in body


def test_a_pipe_in_a_name_does_not_break_the_table():
    """Разваленная таблица выглядит как поломка инструмента, а не как имя."""
    body = render_comment([dict(FAILED, name="weird|name.png")])
    row = next(ln for ln in body.splitlines() if "weird" in ln)
    assert row.count("\\|") == 1                    # черта из имени экранирована
    assert row.count("|") - row.count("\\|") == 5   # пять разделителей, не шесть
    assert "weird\\|name.png" in row


def test_a_newline_in_a_selector_does_not_break_the_table():
    comp = dict(FAILED, regions=[{"kind": "shift", "selector": ".a\n.b"}])
    row = next(ln for ln in render_comment([comp]).splitlines()
               if ln.startswith("| `"))
    assert ".a .b" in row


def test_the_table_says_what_changed_not_just_that_it_did():
    body = render_comment([FAILED])
    row = next(ln for ln in body.splitlines() if ln.startswith("| `"))
    assert "shift" in row
    assert ".header" in row
    assert "0.400%" in row


# --------------------------------------------------------------------------- #
#  Ошибки
# --------------------------------------------------------------------------- #
def test_errors_get_their_own_section_with_the_reason():
    body = render_comment([ERRORED])
    assert "1 not checked" in body
    assert "page did not answer" in body


def test_errors_are_not_mixed_into_the_failure_table():
    """Сломанная проверка и найденный дефект — разные события."""
    body = render_comment([FAILED, ERRORED])
    rows = [ln for ln in body.splitlines() if ln.startswith("| `")]
    assert len(rows) == 1
    assert "login.png" in rows[0]


# --------------------------------------------------------------------------- #
#  Куда уедет «принять»
# --------------------------------------------------------------------------- #
def test_it_warns_that_accepting_lands_on_the_branch():
    """Без этой строки комментарий в MR советует принять — и молчит, в чей эталон.

    Появилось вместе с эталоном на ветку: человек принимает изменение в MR,
    ветка вливается, а базовый набор остаётся со старыми картинками и начинает
    падать снова — уже без всякого MR.
    """
    body = render_comment([FAILED], branch="feature/pay",
                          branch_baselines=True, default_branch="main")
    assert "lands on branch `feature/pay`" in body
    assert "promote the branch" in body


def test_no_branch_warning_when_branch_baselines_are_off():
    """Обещать разделение, которого нет, хуже, чем молчать."""
    body = render_comment([FAILED], branch="feature/pay",
                          branch_baselines=False, default_branch="main")
    assert "lands on branch" not in body


def test_no_branch_warning_on_the_base_branch_itself():
    body = render_comment([FAILED], branch="main",
                          branch_baselines=True, default_branch="main")
    assert "lands on branch" not in body


def test_a_clean_run_gets_no_branch_warning():
    """Принимать нечего — предупреждать не о чем."""
    body = render_comment([PASSED], branch="feature/pay",
                          branch_baselines=True, default_branch="main")
    assert "lands on branch" not in body


# --------------------------------------------------------------------------- #
#  Ссылки и мета
# --------------------------------------------------------------------------- #
def test_the_link_points_at_this_run_not_at_the_list():
    """Список показывает последние шестьдесят.

    Ссылка на список означает, что назавтра комментарий к позавчерашнему MR
    открывает чужой прогон, выглядящий как свой.
    """
    body = render_comment([FAILED], base_url="https://vt.local/", run_id=42)
    assert "https://vt.local/ui/#/runs/42" in body
    assert "https://vt.local/api/runs/42/report.html" in body


def test_without_a_public_url_there_are_no_broken_links():
    """Ссылка на `localhost` из ленты PR ведёт в никуда у каждого читателя."""
    body = render_comment([FAILED], run_key="20260826-1", base_url="")
    assert "http" not in body
    assert "20260826-1" in body


def test_meta_line_carries_branch_and_short_sha():
    body = render_comment([FAILED], branch="feature/pay",
                          git_sha="0123456789abcdef", platform="chromium-1x")
    assert "`feature/pay`" in body
    assert "`01234567`" in body
    assert "0123456789abcdef" not in body
    assert "chromium-1x" in body


def test_it_survives_a_comparison_straight_from_the_database():
    """Второй источник данных: плоские колонки и `meta` строкой с JSON."""
    row = {"snapshot_name": "db.png", "verdict": "fail", "max_severity": 30.0,
           "changed_area_pct": 1.5, "meta": '{"error": null}', "regions": []}
    body = render_comment([row])
    assert "db.png" in body
    assert "1.500%" in body


def test_the_body_ends_with_exactly_one_newline():
    """`gh pr comment --body-file` отдаёт файл как есть."""
    body = render_comment([FAILED])
    assert body.endswith("\n")
    assert not body.endswith("\n\n")
