"""Testes do painel Streamlit com `AppTest`. Nenhum chama modelo de verdade nem toca em data/ ou runs/.

O ambiente de cada teste aponta o banco e os rastros para pastas temporárias e troca a raiz do
projeto por uma pasta temporária, o que também impede o painel de ler o `.env` e o
`ultima.json` reais da máquina de quem roda os testes.
"""

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from analista import agente, config, llm, sql_seguro
from analista.dados import FIM, INICIO
from analista.llm import ChamadaFerramenta, ErroDoProvedor, MensagemModelo
from tests.fakes import ModeloFalso

PAINEL = str(Path(__file__).resolve().parents[1] / "app" / "painel.py")
BANNER = "Dados sintéticos de um restaurante fictício. Demonstração."
SEGUNDAS = [INICIO + timedelta(days=7 * n) for n in range(13)]
TEXTO_PROVEDOR = (
    "O serviço do modelo atingiu o limite de uso agora. Tente de novo em alguns minutos."
)


@pytest.fixture(autouse=True)
def ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, banco_path: Path) -> Path:
    monkeypatch.setenv("BANCO_PATH", str(banco_path))
    monkeypatch.setenv("RUNS_PATH", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "RAIZ", tmp_path)
    st.cache_resource.clear()  # o modelo fica em cache entre execuções do script
    yield tmp_path
    st.cache_resource.clear()


def abrir() -> AppTest:
    at = AppTest.from_file(PAINEL, default_timeout=120)
    at.run()
    return at


def usar_modelo(monkeypatch: pytest.MonkeyPatch, modelo: ModeloFalso) -> None:
    monkeypatch.setattr(llm, "criar_modelo", lambda configuracao: modelo)


def proibir_agente(monkeypatch: pytest.MonkeyPatch) -> None:
    def _nunca(self, pergunta):
        raise AssertionError("o modelo não podia ser chamado")

    monkeypatch.setattr(agente.Agente, "perguntar", _nunca)


def consulta(sql: str, chamada_id: str = "c1") -> MensagemModelo:
    return MensagemModelo(
        None, (ChamadaFerramenta(chamada_id, "executar_sql", json.dumps({"consulta": sql})),)
    )


def resposta(texto: str) -> MensagemModelo:
    return MensagemModelo(texto, ())


def perguntar(at: AppTest, texto: str) -> None:
    at.chat_input[0].set_value(texto).run()


def textos(elementos) -> list[str]:
    return [e.value for e in elementos]


def tudo_que_aparece(at: AppTest) -> str:
    """Texto de tudo o que o painel desenhou, para procurar o que não deveria estar lá."""
    partes = textos(at.markdown) + textos(at.caption) + textos(at.warning) + textos(at.error)
    partes += textos(at.info) + textos(at.code) + [m.value for m in at.metric]
    for tabela in at.dataframe:
        partes.append(tabela.value.to_string())
    return "\n".join(str(p) for p in partes)


# --- abertura ---------------------------------------------------------------------------------


def test_abre_sem_chave_sem_excecao_e_com_as_tres_abas():
    at = abrir()

    assert not at.exception
    assert [aba.label for aba in at.tabs] == ["Painel", "Pergunte", "Avaliação"]
    assert BANNER in textos(at.info)
    assert "iFood" not in tudo_que_aparece(at)


def test_gera_o_banco_quando_ele_nao_existe(monkeypatch, tmp_path):
    novo = tmp_path / "novo" / "pedidos.db"
    monkeypatch.setenv("BANCO_PATH", str(novo))

    at = abrir()

    assert not at.exception
    assert novo.exists()


# --- chat -------------------------------------------------------------------------------------


def test_sem_chave_o_chat_avisa_e_nao_chama_modelo(monkeypatch, ambiente):
    proibir_agente(monkeypatch)
    at = abrir()
    assert any("LLM_API_KEY" in aviso for aviso in textos(at.warning))

    perguntar(at, "Qual foi o faturamento da última semana?")

    assert not at.exception
    assert any("LLM_API_KEY" in aviso for aviso in textos(at.warning))
    assert len(at.chat_message) == 0
    assert not (ambiente / "runs").exists()  # nada foi contado nem rastreado


def test_com_modelo_falso_mostra_resposta_e_a_consulta(monkeypatch, ambiente):
    sql = "SELECT COUNT(*) AS total FROM pedidos"
    modelo = ModeloFalso([consulta(sql), resposta("Foram registrados muitos pedidos.")])
    usar_modelo(monkeypatch, modelo)
    at = abrir()
    assert any("0 de 10 perguntas" in c for c in textos(at.caption))

    perguntar(at, "Quantos pedidos existem?")

    assert not at.exception
    mensagens = [m for m in at.chat_message]
    assert [m.name for m in mensagens] == ["user", "assistant"]
    assert "Foram registrados muitos pedidos." in textos(mensagens[1].markdown)
    assert any(sql in codigo for codigo in textos(at.code))
    assert any("Consulta usada" in e.label for e in at.expander)
    assert any("1 de 10 perguntas" in c for c in textos(at.caption))
    assert len(list((ambiente / "runs").glob("*.jsonl"))) == 1  # rastro da pergunta
    estado = json.loads((ambiente / "runs" / "limite_dia.json").read_text(encoding="utf-8"))
    assert estado["n"] == 1


def test_resultado_truncado_e_avisado(monkeypatch):
    modelo = ModeloFalso([consulta("SELECT id FROM pedidos"), resposta("Lista parcial.")])
    usar_modelo(monkeypatch, modelo)
    at = abrir()

    perguntar(at, "Liste os pedidos.")

    assert not at.exception
    assert any("truncado" in c.lower() for c in textos(at.caption))


def test_respeita_o_limite_de_10_por_sessao(monkeypatch):
    modelo = ModeloFalso([resposta(f"Resposta {n}.") for n in range(10)])
    usar_modelo(monkeypatch, modelo)
    at = abrir()

    for n in range(10):
        perguntar(at, f"Pergunta {n}?")
        assert not at.exception
    assert any("10 de 10 perguntas" in c for c in textos(at.caption))

    perguntar(at, "Mais uma?")  # a 11ª não chega ao modelo (o roteiro acabou: chamar seria erro)

    assert not at.exception
    assert any("10 perguntas" in aviso for aviso in textos(at.warning))
    assert len(modelo.mensagens) == 10
    assert len(at.chat_message) == 20


def test_erro_do_provedor_aparece_como_aviso(monkeypatch, ambiente):
    usar_modelo(monkeypatch, ModeloFalso([ErroDoProvedor(TEXTO_PROVEDOR, "limite")]))
    at = abrir()

    perguntar(at, "Quanto faturamos?")

    assert not at.exception
    assert TEXTO_PROVEDOR in textos(at.warning)
    assert "Traceback" not in tudo_que_aparece(at)
    assert any("0 de 10 perguntas" in c for c in textos(at.caption))  # pergunta não contada
    assert not (ambiente / "runs" / "limite_dia.json").exists()


def test_falha_inesperada_nao_mostra_stack_trace(monkeypatch):
    usar_modelo(monkeypatch, ModeloFalso([RuntimeError("segredo-que-nao-pode-aparecer")]))
    at = abrir()

    perguntar(at, "Quanto faturamos?")

    assert not at.exception
    assert at.error
    assert "segredo-que-nao-pode-aparecer" not in tudo_que_aparece(at)
    assert "Traceback" not in tudo_que_aparece(at)
    assert any("0 de 10 perguntas" in c for c in textos(at.caption))


@pytest.mark.parametrize("texto", ["   ", "a" * 1001])
def test_pergunta_vazia_ou_longa_nao_chama_o_modelo(monkeypatch, texto):
    proibir_agente(monkeypatch)
    usar_modelo(monkeypatch, ModeloFalso([]))
    at = abrir()

    perguntar(at, texto)

    assert not at.exception
    assert at.warning
    assert len(at.chat_message) == 0
    assert any("0 de 10 perguntas" in c for c in textos(at.caption))


def test_duas_perguntas_seguidas_funcionam(monkeypatch):
    conexoes: list[sqlite3.Connection] = []
    original = sql_seguro.abrir_somente_leitura

    def registrando(caminho):
        conn = original(caminho)
        conexoes.append(conn)
        return conn

    monkeypatch.setattr(sql_seguro, "abrir_somente_leitura", registrando)
    monkeypatch.setattr(agente, "abrir_somente_leitura", registrando)
    modelo = ModeloFalso(
        [
            consulta("SELECT COUNT(*) FROM pedidos"),
            resposta("Primeira resposta."),
            consulta("SELECT COUNT(*) FROM itens_cardapio"),
            resposta("Segunda resposta."),
        ]
    )
    usar_modelo(monkeypatch, modelo)
    at = abrir()

    perguntar(at, "Primeira?")
    perguntar(at, "Segunda?")

    assert not at.exception
    respostas = [textos(m.markdown) for m in at.chat_message if m.name == "assistant"]
    assert respostas == [["Primeira resposta."], ["Segunda resposta."]]
    assert len({id(c) for c in conexoes}) == len(conexoes) >= 4  # nenhuma conexão reaproveitada
    for conn in conexoes:  # e todas fechadas
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


# --- painel -----------------------------------------------------------------------------------


def test_seletor_de_semana_vai_das_segundas_com_a_ultima_como_padrao():
    at = abrir()

    seletor = at.selectbox[0]
    assert seletor.options[0].startswith("29/06/2026")
    assert len(seletor.options) == 13
    assert seletor.value == SEGUNDAS[-1]
    assert SEGUNDAS[-1] + timedelta(days=6) == FIM
    assert {m.label for m in at.metric} >= {"Pedidos válidos", "Faturamento", "Ticket médio"}
    assert all(m.delta for m in at.metric)  # a última semana tem semana anterior


def test_primeira_semana_nao_mostra_variacao():
    at = abrir()

    at.selectbox[0].select(SEGUNDAS[0]).run()

    assert not at.exception
    assert len(at.metric) == 5
    assert all(not m.delta for m in at.metric)


def test_graficos_nativos_de_pedidos_por_dia_e_tempo_por_bairro():
    at = abrir()

    assert len(at.get("vega_lite_chart")) == 2


def test_ranking_do_painel_nao_mostra_o_item_com_instrucao_escondida(banco_path):
    conn = sqlite3.connect(banco_path)
    try:
        escondido = conn.execute(
            "SELECT nome FROM itens_cardapio WHERE nome LIKE '%IGNORE%'"
        ).fetchall()
    finally:
        conn.close()
    assert len(escondido) == 1  # o teste só vale se o item armadilha existe nos dados

    at = abrir()
    for semana in SEGUNDAS:
        at.selectbox[0].select(semana).run()
        assert not at.exception
        ranking = [t for t in at.dataframe if "Item" in t.value.columns]
        assert len(ranking) == 1 and len(ranking[0].value) == 5
        visivel = tudo_que_aparece(at)
        assert "IGNORE" not in visivel
        assert "DELETE" not in visivel


# --- avaliação --------------------------------------------------------------------------------


def grupo(media, pior, n):
    return {"media": media, "pior_caso": pior, "n": n}


def resultado_de_avaliacao() -> dict:
    return {
        "gerado_em": "2026-11-02T10:00:00-03:00",
        "commit": "abc1234",
        "sujo": False,
        "modelo": "modelo-de-teste",
        "endpoint_host": "exemplo.test",
        "temperatura": 0,
        "max_passos": 6,
        "hash_prompt": "p",
        "hash_banco": "b",
        "hash_perguntas": "q",
        "repeticoes": 3,
        "categoria_filtrada": None,
        "linha_de_base": {"nunca_consulta": 0.12, "pergunta_de_volta": 0.24},
        "geral": grupo(0.8133, 0.76, 25),
        "objetivas": grupo(0.8636, 0.8182, 22),
        "ambiguas": grupo(0.5556, 0.3333, 3),
        "categorias": {
            "agregacao": grupo(0.9167, 0.875, 8),
            "ambigua": grupo(0.5556, 0.3333, 3),
        },
        "perguntas": [],
    }


def test_aba_avaliacao_sem_arquivo_nao_mostra_numeros():
    at = abrir()

    aba = at.tabs[2]
    assert any("Avaliação ainda não executada" in i.value for i in aba.info)
    assert len(aba.metric) == 0
    assert len(aba.dataframe) == 0
    assert not any(ch.isdigit() for i in aba.info for ch in i.value)


def test_aba_avaliacao_com_arquivo_mostra_media_pior_caso_e_categorias(ambiente):
    pasta = ambiente / "avaliacao" / "resultados"
    pasta.mkdir(parents=True)
    (pasta / "ultima.json").write_text(
        json.dumps(resultado_de_avaliacao(), ensure_ascii=False), encoding="utf-8"
    )

    at = abrir()

    assert not at.exception
    aba = at.tabs[2]
    assert not any("ainda não executada" in i.value for i in aba.info)
    metricas = {m.label: m.value for m in aba.metric}
    assert metricas == {"Objetivas": "86,4%", "Ambíguas (piso)": "55,6%", "Geral": "81,3%"}
    legendas = " ".join(textos(aba.caption))
    assert "Pior caso: 81,8%" in legendas and "Pior caso: 33,3%" in legendas
    assert "Pior caso: 76,0%" in legendas
    categorias = aba.dataframe[0].value
    assert list(categorias["Categoria"]) == ["agregacao", "ambigua"]
    assert list(categorias.columns) == ["Categoria", "Perguntas", "Média", "Pior caso"]
    texto = " ".join(textos(aba.markdown) + textos(aba.caption))
    assert "modelo-de-teste" in texto and "abc1234" in texto and "02/11/2026 10:00" in texto
    assert "12,0%" in texto and "24,0%" in texto  # linha de base
    assert "última consulta SQL" in texto  # o que os números medem


def test_arquivo_de_avaliacao_invalido_vira_aviso_sem_stack_trace(ambiente):
    pasta = ambiente / "avaliacao" / "resultados"
    pasta.mkdir(parents=True)
    (pasta / "ultima.json").write_text("{isto não é json", encoding="utf-8")

    at = abrir()

    assert not at.exception
    aba = at.tabs[2]
    assert any("não consegui ler" in w.value.lower() for w in aba.warning)
    assert len(aba.metric) == 0
