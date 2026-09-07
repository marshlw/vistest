# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Лицензионные ключи: подпись, сроки, лимиты и то, что происходит после.

Ключи здесь подписываются ТЕСТОВОЙ парой, вшитой прямо в файл. Это не
небрежность: подпись — это `pow(m, d, n)`, то есть три числа и одна строка
кода, и держать ради тестов настоящую крипто-библиотеку не нужно. Настоящая
пара живёт у владельца продукта и в репозиторий не попадает никогда.

Главное свойство, которое здесь закрепляется: истёкшая лицензия НЕ забирает у
заказчика его данные. Эталоны читаются, разбор работает, останавливается
только приём новых прогонов — и только после льготного срока.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from vistest import licensing

#  Тестовая пара RSA-2048. Открытая часть — (N, E), закрытая — D.
TEST_N = int(
    "2754904428753498349874630941958420888397307985888636090158245321451834"
    "1423642601969370059383893883351543225578344449730102800241882374779799"
    "3989688075744674365636244475720507232130053897231115032740033073518676"
    "9006097453675980906940482569802328252975837307065799068367770981245043"
    "6136072596343036611409324509116245523768417324520905939711353781833259"
    "6759364352057275798997710940343473829090456158310927693786427549497008"
    "6835789180369882741565403094059673563505871613589771900325286092917362"
    "7658790082276600391949044161590390838028816165086099172885148083878522"
    "118894224446039648652118537916648210058279539805903324809"
)
TEST_E = 65537
TEST_D = int(
    "7842000636375007776688920386606851226558429456756557453033728366318978"
    "8734419421249001807045826379123962745437967084538494711371047903124120"
    "3728371133413914678445299421213043421121232354211371666887664525844588"
    "0829646008674834339293555179661463711093082210349270567760952063813862"
    "5713185272605935502715512363033656848270669935607499480780050861026123"
    "9359843795756520520383774583939868543227903849125213304489646364226841"
    "2634459774695806503847830054531723055419276395394984841747023080744858"
    "9109624093977138207049586947791028159572842525516137658080214397123532"
    "66363850114556307283773682980555725627998755684280140279"
)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_key(**payload) -> str:
    """Подписать полезную нагрузку тестовым ключом.

    Ровно то же, что делает `scripts/issue_license.py`, только без внешней
    библиотеки: собираем блок PKCS#1 v1.5 и возводим в степень.
    """
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    size = (TEST_N.bit_length() + 7) // 8
    block = licensing._expected_block(hashlib.sha256(raw).digest(), size)
    signature = pow(int.from_bytes(block, "big"), TEST_D, TEST_N)
    return f"{_b64(raw)}.{_b64(signature.to_bytes(size, 'big'))}"


@pytest.fixture(autouse=True)
def _test_public_key(monkeypatch):
    """Подменяем публичный ключ продукта на тестовый — на время теста."""
    monkeypatch.setattr(licensing, "PUBLIC_MODULUS_HEX", format(TEST_N, "x"))
    monkeypatch.setattr(licensing, "PUBLIC_EXPONENT", TEST_E)
    monkeypatch.delenv(licensing.ENV_KEY, raising=False)


def _iso(days_from_today: int) -> str:
    return (date.today() + timedelta(days=days_from_today)).isoformat()


# --------------------------------------------------------------------------- #
#  Подпись
# --------------------------------------------------------------------------- #
def test_a_properly_signed_key_is_accepted():
    token = make_key(edition="pro", customer="ACME GmbH",
                     expires_at=_iso(365),
                     limits={"projects": 10, "users": 25})
    lic = licensing.parse(token)

    assert lic.signed and lic.edition == "pro"
    assert lic.customer == "ACME GmbH"
    assert lic.projects == 10 and lic.users == 25


def test_an_edited_payload_is_refused():
    """Самая очевидная попытка: поднять себе лимиты, оставив чужую подпись."""
    token = make_key(customer="ACME", limits={"projects": 1, "users": 2})
    payload_b64, signature = token.split(".")

    payload = json.loads(licensing._b64url_decode(payload_b64))
    payload["limits"]["projects"] = 9999
    forged = _b64(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode())

    with pytest.raises(licensing.LicenseError, match="edited"):
        licensing.parse(f"{forged}.{signature}")


def test_a_key_signed_by_someone_else_is_refused(monkeypatch):
    """Ключ, выпущенный чужой парой, — это не наш ключ."""
    token = make_key(customer="ACME")
    # Возвращаем настоящий публичный ключ продукта: тестовой подписи он не знает.
    monkeypatch.undo()
    with pytest.raises(licensing.LicenseError):
        licensing.parse(token)


def test_garbage_is_refused_without_crashing():
    for junk in ("", "hello", "a.b.c", "not-base64!.also-not"):
        with pytest.raises(licensing.LicenseError):
            licensing.parse(junk)


# --------------------------------------------------------------------------- #
#  Сроки
# --------------------------------------------------------------------------- #
def test_a_current_licence_is_simply_valid():
    lic = licensing.parse(make_key(customer="ACME", expires_at=_iso(30)))
    assert not lic.expired and not lic.in_grace and not lic.blocked
    assert lic.days_left() == 30


def test_a_perpetual_licence_never_expires():
    lic = licensing.parse(make_key(customer="ACME", expires_at=""))
    assert lic.perpetual and lic.days_left() is None
    assert not lic.expired and not lic.blocked


def test_just_after_the_term_the_installation_keeps_working():
    """Льготный срок — решение продукта, а не техническая деталь.

    Инструмент стоит внутри чужого CI: остановка в день окончания ломает
    чей-то релиз из-за неоплаченного счёта, и следующий разговор будет не про
    продление.
    """
    lic = licensing.parse(make_key(customer="ACME", expires_at=_iso(-3)))
    assert lic.expired and lic.in_grace and not lic.blocked


def test_after_the_grace_period_new_runs_stop():
    lic = licensing.parse(
        make_key(customer="ACME",
                 expires_at=_iso(-(licensing.GRACE_DAYS + 1))))
    assert lic.blocked


# --------------------------------------------------------------------------- #
#  Лимиты
# --------------------------------------------------------------------------- #
def test_limits_of_zero_mean_unlimited():
    lic = licensing.parse(make_key(customer="ACME",
                                   limits={"projects": 0, "users": 0}))
    assert lic.allows_projects(1000) and lic.allows_users(1000)


def test_limits_are_inclusive():
    lic = licensing.parse(make_key(customer="ACME",
                                   limits={"projects": 3, "users": 5}))
    assert lic.allows_projects(3) and not lic.allows_projects(4)
    assert lic.allows_users(5) and not lic.allows_users(6)


# --------------------------------------------------------------------------- #
#  Установка ключа и жизнь без него
# --------------------------------------------------------------------------- #
def test_without_a_key_the_free_tier_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path))
    lic = licensing.load(tmp_path)

    assert lic.edition == "community" and not lic.signed
    assert lic.projects == licensing.COMMUNITY["projects"]
    assert not lic.blocked, "бесплатный режим не может «истечь»"


def test_a_broken_key_falls_back_instead_of_locking_everyone_out(tmp_path):
    """Испорченный ключ не должен запирать человека из его же инсталляции."""
    (tmp_path / licensing.KEY_FILENAME).write_text("nonsense", encoding="utf-8")
    lic = licensing.load(tmp_path)

    assert lic.edition == "community"
    assert "could not be verified" in lic.note


def test_installing_a_key_writes_it_and_returns_it(tmp_path, monkeypatch):
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path))
    token = make_key(customer="ACME GmbH", edition="pro", expires_at=_iso(90),
                     limits={"projects": 4, "users": 8})

    lic = licensing.install(token, tmp_path)
    assert lic.customer == "ACME GmbH"
    assert (tmp_path / licensing.KEY_FILENAME).exists()

    assert licensing.load(tmp_path).customer == "ACME GmbH"


def test_the_environment_wins_over_a_stale_file(tmp_path, monkeypatch):
    """Так настраивают контейнер, и файл прошлого срока не должен это перебивать."""
    (tmp_path / licensing.KEY_FILENAME).write_text(
        make_key(customer="Old", expires_at=_iso(-400)), encoding="utf-8")
    monkeypatch.setenv(licensing.ENV_KEY,
                       make_key(customer="New", expires_at=_iso(400)))

    lic = licensing.load(tmp_path)
    assert lic.customer == "New"


# --------------------------------------------------------------------------- #
#  Что видит и чувствует сервис
# --------------------------------------------------------------------------- #
@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)

    import importlib

    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    dbmod._local = threading.local()
    authmod.router = APIRouter()

    import vistest.api.check as checkmod
    importlib.reload(checkmod)
    import vistest.api.license as licmod
    importlib.reload(licmod)
    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    licmod.forget()
    return TestClient(mainmod.app), mainmod, licmod


def test_the_licence_screen_answers_both_halves(service):
    """Ключ говорит, что куплено; база — что используется. Порознь бесполезны."""
    client, mainmod, _licmod = service
    from vistest.api.auth import create_user

    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    state = client.get("/api/license").json()
    assert state["edition"] == "community"
    assert state["usage"]["users"] == 1
    assert state["limits"]["users"] == licensing.COMMUNITY["users"]
    assert state["grace_days"] == licensing.GRACE_DAYS


def test_an_expired_licence_still_serves_the_baselines(service, monkeypatch):
    """То, ради чего всё это устроено именно так."""
    client, mainmod, licmod = service
    monkeypatch.setattr(
        licmod, "_cached",
        licensing.parse(make_key(customer="ACME",
                                 expires_at=_iso(-(licensing.GRACE_DAYS + 5)))))

    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/license").json()["blocked"] is True

    refused = client.post("/api/runs", json={"run_id": "x", "project": "shop",
                                             "comparisons": []})
    assert refused.status_code == 402, refused.text
    assert "grace period" in refused.text


def test_inside_the_grace_period_runs_still_come_in(service, monkeypatch):
    client, _mainmod, licmod = service
    monkeypatch.setattr(
        licmod, "_cached",
        licensing.parse(make_key(customer="ACME", expires_at=_iso(-2))))

    r = client.post("/api/runs", json={"run_id": "x", "project": "shop",
                                       "comparisons": []})
    assert r.status_code == 200, r.text


def test_the_user_limit_is_enforced_when_a_person_appears(service, monkeypatch):
    client, mainmod, licmod = service
    from vistest.api.auth import create_user

    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    monkeypatch.setattr(
        licmod, "_cached",
        licensing.parse(make_key(customer="ACME", limits={"users": 1})))

    r = client.post("/api/users", json={"login": "boris",
                                        "password": "password123"})
    assert r.status_code == 402, r.text
    assert "active users" in r.text
    assert not mainmod.db.one("SELECT id FROM user WHERE login='boris'")


def test_installing_a_key_through_the_api_takes_effect(service):
    client, mainmod, _licmod = service
    from vistest.api.auth import create_user

    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    token = make_key(customer="ACME GmbH", edition="pro", expires_at=_iso(200),
                     limits={"projects": 9, "users": 30})
    r = client.post("/api/license", json={"key": token})
    assert r.status_code == 200, r.text
    assert r.json()["customer"] == "ACME GmbH"

    assert client.get("/api/license").json()["limits"]["users"] == 30


def test_a_bad_key_through_the_api_is_a_clear_400(service):
    client, mainmod, _licmod = service
    from vistest.api.auth import create_user

    create_user(mainmod.db, "anna", "password123", role="admin")
    client.post("/api/auth/login", json={"login": "anna", "password": "password123"})

    r = client.post("/api/license", json={"key": "definitely-not-a-key"})
    assert r.status_code == 400, r.text
