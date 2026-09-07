"""Transcrição via API OpenAI — usada só no ditado do Flow (source=flow-dictate).

Mesmo contrato de retorno do transcriber.py local (success/text/language/segments),
pra ser um substituto direto no worker. Levanta em qualquer falha de rede/HTTP —
quem chama decide o fallback pro Whisper local.
"""
import json
import os
import threading
import time

import httpx

from core import settings

TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"

# US$/min por modelo (ago/2026). Desconhecido = usa o teto (0.006) pra não subestimar.
_PRICE_PER_MIN = {
    "whisper-1": 0.006,
    "gpt-4o-transcribe": 0.006,
    "gpt-4o-mini-transcribe": 0.003,
}

_USAGE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "openai_usage.json")
_usage_lock = threading.Lock()


def transcribe_openai(file_path: str, language: str | None = None, prompt: str | None = None) -> dict:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não configurada")

    data = {"model": settings.OPENAI_TRANSCRIBE_MODEL}
    if language and language != "auto":
        data["language"] = language
    if prompt:
        data["prompt"] = prompt

    with open(file_path, "rb") as fh:
        files = {"file": (os.path.basename(file_path), fh, "audio/wav")}
        resp = httpx.post(
            TRANSCRIBE_URL,
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            data=data,
            files=files,
            timeout=30,
        )
    resp.raise_for_status()
    text = (resp.json().get("text") or "").strip()
    return {"success": True, "text": text, "language": language or "?", "segments": []}


def record_usage(duration_seconds: float) -> None:
    """Estimativa local de gasto (não requer permissão de admin da OpenAI)."""
    price_per_min = _PRICE_PER_MIN.get(settings.OPENAI_TRANSCRIBE_MODEL, 0.006)
    cost = (duration_seconds / 60) * price_per_min
    with _usage_lock:
        summary = _read_usage()
        summary["calls"] += 1
        summary["seconds"] += duration_seconds
        summary["cost_usd"] += cost
        summary["updated_at"] = time.time()
        try:
            with open(_USAGE_PATH, "w", encoding="utf-8") as fh:
                json.dump(summary, fh)
        except OSError:
            pass  # ponytail: contador é conveniência — nunca derruba o ditado


def _read_usage() -> dict:
    try:
        with open(_USAGE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"calls": 0, "seconds": 0.0, "cost_usd": 0.0, "updated_at": 0}


def get_usage_summary() -> dict:
    with _usage_lock:
        return _read_usage()
