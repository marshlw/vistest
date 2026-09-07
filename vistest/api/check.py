# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`POST /api/check` — checking a snapshot sent from anywhere.

This is the entry point for projects already written in Cypress, Playwright JS,
Java, C#, Go or anything else. The test takes a screenshot by its own means and
sends the PNG here; the comparison is performed by the VisTest engine, and the
response is a verdict, metrics, a list of regions and links to artifacts.

Why this way rather than «porting the tests»: nobody will rewrite an existing
test suite just for visual checks. A single HTTP call at the end of an existing
test is a realistic amount of change.

Additionally, `GET /api/stabilize.js` is served — the very same script for
freezing animations, determinism and DOM capture that the Python runner uses.
A client in any language injects it into the page and gets the same snapshot
stability.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from ..capture import dom as _dom
from ..capture import stabilize as _stab
from ..config import VisTestConfig
from ..models import Verdict
from ..service import CheckService, slug

router = APIRouter()

ROOT = Path(os.getenv("VISTEST_ROOT", ".vistest")).resolve()
ARTIFACTS = ROOT / "artifacts"
_cfg = VisTestConfig.load()


def _who(request: Request, project: str | None) -> str:
    """Проверка доступа тем же путём, что и приём прогонов.

    Мягко к одиночной установке (пользователей нет — пускаем, как и везде) и
    строго к сетевой: там нужен токен проекта, `VISTEST_INGEST_TOKEN` или
    сессия ревьюера. Импорт внутри функции — `main` импортирует этот модуль,
    и обратная ссылка на уровне модуля замкнула бы круг.
    """
    from .main import require_ingest

    return require_ingest(request, project or "")


#  Имя проекта по умолчанию. Это placeholder из конфигурации, а не проект:
#  им подписана одиночная установка, где проектов просто нет.
DEFAULT_PROJECT = "default"


def scoped(name: str, project: str | None) -> str:
    """`checkout.png` + проект `shop` → `shop/checkout.png`.

    Поле `project` до сих пор возвращалось в ответе и участвовало в порогах, но
    в ХРАНЕНИИ не участвовало никак: имя уходило в общий набор по платформе как
    есть. Две команды, приславшие `checkout.png`, делили один эталон и молча
    переписывали друг друга — а по вердикту это выглядит как регресс, которого
    в приложении нет.

    Хранилище разделение по проектам умеет с самого начала: слэш в имени и есть
    проект (`split_project`). Оставалось им воспользоваться.

    Два случая имя не трогают, и оба намеренно. `default` — не проект, а
    подпись одиночной установки: там проектов нет, и префикс создал бы каталог
    на пустом месте. Слэш в имени означает, что вызывающий назвал проект сам, —
    спорить с ним нельзя, иначе получится `shop/shop/…`.
    """
    project = (project or "").strip()
    if not project or project == DEFAULT_PROJECT:
        return name
    if "/" in str(name).replace("\\", "/"):
        return name
    return f"{project}/{name}"


# --------------------------------------------------------------------------- #
#  Пределы приёма
#
#  Это вход для чужих наборов на любом языке: сюда стучится CI, у которого нет
#  ни сессии, ни браузера, — и до сих пор он мог прислать что угодно и сколько
#  угодно. Файл читался в память целиком (`await image.read()`), число кадров
#  не ограничивалось, а PIL разворачивал картинку любого разрешения. Один
#  запрос с десятком файлов клал процесс, и выглядело это как «сервис
#  подвисает», то есть причину искали не там.
#
#  Числа те же, что у приёма артефактов прогона (`VISTEST_MAX_ARTIFACT_MB`):
#  два разных потолка на две двери, ведущие в один каталог, разошлись бы на
#  первой же правке.
# --------------------------------------------------------------------------- #
MAX_IMAGE_BYTES = int(os.getenv("VISTEST_MAX_ARTIFACT_MB", "40")) * 1024 * 1024
# Кадры нужны для распознавания анимации: два-три достаточно, десяток — это
# уже не динамика, а способ занять память.
MAX_FRAMES = int(os.getenv("VISTEST_MAX_FRAMES", "8"))
# Пиксели, а не байты: PNG в 2 МБ разворачивается в гигабайты. Ограничение
# считается ДО `convert("RGB")`, то есть до выделения памяти.
MAX_PIXELS = int(os.getenv("VISTEST_MAX_PIXELS", str(60_000_000)))


async def _read_limited(upload, what: str = "image") -> bytes:
    """Прочитать загруженный файл, не дав ему стать больше потолка.

    `Content-Length` здесь не проверяется намеренно: его пишет вызывающий, и
    верить ему — значит не иметь предела вовсе.
    """
    chunks, total = [], 0
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise HTTPException(
                413, f"{what} is larger than "
                     f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(data: bytes) -> np.ndarray:
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data))
        # `Image.open` читает только заголовок — размер известен, пиксели ещё
        # не разложены. Это единственное место, где отказ ничего не стоит.
        width, height = image.size
        if width * height > MAX_PIXELS:
            raise HTTPException(
                413, f"the image is {width}×{height} = "
                     f"{width * height // 1_000_000} Mpx, the limit is "
                     f"{MAX_PIXELS // 1_000_000} Mpx "
                     "(VISTEST_MAX_PIXELS raises it)")
        return np.array(image.convert("RGB"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"could not read the image: {e}") from e


#  Что вызывающий вправе поменять на один вызов — и в каких пределах.
#
#  Проверялись только ИМЕНА: `{"min_region_px": "abc"}` доезжал до движка и
#  падал пятисоткой в середине сравнения, а `{"fail_severity": -5}` тихо
#  превращал проверку в «всё красное». Ни то, ни другое не ошибка вызывающего
#  в том смысле, в каком её можно было понять по ответу: там было «internal
#  error» и идентификатор запроса.
#
#  Границы взяты по смыслу величины, а не «на всякий случай»: ΔE00 больше ста
#  не существует, доля площади измеряется в процентах, севериность
#  нормирована на сто. Верхняя граница `min_region_px` — миллион пикселей: это
#  уже «не смотреть ни на что», и такой порог стоит поставить осознанно, а не
#  промахнувшись на три нуля.
_OVERRIDES: dict[str, tuple] = {
    #  имя                     тип     минимум  максимум
    "delta_e_threshold":      (float,  0.0,     100.0),
    "ssim_threshold":         (float,  0.0,     1.0),
    "fail_severity":          (float,  0.0,     100.0),
    "max_changed_area_pct":   (float,  0.0,     100.0),
    "min_region_px":          (int,    0,       1_000_000),
    "max_align_shift_px":     (float,  0.0,     10_000.0),
    "moved_severity_scale":   (float,  0.0,     10.0),
    "detect_moved":           (bool,   None,    None),
    "fail_on_size_change":    (bool,   None,    None),
    "ignore_kinds":           (list,   None,    None),
}
_ALLOWED_OVERRIDES = frozenset(_OVERRIDES)


def _clean_overrides(raw: dict) -> dict:
    """Разобрать `options` запроса в значения, которые движок переживёт."""
    if not isinstance(raw, dict):
        raise HTTPException(400, "options must be an object: name → value")

    unknown = set(raw) - _ALLOWED_OVERRIDES
    if unknown:
        raise HTTPException(
            400, f"unknown options: {sorted(unknown)}; "
                 f"allowed: {sorted(_ALLOWED_OVERRIDES)}")

    out: dict = {}
    for name, value in raw.items():
        kind, low, high = _OVERRIDES[name]

        if kind is bool:
            if not isinstance(value, bool):
                raise HTTPException(400, f"{name} must be true or false")
            out[name] = value
            continue

        if kind is list:
            # Классы изменений: список строк, и каждая должна быть известной —
            # опечатка здесь молча отключала бы не тот класс.
            from ..models import ChangeKind

            if not isinstance(value, list):
                raise HTTPException(400, f"{name} must be a list of change kinds")
            known = {k.value for k in ChangeKind}
            bad = [v for v in value if v not in known]
            if bad:
                raise HTTPException(
                    400, f"unknown change kinds: {bad}; known: {sorted(known)}")
            out[name] = list(value)
            continue

        # bool — подкласс int, и `True` прошло бы как единица. Для порога это
        # почти наверняка не то, что имел в виду вызывающий.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HTTPException(400, f"{name} must be a number")
        number = kind(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise HTTPException(400, f"{name} must be a finite number")
        if not (low <= number <= high):
            raise HTTPException(
                400, f"{name} must be between {low} and {high} (got {number})")
        out[name] = number
    return out


@router.post("/api/check")
async def check(
    request: Request,
    name: str = Form(..., description="snapshot name, for example checkout.png"),
    image: UploadFile = File(..., description="current PNG screenshot"),
    frames: list[UploadFile] = File(
        default=[], description="extra frames of the same page for dynamics detection"),
    dom: UploadFile | None = File(default=None, description="DOM snapshot JSON"),
    project: str | None = Form(default=None),
    platform: str | None = Form(default=None),
    browser: str = Form(default="chromium"),
    run_key: str | None = Form(default=None),
    update_baseline: bool = Form(default=False),
    preset: str | None = Form(default=None),
    options: str | None = Form(default=None, description="JSON overriding the thresholds"),
):
    """Compare the sent snapshot with the baseline.

    Response:
        {
          "verdict": "pass" | "fail" | "new_baseline",
          "passed": true|false,
          "metrics": {...}, "regions": [...], "notes": [...],
          "artifacts": {"boxes": "/files/...", ...},
          "review_url": "/ui/"
        }
    """
    # Кто прислал. Спрашивается ПЕРВЫМ и здесь, а не в общем страже: токен
    # выдаётся на проект, а имя проекта лежит в теле формы — прочитать его
    # раньше разбора тела нельзя.
    #
    # До этого роут требовал сессионную куку, то есть браузер. У раннера в CI
    # браузера нет, и вход, объявленный входом «для тестов на любом языке»,
    # отвечал им всем 401 — на любой инсталляции, где заведены пользователи.
    _who(request, project)

    rgb = _decode(await _read_limited(image, "the screenshot"))
    real_frames = [f for f in frames if f is not None]
    if len(real_frames) > MAX_FRAMES:
        raise HTTPException(
            413, f"no more than {MAX_FRAMES} extra frames at a time "
                 f"(received {len(real_frames)})")
    extra = [_decode(await _read_limited(f, "a frame")) for f in real_frames]
    all_frames = [rgb, *extra] if extra else None

    dom_data = None
    if dom is not None:
        try:
            dom_data = json.loads(await dom.read())
        except Exception:
            dom_data = None

    overrides = {}
    if options:
        try:
            raw = json.loads(options)
        except Exception as e:
            raise HTTPException(400, f"options did not parse as JSON: {e}") from e
        overrides = _clean_overrides(raw)

    # Порядок здесь и есть ответ на «чей порог сильнее»: пресет или конфиг —
    # основа, поверх ложится то, что выставили в интерфейсе, и только сверху —
    # `options` этого конкретного вызова. Ближе к вызывающему — сильнее.
    cfg = VisTestConfig.preset_of(preset) if preset else _cfg
    try:
        from .main import db
        from .thresholds import apply
        cfg = apply(cfg, db, project)
    except Exception:
        pass
    run_dir = ARTIFACTS / "checks" / (slug(run_key) if run_key else "adhoc")
    svc = CheckService(cfg, platform=platform, browser=browser, run_dir=run_dir)

    # Ключ, под которым снимок живёт в хранилище и в истории.
    scoped_name = scoped(name, project or cfg.service.project)
    stored = scoped_name
    notes: list[str] = []
    if stored != name and not svc.store.exists(stored) and svc.store.exists(name):
        # Эталон, снятый до разделения по проектам. Молча завести новый и
        # объявить снимок новым значило бы потерять точку отсчёта у всех, кто
        # уже пользуется этим входом. Работаем со старым и говорим, что он
        # переедет при следующем апруве.
        stored = name
        notes.append(
            "The baseline is a legacy one, kept without the project prefix. "
            f"The next accepted change will store it as «{scoped_name}».")

    res = svc.check(
        stored, rgb,
        frames=all_frames, dom=dom_data,
        diff_overrides=overrides or None,
        update_baseline=update_baseline or None,
        notes=notes or None,
    )

    body = res.to_dict()
    body["passed"] = res.verdict is not Verdict.FAIL
    body["artifacts"] = {k: _public_uri(v) for k, v in res.artifacts.items()}
    body["project"] = project or cfg.service.project
    body["platform"] = svc.platform
    body["review_url"] = "/ui/"
    return body


@router.get("/api/baselines")
def list_baselines(platform: str | None = None):
    """List of baselines — so the client can find out what has already been recorded."""
    from ..storage import FileBaselineStore

    root = _cfg.root_path / _cfg.paths.baselines
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or (platform and d.name != platform):
            continue
        out.append({"platform": d.name, "names": FileBaselineStore(d).list_names()})
    return out


@router.post("/api/baselines/upload")
async def put_baseline(
    name: str = Form(...),
    image: UploadFile = File(...),
    platform: str | None = Form(default=None),
    browser: str = Form(default="chromium"),
    url: str | None = Form(default=None),
    project: str | None = Form(default=None),
):
    """Record a baseline directly, without comparison.

    `url` is optional, but with it the snapshot becomes «runnable»: it can be
    recreated and checked from the UI with a single button.
    """
    svc = CheckService(_cfg, platform=platform, browser=browser)
    # Тот же ключ, что и у проверки: эталон, записанный мимо проекта, потом не
    # находится проверкой этого проекта — и выглядит это как «загрузка не
    # сработала».
    stored = scoped(name, project or _cfg.service.project)
    svc.save_baseline(stored, _decode(await _read_limited(image, "the screenshot")),
                      meta={"approved_by": "api", "url": url})
    return {"ok": True, "name": stored, "platform": svc.platform}


@router.post("/api/baselines/seed")
async def seed_baseline(
    request: Request,
    name: str = Form(...),
    image: UploadFile = File(...),
    platform: str | None = Form(default=None),
    browser: str = Form(default="chromium"),
    project: str | None = Form(default=None),
):
    """Записать эталон, ТОЛЬКО если его ещё нет. Иначе 409.

    Ради одного сценария, и он важнее, чем кажется. Репортёр Playwright
    забирает из их отчёта тройку `expected / actual / diff` — их собственный
    эталон приезжает вместе с фактом. Первый прогон без этого отвечал бы
    «новый эталон» на каждый снимок и не сравнивал бы ничего: день первый
    выглядел бы как «инструмент не работает».

    Но перезаписывать НАШ эталон их картинкой нельзя ни при каких условиях.
    Их `expected` приезжает на каждом падении, и «просто записать» означало бы
    принимать регресс автоматически — ровно то, ради чего инструмент и стоит.
    Поэтому 409 здесь не ошибка, а штатный ответ, и вызывающий его ждёт.
    """
    who = _who(request, project)
    svc = CheckService(_cfg, platform=platform, browser=browser)
    stored = scoped(name, project or _cfg.service.project)
    if svc.store.exists(stored):
        raise HTTPException(409, {
            "error": "baseline already exists",
            "name": stored,
            "message": "A baseline for this snapshot is already recorded. "
                       "Changing it is an approval, and an approval is made by "
                       "a person in the review screen.",
        })
    svc.save_baseline(stored, _decode(await _read_limited(image, "the screenshot")),
                      meta={"approved_by": who, "seeded": True})
    return {"ok": True, "name": stored, "platform": svc.platform}


@router.get("/api/stabilize.js", response_class=PlainTextResponse)
def stabilize_js():
    """Stabilization script for clients not on Python.

    Injected into the page before the snapshot. Provides the same freezing of
    animations, deterministic Math.random/Date and the DOM snapshot capture
    function that the Python runner uses — meaning the snapshots are comparable.

    After injection the following is available:
        window.__vistest.freeze()        freeze CSS
        window.__vistest.settle()        promise: fonts, images, lazy-load
        window.__vistest.dom(dpr)        DOM snapshot for attribution
        window.__vistest.maskBoxes()     bboxes of dynamic elements
    """
    parts = [
        "(() => {",
        _stab.DETERMINISM_JS,
        "const FREEZE_CSS = " + json.dumps(_stab.FREEZE_CSS) + ";",
        "const MEDIA_SELECTORS = " + json.dumps(list(_stab.DEFAULT_MEDIA_SELECTORS)) + ";",
        "const findBoxes = " + _stab.FIND_BOXES_JS + ";",
        "const findAnimated = " + _stab.FIND_ANIMATED_JS + ";",
        "const waitMedia = " + _stab.WAIT_MEDIA_JS + ";",
        "const scrollThrough = " + _stab.SCROLL_THROUGH_JS + ";",
        "const domSnapshot = " + _dom.SNAPSHOT_JS + ";",
        """
        function freeze() {
          let s = document.getElementById('__vistest_freeze');
          if (!s) {
            s = document.createElement('style');
            s.id = '__vistest_freeze';
            (document.head || document.documentElement).appendChild(s);
          }
          s.textContent = FREEZE_CSS;
          return true;
        }

        async function settle(opts) {
          opts = opts || {};
          freeze();
          if (opts.scroll !== false) await scrollThrough(opts.stepRatio || 0.8);
          await waitMedia();
          return true;
        }

        function maskBoxes(extraSelectors) {
          const sels = MEDIA_SELECTORS.concat(extraSelectors || []);
          return findBoxes(sels).concat(
            findAnimated().map(b => Object.assign(b, { source: 'animated' })));
        }

        window.__vistest = {
          freeze, settle, maskBoxes,
          dom: (dpr) => domSnapshot(dpr || window.devicePixelRatio || 1),
          version: '0.1.0',
        };
        })();
        """,
    ]
    return "\n".join(parts)


def _public_uri(path: str) -> str:
    """Local artifact path → URL served by /files/."""
    try:
        rel = Path(path).resolve().relative_to(ARTIFACTS.resolve())
        return "/files/" + rel.as_posix()
    except Exception:
        return path
