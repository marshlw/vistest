# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""PNG in, PNG out. The one place that knows the file format.

Until now the only decoder in the package lived in
`vistest.capture.playwright_capture`, and that module imports the config
loader, the domain models and the DOM capture helper. Which was fine while
every caller was the service — and wrong the moment the engine had to be
usable on its own: a library user who hands us a `bytes` object and never
starts a browser was paying for the whole capture layer to be imported.

So the decoding moved here, where the engine already is, and
`playwright_capture` re-exports the two historical names. Nothing above
changed its import.

Two policy decisions worth stating, because both are visible in results:

* **Alpha is dropped, not composited.** `IMREAD_COLOR` is what the service
  has always used, and baselines captured before this module exist were
  written under that rule. Compositing onto white here would make the same
  screenshot decode to different pixels depending on which version wrote it.
* **cv2 first, Pillow second.** Also the historical order. Both are base
  dependencies, but OpenCV is what the rest of the cascade runs on, and a
  decoder that disagrees with the comparator about the byte layout is a class
  of bug nobody enjoys.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

__all__ = ["PngError", "decode", "dimensions", "encode", "read", "write"]

#  The eight bytes every PNG starts with, then the length+type of the first
#  chunk, which the format requires to be IHDR.
_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PngError(ValueError):
    """The bytes are not a picture we can read — with a text that says where from.

    A `ValueError`, because that is what every caller already handles around
    image reads. The point of the class is the message: «cannot decode» with no
    path in it sends a person looking through their whole baseline directory.
    """


def _where(source: str | Path | None) -> str:
    return f" ({source})" if source else ""


# --------------------------------------------------------------------------- #
#  Bytes <-> array
# --------------------------------------------------------------------------- #
def decode(png: bytes, *, source: str | Path | None = None) -> np.ndarray:
    """PNG bytes -> RGB uint8, H x W x 3."""
    if not isinstance(png, (bytes, bytearray, memoryview)):
        raise PngError(
            f"expected PNG bytes, got {type(png).__name__}{_where(source)}")
    if not png:
        raise PngError(f"the PNG is empty{_where(source)}")

    try:
        import cv2

        buffer = np.frombuffer(bytes(png), dtype=np.uint8)
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if image is None:
            raise PngError(f"cannot decode this as a PNG{_where(source)}")
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    except ImportError:  # pragma: no cover - Pillow is the fallback path
        import io

        from PIL import Image, UnidentifiedImageError

        try:
            return np.array(Image.open(io.BytesIO(bytes(png))).convert("RGB"))
        except (UnidentifiedImageError, OSError) as e:
            raise PngError(f"cannot decode this as a PNG{_where(source)}: {e}") \
                from None


def encode(rgb: np.ndarray) -> bytes:
    """RGB uint8 -> PNG bytes."""
    array = np.asarray(rgb)
    if array.ndim != 3 or array.shape[2] != 3:
        raise PngError(
            f"expected an RGB array of shape H x W x 3, got {array.shape}")
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)

    try:
        import cv2

        ok, buffer = cv2.imencode(".png", cv2.cvtColor(array, cv2.COLOR_RGB2BGR))
        if not ok:
            raise PngError("OpenCV refused to encode this array as a PNG")
        return bytes(buffer)
    except ImportError:  # pragma: no cover - Pillow is the fallback path
        import io

        from PIL import Image

        out = io.BytesIO()
        Image.fromarray(array).save(out, format="PNG")
        return out.getvalue()


def dimensions(png: bytes, *, source: str | Path | None = None) -> tuple[int, int]:
    """(width, height) straight out of the IHDR chunk, without decoding.

    A baseline's passport carries its size, and the passport is optional — it
    has to be reconstructible from the file alone. Doing that by decoding a
    full-page screenshot means unfolding tens of megabytes to read eight bytes,
    once per snapshot in the run.
    """
    data = bytes(png or b"")
    if len(data) < 24 or not data.startswith(_SIGNATURE):
        raise PngError(f"this does not start like a PNG{_where(source)}")
    if data[12:16] != b"IHDR":
        raise PngError(f"this PNG has no IHDR header{_where(source)}")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


# --------------------------------------------------------------------------- #
#  Files
# --------------------------------------------------------------------------- #
def read(path: str | Path) -> np.ndarray:
    """Read a PNG file into an RGB array."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise PngError(f"cannot read {path}: {e}") from None
    return decode(raw, source=path)


def write(path: str | Path, rgb: np.ndarray) -> None:
    """Write an RGB array as a PNG. Not atomic — see `vistest.storage.atomic`."""
    Path(path).write_bytes(encode(rgb))
