"""Formatação de números e datas em português do Brasil (`R$ 12.345,67`, `18,4%`, `1.234`).

Um único lugar para o relatório semanal, a página de avaliação e o painel, para o mesmo valor
sair igual em todos. Sem dependências além da biblioteca padrão.
"""

from collections.abc import Callable
from datetime import date


def decimal(valor: float, casas: int) -> str:
    return f"{valor:,.{casas}f}".replace(",", "\0").replace(".", ",").replace("\0", ".")


def inteiro(valor: int) -> str:
    return decimal(valor, 0)


def dinheiro(valor: float) -> str:
    return f"R$ {decimal(valor, 2)}"


def percentual(valor: float) -> str:
    return f"{decimal(valor, 1)}%"


def minutos(valor: float) -> str:
    return f"{decimal(valor, 1)} min"


def variacao(valor: float | None, em_pontos: bool = False) -> str:
    """`+5,2%`, `-7,3%` ou, em pontos percentuais, `-0,8 p.p.`; `None` não tem base."""
    if valor is None:
        return "sem base de comparação"
    sinal = "+" if valor > 0 else ""
    return f"{sinal}{decimal(valor, 1)}{' p.p.' if em_pontos else '%'}"


def ou_traco(valor: float | None, formatar: Callable[[float], str]) -> str:
    return "sem dados" if valor is None else formatar(valor)


def data_br(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


def fracao_em_percentual(valor: float | None) -> str:
    """0,8636 -> `86,4%`; `None` -> `n/d`. Para taxas de acerto, que vêm como fração."""
    return "n/d" if valor is None else percentual(valor * 100)
