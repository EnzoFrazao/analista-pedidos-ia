"""As duas ferramentas do agente, o esquema JSON delas e o despacho das chamadas do modelo.

`despachar` devolve dois resultados: o texto JSON que volta ao modelo como resultado da
ferramenta e um dicionário de auditoria para o rastro e para a avaliação. Tudo o que o modelo
pode errar (JSON quebrado, argumento faltando, SQL recusado, erro de sintaxe, ferramenta que
não existe) vira um JSON `{"erro": ..., "motivo": ...}` para ele corrigir; o resto é bug nosso
e sobe como exceção.
"""

import json
import sqlite3
from pathlib import Path

from analista.dados import DEFINICOES, PRAZO_MIN
from analista.sql_seguro import (
    TABELAS_PERMITIDAS,
    ErroDeConsulta,
    ResultadoSql,
    SqlRejeitado,
    abrir_somente_leitura,
    executar_consulta,
)

_DESCRICAO_EXECUTAR_SQL = (
    "Executa uma consulta SQL de leitura (SQLite) sobre os pedidos e devolve colunas, linhas "
    "e `truncado`. Só SELECT ou WITH, uma instrução por chamada; escrita é recusada. Devolve "
    "no máximo 50 linhas e marca `truncado: true` quando havia mais (agregue em vez de listar). "
    "Tabelas permitidas: itens_cardapio, pedidos, itens_pedido. LIKE é permitido; glob e printf "
    "não. Agregados de texto muito longos (group_concat enorme) falham. Chame "
    "descrever_esquema primeiro, para saber colunas, valores e definições de métrica."
)

FERRAMENTAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "descrever_esquema",
            "description": (
                "Descreve as tabelas, colunas e tipos, os valores possíveis de bairro, canal, "
                "status e categoria, o período dos dados e as definições de métrica. Sem "
                "parâmetros."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "executar_sql",
            "description": _DESCRICAO_EXECUTAR_SQL,
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {
                        "type": "string",
                        "description": "A consulta SQL (SELECT ou WITH).",
                    }
                },
                "required": ["consulta"],
            },
        },
    },
]

_MENSAGENS_DE_ERRO = {
    "vazio": "A consulta está vazia.",
    "multiplas_instrucoes": "Envie uma única instrução SQL por chamada.",
    "nao_select": "Só consultas que começam por SELECT ou WITH são aceitas (somente leitura).",
    "tabela": "A consulta usa uma tabela não permitida. Use só itens_cardapio, pedidos e itens_pedido.",
    "funcao": "A consulta usa uma função SQL não permitida. Use funções de agregação, texto e data comuns.",
    "acao": "A consulta tenta uma operação não permitida (somente leitura).",
    "tempo": "A consulta passou do tempo máximo. Simplifique, filtre mais ou agregue.",
}


def _consultar(conn: sqlite3.Connection, sql: str) -> list:
    return conn.execute(sql).fetchall()


def descrever_esquema(caminho: Path) -> str:
    """Texto com o esquema, os valores distintos, o período e as definições de métrica.

    Lê o banco por conexão própria e fecha no fim. Lista só categorias, nunca nomes de itens:
    um nome de item é texto livre e iria para o prompt de toda pergunta.
    """
    conn = abrir_somente_leitura(caminho)
    try:
        partes = ["Tabelas (SQLite):"]
        for tabela in sorted(TABELAS_PERMITIDAS):
            # PRAGMA aqui é código nosso numa conexão somente leitura, fora do caminho do modelo.
            colunas = conn.execute(f"PRAGMA table_info({tabela})").fetchall()
            partes.append(f"- {tabela}(" + ", ".join(f"{c[1]} {c[2]}" for c in colunas) + ")")

        def valores(coluna: str, tabela: str) -> str:
            resultado = executar_consulta(
                conn, f"SELECT DISTINCT {coluna} FROM {tabela} ORDER BY 1", limite_linhas=200
            )
            return ", ".join(str(linha[0]) for linha in resultado.linhas)

        ((primeiro, ultimo),) = executar_consulta(
            conn, "SELECT MIN(date(data_hora)), MAX(date(data_hora)) FROM pedidos"
        ).linhas
        partes += [
            "",
            f"Período dos dados: de {primeiro} a {ultimo} (data_hora em texto ISO).",
            "",
            "Valores possíveis (use exatamente esta grafia):",
            f"- pedidos.bairro: {valores('bairro', 'pedidos')}",
            f"- pedidos.canal: {valores('canal', 'pedidos')}",
            f"- pedidos.status: {valores('status', 'pedidos')}",
            f"- itens_cardapio.categoria: {valores('categoria', 'itens_cardapio')}",
            "",
            "Significado do status:",
            f"- entregue: entrega em até {PRAZO_MIN} min.",
            f"- atrasado: entrega acima de {PRAZO_MIN} min.",
            "- cancelado: sem entrega.",
            "",
            DEFINICOES.rstrip(),
        ]
    finally:
        conn.close()
    return "\n".join(partes)


def _json(conteudo: dict) -> str:
    return json.dumps(conteudo, ensure_ascii=False)


def _auditoria(
    *,
    sql: str | None = None,
    ok: bool,
    motivo: str | None = None,
    resultado: ResultadoSql | None = None,
) -> dict:
    colunas = list(resultado.colunas) if resultado else []
    linhas = [list(linha) for linha in resultado.linhas] if resultado else []
    return {
        "sql": sql,
        "ok": ok,
        "motivo": motivo,
        "n_linhas": len(linhas),
        "colunas": colunas,
        "linhas": linhas,
        "truncado": resultado.truncado if resultado else False,
    }


def _erro(motivo: str, mensagem: str, *, sql: str | None = None) -> tuple[str, dict]:
    return _json({"erro": mensagem, "motivo": motivo}), _auditoria(sql=sql, ok=False, motivo=motivo)


def _ler_argumentos(argumentos_json: str, esperados: tuple[str, ...]) -> dict | tuple[str, dict]:
    """Os argumentos como dicionário de textos, ou o par de erro pronto para devolver."""
    try:
        # Alguns provedores mandam texto vazio para ferramenta sem parâmetros.
        argumentos = json.loads(argumentos_json) if argumentos_json.strip() else {}
    except json.JSONDecodeError:
        return _erro("json_invalido", "Os argumentos não são um JSON válido. Envie um objeto JSON.")
    if not isinstance(argumentos, dict):
        return _erro("argumento_invalido", "Os argumentos devem ser um objeto JSON.")
    ausentes = [nome for nome in esperados if nome not in argumentos]
    if ausentes:
        return _erro("argumento_ausente", f"Faltou o argumento: {', '.join(ausentes)}.")
    extras = [nome for nome in argumentos if nome not in esperados]
    if extras:
        return _erro("argumento_invalido", f"Argumento não aceito: {', '.join(extras)}.")
    if not all(isinstance(valor, str) for valor in argumentos.values()):
        return _erro("argumento_invalido", "Os argumentos devem ser texto.")
    return argumentos


def despachar(
    conn: sqlite3.Connection,
    caminho: Path,
    nome: str,
    argumentos_json: str,
    *,
    limite_linhas: int,
    timeout_s: float,
) -> tuple[str, dict]:
    """Atende uma chamada de ferramenta do modelo: (JSON para o modelo, dicionário de auditoria)."""
    if nome not in ("descrever_esquema", "executar_sql"):
        return _erro(
            "ferramenta_inexistente",
            f"A ferramenta '{nome}' não existe. Use descrever_esquema ou executar_sql.",
        )
    esperados = ("consulta",) if nome == "executar_sql" else ()
    argumentos = _ler_argumentos(argumentos_json, esperados)
    if isinstance(argumentos, tuple):
        return argumentos
    if nome == "descrever_esquema":
        return descrever_esquema(caminho), _auditoria(ok=True)

    sql = argumentos["consulta"]
    try:
        resultado = executar_consulta(conn, sql, limite_linhas=limite_linhas, timeout_s=timeout_s)
    except SqlRejeitado as rejeitado:
        mensagem = _MENSAGENS_DE_ERRO.get(rejeitado.motivo, "A consulta foi recusada.")
        return _erro(rejeitado.motivo, mensagem, sql=sql)
    except ErroDeConsulta as erro:
        return _erro("erro_de_consulta", f"Erro do SQLite: {erro}", sql=sql)
    auditoria = _auditoria(sql=sql, ok=True, resultado=resultado)
    texto = _json(
        {
            "colunas": auditoria["colunas"],
            "linhas": auditoria["linhas"],
            "truncado": resultado.truncado,
        }
    )
    return texto, auditoria
