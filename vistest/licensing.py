# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Commercial licence keys: reading, verifying, and what a key permits.

What this is for. VisTest is sold as software, installed inside someone else's
perimeter, often with no route to the internet at all. So a licence has to be a
file you can hand over — by email, on a flash drive, in a ticket — that the
installation can check entirely on its own.

**What this is not.** The source is AGPL: anyone can read this file, delete the
check and rebuild. That is not a hole to be plugged, it is a property of the
licence the project chose. What a key does is make the terms explicit and
checkable — how many projects, how many people, until when — so that an honest
customer knows what they bought and a renewal is a conversation rather than an
audit. Nothing here is designed to survive an adversary with a text editor, and
pretending otherwise would only make the code worse.

**Why a hand-rolled RSA verification and not a library.** Verifying an RSA
signature is `pow(signature, e, n)` — the standard library has that, and the
comparison afterwards is byte equality against a PKCS#1 v1.5 DigestInfo we
build ourselves. That is forty lines, no dependency, and it works in an offline
installation where `pip install` is not an option. Signing is the other half of
the pair, and it lives in `scripts/issue_license.py`, on the vendor's machine,
where a proper crypto library is a reasonable thing to require.

The key format is deliberately dull:

    <base64url(payload json)>.<base64url(signature)>

Both halves are ASCII, so a key survives copy-paste through a chat window, an
env var, and a Word document — all three of which will happen.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

#  The vendor public key. Replace both halves with your own pair before the
#  first release — `scripts/issue_license.py --new-key` prints a fresh one and
#  writes the private half where you tell it to.
#
#  A public key in source is exactly where a public key belongs. What must
#  never be here is the private half.
PUBLIC_MODULUS_HEX = (
    "9d48bc81fa3a7596522f84367476f43147a2245194be5aed5386082ab93ed1177b724f3d"
    "fd15d7133abc0b31561eeb6d0913d175c3c0d5cc82ce85c4383e38904c7e1361a3bf383a"
    "ad21b10670e581c6a8341c5f0a193fb50277180f26bda94a49a6982a9d00c31f5176c8a6"
    "e27b31056ac915b16d40d80eabab1fc942ce131e63ce02637dde32b4a449a5226e1e8f00"
    "48462c41a04fefa6828bbe6ee432a335a3681d1253bc761ddb8dfec34fdb21d71bc78ac2"
    "786371e3c25262d46b3eb03870f068f826486cc515f0fa8ff949385a8e07ced552ddd142"
    "d69127e134c1c4feb8dedad2c9698deb31066d8bf3345c6704036c88900b123736bad24d"
    "0d610b4fead5491018c81e1a85cab65325f99445b3d33122f82a98919ac7f2c3c85f4c45"
    "e0521d54131fc2846c75857b527608c4784fd4bcff28e1b4d48c50b1a6e46fb8cebae912"
    "3098fa7049420f79d0a3d6073f740448b4dccbf4556bab6b2122855e8175012f07c24a43"
    "76ba91eee261e6b8ab15de2821338d9b3925a85bfb45eea5"
)
PUBLIC_EXPONENT = 65537

#  Prefix of the DigestInfo for SHA-256, per PKCS#1 v1.5. Constant, and it is
#  quoted here rather than assembled so that the check has nothing to get
#  clever about.
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")

#  How long a customer keeps working after the term ends.
#
#  Not zero, and that is a product decision rather than a technicality. This
#  tool sits inside a CI pipeline: an installation that stops on the day of
#  expiry breaks someone's release on a Friday over an unpaid invoice, and the
#  next conversation is not about renewal. Thirty days is enough for a purchase
#  order to go through a procurement department.
GRACE_DAYS = 30

#  What an installation without a key may do. The free tier is meant for one
#  team trying the tool out, and it has to be genuinely usable — a demo that
#  cannot hold a real project teaches nothing about the product.
COMMUNITY = {
    "edition": "community",
    "projects": 2,
    "users": 5,
    "customer": "",
}

ENV_KEY = "VISTEST_LICENSE"
KEY_FILENAME = "license.key"


class LicenseError(ValueError):
    """A key that cannot be trusted: malformed, or signed by someone else."""


@dataclass
class License:
    """A verified key, plus the questions the rest of the service asks it."""

    edition: str = "community"
    customer: str = ""
    issued_at: str = ""
    expires_at: str = ""          # ISO date; empty means perpetual
    projects: int = 0             # 0 = unlimited
    users: int = 0                # 0 = unlimited
    features: list[str] = field(default_factory=list)
    note: str = ""
    signed: bool = False          # False for the built-in community tier
    raw: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    @property
    def perpetual(self) -> bool:
        return not self.expires_at

    def days_left(self, today: date | None = None) -> int | None:
        """Days until the term ends. None for a perpetual key."""
        if self.perpetual:
            return None
        today = today or datetime.now(timezone.utc).date()
        try:
            end = date.fromisoformat(self.expires_at)
        except ValueError:
            return 0
        return (end - today).days

    @property
    def expired(self) -> bool:
        left = self.days_left()
        return left is not None and left < 0

    @property
    def in_grace(self) -> bool:
        left = self.days_left()
        return left is not None and -GRACE_DAYS <= left < 0

    @property
    def blocked(self) -> bool:
        """Past the grace period — new runs stop, everything else stays."""
        left = self.days_left()
        return left is not None and left < -GRACE_DAYS

    def allows_projects(self, current: int) -> bool:
        return self.projects <= 0 or current <= self.projects

    def allows_users(self, current: int) -> bool:
        return self.users <= 0 or current <= self.users

    def has(self, feature: str) -> bool:
        return not self.features or feature in self.features

    def to_dict(self) -> dict:
        return {
            "edition": self.edition,
            "customer": self.customer,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "perpetual": self.perpetual,
            "days_left": self.days_left(),
            "expired": self.expired,
            "in_grace": self.in_grace,
            "blocked": self.blocked,
            "limits": {"projects": self.projects, "users": self.users},
            "features": list(self.features),
            "signed": self.signed,
            "note": self.note,
        }


def community() -> License:
    return License(edition=COMMUNITY["edition"],
                   projects=COMMUNITY["projects"],
                   users=COMMUNITY["users"],
                   signed=False)


# --------------------------------------------------------------------------- #
#  Verification
# --------------------------------------------------------------------------- #
def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _expected_block(digest: bytes, modulus_len: int) -> bytes:
    """The EMSA-PKCS1-v1_5 block a correct signature must decrypt to.

    Built here in full and compared byte for byte. The alternative — parsing
    whatever came out of the exponentiation and picking the digest out of it —
    is how signature checks get accepting of things they should not accept.
    """
    suffix = _SHA256_DIGEST_INFO + digest
    padding_len = modulus_len - len(suffix) - 3
    if padding_len < 8:
        raise LicenseError("The signature is too short for this key")
    return b"\x00\x01" + b"\xff" * padding_len + b"\x00" + suffix


def verify(token: str, *, modulus: int | None = None,
           exponent: int = PUBLIC_EXPONENT) -> dict:
    """Payload of a key whose signature checks out. Raises otherwise."""
    modulus = modulus if modulus is not None else int(PUBLIC_MODULUS_HEX, 16)

    token = (token or "").strip().replace("\n", "").replace(" ", "")
    if token.count(".") != 1:
        raise LicenseError("A licence key looks like «payload.signature»")

    payload_b64, signature_b64 = token.split(".")
    try:
        payload_raw = _b64url_decode(payload_b64)
        signature = _b64url_decode(signature_b64)
    except Exception as e:
        raise LicenseError(f"The key is not readable: {e}") from None

    modulus_len = (modulus.bit_length() + 7) // 8
    if len(signature) > modulus_len:
        raise LicenseError("The signature does not match the key size")

    recovered = pow(int.from_bytes(signature, "big"), exponent, modulus)
    recovered_bytes = recovered.to_bytes(modulus_len, "big")
    expected = _expected_block(hashlib.sha256(payload_raw).digest(), modulus_len)

    import hmac as _hmac

    if not _hmac.compare_digest(recovered_bytes, expected):
        raise LicenseError(
            "This key was not issued for VisTest, or has been edited after "
            "it was issued")

    try:
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as e:
        raise LicenseError(f"The key payload is not JSON: {e}") from None
    if not isinstance(payload, dict):
        raise LicenseError("The key payload is not an object")
    return payload


def parse(token: str, **kwargs) -> License:
    payload = verify(token, **kwargs)
    limits = payload.get("limits") or {}
    return License(
        edition=str(payload.get("edition") or "pro"),
        customer=str(payload.get("customer") or ""),
        issued_at=str(payload.get("issued_at") or ""),
        expires_at=str(payload.get("expires_at") or ""),
        projects=int(limits.get("projects") or 0),
        users=int(limits.get("users") or 0),
        features=list(payload.get("features") or []),
        note=str(payload.get("note") or ""),
        signed=True,
        raw=payload,
    )


# --------------------------------------------------------------------------- #
#  Where the key lives
# --------------------------------------------------------------------------- #
def key_path(root: str | Path | None = None) -> Path:
    base = Path(root or os.getenv("VISTEST_ROOT", ".vistest"))
    return base / KEY_FILENAME


def read_key(root: str | Path | None = None) -> str:
    """The key text: the environment first, then the data volume.

    The environment wins because that is how a container is configured, and a
    file left over from a previous term should not quietly outrank the value
    someone just set in their compose file.
    """
    from_env = (os.getenv(ENV_KEY) or "").strip()
    if from_env:
        return from_env
    path = key_path(root)
    try:
        return path.read_text("utf-8").strip() if path.exists() else ""
    except OSError:
        return ""


def load(root: str | Path | None = None) -> License:
    """The licence in force. Never raises: no key is a valid state."""
    token = read_key(root)
    if not token:
        return community()
    try:
        return parse(token)
    except LicenseError:
        # A broken key must not lock anyone out of their own installation:
        # they fall back to the free tier and see why on the licence screen.
        lic = community()
        lic.note = "the licence key could not be verified"
        return lic


def install(token: str, root: str | Path | None = None) -> License:
    """Check a key and write it to the data volume. Raises if it is not valid."""
    lic = parse(token)
    path = key_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:                                          # pragma: no cover
        pass
    return lic
