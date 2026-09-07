"""Умолчания интерфейса: какой набор эталонов открывается, если не выбирали.

Экран «Baselines» открывался в собственном наборе сервиса всегда. У человека,
который только что подключил проект, собственный набор пуст, а одиннадцать
эталонов лежат в наборе проекта — и экран отвечал «0 baselines» ровно там, где
их видно на соседней вкладке.

Правило исправления узкое, и узость здесь важнее самого исправления: набор по
умолчанию остаётся набором по умолчанию, пока в нём хоть что-то есть.
Подменять непустой выбор «более полным» нельзя — это перекладывание экрана под
ногами.

Функция чистая, поэтому проверяется она как чистая: исходник берётся из того
самого файла, который отдаётся браузеру, и выполняется node. Сборщика у
интерфейса нет, второй копии правила в тестах — тоже быть не должно.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# Функция живёт в общем файле, а не на экране: её зовут два экрана, и вызов
# через границу экрана держится только на том, что оба файла доехали.
JS = Path(__file__).resolve().parents[1] / "frontend" / "js" / "shared.js"


def _extract(name: str) -> str:
    """Функция верхнего уровня из файла интерфейса, как она там написана.

    Копировать её сюда руками было бы худшим из вариантов: копия разойдётся с
    оригиналом молча, и тест начнёт подтверждать правило, которого в браузере
    уже нет.
    """
    text = JS.read_text(encoding="utf-8")
    start = text.index(f"function {name}(")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _call(scopes, want) -> str:
    node = shutil.which("node")
    if not node:                                            # pragma: no cover
        pytest.skip("node не установлен — проверять нечем")
    script = (_extract("nonEmptyScope")
              + f"\nprocess.stdout.write(String(nonEmptyScope("
                f"{json.dumps(scopes)}, {json.dumps(want)})));\n")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _scope(key, *counts):
    return {"scope": key,
            "platforms": [{"platform": f"p{i}", "count": c}
                          for i, c in enumerate(counts)]}


def test_an_empty_default_gives_way_to_a_non_empty_set():
    """Та самая жалоба: свой набор пуст, у проекта — одиннадцать."""
    scopes = [_scope("global", 0), _scope("project:aeron", 11)]
    assert _call(scopes, "global") == "project:aeron"


def test_a_non_empty_default_is_kept_even_next_to_a_fuller_one():
    """Иначе экран уезжает в чужой набор у человека, который ничего не нажимал."""
    scopes = [_scope("global", 3), _scope("project:aeron", 11)]
    assert _call(scopes, "global") == "global"


def test_everything_empty_stays_on_the_default():
    """Пустой экран своего набора хотя бы объясняет, что делать дальше."""
    scopes = [_scope("global", 0), _scope("project:aeron", 0)]
    assert _call(scopes, "global") == "global"


def test_an_unknown_default_is_not_second_guessed():
    """Списка нет — ответ прежний. Отсутствие данных не повод менять экран."""
    assert _call([], "global") == "global"
    assert _call(None, "global") == "global"


def test_the_fullest_of_the_non_empty_wins():
    scopes = [_scope("global", 0), _scope("project:a", 2), _scope("project:b", 9)]
    assert _call(scopes, "global") == "project:b"


def test_counts_are_summed_across_platforms():
    """Набор с эталонами на трёх платформах не «пуст» из-за одной пустой."""
    scopes = [_scope("global", 0, 0, 0), _scope("project:a", 0, 4, 0)]
    assert _call(scopes, "global") == "project:a"


def test_the_navigation_counter_follows_the_pinned_project():
    """Счётчик эталонов в меню отвечает про ЗАКРЕПЛЁННЫЙ проект.

    Он переезжал дважды, и оба раза по одной и той же причине — число в
    навигации не сходилось ни с одним экраном.

    Сначала он считался по `/api/baselines/detail` без параметров, то есть по
    собственному набору сервиса: рядом с подключённым проектом на одиннадцать
    эталонов в меню стоял ноль. Тогда его развернули на всю установку.

    После того как проект закрепился в шапке, «вся установка» стала неправдой
    с другой стороны: человек выбрал один проект, а в меню — сумма по всем.
    Считаем по тому набору, который открывается щелчком по самому пункту.
    """
    shell = (JS.parent / "shell.js").read_text(encoding="utf-8")
    line = next(ln for ln in shell.splitlines()
                if "state.counts.baselines=" in ln.replace(" ", ""))
    assert "/api/baselines/scopes" in shell
    assert "currentScope()" in shell, \
        "счётчик снова не знает про закреплённый проект"
    assert "scopeTotal" in line, \
        "счётчик снова суммирует наборы вместо одного открытого"
    assert not re.search(r"api\('/api/baselines/detail'\)", shell)


# --------------------------------------------------------------------------- #
#  Ключ платформы разбирается на вопросы, а не показывается как есть
# --------------------------------------------------------------------------- #
def _platform(name: str, *args) -> str:
    """Чистая функция из `shared.js`, выполненная node — как и `nonEmptyScope`.

    Копировать правило в тест нельзя по той же причине: копия разойдётся с
    оригиналом молча и начнёт подтверждать то, чего в браузере уже нет.
    """
    node = shutil.which("node")
    if not node:                                            # pragma: no cover
        pytest.skip("node не установлен — проверять нечем")
    src = "".join(_extract(f) for f in ("parsePlatform", "viewportLabel",
                                        "viewportKind"))
    call = ", ".join(json.dumps(a) for a in args)
    script = (src + f"\nprocess.stdout.write(JSON.stringify({name}({call})));\n")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_storage_key_splits_into_machine_engine_and_window():
    """`docker-chromium-1x-390x844` — четыре ответа в одной строке.

    Спрашивают их по одному, поэтому и переключателя на экране два.
    """
    got = _platform("parsePlatform", "linux-chromium-1x-390x844")
    assert got["parsed"] and got["os"] == "linux"
    assert got["browser"] == "chromium" and got["scale"] == "1x"
    assert got["viewport"] == "390x844" and got["w"] == 390


def test_the_base_window_size_has_no_suffix_and_that_is_not_an_omission():
    """Под ключом без суффикса лежит всё, что снято до появления матрицы.

    См. `vistest/matrix.py`: переезд обесценил бы эти эталоны разом. Значит
    пустой `viewport` — это «базовый размер», а не «размер неизвестен».
    """
    got = _platform("parsePlatform", "docker-firefox-1x")
    assert got["parsed"] and got["browser"] == "firefox"
    assert got["viewport"] == "" and got["w"] == 0
    assert _platform("viewportLabel", "", "1440x900") == "1440\u00d7900"
    assert _platform("viewportLabel", "", "") == "base size"


def test_an_unparsed_key_is_shown_as_it_is_rather_than_guessed():
    """Ключ мог быть записан версией, знавшей другой набор движков.

    Отвечать «не знаю» на собственные данные — худшее из поведений; показать
    строку как есть — всегда правда.
    """
    got = _platform("parsePlatform", "нечто-непонятное")
    assert got["parsed"] is False and got["raw"] == "нечто-непонятное"
    assert got["browser"] == ""


def test_a_window_size_is_also_named_in_words():
    """«390x844» ничего не говорит тому, кто держит в голове «телефон».

    А решение «нужен ли мне этот вариант» принимается именно в этих словах.
    """
    assert _platform("viewportKind", 390) == "phone"
    assert _platform("viewportKind", 768) == "tablet"
    assert _platform("viewportKind", 1440) == "laptop"
    assert _platform("viewportKind", 1920) == "desktop"
    assert _platform("viewportKind", 0) == ""


# --------------------------------------------------------------------------- #
#  Живое обновление не подменяет открытый экран
# --------------------------------------------------------------------------- #
COLLAB = JS.parent / "collab.js"


def _live(view, hash_):
    node = shutil.which("node")
    if not node:                                            # pragma: no cover
        pytest.skip("node не установлен — проверять нечем")
    text = COLLAB.read_text(encoding="utf-8")
    start = text.index("function liveShouldRepaintRuns(")
    end = text.index("\n}\n", start) + len("\n}\n")
    script = (text[start:end]
              + f"\nprocess.stdout.write(String(liveShouldRepaintRuns("
                f"{json.dumps(view)}, {json.dumps(hash_)})));\n")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout == "true"


def test_the_run_list_is_refreshed_while_it_is_open():
    """Ради этого обновление и существует: новый прогон виден сам."""
    assert _live("runs", "#/runs")
    assert _live("runs", "")
    assert _live("runs", "#/runs/")


def test_an_open_run_is_never_replaced_by_the_list():
    """Жалоба: сидишь в разборе прогона, и тебя выбрасывает обратно.

    `state.view` — первый сегмент адреса, и у `#/runs/42` он тоже `runs`. На
    странице прогона строк списка нет ни одной, обновление считало это
    расхождением со свежими данными и рисовало список поверх открытого
    прогона — раз в двенадцать секунд, без единого действия человека.
    """
    assert not _live("runs", "#/runs/42")
    assert not _live("runs", "#/runs/ui-smoke-1")


def test_other_screens_are_left_alone():
    assert not _live("baselines", "#/baselines")
    assert not _live("compare", "#/compare/7")
