# Estratégia e mapa de testes

**Última verificação:** 2026-10-31

## Gates

| Comando | Evidência | Resultado em 2026-10-31 |
|---|---|---|
| `python -m pytest -q -W error` | Núcleo, avaliação e painel, sempre com modelo falso, sem rede | 480 passaram |
| `python -m ruff check .` | Estilo e erros estáticos (linha de até 100 colunas) | sem avisos |
| `python -m analista.dados` | O gerador roda e cria o banco de 8000 pedidos | executado |
| `python -m analista.relatorio --semana 2026-09-21 --sem-modelo` | Relatório sem chave | executado; saída em `docs/exemplo-relatorio.md` |
| `python -m analista.verificar_modelo` | Sem chave, ensina a configurar e sai com 2; com chave, confere chave, modelo e tool calling | executado sem chave e com chave real |
| `python -m avaliacao.avaliar` | Acerto medido com modelo real; exige a chave do provedor; fora do CI | executado em 2026-10-31: geral 88,0%, objetivas 100,0%, ambíguas 0,0% |
| `python -I <skill>/scripts/validate_specs.py .` | Consistência desta memória técnica | passou |

O CI (`.github/workflows/ci.yml`) roda `ruff check .` e `pytest -q` com Python 3.12 e nunca usa chave.

## Estratégia

Nenhum teste chama um modelo real nem lê o `.env` do desenvolvedor: `tests/conftest.py` limpa as
variáveis de ambiente que a configuração lê e troca `analista.config.load_dotenv` por uma função que
não lê nada (o teste que confere a leitura do `.env` da raiz troca de novo dentro dele), e `tests/fakes.py` traz `ModeloFalso` (roteiro de
respostas), `ModeloOraculo` (responde com a primeira consulta de referência de cada pergunta) e
`ModeloRuim` (consulta `SELECT 0`). O banco sintético é gerado uma vez por sessão (`banco_path`). As
garantias de segurança são testadas por ataque (escrita, instruções múltiplas, comentário que esconde
escrita, `ATTACH`, `PRAGMA`, `sqlite_master`, funções fora da lista, funções lentas, texto gigante,
threads, consulta curta que amplifica a saída para dezenas de MB), e a máquina de medir é testada pelo oráculo (nota máxima) e pelo modelo ruim (erra os
resultados e passa nas recusas). O painel é testado com o `AppTest` do Streamlit e o modelo falso.

## Responsabilidade por camada

| Comportamento | Local |
|---|---|
| Configuração e variáveis de ambiente | `tests/test_config.py` |
| Gerador de dados, padrões plantados, item com instrução escondida | `tests/test_dados.py` |
| Camadas de segurança do SQL | `tests/test_sql_seguro.py` |
| Ferramentas, despacho, erros devolvidos ao modelo e tetos de saída | `tests/test_ferramentas.py` |
| Laço do agente, rastro (inclusive tamanho da linha com saída amplificada) e cliente do modelo | `tests/test_agente.py`, `tests/test_rastro.py`, `tests/test_llm.py` |
| Verificação do modelo com chave | `tests/test_verificar_modelo.py` |
| Relatório semanal, KPIs, checagem de números e CLI | `tests/test_relatorio.py` |
| Formatos pt-BR | `tests/test_formatacao.py` |
| Limites de uso | `tests/test_limites.py` |
| Perguntas de referência e comparador | `tests/test_perguntas.py`, `tests/test_comparador.py` |
| Executor da avaliação e relatório em Markdown | `tests/test_avaliar.py`, `tests/test_relatorio_md.py` |
| Painel, contagem nos limites, `sys.path` e `requirements.txt` igual ao `pyproject.toml` | `tests/test_painel.py` |

## Mapa por capacidade

| Capability | Arquivos ou globs | Situação |
|---|---|---|
| `core` | `tests/test_*.py` | Implementada e coberta com modelo falso; sem execução com modelo real |

## Lacunas

- Só números de `avaliacao/resultados/ultima.json` podem aparecer no README, no painel ou em texto fora
  do repositório. A medição é de uma execução, com 25 perguntas geradas por IA (Claude) na construção e revisadas pelo mantenedor (as consultas de referência também são geradas por IA).
- A volta do Gemini 3 com `thought_signature` é testada com cliente falso nos testes automatizados; a
  prova com o provedor real é `python -m analista.verificar_modelo` (passou em 2026-10-31) e não roda no CI.
- O texto de `DEFINICOES` e o SQL das consultas do relatório são duas escritas da mesma regra, protegidas
  apenas pelo teste que compara os KPIs com consultas independentes (`test_kpis_batem_com_consulta_direta`).
- O texto em inglês de erros do SQLite é usado por um teste de sintaxe de `tests/test_ferramentas.py`.
- Não há teste de fumaça do painel publicado. O build no Streamlit Community Cloud foi conferido à mão
  em 2026-10-31 (as três abas abriram com os mesmos números do teste local; no chat, uma pergunta
  objetiva e uma recusa foram respondidas, ver `P-004`), e o teste de `sys.path` só olha o código-fonte.
- As perguntas sugeridas do chat são conferidas contra `avaliacao/perguntas.yaml`
  (`test_perguntas_sugeridas_saem_do_conjunto_da_avaliacao`); o visual (cores, cartões, gráficos) só
  foi visto no navegador, em 800 px e 375 px, sem teste automático.
- O contador do teto diário não é testado com processos concorrentes de verdade.
- O vídeo (`docs/video/`) e as capturas do painel (`docs/img/`) não são gerados nem conferidos por
  teste: foram produzidos por script fora do repositório e conferidos à mão contra `ultima.json` e
  contra o banco gerado com a semente 2026 (hash igual ao de `ultima.json`). Se o painel, a avaliação ou
  o banco mudarem, o vídeo e as imagens ficam desatualizados até alguém refazê-los. A narração é de voz
  sintética: a pronúncia (siglas como SQL e IA, Python, GitHub) e a gramática do roteiro só se conferem
  lendo e ouvindo, sem teste automático (a legenda e o texto em `docs/video/legendas.md` são a referência).
