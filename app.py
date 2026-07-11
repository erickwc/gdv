"""
Generador de Video (Imagen/Video + Audio)

Arrastra una imagen o un clip de video junto con un audio y genera un
video MP4: la imagen queda como fondo estatico, o el clip se repite en
loop hasta que el beat termina. El audio se re-codifica en alta calidad
(AAC 320 kbps) o se copia bit a bit si ya es AAC.

Soporta plantillas PNG con zona transparente (el medio se coloca debajo,
centrado y recortado para llenar la ventana), y texturas con modos de
fusion (estilo Photoshop) encima del medio.
"""

import ctypes
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading

import customtkinter as ctk
import imageio_ffmpeg
from PIL import Image
from tkinterdnd2 import DND_FILES, TkinterDnD
from tkinter import filedialog, messagebox
from tkinter import font as tkfont

HD_WIDTH = 1280
HD_HEIGHT = 720
MAX_WIDTH = 1920
MAX_HEIGHT = 1080

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".gif"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".opus"}

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Empaquetada (PyInstaller): los recursos de solo lectura (fuentes) viven
# dentro del paquete; las carpetas del usuario (plantillas, texturas, config)
# van junto al ejecutable para que sean faciles de encontrar y editar.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
    RESOURCE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = APP_DIR

FONTS_DIR = os.path.join(RESOURCE_DIR, "fonts")
TEMPLATES_DIR = os.path.join(APP_DIR, "plantillas")
TEXTURES_DIR = os.path.join(APP_DIR, "texturas")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
FR_PRIVATE = 0x10

NO_TEMPLATE = "Sin plantilla"
NO_TEXTURE = "Sin textura"
BROWSE_TEMPLATE = "Buscar archivo..."

# Modos de fusion (nombres de Photoshop -> modo del filtro blend de ffmpeg)
BLEND_MODES = {
    "Normal": "normal",
    "Aclarar": "lighten",
    "Trama": "screen",
    "Multiplicar": "multiply",
    "Superponer": "overlay",
    "Luz suave": "softlight",
}

# Familias resueltas en load_fonts(): light 300 (descriptivos),
# regular 400 y medium 500 (titulos)
FONT_REGULAR = "Segoe UI"
FONT_LIGHT = "Segoe UI"
FONT_MEDIUM = "Segoe UI"


def load_fonts():
    """Carga Inter (Light 300, Regular 400 y Medium 500) como fuentes
    privadas del proceso. Si algo falla, la app sigue con Segoe UI."""
    global FONT_REGULAR, FONT_LIGHT, FONT_MEDIUM
    if os.name != "nt":
        return
    try:
        for filename in ("Inter-Regular.ttf", "Inter-Light.ttf", "Inter-Medium.ttf"):
            path = os.path.join(FONTS_DIR, filename)
            if os.path.exists(path):
                ctypes.windll.gdi32.AddFontResourceExW(path, FR_PRIVATE, 0)
        families = set(tkfont.families())
        if "Inter" in families:
            FONT_REGULAR = "Inter"
            FONT_LIGHT = "Inter"
            FONT_MEDIUM = "Inter"
        if "Inter Light" in families:
            FONT_LIGHT = "Inter Light"
        if "Inter Medium" in families:
            FONT_MEDIUM = "Inter Medium"
    except Exception:
        pass


# Paleta (claro, oscuro)
GREEN = "#149A5B"
GREEN_HOVER = "#0F7A47"
CARD_BG = ("#ffffff", "#1d1d20")
WINDOW_BG = ("#f0f0f0", "#121214")
CHIP_BG = ("#f5f5f6", "#28282c")
TEXT_DARK = ("#1c1c1e", "#f2f2f3")
TEXT_GRAY = ("#9a9aa0", "#8b8b92")
BORDER = ("#e3e3e6", "#3a3a3f")
HOVER_BG = ("#f2f2f3", "#323236")
DROP_BG = ("#fbfbfc", "#242428")
DROP_BG_ACTIVE = ("#f2faf6", "#17251d")
RED = "#c0392b"


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except OSError:
        pass


def compute_target_resolution(width, height, min_w=HD_WIDTH, min_h=HD_HEIGHT, max_w=MAX_WIDTH, max_h=MAX_HEIGHT):
    scale = max(1.0, min_w / width, min_h / height)
    cap = min(max_w / width, max_h / height)
    if cap < scale:
        scale = cap
    new_w = round(width * scale)
    new_h = round(height * scale)
    if new_w % 2:
        new_w += 1
    if new_h % 2:
        new_h += 1
    return new_w, new_h


def detect_template_window(template_path):
    """Encuentra el rectangulo transparente de la plantilla (x, y, w, h).
    Devuelve None si el PNG no tiene canal alfa o no hay zona transparente.

    Cuenta como 'ventana' cualquier pixel con alfa < 255 (aunque sea grano
    casi opaco) y redondea el rectangulo hacia AFUERA: quedarse corto deja
    bordes visibles detras de la plantilla; pasarse nunca se nota porque
    esa zona la tapa la parte opaca."""
    with Image.open(template_path) as img:
        if "A" not in img.getbands():
            return None
        img_w, img_h = img.size
        alpha = img.convert("RGBA").getchannel("A")
        mask = alpha.point(lambda a: 255 if a < 255 else 0)
        bbox = mask.getbbox()
        if not bbox:
            return None
        left, top, right, bottom = bbox
        w = right - left
        h = bottom - top
        if w < 16 or h < 16:
            return None
        # Redondear a dimensiones pares expandiendo (o moviendo el origen si
        # ya no cabe hacia la derecha/abajo)
        if w % 2:
            if left + w + 1 <= img_w:
                w += 1
            else:
                left -= 1
                w += 1
        if h % 2:
            if top + h + 1 <= img_h:
                h += 1
            else:
                top -= 1
                h += 1
        return left, top, w, h


def probe_media(ffmpeg_exe, media_path):
    """Lee la cabecera del archivo y devuelve un dict con duration,
    audio_codec, sample_rate, video_size (w, h)."""
    proc = subprocess.run(
        [ffmpeg_exe, "-i", media_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    info = {"duration": None, "audio_codec": None, "sample_rate": None, "video_size": None}
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stdout)
    if match:
        h, m, s = match.groups()
        info["duration"] = int(h) * 3600 + int(m) * 60 + float(s)
    match = re.search(r"Audio:\s*(\w+).*?(\d+)\s*Hz", proc.stdout)
    if match:
        info["audio_codec"] = match.group(1).lower()
        info["sample_rate"] = int(match.group(2))
    match = re.search(r"Video:.*?\s(\d{2,5})x(\d{2,5})", proc.stdout)
    if match:
        info["video_size"] = (int(match.group(1)), int(match.group(2)))
    return info


def detect_interlaced(ffmpeg_exe, video_path, frames=200):
    """Analiza los primeros fotogramas con idet para saber si el clip viene
    entrelazado (peine en el movimiento, tipico de rips de TV)."""
    proc = subprocess.run(
        [ffmpeg_exe, "-i", video_path, "-vf", "idet", "-frames:v", str(frames),
         "-an", "-f", "null", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    # idet puede imprimir varios reportes (incluye uno vacio del sondeo);
    # el ultimo es el que tiene los conteos reales
    matches = re.findall(
        r"Multi frame detection:\s*TFF:\s*(\d+)\s*BFF:\s*(\d+)\s*Progressive:\s*(\d+)",
        proc.stdout,
    )
    if not matches:
        return False
    tff, bff, progressive = (int(g) for g in matches[-1])
    # Mayoria clara: el contenido con mucho detalle fino puede confundir a
    # idet, asi que solo corregimos cuando el entrelazado es inequivoco
    return (tff + bff) > 3 * max(progressive, 1)


def extract_video_thumb(ffmpeg_exe, video_path, max_px=44):
    """Extrae el primer fotograma del clip para usarlo de miniatura."""
    tmp = os.path.join(tempfile.gettempdir(), "genvideo_thumb.png")
    proc = subprocess.run(
        [ffmpeg_exe, "-y", "-i", video_path, "-frames:v", "1",
         "-vf", f"scale={max_px}:-1", tmp],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode == 0 and os.path.exists(tmp):
        with Image.open(tmp) as img:
            return img.copy()
    return None


def measure_peak_db(ffmpeg_exe, audio_path):
    """Mide el pico maximo del audio en dBFS usando astats (respeta la
    precision de 32-bit float, incluyendo picos por encima de 0 dB)."""
    proc = subprocess.run(
        [ffmpeg_exe, "-i", audio_path, "-af", "astats=measure_perchannel=none", "-f", "null", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    match = re.search(r"Peak level dB:\s*(-?(?:\d+(?:\.\d+)?|inf))", proc.stdout)
    if match and match.group(1) != "-inf":
        return float(match.group(1))
    return None


def parse_out_time(line):
    value = line.split("=", 1)[1].strip()
    match = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", value)
    if not match:
        return None
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


# Estrategias de audio en orden de preferencia. "copy" solo aplica si el
# audio de origen ya es AAC (0% perdida y sin tiempo de codificacion).
# aac_mf usa el codificador acelerado de Windows (~15x mas rapido que el nativo),
# pero solo acepta 44.1/48 kHz, por eso las frecuencias mayores se bajan a 48k.
def audio_strategy_args(strategy, sample_rate):
    if strategy == "copy":
        return ["-c:a", "copy"]
    if sample_rate in (44100, 48000):
        rate = sample_rate
    else:
        rate = 48000
    return ["-c:a", strategy, "-b:a", "320k", "-ar", str(rate)]


def build_layout(media_size, is_video, scale_pct, template_box):
    """Decide el plan de composicion: tamano interno del medio, lienzo final
    y posicion. Devuelve (layout, ancho_final, alto_final)."""
    if template_box:
        x, y, w, h = template_box
        layout = {"mode": "template", "inner": (w, h), "canvas": (MAX_WIDTH, MAX_HEIGHT), "pos": (x, y)}
    elif not is_video and scale_pct >= 100:
        w, h = compute_target_resolution(*media_size)
        layout = {"mode": "plain", "inner": (w, h), "canvas": (w, h), "pos": (0, 0)}
    else:
        inner_w = int(MAX_WIDTH * scale_pct / 100) // 2 * 2
        inner_h = int(MAX_HEIGHT * scale_pct / 100) // 2 * 2
        layout = {"mode": "bordered", "inner": (inner_w, inner_h),
                  "canvas": (MAX_WIDTH, MAX_HEIGHT), "pos": None}
    return layout, layout["canvas"][0], layout["canvas"][1]


def build_filtergraph(layout, is_video=False, speed=1.0, deinterlace=False,
                      tpl_idx=None, tex_idx=None, tex_mode="lighten", tex_opacity=0.5):
    """Arma el filter_complex completo: medio (des-entrelazado + velocidad +
    escala/recorte/bordes) -> textura mezclada encima -> plantilla encima.
    Los clips de video salen a 30 fps constantes para que el loop por
    copia directa sea perfectamente uniforme (sin glitches)."""
    inner_w, inner_h = layout["inner"]
    canvas_w, canvas_h = layout["canvas"]

    chain = "[0:v]"
    if is_video:
        if deinterlace:
            chain += "yadif,"
        if speed != 1.0:
            chain += f"setpts=(PTS-STARTPTS)/{speed:g},"
        else:
            chain += "setpts=PTS-STARTPTS,"
    if layout["mode"] == "plain":
        chain += f"scale={inner_w}:{inner_h}"
    elif layout["mode"] == "bordered":
        chain += (
            f"scale={inner_w}:{inner_h}:force_original_aspect_ratio=decrease,"
            f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    else:  # template: cubrir la ventana por completo, sin dejar bordes
        x, y = layout["pos"]
        chain += (
            f"scale={inner_w}:{inner_h}:force_original_aspect_ratio=increase:force_divisible_by=2,"
            f"crop={inner_w}:{inner_h},pad={canvas_w}:{canvas_h}:{x}:{y}:color=black"
        )
    if is_video:
        chain += ",fps=30"
    parts = [chain + "[base]"]
    last = "base"

    if tex_idx is not None:
        # La mezcla se hace en RGB plano (gbrp), igual que Photoshop
        parts.append(
            f"[{tex_idx}:v]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
            f"crop={canvas_w}:{canvas_h},format=gbrp[texs]"
        )
        parts.append(f"[{last}]format=gbrp[basef]")
        parts.append(
            f"[basef][texs]blend=all_mode={tex_mode}:all_opacity={tex_opacity:.3f}[textured]"
        )
        last = "textured"

    if tpl_idx is not None:
        parts.append(f"[{last}][{tpl_idx}:v]overlay=0:0[tpld]")
        last = "tpld"

    parts.append(f"[{last}]format=yuv420p[vout]")
    return ";".join(parts)


def build_command(ffmpeg_exe, media_path, audio_path, output_path, duration, audio_args,
                  layout, template_path=None, texture=None):
    """Pasada unica para imagenes fijas (el video es barato a 10 fps)."""
    cmd = [ffmpeg_exe, "-y", "-loop", "1", "-framerate", "1", "-i", media_path, "-i", audio_path]
    idx = 2
    tpl_idx = tex_idx = None
    if template_path:
        cmd += ["-i", template_path]
        tpl_idx = idx
        idx += 1
    if texture:
        cmd += ["-i", texture[0]]
        tex_idx = idx
        idx += 1
    fc = build_filtergraph(
        layout, is_video=False, tpl_idx=tpl_idx, tex_idx=tex_idx,
        tex_mode=texture[1] if texture else "lighten",
        tex_opacity=texture[2] if texture else 0.5,
    )
    cmd += [
        "-filter_complex", fc, "-map", "[vout]", "-map", "1:a:0",
        "-r", "10",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-tune", "stillimage",
        *audio_args,
        "-shortest",
    ]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", output_path]
    return cmd


def build_compose_command(ffmpeg_exe, media_path, temp_path, layout, trim=None, speed=1.0,
                          deinterlace=False, template_path=None, texture=None):
    """FASE 1 (solo clips de video): compone UNA sola vuelta del loop —
    recorte + velocidad + textura + escala/bordes o plantilla — en un mp4
    corto sin audio, a 30 fps constantes y con GOP cerrado para que la
    fase 2 pueda repetirlo con copia directa sin glitches."""
    cmd = [ffmpeg_exe, "-y"]
    if trim:
        cmd += ["-ss", f"{trim[0]:.3f}", "-to", f"{trim[1]:.3f}"]
    cmd += ["-i", media_path]
    idx = 1
    tpl_idx = tex_idx = None
    if template_path:
        cmd += ["-i", template_path]
        tpl_idx = idx
        idx += 1
    if texture:
        cmd += ["-i", texture[0]]
        tex_idx = idx
        idx += 1
    fc = build_filtergraph(
        layout, is_video=True, speed=speed, deinterlace=deinterlace,
        tpl_idx=tpl_idx, tex_idx=tex_idx,
        tex_mode=texture[1] if texture else "lighten",
        tex_opacity=texture[2] if texture else 0.5,
    )
    cmd += [
        "-filter_complex", fc, "-map", "[vout]",
        "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-flags", "+cgop",
        "-video_track_timescale", "15360",
        "-progress", "pipe:1", "-nostats",
        temp_path,
    ]
    return cmd


def write_concat_list(unit_path, repeats, list_path):
    """Escribe la lista del demuxer concat que repite la vuelta del loop."""
    escaped = unit_path.replace("\\", "/").replace("'", "'\\''")
    with open(list_path, "w", encoding="utf-8") as fh:
        for _ in range(repeats):
            fh.write(f"file '{escaped}'\n")


def build_mux_command(ffmpeg_exe, list_path, audio_path, output_path, duration, audio_args):
    """FASE 2 (solo clips de video): encadena la vuelta ya compuesta con el
    demuxer concat (timestamps perfectamente continuos, sin glitches en el
    punto de reinicio) usando copia directa del video, y le pone el beat."""
    cmd = [
        ffmpeg_exe, "-y",
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        *audio_args,
        "-shortest",
    ]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", output_path]
    return cmd


def unique_output_path(folder, base):
    """Devuelve una ruta .mp4 que no exista todavia (agrega _2, _3, ...)."""
    candidate = os.path.join(folder, f"{base}.mp4")
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{base}_{counter}.mp4")
        counter += 1
    return candidate


def format_duration(seconds):
    if seconds is None:
        return None
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def truncate_path(path, max_chars=52):
    if len(path) <= max_chars:
        return path
    keep = max_chars - 3
    head = keep // 2
    tail = keep - head
    return path[:head] + "..." + path[-tail:]


def register_drop_recursive(widget, on_drop, on_enter=None, on_leave=None):
    widget.drop_target_register(DND_FILES)
    widget.dnd_bind("<<Drop>>", on_drop)
    if on_enter is not None:
        widget.dnd_bind("<<DropEnter>>", on_enter)
    if on_leave is not None:
        widget.dnd_bind("<<DropLeave>>", on_leave)
    for child in widget.winfo_children():
        register_drop_recursive(child, on_drop, on_enter, on_leave)


class FileChip(ctk.CTkFrame):
    """Fila estilo 'chip' con miniatura opcional, tipo, nombre,
    boton opcional de vista previa y boton X."""

    def __init__(self, master, kind, on_remove, on_preview=None):
        super().__init__(master, fg_color=CHIP_BG, corner_radius=10, height=64)
        self.on_remove = on_remove
        self.grid_propagate(False)
        self.grid_columnconfigure(1, weight=1)

        self.thumb_label = ctk.CTkLabel(self, text="", width=44, height=44)
        self.thumb_label.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=10)

        self.kind_label = ctk.CTkLabel(
            self, text=kind, text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12), anchor="w", height=15,
        )
        self.kind_label.grid(row=0, column=1, sticky="sw", pady=(11, 0))

        self.name_label = ctk.CTkLabel(
            self, text="", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w", height=19,
        )
        self.name_label.grid(row=1, column=1, sticky="nw", pady=(0, 11))

        column = 2
        if on_preview is not None:
            self.preview_btn = ctk.CTkButton(
                self, text="👁", width=28, height=28, corner_radius=14,
                fg_color="transparent", hover_color=HOVER_BG, text_color=TEXT_GRAY,
                font=ctk.CTkFont(FONT_REGULAR, 13), command=on_preview,
            )
            self.preview_btn.grid(row=0, column=column, rowspan=2, padx=(4, 0))
            column += 1

        self.remove_btn = ctk.CTkButton(
            self, text="✕", width=28, height=28, corner_radius=14,
            fg_color="transparent", hover_color=HOVER_BG, text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_REGULAR, 12), command=self._remove,
        )
        self.remove_btn.grid(row=0, column=column, rowspan=2, padx=(4, 10))

    def show(self, name, kind_text=None, thumb=None):
        self.name_label.configure(text=name)
        if kind_text is not None:
            self.kind_label.configure(text=kind_text)
        if thumb is not None:
            self.thumb_label.configure(image=thumb, text="")
        self.grid()

    def set_kind_text(self, text, color=None):
        self.kind_label.configure(text=text, text_color=color or TEXT_GRAY)

    def set_thumb(self, thumb):
        self.thumb_label.configure(image=thumb, text="")

    def _remove(self):
        self.on_remove()


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__(fg_color=WINDOW_BG)
        self.TkdndVersion = TkinterDnD._require(self)
        load_fonts()

        self.config_data = load_config()
        ctk.set_appearance_mode(self.config_data.get("appearance", "light"))

        self.title("Generador de video")
        self.geometry("640x1010")
        self.minsize(600, 900)

        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        os.makedirs(TEXTURES_DIR, exist_ok=True)

        self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        self.media_path = None
        self.media_is_video = False
        self.media_size = None
        self.media_duration = None
        self.media_interlaced = False
        self.trim_range = None
        self.audio_path = None
        self.output_path = None
        self.user_chose_output = False
        self.template_path = None
        self.template_box = None
        self._template_paths = {}
        self.texture_path = None
        self._texture_paths = {}
        self._texture_cache = None
        self._texture_lock = threading.Lock()
        self.process = None
        self.cancel_requested = False
        self._thumb_ref = None
        self.preview_win = None
        self.preview_label = None
        self._preview_ref = None
        self._preview_after_id = None
        self._preview_token = None
        self._preview_counter = 0

        self._build_widgets()
        self._restore_template_from_config()
        self._restore_texture_from_config()

        # Se puede soltar archivos en cualquier parte de la ventana,
        # y la zona se ilumina mientras arrastras algo encima.
        register_drop_recursive(self, self._handle_drop, self._on_drag_enter, self._on_drag_leave)

    # ------------------------------------------------------------------ UI

    def _build_widgets(self):
        card = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=16)
        card.pack(fill="both", expand=True, padx=20, pady=20)
        card.grid_columnconfigure(0, weight=1)
        self.card = card

        row = 0
        header_row = ctk.CTkFrame(card, fg_color="transparent")
        header_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(22, 0))
        header_row.grid_columnconfigure(0, weight=1)
        row += 1

        title = ctk.CTkLabel(
            header_row, text="Crea tu video", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        title.grid(row=0, column=0, sticky="w")

        self.dark_switch = ctk.CTkSwitch(
            header_row, text="Oscuro", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12),
            progress_color=GREEN, width=44,
            command=self._toggle_appearance,
        )
        self.dark_switch.grid(row=0, column=1, sticky="e")
        if self.config_data.get("appearance") == "dark":
            self.dark_switch.select()

        subtitle = ctk.CTkLabel(
            card,
            text="Arrastra una imagen o un clip de video + un audio. MP4 · AAC 320 kbps · HD/Full HD",
            text_color=TEXT_GRAY, font=ctk.CTkFont(FONT_LIGHT, 12), anchor="w",
        )
        subtitle.grid(row=row, column=0, sticky="ew", padx=24, pady=(2, 14))
        row += 1

        # Zona unica de arrastre
        self.drop_zone = ctk.CTkFrame(
            card, fg_color=DROP_BG, corner_radius=12,
            border_width=1, border_color=BORDER, height=130,
        )
        self.drop_zone.grid(row=row, column=0, sticky="ew", padx=24)
        self.drop_zone.grid_propagate(False)
        self.drop_zone.grid_columnconfigure(0, weight=1)
        self.drop_zone.grid_rowconfigure(0, weight=1)
        self.drop_zone.grid_rowconfigure(3, weight=1)
        row += 1

        drop_main = ctk.CTkLabel(
            self.drop_zone, text="Suelta aquí tus dos archivos",
            text_color=TEXT_DARK, font=ctk.CTkFont(FONT_MEDIUM, 14),
        )
        drop_main.grid(row=1, column=0)
        drop_hint = ctk.CTkLabel(
            self.drop_zone, text="o haz clic para buscarlos, o pega una imagen con Ctrl+V",
            text_color=TEXT_GRAY, font=ctk.CTkFont(FONT_LIGHT, 12),
        )
        drop_hint.grid(row=2, column=0)

        for widget in (self.drop_zone, drop_main, drop_hint):
            widget.bind("<Button-1>", self._handle_click)

        # Link de YouTube / Pinterest
        link_row = ctk.CTkFrame(card, fg_color="transparent")
        link_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(10, 0))
        link_row.grid_columnconfigure(0, weight=1)
        row += 1

        self.link_entry = ctk.CTkEntry(
            link_row, height=34, corner_radius=8,
            fg_color=CARD_BG, border_color=BORDER, border_width=1,
            text_color=TEXT_DARK, placeholder_text_color=TEXT_GRAY,
            placeholder_text="o pega un link de YouTube / Pinterest y descárgalo aquí",
            font=ctk.CTkFont(FONT_LIGHT, 12),
        )
        self.link_entry.grid(row=0, column=0, sticky="ew")
        self.link_entry.bind("<Return>", lambda _e: self._download_from_link())

        self.link_btn = ctk.CTkButton(
            link_row, text="Descargar", width=96, height=34, corner_radius=8,
            fg_color=CARD_BG, hover_color=HOVER_BG, text_color=TEXT_DARK,
            border_width=1, border_color=BORDER,
            font=ctk.CTkFont(FONT_MEDIUM, 12), command=self._download_from_link,
        )
        self.link_btn.grid(row=0, column=1, padx=(8, 0))

        # Chips
        self.media_chip = FileChip(card, "Imagen", self._remove_media, on_preview=self._toggle_preview)
        self.media_chip.grid(row=row, column=0, sticky="ew", padx=24, pady=(12, 0))
        self.media_chip.grid_remove()
        row += 1

        self.audio_chip = FileChip(card, "Audio", self._remove_audio)
        self.audio_chip.grid(row=row, column=0, sticky="ew", padx=24, pady=(10, 0))
        self.audio_chip.grid_remove()
        row += 1

        # Plantilla
        template_row = ctk.CTkFrame(card, fg_color="transparent")
        template_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        template_row.grid_columnconfigure(0, weight=1)
        row += 1

        template_title = ctk.CTkLabel(
            template_row, text="Plantilla", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        template_title.grid(row=0, column=0, columnspan=2, sticky="w")

        self.template_menu = ctk.CTkOptionMenu(
            template_row, values=[NO_TEMPLATE], height=34, corner_radius=8,
            fg_color=CHIP_BG, button_color=CHIP_BG, button_hover_color=HOVER_BG,
            text_color=TEXT_DARK, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT_DARK, dropdown_hover_color=HOVER_BG,
            font=ctk.CTkFont(FONT_REGULAR, 12),
            dropdown_font=ctk.CTkFont(FONT_REGULAR, 12),
            command=self._on_template_selected,
        )
        self.template_menu.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.template_delete_btn = ctk.CTkButton(
            template_row, text="🗑", width=34, height=34, corner_radius=8,
            fg_color=CHIP_BG, hover_color=HOVER_BG, text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_REGULAR, 13), command=self._delete_template,
            state="disabled",
        )
        self.template_delete_btn.grid(row=1, column=1, padx=(8, 0), pady=(6, 0))

        self.template_info = ctk.CTkLabel(
            template_row, text="", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12), anchor="w",
        )
        self.template_info.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Textura (ruido/grano encima del medio, estilo Photoshop)
        texture_row = ctk.CTkFrame(card, fg_color="transparent")
        texture_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        texture_row.grid_columnconfigure(0, weight=1)
        row += 1

        texture_title = ctk.CTkLabel(
            texture_row, text="Textura", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        texture_title.grid(row=0, column=0, columnspan=2, sticky="w")

        self.texture_menu = ctk.CTkOptionMenu(
            texture_row, values=[NO_TEXTURE], height=34, corner_radius=8,
            fg_color=CHIP_BG, button_color=CHIP_BG, button_hover_color=HOVER_BG,
            text_color=TEXT_DARK, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT_DARK, dropdown_hover_color=HOVER_BG,
            font=ctk.CTkFont(FONT_REGULAR, 12),
            dropdown_font=ctk.CTkFont(FONT_REGULAR, 12),
            command=self._on_texture_selected,
        )
        self.texture_menu.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.texture_delete_btn = ctk.CTkButton(
            texture_row, text="🗑", width=34, height=34, corner_radius=8,
            fg_color=CHIP_BG, hover_color=HOVER_BG, text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_REGULAR, 13), command=self._delete_texture,
            state="disabled",
        )
        self.texture_delete_btn.grid(row=1, column=1, padx=(8, 0), pady=(6, 0))

        # Opciones de la textura: modo de fusion + opacidad
        self.texture_opts_row = ctk.CTkFrame(texture_row, fg_color="transparent")
        self.texture_opts_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.texture_opts_row.grid_columnconfigure(1, weight=1)
        self.texture_opts_row.grid_remove()

        self.blend_menu = ctk.CTkOptionMenu(
            self.texture_opts_row, values=list(BLEND_MODES.keys()),
            width=130, height=30, corner_radius=8,
            fg_color=CHIP_BG, button_color=CHIP_BG, button_hover_color=HOVER_BG,
            text_color=TEXT_DARK, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT_DARK, dropdown_hover_color=HOVER_BG,
            font=ctk.CTkFont(FONT_REGULAR, 12),
            dropdown_font=ctk.CTkFont(FONT_REGULAR, 12),
            command=self._on_blend_change,
        )
        self.blend_menu.set("Aclarar")
        self.blend_menu.grid(row=0, column=0)

        self.opacity_slider = ctk.CTkSlider(
            self.texture_opts_row, from_=0, to=100, number_of_steps=100,
            progress_color=GREEN, button_color=GREEN, button_hover_color=GREEN_HOVER,
            command=self._on_opacity_change,
        )
        self.opacity_slider.set(47)
        self.opacity_slider.grid(row=0, column=1, sticky="ew", padx=(12, 8))

        self.opacity_label = ctk.CTkLabel(
            self.texture_opts_row, text="Opacidad 47%", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12), width=96, anchor="e",
        )
        self.opacity_label.grid(row=0, column=2)

        scale_tex_label = ctk.CTkLabel(
            self.texture_opts_row, text="Escala", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12), width=130, anchor="w",
        )
        scale_tex_label.grid(row=1, column=0, sticky="w", pady=(8, 0))

        self.texture_scale_slider = ctk.CTkSlider(
            self.texture_opts_row, from_=10, to=200, number_of_steps=38,
            progress_color=GREEN, button_color=GREEN, button_hover_color=GREEN_HOVER,
            command=self._on_texture_scale_change,
        )
        self.texture_scale_slider.set(100)
        self.texture_scale_slider.grid(row=1, column=1, sticky="ew", padx=(12, 8), pady=(8, 0))

        self.texture_scale_label = ctk.CTkLabel(
            self.texture_opts_row, text="Tamaño 100%", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12), width=96, anchor="e",
        )
        self.texture_scale_label.grid(row=1, column=2, pady=(8, 0))

        # Recorte del loop (solo para clips de video)
        self.trim_row = ctk.CTkFrame(card, fg_color="transparent")
        self.trim_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        self.trim_row.grid_columnconfigure(4, weight=1)
        self.trim_row.grid_remove()
        row += 1

        trim_title = ctk.CTkLabel(
            self.trim_row, text="Recorte del loop", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        trim_title.grid(row=0, column=0, columnspan=5, sticky="w")

        trim_from_label = ctk.CTkLabel(
            self.trim_row, text="Desde", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12),
        )
        trim_from_label.grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.trim_start_entry = ctk.CTkEntry(
            self.trim_row, width=76, height=30, corner_radius=8,
            fg_color=CARD_BG, border_color=BORDER, border_width=1,
            text_color=TEXT_DARK, font=ctk.CTkFont(FONT_REGULAR, 12),
            justify="center",
        )
        self.trim_start_entry.grid(row=1, column=1, padx=(8, 16), pady=(6, 0))
        self.trim_start_entry.bind("<KeyRelease>", lambda _e: self._schedule_preview(600))

        trim_to_label = ctk.CTkLabel(
            self.trim_row, text="Hasta", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12),
        )
        trim_to_label.grid(row=1, column=2, sticky="w", pady=(6, 0))

        self.trim_end_entry = ctk.CTkEntry(
            self.trim_row, width=76, height=30, corner_radius=8,
            fg_color=CARD_BG, border_color=BORDER, border_width=1,
            text_color=TEXT_DARK, font=ctk.CTkFont(FONT_REGULAR, 12),
            justify="center",
        )
        self.trim_end_entry.grid(row=1, column=3, padx=(8, 0), pady=(6, 0))

        trim_hint = ctk.CTkLabel(
            self.trim_row, text="ej. 1:10 o 1.10 — solo ese pedazo se repite en loop",
            text_color=TEXT_GRAY, font=ctk.CTkFont(FONT_LIGHT, 12), anchor="e",
        )
        trim_hint.grid(row=1, column=4, sticky="e", pady=(6, 0))

        # Velocidad del loop (solo para clips de video)
        self.speed_row = ctk.CTkFrame(card, fg_color="transparent")
        self.speed_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        self.speed_row.grid_columnconfigure(1, weight=1)
        self.speed_row.grid_remove()
        row += 1

        speed_title = ctk.CTkLabel(
            self.speed_row, text="Velocidad", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        speed_title.grid(row=0, column=0, sticky="w")

        self.speed_control = ctk.CTkSegmentedButton(
            self.speed_row, values=["0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x"],
            height=30, corner_radius=8,
            fg_color=CHIP_BG, unselected_color=CHIP_BG, unselected_hover_color=HOVER_BG,
            selected_color=GREEN, selected_hover_color=GREEN_HOVER,
            text_color=TEXT_DARK, font=ctk.CTkFont(FONT_REGULAR, 12),
        )
        self.speed_control.set("1x")
        self.speed_control.grid(row=0, column=1, sticky="e")

        # Tamano en pantalla
        self.scale_row = ctk.CTkFrame(card, fg_color="transparent")
        self.scale_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        self.scale_row.grid_columnconfigure(1, weight=1)
        self.scale_row.grid_remove()
        row += 1

        scale_title = ctk.CTkLabel(
            self.scale_row, text="Tamaño en pantalla", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        scale_title.grid(row=0, column=0, sticky="w")

        self.scale_value_label = ctk.CTkLabel(
            self.scale_row, text="100% · pantalla completa", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12), anchor="e",
        )
        self.scale_value_label.grid(row=0, column=1, sticky="e")

        self.scale_slider = ctk.CTkSlider(
            self.scale_row, from_=40, to=100, number_of_steps=12,
            progress_color=GREEN, button_color=GREEN, button_hover_color=GREEN_HOVER,
            command=self._on_scale_change,
        )
        self.scale_slider.set(100)
        self.scale_slider.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        # Guardar como
        save_row = ctk.CTkFrame(card, fg_color="transparent")
        save_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        save_row.grid_columnconfigure(0, weight=1)
        row += 1

        save_label = ctk.CTkLabel(
            save_row, text="Guardar Como", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w", height=17,
        )
        save_label.grid(row=0, column=0, sticky="ew")

        self.output_label = ctk.CTkLabel(
            save_row, text="(se definirá al elegir el audio)",
            text_color=TEXT_GRAY, font=ctk.CTkFont(FONT_LIGHT, 12), anchor="w", height=18,
        )
        self.output_label.grid(row=1, column=0, sticky="ew")

        self.browse_btn = ctk.CTkButton(
            save_row, text="Cambiar", width=88, height=34, corner_radius=8,
            fg_color=CARD_BG, hover_color=HOVER_BG, text_color=TEXT_DARK,
            border_width=1, border_color=BORDER,
            font=ctk.CTkFont(FONT_MEDIUM, 12), command=self._choose_output,
        )
        self.browse_btn.grid(row=0, column=1, rowspan=2, padx=(12, 0))

        # Boton principal
        self.generate_btn = ctk.CTkButton(
            card, text="Generar video", height=44, corner_radius=10,
            fg_color=GREEN, hover_color=GREEN_HOVER, text_color="#ffffff",
            font=ctk.CTkFont(FONT_MEDIUM, 14),
            state="disabled", command=self._start_generation,
        )
        self.generate_btn.grid(row=row, column=0, sticky="ew", padx=24, pady=(16, 0))
        row += 1

        # Progreso + estado
        self.progress = ctk.CTkProgressBar(card, height=8, corner_radius=4, progress_color=GREEN)
        self.progress.set(0)
        self.progress.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        self.progress.grid_remove()
        row += 1

        status_row = ctk.CTkFrame(card, fg_color="transparent")
        status_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(8, 18))
        status_row.grid_columnconfigure(0, weight=1)
        row += 1

        self.status_label = ctk.CTkLabel(
            status_row, text="Esperando imagen/clip y audio...",
            text_color=TEXT_GRAY, font=ctk.CTkFont(FONT_LIGHT, 12), anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew")

        self.cancel_btn = ctk.CTkButton(
            status_row, text="Cancelar", width=88, height=30, corner_radius=8,
            fg_color=CARD_BG, hover_color=HOVER_BG, text_color=TEXT_DARK,
            border_width=1, border_color=BORDER,
            font=ctk.CTkFont(FONT_MEDIUM, 12), command=self._cancel,
        )
        self.cancel_btn.grid(row=0, column=1, padx=(12, 0))
        self.cancel_btn.grid_remove()

        self.open_video_btn = ctk.CTkButton(
            status_row, text="Ver video", width=92, height=30, corner_radius=8,
            fg_color=CARD_BG, hover_color=HOVER_BG, text_color=GREEN,
            border_width=1, border_color=BORDER,
            font=ctk.CTkFont(FONT_MEDIUM, 12), command=self._open_video,
        )
        self.open_video_btn.grid(row=0, column=1, padx=(12, 0))
        self.open_video_btn.grid_remove()

        self.open_folder_btn = ctk.CTkButton(
            status_row, text="Abrir carpeta", width=104, height=30, corner_radius=8,
            fg_color=CARD_BG, hover_color=HOVER_BG, text_color=GREEN,
            border_width=1, border_color=BORDER,
            font=ctk.CTkFont(FONT_MEDIUM, 12), command=self._open_folder,
        )
        self.open_folder_btn.grid(row=0, column=2, padx=(8, 0))
        self.open_folder_btn.grid_remove()

        self._refresh_template_menu()
        self._refresh_texture_menu()

        # Pegar imagen con Ctrl+V (por ejemplo copiada de Pinterest)
        self.bind_all("<Control-v>", self._paste_from_clipboard)
        self.bind_all("<Control-V>", self._paste_from_clipboard)

    # ---------------------------------------------------------- apariencia

    def _toggle_appearance(self):
        mode = "dark" if self.dark_switch.get() else "light"
        ctk.set_appearance_mode(mode)
        self.config_data["appearance"] = mode
        save_config(self.config_data)

    # ----------------------------------------------------------- plantilla
    def _refresh_template_menu(self):
        self._template_paths = {}
        values = [NO_TEMPLATE]
        if os.path.isdir(TEMPLATES_DIR):
            for name in sorted(os.listdir(TEMPLATES_DIR)):
                if name.lower().endswith(".png"):
                    display = os.path.splitext(name)[0]
                    self._template_paths[display] = os.path.join(TEMPLATES_DIR, name)
                    values.append(display)
        values.append(BROWSE_TEMPLATE)
        self.template_menu.configure(values=values)

    def _restore_template_from_config(self):
        saved = self.config_data.get("template")
        if not saved or not os.path.exists(saved):
            return
        display = os.path.splitext(os.path.basename(saved))[0]
        if display not in self._template_paths:
            self._template_paths[display] = saved
            values = list(self.template_menu.cget("values"))
            values.insert(-1, display)
            self.template_menu.configure(values=values)
        self.template_menu.set(display)
        self._activate_template(saved)

    def _on_template_selected(self, choice):
        if choice == NO_TEMPLATE:
            self._deactivate_template()
            return
        if choice == BROWSE_TEMPLATE:
            path = filedialog.askopenfilename(
                title="Seleccionar plantilla PNG",
                filetypes=[("Plantilla PNG", "*.png")],
            )
            if not path:
                self.template_menu.set(NO_TEMPLATE if not self.template_path
                                       else os.path.splitext(os.path.basename(self.template_path))[0])
                return
            display = os.path.splitext(os.path.basename(path))[0]
            self._template_paths[display] = path
            values = list(self.template_menu.cget("values"))
            if display not in values:
                values.insert(-1, display)
                self.template_menu.configure(values=values)
            self.template_menu.set(display)
            self._activate_template(path)
            return
        path = self._template_paths.get(choice)
        if path:
            self._activate_template(path)

    def _activate_template(self, path):
        try:
            box = detect_template_window(path)
        except Exception as exc:
            self.template_menu.set(NO_TEMPLATE)
            self._deactivate_template()
            self.template_info.configure(text=f"No se pudo leer la plantilla: {exc}", text_color=RED)
            return
        if box is None:
            self.template_menu.set(NO_TEMPLATE)
            self._deactivate_template()
            self.template_info.configure(
                text="Esa plantilla no tiene zona transparente — el medio no se vería. Usa un PNG con transparencia.",
                text_color=RED,
            )
            return
        self.template_path = path
        self.template_box = box
        x, y, w, h = box
        self.template_info.configure(
            text=f"Ventana transparente detectada: {w}x{h} en ({x}, {y}) · salida 1920x1080",
            text_color=TEXT_GRAY,
        )
        self.template_delete_btn.configure(state="normal")
        self.config_data["template"] = path
        save_config(self.config_data)
        self._refresh_ready_state()
        self._schedule_preview()

    def _deactivate_template(self):
        self.template_path = None
        self.template_box = None
        self.template_info.configure(text="", text_color=TEXT_GRAY)
        self.template_delete_btn.configure(state="disabled")
        self.config_data["template"] = None
        save_config(self.config_data)
        self._refresh_ready_state()
        self._schedule_preview()

    def _delete_template(self):
        if not self.template_path:
            return
        name = os.path.basename(self.template_path)
        if not messagebox.askyesno(
            "Eliminar plantilla",
            f"¿Eliminar la plantilla \"{name}\"?"
            + ("\n\nEl archivo se borrará de la carpeta plantillas."
               if os.path.dirname(self.template_path) == TEMPLATES_DIR else
               "\n\nSolo se quitará de la lista (el archivo original no se toca)."),
        ):
            return
        path = self.template_path
        if os.path.dirname(path) == TEMPLATES_DIR:
            try:
                os.remove(path)
            except OSError as exc:
                self._set_status(f"No se pudo eliminar la plantilla: {exc}", RED)
                return
        self._deactivate_template()
        self._refresh_template_menu()
        self.template_menu.set(NO_TEMPLATE)
        self._set_status(f"Plantilla eliminada: {name}")

    # ------------------------------------------------------- vista previa

    def _toggle_preview(self):
        if self.preview_win is not None and self.preview_win.winfo_exists() \
                and self.preview_win.state() == "normal":
            self.preview_win.withdraw()
            return
        self._ensure_preview_window()
        self._schedule_preview(1)

    def _ensure_preview_window(self):
        if self.preview_win is None or not self.preview_win.winfo_exists():
            self.preview_win = ctk.CTkToplevel(self, fg_color=WINDOW_BG)
            self.preview_win.title("Vista previa")
            x = self.winfo_rootx() + self.winfo_width() + 8
            y = self.winfo_rooty()
            self.preview_win.geometry(f"1024x612+{x}+{y}")
            self.preview_win.resizable(False, False)
            self.preview_label = ctk.CTkLabel(
                self.preview_win, text="Carga una imagen o un clip para previsualizar",
                text_color=TEXT_GRAY, font=ctk.CTkFont(FONT_LIGHT, 12),
            )
            self.preview_label.pack(expand=True, fill="both", padx=16, pady=16)
            self.preview_win.protocol("WM_DELETE_WINDOW", self.preview_win.withdraw)
        self.preview_win.deiconify()
        self.preview_win.lift()

    def _preview_visible(self):
        try:
            return (self.preview_win is not None and self.preview_win.winfo_exists()
                    and self.preview_win.state() == "normal")
        except Exception:
            return False

    def _schedule_preview(self, delay=300):
        """Reagenda el render de la vista previa (con debounce para el slider)."""
        if not self._preview_visible():
            return
        if self._preview_after_id:
            self.after_cancel(self._preview_after_id)
        self._preview_after_id = self.after(delay, self._render_preview)

    def _render_preview(self):
        self._preview_after_id = None
        if not self._preview_visible():
            return
        if not self.media_path:
            self.preview_label.configure(image=None, text="Carga una imagen o un clip para previsualizar")
            self._preview_ref = None
            return
        if self.media_is_video and not self.media_size:
            self._schedule_preview(400)  # el analisis del clip sigue corriendo
            return

        # Mismo plan de composicion que la exportacion real
        template_box = self.template_box if self.template_path else None
        layout, _, _ = build_layout(
            self.media_size, self.media_is_video, self._current_scale_pct(), template_box,
        )
        texture = None
        if self.texture_path:
            try:
                prepared = self._prepare_texture(*layout["canvas"])
            except Exception:
                prepared = self.texture_path
            texture = (prepared,
                       BLEND_MODES.get(self.blend_menu.get(), "lighten"),
                       self.opacity_slider.get() / 100.0)

        cmd = [self.ffmpeg_exe, "-y"]
        if self.media_is_video:
            start = parse_time_input(self.trim_start_entry.get()) or 0
            if self.media_duration:
                start = min(start, max(0.0, self.media_duration - 0.5))
            if start > 0:
                cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-i", self.media_path]
        idx = 1
        tpl_idx = tex_idx = None
        if self.template_path:
            cmd += ["-i", self.template_path]
            tpl_idx = idx
            idx += 1
        if texture:
            cmd += ["-i", texture[0]]
            tex_idx = idx
            idx += 1
        fc = build_filtergraph(
            layout, is_video=False, tpl_idx=tpl_idx, tex_idx=tex_idx,
            tex_mode=texture[1] if texture else "lighten",
            tex_opacity=texture[2] if texture else 0.5,
        )
        self._preview_counter += 1
        png = os.path.join(tempfile.gettempdir(), f"genvideo_preview_{self._preview_counter}.png")
        cmd += ["-filter_complex", fc, "-map", "[vout]", "-frames:v", "1", png]

        if self._preview_ref is None:
            self.preview_label.configure(text="Generando vista previa...")
        token = object()
        self._preview_token = token
        threading.Thread(target=self._preview_job, args=(token, cmd, png), daemon=True).start()

    def _preview_job(self, token, cmd, png):
        proc = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)
        image = None
        if proc.returncode == 0 and os.path.exists(png):
            try:
                with Image.open(png) as img:
                    image = img.copy()
                os.remove(png)
            except Exception:
                image = None

        def apply():
            if token is not self._preview_token or not self._preview_visible():
                return
            if image is None:
                self.preview_label.configure(image=None, text="No se pudo generar la vista previa")
                self._preview_ref = None
                return
            image.thumbnail((992, 580))
            self._preview_ref = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
            self.preview_label.configure(image=self._preview_ref, text="")
        self.after(0, apply)

    # ------------------------------------------------------------- textura

    def _refresh_texture_menu(self):
        self._texture_paths = {}
        values = [NO_TEXTURE]
        if os.path.isdir(TEXTURES_DIR):
            for name in sorted(os.listdir(TEXTURES_DIR)):
                if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                    display = os.path.splitext(name)[0]
                    self._texture_paths[display] = os.path.join(TEXTURES_DIR, name)
                    values.append(display)
        values.append(BROWSE_TEMPLATE)
        self.texture_menu.configure(values=values)

    def _restore_texture_from_config(self):
        self.blend_menu.set(self.config_data.get("blend_mode", "Aclarar"))
        opacity = self.config_data.get("texture_opacity", 47)
        self.opacity_slider.set(opacity)
        self._on_opacity_change(opacity, save=False)
        tex_scale = self.config_data.get("texture_scale", 100)
        self.texture_scale_slider.set(tex_scale)
        self.texture_scale_label.configure(text=f"Tamaño {int(tex_scale)}%")
        saved = self.config_data.get("texture")
        if not saved or not os.path.exists(saved):
            return
        display = os.path.splitext(os.path.basename(saved))[0]
        if display not in self._texture_paths:
            self._texture_paths[display] = saved
            values = list(self.texture_menu.cget("values"))
            values.insert(-1, display)
            self.texture_menu.configure(values=values)
        self.texture_menu.set(display)
        self._activate_texture(saved)

    def _on_texture_selected(self, choice):
        if choice == NO_TEXTURE:
            self._deactivate_texture()
            return
        if choice == BROWSE_TEMPLATE:
            path = filedialog.askopenfilename(
                title="Seleccionar textura",
                filetypes=[("Imagen", " ".join(f"*{e}" for e in sorted(IMAGE_EXTS)))],
            )
            if not path:
                self.texture_menu.set(NO_TEXTURE if not self.texture_path
                                      else os.path.splitext(os.path.basename(self.texture_path))[0])
                return
            display = os.path.splitext(os.path.basename(path))[0]
            self._texture_paths[display] = path
            values = list(self.texture_menu.cget("values"))
            if display not in values:
                values.insert(-1, display)
                self.texture_menu.configure(values=values)
            self.texture_menu.set(display)
            self._activate_texture(path)
            return
        path = self._texture_paths.get(choice)
        if path:
            self._activate_texture(path)

    def _activate_texture(self, path):
        try:
            with Image.open(path):
                pass
        except Exception as exc:
            self.texture_menu.set(NO_TEXTURE)
            self._deactivate_texture()
            self._set_status(f"No se pudo leer la textura: {exc}", RED)
            return
        self.texture_path = path
        self.texture_opts_row.grid()
        self.texture_delete_btn.configure(state="normal")
        self.config_data["texture"] = path
        save_config(self.config_data)
        self._schedule_preview()

    def _deactivate_texture(self):
        self.texture_path = None
        self.texture_opts_row.grid_remove()
        self.texture_delete_btn.configure(state="disabled")
        self.config_data["texture"] = None
        save_config(self.config_data)
        self._schedule_preview()

    def _delete_texture(self):
        if not self.texture_path:
            return
        name = os.path.basename(self.texture_path)
        if not messagebox.askyesno(
            "Eliminar textura",
            f"¿Eliminar la textura \"{name}\"?"
            + ("\n\nEl archivo se borrará de la carpeta texturas."
               if os.path.dirname(self.texture_path) == TEXTURES_DIR else
               "\n\nSolo se quitará de la lista (el archivo original no se toca)."),
        ):
            return
        path = self.texture_path
        if os.path.dirname(path) == TEXTURES_DIR:
            try:
                os.remove(path)
            except OSError as exc:
                self._set_status(f"No se pudo eliminar la textura: {exc}", RED)
                return
        self._deactivate_texture()
        self._refresh_texture_menu()
        self.texture_menu.set(NO_TEXTURE)
        self._set_status(f"Textura eliminada: {name}")

    def _prepare_texture(self, canvas_w, canvas_h):
        """Genera (con cache) la textura tileada al tamano del lienzo: se
        escala a su tamano natural x el porcentaje elegido y se repite en
        mosaico, en vez de estirarla — asi el grano queda fino."""
        scale = int(round(self.texture_scale_slider.get()))
        key = (self.texture_path, scale, canvas_w, canvas_h)
        with self._texture_lock:
            if self._texture_cache and self._texture_cache[0] == key \
                    and os.path.exists(self._texture_cache[1]):
                return self._texture_cache[1]
            path = os.path.join(tempfile.gettempdir(), "genvideo_textura_preparada.png")
            with Image.open(self.texture_path) as tex:
                tex = tex.convert("RGB")
                tile_w = max(2, round(tex.width * scale / 100))
                tile_h = max(2, round(tex.height * scale / 100))
                tile = tex.resize((tile_w, tile_h), Image.LANCZOS)
            board = Image.new("RGB", (canvas_w, canvas_h))
            for y in range(0, canvas_h, tile_h):
                for x in range(0, canvas_w, tile_w):
                    board.paste(tile, (x, y))
            board.save(path)
            self._texture_cache = (key, path)
            return path

    def _on_texture_scale_change(self, value):
        pct = int(round(float(value)))
        self.texture_scale_label.configure(text=f"Tamaño {pct}%")
        self.config_data["texture_scale"] = pct
        save_config(self.config_data)
        self._schedule_preview()

    def _on_blend_change(self, choice):
        self.config_data["blend_mode"] = choice
        save_config(self.config_data)
        self._schedule_preview()

    def _on_opacity_change(self, value, save=True):
        pct = int(round(float(value)))
        self.opacity_label.configure(text=f"Opacidad {pct}%")
        if save:
            self.config_data["texture_opacity"] = pct
            save_config(self.config_data)
            self._schedule_preview()

    # ------------------------------------------------------ descarga por link

    def _download_from_link(self):
        url = self.link_entry.get().strip()
        if not re.match(r"https?://", url):
            self._set_status("Pega un link válido (que empiece con https://).", RED)
            return
        self._start_download(url)

    def _start_download(self, url):
        self.link_btn.configure(state="disabled", text="Bajando...")
        self._set_status("Obteniendo información del video...")
        threading.Thread(target=self._download_job, args=(url,), daemon=True).start()

    def _download_job(self, url):
        try:
            from yt_dlp import YoutubeDL

            # Limpiar descargas anteriores
            tmpdir = tempfile.gettempdir()
            for name in os.listdir(tmpdir):
                if name.startswith("genvideo_descarga."):
                    try:
                        os.remove(os.path.join(tmpdir, name))
                    except OSError:
                        pass

            def hook(d):
                if d.get("status") == "downloading":
                    pct = (d.get("_percent_str") or "").strip()
                    self.after(0, lambda p=pct: self._set_status(f"Descargando video... {p}"))
                elif d.get("status") == "finished":
                    self.after(0, lambda: self._set_status("Procesando la descarga..."))

            opts = {
                # Sin audio cuando se puede (el beat lo pone la app) y max 1080p
                "format": "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]/b",
                "outtmpl": os.path.join(tmpdir, "genvideo_descarga.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "progress_hooks": [hook],
                "ffmpeg_location": self.ffmpeg_exe,
                "merge_output_format": "mp4",
            }
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info.get("entries"):
                    info = info["entries"][0]
                path = None
                requested = info.get("requested_downloads") or []
                if requested:
                    path = requested[0].get("filepath")
                if not path:
                    path = ydl.prepare_filename(info)
            title = info.get("title") or "Video descargado"

            def apply():
                self.link_btn.configure(state="normal", text="Descargar")
                self.link_entry.delete(0, "end")
                self._set_video(path)
                self.media_chip.name_label.configure(text=title)
                self._set_status("Video descargado ✔ listo para el loop")
            self.after(0, apply)
        except Exception as exc:
            message = str(exc)
            if len(message) > 140:
                message = message[:140] + "..."
            def fail():
                self.link_btn.configure(state="normal", text="Descargar")
                self._set_status(f"No se pudo descargar: {message}", RED)
            self.after(0, fail)

    # -------------------------------------------------- pegar del portapapeles

    def _paste_from_clipboard(self, _event=None):
        try:
            from PIL import ImageGrab
            data = ImageGrab.grabclipboard()
        except Exception as exc:
            self._set_status(f"No se pudo leer el portapapeles: {exc}", RED)
            return
        if data is None:
            # ¿Hay un link en el portapapeles? -> descargarlo
            try:
                text = self.clipboard_get().strip()
            except Exception:
                text = ""
            if re.match(r"https?://", text):
                self.link_entry.delete(0, "end")
                self.link_entry.insert(0, text)
                self._start_download(text)
                return
            self._set_status("El portapapeles no tiene una imagen, archivos ni un link.", RED)
            return
        if isinstance(data, list):
            # Archivos copiados en el Explorador
            self._ingest_files([p for p in data if isinstance(p, str)])
            return
        # Imagen copiada (por ejemplo desde Pinterest o un navegador)
        path = os.path.join(tempfile.gettempdir(), "genvideo_imagen_pegada.png")
        try:
            data.convert("RGB").save(path, "PNG")
        except Exception as exc:
            self._set_status(f"No se pudo guardar la imagen pegada: {exc}", RED)
            return
        self._set_image(path)
        self.media_chip.show(
            "Imagen pegada del portapapeles",
            kind_text=f"Imagen · {data.width}x{data.height}",
        )
        self._set_status("Imagen pegada del portapapeles ✔")

    # ------------------------------------------------------- carga de archivos

    def _on_drag_enter(self, _event):
        self.drop_zone.configure(border_color=GREEN, border_width=2, fg_color=DROP_BG_ACTIVE)

    def _on_drag_leave(self, _event):
        self.drop_zone.configure(border_color=BORDER, border_width=1, fg_color=DROP_BG)

    def _handle_drop(self, event):
        self._on_drag_leave(event)
        files = self.tk.splitlist(event.data)
        self._ingest_files(files)

    def _handle_click(self, _event):
        all_media = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS
        paths = filedialog.askopenfilenames(
            title="Seleccionar imagen/clip y/o audio",
            filetypes=[
                ("Imagen, video o audio", " ".join(f"*{e}" for e in sorted(all_media))),
                ("Todos los archivos", "*.*"),
            ],
        )
        if paths:
            self._ingest_files(paths)

    def _ingest_files(self, paths):
        ignored = []
        for path in paths:
            ext = os.path.splitext(path)[1].lower()
            if ext in IMAGE_EXTS:
                self._set_image(path)
            elif ext in VIDEO_EXTS:
                self._set_video(path)
            elif ext in AUDIO_EXTS:
                self._set_audio(path)
            else:
                ignored.append(os.path.basename(path))
        if ignored:
            self._set_status(f"Ignorado (no es imagen, video ni audio): {', '.join(ignored)}", RED)

    def _set_image(self, path):
        try:
            with Image.open(path) as img:
                width, height = img.size
                thumb_src = img.copy()
        except Exception as exc:
            self._set_status(f"No se pudo leer la imagen: {exc}", RED)
            return

        thumb_src.thumbnail((44, 44))
        self._thumb_ref = ctk.CTkImage(light_image=thumb_src, size=thumb_src.size)

        self.media_path = path
        self.media_is_video = False
        self.media_size = (width, height)
        self.media_chip.show(
            os.path.basename(path),
            kind_text=f"Imagen · {width}x{height}",
            thumb=self._thumb_ref,
        )
        self._update_default_output()
        self._refresh_ready_state()
        self._set_status(f"Imagen lista: {os.path.basename(path)}")
        self._schedule_preview()

    def _set_video(self, path):
        self.media_path = path
        self.media_is_video = True
        self.media_size = None
        self.media_duration = None
        self.media_interlaced = False
        self.media_chip.show(os.path.basename(path), kind_text="Video · analizando...")
        self._update_default_output()
        self._refresh_ready_state()
        self._set_status(f"Clip listo: {os.path.basename(path)} (se repetirá en loop)")
        threading.Thread(target=self._probe_video_job, args=(path,), daemon=True).start()

    def _probe_video_job(self, path):
        info = probe_media(self.ffmpeg_exe, path)
        thumb_img = extract_video_thumb(self.ffmpeg_exe, path)
        interlaced = detect_interlaced(self.ffmpeg_exe, path)
        if path != self.media_path or not self.media_is_video:
            return  # el usuario ya cambio de medio
        self.media_size = info["video_size"]
        self.media_interlaced = interlaced

        parts = ["Video"]
        formatted = format_duration(info["duration"])
        if formatted:
            parts.append(formatted)
        if info["video_size"]:
            parts.append(f"{info['video_size'][0]}x{info['video_size'][1]}")
        if interlaced:
            parts.append("entrelazado (se corregirá)")
        parts.append("loop hasta el final del beat")
        text = " · ".join(parts)

        duration = info["duration"]

        def apply():
            self.media_chip.set_kind_text(text)
            if thumb_img is not None:
                self._thumb_ref = ctk.CTkImage(light_image=thumb_img, size=thumb_img.size)
                self.media_chip.set_thumb(self._thumb_ref)
            self.media_duration = duration
            self.trim_start_entry.delete(0, "end")
            self.trim_start_entry.insert(0, "0:00")
            self.trim_end_entry.delete(0, "end")
            if duration:
                self.trim_end_entry.insert(0, format_duration(duration))
            self._schedule_preview()
        self.after(0, apply)

    def _set_audio(self, path):
        self.audio_path = path
        self.audio_chip.show(os.path.basename(path), kind_text="Audio · analizando...")
        self._update_default_output()
        self._refresh_ready_state()
        self._set_status(f"Audio listo: {os.path.basename(path)}")
        threading.Thread(target=self._measure_peak_job, args=(path,), daemon=True).start()

    def _measure_peak_job(self, path):
        info = probe_media(self.ffmpeg_exe, path)
        peak = measure_peak_db(self.ffmpeg_exe, path)
        if path != self.audio_path:
            return  # el usuario ya cambio de audio
        prefix = "Audio"
        formatted = format_duration(info["duration"])
        if formatted:
            prefix = f"Audio · {formatted}"
        if peak is None:
            self.after(0, lambda: self.audio_chip.set_kind_text(prefix))
        elif peak > 0:
            self.after(0, lambda: self.audio_chip.set_kind_text(
                f"{prefix} · pico máx +{peak:.1f} dB ⚠ pasa de 0, puede clipear", RED))
        else:
            self.after(0, lambda: self.audio_chip.set_kind_text(
                f"{prefix} · pico máx {peak:.1f} dB ✔ no clipea", GREEN))

    def _remove_media(self):
        self.media_path = None
        self.media_is_video = False
        self.media_size = None
        self.media_duration = None
        self._thumb_ref = None
        self.media_chip.grid_remove()
        self.scale_row.grid_remove()
        self.trim_row.grid_remove()
        self.speed_row.grid_remove()
        self._refresh_ready_state()
        self._schedule_preview()

    def _remove_audio(self):
        self.audio_path = None
        self.audio_chip.grid_remove()
        self._refresh_ready_state()

    # -------------------------------------------------------- tamano en pantalla

    def _on_scale_change(self, value):
        pct = int(round(value))
        if pct >= 100:
            self.scale_value_label.configure(text="100% · pantalla completa")
        else:
            self.scale_value_label.configure(text=f"{pct}% · con bordes negros")
        self._schedule_preview()

    def _current_scale_pct(self):
        return int(round(self.scale_slider.get()))

    # ------------------------------------------------------------- salida

    def _update_default_output(self):
        """El video se nombra como la cancion y se guarda junto a ella.
        Si el usuario ya eligio una ruta manualmente, se respeta."""
        if self.user_chose_output:
            return
        source = self.audio_path or self.media_path
        if not source:
            return
        folder = os.path.dirname(source)
        base = os.path.splitext(os.path.basename(source))[0]
        self.output_path = unique_output_path(folder, base)
        self.output_label.configure(text=truncate_path(self.output_path))

    def _choose_output(self):
        initial = self.output_path or "video.mp4"
        path = filedialog.asksaveasfilename(
            title="Guardar video como",
            defaultextension=".mp4",
            filetypes=[("Video MP4", "*.mp4")],
            initialfile=os.path.basename(initial),
            initialdir=os.path.dirname(initial) if os.path.dirname(initial) else None,
        )
        if path:
            self.output_path = path
            self.user_chose_output = True
            self.output_label.configure(text=truncate_path(path))

    def _refresh_ready_state(self):
        ready = bool(self.media_path and self.audio_path)
        self.generate_btn.configure(state="normal" if ready else "disabled")
        # Con plantilla activa el tamano ya viene dado por su ventana transparente
        if self.media_path and not self.template_path:
            self.scale_row.grid()
        else:
            self.scale_row.grid_remove()
        if self.media_is_video:
            self.trim_row.grid()
            self.speed_row.grid()
        else:
            self.trim_row.grid_remove()
            self.speed_row.grid_remove()
        if not ready:
            self._set_status("Esperando imagen/clip y audio...")

    def _set_status(self, text, color=None):
        self.status_label.configure(text=text, text_color=color or TEXT_GRAY)

    # ---------------------------------------------------------- generacion

    def _start_generation(self):
        if not self.media_path or not self.audio_path:
            return
        if self.media_is_video and not self.media_size:
            self._set_status("Todavía analizando el clip, intenta en un segundo...", RED)
            return

        self.trim_range = None
        if self.media_is_video:
            start = parse_time_input(self.trim_start_entry.get())
            end = parse_time_input(self.trim_end_entry.get())
            if start is None or end is None:
                self._set_status("Recorte inválido: usa el formato m:ss, por ejemplo 1:10 o 1.10.", RED)
                return
            duration = self.media_duration
            if duration:
                start = min(start, duration)
                end = min(end, duration)
            if end - start < 0.5:
                self._set_status("Recorte inválido: 'Hasta' debe ser mayor que 'Desde' (mínimo 0.5 s).", RED)
                return
            # Solo recortamos si el rango es mas angosto que el clip completo
            if start > 0.05 or (duration and end < duration - 0.35):
                self.trim_range = (start, end)

        if not self.output_path:
            self._update_default_output()
        if os.path.exists(self.output_path):
            if not messagebox.askyesno(
                "El archivo ya existe",
                f"{os.path.basename(self.output_path)} ya existe. ¿Deseas reemplazarlo?",
            ):
                return

        self.generate_btn.configure(state="disabled")
        self.browse_btn.configure(state="disabled")
        self.open_folder_btn.grid_remove()
        self.open_video_btn.grid_remove()
        self.cancel_btn.grid()
        self.progress.set(0)
        self.progress.grid()
        self.cancel_requested = False
        self._set_status("Preparando...")

        thread = threading.Thread(target=self._run_ffmpeg_job, daemon=True)
        thread.start()

    def _run_ffmpeg_job(self):
        temp_unit = None
        temp_list = None
        try:
            info = probe_media(self.ffmpeg_exe, self.audio_path)
            duration = info["duration"]

            speed = 1.0
            if self.media_is_video:
                try:
                    speed = float(self.speed_control.get().rstrip("x"))
                except ValueError:
                    speed = 1.0

            template_path = self.template_path
            template_box = self.template_box
            speed_note = f" · {speed:g}x" if speed != 1.0 else ""
            tpl_note = " con plantilla" if template_path else ""

            layout, width, height = build_layout(
                self.media_size, self.media_is_video,
                self._current_scale_pct(), template_box if template_path else None,
            )

            texture = None
            if self.texture_path:
                mode = BLEND_MODES.get(self.blend_menu.get(), "lighten")
                opacity = self.opacity_slider.get() / 100.0
                prepared = self._prepare_texture(*layout["canvas"])
                texture = (prepared, mode, opacity)

            if info["audio_codec"] == "aac":
                strategies = ["copy", "aac_mf", "aac"]
            else:
                strategies = ["aac_mf", "aac"]

            if self.media_is_video:
                # FASE 1: componer una sola vuelta del loop (corta y rapida)
                trim = self.trim_range
                if trim:
                    unit_src = trim[1] - trim[0]
                elif self.media_duration:
                    unit_src = self.media_duration
                else:
                    unit_src = None
                unit_duration = unit_src / speed if unit_src else None

                temp_unit = os.path.join(tempfile.gettempdir(), "genvideo_loop.mp4")
                self.after(0, lambda: self._set_status(
                    f"Componiendo el loop a {width}x{height}{tpl_note}{speed_note}..."))
                returncode = self._run_ffmpeg(
                    build_compose_command(
                        self.ffmpeg_exe, self.media_path, temp_unit, layout,
                        trim=trim, speed=speed, deinterlace=self.media_interlaced,
                        template_path=template_path, texture=texture,
                    ),
                    unit_duration,
                )
                if self.cancel_requested or returncode != 0:
                    self.after(0, lambda: self._on_job_done(returncode))
                    return

                # FASE 2: encadenar el loop con concat + copia directa + beat
                unit_info = probe_media(self.ffmpeg_exe, temp_unit)
                real_unit = unit_info["duration"] or unit_duration
                if duration and real_unit:
                    repeats = max(1, math.ceil(duration / real_unit) + 1)
                else:
                    repeats = 1
                temp_list = os.path.join(tempfile.gettempdir(), "genvideo_concat.txt")
                write_concat_list(temp_unit, repeats, temp_list)

                self.after(0, lambda: self._set_status("Generando video (loop + beat)..."))
                returncode = -1
                for strategy in strategies:
                    audio_args = audio_strategy_args(strategy, info["sample_rate"])
                    cmd = build_mux_command(
                        self.ffmpeg_exe, temp_list, self.audio_path,
                        self.output_path, duration, audio_args,
                    )
                    returncode = self._run_ffmpeg(cmd, duration)
                    if returncode == 0 or self.cancel_requested:
                        break
            else:
                # Imagenes: una sola pasada (ya es rapida a 10 fps)
                self.after(0, lambda: self._set_status(
                    f"Generando video a {width}x{height}{tpl_note}..."))
                returncode = -1
                for strategy in strategies:
                    audio_args = audio_strategy_args(strategy, info["sample_rate"])
                    cmd = build_command(
                        self.ffmpeg_exe, self.media_path,
                        self.audio_path, self.output_path, duration, audio_args,
                        layout, template_path=template_path, texture=texture,
                    )
                    returncode = self._run_ffmpeg(cmd, duration)
                    if returncode == 0 or self.cancel_requested:
                        break

            self.after(0, lambda: self._on_job_done(returncode))
        except Exception as exc:
            self.after(0, lambda: self._on_job_error(str(exc)))
        finally:
            for temp in (temp_unit, temp_list):
                if temp and os.path.exists(temp):
                    try:
                        os.remove(temp)
                    except OSError:
                        pass

    def _run_ffmpeg(self, cmd, duration):
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )

        for line in self.process.stdout:
            line = line.strip()
            if line.startswith("out_time=") and duration:
                seconds = parse_out_time(line)
                if seconds is not None:
                    frac = max(0.0, min(1.0, seconds / duration))
                    self.after(0, lambda f=frac: self.progress.set(f))

        return self.process.wait()

    def _on_job_done(self, returncode):
        self.cancel_btn.grid_remove()
        self.browse_btn.configure(state="normal")
        self.generate_btn.configure(state="normal")
        self.process = None

        if returncode == 0:
            self.progress.set(1)
            self._set_status(f"Listo: {os.path.basename(self.output_path)}", GREEN)
            self.open_video_btn.grid()
            self.open_folder_btn.grid()
        elif self.cancel_requested:
            self._set_status("Generación cancelada.")
            self.progress.grid_remove()
        else:
            self._set_status(f"Error al generar el video (código {returncode}).", RED)
            self.progress.grid_remove()

    def _on_job_error(self, message):
        self.cancel_btn.grid_remove()
        self.browse_btn.configure(state="normal")
        self.generate_btn.configure(state="normal")
        self.process = None
        self.progress.grid_remove()
        self._set_status("Error inesperado.", RED)
        messagebox.showerror("Error", message)

    def _cancel(self):
        if self.process and self.process.poll() is None:
            self.cancel_requested = True
            self.process.terminate()
            self._set_status("Cancelando...")

    def _open_video(self):
        if self.output_path and os.path.exists(self.output_path):
            if os.name == "nt":
                os.startfile(self.output_path)
            else:
                subprocess.Popen(["open", self.output_path])

    def _open_folder(self):
        if self.output_path and os.path.exists(self.output_path):
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(self.output_path)])
            else:
                subprocess.Popen(["open", "-R", self.output_path])


if __name__ == "__main__":
    App().mainloop()
