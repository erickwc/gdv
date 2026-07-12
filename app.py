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
import hashlib
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
import tkinter as tk
from PIL import Image, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD
from tkinter import filedialog, messagebox
from tkinter import font as tkfont

MAX_WIDTH = 1920
MAX_HEIGHT = 1080

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".gif"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".opus"}

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
IS_MAC = sys.platform == "darwin"

# En Mac, el Python de python.org no trae certificados SSL configurados y
# toda conexion HTTPS falla (CERTIFICATE_VERIFY_FAILED) -- sin esto la
# descarga por link con yt-dlp no funciona. Usamos los certificados de
# certifi si esta instalado.
if IS_MAC:
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass

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
BROWSE_TEMPLATE = "Buscar archivo..."
NO_PRESET = "(sin presets guardados)"

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


# Calidad/peso del video: veryfast comprime ~2x mejor que ultrafast casi a
# la misma velocidad, y el tope de bitrate (VBV 12 Mbps, el rango que usa
# YouTube para 1080p) evita que el grano dispare el peso -- sin tope, CRF
# intenta preservar el ruido pixel por pixel y un video de 3 min puede
# pasar de 700 MB. Con el tope queda en ~180-250 MB manteniendo la nitidez.
VIDEO_QUALITY_ARGS = [
    "-preset", "veryfast", "-crf", "19",
    "-maxrate", "12M", "-bufsize", "24M",
]

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
    else:
        # Sin plantilla el lienzo siempre es 1920x1080 negro y la base
        # (scale_pct=100) es SIEMPRE un cuadrado fijo de 1080x1080, sin
        # importar el tamano ni la proporcion original del archivo -- asi
        # el mismo % recorta exactamente igual sin importar que imagen se
        # cargue (Ajustar imagen ya decide que parte de la foto entra en
        # ese cuadrado). La altura se queda fija siempre en 1080 -- el
        # control de escala solo mueve el ANCHO (a partir del centro): por
        # debajo de 100% encoge y deja bordes SOLO a los lados; por encima
        # de 100% amplia, recortando lo que sobre (sin deformar) conforme
        # se acerca a llenar el lienzo.
        natural_w = natural_h = MAX_HEIGHT
        scale_factor = max(0.01, scale_pct / 100)
        inner_w = max(2, min(MAX_WIDTH, int(natural_w * scale_factor) // 2 * 2))
        layout = {"mode": "bordered", "inner": (inner_w, natural_h),
                  "canvas": (MAX_WIDTH, MAX_HEIGHT), "pos": None}
    return layout, layout["canvas"][0], layout["canvas"][1]


def build_focus_crop(focus):
    """Recorte manual (zoom + punto de interes) aplicado ANTES de todo lo
    demas: recorta el origen a iw/zoom x ih/zoom (misma proporcion, solo
    mas cerca) centrado en (focus_x, focus_y). zoom=1.0 y foco centrado
    -> no-op, asi que no cambia nada para quien no toque el ajuste."""
    zoom, focus_x, focus_y = focus
    zoom = max(1.0, zoom)
    if zoom == 1.0 and focus_x == 0.5 and focus_y == 0.5:
        return ""
    crop_w = f"iw/{zoom:.4f}"
    crop_h = f"ih/{zoom:.4f}"
    crop_x = f"(iw-({crop_w}))*{focus_x:.4f}"
    crop_y = f"(ih-({crop_h}))*{focus_y:.4f}"
    return f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"


def build_filtergraph(layout, is_video=False, speed=1.0, deinterlace=False,
                      tpl_idx=None, textures=None, focus=None):
    """Arma el filter_complex completo: medio (des-entrelazado + velocidad +
    ajuste manual de encuadre + escala/recorte/bordes) -> texturas mezcladas
    encima (una sobre otra, en el orden de la lista) -> plantilla encima.
    Los clips de video salen a 30 fps constantes para que el loop por copia
    directa sea perfectamente uniforme (sin glitches).

    textures: lista de (indice_de_entrada, modo_ffmpeg, opacidad_0_a_1)."""
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
    if focus is not None:
        chain += build_focus_crop(focus)
    # Escala/recorta al tamano REAL de la foto (inner_w x inner_h) sin
    # rellenar todavia -- el pad (bordes negros) se agrega DESPUES de
    # mezclar la textura, para que la textura solo caiga sobre la foto y
    # nunca sobre el borde negro.
    chain += f"scale={inner_w}:{inner_h}:force_original_aspect_ratio=increase:force_divisible_by=2,crop={inner_w}:{inner_h}"
    if layout["mode"] == "bordered":
        # inner_h es siempre el alto natural de la foto (nunca cambia), asi
        # que "cubrir" inner_w x inner_h solo recorta ANCHO cuando el control
        # de bordes achica inner_w -- el alto nunca se toca. Los bordes
        # verticales que pueda haber (por la proporcion de la foto) se
        # agregan aparte con el pad, y no varian con el control.
        pad_expr = f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black"
    else:  # template: cubrir la ventana por completo, sin dejar bordes
        x, y = layout["pos"]
        pad_expr = f"pad={canvas_w}:{canvas_h}:{x}:{y}:color=black"
    if is_video:
        chain += ",fps=30"
    parts = [chain + "[base]"]
    last = "base"

    # Cada textura se mezcla en RGB plano (gbrp), igual que Photoshop,
    # escalada al mismo tamano de la foto (no del lienzo completo), encima
    # del resultado de la capa anterior -- asi se pueden apilar varias.
    for i, (tex_idx, tex_mode, tex_opacity) in enumerate(textures or []):
        texs_label = f"texs{i}"
        basef_label = f"basef{i}"
        out_label = f"textured{i}"
        parts.append(
            f"[{tex_idx}:v]scale={inner_w}:{inner_h}:force_original_aspect_ratio=increase,"
            f"crop={inner_w}:{inner_h},format=gbrp[{texs_label}]"
        )
        parts.append(f"[{last}]format=gbrp[{basef_label}]")
        parts.append(
            f"[{basef_label}][{texs_label}]blend=all_mode={tex_mode}:all_opacity={tex_opacity:.3f}[{out_label}]"
        )
        last = out_label

    parts.append(f"[{last}]{pad_expr}[padded]")
    last = "padded"

    if tpl_idx is not None:
        parts.append(f"[{last}][{tpl_idx}:v]overlay=0:0[tpld]")
        last = "tpld"

    parts.append(f"[{last}]format=yuv420p[vout]")
    return ";".join(parts)


def build_command(ffmpeg_exe, media_path, audio_path, output_path, duration, audio_args,
                  layout, template_path=None, textures=None, focus=None):
    """Pasada unica para imagenes fijas (el video es barato a 10 fps)."""
    cmd = [ffmpeg_exe, "-y", "-loop", "1", "-framerate", "1", "-i", media_path, "-i", audio_path]
    idx = 2
    tpl_idx = None
    if template_path:
        cmd += ["-i", template_path]
        tpl_idx = idx
        idx += 1
    tex_layers = []
    for path, mode, opacity in (textures or []):
        cmd += ["-i", path]
        tex_layers.append((idx, mode, opacity))
        idx += 1
    fc = build_filtergraph(
        layout, is_video=False, tpl_idx=tpl_idx, textures=tex_layers, focus=focus,
    )
    cmd += [
        "-filter_complex", fc, "-map", "[vout]", "-map", "1:a:0",
        "-r", "10",
        "-c:v", "libx264", *VIDEO_QUALITY_ARGS,
        "-pix_fmt", "yuv420p", "-tune", "stillimage",
        *audio_args,
        "-shortest",
    ]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", output_path]
    return cmd


def build_compose_command(ffmpeg_exe, media_path, temp_path, layout, trim=None, speed=1.0,
                          deinterlace=False, template_path=None, textures=None):
    """FASE 1 (solo clips de video): compone UNA sola vuelta del loop —
    recorte + velocidad + texturas + escala/bordes o plantilla — en un mp4
    corto sin audio, a 30 fps constantes y con GOP cerrado para que la
    fase 2 pueda repetirlo con copia directa sin glitches."""
    cmd = [ffmpeg_exe, "-y"]
    if trim:
        cmd += ["-ss", f"{trim[0]:.3f}", "-to", f"{trim[1]:.3f}"]
    cmd += ["-i", media_path]
    idx = 1
    tpl_idx = None
    if template_path:
        cmd += ["-i", template_path]
        tpl_idx = idx
        idx += 1
    tex_layers = []
    for path, mode, opacity in (textures or []):
        cmd += ["-i", path]
        tex_layers.append((idx, mode, opacity))
        idx += 1
    fc = build_filtergraph(
        layout, is_video=True, speed=speed, deinterlace=deinterlace,
        tpl_idx=tpl_idx, textures=tex_layers,
    )
    cmd += [
        "-filter_complex", fc, "-map", "[vout]",
        "-an",
        "-c:v", "libx264", *VIDEO_QUALITY_ARGS, "-pix_fmt", "yuv420p",
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


def register_drop_recursive(widget, on_drop, on_enter=None, on_leave=None, skip=None):
    """Registra drop de archivos en todo el arbol de widgets. 'skip' es un
    set de widgets cuyo subarbol se deja intacto (para que zonas mas
    especificas -- plantilla, texturas -- puedan tener su propio drop sin
    que este registro generico se lo pise). El popup interno de un
    CTkOptionMenu es un tkinter.Menu (no una ventana normal); registrar un
    drop target ahi no sirve de nada y en Windows puede interferir con cual
    ventana recibe el drop real, dejando la parte visible sin reaccionar."""
    if skip and widget in skip:
        return
    if isinstance(widget, tk.Menu):
        return
    widget.drop_target_register(DND_FILES)
    widget.dnd_bind("<<Drop>>", on_drop)
    if on_enter is not None:
        widget.dnd_bind("<<DropEnter>>", on_enter)
    if on_leave is not None:
        widget.dnd_bind("<<DropLeave>>", on_leave)
    for child in widget.winfo_children():
        register_drop_recursive(child, on_drop, on_enter, on_leave, skip=skip)


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


def resolve_color(color):
    """Las constantes de paleta son tuplas (claro, oscuro); un tk.Canvas
    (a diferencia de los widgets ctk) no se autoajusta al modo, asi que
    hay que resolver el color correcto a mano segun el modo actual."""
    if isinstance(color, (tuple, list)):
        return color[0] if ctk.get_appearance_mode() == "Light" else color[1]
    return color


class RangeSlider(tk.Canvas):
    """Slider de doble asa para el recorte del loop: mismo estilo que los
    CTkSlider de la app (riel gris, tramo activo y manijas en GREEN), pero
    con dos manijas independientes en vez de una."""

    PAD = 8
    MIN_GAP = 0.5  # segundos

    def __init__(self, master, command=None, height=20):
        super().__init__(master, height=height, bg=resolve_color(CARD_BG),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.duration = 1.0
        self.start = 0.0
        self.end = 1.0
        self._grab = None
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_move)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_grab", None))

    def refresh_colors(self):
        self.configure(bg=resolve_color(CARD_BG))
        self._redraw()

    def set_duration(self, duration):
        self.duration = max(self.MIN_GAP, float(duration or 1.0))
        self.start = 0.0
        self.end = self.duration
        self._redraw()

    def set_values(self, start, end):
        self.start = max(0.0, min(float(start), self.duration - self.MIN_GAP))
        self.end = max(self.start + self.MIN_GAP, min(float(end), self.duration))
        self._redraw()

    def get_values(self):
        return self.start, self.end

    def _sec_to_x(self, sec):
        usable = max(1, self.winfo_width() - 2 * self.PAD)
        return self.PAD + (sec / self.duration) * usable

    def _x_to_sec(self, x):
        usable = max(1, self.winfo_width() - 2 * self.PAD)
        frac = (x - self.PAD) / usable
        return max(0.0, min(1.0, frac)) * self.duration

    def _on_press(self, event):
        sec = self._x_to_sec(event.x)
        self._grab = "start" if abs(sec - self.start) <= abs(sec - self.end) else "end"
        self._apply(sec)

    def _on_move(self, event):
        if self._grab:
            self._apply(self._x_to_sec(event.x))

    def _apply(self, sec):
        if self._grab == "start":
            self.start = max(0.0, min(sec, self.end - self.MIN_GAP))
        else:
            self.end = min(self.duration, max(sec, self.start + self.MIN_GAP))
        self._redraw()
        if self.command:
            self.command(self.start, self.end)

    def _redraw(self):
        self.delete("all")
        width = self.winfo_width()
        if width <= 1:
            return
        cy = int(self.winfo_height() / 2)
        x0, x1 = self.PAD, width - self.PAD
        xs, xe = self._sec_to_x(self.start), self._sec_to_x(self.end)
        self.create_line(x0, cy, x1, cy, width=4, fill=resolve_color(BORDER), capstyle="round")
        self.create_line(xs, cy, xe, cy, width=4, fill=GREEN, capstyle="round")
        for x in (xs, xe):
            self.create_oval(x - 7, cy - 7, x + 7, cy + 7, fill=GREEN, outline="")


class ImageFocusPicker(tk.Canvas):
    """Miniatura interactiva para elegir que parte de una imagen resaltar:
    arrastra el recuadro para mover el encuadre; el zoom (controlado desde
    afuera via set_zoom) lo acerca. El recuadro verde muestra el resultado
    FINAL real -- incluye tambien el efecto del control de Escala
    (set_scale_pct): por debajo de 100% dejar bordes a los lados, por
    encima de 100% recorta un poco arriba/abajo para ampliar -- igual que
    build_layout, para que no haya sorpresas entre esta miniatura y el
    video exportado. zoom=100%, centrado y escala=100% = imagen completa,
    sin recortar nada."""

    DISPLAY_W = 352
    DISPLAY_H = 200

    def __init__(self, master, command=None):
        super().__init__(master, width=self.DISPLAY_W, height=self.DISPLAY_H,
                         bg=resolve_color(CHIP_BG), highlightthickness=0, bd=0)
        self.command = command
        self._photo = None
        self.disp_w = self.DISPLAY_W
        self.disp_h = self.DISPLAY_H
        self.zoom = 100
        self.focus_x = 0.5
        self.focus_y = 0.5
        self.scale_pct = 100  # ver set_scale_pct
        self._drag_origin = None
        self._drag_focus_origin = None
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_move)

    def refresh_colors(self):
        self.configure(bg=resolve_color(CHIP_BG))
        self._redraw()

    def set_image(self, pil_image):
        w, h = pil_image.size
        disp_w, disp_h = self.DISPLAY_W, round(self.DISPLAY_W * h / w)
        if disp_h > self.DISPLAY_H:
            disp_h = self.DISPLAY_H
            disp_w = round(self.DISPLAY_H * w / h)
        self.disp_w, self.disp_h = disp_w, disp_h
        thumb = pil_image.convert("RGB").resize((disp_w, disp_h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(thumb)
        self.zoom = 100
        self.focus_x = 0.5
        self.focus_y = 0.5
        self._redraw()

    def set_state(self, zoom, focus_x, focus_y):
        self.zoom = max(100, min(300, zoom))
        self.focus_x = max(0.0, min(1.0, focus_x))
        self.focus_y = max(0.0, min(1.0, focus_y))
        self._redraw()

    def set_zoom(self, zoom):
        self.zoom = max(100, min(300, zoom))
        self._redraw()
        self._notify()

    def set_scale_pct(self, scale_pct):
        """Misma cuenta que build_layout: por debajo de 100% el ancho se
        encoge (bordes a los lados); por encima de 100% el alto es lo que
        cede para poder ampliar el ancho, igual que el filtro real."""
        self.scale_pct = max(1, scale_pct)
        self._redraw()

    def get_state(self):
        return self.zoom / 100.0, self.focus_x, self.focus_y

    def _focus_rect(self):
        frac = 100.0 / self.zoom
        cw, ch = self.disp_w * frac, self.disp_h * frac
        ox, oy = (self.DISPLAY_W - self.disp_w) / 2, (self.DISPLAY_H - self.disp_h) / 2
        cx = ox + (self.disp_w - cw) * self.focus_x
        cy = oy + (self.disp_h - ch) * self.focus_y
        return cx, cy, cx + cw, cy + ch

    def _scale_crop_fracs(self):
        """Fracciones de ancho/alto que sobreviven tras aplicar la escala,
        replicando la logica de cubrir+recortar de build_filtergraph."""
        s = self.scale_pct / 100.0
        cover = max(s, 1.0)
        return s / cover, 1.0 / cover

    def _final_rect(self):
        """Recuadro del focus (zoom+pan), recortado a un cuadrado centrado
        (la base fija de build_layout, sin importar la proporcion de la
        foto) y despues con el efecto de la Escala encima."""
        fx0, fy0, fx1, fy1 = self._focus_rect()
        fw, fh = fx1 - fx0, fy1 - fy0
        side = min(fw, fh)
        sx0 = fx0 + (fw - side) / 2
        sy0 = fy0 + (fh - side) / 2
        sx1, sy1 = sx0 + side, sy0 + side
        w_frac, h_frac = self._scale_crop_fracs()
        shrink_x = side * (1 - w_frac) / 2
        shrink_y = side * (1 - h_frac) / 2
        return sx0 + shrink_x, sy0 + shrink_y, sx1 - shrink_x, sy1 - shrink_y

    def _on_press(self, event):
        if self._photo is None:
            return
        self._drag_origin = (event.x, event.y)
        self._drag_focus_origin = (self.focus_x, self.focus_y)

    def _on_move(self, event):
        if self._photo is None or self._drag_origin is None:
            return
        frac = 100.0 / self.zoom
        range_x = max(1.0, self.disp_w * (1 - frac))
        range_y = max(1.0, self.disp_h * (1 - frac))
        dx = event.x - self._drag_origin[0]
        dy = event.y - self._drag_origin[1]
        fx0, fy0 = self._drag_focus_origin
        self.focus_x = max(0.0, min(1.0, fx0 + dx / range_x))
        self.focus_y = max(0.0, min(1.0, fy0 + dy / range_y))
        self._redraw()
        self._notify()

    def _notify(self):
        if self.command:
            self.command(*self.get_state())

    def _redraw(self):
        self.delete("all")
        if self._photo is None:
            self.create_text(
                self.DISPLAY_W / 2, self.DISPLAY_H / 2, text="Carga una imagen",
                fill=resolve_color(TEXT_GRAY), font=(FONT_LIGHT, 11),
            )
            return
        ox, oy = (self.DISPLAY_W - self.disp_w) / 2, (self.DISPLAY_H - self.disp_h) / 2
        self.create_image(ox, oy, image=self._photo, anchor="nw")
        cx0, cy0, cx1, cy1 = self._final_rect()
        if cx0 > ox + 0.5 or cy0 > oy + 0.5 or cx1 < ox + self.disp_w - 0.5 or cy1 < oy + self.disp_h - 0.5:
            # atenuar lo que queda fuera del recuadro final (cuadrado base + zoom + escala)
            self.create_rectangle(ox, oy, ox + self.disp_w, cy0, fill="#000000", stipple="gray50", outline="")
            self.create_rectangle(ox, cy1, ox + self.disp_w, oy + self.disp_h, fill="#000000", stipple="gray50", outline="")
            self.create_rectangle(ox, cy0, cx0, cy1, fill="#000000", stipple="gray50", outline="")
            self.create_rectangle(cx1, cy0, ox + self.disp_w, cy1, fill="#000000", stipple="gray50", outline="")
        self.create_rectangle(cx0, cy0, cx1, cy1, outline=GREEN, width=2)


class TextureLayerRow(ctk.CTkFrame):
    """Una capa de textura apilable: elegir archivo, modo de fusion,
    opacidad y escala, mas un boton para quitar la capa completa (varias
    de estas, una encima de otra, permiten mezclar mas de una textura)."""

    def __init__(self, master, available_paths_fn, on_change, on_remove, on_browse,
                 make_pct_entry, bind_pct_entry, set_pct_entry,
                 register_path_fn=None, on_delete_file=None):
        super().__init__(master, fg_color=CHIP_BG, corner_radius=10)
        self.available_paths_fn = available_paths_fn
        self.on_change = on_change
        self.on_remove = on_remove
        self.on_browse = on_browse
        self.register_path_fn = register_path_fn
        self.on_delete_file = on_delete_file
        self.path = None
        self.grid_columnconfigure(0, weight=1)

        file_row = ctk.CTkFrame(self, fg_color="transparent")
        file_row.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        file_row.grid_columnconfigure(0, weight=1)

        self.file_menu = ctk.CTkOptionMenu(
            file_row, values=[BROWSE_TEMPLATE], height=30, corner_radius=8,
            fg_color=CARD_BG, button_color=CARD_BG, button_hover_color=HOVER_BG,
            text_color=TEXT_DARK, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT_DARK, dropdown_hover_color=HOVER_BG,
            font=ctk.CTkFont(FONT_REGULAR, 12),
            dropdown_font=ctk.CTkFont(FONT_REGULAR, 12),
            command=self._on_file_selected,
        )
        self.file_menu.grid(row=0, column=0, sticky="ew")

        self.delete_file_btn = ctk.CTkButton(
            file_row, text="🗑", width=30, height=30, corner_radius=8,
            fg_color=CARD_BG, hover_color=HOVER_BG, text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_REGULAR, 13), command=self._delete_file,
        )
        self.delete_file_btn.grid(row=0, column=1, padx=(8, 0))

        self.remove_btn = ctk.CTkButton(
            file_row, text="✕", width=30, height=30, corner_radius=8,
            fg_color=CARD_BG, hover_color=HOVER_BG, text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_REGULAR, 12), command=self._remove,
        )
        self.remove_btn.grid(row=0, column=2, padx=(8, 0))

        opts_row = ctk.CTkFrame(self, fg_color="transparent")
        opts_row.grid(row=1, column=0, sticky="ew", padx=10, pady=(8, 10))
        opts_row.grid_columnconfigure(1, weight=1)

        self.blend_menu = ctk.CTkOptionMenu(
            opts_row, values=list(BLEND_MODES.keys()),
            width=118, height=28, corner_radius=8,
            fg_color=CARD_BG, button_color=CARD_BG, button_hover_color=HOVER_BG,
            text_color=TEXT_DARK, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT_DARK, dropdown_hover_color=HOVER_BG,
            font=ctk.CTkFont(FONT_REGULAR, 12),
            dropdown_font=ctk.CTkFont(FONT_REGULAR, 12),
            command=lambda _c: self.on_change(),
        )
        self.blend_menu.set("Aclarar")
        self.blend_menu.grid(row=0, column=0)

        self.opacity_slider = ctk.CTkSlider(
            opts_row, from_=0, to=100, number_of_steps=100,
            progress_color=GREEN, button_color=GREEN, button_hover_color=GREEN_HOVER,
            command=lambda v: self._on_opacity_change(v),
        )
        self.opacity_slider.set(47)
        self.opacity_slider.grid(row=0, column=1, sticky="ew", padx=(10, 8))

        self.opacity_entry = make_pct_entry(opts_row)
        self.opacity_entry.insert(0, "47")
        self.opacity_entry.grid(row=0, column=2)
        bind_pct_entry(self.opacity_entry, self.opacity_slider, 0, 100,
                       lambda v: self._on_opacity_change(v))
        self._set_pct_entry = set_pct_entry

        scale_label = ctk.CTkLabel(
            opts_row, text="Escala", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12), width=118, anchor="w",
        )
        scale_label.grid(row=1, column=0, sticky="w", pady=(8, 0))

        self.scale_slider = ctk.CTkSlider(
            opts_row, from_=10, to=200, number_of_steps=38,
            progress_color=GREEN, button_color=GREEN, button_hover_color=GREEN_HOVER,
            command=lambda v: self._on_scale_change(v),
        )
        self.scale_slider.set(100)
        self.scale_slider.grid(row=1, column=1, sticky="ew", padx=(10, 8), pady=(8, 0))

        self.scale_entry = make_pct_entry(opts_row)
        self.scale_entry.insert(0, "100")
        self.scale_entry.grid(row=1, column=2, pady=(8, 0))
        bind_pct_entry(self.scale_entry, self.scale_slider, 10, 200,
                       lambda v: self._on_scale_change(v))

        self.refresh_files()

        # Soltar una imagen sobre esta capa reemplaza SU archivo -- se
        # registra al final, sobre todo el subarbol de la fila, para que
        # gane sobre cualquier drop mas generico de la seccion completa.
        register_drop_recursive(self, self._on_file_dropped)

    def _on_file_dropped(self, event):
        for path in self.tk.splitlist(event.data):
            if os.path.splitext(path)[1].lower() in IMAGE_EXTS:
                self.set_path(path)
                return

    def refresh_files(self):
        values = list(self.available_paths_fn().keys()) + [BROWSE_TEMPLATE]
        self.file_menu.configure(values=values)
        if self.path:
            display = os.path.splitext(os.path.basename(self.path))[0]
            if display not in values:
                self.file_menu.configure(values=[display] + values)
            self.file_menu.set(display)

    def _on_file_selected(self, choice):
        if choice == BROWSE_TEMPLATE:
            path = self.on_browse()
            if not path:
                if self.path:
                    self.file_menu.set(os.path.splitext(os.path.basename(self.path))[0])
                return
            self.set_path(path)
            return
        path = self.available_paths_fn().get(choice)
        if path:
            self.set_path(path)

    def set_path(self, path):
        self.path = path
        if self.register_path_fn:
            self.register_path_fn(path)
        self.refresh_files()
        self.on_change()

    def _delete_file(self):
        if self.on_delete_file:
            self.on_delete_file(self.path)

    def _on_opacity_change(self, value):
        pct = int(round(float(value)))
        self._set_pct_entry(self.opacity_entry, pct)
        self.on_change()

    def _on_scale_change(self, value):
        pct = int(round(float(value)))
        self._set_pct_entry(self.scale_entry, pct)
        self.on_change()

    def get_state(self):
        if not self.path:
            return None
        return {
            "path": self.path,
            "blend": self.blend_menu.get(),
            "opacity": int(round(self.opacity_slider.get())),
            "scale": int(round(self.scale_slider.get())),
        }

    def set_state(self, state):
        if state.get("path"):
            self.path = state["path"]
            self.refresh_files()
        self.blend_menu.set(state.get("blend", "Aclarar"))
        opacity = state.get("opacity", 47)
        self.opacity_slider.set(opacity)
        self._set_pct_entry(self.opacity_entry, opacity)
        scale = state.get("scale", 100)
        self.scale_slider.set(scale)
        self._set_pct_entry(self.scale_entry, scale)

    def _remove(self):
        self.on_remove(self)


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
        self.custom_output_name = ""
        self.template_path = None
        self.template_box = None
        self._template_paths = {}
        self._texture_paths = {}
        self._texture_cache = {}
        self._texture_lock = threading.Lock()
        self.process = None
        self.cancel_requested = False
        self._thumb_ref = None
        self.presets = [p for p in self.config_data.get("presets", []) if isinstance(p, dict) and p.get("name")]
        self.preview_win = None
        self.preview_label = None
        self._preview_ref = None
        self._preview_after_id = None
        self._preview_token = None
        self._preview_counter = 0

        self._build_widgets()
        self._restore_template_from_config()
        self._restore_texture_from_config()
        self._apply_textures_panel_state()

        # Se puede soltar archivos en cualquier parte de la ventana (como
        # imagen/video/audio principal), excepto en Plantilla y Texturas,
        # que ya tienen su propio drop mas especifico registrado aparte.
        register_drop_recursive(
            self, self._handle_drop, self._on_drag_enter, self._on_drag_leave,
            skip={self.template_row, self.texture_row},
        )

    # ------------------------------------------------------------------ UI

    def _build_widgets(self):
        card = ctk.CTkScrollableFrame(
            self, fg_color=CARD_BG, corner_radius=16,
            scrollbar_button_color=BORDER, scrollbar_button_hover_color=HOVER_BG,
        )
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

        # Ajustar imagen: arrastra el recuadro para elegir que parte
        # resaltar y usa el zoom para acercarlo (solo para imagenes)
        self.focus_row = ctk.CTkFrame(card, fg_color="transparent")
        self.focus_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        self.focus_row.grid_columnconfigure(0, weight=1)
        self.focus_row.grid_remove()
        row += 1

        focus_title = ctk.CTkLabel(
            self.focus_row, text="Ajustar imagen", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        focus_title.grid(row=0, column=0, sticky="w")

        self.focus_reset_btn = ctk.CTkButton(
            self.focus_row, text="↺ Centrar", width=76, height=24, corner_radius=8,
            fg_color=CHIP_BG, hover_color=HOVER_BG, text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_REGULAR, 11), command=self._reset_focus,
        )
        self.focus_reset_btn.grid(row=0, column=1, sticky="e")

        self.focus_picker = ImageFocusPicker(self.focus_row, command=self._on_focus_change)
        self.focus_picker.grid(row=1, column=0, columnspan=2, pady=(8, 0))

        focus_zoom_row = ctk.CTkFrame(self.focus_row, fg_color="transparent")
        focus_zoom_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        focus_zoom_row.grid_columnconfigure(0, weight=1)

        self.focus_zoom_slider = ctk.CTkSlider(
            focus_zoom_row, from_=100, to=300, number_of_steps=40,
            progress_color=GREEN, button_color=GREEN, button_hover_color=GREEN_HOVER,
            command=self._on_focus_zoom_change,
        )
        self.focus_zoom_slider.set(100)
        self.focus_zoom_slider.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.focus_zoom_entry = self._make_pct_entry(focus_zoom_row)
        self.focus_zoom_entry.insert(0, "100")
        self.focus_zoom_entry.grid(row=0, column=1, padx=(0, 6))
        self._bind_pct_entry(self.focus_zoom_entry, self.focus_zoom_slider, 100, 300,
                             lambda v: self._on_focus_zoom_change(v))

        self.focus_zoom_label = ctk.CTkLabel(
            focus_zoom_row, text="Zoom 100%", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12), width=52, anchor="e",
        )
        self.focus_zoom_label.grid(row=0, column=2)

        # Plantilla
        self.template_row = ctk.CTkFrame(card, fg_color="transparent")
        self.template_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        self.template_row.grid_columnconfigure(0, weight=1)
        row += 1

        template_title = ctk.CTkLabel(
            self.template_row, text="Plantilla", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        template_title.grid(row=0, column=0, columnspan=2, sticky="w")

        self.template_menu = ctk.CTkOptionMenu(
            self.template_row, values=[NO_TEMPLATE], height=34, corner_radius=8,
            fg_color=CHIP_BG, button_color=CHIP_BG, button_hover_color=HOVER_BG,
            text_color=TEXT_DARK, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT_DARK, dropdown_hover_color=HOVER_BG,
            font=ctk.CTkFont(FONT_REGULAR, 12),
            dropdown_font=ctk.CTkFont(FONT_REGULAR, 12),
            command=self._on_template_selected,
        )
        self.template_menu.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.template_delete_btn = ctk.CTkButton(
            self.template_row, text="🗑", width=34, height=34, corner_radius=8,
            fg_color=CHIP_BG, hover_color=HOVER_BG, text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_REGULAR, 13), command=self._delete_template,
            state="disabled",
        )
        self.template_delete_btn.grid(row=1, column=1, padx=(8, 0), pady=(6, 0))

        self.template_info = ctk.CTkLabel(
            self.template_row, text="arrastra un .png aquí para usarlo de plantilla",
            text_color=TEXT_GRAY, font=ctk.CTkFont(FONT_LIGHT, 12), anchor="w",
        )
        self.template_info.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Arrastrar un PNG aqui lo usa directo como plantilla (registrado
        # aparte del drop global de arriba, que solo entiende imagen/video/audio)
        register_drop_recursive(self.template_row, self._handle_template_drop)

        # Texturas (ruido/grano encima del medio, estilo Photoshop) --
        # varias capas apilables, cada una con su propio archivo, modo de
        # fusion, opacidad y escala
        self.texture_row = ctk.CTkFrame(card, fg_color="transparent")
        self.texture_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        self.texture_row.grid_columnconfigure(0, weight=1)
        row += 1

        texture_head = ctk.CTkFrame(self.texture_row, fg_color="transparent")
        texture_head.grid(row=0, column=0, sticky="ew")
        texture_head.grid_columnconfigure(1, weight=1)

        # Panel desplegable: la flecha y el titulo ocultan/muestran las
        # capas de textura para que la seccion no coma espacio cuando hay
        # varias texturas cargadas.
        self.textures_collapsed = bool(self.config_data.get("textures_collapsed", False))

        self.texture_toggle = ctk.CTkLabel(
            texture_head, text="▼", text_color=GREEN, width=26,
            font=ctk.CTkFont(FONT_MEDIUM, 13), anchor="w",
        )
        self.texture_toggle.grid(row=0, column=0, sticky="w")

        self.texture_title = ctk.CTkLabel(
            texture_head, text="Texturas", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        self.texture_title.grid(row=0, column=1, sticky="ew")

        for widget in (self.texture_toggle, self.texture_title):
            widget.bind("<Button-1>", lambda _e: self._toggle_textures_panel())
            try:
                widget._label.configure(cursor="hand2")
            except Exception:
                pass

        self.texture_add_btn = ctk.CTkButton(
            texture_head, text="+ Agregar textura", height=28, corner_radius=8,
            fg_color=CHIP_BG, hover_color=HOVER_BG, text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 12), command=self._on_add_texture_click,
        )
        self.texture_add_btn.grid(row=0, column=2, sticky="e")

        # Todo el contenido plegable (las capas de textura) vive en un
        # solo frame que NUNCA se desmonta: plegar solo reduce su altura a
        # 1px (grid_propagate off). Desmontar/montar widgets provoca un
        # parpadeo blanco en macOS (Tk pinta el area antes del primer
        # dibujado); manteniendolos montados el parpadeo no puede ocurrir.
        self.texture_body = ctk.CTkFrame(self.texture_row, fg_color="transparent")
        self.texture_body.grid(row=1, column=0, sticky="ew")
        self.texture_body.grid_columnconfigure(0, weight=1)

        self.texture_layers_container = ctk.CTkFrame(self.texture_body, fg_color="transparent")
        self.texture_layers_container.grid(row=0, column=0, sticky="ew", pady=(8, 0))
        self.texture_layers_container.grid_columnconfigure(0, weight=1)

        self.texture_empty_hint = ctk.CTkLabel(
            self.texture_body,
            text="Sin texturas — usa \"+ Agregar textura\" o arrastra una o varias imágenes aquí",
            text_color=TEXT_GRAY, font=ctk.CTkFont(FONT_LIGHT, 12), anchor="w", wraplength=360,
        )
        self.texture_empty_hint.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        # Soltar imagenes en cualquier parte de esta seccion (titulo, boton,
        # espacio vacio) crea una capa nueva por archivo. Cada capa,
        # individualmente, registra su propio drop para reemplazar SU
        # archivo (ver TextureLayerRow) -- como todavia no hay ninguna capa
        # creada en este punto, este registro no se les pisa.
        register_drop_recursive(self.texture_row, self._handle_texture_new_drop)

        self.texture_layers = []

        # Recorte del loop (solo para clips de video)
        self.trim_row = ctk.CTkFrame(card, fg_color="transparent")
        self.trim_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        self.trim_row.grid_columnconfigure(0, weight=1)
        self.trim_row.grid_remove()
        row += 1

        trim_title = ctk.CTkLabel(
            self.trim_row, text="Recorte del loop", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        trim_title.grid(row=0, column=0, sticky="w")

        self.trim_readout = ctk.CTkLabel(
            self.trim_row, text="0:00 – 0:00 · 0:00 de loop", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12), anchor="e",
        )
        self.trim_readout.grid(row=0, column=1, sticky="e")

        self.loop_slider = RangeSlider(self.trim_row, command=self._on_loop_change)
        self.loop_slider.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        trim_ticks = ctk.CTkFrame(self.trim_row, fg_color="transparent")
        trim_ticks.grid(row=2, column=0, columnspan=2, sticky="ew")
        trim_ticks.grid_columnconfigure(0, weight=1)

        tick_start = ctk.CTkLabel(
            trim_ticks, text="0:00", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 11), anchor="w",
        )
        tick_start.grid(row=0, column=0, sticky="w")

        self.trim_total_label = ctk.CTkLabel(
            trim_ticks, text="0:00", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 11), anchor="e",
        )
        self.trim_total_label.grid(row=0, column=1, sticky="e")

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

        # Bordes en las orillas
        self.scale_row = ctk.CTkFrame(card, fg_color="transparent")
        self.scale_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        self.scale_row.grid_columnconfigure(1, weight=1)
        self.scale_row.grid_remove()
        row += 1

        scale_title = ctk.CTkLabel(
            self.scale_row, text="Escala", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        scale_title.grid(row=0, column=0, sticky="w")

        self.scale_entry = self._make_pct_entry(self.scale_row)
        self.scale_entry.insert(0, "100")
        self.scale_entry.grid(row=0, column=2, padx=(0, 6))

        self.scale_value_label = ctk.CTkLabel(
            self.scale_row, text="100% · tamaño natural", text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_LIGHT, 12), anchor="e",
        )
        self.scale_value_label.grid(row=0, column=3, sticky="e")

        self.scale_slider = ctk.CTkSlider(
            self.scale_row, from_=20, to=200, number_of_steps=180,
            progress_color=GREEN, button_color=GREEN, button_hover_color=GREEN_HOVER,
            command=self._on_scale_change,
        )
        self.scale_slider.set(100)
        self.scale_slider.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        self._bind_pct_entry(self.scale_entry, self.scale_slider, 20, 200,
                             lambda v: self._on_scale_change(v))

        # Presets: guarda toda la configuracion de estilo actual (plantilla,
        # textura y sus ajustes, tamano en pantalla, velocidad) para
        # reusarla despues con otro video/imagen
        preset_row = ctk.CTkFrame(card, fg_color="transparent")
        preset_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        preset_row.grid_columnconfigure(0, weight=1)
        row += 1

        preset_title = ctk.CTkLabel(
            preset_row, text="Presets", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        preset_title.grid(row=0, column=0, columnspan=4, sticky="w")

        self.preset_menu = ctk.CTkOptionMenu(
            preset_row, values=[NO_PRESET], height=34, corner_radius=8,
            fg_color=CHIP_BG, button_color=CHIP_BG, button_hover_color=HOVER_BG,
            text_color=TEXT_DARK, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT_DARK, dropdown_hover_color=HOVER_BG,
            font=ctk.CTkFont(FONT_REGULAR, 12),
            dropdown_font=ctk.CTkFont(FONT_REGULAR, 12),
            command=self._on_preset_selected,
        )
        self.preset_menu.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.preset_save_btn = ctk.CTkButton(
            preset_row, text="💾", width=34, height=34, corner_radius=8,
            fg_color=CHIP_BG, hover_color=HOVER_BG, text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_REGULAR, 13), command=self._save_preset,
        )
        self.preset_save_btn.grid(row=1, column=1, padx=(8, 0), pady=(6, 0))

        self.preset_rename_btn = ctk.CTkButton(
            preset_row, text="✏", width=34, height=34, corner_radius=8,
            fg_color=CHIP_BG, hover_color=HOVER_BG, text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_REGULAR, 13), command=self._rename_preset,
            state="disabled",
        )
        self.preset_rename_btn.grid(row=1, column=2, padx=(8, 0), pady=(6, 0))

        self.preset_delete_btn = ctk.CTkButton(
            preset_row, text="🗑", width=34, height=34, corner_radius=8,
            fg_color=CHIP_BG, hover_color=HOVER_BG, text_color=TEXT_GRAY,
            font=ctk.CTkFont(FONT_REGULAR, 13), command=self._delete_preset,
            state="disabled",
        )
        self.preset_delete_btn.grid(row=1, column=3, padx=(8, 0), pady=(6, 0))

        preset_hint = ctk.CTkLabel(
            preset_row, text="Guarda plantilla, textura, tamaño y velocidad actuales para reusarlos después",
            text_color=TEXT_GRAY, font=ctk.CTkFont(FONT_LIGHT, 12), anchor="w",
        )
        preset_hint.grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # Nombre del archivo: reemplaza el nombre automatico (el del audio)
        # con el que escriba el usuario, sin tener que abrir el buscador.
        name_row = ctk.CTkFrame(card, fg_color="transparent")
        name_row.grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 0))
        name_row.grid_columnconfigure(0, weight=1)
        row += 1

        name_label = ctk.CTkLabel(
            name_row, text="Nombre del archivo", text_color=TEXT_DARK,
            font=ctk.CTkFont(FONT_MEDIUM, 14), anchor="w",
        )
        name_label.grid(row=0, column=0, sticky="ew")

        self.filename_entry = ctk.CTkEntry(
            name_row, height=34, corner_radius=8,
            fg_color=CARD_BG, border_color=BORDER, border_width=1,
            text_color=TEXT_DARK, placeholder_text_color=TEXT_GRAY,
            placeholder_text="se usa el nombre del audio si lo dejas vacío",
            font=ctk.CTkFont(FONT_LIGHT, 12),
        )
        self.filename_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.filename_entry.bind("<KeyRelease>", self._on_filename_entry_change)

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
        self._refresh_texture_layers_visibility()
        self._refresh_preset_menu()

        # Pegar imagen con Ctrl+V (por ejemplo copiada de Pinterest)
        self.bind_all("<Control-v>", self._paste_from_clipboard)
        self.bind_all("<Control-V>", self._paste_from_clipboard)
        if IS_MAC:  # en Mac el atajo es Cmd+V
            self.bind_all("<Command-v>", self._paste_from_clipboard)
            self.bind_all("<Command-V>", self._paste_from_clipboard)

        if IS_MAC:
            # En Tk 9 (el que trae Python 3.14) el scroll de Mac cambio:
            # la rueda del mouse manda <MouseWheel> con deltas estilo
            # Windows (multiplos de 120) y el gesto de dos dedos del
            # trackpad manda un evento NUEVO, <TouchpadScroll>, que ni
            # customtkinter ni el handler viejo escuchan. Se reemplazan
            # por handlers propios (el canvas queda con unidades de 1px).
            self.unbind_all("<MouseWheel>")
            self.bind_all("<MouseWheel>", self._on_mac_mousewheel, add=True)
            try:
                self.bind_all("<TouchpadScroll>", self._on_mac_touchpad, add=True)
            except tk.TclError:
                pass  # Tk < 9 no conoce el evento; la rueda basta
            self.card._parent_canvas.configure(yscrollincrement=1)
            self._touchpad_active_until = 0

    def _make_pct_entry(self, parent):
        """Casilla chica para escribir un numero a mano junto a un slider."""
        entry = ctk.CTkEntry(
            parent, width=44, height=26, corner_radius=6,
            fg_color=CARD_BG, border_color=BORDER, border_width=1,
            text_color=TEXT_DARK, font=ctk.CTkFont(FONT_REGULAR, 12),
            justify="center",
        )
        return entry

    def _bind_pct_entry(self, entry, slider, min_v, max_v, apply_fn):
        """Conecta la casilla con el slider: escribir un numero y darle
        Enter (o salir del campo) aplica ese valor, clampeado al rango del
        control -- alternativa a arrastrar la perilla."""
        def commit(_event=None):
            text = entry.get().strip().rstrip("%")
            try:
                value = float(text)
            except ValueError:
                value = slider.get()
            value = max(min_v, min(max_v, value))
            slider.set(value)
            apply_fn(value)
        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)

    def _set_pct_entry(self, entry, value):
        if entry.focus_get() is entry:
            return  # no pisar lo que el usuario esta escribiendo
        entry.delete(0, "end")
        entry.insert(0, str(int(round(value))))

    # ---------------------------------------------------------- apariencia

    def _toggle_appearance(self):
        mode = "dark" if self.dark_switch.get() else "light"
        # customtkinter repinta la ventana completa despues de recolorear
        # CADA widget (update_idletasks dentro de _set_appearance_mode); ese
        # repintado intermedio hace que el cambio se vea "por partes" cuando
        # el render es caro (macOS). Se suprime durante el cambio y se hace
        # un solo repintado al final.
        original_update = tk.Misc.update_idletasks
        tk.Misc.update_idletasks = lambda _self: None
        try:
            ctk.set_appearance_mode(mode)
        finally:
            tk.Misc.update_idletasks = original_update
        self.config_data["appearance"] = mode
        save_config(self.config_data)
        self.loop_slider.refresh_colors()
        self.focus_picker.refresh_colors()
        self.update_idletasks()

    # ------------------------------------------------------- scroll (macOS)

    def _scrollable_canvas_for(self, event):
        """El canvas del card si el evento cae dentro de el y hay contenido
        oculto que scrollear; si no, None."""
        try:
            if not self.card._check_if_valid_scroll(event.widget):
                return None
        except Exception:
            return None
        canvas = self.card._parent_canvas
        if canvas.yview() == (0.0, 1.0):
            return None  # todo el contenido ya esta visible
        return canvas

    def _on_mac_mousewheel(self, event):
        if event.time < getattr(self, "_touchpad_active_until", 0):
            return  # gesto de trackpad en curso; evitar scroll doble
        canvas = self._scrollable_canvas_for(event)
        if canvas is None:
            return
        try:
            delta = float(event.delta)
        except (TypeError, ValueError):
            return
        if delta == 0:
            return
        if abs(delta) >= 60:
            notches = delta / 120.0  # muescas estilo Tk 9 / Windows
        else:
            notches = delta  # formato viejo de Tk 8.6 en Mac
        # ~40px por muesca, con tope por evento para que sea suave
        pixels = max(8, min(160, int(round(abs(notches) * 40))))
        canvas.yview("scroll", -pixels if delta > 0 else pixels, "units")

    def _on_mac_touchpad(self, event):
        # <TouchpadScroll> (Tk 9): %D trae dx y dy empaquetados como dos
        # enteros de 16 bits con signo; dy viene en pixeles.
        try:
            packed = int(event.delta)
        except (TypeError, ValueError):
            return
        dy = ((packed & 0xFFFF) ^ 0x8000) - 0x8000
        if dy == 0:
            return
        self._touchpad_active_until = event.time + 500
        canvas = self._scrollable_canvas_for(event)
        if canvas is None:
            return
        canvas.yview("scroll", -dy, "units")

    # ----------------------------------------------------------- plantilla
    def _refresh_template_menu(self):
        # Conservar las plantillas externas (agregadas con "Buscar
        # archivo...", fuera de la carpeta plantillas) -- solo se vuelve a
        # escanear la carpeta administrada, no se pierden las de otro lado.
        self._template_paths = {
            display: path for display, path in self._template_paths.items()
            if os.path.dirname(path) != TEMPLATES_DIR and os.path.exists(path)
        }
        if os.path.isdir(TEMPLATES_DIR):
            for name in sorted(os.listdir(TEMPLATES_DIR)):
                if name.lower().endswith(".png"):
                    display = os.path.splitext(name)[0]
                    self._template_paths[display] = os.path.join(TEMPLATES_DIR, name)
        values = [NO_TEMPLATE] + sorted(self._template_paths.keys())
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
            self._add_template_option(path)
            self._activate_template(path)
            return
        path = self._template_paths.get(choice)
        if path:
            self._activate_template(path)

    def _add_template_option(self, path):
        """Registra 'path' en el dropdown de plantillas (si no estaba) y lo
        deja seleccionado -- sin esto, _activate_template solo actualiza el
        estado interno pero el dropdown se queda mostrando lo de antes hasta
        reiniciar la app."""
        display = os.path.splitext(os.path.basename(path))[0]
        if display not in self._template_paths:
            self._template_paths[display] = path
            values = list(self.template_menu.cget("values"))
            if display not in values:
                values.insert(-1, display)
                self.template_menu.configure(values=values)
        self.template_menu.set(display)

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
        self.template_info.configure(
            text="arrastra un .png aquí para usarlo de plantilla", text_color=TEXT_GRAY)
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
        # Quitarla del diccionario para que no reaparezca al refrescar (las
        # demas plantillas externas si se conservan)
        display = next((d for d, p in self._template_paths.items() if p == path), None)
        if display:
            self._template_paths.pop(display, None)
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
        # CTkToplevel esconde y vuelve a mostrar la ventana ~15ms despues de
        # crearla (para pintar la barra de titulo oscura en Windows) -- si
        # agendamos el render antes de eso, _preview_visible() lo ve como
        # "no visible todavia" y no pasa nada hasta tocar otro control.
        # Con este margen esperamos a que termine ese truco interno.
        self.after(80, lambda: self._schedule_preview(1))

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
        textures = self._current_textures(*layout["canvas"])

        cmd = [self.ffmpeg_exe, "-y"]
        if self.media_is_video:
            start, _end = self.loop_slider.get_values()
            if self.media_duration:
                start = min(start, max(0.0, self.media_duration - 0.5))
            if start > 0:
                cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-i", self.media_path]
        idx = 1
        tpl_idx = None
        if self.template_path:
            cmd += ["-i", self.template_path]
            tpl_idx = idx
            idx += 1
        tex_layers = []
        for path, mode, opacity in textures:
            cmd += ["-i", path]
            tex_layers.append((idx, mode, opacity))
            idx += 1
        fc = build_filtergraph(
            layout, is_video=False, tpl_idx=tpl_idx, textures=tex_layers,
            focus=self._current_focus(),
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

    def _available_texture_paths(self):
        """Dict {nombre: ruta} de texturas disponibles para cualquier capa:
        las de la carpeta administrada + las externas agregadas con
        "Buscar archivo..." (en cualquier capa, se comparten entre todas)."""
        self._texture_paths = {
            display: path for display, path in self._texture_paths.items()
            if os.path.dirname(path) != TEXTURES_DIR and os.path.exists(path)
        }
        if os.path.isdir(TEXTURES_DIR):
            for name in sorted(os.listdir(TEXTURES_DIR)):
                if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                    display = os.path.splitext(name)[0]
                    self._texture_paths[display] = os.path.join(TEXTURES_DIR, name)
        return self._texture_paths

    def _register_texture_path(self, path):
        """Agrega 'path' al diccionario compartido de texturas disponibles
        (se llama al elegir por archivo o al soltarla en una capa), asi
        cualquier otra capa tambien puede ofrecerla en su propio dropdown."""
        display = os.path.splitext(os.path.basename(path))[0]
        self._texture_paths[display] = path

    def _browse_texture_file(self):
        path = filedialog.askopenfilename(
            title="Seleccionar textura",
            filetypes=[("Imagen", " ".join(f"*{e}" for e in sorted(IMAGE_EXTS)))],
        )
        if not path:
            return None
        try:
            with Image.open(path):
                pass
        except Exception as exc:
            self._set_status(f"No se pudo leer la textura: {exc}", RED)
            return None
        self._register_texture_path(path)
        return path

    def _delete_texture_file(self, path):
        """Borra el ARCHIVO de textura (solo si vive en la carpeta
        administrada) y quita cualquier capa que lo tenga puesto."""
        if not path:
            return
        name = os.path.basename(path)
        if os.path.dirname(path) != TEXTURES_DIR:
            messagebox.showinfo(
                "No se puede eliminar",
                f"\"{name}\" no está en la carpeta administrada de texturas, "
                "así que no se borra el archivo original.\n\n"
                "Usa la ✕ de la capa para quitarla de esta composición.",
            )
            return
        if not messagebox.askyesno(
            "Eliminar textura",
            f"¿Eliminar \"{name}\" de la carpeta texturas? Se borra del disco "
            "y de cualquier capa que la esté usando.",
        ):
            return
        try:
            os.remove(path)
        except OSError as exc:
            self._set_status(f"No se pudo eliminar la textura: {exc}", RED)
            return
        display = os.path.splitext(name)[0]
        self._texture_paths.pop(display, None)
        for layer in list(self.texture_layers):
            if layer.path == path:
                self._remove_texture_layer(layer)
        for layer in self.texture_layers:
            layer.refresh_files()
        self._set_status(f"Textura eliminada: {name}", GREEN)

    def _add_texture_layer(self, state=None):
        row = TextureLayerRow(
            self.texture_layers_container,
            available_paths_fn=self._available_texture_paths,
            on_change=self._on_texture_layers_changed,
            on_remove=self._remove_texture_layer,
            on_browse=self._browse_texture_file,
            make_pct_entry=self._make_pct_entry,
            bind_pct_entry=self._bind_pct_entry,
            set_pct_entry=self._set_pct_entry,
            register_path_fn=self._register_texture_path,
            on_delete_file=self._delete_texture_file,
        )
        row.grid(row=len(self.texture_layers), column=0, sticky="ew", pady=(0, 8))
        self.texture_layers.append(row)
        if state:
            row.set_state(state)
        self._refresh_texture_layers_visibility()
        self._on_texture_layers_changed()
        return row

    def _remove_texture_layer(self, row):
        if row in self.texture_layers:
            self.texture_layers.remove(row)
        row.destroy()
        for i, remaining in enumerate(self.texture_layers):
            remaining.grid(row=i, column=0, sticky="ew", pady=(0, 8))
        self._refresh_texture_layers_visibility()
        self._on_texture_layers_changed()

    def _refresh_texture_layers_visibility(self):
        # El contenedor de capas se oculta cuando esta VACIO: un CTkFrame
        # sin hijos reserva su tamano por defecto (200px de alto) y deja
        # un hueco enorme en la seccion. Con el panel plegado no importa
        # (el cuerpo entero mide 1px), asi que aqui solo se decide entre
        # contenedor y texto de ayuda.
        if self.texture_layers:
            self.texture_layers_container.grid()
            self.texture_empty_hint.grid_remove()
        else:
            self.texture_layers_container.grid_remove()
            self.texture_empty_hint.grid()

    # ------------------------------------------- panel de texturas plegable

    def _toggle_textures_panel(self):
        self.textures_collapsed = not self.textures_collapsed
        self.config_data["textures_collapsed"] = self.textures_collapsed
        save_config(self.config_data)
        self._apply_textures_panel_state()

    def _apply_textures_panel_state(self):
        """Pliega o despliega el contenido del panel de Texturas (capas).
        Plegar NO desmonta los widgets (eso parpadea en blanco en macOS):
        solo congela la altura del cuerpo en 1px.
        El encabezado con la flecha queda siempre visible; plegado, el
        titulo resume cuantas capas hay."""
        if self.textures_collapsed:
            self.texture_body.grid_propagate(False)
            self.texture_body.configure(height=1)
            self.texture_toggle.configure(text="▶")
            n = len(self.texture_layers)
            extra = f" ({n} capa{'s' if n != 1 else ''})" if n else ""
            self.texture_title.configure(text=f"Texturas{extra}")
        else:
            self.texture_body.grid_propagate(True)
            self.texture_toggle.configure(text="▼")
            self.texture_title.configure(text="Texturas")
            self._refresh_texture_layers_visibility()

    def _expand_textures_panel(self):
        """Abre el panel si estaba plegado (al agregar una textura, para
        que el usuario vea lo que acaba de entrar)."""
        if self.textures_collapsed:
            self.textures_collapsed = False
            self.config_data["textures_collapsed"] = False
            save_config(self.config_data)
            self._apply_textures_panel_state()

    def _on_add_texture_click(self):
        self._expand_textures_panel()
        self._add_texture_layer()

    def _on_texture_layers_changed(self):
        self.config_data["textures"] = [
            state for layer in self.texture_layers if (state := layer.get_state())
        ]
        save_config(self.config_data)
        self._schedule_preview()

    def _restore_texture_from_config(self):
        layers_data = self.config_data.get("textures")
        if layers_data is None:
            # Migracion desde el formato anterior (una sola textura)
            legacy_path = self.config_data.get("texture")
            if legacy_path and os.path.exists(legacy_path):
                layers_data = [{
                    "path": legacy_path,
                    "blend": self.config_data.get("blend_mode", "Aclarar"),
                    "opacity": self.config_data.get("texture_opacity", 47),
                    "scale": self.config_data.get("texture_scale", 100),
                }]
            else:
                layers_data = []
        for state in layers_data:
            if state.get("path") and os.path.exists(state["path"]):
                self._add_texture_layer(state=state)

    def _current_textures(self, canvas_w, canvas_h):
        """Prepara (con cache) cada capa activa y devuelve la lista lista
        para build_command/build_compose_command: [(ruta, modo, opacidad)]."""
        textures = []
        for layer in self.texture_layers:
            state = layer.get_state()
            if not state:
                continue
            try:
                prepared = self._prepare_texture(state["path"], state["scale"], canvas_w, canvas_h)
            except Exception:
                prepared = state["path"]
            mode = BLEND_MODES.get(state["blend"], "lighten")
            textures.append((prepared, mode, state["opacity"] / 100.0))
        return textures

    def _prepare_texture(self, texture_path, scale_pct, canvas_w, canvas_h):
        """Genera (con cache) la textura tileada al tamano del lienzo: se
        escala a su tamano natural x el porcentaje elegido y se repite en
        mosaico, en vez de estirarla — asi el grano queda fino. Un archivo
        por textura de origen (hash de la ruta) para que varias capas no se
        pisen el cache entre si."""
        scale = int(round(scale_pct))
        key = (texture_path, scale, canvas_w, canvas_h)
        with self._texture_lock:
            cached = self._texture_cache.get(texture_path)
            if cached and cached[0] == key and os.path.exists(cached[1]):
                return cached[1]
            digest = hashlib.md5(texture_path.encode("utf-8")).hexdigest()[:10]
            path = os.path.join(tempfile.gettempdir(), f"genvideo_textura_preparada_{digest}.png")
            with Image.open(texture_path) as tex:
                tex = tex.convert("RGB")
                tile_w = max(2, round(tex.width * scale / 100))
                tile_h = max(2, round(tex.height * scale / 100))
                tile = tex.resize((tile_w, tile_h), Image.LANCZOS)
            board = Image.new("RGB", (canvas_w, canvas_h))
            for y in range(0, canvas_h, tile_h):
                for x in range(0, canvas_w, tile_w):
                    board.paste(tile, (x, y))
            board.save(path)
            self._texture_cache[texture_path] = (key, path)
            return path

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

    @staticmethod
    def _mac_clipboard_files():
        """Rutas de archivos copiados en el Finder (Cmd+C). Pillow solo
        devuelve listas de archivos en Windows; en Mac se leen del
        portapapeles nativo (NSPasteboard) via osascript."""
        script = (
            "ObjC.import('AppKit');"
            "const pb = $.NSPasteboard.generalPasteboard;"
            "const opts = $.NSDictionary.dictionaryWithObjectForKey("
            "true, 'NSPasteboardURLReadingFileURLsOnlyKey');"
            "const urls = pb.readObjectsForClassesOptions("
            "$.NSArray.arrayWithObject($.NSURL), opts);"
            "const out = [];"
            "if (urls) { for (let i = 0; i < urls.count; i++)"
            " out.push(ObjC.unwrap(urls.objectAtIndex(i).path)); }"
            "out.join('\\n');"
        )
        try:
            proc = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", script],
                capture_output=True, text=True, timeout=5,
            )
            return [p for p in proc.stdout.splitlines() if p.strip()]
        except Exception:
            return []

    def _paste_from_clipboard(self, _event=None):
        # En Mac, revisar PRIMERO si hay archivos copiados (Finder): al
        # copiar un audio, macOS tambien expone su caratula como imagen y
        # ImageGrab la devolveria antes -- pegando la imagen en lugar del
        # audio. _ingest_files ya reparte por extension (audio vs imagen).
        if IS_MAC:
            files = self._mac_clipboard_files()
            if files:
                self._ingest_files(files)
                return
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

    def _handle_template_drop(self, event):
        """Soltar un PNG sobre la seccion de Plantilla lo usa directo,
        sin pasar por el buscador de archivos."""
        for path in self.tk.splitlist(event.data):
            if path.lower().endswith(".png"):
                self._add_template_option(path)
                self._activate_template(path)
                return
        self._set_status("Suelta un archivo .png para usarlo como plantilla.", RED)

    def _handle_texture_new_drop(self, event):
        """Soltar una o mas imagenes sobre la seccion de Texturas (fuera de
        una capa existente) agrega una capa nueva por cada archivo valido."""
        added = 0
        for path in self.tk.splitlist(event.data):
            if os.path.splitext(path)[1].lower() not in IMAGE_EXTS:
                continue
            try:
                with Image.open(path):
                    pass
            except Exception:
                continue
            self._register_texture_path(path)
            self._add_texture_layer(state={"path": path, "blend": "Aclarar", "opacity": 47, "scale": 100})
            added += 1
        if not added:
            self._set_status("Suelta una imagen para agregarla como textura.", RED)
        else:
            self._expand_textures_panel()

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
                focus_src = img.copy()
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
        self.focus_picker.set_image(focus_src)
        self.focus_picker.set_scale_pct(self._current_scale_pct())
        self.focus_zoom_slider.set(100)
        self.focus_zoom_label.configure(text="Zoom 100%")
        self.focus_row.grid()
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
        self.focus_row.grid_remove()
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
            self.loop_slider.set_duration(duration)
            self.trim_total_label.configure(text=format_duration(duration) or "0:00")
            self._update_trim_readout()
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
        self.focus_row.grid_remove()
        self._refresh_ready_state()
        self._schedule_preview()

    def _remove_audio(self):
        self.audio_path = None
        self.audio_chip.grid_remove()
        self._refresh_ready_state()

    # -------------------------------------------------------- tamano en pantalla

    def _on_scale_change(self, value):
        pct = int(round(value))
        if pct == 100:
            self.scale_value_label.configure(text="100% · tamaño natural")
        elif pct < 100:
            self.scale_value_label.configure(text=f"{pct}% · con bordes")
        else:
            self.scale_value_label.configure(text=f"{pct}% · ampliada")
        self._set_pct_entry(self.scale_entry, pct)
        self.focus_picker.set_scale_pct(pct)
        self._schedule_preview()

    def _current_scale_pct(self):
        return int(round(self.scale_slider.get()))

    # ---------------------------------------------------- ajustar imagen

    def _on_focus_change(self, zoom, _focus_x, _focus_y):
        pct = round(zoom * 100)
        self.focus_zoom_slider.set(pct)
        self.focus_zoom_label.configure(text=f"Zoom {pct}%")
        self._set_pct_entry(self.focus_zoom_entry, pct)
        self._schedule_preview()

    def _on_focus_zoom_change(self, value):
        pct = int(round(value))
        self.focus_zoom_label.configure(text=f"Zoom {pct}%")
        self._set_pct_entry(self.focus_zoom_entry, pct)
        self.focus_picker.set_zoom(pct)  # dispara _on_focus_change -> _schedule_preview

    def _reset_focus(self):
        self.focus_picker.set_state(100, 0.5, 0.5)
        self.focus_zoom_slider.set(100)
        self.focus_zoom_label.configure(text="Zoom 100%")
        self._set_pct_entry(self.focus_zoom_entry, 100)
        self._schedule_preview()

    def _current_focus(self):
        if self.media_is_video or not self.media_path:
            return None
        return self.focus_picker.get_state()

    # --------------------------------------------------------- recorte del loop

    def _update_trim_readout(self):
        start, end = self.loop_slider.get_values()
        self.trim_readout.configure(
            text=f"{format_duration(start)} – {format_duration(end)}"
                 f" · {format_duration(end - start)} de loop")

    def _on_loop_change(self, _start, _end):
        self._update_trim_readout()
        self._schedule_preview(500)

    # -------------------------------------------------------------- presets

    def _refresh_preset_menu(self, select=None):
        names = [p["name"] for p in self.presets]
        has_presets = bool(names)
        self.preset_menu.configure(values=names if has_presets else [NO_PRESET])
        self.preset_menu.set(select if select in names else (names[0] if has_presets else NO_PRESET))
        state = "normal" if has_presets else "disabled"
        self.preset_rename_btn.configure(state=state)
        self.preset_delete_btn.configure(state=state)

    def _find_preset(self, name):
        return next((p for p in self.presets if p["name"] == name), None)

    def _snapshot_preset(self, name):
        return {
            "name": name,
            "template": self.template_path,
            "textures": [state for layer in self.texture_layers if (state := layer.get_state())],
            "scale_pct": self._current_scale_pct(),
            "speed": self.speed_control.get(),
        }

    def _save_preset(self):
        dialog = ctk.CTkInputDialog(text="Nombre del preset:", title="Guardar preset")
        name = (dialog.get_input() or "").strip()
        if not name:
            return
        existing = self._find_preset(name)
        if existing and not messagebox.askyesno(
            "Sobrescribir preset", f"Ya existe un preset llamado \"{name}\". ¿Sobrescribirlo?"
        ):
            return
        snapshot = self._snapshot_preset(name)
        if existing:
            self.presets[self.presets.index(existing)] = snapshot
        else:
            self.presets.append(snapshot)
        self.config_data["presets"] = self.presets
        save_config(self.config_data)
        self._refresh_preset_menu(select=name)
        self._set_status(f"Preset guardado: {name}", GREEN)

    def _on_preset_selected(self, name):
        preset = self._find_preset(name)
        if not preset:
            return
        template = preset.get("template")
        if template and os.path.exists(template):
            display = os.path.splitext(os.path.basename(template))[0]
            if display not in self._template_paths:
                self._template_paths[display] = template
                values = list(self.template_menu.cget("values"))
                values.insert(-1, display)
                self.template_menu.configure(values=values)
            self.template_menu.set(display)
            self._activate_template(template)
        else:
            self.template_menu.set(NO_TEMPLATE)
            self._deactivate_template()

        # Quitar las capas de textura actuales y reconstruir con las del preset
        for layer in list(self.texture_layers):
            self._remove_texture_layer(layer)
        textures_data = preset.get("textures")
        if textures_data is None:
            # Presets guardados antes de las capas multiples (una sola textura)
            legacy_path = preset.get("texture")
            textures_data = [{
                "path": legacy_path,
                "blend": preset.get("blend_mode", "Aclarar"),
                "opacity": preset.get("texture_opacity", 47),
                "scale": preset.get("texture_scale", 100),
            }] if legacy_path else []
        missing_textures = []
        for state in textures_data:
            path = state.get("path")
            if path and os.path.exists(path):
                self._add_texture_layer(state=state)
            elif path:
                missing_textures.append(os.path.basename(path))

        if "scale_pct" in preset:
            scale_pct = preset["scale_pct"]
        elif "border_pct" in preset:
            # Presets guardados durante la version intermedia del control
            # (0% = sin borde, 80% = borde maximo) -- se convierte a la
            # escala actual (100% = natural, bidireccional)
            scale_pct = max(20, min(200, 100 - preset["border_pct"]))
        else:
            scale_pct = 100
        self.scale_slider.set(scale_pct)
        self._on_scale_change(scale_pct)
        speed = preset.get("speed")
        if speed:
            self.speed_control.set(speed)

        missing = []
        if template and not os.path.exists(template):
            missing.append("plantilla")
        if missing_textures:
            missing.append("textura" if len(missing_textures) == 1 else "texturas")
        if missing:
            self._set_status(
                f"Preset \"{name}\" aplicado — no se encontró la {'/'.join(missing)} guardada", RED)
        else:
            self._set_status(f"Preset aplicado: {name}", GREEN)

    def _rename_preset(self):
        old_name = self.preset_menu.get()
        preset = self._find_preset(old_name)
        if not preset:
            return
        dialog = ctk.CTkInputDialog(text=f"Nuevo nombre para \"{old_name}\":", title="Renombrar preset")
        new_name = (dialog.get_input() or "").strip()
        if not new_name or new_name == old_name:
            return
        if self._find_preset(new_name):
            self._set_status(f"Ya existe un preset llamado \"{new_name}\".", RED)
            return
        preset["name"] = new_name
        self.config_data["presets"] = self.presets
        save_config(self.config_data)
        self._refresh_preset_menu(select=new_name)
        self._set_status(f"Preset renombrado a: {new_name}", GREEN)

    def _delete_preset(self):
        name = self.preset_menu.get()
        preset = self._find_preset(name)
        if not preset:
            return
        if not messagebox.askyesno("Eliminar preset", f"¿Eliminar el preset \"{name}\"?"):
            return
        self.presets.remove(preset)
        self.config_data["presets"] = self.presets
        save_config(self.config_data)
        self._refresh_preset_menu()
        self._set_status(f"Preset eliminado: {name}")

    # ------------------------------------------------------------- salida

    def _update_default_output(self):
        """El video se nombra como el "Nombre del archivo" que haya escrito
        el usuario, o si esta vacio, como la cancion -- y se guarda junto a
        ella. Si el usuario ya eligio una ruta manualmente, se respeta."""
        if self.user_chose_output:
            return
        source = self.audio_path or self.media_path
        if not source:
            return
        folder = os.path.dirname(source)
        base = self.custom_output_name.strip() or os.path.splitext(os.path.basename(source))[0]
        self.output_path = unique_output_path(folder, base)
        self.output_label.configure(text=truncate_path(self.output_path))

    def _on_filename_entry_change(self, _event=None):
        self.custom_output_name = self.filename_entry.get()
        if self.user_chose_output and self.output_path:
            # Ya eligio carpeta con "Cambiar" -- esa carpeta se respeta,
            # pero el nombre se sigue actualizando con lo que escriba (o el
            # del audio si lo deja vacio). Sin esto, escribir un nombre
            # nuevo despues de "Cambiar" no tenia ningun efecto y el video
            # se guardaba con el nombre de antes de ese click.
            source = self.audio_path or self.media_path
            default_base = (
                os.path.splitext(os.path.basename(source))[0] if source
                else os.path.splitext(os.path.basename(self.output_path))[0]
            )
            base = self.custom_output_name.strip() or default_base
            self.output_path = unique_output_path(os.path.dirname(self.output_path), base)
            self.output_label.configure(text=truncate_path(self.output_path))
        else:
            self._update_default_output()

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
            start, end = self.loop_slider.get_values()
            duration = self.media_duration
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

            textures = self._current_textures(*layout["canvas"])

            # aac_mf solo existe en Windows (MediaFoundation); en otros
            # sistemas intentarlo es un fallo garantizado de ffmpeg.
            encoders = ["aac_mf", "aac"] if os.name == "nt" else ["aac"]
            if info["audio_codec"] == "aac":
                strategies = ["copy"] + encoders
            else:
                strategies = encoders

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
                        template_path=template_path, textures=textures,
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
                        layout, template_path=template_path, textures=textures,
                        focus=self._current_focus(),
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
