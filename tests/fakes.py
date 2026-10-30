"""Dublês de teste. Nenhum deles faz rede."""

import copy

from analista.llm import MensagemModelo


class ModeloFalso:
    """Devolve as mensagens do roteiro em ordem; um item que seja exceção é levantado.

    `mensagens` guarda uma cópia da conversa recebida em cada chamada (a conversa real é uma
    lista que o agente continua alterando). Pedir mais do que o roteiro tem é erro de teste.
    """

    def __init__(self, roteiro: list[MensagemModelo | Exception]):
        self._roteiro = list(roteiro)
        self.mensagens: list[list[dict]] = []
        self.ferramentas: list[list[dict]] = []

    def completar(self, mensagens: list[dict], ferramentas: list[dict]) -> MensagemModelo:
        self.mensagens.append(copy.deepcopy(mensagens))
        self.ferramentas.append(ferramentas)
        if len(self.mensagens) > len(self._roteiro):
            raise AssertionError("o agente chamou o modelo além do roteiro")
        item = self._roteiro[len(self.mensagens) - 1]
        if isinstance(item, Exception):
            raise item
        return item
