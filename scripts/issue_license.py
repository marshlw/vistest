#!/usr/bin/env python3
# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Issue a commercial licence key. Runs on the vendor's machine, never on a
customer's.

This is the other half of `vistest/licensing.py`: that one verifies, this one
signs. The split is deliberate — verification has to work in an offline
installation with no dependencies at all, so it is forty lines of `pow()`;
signing happens here, where requiring a proper crypto library costs nothing.

    # once, when setting up:
    python scripts/issue_license.py --new-key ~/.vistest-signing/private.pem

    # per customer:
    python scripts/issue_license.py \\
        --key ~/.vistest-signing/private.pem \\
        --customer "ACME GmbH" --expires 2027-09-01 \\
        --projects 10 --users 25 --edition pro

The private key never goes into the repository, never into a backup that leaves
your machine, and never into a support ticket. Anyone holding it can issue
licences in your name.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _load_private(path: Path):
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        print("This script needs the `cryptography` package:\n"
              "    pip install cryptography", file=sys.stderr)
        raise SystemExit(2) from None
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def new_key(path: Path, bits: int = 3072) -> None:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        print("This script needs the `cryptography` package:\n"
              "    pip install cryptography", file=sys.stderr)
        raise SystemExit(2) from None

    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    try:
        path.chmod(0o600)
    except OSError:
        pass

    modulus_hex = format(key.public_key().public_numbers().n, "x")
    chunks = [modulus_hex[i:i + 72] for i in range(0, len(modulus_hex), 72)]
    block = "\n".join(f'    "{c}"' for c in chunks)

    print(f"Private key written to {path} — keep it off every repository and "
          f"every shared drive.\n")
    print("Put this into vistest/licensing.py, replacing PUBLIC_MODULUS_HEX:\n")
    print("PUBLIC_MODULUS_HEX = (")
    print(block)
    print(")")


def sign(private_key, payload: dict) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(raw, padding.PKCS1v15(), hashes.SHA256())
    return f"{_b64url(raw)}.{_b64url(signature)}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--new-key", metavar="PATH",
                   help="generate a signing pair and print the public half")
    p.add_argument("--key", metavar="PATH", help="private key to sign with")
    p.add_argument("--customer", default="", help="who the licence is issued to")
    p.add_argument("--edition", default="pro", choices=["pro", "enterprise"])
    p.add_argument("--expires", default="",
                   help="last day of the term, YYYY-MM-DD; omit for perpetual")
    p.add_argument("--projects", type=int, default=0,
                   help="how many connected projects; 0 = unlimited")
    p.add_argument("--users", type=int, default=0,
                   help="how many active users; 0 = unlimited")
    p.add_argument("--features", default="",
                   help="comma-separated feature names; empty = everything")
    p.add_argument("--note", default="", help="free text shown on the licence screen")
    p.add_argument("--out", metavar="PATH", help="write the key here as well")
    args = p.parse_args(argv)

    if args.new_key:
        new_key(Path(args.new_key).expanduser())
        return 0

    if not args.key:
        p.error("--key is required (or --new-key to create one)")
    if not args.customer:
        p.error("--customer is required: a licence with no name on it cannot "
                "be supported, renewed, or revoked")
    if args.expires:
        try:
            date.fromisoformat(args.expires)
        except ValueError:
            p.error("--expires must be YYYY-MM-DD")

    payload = {
        "edition": args.edition,
        "customer": args.customer,
        "issued_at": datetime.now(timezone.utc).date().isoformat(),
        "expires_at": args.expires,
        "limits": {"projects": args.projects, "users": args.users},
        "features": [f.strip() for f in args.features.split(",") if f.strip()],
        "note": args.note,
    }

    token = sign(_load_private(Path(args.key).expanduser()), payload)

    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(token + "\n", encoding="utf-8")
        print(f"Written to {out}")

    print(token)
    print(f"\nIssued to {args.customer}, edition {args.edition}, "
          + (f"valid until {args.expires}." if args.expires else "perpetual."))
    print("The customer installs it on the Licence screen, or as "
          "VISTEST_LICENSE, or as .vistest/license.key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
