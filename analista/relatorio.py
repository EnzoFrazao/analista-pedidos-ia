"""Relatório semanal: os números vêm do SQL, o modelo só escreve um parágrafo.

Fluxo: `calcular_kpis` (SQL, seguindo `DEFINICOES`) -> `variacoes` contra a semana anterior ->
parágrafo do modelo -> `numeros_nao_suportados` recusa qualquer número que não esteja nos KPIs.
Recusado duas vezes, ou sem modelo, ou com o provedor fora do ar, o parágrafo sai de
`resumo_deterministico`: o relatório nunca falha por causa do modelo.

Números no texto seguem o formato pt-BR: `R$ 12.345,67`, `18,4%`, `1.234`.
"""

import argparse
import re
import sqlite3
import sys
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

from analista.config import RAIZ, carregar_configuracao
from analista.dados import FIM, INICIO
from analista.formatacao import (
    data_br,
    dinheiro,
    inteiro,
    minutos,
    ou_traco,
    percentual,
    variacao,
)
from analista.llm import ErroDoProvedor, ModeloLLM, criar_modelo
from analista.sql_seguro import abrir_somente_leitura, executar_consulta

PASTA_RELATORIOS = RAIZ / "relatorios"

N_ITENS = 5
N_BAIRROS = 3
MIN_PEDIDOS_BAIRRO = 30
# Contagens que o próprio modelo de relatório cita ("os 5 itens", "os 3 bairros"): só valem
# coladas ao substantivo, para o texto poder dizer quantos itens e bairros está listando.
CONTAGENS_POR_SUBSTANTIVO = {"itens": N_ITENS, "bairros": N_BAIRROS}

_TOLERANCIA = 0.05
CHAVE_EM_PONTOS = "cancelamento_pct"  # a variação desta chave é em pontos percentuais
_CHAVES_VARIACAO = (
    "pedidos",
    "faturamento",
    "ticket_medio",
    "tempo_medio_entrega_min",
    "cancelamento_pct",
)
# Milhar com ponto (1.234,5), decimal com ponto (3.5) ou número simples/decimal com vírgula.
_NUMERO = re.compile(r"\d{1,3}(?:\.\d{3})+(?!\d)(?:,\d+)?|\d+\.\d+|\d+(?:,\d+)?")
_MESES = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]
_MES_NOME = "|".join(_MESES)
# Datas em que dia, mês e ano são aceitos; fora delas esses números valem como qualquer outro.
_DATA_EXTENSO = re.compile(
    rf"\b(\d{{1,2}})(?: a (\d{{1,2}}))? de ({_MES_NOME})(?: de (\d{{4}}))?\b", re.IGNORECASE
)
_DATA_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATA_BARRA = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b")
_ANO_COM_DE = re.compile(r"\bde (\d{4})\b", re.IGNORECASE)
_CONTAGEM = re.compile(
    "|".join(rf"\b{n}\s+{nome}\b" for nome, n in CONTAGENS_POR_SUBSTANTIVO.items()), re.IGNORECASE
)

_ROTULO_MODELO = (
    "_Resumo escrito pelo modelo; os números em algarismos foram conferidos contra os KPIs._"
)
_ROTULO_CODIGO = "_Resumo montado pelo código a partir dos KPIs (sem o modelo)._"

_INSTRUCAO_DO_SISTEMA = (
    "Você escreve o resumo semanal de um restaurante fictício, em português do Brasil. "
    "Escreva um único parágrafo de até 5 frases, sem listas e sem títulos. "
    "Use somente os números fornecidos, copiados exatamente como aparecem (mesmo formato, "
    "mesmas casas decimais). Não calcule, não arredonde e não invente nenhum número. "
    "Comente o que mudou em relação à semana anterior e destaque os bairros mais lentos."
)


# --- período ----------------------------------------------------------------------------------


def validar_semana(semana_inicio: date) -> None:
    """Levanta `ValueError` se a data não for uma segunda-feira dentro do período dos dados."""
    if semana_inicio.weekday() != 0:
        raise ValueError(
            f"{semana_inicio.isoformat()} não é segunda-feira; a semana vai de segunda a domingo."
        )
    if semana_inicio < INICIO or semana_inicio + timedelta(days=6) > FIM:
        raise ValueError(
            f"{semana_inicio.isoformat()} está fora do período dos dados "
            f"({INICIO.isoformat()} a {FIM.isoformat()}); a semana precisa caber inteira nele."
        )


def _filtro(inicio: date, fim: date, coluna: str = "data_hora") -> str:
    # data_hora é TEXTO 'AAAA-MM-DD HH:MM:SS'; as datas vêm de `date`, nunca de texto livre.
    seguinte = fim + timedelta(days=1)
    return (
        f"{coluna} >= '{inicio.isoformat()} 00:00:00' "
        f"AND {coluna} < '{seguinte.isoformat()} 00:00:00'"
    )


# --- KPIs -------------------------------------------------------------------------------------


def calcular_kpis(conn: sqlite3.Connection, inicio: date, fim: date) -> dict:
    """KPIs do período [inicio, fim], inclusive, por SQL e conforme `DEFINICOES`.

    `pedidos` conta os pedidos válidos (status <> 'cancelado'). Sem pedidos, o faturamento é 0
    e as médias e o cancelamento são `None`.
    """
    filtro = _filtro(inicio, fim)
    resumo = executar_consulta(
        conn,
        f"""SELECT COUNT(*),
                   SUM(CASE WHEN status <> 'cancelado' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status <> 'cancelado' THEN valor END),
                   AVG(CASE WHEN status <> 'cancelado' THEN tempo_entrega_min END)
            FROM pedidos WHERE {filtro}""",
    ).linhas[0]
    total, validos, faturamento, tempo = resumo
    validos = validos or 0
    faturamento = faturamento or 0.0
    cancelados = total - validos

    itens = executar_consulta(
        conn,
        f"""SELECT c.nome, SUM(i.quantidade) AS vendidos
            FROM itens_pedido i
            JOIN pedidos p ON p.id = i.pedido_id
            JOIN itens_cardapio c ON c.id = i.item_id
            WHERE p.status <> 'cancelado' AND {_filtro(inicio, fim, "p.data_hora")}
            GROUP BY c.id, c.nome
            ORDER BY vendidos DESC, c.nome
            LIMIT {N_ITENS}""",
    ).linhas
    bairros = _bairros_por_tempo(conn, inicio, fim, N_BAIRROS)

    return {
        "pedidos": validos,
        "faturamento": round(faturamento, 2),
        "ticket_medio": round(faturamento / validos, 2) if validos else None,
        "tempo_medio_entrega_min": round(tempo, 1) if tempo is not None else None,
        "cancelamento_pct": round(cancelados / total * 100, 1) if total else None,
        "itens_mais_vendidos": [{"nome": nome, "quantidade": qtd} for nome, qtd in itens],
        "bairros_mais_lentos": [
            {"bairro": bairro, "tempo_medio_min": round(tempo_b, 1), "pedidos": qtd}
            for bairro, tempo_b, qtd in bairros
        ],
    }


def _bairros_por_tempo(
    conn: sqlite3.Connection, inicio: date, fim: date, limite: int | None = None
) -> list[tuple[str, float, int]]:
    """(bairro, tempo médio, pedidos válidos), do mais lento ao mais rápido.

    Só entram bairros com pelo menos `MIN_PEDIDOS_BAIRRO` pedidos válidos: média de poucos
    pedidos engana. É a única definição desta regra; o relatório e o painel passam por aqui.
    """
    corte = f"LIMIT {limite}" if limite else ""
    return conn.execute(
        f"""SELECT bairro, AVG(tempo_entrega_min) AS tempo_medio, COUNT(*) AS pedidos
            FROM pedidos
            WHERE status <> 'cancelado' AND {_filtro(inicio, fim)}
            GROUP BY bairro
            HAVING COUNT(*) >= {MIN_PEDIDOS_BAIRRO}
            ORDER BY tempo_medio DESC, bairro
            {corte}"""
    ).fetchall()


def tempo_por_bairro(conn: sqlite3.Connection, inicio: date, fim: date) -> list[tuple[str, float]]:
    """Tempo médio de entrega (1 casa) por bairro no período, do mais lento ao mais rápido."""
    return [(bairro, round(tempo, 1)) for bairro, tempo, _ in _bairros_por_tempo(conn, inicio, fim)]


def pedidos_por_dia(conn: sqlite3.Connection, inicio: date, fim: date) -> list[tuple[date, int]]:
    """Pedidos válidos de cada dia de [inicio, fim], inclusive; dia sem pedido vale 0."""
    por_dia = dict(
        conn.execute(
            f"""SELECT substr(data_hora, 1, 10), COUNT(*)
                FROM pedidos
                WHERE status <> 'cancelado' AND {_filtro(inicio, fim)}
                GROUP BY 1"""
        ).fetchall()
    )
    dias = [inicio + timedelta(days=n) for n in range((fim - inicio).days + 1)]
    return [(dia, por_dia.get(dia.isoformat(), 0)) for dia in dias]


def variacoes(atual: dict, anterior: dict) -> dict:
    """Variação de cada KPI escalar, com 1 casa e nunca -0,0.

    Pedidos, faturamento, ticket e tempo: variação percentual, `None` se o anterior for zero ou
    nulo. Cancelamento: diferença em pontos percentuais (atual - anterior), `None` só se um dos
    lados for nulo, porque a variação relativa de uma taxa engana.
    """
    resultado: dict[str, float | None] = {}
    for chave in _CHAVES_VARIACAO:
        a, b = atual.get(chave), anterior.get(chave)
        if a is None or b is None:
            resultado[chave] = None
        elif chave == CHAVE_EM_PONTOS:
            resultado[chave] = round(a - b, 1) or 0.0  # `or 0.0` troca -0.0 por 0.0
        elif b == 0:
            resultado[chave] = None
        else:
            resultado[chave] = round((a - b) / b * 100, 1) or 0.0
    return resultado


# --- checagem de números ----------------------------------------------------------------------


def _valores_numericos(obj) -> Iterable[float]:
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return
    if isinstance(obj, int | float):
        yield float(obj)
    elif isinstance(obj, dict):
        for valor in obj.values():
            yield from _valores_numericos(valor)
    elif isinstance(obj, list | tuple):
        for valor in obj:
            yield from _valores_numericos(valor)


def _para_float(achado: str) -> float:
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", achado):
        return float(achado.replace(".", "").replace(",", "."))  # milhar: 1.234,5
    return float(achado.replace(",", "."))  # 3,5 ou 3.5


def _datas_validas(achados: list[str], aceitos: set[int]) -> set[str]:
    return {x for x in achados if x and int(x) not in aceitos}


def numeros_nao_suportados(
    texto: str, kpis: dict, variacoes: dict, periodo: tuple[date, date]
) -> set[str]:
    """Números do texto (pt-BR, em algarismos) que não aparecem nos KPIs nem no período.

    Aceitos: todo valor numérico de `kpis` e `variacoes` (as variações também em módulo, porque
    "queda de 7,3%" cita -7,3); dia, mês e ano do período, mas só dentro de uma data
    (`21/09/2026`, `21/09`, `21 a 27 de setembro de 2026`, `2026-09-21`, `de 2026`); e 5 antes
    de "itens" / 3 antes de "bairros" (`CONTAGENS_POR_SUBSTANTIVO`). Fora disso um "27" ou "5"
    solto é um número como outro qualquer e precisa estar nos KPIs. A comparação arredonda os
    dois lados a 1 casa e tolera 0,05. Devolve os números como estão escritos no texto.
    """
    inicio, fim = periodo
    dias, meses, anos = set(), set(), set()
    dia = inicio
    while dia <= fim:
        dias.add(dia.day)
        meses.add(dia.month)
        anos.add(dia.year)
        dia += timedelta(days=1)

    suspeitos: set[str] = set()
    restante = texto

    def tirar(padrao: re.Pattern, conferir) -> None:
        nonlocal restante

        def trocar(m: re.Match) -> str:
            suspeitos.update(conferir(m))
            return " " * len(m.group(0))

        restante = padrao.sub(trocar, restante)

    def extenso(m: re.Match) -> set[str]:
        d1, d2, mes, ano = m.groups()
        erros = _datas_validas([d1, d2], dias) | _datas_validas([ano], anos)
        if _MESES.index(mes.lower()) + 1 not in meses:
            erros.add(mes)
        return erros

    def iso(m: re.Match) -> set[str]:
        ano, mes, d = m.groups()
        return (
            _datas_validas([ano], anos) | _datas_validas([mes], meses) | _datas_validas([d], dias)
        )

    def barra(m: re.Match) -> set[str]:
        d, mes, ano = m.groups()
        return (
            _datas_validas([d], dias) | _datas_validas([mes], meses) | _datas_validas([ano], anos)
        )

    tirar(_DATA_EXTENSO, extenso)
    tirar(_DATA_ISO, iso)
    tirar(_DATA_BARRA, barra)
    tirar(_ANO_COM_DE, lambda m: _datas_validas([m.group(1)], anos))
    tirar(_CONTAGEM, lambda m: set())  # só 5 itens / 3 bairros casam; o resto segue a regra geral

    permitidos = set()
    for valor in (*_valores_numericos(kpis), *_valores_numericos(variacoes)):
        permitidos.add(round(valor, 1))
        permitidos.add(round(abs(valor), 1))
    for achado in _NUMERO.findall(restante):
        valor = round(_para_float(achado), 1)
        if not any(abs(valor - p) <= _TOLERANCIA + 1e-9 for p in permitidos):
            suspeitos.add(achado)
    return suspeitos


# --- texto ------------------------------------------------------------------------------------


def resumo_deterministico(kpis: dict, variacoes: dict) -> str:
    """Parágrafo montado só com os KPIs; passa na própria checagem de números."""
    frases = [
        (
            f"A semana teve {inteiro(kpis['pedidos'])} pedidos válidos e faturamento de "
            f"{dinheiro(kpis['faturamento'])}, com ticket médio de "
            f"{ou_traco(kpis['ticket_medio'], dinheiro)}."
        ),
        (
            "O tempo médio de entrega foi de "
            f"{ou_traco(kpis['tempo_medio_entrega_min'], minutos)} "
            f"e o cancelamento ficou em {ou_traco(kpis['cancelamento_pct'], percentual)}."
        ),
        (
            "Em relação à semana anterior, a variação foi de "
            f"{variacao(variacoes.get('faturamento'))} no faturamento, "
            f"{variacao(variacoes.get('pedidos'))} nos pedidos e "
            f"{variacao(variacoes.get('tempo_medio_entrega_min'))} no tempo médio de entrega."
        ),
    ]
    if kpis["bairros_mais_lentos"]:
        lentos = "; ".join(
            f"{b['bairro']} ({minutos(b['tempo_medio_min'])})" for b in kpis["bairros_mais_lentos"]
        )
        frases.append(f"Bairros mais lentos: {lentos}.")
    if kpis["itens_mais_vendidos"]:
        itens = "; ".join(
            f"{i['nome']} ({inteiro(i['quantidade'])})" for i in kpis["itens_mais_vendidos"]
        )
        frases.append(f"Itens mais vendidos: {itens}.")
    return " ".join(frases)


def _dados_para_o_modelo(inicio: date, fim: date, kpis: dict, var: dict) -> str:
    linhas = [
        f"Período: {data_br(inicio)} a {data_br(fim)}",
        (f"Pedidos válidos: {inteiro(kpis['pedidos'])} (variação {variacao(var.get('pedidos'))})"),
        (
            f"Faturamento: {dinheiro(kpis['faturamento'])} "
            f"(variação {variacao(var.get('faturamento'))})"
        ),
        (
            f"Ticket médio: {ou_traco(kpis['ticket_medio'], dinheiro)} "
            f"(variação {variacao(var.get('ticket_medio'))})"
        ),
        (
            "Tempo médio de entrega: "
            f"{ou_traco(kpis['tempo_medio_entrega_min'], minutos)} "
            f"(variação {variacao(var.get('tempo_medio_entrega_min'))})"
        ),
        (
            f"Cancelamento: {ou_traco(kpis['cancelamento_pct'], percentual)} "
            f"(variação {variacao(var.get('cancelamento_pct'), em_pontos=True)}, "
            "em pontos percentuais)"
        ),
        f"{N_BAIRROS} bairros mais lentos (tempo médio de entrega):",
        *(
            f"- {b['bairro']}: {minutos(b['tempo_medio_min'])}, {inteiro(b['pedidos'])} pedidos"
            for b in kpis["bairros_mais_lentos"]
        ),
        f"{N_ITENS} itens mais vendidos (unidades):",
        *(f"- {i['nome']}: {inteiro(i['quantidade'])}" for i in kpis["itens_mais_vendidos"]),
    ]
    return "\n".join(linhas)


def _paragrafo_do_modelo(
    modelo: ModeloLLM, dados: str, kpis: dict, var: dict, periodo: tuple[date, date]
) -> str | None:
    """Parágrafo aprovado na checagem, ou `None` (provedor fora do ar, vazio ou recusado 2x)."""
    mensagens: list[dict] = [
        {"role": "system", "content": _INSTRUCAO_DO_SISTEMA},
        {"role": "user", "content": dados},
    ]
    for tentativa in range(2):
        try:
            resposta = modelo.completar(mensagens, [])
        except ErroDoProvedor:
            return None
        texto = (resposta.conteudo or "").strip()
        if not texto:
            return None
        invalidos = numeros_nao_suportados(texto, kpis, var, periodo)
        if not invalidos:
            return texto
        if tentativa == 0:
            mensagens = [
                *mensagens,
                {"role": "assistant", "content": texto},
                {
                    "role": "user",
                    "content": (
                        "Estes números não estão nos dados: "
                        f"{', '.join(sorted(invalidos))}. Reescreva o parágrafo usando "
                        "somente os números fornecidos, copiados exatamente."
                    ),
                },
            ]
    return None


def _tabela(kpis: dict, var: dict) -> list[str]:
    linhas = [
        ("Pedidos válidos", inteiro(kpis["pedidos"]), variacao(var.get("pedidos"))),
        ("Faturamento", dinheiro(kpis["faturamento"]), variacao(var.get("faturamento"))),
        (
            "Ticket médio",
            ou_traco(kpis["ticket_medio"], dinheiro),
            variacao(var.get("ticket_medio")),
        ),
        (
            "Tempo médio de entrega",
            ou_traco(kpis["tempo_medio_entrega_min"], minutos),
            variacao(var.get("tempo_medio_entrega_min")),
        ),
        (
            "Cancelamento",
            ou_traco(kpis["cancelamento_pct"], percentual),
            variacao(var.get("cancelamento_pct"), em_pontos=True),
        ),
    ]
    return [
        "| KPI | Semana | Variação |",
        "|---|---:|---:|",
        *(f"| {nome} | {valor} | {delta} |" for nome, valor, delta in linhas),
    ]


def gerar_relatorio(conn: sqlite3.Connection, modelo: ModeloLLM | None, semana_inicio: date) -> str:
    """Relatório da semana (segunda a domingo) em Markdown.

    Levanta `ValueError` se `semana_inicio` não for segunda-feira ou cair fora dos dados.
    """
    validar_semana(semana_inicio)
    fim = semana_inicio + timedelta(days=6)
    kpis = calcular_kpis(conn, semana_inicio, fim)
    anterior = calcular_kpis(conn, semana_inicio - timedelta(days=7), fim - timedelta(days=7))
    var = variacoes(kpis, anterior)

    paragrafo = None
    if modelo is not None:
        dados = _dados_para_o_modelo(semana_inicio, fim, kpis, var)
        paragrafo = _paragrafo_do_modelo(modelo, dados, kpis, var, (semana_inicio, fim))
    rotulo = _ROTULO_MODELO
    if paragrafo is None:
        paragrafo = resumo_deterministico(kpis, var)
        rotulo = _ROTULO_CODIGO

    partes = [
        f"# Relatório semanal: {data_br(semana_inicio)} a {data_br(fim)}",
        "",
        "Dados sintéticos de um restaurante fictício.",
        "",
        "## Resumo",
        "",
        paragrafo,
        "",
        rotulo,
        "",
        "## KPIs",
        "",
        *_tabela(kpis, var),
        "",
        f"## Bairros mais lentos (mínimo de {MIN_PEDIDOS_BAIRRO} pedidos)",
        "",
        *(
            f"- {b['bairro']}: {minutos(b['tempo_medio_min'])} em {inteiro(b['pedidos'])} pedidos"
            for b in kpis["bairros_mais_lentos"]
        ),
        "",
        f"## {N_ITENS} itens mais vendidos",
        "",
        *(
            f"- {i['nome']}: {inteiro(i['quantidade'])} unidades"
            for i in kpis["itens_mais_vendidos"]
        ),
        "",
    ]
    return "\n".join(partes)


# --- linha de comando -------------------------------------------------------------------------


def _erro(mensagem: str) -> None:
    print(mensagem, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Sai com 0 (ok), 1 (banco ausente) ou 2 (argumento inválido)."""
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8")  # acentos no console do Windows

    parser = argparse.ArgumentParser(
        prog="python -m analista.relatorio",
        description="Relatório semanal do restaurante fictício.",
    )
    parser.add_argument("--semana", required=True, help="segunda-feira da semana, AAAA-MM-DD")
    parser.add_argument("--sem-modelo", action="store_true", help="resumo montado pelo código")
    parser.add_argument(
        "--saida",
        action="store_true",
        help="grava relatorios/semana-AAAA-MM-DD.md em vez de imprimir",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as saida:  # argparse já escreveu a mensagem; devolve o código
        return int(saida.code or 0)

    try:
        semana = date.fromisoformat(args.semana)
    except ValueError:
        _erro(f"Data inválida: '{args.semana}'. Use o formato AAAA-MM-DD, por exemplo 2026-09-21.")
        return 2
    try:
        validar_semana(semana)
    except ValueError as erro:
        _erro(str(erro))
        return 2

    config = carregar_configuracao()
    if not config.banco.exists():
        _erro(f"Banco não encontrado em {config.banco}. Gere-o com: python -m analista.dados")
        return 1

    modelo = None if args.sem_modelo else criar_modelo(config)
    conn = abrir_somente_leitura(config.banco)
    try:
        texto = gerar_relatorio(conn, modelo, semana)
    finally:
        conn.close()

    if args.saida:
        caminho: Path = PASTA_RELATORIOS / f"semana-{semana.isoformat()}.md"
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(texto, encoding="utf-8")
        print(f"Relatório gravado em {caminho}")
    else:
        print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
