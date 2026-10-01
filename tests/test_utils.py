"""Tests for small helpers in autoshorts.utils (.env writing, redaction)."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from autoshorts import utils
from autoshorts.utils import redact, update_env

REPO = Path(__file__).resolve().parent.parent


def test_update_env_replaces_and_appends_keeping_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes("﻿# secrets\nPEXELS_API_KEY=px\nexport TIKTOK_ACCESS_TOKEN=old\n\n".encode("utf-8"))
    update_env(env, {"TIKTOK_ACCESS_TOKEN": "new", "TIKTOK_REFRESH_TOKEN": "r2"})
    assert env.read_text(encoding="utf-8") == (
        "# secrets\nPEXELS_API_KEY=px\nTIKTOK_ACCESS_TOKEN=new\n\nTIKTOK_REFRESH_TOKEN=r2\n"
    )
    if os.name == "posix":
        assert env.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in tmp_path.iterdir()] == [".env"]  # no temp files left


def test_update_env_rewrites_in_place_when_the_folder_is_not_writable(tmp_path, monkeypatch):
    """deploy/install.sh keeps the app folder root-owned; the service owns only .env."""
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")

    def denied(*a, **kw):
        raise PermissionError("folder not writable")

    monkeypatch.setattr(utils.tempfile, "mkstemp", denied)
    update_env(env, {"A": "2"})
    assert env.read_text(encoding="utf-8") == "A=2\n"


def test_tiktok_token_helper_uses_the_shared_writer():
    spec = importlib.util.spec_from_file_location("tiktok_token", REPO / "deploy" / "tiktok_token.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.update_env is update_env


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_tiktok_token_helper_explains_permission_errors(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("tiktok_token", REPO / "deploy" / "tiktok_token.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = tmp_path / ".env"
    env.write_text("TIKTOK_REFRESH_TOKEN=x\n", encoding="utf-8")
    env.chmod(0)
    try:
        if os.access(env, os.R_OK):  # running as root: permissions do not apply
            pytest.skip("root can read anything")
        assert module.main(["--env", str(env), "--refresh"]) == 2
    finally:
        env.chmod(0o600)
    assert "sudo -u autoshorts" in capsys.readouterr().err


def test_redact_masks_credential_query_values_only():
    text = "GET /api/videos/?key=SECRET&q=ocean&upload_token=T0K&access_token=A1 failed"
    assert redact(text) == "GET /api/videos/?key=***&q=ocean&upload_token=***&access_token=*** failed"
    assert redact("nothing to hide") == "nothing to hide"
