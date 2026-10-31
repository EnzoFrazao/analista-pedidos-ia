---
id: ADR-0001
status: accepted
date: 2026-10-30
---

# ADR-0001 — SQL do modelo em camadas, com authorizer de lista permitida

## Contexto

O modelo escreve SQL que roda sobre o banco de pedidos. O prompt manda recusar escrita, mas um prompt
não é garantia: o modelo pode errar ou ser induzido por texto vindo dos dados. A defesa precisa valer
mesmo que o modelo obedeça a uma instrução maliciosa, e precisa falhar fechada.

## Decisão

Cada consulta passa por camadas independentes em `analista/sql_seguro.py`: conexão `mode=ro` com
`PRAGMA query_only=ON`; uma única instrução começando por `SELECT` ou `WITH`; *authorizer* do SQLite que
aprova só uma lista de ações, de tabelas (`TABELAS_PERMITIDAS`) e de funções (`FUNCOES_PERMITIDAS`);
teto de linhas imposto com `fetchmany` (a consulta não precisa conter `LIMIT`); *progress handler* de 2 s
e limites do SQLite para o tamanho de valor e de padrão `LIKE`, que contêm funções de custo quadrático
dentro de um único passo. O estado do authorizer e do prazo é uma instância por chamada, e cada pergunta
usa a sua conexão.

## Alternativas consideradas

- Só o prompt: rejeitada, não é garantia.
- Validar o texto por expressão regular ou lista de palavras: rejeitada, comentários, caixa e
  subconsultas escapam.
- Lista de bloqueio de funções: rejeitada, falha aberta quando esquece uma. A lista permitida falha
  fechada.
- Exigir `LIMIT` no texto: rejeitada, depende do modelo; o teto na execução é garantia por construção.
- Authorizer e prazo em variável de módulo: rejeitada, quebra com sessões em threads.

## Consequências

O modelo às vezes pede uma função útil que não foi liberada e recebe um erro para corrigir; a lista já
foi ampliada uma vez (`LIKE`, funções de janela, `time`) e `glob` e `printf` continuam fora. A existência
de uma tabela pode ser sondada pelo tipo do erro, e o consumo de memória de ordenações e agrupamentos
enormes só é contido pelo tempo. Um trecho de texto ou blob com mais de 10 mil bytes gera erro.

## Evidência

- Implementação: `analista/sql_seguro.py` (`abrir_somente_leitura`, `executar_consulta`).
- Testes: `python -m pytest tests/test_sql_seguro.py -q`.
