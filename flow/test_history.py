# -*- coding: utf-8 -*-
"""Self-check do log de ditados: UTF-8 com acento e uma linha por ditado.

Uso: python test_history.py   (offline, não precisa do motor no ar)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flow  # noqa: E402

flow.HISTORY_PATH = os.path.join(tempfile.mkdtemp(), "dictations.log")
flow.log_dictation("Reforma tributária: cálculo do INSS\ncom quebra de linha")
flow.log_dictation("segundo ditado")

with open(flow.HISTORY_PATH, encoding="utf-8") as f:
    lines = f.read().splitlines()

assert len(lines) == 2, lines
assert lines[0].endswith("Reforma tributária: cálculo do INSS com quebra de linha"), lines[0]
assert lines[1].endswith("segundo ditado"), lines[1]
print("ok — dictations.log grava acento e uma linha por ditado")
