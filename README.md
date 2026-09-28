# Transcritor

Servidor local de **transcrição de áudio e vídeo em texto**, com Whisper rodando na sua própria GPU. Grava, recebe upload, importa por URL (YouTube, Google Drive, link direto), transcreve com timestamps por palavra e separa falantes (diarização). Interface web inclusa, API REST + WebSocket para automação.

Repositório: https://github.com/NycolasSF/transcritor.git

---

## O que ele faz

| Recurso | Detalhe |
|---|---|
| **Transcrição** | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2), modelos `tiny` a `large-v3`, trocáveis na interface sem reiniciar. Cerca de 4 a 6x mais rápido que o `openai-whisper` na GPU (RTF ~0,11 com `medium` numa RTX 3060 Ti). |
| **Timestamps por palavra** | Segmentos e palavras com início e fim, prontos para legenda (`.vtt`) e corte de vídeo. |
| **Áudio longo** | Arquivos de horas são processados em blocos de 30 min, com pico de RAM de ~1 GB independente da duração. |
| **Diarização** | Separação de falantes com 3D-Speaker (ModelScope), num microsserviço à parte (porta 9020). |
| **Entradas** | Gravação (microfone, áudio do sistema via WASAPI, abas do navegador), upload (`mp3 mp4 wav m4a ogg webm flac`) e `POST /import-url`. |
| **Fila** | SQLite com fila de jobs; um worker é o único dono da GPU (evita `CUDA out of memory`). Jobs travados são recuperados após reinício. |
| **Interface web** | Histórico, busca, títulos editáveis, nomes de falantes, retranscrição com outro modelo e exportação `.txt` `.md` `.vtt` `.pdf`. |
| **Contas** | Login com JWT (`/auth/register`, `/auth/login`) e cota de minutos por usuário. |
| **Flow (opcional)** | Cliente de ditado por voz para Windows: segure **Ctrl+Win**, fale, solte, e o texto é colado no app ativo. Pode usar a API da OpenAI só para o ditado, com o Whisper local como fallback. |

---

## Requisitos

- **Python 3.10+** e **ffmpeg** no `PATH`.
- **GPU NVIDIA com CUDA** recomendada (sem GPU, roda em CPU, bem mais lento).
- **Windows 10/11** para a captura do áudio do sistema (WASAPI, `pyaudiowpatch`). No macOS/Linux, upload, URL e API funcionam; a captura do sistema precisa de driver virtual (ex.: BlackHole) ou do modo Abas.
- **Diarização no Windows**: o 3D-Speaker não roda no Python nativo do Windows, então vai no **WSL2 (Ubuntu)**. No Linux/macOS roda nativo. Se não for usar diarização, pule essa parte.

---

## Instalação

```bash
git clone https://github.com/NycolasSF/transcritor.git
cd transcritor

python -m venv .venv
# Windows: .venv\Scripts\activate    |    Linux/macOS: source .venv/bin/activate

# GPU NVIDIA: instale o PyTorch com CUDA ANTES do requirements
# https://pytorch.org/get-started/locally/

pip install -r requirements.txt       # no macOS/Linux, sem o pyaudiowpatch:
                                      # grep -v pyaudiowpatch requirements.txt | pip install -r /dev/stdin
cp .env.example .env                  # Windows: copy .env.example .env
```

No Windows existe também o `install.bat`.

### Configurar o `.env`

Ajuste no mínimo:

```env
WHISPER_MODEL=medium            # tiny | base | small | medium | large-v3
JWT_SECRET_KEY=<gere um valor>  # python -c "import secrets; print(secrets.token_hex(32))"
                                # sem ele, todo login cai a cada restart
DIARIZER_AUTOSTART=false        # true só se o microsserviço de diarização estiver instalado
```

Se for usar diarização no WSL, troque `DIARIZER_START_SCRIPT` pelo caminho do `wsl-diarizer/start.sh` na **sua** máquina (o valor do exemplo aponta para a pasta original). Os demais parâmetros (fila, limite de GPU/CPU, descarregar modelo ocioso) estão comentados no próprio `.env.example`.

Os pesos do Whisper são baixados do HuggingFace no primeiro uso (`medium` ~1,5 GB).

---

## Nota para macOS

O servidor, a interface, o upload, o import por URL e a API funcionam no Mac. O que muda:

| Item | No Mac |
|---|---|
| **Instalação** | `brew install python@3.12 ffmpeg`, depois o venv com `python3.12`. Não instale o `pyaudiowpatch` (é só Windows): `grep -v pyaudiowpatch requirements.txt \| pip install -r /dev/stdin`. `pip install torch torchaudio` padrão, sem índice CUDA. |
| **Velocidade** | O faster-whisper (CTranslate2) não usa a GPU da Apple (Metal/MPS): roda em **CPU com int8**. Num M1/M2/M3 o `small` e o `medium` são usáveis; o `large-v3` fica lento para áudio longo. Deixe `WHISPER_COMPUTE_TYPE` vazio. |
| **Gravar áudio do sistema** | O botão de gravação do sistema responde "exige Windows". Use o **modo Abas** do navegador (Chrome/Edge), upload de arquivo ou `/import-url`. Para capturar o som do Mac, grave com um driver virtual (BlackHole) em outro app e faça upload. |
| **Diarização** | Sem WSL: no `.env`, `DIARIZER_AUTOSTART=false`. Instale as dependências da seção de diarização no próprio venv e rode em outro terminal: `cd wsl-diarizer && DIARIZER_USE_CUDA=false uvicorn server:app --host 127.0.0.1 --port 9020`. Em CPU é lenta; se não precisar separar falantes, ignore. |
| **Scripts `.bat`** | `install.bat`, `start.bat` e tudo em `flow/` são Windows. No Mac, suba com `python main.py`. |
| **Flow (ditado)** | Não roda no Mac (usa hook de teclado e áudio do Windows). |

---

## Uso

```bash
python main.py        # ou start.bat no Windows
```

Abra **http://localhost:8020**, crie uma conta na tela de login e use a interface.

> O servidor escuta só em `127.0.0.1` (não fica exposto na rede). Para acessar de outra máquina, defina `TRANSCRITOR_HOST` no ambiente, de preferência com o IP de uma VPN/tailnet, nunca `0.0.0.0` numa rede aberta.

### Por script (API)

`clients/transcritor_client.py` é um cliente em Python puro (só stdlib). Ele espera o servidor subir, faz login com `TRANSCRITOR_EMAIL` / `TRANSCRITOR_PASSWORD` do `.env` (crie antes essa conta na interface), envia o arquivo e devolve texto + segmentos + palavras:

```python
import sys
sys.path.insert(0, "caminho/para/transcritor/clients")
from transcritor_client import transcribe_media   # vídeo ou áudio (extrai o áudio com ffmpeg)

resultado = transcribe_media("aula.mp4", model="medium", language="pt")
print(resultado["text"])
```

O guia completo do cliente (lote, word-level, retranscrever um trecho com modelo maior) está em `tool_transcritor.md`.

Principais endpoints:

| Método | Rota | Uso |
|---|---|---|
| `POST` | `/auth/register`, `/auth/login` | conta e token JWT |
| `POST` | `/upload` | envia arquivo e cria o job |
| `POST` | `/import-url` | YouTube, Google Drive ou link direto |
| `GET` | `/transcriptions/{id}/status` | acompanha o job |
| `GET` | `/transcriptions` | histórico |
| `POST` | `/transcriptions/{id}/retranscribe` | refaz com outro modelo |
| `WS` | `/ws` | gravação e progresso em tempo real |

### Regra de ouro

Não carregue um Whisper próprio em outro script enquanto o servidor estiver no ar. Com uma GPU só, dois modelos carregados estouram a VRAM. Todo script deve chamar este servidor, que serializa os jobs.

---

## Diarização (WSL2, só Windows)

Instalação única, no PowerShell:

```powershell
wsl --install -d Ubuntu --no-launch
wsl -d Ubuntu -u root -- bash -c "
  curl -LsSf https://astral.sh/uv/install.sh | sh &&
  apt-get update -y && DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg libsndfile1 &&
  /root/.local/bin/uv python install 3.12 &&
  /root/.local/bin/uv venv --python 3.12 /opt/audio-diarizer/venv &&
  export VIRTUAL_ENV=/opt/audio-diarizer/venv &&
  /root/.local/bin/uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124 &&
  /root/.local/bin/uv pip install 'numpy<2' modelscope datasets funasr fastapi uvicorn soundfile \
    addict simplejson sortedcontainers pillow opencv-python-headless hdbscan umap-learn pyyaml kaldiio librosa scipy
"
```

Depois, com `DIARIZER_AUTOSTART=true`, o `main.py` sobe e derruba o microsserviço sozinho. No Linux/macOS, instale as mesmas dependências no seu ambiente e rode `uvicorn server:app --host 127.0.0.1 --port 9020` dentro de `wsl-diarizer/`.

---

## Flow: ditado por voz (opcional, Windows)

```bash
pip install -r flow/requirements.txt
flow\start-flow.bat
```

Segure **Ctrl+Win**, fale e solte. O ícone na bandeja troca o modelo (API OpenAI, `small`, `medium`, `large-v3`). A opção API exige `OPENAI_API_KEY` no `.env`; qualquer falha cai no Whisper local. `flow/vocab.txt` é o dicionário de termos do seu domínio (um por linha), usado como dica para o Whisper. `flow/install-watchdog.bat` agenda um vigia que reinicia o Flow e o servidor se travarem.

---

## Estrutura

```
transcritor/
├── main.py                  # sobe o servidor (FastAPI + uvicorn, porta 8020)
├── apps/api/                # rotas REST, auth JWT, WebSocket, microsserviço de diarização
├── apps/worker/             # worker da fila (único dono da GPU)
├── core/services/           # transcriber (faster-whisper), recorder, diarizer, downloader
├── core/settings.py         # configuração lida do .env
├── infra/                   # SQLite: banco, fila, repositórios, benchmark
├── clients/                 # transcritor_client.py (cliente Python para scripts)
├── wsl-diarizer/            # microsserviço 3D-Speaker (porta 9020)
├── flow/                    # cliente de ditado por voz + watchdog
├── scripts/                 # migração, auditoria de gravações, visualização do banco
└── static/index.html        # interface web (arquivo único, sem framework)
```

Dados locais (fora do git): `recordings/` (áudios), `transcriptions/data.db` (banco), `models/` (pesos da diarização).

Detalhes de arquitetura, protocolo do WebSocket, ciclo de vida do job e troubleshooting: `CLAUDE.md`. Histórico de bugs: `bugsfix.md` e `nemo-windows-issues.md`.
