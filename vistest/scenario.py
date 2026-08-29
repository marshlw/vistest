# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Шаги сценария: авторизация и действия перед снимком.

Без этого визуальное тестирование покрывает только публичные страницы —
то есть примерно ничего в реальном продукте. Личный кабинет, корзина,
админка, любая форма после логина недоступны.

Шаг — простой словарь, чтобы его можно было и записать мышью, и отредактировать
в UI, и положить в YAML, и сгенерировать в код:

    steps:
      - {action: goto,        url: "${BASE_URL}/login"}
      - {action: fill,        selector: "#username", value: "${VISTEST_USER}"}
      - {action: fill,        selector: "#password", value: "${VISTEST_PASSWORD}"}
      - {action: click,       selector: "button[type=submit]"}
      - {action: wait_for,    selector: ".user-menu"}
      - {action: goto,        url: "${BASE_URL}/checkout"}

Три вещи, ради которых это сделано именно так:

1. **Пароли не хранятся.** Значение `${VISTEST_PASSWORD}` подставляется из
   переменной окружения в момент выполнения. В `meta.json`, в git и в UI
   остаётся только имя переменной.

2. **Логин пишется один раз.** Последовательность выносится в `flows` в
   `vistest.yaml` и подключается шагом `{action: flow, name: login}`.
   Тридцать снимков за логином не означают тридцать копий формы входа.

3. **Логин выполняется один раз за прогон.** После успешного flow состояние
   браузера (куки, localStorage) сохраняется и переиспользуется. Иначе прогон
   из пятидесяти снимков — это пятьдесят авторизаций, минуты времени и
   гарантированный бан по rate-limit.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Что умеет исполнитель. Список намеренно короткий: это не замена
# функциональным тестам, а способ довести страницу до нужного состояния.
ACTIONS = {
    "goto": "go to",
    "click": "click",
    "fill": "clear and type text",
    "type": "type text character by character",
    "press": "press a key",
    "select": "pick a value in <select>",
    "check": "tick the checkbox",
    "uncheck": "untick the checkbox",
    "hover": "hover the cursor",
    "scroll": "scroll to an element or by N pixels",
    "wait": "pause, ms",
    "wait_for": "wait for an element to appear",
    "wait_for_url": "wait for a URL (may contain * )",
    "eval": "run JS",
    "flow": "run a named scenario from vistest.yaml",
}

_PLACEHOLDER = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}"
    r"|\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_SECRET_HINT = re.compile(r"pass|secret|token|otp|pin|cvv", re.I)


class StepError(RuntimeError):
    """Шаг не выполнился. Содержит номер и описание, чтобы было понятно где."""

    def __init__(self, index: int, step: dict, cause: Exception):
        self.index = index
        self.step = step
        self.cause = cause
        super().__init__(f"step {index + 1} ({describe(step)}): "
                         f"{type(cause).__name__}: {cause}")


# --------------------------------------------------------------------------- #
#  Подстановка значений
# --------------------------------------------------------------------------- #
def resolve(value: Any, extra: dict | None = None) -> Any:
    """`${VAR}` и `{{VAR}}` → переменные окружения (или extra).

    Отсутствующая переменная НЕ заменяется на пустую строку: молча ввести
    пустой пароль и получить непонятный дифф — худший из возможных исходов.
    """
    if not isinstance(value, str):
        return value

    missing: list[str] = []

    def sub(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        if extra and name in extra:
            return str(extra[name])
        val = os.getenv(name)
        if val is None:
            missing.append(name)
            return m.group(0)
        return val

    out = _PLACEHOLDER.sub(sub, value)
    if missing:
        raise RuntimeError(
            f"environment variables are not set: {', '.join(sorted(set(missing)))}. "
            f"Set them or put them in .vistest/secrets.env"
        )
    return out


def is_secret(step: dict) -> bool:
    if step.get("secret"):
        return True
    return bool(_SECRET_HINT.search(str(step.get("selector", ""))
                                    + str(step.get("value", ""))))


def describe(step: dict, *, mask: bool = True) -> str:
    """Человекочитаемое описание шага. Секреты маскируются."""
    action = step.get("action", "?")
    target = step.get("selector") or step.get("url") or ""
    value = step.get("value", "")
    if value and mask and is_secret(step):
        value = "••••••"
    parts = [action]
    if target:
        parts.append(str(target)[:70])
    if value != "":
        parts.append(f"= {str(value)[:40]}")
    if step.get("ms"):
        parts.append(f"{step['ms']} ms")
    return " ".join(parts)


def load_secrets(root: Path) -> int:
    """Подтянуть `.vistest/secrets.env` в окружение. Файл не должен быть в git."""
    path = Path(root) / "secrets.env"
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))
        count += 1
    return count


# --------------------------------------------------------------------------- #
#  Нормализация
# --------------------------------------------------------------------------- #
def normalize(raw) -> list[dict]:
    """Привести шаги к единому виду. Принимает список словарей или строк.

    Строчная форма — для быстрого набора руками:
        "goto https://app/login"
        "fill #username ${VISTEST_USER}"
        "click button[type=submit]"
    """
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [ln for ln in raw.splitlines() if ln.strip()]

    out: list[dict] = []
    for item in raw:
        step = _parse_line(item) if isinstance(item, str) else dict(item)
        action = str(step.get("action", "")).strip().lower()
        if action not in ACTIONS:
            raise ValueError(
                f"unknown action {action!r}. Available: {', '.join(sorted(ACTIONS))}")
        step["action"] = action
        if action in ("click", "fill", "type", "select", "check", "uncheck",
                      "hover", "wait_for") and not step.get("selector"):
            raise ValueError(f"action {action} needs a selector")
        if action == "goto" and not (step.get("url") or step.get("value")):
            raise ValueError("action goto needs a url")
        if action == "flow" and not step.get("name"):
            raise ValueError("action flow needs a name")
        out.append(step)
    return out


def _parse_line(line: str) -> dict:
    parts = line.strip().split(None, 2)
    action = parts[0].lower()
    if action == "goto":
        return {"action": "goto", "url": parts[1] if len(parts) > 1 else ""}
    if action == "wait":
        return {"action": "wait", "ms": int(parts[1]) if len(parts) > 1 else 500}
    if action == "flow":
        return {"action": "flow", "name": parts[1] if len(parts) > 1 else ""}
    step: dict = {"action": action}
    if len(parts) > 1:
        step["selector"] = parts[1]
    if len(parts) > 2:
        step["value"] = parts[2]
    return step


# --------------------------------------------------------------------------- #
#  Исполнение
# --------------------------------------------------------------------------- #
def execute(
    page,
    steps: list[dict],
    *,
    flows: dict[str, list] | None = None,
    timeout_ms: int = 15000,
    log: Callable[[str], None] | None = None,
    _depth: int = 0,
) -> None:
    """Выполнить шаги на странице Playwright."""
    if _depth > 3:
        raise RuntimeError("flow nesting is too deep (a cycle?)")

    flows = flows or {}
    say = log or (lambda _t: None)

    for i, step in enumerate(normalize(steps)):
        say(f"step {i + 1}: {describe(step)}")
        try:
            _run_step(page, step, flows=flows, timeout_ms=timeout_ms,
                      log=log, depth=_depth)
        except Exception as e:
            raise StepError(i, step, e) from e


def _run_step(page, step: dict, *, flows, timeout_ms, log, depth) -> None:
    action = step["action"]
    sel = step.get("selector")
    # В JS шага `eval` вполне законно встречаются шаблонные строки `${...}` —
    # подставлять в них переменные окружения нельзя.
    val = step.get("value") if action == "eval" else resolve(step.get("value"))
    to = int(step.get("timeout_ms") or timeout_ms)

    if action == "flow":
        name = step["name"]
        if name not in flows:
            raise KeyError(f"flow {name!r} is not described in vistest.yaml (flows section)")
        execute(page, flows[name], flows=flows, timeout_ms=timeout_ms,
                log=log, _depth=depth + 1)
        return

    if action == "goto":
        page.goto(resolve(step.get("url") or step.get("value")),
                  wait_until="domcontentloaded", timeout=to * 2)
    elif action == "click":
        page.click(sel, timeout=to)
    elif action == "fill":
        page.fill(sel, val or "", timeout=to)
    elif action == "type":
        page.click(sel, timeout=to)
        page.type(sel, val or "", delay=int(step.get("delay", 20)))
    elif action == "press":
        (page.press(sel, val, timeout=to) if sel
         else page.keyboard.press(val or "Enter"))
    elif action == "select":
        page.select_option(sel, val, timeout=to)
    elif action == "check":
        page.check(sel, timeout=to)
    elif action == "uncheck":
        page.uncheck(sel, timeout=to)
    elif action == "hover":
        page.hover(sel, timeout=to)
    elif action == "scroll":
        if sel:
            page.locator(sel).scroll_into_view_if_needed(timeout=to)
        else:
            page.evaluate("(y) => window.scrollTo(0, y)", int(step.get("value") or 0))
    elif action == "wait":
        page.wait_for_timeout(int(step.get("ms") or step.get("value") or 500))
    elif action == "wait_for":
        page.wait_for_selector(sel, timeout=to,
                               state=step.get("state", "visible"))
    elif action == "wait_for_url":
        page.wait_for_url(resolve(step.get("url") or step.get("value")), timeout=to)
    elif action == "eval":
        page.evaluate(step.get("value") or step.get("script") or "() => null")


# --------------------------------------------------------------------------- #
#  Авторизация с переиспользованием состояния
# --------------------------------------------------------------------------- #
def state_file(root: Path, flow_name: str) -> Path:
    return Path(root) / "state" / f"{flow_name}.json"


def fresh_state(root: Path, flow_name: str, ttl_minutes: int) -> Path | None:
    """Сохранённая сессия, если она ещё не протухла."""
    p = state_file(root, flow_name)
    if not p.exists():
        return None
    if ttl_minutes > 0 and (time.time() - p.stat().st_mtime) > ttl_minutes * 60:
        return None
    return p


def establish_auth(
    browser,
    cfg,
    flow_name: str,
    *,
    log: Callable[[str], None] | None = None,
    force: bool = False,
) -> Path | None:
    """Выполнить flow авторизации один раз и сохранить состояние браузера.

    Возвращает путь к storage_state, который дальше передаётся во все
    контексты. Пятьдесят снимков за логином не должны означать пятьдесят
    авторизаций: это минуты времени и верный способ поймать rate-limit.
    """
    say = log or (lambda _t: None)
    root = cfg.root_path
    steps = (cfg.flows or {}).get(flow_name)
    if not steps:
        return None

    if not force:
        cached = fresh_state(root, flow_name, cfg.auth.state_ttl_minutes)
        if cached:
            say(f"session {flow_name}: reusing the saved one ({cached.name})")
            return cached

    say(f"session {flow_name}: logging in")
    from .capture.stabilize import context_options, install

    context = browser.new_context(**context_options(cfg))
    install(context, determinism=cfg.capture.determinism, cfg=cfg)
    page = context.new_page()
    page.set_default_timeout(cfg.capture.step_timeout_ms)
    try:
        execute(page, steps, flows=cfg.flows,
                timeout_ms=cfg.capture.step_timeout_ms, log=say)
        out = state_file(root, flow_name)
        out.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(out))
        say(f"session {flow_name}: saved")
        return out
    finally:
        context.close()


def summarize(steps: list[dict]) -> str:
    """Короткая сводка для UI и логов."""
    steps = normalize(steps)
    if not steps:
        return "no steps"
    kinds: dict[str, int] = {}
    for s in steps:
        kinds[s["action"]] = kinds.get(s["action"], 0) + 1
    return ", ".join(f"{k}×{v}" if v > 1 else k for k, v in kinds.items())


def to_json(steps: list[dict]) -> str:
    return json.dumps(normalize(steps), ensure_ascii=False, indent=2)
