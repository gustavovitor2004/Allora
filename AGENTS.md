# Allora — guia do projeto

App de desktop em **Python 3 + PySide6 (Qt6)** para Windows. Três funções: baixar
vídeos (yt-dlp), converter mídia (ffmpeg) e trabalhar com documentos (digitalizar
fotos de papel + converter/mesclar PDF/DOCX/TXT/imagem).

Interface **100% em português**. Comentários e docstrings do código **em inglês**.

Repositório: `gustavovitor2004/Allora` · Versão atual: **1.3.4**

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
pode levantar exceção. É por isso que o yt-dlp recebe um `_QuietLogger` explícito em
`downloader.py`.

Dependências externas ficam em `tools/` ao lado do .exe (ffmpeg, poppler) — não são
empacotadas. `config.json` também fica ao lado do .exe.

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
    scanner_engine.py      Pipeline OpenCV puro (warp de perspectiva + realçe). Sem Qt aqui.
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
- Cancelamento: levanta `KeyboardInterrupt` de dentro do progress hook do yt-dlp — é a
  forma padrão de abortar limpo no meio da transferência.
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

**Médio**

- `DownloadManager._fetch_metadata()` cria uma thread por URL, fora do `active_threads`
  e sem respeitar `max_simultaneous`. Colar 50 links dispara 50 `extract_info`
  simultâneos. Precisa de um pool limitado — é mudança de arquitetura, não conserto
  pontual.

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
  `DownloadItem`/`ConversionItem`, chaves EN em `DocConversionItemWidget._CARD_STATUS`).
  Unificar mexe em fluxo de UI dos três lugares ao mesmo tempo.

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
- **Ícone fantasma na caixa de URL (parte 2)**: o `_detach_windows_ime()` que tentava
  resolver isso via Win32 foi **removido** — nunca foi a causa (era a scrollbar do Qt) e
  ainda quebrava digitação CJK. Não reintroduza.
