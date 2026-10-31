"""Testes do painel Streamlit com `AppTest`. Nenhum chama modelo de verdade nem toca em data/ ou runs/.

O ambiente de cada teste aponta banco, rastros e resultado da avaliação para pastas temporárias
por variável de ambiente e desliga a leitura do `.env`, para a chave real da máquina de quem roda
os testes nunca entrar.
"""

import json
import sqlite3
import tomllib
from datetime import date, timedelta
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from analista import agente, config, llm, relatorio, sql_seguro
from analista.dados import FIM, INICIO
from analista.llm import ChamadaFerramenta, ErroDoProvedor, MensagemModelo
from avaliacao import relatorio_md
from tests.banco_minimo import criar_banco_minimo
from tests.fakes import ModeloFalso

RAIZ_DO_PROJETO = Path(__file__).resolve().parents[1]
PAINEL = str(RAIZ_DO_PROJETO / "app" / "painel.py")
BANNER = "Dados sintéticos de um restaurante fictício. Demonstração."
SEGUNDAS = [INICIO + timedelta(days=7 * n) for n in range(13)]
TEXTO_PROVEDOR = (
    "O serviço do modelo atingiu o limite de uso agora. Tente de novo em alguns minutos."
)
CHAVE = "chave-secreta-de-teste-123"


@pytest.fixture(autouse=True)
def ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, banco_path: Path) -> Path:
    monkeypatch.setenv("BANCO_PATH", str(banco_path))
    monkeypatch.setenv("RUNS_PATH", str(tmp_path / "runs"))
    monkeypatch.setenv("AVALIACAO_PATH", str(tmp_path / "resultados" / "ultima.json"))
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)
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
    partes += textos(at.info) + textos(at.code) + textos(at.subheader) + textos(at.title)
    partes += [f"{m.label} {m.value} {m.delta}" for m in at.metric]
    for tabela in at.dataframe:
        partes.append(tabela.value.to_string())
    partes.append(repr(at.main))  # a árvore inteira, inclusive o que acima não cobre
    return "\n".join(str(p) for p in partes)


def gravar_avaliacao(ambiente: Path, conteudo: str | dict) -> None:
    caminho = ambiente / "resultados" / "ultima.json"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(conteudo, str):
        conteudo = json.dumps(conteudo, ensure_ascii=False)
    caminho.write_text(conteudo, encoding="utf-8")


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


def test_streamlit_esconde_detalhes_de_erro_do_navegador():
    configuracao = RAIZ_DO_PROJETO / ".streamlit" / "config.toml"

    dados = tomllib.loads(configuracao.read_text(encoding="utf-8"))

    assert dados["client"]["showErrorDetails"] == "none"


def test_requirements_instala_o_projeto_em_modo_editavel():
    linhas = (RAIZ_DO_PROJETO / "requirements.txt").read_text(encoding="utf-8").split()

    assert linhas == ["-e", ".[painel]"] or linhas == ["-e .[painel]"]


# --- chat -------------------------------------------------------------------------------------


def test_sem_chave_o_chat_avisa_e_nao_chama_modelo(monkeypatch, ambiente):
    proibir_agente(monkeypatch)
    at = abrir()
    assert any("LLM_API_KEY" in aviso for aviso in textos(at.warning))
    assert at.chat_input[0].disabled

    at.run()  # o campo está desabilitado: o AppTest nem deixa digitar, como o navegador

    assert not at.exception
    assert any("LLM_API_KEY" in aviso for aviso in textos(at.warning))
    assert len(at.chat_message) == 0
    assert not (ambiente / "runs").exists()  # nada foi contado nem rastreado


def test_com_modelo_falso_mostra_resposta_e_a_consulta(monkeypatch, ambiente):
    sql = "SELECT COUNT(*) AS total FROM pedidos"
    modelo = ModeloFalso([consulta(sql), resposta("Foram registrados muitos pedidos.")])
    usar_modelo(monkeypatch, modelo)
    at = abrir()
    assert not at.chat_input[0].disabled
    assert any("0 de 10 perguntas" in c for c in textos(at.caption))

    perguntar(at, "Quantos pedidos existem?")

    assert not at.exception
    mensagens = list(at.chat_message)
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


def test_respeita_o_limite_de_10_por_sessao_e_o_aviso_sobrevive_ao_rerun(monkeypatch):
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
    at.run()  # outra interação qualquer: o aviso continua na conversa
    assert any("10 perguntas" in aviso for aviso in textos(at.warning))
    assert len(at.chat_message) == 22  # 10 perguntas respondidas + a barrada, com o aviso


def test_teto_diario_barra_a_pergunta_sem_chamar_o_modelo(monkeypatch, ambiente):
    monkeypatch.setenv("LIMITE_DIA", "2")
    runs = ambiente / "runs"
    runs.mkdir()
    (runs / "limite_dia.json").write_text(
        json.dumps({"data": date.today().isoformat(), "n": 2}), encoding="utf-8"
    )
    proibir_agente(monkeypatch)
    usar_modelo(monkeypatch, ModeloFalso([]))
    at = abrir()

    perguntar(at, "Quanto faturamos?")

    assert not at.exception
    assert any("teto diário de 2 perguntas" in aviso for aviso in textos(at.warning))
    assert any("0 de 10 perguntas" in c for c in textos(at.caption))  # nada foi contado


def test_erro_do_provedor_aparece_como_aviso(monkeypatch, ambiente):
    usar_modelo(monkeypatch, ModeloFalso([ErroDoProvedor(TEXTO_PROVEDOR, "limite")]))
    at = abrir()

    perguntar(at, "Quanto faturamos?")

    assert not at.exception
    assert TEXTO_PROVEDOR in textos(at.warning)
    assert "Traceback" not in tudo_que_aparece(at)
    assert any("0 de 10 perguntas" in c for c in textos(at.caption))  # pergunta não contada
    assert not (ambiente / "runs" / "limite_dia.json").exists()


def test_depois_de_falha_do_provedor_a_proxima_pergunta_funciona(monkeypatch):
    modelo = ModeloFalso([ErroDoProvedor(TEXTO_PROVEDOR, "limite"), resposta("Agora foi.")])
    usar_modelo(monkeypatch, modelo)
    at = abrir()

    perguntar(at, "Primeira?")
    perguntar(at, "Segunda?")

    assert not at.exception
    assert TEXTO_PROVEDOR in textos(at.warning)  # a falha continua na conversa
    assert [m.name for m in at.chat_message] == ["user", "assistant", "user", "assistant"]
    assert "Agora foi." in textos(list(at.chat_message)[3].markdown)
    assert any("1 de 10 perguntas" in c for c in textos(at.caption))  # só a respondida conta


def test_falha_inesperada_nao_mostra_stack_trace(monkeypatch):
    usar_modelo(monkeypatch, ModeloFalso([RuntimeError("segredo-que-nao-pode-aparecer")]))
    at = abrir()

    perguntar(at, "Quanto faturamos?")

    assert not at.exception
    assert at.error
    assert "segredo-que-nao-pode-aparecer" not in tudo_que_aparece(at)
    assert "Traceback" not in tudo_que_aparece(at)
    assert any("0 de 10 perguntas" in c for c in textos(at.caption))


def test_a_chave_nunca_aparece_na_tela(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", CHAVE)
    modelo = ModeloFalso(
        [
            consulta("SELECT COUNT(*) FROM pedidos"),
            resposta("Resposta normal."),
            ErroDoProvedor(TEXTO_PROVEDOR, "limite"),
            RuntimeError(f"falhou com {CHAVE}"),
        ]
    )
    usar_modelo(monkeypatch, modelo)
    at = abrir()
    assert CHAVE not in tudo_que_aparece(at)

    for texto in ("Uma?", "Duas?", "Três?"):
        perguntar(at, texto)
        assert not at.exception
        assert CHAVE not in tudo_que_aparece(at)


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


def test_semana_sem_pedidos_mostra_aviso_em_vez_de_grafico_vazio(monkeypatch, tmp_path):
    banco = criar_banco_minimo(
        tmp_path / "vazio.db", [("2026-09-22 12:00:00", "Anil", "entregue", 40)]
    )
    monkeypatch.setenv("BANCO_PATH", str(banco))
    at = abrir()

    at.selectbox[0].select(SEGUNDAS[0]).run()  # 29/06: sem nenhum pedido no banco

    assert not at.exception
    assert len(at.get("vega_lite_chart")) == 0
    avisos = " ".join(textos(at.info) + textos(at.caption))
    assert "Sem pedidos válidos nesta semana" in avisos
    assert "Nenhum bairro com pelo menos 30 pedidos" in avisos
    assert "Nenhum item vendido nesta semana" in avisos
    assert len(at.dataframe) == 0


def test_falha_ao_calcular_a_semana_vira_erro_amigavel(monkeypatch):
    def quebra(*args, **kwargs):
        raise RuntimeError("detalhe-interno-que-nao-pode-aparecer")

    monkeypatch.setattr(relatorio, "calcular_kpis", quebra)

    at = abrir()

    assert not at.exception
    assert at.tabs[0].error
    assert "detalhe-interno" not in tudo_que_aparece(at)
    assert [aba.label for aba in at.tabs] == ["Painel", "Pergunte", "Avaliação"]


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


def resultado_minimo(repeticoes: int = 3) -> dict:
    """Só o que a tela usa: perguntas, hashes, endpoint e commit podem faltar."""
    completo = resultado_de_avaliacao()
    chaves = (
        "gerado_em",
        "modelo",
        "linha_de_base",
        "geral",
        "objetivas",
        "ambiguas",
        "categorias",
    )
    return {**{k: completo[k] for k in chaves}, "repeticoes": repeticoes}


def test_aba_avaliacao_sem_arquivo_nao_mostra_numeros():
    at = abrir()

    aba = at.tabs[2]
    assert any("Avaliação ainda não executada" in i.value for i in aba.info)
    assert len(aba.metric) == 0
    assert len(aba.dataframe) == 0
    assert not any(ch.isdigit() for i in aba.info for ch in i.value)


def test_aba_avaliacao_com_arquivo_mostra_media_pior_caso_e_categorias(ambiente):
    gravar_avaliacao(ambiente, resultado_de_avaliacao())

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
    assert "modelo-de-teste" in texto and "abc1234" in texto and "2026-11-02" in texto
    assert "3 repetições" in texto
    assert "12,0%" in texto and "24,0%" in texto  # linha de base
    assert "última consulta SQL" in texto  # o que os números medem


def test_ressalvas_da_medicao_aparecem_logo_abaixo_dos_numeros(ambiente):
    gravar_avaliacao(ambiente, resultado_de_avaliacao())

    at = abrir()

    legendas = textos(at.tabs[2].caption)  # fora do expansor, que só tem markdown
    for limite in relatorio_md.LIMITES_DA_MEDICAO:
        assert limite in legendas
    assert relatorio_md.texto_media_e_pior_caso() in legendas
    posicoes = [legendas.index(limite) for limite in relatorio_md.LIMITES_DA_MEDICAO]
    assert max(posicoes) < next(
        i for i, c in enumerate(legendas) if c.startswith("**Linha de base**")
    )


def test_avaliacao_com_uma_repeticao_usa_o_singular(ambiente):
    gravar_avaliacao(ambiente, resultado_minimo(repeticoes=1))

    at = abrir()

    texto = " ".join(textos(at.tabs[2].caption))
    assert "1 repetição por pergunta" in texto and "1 repetições" not in texto


def test_avaliacao_sem_os_campos_opcionais_ainda_aparece(ambiente):
    gravar_avaliacao(ambiente, resultado_minimo())

    at = abrir()

    assert not at.exception
    aba = at.tabs[2]
    assert len(aba.metric) == 3
    assert "Commit: indisponível" in " ".join(textos(aba.caption))


def test_avaliacao_parcial_avisa_que_nao_vale_como_nota_geral(ambiente):
    gravar_avaliacao(ambiente, {**resultado_de_avaliacao(), "categoria_filtrada": "recusa"})

    at = abrir()

    assert any("Avaliação parcial" in w.value for w in at.tabs[2].warning)


def test_arquivo_de_avaliacao_invalido_vira_aviso_sem_stack_trace(ambiente):
    gravar_avaliacao(ambiente, "{isto não é json")

    at = abrir()

    assert not at.exception
    aba = at.tabs[2]
    assert any("não consegui ler" in w.value.lower() for w in aba.warning)
    assert len(aba.metric) == 0


def test_falha_inesperada_na_avaliacao_vira_aviso(monkeypatch, ambiente):
    gravar_avaliacao(ambiente, resultado_de_avaliacao())

    def quebra(resultado):
        raise RuntimeError("detalhe-interno-que-nao-pode-aparecer")

    monkeypatch.setattr(relatorio_md, "texto_origem", quebra)

    at = abrir()

    assert not at.exception
    assert any("não consegui ler" in w.value.lower() for w in at.tabs[2].warning)
    assert "detalhe-interno" not in tudo_que_aparece(at)
