# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Абстракция хранилища бейзлайнов."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class BaselineRecord:
    name: str
    image: np.ndarray
    stability_mask: np.ndarray | None = None
    dom: dict | None = None
    ignore_boxes: list[dict] | None = None
    version: int = 1
    meta: dict | None = None


class BaselineStore(ABC):
    @abstractmethod
    def exists(self, name: str) -> bool: ...

    @abstractmethod
    def load(self, name: str) -> BaselineRecord | None: ...

    @abstractmethod
    def save(self, record: BaselineRecord) -> None: ...

    @abstractmethod
    def list_names(self) -> list[str]: ...

    @abstractmethod
    def add_ignore_box(self, name: str, box: dict) -> None: ...
