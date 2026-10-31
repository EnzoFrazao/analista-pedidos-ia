"""Comparador de resultados: acerto = mesmo resultado que a referência, não o mesmo SQL.

Regras (parecido com execution accuracy):
- as linhas são comparadas como multiconjunto, ou na ordem quando `ordenado=True`;
- nomes de coluna são ignorados, mas o número de colunas tem de ser igual (coluna a mais é erro);
- números são iguais quando a diferença absoluta é de no máximo 0,005 (3 e 3.0 são iguais; 1,005 e
  1,01 também); booleano não é número;
- texto compara sem espaços nas pontas e sem diferença de maiúsculas e minúsculas;
- nulo só equivale a nulo;
- resultado truncado pelo teto de linhas nunca é equivalente: faltam linhas para comparar.

A tolerância absoluta substitui "arredondar a 2 casas e comparar", que separava valores quase iguais
quando o arredondamento caía em lados diferentes (1,0049 virava 1,0 e 1,0051 virava 1,01).
"""

from analista.sql_seguro import ResultadoSql

TOLERANCIA = 0.005
# Folga para o erro de representação do float: 0,13 - 0,125 dá 0,0050000000000000044.
_FOLGA_FLOAT = 1e-9


def _e_numero(valor: object) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _chave(valor: object) -> object:
    """Chave comparável de uma célula que não é número; o primeiro item separa os tipos."""
    if valor is None:
        return ("nulo",)
    if isinstance(valor, bool):
        return ("bool", valor)
    if isinstance(valor, str):
        return ("texto", valor.strip().casefold())
    return ("outro", repr(valor))


def _celulas_iguais(a: object, b: object) -> bool:
    if _e_numero(a) or _e_numero(b):
        return _e_numero(a) and _e_numero(b) and abs(a - b) <= TOLERANCIA + _FOLGA_FLOAT
    return _chave(a) == _chave(b)


def _linhas_iguais(a: tuple, b: tuple) -> bool:
    return len(a) == len(b) and all(_celulas_iguais(x, y) for x, y in zip(a, b, strict=True))


def _emparelha_todas(esperadas: list[tuple], recebidas: list[tuple]) -> bool:
    """Multiconjunto com tolerância: existe um par recebido para cada linha esperada?

    Emparelhamento bipartido por caminhos aumentantes (Kuhn). Um guloso erraria quando uma linha
    recebida serve a duas esperadas; com o teto de 50 linhas o custo é desprezível.
    """
    if len(esperadas) != len(recebidas):
        return False
    vizinhos = [
        [j for j, recebida in enumerate(recebidas) if _linhas_iguais(esperada, recebida)]
        for esperada in esperadas
    ]
    par_da_recebida: list[int | None] = [None] * len(recebidas)

    def aumentar(i: int, visitadas: set[int]) -> bool:
        for j in vizinhos[i]:
            if j in visitadas:
                continue
            visitadas.add(j)
            if par_da_recebida[j] is None or aumentar(par_da_recebida[j], visitadas):
                par_da_recebida[j] = i
                return True
        return False

    return all(aumentar(i, set()) for i in range(len(esperadas)))


def resultados_equivalentes(
    referencia: ResultadoSql, obtido: ResultadoSql, *, ordenado: bool
) -> bool:
    if referencia.truncado or obtido.truncado:
        return False
    if len(referencia.colunas) != len(obtido.colunas):
        return False
    esperadas = list(referencia.linhas)
    recebidas = list(obtido.linhas)
    if ordenado:
        return len(esperadas) == len(recebidas) and all(
            _linhas_iguais(a, b) for a, b in zip(esperadas, recebidas, strict=True)
        )
    return _emparelha_todas(esperadas, recebidas)
