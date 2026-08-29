# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Откуда фикстура берёт значение.

Задача узкая и появилась из живого случая. Прогон подключённого набора упал
девять раз подряд на setup, в шапке ошибки стояло `user_pass = None`, и VisTest
честно говорил: «значение не доехало до прогона, заведите переменную». Верно —
но человек в этот момент всё равно идёт открывать чужой `conftest.py`, чтобы
узнать **какую именно**.

Мы можем узнать это за него. Проект лежит рядом, читается, и вопрос «откуда
берётся `user_pass`» решается разбором AST, а не выполнением кода. Выполнять
чужой conftest ради этого нельзя — он поднимает браузеры, ходит в сеть и вообще
делает что хочет.

Разбор намеренно неглубокий. Мы не строим граф вызовов и не разворачиваем цепочки
фикстур: цель — не «доказать», а «подсказать, куда смотреть». Если однозначного
ответа нет, честнее вернуть файл и строку, чем угадать неправильную переменную.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__",
             ".pytest_cache", ".idea", ".vscode", "dist", "build", ".tox",
             "site-packages"}

MAX_FILES = 400          # чужой монорепозиторий не должен вешать диагностику


@dataclass
class FixtureSource:
    """Где определена фикстура и откуда она берёт значение."""

    name: str
    file: str = ""
    line: int = 0
    kind: str = "unknown"       # env | option | unknown
    key: str = ""               # имя переменной среды или опции

    def describe(self) -> str:
        where = f"{self.file}:{self.line}" if self.file else "an unknown place"
        if self.kind == "env":
            return f"`{self.name}` ({where}) reads the variable {self.key}"
        if self.kind == "option":
            return f"`{self.name}` ({where}) reads the pytest option {self.key}"
        return (f"`{self.name}` is defined in {where}, the source of its value "
                "is not obvious")

    def advice(self) -> str:
        # The exact place is named on purpose. «Add the variable somewhere» sends
        # the person looking through three files; the section in the interface is
        # called «Secrets», and the file behind it is `secrets.env` next to the
        # rest of the service data.
        if self.kind == "env":
            return (f"add {self.key} in «Settings → Secrets» — the same as the "
                    f"line {self.key}=<value> in the service's secrets.env")
        if self.kind == "option":
            return f"add {self.key}=<value> to the project's pytest_args"
        return "open that file and see where the value comes from"


# --------------------------------------------------------------------------- #
def find_fixture_sources(root: str | Path, names) -> dict[str, FixtureSource]:
    """Имена фикстур → где определены и откуда берут значение.

    `conftest.py` просматриваются первыми: фикстуры почти всегда там, и на
    большом репозитории это экономит весь обход.
    """
    wanted = {str(n) for n in names if n}
    if not wanted:
        return {}

    root = Path(root)
    found: dict[str, FixtureSource] = {}
    for path in _candidates(root):
        try:
            tree = ast.parse(path.read_text("utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue

        module_level = _module_assignments(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in wanted or node.name in found:
                continue
            if not _looks_like_fixture(node):
                continue

            source = FixtureSource(
                name=node.name,
                file=_relative(path, root),
                line=node.lineno,
            )
            _fill_source(source, node, module_level)
            found[node.name] = source

        if len(found) == len(wanted):
            break
    return found


def _candidates(root: Path):
    """conftest.py по всему дереву, затем остальные модули."""
    conftests: list[Path] = []
    others: list[Path] = []
    seen = 0
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        seen += 1
        if seen > MAX_FILES:
            break
        (conftests if path.name == "conftest.py" else others).append(path)
    return conftests + others


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _looks_like_fixture(node) -> bool:
    """Декоратор, в котором встречается `fixture`.

    Проверяем по тексту декоратора, а не по объекту: `@pytest.fixture`,
    `@fixture`, `@pytest.fixture(scope="session")` и `@pytest_asyncio.fixture`
    — это всё одно и то же с точки зрения вопроса, который мы задаём.
    """
    for dec in node.decorator_list:
        if "fixture" in ast.dump(dec):
            return True
    return False


def _module_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    """Присваивания на уровне модуля — фикстура часто возвращает готовую константу."""
    out: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
    return out


def _fill_source(source: FixtureSource, node, module_level: dict) -> None:
    """Найти в теле фикстуры чтение окружения или опции pytest."""
    for expr in ast.walk(node):
        hit = _read_of(expr)
        if hit:
            source.kind, source.key = hit
            return

    # Фикстура вернула имя, вычисленное на уровне модуля: `return USER_PASS`.
    # Один шаг разворачивания — дальше начинается угадывание.
    for expr in ast.walk(node):
        if isinstance(expr, ast.Name) and expr.id in module_level:
            for inner in ast.walk(module_level[expr.id]):
                hit = _read_of(inner)
                if hit:
                    source.kind, source.key = hit
                    return


def _read_of(expr) -> tuple[str, str] | None:
    """`os.getenv("X")` / `os.environ["X"]` / `.getoption("--x")` → (вид, ключ)."""
    if isinstance(expr, ast.Call):
        name = _attr_name(expr.func)
        first = expr.args[0] if expr.args else None
        literal = _literal(first)
        if not literal:
            return None
        # Хвост важен, а не полный путь: `os.getenv`, `getenv`,
        # `request.config.getoption` и `pytestconfig.getoption` — одно и то же
        # обращение, записанное по-разному.
        if _tail_is(name, "getenv") or _tail_is(name, "environ.get"):
            return "env", literal
        if _tail_is(name, "getoption"):
            return "option", literal if literal.startswith("-") else f"--{literal}"
        return None

    # os.environ["X"] — подписка, а не вызов
    if isinstance(expr, ast.Subscript) and _tail_is(_attr_name(expr.value), "environ"):
        literal = _literal(expr.slice)
        if literal:
            return "env", literal
    return None


def _tail_is(name: str, suffix: str) -> bool:
    return name == suffix or name.endswith("." + suffix)


def _attr_name(node) -> str:
    """`os.environ.get` → "environ.get", `getenv` → "getenv"."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    parts.reverse()
    # Модуль в начале не важен: os.getenv и getenv — одно и то же обращение.
    if parts and parts[0] in ("os", "pytest", "request", "config",
                              "pytestconfig", "self"):
        parts = parts[1:]
    return ".".join(parts)


def _literal(node) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""
