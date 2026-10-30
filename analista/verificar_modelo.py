"""Teste de fumaça com chave real: `python -m analista.verificar_modelo`.

Faz UMA chamada pequena, com uma ferramenta de teste, e diz se a chave, o modelo e o tool
calling funcionam. Nunca imprime a chave. Sai com 0 (tudo OK), 1 (algo falhou) ou 2 (sem chave).
"""

import sys
from collections.abc import Callable

from analista.config import Configuracao, carregar_configuracao
from analista.llm import ErroDoProvedor, ModeloLLM, criar_modelo

_FERRAMENTA_DE_TESTE = {
    "type": "function",
    "function": {
        "name": "responder_ok",
        "description": "Ferramenta de teste: chame com texto igual a 'ok'.",
        "parameters": {
            "type": "object",
            "properties": {"texto": {"type": "string"}},
            "required": ["texto"],
        },
    },
}

_AJUDA_SEM_CHAVE = """\
Falta a chave do modelo (LLM_API_KEY).
Como configurar:
  1. Crie uma chave gratuita no provedor (o padrão é o Google AI Studio, plano gratuito).
  2. Copie .env.example para .env e preencha LLM_API_KEY=<sua chave>. O .env não vai para o Git.
  3. Rode de novo: python -m analista.verificar_modelo
Para outro provedor compatível com a API da OpenAI, ajuste também LLM_BASE_URL e LLM_MODEL."""


def verificar(
    config: Configuracao,
    modelo: ModeloLLM | None = None,
    saida: Callable[[str], None] = print,
) -> int:
    """Roda a verificação e devolve o código de saída. `modelo` permite injetar um falso."""
    if not config.llm_api_key:
        saida(_AJUDA_SEM_CHAVE)
        return 2
    modelo = modelo or criar_modelo(config)
    if modelo is None:  # defensivo: criar_modelo só devolve None sem chave
        saida(_AJUDA_SEM_CHAVE)
        return 2

    saida(f"Modelo: {config.llm_model}")
    saida(f"Endereço: {config.llm_base_url}")
    mensagens = [{"role": "user", "content": "Chame a ferramenta responder_ok com o texto 'ok'."}]
    try:
        resposta = modelo.completar(mensagens, [_FERRAMENTA_DE_TESTE])
    except ErroDoProvedor as erro:
        # 429 e modelo inexistente só aparecem depois de a chave ser aceita.
        chave_ok = erro.tipo in ("limite", "modelo")
        saida("chave: OK" if chave_ok else f"chave: erro - {erro}")
        if erro.tipo == "modelo":
            saida(f"modelo: erro - {erro}")
        else:
            saida("modelo: não verificado")
            if chave_ok or erro.tipo in ("conexao", "outro"):
                saida(f"detalhe: {erro}")
        saida("tool calling: não verificado")
        return 1

    saida("chave: OK")
    saida("modelo: OK")
    if resposta.chamadas and resposta.chamadas[0].nome == "responder_ok":
        saida("tool calling: OK")
        return 0
    saida("tool calling: erro - o modelo respondeu em texto em vez de chamar a ferramenta")
    saida("Este modelo não serve para o agente; escolha outro em LLM_MODEL.")
    return 1


def main() -> int:
    # Acentos no console do Windows: não depender da página de código do terminal.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return verificar(carregar_configuracao())


if __name__ == "__main__":
    sys.exit(main())
