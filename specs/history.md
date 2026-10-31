# Marcos do projeto

## 2026-10-31 — histórico reorganizado e dois ajustes antes da publicação

- `executar_consulta` passou a ler linha a linha com teto de cerca de 1 MB, em vez de `fetchmany`: uma
  consulta larga chegava a centenas de MB em memória antes do corte da saída. A avaliação deixou de
  tratar nome de coluna encurtado como resultado cortado (o comparador não olha nomes).

- Antes do primeiro push, o histórico foi reescrito para tirar rascunhos de planejamento que não fazem
  parte do projeto. A memória técnica (`specs/`) e o `AGENTS.md` entraram num commit próprio, e o
  `docs/state.md` passou a ser só local (fora do Git).

## 2026-10-30 — revisão final da branch

- A revisão da branch inteira achou que a saída de `executar_sql` não tinha teto em bytes (uma consulta
  curta gerava cerca de 98 MB por mensagem e por linha de rastro); `despachar` passou a cortar célula,
  colunas e o JSON inteiro, e o painel passou a contar a pergunta quando o modelo já respondeu um passo.
- A avaliação passou a reprovar execução que esgota os passos e a comparar números com tolerância
  absoluta. O painel deixou de exigir instalação editável (ADR-0005 substitui o ADR-0003).
- Textos que prometiam mais que o código foram corrigidos (camadas do SQL, espera do provedor por
  chamada). A suíte passou a 461 testes, sempre com modelo falso.

## 2026-10-30 — implementação inicial e documentação

- Os módulos do plano foram implementados numa branch de trabalho, com teste antes do código e revisão
  por tarefa: gerador de dados, SQL em camadas, ferramentas, laço do agente e cliente do modelo,
  relatório semanal, limites de uso, 25 perguntas de referência, executor da avaliação e painel.
- A suíte tem 430 testes, todos com modelo falso. Nenhuma execução com provedor real foi feita.
- Duas decisões mudaram o plano durante a implementação: o item de cardápio com instrução escondida
  passou a exigir um `DELETE`, para que obedecer à injeção seja detectável como tentativa de escrita; e o
  painel passou a exigir instalação editável (ADR-0003), por causa de `RAIZ`.
- Foram escritos README, `docs/como-funciona.md`, `docs/publicar-painel.md`, o relatório de exemplo e
  esta memória técnica atualizada ao código, com quatro ADRs. A referência é `working-tree`; o contrato
  da capability continua em `draft` e não há publicação.

## 2026-10-29 — desenho e plano

- O desenho e um plano de execução em 12 tarefas foram escritos antes de existir código; ficaram fora
  do repositório.
