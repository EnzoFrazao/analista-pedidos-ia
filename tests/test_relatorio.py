"""Relatório semanal: KPIs por SQL, checagem de números e fallback. Modelo sempre falso."""

import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from analista import relatorio
from analista.llm import ErroDoProvedor, MensagemModelo
from analista.relatorio import (
    calcular_kpis,
    gerar_relatorio,
    numeros_nao_suportados,
    resumo_deterministico,
    variacoes,
)
from analista.sql_seguro import abrir_somente_leitura
from tests.fakes import ModeloFalso

SEMANA = date(2026, 9, 21)
SEMANA_FIM = date(2026, 9, 27)
PERIODO = (SEMANA, SEMANA_FIM)


@pytest.fixture
def conn(banco_path: Path):
    conexao = abrir_somente_leitura(banco_path)
    yield conexao
    conexao.close()


def _texto(conteudo: str | None) -> MensagemModelo:
    return MensagemModelo(conteudo=conteudo, chamadas=())


def _kpis_exemplo() -> dict:
    return {
        "pedidos": 1234,
        "faturamento": 12345.67,
        "ticket_medio": 10.0,
        "tempo_medio_entrega_min": 32.4,
        "cancelamento_pct": 18.4,
        "itens_mais_vendidos": [{"nome": "Feijoada da Casa", "quantidade": 321}],
        "bairros_mais_lentos": [{"bairro": "Turu", "tempo_medio_min": 41.2, "pedidos": 88}],
    }


def _variacoes_exemplo() -> dict:
    return {
        "pedidos": 5.2,
        "faturamento": -7.3,
        "ticket_medio": None,
        "tempo_medio_entrega_min": 12.0,
        "cancelamento_pct": 0.0,
    }


# --- KPIs -------------------------------------------------------------------------------------


def test_kpis_batem_com_consulta_direta(conn: sqlite3.Connection, banco_path: Path):
    kpis = calcular_kpis(conn, SEMANA, SEMANA_FIM)

    # Referência escrita à parte, direto no sqlite3, seguindo DEFINICOES.
    ref = sqlite3.connect(banco_path)
    filtro = "date(data_hora) BETWEEN '2026-09-21' AND '2026-09-27'"
    validos, faturamento, tempo = ref.execute(
        f"SELECT COUNT(*), SUM(valor), AVG(tempo_entrega_min) FROM pedidos "
        f"WHERE status <> 'cancelado' AND {filtro}"
    ).fetchone()
    total, cancelados = ref.execute(
        f"SELECT COUNT(*), SUM(status = 'cancelado') FROM pedidos WHERE {filtro}"
    ).fetchone()
    itens = ref.execute(
        f"""SELECT c.nome, SUM(i.quantidade) AS q FROM itens_pedido i
            JOIN pedidos p ON p.id = i.pedido_id JOIN itens_cardapio c ON c.id = i.item_id
            WHERE p.status <> 'cancelado' AND {filtro.replace("data_hora", "p.data_hora")}
            GROUP BY c.id ORDER BY q DESC, c.nome LIMIT 5"""
    ).fetchall()
    bairros = ref.execute(
        f"""SELECT bairro, AVG(tempo_entrega_min) AS t, COUNT(*) FROM pedidos
            WHERE status <> 'cancelado' AND {filtro}
            GROUP BY bairro HAVING COUNT(*) >= 30 ORDER BY t DESC, bairro LIMIT 3"""
    ).fetchall()
    ref.close()

    assert kpis["pedidos"] == validos > 0
    assert kpis["faturamento"] == pytest.approx(round(faturamento, 2))
    assert kpis["ticket_medio"] == pytest.approx(round(faturamento / validos, 2))
    assert kpis["tempo_medio_entrega_min"] == pytest.approx(round(tempo, 1))
    assert kpis["cancelamento_pct"] == pytest.approx(round(cancelados / total * 100, 1))
    assert [(i["nome"], i["quantidade"]) for i in kpis["itens_mais_vendidos"]] == itens
    assert len(kpis["itens_mais_vendidos"]) == 5
    assert [b["bairro"] for b in kpis["bairros_mais_lentos"]] == [b[0] for b in bairros]
    assert [b["tempo_medio_min"] for b in kpis["bairros_mais_lentos"]] == pytest.approx(
        [round(b[1], 1) for b in bairros]
    )
    assert [b["pedidos"] for b in kpis["bairros_mais_lentos"]] == [b[2] for b in bairros]


def test_semana_sem_pedidos_devolve_kpis_vazios(conn: sqlite3.Connection):
    kpis = calcular_kpis(conn, date(2026, 6, 22), date(2026, 6, 28))
    assert kpis["pedidos"] == 0
    assert kpis["faturamento"] == 0
    assert kpis["ticket_medio"] is None
    assert kpis["tempo_medio_entrega_min"] is None
    assert kpis["cancelamento_pct"] is None
    assert kpis["itens_mais_vendidos"] == []
    assert kpis["bairros_mais_lentos"] == []


def test_variacao_com_semana_anterior_vazia_e_none(conn: sqlite3.Connection):
    atual = calcular_kpis(conn, date(2026, 6, 29), date(2026, 7, 5))
    anterior = calcular_kpis(conn, date(2026, 6, 22), date(2026, 6, 28))
    resultado = variacoes(atual, anterior)
    assert set(resultado) == {
        "pedidos",
        "faturamento",
        "ticket_medio",
        "tempo_medio_entrega_min",
        "cancelamento_pct",
    }
    assert all(valor is None for valor in resultado.values())


def test_variacao_percentual_arredondada_a_uma_casa():
    atual = {
        "pedidos": 110,
        "faturamento": 900.0,
        "ticket_medio": None,
        "tempo_medio_entrega_min": 30.0,
        "cancelamento_pct": 5.0,
    }
    anterior = {
        "pedidos": 100,
        "faturamento": 1000.0,
        "ticket_medio": 9.0,
        "tempo_medio_entrega_min": 24.0,
        "cancelamento_pct": 0.0,
    }
    resultado = variacoes(atual, anterior)
    assert resultado["pedidos"] == 10.0
    assert resultado["faturamento"] == -10.0
    assert resultado["ticket_medio"] is None  # atual nulo
    assert resultado["tempo_medio_entrega_min"] == 25.0
    assert resultado["cancelamento_pct"] == 5.0  # pontos percentuais, não relativo


def test_variacao_do_cancelamento_e_em_pontos_percentuais():
    atual = {**_kpis_exemplo(), "cancelamento_pct": 5.0}
    anterior = {**_kpis_exemplo(), "cancelamento_pct": 5.8}
    assert variacoes(atual, anterior)["cancelamento_pct"] == -0.8
    # None só quando um dos lados é nulo
    assert variacoes({**atual, "cancelamento_pct": None}, anterior)["cancelamento_pct"] is None
    assert variacoes(atual, {**anterior, "cancelamento_pct": None})["cancelamento_pct"] is None


def test_variacao_nunca_e_zero_negativo():
    atual = {**_kpis_exemplo(), "faturamento": 99.96, "cancelamento_pct": 4.96}
    anterior = {**_kpis_exemplo(), "faturamento": 100.0, "cancelamento_pct": 5.0}
    resultado = variacoes(atual, anterior)
    for chave in ("faturamento", "cancelamento_pct"):
        assert resultado[chave] == 0.0
        assert math.copysign(1, resultado[chave]) == 1


def test_tabela_mostra_cancelamento_em_pontos_percentuais():
    kpis = {**_kpis_exemplo(), "cancelamento_pct": 5.0}
    var = {**_variacoes_exemplo(), "cancelamento_pct": 0.8}
    linha_mais = [x for x in relatorio._tabela(kpis, var) if x.startswith("| Cancelamento")]
    assert linha_mais == ["| Cancelamento | 5,0% | +0,8 p.p. |"]
    var["cancelamento_pct"] = -0.8
    linha_menos = [x for x in relatorio._tabela(kpis, var) if x.startswith("| Cancelamento")]
    assert linha_menos == ["| Cancelamento | 5,0% | -0,8 p.p. |"]
    # os demais continuam em % relativo
    assert "| Faturamento | R$ 12.345,67 | -7,3% |" in relatorio._tabela(kpis, var)


def test_prompt_do_modelo_rotula_cancelamento_como_pontos_percentuais():
    kpis = {**_kpis_exemplo(), "cancelamento_pct": 5.0}
    var = {**_variacoes_exemplo(), "cancelamento_pct": -0.8}
    dados = relatorio._dados_para_o_modelo(SEMANA, SEMANA_FIM, kpis, var)
    assert "Cancelamento: 5,0% (variação -0,8 p.p., em pontos percentuais)" in dados


def test_checagem_aceita_a_variacao_do_cancelamento_em_pontos_percentuais():
    kpis = {**_kpis_exemplo(), "cancelamento_pct": 5.0}
    var = {**_variacoes_exemplo(), "cancelamento_pct": -0.8}
    texto = "O cancelamento caiu 0,8 p.p., para 5,0%."
    assert numeros_nao_suportados(texto, kpis, var, PERIODO) == set()


@pytest.mark.parametrize("inicio", [date(2026, 9, 22), date(2026, 9, 27)])
def test_semana_que_nao_comeca_na_segunda_levanta_value_error(conn, inicio: date):
    with pytest.raises(ValueError, match="segunda"):
        gerar_relatorio(conn, None, inicio)


@pytest.mark.parametrize("inicio", [date(2026, 6, 22), date(2026, 9, 28)])
def test_semana_fora_do_periodo_dos_dados_levanta_value_error(conn, inicio: date):
    with pytest.raises(ValueError, match="período"):
        gerar_relatorio(conn, None, inicio)


def test_semana_comeca_no_primeiro_segundo_da_segunda_e_acaba_no_ultimo_do_domingo(tmp_path):
    caminho = tmp_path / "limite.db"
    ligacao = sqlite3.connect(caminho)
    ligacao.executescript(
        """
        CREATE TABLE itens_cardapio (id INTEGER PRIMARY KEY, nome TEXT, categoria TEXT, preco REAL);
        CREATE TABLE pedidos (id INTEGER PRIMARY KEY, data_hora TEXT, bairro TEXT, canal TEXT,
            status TEXT, valor REAL, taxa_entrega REAL, tempo_entrega_min INTEGER, nota INTEGER);
        CREATE TABLE itens_pedido (pedido_id INTEGER, item_id INTEGER, quantidade INTEGER,
            preco_unit REAL);
        INSERT INTO pedidos (data_hora, bairro, canal, status, valor, taxa_entrega,
            tempo_entrega_min) VALUES
          ('2026-09-20 23:59:59', 'Centro', 'balcao', 'entregue', 1, 0, 10),
          ('2026-09-21 00:00:00', 'Centro', 'balcao', 'entregue', 10, 0, 10),
          ('2026-09-27 23:59:59', 'Centro', 'balcao', 'entregue', 100, 0, 10),
          ('2026-09-28 00:00:00', 'Centro', 'balcao', 'entregue', 1000, 0, 10);
        """
    )
    ligacao.commit()
    ligacao.close()
    conexao = abrir_somente_leitura(caminho)
    try:
        kpis = calcular_kpis(conexao, SEMANA, SEMANA_FIM)
    finally:
        conexao.close()
    assert kpis["pedidos"] == 2
    assert kpis["faturamento"] == 110


# --- checagem de números ----------------------------------------------------------------------


def test_checagem_aceita_numeros_dos_kpis_em_formato_pt_br():
    texto = (
        "O faturamento foi de R$ 12.345,67, o cancelamento ficou em 18,4% e "
        "houve 1.234 pedidos, com tempo médio de 32,4 min."
    )
    assert numeros_nao_suportados(texto, _kpis_exemplo(), _variacoes_exemplo(), PERIODO) == set()


def test_checagem_rejeita_numero_inventado():
    texto = "Foram 999 pedidos e o cancelamento chegou a 37,5%."
    resultado = numeros_nao_suportados(texto, _kpis_exemplo(), _variacoes_exemplo(), PERIODO)
    assert resultado == {"999", "37,5"}


def test_checagem_aceita_variacao_com_sinal_trocado_por_palavra():
    # "queda de 7,3%" cita o valor absoluto de -7,3.
    texto = "O faturamento teve queda de 7,3% e os pedidos subiram 5,2%."
    assert numeros_nao_suportados(texto, _kpis_exemplo(), _variacoes_exemplo(), PERIODO) == set()


def test_checagem_tolera_arredondamento_a_uma_casa():
    # 32,4 está nos KPIs; 32,43 arredonda para 32,4. 32,5 não.
    kpis, var = _kpis_exemplo(), _variacoes_exemplo()
    assert numeros_nao_suportados("Tempo de 32,43 min.", kpis, var, PERIODO) == set()
    assert numeros_nao_suportados("Tempo de 32,5 min.", kpis, var, PERIODO) == {"32,5"}


@pytest.mark.parametrize(
    "texto",
    [
        "Semana de 21 a 27 de setembro de 2026.",
        "Semana de 21/09/2026 a 27/09/2026.",
        "Semana de 21/09 a 27/09/2026.",
        "Semana de 2026-09-21 a 2026-09-27.",
        "Em setembro de 2026, de 21 a 27 de setembro.",
    ],
)
def test_checagem_aceita_dias_mes_e_ano_dentro_de_datas_do_periodo(texto: str):
    assert numeros_nao_suportados(texto, _kpis_exemplo(), _variacoes_exemplo(), PERIODO) == set()


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Foram 27 pedidos.", {"27"}),
        ("Foram 25 pedidos atrasados.", {"25"}),
        ("Foram 5 pedidos.", {"5"}),
        ("Foram 3 pedidos cancelados.", {"3"}),
        ("Foram 9 pedidos.", {"9"}),
        ("Em 2026 foram 2026 pedidos.", {"2026"}),
        ("Semana de 28/09/2026 a 04/10/2026.", {"28", "04", "10"}),
        ("Semana de 21 a 30 de setembro de 2026.", {"30"}),
    ],
)
def test_checagem_nao_libera_inteiro_solto_so_porque_e_dia_mes_ou_contagem(texto, esperado):
    resultado = numeros_nao_suportados(texto, _kpis_exemplo(), _variacoes_exemplo(), PERIODO)
    assert esperado <= resultado


def test_checagem_aceita_as_contagens_so_antes_de_itens_e_bairros():
    kpis, var = _kpis_exemplo(), _variacoes_exemplo()
    texto = "Os 5 itens mais vendidos e os 3 bairros mais lentos."
    assert numeros_nao_suportados(texto, kpis, var, PERIODO) == set()
    assert numeros_nao_suportados("Os 4 itens mais vendidos.", kpis, var, PERIODO) == {"4"}
    assert numeros_nao_suportados("Os 3 itens mais vendidos.", kpis, var, PERIODO) == {"3"}
    assert numeros_nao_suportados("Os 5 bairros mais lentos.", kpis, var, PERIODO) == {"5"}


def test_checagem_entende_decimal_com_ponto_como_um_numero_so():
    kpis, var = _kpis_exemplo(), _variacoes_exemplo()
    assert numeros_nao_suportados("Tempo de 32.4 min.", kpis, var, PERIODO) == set()
    # "9.5" é um número só (9,5), não os inteiros 9 e 5
    assert numeros_nao_suportados("Tempo de 9.5 min.", kpis, var, PERIODO) == {"9.5"}
    # grupo de milhar continua sendo milhar
    assert numeros_nao_suportados("Foram 1.234 pedidos.", kpis, var, PERIODO) == set()


def test_checagem_ignora_nomes_e_valores_nulos():
    kpis = _kpis_exemplo()
    kpis["ticket_medio"] = None
    assert numeros_nao_suportados("Sem números aqui.", kpis, _variacoes_exemplo(), PERIODO) == set()


# --- resumo e relatório -----------------------------------------------------------------------


def test_resumo_deterministico_passa_na_propria_checagem(conn: sqlite3.Connection):
    atual = calcular_kpis(conn, SEMANA, SEMANA_FIM)
    anterior = calcular_kpis(conn, SEMANA - timedelta(days=7), SEMANA_FIM - timedelta(days=7))
    var = variacoes(atual, anterior)
    resumo = resumo_deterministico(atual, var)
    assert resumo.strip()
    assert numeros_nao_suportados(resumo, atual, var, PERIODO) == set()


def test_resumo_deterministico_sem_variacao_nao_quebra():
    var = dict.fromkeys(_variacoes_exemplo())
    assert "R$ 12.345,67" in resumo_deterministico(_kpis_exemplo(), var)


def test_relatorio_sem_modelo_funciona(conn: sqlite3.Connection):
    texto = gerar_relatorio(conn, None, SEMANA)
    assert texto.startswith("# ")
    assert "21/09/2026" in texto and "27/09/2026" in texto
    assert "Faturamento" in texto
    assert "montado pelo código" in texto


def test_o_relatorio_mostra_os_bairros_mais_lentos_da_semana_de_21_a_27_de_setembro(conn):
    texto = gerar_relatorio(conn, None, SEMANA)
    secao = texto.split("## Bairros mais lentos", 1)[1]
    assert "Cohama" in secao or "Turu" in secao


def test_relatorio_usa_o_paragrafo_do_modelo_quando_os_numeros_conferem(conn):
    atual = calcular_kpis(conn, SEMANA, SEMANA_FIM)
    frase = f"A semana teve {atual['pedidos']} pedidos válidos."
    modelo = ModeloFalso([_texto(frase)])
    texto = gerar_relatorio(conn, modelo, SEMANA)
    assert frase in texto
    assert "escrito pelo modelo" in texto
    assert "algarismos" in texto  # o rótulo não promete checar números por extenso
    assert modelo.ferramentas == [[]]  # sem ferramentas
    prompt = "\n".join(str(m["content"]) for m in modelo.mensagens[0])
    assert "Faturamento" in prompt and "21/09/2026" in prompt


def test_modelo_que_inventa_numero_cai_no_resumo_deterministico(conn):
    modelo = ModeloFalso([_texto("Foram 999 pedidos."), _texto("De novo, 999 pedidos.")])
    texto = gerar_relatorio(conn, modelo, SEMANA)
    assert "999" not in texto
    assert "montado pelo código" in texto
    assert len(modelo.mensagens) == 2  # uma tentativa e uma nova tentativa
    reenvio = modelo.mensagens[1][-1]["content"]
    assert "999" in reenvio  # a nova tentativa diz o que estava errado


def test_segunda_tentativa_valida_e_aproveitada(conn):
    atual = calcular_kpis(conn, SEMANA, SEMANA_FIM)
    boa = f"Foram {atual['pedidos']} pedidos válidos."
    modelo = ModeloFalso([_texto("Foram 999 pedidos."), _texto(boa)])
    texto = gerar_relatorio(conn, modelo, SEMANA)
    assert boa in texto and "escrito pelo modelo" in texto


def test_erro_do_provedor_cai_no_resumo_deterministico(conn):
    modelo = ModeloFalso([ErroDoProvedor("limite", "limite")])
    texto = gerar_relatorio(conn, modelo, SEMANA)
    assert "montado pelo código" in texto


@pytest.mark.parametrize("conteudo", [None, "", "   "])
def test_modelo_sem_conteudo_cai_no_resumo_deterministico(conn, conteudo):
    modelo = ModeloFalso([_texto(conteudo)])
    texto = gerar_relatorio(conn, modelo, SEMANA)
    assert "montado pelo código" in texto


# --- linha de comando -------------------------------------------------------------------------


def test_cli_imprime_o_markdown(banco_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("BANCO_PATH", str(banco_path))
    codigo = relatorio.main(["--semana", "2026-09-21", "--sem-modelo"])
    assert codigo == 0
    saida = capsys.readouterr().out
    assert saida.startswith("# ") and "## Bairros mais lentos" in saida


def test_cli_com_saida_grava_o_arquivo_em_utf8(banco_path: Path, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BANCO_PATH", str(banco_path))
    monkeypatch.setattr(relatorio, "PASTA_RELATORIOS", tmp_path / "relatorios")
    codigo = relatorio.main(["--semana", "2026-09-21", "--sem-modelo", "--saida"])
    assert codigo == 0
    arquivo = tmp_path / "relatorios" / "semana-2026-09-21.md"
    conteudo = arquivo.read_text(encoding="utf-8")
    assert "Relatório semanal" in conteudo and "sintéticos" in conteudo
    assert "semana-2026-09-21.md" in capsys.readouterr().out


@pytest.mark.parametrize(
    "semana, trecho",
    [
        ("21/09/2026", "AAAA-MM-DD"),
        ("2026-09-22", "segunda-feira"),
        ("2026-06-22", "período"),
        ("2026-10-05", "período"),
    ],
)
def test_cli_data_invalida_da_mensagem_clara_e_codigo_2(
    banco_path: Path, monkeypatch, capsys, semana: str, trecho: str
):
    monkeypatch.setenv("BANCO_PATH", str(banco_path))
    codigo = relatorio.main(["--semana", semana, "--sem-modelo"])
    captura = capsys.readouterr()
    assert codigo == 2
    assert trecho in captura.err
    assert "Traceback" not in captura.err
    assert captura.out == ""


def test_cli_sem_banco_avisa_sem_traceback(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("BANCO_PATH", str(tmp_path / "nao-existe.db"))
    codigo = relatorio.main(["--semana", "2026-09-21", "--sem-modelo"])
    assert codigo == 1
    assert "analista.dados" in capsys.readouterr().err
