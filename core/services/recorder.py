import threading
import time
import wave
from datetime import datetime
from pathlib import Path

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None  # ponytail: macOS/Linux sem WASAPI; o servidor sobe, só a gravação do sistema fica indisponível

from core import settings

RECORDINGS_DIR = Path(settings.RECORDINGS_DIR)
RECORDINGS_DIR.mkdir(exist_ok=True)

CHUNK = 1024
FORMAT = 8  # pyaudio.paInt16


class AudioRecorder:
    def __init__(self):
        self.is_recording = False
        self.frames = []
        self.thread = None
        self.start_time = None
        self._pa = None
        self._stream = None
        self._channels = 2
        self._rate = 44100

    def _find_loopback_device(self):
        if pyaudio is None:
            raise RuntimeError(
                "Gravação do áudio do sistema exige Windows (pyaudiowpatch/WASAPI). "
                "Use upload, import por URL ou o modo Abas."
            )
        pa = pyaudio.PyAudio()
        try:
            wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_speakers = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            for loopback in pa.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    return loopback, pa
            raise RuntimeError(
                f"Dispositivo loopback não encontrado para '{default_speakers['name']}'"
            )
        except Exception:
            pa.terminate()
            raise

    def start(self) -> dict:
        if self.is_recording:
            return {"success": False, "message": "Já está gravando"}

        try:
            device_info, self._pa = self._find_loopback_device()
            self._channels = device_info.get("maxInputChannels", 2)
            self._rate = int(device_info.get("defaultSampleRate", 44100))

            self._stream = self._pa.open(
                format=FORMAT,
                channels=self._channels,
                rate=self._rate,
                input=True,
                input_device_index=device_info["index"],
                frames_per_buffer=CHUNK,
            )

            self.frames = []
            self.is_recording = True
            self.start_time = time.time()

            self.thread = threading.Thread(target=self._record_loop, daemon=True)
            self.thread.start()

            return {"success": True, "message": f"Gravando áudio de: {device_info['name']}"}
        except Exception as e:
            if self._pa:
                self._pa.terminate()
                self._pa = None
            return {"success": False, "message": f"Erro ao iniciar: {e}"}

    def _record_loop(self):
        while self.is_recording:
            try:
                data = self._stream.read(CHUNK, exception_on_overflow=False)
                self.frames.append(data)
            except Exception:
                self.is_recording = False  # sinaliza queda para o servidor detectar
                break

    def stop(self) -> dict:
        if not self.is_recording:
            return {"success": False, "message": "Não está gravando", "file_path": None}

        self.is_recording = False
        duration = time.time() - self.start_time

        if self.thread:
            self.thread.join(timeout=10)

        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

        if not self.frames:
            if self._pa:
                self._pa.terminate()
                self._pa = None
            return {"success": False, "message": "Nenhum áudio capturado", "file_path": None}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = RECORDINGS_DIR / f"recording_{timestamp}.wav"

        with wave.open(str(file_path), "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(self._pa.get_sample_size(FORMAT))
            wf.setframerate(self._rate)
            wf.writeframes(b"".join(self.frames))

        if self._pa:
            self._pa.terminate()
            self._pa = None

        return {
            "success": True,
            "file_path": str(file_path),
            "duration": round(duration, 1),
            "message": f"Gravação salva ({duration:.1f}s)",
        }

    def get_duration(self) -> float:
        if not self.is_recording or not self.start_time:
            return 0.0
        return time.time() - self.start_time
