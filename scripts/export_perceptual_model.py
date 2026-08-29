# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Экспорт лёгкой перцептивной модели в ONNX.

    pip install torch torchvision timm
    python scripts/export_perceptual_model.py --arch mobilenetv3 --out models/perceptual.onnx

Модель нужна только для необязательного шага `ai.perceptual_enabled`.
Берётся предобученная на ImageNet сеть без классификационной головы:
эмбеддинги такой сети коррелируют с человеческим восприятием сходства
заметно лучше, чем попиксельные метрики (это и есть основная идея LPIPS).
"""

from __future__ import annotations

import argparse
from pathlib import Path


def build(arch: str):
    import torch
    import torch.nn as nn

    if arch == "mobilenetv3":
        from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

        m = mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.IMAGENET1K_V1)
        backbone = nn.Sequential(m.features, nn.AdaptiveAvgPool2d(1), nn.Flatten())
    elif arch == "squeezenet":
        from torchvision.models import SqueezeNet1_1_Weights, squeezenet1_1

        m = squeezenet1_1(weights=SqueezeNet1_1_Weights.IMAGENET1K_V1)
        backbone = nn.Sequential(m.features, nn.AdaptiveAvgPool2d(1), nn.Flatten())
    else:
        raise SystemExit(f"unknown architecture: {arch}")

    backbone.eval()
    return backbone, torch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", default="mobilenetv3", choices=["mobilenetv3", "squeezenet"])
    ap.add_argument("--out", default="models/perceptual.onnx")
    ap.add_argument("--size", type=int, default=224)
    args = ap.parse_args()

    model, torch = build(args.arch)
    dummy = torch.randn(1, 3, args.size, args.size)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model, dummy, str(out),
        input_names=["input"], output_names=["embedding"],
        dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=17,
    )
    size_mb = out.stat().st_size / 1e6
    print(f"Done: {out} ({size_mb:.1f} MB)")
    print("Enable in vistest.yaml:  ai.perceptual_enabled: true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
