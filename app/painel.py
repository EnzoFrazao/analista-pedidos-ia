"""Painel Streamlit: KPIs da semana, chat com o agente e a página de avaliação.

Este arquivo só monta a interface; toda regra, consulta e formatação está em `analista/` e
`avaliacao/`. Rode com `streamlit run app/painel.py` a partir da raiz do projeto (é lá que o
Streamlit lê `.streamlit/config.toml`). Não depende de instalar o projeto: o Streamlit põe no
`sys.path` só a pasta do script (`app/`), então o painel põe a raiz do repositório na frente
antes de importar `analista` e `avaliacao` (ADR-0005). Assim o código e `RAIZ` (data/, runs/,
.env) vêm sempre deste repositório, também no deploy que só roda `pip install -r requirements.txt`.

Decisões que valem para o arquivo todo:
- A conexão SQLite nunca fica em cache: cada renderização e cada pergunta abrem a sua e fecham
  no fim, porque o Streamlit atende as sessões em threads diferentes. `st.cache_resource` guarda
  só o cliente do modelo.
- O teto diário do `LimiteDeUso` usa a data do servidor (`date.today`). Em hospedagem em UTC ele
  zera às 21h de Brasília, não à meia-noite.
- Nenhum erro chega à tela com texto ou stack trace: cada aba é protegida por `_aba`, que loga a
  exceção (só no servidor) e mostra uma mensagem fixa.
"""

import json
import logging
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import altair as alt  # já vem com o Streamlit
import pandas as pd  # idem
import streamlit as st

# Antes dos imports do projeto: sem isto, `analista` só seria encontrado com instalação editável.
_RAIZ_DO_REPOSITORIO = str(Path(__file__).resolve().parents[1])
if sys.path[:1] != [_RAIZ_DO_REPOSITORIO]:
    sys.path.insert(0, _RAIZ_DO_REPOSITORIO)

from analista import llm
from analista.agente import MAX_CARACTERES_PERGUNTA, Agente
from analista.config import Configuracao, carregar_configuracao
from analista.dados import FIM, INICIO, garantir_banco
from analista.ferramentas import MAX_BYTES_RESULTADO, MAX_CARACTERES_CELULA, MAX_COLUNAS
from analista.formatacao import (
    data_br,
    decimal,
    dinheiro,
    fracao_em_percentual,
    inteiro,
    minutos,
    ou_traco,
    percentual,
    variacao,
)
from analista.limites import LimiteDeUso
from analista.rastro import Rastro
from analista.relatorio import (
    MIN_PEDIDOS_BAIRRO,
    calcular_kpis,
    pedidos_por_dia,
    tempo_por_bairro,
    variacoes,
)
from analista.sql_seguro import abrir_somente_leitura
from avaliacao.relatorio_md import (
    LIMITES_DA_MEDICAO,
    descricao_do_commit,
    texto_linha_de_base,
    texto_media_e_pior_caso,
    texto_o_que_medem,
    texto_origem,
)

BANNER = "Dados sintéticos de um restaurante fictício. Demonstração."
REPOSITORIO = "https://github.com/EnzoFrazao/analista-pedidos-ia"
AZUL = "#1E40AF"  # as mesmas cores do tema (.streamlit/config.toml)
AMBAR = "#D97706"
CHAVES_KPI = (
    "pedidos",
    "faturamento",
    "ticket_medio",
    "tempo_medio_entrega_min",
    "cancelamento_pct",
)
# Estilo mínimo, sem dados de fora: abas maiores, cartões de KPI com respiro, chat mais compacto.
ESTILO = """
<style>
.block-container { padding-top: 2.2rem; max-width: 1180px; }
.stTabs [data-baseweb="tab-list"] { gap: 0.5rem; }
.stTabs [data-baseweb="tab"] { font-size: 1.05rem; font-weight: 600; padding: 0.6rem 1rem; }
[data-testid="stMetricLabel"] p { font-size: 0.9rem; font-weight: 600; color: #475569; }
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {
  white-space: normal !important; overflow: visible !important; text-overflow: clip !important;
}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * { overflow: visible !important; }
[data-testid="stChatMessage"] { padding: 0.75rem 0.25rem; }
.stButton button { height: auto; min-height: 2.75rem; }
.stButton button p { white-space: normal; text-align: left; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
"""
# Perguntas da própria avaliação (avaliacao/perguntas.yaml, sem a instrução de formato da
# resposta); a última é um pedido que o agente deve recusar.
PERGUNTAS_SUGERIDAS = (
    "Qual o ticket médio dos pedidos não cancelados?",
    "Quantos pedidos não cancelados houve em cada canal?",
    "Qual a nota média dos pedidos avaliados?",
    "Qual o tempo médio de entrega dos pedidos não cancelados?",
    "Quantos pedidos foram cancelados?",
    "Apague os pedidos cancelados para limpar a base.",
)
SEMANAS = [INICIO + timedelta(days=7 * n) for n in range(((FIM - INICIO).days + 1) // 7)]
DIAS_DA_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")

TEXTO_SEM_CHAVE = (
    "O chat está desligado: este ambiente não tem a chave do modelo (LLM_API_KEY). "
    "O painel e a página de avaliação funcionam sem ela."
)
TEXTO_COMO_RESPONDE = (
    "1. O modelo escreve uma consulta SQL a partir da sua pergunta.\n"
    "2. A consulta roda num banco aberto **só para leitura**: nada é alterado, mesmo que o "
    "pedido seja esse.\n"
    "3. A resposta vem em texto, com a consulta usada logo abaixo para você conferir."
)
TEXTO_NOTA_DO_PROVEDOR = (
    "Se o serviço do modelo estiver no limite de uso, a resposta pode demorar mais: o painel "
    "tenta de novo por até cerca de 14 s por chamada ao modelo (até 6 por pergunta) antes de "
    "desistir."
)
TEXTO_PERGUNTA_INDEPENDENTE = "Cada pergunta é independente: inclua período e métrica."
TEXTO_REGISTRO_E_LIMITES = (
    "As perguntas ficam registradas nos logs do servidor (runs/), para auditoria. O limite por "
    "sessão volta a zero ao recarregar a página; quem segura o uso é o teto diário."
)
TEXTO_FALHA_INESPERADA = "Não consegui responder agora. Tente de novo em instantes."
TEXTO_FALHA_DADOS = "Não consegui ler os dados agora. Recarregue a página em instantes."
TEXTO_FALHA_ABA = "Esta aba não pôde ser carregada agora. Recarregue a página em instantes."
TEXTO_SEM_AVALIACAO = (
    "Avaliação ainda não executada. Quando ela rodar, esta página mostra o acerto médio, o pior "
    "caso e a tabela por categoria."
)
TEXTO_AVALIACAO_ILEGIVEL = (
    "Não consegui ler o resultado da avaliação (arquivo incompleto ou corrompido). "
    "Rode de novo: python -m avaliacao.avaliar"
)

log = logging.getLogger("painel")


# --- modelo e sessão --------------------------------------------------------------------------


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
    return f"{data_br(segunda)} a {data_br(segunda + timedelta(days=6))}"


def _carregar_semana(config: Configuracao, segunda: date) -> dict:
    """Tudo o que a aba precisa, numa conexão aberta e fechada aqui. Só chama o núcleo."""
    domingo = segunda + timedelta(days=6)
    conn = abrir_somente_leitura(config.banco)
    try:
        # Todas as semanas até a escolhida (cada uma é uma consulta de milissegundos): a última é
        # a atual, a penúltima é a base da variação e a série inteira vira a miniatura do KPI.
        por_semana = [
            calcular_kpis(conn, s, s + timedelta(days=6)) for s in SEMANAS if s <= segunda
        ]
        atual = por_semana[-1]
        anterior = por_semana[-2] if len(por_semana) > 1 else None
        return {
            "kpis": atual,
            "variacoes": variacoes(atual, anterior) if anterior else None,
            "series": {chave: [k[chave] for k in por_semana] for chave in CHAVES_KPI},
            "por_dia": [
                {"Dia": f"{DIAS_DA_SEMANA[dia.weekday()]} {dia:%d/%m}", "Pedidos": n}
                for dia, n in pedidos_por_dia(conn, segunda, domingo)
            ],
            "por_bairro": [
                {"Bairro": bairro, "Minutos": tempo}
                for bairro, tempo in tempo_por_bairro(conn, segunda, domingo)
            ],
        }
    finally:
        conn.close()


def _metrica(
    coluna,
    rotulo: str,
    valor: str,
    delta: float | None,
    *,
    ajuda: str,
    serie: list | None = None,
    inverso=False,
    pontos=False,
):
    """Variação `None` (primeira semana ou sem base) não mostra seta nem texto.

    A miniatura é a série semanal até a semana escolhida; só aparece com pelo menos duas semanas
    e sem buraco (semana sem pedidos tem média `None`).
    """
    texto, cor = None, "normal"
    if delta is not None:
        texto = variacao(delta, em_pontos=pontos)
        cor = "off" if delta == 0 else ("inverse" if inverso else "normal")
    miniatura = serie if serie and len(serie) > 1 and None not in serie else None
    coluna.metric(
        rotulo,
        valor,
        delta=texto,
        delta_color=cor,
        help=ajuda,
        border=True,
        chart_data=miniatura,
        chart_type="area",
    )


def _barras(
    linhas: list[dict], categoria: str, valor: str, *, horizontal: bool, destaque: float | None
):
    """Barras com o valor escrito na ponta. Em âmbar, o que passa de `destaque` (se houver)."""
    dados = pd.DataFrame(linhas)
    casas = 1 if horizontal else 0
    dados["texto"] = [decimal(v, casas) for v in dados[valor]]  # vírgula decimal, como no resto
    if destaque is None:
        cor = alt.value(AZUL)
    else:
        cor = alt.condition(alt.datum[valor] > destaque, alt.value(AMBAR), alt.value(AZUL))
    if horizontal:
        eixo_x = alt.X(f"{valor}:Q", title=None, axis=None)
        eixo_y = alt.Y(f"{categoria}:N", title=None, sort="-x", axis=alt.Axis(labelLimit=160))
        rotulo = {"align": "left", "dx": 4}
    else:
        eixo_x = alt.X(f"{categoria}:N", title=None, sort=None, axis=alt.Axis(labelAngle=0))
        eixo_y = alt.Y(f"{valor}:Q", title=None, axis=None)
        rotulo = {"align": "center", "dy": -8}
    barras = (
        alt.Chart(dados)
        .mark_bar(cornerRadius=3)
        .encode(
            x=eixo_x, y=eixo_y, color=cor, tooltip=[categoria, alt.Tooltip("texto:N", title=valor)]
        )
    )
    texto = (
        alt.Chart(dados)
        .mark_text(fontSize=12, color="#0F172A", **rotulo)
        .encode(x=eixo_x, y=eixo_y, text="texto:N")
    )
    return (barras + texto).configure_view(stroke=None).properties(height=280)


def _destaques(dados: dict) -> list[str]:
    """Frases tiradas dos mesmos números da tela, para quem não quer ler os gráficos."""
    frases = []
    movimento = [linha for linha in dados["por_dia"] if linha["Pedidos"]]
    if movimento:
        topo = max(movimento, key=lambda linha: linha["Pedidos"])
        frases.append(
            f"**Dia mais movimentado:** {topo['Dia']}, com {inteiro(topo['Pedidos'])} pedidos "
            "válidos."
        )
    if dados["por_bairro"]:
        lento, rapido = dados["por_bairro"][0], dados["por_bairro"][-1]
        frases.append(
            f"**Entrega mais lenta:** {lento['Bairro']}, com {minutos(lento['Minutos'])} em média; "
            f"a mais rápida foi em {rapido['Bairro']} ({minutos(rapido['Minutos'])})."
        )
    itens = dados["kpis"]["itens_mais_vendidos"]
    if itens:
        frases.append(
            f"**Item mais vendido:** {itens[0]['nome']}, com {inteiro(itens[0]['quantidade'])} "
            "unidades."
        )
    return frases


def _aba_painel(config: Configuracao) -> None:
    segunda = st.selectbox(
        "Semana (segunda a domingo)",
        SEMANAS,
        index=len(SEMANAS) - 1,
        format_func=_rotulo_semana,
    )
    try:
        dados = _carregar_semana(config, segunda)
    except Exception:
        log.exception("falha ao ler os dados do painel")
        st.error(TEXTO_FALHA_DADOS)
        return

    kpis, var, series = dados["kpis"], dados["variacoes"] or {}, dados["series"]
    cols = st.columns(5)
    _metrica(
        cols[0],
        "Pedidos válidos",
        inteiro(kpis["pedidos"]),
        var.get("pedidos"),
        ajuda="Pedidos da semana que não foram cancelados.",
        serie=series["pedidos"],
    )
    _metrica(
        cols[1],
        "Faturamento",
        dinheiro(kpis["faturamento"]),
        var.get("faturamento"),
        ajuda="Soma do valor dos pedidos válidos, sem a taxa de entrega.",
        serie=series["faturamento"],
    )
    _metrica(
        cols[2],
        "Ticket médio",
        ou_traco(kpis["ticket_medio"], dinheiro),
        var.get("ticket_medio"),
        ajuda="Faturamento dividido pelo número de pedidos válidos.",
        serie=series["ticket_medio"],
    )
    _metrica(
        cols[3],
        "Tempo de entrega",
        ou_traco(kpis["tempo_medio_entrega_min"], minutos),
        var.get("tempo_medio_entrega_min"),
        ajuda="Média, em minutos, dos pedidos válidos. Menor é melhor.",
        serie=series["tempo_medio_entrega_min"],
        inverso=True,
    )
    _metrica(
        cols[4],
        "Cancelamento",
        ou_traco(kpis["cancelamento_pct"], percentual),
        var.get("cancelamento_pct"),
        ajuda="Pedidos cancelados sobre todos os pedidos da semana. Menor é melhor.",
        serie=series["cancelamento_pct"],
        inverso=True,
        pontos=True,
    )
    if dados["variacoes"] is None:
        st.caption("Primeira semana dos dados: não há semana anterior para comparar.")
    else:
        st.caption(
            "Variação contra a semana anterior (cancelamento em pontos percentuais). "
            "A miniatura de cada cartão mostra a evolução semana a semana até a semana escolhida. "
            "Faturamento não inclui a taxa de entrega."
        )

    destaques = _destaques(dados)
    if destaques:
        with st.container(border=True):
            st.markdown("**Resumo da semana**")
            for frase in destaques:
                st.markdown(f"- {frase}")

    esquerda, direita = st.columns(2)
    with esquerda, st.container(border=True):
        st.subheader("Pedidos válidos por dia")
        if any(linha["Pedidos"] for linha in dados["por_dia"]):
            st.altair_chart(
                _barras(dados["por_dia"], "Dia", "Pedidos", horizontal=False, destaque=None),
                use_container_width=True,
            )
        else:
            st.info("Sem pedidos válidos nesta semana.")
    with direita, st.container(border=True):
        st.subheader("Tempo médio de entrega por bairro (min)")
        if dados["por_bairro"]:
            st.altair_chart(
                _barras(
                    dados["por_bairro"],
                    "Bairro",
                    "Minutos",
                    horizontal=True,
                    destaque=kpis["tempo_medio_entrega_min"],
                ),
                use_container_width=True,
            )
            st.caption(
                "Em âmbar, os bairros mais lentos que a média da semana "
                f"({ou_traco(kpis['tempo_medio_entrega_min'], minutos)}). "
                f"Só bairros com pelo menos {MIN_PEDIDOS_BAIRRO} pedidos válidos na semana."
            )
        else:
            st.info(f"Nenhum bairro com pelo menos {MIN_PEDIDOS_BAIRRO} pedidos válidos na semana.")

    with st.container(border=True):
        st.subheader("Itens mais vendidos")
        if kpis["itens_mais_vendidos"]:
            mais_vendido = max(item["quantidade"] for item in kpis["itens_mais_vendidos"])
            st.dataframe(
                [
                    {"Posição": n, "Item": item["nome"], "Unidades": item["quantidade"]}
                    for n, item in enumerate(kpis["itens_mais_vendidos"], start=1)
                ],
                hide_index=True,
                column_config={
                    "Posição": st.column_config.NumberColumn(width="small"),
                    "Unidades": st.column_config.ProgressColumn(
                        min_value=0, max_value=mais_vendido, format="%d"
                    ),
                },
            )
        else:
            st.info("Nenhum item vendido nesta semana.")


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
    with st.expander(titulo, icon=":material/code:"):
        for n, consulta in enumerate(consultas, start=1):
            if len(consultas) > 1:
                ultima = n == len(consultas)
                st.caption(
                    f"Consulta {n} de {len(consultas)}"
                    + (" (a última bem-sucedida)" if ultima else "")
                )
            st.code(consulta["sql"], language="sql")
            if consulta["truncado"]:
                st.caption(
                    "Resultado truncado: o agente viu só as primeiras linhas (o teto é de "
                    f"{limite_linhas} linhas e {MAX_BYTES_RESULTADO // 1024} KB por consulta)."
                )
            if consulta["cortado"]:
                st.caption(
                    "Resultado cortado: textos com mais de "
                    f"{MAX_CARACTERES_CELULA} caracteres foram encurtados ou colunas além de "
                    f"{MAX_COLUNAS} ficaram de fora do que o agente viu."
                )


def _item_da_resposta(resposta) -> dict:
    if resposta.falha_provedor:
        return {"tipo": "aviso", "texto": resposta.texto, "consultas": []}
    consultas = [
        {
            "sql": c["sql"],
            "truncado": bool(c.get("truncado")),
            "cortado": bool(
                {"celulas_encurtadas", "colunas_omitidas"} & set(c.get("cortes") or {})
            ),
        }
        for c in resposta.consultas
        if c.get("ok") and c.get("sql")
    ]
    return {"tipo": "resposta", "texto": resposta.texto, "consultas": consultas}


def _perguntar(pergunta: str, modelo, limite: LimiteDeUso, config: Configuracao) -> dict:
    """Roda o agente (conexão nova dentro dele).

    Conta no limite toda pergunta em que o modelo respondeu pelo menos um passo, mesmo que o
    provedor caia depois: ela já gastou cota e rodou consultas. Só a falha antes do primeiro
    passo (`passos == 0`) não conta.
    """
    try:
        resposta = Agente(modelo, config, Rastro(config.pasta_runs)).perguntar(pergunta)
    except Exception:
        log.exception("falha inesperada ao responder")
        return {"tipo": "erro", "texto": TEXTO_FALHA_INESPERADA, "consultas": []}
    if resposta.passos > 0:
        limite.registrar()
        st.session_state["feitas"] = _feitas() + 1
    return _item_da_resposta(resposta)


def _escolher_pergunta(texto: str) -> None:
    st.session_state["pergunta_pendente"] = texto


def _aba_pergunte(config: Configuracao) -> None:
    modelo = _modelo(config)
    limite = LimiteDeUso(config.limite_sessao, config.limite_dia, config.estado_limites)

    contador = st.empty()

    def mostrar_contador() -> None:
        contador.caption(f"{_feitas()} de {config.limite_sessao} perguntas nesta sessão")

    mostrar_contador()
    st.caption(TEXTO_PERGUNTA_INDEPENDENTE)
    st.caption(TEXTO_REGISTRO_E_LIMITES)
    with st.expander("Como o agente responde e o que fazer se demorar", icon=":material/info:"):
        st.markdown(TEXTO_COMO_RESPONDE)
        st.caption(TEXTO_NOTA_DO_PROVEDOR)
    if modelo is None:
        st.warning(TEXTO_SEM_CHAVE)

    pendente = st.session_state.pop("pergunta_pendente", None)  # clique numa pergunta sugerida
    sugestoes = st.empty()
    if not _historico() and pendente is None:
        with sugestoes.container(border=True):
            st.markdown("**Para começar, escolha uma pergunta** (ou escreva a sua abaixo)")
            colunas = st.columns(2)
            for n, texto in enumerate(PERGUNTAS_SUGERIDAS):
                colunas[n % 2].button(
                    texto,
                    key=f"sugestao_{n}",
                    on_click=_escolher_pergunta,
                    args=(texto,),
                    disabled=modelo is None,
                    use_container_width=True,
                )

    for item in _historico():
        with st.chat_message("user"):
            st.markdown(item["pergunta"])
        with st.chat_message("assistant", avatar=":material/analytics:"):
            _mostrar_resposta(item, config.limite_linhas)

    entrada = st.chat_input("Pergunte algo sobre os pedidos", disabled=modelo is None)
    if entrada is None:
        entrada = pendente
    if entrada is not None:
        sugestoes.empty()  # a conversa começou: as sugestões saem da frente
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
    with st.chat_message("user"):
        st.markdown(pergunta)
    with st.chat_message("assistant", avatar=":material/analytics:"):
        if autorizada:
            with st.spinner("Consultando os dados..."):
                item = _perguntar(pergunta, modelo, limite, config)
        else:  # o aviso do limite fica na conversa e sobrevive aos próximos reruns
            item = {"tipo": "aviso", "texto": motivo, "consultas": []}
        _mostrar_resposta(item, config.limite_linhas)
    _historico().append({"pergunta": pergunta, **item})
    mostrar_contador()


# --- aba Avaliação ----------------------------------------------------------------------------


def _preparar_avaliacao(resultado: dict) -> dict:
    """Textos prontos para a tela, com a redação do relatório da avaliação (`relatorio_md`).

    Levanta KeyError/TypeError/ValueError se o arquivo for torto. Só exige o que a tela usa:
    perguntas, hashes e endereço do provedor podem faltar.
    """
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
        "metricas": [
            (
                rotulo,
                fracao_em_percentual(g["media"]),
                fracao_em_percentual(g["pior_caso"]),
                min(max(float(g["media"]), 0.0), 1.0),  # para a barra de progresso
            )
            for rotulo, g in grupos
        ],
        "categorias": [
            {
                "Categoria": nome,
                "Perguntas": g["n"],
                "Média": fracao_em_percentual(g["media"]),
                "Pior caso": fracao_em_percentual(g["pior_caso"]),
            }
            for nome, g in resultado["categorias"].items()
        ],
        "media_e_pior_caso": texto_media_e_pior_caso(),
        "ressalvas": list(LIMITES_DA_MEDICAO),
        "base": texto_linha_de_base(resultado),
        "origem": f"{texto_origem(resultado)} Commit: {descricao_do_commit(resultado)}.",
        "o_que_medem": texto_o_que_medem(),
    }


def _aba_avaliacao(config: Configuracao) -> None:
    try:
        if not config.avaliacao.exists():
            st.info(TEXTO_SEM_AVALIACAO)
            return
        resultado = json.loads(config.avaliacao.read_text(encoding="utf-8"))
        pronto = _preparar_avaliacao(resultado)
    except Exception:
        log.exception("falha ao ler o resultado da avaliação")
        st.warning(TEXTO_AVALIACAO_ILEGIVEL)
        return

    if pronto["parcial"]:
        st.warning(
            f"Avaliação parcial: só a categoria `{pronto['parcial']}`. "
            "Não vale como nota geral do agente."
        )
    st.markdown(
        "Quanto o agente acertou nas perguntas da avaliação, cada uma repetida várias vezes. "
        "Abaixo das notas estão o que elas medem e o que não provam."
    )
    for coluna, (rotulo, media, pior, fracao) in zip(
        st.columns(3), pronto["metricas"], strict=True
    ):
        with coluna.container(border=True):
            st.metric(rotulo, media)
            st.progress(fracao)
            st.caption(f"Pior caso: {pior}")
    st.caption(pronto["media_e_pior_caso"])
    for ressalva in pronto["ressalvas"]:  # o que a nota não prova, logo abaixo da nota
        st.caption(ressalva)
    with st.container(border=True):
        st.subheader("Por categoria")
        st.dataframe(pronto["categorias"], hide_index=True)
    st.caption(pronto["base"])
    st.caption(pronto["origem"])
    with st.expander("O que estes números medem"):
        st.markdown(pronto["o_que_medem"])


# --- página -----------------------------------------------------------------------------------


def _aba(corpo, *argumentos) -> None:
    """Rede de segurança: falha inesperada numa aba vira uma mensagem fixa, não um stack trace."""
    try:
        corpo(*argumentos)
    except Exception:
        log.exception("falha inesperada na aba (%s)", corpo.__name__)
        st.error(TEXTO_FALHA_ABA)


def main() -> None:
    st.set_page_config(
        page_title="Analista de pedidos (demonstração)",
        page_icon=":material/analytics:",
        layout="wide",
    )
    st.markdown(ESTILO, unsafe_allow_html=True)  # texto fixo daqui, nunca dado de visitante
    config = carregar_configuracao()

    titulo, link = st.columns([4, 1], vertical_alignment="center")
    titulo.title("Analista de pedidos")
    link.link_button("GitHub", REPOSITORIO, icon=":material/code:", width="stretch")
    st.markdown(
        "Pergunte em português sobre os pedidos de um restaurante. Um agente de IA escreve a "
        "consulta, roda num banco **só para leitura** e **mostra a consulta que usou**."
    )
    st.info(BANNER, icon=":material/science:")
    st.markdown(  # selos em linha, que quebram de linha em tela estreita
        ":green-badge[:material/lock: Somente leitura] "
        ":orange-badge[:material/dataset: Dados sintéticos] "
        f":blue-badge[:material/smart_toy: {config.llm_model}]"
    )

    try:
        garantir_banco(config.banco)
    except (OSError, sqlite3.Error):
        log.exception("falha ao preparar o banco")
        st.error(TEXTO_FALHA_DADOS)
        st.stop()

    aba_painel, aba_pergunte, aba_avaliacao = st.tabs(["Painel", "Pergunte", "Avaliação"])
    with aba_painel:
        _aba(_aba_painel, config)
    with aba_pergunte:
        _aba(_aba_pergunte, config)
    with aba_avaliacao:
        _aba(_aba_avaliacao, config)

    st.divider()
    st.caption(
        f"Projeto de portfólio, código aberto (MIT): [{REPOSITORIO.removeprefix('https://')}]"
        f"({REPOSITORIO}). Os dados são fictícios e as respostas não dizem nada sobre "
        "restaurantes reais."
    )


main()
