# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Como USAR a tool (do mundo, em scripts):** este CLAUDE.md cobre só configuração e ajuste do servidor. Para o guia de uso (cliente, exemplos, modelos), veja `tool_transcritor.md` nesta mesma pasta.

## Running the app

```bash
python main.py
# Access at http://localhost:8020
```

**Bind é `127.0.0.1` desde 06/09/2026.** Estava em `0.0.0.0`, ou seja aberto a **qualquer rede
em que o PC entrasse** (wifi de fora inclusive), sem autenticação, com acesso a arquivo e à
`OPENAI_API_KEY`. Descoberto durante a auditoria da ponte do Hermes. Nada no hub perde nada:
todo cliente chama `localhost:8020`. Se algum dia algo precisar alcançar o motor de outra
máquina, a escotilha é `TRANSCRITOR_HOST=0.0.0.0` no ambiente — mas prefira o IP da tailnet a
`0.0.0.0`. **Vale a partir do próximo restart do motor.**

## Flow — ditado por voz (cliente estilo Wispr)

`flow/flow.py` é um tray app client-side: segure **Ctrl+Win**, fale, solte — o texto é digitado no app ativo. Grava o microfone, muta o som do sistema durante a gravação (pycaw; `FLOW_MUTE_SYSTEM=false` desliga), envia ao `POST /local/dictate` (síncrono, sem auth, só 127.0.0.1; o job passa pela fila normal — o pool segue único dono da GPU — e é apagado ao final: não entra no histórico nem na quota) e digita o resultado via `keyboard.write` (clipboard não é tocado automaticamente). Depois de transcrever, a pill mostra o texto por `RESULT_DISPLAY_SECONDS` (15s) com "clique pra copiar" — clique copia pra área de transferência (só aí o clipboard é tocado). Modelo trocável na bandeja (small/medium/large-v3; default `FLOW_MODEL=medium`).

**Transcrição via API OpenAI (opcional, ago/2026):** se `OPENAI_API_KEY` estiver setada no `.env` do transcritor, o worker tenta a API primeiro **só pra jobs `source=flow-dictate`** (`core/services/openai_transcriber.py`, modelo em `OPENAI_TRANSCRIBE_MODEL`, default `gpt-4o-transcribe`) — sem cold start de VRAM (ver `MODEL_IDLE_TIMEOUT_SECONDS` abaixo). Qualquer falha (rede, key, rate limit) cai pro Whisper local automaticamente; ditado nunca fica dependente de internet. Gasto é estimado localmente (duração × preço/min, não é o saldo real da conta — a OpenAI não expõe saldo/gasto pra uma key de projeto comum) em `openai_usage.json` (gitignored), lido por `GET /local/openai-usage` e mostrado num item da bandeja do Flow. Dicionário: `flow/vocab.txt` vira `initial_prompt` por job (coluna `initial_prompt` em `jobs`). Como o job é apagado no motor, cada ditado é anexado a `flow/dictations.log` (gitignored, uma linha `timestamp\ttexto`, gravado ANTES do `keyboard.write`) — é a única cópia recuperável; abrir pela bandeja em "Histórico de ditados". Subir: `flow/start-flow.bat` (autostart: atalho em `shell:startup`). Deps próprias em `flow/requirements.txt`. Crash duro deixa traceback em `flow/flow-crash.log` (append — reinício não apaga o traceback anterior).

### Watchdog — reinício automático (Flow + motor)

`flow/watchdog.py` roda a cada 2 min pelo Agendador de Tarefas do Windows — instalar uma vez com `flow/install-watchdog.bat`, remover com `schtasks /Delete /TN Flow-Watchdog /F`, rodar na hora com `schtasks /Run /TN Flow-Watchdog`. Ele vigia duas coisas com sinais diferentes:

- **Flow** — o loop do Tk toca `flow/flow.heartbeat` (mtime = vida, conteúdo = PID) a cada 5s, e só toca se a UI está viva **e** nenhum ditado está pendurado (>5 min em REC/BUSY). Heartbeat com mais de 90s = `taskkill` + subida pelo `start-flow.bat` (idempotente pela trava na porta 8021). "Sair" na bandeja deixa `flow/flow.stopped` e o watchdog não ressuscita — voltar é ação humana.
- **Motor** — job em `processing` sem tocar `updated_at` = worker congelado: mata o PID que ouve a 8020, marca os jobs órfãos como erro (senão o ciclo seguinte os lê como travados e reinicia o motor para sempre) e sobe de novo. O limite é por tipo: **3 min** para ditado (`source='flow-dictate'`, curto por natureza e o que dói na hora) e **20 min** para o resto (diarização e áudio de horas passam minutos sem tocar o progresso). Porta aberta **não** prova saúde: o motor travado segue respondendo HTTP.

Eventos vão para `flow/watchdog.log`; `python watchdog.py --check` roda o self-check da lógica de decisão.

### Autocura dentro do Flow (travamentos de 2026-07-29)

O watchdog é a última linha; antes dele o Flow se destrava sozinho — reiniciar o processo perde o ditado, então cada estado tem teto e saída:

- **Gravação que nunca fechava.** O `on_key` acumula `_ctrl`/`_win` do hook, e key-up perdido (outro app rouba o foco, o shell consome o Win) deixava os dois `True` para sempre: gravava sem fim, `_finish` nunca disparava. O loop de captura agora confere as teclas no driver (`keys_really_held`, `GetAsyncKeyState` — o `keyboard.is_pressed` mente porque lê o mesmo hook furado) e tem teto de `MAX_SECONDS`.
- **`break` do `stream.read` deixava o state em `REC` para sempre** (mic caindo no meio) — hotkey morta até reiniciar. Sair do loop por qualquer motivo agora fecha o ditado (`if self.state == REC: self._finish()`).
- **`BUSY` pendurado** (motor travado, `keyboard.write` preso): `watchdog_tick`, chamado pelo loop do overlay, aborta em `BUSY_TIMEOUT` (= `REQUEST_TIMEOUT` + 30s) e avisa no pill. Como só se grava a partir de `IDLE`, sem esse teto a hotkey ficava morta indefinidamente.
- **Áudio não se perde**: envio que falha grava o WAV em `flow/failed/` (gitignored) para retranscrever depois.

`STUCK_SECONDS` (heartbeat) é deliberadamente maior que `MAX_SECONDS + BUSY_TIMEOUT`: dentro desse envelope o Flow resolve sozinho; passar disso é travamento fora do modelo e aí o watchdog reinicia. `python flow/test_selfheal.py` cobre os quatro casos.

**Causa raiz do travamento de 2026-07-29 (QuickEdit do console):** com o QuickEdit ligado, um clique na janela do motor põe o console em modo de seleção (título vira "Selecionar Transcritor") e **bloqueia toda escrita no stdout**. O worker congelou dentro do `print` de "[Whisper] Carregando modelo…" — ainda segurando o `_model_lock` — e por 4h todo job dormiu em `processing` enquanto o servidor respondia HTTP normalmente. `main.py` agora desliga o QuickEdit no boot (`_disable_console_quick_edit`) e tem trava de instância única na porta 8022: a 8020 só passa a ouvir **depois** do lifespan (que espera o diarizer no WSL), então "porta fechada" não significa "motor ausente" — sem a trava, o watchdog subiria um segundo motor carregando Whisper na mesma GPU (CUDA OOM).

## First-time migration (from v1 JSON)

If you have an existing `transcriptions/data.json`, migrate it to SQLite before starting:

```bash
python scripts/migrate_json_to_sqlite.py
```

## Installing dependencies

```bash
pip install -r requirements.txt
```

> For NVIDIA GPU support, install PyTorch with CUDA from [pytorch.org](https://pytorch.org/get-started/locally/) **before** running pip install.

## Speaker diarization (WSL2 microservice)

3D-Speaker (Alibaba/ModelScope) segfaults under native Python on Windows, so diarization runs inside **WSL2 Ubuntu** as a separate FastAPI microservice on port 9020. The Windows app talks to it over HTTP.

**One-time WSL setup** (already done on this machine — re-run only on a fresh box):

```powershell
# 1. Install Ubuntu in WSL2
wsl --install -d Ubuntu --no-launch
wsl --set-default Ubuntu

# 2. Inside Ubuntu (as root): install uv + Python 3.12 + venv + PyTorch CUDA + modelscope deps
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

**Daily usage** — the app spawns the microservice automatically on startup (`apps/api/diarizer_service.py`, hooked into the FastAPI lifespan). Just run:

```powershell
python main.py
```

The startup logs will show `[Diarizer] Microsservico online em http://127.0.0.1:9020.` once `/health` answers. On shutdown the app sends `pkill -f 'uvicorn server:app'` inside WSL and terminates the `wsl.exe` subprocess.

To opt out (run the microservice manually in another terminal), set `DIARIZER_AUTOSTART=false` in `.env`. To run it manually:

```powershell
wsl -d Ubuntu -u root -- /mnt/f/claude-projetos/_infra/transcritor/wsl-diarizer/start.sh
```

If diarization is disabled for every job (`diarize=False`), the microservice doesn't actually need to be running — autostart is best-effort and never blocks the app boot.

**Models** are pre-downloaded under `models/modelscope/` (~110 MB). The microservice uses `/mnt/f/claude-projetos/_infra/transcritor/models/modelscope/` directly — do not delete this folder.

## Transcription engine (faster-whisper)

Transcription runs on **faster-whisper** (CTranslate2), not the reference `openai-whisper`. Same Whisper models, same text quality and word-level timestamps, but ~4-6x faster on GPU — measured **RTF ~0.11 for `medium` on an RTX 3060 Ti** (vs ~0.6 on openai-whisper alone, and >1.0 when two openai-whisper jobs split the card).

`core/services/transcriber.py` keeps the same `transcribe_with_progress()` signature, so the worker is unchanged. It loads a `WhisperModel` (cached by `(model_name, device, compute_type)`) and drives progress by iterating the lazy `segments` generator — no more `tqdm` monkey-patching. All anti-hallucination params are preserved (`condition_on_previous_text=False`, capped temperature, `no_speech`/`logprob`/`compression_ratio` thresholds).

**Long audio is transcribed in 30-min chunks** (decoded once via `decode_audio`, sliced as ndarray, timestamps re-offset). Reason: faster-whisper's `FeatureExtractor` computes the STFT of the whole audio at once and `np.fft.rfft` upcasts to float64/complex128 internally — a 3h file allocates ~6.7 GB of RAM and dies with `Unable to allocate X GiB`. Chunking caps the peak at ~1 GB regardless of duration. Language is detected on the first chunk and pinned for the rest; a word exactly on a chunk boundary may get clipped (cut on silence via VAD if that ever matters).

The CTranslate2 weights are auto-downloaded from HuggingFace on first use (e.g. `medium` ~1.5 GB), cached under `~/.cache/huggingface` — independent of the `openai-whisper` cache.

## Environment

Copy `.env.example` to `.env`:

```env
WHISPER_MODEL=base       # tiny | base | small | medium | large | large-v3
WHISPER_COMPUTE_TYPE=    # blank = float16 on GPU / int8 on CPU. int8_float16 = faster + less VRAM, ~no quality loss
WHISPER_VAD_FILTER=false # true = Silero VAD trims silence (faster + fewer hallucinations; may clip edge speech)
WHISPER_INITIAL_PROMPT=  # optional decoding hint (domain vocabulary, etc.)

DIARIZER_URL=http://127.0.0.1:9020   # WSL microservice (see above)
DIARIZER_TIMEOUT=1800    # seconds — bump for very long audios
DIARIZER_AUTOSTART=true  # false = run the WSL microservice manually

# Worker pool
GPU_UTIL_LIMIT=95        # worker sleeps between segments above this GPU util%. Lower it for a responsive desktop.
CPU_UTIL_LIMIT=50        # same idea for CPU overflow workers
CPU_WORKERS=1            # extra CPU-only workers. SET 0 for GPU batch jobs — see note below.
MODEL_IDLE_TIMEOUT_SECONDS=60  # unload models from VRAM after this many idle seconds (0 = keep hot)
```

The Whisper model can be changed at runtime via the UI without restarting the server.

**Batch tuning (transcribing many files on the GPU, e.g. overnight):** set `CPU_WORKERS=0`. With faster-whisper a single GPU job already runs at RTF ~0.11; a parallel CPU worker only fights the GPU for the same files and *inflates* per-job RTF. One job at a time on the GPU is fastest — running two at once is what caused the >1.0 RTF on the old engine.

## Architecture

**Hybrid Windows + WSL2** — audio capture and faster-whisper transcription run on Windows (WASAPI loopback via `pyaudiowpatch` is Windows-only). Speaker diarization runs in WSL2 (3D-Speaker has no working Windows wheel) as a microservice the app calls over HTTP.

### Folder structure

```
transcritor/
├── apps/
│   ├── api/
│   │   ├── main.py              # FastAPI app assembly
│   │   ├── state.py             # Shared singletons (repo, queue, worker, recorder)
│   │   ├── routes/
│   │   │   ├── transcriptions.py  # GET/DELETE/PATCH/status/retranscribe
│   │   │   └── upload.py          # POST /upload + POST /import-url
│   │   └── ws/
│   │       └── handler.py         # WebSocket /ws handler
│   └── worker/
│       └── worker.py              # Background transcription worker thread
├── core/
│   ├── domain/models.py           # Job dataclass
│   ├── interfaces/                # repo.py + queue.py ABCs
│   ├── services/
│   │   ├── recorder.py            # WASAPI audio capture
│   │   ├── transcriber.py         # faster-whisper wrapper with progress
│   │   ├── audio_utils.py         # get_audio_duration()
│   │   ├── diarizer.py            # HTTP client to WSL microservice
│   │   └── downloader.py          # yt-dlp + httpx download helpers
│   └── settings.py                # Config from env vars
├── infra/
│   ├── db.py                      # SQLite init + schema
│   ├── repo_sqlite.py             # TranscriptionRepo implementation
│   ├── queue_sqlite.py            # JobQueue implementation
│   ├── storage_local.py           # File path helpers
│   └── benchmark.py               # RTF context manager
├── wsl-diarizer/                  # microservice running INSIDE WSL2 Ubuntu
│   ├── server.py                  # FastAPI: /health /diarize /unload (port 9020)
│   ├── start.sh                   # uvicorn boot script
│   └── smoke_test.py              # standalone PoC for the 3D-Speaker pipeline
├── scripts/
│   └── migrate_json_to_sqlite.py  # One-time JSON → SQLite migration
└── static/index.html              # Single-file frontend (no framework)
```

### Data flow

1. **`core/services/recorder.py`** — `AudioRecorder` finds the system's default loopback WASAPI device and records in a background thread, saving to `recordings/recording_<timestamp>.wav`.
2. **`core/services/transcriber.py`** — `transcribe_with_progress()` loads a faster-whisper `WhisperModel` (cached in `_model_cache` by `(model_name, device, compute_type)`) and iterates the lazy `segments` generator, calling `progress_cb(pct)` between segments (progress = `segment.end / audio_duration`). GPU/CPU throttle and user-cancellation are checked per segment.
3. **`apps/worker/worker.py`** — `Worker` is a daemon thread that polls `infra/queue_sqlite.py` for pending jobs. For each job: marks processing → transcribes → marks done/error. Calls `progress_callbacks[job_id](pct)` from the worker thread (thread-safe via `asyncio.run_coroutine_threadsafe`).
4. **`apps/api/`** — FastAPI server:
   - **`/ws` WebSocket** — recording control + real-time progress. On stop: creates job in SQLite, enqueues it, then polls `repo.get_job()` every 0.5s and forwards progress/completion to the client. 25s timeout heartbeat (ping/pong) detects unexpected recording drops.
   - **REST endpoints** — `GET /transcriptions`, `DELETE /transcriptions/{tid}`, `PATCH /transcriptions/{tid}/title`, `GET /transcriptions/{tid}/status`, `POST /transcriptions/{tid}/retranscribe`, `POST /upload`, `POST /import-url`.
5. **`static/index.html`** — single-file frontend, no external dependencies. Communicates exclusively through WebSocket + REST. Uses `Date.now()` for recording timer, Web Audio API for sounds, Notifications API for OS alerts.

### Persistence

- `recordings/` — temporary WAV/MP3 files (git-ignored)
- `transcriptions/data.db` — SQLite database (git-ignored). Tables: `jobs`, `job_queue`, `benchmarks`, `speaker_profiles`, `speaker_embeddings`.

#### Limpar/arquivar `recordings/` — auditar ANTES (05/08/2026)

`recordings/` cresce sem teto: em 05/08/2026 estava com **6.713 arquivos / 25,48 GB**. É o maior consumo de disco da infra, e "temporary" no parágrafo acima engana — nem todo arquivo ali é descartável.

**Nunca mover/apagar a pasta inteira.** Rodar antes `python scripts/audit_recordings.py`, que cruza os arquivos em disco com `jobs.file_path`/`jobs.text` (abre o banco read-only, pode rodar com o servidor no ar) e separa em três grupos:

| Grupo | Significado | Ação |
|---|---|---|
| texto salvo em `jobs.text` | a transcrição está no BD; o áudio é redundante | arquivável |
| job existe mas sem texto (`error`/`cancelled`) | nunca transcrito com sucesso | **manter** — é a única cópia |
| sem job nenhum no BD | órfão (upload que não virou job) | **manter** — é a única cópia |

Resultado da auditoria de 05/08: **6.605 arq / 24,57 GB arquiváveis** · 105 arq / 0,66 GB com job sem texto · 3 arq / 0,25 GB órfãos.

⚠️ Mover os arquiváveis quebra o `file_path` no BD, então o botão **retranscrever** deixa de funcionar para eles — o texto e os segmentos permanecem, só o áudio-fonte sai do alcance. Trade-off aceitável para liberar 24 GB, mas é uma via de mão única.

#### ✅ Arquivamento EXECUTADO em 05/08/2026

Os **6.605 arquiváveis (24,57 GB)** foram movidos para **`E:\_BACKUP-claude-projetos\transcritor-recordings\`** (SSD interno, fora do espelho `/MIR` — ver o `_LEIA-ME.md` de lá). Os **108 sem transcrição ficaram** em `recordings/`, que hoje tem 108 arq / 0,91 GB.

O servidor rodava durante a operação e continuou funcional (HTTP 200, banco intacto em 191,9 MB); o `robocopy` excluiu por nome os 108 protegidos e qualquer arquivo tocado nas 2h anteriores (nenhum, na hora). Reexecutar o mesmo processo quando `recordings/` voltar a crescer: rodar `scripts/audit_recordings.py`, gerar a lista dos não-arquiváveis e passar em `/XF`.

### Job lifecycle

```
pending → (worker picks up) → processing → done
                                         ↘ error
```

Status endpoint (`GET /transcriptions/{tid}/status`) returns:
- `"processing"` — job in queue or being processed
- `"done"` — completed or errored (check `error` field)
- `"stuck"` — server restarted while job was in progress

### WebSocket message protocol

| Direction | `type` | Purpose |
|-----------|--------|---------|
| server → client | `status` | recording state + device info |
| server → client | `ping` | heartbeat keepalive |
| server → client | `transcription_start` | new card created |
| server → client | `transcription_progress` | `percent` 0–100 |
| server → client | `transcription_complete` | text + segments + language |
| server → client | `transcription_error` | error message |
| server → client | `recording_stopped` | unexpected drop detected |
| client → server | `action: start/stop/status/change_model` | control messages |

### File upload flow

`POST /upload` accepts mp3/mp4/wav/m4a/ogg/webm/flac, saves to `recordings/upload_<id>.<ext>`, creates a job in SQLite, enqueues it to the worker, and returns `{id, status: "processing"}`. The client polls `GET /transcriptions/{tid}/status`.

### URL import flow

`POST /import-url` accepts YouTube, Google Drive, or direct audio/video URLs. Creates a pending job immediately (so the client can show a card), then downloads the file in a background thread (yt-dlp or httpx), updates `file_path` + `duration` in SQLite, and enqueues the job for the worker.

### Benchmarking

After every transcription, `infra/benchmark.py` records `(job_id, model, device, audio_duration, transcription_time, rtf)` in the `benchmarks` table. RTF = transcription_time / audio_duration.
