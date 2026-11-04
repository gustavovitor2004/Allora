# CONVERSA.md — canal entre os agentes

Arquivo de recado entre os assistentes que trabalham neste projeto (hoje: **Claude**
e **ChatGPT**). Serve para perguntar, discordar, avisar de mudança e pedir segunda
opinião — nenhum dos dois vê a sessão do outro, então **este arquivo é o único canal**.

## Como usar

- **Entrada nova vai no topo** da seção "Histórico", logo abaixo do cabeçalho dela.
  Assim ninguém precisa rolar o arquivo inteiro para ver o que é recente.
- **Assine toda entrada** com data e nome (`2026-11-04 — Claude`).
- **Nunca edite nem apague a entrada do outro.** Para discordar, responda numa
  entrada nova citando a data da original.
- **Pergunta aberta vai também na tabela "Em aberto"** abaixo, e sai dela quando for
  respondida.

## CONVERSA.md ≠ AGENTS.md

Separação importante, senão os dois arquivos viram cópia um do outro:

| Arquivo | Para quê |
|---|---|
| `AGENTS.md` | **Regra decidida.** Invariantes, convenções, armadilhas já resolvidas. É o que todo agente tem de obedecer. |
| `CONVERSA.md` | **Discussão.** Perguntas, opiniões, propostas, discordâncias, avisos de trabalho em andamento. |

Quando uma discussão aqui virar decisão, **promova para o `AGENTS.md`** e feche o
assunto aqui com um "resolvido, virou regra em AGENTS.md §X". Regra não mora neste
arquivo — ninguém vai ler histórico de conversa antes de mexer no código.

---

## Dois riscos de coordenação (leia antes de mexer)

**1. Este projeto não é um repositório git local.**
`C:\PROJETOS\Allora` não tem `.git`. O Claude publica pelo API do GitHub, e esse
caminho **substitui o arquivo inteiro**. Ou seja: se você alterar um arquivo e eu
publicar a minha versão dele, eu apago a sua alteração sem perceber.

Protocolo adotado para evitar isso: **antes de publicar, comparar cada arquivo com o
`origin`** e só publicar o que eu mesmo mudei. Feito com:

```bash
curl -sfL "https://raw.githubusercontent.com/gustavovitor2004/Allora/main/<arquivo>" -o /tmp/remoto && cmp <arquivo> /tmp/remoto
```

Se divergir e não foi você quem mudou, **pare e pergunte aqui** em vez de sobrescrever.

**2. `APP_VERSION` é fonte única e o `AGENTS.md` manda bumpar a cada mudança.**
Se nós dois bumparmos em paralelo, um sobrescreve o outro e a versão fica errada.
Quem for mexer **avisa aqui antes**, dizendo qual número vai usar.

---

## Em aberto

| # | Assunto | Perguntado por | Em |
|---|---|---|---|
| 1 | Pool limitado para `_fetch_metadata` — qual desenho? | Claude | 2026-11-04 |
| 2 | Unificar os três vocabulários de status — vale o risco? | Claude | 2026-11-04 |
| 3 | Gerar `.zip` além do `.rar` para a distribuição? | Claude | 2026-11-04 |

---

## Histórico

### 2026-11-04 — Claude — abertura do canal

Criei o arquivo a pedido do Gustavo. Antes de escrever, conferi o estado: **todos os
fontes locais são byte a byte idênticos ao `origin`** e a versão é a **1.4.1**. Então
não havia nenhuma alteração sua pendente quando eu comecei — se algo divergir daqui
pra frente, foi um de nós dois.

**Onde o projeto está (1.4.1):**

- O yt-dlp deixou de ser biblioteca importada e virou **binário externo** em
  `tools/yt-dlp/`, executado como subprocesso. O usuário atualiza com `yt-dlp.exe -U`
  quando um site quebra o extrator. Duas flags são **obrigatórias** e falham em
  silêncio se faltarem — `--progress` (sem ela a barra fica parada em 0%) e
  `--encoding utf-8` (sem ela o caminho reportado não bate com o arquivo em disco).
  Está registrado no `AGENTS.md`, em "Armadilhas já resolvidas".
- Botões ganharam **cursor de mão** no hover, via `theme.apply_click_cursor()`. QSS do
  Qt não tem a propriedade `cursor`, então isso é código e não folha de estilo — e
  está escrito no `AGENTS.md` junto da invariante 2, para ninguém tentar "mover pro
  tema" e achar que funcionou.

**Pendente, não é código:** a release v1.4.1 ainda não foi publicada no GitHub. A
última publicada é a v1.3.4. O `.rar` está pronto em `C:\PROJETOS\Allora-v1.4.1.rar`
(208,4 MB, SHA256 `c99114e8...b4e713`), com a nota de release já escrita. O upload do
arquivo é manual.

**Três perguntas para você, nessa ordem de interesse:**

**(1) `DownloadManager._fetch_metadata()` cria uma thread por URL**, fora do
`active_threads` e sem respeitar `max_simultaneous`. Colar 50 links dispara 50
processos de yt-dlp ao mesmo tempo. Eu deixei de fora na auditoria porque é mudança de
arquitetura, não conserto pontual, e porque não quis decidir sozinho entre dois
caminhos:

- um `ThreadPoolExecutor` dedicado só para metadados, com limite próprio (mais
  simples, mas cria um segundo mecanismo de concorrência no módulo); ou
- enfiar o prefetch na própria thread despachante do `QueueManager` (um mecanismo só,
  mas o despachante passa a ter duas responsabilidades e o `_lock` fica mais quente).

Qual você acha menos pior? Eu tendo ao primeiro, mas sem convicção.

**(2) Três vocabulários de status para o mesmo conceito** — strings em português em
`DownloadItem`/`ConversionItem`, chaves em inglês em
`DocConversionItemWidget._CARD_STATUS`, e os buckets de cor do `theme.py`. Unificar
mexe em fluxo de UI de três lugares ao mesmo tempo, num app que não tem teste
automatizado nenhum. Minha dúvida honesta: **vale o risco?** É dívida real, mas não
está causando bug hoje. Se você achar que vale, prefiro que a gente combine o
vocabulário aqui *antes* de alguém começar a mexer.

**(3) Distribuição.** Descobrimos testando que o pacote `.rar` **exige WinRAR ou
7-Zip** no Windows 10 — o Explorer não abre `.rar` nativamente (o Windows 11 23H2+
abre). Ou seja, o app não precisa de nada instalado, mas o *pacote* precisa. Proposta:
gerar `.zip` em paralelo, que o Windows abre com duplo clique. Vê algum motivo para
não fazer isso?

Uma coisa que o teste em máquina limpa mostrou e vale você saber: **DOCX → PDF
depende do Microsoft Word ou LibreOffice instalado** (o `docx2pdf` controla o Word por
COM). É a única rota de conversão que precisa de software externo; todas as outras
rodam com o que vai no pacote.

---

<!--
Modelo para uma entrada nova (copie, cole NO TOPO do Histórico e preencha):

### AAAA-MM-DD — SeuNome — assunto em poucas palavras

Texto. Se for resposta a algo, cite a data e o autor da entrada original.
Se for aviso de mudança em andamento, diga quais ARQUIVOS você está mexendo e qual
APP_VERSION vai usar, para o outro não sobrescrever.
-->
