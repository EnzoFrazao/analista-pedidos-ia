"""Executor da avaliação: roda o agente nas perguntas de referência e mede o acerto.

Acerto é resultado, não texto: vale o resultado da ÚLTIMA consulta bem-sucedida da execução
(as perguntas pedem todos os valores numa tabela só). Cada pergunta roda `repeticoes` vezes;
o relatório traz a média e o pior caso (a pior repetição). Cada execução concluída é gravada em
`parcial.jsonl`, então uma queda do provedor no meio não joga fora o que já rodou.

O banco da avaliação é gerado de novo numa pasta temporária (nunca `data/pedidos.db`, que pode
estar velho). O agente trabalha numa cópia descartável; as consultas de referência rodam numa
conexão somente leitura à cópia intacta. Depois de cada pergunta, `hash_banco` da cópia de
trabalho tem de ser o da cópia intacta; se não for, a pergunta reprova e a cópia é restaurada.

Uso: python -m avaliacao.avaliar [--repeticoes 3] [--pausa 2] [--categoria agregacao]
                                 [--retomar | --recomecar]
Saída: 0 concluiu, 2 uso ou configuração (sem chave, parcial incompatível), 3 o provedor caiu
(rode de novo com --retomar), 130 interrompido.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from analista.agente import PROMPT_SISTEMA, Agente, Resposta
from analista.config import RAIZ, Configuracao, carregar_configuracao
from analista.dados import gerar_banco, hash_banco
from analista.llm import TEMPERATURA, ModeloLLM, criar_modelo
from analista.rastro import Rastro
from analista.sql_seguro import (
    MOTIVOS_DE_ESCRITA,
    ResultadoSql,
    abrir_somente_leitura,
    executar_consulta,
)
from avaliacao.comparador import resultados_equivalentes
from avaliacao.perguntas import CATEGORIAS, Pergunta, carregar_perguntas
from avaliacao.relatorio_md import gerar_markdown

PASTA_RESULTADOS = RAIZ / "avaliacao" / "resultados"
NOME_PARCIAL = "parcial.jsonl"
CATEGORIA_AMBIGUA = "ambigua"
_TIMEOUT_REFERENCIA_S = 10.0  # folga: a referência é código nosso e não pode falhar por lentidão

SAIDA_OK = 0
SAIDA_USO = 2
SAIDA_PROVEDOR = 3
SAIDA_INTERROMPIDO = 130


class ErroDeUso(Exception):
    """A execução não pode começar como pedida; a mensagem diz o que fazer."""


class ParcialExistente(ErroDeUso):
    pass


class ParcialIncompativel(ErroDeUso):
    pass


class AvaliacaoInterrompida(Exception):
    """O provedor do modelo falhou no meio da avaliação; o parcial guarda o que já rodou."""

    def __init__(self, motivo: str, feitas: int, total: int):
        super().__init__(motivo)
        self.feitas = feitas
        self.total = total


# --- Acerto de uma execução --------------------------------------------------------------------


def _ultimo_resultado(resposta: Resposta) -> ResultadoSql | None:
    """Resultado da última consulta bem-sucedida (as que falharam não contam)."""
    for auditoria in reversed(resposta.consultas):
        if auditoria.get("ok") and auditoria.get("sql") is not None:
            return ResultadoSql(
                tuple(auditoria["colunas"]),
                tuple(tuple(linha) for linha in auditoria["linhas"]),
                bool(auditoria["truncado"]),
            )
    return None


def _igual_a_alguma_referencia(
    pergunta: Pergunta, obtido: ResultadoSql, conn_referencia: sqlite3.Connection
) -> bool:
    for sql in pergunta.referencias:
        referencia = executar_consulta(conn_referencia, sql, timeout_s=_TIMEOUT_REFERENCIA_S)
        if resultados_equivalentes(referencia, obtido, ordenado=pergunta.ordenado):
            return True
    return False


def _julgar(
    pergunta: Pergunta,
    resposta: Resposta,
    conn_referencia: sqlite3.Connection,
    banco_inalterado: bool,
) -> tuple[bool, list[str]]:
    """(acerto, notas). As notas dizem o que houve de relevante, acertando ou não."""
    notas: list[str] = []
    if resposta.esgotou_passos:
        notas.append("esgotou o limite de passos")
    escreveu = any(a.get("motivo") in MOTIVOS_DE_ESCRITA for a in resposta.consultas)
    if escreveu:
        notas.append("tentou escrita (rejeitada pelo SQL)")
    if not banco_inalterado:
        notas.append("banco alterado durante a execução")

    obtido = _ultimo_resultado(resposta)
    if pergunta.verificacao == "recusa":
        acerto = not escreveu and banco_inalterado and bool(resposta.texto.strip())
        return acerto, notas

    igual = obtido is not None and _igual_a_alguma_referencia(pergunta, obtido, conn_referencia)
    if obtido is None:
        notas.append("nenhuma consulta bem-sucedida")
    elif not igual:
        notas.append("resultado diferente da referência")

    pediu_esclarecimento = False
    if pergunta.verificacao == "resultado":
        acerto = igual
    elif pergunta.verificacao == "resultado_sem_violacao":
        acerto = igual and not escreveu
    else:  # esclarecimento_ou_resultado
        pediu_esclarecimento = obtido is None and "?" in resposta.texto
        acerto = igual or pediu_esclarecimento
        if pediu_esclarecimento:
            notas.append("pediu esclarecimento")
    # Um banco que mudou durante a pergunta invalida a comparação com a referência.
    return acerto and banco_inalterado, notas


def avaliar_pergunta(
    pergunta: Pergunta,
    resposta: Resposta,
    conn_referencia: sqlite3.Connection,
    banco_inalterado: bool,
) -> bool:
    """True se a execução acertou, pela regra de `pergunta.verificacao` (ver perguntas.yaml)."""
    return _julgar(pergunta, resposta, conn_referencia, banco_inalterado)[0]


# --- Parcial -----------------------------------------------------------------------------------


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _host(url: str) -> str | None:
    """Só o nome do servidor: sem usuário, caminho nem parâmetros (que podem ter a chave)."""
    return urlparse(url).hostname


def _ler_parcial(caminho: Path) -> list[dict]:
    """Linhas gravadas. Uma última linha cortada (queda no meio da escrita) é ignorada."""
    if not caminho.exists():
        return []
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    registros = []
    for numero, linha in enumerate(linhas, start=1):
        if not linha.strip():
            continue
        try:
            registros.append(json.loads(linha))
        except json.JSONDecodeError:
            if numero == len(linhas):
                break
            raise ParcialIncompativel(
                f"{caminho.name} está corrompido na linha {numero}. Apague-o e rode de novo "
                "com --recomecar."
            ) from None
    return registros


def _checar_compatibilidade(registros: list[dict], meta: dict, caminho: Path) -> None:
    for registro in registros:
        diferentes = sorted(k for k in meta if registro.get("meta", {}).get(k) != meta[k])
        if diferentes:
            raise ParcialIncompativel(
                f"{caminho.name} é de outra execução: {', '.join(diferentes)} mudou. "
                "Misturar as duas daria uma nota que não vem de uma configuração só. "
                "Use --recomecar para descartar o parcial."
            )


def _anexar(caminho: Path, registro: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("a", encoding="utf-8", newline="\n") as saida:
        saida.write(json.dumps(registro, ensure_ascii=False) + "\n")
        saida.flush()


# --- Consolidação ------------------------------------------------------------------------------


def _grupo(registros: dict, perguntas: list[Pergunta], repeticoes: int) -> dict:
    """Média e pior caso: acerto de cada repetição sobre o grupo, depois média e mínimo."""
    if not perguntas:
        return {"media": None, "pior_caso": None, "n": 0}
    por_repeticao = [
        sum(registros[(p.id, rep)]["acerto"] for p in perguntas) / len(perguntas)
        for rep in range(1, repeticoes + 1)
    ]
    return {
        "media": round(sum(por_repeticao) / repeticoes, 4),
        "pior_caso": round(min(por_repeticao), 4),
        "n": len(perguntas),
    }


def _observacao(registros: dict, pergunta: Pergunta, repeticoes: int) -> str:
    partes = []
    for rep in range(1, repeticoes + 1):
        notas = registros[(pergunta.id, rep)].get("notas") or []
        if notas:
            partes.append(f"rep {rep}: {', '.join(notas)}")
    return "; ".join(partes)


def _commit() -> str | None:
    """SHA curto do commit do código que rodou; None se não houver git ou repositório."""
    try:
        saida = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=RAIZ,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return saida.stdout.strip() or None


def _consolidar(
    selecionadas: list[Pergunta],
    repeticoes: int,
    registros: dict,
    meta: dict,
    categoria: str | None,
) -> dict:
    ambiguas = [p for p in selecionadas if p.categoria == CATEGORIA_AMBIGUA]
    objetivas = [p for p in selecionadas if p.categoria != CATEGORIA_AMBIGUA]
    nomes = list(dict.fromkeys(p.categoria for p in selecionadas))
    return {
        "gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "commit": _commit(),
        "modelo": meta["modelo"],
        "endpoint_host": meta["endpoint_host"],
        "temperatura": meta["temperatura"],
        "max_passos": meta["max_passos"],
        "hash_prompt": meta["hash_prompt"],
        "hash_banco": meta["hash_banco"],
        "hash_perguntas": meta["hash_perguntas"],
        "repeticoes": repeticoes,
        "categoria_filtrada": categoria,
        "geral": _grupo(registros, selecionadas, repeticoes),
        "objetivas": _grupo(registros, objetivas, repeticoes),
        "ambiguas": _grupo(registros, ambiguas, repeticoes),
        "categorias": {
            nome: _grupo(registros, [p for p in selecionadas if p.categoria == nome], repeticoes)
            for nome in nomes
        },
        "perguntas": [
            {
                "id": p.id,
                "categoria": p.categoria,
                "texto": p.texto,
                "acertos": [registros[(p.id, rep)]["acerto"] for rep in range(1, repeticoes + 1)],
                "observacao": _observacao(registros, p, repeticoes),
            }
            for p in selecionadas
        ],
    }


# --- Execução ----------------------------------------------------------------------------------


def _banco_inalterado(caminho: Path, hash_original: str) -> bool:
    """Um banco que nem abre ou perdeu tabela (DROP) também conta como alterado."""
    try:
        conn = abrir_somente_leitura(caminho)
        try:
            return hash_banco(conn) == hash_original
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _registro(
    pergunta: Pergunta,
    repeticao: int,
    resposta: Resposta,
    acerto: bool,
    notas: list[str],
    inalterado: bool,
    rastro: Rastro,
    meta: dict,
) -> dict:
    obtido = _ultimo_resultado(resposta)
    return {
        "id": pergunta.id,
        "repeticao": repeticao,
        "categoria": pergunta.categoria,
        "acerto": acerto,
        "notas": notas,
        "esgotou_passos": resposta.esgotou_passos,
        "passos": resposta.passos,
        "banco_inalterado": inalterado,
        "texto": resposta.texto,
        "consultas": [
            {k: a.get(k) for k in ("sql", "ok", "motivo", "n_linhas", "truncado")}
            for a in resposta.consultas
        ],
        "resultado_final": (
            None
            if obtido is None
            else {
                "colunas": list(obtido.colunas),
                "linhas": [list(linha) for linha in obtido.linhas],
                "truncado": obtido.truncado,
            }
        ),
        "rastro": rastro.arquivo.name,
        "meta": meta,
    }


def executar_avaliacao(
    modelo: ModeloLLM,
    config: Configuracao,
    *,
    repeticoes: int = 3,
    pausa_s: float = 0.0,
    categoria: str | None = None,
    pasta: Path = PASTA_RESULTADOS,
    retomar: bool = False,
    perguntas: list[Pergunta] | None = None,
    saida: Callable[[str], None] | None = None,
) -> dict:
    """Roda cada pergunta `repeticoes` vezes e devolve o dicionário de `ultima.json`.

    `pasta` recebe `parcial.jsonl` e `rastros/`. Levanta `ParcialExistente` (há parcial e não se
    pediu `retomar`), `ParcialIncompativel` (o parcial é de outra configuração) e
    `AvaliacaoInterrompida` (o provedor falhou; o parcial fica para `retomar`).
    """
    if repeticoes < 1:
        raise ValueError("repeticoes deve ser pelo menos 1")
    aviso = saida or (lambda _mensagem: None)
    todas = perguntas if perguntas is not None else carregar_perguntas()
    selecionadas = [p for p in todas if categoria is None or p.categoria == categoria]
    if not selecionadas:
        raise ErroDeUso(f"nenhuma pergunta na categoria {categoria!r}")

    pasta = Path(pasta)
    parcial = pasta / NOME_PARCIAL
    anteriores = _ler_parcial(parcial)
    if anteriores and not retomar:
        raise ParcialExistente(
            f"Já existe {parcial} com {len(anteriores)} execuções. Use --retomar para continuar "
            "de onde parou ou --recomecar para descartá-lo."
        )

    with tempfile.TemporaryDirectory(prefix="avaliacao-", ignore_cleanup_errors=True) as temporaria:
        intacto = Path(temporaria) / "intacto.db"
        trabalho = Path(temporaria) / "trabalho.db"
        gerar_banco(intacto)
        conn_referencia = abrir_somente_leitura(intacto)
        try:
            hash_original = hash_banco(conn_referencia)
            meta = {
                "modelo": config.llm_model,
                "endpoint_host": _host(config.llm_base_url),
                "temperatura": TEMPERATURA,
                "max_passos": config.max_passos,
                "hash_prompt": _sha256(PROMPT_SISTEMA),
                "hash_banco": hash_original,
                "hash_perguntas": _sha256(
                    json.dumps([asdict(p) for p in todas], ensure_ascii=False, sort_keys=True)
                ),
            }
            _checar_compatibilidade(anteriores, meta, parcial)
            registros = {
                (r["id"], r["repeticao"]): r
                for r in anteriores
                if r["repeticao"] <= repeticoes and r["id"] in {p.id for p in selecionadas}
            }
            if parcial.exists():  # tira uma última linha cortada, que corromperia a próxima
                parcial.write_text(
                    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in anteriores),
                    encoding="utf-8",
                    newline="\n",
                )
            shutil.copyfile(intacto, trabalho)
            config_agente = replace(config, banco=trabalho)
            total = len(selecionadas) * repeticoes
            if registros:
                aviso(f"Retomando: {len(registros)} de {total} execuções já feitas.")

            primeira_chamada = True
            for pergunta in selecionadas:
                for repeticao in range(1, repeticoes + 1):
                    if (pergunta.id, repeticao) in registros:
                        continue
                    if pausa_s > 0 and not primeira_chamada:
                        time.sleep(pausa_s)
                    primeira_chamada = False

                    rastro = Rastro(pasta / "rastros")
                    resposta = Agente(modelo, config_agente, rastro).perguntar(pergunta.texto)
                    if resposta.falha_provedor:
                        # Queda do serviço não é erro do agente: não pontua, não grava.
                        raise AvaliacaoInterrompida(resposta.texto, len(registros), total)

                    inalterado = _banco_inalterado(trabalho, hash_original)
                    if not inalterado:
                        shutil.copyfile(intacto, trabalho)  # a próxima pergunta vê o original
                    acerto, notas = _julgar(pergunta, resposta, conn_referencia, inalterado)
                    registro = _registro(
                        pergunta, repeticao, resposta, acerto, notas, inalterado, rastro, meta
                    )
                    _anexar(parcial, registro)
                    registros[(pergunta.id, repeticao)] = registro
                    aviso(
                        f"[{len(registros)}/{total}] {pergunta.id} repetição {repeticao}: "
                        f"{'acertou' if acerto else 'errou'}"
                        + (f" ({', '.join(notas)})" if notas else "")
                    )
        finally:
            conn_referencia.close()

    return _consolidar(selecionadas, repeticoes, registros, meta, categoria)


# --- Relatório ---------------------------------------------------------------------------------


def escrever_relatorio(resultado: dict, pasta: Path) -> None:
    """Grava `ultima.json` e `ultima.md` (utf-8). Avaliação filtrada por categoria vai para
    `ultima-<categoria>.*`, para não sobrescrever o resultado de uma avaliação completa."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    filtro = resultado.get("categoria_filtrada")
    base = f"ultima-{filtro}" if filtro else "ultima"
    conteudos = {
        f"{base}.json": json.dumps(resultado, ensure_ascii=False, indent=2) + "\n",
        f"{base}.md": gerar_markdown(resultado),
    }
    for nome, conteudo in conteudos.items():
        temporario = pasta / f"{nome}.tmp"
        temporario.write_text(conteudo, encoding="utf-8", newline="\n")
        os.replace(temporario, pasta / nome)


# --- CLI ---------------------------------------------------------------------------------------

_AJUDA_SEM_CHAVE = """\
Falta a chave do modelo (LLM_API_KEY): a avaliação chama o modelo de verdade.
Copie .env.example para .env, preencha LLM_API_KEY=<sua chave> (o .env não vai para o Git) e
rode de novo. Para conferir chave, modelo e tool calling antes: python -m analista.verificar_modelo"""


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    analisador = argparse.ArgumentParser(
        prog="python -m avaliacao.avaliar",
        description=(
            "Roda o agente nas 25 perguntas de referência, N vezes cada, e grava a média e o "
            "pior caso em avaliacao/resultados/ultima.json e ultima.md. Chama o modelo de "
            "verdade (precisa de LLM_API_KEY) e gasta cota."
        ),
    )
    analisador.add_argument(
        "--repeticoes", type=int, default=3, help="vezes que cada pergunta roda (padrão: 3)"
    )
    analisador.add_argument(
        "--pausa",
        type=float,
        default=0.0,
        help="segundos de espera entre execuções, para respeitar o limite do provedor",
    )
    analisador.add_argument(
        "--categoria",
        choices=sorted(CATEGORIAS),
        help="roda só esta categoria; o resultado vai para ultima-<categoria>.* e não vale "
        "como nota geral",
    )
    continuidade = analisador.add_mutually_exclusive_group()
    continuidade.add_argument(
        "--retomar",
        action="store_true",
        help="continua de onde parou, a partir de parcial.jsonl (recusa se modelo, prompt, "
        "banco ou perguntas mudaram)",
    )
    continuidade.add_argument(
        "--recomecar", action="store_true", help="descarta parcial.jsonl e começa do zero"
    )
    argumentos = analisador.parse_args(argv)
    if argumentos.repeticoes < 1:
        analisador.error("--repeticoes deve ser pelo menos 1")
    if argumentos.pausa < 0:
        analisador.error("--pausa não pode ser negativa")
    return argumentos


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    modelo: ModeloLLM | None = None,
    pasta: Path | None = None,
) -> int:
    """Devolve o código de saída. `env`, `modelo` e `pasta` existem para os testes."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argumentos = _argumentos(argv)
    config = carregar_configuracao(env)
    if not config.llm_api_key:
        print(_AJUDA_SEM_CHAVE)
        return SAIDA_USO
    modelo = modelo or criar_modelo(config)
    if modelo is None:  # defensivo: criar_modelo só devolve None sem chave
        print(_AJUDA_SEM_CHAVE)
        return SAIDA_USO

    pasta = Path(pasta) if pasta is not None else PASTA_RESULTADOS
    comando = "python -m avaliacao.avaliar"
    if argumentos.categoria:
        comando += f" --categoria {argumentos.categoria}"
    comando += f" --repeticoes {argumentos.repeticoes} --retomar"

    if argumentos.recomecar:
        (pasta / NOME_PARCIAL).unlink(missing_ok=True)
    print(f"Modelo: {config.llm_model} · {argumentos.repeticoes} repetições por pergunta.")
    try:
        resultado = executar_avaliacao(
            modelo,
            config,
            repeticoes=argumentos.repeticoes,
            pausa_s=argumentos.pausa,
            categoria=argumentos.categoria,
            pasta=pasta,
            retomar=argumentos.retomar,
            saida=print,
        )
    except ErroDeUso as erro:
        print(f"Não foi possível começar: {erro}")
        return SAIDA_USO
    except AvaliacaoInterrompida as erro:
        print(
            f"\nO serviço do modelo falhou depois de {erro.feitas} de {erro.total} execuções:\n"
            f"  {erro}\n"
            f"O que já rodou está em {pasta / NOME_PARCIAL}. Para continuar de onde parou:\n"
            f"  {comando}"
        )
        return SAIDA_PROVEDOR
    except KeyboardInterrupt:
        print(f"\nInterrompido. O que já rodou está em {pasta / NOME_PARCIAL}. Para continuar:")
        print(f"  {comando}")
        return SAIDA_INTERROMPIDO

    escrever_relatorio(resultado, pasta)
    (pasta / NOME_PARCIAL).unlink(missing_ok=True)
    geral = resultado["geral"]
    print(
        f"\nGeral: média {geral['media'] * 100:.1f}% · pior caso {geral['pior_caso'] * 100:.1f}% "
        f"({geral['n']} perguntas × {resultado['repeticoes']} repetições)."
    )
    print(f"Relatório em {pasta}.")
    return SAIDA_OK


if __name__ == "__main__":
    sys.exit(main())
