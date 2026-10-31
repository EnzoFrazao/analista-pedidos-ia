from analista.sql_seguro import ResultadoSql
from avaliacao.comparador import resultados_equivalentes


def _res(colunas, *linhas, truncado=False):
    return ResultadoSql(colunas=tuple(colunas), linhas=tuple(linhas), truncado=truncado)


def test_ignora_ordem_e_nome_de_coluna():
    ref = _res(("bairro", "total"), ("Centro", 10), ("Turu", 4))
    obtido = _res(("b", "n"), ("Turu", 4), ("Centro", 10))
    assert resultados_equivalentes(ref, obtido, ordenado=False)


def test_respeita_ordem_quando_ordenado():
    ref = _res(("bairro",), ("Centro",), ("Turu",))
    invertido = _res(("bairro",), ("Turu",), ("Centro",))
    assert not resultados_equivalentes(ref, invertido, ordenado=True)
    assert resultados_equivalentes(ref, ref, ordenado=True)
    assert resultados_equivalentes(ref, invertido, ordenado=False)


def test_arredonda_decimais_em_duas_casas():
    ref = _res(("x",), (1.2349,))
    assert resultados_equivalentes(ref, _res(("x",), (1.23,)), ordenado=False)
    assert resultados_equivalentes(ref, _res(("x",), (1.2301,)), ordenado=False)
    assert not resultados_equivalentes(ref, _res(("x",), (1.24,)), ordenado=False)
    assert not resultados_equivalentes(ref, _res(("x",), (1.2,)), ordenado=False)


def test_inteiro_e_decimal_iguais_sao_equivalentes():
    assert resultados_equivalentes(_res(("n",), (3,)), _res(("n",), (3.0,)), ordenado=False)


def test_coluna_a_mais_e_erro():
    ref = _res(("n",), (7,))
    a_mais = _res(("n", "extra"), (7, "x"))
    assert not resultados_equivalentes(ref, a_mais, ordenado=False)
    assert not resultados_equivalentes(a_mais, ref, ordenado=False)


def test_texto_com_caixa_e_espaco_diferentes_e_igual():
    ref = _res(("bairro",), ("Olho d'Água",))
    obtido = _res(("bairro",), ("  OLHO D'ÁGUA ",))
    assert resultados_equivalentes(ref, obtido, ordenado=False)
    assert not resultados_equivalentes(ref, _res(("bairro",), ("Olho",)), ordenado=False)


def test_resultados_vazios_so_equivalem_a_vazio():
    vazio = _res(("n",))
    assert resultados_equivalentes(vazio, _res(("m",)), ordenado=False)
    assert not resultados_equivalentes(vazio, _res(("n",), (0,)), ordenado=False)
    assert not resultados_equivalentes(_res(("n",), (0,)), vazio, ordenado=False)


def test_nulo_so_equivale_a_nulo():
    assert resultados_equivalentes(_res(("n",), (None,)), _res(("n",), (None,)), ordenado=False)
    assert not resultados_equivalentes(_res(("n",), (None,)), _res(("n",), (0,)), ordenado=False)
    assert not resultados_equivalentes(_res(("n",), (0,)), _res(("n",), (None,)), ordenado=False)


def test_booleano_nao_e_numero():
    assert not resultados_equivalentes(_res(("n",), (1,)), _res(("n",), (True,)), ordenado=False)
    assert not resultados_equivalentes(_res(("n",), (True,)), _res(("n",), (1,)), ordenado=False)
    assert resultados_equivalentes(_res(("n",), (True,)), _res(("n",), (True,)), ordenado=False)


def test_numero_nao_equivale_a_texto():
    assert not resultados_equivalentes(_res(("n",), (3,)), _res(("n",), ("3",)), ordenado=False)


def test_conta_linhas_repetidas_como_multiconjunto():
    dois = _res(("n",), (1,), (1,))
    assert resultados_equivalentes(dois, _res(("n",), (1,), (1,)), ordenado=False)
    assert not resultados_equivalentes(dois, _res(("n",), (1,), (2,)), ordenado=False)
    assert not resultados_equivalentes(dois, _res(("n",), (1,)), ordenado=False)


def test_resultado_truncado_nunca_e_equivalente():
    ref = _res(("n",), (1,))
    cortado = _res(("n",), (1,), truncado=True)
    assert not resultados_equivalentes(ref, cortado, ordenado=False)
    assert not resultados_equivalentes(cortado, cortado, ordenado=False)


def test_numeros_comparam_com_tolerancia_absoluta_de_meio_centesimo():
    """Arredondar e depois comparar separava valores a 0,001 de distância (1,005 vira 1,0 e
    1,0051 vira 1,01); a tolerância absoluta de 0,005 não depende de onde cai o arredondamento."""
    assert resultados_equivalentes(_res(("x",), (1.0049,)), _res(("x",), (1.0051,)), ordenado=False)
    assert resultados_equivalentes(_res(("x",), (1.005,)), _res(("x",), (1.01,)), ordenado=False)
    assert resultados_equivalentes(_res(("x",), (1.005,)), _res(("x",), (1.0,)), ordenado=False)
    assert resultados_equivalentes(_res(("x",), (0.125,)), _res(("x",), (0.13,)), ordenado=True)
    assert not resultados_equivalentes(
        _res(("x",), (1.0,)), _res(("x",), (1.0051,)), ordenado=False
    )
    assert not resultados_equivalentes(_res(("x",), (2,)), _res(("x",), (2.006,)), ordenado=False)


def test_tolerancia_vale_tambem_sem_ordem_e_com_linhas_repetidas():
    ref = _res(("b", "v"), ("A", 1.004), ("B", 2.0), ("A", 1.004))
    obtido = _res(("b", "v"), ("B", 2.004), ("A", 1.0), ("A", 1.008))
    assert resultados_equivalentes(ref, obtido, ordenado=False)
    assert not resultados_equivalentes(ref, obtido, ordenado=True)
    sobra = _res(("b", "v"), ("B", 2.0), ("A", 1.0), ("B", 2.0))
    assert not resultados_equivalentes(ref, sobra, ordenado=False)


def test_booleano_continua_fora_da_tolerancia_numerica():
    assert not resultados_equivalentes(_res(("x",), (True,)), _res(("x",), (1,)), ordenado=False)
    assert not resultados_equivalentes(_res(("x",), (1.0,)), _res(("x",), (True,)), ordenado=False)
