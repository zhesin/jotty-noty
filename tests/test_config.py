import pytest
from pydantic import ValidationError

from noty.config import ConfigError, Settings, load_users


def write(tmp_path, text):
    path = tmp_path / "users.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_users(tmp_path):
    users = load_users(write(tmp_path, """
- username: alice
  api_key: ck_a
  chat_id: 123
- username: john@example.com
  api_key: ck_b
  chat_id: "@team_channel"
"""))
    assert [(u.username, u.chat_id) for u in users] == [("alice", 123), ("john@example.com", "@team_channel")]
    assert users[0].api_key.get_secret_value() == "ck_a"
    assert "ck_a" not in repr(users[0])


@pytest.mark.parametrize("text", ["", "users: []", "[]"])
def test_requires_non_empty_list(tmp_path, text):
    with pytest.raises(ConfigError):
        load_users(write(tmp_path, text))


def test_rejects_duplicates(tmp_path):
    with pytest.raises(ConfigError, match="Duplicate"):
        load_users(write(tmp_path, "- {username: a, api_key: k, chat_id: 1}\n- {username: a, api_key: k, chat_id: 2}\n"))


def test_rejects_unsafe_username(tmp_path):
    with pytest.raises(ValidationError):
        load_users(write(tmp_path, "- {username: ../x, api_key: k, chat_id: 1}\n"))


def test_yaml_error_does_not_echo_content(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_users(write(tmp_path, "- username: a\n  api_key: ck_secret: [\n"))
    assert "ck_secret" not in str(info.value)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read"):
        load_users(tmp_path / "nope.yaml")


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JOTTY_URL", "http://jotty:3000")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("POLL_INTERVAL", "30")
    monkeypatch.setenv("JOTTY_DATA_DIR", "/jotty-data")

    settings = Settings()

    assert settings.poll_interval == 30
    assert settings.app_url is None
    assert str(settings.jotty_data_dir) == "/jotty-data"
    assert settings.telegram_bot_token.get_secret_value() == "123:abc"


def test_empty_values_count_as_unset(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JOTTY_URL", "http://jotty:3000")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("JOTTY_DATA_DIR", "")
    monkeypatch.setenv("APP_URL", " ")

    settings = Settings()

    assert settings.jotty_data_dir is None
    assert settings.app_url is None


def test_log_level(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JOTTY_URL", "http://jotty:3000")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert Settings().log_level == "DEBUG"
    monkeypatch.setenv("LOG_LEVEL", "loud")
    with pytest.raises(ValidationError):
        Settings()


def test_describe_hides_input_values(tmp_path):
    from noty.__main__ import _describe

    with pytest.raises(ValidationError) as info:
        load_users(write(tmp_path, "- {api_key: ck_secret, chat_id: 1}\n"))
    text = _describe(info.value)
    assert "username" in text
    assert "ck_secret" not in text
