import time
import threading
from typing import Callable, Dict, Optional

from core import settings
from core.interfaces.queue import JobQueue
from core.interfaces.repo import TranscriptionRepo
from core.services.transcriber import transcribe_with_progress, unload_models
from infra.benchmark import BenchmarkContext, record as record_benchmark
from apps.api import state as _state


class CancelledTranscriptionError(Exception):
    """Raised by progress_cb when the job was cancelled by user via /cancel."""
    pass


class Worker(threading.Thread):
    """Background worker that dequeues and processes transcription jobs."""

    def __init__(
        self,
        queue: JobQueue,
        repo: TranscriptionRepo,
        progress_callbacks: Dict[str, Callable],
        user_repo=None,
        device: str = "cpu",
        gpu_limit: int = 70,
        cpu_limit: int = 100,
        name: str = "TranscriptionWorker",
        should_dequeue: Optional[Callable[[], bool]] = None,
    ):
        super().__init__(daemon=True, name=name)
        self.queue = queue
        self.repo = repo
        self.progress_callbacks = progress_callbacks
        self.user_repo = user_repo
        self.device = device
        self.gpu_limit = gpu_limit
        self.cpu_limit = cpu_limit
        # Se fornecido, o worker só dequeua quando este callback retornar True.
        # Usado para workers CPU esperarem a GPU estar ocupada.
        self._should_dequeue = should_dequeue
        # Exposed for stuck-detection in the status endpoint
        self.current_job_id: Optional[str] = None

    def run(self) -> None:
        while True:
            try:
                if self._should_dequeue and not self._should_dequeue():
                    time.sleep(0.5)
                    continue
                job_id = self.queue.next_job()
                if job_id is None:
                    time.sleep(0.5)
                    continue
                self._process(job_id)
            except Exception as e:
                print(f"[Worker] Erro inesperado no loop: {e}")
                time.sleep(1)

    def _try_openai(self, job_id: str, job: dict, duration: float, lang_arg: Optional[str]) -> Optional[dict]:
        """Ditado do Flow: tenta a API OpenAI primeiro (sem cold start de VRAM).

        None = falhou (rede, key inválida, rate limit...) e quem chama cai pro
        Whisper local — ditado nunca fica dependente de internet.
        """
        from core.services.openai_transcriber import record_usage, transcribe_openai

        t0 = time.monotonic()
        try:
            result = transcribe_openai(job["file_path"], language=lang_arg, prompt=job.get("initial_prompt"))
        except Exception as e:
            print(f"[Worker] API OpenAI falhou ({e}) — caindo pro Whisper local.")
            return None

        record_benchmark(job_id, settings.OPENAI_TRANSCRIBE_MODEL, "openai", duration, time.monotonic() - t0)
        record_usage(duration)
        return result

    def _process(self, job_id: str) -> None:
        self.current_job_id = job_id
        try:
            job = self.repo.get_job(job_id)
            if not job:
                print(f"[Worker] Job {job_id} não encontrado, descartando.")
                return

            # Capture before any DB mutation so we don't need a second get_job later
            user_id = job.get("user_id")
            duration = job.get("duration") or 0.0
            model = job.get("model")

            self.repo.mark_processing(job_id)

            def progress_cb(pct: int) -> None:
                if job_id in _state.cancelled_ids:
                    raise CancelledTranscriptionError(job_id)
                self.repo.update_progress(job_id, pct)
                _state.progress_store[job_id] = pct
                cb = self.progress_callbacks.get(job_id)
                if cb:
                    try:
                        cb(pct)
                    except Exception:
                        pass

            input_language = job.get("input_language") or "pt"
            lang_arg = None if input_language == "auto" else input_language

            result = None
            if job_id in _state.api_dictate_ids and settings.OPENAI_API_KEY:
                result = self._try_openai(job_id, job, duration, lang_arg)

            if result is None:
                with BenchmarkContext(
                    job_id=job_id,
                    model=model,
                    device=self.device,
                    audio_duration=duration,
                ):
                    result = transcribe_with_progress(
                        file_path=job["file_path"],
                        model_name=model,
                        device=self.device,
                        progress_cb=progress_cb,
                        language=lang_arg,
                        gpu_limit=self.gpu_limit,
                        cpu_limit=self.cpu_limit,
                        initial_prompt=job.get("initial_prompt"),
                    )

            if result["success"] and job.get("diarize"):
                try:
                    from core.services.diarizer import diarize, merge_speaker_labels
                    # Libera VRAM do Whisper antes da diarização — em GPUs de 8GB
                    # (RTX 3060 Ti), Whisper large + 3D-Speaker simultâneos
                    # estouram (CUDA OOM). Custo: recarregar Whisper no próximo job.
                    if self.device == "cuda":
                        unload_models()
                    print(f"[Worker] Diarizando {job_id}…")
                    d_segs = diarize(job["file_path"])
                    result["segments"] = merge_speaker_labels(result["segments"], d_segs)
                except Exception as e:
                    msg = f"Falha na identificação de falantes: {e}"
                    result["error"] = msg
                    print(f"[Worker] {msg}")

            if result["success"]:
                self.repo.mark_done(job_id, result)
                if self.user_repo and user_id and duration:
                    try:
                        self.user_repo.record_usage(user_id, job_id, duration / 60, model)
                    except Exception as e:
                        print(f"[Worker] Erro ao registrar uso para {job_id}: {e}")
            else:
                self.repo.mark_error(job_id, result.get("error", "Erro desconhecido"))

        except CancelledTranscriptionError:
            print(f"[Worker] Job {job_id} cancelado pelo usuário.")
            try:
                from infra.db import get_connection
                from datetime import datetime
                conn = get_connection()
                try:
                    conn.execute(
                        "UPDATE jobs SET status='cancelled', error='Cancelado pelo usuário', updated_at=? WHERE id=?",
                        (datetime.now().isoformat(), job_id),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                print(f"[Worker] Erro ao marcar {job_id} como cancelled: {e}")
        except Exception as e:
            print(f"[Worker] Erro ao processar {job_id}: {e}")
            try:
                self.repo.mark_error(job_id, str(e))
            except Exception:
                pass
        finally:
            self.current_job_id = None
            _state.progress_store.pop(job_id, None)
            _state.cancelled_ids.discard(job_id)
            _state.api_dictate_ids.discard(job_id)
