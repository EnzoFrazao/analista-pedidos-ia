import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from analista import limites
from analista.limites import LimiteDeUso


class Relogio:
    def __init__(self, dia: date) -> None:
        self.dia = dia

    def __call__(self) -> date:
        return self.dia


@pytest.fixture
def relogio() -> Relogio:
    return Relogio(date(2026, 10, 30))


@pytest.fixture
def estado(tmp_path: Path) -> Path:
    return tmp_path / "runs" / "limite_dia.json"


def test_libera_ate_o_limite_da_sessao_e_bloqueia_na_11a(estado, relogio):
    limite = LimiteDeUso(10, 200, estado, relogio)

    for feitas in range(10):
        assert limite.autorizar(feitas) == (True, "")

    liberado, mensagem = limite.autorizar(10)
    assert liberado is False
    assert "10" in mensagem
    assert "sessão" in mensagem
    assert "diário" not in mensagem


def test_bloqueia_ao_atingir_o_teto_diario(estado, relogio):
    limite = LimiteDeUso(10, 3, estado, relogio)

    for _ in range(2):
        limite.registrar()
    assert limite.autorizar(0) == (True, "")

    limite.registrar()
    liberado, mensagem = limite.autorizar(0)
    assert liberado is False
    assert "diário" in mensagem
    assert "amanhã" in mensagem


def test_contador_zera_no_dia_seguinte(estado, relogio):
    limite = LimiteDeUso(10, 2, estado, relogio)
    limite.registrar()
    limite.registrar()
    assert limite.autorizar(0)[0] is False

    relogio.dia += timedelta(days=1)
    assert limite.autorizar(0) == (True, "")

    limite.registrar()
    dados = json.loads(estado.read_text(encoding="utf-8"))
    assert dados == {"data": "2026-10-31", "n": 1}


def test_registrar_cria_a_pasta_e_persiste_entre_instancias(estado, relogio):
    LimiteDeUso(10, 200, estado, relogio).registrar()
    LimiteDeUso(10, 200, estado, relogio).registrar()

    assert json.loads(estado.read_text(encoding="utf-8")) == {"data": "2026-10-30", "n": 2}
    assert [p.name for p in estado.parent.iterdir()] == ["limite_dia.json"]


def test_sessao_estourada_tem_prioridade_sobre_o_dia(estado, relogio):
    limite = LimiteDeUso(2, 1, estado, relogio)
    limite.registrar()

    liberado, mensagem = limite.autorizar(2)

    assert liberado is False
    assert "sessão" in mensagem


@pytest.mark.parametrize(
    "conteudo",
    [
        "{isto não é json",
        "",
        "[1, 2]",
        '{"data": "2026-10-30"}',
        '{"n": 5}',
        '{"data": "2026-10-30", "n": "muitos"}',
        '{"data": "2026-10-30", "n": -4}',
        '{"data": "2026-10-30", "n": true}',
        '{"data": 20261030, "n": 5}',
        '{"data": "ontem", "n": 5}',
    ],
)
def test_estado_corrompido_recomeca_em_zero_sem_excecao(estado, relogio, conteudo):
    estado.parent.mkdir(parents=True)
    estado.write_text(conteudo, encoding="utf-8")
    limite = LimiteDeUso(10, 1, estado, relogio)

    assert limite.autorizar(0) == (True, "")

    limite.registrar()
    assert json.loads(estado.read_text(encoding="utf-8")) == {"data": "2026-10-30", "n": 1}


def test_estado_ilegivel_nao_derruba_a_pergunta(tmp_path, relogio):
    # `estado` apontando para uma pasta: não dá para ler nem gravar.
    pasta = tmp_path / "limite_dia.json"
    pasta.mkdir()
    limite = LimiteDeUso(10, 200, pasta, relogio)

    assert limite.autorizar(0) == (True, "")
    limite.registrar()  # não levanta
    assert pasta.is_dir()


def test_registrar_tenta_de_novo_quando_o_windows_segura_o_arquivo(estado, relogio, monkeypatch):
    chamadas = []
    real = limites.os.replace

    def replace_instavel(origem, destino):
        chamadas.append(1)
        if len(chamadas) < 3:
            raise PermissionError("em uso por outro processo")
        real(origem, destino)

    monkeypatch.setattr(limites.os, "replace", replace_instavel)
    monkeypatch.setattr(limites.time, "sleep", lambda _s: None)

    LimiteDeUso(10, 200, estado, relogio).registrar()

    assert len(chamadas) == 3
    assert json.loads(estado.read_text(encoding="utf-8"))["n"] == 1


def test_registrar_desiste_sem_excecao_e_sem_deixar_temporario(estado, relogio, monkeypatch):
    def replace_sempre_falha(_origem, _destino):
        raise PermissionError("em uso por outro processo")

    monkeypatch.setattr(limites.os, "replace", replace_sempre_falha)
    monkeypatch.setattr(limites.time, "sleep", lambda _s: None)

    LimiteDeUso(10, 200, estado, relogio).registrar()  # não levanta

    assert list(estado.parent.iterdir()) == []
