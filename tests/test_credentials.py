"""Credential resolution for unattended re-login (pcxa/_credentials.py)."""

import pytest

from pcxa._credentials import (
    auto_login_disabled,
    find_env_file,
    load_credentials,
    parse_env_file,
)


# ────────────────────────────────────────────────────────────────────────────
# Environment variables
# ────────────────────────────────────────────────────────────────────────────

def test_env_vars_win(monkeypatch):
    monkeypatch.setenv("PCXA_EMAIL", "bot@example.com")
    monkeypatch.setenv("PCXA_PASSWORD", "s3cret")
    assert load_credentials() == ("bot@example.com", "s3cret", "environment")


def test_username_alias(monkeypatch):
    monkeypatch.setenv("PCXA_USERNAME", "bot@example.com")
    monkeypatch.setenv("PCXA_PASSWORD", "s3cret")
    assert load_credentials()[0] == "bot@example.com"


def test_password_without_username_is_not_credentials(monkeypatch):
    monkeypatch.setenv("PCXA_PASSWORD", "s3cret")
    assert load_credentials() is None


def test_nothing_configured_returns_none():
    # The autouse fixture strips PCXA_* and points PCXA_ENV_FILE at a
    # nonexistent path, so this is the default state.
    assert load_credentials() is None


@pytest.mark.parametrize("value", ["0", "false", "no", "OFF", " 0 "])
def test_auto_login_kill_switch(monkeypatch, value):
    monkeypatch.setenv("PCXA_EMAIL", "bot@example.com")
    monkeypatch.setenv("PCXA_PASSWORD", "s3cret")
    monkeypatch.setenv("PCXA_AUTO_LOGIN", value)
    assert auto_login_disabled() is True
    assert load_credentials() is None


def test_auto_login_enabled_by_default(monkeypatch):
    assert auto_login_disabled() is False
    monkeypatch.setenv("PCXA_AUTO_LOGIN", "1")
    assert auto_login_disabled() is False


# ────────────────────────────────────────────────────────────────────────────
# .env parsing
# ────────────────────────────────────────────────────────────────────────────

def test_parse_env_file_handles_quotes_export_and_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "OTHER=ignored\n"
        'export PCXA_EMAIL= "bot@example.com" \n'
        "PCXA_PASSWORD='p@ss=word'\n"
        "PCXA_PASSWORD=later-wins-not\n"
    )
    parsed = parse_env_file(env, {"PCXA_EMAIL", "PCXA_PASSWORD"})
    assert parsed["PCXA_EMAIL"] == "bot@example.com"
    # Embedded '=' survives, and the first occurrence wins.
    assert parsed["PCXA_PASSWORD"] == "p@ss=word"
    assert "OTHER" not in parsed


def test_env_file_used_when_env_vars_absent(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("PCXA_EMAIL=bot@example.com\nPCXA_PASSWORD=from-file\n")
    monkeypatch.setenv("PCXA_ENV_FILE", str(env))

    assert load_credentials() == ("bot@example.com", "from-file", str(env))


def test_env_var_and_file_compose(monkeypatch, tmp_path):
    """A password in .env pairs with a username exported in the shell."""
    env = tmp_path / ".env"
    env.write_text("PCXA_PASSWORD=from-file\n")
    monkeypatch.setenv("PCXA_ENV_FILE", str(env))
    monkeypatch.setenv("PCXA_EMAIL", "shell@example.com")

    username, password, _ = load_credentials()
    assert (username, password) == ("shell@example.com", "from-file")


def test_missing_explicit_env_file_does_not_fall_back(monkeypatch, tmp_path):
    """A typo'd PCXA_ENV_FILE must not silently pick up some other .env."""
    (tmp_path / ".env").write_text("PCXA_EMAIL=wrong@example.com\nPCXA_PASSWORD=nope\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PCXA_ENV_FILE", str(tmp_path / "typo.env"))

    assert find_env_file() is None
    assert load_credentials() is None


def test_walks_up_from_cwd(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    (repo / ".env").write_text("PCXA_EMAIL=bot@example.com\nPCXA_PASSWORD=walked\n")
    monkeypatch.delenv("PCXA_ENV_FILE", raising=False)
    monkeypatch.chdir(nested)

    assert find_env_file() == repo / ".env"
    assert load_credentials()[1] == "walked"


def test_unreadable_env_file_is_not_fatal(tmp_path):
    assert parse_env_file(tmp_path / "does-not-exist.env", {"PCXA_EMAIL"}) == {}
