# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Проверка генератора page objects.

Главное требование к сгенерированному коду — его должны принять на ревью.
Поэтому тесты проверяют не только «работает», но и «выглядит как написанное
руками»: прочные локаторы, осмысленные имена, отсутствие мусора.

Отдельно проверяется то, ради чего page object и вводился: пересборка не
затирает правки человека.
"""

from __future__ import annotations

import pytest

from vistest.record import pom


def _step(action="click", selector="div > button", element=None, **extra):
    return {"action": action, "selector": selector, "element": element, **extra}


def _item(url="https://shop.local/checkout", steps=None, **extra):
    return {"name": "checkout.png", "short": "checkout.png", "url": url,
            "steps": steps or [], "viewport": (1440, 900), **extra}


# --------------------------------------------------------------------------- #
#  Выбор локатора: прочность важнее удобства генератора
# --------------------------------------------------------------------------- #
def test_testid_wins_over_everything():
    el = pom.element_from_step(_step(element={
        "tag": "button", "testid": "checkout-submit", "testid_attr": "data-testid",
        "role": "button", "name": "Оформить заказ", "role_unique": True,
    }), used=set())

    assert el.kind == "testid"
    assert el.attr == "checkout_submit"
    assert el.expression() == 'self.page.get_by_test_id("checkout-submit")'


def test_nonstandard_testid_attribute_falls_back_to_css():
    """get_by_test_id читает только data-testid, пока проект не настроен иначе."""
    el = pom.element_from_step(_step(element={
        "tag": "button", "testid": "submit", "testid_attr": "data-qa",
    }), used=set())

    assert el.kind == "testid"
    assert el.expression() == 'self.page.locator("[data-qa=\\"submit\\"]")'


def test_role_used_when_unique():
    el = pom.element_from_step(_step(element={
        "tag": "button", "role": "button", "name": "Оформить заказ",
        "role_unique": True,
    }), used=set())

    assert el.kind == "role"
    assert el.expression() == (
        'self.page.get_by_role("button", name="Оформить заказ")')
    assert el.attr == "oformit_zakaz_button", "имя транслитерируется читаемо"


def test_role_not_used_when_ambiguous():
    """Неуникальная пара роль+имя дала бы strict mode violation в Playwright."""
    el = pom.element_from_step(_step(selector=".cart button", element={
        "tag": "button", "role": "button", "name": "Купить", "role_unique": False,
    }), used=set())

    assert el.kind == "css"
    assert el.value == ".cart button"


def test_css_fallback_without_element_passport():
    """Старые записи без паспорта элемента обязаны продолжать работать."""
    el = pom.element_from_step(_step(selector="#promo", element=None), used=set())

    assert el.kind == "css"
    assert el.expression() == 'self.page.locator("#promo")'


def test_fragile_css_is_flagged_in_code():
    el = pom.element_from_step(_step(selector="main > div:nth-of-type(3) > button"),
                               used=set())
    assert "data-testid" in el.comment, \
        "хрупкий локатор должен подсказывать, как его укрепить"


def test_attribute_names_do_not_collide():
    used: set[str] = set()
    a = pom.element_from_step(_step(element={"tag": "button", "testid": "submit"}),
                              used)
    b = pom.element_from_step(_step(element={"tag": "input", "testid": "submit"}),
                              used)
    assert a.attr != b.attr


# --------------------------------------------------------------------------- #
#  Сборка страницы
# --------------------------------------------------------------------------- #
def test_page_class_and_module_names():
    assert pom.class_name_for("https://shop.local/checkout") == "CheckoutPage"
    assert pom.module_name_for("https://shop.local/checkout") == "checkout_page"
    assert pom.class_name_for("https://shop.local/") == "IndexPage"
    assert pom.class_name_for("https://shop.local/user/cart") == "UserCartPage"


def test_same_element_recorded_twice_gives_one_property():
    steps = [
        _step("click", element={"tag": "button", "testid": "submit"}),
        _step("click", element={"tag": "button", "testid": "submit"}),
    ]
    pages = pom.build_pages([_item(steps=steps)])
    po = next(iter(pages.values()))

    assert len(po.elements) == 1


def test_login_flow_becomes_login_method():
    steps = [
        _step("fill", selector="#user", value="${VISTEST_USER}",
              element={"tag": "input", "testid": "login-user"}),
        _step("fill", selector="#pass", value="${VISTEST_PASSWORD}",
              element={"tag": "input", "testid": "login-pass"}),
        _step("click", element={"tag": "button", "testid": "login-submit"}),
    ]
    pages = pom.build_pages([_item(url="https://app.local/login", steps=steps)])
    po = next(iter(pages.values()))

    assert [m[0] for m in po.methods] == ["login"]
    code = po.render()
    assert 'os.environ["VISTEST_PASSWORD"]' in code
    assert "VISTEST_PASSWORD" not in code.replace(
        'os.environ["VISTEST_PASSWORD"]', ""), "пароль не должен утечь в код"
    assert "import os" in code


def test_no_os_import_when_not_needed():
    pages = pom.build_pages([_item(steps=[
        _step("click", element={"tag": "button", "testid": "go"})])])
    code = next(iter(pages.values())).render()
    assert "import os" not in code, "неиспользуемый импорт подчеркнёт линтер"


# --------------------------------------------------------------------------- #
#  Сгенерированный код должен быть валидным Python
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("element", [
    {"tag": "button", "testid": "submit"},
    {"tag": "button", "role": "button", "name": 'Кнопка с "кавычками"',
     "role_unique": True},
    {"tag": "div", "name": "Раздел «Доставка»"},
    None,
])
def test_generated_code_compiles(element):
    steps = [_step("click", selector='a[href="/x"]', element=element)]
    pages = pom.build_pages([_item(steps=steps)])
    code = next(iter(pages.values())).render()
    compile(code, "<generated>", "exec")


def test_generated_code_has_markers():
    pages = pom.build_pages([_item(steps=[_step()])])
    code = next(iter(pages.values())).render()
    assert pom.LOC_BEGIN in code
    assert pom.LOC_END in code


# --------------------------------------------------------------------------- #
#  Пересборка не должна терять правки человека
# --------------------------------------------------------------------------- #
def test_merge_keeps_manual_edits():
    pages = pom.build_pages([_item(steps=[
        _step("click", element={"tag": "button", "testid": "submit"})])])
    original = next(iter(pages.values())).render()

    # Человек починил локатор руками.
    edited = original.replace(
        'self.page.get_by_test_id("submit")',
        'self.page.get_by_test_id("submit").first  # чинил вручную')
    assert edited != original

    merged = pom.merge_locators(edited, original)
    assert "чинил вручную" in merged
    assert merged.count("def submit(") == 1, "свойство не должно задвоиться"


def test_merge_adds_new_properties():
    one = pom.build_pages([_item(steps=[
        _step("click", element={"tag": "button", "testid": "submit"})])])
    two = pom.build_pages([_item(steps=[
        _step("click", element={"tag": "button", "testid": "submit"}),
        _step("click", element={"tag": "a", "testid": "back"}),
    ])])

    old_code = next(iter(one.values())).render()
    new_code = next(iter(two.values())).render()

    merged = pom.merge_locators(old_code, new_code)
    assert "def submit(" in merged
    assert "def back(" in merged
    compile(merged, "<merged>", "exec")


def test_merge_does_not_touch_file_without_markers():
    """Человек убрал маркеры — значит взял файл под свой контроль."""
    manual = "class CheckoutPage:\n    pass\n"
    pages = pom.build_pages([_item(steps=[_step()])])
    assert pom.merge_locators(manual, next(iter(pages.values())).render()) == manual


def test_write_page_objects_creates_package(tmp_path):
    pages = pom.build_pages([_item(steps=[
        _step("click", element={"tag": "button", "testid": "submit"})])])
    written = pom.write_page_objects(pages, tmp_path / "pages")

    assert (tmp_path / "pages" / "__init__.py").exists()
    assert written and written[0].exists()
    compile(written[0].read_text("utf-8"), str(written[0]), "exec")


def test_rewrite_preserves_edits_end_to_end(tmp_path):
    """Полный цикл: сгенерировали, поправили руками, пересобрали."""
    out = tmp_path / "pages"
    pages = pom.build_pages([_item(steps=[
        _step("click", element={"tag": "button", "testid": "submit"})])])
    path = pom.write_page_objects(pages, out)[0]

    text = path.read_text("utf-8").replace(
        'get_by_test_id("submit")', 'get_by_test_id("submit-v2")')
    path.write_text(text, encoding="utf-8")

    pom.write_page_objects(pages, out)          # пересборка
    assert 'get_by_test_id("submit-v2")' in path.read_text("utf-8")
