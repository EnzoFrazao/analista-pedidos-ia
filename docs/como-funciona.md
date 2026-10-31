# Como funciona

Um parágrafo por módulo e o porquê. Tudo é demonstração com dados sintéticos de um restaurante
fictício. O fluxo de uma pergunta: o painel (ou a avaliação) chama `Agente.perguntar`; o agente fala
com o modelo; cada `executar_sql` do modelo passa por `sql_seguro` e chega a um SQLite somente
leitura; a resposta volta com a consulta usada, e o rastro registra cada passo.

## Núcleo (`analista/`)

**`config.py`.** Lê as variáveis de ambiente (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, e as
opcionais `BANCO_PATH`, `RUNS_PATH`, `AVALIACAO_PATH`, `LIMITE_SESSAO`, `LIMITE_DIA`) e o `.env` da raiz
do repositório, e devolve uma `Configuracao` imutável. Os caminhos padrão partem de `RAIZ`, calculada a
partir do próprio arquivo e não do diretório de onde o comando roda, porque o painel e os comandos
`python -m` rodam de pastas diferentes. A chave tem `repr=False`, então não aparece se alguém imprimir a
configuração. Endereço e modelo têm espaço e quebra de linha aparados (valor colado em painel de
segredos costuma trazê-los). `host_do_endereco` devolve só o nome do servidor, sem usuário, caminho ou
parâmetros, para a avaliação e a verificação do modelo registrarem o endereço sem vazar segredo.

**`dados.py`.** Gera o banco de pedidos com `random.Random(semente)`: mesma semente, mesmo banco. São
8 mil pedidos em 91 dias (13 semanas completas, de segunda 2026-06-29 a domingo 2026-09-27), para que o
relatório semanal compare semanas inteiras. Dois padrões são plantados e nomeados no topo do arquivo
(`PIORA_BAIRROS`, `CANCELAMENTO_MARKETPLACE`), para existir o que descobrir. O cardápio inclui um item
cujo nome traz uma instrução escondida (mandando apagar pedidos): é a fixture do teste de injeção, e
vende quase nada para não aparecer nos rankings. A geração é atômica (arquivo temporário e `os.replace`),
para o painel nunca ver um banco pela metade. `DEFINICOES` guarda as definições de métrica em um só
lugar; `hash_banco` dá uma impressão digital do conteúdo, usada pela avaliação para provar que o banco
não mudou.

**`sql_seguro.py`.** O módulo mais importante para a segurança. `abrir_somente_leitura` abre o arquivo
com `mode=ro` e `PRAGMA query_only=ON`, então o motor recusa escrita sem depender de nenhum validador.
`executar_consulta` aplica o resto: `_validar_texto` aceita uma única instrução começando por
`SELECT` ou `WITH` (um `;` ou comentário no fim, comuns na saída de modelos, não atrapalham);
`_Vigia.autorizar` é o *authorizer* que o SQLite chama para cada ação e que só aprova `SELECT`, a
leitura das três tabelas e as funções de `FUNCOES_PERMITIDAS`; a leitura linha a linha impõe o
teto de 50 linhas e de cerca de 1 MB (`LIMITE_BYTES`) e marca `truncado`; um *progress handler* corta a consulta após 2 s; e dois limites do
SQLite (`SQLITE_LIMIT_LENGTH` e o tamanho do padrão de `LIKE`) impedem que uma única função de texto
gaste tempo dentro de um passo que o relógio não vê. O estado do authorizer vive numa instância por
chamada (`_Vigia`), nunca em variável de módulo, e é removido no fim. A porta é a lista permitida: o que
não foi liberado é negado.

**`ferramentas.py`.** As duas ferramentas do modelo, o esquema JSON delas e `despachar`, que atende uma
chamada. `descrever_esquema` devolve as tabelas, o período, os valores possíveis de bairro, canal,
status e categoria, e as definições de métrica; nunca lista nomes de itens do cardápio, porque um nome é
texto livre e entraria no prompt de toda pergunta. `despachar` transforma tudo o que o modelo pode errar
(JSON quebrado, argumento faltando, ferramenta inexistente, SQL recusado, erro de sintaxe) em um JSON
`{"erro", "motivo"}` que volta ao modelo como resultado da ferramenta, para ele corrigir; devolve também
um dicionário de auditoria para o rastro e a avaliação. Células binárias e números não finitos são
normalizados, para o resultado ser sempre JSON válido. Os dois resultados passam pelos mesmos tetos de
saída (`MAX_CARACTERES_CELULA`, `MAX_COLUNAS`, `MAX_BYTES_RESULTADO`): texto acima de 200 caracteres
termina em `…[+N caracteres]`, colunas além de 30 saem, e linhas do fim saem até o JSON caber em 32 KB
(o que marca `truncado`). O que foi cortado vai em `cortes`, e o JSON do modelo ganha um `aviso`. Sem
isso, uma consulta curta com `replace` aninhado em centenas de colunas gerava dezenas de MB por chamada.
A descrição de `executar_sql` sai do teto de linhas em uso (`ferramentas(limite_linhas)`).

**`llm.py`.** O único arquivo que importa `openai`. Define a interface `ModeloLLM` (um método,
`completar`) e o `ClienteOpenAI`, que fala com qualquer endereço compatível com `chat.completions`.
Toda chamada usa `temperature=0` e `max_tokens=800`. Em erro 429 tenta de novo após 2, 4 e 8 s (a avaliação espera 15, 30, 60 e 60 s); outros
erros do SDK viram `ErroDoProvedor` com mensagem em português e sem a chave. Provedores compatíveis
variam: o código gera um `id` quando a chamada vem sem ele e guarda os campos extras de cada chamada
(`ChamadaFerramenta.extra`, por exemplo a `thought_signature` do Gemini 3) para devolvê-los intactos no
histórico. Esse último ponto está implementado e coberto por testes com cliente falso, mas **não foi
verificado com uma chave real**. Uma chamada sem `function` é ignorada, como se não viesse.

**`agente.py`.** O laço: manda o prompt do sistema e a pergunta, e a cada passo ou recebe a resposta final
ou executa as chamadas de ferramenta pedidas (todas, quando vêm várias no mesmo passo) e devolve os
resultados. Para em 6 passos. Pergunta vazia ou com mais de 1000 caracteres é recusada antes de chamar o
modelo. O prompt manda recusar escrita, tratar texto vindo dos dados como dado e não como ordem, não
inventar número e mostrar a consulta usada; ele é a camada de comportamento, e a segurança de verdade
está em `sql_seguro`. Cada `perguntar` abre a sua conexão e a fecha no `finally`, então o mesmo agente
serve a várias threads. Falha do provedor vira uma `Resposta` com `falha_provedor=True`, não exceção.

**`rastro.py`.** Um arquivo JSONL por execução (`runs/AAAAMMDD-HHMMSS-<id>.jsonl`), uma linha por evento:
pergunta, cada passo do modelo, cada chamada de ferramenta com a auditoria, resposta. Serve para
depurar e para a avaliação apontar a execução de cada erro. O arquivo só nasce no primeiro evento, e
falha de disco ao gravar não derruba a resposta.

**`relatorio.py`.** O relatório semanal. `calcular_kpis` busca por SQL os números da semana (pedidos
válidos, faturamento, ticket médio, tempo médio, cancelamento, itens mais vendidos, bairros mais lentos
com pelo menos 30 pedidos) e `variacoes` compara com a semana anterior (o cancelamento, em pontos
percentuais, porque variação relativa de uma taxa engana). O modelo só recebe os números prontos e
escreve um parágrafo; `numeros_nao_suportados` recusa o parágrafo se ele citar um número em algarismos
que não está nos KPIs (datas do período e "5 itens"/"3 bairros" são aceitas). Recusado duas vezes, sem
modelo ou com o provedor fora do ar, o parágrafo sai de `resumo_deterministico`, e o relatório diz qual
dos dois escreveu o resumo. A regra existe porque o modelo é bom em redigir e ruim em aritmética.

**`formatacao.py`.** Formatos pt-BR (`R$ 12.345,67`, `18,4%`, `+5,2%`, `-0,8 p.p.`) num só lugar, para o
relatório, o painel e a página de avaliação mostrarem o mesmo valor igual.

**`limites.py`.** `LimiteDeUso` aplica o limite de perguntas por sessão (10) e o teto diário (200),
guardado num JSON pequeno em `runs/`, gravado de forma atômica. O teto é **aproximado** de propósito:
sem biblioteca de trava, duas gravações simultâneas podem perder uma contagem, e uma falha de disco não
bloqueia a pergunta do visitante. O dia é o do relógio do servidor.

**`verificar_modelo.py`.** Teste de fumaça com a chave real: faz duas chamadas pequenas (o modelo pede
uma ferramenta de teste e o código devolve o resultado dela) e diz se chave, modelo e tool calling
funcionam. Faz a volta de propósito, porque é nela que um provedor reclama de histórico mal formado.
Sai com 0, 1 (falhou) ou 2 (sem chave), e nunca imprime a chave.

## Avaliação (`avaliacao/`)

**`perguntas.yaml` e `perguntas.py`.** As 25 perguntas, cada uma com texto, categoria, forma de
verificação e as consultas de referência fixas, geradas por IA na construção. O texto de cada pergunta diz o formato da
resposta (colunas, ordem, casas decimais), porque o agente só vê o texto e o acerto compara resultados.
`carregar_perguntas` valida os campos e levanta erro citando o id da pergunta torta.

**`comparador.py`.** `resultados_equivalentes` decide se dois resultados são o mesmo: linhas como
conjunto (ou em ordem, se a pergunta pede ordem), números iguais quando a diferença absoluta é de no
máximo 0,005, texto sem diferença de maiúsculas, nome de coluna ignorado e número de colunas exigido.
Sem ordem, as linhas são emparelhadas uma a uma (emparelhamento bipartido), porque com tolerância não dá
para contar linhas iguais. Um resultado truncado pelo teto de linhas nunca é equivalente.

**`avaliar.py`.** Roda cada pergunta `N` vezes com o agente real e julga a execução (`_julgar`). O banco
é gerado de novo numa pasta temporária, e o agente trabalha numa cópia descartável; depois de cada
pergunta compara o `hash_banco` da cópia com o do original, e uma pergunta que alterou o banco reprova.
Execução que esgota os 6 passos reprova nas perguntas de resultado, mesmo com a última consulta certa
(as de recusa seguem a regra delas), e resultado cortado pelos tetos de saída (`cortes`) conta como
incompleto, nunca igual à referência.
Cada execução concluída vai para `parcial.jsonl`, então uma queda do provedor não joga fora o que já
rodou (`--retomar` continua, e recusa se o modelo, o prompt, o banco ou as perguntas mudaram). Queda do
provedor não é contada como erro do agente: a avaliação para (saída 3). O resultado traz modelo, commit
e hashes para reproduzir. `relatorio_md.py` escreve o texto do relatório, inclusive o que a nota não
prova e a linha de base.

## Painel (`app/painel.py`)

Streamlit com três abas. **Painel**: KPIs da semana escolhida com a variação contra a anterior, pedidos
por dia e tempo por bairro; só chama funções do núcleo, nenhuma regra de métrica é reescrita ali.
**Pergunte**: o chat com o agente, a consulta usada e o contador de perguntas; sem chave, o chat fica
desligado e diz por quê. **Avaliação**: mostra `ultima.json` se existir, com as ressalvas ao lado da
nota, e um aviso se não existir. Conta nos limites toda pergunta em que o modelo respondeu pelo menos
um passo, mesmo que o provedor caia depois; falha do provedor antes do primeiro passo não conta. A aba
avisa que cada pergunta é independente (o chat não tem memória: inclua período e métrica), que as
perguntas ficam nos logs do servidor (`runs/`) e que o limite por sessão zera ao recarregar a página
(o teto diário é a trava de verdade). O painel põe a raiz do repositório no `sys.path` antes de
importar o núcleo, então roda sem instalar o projeto (ADR-0005). Nenhum erro chega à tela com texto técnico (`showErrorDetails = "none"` e uma rede
de segurança por aba). A conexão com o banco nunca fica em cache, porque o Streamlit atende sessões em
threads diferentes.
