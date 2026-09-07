import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "base")

VALID_MODELS: set = {"tiny", "base", "small", "medium", "large", "large-v3"}

DB_PATH: str = "transcriptions/data.db"

RECORDINGS_DIR: str = "recordings"

TRANSCRIPTIONS_DIR: str = "transcriptions"

ALLOWED_EXTENSIONS: set = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".webm", ".flac"}

# ── Auth ───────────────────────────────────────────────────────────────────
_raw_secret = os.getenv("JWT_SECRET_KEY")
if not _raw_secret:
    import warnings
    warnings.warn(
        "JWT_SECRET_KEY não definida — tokens serão invalidados a cada restart. "
        "Defina JWT_SECRET_KEY no .env para produção.",
        stacklevel=1,
    )
JWT_SECRET_KEY: str = _raw_secret or secrets.token_hex(32)
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_DAYS: int = int(os.getenv("JWT_EXPIRE_DAYS", "30"))
DEFAULT_QUOTA_MINUTES: float = float(os.getenv("DEFAULT_QUOTA_MINUTES", "60.0"))

# ── Diarização — microsserviço 3D-Speaker no WSL2 ──────────────────────────
# O pipeline ModelScope segfauta no Python nativo do Windows, então a
# diarização roda dentro do WSL2 (Linux + GPU). Este app fala HTTP com ele.
# Ver wsl-diarizer/server.py + start.sh para o servidor.
DIARIZER_URL: str = os.getenv("DIARIZER_URL", "http://127.0.0.1:9020")
# Timeout das chamadas /diarize em segundos (áudios longos podem precisar mais).
DIARIZER_TIMEOUT: float = float(os.getenv("DIARIZER_TIMEOUT", "1800"))

# Auto-start do microsserviço quando o app sobe (Windows + WSL2).
# Desligue se preferir subir manualmente o uvicorn dentro do WSL.
DIARIZER_AUTOSTART: bool = os.getenv("DIARIZER_AUTOSTART", "true").lower() in (
    "1", "true", "yes",
)
DIARIZER_WSL_DISTRO: str = os.getenv("DIARIZER_WSL_DISTRO", "Ubuntu")
DIARIZER_WSL_USER: str = os.getenv("DIARIZER_WSL_USER", "root")
DIARIZER_START_SCRIPT: str = os.getenv(
    "DIARIZER_START_SCRIPT",
    "/mnt/f/claude-projetos/_infra/transcritor/wsl-diarizer/start.sh",
)
# Segundos de espera pelo /health do microsserviço durante o autostart.
DIARIZER_STARTUP_TIMEOUT: float = float(os.getenv("DIARIZER_STARTUP_TIMEOUT", "30"))

# ── Worker pool ─────────────────────────────────────────────────────────────
# Máximo de utilização da GPU (%). Quando ultrapassado, o worker dorme entre
# segmentos para liberar ciclos para outras aplicações.
GPU_UTIL_LIMIT: int = int(os.getenv("GPU_UTIL_LIMIT", "70"))

# Máximo de utilização da CPU (%). Mesmo princípio do GPU_UTIL_LIMIT: quando a
# CPU global ultrapassa o limite, workers CPU dormem entre segmentos. Cobre o
# caso de lote grande com múltiplos CPU_WORKERS sem travar o resto do sistema.
CPU_UTIL_LIMIT: int = int(os.getenv("CPU_UTIL_LIMIT", "50"))

# Workers extras usando apenas CPU. 0 = só GPU (ou CPU se CUDA indisponível).
# Com CPU_UTIL_LIMIT ativo, é seguro subir (ex.: 2-4) sem saturar a máquina.
CPU_WORKERS: int = int(os.getenv("CPU_WORKERS", "1"))

# Segundos de ociosidade do pool antes de descarregar Whisper + diarizador
# da VRAM/memória. 0 = nunca descarrega (modelos ficam quentes — comportamento legado).
MODEL_IDLE_TIMEOUT_SECONDS: int = int(os.getenv("MODEL_IDLE_TIMEOUT_SECONDS", "60"))

# ── Transcrição via API OpenAI (ditado do Flow) ─────────────────────────────
# Se definida, o ditado (source=flow-dictate) tenta a API primeiro — sem cold
# start de VRAM — e cai pro Whisper local se a chamada falhar. Vazio = sempre local.
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_TRANSCRIBE_MODEL: str = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
