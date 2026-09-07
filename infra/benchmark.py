import time
from datetime import datetime

from infra.db import get_connection


def record(job_id: str, model: str, device: str, audio_duration: float, elapsed: float) -> None:
    """Grava uma linha de benchmark (usado pelo BenchmarkContext e por chamadas fora do Whisper local, ex.: API OpenAI)."""
    rtf = round(elapsed / audio_duration, 3) if audio_duration else 0.0
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO benchmarks
              (job_id, model, device, audio_duration, transcription_time, rtf, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, model, device, audio_duration, round(elapsed, 3), rtf, datetime.now().isoformat()),
        )
        conn.commit()
    except Exception as e:
        print(f"[Benchmark] Erro ao salvar RTF: {e}")
    finally:
        conn.close()


class BenchmarkContext:
    """Context manager that records RTF after a transcription job."""

    def __init__(self, job_id: str, model: str, device: str, audio_duration: float):
        self._job_id = job_id
        self._model = model
        self._device = device
        self._audio_duration = audio_duration
        self._start: float = 0.0

    def __enter__(self) -> "BenchmarkContext":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        elapsed = time.monotonic() - self._start
        record(self._job_id, self._model, self._device, self._audio_duration, elapsed)
        # Do not suppress exceptions from the wrapped block
        return False
