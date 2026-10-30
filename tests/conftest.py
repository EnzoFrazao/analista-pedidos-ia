"""Fixtures compartilhadas. Nenhum teste chama o modelo nem lê o .env real."""

import pytest

VARIAVEIS = (
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "BANCO_PATH",
    "RUNS_PATH",
    "LIMITE_SESSAO",
    "LIMITE_DIA",
)


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tira do ambiente do processo as variáveis que a configuração lê."""
    for nome in VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)
