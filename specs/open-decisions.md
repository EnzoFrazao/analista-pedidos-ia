# Decisões abertas

## P-001 — Aprovação do contrato

- **Problema:** o plano foi executado e a capability `core` está implementada, mas o mantenedor ainda não
  aprovou explicitamente o texto do contrato (`specs/capabilities/core.md`) nem os ajustes feitos ao
  desenho durante a implementação (91 dias, teto de 50 linhas imposto na execução, lista permitida de
  funções, acerto pela última consulta bem-sucedida, perguntas objetivas e ambíguas reportadas à parte).
- **Pergunta:** o contrato está aprovado como está escrito?
- **Responsável esperado:** mantenedor.
- **Impacto:** o `contract_status` de `core` continua em `draft` até a resposta.
- **Regra provisória:** manter `draft`; não tratar o contrato como confirmado em nenhum texto.
- **Capability:** `core`.

## P-002 — Modelo padrão e plano gratuito

- **Problema:** `gemini-3.5-flash-lite` vem da página de modelos do Gemini (2026-10-30). Com chave real
  (2026-10-31), `verificar_modelo` passou, inclusive a volta com `thought_signature`, e a avaliação de 75
  execuções terminou. Ficam em aberto os limites numéricos do plano gratuito, que só aparecem no Google
  AI Studio: a primeira tentativa parou no limite por minuto depois de 4 execuções. No Groq, os modelos
  listados têm 8 mil tokens por minuto, pouco para a avaliação.
- **Pergunta:** o Gemini Flash-Lite fica como padrão, e quais são os limites do plano gratuito?
- **Responsável esperado:** mantenedor, depois de criar a chave e rodar `python -m analista.verificar_modelo`.
- **Impacto:** `.env.example`, duração e custo da avaliação, e o texto do README.
- **Regra provisória:** manter o Gemini Flash-Lite no `.env.example`, configurável por variável, com o
  Groq (`openai/gpt-oss-120b`) como alternativa comentada; o Gemini 3 está verificado só para esse modelo e esse provedor.
- **Capability:** `core`.

## P-003 — Licença

- **Problema:** o desenho não define licença; o arquivo `LICENSE` (MIT) foi
  criado como regra provisória.
- **Pergunta:** a licença MIT está aprovada?
- **Responsável esperado:** mantenedor.
- **Impacto:** o `LICENSE` e a seção Licença do README antes do primeiro push.
- **Regra provisória:** manter a MIT no repositório local; revisar antes de publicar.
- **Capability:** `core`.

## P-004 — Onde publicar o painel e como o build instala o projeto

- **Problema:** o desenho aceita Hugging Face Spaces ou Streamlit Cloud. O painel não depende mais de
  instalação editável (ADR-0005): o `requirements.txt` lista só as dependências e o painel põe a raiz
  do repositório no `sys.path`. Isso foi conferido localmente, mas nenhum build num serviço real foi
  feito.
- **Pergunta:** qual serviço, e o build com o `requirements.txt` atual funciona nele?
- **Responsável esperado:** mantenedor (criar o serviço, cadastrar o segredo e conferir o primeiro build).
- **Impacto:** `docs/publicar-painel.md` e, se o build falhar, um `Dockerfile` ou outro serviço.
  O cabeçalho YAML do Space (`python_version: "3.12"`) também precisa ser conferido no primeiro build.
- **Regra provisória:** documentar o Hugging Face Spaces, que também deixa o Space no perfil do autor, com
  o ponto do build marcado como não testado. Nada é publicado sem o OK do mantenedor.
- **Capability:** `core`.
