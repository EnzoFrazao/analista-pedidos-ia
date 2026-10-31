---
id: core
contract_status: draft
implementation_status: implemented
last_verified: 2026-10-30
last_verified_ref: working-tree
---

# Analista de pedidos: perguntar, medir e relatar

## Finalidade e limites

Responder, em português, perguntas de negócio de um restaurante fictício sobre um conjunto sintético de
pedidos, sem o usuário escrever SQL. É uma demonstração de agente com
ferramentas, análise de dados com IA, avaliação com número e painel. Fora do escopo: dados reais,
vários restaurantes ou login, RAG, vetores, fine-tuning, framework de agentes (ADR-0004) e gráficos
gerados pelo modelo. Envio do relatório por mensageria também ficou de fora.

## Atores, permissões, entradas e resultados

- **Visitante do painel:** faz até 10 perguntas por sessão, sujeito a um teto diário de 200; lê KPIs e a
  página de avaliação. Não escreve em nada.
- **Mantenedor (operador):** fornece a chave do provedor, roda a verificação do modelo e a avaliação, publica
  o painel.
- **Entrada:** pergunta em texto, de 1 a 1000 caracteres depois de aparar os espaços; vazia ou maior é
  recusada antes de chamar o modelo.
- **Saída:** resposta em texto com a consulta usada; rastro JSONL por execução (`runs/`); relatório
  semanal em Markdown; resultado da avaliação em `avaliacao/resultados/ultima.json` e `ultima.md`.

## Contrato comportamental e critérios de aceite

- O agente consulta o banco só por `descrever_esquema()` e `executar_sql(consulta)`, em no máximo 6
  passos, e atende todas as chamadas de ferramenta de um mesmo passo. Erro do modelo ao chamar uma
  ferramenta volta a ele como resultado, sem exceção.
- Cada consulta SQL passa pelas camadas de ADR-0001: conexão somente leitura, uma instrução
  `SELECT`/`WITH` (um `;` ou comentário no fim é aceito), authorizer com ações, tabelas e funções
  permitidas, teto de 50 linhas (`truncado`), tempo máximo de 2 s. Escrita falha mesmo sem o validador.
- O resultado de `executar_sql` que volta ao modelo e vai à auditoria tem tetos de saída: texto de
  célula até 200 caracteres (o resto vira `…[+N caracteres]`), até 30 colunas, nome de coluna até 64
  caracteres e JSON inteiro até 32 KB (linhas do fim saem e `truncado` fica verdadeiro). O que foi
  cortado aparece em `cortes`, e o modelo recebe um `aviso`.
- Pedido de escrita e instrução escondida nos dados não alteram o banco. `descrever_esquema` lista
  categorias e nunca nomes de itens.
- Falha do provedor do modelo (limite, chave, rede) vira uma mensagem em português, sem a chave e sem
  stack trace; em erro 429 o cliente tenta de novo após 2, 4 e 8 s.
- A avaliação roda 25 perguntas de referência (8 agregação simples, 6 filtro e junção, 4 comparação de
  períodos, 3 ambíguas, 2 escrita a recusar, 2 injeção), 3 repetições por padrão, e reporta média e pior
  caso (ADR-0002). Roda num banco gerado de novo em pasta temporária, reprova a pergunta que altera o
  banco, grava cada execução em `parcial.jsonl` e para sem pontuar quando o provedor cai (saída 3).
  Execução que esgota os passos reprova nas perguntas de resultado (a recusa segue a regra dela);
  resultado truncado ou com `cortes` nunca é igual à referência; números comparam com tolerância
  absoluta de 0,005.
- O relatório semanal calcula os números por SQL e compara com a semana anterior (cancelamento em pontos
  percentuais); o modelo só redige o parágrafo, e um parágrafo com número em algarismos que não está nos
  KPIs é recusado. Sem modelo, recusado duas vezes ou com o provedor fora, o resumo é montado pelo código
  e o relatório diz qual dos dois escreveu.
- O painel tem as abas Painel, Pergunte e Avaliação. Sem chave, o chat fica desligado e as outras abas
  funcionam. Conta nos limites a pergunta em que o modelo respondeu pelo menos um passo, mesmo que o
  provedor caia depois; falha antes do primeiro passo não conta. A aba Pergunte avisa que cada pergunta
  é independente (sem memória do chat), que as perguntas ficam nos logs do servidor e que o limite por
  sessão zera ao recarregar a página. A aba Avaliação só mostra números
  quando existe um `ultima.json` e traz as ressalvas da medição ao lado da nota. Nunca exibe a chave nem
  detalhe técnico de erro.

## Invariantes e regras de negócio

- Nenhuma chave no repositório, no histórico, nos logs ou no painel.
- Definições de métrica únicas em `analista/dados.py` (`DEFINICOES`): pedido válido é `status <>
  'cancelado'`; faturamento soma `valor` dos válidos sem a taxa de entrega; ticket médio é faturamento
  dividido pelos válidos; tempo médio e nota média ignoram nulos; cancelamento é cancelados dividido pelo
  total, vezes 100. O agente, o relatório e as referências da avaliação seguem essas definições.
- Os dados são sintéticos e determinísticos para uma semente (8000 pedidos, de 2026-06-29 a 2026-09-27);
  nada parece pessoa real; sem marca de empresa real.
- O texto do projeto não afirma uso real: o README abre dizendo que os dados são sintéticos, e nenhum
  número de acerto aparece sem um `ultima.json` gerado por execução real.
- Toda chamada ao modelo usa `temperature=0` e `max_tokens=800`.
- Os testes automatizados nunca chamam um modelo real.

## Estado atual e lacunas

Implementada e coberta por testes com modelo falso; `implementation_status` não é `verified` porque
nenhuma avaliação com modelo real foi executada. O contrato continua em `draft` até o mantenedor aprovar o
texto. Lacunas conhecidas:

- O modelo padrão (`gemini-3.5-flash-lite`) e os limites do plano gratuito não foram confirmados com
  chave real, e a volta do Gemini 3 com `thought_signature` está implementada mas não verificada
  (`P-002`).
- Nada foi publicado: sem repositório remoto, Space nem painel no ar (`P-004`).
- O teto diário é aproximado e usa a data do servidor (21h de Brasília em hospedagem em UTC); o contador
  fica em arquivo, que pode não persistir entre reinícios.
- Um erro fixo do provedor numa pergunta faz `--retomar` parar sempre no mesmo ponto.
- Ao esgotar os 6 passos, os resultados do último passo não chegam ao modelo; a avaliação conta essa
  execução como erro nas perguntas de resultado.
- O chat não tem memória: se o agente perguntar de volta, a resposta do usuário chega como pergunta
  nova, sem o contexto.
- O acerto mede a consulta e não a redação; a nota das ambíguas é um piso; as verificações de recusa não
  medem a qualidade da recusa; a checagem de números do relatório só vê algarismos.
- `avaliacao/avaliar.py` grava sempre em `avaliacao/resultados/`, sem ler `AVALIACAO_PATH`, que só o
  painel usa.

## Evidências de implementação e teste

- Implementação: pacotes `analista/`, `avaliacao/` e `app/painel.py`; arquitetura em `../system.md`.
- Suíte: `python -m pytest -q -W error` (461 passaram em 2026-10-30, depois da revisão final) e
  `python -m ruff check .` (sem avisos).
- Dados: `python -m analista.dados` gera `data/pedidos.db` com 8000 pedidos.
- Relatório sem chave: `python -m analista.relatorio --semana 2026-09-21 --sem-modelo` (saída
  arquivada em [`docs/exemplo-relatorio.md`](../../docs/exemplo-relatorio.md)).
- Sem chave: `python -m analista.verificar_modelo` imprime como configurar e sai com código 2; `python -m
  avaliacao.avaliar --help` lista as opções.
- Mapa de testes por comportamento: `../testing.md`.
- Não existe evidência de execução com modelo real; ela depende da chave do mantenedor.

## Relações

- Decisões abertas: `P-001`, `P-002`, `P-003`, `P-004`, em `../open-decisions.md`.
- ADRs: `ADR-0001`, `ADR-0002`, `ADR-0004` e `ADR-0005` (que substitui o `ADR-0003`), em
  `../decisions/`.
