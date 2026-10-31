"""Painel Streamlit: KPIs da semana, chat com o agente e a página de avaliação.

Este arquivo só monta a interface; toda regra está em `analista/` e `avaliacao/`. Rode com
`streamlit run app/painel.py` (o pacote `analista` precisa estar instalado: `pip install -e .`
local, `requirements.txt` no deploy, porque o Streamlit põe no `sys.path` a pasta do script e
não a raiz do projeto).

Duas decisões que valem para o arquivo todo:
- A conexão SQLite nunca fica em cache: cada renderização e cada pergunta abrem a sua e fecham
  no fim, porque o Streamlit atende as sessões em threads diferentes. `st.cache_resource` guarda
  só o cliente do modelo.
- O teto diário do `LimiteDeUso` usa a data do servidor (`date.today`). Em hospedagem em UTC ele
  zera às 21h de Brasília, não à meia-noite.
"""

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta

import streamlit as st

from analista import llm
from analista.agente import MAX_CARACTERES_PERGUNTA, Agente
from analista.config import RAIZ, Configuracao, carregar_configuracao
from analista.dados import FIM, INICIO, garantir_banco
from analista.limites import LimiteDeUso
from analista.rastro import Rastro
from analista.relatorio import (
    MIN_PEDIDOS_BAIRRO,
    _data_br,
    _dinheiro,
    _filtro,
    _inteiro,
    _minutos,
    _ou_traco,
    _percentual,
    _variacao,
    calcular_kpis,
    variacoes,
)
from analista.sql_seguro import (
    ErroDeConsulta,
    SqlRejeitado,
    abrir_somente_leitura,
    executar_consulta,
)
from avaliacao.relatorio_md import _commit, _pct, gerar_markdown

BANNER = "Dados sintéticos de um restaurante fictício. Demonstração."
SEMANAS = [INICIO + timedelta(days=7 * n) for n in range(((FIM - INICIO).days + 1) // 7)]
DIAS_DA_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")

TEXTO_SEM_CHAVE = (
    "O chat está desligado: este ambiente não tem a chave do modelo (LLM_API_KEY). "
    "O painel e a página de avaliação funcionam sem ela."
)
TEXTO_NOTA_DO_PROVEDOR = (
    "Se o serviço do modelo estiver no limite de uso, a resposta pode levar alguns segundos a "
    "mais: o painel tenta de novo por até cerca de 14 s antes de desistir."
)
TEXTO_FALHA_INESPERADA = "Não consegui responder agora. Tente de novo em instantes."
TEXTO_FALHA_DADOS = "Não consegui ler os dados agora. Recarregue a página em instantes."
TEXTO_SEM_AVALIACAO = (
    "Avaliação ainda não executada. Quando ela rodar, esta página mostra o acerto médio, o pior "
    "caso e a tabela por categoria."
)
TEXTO_AVALIACAO_ILEGIVEL = (
    "Não consegui ler o resultado da avaliação (arquivo incompleto ou corrompido). "
    "Rode de novo: python -m avaliacao.avaliar"
)

log = logging.getLogger("painel")


# --- modelo e limites -------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def _modelo(config: Configuracao):
    """O cliente do modelo (ou `None` sem chave). É o único recurso em cache do painel."""
    return llm.criar_modelo(config)


def _feitas() -> int:
    return st.session_state.setdefault("feitas", 0)


def _historico() -> list[dict]:
    return st.session_state.setdefault("historico", [])


# --- aba Painel -------------------------------------------------------------------------------


def _rotulo_semana(segunda: date) -> str:
    return f"{_data_br(segunda)} a {_data_br(segunda + timedelta(days=6))}"


def _pedidos_por_dia(conn: sqlite3.Connection, segunda: date) -> list[dict]:
    domingo = segunda + timedelta(days=6)
    linhas = executar_consulta(
        conn,
        f"""SELECT substr(data_hora, 1, 10) AS dia, COUNT(*)
            FROM pedidos
            WHERE status <> 'cancelado' AND {_filtro(segunda, domingo)}
            GROUP BY dia""",
    ).linhas
    por_dia = dict(linhas)
    dias = [segunda + timedelta(days=n) for n in range(7)]
    return [
        {
            "Dia": f"{DIAS_DA_SEMANA[dia.weekday()]} {dia:%d/%m}",
            "Pedidos": por_dia.get(dia.isoformat(), 0),
        }
        for dia in dias
    ]


def _tempo_por_bairro(conn: sqlite3.Connection, segunda: date) -> list[dict]:
    domingo = segunda + timedelta(days=6)
    linhas = executar_consulta(
        conn,
        f"""SELECT bairro, ROUND(AVG(tempo_entrega_min), 1) AS minutos
            FROM pedidos
            WHERE status <> 'cancelado' AND {_filtro(segunda, domingo)}
            GROUP BY bairro
            HAVING COUNT(*) >= {MIN_PEDIDOS_BAIRRO}
            ORDER BY minutos DESC, bairro""",
    ).linhas
    return [{"Bairro": bairro, "Minutos": minutos} for bairro, minutos in linhas]


def _carregar_semana(config: Configuracao, segunda: date) -> dict:
    """Tudo o que a aba precisa, numa conexão aberta e fechada aqui."""
    conn = abrir_somente_leitura(config.banco)
    try:
        atual = calcular_kpis(conn, segunda, segunda + timedelta(days=6))
        anterior = None
        if segunda > INICIO:
            anterior_segunda = segunda - timedelta(days=7)
            anterior = calcular_kpis(conn, anterior_segunda, segunda - timedelta(days=1))
        return {
            "kpis": atual,
            "variacoes": variacoes(atual, anterior) if anterior else None,
            "por_dia": _pedidos_por_dia(conn, segunda),
            "por_bairro": _tempo_por_bairro(conn, segunda),
        }
    finally:
        conn.close()


def _metrica(
    coluna, rotulo: str, valor: str, variacao: float | None, *, inverso=False, pontos=False
):
    """Variação `None` (primeira semana ou sem base) não mostra seta nem texto."""
    delta, cor = None, "normal"
    if variacao is not None:
        delta = _variacao(variacao, em_pontos=pontos)
        cor = "off" if variacao == 0 else ("inverse" if inverso else "normal")
    coluna.metric(rotulo, valor, delta=delta, delta_color=cor)


def _aba_painel(config: Configuracao) -> None:
    segunda = st.selectbox(
        "Semana (segunda a domingo)",
        SEMANAS,
        index=len(SEMANAS) - 1,
        format_func=_rotulo_semana,
    )
    try:
        dados = _carregar_semana(config, segunda)
    except (sqlite3.Error, SqlRejeitado, ErroDeConsulta, OSError) as erro:
        log.error("falha ao ler os dados do painel: %s", type(erro).__name__)
        st.error(TEXTO_FALHA_DADOS)
        return

    kpis, var = dados["kpis"], dados["variacoes"] or {}
    cols = st.columns(5)
    _metrica(cols[0], "Pedidos válidos", _inteiro(kpis["pedidos"]), var.get("pedidos"))
    _metrica(cols[1], "Faturamento", _dinheiro(kpis["faturamento"]), var.get("faturamento"))
    _metrica(
        cols[2], "Ticket médio", _ou_traco(kpis["ticket_medio"], _dinheiro), var.get("ticket_medio")
    )
    _metrica(
        cols[3],
        "Tempo médio de entrega",
        _ou_traco(kpis["tempo_medio_entrega_min"], _minutos),
        var.get("tempo_medio_entrega_min"),
        inverso=True,
    )
    _metrica(
        cols[4],
        "Cancelamento",
        _ou_traco(kpis["cancelamento_pct"], _percentual),
        var.get("cancelamento_pct"),
        inverso=True,
        pontos=True,
    )
    if dados["variacoes"] is None:
        st.caption("Primeira semana dos dados: não há semana anterior para comparar.")
    else:
        st.caption(
            "Variação contra a semana anterior (cancelamento em pontos percentuais). "
            "Faturamento não inclui a taxa de entrega."
        )

    esquerda, direita = st.columns(2)
    with esquerda:
        st.subheader("Pedidos válidos por dia")
        st.bar_chart(dados["por_dia"], x="Dia", y="Pedidos", sort=False)
    with direita:
        st.subheader("Tempo médio de entrega por bairro (min)")
        st.bar_chart(dados["por_bairro"], x="Bairro", y="Minutos", sort=False)
        st.caption(f"Só bairros com pelo menos {MIN_PEDIDOS_BAIRRO} pedidos válidos na semana.")

    st.subheader("Itens mais vendidos")
    st.dataframe(
        [
            {"Posição": n, "Item": item["nome"], "Unidades": item["quantidade"]}
            for n, item in enumerate(kpis["itens_mais_vendidos"], start=1)
        ],
        hide_index=True,
    )


# --- aba Pergunte -----------------------------------------------------------------------------


def _mostrar_resposta(item: dict, limite_linhas: int) -> None:
    if item["tipo"] == "aviso":
        st.warning(item["texto"])
        return
    if item["tipo"] == "erro":
        st.error(item["texto"])
        return
    st.markdown(item["texto"])
    consultas = item["consultas"]
    if not consultas:
        return
    titulo = "Consulta usada" if len(consultas) == 1 else f"Consultas usadas ({len(consultas)})"
    with st.expander(titulo):
        for n, consulta in enumerate(consultas, start=1):
            if len(consultas) > 1:
                ultima = (
                    " (a última bem-sucedida, a que sustenta a resposta)"
                    if (n == len(consultas))
                    else ""
                )
                st.caption(f"Consulta {n} de {len(consultas)}{ultima}")
            st.code(consulta["sql"], language="sql")
            if consulta["truncado"]:
                st.caption(
                    f"Resultado truncado: a consulta devolveu mais de {limite_linhas} linhas e "
                    "o agente viu só as primeiras."
                )


def _item_da_resposta(resposta) -> dict:
    if resposta.falha_provedor:
        return {"tipo": "aviso", "texto": resposta.texto, "consultas": []}
    consultas = [
        {"sql": c["sql"], "truncado": bool(c.get("truncado"))}
        for c in resposta.consultas
        if c.get("ok") and c.get("sql")
    ]
    return {"tipo": "resposta", "texto": resposta.texto, "consultas": consultas}


def _perguntar(pergunta: str, modelo, limite: LimiteDeUso, config: Configuracao) -> dict:
    """Roda o agente (conexão nova dentro dele). Só pergunta respondida entra na contagem."""
    try:
        resposta = Agente(modelo, config, Rastro(config.pasta_runs)).perguntar(pergunta)
    except Exception as erro:  # noqa: BLE001 - a tela nunca mostra stack trace nem o erro
        log.error("falha inesperada ao responder: %s", type(erro).__name__)
        return {"tipo": "erro", "texto": TEXTO_FALHA_INESPERADA, "consultas": []}
    if not resposta.falha_provedor:
        limite.registrar()
        st.session_state["feitas"] = _feitas() + 1
    return _item_da_resposta(resposta)


def _aba_pergunte(config: Configuracao) -> None:
    modelo = _modelo(config)
    limite = LimiteDeUso(config.limite_sessao, config.limite_dia, config.estado_limites)

    contador = st.empty()

    def mostrar_contador() -> None:
        contador.caption(f"{_feitas()} de {config.limite_sessao} perguntas nesta sessão")

    mostrar_contador()
    st.caption(TEXTO_NOTA_DO_PROVEDOR)
    st.caption(
        'Exemplos: "Qual canal tem a maior taxa de cancelamento?" ou '
        '"Quais bairros ficaram mais lentos nas últimas semanas?"'
    )
    if modelo is None:
        st.warning(TEXTO_SEM_CHAVE)

    for item in _historico():
        with st.chat_message("user"):
            st.markdown(item["pergunta"])
        with st.chat_message("assistant"):
            _mostrar_resposta(item, config.limite_linhas)

    entrada = st.chat_input("Pergunte algo sobre os pedidos")
    if entrada is None or modelo is None:
        return

    pergunta = entrada.strip()
    if not pergunta:
        st.warning("Escreva uma pergunta antes de enviar.")
        return
    if len(pergunta) > MAX_CARACTERES_PERGUNTA:
        st.warning(
            f"A pergunta passa de {MAX_CARACTERES_PERGUNTA} caracteres; resuma e tente de novo."
        )
        return
    autorizada, motivo = limite.autorizar(_feitas())
    if not autorizada:
        st.warning(motivo)
        return

    with st.chat_message("user"):
        st.markdown(pergunta)
    with st.chat_message("assistant"):
        with st.spinner("Consultando os dados..."):
            item = _perguntar(pergunta, modelo, limite, config)
        _mostrar_resposta(item, config.limite_linhas)
    _historico().append({"pergunta": pergunta, **item})
    mostrar_contador()


# --- aba Avaliação ----------------------------------------------------------------------------


def _data_da_avaliacao(texto: str) -> str:
    try:
        return datetime.fromisoformat(texto).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return str(texto)


def _preparar_avaliacao(resultado: dict) -> dict:
    """Textos prontos para a tela. Levanta KeyError/TypeError/ValueError se o arquivo for torto."""
    cabecalho = gerar_markdown(resultado).split("\n## ", 1)[0]
    cabecalho = cabecalho.removeprefix("# Avaliação do agente").strip()
    base = resultado["linha_de_base"]
    grupos = [
        (rotulo, resultado[chave])
        for rotulo, chave in (
            ("Objetivas", "objetivas"),
            ("Ambíguas (piso)", "ambiguas"),
            ("Geral", "geral"),
        )
    ]
    return {
        "parcial": resultado.get("categoria_filtrada"),
        "metricas": [(rotulo, _pct(g["media"]), _pct(g["pior_caso"])) for rotulo, g in grupos],
        "categorias": [
            {
                "Categoria": nome,
                "Perguntas": g["n"],
                "Média": _pct(g["media"]),
                "Pior caso": _pct(g["pior_caso"]),
            }
            for nome, g in resultado["categorias"].items()
        ],
        "base": (
            f"Linha de base: um modelo que nunca consulta e só responde sem escrever acertaria "
            f"{_pct(base['nunca_consulta'])}; se respondesse com uma pergunta ('?') a toda "
            f"ambígua, {_pct(base['pergunta_de_volta'])}. Compare a nota do agente com esses "
            "números."
        ),
        "origem": (
            f"Modelo `{resultado['modelo']}` · gerado em {_data_da_avaliacao(resultado['gerado_em'])} "
            f"· {resultado['repeticoes']} repetições por pergunta · commit {_commit(resultado)}"
        ),
        # O que os números medem: o texto de abertura do relatório, sem repetir a linha de base.
        "o_que_medem": cabecalho.split("**Linha de base**", 1)[0].strip(),
    }


def _aba_avaliacao() -> None:
    caminho = RAIZ / "avaliacao" / "resultados" / "ultima.json"
    if not caminho.exists():
        st.info(TEXTO_SEM_AVALIACAO)
        return
    try:
        pronto = _preparar_avaliacao(json.loads(caminho.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        st.warning(TEXTO_AVALIACAO_ILEGIVEL)
        return

    if pronto["parcial"]:
        st.warning(
            f"Avaliação parcial: só a categoria `{pronto['parcial']}`. "
            "Não vale como nota geral do agente."
        )
    for coluna, (rotulo, media, pior) in zip(st.columns(3), pronto["metricas"], strict=True):
        coluna.metric(rotulo, media)
        coluna.caption(f"Pior caso: {pior}")
    st.caption(
        "A média é o acerto médio entre as repetições; o pior caso é o menor acerto de uma "
        "repetição. Ambíguas é um piso: outras respostas razoáveis contam como erro."
    )
    st.subheader("Por categoria")
    st.dataframe(pronto["categorias"], hide_index=True)
    st.caption(pronto["base"])
    st.caption(pronto["origem"])
    with st.expander("O que estes números medem"):
        st.markdown(pronto["o_que_medem"])


# --- página -----------------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="Analista de pedidos (demonstração)", layout="wide")
    st.info(BANNER)
    st.title("Analista de pedidos")
    st.caption(
        "Um agente responde perguntas de negócio sobre os pedidos consultando o banco só para "
        "leitura, e mostra a consulta que usou."
    )

    config = carregar_configuracao()
    try:
        garantir_banco(config.banco)
    except (OSError, sqlite3.Error) as erro:
        log.error("falha ao preparar o banco: %s", type(erro).__name__)
        st.error(TEXTO_FALHA_DADOS)
        st.stop()

    aba_painel, aba_pergunte, aba_avaliacao = st.tabs(["Painel", "Pergunte", "Avaliação"])
    with aba_painel:
        _aba_painel(config)
    with aba_pergunte:
        _aba_pergunte(config)
    with aba_avaliacao:
        _aba_avaliacao()


main()
