"""Banco SQLite minúsculo, com o mesmo esquema do gerador, para testar casos de borda."""

import sqlite3
from pathlib import Path

_ESQUEMA = """
CREATE TABLE itens_cardapio (
    id INTEGER PRIMARY KEY, nome TEXT NOT NULL, categoria TEXT NOT NULL, preco REAL NOT NULL
);
CREATE TABLE pedidos (
    id INTEGER PRIMARY KEY, data_hora TEXT NOT NULL, bairro TEXT NOT NULL, canal TEXT NOT NULL,
    status TEXT NOT NULL, valor REAL NOT NULL, taxa_entrega REAL NOT NULL,
    tempo_entrega_min INTEGER, nota INTEGER
);
CREATE TABLE itens_pedido (
    pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
    item_id INTEGER NOT NULL REFERENCES itens_cardapio(id),
    quantidade INTEGER NOT NULL, preco_unit REAL NOT NULL, PRIMARY KEY (pedido_id, item_id)
);
"""


def criar_banco_minimo(caminho: Path, pedidos: list[tuple[str, str, str, int | None]]) -> Path:
    """Cada pedido é (data_hora, bairro, status, tempo_entrega_min); o resto é fixo."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho)
    try:
        conn.executescript(_ESQUEMA)
        conn.executemany(
            "INSERT INTO pedidos (data_hora, bairro, canal, status, valor, taxa_entrega,"
            " tempo_entrega_min, nota) VALUES (?, ?, 'app_proprio', ?, 50.0, 5.0, ?, NULL)",
            pedidos,
        )
        conn.commit()
    finally:
        conn.close()
    return caminho
