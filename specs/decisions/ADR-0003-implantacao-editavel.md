---
id: ADR-0003
status: superseded
date: 2026-10-30
---

# ADR-0003 — Painel instalado em modo editável

Substituído pelo ADR-0005 (`ADR-0005-implantacao-sem-instalacao.md`): o painel põe a raiz do
repositório no `sys.path` e o `requirements.txt` lista as dependências reais, sem `-e`.

## Contexto

`RAIZ`, em `analista/config.py`, é calculada a partir do arquivo do código e define onde ficam `data/`,
`runs/`, `avaliacao/resultados/` e o `.env`. Numa instalação comum o código vai para `site-packages` e
esses caminhos passariam a apontar para o lugar errado. O Streamlit também põe no `sys.path` a pasta do
script, não a raiz do projeto.

## Decisão

O `requirements.txt` tem uma linha, `-e .[painel]`, de modo que a hospedagem instale o projeto em modo
editável e `RAIZ` continue sendo o repositório. Quem quiser mudar os caminhos usa `BANCO_PATH`,
`RUNS_PATH` e `AVALIACAO_PATH`.

## Alternativas consideradas

- Caminhos relativos ao diretório de trabalho: rejeitada, mudam conforme de onde o comando roda.
- Instalar o pacote sem modo editável e exigir variáveis de ambiente para os caminhos: rejeitada, mais
  uma coisa para configurar no serviço.

## Consequências

Uma hospedagem que não aceite `-e` no `requirements.txt` ou que o instale antes de copiar o projeto
precisa de outra forma de instalar (por exemplo um `Dockerfile`). Isso não foi testado em nenhum serviço;
ver `P-004` em `../open-decisions.md`.

## Evidência

- Implementação: `requirements.txt`, `analista/config.py`.
- Testes: `python -m pytest tests/test_painel.py -q -k requirements`.
