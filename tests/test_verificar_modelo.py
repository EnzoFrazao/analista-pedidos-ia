"""Smoke test do modelo: a lógica é testada com cliente falso, sem rede."""

import pytest

from analista import verificar_modelo
from analista.config import Configuracao
from analista.llm import ChamadaFerramenta, ErroDoProvedor, MensagemModelo
from tests.fakes import ModeloFalso

CHAVE = "chave-secreta-de-teste-123"


def _rodar(config: Configuracao, modelo=None) -> tuple[int, str]:
    linhas: list[str] = []
    codigo = verificar_modelo.verificar(config, modelo=modelo, saida=linhas.append)
    return codigo, "\n".join(linhas)


def test_sem_chave_explica_como_configurar_e_sai_com_2():
    codigo, texto = _rodar(Configuracao(llm_api_key=None))

    assert codigo == 2
    assert "LLM_API_KEY" in texto
    assert ".env" in texto


def _pede_ok(extra: dict | None = None) -> MensagemModelo:
    chamada = ChamadaFerramenta("1", "responder_ok", '{"texto": "ok"}', extra)
    return MensagemModelo(None, (chamada,))


def _responde(texto: str = "ok") -> MensagemModelo:
    return MensagemModelo(texto, ())


def test_caminho_ok_com_cliente_falso():
    modelo = ModeloFalso([_pede_ok(), _responde("Recebi o resultado.")])

    codigo, texto = _rodar(Configuracao(llm_api_key=CHAVE), modelo)

    assert codigo == 0
    assert "chave: OK" in texto
    assert "modelo: OK" in texto
    assert "tool calling: OK" in texto
    assert CHAVE not in texto
    assert len(modelo.mensagens) == 2  # pedido + volta com o resultado


def test_a_volta_reenvia_o_pedido_e_o_resultado_pelo_caminho_do_agente():
    modelo = ModeloFalso(
        [_pede_ok({"extra_content": {"google": {"thought_signature": "abc"}}}), _responde()]
    )

    _rodar(Configuracao(llm_api_key=CHAVE), modelo)

    volta = modelo.mensagens[1]
    assert [m["role"] for m in volta] == ["user", "assistant", "tool"]
    (chamada,) = volta[1]["tool_calls"]
    assert chamada["id"] == "1"
    assert chamada["extra_content"] == {"google": {"thought_signature": "abc"}}
    assert volta[2]["tool_call_id"] == "1"
    assert modelo.ferramentas[1]  # a segunda chamada também leva a ferramenta


def test_volta_que_o_provedor_rejeita_nao_e_tool_calling_ok():
    modelo = ModeloFalso([_pede_ok(), ErroDoProvedor("histórico recusado", "outro")])

    codigo, texto = _rodar(Configuracao(llm_api_key=CHAVE), modelo)

    assert codigo == 1
    assert "tool calling: erro" in texto
    assert "tool calling: OK" not in texto
    assert "histórico recusado" in texto
    assert "chave: OK" in texto  # o primeiro turno passou


@pytest.mark.parametrize("segunda", [MensagemModelo(None, ()), MensagemModelo("  ", ())])
def test_volta_sem_texto_nao_e_tool_calling_ok(segunda: MensagemModelo):
    codigo, texto = _rodar(Configuracao(llm_api_key=CHAVE), ModeloFalso([_pede_ok(), segunda]))

    assert codigo == 1
    assert "tool calling: erro" in texto
    assert "tool calling: OK" not in texto


def test_volta_que_pede_a_ferramenta_de_novo_nao_e_tool_calling_ok():
    codigo, texto = _rodar(Configuracao(llm_api_key=CHAVE), ModeloFalso([_pede_ok(), _pede_ok()]))

    assert codigo == 1
    assert "tool calling: OK" not in texto


def test_modelo_que_nao_chama_a_ferramenta_e_erro_de_tool_calling():
    modelo = ModeloFalso([MensagemModelo("ok", ())])

    codigo, texto = _rodar(Configuracao(llm_api_key=CHAVE), modelo)

    assert codigo == 1
    assert "tool calling: erro" in texto
    assert "modelo: OK" in texto
    assert len(modelo.mensagens) == 1


@pytest.mark.parametrize(
    ("tipo", "esperado", "ausente"),
    [
        ("chave", "chave: erro", None),
        ("modelo", "modelo: erro", "chave: erro"),
        ("limite", "chave: OK", "chave: erro"),
        ("conexao", "chave: não verificado", "chave: erro"),
        ("outro", "chave: não verificado", "chave: erro"),
    ],
)
def test_erro_do_provedor_aponta_so_o_que_falhou(tipo: str, esperado: str, ausente: str | None):
    modelo = ModeloFalso([ErroDoProvedor("mensagem amigável", tipo)])

    codigo, texto = _rodar(Configuracao(llm_api_key=CHAVE), modelo)

    assert codigo == 1
    assert esperado in texto
    if ausente:
        assert ausente not in texto
    assert "mensagem amigável" in texto
    assert "tool calling: não verificado" in texto
    assert CHAVE not in texto
