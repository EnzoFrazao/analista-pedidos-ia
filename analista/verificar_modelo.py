"""Teste de fumaça com chave real: `python -m analista.verificar_modelo`.

Faz duas chamadas pequenas (pedido da ferramenta de teste e volta com o resultado dela) e diz
se a chave, o modelo e o tool calling funcionam. Nunca imprime a chave. Sai com 0 (tudo OK),
1 (algo falhou) ou 2 (sem chave).
"""

import sys
from collections.abc import Callable

from analista.agente import mensagem_da_ferramenta, mensagem_do_assistente
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
        _relatar_falha_do_primeiro_turno(erro, saida)
        return 1

    # O primeiro turno só provou que a chave e o modelo respondem.
    saida("chave: OK")
    saida("modelo: OK")
    if not resposta.chamadas or resposta.chamadas[0].nome != "responder_ok":
        saida("tool calling: erro - o modelo respondeu em texto em vez de chamar a ferramenta")
        saida("Este modelo não serve para o agente; escolha outro em LLM_MODEL.")
        return 1

    # Segundo turno: devolve o resultado da ferramenta pelo mesmo caminho que o Agente usa e
    # exige texto. É aqui que o provedor reclama de histórico mal formado (id, assinatura...).
    mensagens += [
        mensagem_do_assistente(resposta),
        mensagem_da_ferramenta(resposta.chamadas[0].id, '{"ok": true}'),
    ]
    try:
        final = modelo.completar(mensagens, [_FERRAMENTA_DE_TESTE])
    except ErroDoProvedor as erro:
        saida(f"tool calling: erro - a volta com o resultado da ferramenta falhou: {erro}")
        return 1
    if final.chamadas or not (final.conteudo or "").strip():
        saida(
            "tool calling: erro - depois do resultado da ferramenta o modelo não respondeu em texto"
        )
        return 1
    saida("tool calling: OK (pedido e volta com o resultado)")
    return 0


def _relatar_falha_do_primeiro_turno(erro: ErroDoProvedor, saida: Callable[[str], None]) -> None:
    """Diz só o que a falha prova. 429 e modelo inexistente chegam depois de a chave ser aceita;
    rede ou erro genérico não provam nada sobre a chave."""
    if erro.tipo == "chave":
        saida(f"chave: erro - {erro}")
        saida("modelo: não verificado")
    elif erro.tipo == "modelo":
        saida("chave: OK")
        saida(f"modelo: erro - {erro}")
    elif erro.tipo == "limite":
        saida("chave: OK")
        saida("modelo: não verificado")
        saida(f"detalhe: {erro}")
    else:  # conexao, outro
        saida("chave: não verificado")
        saida("modelo: não verificado")
        saida(f"detalhe: {erro}")
    saida("tool calling: não verificado")


def main() -> int:
    # Acentos no console do Windows: não depender da página de código do terminal.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return verificar(carregar_configuracao())


if __name__ == "__main__":
    sys.exit(main())
