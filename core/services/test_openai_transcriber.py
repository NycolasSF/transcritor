# -*- coding: utf-8 -*-
"""Check da matemática de custo/acumulo do contador de uso da API OpenAI.

Não bate na rede (custaria dinheiro) — só valida record_usage/get_usage_summary.
Uso: python test_openai_transcriber.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.services import openai_transcriber as ot  # noqa: E402

ot._USAGE_PATH = os.path.join(tempfile.gettempdir(), "flow_openai_usage_test.json")
if os.path.exists(ot._USAGE_PATH):
    os.remove(ot._USAGE_PATH)

assert ot.get_usage_summary() == {"calls": 0, "seconds": 0.0, "cost_usd": 0.0, "updated_at": 0}

ot.record_usage(30.0)   # 30s no modelo default (gpt-4o-transcribe, $0.006/min)
s = ot.get_usage_summary()
assert s["calls"] == 1, s
assert abs(s["cost_usd"] - 0.003) < 1e-9, s  # 0.5 min * 0.006

ot.record_usage(60.0)   # +1 min
s = ot.get_usage_summary()
assert s["calls"] == 2, s
assert abs(s["cost_usd"] - 0.009) < 1e-9, s  # acumulado: 0.003 + 0.006

os.remove(ot._USAGE_PATH)
print("ok")
