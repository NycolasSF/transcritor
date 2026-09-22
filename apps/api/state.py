"""Shared application state: singletons instantiated once at startup."""
from typing import Callable, Dict

from core.services.recorder import AudioRecorder
from core.services.transcriber import get_device
from core.settings import GPU_UTIL_LIMIT, CPU_UTIL_LIMIT, CPU_WORKERS, MODEL_IDLE_TIMEOUT_SECONDS
from infra.repo_sqlite import SQLiteRepo
from infra.queue_sqlite import SQLiteQueue
from infra.user_repo import UserRepo
from apps.worker.pool import WorkerPool

DEVICE: str = get_device()

repo = SQLiteRepo()
queue = SQLiteQueue()
user_repo = UserRepo()

# job_id → thread-safe callable(pct: int)
# Populated by ws/handler.py when a WebSocket client is waiting for a job.
progress_callbacks: Dict[str, Callable] = {}

# job_id → last reported progress % (0–100). Written by worker, read by dashboard.
progress_store: Dict[str, float] = {}

# Job IDs flagged for cancellation. Read by Worker.progress_cb between Whisper
# segments — when it sees its own job_id here, raises CancelledTranscriptionError.
cancelled_ids: set = set()
# ditados do Flow que pediram a API OpenAI (seletor da bandeja = "api")
api_dictate_ids: set = set()

pool = WorkerPool(
    queue=queue,
    repo=repo,
    progress_callbacks=progress_callbacks,
    user_repo=user_repo,
    gpu_util_limit=GPU_UTIL_LIMIT,
    cpu_util_limit=CPU_UTIL_LIMIT,
    cpu_workers=CPU_WORKERS,
    model_idle_seconds=MODEL_IDLE_TIMEOUT_SECONDS,
)

recorder = AudioRecorder()
