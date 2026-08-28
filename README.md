# 🟥 Allora

Aplicativo pessoal de desktop para baixar vídeos do YouTube, Instagram,
Twitter/X, TikTok, Reddit, Facebook e qualquer outra plataforma suportada
pelo [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), com seleção de qualidade
até 4K — além de conversão de mídia local e uma aba de digitalização e
conversão de documentos.

Uso pessoal e livre: qualquer um pode baixar, usar e redistribuir (veja
[LICENSE](LICENSE) — MIT).

## Requisitos

- Windows 10/11
- **Nenhum outro requisito** — o Python, ffmpeg e Poppler já vêm todos
  embutidos no `.exe`.

## Instalação e uso

1. Baixe `Allora-standalone.zip` da [página de
   Releases](https://github.com/gustavovitor2004/Allora/releases) e
   extraia em qualquer pasta.
2. Dê duplo clique em `Allora.exe` dentro dela.

Pronto — não precisa instalar Python, ffmpeg, Poppler nem rodar nenhum
instalador. Tudo isso já vem dentro da pasta extraída, junto com o `.exe`.

Esse pacote é gerado com [PyInstaller](https://pyinstaller.org/), que embute
o interpretador Python e todas as bibliotecas dentro do próprio `.exe` — veja
"Gerando um novo `.exe`" abaixo se quiser compilar você mesmo a partir do
código-fonte.

### Desinstalar

Não há nada registrado no Windows para desfazer — o `.exe` não mexe no
registro, no `PATH` do sistema nem em nenhuma pasta fora de onde você
extraiu o `.zip`. Para remover o Allora, basta apagar a pasta onde você
extraiu (`config.json` e a pasta `tools\` ficam dentro dela).

## Como usar

1. Cole um link de vídeo no campo de texto (pode colar vários links, um por
   linha, para adicionar todos de uma vez).
2. Escolha a qualidade desejada no dropdown (`4K`, `1080p`, `720p`, `480p`,
   `360p`, `Melhor qualidade disponível` ou `Apenas áudio (MP3)`).
3. Clique em **▶ Adicionar** — o vídeo entra na fila e o app busca título e
   thumbnail em segundo plano.
4. Clique em **▶ Iniciar tudo** para começar a baixar a fila.
5. Acompanhe progresso, velocidade e ETA de cada item em tempo real.
6. Use **⏸ Pausar** para impedir que novos itens comecem (downloads já em
   andamento são concluídos normalmente — pausar um download HTTP no meio da
   transferência não é algo que o `yt-dlp` suporte de forma segura).
7. Use **✕ Cancelar** em um item para interrompê-lo, ou **↻ Tentar
   novamente** em itens com erro.
8. Use **🗑 Limpar concluídos** para remover da lista os itens já baixados
   ou cancelados.

### Convertendo arquivos locais (aba "🔄 Converter Arquivos")

Além de baixar vídeos, o app também converte arquivos que já estão no seu
PC entre formatos de vídeo, áudio ou imagem:

1. Na aba **🔄 Converter Arquivos**, clique em **🗂 Selecionar arquivo(s)**
   ou arraste um ou mais arquivos para a área pontilhada.
2. O app identifica automaticamente o formato pelo tipo do arquivo (vídeo,
   áudio ou imagem) e sugere um formato de destino diferente no dropdown ao
   lado — troque para qualquer outro formato da mesma categoria antes de
   converter.
3. Clique em **▶ Converter tudo**. O progresso, velocidade e status de cada
   arquivo aparecem em tempo real, igual à fila de downloads.
4. O arquivo convertido é salvo na mesma **pasta de destino** configurada
   para os downloads.

Formatos suportados:

- **Vídeo:** MP4, MKV, AVI, MOV, WEBM, FLV
- **Áudio:** MP3, WAV, AAC, FLAC, OGG, M4A, WMA
- **Imagem:** PNG, JPG/JPEG, WEBP, BMP, GIF, TIFF

A conversão só troca entre formatos da mesma categoria (ex: MP4 → MKV, ou
MP3 → FLAC) — para extrair o áudio de um vídeo baixado, use a opção
`Apenas áudio (MP3)` na aba de Downloads. A conversão usa o mesmo `ffmpeg`
já exigido pelo restante do app.

### Qualidade e fallback automático

Ao selecionar uma qualidade (ex: `1080p Full HD`), o app pede ao `yt-dlp` os
melhores streams de vídeo e áudio disponíveis **até** aquele limite de
altura. Se a plataforma não oferecer essa qualidade para o vídeo em questão,
o `yt-dlp` cai automaticamente para a melhor qualidade disponível abaixo
disso. Depois que o download termina, o app mostra, ao lado do título, a
qualidade **realmente** baixada (que pode ser menor do que a selecionada).

### Tema claro/escuro

O ícone ☀/🌙 no canto superior direito do cabeçalho alterna entre os temas
escuro (padrão) e claro instantaneamente, sem precisar reiniciar o app. A
preferência é salva em `config.json` e usada automaticamente na próxima
abertura. O mesmo tema também pode ser escolhido na tela de Configurações.

### Configurações

Acessível pelo botão **⚙ Config** no cabeçalho:

- Pasta de destino (padrão: `~/Videos/Downloads`)
- Qualidade padrão usada ao adicionar novos links
- Número de downloads simultâneos (1 a 3)
- Tema (escuro/claro)
- Usar ffmpeg para mesclar áudio/vídeo (ligado por padrão)
- Salvar thumbnail junto com o vídeo
- Salvar metadados do vídeo (`.info.json`)
- Caminho customizado do ffmpeg (caso não esteja no `PATH`)

Tudo é salvo automaticamente em `config.json`, na raiz do projeto.

## Aba "📄 Documentos" (digitalização e conversão de documentos)

Uma terceira aba, independente das de Downloads, com duas funções que
funcionam 100% offline (nenhuma delas depende de internet):

### 🔍 Digitalizar

Transforma a foto de um documento tirada com o celular em uma digitalização
limpa e corrigida — como um scanner de mesa, ou apps como CamScanner/Adobe
Scan. Não faz reconhecimento de texto, é só o processamento visual.

1. Clique em **📂 Selecionar Imagem** e escolha a foto (`.jpg`, `.png`,
   `.bmp`, `.webp`, `.tiff`).
2. O app detecta automaticamente as 4 bordas do documento na foto e mostra
   uma prévia com os cantos marcados — **arraste os pontos** para ajustar
   manualmente se a detecção não ficou perfeita.
3. Escolha o **modo**: Colorido, Escala de cinza, ou Preto e branco
   (visual clássico de scanner).
4. Clique em **✨ Digitalizar** — o processamento roda em segundo plano
   (nunca trava a interface): corrige a perspectiva, deixando o documento
   reto e retangular, e realça contraste/nitidez.
5. Use **👁 Ver original / Ver digitalizado** para comparar o antes e
   depois, e salve como **JPEG**, **PNG** ou **PDF** (cada botão pede o
   nome do arquivo).

Saída padrão: `~/Documents/Digitalizados` (configurável só editando
`config.json` por enquanto — não há campo na tela de Configurações para
isso ainda).

### 🔄 Converter Formato

1. Clique em **+ Adicionar arquivos** (podem ser de formatos diferentes,
   desde que compartilhem pelo menos um formato de destino em comum — "PDF"
   está sempre disponível como destino).
2. Escolha o formato de destino no dropdown — as opções mudam de acordo com
   o(s) arquivo(s) selecionado(s). Arquivos que não suportam o destino
   escolhido ficam marcados "Não suportado" e são pulados automaticamente.
3. Escolha a pasta de saída e clique em **▶ Converter**.
4. Cada arquivo mostra seu status (`Aguardando`, `Convertendo...`,
   `Concluído ✓`, `Erro ✗`) e o rodapé mostra o progresso geral
   ("2 de 3 arquivos convertidos"). Use o **[✕]** de cada linha para
   remover um arquivo específico da fila, ou **🗑 Remover todos** para
   limpar tudo de uma vez.
5. Ao terminar, use **📂 Abrir pasta de saída** para ver o resultado no
   Explorer.

Conversões suportadas:

| De | Para |
|---|---|
| JPG / PNG / BMP / WEBP / TIFF | PDF, ou qualquer outro formato de imagem da lista |
| PDF | JPG, PNG (uma imagem por página), DOCX, TXT |
| DOCX | PDF, TXT |
| TXT | PDF, DOCX |
| Qualquer mistura dos formatos acima | Um único PDF mesclado (marque "Mesclar tudo em um único PDF" quando o destino for PDF) — arraste as linhas para reordenar as páginas antes de converter, e PDFs protegidos por senha são suportados (o app pede a senha de cada um) |

Saída padrão: `~/Documents/Convertidos`.

As operações com PDF (converter PDF → imagem, mesclar PDF) usam o Poppler,
já embutido no `.exe`. **DOCX → PDF** precisa do **Microsoft Word**
instalado (Windows, usado via automação COM) **ou** do **LibreOffice**
instalado como alternativa (`soffice --headless`) — nenhum dos dois vem
junto com o Allora. Sem nenhum dos dois, o app mostra um erro claro em vez
de travar.

## Gerando um novo `.exe` (para desenvolvimento)

Se você alterou o código-fonte (pasta `src/`) e quer gerar um novo
`Allora.exe`:

```bash
pip install -r requirements.txt
```

Depois, garanta que `tools\ffmpeg\ffmpeg.exe` e `tools\poppler\pdftoppm.exe`
existam (baixe manualmente em https://ffmpeg.org/download.html e
https://github.com/oschwartz10612/poppler-windows/releases se ainda não
tiver), e rode:

```powershell
.\build_exe.ps1
```

Isso gera `dist\Allora\Allora.exe` via PyInstaller (embute o interpretador
Python e todas as bibliotecas). Copie `tools\ffmpeg` e `tools\poppler` para
dentro de `dist\Allora\tools\` (o `.exe` procura ali), e compacte a pasta
`dist\Allora\` inteira em `.zip` para publicar como release — esse é o
`Allora-standalone.zip`.

O `.exe` fica grande (~300MB compactado, ~700MB extraído) porque leva o
Python e todas as bibliotecas (PySide6, OpenCV etc.) embutidos — é a troca
por não precisar de nenhuma instalação na máquina de quem for usar.

Para rodar direto do código-fonte sem compilar (útil durante o
desenvolvimento):

```bash
python src/main.py
```

## Estrutura de arquivos

```
/Allora
├── src/                      # todo o código-fonte Python
│   ├── main.py               # Ponto de entrada — inicia a interface gráfica
│   ├── startup_check.py      # Confere pacotes/ferramentas ausentes ao iniciar
│   ├── ui.py                 # Componentes de interface (PySide6)
│   ├── theme.py              # Sistema de tema centralizado (dark/light, apply_theme)
│   ├── downloader.py         # Wrapper do yt-dlp, fila de downloads e threading
│   ├── converter.py          # Conversor de mídia local via ffmpeg, fila e threading
│   ├── settings.py           # Carregar/salvar config.json (na raiz do projeto)
│   ├── utils.py               # Validação de URL, detecção de plataforma, detecção de
│   │                          # ffmpeg/Poppler, helpers
│   └── documentos/           # Aba "Documentos": digitalização e conversão
│       ├── __init__.py
│       ├── scanner_engine.py # Pipeline de digitalização via OpenCV (perspectiva + realce)
│       ├── converter.py      # Conversão de imagem/PDF/DOCX/TXT (Pillow, reportlab,
│       │                     # pdf2image, pdf2docx, pdfplumber, docx2pdf, pypdf)
│       ├── workers.py        # QThread workers de digitalização e conversão
│       └── tab_documentos.py # Widget da aba (sub-abas Digitalizar/Converter)
├── build_exe.ps1              # Gera o .exe standalone via PyInstaller (dist/Allora/)
├── tools/                    # ffmpeg/Poppler portáteis (git-ignored; incluídos no .exe publicado)
├── config.json                # Criado automaticamente na primeira execução (git-ignored)
├── requirements.txt
├── LICENSE
└── README.md
```

## Detalhes técnicos

- **GUI**: PySide6, rodando 100% na thread principal. Nenhum download roda
  na thread da interface — cada item baixa em sua própria `threading.Thread`,
  e o progresso é reportado de volta à interface via sinais Qt
  (thread-safe por padrão).
- **Tema**: centralizado em `theme.py` — uma única `apply_theme()` aplica o
  stylesheet inteiro da aplicação; nenhum widget define estilo inline para
  fins de tema. Linhas de fila (downloads/conversões) mudam a cor da borda
  esquerda dinamicamente via uma propriedade Qt (`status`), não por CSS
  fixo, então o mesmo código funciona em ambos os temas automaticamente.
- **Engine**: `yt-dlp` é usado via API Python (não subprocess), o que
  permite hooks de progresso em tempo real, seleção fina de formato e
  pós-processamento (merge/conversão) integrados.
- **Retry automático**: até 3 tentativas por item em caso de erro
  transitório (ex: limitação de taxa), com backoff progressivo. Vídeos
  privados/indisponíveis são detectados e marcados como `Indisponível` sem
  consumir tentativas de retry.
- **Mesclagem**: quando a qualidade selecionada exige stream de vídeo e
  áudio separados (comum a partir de 720p/1080p no YouTube), o `yt-dlp`
  usa o ffmpeg para mesclá-los automaticamente em `.mp4`.

## Solução de problemas

| Problema | Causa provável | Solução |
|---|---|---|
| "ffmpeg não encontrado" | O `.exe` não encontra `tools\ffmpeg\ffmpeg.exe` | Confirme que a pasta `tools\` está junto do `Allora.exe` (extraída do mesmo `.zip`), ou informe um caminho customizado em Configurações |
| Item fica "Indisponível" | Vídeo privado, removido ou exige login | Nada a fazer no app — o conteúdo não está acessível publicamente |
| Download trava em 0% | Link inválido ou plataforma não suportada pelo yt-dlp | Verifique a URL; consulte a lista de extratores do yt-dlp |
| Qualidade baixada é menor que a selecionada | A plataforma não oferece aquele stream para este vídeo | Comportamento esperado — o app mostra a qualidade real ao lado do título |

## Aviso

Este aplicativo é destinado a uso pessoal. Respeite os termos de uso das
plataformas e os direitos autorais do conteúdo baixado.
