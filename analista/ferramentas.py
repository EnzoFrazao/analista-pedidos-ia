"""As duas ferramentas do agente, o esquema JSON delas e o despacho das chamadas do modelo.

`despachar` devolve dois resultados: o texto JSON que volta ao modelo como resultado da
ferramenta e um dicionário de auditoria para o rastro e para a avaliação. Tudo o que o modelo
pode errar (JSON quebrado, argumento faltando, SQL recusado, erro de sintaxe, ferramenta que
não existe) vira um JSON `{"erro": ..., "motivo": ...}` para ele corrigir; o resto é bug nosso
e sobe como exceção.

Os dois resultados passam pelos mesmos tetos de saída (célula, colunas e bytes, constantes
`MAX_*` abaixo): o que foi cortado aparece em `cortes`, e linhas tiradas marcam `truncado`.
"""

import json
import math
import sqlite3
from pathlib import Path

from analista.dados import DEFINICOES, PRAZO_MIN
from analista.sql_seguro import (
    LIMITE_LINHAS,
    TABELAS_PERMITIDAS,
    ErroDeConsulta,
    ResultadoSql,
    SqlRejeitado,
    abrir_somente_leitura,
    executar_consulta,
)

# Tetos da saída de `executar_sql`, aplicados no `despachar` (um lugar só): valem para o JSON
# que volta ao modelo e para a auditoria que vai ao rastro e à avaliação. Sem eles, uma
# consulta curta (replace aninhado, muitas colunas, junção) gera dezenas de MB por chamada.
MAX_CARACTERES_CELULA = 200
MAX_CARACTERES_COLUNA = 64  # nome de coluna; 30 x 64 deixa espaço para linhas no teto em bytes
MAX_COLUNAS = 30
MAX_BYTES_RESULTADO = 32 * 1024  # JSON inteiro em UTF-8

_AVISO_DE_CORTE = (
    "O resultado foi cortado para caber no limite da ferramenta (veja `cortes`): textos longos "
    "terminam em '…[+N caracteres]', colunas além do limite e linhas do fim foram omitidas. "
    "Não trate o resultado como completo; agregue, selecione menos colunas ou use substr()."
)


def _descricao_executar_sql(limite_linhas: int) -> str:
    return (
        "Executa uma consulta SQL de leitura (SQLite) sobre os pedidos e devolve colunas, "
        "linhas e `truncado`. Só SELECT ou WITH, uma instrução por chamada; escrita é recusada. "
        f"Devolve no máximo {limite_linhas} linhas e marca `truncado: true` quando havia mais "
        "(agregue em vez de listar). Saída limitada: até "
        f"{MAX_COLUNAS} colunas, textos de até {MAX_CARACTERES_CELULA} caracteres e "
        f"{MAX_BYTES_RESULTADO // 1024} KB no total; o que passar disso é cortado e listado em "
        "`cortes`. Tabelas permitidas: itens_cardapio, pedidos, itens_pedido. LIKE é "
        "permitido; glob e printf não. Agregados de texto muito longos (group_concat enorme) "
        "falham. Chame descrever_esquema primeiro, para saber colunas, valores e definições de "
        "métrica."
    )


def ferramentas(limite_linhas: int = LIMITE_LINHAS) -> list[dict]:
    """O esquema das ferramentas no formato da API de chat, com o teto de linhas em uso."""
    return [
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
                "description": _descricao_executar_sql(limite_linhas),
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


FERRAMENTAS: list[dict] = ferramentas()

_MENSAGENS_DE_ERRO = {
    "vazio": "A consulta está vazia.",
    "multiplas_instrucoes": "Envie uma única instrução SQL por chamada.",
    "nao_select": "Só consultas que começam por SELECT ou WITH são aceitas (somente leitura).",
    "tabela": "A consulta usa uma tabela não permitida. Use só itens_cardapio, pedidos e itens_pedido.",
    "funcao": "A consulta usa uma função SQL não permitida. Use funções de agregação, texto e data comuns.",
    "acao": "A consulta tenta uma operação não permitida (somente leitura).",
    "tempo": "A consulta passou do tempo máximo. Simplifique, filtre mais ou agregue.",
}


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


def _json(conteudo: object) -> str:
    # allow_nan=False: qualquer NaN/Infinity que escape da normalização estoura nos testes.
    return json.dumps(conteudo, ensure_ascii=False, allow_nan=False)


def _celula(valor: object) -> object:
    """Valor de uma célula como JSON válido: blob vira texto curto, não finito vira null."""
    if isinstance(valor, bytes):
        return f"<blob {len(valor)} bytes>"
    if isinstance(valor, float) and not math.isfinite(valor):
        return None
    return valor


def _encurtar(texto: str, maximo: int) -> tuple[str, bool]:
    """O texto até `maximo` caracteres, com um marcador visível do que faltou."""
    if len(texto) <= maximo:
        return texto, False
    return f"{texto[:maximo]}…[+{len(texto) - maximo} caracteres]", True


def _limitar(resultado: ResultadoSql) -> tuple[list[str], list[list], bool, dict[str, int]]:
    """Aplica os tetos de saída: (colunas, linhas, truncado, cortes).

    `cortes` conta o que foi tirado (só as chaves com valor positivo); linhas tiradas para
    caber em `MAX_BYTES_RESULTADO` também marcam `truncado`, porque faltam linhas.
    """
    cortes: dict[str, int] = {}

    def contar(chave: str, n: int = 1) -> None:
        if n > 0:
            cortes[chave] = cortes.get(chave, 0) + n

    contar("colunas_omitidas", len(resultado.colunas) - MAX_COLUNAS)
    colunas = []
    for nome in resultado.colunas[:MAX_COLUNAS]:
        nome, cortado = _encurtar(str(nome), MAX_CARACTERES_COLUNA)
        colunas.append(nome)
        contar("nomes_de_coluna_encurtados", int(cortado))

    linhas = []
    for linha in resultado.linhas:
        nova = []
        for valor in linha[:MAX_COLUNAS]:
            valor = _celula(valor)
            if isinstance(valor, str):
                valor, cortado = _encurtar(valor, MAX_CARACTERES_CELULA)
                contar("celulas_encurtadas", int(cortado))
            nova.append(valor)
        linhas.append(nova)

    # Reserva para o resto do JSON (colunas, chaves, `cortes` e `aviso`); as linhas entram em
    # ordem enquanto couberem. A conferência final no `despachar` garante o teto de qualquer jeito.
    orcamento = MAX_BYTES_RESULTADO - _tamanho(colunas) - 1024
    cabem = 0
    for linha in linhas:
        orcamento -= _tamanho(linha) + 2
        if orcamento < 0:
            break
        cabem += 1
    contar("linhas_omitidas", len(linhas) - cabem)
    return colunas, linhas[:cabem], resultado.truncado or cabem < len(linhas), cortes


def _tamanho(valor: object) -> int:
    return len(_json(valor).encode("utf-8"))


def _auditoria(
    *,
    sql: str | None = None,
    ok: bool,
    motivo: str | None = None,
    resultado: ResultadoSql | None = None,
) -> dict:
    colunas, linhas, truncado, cortes = _limitar(resultado) if resultado else ([], [], False, {})
    return {
        "sql": sql,
        "ok": ok,
        "motivo": motivo,
        "n_linhas": len(linhas),
        "colunas": colunas,
        "linhas": linhas,
        "truncado": truncado,
        "cortes": cortes,
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
    """Atende uma chamada de ferramenta do modelo: (JSON para o modelo, dicionário de auditoria).

    As `linhas` da auditoria são listas (e já normalizadas: blob vira texto, não finito vira
    null, texto longo encurtado com marcador); quem reconstrói um `ResultadoSql` a partir delas
    deve converter cada linha em tupla e tratar `cortes` não vazio como resultado incompleto.
    """
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
    texto = _texto_do_resultado(auditoria)
    while len(texto.encode("utf-8")) > MAX_BYTES_RESULTADO and auditoria["linhas"]:
        # Defesa: a estimativa de `_limitar` já deixa folga, mas o teto é garantido aqui.
        auditoria["linhas"].pop()
        auditoria["n_linhas"] -= 1
        auditoria["truncado"] = True
        auditoria["cortes"]["linhas_omitidas"] = auditoria["cortes"].get("linhas_omitidas", 0) + 1
        texto = _texto_do_resultado(auditoria)
    return texto, auditoria


def _texto_do_resultado(auditoria: dict) -> str:
    """O JSON que o modelo recebe; `cortes` e `aviso` só aparecem quando algo foi cortado."""
    conteudo = {
        "colunas": auditoria["colunas"],
        "linhas": auditoria["linhas"],
        "truncado": auditoria["truncado"],
    }
    if auditoria["cortes"]:
        conteudo["cortes"] = auditoria["cortes"]
        conteudo["aviso"] = _AVISO_DE_CORTE
    return _json(conteudo)
