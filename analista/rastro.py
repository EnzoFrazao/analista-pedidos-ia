"""Rastro de uma execução: um arquivo JSONL, uma linha JSON por evento."""

import json
import uuid
from datetime import datetime
from pathlib import Path


class Rastro:
    """Um arquivo `<pasta>/<AAAAMMDD-HHMMSS>-<id curto>.jsonl` por instância.

    O arquivo só nasce no primeiro `registrar`, para não deixar arquivo vazio de quem nunca
    escreveu. O chamador decide o que entra: aqui não há chave nem prompt, só o que for passado.
    """

    def __init__(self, pasta: Path):
        self._pasta = Path(pasta)
        agora = datetime.now()
        self.arquivo = self._pasta / f"{agora:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.jsonl"

    def registrar(self, evento: str, **dados: object) -> None:
        linha = {"ts": datetime.now().isoformat(timespec="milliseconds"), "evento": evento}
        linha.update(dados)
        self._pasta.mkdir(parents=True, exist_ok=True)
        with self.arquivo.open("a", encoding="utf-8") as saida:
            saida.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")
