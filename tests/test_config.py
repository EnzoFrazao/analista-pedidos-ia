from pathlib import Path

import dotenv
import pytest

from analista import config
from analista.config import RAIZ, Configuracao, carregar_configuracao, host_do_endereco


def test_padroes():
    cfg = carregar_configuracao({})

    assert cfg.llm_base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert cfg.llm_model == "gemini-3.5-flash-lite"
    assert cfg.llm_api_key is None
    assert cfg.banco == RAIZ / "data" / "pedidos.db"
    assert cfg.pasta_runs == RAIZ / "runs"
    assert cfg.estado_limites == RAIZ / "runs" / "limite_dia.json"
    assert cfg.avaliacao == RAIZ / "avaliacao" / "resultados" / "ultima.json"
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


def test_sem_mapeamento_le_o_env_da_raiz_e_o_ambiente_do_processo(monkeypatch):
    lidos = []
    monkeypatch.setattr(config, "load_dotenv", lambda caminho, **kw: lidos.append(caminho))
    monkeypatch.setenv("LLM_MODEL", "modelo-do-ambiente")

    assert carregar_configuracao().llm_model == "modelo-do-ambiente"
    assert lidos == [RAIZ / ".env"]


def test_caminho_da_avaliacao_pode_ser_sobrescrito(tmp_path: Path):
    cfg = carregar_configuracao({"AVALIACAO_PATH": str(tmp_path / "outra.json")})

    assert cfg.avaliacao == tmp_path / "outra.json"


@pytest.mark.parametrize("colado", ['"abc123"', "'abc123'", ' "abc123"\n', "\"'abc123'\""])
def test_aspas_sobrando_em_volta_da_chave_e_do_modelo_sao_tiradas(colado):
    """O Gemini responde 400 a chave ou modelo com aspas; o painel de segredos pode trazê-las."""
    cfg = carregar_configuracao({"LLM_API_KEY": colado, "LLM_MODEL": colado})

    assert cfg.llm_api_key == "abc123"
    assert cfg.llm_model == "abc123"


def test_aspas_no_meio_do_valor_ficam():
    cfg = carregar_configuracao({"LLM_API_KEY": 'abc"123'})

    assert cfg.llm_api_key == 'abc"123'


def test_endereco_e_modelo_sem_espacos_nem_quebra_de_linha():
    """Valor colado no painel de segredos costuma trazer espaço ou quebra de linha no fim."""
    cfg = carregar_configuracao(
        {"LLM_BASE_URL": "  https://api.groq.com/openai/v1\n", "LLM_MODEL": "\tmodelo-x \r\n"}
    )

    assert cfg.llm_base_url == "https://api.groq.com/openai/v1"
    assert cfg.llm_model == "modelo-x"


def test_endereco_ou_modelo_so_com_espacos_usam_o_padrao():
    cfg = carregar_configuracao({"LLM_BASE_URL": "  ", "LLM_MODEL": "\n"})

    assert cfg.llm_base_url == Configuracao().llm_base_url
    assert cfg.llm_model == Configuracao().llm_model


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://usuario:senha@api.exemplo.com:8443/v1/caminho?key=segredo", "api.exemplo.com"),
        (
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            "generativelanguage.googleapis.com",
        ),
        ("isto não é endereço", None),
    ],
)
def test_host_do_endereco_tira_usuario_porta_caminho_e_parametros(url, host):
    assert host_do_endereco(url) == host


def test_os_testes_nunca_leem_o_env_do_desenvolvedor():
    """O conftest troca `load_dotenv` por uma função que não lê nada (o `.env` real de quem roda
    os testes pode ter a chave)."""
    assert config.load_dotenv is not dotenv.load_dotenv
    assert config.load_dotenv(RAIZ / ".env", encoding="utf-8") is False
