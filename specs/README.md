# Memória técnica do projeto

Este diretório contém o contrato necessário para compreender e modificar o software.

## Ordem de leitura

1. Leia este índice.
2. Abra somente a capability relacionada.
3. Consulte `system.md` para confirmar o implementado.
4. Consulte `testing.md` antes de alterar cobertura.
5. Leia ADRs e decisões abertas citados.
6. Consulte `docs/state.md` para o handoff local, quando existir.

## Autoridade

| Documento | Autoridade |
|---|---|
| `capabilities/*.md` | Contrato e estado de entrega |
| `system.md` | Arquitetura realmente implementada |
| `testing.md` | Estratégia e mapa de evidências |
| `open-decisions.md` | Questões que não podem ser inventadas |
| `decisions/*.md` | Motivos e consequências duradouras |
| `history.md` | Marcos; Git preserva o detalhe |

O contrato vigente fica na capability, e os ADRs explicam o porquê. Os textos para leitores
(`README.md`, `docs/como-funciona.md`, `docs/publicar-painel.md`) explicam o projeto, mas não têm
autoridade sobre o contrato.

## Roteamento por tarefa

| Tema | Ler |
|---|---|
| Agente, SQL seguro, avaliação, relatório e painel | `capabilities/core.md` |
| Arquitetura ou integração | `system.md` e ADR relacionado |
| Segurança do SQL | `decisions/ADR-0001-sql-do-modelo-em-camadas.md` |
| Como o acerto é medido | `decisions/ADR-0002-acerto-por-resultado.md` |
| Publicação do painel e caminhos | `decisions/ADR-0005-implantacao-sem-instalacao.md` (substitui o ADR-0003) |
| Agente sem framework e isolamento do SDK | `decisions/ADR-0004-agente-sem-framework.md` |
| Testes | `testing.md` |

## Manutenção

- Mudança funcional atualiza a capability.
- Mudança de evidência atualiza `testing.md`.
- Decisão duradoura cria ou substitui ADR.
- Marco relevante atualiza `history.md`.
