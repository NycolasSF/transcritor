# -*- coding: utf-8 -*-
"""Watchdog do stack de ditado — sobe/reinicia o Flow e o motor quando travam.

Instalar (uma vez): `install-watchdog.bat` registra no Agendador de Tarefas do
Windows uma execução a cada 2 min via pythonw (sem janela piscando).
Remover: `schtasks /Delete /TN Flow-Watchdog /F`.
Rodar na mão: `schtasks /Run /TN Flow-Watchdog`.

Duas vigílias, cada uma com seu sinal:

1. **Flow** — mtime de `flow.heartbeat`, que o Flow atualiza a cada 5s SÓ enquanto
   o loop do Tk está vivo e nenhum ditado está pendurado (`Overlay._beat`). Um
   heartbeat velho cobre os três modos de falha numa checagem: processo morto,
   UI travada e estado preso em REC/BUSY.
2. **Motor** — job em `processing` sem tocar `updated_at` há muito tempo. O motor
   travado continua no ar e respondendo HTTP (só o worker congela), então porta
   aberta não prova nada: quem prova é o progresso do job.

Se o Flow foi encerrado de propósito ("Sair" na bandeja), ele deixa
`flow.stopped` e o watchdog não mexe em nada — subir de novo é ação humana.
"""
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
HEARTBEAT = os.path.join(HERE, "flow.heartbeat")
STOPPED = os.path.join(HERE, "flow.stopped")
LOG = os.path.join(HERE, "watchdog.log")
DB = os.path.join(HERE, "..", "transcriptions", "data.db")
STALE_SECONDS = 90          # heartbeat do Flow mais velho que isso = travado/morto
ENGINE_PORT = 8020
# Generoso de propósito: diarização e áudio de horas passam minutos sem tocar o
# progresso. Só um travamento de verdade fica parado além disso.
ENGINE_STUCK_SECONDS = 20 * 60
# Ditado é curto por natureza (segundos) e é o que dói na hora: 3 min parado já é
# trava. Fila cheia atrasa o começo, não o progresso — mark_processing já bate o
# updated_at, então a contagem só corre com o job em mãos do worker.
DICTATE_STUCK_SECONDS = 3 * 60
NO_WINDOW = 0x08000000      # CREATE_NO_WINDOW


def log(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{msg}\n")
    except OSError:
        pass


def _run(args) -> str:
    return subprocess.run(args, capture_output=True, text=True,
                          creationflags=NO_WINDOW).stdout or ""


# ── Flow ────────────────────────────────────────────────────────────────────
def heartbeat_age():
    """Segundos desde o último heartbeat, ou None se o arquivo não existe."""
    try:
        return time.time() - os.path.getmtime(HEARTBEAT)
    except OSError:
        return None


def is_hung(age) -> bool:
    """None = Flow parado (nada a matar). Velho = travado ou morto sujo."""
    return age is not None and age > STALE_SECONDS


def kill_flow() -> None:
    """Mata o PID gravado no heartbeat e limpa o arquivo."""
    try:
        pid = open(HEARTBEAT, encoding="utf-8").read().strip()
    except OSError:
        return
    try:
        # o PID pode ter sido reciclado pelo Windows — só mata se ainda é python
        if pid.isdigit() and "python" in _run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"]).lower():
            _run(["taskkill", "/F", "/PID", pid])
            log(f"Flow: pid {pid} morto")
    finally:
        try:
            os.remove(HEARTBEAT)  # evita matar duas vezes no ciclo seguinte
        except OSError:
            pass


# ── Motor ───────────────────────────────────────────────────────────────────
def stuck_job():
    """(job_id, idade_s) do job 'processing' parado há tempo demais, ou None."""
    try:
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        try:
            rows = db.execute(
                "SELECT id, updated_at, source FROM jobs WHERE status='processing'").fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    for jid, updated, source in rows:
        try:
            age = (datetime.now() - datetime.fromisoformat(updated)).total_seconds()
        except (TypeError, ValueError):
            continue
        limit = (DICTATE_STUCK_SECONDS if source == "flow-dictate"
                 else ENGINE_STUCK_SECONDS)
        if age > limit:
            return jid, age
    return None


def _pid_listening_on(port: int, netstat_out: str):
    """PID que escuta na porta, lido da saída do netstat (puro, testável)."""
    for line in netstat_out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
            return parts[4]
    return None


def restart_engine() -> None:
    """Mata o motor e limpa os jobs órfãos (o start-flow.bat sobe de novo)."""
    pid = _pid_listening_on(ENGINE_PORT, _run(["netstat", "-ano", "-p", "TCP"]))
    if pid:
        _run(["taskkill", "/F", "/PID", pid])
        log(f"motor: pid {pid} morto")
    # Sem isto o próximo ciclo leria os mesmos jobs como travados e o watchdog
    # reiniciaria o motor de 2 em 2 min, para sempre.
    try:
        db = sqlite3.connect(DB, timeout=10)
        try:
            db.execute("UPDATE jobs SET status='error', "
                       "error='Travado no motor — reiniciado pelo watchdog', "
                       "updated_at=? WHERE status='processing'",
                       (datetime.now().isoformat(),))
            db.commit()
        finally:
            db.close()
    except sqlite3.Error as e:
        log(f"motor: falha ao limpar jobs travados: {e}")


# ── Ciclo ───────────────────────────────────────────────────────────────────
def main() -> None:
    if os.path.exists(STOPPED):
        return

    stuck = stuck_job()
    if stuck:
        log(f"motor travado: job {stuck[0]} sem progresso há {stuck[1] / 60:.0f} min")
        restart_engine()

    age = heartbeat_age()
    if is_hung(age):
        log(f"Flow travado: {age:.0f}s sem heartbeat")
        kill_flow()
    elif age is None:
        log("Flow fora do ar")

    # start-flow.bat é idempotente (trava de instância única na porta 8021): se o
    # Flow está de pé ele sai na hora, e o motor em 8020 sobe se estiver faltando.
    subprocess.run([os.path.join(HERE, "start-flow.bat")], cwd=HERE,
                   creationflags=NO_WINDOW)


if __name__ == "__main__":
    if "--check" in sys.argv:
        assert is_hung(None) is False, "sem heartbeat: subir, nunca matar"
        assert is_hung(5.0) is False, "batendo agora: nao mexer"
        assert is_hung(STALE_SECONDS + 1) is True, "heartbeat velho: reiniciar"
        sample = ("  TCP    0.0.0.0:8020    0.0.0.0:0    LISTENING    6784\n"
                  "  TCP    127.0.0.1:8020  127.0.0.1:1  ESTABLISHED  999\n")
        assert _pid_listening_on(8020, sample) == "6784", "acha o pid do motor"
        assert _pid_listening_on(9999, sample) is None, "porta ausente = None"
        print("ok")
    else:
        main()
