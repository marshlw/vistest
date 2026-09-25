"""The frozen corpus's `jpeg q=75` passes only because generator and detector
share a channel-order mistake. Encode the same frame correctly and it fails.

    python jpeg_channel_order.py <repo>/tests/benchmark_corpus/identical/expected.png
"""
import io, sys
import cv2, numpy as np
from PIL import Image
from vistest.core.comparator import compare

e = np.asarray(Image.open(sys.argv[1]).convert("RGB"))
for q in (88, 75):
    ok, buf = cv2.imencode(".jpg", e, [cv2.IMWRITE_JPEG_QUALITY, q])            # as tests/synthetic.py does
    same = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(e, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
    right = cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    b = io.BytesIO(); Image.fromarray(e).save(b, "JPEG", quality=q); pil = np.asarray(Image.open(b).convert("RGB"))
    for label, a in (("corpus-style (RGB as BGR)", same), ("OpenCV, correct order", right), ("Pillow defaults", pil)):
        r = compare(e, a)
        print(f"q={q} {label:26s} {r.verdict.value:4s} regions={len(r.regions)}")
