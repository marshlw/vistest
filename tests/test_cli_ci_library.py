# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.


"""run.py: the commands declared in one place and forgotten in another (o1c:
split from server/tests/test_cli_ci.py, which keeps `vistest gate` and
`vistest comment`, the server's)."""

from __future__ import annotations


# --------------------------------------------------------------------------- #
#  run.py: two commands, declared in one place and forgotten in another
# --------------------------------------------------------------------------- #
def test_every_launcher_command_has_a_handler():
    """`run.py <команда>` обязан отвечать подсказкой, а не стеком.

    Здесь стояло два списка: цикл, объявляющий парсеры, и словарь обработчиков
    под ним. Расходились они молча и разошлись: команда объявлялась в парсере,
    забывалась в словаре, и `run.py matrix` печатал `KeyError: 'matrix'` со
    стеком. Для человека, который просто прочитал README, это выглядит как
    сломанный инструмент, а не как забытая строка.
    """
    import argparse
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("vistest_launcher",
                                                  root / "run.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    declared = {name for name, _ in launcher.PASSTHROUGH}
    assert declared, "список проксируемых команд пуст — проверка потеряла смысл"

    # Собираем парсер так же, как это делает `main()`, и спрашиваем у него,
    # какие команды он вообще знает.
    known = set()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    for name, help_ in launcher.PASSTHROUGH:
        sub.add_parser(name, help=help_)
        known.add(name)
    assert declared <= known

    # И главное: у каждой объявленной команды есть обработчик. Проверяется по
    # исходнику `main()`, потому что словарь собирается внутри него.
    source = (root / "run.py").read_text(encoding="utf-8")
    assert "for name, _ in PASSTHROUGH" in source, (
        "словарь обработчиков снова собирается вручную — он разойдётся "
        "с PASSTHROUGH так же, как разошёлся в прошлый раз")


def test_the_launcher_and_the_cli_agree_on_the_command_names():
    """Проксируемой команды может не быть в самом CLI — тогда она мертва.

    `run.py` объявит её, покажет в `--help` и передаст в `vistest.cli`, а тот
    ответит «invalid choice». Проверяется по исходнику: парсер CLI собирается
    внутри `main()`, и поднимать его целиком ради списка имён дороже, чем
    прочитать строки объявлений.
    """
    import importlib.util
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("vistest_launcher2",
                                                  root / "run.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    cli_source = "".join((root / pkg / "cli.py").read_text(encoding="utf-8")
                         for pkg in ("vistest", "server/vistest_server"))
    declared = set(re.findall(r'add_parser\(\s*"([\w-]+)"', cli_source))

    missing = {name for name, _ in launcher.PASSTHROUGH} - declared
    assert not missing, f"run.py проксирует несуществующие команды: {sorted(missing)}"
