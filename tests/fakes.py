"""Dublês de teste. Nenhum deles faz rede."""

import copy
import json

from analista.llm import ChamadaFerramenta, MensagemModelo
from avaliacao.perguntas import Pergunta


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


class ModeloOraculo:
    """Responde cada pergunta da avaliação como o gabarito: uma consulta (a primeira referência)
    e a resposta; nas perguntas de recusa, só recusa, sem consultar. Reconhece a pergunta pelo
    texto da mensagem do usuário. `conversas` conta as perguntas iniciadas."""

    TEXTO_RECUSA = "Não posso alterar os dados: eu só consulto. Quer que eu faça uma consulta?"

    def __init__(self, perguntas: list[Pergunta]):
        self._por_texto = {p.texto: p for p in perguntas}
        self.conversas = 0

    def _consulta(self, pergunta: Pergunta) -> str | None:
        return None if pergunta.verificacao == "recusa" else pergunta.referencias[0]

    def completar(self, mensagens: list[dict], ferramentas: list[dict]) -> MensagemModelo:
        primeira_vez = len(mensagens) == 2  # só system + user
        if primeira_vez:
            self.conversas += 1
        pergunta = self._por_texto[mensagens[1]["content"]]
        consulta = self._consulta(pergunta)
        if consulta is None:
            return MensagemModelo(self.TEXTO_RECUSA, ())
        if primeira_vez:
            chamada = ChamadaFerramenta("c1", "executar_sql", json.dumps({"consulta": consulta}))
            return MensagemModelo(None, (chamada,))
        return MensagemModelo("Aqui está o resultado da consulta.", ())


class ModeloRuim(ModeloOraculo):
    """Consulta `SELECT 0` em toda pergunta, inclusive nas de recusa: erra o resultado, mas
    não viola nada."""

    def _consulta(self, pergunta: Pergunta) -> str | None:
        return "SELECT 0"
