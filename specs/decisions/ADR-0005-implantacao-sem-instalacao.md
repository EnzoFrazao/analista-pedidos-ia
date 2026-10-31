---
id: ADR-0005
status: accepted
date: 2026-10-30
---

# ADR-0005 — Painel roda do repositório, sem instalar o projeto

Substitui o ADR-0003.

## Contexto

`RAIZ`, em `analista/config.py`, é calculada a partir do arquivo do código e define onde ficam `data/`,
`runs/`, `avaliacao/resultados/` e o `.env`. O Streamlit põe no `sys.path` só a pasta do script
(`app/`), não a raiz do projeto. O ADR-0003 resolvia as duas coisas com `-e .[painel]` no
`requirements.txt`, mas o build gerado por serviços como o Hugging Face Spaces costuma instalar o
`requirements.txt` antes de copiar o repositório, quando ainda não existe `pyproject.toml` para o `-e .`.

## Decisão

O projeto não é instalado no deploy. `app/painel.py` põe `Path(__file__).resolve().parents[1]` (a raiz
do repositório) na frente do `sys.path` antes de importar `analista` e `avaliacao`, então o código e
`RAIZ` vêm sempre da cópia do repositório. O `requirements.txt` lista só as dependências de execução
(`openai`, `python-dotenv`, `pyyaml`, `streamlit`) com as mesmas faixas do `pyproject.toml`
(`dependencies` mais o extra `painel`), e um teste confere que as duas listas batem. Para desenvolver
continua valendo `pip install -e ".[dev,painel]"`.

## Alternativas consideradas

- Manter `-e .[painel]` (ADR-0003): rejeitada, depende da ordem do build do serviço.
- `Dockerfile` próprio que copia o projeto e só depois instala: rejeitada por ora, mais uma peça para
  manter; continua possível se o serviço exigir.
- Caminhos relativos ao diretório de trabalho: rejeitada, mudam conforme de onde o comando roda.

## Consequências

O painel só funciona rodando o arquivo de dentro do repositório (`streamlit run app/painel.py`), não
como pacote instalado. As faixas de versão ficam escritas em dois arquivos; o teste
`test_requirements_lista_as_dependencias_de_execucao_com_as_faixas_do_pyproject` impede que divirjam.
O build num serviço real ainda não foi feito (`P-004`).

## Evidência

- Implementação: `app/painel.py`, `requirements.txt`, `pyproject.toml`.
- Testes: `python -m pytest tests/test_painel.py -q -k "requirements or sys_path"`.
- Conferido à mão em 2026-10-30: com `python -S` e só `site-packages` no caminho (sem o `.pth` da
  instalação editável), `analista` não é importável fora do painel e o `AppTest` abre o painel com as
  três abas, sem exceção.
