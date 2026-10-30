"""Ferramentas do agente: esquema, execução de SQL e despacho. O modelo nunca é chamado."""

import json
import sqlite3
from pathlib import Path

import pytest

from analista.dados import BAIRROS, CANAIS, DEFINICOES, FIM, INICIO
from analista.ferramentas import FERRAMENTAS, descrever_esquema, despachar
from analista.sql_seguro import abrir_somente_leitura


@pytest.fixture
def conn(banco_path: Path):
    conexao = abrir_somente_leitura(banco_path)
    yield conexao
    conexao.close()


def _chamar(conn, banco_path: Path, nome: str, argumentos: str, limite_linhas: int = 50):
    texto, auditoria = despachar(
        conn, banco_path, nome, argumentos, limite_linhas=limite_linhas, timeout_s=2.0
    )
    return json.loads(texto), auditoria


def _sql(consulta: str) -> str:
    return json.dumps({"consulta": consulta})


def test_esquema_lista_tabelas_colunas_e_valores(banco_path: Path) -> None:
    texto = descrever_esquema(banco_path)
    for tabela in ("itens_cardapio", "pedidos", "itens_pedido"):
        assert tabela in texto
    for coluna in ("data_hora", "taxa_entrega", "tempo_entrega_min", "preco_unit"):
        assert coluna in texto
    assert "INTEGER" in texto and "TEXT" in texto
    for valor in (*BAIRROS, *CANAIS, "entregue", "atrasado", "cancelado"):
        assert valor in texto
    assert INICIO.isoformat() in texto and FIM.isoformat() in texto
    assert "até 45 min" in texto and "acima de 45 min" in texto
    assert DEFINICOES.strip() in texto


def test_esquema_lista_categorias_mas_nunca_nomes_de_itens(banco_path: Path) -> None:
    texto = descrever_esquema(banco_path)
    with sqlite3.connect(banco_path) as bruta:
        categorias = [c for (c,) in bruta.execute("SELECT DISTINCT categoria FROM itens_cardapio")]
        nomes = [n for (n,) in bruta.execute("SELECT nome FROM itens_cardapio")]
    assert categorias
    assert all(categoria in texto for categoria in categorias)
    assert "IGNORE" not in texto
    assert not any(nome in texto for nome in nomes)


def test_executar_sql_devolve_colunas_linhas_e_truncado(conn, banco_path: Path) -> None:
    consulta = "SELECT canal, COUNT(*) AS n FROM pedidos GROUP BY 1"
    resposta, auditoria = _chamar(conn, banco_path, "executar_sql", _sql(consulta))
    assert resposta["colunas"] == ["canal", "n"]
    assert len(resposta["linhas"]) == len(CANAIS)
    assert all(isinstance(linha, list) for linha in resposta["linhas"])
    assert resposta["truncado"] is False
    assert auditoria == {
        "sql": consulta,
        "ok": True,
        "motivo": None,
        "n_linhas": len(CANAIS),
        "colunas": ["canal", "n"],
        "linhas": resposta["linhas"],
        "truncado": False,
    }


def test_resultado_acima_do_limite_vem_truncado(conn, banco_path: Path) -> None:
    resposta, auditoria = _chamar(
        conn, banco_path, "executar_sql", _sql("SELECT id FROM pedidos"), limite_linhas=5
    )
    assert len(resposta["linhas"]) == 5
    assert resposta["truncado"] is True
    assert auditoria["n_linhas"] == 5 and auditoria["truncado"] is True


def test_sql_rejeitado_vira_erro_para_o_modelo(conn, banco_path: Path) -> None:
    resposta, auditoria = _chamar(conn, banco_path, "executar_sql", _sql("DELETE FROM pedidos"))
    assert resposta["motivo"] == "nao_select"
    assert resposta["erro"]
    assert auditoria["ok"] is False and auditoria["motivo"] == "nao_select"
    assert auditoria["colunas"] == [] and auditoria["linhas"] == []
    assert auditoria["n_linhas"] == 0 and auditoria["truncado"] is False


def test_tabela_fora_da_lista_vira_erro_para_o_modelo(conn, banco_path: Path) -> None:
    resposta, _ = _chamar(conn, banco_path, "executar_sql", _sql("SELECT * FROM sqlite_master"))
    assert resposta["motivo"] == "tabela"


def test_erro_de_sintaxe_vira_erro_para_o_modelo(conn, banco_path: Path) -> None:
    resposta, auditoria = _chamar(conn, banco_path, "executar_sql", _sql("SELECT FROM pedidos"))
    assert resposta["motivo"] == "erro_de_consulta"
    assert "syntax" in resposta["erro"]
    assert auditoria["ok"] is False and auditoria["motivo"] == "erro_de_consulta"


def test_json_invalido_nos_argumentos(conn, banco_path: Path) -> None:
    resposta, auditoria = _chamar(conn, banco_path, "executar_sql", "{")
    assert resposta["motivo"] == "json_invalido"
    assert resposta["erro"]
    assert auditoria["ok"] is False and auditoria["motivo"] == "json_invalido"
    assert auditoria["sql"] is None


def test_argumento_ausente(conn, banco_path: Path) -> None:
    resposta, auditoria = _chamar(conn, banco_path, "executar_sql", "{}")
    assert resposta["motivo"] == "argumento_ausente"
    assert "consulta" in resposta["erro"]
    assert auditoria["motivo"] == "argumento_ausente"


@pytest.mark.parametrize(
    "argumentos",
    ['{"consulta": 5}', '{"consulta": "SELECT 1", "outro": 1}', "[]", '"SELECT 1"'],
)
def test_argumento_extra_ou_que_nao_e_texto(conn, banco_path: Path, argumentos: str) -> None:
    resposta, auditoria = _chamar(conn, banco_path, "executar_sql", argumentos)
    assert resposta["motivo"] == "argumento_invalido"
    assert auditoria["ok"] is False


def test_ferramenta_inexistente(conn, banco_path: Path) -> None:
    resposta, auditoria = _chamar(conn, banco_path, "apagar_tudo", "{}")
    assert resposta["motivo"] == "ferramenta_inexistente"
    assert "apagar_tudo" in resposta["erro"]
    assert auditoria["ok"] is False and auditoria["motivo"] == "ferramenta_inexistente"


@pytest.mark.parametrize("argumentos", ["{}", "", "  "])
def test_descrever_esquema_pelo_despacho(conn, banco_path: Path, argumentos: str) -> None:
    texto, auditoria = despachar(
        conn, banco_path, "descrever_esquema", argumentos, limite_linhas=50, timeout_s=2.0
    )
    assert texto == descrever_esquema(banco_path)
    assert auditoria == {
        "sql": None,
        "ok": True,
        "motivo": None,
        "n_linhas": 0,
        "colunas": [],
        "linhas": [],
        "truncado": False,
    }


def test_descrever_esquema_recusa_argumento_extra(conn, banco_path: Path) -> None:
    resposta, auditoria = _chamar(conn, banco_path, "descrever_esquema", '{"x": 1}')
    assert resposta["motivo"] == "argumento_invalido"
    assert auditoria["ok"] is False


def test_valores_nulos_serializam_como_null(conn, banco_path: Path) -> None:
    texto, _ = despachar(
        conn,
        banco_path,
        "executar_sql",
        _sql("SELECT nota, tempo_entrega_min FROM pedidos WHERE status = 'cancelado' LIMIT 1"),
        limite_linhas=50,
        timeout_s=2.0,
    )
    assert '"linhas": [[null, null]]' in texto


def test_texto_com_acento_nao_vira_escape(conn, banco_path: Path) -> None:
    texto, _ = despachar(
        conn,
        banco_path,
        "executar_sql",
        _sql("SELECT DISTINCT bairro FROM pedidos WHERE bairro = 'Renascença'"),
        limite_linhas=50,
        timeout_s=2.0,
    )
    assert "Renascença" in texto


def test_ferramentas_no_formato_da_api_de_chat() -> None:
    por_nome = {f["function"]["name"]: f for f in FERRAMENTAS}
    assert set(por_nome) == {"descrever_esquema", "executar_sql"}
    assert all(f["type"] == "function" for f in FERRAMENTAS)
    assert por_nome["descrever_esquema"]["function"]["parameters"] == {
        "type": "object",
        "properties": {},
    }
    parametros = por_nome["executar_sql"]["function"]["parameters"]
    assert parametros["properties"]["consulta"]["type"] == "string"
    assert parametros["required"] == ["consulta"]
    descricao = por_nome["executar_sql"]["function"]["description"]
    for trecho in ("SELECT", "WITH", "50", "truncado", "itens_pedido", "LIKE", "glob", "printf"):
        assert trecho in descricao
    assert "descrever_esquema" in descricao


@pytest.mark.parametrize("consulta", ["SELECT x'00ff'", "SELECT CAST('a' AS BLOB)"])
def test_blob_vira_texto_no_modelo_e_na_auditoria(conn, banco_path: Path, consulta: str) -> None:
    n_bytes = 2 if "00ff" in consulta else 1
    resposta, auditoria = _chamar(conn, banco_path, "executar_sql", _sql(consulta))
    esperado = [[f"<blob {n_bytes} bytes>"]]
    assert resposta["linhas"] == esperado
    assert auditoria["ok"] is True and auditoria["linhas"] == esperado


@pytest.mark.parametrize("valor", ["1e999", "-1e999"])
def test_infinito_vira_null_no_modelo_e_na_auditoria(conn, banco_path: Path, valor: str) -> None:
    texto, auditoria = despachar(
        conn, banco_path, "executar_sql", _sql(f"SELECT {valor}"), limite_linhas=50, timeout_s=2.0
    )
    assert "Infinity" not in texto and "NaN" not in texto
    assert json.loads(texto)["linhas"] == [[None]]
    assert auditoria["linhas"] == [[None]]
