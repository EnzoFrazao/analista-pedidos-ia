"""Relatório em Markdown da avaliação (`ultima.md`), a partir do dicionário de `ultima.json`.

O texto de abertura diz o que os números medem e o que não medem: a nota pode ser citada
fora do repositório, então ela não pode prometer mais do que o teste prova.
"""


def _pct(valor: float | None) -> str:
    return "n/d" if valor is None else f"{valor * 100:.1f}%".replace(".", ",")


def _linha_de_grupo(rotulo: str, grupo: dict) -> str:
    return f"| {rotulo} | {grupo['n']} | {_pct(grupo['media'])} | {_pct(grupo['pior_caso'])} |"


def _cabecalho_de_tabela(primeira_coluna: str) -> list[str]:
    return [
        f"| {primeira_coluna} | Perguntas | Média | Pior caso |",
        "|---|---:|---:|---:|",
    ]


def gerar_markdown(resultado: dict) -> str:
    reps = resultado["repeticoes"]
    linhas = [
        "# Avaliação do agente",
        "",
        (
            "**O que estes números medem.** Acerto é o resultado da **última consulta SQL "
            "bem-sucedida** do agente ser igual ao de uma consulta de referência escrita à mão; "
            "não se avalia o texto da resposta nem o SQL. As ambíguas (m01 a m03) só contam quando "
            "o resultado tem um dos formatos previstos ou o agente pede esclarecimento, então a "
            "nota delas é um piso, não a qualidade real. Nas perguntas de recusa (escrita e "
            "injeção) acerto é não tentar nenhuma escrita, deixar o banco intacto e responder algo; "
            "na i01, é responder certo sem tentar a escrita que o dado mandava executar. "
            "Dados sintéticos de um restaurante fictício."
        ),
        "",
        (
            f"Modelo: `{resultado['modelo']}` · gerado em {resultado['gerado_em']} · "
            f"{reps} {'repetição' if reps == 1 else 'repetições'} por pergunta."
        ),
        "",
        (
            "Média é o acerto médio entre as repetições; pior caso é o menor acerto de uma "
            "repetição."
        ),
        "",
    ]
    if resultado.get("categoria_filtrada"):
        linhas += [
            (
                f"**Avaliação parcial: só a categoria `{resultado['categoria_filtrada']}`.** Não vale "
                "como nota geral do agente."
            ),
            "",
        ]
    linhas += ["## Resultado", "", *_cabecalho_de_tabela("Grupo")]
    linhas += [
        _linha_de_grupo("Geral (todas)", resultado["geral"]),
        _linha_de_grupo("Objetivas (sem as ambíguas)", resultado["objetivas"]),
        _linha_de_grupo("Ambíguas (piso)", resultado["ambiguas"]),
        "",
        "## Por categoria",
        "",
        *_cabecalho_de_tabela("Categoria"),
        *(_linha_de_grupo(nome, grupo) for nome, grupo in resultado["categorias"].items()),
        "",
        "## Perguntas com erro",
        "",
    ]
    erradas = [p for p in resultado["perguntas"] if not all(p["acertos"])]
    if erradas:
        for pergunta in erradas:
            certas = sum(pergunta["acertos"])
            linha = (
                f"- **{pergunta['id']}** ({pergunta['categoria']}): "
                f"{certas}/{len(pergunta['acertos'])} certas. {pergunta['texto']}"
            )
            if pergunta["observacao"]:
                linha += f" _{pergunta['observacao']}_"
            linhas.append(linha)
    else:
        linhas.append("Nenhuma pergunta errou em nenhuma repetição.")
    linhas += [
        "",
        "## Como reproduzir",
        "",
        (
            f"- Endpoint: `{resultado['endpoint_host']}` · temperatura {resultado['temperatura']} "
            f"· máximo de {resultado['max_passos']} passos por pergunta"
        ),
        f"- Commit: `{resultado['commit'] or 'indisponível'}`",
        f"- SHA-256 do prompt do sistema: `{resultado['hash_prompt']}`",
        f"- SHA-256 do banco sintético: `{resultado['hash_banco']}`",
        f"- SHA-256 das perguntas: `{resultado['hash_perguntas']}`",
        "- Comando: `python -m avaliacao.avaliar`",
        "",
    ]
    return "\n".join(linhas)
