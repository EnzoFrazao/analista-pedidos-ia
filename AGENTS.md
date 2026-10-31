# AGENTS.md

Um agente que responde, em português, perguntas de negócio de um restaurante fictício sobre pedidos sintéticos, com SQL somente
leitura, acerto medido e painel Streamlit. É uma demonstração com dados sintéticos, não um produto.

## Memória técnica

1. Leia `specs/README.md`.
2. Abra somente as capabilities relacionadas à tarefa.
3. Consulte `specs/system.md` para confirmar o implementado.
4. Consulte `specs/testing.md` antes de alterar cobertura.
5. Use `docs/state.md` somente para handoff local.

Mudança funcional atualiza a capability; decisão duradoura cria ou substitui ADR; alteração de
evidência atualiza `testing.md`; marco relevante atualiza `history.md`.

## Regras do projeto

- Mudanças novas partem das specs, com teste antes do código.
- `analista/` não importa `app/` nem `avaliacao/`; só `analista/llm.py` importa `openai`; só `app/painel.py` importa `streamlit`.
- A segurança do SQL vem da conexão somente leitura e do authorizer do SQLite, nunca do prompt.
- Nenhuma chave no repositório, no histórico, nos logs ou no painel. Os testes nunca chamam o modelo.
- Nada de dado que pareça pessoa real, nem marca ou logotipo de empresa real. Não escrever "em produção".
- Número de acerto só entra no README ou em texto fora do repositório se estiver em `avaliacao/resultados/ultima.json`, gerado por uma execução real.
- `README.md` e `docs/` descrevem só o que o código e os testes provam; ao mudar comportamento, confira `docs/como-funciona.md` e a seção Limitações do README.
- Rode os comandos com o Python do `.venv`; todo arquivo é lido e escrito em UTF-8.
- Commits locais sem `Co-Authored-By`. Push, criação do repositório remoto e deploy só com o OK do mantenedor.
