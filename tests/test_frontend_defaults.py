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


def test_the_navigation_counter_covers_the_whole_installation():
    """Счётчик в меню отвечает на «есть ли эталоны вообще», а не «что открыто».

    Он считался по `/api/baselines/detail` без параметров — то есть по одному
    собственному набору сервиса, — и рядом с подключённым проектом на
    одиннадцать эталонов в меню стоял ноль.
    """
    shell = (JS.parent / "shell.js").read_text(encoding="utf-8")
    line = next(ln for ln in shell.splitlines()
                if "state.counts.baselines=" in ln.replace(" ", ""))
    assert "/api/baselines/scopes" in shell
    assert "scopes" in line or "sc.scopes" in shell
    assert not re.search(r"api\('/api/baselines/detail'\)", shell), \
        "счётчик снова считает один набор вместо всей установки"


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
