# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The directory settings routes, served by `DirectoryAuthProvider`.

A module of its own, and deliberately without ``from __future__ import
annotations``: FastAPI reads the parameter annotations of a route to know
what to inject, and postponed (string) annotations are resolved against the
module's globals — where `directory.py` cannot import FastAPI at the top, since
it is also loaded in the library mode, which has no web framework.
"""

from fastapi import APIRouter, Body, HTTPException, Request

from . import directory


def build_router(host) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> dict:
        return host.require(request, "admin")

    @router.get("/api/ldap")
    def ldap_settings(request: Request):
        """Directory settings. The service account password never leaves."""
        admin(request)
        cfg = directory.settings(host.settings)
        return cfg.to_dict(bind_password_set=bool(directory.bind_password()))

    @router.put("/api/ldap")
    def ldap_save(request: Request, payload: dict = Body(...)):
        me_ = admin(request)
        # Turning the integration on without checking it is a way to find out
        # it does not work in front of everyone on a Monday morning.
        if payload.get("enabled"):
            cfg = directory.save_settings(host.settings,
                                          {**payload, "enabled": False},
                                          me_["login"])
            check = directory.probe(cfg)
            if not check["ok"]:
                raise HTTPException(
                    400, f"The settings are saved but the directory is not "
                         f"turned on: {check['error']}")
        cfg = directory.save_settings(host.settings, payload, me_["login"])
        host.audit(me_["login"], "ldap.configured", cfg.server,
                   enabled=cfg.enabled, base_dn=cfg.base_dn)
        return cfg.to_dict(bind_password_set=bool(directory.bind_password()))

    @router.post("/api/ldap/test")
    def ldap_test(request: Request, payload: dict = Body(default={})):
        """Connection check, step by step: "it does not work" is unfixable."""
        admin(request)
        # What is in the form right now, not what is saved: otherwise a setting
        # would have to be saved to find out whether it is right.
        cfg = directory.settings(host.settings)
        for key, value in (payload.get("settings") or {}).items():
            if hasattr(cfg, key) and value is not None:
                if key == "role_map" and isinstance(value, str):
                    value = directory.parse_role_map(value)
                setattr(cfg, key, value)
        return directory.probe(cfg, str(payload.get("login") or "").strip())

    return router
