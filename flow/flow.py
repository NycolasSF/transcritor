# -*- coding: utf-8 -*-
"""Flow — ditado por voz estilo Wispr Flow, movido pelo motor transcritor.

Segure CTRL+WIN, fale, solte: o texto aparece onde o cursor estiver, em
qualquer aplicativo do Windows.

Peças:
  - hotkey global (lib keyboard): Ctrl+Win segurado = push-to-talk
  - microfone padrão do Windows via pyaudiowpatch
  - POST /local/dictate no transcritor (localhost:8020, sem auth, síncrono)
  - o texto é DIGITADO no app ativo via keyboard.write — não toca o clipboard
  - overlay pill (Tkinter) no rodapé da tela: nível do mic + estado
  - ícone na bandeja (pystray): pausar/retomar, sair

Rode com:  pythonw flow.py   (ou start-flow.bat)
Deps:      pip install -r requirements.txt  (keyboard, pystray, Pillow)
Requer o motor no ar (python main.py na pasta do transcritor).
"""
import array
import io
import json
import os
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import uuid
import wave
import winsound

import keyboard
import pyaudiowpatch as pyaudio

# ── Config ──────────────────────────────────────────────────────────────────
API = os.getenv("TRANSCRITOR_URL", "http://localhost:8020")
MODEL = os.getenv("FLOW_MODEL", "medium")
MODEL_CHOICES = ("small", "medium", "large-v3")   # submenu da bandeja
LANGUAGE = os.getenv("FLOW_LANGUAGE", "pt")
MIN_SECONDS = 0.3          # gravação menor que isso = toque acidental, ignora
MAX_SECONDS = 300          # teto da gravação: acima disso fecha e manda o que tem
REQUEST_TIMEOUT = 120      # segundos esperando o motor responder
RESULT_DISPLAY_SECONDS = 15  # quanto tempo o texto fica na pill, pronto pra copiar
BUSY_TIMEOUT = REQUEST_TIMEOUT + 30   # teto do estado BUSY (ver watchdog_tick)
# Muta a saída de som do Windows enquanto grava (estilo Wispr), para o mic não
# captar o que estiver tocando. FLOW_MUTE_SYSTEM=false desliga.
MUTE_WHILE_REC = os.getenv("FLOW_MUTE_SYSTEM", "true").lower() in ("1", "true", "yes")
HERE = os.path.dirname(os.path.abspath(__file__))
VOCAB_PATH = os.path.join(HERE, "vocab.txt")
# O motor apaga o job do ditado ao final (fora do histórico e da quota), então
# este arquivo é a ÚNICA cópia recuperável do que foi ditado.
HISTORY_PATH = os.path.join(HERE, "dictations.log")
# Sinais para o watchdog (watchdog.py): o heartbeat é tocado pelo loop do
# overlay só enquanto o Flow está sadio; o marcador de parada evita que um
# "Sair" na bandeja seja ressuscitado.
HEARTBEAT_PATH = os.path.join(HERE, "flow.heartbeat")
STOPPED_PATH = os.path.join(HERE, "flow.stopped")
# Pior ditado legítimo = gravação até o teto + espera do motor até o teto. Passar
# disso não é lentidão, é travamento fora do modelo (driver de áudio preso, UI
# congelada): o heartbeat para e o watchdog reinicia o Flow.
STUCK_SECONDS = MAX_SECONDS + BUSY_TIMEOUT + 60
# Envio que falha perderia o áudio para sempre — o WAV vai para cá.
FAILED_DIR = os.path.join(HERE, "failed")

CHUNK = 1024
FORMAT = pyaudio.paInt16

# Cores da marca
NAVY = "#050d1a"
GOLD = "#c9a84c"
RED = "#e05252"
GREY = "#5a5a6e"

IDLE, REC, BUSY = "idle", "rec", "busy"


def fetch_openai_usage_text() -> str:
    """Gasto local estimado com a API OpenAI (motor tenta API primeiro no ditado)."""
    try:
        req = urllib.request.Request(API + "/local/openai-usage")
        with urllib.request.urlopen(req, timeout=2) as r:
            d = json.loads(r.read().decode())
        return f"Gasto OpenAI: US$ {d.get('cost_usd', 0):.3f} ({int(d.get('calls', 0))} ditados)"
    except Exception:
        return "Gasto OpenAI: indisponível"


def load_vocab() -> str:
    """Lê vocab.txt (um termo por linha, # = comentário) e vira initial_prompt."""
    try:
        with open(VOCAB_PATH, encoding="utf-8") as f:
            terms = [t.strip() for t in f if t.strip() and not t.strip().startswith("#")]
        return "Vocabulário: " + ", ".join(terms) + "." if terms else ""
    except OSError:
        return ""


def log_dictation(text: str) -> None:
    """Append de uma linha por ditado (timestamp + texto) em dictations.log."""
    try:
        line = text.replace("\r", "").replace("\n", " ")
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{line}\n")
    except OSError:
        pass  # ponytail: histórico é conveniência — nunca derruba o ditado


def keys_really_held() -> bool:
    """Ctrl+Win ainda pressionados DE VERDADE, direto do driver.

    O hook da lib keyboard perde key-up quando outro app rouba o foco ou o shell
    consome o Win — e aí self._ctrl/_win ficam True para sempre: a gravação nunca
    fecha, nada é enviado, e o Flow parece "em loop". GetAsyncKeyState não mente.
    """
    try:
        import ctypes

        g = ctypes.windll.user32.GetAsyncKeyState
        ctrl = g(0x11) & 0x8000            # VK_CONTROL
        win = (g(0x5B) | g(0x5C)) & 0x8000  # VK_LWIN / VK_RWIN
        return bool(ctrl and win)
    except Exception:
        return True  # ponytail: sem a API, não interfere — quem decide é o hook


def set_system_mute(state: bool):
    """Muta/desmuta a saída padrão do Windows. Devolve o estado ANTERIOR (None se falhou)."""
    try:
        from comtypes import CoInitialize, CoUninitialize
        from pycaw.pycaw import AudioUtilities

        CoInitialize()
        try:
            vol = AudioUtilities.GetSpeakers().EndpointVolume
            previous = bool(vol.GetMute())
            vol.SetMute(1 if state else 0, None)
            return previous
        finally:
            CoUninitialize()
    except Exception:
        return None


def post_dictate(wav_bytes: bytes, vocab: str, model: str = MODEL) -> dict:
    """POST multipart no /local/dictate (stdlib pura, mesmo padrão do cliente canônico)."""
    boundary = uuid.uuid4().hex

    def part(name, value):
        return (f"--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"{name}\"\r\n\r\n{value}\r\n").encode()

    body = part("model", model) + part("language", LANGUAGE)
    if vocab:
        body += part("initial_prompt", vocab)
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"dictate.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode()
    body += wav_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(API + "/local/dictate", data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        return json.loads(r.read().decode())


class Flow:
    """Estado central: hotkey, gravação do mic e envio ao motor."""

    def __init__(self):
        self.enabled = True
        self.model = MODEL
        self.quit_requested = False   # setado pela bandeja; o loop Tk encerra
        self.state = IDLE
        self.error_msg = ""        # exibido no overlay por alguns segundos
        self.error_until = 0.0
        self.last_text = ""        # último texto transcrito, pronto pra copiar da pill
        self.last_text_until = 0.0
        self.levels = [0.0] * 16   # níveis recentes do mic (0..1) para o overlay
        self.vocab = load_vocab()
        self._prev_mute = None     # estado do mute do sistema antes da gravação
        self._lock = threading.Lock()
        self._ctrl = False
        self._win = False
        self._frames: list[bytes] = []
        self._pa = None
        self._stream = None
        self._rate = 16000
        self._t0 = 0.0
        self._busy_since = 0.0

    # ── Hotkey ──────────────────────────────────────────────────────────────
    def on_key(self, e) -> None:
        name = (e.name or "").lower()
        is_ctrl = "ctrl" in name
        is_win = "windows" in name or name == "win"
        if not (is_ctrl or is_win):
            return
        down = e.event_type == "down"
        if is_ctrl:
            self._ctrl = down
        if is_win:
            self._win = down

        if self.enabled and self._ctrl and self._win and self.state == IDLE:
            threading.Thread(target=self._start_recording, daemon=True).start()
        elif self.state == REC and not (self._ctrl and self._win):
            threading.Thread(target=self._finish, daemon=True).start()

    # ── Gravação ────────────────────────────────────────────────────────────
    def _start_recording(self) -> None:
        with self._lock:
            if self.state != IDLE:
                return
            self.state = REC
        try:
            self._pa = pyaudio.PyAudio()
            info = self._pa.get_default_input_device_info()
            self._rate = int(info.get("defaultSampleRate", 16000))
            self._stream = self._pa.open(
                format=FORMAT, channels=1, rate=self._rate, input=True,
                input_device_index=info["index"], frames_per_buffer=CHUNK,
            )
        except Exception as e:
            self._teardown_audio()
            self._fail(f"Microfone indisponível: {e}")
            return

        self._frames = []
        self._t0 = time.time()
        threading.Thread(target=self._beep_then_mute, daemon=True).start()
        while self.state == REC:
            try:
                data = self._stream.read(CHUNK, exception_on_overflow=False)
            except Exception:
                break
            self._frames.append(data)
            samples = array.array("h", data)
            peak = max((abs(s) for s in samples), default=0) / 32768.0
            self.levels.append(min(peak * 3.0, 1.0))  # ganho p/ visual responder
            del self.levels[0]
            # Rede de segurança do push-to-talk: o hook pode ter perdido o key-up
            # (gravação eterna) — o driver é a fonte de verdade. E teto de duração
            # para o caso de o teclado ficar fisicamente preso.
            if not keys_really_held() or time.time() - self._t0 > MAX_SECONDS:
                break

        # Sair do loop por QUALQUER motivo — tecla solta, teto de duração, falha
        # de leitura do mic — tem que fechar o ditado. Sem isto o `break` do read
        # deixava o state em REC para sempre e a hotkey morria (só reiniciar
        # resolvia): era o "gravou e nunca enviou nada".
        if self.state == REC:
            self._finish()

    def _beep_then_mute(self) -> None:
        """Beep de início audível e SÓ DEPOIS muta o sistema (mesma thread: sem corrida)."""
        winsound.Beep(880, 70)
        if MUTE_WHILE_REC and self.state == REC:
            self._prev_mute = set_system_mute(True)
            if self.state != REC:  # soltou durante o mute — desfaz já
                self._restore_mute()

    def _restore_mute(self) -> None:
        prev, self._prev_mute = self._prev_mute, None
        if prev is False:  # só desmuta se NÃO estava mudo antes da gravação
            set_system_mute(False)

    def _finish(self) -> None:
        with self._lock:
            if self.state != REC:
                return
            self.state = BUSY
            self._busy_since = time.time()
        self._restore_mute()
        _beep(440)
        duration = time.time() - self._t0
        frames, self._frames = self._frames, []
        self._teardown_audio()

        if duration < MIN_SECONDS or not frames:
            self.state = IDLE
            return

        wav_bytes = b""
        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self._rate)
                wf.writeframes(b"".join(frames))
            wav_bytes = buf.getvalue()
            result = post_dictate(wav_bytes, self.vocab, self.model)
            text = (result.get("text") or "").strip()
        except urllib.error.URLError:
            self._save_failed(wav_bytes)
            self._fail("Motor fora do ar — áudio salvo em flow/failed")
            return
        except Exception as e:
            self._save_failed(wav_bytes)
            self._fail(f"Erro: {e} (áudio em flow/failed)")
            return

        if text:
            log_dictation(text)  # grava ANTES de digitar: se o write falhar, o texto sobrevive
            self.last_text = text
            self.last_text_until = time.time() + RESULT_DISPLAY_SECONDS
            self._wait_modifiers_released()
            keyboard.write(text + " ", delay=0)
        self.state = IDLE

    def _wait_modifiers_released(self, timeout: float = 3.0) -> None:
        """Espera Ctrl/Win soltarem de verdade — senão o texto digitado vira atalho."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                held = (keyboard.is_pressed("ctrl") or keyboard.is_pressed("left windows")
                        or keyboard.is_pressed("right windows"))
            except Exception:
                held = self._ctrl or self._win
            if not held:
                return
            time.sleep(0.03)

    def _teardown_audio(self) -> None:
        try:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            pass
        finally:
            self._stream = None
        try:
            if self._pa:
                self._pa.terminate()
        except Exception:
            pass
        finally:
            self._pa = None

    def _save_failed(self, wav_bytes: bytes) -> None:
        """Guarda o WAV que não conseguiu ser enviado — senão o ditado sumia."""
        if not wav_bytes:
            return
        try:
            os.makedirs(FAILED_DIR, exist_ok=True)
            path = os.path.join(FAILED_DIR, f"dictate_{time.strftime('%Y%m%d_%H%M%S')}.wav")
            with open(path, "wb") as f:
                f.write(wav_bytes)
        except OSError:
            pass

    def watchdog_tick(self) -> None:
        """Autocura chamada pelo loop do overlay.

        Sem teto, um BUSY pendurado (motor travado, keyboard.write preso) deixa a
        hotkey morta para sempre: Ctrl+Win só grava a partir de IDLE. Aqui o Flow
        se destrava sozinho, sem depender de reinício externo.
        """
        if self.state == BUSY and time.time() - self._busy_since > BUSY_TIMEOUT:
            self._fail("Sem resposta do motor — ditado abortado")

    def _fail(self, msg: str) -> None:
        self._restore_mute()
        self.error_msg = msg
        self.error_until = time.time() + 3.0
        self.state = IDLE


def _beep(freq: int) -> None:
    threading.Thread(target=lambda: winsound.Beep(freq, 70), daemon=True).start()


# ── Overlay (pill no rodapé, Tkinter) ───────────────────────────────────────
class Overlay:
    W, H, R = 240, 46, 22
    TRANSPARENT = "#ff00ff"

    def __init__(self, root: tk.Tk, flow: Flow):
        self.root = root
        self.flow = flow
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", self.TRANSPARENT)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{self.W}x{self.H}+{(sw - self.W) // 2}+{sh - self.H - 70}")
        self.canvas = tk.Canvas(root, width=self.W, height=self.H,
                                bg=self.TRANSPARENT, highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_click)
        self._visible = False
        self._last_beat = 0.0
        self._show_copied = False
        root.withdraw()
        self._tick()

    def _on_click(self, _event) -> None:
        """Clique na pill com o resultado copia o texto pra área de transferência."""
        f = self.flow
        show_result = bool(f.last_text) and time.time() < f.last_text_until
        show_error = time.time() < f.error_until
        if f.state != IDLE or not show_result or show_error:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(f.last_text)
            self.root.update()
        except tk.TclError:
            pass
        self._show_copied = True
        f.last_text_until = time.time() + 1.2  # feedback breve e some

    def _round_pill(self):
        c, w, h, r = self.canvas, self.W, self.H, self.R
        c.create_oval(0, 0, 2 * r, h, fill=NAVY, outline=NAVY)
        c.create_oval(w - 2 * r, 0, w, h, fill=NAVY, outline=NAVY)
        c.create_rectangle(r, 0, w - r, h, fill=NAVY, outline=NAVY)

    def _beat(self) -> None:
        """Heartbeat para o watchdog — só bate se o Flow está sadio de verdade:
        este método roda no loop do Tk (main thread viva) e nenhum ditado está
        pendurado. Heartbeat velho = watchdog mata e sobe de novo."""
        f = self.flow
        if f.state != IDLE and time.time() - f._t0 > STUCK_SECONDS:
            return
        if time.time() - self._last_beat < 5:
            return
        self._last_beat = time.time()
        try:
            with open(HEARTBEAT_PATH, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
        except OSError:
            pass

    def _tick(self):
        f = self.flow
        f.watchdog_tick()
        self._beat()
        if f.quit_requested:
            # Sair pedido pela thread da bandeja — destruir Tk AQUI (main thread);
            # chamar root.destroy/after de outra thread derruba o processo.
            self.root.destroy()
            return
        show_error = time.time() < f.error_until
        show_result = f.state == IDLE and bool(f.last_text) and time.time() < f.last_text_until
        if not show_result:
            self._show_copied = False
        target_visible = f.state in (REC, BUSY) or show_error or show_result

        if target_visible and not self._visible:
            self.root.deiconify()
            self.root.lift()
            self._visible = True
        elif not target_visible and self._visible:
            self.root.withdraw()
            self._visible = False

        if self._visible:
            self.canvas.delete("all")
            self._round_pill()
            if show_error and f.state == IDLE:
                self.canvas.create_text(self.W // 2, self.H // 2, text=f.error_msg[:38],
                                        fill=RED, font=("Segoe UI", 9))
            elif f.state == REC:
                self.canvas.create_oval(16, self.H // 2 - 5, 26, self.H // 2 + 5,
                                        fill=RED, outline=RED)
                n = len(f.levels)
                span = self.W - 60
                for i, lvl in enumerate(f.levels):
                    x = 40 + i * (span // n)
                    bar = max(3, int(lvl * (self.H - 18)))
                    self.canvas.create_rectangle(x, self.H // 2 - bar // 2,
                                                 x + 4, self.H // 2 + bar // 2,
                                                 fill=GOLD, outline=GOLD)
            elif f.state == BUSY:
                dots = "." * (int(time.time() * 3) % 4)
                self.canvas.create_text(self.W // 2, self.H // 2,
                                        text=f"transcrevendo{dots}",
                                        fill=GOLD, font=("Segoe UI", 10, "italic"))
            elif show_result:
                if self._show_copied:
                    self.canvas.create_text(self.W // 2, self.H // 2, text="Copiado!",
                                            fill=GOLD, font=("Segoe UI", 10, "bold"))
                else:
                    preview = f.last_text if len(f.last_text) <= 34 else f.last_text[:31] + "..."
                    self.canvas.create_text(self.W // 2, self.H // 2 - 7, text=preview,
                                            fill="#ffffff", font=("Segoe UI", 9))
                    self.canvas.create_text(self.W // 2, self.H // 2 + 11,
                                            text="clique pra copiar",
                                            fill=GOLD, font=("Segoe UI", 8))
        self.root.after(40, self._tick)


# ── Bandeja (pystray) ───────────────────────────────────────────────────────
def start_tray(flow: Flow, root: tk.Tk) -> None:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("[Flow] pystray/Pillow ausentes — rodando sem ícone na bandeja.")
        return

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=NAVY)
    d.ellipse((22, 14, 42, 40), fill=GOLD)          # cápsula do mic
    d.rectangle((30, 40, 34, 50), fill=GOLD)         # haste

    def toggle(icon, item):
        flow.enabled = not flow.enabled

    def quit_app(icon, item):
        icon.stop()
        try:  # marcador: o watchdog não ressuscita um Flow encerrado de propósito
            open(STOPPED_PATH, "w").close()
        except OSError:
            pass
        flow.quit_requested = True  # o tick do overlay (main thread) encerra o Tk

    def open_history(icon, item):
        try:
            if not os.path.exists(HISTORY_PATH):
                open(HISTORY_PATH, "a", encoding="utf-8").close()
            os.startfile(HISTORY_PATH)
        except OSError:
            pass

    def pick_model(name):
        def _set(icon, item):
            flow.model = name
        return _set

    def model_checked(name):
        return lambda item: flow.model == name

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: "Pausar" if flow.enabled else "Retomar", toggle),
        pystray.MenuItem(lambda item: fetch_openai_usage_text(), lambda icon, item: None),
        pystray.MenuItem("Histórico de ditados", open_history),
        pystray.MenuItem("Modelo", pystray.Menu(*[
            pystray.MenuItem(m, pick_model(m), checked=model_checked(m), radio=True)
            for m in MODEL_CHOICES
        ])),
        pystray.MenuItem("Sair", quit_app),
    )
    icon = pystray.Icon("flow", img, "Flow — ditado (Ctrl+Win)", menu)
    threading.Thread(target=icon.run, daemon=True).start()


_instance_lock = None


def _ensure_single_instance() -> None:
    """Trava de instância única via porta local — dois Flows = hotkey em dobro."""
    global _instance_lock
    import socket
    _instance_lock = socket.socket()
    try:
        _instance_lock.bind(("127.0.0.1", 8021))
    except OSError:
        print("[Flow] Já existe um Flow rodando — saindo.")
        sys.exit(0)


def main() -> None:
    _ensure_single_instance()
    try:  # start (manual ou pelo watchdog) desfaz o "Sair" anterior
        os.remove(STOPPED_PATH)
    except OSError:
        pass
    # Crash duro (COM, Tk, hook nativo) não deixa traceback no console — o
    # faulthandler despeja o estado das threads em flow-crash.log.
    import faulthandler
    # append: reinício pelo watchdog não pode apagar o traceback da trava anterior
    crash_log = open(os.path.join(HERE, "flow-crash.log"), "a")
    faulthandler.enable(crash_log)

    flow = Flow()
    keyboard.hook(flow.on_key)

    root = tk.Tk()
    Overlay(root, flow)
    start_tray(flow, root)
    print(f"[Flow] Ativo. Segure Ctrl+Win para ditar (modelo {MODEL}, motor {API}).")
    try:
        root.mainloop()
    finally:
        keyboard.unhook_all()
        os._exit(0)  # mata threads de gravação/tray pendentes


if __name__ == "__main__":
    main()
