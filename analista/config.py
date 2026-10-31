"""Variáveis de ambiente -> Configuracao. Sem efeito colateral além de ler o .env."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from analista.sql_seguro import LIMITE_LINHAS

RAIZ = Path(__file__).resolve().parents[1]

_URL_PADRAO = "https://generativelanguage.googleapis.com/v1beta/openai/"
_MODELO_PADRAO = "gemini-3.5-flash-lite"


@dataclass(frozen=True)
class Configuracao:
    llm_base_url: str = _URL_PADRAO
    llm_model: str = _MODELO_PADRAO
    llm_api_key: str | None = field(default=None, repr=False)
    banco: Path = RAIZ / "data" / "pedidos.db"
    pasta_runs: Path = RAIZ / "runs"
    avaliacao: Path = RAIZ / "avaliacao" / "resultados" / "ultima.json"
    max_passos: int = 6
    limite_linhas: int = LIMITE_LINHAS
    limite_sessao: int = 10
    limite_dia: int = 200
    timeout_sql_s: float = 2.0

    @property
    def estado_limites(self) -> Path:
        return self.pasta_runs / "limite_dia.json"


def _valor_colado(valor: str | None) -> str:
    """Valor colado em painel de segredos: tira o espaço ou a quebra de linha do fim e as aspas
    que sobram em volta (o Gemini responde 400 a chave ou modelo com aspas)."""
    limpo = (valor or "").strip()
    while len(limpo) >= 2 and limpo[0] == limpo[-1] and limpo[0] in "\"'":
        limpo = limpo[1:-1].strip()
    return limpo


def carregar_configuracao(env: Mapping[str, str] | None = None) -> Configuracao:
    """Monta a configuração. Sem `env`, lê o `.env` da raiz e o ambiente do processo."""
    if env is None:
        load_dotenv(RAIZ / ".env", encoding="utf-8")
        env = os.environ

    padrao = Configuracao()
    chave = _valor_colado(env.get("LLM_API_KEY"))
    endereco = _valor_colado(env.get("LLM_BASE_URL"))
    modelo = _valor_colado(env.get("LLM_MODEL"))
    banco = env.get("BANCO_PATH")
    runs = env.get("RUNS_PATH")
    avaliacao = env.get("AVALIACAO_PATH")
    return Configuracao(
        llm_base_url=endereco or padrao.llm_base_url,
        llm_model=modelo or padrao.llm_model,
        llm_api_key=chave or None,
        banco=Path(banco) if banco else padrao.banco,
        pasta_runs=Path(runs) if runs else padrao.pasta_runs,
        avaliacao=Path(avaliacao) if avaliacao else padrao.avaliacao,
        limite_sessao=int(env.get("LIMITE_SESSAO") or padrao.limite_sessao),
        limite_dia=int(env.get("LIMITE_DIA") or padrao.limite_dia),
    )


def host_do_endereco(url: str) -> str | None:
    """Só o nome do servidor: sem usuário, porta, caminho nem parâmetros (que podem ter a chave)."""
    return urlparse(url).hostname
