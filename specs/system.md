# Sistema implementado

**Última verificação:** 2026-10-31  
**Referência:** `aceaad3` (`main`)

## Finalidade e unidade executável

Um agente de tool calling que responde perguntas sobre pedidos sintéticos de um restaurante fictício,
mais um relatório semanal, um executor de avaliação e um painel Streamlit. O painel roda publicado no
Streamlit Community Cloud (`https://pedidoai.streamlit.app/`), construído a partir do `main` do
repositório público; o resto roda localmente. Contrato em `capabilities/core.md`.

## Stack e entrypoints

Python 3.12 (`requires-python >=3.12`), SQLite da biblioteca padrão, `openai>=3.41,<4` (cliente para
endereço compatível), `pyyaml`, `python-dotenv`; extra `painel` com `streamlit>=1.66,<2`; extra `dev`
com `pytest` e `ruff`. Instalação para desenvolver: `pip install -e ".[dev,painel]"` (`pyproject.toml`,
licença SPDX `MIT`, `setuptools>=77`). Deploy do painel: `pip install -r requirements.txt`, que lista as
mesmas faixas de `dependencies` mais o extra `painel`, sem instalar o projeto (ADR-0005).

| Comando | Módulo | Função |
|---|---|---|
| `python -m analista.dados` | `analista/dados.py` | Gera `data/pedidos.db` (8000 pedidos, semente 2026) |
| `python -m analista.relatorio --semana AAAA-MM-DD [--sem-modelo] [--saida]` | `analista/relatorio.py` | Relatório semanal em Markdown |
| `python -m analista.verificar_modelo` | `analista/verificar_modelo.py` | Teste de fumaça com a chave: chave, modelo, tool calling em dois turnos |
| `python -m avaliacao.avaliar [--repeticoes N] [--pausa S] [--categoria C] [--retomar \| --recomecar]` | `avaliacao/avaliar.py` | Avaliação com o modelo real |
| `streamlit run app/painel.py` | `app/painel.py` | Painel com as abas Painel, Pergunte e Avaliação |

## Fronteiras e fluxo

```
analista/   núcleo, sem Streamlit
  config.py       variáveis de ambiente e .env -> Configuracao (RAIZ = pasta do repositório),
                  host_do_endereco
  dados.py        gerador determinístico, DEFINICOES de métrica, hash_banco
  sql_seguro.py   conexão ro, authorizer, teto de linhas e de bytes (LIMITE_LINHAS,
                  LIMITE_BYTES), tempo e tamanho de valor
  ferramentas.py  descrever_esquema, executar_sql, esquema JSON (ferramentas(limite_linhas)),
                  despachar e os tetos de saída (célula, colunas, bytes)
  llm.py          interface ModeloLLM e ClienteOpenAI (único arquivo que importa openai)
  agente.py       laço de tool calling (até 6 passos) e PROMPT_SISTEMA
  rastro.py       JSONL por execução
  relatorio.py    KPIs por SQL, variações, checagem de números, CLI
  formatacao.py   formatos pt-BR compartilhados
  limites.py      limite por sessão e teto diário
  verificar_modelo.py
avaliacao/  perguntas.yaml + perguntas.py, comparador.py, avaliar.py, relatorio_md.py
app/painel.py   interface; só ela importa streamlit
```

Regras de dependência: `analista/` não importa `app/` nem `avaliacao/`; `avaliacao/` e `app/` consomem o
núcleo; só `analista/llm.py` importa `openai`.

Fluxo de uma pergunta: `Agente.perguntar` valida o tamanho, abre uma conexão somente leitura nova, manda
o prompt e a pergunta ao `ModeloLLM`; a cada passo ou recebe o texto final ou executa as chamadas de
ferramenta com `ferramentas.despachar`, que usa `sql_seguro.executar_consulta` (camadas em ADR-0001) e
devolve ao modelo o resultado ou um JSON de erro. Antes de voltar, o resultado passa pelos tetos de
saída de `despachar` (texto de célula em 200 caracteres com marcador, 30 colunas, nome de coluna em 64
caracteres, JSON inteiro em 32 KB tirando linhas do fim); o mesmo corte vai à auditoria, com `cortes`
dizendo o que saiu. O rastro registra pergunta, passos, chamadas com auditoria e resposta. Falha do provedor vira `Resposta.falha_provedor`, não exceção. A avaliação usa o
mesmo `Agente` sobre uma cópia descartável de um banco gerado em pasta temporária e compara com as
referências numa conexão somente leitura à cópia intacta (ADR-0002); execução que esgota os passos
reprova nas perguntas de resultado, e resultado com `cortes` é tratado como truncado. O comparador usa
tolerância absoluta de 0,005 nos números, com emparelhamento bipartido das linhas quando a ordem não
importa.

## Estado, persistência e integrações

| Arquivo ou pasta | Quem escreve | Observação |
|---|---|---|
| `data/pedidos.db` | `analista/dados.py`, `garantir_banco` no painel | Gerado, fora do Git; troca atômica |
| `runs/*.jsonl` | `Rastro` | Um arquivo por execução, fora do Git |
| `runs/limite_dia.json` | `LimiteDeUso` | `{"data", "n"}`; escrita atômica; o dia é o do relógio do servidor |
| `relatorios/semana-*.md` | `analista.relatorio --saida` | Fora do Git |
| `avaliacao/resultados/ultima.json` e `ultima.md` | `avaliar.escrever_relatorio` | Versionável; `ultima-<categoria>.*` quando filtrada |
| `avaliacao/resultados/parcial.jsonl` e `rastros/` | `avaliar.py` | Fora do Git; o parcial sai quando a avaliação termina |

Integração externa: um provedor de modelo por endereço compatível com `chat.completions` da OpenAI
(padrão: Gemini, `gemini-3.5-flash-lite`), configurado por `LLM_BASE_URL`, `LLM_MODEL` e `LLM_API_KEY`.
Opcionais: `BANCO_PATH`, `RUNS_PATH`, `AVALIACAO_PATH` (só o painel), `LIMITE_SESSAO` (10) e `LIMITE_DIA`
(200). `LLM_API_KEY`, `LLM_BASE_URL` e `LLM_MODEL` têm espaço, quebra de linha e aspas sobrando em volta
aparados (o Gemini responde 400 a chave ou modelo com aspas). Os caminhos padrão partem de
`RAIZ`; o painel põe a raiz do repositório na frente do `sys.path` antes de importar o núcleo, então
`RAIZ` é o repositório sem instalação editável (ADR-0005). CI em `.github/workflows/ci.yml`
(`permissions: contents: read`): `ruff check .` e `pytest -q` com Python 3.12.

## Restrições e lacunas

- Toda chamada ao modelo usa `temperature=0` e `max_tokens=800`; cada tentativa em 429 espera 2, 4 e 8 s
  (15, 30, 60 e 60 s na avaliação, `ESPERAS_429_S`).
- Banco de 91 dias (13 semanas, segunda 2026-06-29 a domingo 2026-09-27); o relatório só aceita semanas
  inteiras dentro desse período.
- O teto diário é aproximado (ler, somar e gravar não é atômico); falha ao gravar o contador não bloqueia
  a pergunta.
- Dados sintéticos com dois padrões plantados (piora do tempo em Cohama e Turu a partir de 2026-09-07;
  cancelamento maior no marketplace) e um item de cardápio com instrução escondida, fixture da injeção.
- Não há persistência de conversa: o histórico do chat vive na sessão do Streamlit, e o agente recebe
  cada pergunta sozinha (sem as anteriores); o painel avisa para incluir período e métrica.
- O painel conta no limite a pergunta em que o modelo respondeu pelo menos um passo (`passos > 0`),
  mesmo que o provedor caia depois. O limite por sessão zera ao recarregar a página; o teto diário é a
  trava efetiva. As perguntas ficam em `runs/*.jsonl` no servidor, e o painel avisa isso.
- Uma avaliação completa com provedor real foi feita em 2026-10-31 (`gemini-3.5-flash-lite`, plano
  gratuito, commit `758b782`); `verificar_modelo` confirmou a volta com `thought_signature`.
- Publicação em 2026-10-31: repositório público `EnzoFrazao/analista-pedidos-ia` e painel no Streamlit
  Community Cloud, com o build só de `requirements.txt` funcionando (ADR-0005). As abas Painel e
  Avaliação foram conferidas no endereço público. **O chat publicado ainda não respondeu uma
  pergunta:** o provedor recusou a chave cadastrada nos Secrets do serviço (a mesma chave passa em
  `verificar_modelo` na máquina do mantenedor); o painel agora diz qual segredo conferir.
- Interface do painel: tema claro em `.streamlit/config.toml`; KPIs em cartões com miniatura da
  evolução semanal; gráficos Altair com valor na barra (âmbar = bairro mais lento que a média da
  semana); resumo da semana em frases tiradas dos mesmos números; chat com seis perguntas sugeridas
  (cinco da avaliação e uma recusa). O disparo do clique usa `st.session_state["pergunta_pendente"]`.
- O erro do provedor vai para o log do servidor (status e corpo sem a chave), nunca para a tela.
