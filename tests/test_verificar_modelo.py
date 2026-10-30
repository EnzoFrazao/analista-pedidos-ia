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


def test_caminho_ok_com_cliente_falso():
    modelo = ModeloFalso(
        [MensagemModelo(None, (ChamadaFerramenta("1", "responder_ok", '{"texto": "ok"}'),))]
    )

    codigo, texto = _rodar(Configuracao(llm_api_key=CHAVE), modelo)

    assert codigo == 0
    assert "chave: OK" in texto
    assert "modelo: OK" in texto
    assert "tool calling: OK" in texto
    assert CHAVE not in texto
    assert len(modelo.mensagens) == 1  # UMA chamada só


def test_modelo_que_nao_chama_a_ferramenta_e_erro_de_tool_calling():
    modelo = ModeloFalso([MensagemModelo("ok", ())])

    codigo, texto = _rodar(Configuracao(llm_api_key=CHAVE), modelo)

    assert codigo == 1
    assert "tool calling: erro" in texto
    assert "modelo: OK" in texto


@pytest.mark.parametrize(
    ("tipo", "esperado"),
    [("chave", "chave: erro"), ("modelo", "modelo: erro"), ("limite", "chave: OK")],
)
def test_erro_do_provedor_aponta_o_que_falhou(tipo: str, esperado: str):
    modelo = ModeloFalso([ErroDoProvedor("mensagem amigável", tipo)])

    codigo, texto = _rodar(Configuracao(llm_api_key=CHAVE), modelo)

    assert codigo == 1
    assert esperado in texto
    assert "mensagem amigável" in texto
    assert CHAVE not in texto
