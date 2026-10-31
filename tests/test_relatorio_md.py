"""Textos públicos do relatório da avaliação: o painel os reaproveita em vez de reescrevê-los."""

from avaliacao.relatorio_md import (
    LIMITES_DA_MEDICAO,
    descricao_do_commit,
    gerar_markdown,
    texto_linha_de_base,
    texto_media_e_pior_caso,
    texto_o_que_medem,
    texto_origem,
)


def _grupo(media, pior, n):
    return {"media": media, "pior_caso": pior, "n": n}


def _completo() -> dict:
    return {
        "gerado_em": "2026-11-02T10:00:00-03:00",
        "commit": "abc1234",
        "sujo": False,
        "modelo": "modelo-de-teste",
        "endpoint_host": "exemplo.test",
        "temperatura": 0,
        "max_passos": 6,
        "hash_prompt": "p",
        "hash_banco": "b",
        "hash_perguntas": "q",
        "repeticoes": 3,
        "categoria_filtrada": None,
        "linha_de_base": {"nunca_consulta": 0.12, "pergunta_de_volta": 0.24},
        "geral": _grupo(0.8, 0.7, 25),
        "objetivas": _grupo(0.9, 0.8, 22),
        "ambiguas": _grupo(0.5, 0.3, 3),
        "categorias": {"agregacao": _grupo(0.9, 0.8, 8)},
        "perguntas": [],
    }


def _minimo(repeticoes: int = 3) -> dict:
    """Só o que as frases de abertura usam: o resto (hashes, endpoint, perguntas) pode faltar."""
    return {
        "gerado_em": "2026-11-02T10:00:00-03:00",
        "modelo": "modelo-de-teste",
        "repeticoes": repeticoes,
        "linha_de_base": {"nunca_consulta": 0.12, "pergunta_de_volta": 0.24},
    }


def test_linha_de_base_usa_as_duas_taxas_em_pt_br():
    texto = texto_linha_de_base(_minimo())

    assert "12,0%" in texto and "24,0%" in texto and "Linha de base" in texto


def test_origem_concorda_no_singular_e_no_plural():
    assert "1 repetição por pergunta" in texto_origem(_minimo(1))
    assert "3 repetições por pergunta" in texto_origem(_minimo(3))
    assert "modelo-de-teste" in texto_origem(_minimo())
    assert "2026-11-02" in texto_origem(_minimo())


def test_commit_ausente_ou_sujo_e_descrito_sem_quebrar():
    assert descricao_do_commit({}) == "indisponível"
    assert descricao_do_commit({"commit": "abc1234", "sujo": None}) == "`abc1234`"
    assert "não commitadas" in descricao_do_commit({"commit": "abc1234", "sujo": True})


def test_o_que_medem_e_os_limites_dizem_o_que_nao_se_mede():
    assert "última consulta SQL" in texto_o_que_medem()
    assert "não" in texto_o_que_medem()
    juntos = " ".join(LIMITES_DA_MEDICAO)
    assert "piso" in juntos and "w01" in juntos and "i01" in juntos
    assert "pior caso" in texto_media_e_pior_caso()


def test_o_relatorio_em_markdown_usa_as_mesmas_frases():
    resultado = _completo()

    md = gerar_markdown(resultado)

    assert texto_o_que_medem() in md
    assert texto_linha_de_base(resultado) in md
    assert texto_origem(resultado) in md
    assert texto_media_e_pior_caso() in md
    for limite in LIMITES_DA_MEDICAO:
        assert f"- {limite}" in md


def test_limites_dizem_que_passos_esgotados_e_resultado_cortado_contam_como_erro():
    juntos = " ".join(LIMITES_DA_MEDICAO)

    assert "limite de passos" in juntos
    assert "cortado" in juntos
