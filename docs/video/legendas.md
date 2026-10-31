# Transcrição do vídeo

O vídeo (`analista-pedidos-ia.mp4`) tem narração em português do Brasil, com voz sintética (gerada por texto), e legendas em português gravadas na imagem; esta é a transcrição.

Dados sintéticos de um restaurante fictício; é uma demonstração, não um produto.

## Capítulos

| Tempo | Capítulo |
|---|---|
| 0:00 | Visão geral |
| 0:07 | O problema |
| 0:16 | Arquitetura |
| 0:31 | Chamada de ferramentas na prática |
| 0:48 | Banco de dados |
| 0:54 | Segurança em camadas |
| 1:05 | Avaliação |
| 1:25 | Construído com IA |
| 1:39 | Demo ao vivo |
| 2:01 | Fecho |

## Narração e legendas

| Tempo | Narração e legenda |
|---|---|
| 0:00 | Este é o Analista de pedidos com IA: um agente que responde, em português, perguntas sobre os pedidos de um restaurante. |
| 0:07 | O dono de um restaurante quer saber se a entrega piorou, mas não sabe consultar o banco. |
| 0:12 | O agente escreve a consulta por ele e mostra a resposta com a consulta usada. |
| 0:16 | A pergunta entra num agente escrito em Python puro, que conversa com o modelo de linguagem. |
| 0:21 | O modelo não responde direto: pede ferramentas (tool calling). Uma descreve o banco, e a outra executa a consulta. |
| 0:27 | Toda consulta passa por cinco camadas de segurança antes de chegar ao banco. |
| 0:32 | Um caso real: em qual bairro o tempo de entrega mais subiu de uma semana para a outra? |
| 0:36 | Passo um: o modelo pede o esquema do banco. |
| 0:39 | Passo dois: ele escreve a consulta e a executa, passando pelas cinco camadas. |
| 0:43 | Passo três: ele responde que foi o Calhau e mostra a consulta usada. |
| 0:48 | O banco tem 8.000 pedidos sintéticos, com dois padrões plantados de propósito para haver o que descobrir. |
| 0:54 | A segurança não está nas instruções do modelo, e sim no código: são cinco camadas, e qualquer uma pode recusar. |
| 1:00 | Os testes tentam ações proibidas, como apagar tabelas, e conferem que tudo é recusado. |
| 1:06 | A avaliação faz 25 perguntas, três vezes cada, e compara o resultado com o de uma consulta de referência. |
| 1:12 | Resultado: 88% no geral, 100% nas 22 perguntas objetivas e 0% nas 3 ambíguas. |
| 1:20 | Atenção: são perguntas do próprio projeto; isso não prova que o agente acerta perguntas novas. |
| 1:26 | O projeto foi construído com agentes Claude: um implementava cada tarefa, escrevendo o teste antes, e outro revisava. |
| 1:32 | Cada falha achada virou correção com teste: hoje são 478 testes, todos com um modelo falso. |
| 1:39 | Agora, ao vivo: quantos pedidos foram cancelados? |
| 1:43 | O modelo leva alguns segundos; aqui, a espera foi acelerada. |
| 1:46 | A resposta: 473 pedidos, igual ao banco, com a consulta logo abaixo. |
| 1:52 | Agora, um pedido que não deve ser atendido: apagar os pedidos cancelados. |
| 1:58 | O agente recusa, porque o acesso é só de leitura. |
| 2:02 | Dados sintéticos, só demonstração. O código, o painel e as especificações estão no GitHub. |
