# Transcrição do vídeo

O vídeo (`analista-pedidos-ia.mp4`) tem narração em português do Brasil, com voz sintética (gerada por texto), e legendas em português gravadas na imagem; esta é a transcrição.

Os dados do restaurante foram criados para o desenvolvimento do projeto.

## Capítulos

| Tempo | Capítulo |
|---|---|
| 0:00 | O que é |
| 0:14 | Para que serve |
| 0:29 | Como funciona |
| 0:40 | Segurança |
| 0:51 | Resultado da avaliação |
| 1:12 | Como foi construído |
| 1:26 | Fecho |

## Narração e legendas

| Tempo | Narração e legenda |
|---|---|
| 0:00 | Este é o Analista de pedidos: você pergunta em português sobre os pedidos de um restaurante, e um agente de IA responde. |
| 0:07 | Ele mostra a resposta e a consulta que usou, para você conferir. Aqui: 473 pedidos cancelados. |
| 0:14 | O objetivo: o dono do restaurante entender o que está acontecendo, sem precisar saber consultar um banco de dados. |
| 0:20 | O painel mostra a semana e destaca em âmbar os bairros com entrega mais lenta que a média: nesta semana, o Turu, com cerca de 50 minutos. |
| 0:29 | Por dentro: a pergunta chega ao agente, que escreve a consulta. Antes de chegar ao banco, ela passa por cinco camadas de segurança. |
| 0:36 | O banco é só de leitura, e a resposta volta com a consulta usada. |
| 0:41 | Um pedido que não deve ser atendido: apagar os pedidos cancelados. |
| 0:45 | O agente recusa. A segurança está no código, e os testes tentam apagar dados para conferir que tudo é recusado. |
| 0:52 | Para medir, o agente respondeu 25 perguntas, três vezes cada, e foi comparado com consultas de referência. |
| 0:59 | Resultado: 88% no geral, 100% nas 22 perguntas objetivas e 0% nas 3 ambíguas. |
| 1:06 | Atenção: são perguntas do próprio projeto; isso não prova que o agente acerta perguntas novas. |
| 1:12 | Foi construído com agentes de IA no Claude Code: especificação e plano, um agente que implementa com o teste antes, e outro que revisa. |
| 1:20 | Cada falha achada virou correção com teste: hoje são 480 testes, todos utilizando um modelo de teste. |
| 1:27 | Os dados foram criados para o desenvolvimento do projeto. Para acessar o código, abra os links anexados neste post. |
