"""Comparador de resultados: acerto = mesmo resultado que a referência, não o mesmo SQL.

Regras (execution accuracy):
- as linhas são comparadas como multiconjunto, ou na ordem quando `ordenado=True`;
- nomes de coluna são ignorados, mas o número de colunas tem de ser igual (coluna a mais é erro);
- números são arredondados a 2 casas (3 e 3.0 são iguais); booleano não é número;
- texto compara sem espaços nas pontas e sem diferença de maiúsculas e minúsculas;
- nulo só equivale a nulo;
- resultado truncado pelo teto de linhas nunca é equivalente: faltam linhas para comparar.
"""

from collections import Counter

from analista.sql_seguro import ResultadoSql

CASAS_DECIMAIS = 2


def _normalizar(valor: object) -> object:
    """Chave comparável de uma célula; o primeiro item separa tipos que não se misturam."""
    if valor is None:
        return ("nulo",)
    if isinstance(valor, bool):
        return ("bool", valor)
    if isinstance(valor, (int, float)):
        return ("num", round(valor, CASAS_DECIMAIS))
    if isinstance(valor, str):
        return ("texto", valor.strip().casefold())
    return ("outro", repr(valor))


def _linhas_normalizadas(resultado: ResultadoSql) -> list[tuple]:
    return [tuple(_normalizar(celula) for celula in linha) for linha in resultado.linhas]


def resultados_equivalentes(
    referencia: ResultadoSql, obtido: ResultadoSql, *, ordenado: bool
) -> bool:
    if referencia.truncado or obtido.truncado:
        return False
    if len(referencia.colunas) != len(obtido.colunas):
        return False
    esperadas = _linhas_normalizadas(referencia)
    recebidas = _linhas_normalizadas(obtido)
    if ordenado:
        return esperadas == recebidas
    return Counter(esperadas) == Counter(recebidas)
