"""Interface do modelo e o cliente para APIs compatíveis com a da OpenAI.

Único arquivo do projeto que importa `openai`: trocar de provedor ou de SDK mexe só aqui.
O restante do código fala com `ModeloLLM` e recebe `MensagemModelo`.
"""

import itertools
import time
from dataclasses import dataclass
from typing import Protocol

import openai

from analista.config import Configuracao

TEMPERATURA = 0
MAX_TOKENS = 800
_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class ChamadaFerramenta:
    id: str
    nome: str
    argumentos: str


@dataclass(frozen=True)
class MensagemModelo:
    conteudo: str | None
    chamadas: tuple[ChamadaFerramenta, ...]


class ModeloLLM(Protocol):
    def completar(self, mensagens: list[dict], ferramentas: list[dict]) -> MensagemModelo: ...


class ErroDoProvedor(Exception):
    """Falha do serviço do modelo, em português e sem a chave, pronta para mostrar ao usuário.

    `tipo` ajuda o diagnóstico: "chave", "modelo", "limite", "conexao" ou "outro".
    """

    def __init__(self, mensagem: str, tipo: str = "outro"):
        super().__init__(mensagem)
        self.tipo = tipo


def _erro_amigavel(erro: openai.APIError) -> ErroDoProvedor:
    """Mensagem própria por tipo de erro. Nunca reaproveita o texto do SDK: o do provedor
    pode ecoar a chave. A causa original também não é encadeada, pelo mesmo motivo."""
    if isinstance(erro, openai.RateLimitError):
        return ErroDoProvedor(
            "O serviço do modelo atingiu o limite de uso agora. Tente de novo em alguns minutos.",
            "limite",
        )
    if isinstance(erro, openai.AuthenticationError | openai.PermissionDeniedError):
        return ErroDoProvedor(
            "O serviço do modelo recusou a chave de acesso. Confira LLM_API_KEY.", "chave"
        )
    if isinstance(erro, openai.NotFoundError):
        return ErroDoProvedor(
            "O serviço do modelo não encontrou o modelo configurado. Confira LLM_MODEL "
            "e LLM_BASE_URL.",
            "modelo",
        )
    if isinstance(erro, openai.APIConnectionError):
        return ErroDoProvedor(
            "Não consegui conexão com o serviço do modelo. Confira a rede e LLM_BASE_URL.",
            "conexao",
        )
    status = getattr(erro, "status_code", None)
    detalhe = f" (código {status})" if status else ""
    return ErroDoProvedor(
        f"O provedor do modelo devolveu um erro{detalhe}. Tente de novo mais tarde.", "outro"
    )


class ClienteOpenAI:
    """Fala com qualquer endereço compatível com `chat.completions` da OpenAI (Gemini inclusive)."""

    def __init__(
        self,
        config: Configuracao,
        cliente=None,
        esperas_s: tuple[float, ...] = (2, 4, 8),
    ):
        self._modelo = config.llm_model
        self._esperas_s = esperas_s
        self._contador = itertools.count(1)
        # max_retries=0: as tentativas em 429 são nossas, com esperas conhecidas e testáveis.
        self._cliente = cliente or openai.OpenAI(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            timeout=_TIMEOUT_S,
            max_retries=0,
        )

    def completar(self, mensagens: list[dict], ferramentas: list[dict]) -> MensagemModelo:
        parametros: dict = {
            "model": self._modelo,
            "messages": mensagens,
            "temperature": TEMPERATURA,
            "max_tokens": MAX_TOKENS,
        }
        if ferramentas:
            parametros["tools"] = ferramentas

        tentativas = len(self._esperas_s) + 1
        for tentativa in range(tentativas):
            try:
                resposta = self._cliente.chat.completions.create(**parametros)
                break
            except openai.RateLimitError as erro:
                if tentativa == tentativas - 1:
                    raise _erro_amigavel(erro) from None
                time.sleep(self._esperas_s[tentativa])
            except openai.APIError as erro:
                raise _erro_amigavel(erro) from None
        mensagem = resposta.choices[0].message
        chamadas = tuple(
            ChamadaFerramenta(
                # Alguns provedores compatíveis omitem o id; o contador evita repetir entre passos.
                id=chamada.id or f"chamada_{next(self._contador)}",
                nome=chamada.function.name,
                argumentos=chamada.function.arguments or "",
            )
            for chamada in mensagem.tool_calls or []
        )
        return MensagemModelo(conteudo=mensagem.content, chamadas=chamadas)


def criar_modelo(config: Configuracao) -> ModeloLLM | None:
    """O cliente real, ou `None` quando não há chave (quem chama decide o que mostrar)."""
    if not config.llm_api_key:
        return None
    return ClienteOpenAI(config)
