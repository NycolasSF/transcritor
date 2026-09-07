# -*- coding: utf-8 -*-
"""Check do resultado copiável na pill (ver RESULT_DISPLAY_SECONDS em flow.py).

Uso: python test_copy_result.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flow import IDLE, RESULT_DISPLAY_SECONDS, Flow  # noqa: E402

f = Flow()
assert f.last_text == "" and f.last_text_until == 0.0, "estado inicial deve vir vazio"

# Simula o que _finish faz ao terminar uma transcrição com texto.
f.last_text = "teste de ditado"
f.last_text_until = time.time() + RESULT_DISPLAY_SECONDS
f.state = IDLE

show_result = f.state == IDLE and bool(f.last_text) and time.time() < f.last_text_until
assert show_result, "logo após transcrever, a pill tem que oferecer copiar"

f.last_text_until = time.time() - 1
show_result = f.state == IDLE and bool(f.last_text) and time.time() < f.last_text_until
assert not show_result, "depois do prazo (ou do clique), a pill não deve mais oferecer copiar"

print("ok")
