# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Генерация page objects из записанной сессии.

Зачем это вообще. Плоский сгенерированный тест с CSS-путями внутри читается
один раз — в момент генерации. Через месяц вёрстка меняется, двадцать тестов
краснеют, и правки приходится вносить в двадцати местах. Именно на этом
умирают записанные тесты во всех рекордерах: их проще перезаписать, чем
починить, а перезапись теряет всё, что человек дописал руками.

Page object решает ровно эту проблему: локатор живёт в одном месте, тест
говорит о намерении. Поэтому здесь генерируется не «код, который запускается»,
а код, который **примут на ревью** и станут поддерживать.

Локаторы выбираются по прочности, а не по удобству генератора:

1. `data-testid` — переживает и перевёрстку, и перевод интерфейса
2. роль + доступное имя — переживает перевёрстку, ломается при переводе
3. CSS-путь — не переживает ничего, поэтому идёт последним и с комментарием

Правки человека при пересборке сохраняются: блок локаторов размечен маркерами,
и генератор **дописывает** в него новое, а не переписывает целиком.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# --------------------------------------------------------------------------- #
#  Маркеры блока локаторов
# --------------------------------------------------------------------------- #
LOC_BEGIN = "    # --- locators: generated, your edits are preserved ---"
LOC_END = "    # --- end of locators ---"

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Роль → суффикс имени атрибута. Читается как «кнопка отправки», а не «элемент 3».
_ROLE_SUFFIX = {
    "button": "button", "link": "link", "textbox": "field",
    "searchbox": "search", "checkbox": "checkbox", "radio": "radio",
    "combobox": "select", "spinbutton": "field", "heading": "heading",
    "img": "image", "tab": "tab", "menuitem": "menu_item",
}


def translit(text: str) -> str:
    out = []
    for ch in str(text).lower():
        out.append(_TRANSLIT.get(ch, ch))
    return "".join(out)


def safe_doc(text: str) -> str:
    """Текст со страницы → безопасная докстрока.

    Названия кнопок приходят с чужого сайта и содержат что угодно, включая
    кавычки и обратные слэши. Один такой символ превращает сгенерированный
    файл в синтаксическую ошибку, поэтому фильтруем на входе, а не надеемся.
    """
    cleaned = str(text).replace("\\", "").replace('"', "'")
    return " ".join(cleaned.split())[:120]


def ident(text: str, fallback: str = "element") -> str:
    """Строка → допустимый и читаемый идентификатор Python."""
    slug = re.sub(r"[^a-z0-9]+", "_", translit(text)).strip("_")
    if not slug:
        return fallback
    if slug[0].isdigit():
        slug = f"_{slug}"
    return slug[:48]


def class_name_for(url: str) -> str:
    path = (urlparse(url).path or "/").strip("/")
    slug = ident(path or "index", "index")
    return "".join(p.capitalize() for p in slug.split("_") if p) + "Page"


def module_name_for(url: str) -> str:
    path = (urlparse(url).path or "/").strip("/")
    return ident(path or "index", "index") + "_page"


# --------------------------------------------------------------------------- #
#  Элемент
# --------------------------------------------------------------------------- #
@dataclass
class Element:
    attr: str                    # имя свойства в классе
    kind: str                    # testid | role | css
    value: str = ""              # значение testid либо CSS-селектор
    testid_attr: str = "data-testid"
    role: str = ""
    name: str = ""
    comment: str = ""

    def expression(self) -> str:
        """Код локатора для Playwright."""
        if self.kind == "testid":
            if self.testid_attr == "data-testid":
                return f'self.page.get_by_test_id({_q(self.value)})'
            # Нестандартный атрибут: get_by_test_id читает только data-testid,
            # пока проект не переопределил testIdAttribute. CSS надёжнее.
            return f'self.page.locator({_q(f"[{self.testid_attr}={_attr_q(self.value)}]")})'
        if self.kind == "role":
            return (f'self.page.get_by_role({_q(self.role)}, '
                    f'name={_q(self.name)})')
        return f'self.page.locator({_q(self.value)})'

    def render(self) -> str:
        lines = ["    @property",
                 f"    def {self.attr}(self):"]
        if self.comment:
            lines.append(f'        """{safe_doc(self.comment)}"""')
        lines.append(f"        return {self.expression()}")
        return "\n".join(lines)


def element_from_step(step: dict, used: set[str]) -> Element | None:
    """Паспорт элемента из шага записи → локатор.

    Если рекордер паспорт не приложил (старая запись, шаг набран руками),
    честно падаем на CSS-селектор — это по-прежнему рабочий page object,
    просто менее прочный.
    """
    selector = step.get("selector")
    meta = step.get("element") or {}
    if not selector and not meta:
        return None

    testid = meta.get("testid")
    role = meta.get("role")
    name = meta.get("name")
    tag = meta.get("tag") or ""

    if testid:
        attr = _unique(ident(testid, tag or "element"), used)
        return Element(attr=attr, kind="testid", value=testid,
                       testid_attr=meta.get("testid_attr") or "data-testid",
                       comment=name or meta.get("text") or "")

    if role and name and meta.get("role_unique"):
        suffix = _ROLE_SUFFIX.get(role, ident(role, "element"))
        base = ident(name, role)
        attr = _unique(base if base.endswith(suffix) else f"{base}_{suffix}", used)
        return Element(attr=attr, kind="role", role=role, name=name,
                       comment=f"{role}: {name}")

    if not selector:
        return None

    base = ident(name or meta.get("text") or tag or "element", "element")
    suffix = _ROLE_SUFFIX.get(role or "", "")
    attr = _unique(f"{base}_{suffix}" if suffix and not base.endswith(suffix)
                   else base, used)
    return Element(
        attr=attr, kind="css", value=selector,
        comment=("CSS path: the most fragile locator. Give the element a "
                 "data-testid and a rebuild will replace it automatically"
                 if _looks_fragile(selector) else (name or "")),
    )


def _looks_fragile(selector: str) -> bool:
    return ">" in selector or ":nth-of-type" in selector


# --------------------------------------------------------------------------- #
#  Страница
# --------------------------------------------------------------------------- #
@dataclass
class PageObject:
    class_name: str
    module: str
    url: str
    path: str = "/"
    elements: list[Element] = field(default_factory=list)
    methods: list[tuple[str, list[str], str]] = field(default_factory=list)
    # methods: (имя, строки тела, докстрока)

    def needs_os(self) -> bool:
        return any("os.environ" in line
                   for _, lines, _ in self.methods for line in lines)

    def render(self) -> str:
        head = (
            f'"""Page object: {self.path}\n\n'
            f"Generated by vistest {datetime.now().strftime('%Y-%m-%d %H:%M')}.\n"
            "Rebuild: python run.py codegen --pom\n\n"
            "The file is meant to be edited by hand. The locator block is\n"
            "marked out: the generator appends new entries to it and does not\n"
            'touch what you changed. Everything outside the block is left alone.\n'
            '"""\n'
        )
        head += "\nimport os\n" if self.needs_os() else ""
        head += "\n\n"
        body = [f"class {self.class_name}:",
                f'    """{safe_doc(self.path)}"""',
                "",
                f"    PATH = {_q(self.path)}",
                "",
                "    def __init__(self, page):",
                "        self.page = page",
                "",
                LOC_BEGIN]
        if self.elements:
            for el in self.elements:
                body.append("")
                body.append(el.render())
        else:
            body.append("    # no elements recorded")
        body.append("")
        body.append(LOC_END)

        body.append("")
        body.append("    def open(self, base_url=\"\"):")
        body.append('        """Open the page. Returns self, so calls can be chained."""')
        body.append("        self.page.goto(base_url + self.PATH)")
        body.append("        return self")

        for meth, lines, doc in self.methods:
            body.append("")
            body.append(f"    def {meth}(self):")
            body.append(f'        """{safe_doc(doc)}"""')
            body.extend(lines or ["        pass"])
            body.append("        return self")

        return head + "\n".join(body).rstrip() + "\n"


# --------------------------------------------------------------------------- #
#  Сборка
# --------------------------------------------------------------------------- #
def build_pages(items: list[dict]) -> dict[str, PageObject]:
    """items — те же записи, что использует codegen (url, steps, selector…)."""
    pages: dict[str, PageObject] = {}

    for item in items:
        url = item.get("url") or ""
        if not url:
            continue
        key = _page_key(url)
        po = pages.get(key)
        if po is None:
            po = PageObject(class_name=class_name_for(url),
                            module=module_name_for(url),
                            url=url, path=_path_of(url))
            pages[key] = po

        used = {e.attr for e in po.elements}
        by_selector = {e.value: e for e in po.elements if e.kind == "css"}

        method_lines: list[str] = []
        for step in item.get("steps") or []:
            el = _element_for(step, po, used, by_selector)
            line = _action_line(step, el)
            if line:
                method_lines.append(line)

        if method_lines:
            name, doc = _method_name(item.get("steps") or [], po)
            if not any(m[0] == name for m in po.methods):
                po.methods.append((name, method_lines, doc))

        # Селектор снимка (clip_selector) тоже заслуживает локатора.
        if item.get("selector"):
            _element_for({"selector": item["selector"],
                          "element": item.get("selector_element") or {}},
                         po, used, by_selector)

    return pages


def _element_for(step, po: PageObject, used: set[str], by_selector: dict):
    sel = step.get("selector")
    if sel and sel in by_selector:
        return by_selector[sel]
    el = element_from_step(step, used)
    if el is None:
        return None
    # Один и тот же элемент, записанный дважды, не должен дать два свойства.
    for existing in po.elements:
        if existing.kind == el.kind and existing.value == el.value \
                and existing.role == el.role and existing.name == el.name:
            return existing
    po.elements.append(el)
    if el.kind == "css":
        by_selector[el.value] = el
    return el


def _action_line(step: dict, el: Element | None) -> str:
    action = step.get("action")
    value = step.get("value")
    ref = f"self.{el.attr}" if el else None

    if action == "goto":
        return f"        self.page.goto({_q(step.get('url') or '')})"
    if action == "wait":
        return f"        self.page.wait_for_timeout({int(step.get('ms') or 500)})"
    if not ref:
        return ""
    if action == "click":
        return f"        {ref}.click()"
    if action in ("fill", "type"):
        return f"        {ref}.fill({_value_expr(value)})"
    if action == "press":
        return f"        {ref}.press({_q(str(value or 'Enter'))})"
    if action == "select":
        return f"        {ref}.select_option({_value_expr(value)})"
    if action == "check":
        return f"        {ref}.check()"
    if action == "uncheck":
        return f"        {ref}.uncheck()"
    if action == "hover":
        return f"        {ref}.hover()"
    if action == "wait_for":
        return f"        {ref}.wait_for()"
    return ""


def _method_name(steps: list[dict], po: PageObject) -> tuple[str, str]:
    """Имя метода по смыслу шагов, а не по их номеру."""
    if any(str(s.get("value", "")).find("PASSWORD") >= 0 for s in steps):
        return "login", "Log in. The values come from environment variables."
    clicks = [s for s in steps if s.get("action") == "click"]
    if clicks:
        meta = clicks[-1].get("element") or {}
        label = meta.get("name") or meta.get("text")
        if label:
            return (_unique(f"open_{ident(label, 'section')}",
                            {m[0] for m in po.methods}),
                    f"Bring the page to the “{label}” state.")
    return (_unique("prepare", {m[0] for m in po.methods}),
            "Bring the page to the state in which the baseline is captured.")


# --------------------------------------------------------------------------- #
#  Запись на диск с сохранением ручных правок
# --------------------------------------------------------------------------- #
def write_page_objects(pages: dict[str, PageObject],
                       out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    init = out / "__init__.py"
    if not init.exists():
        init.write_text('"""Page objects generated by vistest."""\n',
                        encoding="utf-8")

    written: list[Path] = []
    for po in pages.values():
        path = out / f"{po.module}.py"
        code = po.render()
        if path.exists():
            code = merge_locators(path.read_text("utf-8"), code)
        if not path.exists() or path.read_text("utf-8") != code:
            path.write_text(code, encoding="utf-8")
        written.append(path)
    return written


def merge_locators(existing: str, generated: str) -> str:
    """Сохранить правки человека в блоке локаторов.

    Правило простое и предсказуемое: всё, что человек написал в блоке,
    остаётся как есть; из сгенерированного добавляются только те свойства,
    имён которых в блоке ещё нет. Никакого умного трёхстороннего слияния —
    оно непредсказуемо, а непредсказуемый генератор кода хуже отсутствующего.

    Если маркеров в файле нет (человек их убрал), файл не трогается вообще.
    """
    old_block = _extract_block(existing)
    new_block = _extract_block(generated)
    if old_block is None:
        return existing
    if new_block is None:
        return existing

    have = set(_property_names(old_block))
    additions = [chunk for chunk in _property_chunks(new_block)
                 if _first_property_name(chunk) not in have]

    # Отступы нормализуются, иначе каждая пересборка добавляла бы по пустой
    # строке и файл через десяток прогонов раздувался бы пробелами.
    parts = [old_block.strip("\n")] + [c.strip("\n") for c in additions]
    merged = "\n\n".join(p for p in parts if p.strip())

    head, _, rest = existing.partition(LOC_BEGIN)
    _, _, tail = rest.partition(LOC_END)
    return f"{head}{LOC_BEGIN}\n\n{merged}\n\n{LOC_END}{tail}"


def _extract_block(text: str) -> str | None:
    if LOC_BEGIN not in text or LOC_END not in text:
        return None
    return text.split(LOC_BEGIN, 1)[1].split(LOC_END, 1)[0]


def _property_chunks(block: str) -> list[str]:
    """Блок → куски по одному свойству (декоратор + def + тело)."""
    chunks, current = [], []
    for line in block.splitlines():
        if line.strip() == "@property" and current:
            chunks.append("\n".join(current))
            current = [line]
        elif line.strip() == "@property":
            current = [line]
        elif current:
            current.append(line)
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c.strip()]


def _property_names(block: str) -> list[str]:
    return re.findall(r"^\s*def\s+([A-Za-z_]\w*)\s*\(", block, re.M)


def _first_property_name(chunk: str) -> str | None:
    names = _property_names(chunk)
    return names[0] if names else None


# --------------------------------------------------------------------------- #
def _page_key(url: str) -> str:
    p = urlparse(url)
    return f"{p.netloc}{p.path or '/'}"


def _path_of(url: str) -> str:
    p = urlparse(url)
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


def _unique(name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    i = 2
    while f"{name}_{i}" in used:
        i += 1
    used.add(f"{name}_{i}")
    return f"{name}_{i}"


def _q(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _attr_q(text: str) -> str:
    return '"' + str(text).replace('"', '\\"') + '"'


def _value_expr(value) -> str:
    """`${VAR}` → os.environ["VAR"]: секрет не должен попасть в git."""
    if value is None:
        return '""'
    text = str(value)
    m = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text.strip())
    if m:
        return f'os.environ["{m.group(1)}"]'
    if "${" in text:
        inner = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                       lambda mm: f'{{os.environ["{mm.group(1)}"]}}', text)
        return 'f"' + inner.replace('"', '\\"') + '"'
    return _q(text)
