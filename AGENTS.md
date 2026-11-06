# Allora — guia do projeto

App de desktop em **Python 3 + PySide6 (Qt6)** para Windows. Três funções: baixar
vídeos (yt-dlp), converter mídia (ffmpeg) e trabalhar com documentos (digitalizar
fotos de papel + converter/mesclar PDF/DOCX/TXT/imagem).

Interface **100% em português**. Comentários e docstrings do código **em inglês**.

Repositório: `gustavovitor2004/Allora` · Versão atual: **1.6.0**

---

## Como rodar e buildar

```bash
pip install -r requirements.txt
python src/main.py            # rodar do fonte
```

```powershell
.\build_exe.ps1               # gera dist\Allora\Allora.exe (PyInstaller --onedir --windowed)
```

O build é `--windowed`: **não existe console**. `sys.stdout` é `None`, então `print()`
não aparece em lugar nenhum e qualquer biblioteca que escreva direto em stdout/stderr
pode levantar exceção. O yt-dlp costumava precisar de um `_QuietLogger` explícito por
causa disso; rodando como subprocesso o problema deixa de existir por construção — o
processo filho tem os próprios pipes e nunca compartilha stream com a GUI.

Dependências externas ficam em `tools/` ao lado do .exe (**ffmpeg, poppler, yt-dlp**) —
não são empacotadas. `config.json` também fica ao lado do .exe.

O yt-dlp é excluído do bundle de propósito (`--exclude-module yt_dlp` em `build_exe.ps1`).
Ele quebra sempre que um site muda de extrator, e congelado dentro do .exe o usuário não
teria como consertar sem esperar uma release nova. Como binário próprio, `yt-dlp.exe -U`
atualiza no lugar. **Isso não reduz o pacote** — o bundle cai só 2 MB e o yt-dlp.exe pesa
17 MB. O ganho é exclusivamente poder atualizar.

---

## Estrutura

```
src/
  main.py                  Entrada. QApplication, ícone da taskbar, dialog de erro na inicialização.
  ui.py                    Janela principal, fila de downloads, aba de conversão, Configurações. (~1700 linhas)
  theme.py                 12 temas + gerador da folha QSS aplicada ao app inteiro.
  settings.py              config.json: carga, validação de tipos, gravação atômica.
  version.py               APP_VERSION + política de semver.
  queue_manager.py         QueueManager base: registro de itens, pause/start, thread despachante.
  downloader.py            yt-dlp: fila, workers, prefetch de metadados.
  converter.py             ffmpeg: mesma base de fila do downloader.
  icons.py                 Ícones SVG renderizados sob demanda na cor do tema (memoizado).
  utils.py                 Detecção de ffmpeg/poppler/plataforma, formatação, nomes de arquivo seguros.
  startup_check.py         Verificação de dependências na inicialização.
  documentos/
    tab_documentos.py      Aba Documentos: editor de cantos arrastáveis + lista de conversão.
    scanner_engine.py      Pipeline OpenCV puro (warp de perspectiva + realce). Sem Qt aqui.
    converter.py           Conversão local de documentos e mesclagem de PDFs.
    workers.py             QThreads da aba Documentos.
assets/                    icon.ico, logo.png (empacotados via --add-data)
```

---

## Arquitetura

### Filas de download e conversão (`queue_manager.py`, `downloader.py`, `converter.py`)

`QueueManager` (em `queue_manager.py`) é a base compartilhada pelos dois managers —
antes eram ~120 linhas idênticas duplicadas, e o mesmo bug crítico de corrida existia
nos dois e precisou ser corrigido duas vezes.

- `DownloadItem` / `ConversionItem`: dado puro + um `threading.Event` de cancelamento.
- `DownloadManager` / `ConversionManager`: herdam de `QueueManager` (um `QObject` que
  emite sinais Qt de dentro das threads worker). O PySide6 marshala automaticamente
  para a thread da GUI (conexão enfileirada), então a interface nunca faz polling.
  Cada subclasse implementa quatro hooks (`_waiting_status`, `_done_statuses`,
  `_start_item`, `_make_worker_thread`) e só o que é realmente diferente.
- Uma thread **despachante** decide quando iniciar o próximo item, respeitando
  `max_simultaneous` e as flags `running`/`paused`.
- O yt-dlp roda como **subprocesso**, não como biblioteca. Tudo que antes vinha por
  objetos Python agora sai pelo stdout: `--dump-single-json` para metadados,
  `--progress-template` para progresso, `--print after_move:` para caminho final e
  altura. Ver `_run_ytdlp`/`_fetch_metadata` em `downloader.py`.
- O prefetch de metadados roda num **pool limitado** (`METADATA_WORKERS = 4` em
  `downloader.py`), não mais uma thread por URL. O número é medido, não chutado: cada
  `--dump-single-json` tem pico de ~83 MB e leva ~3,1s, então colar 50 links disparava
  ~4,1 GB de pico simultâneo. Com 4, o teto é ~330 MB.
- Cancelamento: mata a **árvore** do processo (`utils.terminate_process_tree`), não só
  o processo que iniciamos — ver a armadilha sobre o onefile mais abaixo. É mais direto
  que o caminho antigo, que só conseguia levantar `KeyboardInterrupt` de dentro do
  progress hook e portanto tinha que esperar o próximo callback.
- "Pausar" só impede o despachante de iniciar **novos** itens; o que já está baixando
  termina naturalmente (o yt-dlp não expõe pausa real). Retomar é `resume()` — nunca
  escreva `manager.paused` direto da UI.

### Aba Documentos

Usa **QThread** (`workers.py`), não o padrão de manager acima. É proposital: as ações
são one-shot (start → progress → finished), não uma fila contínua.

O worker de conversão pode pedir a senha de um PDF ao usuário via
`BlockingQueuedConnection` — a thread worker bloqueia esperando a GUI, nunca o
contrário. Por isso `ConvertSubTab.shutdown()` desconecta esse sinal antes de esperar:
um `wait()` sem limite a partir da GUI daria deadlock.

---

## Invariantes obrigatórias

Estas quatro regras já foram quebradas e causaram bugs reais. Não quebre de novo.

### 1. Nunca mexer em `items`/`order` sem o lock do manager

A interface **não pode** fazer `manager.items.pop(...)` ou `manager.order.remove(...)`
direto. Use os métodos do próprio manager (`add_url`, `remove_item`, `clear_completed`,
`pause`, `resume`), que fazem tudo sob `_lock`.

**Por quê:** a thread despachante percorre `items` e `order` sob esse lock. Se a GUI
mexer sem ele, existe uma janela onde `order` tem um id que `items` já não tem — o
despachante levanta `KeyError`, nada captura isso dentro do laço, **a thread morre e a
fila para de funcionar em silêncio pelo resto da sessão**.

Toda compreensão que indexa `self.items[i]` iterando `self.order` precisa da guarda
`if i in self.items`. Isso vale para **todas** — `clear_completed()` era a única sem a
guarda e foi corrigida em 1.3.4.

### 2. Nunca usar stylesheet inline para cor

Todo o tema vive em `theme.py`, aplicado como uma única folha QSS no app inteiro.
Widgets que mudam de estado usam **propriedade dinâmica** (`status`, `variant`) seguida
de `repolish(widget)`.

Estilo inline com cor quebra nos temas claros. O último caso pendente
(`result_label` em `tab_documentos.py`) foi resolvido — agora usa
`QLabel#ResultPreview` em `theme.py`. Não reintroduza esse padrão.

**Exceção: cursor.** QSS do Qt **não tem** a propriedade `cursor` — escrever
`cursor: pointer;` na folha de estilo não dá erro, simplesmente não faz nada.
O cursor de mão nos botões vem de `theme.apply_click_cursor(root)`, chamado no
fim do `__init__` de `MainWindow` e `SettingsDialog` (a função varre a árvore
com `findChildren`, então só enxerga o que já existe). Widgets de linha criados
depois — `make_row_action_button()` em `ui.py` e o `delete_btn` de
`DocConversionItemWidget` — setam o próprio cursor. Se você criar um novo tipo
de linha com botão, faça o mesmo.

Só `QPushButton` recebe a mão: checkbox, radio e combo ficam com a seta, igual
ao padrão do Windows. Botão desabilitado não precisa de tratamento — o Qt sobe
até o ancestral habilitado mais próximo na hora de escolher o cursor, então ele
mostra a seta sozinho (verificado no Windows lendo o `GetCursorInfo`).

### 3. Ícones são pixmaps assados — QSS não recolore

`make_icon()` renderiza o SVG com a cor já embutida. Ao trocar de tema, **todo widget
com ícone precisa ser reconstruído explicitamente** — ver `MainWindow._refresh_icon_theme()`
e os `set_theme()` de cada componente. Se você adicionar um botão com ícone, registre-o
para receber o refresh de tema.

### 4. Nada pesado na thread da GUI

I/O de arquivo, rede, `subprocess`, OpenCV, yt-dlp — tudo em worker. A janela é
frameless: quando ela congela, não redesenha e parece morta.

As threads de background rodam em `THREAD_PRIORITY_BELOW_NORMAL` no Windows
(`_lower_thread_priority()` em `downloader.py`) para o escalonador sempre preferir a
thread da GUI. Isso resolveu um travamento real no início de vídeos longos.

---

## Convenções

- **Janelas frameless**: `Qt.FramelessWindowHint`. A classe `Header` faz as vezes de
  barra de título (arrastar move via `startSystemMove()`, duplo-clique maximiza).
  Redimensionar pela borda é reimplementado em `MainWindow.nativeEvent` respondendo
  `WM_NCHITTEST`. Maximizar é manual (`_toggle_maximize`) porque o `showMaximized()`
  nativo cobre a barra de tarefas numa janela sem moldura.
- **`resource_path()` vs `project_root()`**: `resource_path()` é para assets empacotados
  (usa `sys._MEIPASS`, que aponta para `_internal/`). `project_root()` é para arquivos
  graváveis do usuário (config.json, tools/) e aponta para a pasta do .exe. **Não são a
  mesma coisa** — trocar um pelo outro quebra o logo no build.
- **Caminhos**: sempre `os.path.join`. Nunca separador hardcoded.
- **Erros**: nada de falhar em silêncio. Erros de item vão para o próprio item da fila
  (status + mensagem); erros globais vão para `QMessageBox`.
- **Diálogos na inicialização**: nada de `QMessageBox` dentro de `MainWindow.__init__` —
  ele roda antes de `window.show()`, então o usuário vê um modal sozinho numa área de
  trabalho vazia. Use `QTimer.singleShot(0, ...)`.
- **Largura fixa em pixel não sobrevive à troca de tema**: cada tema traz a própria
  fonte (Impact, Consolas, Constantia...), então a mesma string tem largura diferente
  em cada um. Para dimensionar algo pelo texto, meça com `fontMetrics()` do próprio
  widget e **recalcule em `_refresh_icon_theme()`** — ver `_fit_quality_combo()`.
  `setMinimumContentsLength()` também não serve: ele reserva N larguras de caractere
  *médio*, o que pediu 282px para um texto que desenha em 167.
- **Rótulo com caminho longo**: use `ElidedLabel` (`ui.py`). Um `QLabel` comum tem
  `minimumSizeHint` igual à largura total do texto, então um caminho longo entorta a
  linha inteira. O `ElidedLabel` encolhe mostrando o **fim** do texto, que é a parte
  informativa, e guarda o original no tooltip.
- **Lint**: `pyflakes` limpo em todos os módulos. Mantenha assim.

---

## Versionamento

`APP_VERSION` em `src/version.py` é a fonte única. **Bump obrigatório a cada mudança**,
seguindo semver:

- **MAJOR** — quebra comportamento existente
- **MINOR** — funcionalidade nova, compatível
- **PATCH** — correção de bug, ajuste visual, mudança interna

A versão aparece em Configurações (rodapé) e no diálogo "Sobre".

---

## Problemas conhecidos em aberto

A lista levantada na auditoria de 01/09/2026 foi **toda resolvida** em duas levas
(1.3.3 e 1.3.4) — ver `CLAUDE_IMPLEMENTATION_REPORT.md` e `AUDIT_REPORT.md`. O que
segue abaixo é o que continua em aberto **hoje**, deixado de fora porque muda
arquitetura ou exige decisão de produto.

**Baixo**

- `convert_file(..., progress_cb=None)` é um ponto de extensão morto: nenhum chamador
  passa o callback, então os `if progress_cb:` em `_pdf_to_images`/`_pdf_to_txt` nunca
  rodam. Custo zero; mantido de propósito para quando a aba Documentos mostrar
  progresso por página.
- O botão de claro/escuro em Configurações usa emoji (`☀`/`🌙`) como texto, que é
  exatamente o que `icons.py` existe para evitar. Os ícones `sun`/`moon` foram
  removidos por estarem mortos; refazer isso é mudança visual, não correção.
- Ao remover uma linha da fila, `QListWidget.takeItem()` descarta o `QListWidgetItem`,
  mas a posse do widget associado (`setItemWidget`) fica a cargo do Qt. Não confirmado
  como vazamento; se for investigar, meça antes de mexer — `deleteLater()` num widget
  que o Qt também destrói é pior que o problema.

**Dívida estrutural**

- Três vocabulários de status diferentes para o mesmo conceito (strings PT em
  `DownloadItem`/`ConversionItem`, chaves EN em `DocConversionItemWidget._CARD_STATUS`,
  e os buckets de cor do `theme.py`). Unificar mexe em fluxo de UI dos três lugares ao
  mesmo tempo.

  **Decisão de 05/11/2026: adiar deliberadamente.** Não é esquecimento — Claude e
  ChatGPT avaliaram em separado e chegaram à mesma conclusão (ver `CONVERSA.md`,
  entradas de 04 e 05/11). O motivo: unificar muda transições de estado, ações
  disponíveis por linha e aparência nas três áreas de uma vez, num app **sem nenhum
  teste automatizado** — o risco de regressão não se paga, porque a duplicação não
  causa bug hoje, só atrito de manutenção.

  **Se algum dia houver motivo concreto para mexer**, o caminho acordado é:

  1. Separar em três camadas: estado interno (chave estável), rótulo em português
     (exibição) e categoria visual do tema (cor). Hoje os três estão colados.
  2. Começar pelas duas filas que já compartilham o `QueueManager` — são as que têm
     base comum e o menor salto.
  3. Deixar a aba Documentos com o fluxo próprio; ela não passa pelo `QueueManager` e
     arrastá-la para a mesma mudança triplica a superfície de risco.

  Antes de começar, combine o vocabulário no `CONVERSA.md` — mexer primeiro e alinhar
  depois garante retrabalho.

---

## Armadilhas já resolvidas (não reintroduza)

- **Ícone da taskbar**: janela frameless não herda o ícone do .exe. Precisa de
  `app.setWindowIcon()` explícito em `main.py`.
- **QLabel com fundo errado**: a regra genérica `QWidget{background-color:bg_primary}`
  pintava caixa opaca atrás de todo QLabel. Resolvido com
  `QLabel { background-color: transparent; }` logo após a regra base.
- **Ícone fantasma na caixa de URL**: não era IME nem teclado virtual do Windows — eram
  os botões da scrollbar do próprio Qt. Resolvido com `ScrollBarAlwaysOff`.
- **PDF digitalizado travando o app**: `pdf2docx` trava indefinidamente em PDFs só de
  imagem. Roda com timeout de 180s em processo filho de verdade (`multiprocessing`),
  não thread — thread abandonada continua rodando e escrevendo o arquivo depois do erro.
- **Modo Preto e branco ruidoso**: threshold adaptativo com janela fixa de 11px reagia à
  textura do papel em fotos de alta resolução. Agora tem denoise antes e janela
  proporcional à resolução.
- **config.json corrompido**: gravação era não-atômica (`open(..., "w")` truncava antes
  de escrever). Agora é temp + `os.replace`. E `Settings.from_dict()` valida tipos —
  antes, um `null` no config impedia o app de abrir.
- **Vazamento do SettingsDialog**: dialog parented à janela principal sobrevive ao escopo
  Python. Precisa de `deleteLater()` explícito.
- **QThread destruída viva = abort do processo**: `self.worker = NovoWorker(...)` derruba
  a referência da anterior. Se ela ainda estiver rodando, o PySide6 destrói a QThread em
  execução e o Qt mata o processo. Todo ponto que troca um worker passa por
  `_retire_worker()` (`tab_documentos.py`), que estaciona o antigo em `_ORPHANED_WORKERS`
  até ele terminar sozinho. Nunca só reatribua.
- **`queue_idle` é evento de borda, não de nível**: o despachante reavalia a condição a
  cada 0.4s; sem o latch `_idle_emitted` ele reemitia o sinal para sempre depois que a
  fila esvaziava. Se adicionar sinal parecido, trave a borda.
- **Cancelar tem que sempre chegar a um status terminal**: se um caminho de cancelamento
  sai do worker sem gravar `STATUS_CANCELLED`/`STATUS_ERROR`, a linha fica presa em
  "Baixando..." para sempre e o botão de cancelar não faz mais nada.
- **`os.startfile()` não abre caminho curto 8.3**: com um componente no formato
  `GUSTAV~1.GOM` ele **não faz nada** — não abre janela e não levanta exceção, então o
  código acha que deu certo. Comprovado lado a lado: caminho curto abriu zero janelas,
  o mesmo caminho expandido abriu normalmente. `utils.open_folder()` passa por
  `os.path.realpath()` antes, que é o que expande. O `explorer` por linha de comando
  não tem esse problema.
- **`terminate()` não mata o yt-dlp — mata só o lançador**: o `yt-dlp.exe` distribuído
  é PyInstaller *onefile*. O processo que a gente inicia apenas se desempacota e gera um
  **filho**, e é o filho que trabalha. Matar o pai deixa o filho vivo: comprovado no
  Windows, com o filho ainda rodando 2,5s depois do `terminate()`. Na prática, cancelar
  um download encerrava a linha na interface enquanto o download continuava, e fechar o
  app deixava processos órfãos. Todo encerramento passa por
  `utils.terminate_process_tree()`, que usa `taskkill /T`. Não volte a chamar
  `process.terminate()` direto num binário onefile.
- **`--progress` é obrigatório ao chamar o yt-dlp por subprocesso**: com stdout num pipe
  (que é sempre o nosso caso) ele suprime o relatório de progresso por completo, e o
  `--progress-template` simplesmente nunca emite nada. A barra ficaria parada em 0% sem
  erro nenhum. Comprovado contra o binário real.
- **`--encoding utf-8` também é obrigatório**: por padrão o yt-dlp escreve o stdout no
  codepage ANSI do Windows (cp1252 aqui). Sem a flag, um título acentuado volta como
  mojibake e o `output_path` aponta para um nome que não bate com o arquivo real em
  disco — que, esse sim, é gravado corretamente. O "Abrir pasta" abriria no vazio.
  `PYTHONIOENCODING` não resolve: o binário congelado ignora.
- **Ícone fantasma na caixa de URL (parte 2)**: o `_detach_windows_ime()` que tentava
  resolver isso via Win32 foi **removido** — nunca foi a causa (era a scrollbar do Qt) e
  ainda quebrava digitação CJK. Não reintroduza.
