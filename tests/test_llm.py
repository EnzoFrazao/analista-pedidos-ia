"""Cliente do modelo: mapeamento da resposta do SDK, tentativas em 429 e erros amigáveis."""

from types import SimpleNamespace

import httpx2
import openai
import pytest

from analista.config import Configuracao
from analista.llm import ClienteOpenAI, ErroDoProvedor, MensagemModelo, criar_modelo

CHAVE = "chave-secreta-de-teste-123"


class _Completions:
    def __init__(self, respostas: list):
        self._respostas = list(respostas)
        self.chamadas: list[dict] = []

    def create(self, **kwargs):
        self.chamadas.append(kwargs)
        resposta = self._respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


class SdkFalso:
    """Imita só o trecho `cliente.chat.completions.create` do SDK."""

    def __init__(self, respostas: list):
        self.completions = _Completions(respostas)
        self.chat = SimpleNamespace(completions=self.completions)


def _resposta(conteudo: str | None = None, chamadas: list | None = None):
    mensagem = SimpleNamespace(content=conteudo, tool_calls=chamadas)
    return SimpleNamespace(choices=[SimpleNamespace(message=mensagem)])


def _chamada(id_: str | None, nome: str, argumentos: str | None):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=nome, arguments=argumentos))


def _erro_429() -> openai.RateLimitError:
    resposta = httpx2.Response(429, request=httpx2.Request("POST", "http://x"))
    return openai.RateLimitError("limite", response=resposta, body=None)


def _erro_http(classe: type[openai.APIStatusError], status: int, texto: str):
    resposta = httpx2.Response(status, request=httpx2.Request("POST", "http://x"))
    return classe(texto, response=resposta, body=None)


def _cliente(sdk: SdkFalso, esperas: tuple[float, ...] = (0, 0, 0)) -> ClienteOpenAI:
    return ClienteOpenAI(Configuracao(llm_api_key=CHAVE), cliente=sdk, esperas_s=esperas)


def test_mapeia_resposta_do_sdk_para_mensagem_modelo():
    sdk = SdkFalso(
        [_resposta("pensando", [_chamada("c1", "executar_sql", '{"consulta": "SELECT 1"}')])]
    )

    mensagem = _cliente(sdk).completar([{"role": "user", "content": "oi"}], [])

    assert isinstance(mensagem, MensagemModelo)
    assert mensagem.conteudo == "pensando"
    assert len(mensagem.chamadas) == 1
    assert mensagem.chamadas[0].id == "c1"
    assert mensagem.chamadas[0].nome == "executar_sql"
    assert mensagem.chamadas[0].argumentos == '{"consulta": "SELECT 1"}'


def test_resposta_so_com_texto_nao_tem_chamadas():
    mensagem = _cliente(SdkFalso([_resposta("olá", None)])).completar([], [])

    assert mensagem == MensagemModelo(conteudo="olá", chamadas=())


def test_tenta_de_novo_em_429_e_depois_sucesso():
    sdk = SdkFalso([_erro_429(), _erro_429(), _resposta("pronto")])

    mensagem = _cliente(sdk).completar([], [])

    assert mensagem.conteudo == "pronto"
    assert len(sdk.completions.chamadas) == 3


def test_429_esgotado_levanta_erro_do_provedor():
    sdk = SdkFalso([_erro_429() for _ in range(4)])

    with pytest.raises(ErroDoProvedor) as erro:
        _cliente(sdk).completar([], [])

    assert len(sdk.completions.chamadas) == 4  # 1 tentativa + 3 esperas
    assert "limite" in str(erro.value).lower()
    assert erro.value.tipo == "limite"


def test_espera_entre_tentativas_usa_as_esperas_configuradas(monkeypatch: pytest.MonkeyPatch):
    dormidas: list[float] = []
    monkeypatch.setattr("analista.llm.time.sleep", dormidas.append)
    sdk = SdkFalso([_erro_429(), _erro_429(), _resposta("ok")])

    _cliente(sdk, esperas=(1.5, 3.0, 6.0)).completar([], [])

    assert dormidas == [1.5, 3.0]


def test_envia_temperatura_zero_e_max_tokens():
    sdk = SdkFalso([_resposta("ok")])
    ferramentas = [{"type": "function", "function": {"name": "f"}}]
    mensagens = [{"role": "user", "content": "oi"}]

    _cliente(sdk).completar(mensagens, ferramentas)

    enviado = sdk.completions.chamadas[0]
    assert enviado["temperature"] == 0
    assert enviado["max_tokens"] == 800
    assert enviado["model"] == Configuracao().llm_model
    assert enviado["messages"] == mensagens
    assert enviado["tools"] == ferramentas


def test_sem_ferramentas_nao_envia_o_campo_tools():
    sdk = SdkFalso([_resposta("ok")])

    _cliente(sdk).completar([], [])

    assert "tools" not in sdk.completions.chamadas[0]


def test_chamada_sem_id_recebe_id_gerado():
    sdk = SdkFalso(
        [
            _resposta(
                None,
                [
                    _chamada(None, "descrever_esquema", ""),
                    _chamada("", "executar_sql", "{}"),
                    _chamada("real", "executar_sql", "{}"),
                ],
            )
        ]
    )

    chamadas = _cliente(sdk).completar([], []).chamadas

    ids = [c.id for c in chamadas]
    assert all(ids)
    assert len(set(ids)) == 3
    assert ids[2] == "real"


def test_argumentos_ausentes_viram_texto_vazio():
    sdk = SdkFalso([_resposta(None, [_chamada("c", "descrever_esquema", None)])])

    assert _cliente(sdk).completar([], []).chamadas[0].argumentos == ""


@pytest.mark.parametrize(
    ("erro", "trecho", "tipo"),
    [
        (
            _erro_http(openai.AuthenticationError, 401, f"Incorrect API key: {CHAVE}"),
            "chave",
            "chave",
        ),
        (_erro_http(openai.NotFoundError, 404, f"model not found {CHAVE}"), "modelo", "modelo"),
        (_erro_http(openai.InternalServerError, 500, "boom"), "provedor", "outro"),
    ],
)
def test_outros_erros_da_api_viram_erro_do_provedor_sem_vazar_a_chave(erro, trecho, tipo):
    sdk = SdkFalso([erro])

    with pytest.raises(ErroDoProvedor) as capturado:
        _cliente(sdk).completar([], [])

    assert trecho in str(capturado.value).lower()
    assert capturado.value.tipo == tipo
    assert CHAVE not in str(capturado.value)
    assert CHAVE not in repr(capturado.value)
    assert len(sdk.completions.chamadas) == 1  # não insiste em erro que não é de limite


def test_erro_de_conexao_vira_erro_do_provedor():
    erro = openai.APIConnectionError(request=httpx2.Request("POST", "http://x"))

    with pytest.raises(ErroDoProvedor) as capturado:
        _cliente(SdkFalso([erro])).completar([], [])

    assert "conex" in str(capturado.value).lower()
    assert capturado.value.tipo == "conexao"


def test_criar_modelo_sem_chave_devolve_none():
    assert criar_modelo(Configuracao(llm_api_key=None)) is None


def test_criar_modelo_com_chave_devolve_cliente():
    assert isinstance(criar_modelo(Configuracao(llm_api_key=CHAVE)), ClienteOpenAI)
