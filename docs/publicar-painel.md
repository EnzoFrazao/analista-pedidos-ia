# Publicar o painel

Passos para publicar o painel. **Nada aqui foi feito ainda.** Este guia descreve o Hugging Face Spaces, que também deixa
um Space no perfil; o Streamlit Community Cloud serve igual (pule o cabeçalho YAML e o passo do Space).

## Antes de publicar

1. Crie uma chave gratuita do provedor (o padrão é o Google AI Studio), coloque em `.env` como
   `LLM_API_KEY` e rode `python -m analista.verificar_modelo`. Ele confere chave, modelo e tool calling,
   inclusive a volta com o resultado da ferramenta que o Gemini 3 exige (ainda não verificada).
2. Rode `python -m avaliacao.avaliar --pausa 2` (se o plano gratuito tiver limite por minuto, a pausa
   ajuda; `--retomar` continua se o provedor cair). Se quiser que a aba Avaliação do painel publicado
   mostre o resultado, **versione** `avaliacao/resultados/ultima.json` e `ultima.md` (o Git ignora
   só `rastros/`, `parcial.jsonl`, os temporários `*.tmp` e os resultados filtrados por categoria,
   `ultima-<categoria>.*`, que não valem como nota geral). Sem o arquivo, a aba
   diz que a avaliação ainda não foi executada. Só publique números que estejam nesse arquivo.
3. Confira que `.env` e `data/` não estão no que vai subir (`git status` e `git ls-files`).

## Criar o Space

1. No Hugging Face, **Create new Space**: SDK **Streamlit**, hardware gratuito (CPU basic).
2. O Hugging Face lê a configuração do Space de um cabeçalho YAML no topo do `README.md` **do
   repositório do Space**. Esse cabeçalho não vai para o README do GitHub (ele ficaria estranho na
   página do repositório). O que o Space precisa:

   ```yaml
   ---
   title: Analista de pedidos (demonstração)
   sdk: streamlit
   app_file: app/painel.py
   python_version: "3.12"
   ---
   ```

   O `app_file` aponta para o painel, e o `python_version` precisa ser 3.12 (o projeto exige
   `>=3.12`; o padrão do Hugging Face costuma ser mais antigo).
3. O repositório do Space é um Git à parte (`https://huggingface.co/spaces/<usuário>/<nome>`). Uma forma
   simples de manter o GitHub limpo: criar uma branch só para o Space, com o cabeçalho no topo do
   `README.md`, e enviar essa branch para o `main` do Space. O código é o mesmo; só o `README.md`
   difere. Os comandos de Git são seus.
4. Em **Settings, Variables and secrets** do Space, cadastre:
   - `LLM_API_KEY` como **secret** (é a chave; nunca no código nem no repositório);
   - `LLM_BASE_URL` e `LLM_MODEL` como variáveis (ou também como secrets), com os mesmos valores do
     `.env.example`, ou os do provedor que você escolher;
   - opcionais: `LIMITE_SESSAO` e `LIMITE_DIA`, para mudar os 10 por sessão e os 200 por dia.

## O que o build precisa

O `requirements.txt` lista só as dependências de execução (`openai`, `python-dotenv`, `pyyaml`,
`streamlit`), com as mesmas faixas do `pyproject.toml`; um teste confere que as duas listas batem. O
projeto em si **não** é instalado: `app/painel.py` põe a raiz do repositório na frente do `sys.path`
antes de importar `analista` e `avaliacao`, então o código e `RAIZ` (que localiza `data/`, `runs/` e
`.env`) vêm sempre da cópia do repositório no serviço (ADR-0005). Assim não importa se o serviço
instala o `requirements.txt` antes ou depois de copiar o resto do código.

**Ponto a confirmar no primeiro build:** isto foi conferido localmente (o painel abre com o Python sem
o pacote instalado), mas não num build do Hugging Face nem do Streamlit Community Cloud.

## O que esperar depois de publicado

- O banco `data/pedidos.db` é gerado na primeira abertura (`garantir_banco`), a partir da semente: não
  precisa ir para o repositório.
- Sem `LLM_API_KEY`, o chat fica desligado e diz por quê; o painel e a aba Avaliação continuam.
- O contador do teto diário fica em `runs/limite_dia.json`, no disco do Space, que normalmente não
  persiste entre reinícios; por isso o teto é aproximado (veja `analista/limites.py`). Ele zera à
  meia-noite do relógio do servidor, que costuma ser UTC: 21h de Brasília.
- Quando o provedor responde com limite de uso (429), o cliente tenta de novo por até cerca de 14 s
  por chamada ao modelo (até 6 por pergunta) antes de mostrar um aviso.
- Visitantes fazem até 10 perguntas por sessão; esse limite volta a zero ao recarregar a página, então
  a trava de verdade é o teto diário. O painel nunca exibe a chave nem stack trace.
- As perguntas dos visitantes ficam em `runs/*.jsonl` no disco do serviço, para auditoria (o painel
  avisa isso na aba Pergunte).

## Depois

Teste uma pergunta de cada tipo no Space publicado, confira a aba Avaliação, e só então ponha o link
no README e no perfil.
