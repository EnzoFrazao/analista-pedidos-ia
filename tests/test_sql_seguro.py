"""Camadas de segurança do SQL escrito pelo modelo. Cada ataque tem o `motivo` esperado."""

import shutil
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from analista.sql_seguro import (
    FUNCOES_PERMITIDAS,
    MOTIVOS_DE_ESCRITA,
    TABELAS_PERMITIDAS,
    ErroDeConsulta,
    ResultadoSql,
    SqlRejeitado,
    abrir_somente_leitura,
    executar_consulta,
    tenta_escrita,
)


@pytest.fixture
def conn(banco_path: Path) -> Iterator[sqlite3.Connection]:
    conexao = abrir_somente_leitura(banco_path)
    yield conexao
    conexao.close()


def _motivo(conn: sqlite3.Connection, sql: str, **kwargs: float) -> str:
    with pytest.raises(SqlRejeitado) as erro:
        executar_consulta(conn, sql, **kwargs)
    return erro.value.motivo


# --- contrato ---------------------------------------------------------------


def test_contrato_das_constantes() -> None:
    assert MOTIVOS_DE_ESCRITA == frozenset({"nao_select", "acao"})
    assert TABELAS_PERMITIDAS == frozenset({"itens_cardapio", "pedidos", "itens_pedido"})
    assert {"count", "sum", "strftime", "group_concat", "iif", "total"} <= FUNCOES_PERMITIDAS
    assert "load_extension" not in FUNCOES_PERMITIDAS


# --- o que deve passar ------------------------------------------------------


def test_aceita_select_simples(conn: sqlite3.Connection) -> None:
    resultado = executar_consulta(conn, "SELECT count(*) AS total FROM pedidos")
    assert resultado == ResultadoSql(colunas=("total",), linhas=((8000,),), truncado=False)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1;",
        "SELECT 1 -- ok",
        "SELECT 1; -- ok",
        "SELECT 1 /* fim */ ;",
        "SELECT 1;\n\n",
    ],
)
def test_aceita_ponto_e_virgula_final_e_comentario_final(
    conn: sqlite3.Connection, sql: str
) -> None:
    assert executar_consulta(conn, sql).linhas == ((1,),)


def test_aceita_with(conn: sqlite3.Connection) -> None:
    sql = "WITH v AS (SELECT valor FROM pedidos WHERE status <> 'cancelado') SELECT count(*) FROM v"
    (linha,) = executar_consulta(conn, sql).linhas
    assert 0 < linha[0] < 8000


def test_aceita_maiusculas_e_espacos_iniciais(conn: sqlite3.Connection) -> None:
    assert executar_consulta(conn, "  \n sElEcT 1").linhas == ((1,),)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'a;b'",
        "SELECT 'a -- b'",
        "SELECT 'a /* b */'",
        "SELECT 'it''s; ok'",
    ],
)
def test_ponto_e_virgula_e_comentario_dentro_de_string_nao_contam(
    conn: sqlite3.Connection, sql: str
) -> None:
    (linha,) = executar_consulta(conn, sql).linhas
    assert linha[0] == sql[len("SELECT '") : -1].replace("''", "'")


def test_aceita_juncao_com_agrupamento_e_ordenacao(conn: sqlite3.Connection) -> None:
    sql = """
        SELECT c.categoria, sum(ip.quantidade * ip.preco_unit) AS receita, count(DISTINCT p.id)
        FROM pedidos p
        JOIN itens_pedido ip ON ip.pedido_id = p.id
        JOIN itens_cardapio c ON c.id = ip.item_id
        WHERE p.status <> 'cancelado'
        GROUP BY c.categoria
        ORDER BY receita DESC
    """
    resultado = executar_consulta(conn, sql)
    assert len(resultado.colunas) == 3
    assert resultado.linhas
    receitas = [linha[1] for linha in resultado.linhas]
    assert receitas == sorted(receitas, reverse=True)


def test_aceita_agrupamento_por_semana(conn: sqlite3.Connection) -> None:
    sql = """
        SELECT strftime('%Y-%W', data_hora) AS semana,
               round(avg(tempo_entrega_min), 1) AS tempo_medio,
               round(100.0 * total(iif(status = 'cancelado', 1, 0)) / count(*), 2) AS cancel_pct
        FROM pedidos
        GROUP BY semana
        ORDER BY semana
    """
    resultado = executar_consulta(conn, sql)
    assert resultado.colunas == ("semana", "tempo_medio", "cancel_pct")
    assert len(resultado.linhas) == 13  # %W começa na segunda; o período tem 13 semanas inteiras


def test_aceita_cte_com_subconsulta_e_recursiva(conn: sqlite3.Connection) -> None:
    sql = """
        WITH RECURSIVE dias(n) AS (SELECT 0 UNION ALL SELECT n + 1 FROM dias WHERE n < 6),
        por_bairro AS (
            SELECT bairro, coalesce(avg(nota), 0) AS nota FROM pedidos
            WHERE id IN (SELECT pedido_id FROM itens_pedido) GROUP BY bairro
        )
        SELECT (SELECT count(*) FROM dias), count(*), max(nota) FROM por_bairro
    """
    (linha,) = executar_consulta(conn, sql).linhas
    assert linha[0] == 7
    assert linha[1] > 0


@pytest.mark.parametrize(
    "sql",
    [
        # CTE usada só em count(*): o SQLite avisa a leitura sem coluna com o nome da CTE.
        "WITH b AS (SELECT bairro FROM pedidos GROUP BY bairro) SELECT count(*) FROM b",
        'WITH "B" AS MATERIALIZED (SELECT DISTINCT bairro FROM pedidos) SELECT count(*) FROM b',
        (
            "WITH [b](x) AS NOT MATERIALIZED (SELECT DISTINCT bairro FROM pedidos) "
            "SELECT count(*) FROM B"
        ),
        (
            "WITH b AS (SELECT DISTINCT bairro FROM pedidos) "
            "SELECT (SELECT count(*) FROM b) FROM b x WHERE x.bairro IS NOT NULL LIMIT 1"
        ),
        (
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 8) "
            "SELECT count(*) FROM n"
        ),
    ],
)
def test_aceita_contagem_sobre_cte(conn: sqlite3.Connection, sql: str) -> None:
    assert executar_consulta(conn, sql).linhas == ((8,),)


def test_resultado_vazio_tem_colunas(conn: sqlite3.Connection) -> None:
    resultado = executar_consulta(conn, "SELECT id, valor FROM pedidos WHERE 0")
    assert resultado == ResultadoSql(colunas=("id", "valor"), linhas=(), truncado=False)


# --- camada de texto --------------------------------------------------------


@pytest.mark.parametrize("sql", ["", "   \n", ";", "-- só comentário", "/* nada */ ;"])
def test_rejeita_vazio(conn: sqlite3.Connection, sql: str) -> None:
    assert _motivo(conn, sql) == "vazio"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO pedidos (id) VALUES (999999)",
        "UPDATE pedidos SET valor = 0",
        "DELETE FROM pedidos",
        "DROP TABLE pedidos",
        "CREATE TABLE x (a)",
        "ALTER TABLE pedidos ADD COLUMN x",
        "VACUUM INTO 'copia.db'",
        "REPLACE INTO pedidos (id) VALUES (1)",
    ],
)
def test_rejeita_insert_update_delete_drop_create_alter(conn: sqlite3.Connection, sql: str) -> None:
    assert _motivo(conn, sql) == "nao_select"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE pedidos",
        "SELECT 1; SELECT 2",
        "SELECT 'x;'; DELETE FROM pedidos",
        "SELECT 1 -- c\n; DELETE FROM pedidos",
    ],
)
def test_rejeita_duas_instrucoes(conn: sqlite3.Connection, sql: str) -> None:
    assert _motivo(conn, sql) == "multiplas_instrucoes"


@pytest.mark.parametrize("sql", ["/* x */ DELETE FROM pedidos", "-- SELECT\nDELETE FROM pedidos"])
def test_rejeita_comentario_que_esconde_escrita(conn: sqlite3.Connection, sql: str) -> None:
    assert _motivo(conn, sql) == "nao_select"


def test_rejeita_with_seguido_de_delete(conn: sqlite3.Connection) -> None:
    motivo = _motivo(conn, "WITH x AS (SELECT 1) DELETE FROM pedidos")
    assert motivo == "acao"
    assert motivo in MOTIVOS_DE_ESCRITA


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH DATABASE 'outro.db' AS outro",
        "PRAGMA query_only = OFF",
        "PRAGMA table_info(pedidos)",
        "attach 'outro.db' as outro",
    ],
)
def test_rejeita_attach_e_pragma(
    conn: sqlite3.Connection, sql: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _motivo(conn, sql) in MOTIVOS_DE_ESCRITA
    # ATTACH num banco `mode=ro` ainda criaria o arquivo vazio: quem impede é o validador.
    assert list(tmp_path.iterdir()) == []
    assert conn.execute("PRAGMA query_only").fetchone() == (1,)


@pytest.mark.parametrize(
    "sql",
    ["SELECT name FROM pragma_table_list", "SELECT * FROM pragma_table_info('pedidos')"],
)
def test_rejeita_pragma_como_funcao_de_tabela(conn: sqlite3.Connection, sql: str) -> None:
    # O SQLite avisa a leitura da "tabela" pragma_* antes da ação PRAGMA; os dois são negados.
    assert _motivo(conn, sql) in {"tabela", "acao"}


# --- authorizer -------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sqlite_master",
        "SELECT count(*) FROM sqlite_master",
        "SELECT 1 FROM sqlite_schema",
        "SELECT EXISTS (SELECT 1 FROM sqlite_master)",
        "WITH t AS (SELECT name FROM sqlite_master) SELECT * FROM t",
        "SELECT 1 FROM temp.sqlite_master",
        "SELECT p.id FROM pedidos p JOIN sqlite_master m ON 1 LIMIT 1",
    ],
)
def test_rejeita_sqlite_master(conn: sqlite3.Connection, sql: str) -> None:
    assert _motivo(conn, sql) == "tabela"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT load_extension('x')",
        "SELECT randomblob(10)",
        "SELECT zeroblob(10)",
        "SELECT LOAD_EXTENSION('x')",
        "SELECT count(*) FROM pedidos WHERE length(randomblob(1)) > 0",
    ],
)
def test_rejeita_funcao_fora_da_lista(conn: sqlite3.Connection, sql: str) -> None:
    assert _motivo(conn, sql) == "funcao"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT nome FROM itens_cardapio WHERE nome GLOB '*a*'",
        "SELECT printf('%.2f', avg(valor)) FROM pedidos",
    ],
)
def test_glob_e_printf_continuam_fora(conn: sqlite3.Connection, sql: str) -> None:
    assert _motivo(conn, sql) == "funcao"


def test_aceita_like(conn: sqlite3.Connection) -> None:
    (linha,) = executar_consulta(
        conn, "SELECT count(*) FROM pedidos WHERE bairro LIKE '%a%'"
    ).linhas
    assert 0 < linha[0] <= 8000


def test_like_com_padrao_longo_demais_falha_limpo(conn: sqlite3.Connection) -> None:
    padrao = "%" + "a" * 100  # 101 caracteres
    with pytest.raises(ErroDeConsulta, match="pattern too complex"):
        executar_consulta(conn, f"SELECT count(*) FROM pedidos WHERE bairro LIKE '{padrao}'")
    assert executar_consulta(
        conn, f"SELECT count(*) FROM pedidos WHERE bairro LIKE '{padrao[:100]}'"
    ).linhas == ((0,),)


def test_aceita_funcoes_de_janela_e_time(conn: sqlite3.Connection) -> None:
    sql = """
        SELECT bairro,
               row_number() OVER (ORDER BY count(*) DESC) AS posicao,
               rank() OVER w, dense_rank() OVER w,
               lag(count(*)) OVER w, lead(count(*)) OVER w,
               min(time(data_hora))
        FROM pedidos
        GROUP BY bairro
        WINDOW w AS (ORDER BY count(*) DESC)
        ORDER BY posicao
    """
    resultado = executar_consulta(conn, sql)
    assert [linha[1] for linha in resultado.linhas] == list(range(1, 9))
    assert resultado.linhas[0][4] is None  # lag da primeira linha


@pytest.mark.parametrize(
    "sql",
    [
        # Nome de CTE que coincide com tabela interna ou com tabela real fora da lista.
        (
            "WITH RECURSIVE sqlite_master(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM sqlite_master "
            "WHERE n < 3) SELECT count(*) FROM sqlite_master"
        ),
        (
            "SELECT (WITH x AS MATERIALIZED (SELECT 1) SELECT count(*) FROM x), "
            "(SELECT count(*) FROM sqlite_master)"
        ),
        (
            "SELECT (WITH segredo AS MATERIALIZED (SELECT 1) SELECT count(*) FROM segredo), "
            "(SELECT count(*) FROM segredo)"
        ),
        "SELECT count(*) FROM segredo",
        "SELECT count(*) FROM main.segredo",
        "SELECT x FROM segredo",
        # CTE declarada só dentro de string não conta.
        "SELECT 'json_each AS (' FROM pedidos, json_each('[1]') LIMIT 1",
        "SELECT count(*) FROM json_each('[1, 2]')",
        # `WINDOW nome AS (` não declara CTE.
        "SELECT count(*) FROM json_each('[1,2,3]') WINDOW json_each AS ()",
        "SELECT count(*) FROM json_tree('[1,[2]]') WINDOW json_tree AS ()",
        "SELECT count(*) FROM json_each('[1]') WINDOW w AS (), json_each AS ()",
        "SELECT count(*) FROM segredo WINDOW segredo AS ()",
        # CTE com nome de módulo (função de tabela) em outro escopo.
        (
            "SELECT (WITH json_each AS (SELECT 1) SELECT 1), "
            "(SELECT count(*) FROM json_each('[1,2,3]'))"
        ),
        (
            "SELECT (WITH json_tree AS MATERIALIZED (SELECT 1) SELECT count(*) FROM json_tree), "
            "(SELECT count(*) FROM json_tree('[1,[2]]'))"
        ),
    ],
)
def test_leitura_sem_coluna_falha_fechada_para_o_que_nao_e_cte(
    banco_com_tabela_extra: Path, sql: str
) -> None:
    conexao = abrir_somente_leitura(banco_com_tabela_extra)
    try:
        assert _motivo(conexao, sql) == "tabela"
    finally:
        conexao.close()


@pytest.fixture
def banco_com_tabela_extra(banco_path: Path, tmp_path: Path) -> Path:
    copia = tmp_path / "com_segredo.db"
    shutil.copyfile(banco_path, copia)
    with sqlite3.connect(copia) as escrita:
        escrita.execute("CREATE TABLE segredo (x TEXT)")
        escrita.execute("INSERT INTO segredo VALUES ('não pode sair')")
    escrita.close()
    return copia


def test_authorizer_vale_mesmo_com_instrucao_ja_preparada_sem_ele(
    conn: sqlite3.Connection,
) -> None:
    sql = "SELECT name FROM sqlite_master"
    conn.execute(sql).fetchall()  # fica no cache de instruções da conexão
    assert _motivo(conn, sql) == "tabela"


def test_authorizer_e_removido_depois_da_consulta(conn: sqlite3.Connection) -> None:
    with pytest.raises(SqlRejeitado):
        executar_consulta(conn, "SELECT * FROM sqlite_master")
    # Leitura direta (fora do validador) continua possível, por exemplo para descrever o esquema.
    assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] >= 3


# --- erros do próprio SQLite --------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "trecho"),
    [
        ("SELECT coluna_que_nao_existe FROM pedidos", "no such column"),
        ("SELECT * FROM pedidos WHERE AND", "syntax error"),
        ("SELECT * FROM pedidos WHERE", "incomplete input"),
        ("SELECT * FROM tabela_inexistente", "no such table"),
        ("SELECT :parametro", "binding"),
    ],
)
def test_erro_do_sqlite_vira_erro_de_consulta(
    conn: sqlite3.Connection, sql: str, trecho: str
) -> None:
    with pytest.raises(ErroDeConsulta, match=trecho):
        executar_consulta(conn, sql)
    assert executar_consulta(conn, "SELECT 1").linhas == ((1,),)


# --- teto de linhas e tempo ---------------------------------------------------


def test_trunca_no_limite(conn: sqlite3.Connection) -> None:
    resultado = executar_consulta(conn, "SELECT id FROM pedidos ORDER BY id", limite_linhas=5)
    assert resultado.linhas == ((1,), (2,), (3,), (4,), (5,))
    assert resultado.truncado is True


def test_nao_trunca_quando_cabe_exatamente(conn: sqlite3.Connection) -> None:
    resultado = executar_consulta(conn, "SELECT id FROM pedidos WHERE id <= 5", limite_linhas=5)
    assert len(resultado.linhas) == 5
    assert resultado.truncado is False


def test_limite_padrao_e_50(conn: sqlite3.Connection) -> None:
    resultado = executar_consulta(conn, "SELECT id FROM pedidos")
    assert len(resultado.linhas) == 50
    assert resultado.truncado is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM pedidos a, pedidos b, pedidos c",
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT max(i) FROM n",
    ],
)
def test_tempo_maximo(conn: sqlite3.Connection, sql: str) -> None:
    inicio = time.monotonic()
    assert _motivo(conn, sql, timeout_s=0.2) == "tempo"
    assert time.monotonic() - inicio < 2
    # A conexão continua utilizável e o prazo é renovado a cada chamada.
    assert executar_consulta(conn, "SELECT count(*) FROM pedidos", timeout_s=0.2).linhas == (
        (8000,),
    )


def _texto(niveis: int, letra: str = "a") -> str:
    """Expressão SQL que vale `letra` repetida 3**niveis vezes (replace aninhado)."""
    expressao = f"'{letra}'"
    for _ in range(niveis):
        expressao = f"replace({expressao}, '{letra}', '{letra * 3}')"
    return expressao


@pytest.mark.parametrize(
    ("sql", "esperado"),
    [
        # trim(X, Y) compara cada caractere de X com cada um de Y dentro de um só passo da VM:
        # o teto de tamanho barra antes de começar.
        (f"SELECT length(trim({_texto(11)}, {_texto(10, 'b')} || 'a'))", "too big"),
        (f"SELECT instr({_texto(12)}, {_texto(11)} || 'b')", "too big"),
        (
            (
                f"SELECT max(length(trim({_texto(10)} || id, {_texto(9, 'b')} || 'a'))) "
                "FROM (SELECT id FROM pedidos LIMIT 20)"
            ),
            "too big",
        ),
        # Logo abaixo dos tetos, repetido por linha: o tempo corta entre uma linha e outra.
        (
            f"SELECT max(length(trim({_texto(8)} || id, {_texto(6, 'b')} || 'a'))) FROM pedidos",
            "tempo",
        ),
        (f"SELECT max(instr({_texto(8)} || id, {_texto(7)} || 'b')) FROM pedidos", "tempo"),
    ],
)
def test_funcao_quadratica_nao_passa_do_tempo(
    conn: sqlite3.Connection, sql: str, esperado: str
) -> None:
    timeout_s = 0.3
    inicio = time.monotonic()
    with pytest.raises((SqlRejeitado, ErroDeConsulta)) as erro:
        executar_consulta(conn, sql, timeout_s=timeout_s)
    assert time.monotonic() - inicio < 2 * timeout_s
    if esperado == "tempo":
        assert isinstance(erro.value, SqlRejeitado)
        assert erro.value.motivo == "tempo"
    else:
        assert isinstance(erro.value, ErroDeConsulta)
        assert esperado in str(erro.value)


def test_caractere_invalido_vira_erro_de_consulta(conn: sqlite3.Connection) -> None:
    with pytest.raises(ErroDeConsulta):
        executar_consulta(conn, "SELECT '\ud800'")
    with pytest.raises(ErroDeConsulta):
        executar_consulta(conn, "SELECT 1\x00")


def test_texto_gigante_numa_so_funcao_e_cortado(conn: sqlite3.Connection) -> None:
    # replace aninhado multiplica o texto por 4 a cada nível sem dar passos à VM: 4**14 = 256 MB.
    expressao = "'a'"
    for _ in range(14):
        expressao = f"replace({expressao}, 'a', 'aaaa')"
    with pytest.raises(ErroDeConsulta, match="too big"):
        executar_consulta(conn, f"SELECT length({expressao})")
    assert executar_consulta(conn, "SELECT length(group_concat(nome)) FROM itens_cardapio").linhas


def test_estado_e_por_chamada_com_conexoes_em_threads(banco_path: Path) -> None:
    resultados: dict[str, object] = {}

    def rodar(nome: str, sql: str, timeout_s: float) -> None:
        conexao = abrir_somente_leitura(banco_path)  # cada thread com a sua conexão
        try:
            resultados[nome] = executar_consulta(conexao, sql, timeout_s=timeout_s).linhas
        except SqlRejeitado as erro:
            resultados[nome] = erro.motivo
        finally:
            conexao.close()

    threads = [
        threading.Thread(
            target=rodar,
            args=("lenta", "SELECT count(*) FROM pedidos a, pedidos b, pedidos c", 0.3),
        ),
        threading.Thread(target=rodar, args=("negada", "SELECT * FROM sqlite_master", 2.0)),
        threading.Thread(target=rodar, args=("boa", "SELECT count(*) FROM pedidos", 2.0)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert resultados == {"lenta": "tempo", "negada": "tabela", "boa": ((8000,),)}


# --- camada 1 isolada e caminho do banco --------------------------------------


def test_escrita_falha_no_motor_mesmo_sem_o_validador(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM pedidos")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TEMP TABLE t (x)")
    assert conn.execute("PRAGMA query_only").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM pedidos").fetchone() == (8000,)


def test_banco_em_caminho_com_espaco_e_acento(banco_path: Path, tmp_path: Path) -> None:
    pasta = tmp_path / "pasta com espaço e ã"
    pasta.mkdir()
    copia = pasta / "pedidos.db"
    shutil.copyfile(banco_path, copia)
    conexao = abrir_somente_leitura(copia)
    try:
        assert executar_consulta(conexao, "SELECT count(*) FROM pedidos").linhas == ((8000,),)
        with pytest.raises(sqlite3.OperationalError):
            conexao.execute("DELETE FROM pedidos")
    finally:
        conexao.close()
    assert sorted(p.name for p in pasta.iterdir()) == ["pedidos.db"]


@pytest.mark.parametrize(
    ("sql", "esperado"),
    [
        ("SELECT 1", False),
        ("SELECT 1; SELECT 2", False),
        ("WITH t AS (SELECT 1) SELECT * FROM t;", False),
        ("DROP TABLE pedidos; SELECT 1", True),
        ("SELECT 1; DELETE FROM pedidos", True),
        ("SELECT 1; /* ok */ -- fim\n update pedidos SET valor = 0", True),
        ("/* DELETE */ SELECT 1", False),
        ("SELECT 'a; DROP TABLE pedidos' ; SELECT 2", False),
        ("SELECT 1 -- ; DELETE FROM pedidos", False),
        ("INSERT INTO pedidos VALUES (1)", True),
        ("", False),
    ],
)
def test_tenta_escrita_olha_cada_instrucao(sql: str, esperado: bool) -> None:
    assert tenta_escrita(sql) is esperado
