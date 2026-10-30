"""Execução somente leitura do SQL escrito pelo modelo, em cinco camadas.

1. Conexão `mode=ro` + `PRAGMA query_only=ON` (o motor recusa escrita).
2. Texto: uma única instrução, começando por SELECT ou WITH.
3. Authorizer do SQLite: lista permitida de ações, tabelas e funções; o resto é negado.
4. Teto de linhas com `fetchmany(limite_linhas + 1)`.
5. Progress handler que interrompe a consulta depois de `timeout_s`.

O authorizer e o progress handler são instalados a cada chamada, com estado próprio da chamada,
e removidos no fim: nada fica em variável de módulo, e cada pergunta usa a sua conexão.
"""

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

MOTIVOS_DE_ESCRITA = frozenset({"nao_select", "acao"})
TABELAS_PERMITIDAS = frozenset({"itens_cardapio", "pedidos", "itens_pedido"})
FUNCOES_PERMITIDAS = frozenset(
    {
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "round",
        "abs",
        "coalesce",
        "ifnull",
        "nullif",
        "length",
        "lower",
        "upper",
        "substr",
        "trim",
        "replace",
        "instr",
        "strftime",
        "date",
        "datetime",
        "julianday",
        "iif",
        "total",
        "group_concat",
        # Acrescentadas na revisão: comuns em análise e sem efeito colateral.
        "like",
        "time",
        "row_number",
        "rank",
        "dense_rank",
        "lag",
        "lead",
    }
)

_INSTRUCOES_POR_CHECAGEM = 100  # passos da VM do SQLite entre duas olhadas no relógio
# Bytes de um texto ou blob (o padrão do SQLite é 1 GB). trim/instr/replace custam
# len(X) * len(Y) dentro de um único passo da VM, fora do alcance do progress handler:
# o teto limita o pior caso de uma chamada (medido no SQLite 3.49.1: ~8 ms, trim).
_TAMANHO_MAXIMO_VALOR = 10_000
_TAMANHO_MAXIMO_LIKE = 100  # caracteres de um padrão LIKE
_INICIO_PERMITIDO = re.compile(r"(?:SELECT|WITH)\b", re.IGNORECASE)
_TOKEN = re.compile(r"""'(?:[^']|'')*'?|"(?:[^"]|"")*"?|`(?:[^`]|``)*`?|\[[^\]]*\]?|\w+|\S""")


class SqlRejeitado(Exception):
    """A consulta violou uma regra; `motivo` diz qual."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class ErroDeConsulta(Exception):
    """Erro do próprio SQLite (sintaxe, coluna inexistente); a mensagem volta ao modelo."""


@dataclass(frozen=True)
class ResultadoSql:
    colunas: tuple[str, ...]
    linhas: tuple[tuple, ...]
    truncado: bool


def abrir_somente_leitura(caminho: Path) -> sqlite3.Connection:
    """Conexão em que o motor recusa escrita, sem depender de validador nenhum."""
    conn = sqlite3.connect(Path(caminho).resolve().as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    # Uma única chamada de função (replace aninhado, trim, instr) pode gastar memória ou tempo
    # num passo só da VM, antes de o progress handler olhar o relógio. Os tetos cortam isso.
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _TAMANHO_MAXIMO_VALOR)
    conn.setlimit(sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, _TAMANHO_MAXIMO_LIKE)
    return conn


def _instrucoes(sql: str) -> list[str]:
    """Separa as instruções por `;` fora de strings e identificadores, sem os comentários."""
    partes: list[str] = []
    atual: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c in "'\"`[":
            fecha = "]" if c == "[" else c
            j = i + 1
            while True:
                j = sql.find(fecha, j)
                if j == -1:
                    j = n  # sem fechamento: o SQLite acusa o erro de sintaxe
                    break
                if fecha != "]" and sql.startswith(fecha * 2, j):
                    j += 2  # aspas dobradas são escape dentro do literal
                    continue
                j += 1
                break
            atual.append(sql[i:j])
            i = j
        elif sql.startswith("--", i):
            fim = sql.find("\n", i)
            i = n if fim == -1 else fim
            atual.append(" ")
        elif sql.startswith("/*", i):
            fim = sql.find("*/", i + 2)
            i = n if fim == -1 else fim + 2
            atual.append(" ")
        elif c == ";":
            partes.append("".join(atual))
            atual = []
            i += 1
        else:
            atual.append(c)
            i += 1
    partes.append("".join(atual))
    return [parte.strip() for parte in partes if parte.strip()]


def _validar_texto(sql: str) -> str:
    instrucoes = _instrucoes(sql)
    if not instrucoes:
        raise SqlRejeitado("vazio")
    if len(instrucoes) > 1:
        raise SqlRejeitado("multiplas_instrucoes")
    (instrucao,) = instrucoes
    if not _INICIO_PERMITIDO.match(instrucao):
        raise SqlRejeitado("nao_select")
    return instrucao


def _sem_aspas(identificador: str) -> str:
    if identificador[0] == "[":
        return identificador[1:-1]
    if identificador[0] in '"`':
        aspa = identificador[0]
        return identificador[1:-1].replace(aspa * 2, aspa)
    return identificador


def _depois_dos_parenteses(tokens: list[str], i: int) -> int:
    """Com `tokens[i] == "("`, devolve o índice logo após o `)` correspondente."""
    profundidade = 0
    while i < len(tokens):
        profundidade += {"(": 1, ")": -1}.get(tokens[i], 0)
        i += 1
        if profundidade == 0:
            break
    return i


def _ctes_declaradas(instrucao: str) -> set[str]:
    """Nomes de CTE declarados após cada `WITH [RECURSIVE]`, normalizados com casefold.

    Lê só a lista `nome [(colunas)] AS [NOT] [MATERIALIZED] (...) [, ...]` que segue cada WITH
    (inclusive os aninhados), então `WINDOW nome AS (` não conta. Errar para mais só libera a
    leitura sem colunas (ver `_Vigia.autorizar`) de um nome que não é tabela nem módulo; errar
    para menos só recusa a consulta. Os dois lados falham fechados.
    """
    tokens = _TOKEN.findall(instrucao)
    n = len(tokens)
    nomes: set[str] = set()
    for inicio, token in enumerate(tokens):
        if token.upper() != "WITH":
            continue
        i = inicio + 1
        if i < n and tokens[i].upper() == "RECURSIVE":
            i += 1
        while i < n:
            nome = tokens[i]
            if not (nome[0].isalnum() or nome[0] in '_"`['):
                break
            i += 1
            if i < n and tokens[i] == "(":
                i = _depois_dos_parenteses(tokens, i)
            if i >= n or tokens[i].upper() != "AS":
                break
            i += 1
            for opcional in ("NOT", "MATERIALIZED"):
                if i < n and tokens[i].upper() == opcional:
                    i += 1
            if i >= n or tokens[i] != "(":
                break
            nomes.add(_sem_aspas(nome).casefold())
            i = _depois_dos_parenteses(tokens, i)
            if i < n and tokens[i] == ",":
                i += 1
                continue
            break
    return nomes


def _nomes_reservados(conn: sqlite3.Connection) -> set[str]:
    """Nomes que um FROM pode resolver sem ser CTE: objetos do esquema e módulos (json_each...)."""
    linhas = conn.execute(
        "SELECT name FROM sqlite_schema UNION SELECT name FROM sqlite_temp_schema "
        "UNION SELECT name FROM pragma_module_list"
    ).fetchall()
    return {nome.casefold() for (nome,) in linhas}


class _Vigia:
    """Estado de uma única chamada: CTEs aceitas, o primeiro motivo de negação e o prazo."""

    def __init__(self, timeout_s: float, ctes: set[str]) -> None:
        self.motivo: str | None = None
        self.esgotou = False
        self._ctes = ctes
        self._prazo = time.monotonic() + timeout_s

    def _negar(self, motivo: str) -> int:
        if self.motivo is None:
            self.motivo = motivo
        return sqlite3.SQLITE_DENY

    def autorizar(
        self, acao: int, arg1: str | None, arg2: str | None, banco: str | None, _gatilho: str | None
    ) -> int:
        if acao in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if acao == sqlite3.SQLITE_READ:
            # Leitura de coluna chega sempre com a tabela real de origem, mesmo via CTE ou
            # subconsulta. Leitura sem coluna (arg2 == "", ex.: count(*)) chega com o nome como
            # escrito, que pode ser uma CTE materializada: só passa se for CTE declarada no texto
            # que não coincide com nome do esquema, de módulo nem com sqlite_*/pragma_*.
            tabela = (arg1 or "").casefold()
            if tabela in TABELAS_PERMITIDAS and banco in (None, "main"):
                return sqlite3.SQLITE_OK
            if arg2 == "" and banco is None and tabela in self._ctes:
                return sqlite3.SQLITE_OK
            return self._negar("tabela")
        if acao == sqlite3.SQLITE_FUNCTION:
            if (arg2 or "").casefold() in FUNCOES_PERMITIDAS:
                return sqlite3.SQLITE_OK
            return self._negar("funcao")
        return self._negar("acao")

    def progresso(self) -> int:
        if time.monotonic() > self._prazo:
            self.esgotou = True
            return 1
        return 0


def executar_consulta(
    conn: sqlite3.Connection, sql: str, *, limite_linhas: int = 50, timeout_s: float = 2.0
) -> ResultadoSql:
    """Roda uma consulta do modelo. Levanta `SqlRejeitado` ou `ErroDeConsulta`."""
    instrucao = _validar_texto(sql)
    ctes = {
        nome
        for nome in _ctes_declaradas(instrucao) - _nomes_reservados(conn)
        if not nome.startswith(("sqlite_", "pragma_"))
    }
    vigia = _Vigia(timeout_s, ctes)
    conn.set_authorizer(vigia.autorizar)
    conn.set_progress_handler(vigia.progresso, _INSTRUCOES_POR_CHECAGEM)
    cursor = None
    try:
        cursor = conn.execute(instrucao)
        linhas = cursor.fetchmany(limite_linhas + 1)
        colunas = tuple(descricao[0] for descricao in cursor.description or ())
    except (sqlite3.Error, UnicodeError) as erro:  # UnicodeError: ex. surrogate solto no texto
        if vigia.motivo is not None:
            raise SqlRejeitado(vigia.motivo) from erro
        if vigia.esgotou:
            raise SqlRejeitado("tempo") from erro
        raise ErroDeConsulta(str(erro)) from erro
    finally:
        if cursor is not None:
            cursor.close()
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)
    return ResultadoSql(
        colunas=colunas,
        linhas=tuple(linhas[:limite_linhas]),
        truncado=len(linhas) > limite_linhas,
    )
