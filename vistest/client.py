# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""HTTP-клиент к сервису. Offline-first: недоступный API не роняет тесты."""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("vistest.client")


class ApiClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = (cfg.api_url or "").rstrip("/")

    def _post(self, path: str, *, json_body=None, files=None):
        import requests

        url = f"{self.base}{path}"
        try:
            r = requests.post(url, json=json_body, files=files,
                              timeout=self.cfg.timeout_s)
            r.raise_for_status()
            return r.json() if r.content else {}
        except Exception as e:
            if not self.cfg.fail_open:
                raise
            log.warning("API unavailable (%s): %s", url, e)
            return None

    def push_run(self, payload: dict, run_dir: Path) -> dict | None:
        body = {**payload, "project": self.cfg.project}
        created = self._post("/api/runs", json_body=body)
        if created is None:
            # Помечаем прогон как «не отправлен» — заберём командой `vistest push`.
            (run_dir / ".pending").write_text("1", encoding="utf-8")
            return None

        if self.cfg.push_artifacts:
            self._upload_artifacts(created.get("id"), payload, run_dir)
        return created

    def _upload_artifacts(self, run_id, payload: dict, run_dir: Path) -> None:
        import requests

        for comp in payload.get("comparisons", []):
            for kind, p in (comp.get("artifacts") or {}).items():
                f = Path(p)
                if not f.exists() or f.stat().st_size > 25 * 1024 * 1024:
                    continue
                try:
                    with f.open("rb") as fh:
                        requests.post(
                            f"{self.base}/api/runs/{run_id}/artifacts",
                            data={"snapshot": comp["name"], "kind": kind},
                            files={"file": (f.name, fh, "image/png")},
                            timeout=self.cfg.timeout_s,
                        )
                except Exception as e:
                    log.debug("artifact %s not uploaded: %s", f, e)


def push_pending(root: Path, cfg) -> int:
    """Догрузить прогоны, накопившиеся пока сервис лежал."""
    client = ApiClient(cfg)
    sent = 0
    for marker in root.glob("*/.pending"):
        run_dir = marker.parent
        run_json = run_dir / "run.json"
        if not run_json.exists():
            continue
        payload = json.loads(run_json.read_text("utf-8"))
        if client.push_run(payload, run_dir) is not None:
            marker.unlink(missing_ok=True)
            sent += 1
    return sent
