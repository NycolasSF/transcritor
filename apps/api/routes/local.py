"""
Endpoints locais sem autenticação — acessíveis apenas de 127.0.0.1.
Usados pelo Claude Code e outras ferramentas internas para consultar transcrições.
"""
from __future__ import annotations

import asyncio
import os
import uuid
import wave
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile

from apps.api import state
from core import settings

router = APIRouter(prefix="/local", tags=["local"])


def _require_localhost(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Acesso apenas local")


def _job_meta(j: dict) -> dict:
    """Retorna campos de metadados sem o texto completo."""
    return {
        "id":        j.get("id"),
        "title":     j.get("title") or j.get("source") or j.get("id"),
        "source":    j.get("source"),
        "model":     j.get("model"),
        "device":    j.get("device"),
        "duration":  j.get("duration"),
        "language":  j.get("language"),
        "timestamp": j.get("timestamp"),
        "status":    j.get("status"),
    }


@router.get("/transcriptions")
async def list_transcriptions(request: Request, limit: int = Query(100, le=500)):
    """Lista todas as transcrições concluídas (metadados, sem texto)."""
    _require_localhost(request)
    jobs = state.repo.list_jobs(limit=limit)
    return [_job_meta(j) for j in jobs if j.get("status") == "done"]


@router.get("/transcriptions/{tid}")
async def get_transcription(tid: str, request: Request):
    """Retorna uma transcrição completa incluindo texto e segmentos."""
    _require_localhost(request)
    job = state.repo.get_job(tid)
    if not job or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Transcrição não encontrada ou ainda em andamento")
    return job


@router.post("/dictate")
async def dictate(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form("medium"),
    language: str = Form("pt"),
    initial_prompt: str = Form(""),
):
    """Ditado síncrono (cliente Flow): transcreve e devolve o texto na resposta.

    Sem auth (localhost). O job passa pela fila normal — o pool segue único dono
    da GPU — e é apagado ao final: ditado não é histórico, não conta quota.
    """
    _require_localhost(request)
    if model not in settings.VALID_MODELS:
        model = "medium"

    card_id = "dict" + uuid.uuid4().hex[:8]
    os.makedirs(settings.RECORDINGS_DIR, exist_ok=True)
    dest = os.path.join(settings.RECORDINGS_DIR, f"dictate_{card_id}.wav")
    with open(dest, "wb") as out:
        out.write(await file.read())

    try:
        with wave.open(dest) as w:
            duration = w.getnframes() / (w.getframerate() or 1)
    except Exception:
        duration = 0.0

    state.repo.create_job({
        "id": card_id,
        "status": "pending",
        "model": model,
        "device": state.DEVICE,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "duration": duration,
        "file_path": dest,
        "title": "",
        "source": "flow-dictate",
        "user_id": None,  # sem dono: fora do histórico da UI e da quota
        "diarize": 0,
        "input_language": language,
        "initial_prompt": initial_prompt or None,
        "created_at": datetime.now().isoformat(),
    })
    state.queue.enqueue(card_id)

    # ponytail: espera na própria request (ditado dura segundos). Se um lote
    # longo ocupar a GPU, o ditado espera atrás dele — fila única por design;
    # prioridade na fila se um dia incomodar.
    job = None
    try:
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        while True:
            await asyncio.sleep(0.15)
            job = state.repo.get_job(card_id)
            if job is None or job["status"] in ("done", "error", "cancelled"):
                break
            if loop.time() - t0 > 180:
                state.cancelled_ids.add(card_id)
                raise HTTPException(status_code=504, detail="Timeout no ditado (motor ocupado?)")

        if job is None or job["status"] != "done" or job.get("error"):
            detail = (job or {}).get("error") or "Falha na transcrição"
            raise HTTPException(status_code=500, detail=detail)

        return {
            "text": (job["text"] or "").strip(),
            "language": job.get("language"),
            "duration": duration,
        }
    finally:
        try:
            os.remove(dest)
        except OSError:
            pass
        try:
            state.repo.delete_job(card_id)
        except Exception:
            pass


@router.get("/openai-usage")
async def openai_usage(request: Request):
    """Gasto estimado localmente com a API OpenAI (ditado do Flow).

    A OpenAI não expõe saldo/gasto pra uma key de projeto comum (só via Admin
    API key com escopo api.usage.read) — este número é calculado aqui, por
    duração de áudio × preço do modelo, não é o saldo oficial da conta.
    """
    _require_localhost(request)
    from core.services.openai_transcriber import get_usage_summary

    return get_usage_summary()


@router.get("/search")
async def search_transcriptions(
    request: Request,
    q: str = Query(..., min_length=1, description="Termo de busca"),
    limit: int = Query(10, le=50),
):
    """
    Busca por título e conteúdo de texto das transcrições.
    Retorna lista com id, título e trecho do texto onde o termo foi encontrado.
    """
    _require_localhost(request)
    q_lower = q.lower()
    jobs = state.repo.list_jobs(limit=500, full=True)  # busca em conteúdo precisa do text inteiro
    results = []

    for j in jobs:
        if j.get("status") != "done":
            continue

        title = (j.get("title") or j.get("source") or "").lower()
        text  = (j.get("text") or "").lower()

        if q_lower not in title and q_lower not in text:
            continue

        # Extrai trecho ao redor da primeira ocorrência
        snippet = ""
        idx = text.find(q_lower)
        if idx >= 0:
            start = max(0, idx - 80)
            end   = min(len(text), idx + len(q_lower) + 120)
            raw   = j.get("text", "")[start:end].replace("\n", " ").strip()
            snippet = ("…" if start > 0 else "") + raw + ("…" if end < len(text) else "")

        results.append({
            **_job_meta(j),
            "snippet":    snippet,
            "match_title": q_lower in title,
            "match_text":  idx >= 0,
        })

        if len(results) >= limit:
            break

    return {"query": q, "total": len(results), "results": results}
