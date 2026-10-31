"""Fixtures compartilhadas. Nenhum teste chama o modelo nem lê o .env real."""

from pathlib import Path

import pytest

from analista import config
from analista.dados import gerar_banco

VARIAVEIS = (
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "BANCO_PATH",
    "RUNS_PATH",
    "AVALIACAO_PATH",
    "LIMITE_SESSAO",
    "LIMITE_DIA",
)


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tira do ambiente do processo as variáveis que a configuração lê e impede a leitura do
    `.env` do desenvolvedor (que pode ter a chave real): `load_dotenv` vira uma função que não
    lê nada. Um teste que precise ver a chamada troca de novo dentro dele."""
    for nome in VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)


@pytest.fixture(scope="session")
def banco_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Banco sintético gerado uma vez por sessão (semente padrão)."""
    caminho = tmp_path_factory.mktemp("dados") / "pedidos.db"
    gerar_banco(caminho)
    return caminho
