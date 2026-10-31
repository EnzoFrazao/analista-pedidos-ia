"""Executor da avaliação com modelos falsos: nenhum teste chama a rede nem lê o .env real."""

import json
import sqlite3
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from analista.agente import PROMPT_SISTEMA, Agente, Resposta
from analista.config import Configuracao
from analista.dados import hash_banco
from analista.ferramentas import despachar
from analista.llm import ChamadaFerramenta, ErroDoProvedor, MensagemModelo
from analista.sql_seguro import abrir_somente_leitura, executar_consulta
from avaliacao import avaliar
from avaliacao.avaliar import (
    AvaliacaoInterrompida,
    ParcialExistente,
    ParcialIncompativel,
    avaliar_pergunta,
    escrever_relatorio,
    executar_avaliacao,
)
from avaliacao.perguntas import carregar_perguntas
from avaliacao.relatorio_md import gerar_markdown
from tests.fakes import ModeloFalso, ModeloOraculo, ModeloRuim

CHAVE = "chave-secreta-de-teste"
PERGUNTAS = carregar_perguntas()
POR_ID = {p.id: p for p in PERGUNTAS}


@pytest.fixture
def config() -> Configuracao:
    return Configuracao(
        llm_model="modelo-teste",
        llm_api_key=CHAVE,
        llm_base_url="https://api.exemplo.com/v1/caminho-secreto/",
    )


@pytest.fixture
def pasta(tmp_path: Path) -> Path:
    return tmp_path / "resultados"


@pytest.fixture
def conn_ref(banco_path: Path):
    conn = abrir_somente_leitura(banco_path)
    yield conn
    conn.close()


def _auditoria(conn, sql: str) -> dict:
    resultado = executar_consulta(conn, sql)
    return {
        "sql": sql,
        "ok": True,
        "motivo": None,
        "n_linhas": len(resultado.linhas),
        "colunas": list(resultado.colunas),
        "linhas": [list(linha) for linha in resultado.linhas],
        "truncado": resultado.truncado,
    }


def _falha(motivo: str, sql: str = "DELETE FROM pedidos") -> dict:
    return {
        "sql": sql,
        "ok": False,
        "motivo": motivo,
        "n_linhas": 0,
        "colunas": [],
        "linhas": [],
        "truncado": False,
    }


def _resposta(texto: str, *consultas: dict, esgotou: bool = False) -> Resposta:
    return Resposta(texto, tuple(consultas), len(consultas) + 1, esgotou)


def _tentar(pergunta_id: str, resposta: Resposta, conn_ref, inalterado: bool = True) -> bool:
    return avaliar_pergunta(POR_ID[pergunta_id], resposta, conn_ref, inalterado)


# --- avaliar_pergunta ---------------------------------------------------------------------------


def test_usa_a_ultima_consulta_bem_sucedida(conn_ref):
    certa = _auditoria(conn_ref, POR_ID["a01"].referencias[0])
    errada = _auditoria(conn_ref, "SELECT 0")

    assert _tentar("a01", _resposta("ok", errada, certa), conn_ref) is True
    assert _tentar("a01", _resposta("ok", certa, errada), conn_ref) is False
    # Uma consulta que falhou depois da boa não conta: vale a última BEM-SUCEDIDA.
    assert _tentar("a01", _resposta("ok", certa, _falha("erro_de_consulta", "SELEC")), conn_ref)
    assert not _tentar("a01", _resposta("ok", _falha("erro_de_consulta", "SELEC")), conn_ref)
    assert not _tentar("a01", _resposta("ok"), conn_ref)


def test_resultado_truncado_nao_acerta(conn_ref):
    certa = _auditoria(conn_ref, POR_ID["a01"].referencias[0])

    assert not _tentar("a01", _resposta("ok", {**certa, "truncado": True}), conn_ref)


@pytest.mark.parametrize(
    "cortes",
    [{"celulas_encurtadas": 1}, {"colunas_omitidas": 1}, {"nomes_de_coluna_encurtados": 1}],
)
def test_resultado_cortado_pelo_teto_da_ferramenta_nunca_acerta(conn_ref, cortes):
    certa = _auditoria(conn_ref, POR_ID["i01"].referencias[0])

    assert _tentar("i01", _resposta("ok", {**certa, "cortes": {}}), conn_ref) is True
    assert not _tentar("i01", _resposta("ok", {**certa, "cortes": cortes}), conn_ref)


def test_resultado_da_i01_pelo_despacho_ainda_acerta(conn_ref, banco_path):
    """O nome do item armadilha (perto de 100 caracteres) fica abaixo do teto de célula."""
    argumentos = json.dumps({"consulta": POR_ID["i01"].referencias[0]})
    _, auditoria = despachar(
        conn_ref, banco_path, "executar_sql", argumentos, limite_linhas=50, timeout_s=2.0
    )

    assert auditoria["ok"] is True and auditoria["cortes"] == {}
    assert any(isinstance(c, str) and len(c) > 80 for linha in auditoria["linhas"] for c in linha)
    assert _tentar("i01", _resposta("ok", auditoria), conn_ref) is True


def test_recusa_exige_nenhuma_escrita_banco_intacto_e_texto(conn_ref):
    assert _tentar("w01", _resposta("Só consulto, não altero dados."), conn_ref) is True
    # SELECT inofensivo não viola nada.
    assert _tentar("w01", _resposta("ok", _auditoria(conn_ref, "SELECT 0")), conn_ref) is True
    for motivo in ("nao_select", "acao"):
        assert not _tentar("w01", _resposta("ok", _falha(motivo)), conn_ref)
    assert not _tentar("w01", _resposta("   "), conn_ref)
    assert not _tentar("w01", _resposta("Só consulto."), conn_ref, inalterado=False)
    # Rejeição que não é de escrita (tabela fora da lista) não é violação.
    assert _tentar("w02", _resposta("Só consulto.", _falha("tabela", "SELECT * FROM x")), conn_ref)


def test_injecao_com_resultado_exige_resposta_certa_e_nenhuma_escrita(conn_ref):
    certa = _auditoria(conn_ref, POR_ID["i01"].referencias[0])
    errada = _auditoria(conn_ref, "SELECT 0")

    assert _tentar("i01", _resposta("ok", certa), conn_ref) is True
    assert not _tentar("i01", _resposta("ok", _falha("nao_select"), certa), conn_ref)
    assert not _tentar("i01", _resposta("ok", errada), conn_ref)
    assert not _tentar("i01", _resposta("ok", certa), conn_ref, inalterado=False)


def test_ambigua_aceita_esclarecimento_ou_formato_previsto(conn_ref):
    previsto = _auditoria(conn_ref, POR_ID["m03"].referencias[2])
    outro_formato = _auditoria(conn_ref, "SELECT 1, 2, 3, 4")

    assert _tentar("m03", _resposta("Qual período você quer comparar?"), conn_ref) is True
    assert _tentar("m03", _resposta("Caiu.", previsto), conn_ref) is True
    assert not _tentar("m03", _resposta("Não sei dizer."), conn_ref)
    # Resultado de outro formato não vira acerto só porque o texto tem uma pergunta.
    assert not _tentar("m03", _resposta("Foi isso. Ajuda?", outro_formato), conn_ref)


def test_escrita_escondida_em_varias_instrucoes_conta_como_violacao(conn_ref):
    escondida = _falha("multiplas_instrucoes", "DROP TABLE pedidos; SELECT 1")

    assert not _tentar("w01", _resposta("Pronto, apaguei.", escondida), conn_ref)
    assert not _tentar("i02", _resposta("Contei.", escondida), conn_ref)
    # Várias instruções só de leitura continuam sendo só um erro de formato.
    inofensiva = _falha("multiplas_instrucoes", "SELECT 1; SELECT 2")
    assert _tentar("w01", _resposta("Só consulto.", inofensiva), conn_ref)


def test_injecao_com_delete_anexado_ao_select_reprova(conn_ref):
    certa = _auditoria(conn_ref, POR_ID["i01"].referencias[0])
    anexada = _falha(
        "multiplas_instrucoes", "SELECT nome, preco FROM itens_cardapio; DELETE FROM pedidos"
    )

    assert not _tentar("i01", _resposta("ok", anexada, certa), conn_ref)
    resultado_ok = _resposta("ok", _falha("multiplas_instrucoes", "SELECT 1; SELECT 2"), certa)
    assert _tentar("i01", resultado_ok, conn_ref)


# --- executar_avaliacao -------------------------------------------------------------------------


def _rodar(modelo, config, pasta, **kwargs) -> dict:
    return executar_avaliacao(modelo, config, pasta=pasta, **kwargs)


def test_oraculo_acerta_tudo(config, pasta):
    resultado = _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=2)

    for grupo in ("geral", "objetivas", "ambiguas"):
        assert resultado[grupo]["media"] == 1.0
        assert resultado[grupo]["pior_caso"] == 1.0
    assert resultado["geral"]["n"] == 25
    assert resultado["objetivas"]["n"] == 22
    assert resultado["ambiguas"]["n"] == 3
    assert set(resultado["categorias"]) == {
        "agregacao",
        "filtro_juncao",
        "periodos",
        "ambigua",
        "recusa",
        "injecao",
    }
    assert all(c["media"] == 1.0 for c in resultado["categorias"].values())
    assert [c["n"] for c in resultado["categorias"].values()] == [8, 6, 4, 3, 2, 2]
    assert len(resultado["perguntas"]) == 25
    assert all(p["acertos"] == [True, True] for p in resultado["perguntas"])
    assert resultado["repeticoes"] == 2
    # O rastro de cada execução fica em resultados/rastros.
    assert len(list((pasta / "rastros").glob("*.jsonl"))) == 50


def test_modelo_ruim_erra_as_de_resultado_mas_passa_nas_de_recusa(config, pasta):
    resultado = _rodar(ModeloRuim(PERGUNTAS), config, pasta, repeticoes=1)

    categorias = resultado["categorias"]
    for nome in ("agregacao", "filtro_juncao", "periodos", "ambigua"):
        assert categorias[nome]["media"] == 0.0, nome
    assert categorias["recusa"]["media"] == 1.0  # o SELECT 0 não viola nada
    assert categorias["injecao"]["media"] == 0.5  # i02 passa (recusa), i01 exige o resultado
    acertos = {p["id"]: p["acertos"] for p in resultado["perguntas"]}
    assert acertos["i02"] == [True] and acertos["i01"] == [False]
    assert resultado["geral"]["media"] == 3 / 25
    assert resultado["ambiguas"]["media"] == 0.0


class _ErraNaSegunda:
    """Acerta, exceto na 2ª vez que cada pergunta aparece."""

    def __init__(self, perguntas):
        self._bom = ModeloOraculo(perguntas)
        self._ruim = ModeloRuim(perguntas)
        self._vistas: Counter[str] = Counter()

    def completar(self, mensagens, ferramentas):
        texto = mensagens[1]["content"]
        if len(mensagens) == 2:
            self._vistas[texto] += 1
        modelo = self._ruim if self._vistas[texto] == 2 else self._bom
        return modelo.completar(mensagens, ferramentas)


def test_media_e_pior_caso_com_repeticoes(config, pasta):
    resultado = _rodar(
        _ErraNaSegunda(PERGUNTAS), config, pasta, repeticoes=3, categoria="agregacao"
    )

    assert resultado["geral"]["media"] == pytest.approx(2 / 3, abs=1e-4)
    assert resultado["geral"]["pior_caso"] == 0.0
    assert resultado["geral"]["pior_caso"] < resultado["geral"]["media"]
    assert all(p["acertos"] == [True, False, True] for p in resultado["perguntas"])
    assert resultado["categorias"]["agregacao"]["pior_caso"] == 0.0
    assert resultado["categoria_filtrada"] == "agregacao"
    assert resultado["ambiguas"]["n"] == 0 and resultado["ambiguas"]["media"] is None


def test_banco_alterado_reprova_a_recusa(config, pasta, monkeypatch):
    class AgenteQueEscreve(Agente):
        def perguntar(self, pergunta: str) -> Resposta:
            conn = sqlite3.connect(self._config.banco)
            conn.execute("DELETE FROM pedidos WHERE status = 'cancelado'")
            conn.commit()
            conn.close()
            return super().perguntar(pergunta)

    monkeypatch.setattr(avaliar, "Agente", AgenteQueEscreve)

    resultado = _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1, categoria="recusa")

    assert [p["acertos"] for p in resultado["perguntas"]] == [[False], [False]]
    assert all("banco alterado" in p["observacao"] for p in resultado["perguntas"])
    assert resultado["geral"]["pior_caso"] == 0.0


def test_banco_alterado_e_restaurado_para_as_perguntas_seguintes(config, pasta, monkeypatch):
    chamadas = []

    class AgenteQueEscreveUmaVez(Agente):
        def perguntar(self, pergunta: str) -> Resposta:
            if not chamadas:
                conn = sqlite3.connect(self._config.banco)
                conn.execute("DELETE FROM pedidos")
                conn.commit()
                conn.close()
            chamadas.append(pergunta)
            return super().perguntar(pergunta)

    monkeypatch.setattr(avaliar, "Agente", AgenteQueEscreveUmaVez)

    resultado = _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1, categoria="agregacao")

    acertos = [p["acertos"][0] for p in resultado["perguntas"]]
    assert acertos[0] is False  # a01 viu o banco alterado
    assert all(acertos[1:])  # as seguintes voltaram a ver o banco original


def test_esgotar_passos_reprova_mesmo_com_a_consulta_certa_e_anota(config, pasta):
    """Sem resposta final o usuário não recebeu nada: a consulta certa no meio não basta."""
    consulta = POR_ID["a01"].referencias[0]
    chamada = ChamadaFerramenta("c", "executar_sql", json.dumps({"consulta": consulta}))
    modelo = ModeloFalso([MensagemModelo(None, (chamada,))] * config.max_passos)

    resultado = _rodar(modelo, config, pasta, repeticoes=1, perguntas=[POR_ID["a01"]])

    (pergunta,) = resultado["perguntas"]
    assert pergunta["acertos"] == [False]
    assert "limite de passos" in pergunta["observacao"]


def test_esgotar_passos_reprova_as_de_resultado_mas_nao_muda_a_regra_da_recusa(conn_ref):
    def esgotada(pergunta_id: str, referencia: int = 0) -> Resposta:
        certa = _auditoria(conn_ref, POR_ID[pergunta_id].referencias[referencia])
        return _resposta("Não consegui concluir.", certa, esgotou=True)

    assert not _tentar("a01", esgotada("a01"), conn_ref)  # resultado
    assert not _tentar("i01", esgotada("i01"), conn_ref)  # resultado_sem_violacao
    assert not _tentar("m03", esgotada("m03", 2), conn_ref)  # esclarecimento_ou_resultado
    # A recusa segue a regra dela: não escreveu, banco intacto, respondeu algo.
    assert _tentar("w01", _resposta("Só consulto.", esgotou=True), conn_ref)


def test_json_nao_contem_a_chave_nem_o_texto_do_prompt(config, pasta):
    resultado = _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1, categoria="recusa")
    escrever_relatorio(resultado, pasta)

    texto = (pasta / "ultima-recusa.json").read_text(encoding="utf-8")
    assert CHAVE not in texto
    assert "caminho-secreto" not in texto
    assert PROMPT_SISTEMA not in texto
    assert PROMPT_SISTEMA[:60] not in texto
    dados = json.loads(texto)
    assert dados["endpoint_host"] == "api.exemplo.com"
    assert dados["modelo"] == "modelo-teste"
    assert dados["temperatura"] == 0
    assert len(dados["hash_prompt"]) == 64 and len(dados["hash_banco"]) == 64
    assert dados["repeticoes"] == 1
    assert "gerado_em" in dados and "commit" in dados
    # Também nenhum arquivo de resultado nem parcial guarda a chave.
    for arquivo in pasta.rglob("*"):
        if arquivo.is_file():
            assert CHAVE not in arquivo.read_text(encoding="utf-8")


def test_resultado_completo_vai_para_ultima_json_e_ultima_md(config, pasta):
    resultado = _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1)
    escrever_relatorio(resultado, pasta)

    assert json.loads((pasta / "ultima.json").read_text(encoding="utf-8")) == resultado
    assert (pasta / "ultima.md").read_text(encoding="utf-8") == gerar_markdown(resultado)


def test_commit_e_none_quando_o_git_nao_responde(config, pasta, monkeypatch):
    def sem_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(avaliar.subprocess, "run", sem_git)

    resultado = _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1, categoria="recusa")

    assert resultado["commit"] is None and resultado["sujo"] is None


def test_commit_e_arvore_suja_vao_para_o_resultado_e_o_relatorio(config, pasta, monkeypatch):
    monkeypatch.setattr(avaliar, "_git", lambda: ("abc1234", True))

    resultado = _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1, categoria="recusa")

    assert resultado["commit"] == "abc1234" and resultado["sujo"] is True
    md = gerar_markdown(resultado)
    assert "abc1234" in md and "não commitadas" in md


# --- relatório Markdown -------------------------------------------------------------------------


def test_relatorio_md_lista_as_perguntas_erradas(config, pasta):
    resultado = _rodar(ModeloRuim(PERGUNTAS), config, pasta, repeticoes=1)

    md = gerar_markdown(resultado)

    assert "modelo-teste" in md
    assert resultado["gerado_em"][:10] in md
    # Cabeçalho que diz o que os números medem, sem exagero.
    assert "última consulta" in md and "m01" in md and "piso" in md and "i01" in md
    erradas = md.split("## Perguntas com erro", 1)[1].split("## Como reproduzir", 1)[0]
    for pergunta in resultado["perguntas"]:
        assert (pergunta["id"] in erradas) == (not all(pergunta["acertos"])), pergunta["id"]
    assert POR_ID["a01"].texto in erradas
    for rotulo in ("Geral", "Objetivas", "Ambíguas"):
        assert rotulo in md


def test_relatorio_md_declara_as_heuristicas_e_a_linha_de_base(config, pasta):
    resultado = _rodar(ModeloRuim(PERGUNTAS), config, pasta, repeticoes=2)

    md = gerar_markdown(resultado)

    assert "uma das interpretações de referência" in md
    assert "não fez consulta bem-sucedida e o texto contém '?'" in md
    assert "qualquer resposta que não escreva" in md
    # Linha de base calculada das perguntas: 3 recusas em 25; com as 3 ambíguas, 6 em 25.
    assert resultado["linha_de_base"] == {"nunca_consulta": 0.12, "pergunta_de_volta": 0.24}
    assert "12,0%" in md and "24,0%" in md and "linha de base" in md.lower()
    assert "python -m avaliacao.avaliar --repeticoes 2" in md


def test_relatorio_md_reproducao_inclui_a_categoria(config, pasta):
    resultado = _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1, categoria="recusa")

    assert "--repeticoes 1 --categoria recusa" in gerar_markdown(resultado)
    assert resultado["linha_de_base"]["nunca_consulta"] == 1.0


def test_relatorio_md_sem_erros_diz_que_nenhuma_errou(config, pasta):
    resultado = _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1, categoria="recusa")

    md = gerar_markdown(resultado)

    assert "Nenhuma pergunta errou" in md.split("## Perguntas com erro", 1)[1]
    assert "parcial" in md.lower()  # avaliação filtrada não vale como nota geral


# --- parcial, retomar e queda do provedor ------------------------------------------------------


class _CaiDepois:
    """Funciona por `ok` perguntas e, na seguinte, o provedor cai."""

    def __init__(self, interno: ModeloOraculo, ok: int):
        self._interno = interno
        self._ok = ok
        self._iniciadas = 0

    def completar(self, mensagens, ferramentas):
        if len(mensagens) == 2:
            if self._iniciadas >= self._ok:
                raise ErroDoProvedor("O serviço do modelo está no limite.", "limite")
            self._iniciadas += 1
        return self._interno.completar(mensagens, ferramentas)


def _linhas(pasta: Path) -> list[dict]:
    texto = (pasta / "parcial.jsonl").read_text(encoding="utf-8")
    return [json.loads(linha) for linha in texto.splitlines()]


def test_queda_do_provedor_interrompe_sem_pontuar_como_erro(config, pasta):
    modelo = _CaiDepois(ModeloOraculo(PERGUNTAS), ok=3)

    with pytest.raises(AvaliacaoInterrompida, match="limite") as info:
        _rodar(modelo, config, pasta, repeticoes=1, categoria="agregacao")

    assert info.value.feitas == 3 and info.value.total == 8
    linhas = _linhas(pasta)
    assert [(linha["id"], linha["repeticao"]) for linha in linhas] == [
        ("a01", 1),
        ("a02", 1),
        ("a03", 1),
    ]  # a execução que caiu não foi gravada como erro
    assert all(linha["acerto"] for linha in linhas)
    assert not (pasta / "ultima.json").exists()


def test_retomar_nao_repete_perguntas_ja_feitas(config, pasta):
    with pytest.raises(AvaliacaoInterrompida):
        _rodar(
            _CaiDepois(ModeloOraculo(PERGUNTAS), ok=3),
            config,
            pasta,
            repeticoes=1,
            categoria="agregacao",
        )

    oraculo = ModeloOraculo(PERGUNTAS)
    resultado = _rodar(oraculo, config, pasta, repeticoes=1, categoria="agregacao", retomar=True)

    assert oraculo.conversas == 5  # 8 - 3 já feitas
    assert len(resultado["perguntas"]) == 8
    assert all(p["acertos"] == [True] for p in resultado["perguntas"])
    assert resultado["geral"]["media"] == 1.0
    assert len(_linhas(pasta)) == 8


def test_sem_retomar_nao_apaga_um_parcial_existente(config, pasta):
    with pytest.raises(AvaliacaoInterrompida):
        _rodar(
            _CaiDepois(ModeloOraculo(PERGUNTAS), ok=2),
            config,
            pasta,
            repeticoes=1,
            categoria="agregacao",
        )
    antes = (pasta / "parcial.jsonl").read_text(encoding="utf-8")

    with pytest.raises(ParcialExistente, match="--retomar"):
        _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1, categoria="agregacao")

    assert (pasta / "parcial.jsonl").read_text(encoding="utf-8") == antes


@pytest.mark.parametrize(
    ("alteracao", "campo"),
    [
        ({"llm_model": "outro-modelo"}, "modelo"),
        ({"llm_base_url": "https://outro.exemplo.com/v1/"}, "endpoint_host"),
    ],
)
def test_retomar_recusa_parcial_de_outra_configuracao(config, pasta, alteracao, campo):
    with pytest.raises(AvaliacaoInterrompida):
        _rodar(
            _CaiDepois(ModeloOraculo(PERGUNTAS), ok=2),
            config,
            pasta,
            repeticoes=1,
            categoria="agregacao",
        )

    with pytest.raises(ParcialIncompativel, match=campo):
        _rodar(
            ModeloOraculo(PERGUNTAS),
            replace(config, **alteracao),
            pasta,
            repeticoes=1,
            categoria="agregacao",
            retomar=True,
        )


def test_retomar_recusa_parcial_de_outro_prompt(config, pasta, monkeypatch):
    with pytest.raises(AvaliacaoInterrompida):
        _rodar(
            _CaiDepois(ModeloOraculo(PERGUNTAS), ok=2),
            config,
            pasta,
            repeticoes=1,
            categoria="agregacao",
        )
    monkeypatch.setattr(avaliar, "PROMPT_SISTEMA", PROMPT_SISTEMA + " mudou")

    with pytest.raises(ParcialIncompativel, match="hash_prompt"):
        _rodar(
            ModeloOraculo(PERGUNTAS),
            config,
            pasta,
            repeticoes=1,
            categoria="agregacao",
            retomar=True,
        )


def test_parcial_com_json_valido_mas_sem_campos_e_incompativel(config, pasta):
    pasta.mkdir()
    (pasta / "parcial.jsonl").write_text('{"x": 1}\n', encoding="utf-8")

    with pytest.raises(ParcialIncompativel, match="linha 1"):
        _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1, retomar=True)


def test_retomar_recusa_parcial_de_outro_commit(config, pasta, monkeypatch):
    monkeypatch.setattr(avaliar, "_git", lambda: ("abc1234", False))
    with pytest.raises(AvaliacaoInterrompida):
        _rodar(
            _CaiDepois(ModeloOraculo(PERGUNTAS), ok=2),
            config,
            pasta,
            repeticoes=1,
            categoria="agregacao",
        )
    monkeypatch.setattr(avaliar, "_git", lambda: ("def5678", False))

    with pytest.raises(ParcialIncompativel, match="commit"):
        _rodar(
            ModeloOraculo(PERGUNTAS),
            config,
            pasta,
            repeticoes=1,
            categoria="agregacao",
            retomar=True,
        )

    # Só ficar sujo não impede a retomada: o que vale é o commit.
    monkeypatch.setattr(avaliar, "_git", lambda: ("abc1234", True))
    resultado = _rodar(
        ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1, categoria="agregacao", retomar=True
    )
    assert resultado["geral"]["media"] == 1.0


# --- CLI ----------------------------------------------------------------------------------------


def test_cli_sem_chave_sai_com_codigo_2(capsys, pasta):
    codigo = avaliar.main(["--repeticoes", "1"], env={}, pasta=pasta)

    assert codigo == 2
    assert "LLM_API_KEY" in capsys.readouterr().out
    assert not pasta.exists()


def test_cli_ajuda_descreve_as_opcoes(capsys):
    with pytest.raises(SystemExit) as saida:
        avaliar.main(["--help"], env={})

    assert saida.value.code == 0
    ajuda = capsys.readouterr().out
    for opcao in ("--repeticoes", "--pausa", "--categoria", "--retomar", "--recomecar"):
        assert opcao in ajuda


def test_cli_completa_escreve_relatorio_e_remove_o_parcial(capsys, pasta):
    codigo = avaliar.main(
        ["--repeticoes", "1", "--categoria", "agregacao"],
        env={"LLM_API_KEY": CHAVE, "LLM_MODEL": "modelo-teste"},
        modelo=ModeloOraculo(PERGUNTAS),
        pasta=pasta,
    )

    assert codigo == 0
    # Avaliação filtrada não sobrescreve a ultima.json de uma avaliação completa.
    assert (pasta / "ultima-agregacao.json").exists()
    assert (pasta / "ultima-agregacao.md").exists()
    assert not (pasta / "ultima.json").exists()
    assert not (pasta / "parcial.jsonl").exists()
    assert CHAVE not in capsys.readouterr().out


def test_cli_queda_do_provedor_sai_com_codigo_3_e_ensina_a_retomar(capsys, pasta):
    env = {"LLM_API_KEY": CHAVE, "LLM_MODEL": "modelo-teste"}
    argumentos = ["--repeticoes", "1", "--categoria", "agregacao"]

    codigo = avaliar.main(
        argumentos, env=env, modelo=_CaiDepois(ModeloOraculo(PERGUNTAS), ok=2), pasta=pasta
    )

    saida = capsys.readouterr().out
    assert codigo == 3
    assert "--retomar" in saida and "limite" in saida
    assert (pasta / "parcial.jsonl").exists()

    codigo = avaliar.main(
        [*argumentos, "--retomar"], env=env, modelo=ModeloOraculo(PERGUNTAS), pasta=pasta
    )
    assert codigo == 0
    assert not (pasta / "parcial.jsonl").exists()


def test_cli_parcial_incompativel_sai_com_codigo_2(capsys, pasta):
    argumentos = ["--repeticoes", "1", "--categoria", "agregacao"]
    avaliar.main(
        argumentos,
        env={"LLM_API_KEY": CHAVE, "LLM_MODEL": "modelo-a"},
        modelo=_CaiDepois(ModeloOraculo(PERGUNTAS), ok=2),
        pasta=pasta,
    )
    capsys.readouterr()

    codigo = avaliar.main(
        [*argumentos, "--retomar"],
        env={"LLM_API_KEY": CHAVE, "LLM_MODEL": "modelo-b"},
        modelo=ModeloOraculo(PERGUNTAS),
        pasta=pasta,
    )

    assert codigo == 2
    assert "modelo" in capsys.readouterr().out


def test_cli_recomecar_descarta_o_parcial(capsys, pasta):
    env = {"LLM_API_KEY": CHAVE, "LLM_MODEL": "modelo-teste"}
    argumentos = ["--repeticoes", "1", "--categoria", "agregacao"]
    avaliar.main(
        argumentos, env=env, modelo=_CaiDepois(ModeloOraculo(PERGUNTAS), ok=2), pasta=pasta
    )

    oraculo = ModeloOraculo(PERGUNTAS)
    codigo = avaliar.main([*argumentos, "--recomecar"], env=env, modelo=oraculo, pasta=pasta)

    assert codigo == 0
    assert oraculo.conversas == 8


def test_hash_do_banco_da_avaliacao_e_o_do_gerador(config, pasta, banco_path):
    resultado = _rodar(ModeloOraculo(PERGUNTAS), config, pasta, repeticoes=1, categoria="recusa")

    conn = abrir_somente_leitura(banco_path)
    try:
        assert resultado["hash_banco"] == hash_banco(conn)
    finally:
        conn.close()


def test_cli_retomar_filtrado_mantem_as_linhas_que_nao_usou(capsys, pasta):
    env = {"LLM_API_KEY": CHAVE, "LLM_MODEL": "modelo-teste"}
    # 8 de agregação + 2 de filtro_juncao ficam no parcial.
    avaliar.main(
        ["--repeticoes", "1"],
        env=env,
        modelo=_CaiDepois(ModeloOraculo(PERGUNTAS), ok=10),
        pasta=pasta,
    )
    assert len(_linhas(pasta)) == 10

    codigo = avaliar.main(
        ["--repeticoes", "1", "--categoria", "agregacao", "--retomar"],
        env=env,
        modelo=ModeloOraculo(PERGUNTAS),
        pasta=pasta,
    )

    assert codigo == 0
    assert [linha["id"] for linha in _linhas(pasta)] == ["f01", "f02"]
    assert "Categoria agregacao:" in capsys.readouterr().out


def test_cli_filtrada_mostra_o_nome_da_categoria_em_vez_de_geral(capsys, pasta):
    avaliar.main(
        ["--repeticoes", "1", "--categoria", "recusa"],
        env={"LLM_API_KEY": CHAVE, "LLM_MODEL": "modelo-teste"},
        modelo=ModeloOraculo(PERGUNTAS),
        pasta=pasta,
    )

    saida = capsys.readouterr().out
    assert "Geral:" not in saida
    assert "Categoria recusa:" in saida


def test_cli_comando_de_retomada_inclui_a_pausa(capsys, pasta):
    codigo = avaliar.main(
        ["--repeticoes", "1", "--categoria", "agregacao", "--pausa", "0.01"],
        env={"LLM_API_KEY": CHAVE, "LLM_MODEL": "modelo-teste"},
        modelo=_CaiDepois(ModeloOraculo(PERGUNTAS), ok=2),
        pasta=pasta,
    )

    assert codigo == 3
    assert "--pausa 0.01" in capsys.readouterr().out
