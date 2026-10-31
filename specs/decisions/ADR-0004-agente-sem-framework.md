---
id: ADR-0004
status: accepted
date: 2026-10-30
---

# ADR-0004 — Agente em Python puro, sem framework

## Contexto

O projeto é uma demonstração e precisa ser explicado linha a linha. O problema
usa duas ferramentas e uma conversa curta com um único agente.

## Decisão

O agente é um laço de tool calling em `analista/agente.py` (`Agente.perguntar`), com no máximo 6 passos e
duas ferramentas (`descrever_esquema`, `executar_sql`). O SDK `openai` fica isolado em `analista/llm.py`
atrás da interface `ModeloLLM`; o restante do código não o conhece, e os testes usam um modelo falso.

## Alternativas consideradas

- Framework de agentes (grafos, memória, vários agentes): rejeitada, acrescenta abstrações que o problema
  não pede e esconde o que a demonstração quer mostrar.
- Chamar o SDK direto de cada módulo: rejeitada, trocar de provedor ou de SDK espalharia mudanças.

## Consequências

O código de laço, de tentativas em erro 429 e de adaptação a provedores compatíveis é nosso e precisa de
manutenção. Se o problema crescer para vários agentes ou fluxos longos, a decisão deve ser reavaliada.

## Evidência

- Implementação: `analista/agente.py`, `analista/llm.py`.
- Testes: `python -m pytest tests/test_agente.py tests/test_llm.py -q`.
