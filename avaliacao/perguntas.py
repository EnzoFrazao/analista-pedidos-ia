"""Carrega as 25 perguntas de avaliação de `perguntas.yaml`, validando campos e valores."""

from dataclasses import dataclass
from pathlib import Path

import yaml

CAMINHO_PADRAO = Path(__file__).resolve().parent / "perguntas.yaml"

CATEGORIAS = frozenset({"agregacao", "filtro_juncao", "periodos", "ambigua", "recusa", "injecao"})
VERIFICACOES = frozenset(
    {"resultado", "esclarecimento_ou_resultado", "recusa", "resultado_sem_violacao"}
)
_OBRIGATORIOS = ("id", "categoria", "texto", "verificacao", "referencias", "ordenado")


@dataclass(frozen=True)
class Pergunta:
    id: str
    categoria: str
    texto: str
    verificacao: str
    referencias: tuple[str, ...]
    ordenado: bool


def _texto_nao_vazio(valor: object) -> bool:
    return isinstance(valor, str) and bool(valor.strip())


def _validar(indice: int, bruto: object) -> Pergunta:
    if not isinstance(bruto, dict):
        raise ValueError(f"item {indice}: esperado um mapa de campos, veio {type(bruto).__name__}")  # noqa: TRY004
    id_ = bruto.get("id")
    if not _texto_nao_vazio(id_):
        raise ValueError(f"item {indice}: campo 'id' ausente ou vazio")

    def erro(mensagem: str) -> ValueError:
        return ValueError(f"pergunta {id_}: {mensagem}")

    faltando = [campo for campo in _OBRIGATORIOS if campo not in bruto]
    if faltando:
        raise erro(f"campos ausentes: {', '.join(faltando)}")
    sobrando = sorted(set(bruto) - set(_OBRIGATORIOS))
    if sobrando:
        raise erro(f"campos desconhecidos: {', '.join(map(str, sobrando))}")
    if bruto["categoria"] not in CATEGORIAS:
        raise erro(f"categoria inválida {bruto['categoria']!r}; use {sorted(CATEGORIAS)}")
    if not _texto_nao_vazio(bruto["texto"]):
        raise erro("texto ausente ou vazio")
    if bruto["verificacao"] not in VERIFICACOES:
        raise erro(f"verificacao inválida {bruto['verificacao']!r}; use {sorted(VERIFICACOES)}")
    if not isinstance(bruto["ordenado"], bool):
        raise erro(f"ordenado deve ser true ou false, veio {bruto['ordenado']!r}")
    referencias = bruto["referencias"]
    if not isinstance(referencias, list) or not all(map(_texto_nao_vazio, referencias)):
        raise erro("referencias deve ser uma lista de consultas SQL não vazias")
    if bruto["verificacao"] == "recusa" and referencias:
        raise erro("referencias deve ser vazia quando a verificacao é 'recusa'")
    if bruto["verificacao"] != "recusa" and not referencias:
        raise erro(
            f"referencias não pode ser vazia quando a verificacao é {bruto['verificacao']!r}"
        )
    return Pergunta(
        id=id_,
        categoria=bruto["categoria"],
        texto=bruto["texto"].strip(),
        verificacao=bruto["verificacao"],
        referencias=tuple(sql.strip() for sql in referencias),
        ordenado=bruto["ordenado"],
    )


def carregar_perguntas(caminho: Path = CAMINHO_PADRAO) -> list[Pergunta]:
    """Lê e valida o YAML. Levanta `ValueError` citando o id da pergunta com problema."""
    with Path(caminho).open(encoding="utf-8") as arquivo:
        bruto = yaml.safe_load(arquivo)
    if not isinstance(bruto, list):
        raise ValueError(f"{caminho}: esperada uma lista de perguntas no topo do arquivo")  # noqa: TRY004
    perguntas: list[Pergunta] = []
    vistos: set[str] = set()
    for indice, item in enumerate(bruto, start=1):
        pergunta = _validar(indice, item)
        if pergunta.id in vistos:
            raise ValueError(f"pergunta {pergunta.id}: id duplicado")
        vistos.add(pergunta.id)
        perguntas.append(pergunta)
    return perguntas
