# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Пересборка сервиса под текущий VISTEST_ROOT.

`vistest.api.main` читает `VISTEST_ROOT` и открывает базу **на импорте**. Пока
e2e-модуль был один, это никого не трогало. Модулей стало два — с входом и без
него, — и обычный `import` отдал бы второму приложение первого: чужая база,
чужие эталоны, чужой реестр проектов. Симптом узнаваемый: поодиночке проходит
всё, вместе — второй модуль целиком красный.
"""

from __future__ import annotations

import importlib
import threading


def rebind_service():
    """Перезагрузить сервис так, чтобы он смотрел на текущий VISTEST_ROOT."""
    from fastapi import APIRouter

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod

    # Соединения кэшируются по потоку и пути; временная база каждый раз новая.
    dbmod._local = threading.local()
    # `build_router` вешает маршруты на МОДУЛЬНЫЙ router и замыкает в них
    # конкретную базу. Без сброса перезагрузка добавляет второй комплект
    # маршрутов, а срабатывает первый — с базой предыдущего модуля.
    authmod.router = APIRouter()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return mainmod.app, mainmod.db
