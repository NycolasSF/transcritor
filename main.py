import os
import threading
import time

import uvicorn


def _disable_console_quick_edit() -> None:
    """Desliga o QuickEdit do console (Windows).

    Com o QuickEdit ligado, um clique acidental na janela põe o console em modo
    de seleção e BLOQUEIA toda escrita no stdout. O worker congela no meio do
    print de "[Whisper] Carregando modelo…" — ainda segurando o _model_lock — e
    aí nenhuma transcrição mais anda: o motor fica no ar, responde HTTP, e todo
    job dorme em 'processing' para sempre. Foi exatamente o que travou o motor
    em 2026-07-29 (título da janela virou "Selecionar Transcritor").
    """
    try:
        import ctypes

        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            ENABLE_EXTENDED_FLAGS, ENABLE_QUICK_EDIT = 0x0080, 0x0040
            k.SetConsoleMode(h, (mode.value & ~ENABLE_QUICK_EDIT) | ENABLE_EXTENDED_FLAGS)
    except Exception:
        pass  # ponytail: sem console (serviço, pipe) não há o que desligar


_instance_lock = None


def _ensure_single_instance() -> None:
    """Trava de instância única — dois motores na mesma GPU = CUDA out of memory.

    A porta 8020 só passa a ouvir DEPOIS do lifespan (que espera o microsserviço
    de diarização no WSL), então "porta fechada" não significa "motor ausente":
    quem sobe o motor de novo nessa janela (o watchdog, de 2 em 2 min) criaria um
    segundo processo carregando Whisper na mesma placa. A trava fecha essa
    corrida logo no boot, antes de qualquer import de torch.
    """
    global _instance_lock
    import socket

    _instance_lock = socket.socket()
    try:
        _instance_lock.bind(("127.0.0.1", 8022))
    except OSError:
        print("[Motor] Já existe um Transcritor rodando (trava 8022) — saindo.")
        raise SystemExit(0)


def _fallback_loop(server: uvicorn.Server) -> None:
    """Keep main thread alive when dashboard is unavailable."""
    try:
        while not server.should_exit:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True


if __name__ == "__main__":
    _ensure_single_instance()
    _disable_console_quick_edit()
    config = uvicorn.Config(
        "apps.api.main:app",
        # ponytail: loopback por padrao. Em 0.0.0.0 o motor ficava aberto a QUALQUER
        # rede em que o PC entrasse (wifi de fora inclusive), sem auth, com acesso a
        # arquivo e a OPENAI_API_KEY. Escotilha: TRANSCRITOR_HOST=0.0.0.0 no ambiente.
        host=os.getenv("TRANSCRITOR_HOST", "127.0.0.1"),
        port=8020,
        reload=False,
        log_level="warning",        # silencia access logs no painel
        access_log=False,
    )
    server = uvicorn.Server(config)

    t = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    t.start()

    # Aguarda uvicorn inicializar (singletons prontos em apps.api.main startup)
    while not server.started:
        time.sleep(0.05)

    # Importa state DEPOIS do startup — garante que singletons estão inicializados
    from apps.api import state

    try:
        from monitor.dashboard import run_dashboard
        run_dashboard(state, server)
    except KeyboardInterrupt:
        server.should_exit = True
    except Exception as e:
        print(f"[Dashboard] Erro ao iniciar painel: {e}")
        print("[Dashboard] Servidor continua rodando normalmente.")
        _fallback_loop(server)
    finally:
        server.should_exit = True
