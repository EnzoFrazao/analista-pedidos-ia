"""Rastro JSONL: uma linha JSON por evento, um arquivo por execução, utf-8."""

import hashlib
import json
import sqlite3
from pathlib import Path

from analista.agente import PROMPT_SISTEMA, Agente
from analista.config import Configuracao
from analista.llm import ChamadaFerramenta, MensagemModelo
from analista.rastro import Rastro
from tests.fakes import ModeloFalso


def _linhas(rastro: Rastro) -> list[dict]:
    texto = rastro.arquivo.read_text(encoding="utf-8")
    return [json.loads(linha) for linha in texto.splitlines()]


def _total_pedidos(caminho: Path) -> int:
    conn = sqlite3.connect(caminho)
    try:
        return conn.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0]
    finally:
        conn.close()


def test_cada_linha_e_json_valido(tmp_path: Path):
    rastro = Rastro(tmp_path)

    rastro.registrar("a", x=1)
    rastro.registrar("b", y=[1, 2], z={"k": None})

    linhas = _linhas(rastro)
    assert [linha["evento"] for linha in linhas] == ["a", "b"]
    assert linhas[0]["x"] == 1
    assert all("ts" in linha for linha in linhas)


def test_nome_do_arquivo_tem_data_hora_e_id_curto(tmp_path: Path):
    rastro = Rastro(tmp_path)

    assert rastro.arquivo.parent == tmp_path
    assert rastro.arquivo.suffix == ".jsonl"
    data, hora, curto = rastro.arquivo.stem.split("-")
    assert len(data) == 8 and data.isdigit()
    assert len(hora) == 6 and hora.isdigit()
    assert curto


def test_arquivos_distintos_por_execucao(tmp_path: Path):
    assert Rastro(tmp_path).arquivo != Rastro(tmp_path).arquivo


def test_cria_a_pasta_que_nao_existe(tmp_path: Path):
    rastro = Rastro(tmp_path / "runs" / "novas")

    rastro.registrar("a")

    assert rastro.arquivo.exists()


def test_texto_com_acento_grava_em_utf8(tmp_path: Path):
    rastro = Rastro(tmp_path)

    rastro.registrar("pergunta", texto="Qual o faturamento de São Paulo na semana?")

    bruto = rastro.arquivo.read_bytes()
    assert "São Paulo".encode() in bruto  # utf-8 de verdade, sem ã
    assert _linhas(rastro)[0]["texto"].startswith("Qual o faturamento de São")


def test_registra_chamada_consulta_linhas_e_resposta(banco_path: Path, tmp_path: Path):
    config = Configuracao(banco=banco_path, llm_api_key="SEGREDO-DA-CHAVE-999")
    consulta = json.dumps({"consulta": "SELECT COUNT(*) FROM pedidos"})
    modelo = ModeloFalso(
        [
            MensagemModelo("vou consultar", (ChamadaFerramenta("c1", "executar_sql", consulta),)),
            MensagemModelo("Há pedidos.", ()),
        ]
    )
    rastro = Rastro(tmp_path)

    resposta = Agente(modelo, config, rastro).perguntar("Quantos pedidos existem?")

    linhas = _linhas(rastro)
    assert [linha["evento"] for linha in linhas] == [
        "pergunta",
        "passo",
        "ferramenta",
        "passo",
        "resposta",
    ]

    assert linhas[0]["pergunta"] == "Quantos pedidos existem?"
    assert (
        linhas[0]["prompt_sistema_sha256"]
        == hashlib.sha256(PROMPT_SISTEMA.encode("utf-8")).hexdigest()
    )
    assert linhas[1]["conteudo"] == "vou consultar"
    assert linhas[1]["chamadas"][0]["nome"] == "executar_sql"

    ferramenta = linhas[2]
    assert ferramenta["nome"] == "executar_sql"
    assert ferramenta["id"] == "c1"
    assert ferramenta["auditoria"]["sql"] == "SELECT COUNT(*) FROM pedidos"
    assert ferramenta["auditoria"]["ok"] is True
    assert ferramenta["auditoria"]["linhas"] == [[_total_pedidos(banco_path)]]

    assert linhas[-1]["texto"] == "Há pedidos."
    assert linhas[-1]["esgotou_passos"] is False
    assert linhas[-1]["passos"] == resposta.passos == 2


def test_registra_erro_do_provedor(banco_path: Path, tmp_path: Path):
    from analista.llm import ErroDoProvedor

    rastro = Rastro(tmp_path)
    modelo = ModeloFalso([ErroDoProvedor("fora do ar")])

    Agente(modelo, Configuracao(banco=banco_path), rastro).perguntar("Oi")

    linhas = _linhas(rastro)
    assert [linha["evento"] for linha in linhas] == ["pergunta", "erro_provedor", "resposta"]
    assert linhas[1]["mensagem"] == "fora do ar"


def test_nao_grava_a_chave_nem_o_prompt_de_sistema(banco_path: Path, tmp_path: Path):
    config = Configuracao(banco=banco_path, llm_api_key="SEGREDO-DA-CHAVE-999")
    rastro = Rastro(tmp_path)

    Agente(ModeloFalso([MensagemModelo("ok", ())]), config, rastro).perguntar("oi")

    bruto = rastro.arquivo.read_text(encoding="utf-8")
    assert "SEGREDO-DA-CHAVE-999" not in bruto
    assert PROMPT_SISTEMA not in bruto
    assert PROMPT_SISTEMA[:60] not in bruto


def test_registra_o_motivo_de_fim_do_passo(banco_path: Path, tmp_path: Path):
    rastro = Rastro(tmp_path)
    modelo = ModeloFalso([MensagemModelo("cortado", (), "length")])

    Agente(modelo, Configuracao(banco=banco_path), rastro).perguntar("Oi")

    passo = next(linha for linha in _linhas(rastro) if linha["evento"] == "passo")
    assert passo["motivo_fim"] == "length"
