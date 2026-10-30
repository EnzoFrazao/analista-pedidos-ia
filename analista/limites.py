"""Limite de perguntas por sessão e teto diário (poupa a cota gratuita do modelo).

O contador do dia fica num JSON `{"data": "AAAA-MM-DD", "n": 0}`. Limitação conhecida: com
várias sessões simultâneas o teto diário é aproximado (ler, somar e gravar não é uma operação
única; sem biblioteca de trava, duas gravações ao mesmo tempo podem perder uma contagem).
"""

import json
import os
import tempfile
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

_TENTATIVAS_GRAVACAO = 5
_ESPERA_S = 0.05


class LimiteDeUso:
    def __init__(
        self,
        sessao_max: int,
        dia_max: int,
        estado: Path,
        hoje: Callable[[], date] = date.today,
    ) -> None:
        self.sessao_max = sessao_max
        self.dia_max = dia_max
        self.estado = estado
        self._hoje = hoje

    def autorizar(self, feitas_na_sessao: int) -> tuple[bool, str]:
        """Diz se mais uma pergunta pode ser feita; se não, a mensagem aponta o limite que estourou."""
        if feitas_na_sessao >= self.sessao_max:
            return False, (
                f"Você já fez as {self.sessao_max} perguntas permitidas nesta sessão. "
                "O limite existe para não esgotar a cota gratuita do modelo."
            )
        if self._feitas_hoje() >= self.dia_max:
            return False, (
                f"O painel atingiu o teto diário de {self.dia_max} perguntas. "
                "O contador zera amanhã; volte então."
            )
        return True, ""

    def registrar(self) -> None:
        """Soma 1 no contador do dia. Falha de gravação não derruba a pergunta do usuário."""
        dados = {"data": self._hoje().isoformat(), "n": self._feitas_hoje() + 1}
        try:
            self._gravar(dados)
        except OSError:
            pass  # o teto é aproximado; perder uma contagem é melhor que travar a pergunta

    def _feitas_hoje(self) -> int:
        try:
            dados = json.loads(self.estado.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(dados, dict) or dados.get("data") != self._hoje().isoformat():
            return 0
        n = dados.get("n")
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            return 0
        return n

    def _gravar(self, dados: dict) -> None:
        """Grava num temporário da mesma pasta e troca com `os.replace` (escrita atômica)."""
        self.estado.parent.mkdir(parents=True, exist_ok=True)
        descritor, nome = tempfile.mkstemp(
            dir=self.estado.parent, prefix=self.estado.name + ".", suffix=".tmp"
        )
        temporario = Path(nome)
        try:
            with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
                json.dump(dados, arquivo)
            for tentativa in range(_TENTATIVAS_GRAVACAO):
                try:
                    os.replace(temporario, self.estado)
                    return
                except PermissionError:
                    # No Windows outro processo pode estar com o destino aberto.
                    if tentativa == _TENTATIVAS_GRAVACAO - 1:
                        raise
                    time.sleep(_ESPERA_S)
        finally:
            temporario.unlink(missing_ok=True)
