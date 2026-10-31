"""Formatação pt-BR compartilhada pelo relatório, pela avaliação e pelo painel."""

from datetime import date

import pytest

from analista import formatacao as f


def test_numeros_em_pt_br():
    assert f.inteiro(1234) == "1.234"
    assert f.dinheiro(12345.67) == "R$ 12.345,67"
    assert f.percentual(18.4) == "18,4%"
    assert f.minutos(32.4) == "32,4 min"
    assert f.decimal(1234567.891, 1) == "1.234.567,9"
    assert f.data_br(date(2026, 9, 21)) == "21/09/2026"


@pytest.mark.parametrize(
    ("valor", "em_pontos", "esperado"),
    [
        (5.2, False, "+5,2%"),
        (-7.3, False, "-7,3%"),
        (0.0, False, "0,0%"),
        (-0.8, True, "-0,8 p.p."),
        (None, False, "sem base de comparação"),
    ],
)
def test_variacao(valor, em_pontos, esperado):
    assert f.variacao(valor, em_pontos=em_pontos) == esperado


def test_ou_traco():
    assert f.ou_traco(None, f.dinheiro) == "sem dados"
    assert f.ou_traco(10.0, f.dinheiro) == "R$ 10,00"


@pytest.mark.parametrize(
    ("fracao", "esperado"), [(0.8636, "86,4%"), (1.0, "100,0%"), (0.0, "0,0%"), (None, "n/d")]
)
def test_fracao_em_percentual(fracao, esperado):
    assert f.fracao_em_percentual(fracao) == esperado
