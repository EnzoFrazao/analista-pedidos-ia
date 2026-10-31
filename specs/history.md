# Marcos do projeto

## 2026-10-31 — autoria das perguntas e aviso do plano gratuito

- As 25 perguntas e as consultas de referência foram geradas por IA (Claude) na construção, e não
  revisadas pelo mantenedor; README, specs e o texto do relatório de avaliação deixaram de dizer
  "escritas pelo autor". Nenhum número de acerto mudou.
- O painel passou a avisar, antes do campo de pergunta, que o chat usa o plano gratuito do Gemini
  (o Google pode usar e ler o conteúdo enviado), e o README ganhou o mesmo aviso. O mantenedor decidiu
  seguir no plano gratuito, sem custo. Dois testes novos (480).

## 2026-10-31 — vídeo e README reescrito

- O README foi reorganizado e ilustrado (vídeo, capturas do painel, diagramas, banco de dados, camadas
  de segurança com o teste de cada uma, avaliação, processo de construção com IA), sem remover o
  conteúdo anterior nem as limitações. Os números de acerto continuam só os de `ultima.json`.
- Entrou um vídeo de 2 min 10 s (`docs/video/`), narrado e legendado em português do Brasil, com voz
  sintética (edge-tts, `pt-BR-FranciscaNeural`), prévia em GIF e transcrição que inclui o texto da narração.
  Houve três versões: muda (2 min 12 s, legendas em português e inglês), narrada longa (4 min 50 s,
  descartada por ser longa demais e ter erros de português na fala) e esta, de roteiro curto, cenas cronometradas
  pela fala, sem termos em inglês na voz e com a primeira cena desenhada como capa (o quadro inicial é o
  pôster). As cenas usam um rastro real da avaliação, o `ultima.json` e o banco da semente 2026; a demo é
  uma gravação do painel publicado, com a espera do modelo acelerada. Os scripts que geraram o vídeo e a
  voz ficaram fora do repositório.

## 2026-10-31 — publicação e redesenho do painel

- Repositório público (MIT) e painel no Streamlit Community Cloud. A primeira CI falhou
  (`ModuleNotFoundError: tests` com `pytest` puro) e foi corrigida com `pythonpath = ["."]`.
- O chat publicado devolveu erro 400 do provedor: o classificador não reconhecia "Please pass a valid API
  key" (chave com aspas) nem "unexpected model name format" (modelo com aspas). As duas mensagens
  passaram a apontar o segredo certo, a resposta do provedor vai ao log sem a chave e aspas sobrando
  em volta da chave, do modelo e do endereço são tiradas.
- Painel redesenhado (tema claro, cartões de KPI com miniatura, gráficos com valor na barra, resumo da
  semana, perguntas sugeridas), a partir da skill de UI/UX e de painéis de exemplo (Streamlit
  `st.metric`, Evidence, Posit querychat, Metabase).
- Com os Secrets corrigidos pelo mantenedor, o chat publicado respondeu "Quantos pedidos foram
  cancelados?" (473) e recusou o pedido de apagar dados. Em seguida o painel foi reorganizado em torno
  do chat: aba Pergunte primeiro, campo em destaque, guia de uso, sugestões e conversa com a resposta
  mais nova no topo.

## 2026-10-31 — primeira avaliação com modelo real

- `verificar_modelo` passou com chave real do Gemini (plano gratuito), e a avaliação de 75 execuções
  terminou no commit `758b782`: geral 88,0%, objetivas 100,0%, ambíguas 0,0%, linha de base 12,0%. A
  primeira tentativa parou no limite por minuto; a avaliação passou a esperar 15, 30, 60 e 60 s em 429.
- O `--retomar` recusou um parcial de outro commit, como projetado; foi preciso `--recomecar`.

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
