# Allora — guia do projeto

App de desktop em **Python 3 + PySide6 (Qt6)** para Windows. Três funções: baixar
vídeos (yt-dlp), converter mídia (ffmpeg) e trabalhar com documentos (digitalizar
fotos de papel + converter/mesclar PDF/DOCX/TXT/imagem).

Interface **100% em português**. Comentários e docstrings do código **em inglês**.

Repositório: `gustavovitor2004/Allora` · Versão atual: **1.3.2**

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
  downloader.py            yt-dlp: fila, thread despachante, workers.
  converter.py             ffmpeg: mesma arquitetura de fila do downloader.
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

### Filas de download e conversão (`downloader.py`, `converter.py`)

Arquitetura **idêntica** nos dois arquivos:

- `DownloadItem` / `ConversionItem`: dado puro + um `threading.Event` de cancelamento.
- `DownloadManager` / `ConversionManager`: `QObject` que emite sinais Qt de dentro das
  threads worker. O PySide6 marshala automaticamente para a thread da GUI (conexão
  enfileirada), então a interface nunca faz polling.
- Uma thread **despachante** decide quando iniciar o próximo item, respeitando
  `max_simultaneous` e as flags `running`/`paused`.
- Cancelamento: levanta `KeyboardInterrupt` de dentro do progress hook do yt-dlp — é a
  forma padrão de abortar limpo no meio da transferência.
- "Pausar" só impede o despachante de iniciar **novos** itens; o que já está baixando
  termina naturalmente (o yt-dlp não expõe pausa real).

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
direto. Use os métodos do próprio manager (`add_url`, `remove_item`, `clear_completed`),
que fazem tudo sob `_lock`.

**Por quê:** a thread despachante percorre `items` e `order` sob esse lock. Se a GUI
mexer sem ele, existe uma janela onde `order` tem um id que `items` já não tem — o
despachante levanta `KeyError`, nada captura isso dentro do laço, **a thread morre e a
fila para de funcionar em silêncio pelo resto da sessão**.

Toda compreensão que indexa `self.items[i]` iterando `self.order` precisa da guarda
`if i in self.items`.

### 2. Nunca usar stylesheet inline para cor

Todo o tema vive em `theme.py`, aplicado como uma única folha QSS no app inteiro.
Widgets que mudam de estado usam **propriedade dinâmica** (`status`, `variant`) seguida
de `repolish(widget)`.

Estilo inline com cor quebra nos temas claros. Ainda existe um caso pendente em
`tab_documentos.py` (`result_label`) — não copie esse padrão.

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

Levantados na auditoria de 01/09/2026 e ainda **não corrigidos** — foram deixados porque
mudam comportamento, interface ou arquitetura.

**Alto**

- `tab_documentos._load_image()` roda decodificação de imagem + detecção de bordas
  (Canny/findContours) na thread da GUI. Trava 1–2s numa foto de celular. Precisa ir
  para um worker.
- `startup_check.verify_environment()` importa 13 pacotes pesados e dispara
  `ffmpeg -version` antes da janela aparecer — e o resultado sai por `print()`, que é
  invisível no .exe. Custo alto, entrega zero no build empacotado.

**Médio**

- `documentos/converter._docx_to_pdf()` chama `docx2pdf` (COM/Word) de dentro de uma
  QThread sem `pythoncom.CoInitialize()`. Provavelmente **nunca funciona**, mesmo com
  Word instalado — cai no fallback do LibreOffice e termina em erro.
- `CornerEditor.paintEvent` reescala o pixmap em resolução total a cada movimento do
  mouse ao arrastar um canto. Idem `_refresh_result_display` a cada `resizeEvent`.
- A imagem é decodificada duas vezes ao abrir (Qt + OpenCV).
- `ffmpeg -version` nunca é cacheado — roda 2× na inicialização e a cada abertura de
  Configurações.
- `result_label` usa `rgba(255,255,255,15)` inline: invisível em tema claro.
- `unique_path()` tem TOCTOU — duas conversões paralelas podem colidir no mesmo nome.
- `UrlInput._detach_windows_ime()` é comprovadamente inútil (a causa real do ícone
  fantasma era a scrollbar do Qt, já corrigida). Continua forçando handles nativos e
  desabilitando digitação CJK. Candidata a remoção.

**Dívida estrutural**

- `DownloadManager` e `ConversionManager` têm **~120 linhas idênticas**. O bug crítico
  da corrida existia igual nos dois e precisou ser corrigido duas vezes. Extrair uma
  `QueueManager` base é a melhoria de maior retorno do projeto.
- `IMAGE_EXTS` duplicado em `documentos/converter.py` e `documentos/scanner_engine.py`.
- Três vocabulários de status diferentes para o mesmo conceito (strings PT em
  `DownloadItem`/`ConversionItem`, chaves EN em `DocConversionItemWidget._CARD_STATUS`).

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
  imagem. Roda com timeout de 180s em thread separada.
- **Modo Preto e branco ruidoso**: threshold adaptativo com janela fixa de 11px reagia à
  textura do papel em fotos de alta resolução. Agora tem denoise antes e janela
  proporcional à resolução.
- **config.json corrompido**: gravação era não-atômica (`open(..., "w")` truncava antes
  de escrever). Agora é temp + `os.replace`. E `Settings.from_dict()` valida tipos —
  antes, um `null` no config impedia o app de abrir.
- **Vazamento do SettingsDialog**: dialog parented à janela principal sobrevive ao escopo
  Python. Precisa de `deleteLater()` explícito.
