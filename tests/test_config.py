from pathlib import Path

import pytest

from analista.config import RAIZ, Configuracao, carregar_configuracao


def test_padroes():
    cfg = carregar_configuracao({})

    assert cfg.llm_base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert cfg.llm_model == "gemini-3.5-flash-lite"
    assert cfg.llm_api_key is None
    assert cfg.banco == RAIZ / "data" / "pedidos.db"
    assert cfg.pasta_runs == RAIZ / "runs"
    assert cfg.estado_limites == RAIZ / "runs" / "limite_dia.json"
    assert cfg.max_passos == 6
    assert cfg.limite_linhas == 50
    assert cfg.limite_sessao == 10
    assert cfg.limite_dia == 200
    assert cfg.timeout_sql_s == 2.0


def test_variaveis_de_ambiente_sobrescrevem(tmp_path: Path):
    env = {
        "LLM_BASE_URL": "https://api.groq.com/openai/v1",
        "LLM_MODEL": "openai/gpt-oss-120b",
        "LLM_API_KEY": "segredo",
        "BANCO_PATH": str(tmp_path / "outro.db"),
        "LIMITE_SESSAO": "3",
        "LIMITE_DIA": "40",
    }

    cfg = carregar_configuracao(env)

    assert cfg.llm_base_url == "https://api.groq.com/openai/v1"
    assert cfg.llm_model == "openai/gpt-oss-120b"
    assert cfg.llm_api_key == "segredo"
    assert cfg.banco == tmp_path / "outro.db"
    assert cfg.limite_sessao == 3
    assert cfg.limite_dia == 40


def test_pasta_runs_pode_ser_sobrescrita(tmp_path: Path):
    cfg = carregar_configuracao({"RUNS_PATH": str(tmp_path / "execucoes")})

    assert cfg.pasta_runs == tmp_path / "execucoes"
    assert cfg.estado_limites == tmp_path / "execucoes" / "limite_dia.json"


@pytest.mark.parametrize("valor", [None, "", "   "])
def test_chave_ausente_ou_vazia_vira_none(valor):
    env = {} if valor is None else {"LLM_API_KEY": valor}

    assert carregar_configuracao(env).llm_api_key is None


def test_repr_nao_mostra_a_chave():
    cfg = carregar_configuracao({"LLM_API_KEY": "segredo"})

    assert "segredo" not in repr(cfg)


def test_banco_padrao_independe_do_diretorio_atual(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    cfg = carregar_configuracao({})

    assert cfg.banco.is_absolute()
    assert cfg.banco.is_relative_to(RAIZ)


def test_configuracao_e_congelada():
    cfg = carregar_configuracao({})

    with pytest.raises(AttributeError):
        cfg.max_passos = 99  # type: ignore[misc]
    assert isinstance(cfg, Configuracao)


def test_sem_mapeamento_le_o_ambiente_do_processo(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "modelo-do-ambiente")

    assert carregar_configuracao().llm_model == "modelo-do-ambiente"
