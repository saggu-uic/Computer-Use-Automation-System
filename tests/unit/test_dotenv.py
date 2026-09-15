import os

import pytest

from rote.config import ENV_ANTHROPIC_KEY, ENV_GEMINI_KEY, ENV_OPERATOR_PASSWORD, load_dotenv_keys


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # setenv (not delenv) so monkeypatch restores the real values after the loader writes os.environ
    for name in (ENV_ANTHROPIC_KEY, ENV_GEMINI_KEY, ENV_OPERATOR_PASSWORD):
        monkeypatch.setenv(name, "")


def test_loads_only_model_keys(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# model keys\n\nexport ANTHROPIC_API_KEY=\"sk-test-1\"\nGEMINI_API_KEY = 'g-test-2'\n"
        f"{ENV_OPERATOR_PASSWORD}=not-from-dotenv\nnot a pair\n",
        encoding="utf-8",
    )
    assert load_dotenv_keys(path) == [ENV_ANTHROPIC_KEY, ENV_GEMINI_KEY]
    assert os.environ[ENV_ANTHROPIC_KEY] == "sk-test-1"
    assert os.environ[ENV_GEMINI_KEY] == "g-test-2"
    assert os.environ[ENV_OPERATOR_PASSWORD] == ""


def test_real_environment_variable_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_ANTHROPIC_KEY, "from-env")
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
    assert load_dotenv_keys(path) == []
    assert os.environ[ENV_ANTHROPIC_KEY] == "from-env"


def test_missing_file_is_a_no_op(tmp_path):
    assert load_dotenv_keys(tmp_path / ".env") == []


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_windows_encodings(tmp_path, encoding):
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=sk-test-3\r\n", encoding=encoding)
    assert load_dotenv_keys(path) == [ENV_ANTHROPIC_KEY]
    assert os.environ[ENV_ANTHROPIC_KEY] == "sk-test-3"
