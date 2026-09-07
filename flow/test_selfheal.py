# -*- coding: utf-8 -*-
"""Check da autocura do Flow (os dois travamentos reais de 2026-07-29).

Uso: python test_selfheal.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flow import BUSY, BUSY_TIMEOUT, IDLE, REC, STUCK_SECONDS, MAX_SECONDS, Flow  # noqa: E402

f = Flow()

# 1. BUSY pendurado (motor travado) tem que voltar a IDLE — senão a hotkey morre.
f.state, f._busy_since = BUSY, time.time()
f.watchdog_tick()
assert f.state == BUSY, "BUSY recente não pode ser abortado"

f._busy_since = time.time() - BUSY_TIMEOUT - 1
f.watchdog_tick()
assert f.state == IDLE, "BUSY estourado tem que voltar a IDLE"
assert f.error_msg, "aborto precisa avisar no overlay"

# 2. REC não é abortado pelo tick (quem cuida dele é o loop de gravação, pelo
#    estado real das teclas) — o tick só não pode matar gravação em andamento.
f.state, f.error_msg = REC, ""
f._t0 = time.time() - MAX_SECONDS - 1
f.watchdog_tick()
assert f.state == REC, "tick não mexe em REC"

# 3. O heartbeat só pode reprovar o que passa do pior ditado legítimo, senão o
#    watchdog mata um ditado longo no meio.
assert STUCK_SECONDS > MAX_SECONDS + BUSY_TIMEOUT, "heartbeat apertado demais"

# 4. O bug do "loop": hook com key-up perdido (_ctrl/_win presos em True) não pode
#    gravar para sempre — o loop consulta o driver e fecha. Grava ~0,1s do mic e
#    descarta por MIN_SECONDS, sem enviar nada ao motor. Sem microfone, cai no
#    _fail e também volta a IDLE: o que importa é NÃO ficar em REC.
import threading  # noqa: E402

f2 = Flow()
f2._ctrl = f2._win = True          # hook mentindo, como no travamento real
threading.Thread(target=f2._start_recording, daemon=True).start()
t0 = time.time()
while f2.state != IDLE and time.time() - t0 < 15:  # abrir o mic frio pode demorar
    time.sleep(0.05)
assert f2.state == IDLE, f"gravação não fechou sozinha (state={f2.state})"

print("ok")
