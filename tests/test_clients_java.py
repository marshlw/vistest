# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Клиент для JVM: что он на самом деле кладёт в запрос.

Один файл без зависимостей — это осознанный отказ от Java-агента. Агент,
подменяющий сравнение внутри чужого набора, — самое дорогое, что есть в
планах, и самое непроверяемое: каждый набор цепляет скриншоты по-своему, и
агент, угадавший неверно, молчит. Зависимость в их `pom.xml` тоже не бесплатна:
это ревью, версия и согласование безопасности. Файл, который читается за пять
минут, — та величина изменения, на которую команда соглашается.

Проверяется здесь то, что ломается тихо: форма multipart (сервер ответит 422, а
в логе теста будет «не прошло»), заголовок с токеном и валидность JSON прогона.
Ответы сервиса вставляются в прогон как есть — одна лишняя запятая, и весь
прогон не примут.

Java может не стоять на машине, где гоняют только Python: тогда проверка
пропускается, а не краснеет.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "clients" / "java" / "VisTest.java"

CHECK_RESPONSE = {
    "name": "shop/checkout.png",
    "verdict": "fail",
    "metrics": {"max_severity": 100.0, "ssim_global": 0.99},
    "regions": [{"x": 1, "y": 2, "w": 3, "h": 4, "kind": "color"}],
    "artifacts": {"boxes": "/files/checks/ci-1/boxes.png"},
    "notes": ["Reason for the failure: severity 100.0"],
    "passed": False,
    "project": "shop",
    "platform": "linux-chromium-1x",
}


class _Recorder(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):                                     # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).seen.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })
        payload = json.dumps(CHECK_RESPONSE if self.path == "/api/check"
                             else {"id": 7}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):                         # тишина в отчёте
        return


@pytest.fixture
def service():
    _Recorder.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", _Recorder
    finally:
        server.shutdown()
        server.server_close()


def _java() -> str:
    java = shutil.which("java")
    if not java:
        pytest.skip("нет java — проверка JVM-клиента пропущена")
    return java


def _png(path: Path) -> Path:
    path.write_bytes(bytes.fromhex("89504e470d0a1a0a") + b"vistest")
    return path


def test_the_client_compiles_and_says_how_to_use_it(tmp_path):
    """`java VisTest.java` — запуск из исходника, без сборки.

    Это и есть проверка компиляции: единый файл обязан собираться сам, иначе
    «скопируйте в src/test/java» перестаёт быть правдой.
    """
    done = subprocess.run([_java(), str(CLIENT)], capture_output=True,
                          text=True, timeout=180, cwd=tmp_path)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "usage: java VisTest.java" in done.stderr


def test_a_check_carries_the_snapshot_the_project_and_the_token(service, tmp_path):
    url, recorder = service
    shot = _png(tmp_path / "shot.png")

    done = subprocess.run(
        [_java(), str(CLIENT), url, "shop", "checkout.png", str(shot)],
        capture_output=True, text=True, timeout=180, cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "VISTEST_TOKEN": "s3cret"})

    # Вердикт `fail` — это ответ сервиса, а не ошибка клиента: код 1 нужен,
    # чтобы падение снимка роняло шаг пайплайна.
    assert done.returncode == 1, done.stdout + done.stderr
    assert "shop/checkout.png -> fail" in done.stdout

    assert [r["path"] for r in recorder.seen] == ["/api/check"]
    sent = recorder.seen[0]
    assert sent["headers"]["X-VisTest-Token"] == "s3cret"
    assert sent["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")

    body = sent["body"]
    for expected in (b'name="name"', b"checkout.png", b'name="project"', b"shop",
                     b'name="browser"', b'filename="actual.png"',
                     b"Content-Type: image/png"):
        assert expected in body, expected
    assert shot.read_bytes() in body, "картинка доехала байт в байт"


def test_the_run_is_closed_with_the_service_answers_inside(service, tmp_path):
    """Ответы вставляются в прогон как есть.

    Собирать их заново значило бы писать JSON-сериализатор ради данных, уже
    пришедших валидным JSON'ом, — и терять на этом регионы, метрики и ссылки
    на артефакты. Цена — одна лишняя запятая рушит весь прогон, поэтому
    результат разбирается здесь настоящим парсером.
    """
    url, recorder = service
    shot = _png(tmp_path / "shot.png")

    subprocess.run([_java(), str(CLIENT), url, "shop", "checkout.png", str(shot)],
                   capture_output=True, text=True, timeout=180, cwd=tmp_path,
                   env={"PATH": "/usr/bin:/bin", "VISTEST_RUN_ID": "ci-42",
                        "VISTEST_BRANCH": "release/7", "VISTEST_COMMIT": "abc123"})

    assert [r["path"] for r in recorder.seen] == ["/api/check", "/api/runs"]
    run = json.loads(recorder.seen[1]["body"])

    assert run["run_id"] == "ci-42"
    assert run["project"] == "shop"
    assert run["platform"] == "linux-chromium-1x", "платформу решает сервис"
    assert run["git"] == {"branch": "release/7", "sha": "abc123"}
    assert run["totals"] == {"total": 1, "failed": 1, "new": 0, "passed": 0}

    comparison = run["comparisons"][0]
    assert comparison["name"] == "shop/checkout.png"
    assert comparison["regions"], "регионы доехали, а не потерялись по дороге"
    assert comparison["artifacts"]["boxes"].endswith("boxes.png")


def test_a_run_without_a_key_is_not_invented(service, tmp_path):
    """Прогон нечем назвать — значит, его нет.

    Придумать ключ самим значило бы завести в истории прогон, который CI не
    сможет ни найти, ни перезалить при перезапуске задачи.
    """
    url, recorder = service
    shot = _png(tmp_path / "shot.png")

    subprocess.run([_java(), str(CLIENT), url, "shop", "checkout.png", str(shot)],
                   capture_output=True, text=True, timeout=180, cwd=tmp_path,
                   env={"PATH": "/usr/bin:/bin"})

    assert [r["path"] for r in recorder.seen] == ["/api/check"]
