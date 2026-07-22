"""
Backend puro del Generador de Video: deteccion de plantillas, sondeo de
medios, armado de comandos ffmpeg y utilidades de config/formato. Sin
ninguna dependencia de UI (ni tkinter ni pywebview) -- las mismas
funciones sirven para la version customtkinter (app.py) y para la nueva
UI en pywebview (webapp/).
"""

import json
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image

MAX_WIDTH = 1920
MAX_HEIGHT = 1080

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".gif"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".opus"}

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
IS_MAC = sys.platform == "darwin"

# En Mac, el Python de python.org no trae certificados SSL configurados y
# toda conexion HTTPS falla (CERTIFICATE_VERIFY_FAILED) -- sin esto la
# descarga por link con yt-dlp dependeria de que yt-dlp encuentre certifi
# por su cuenta. Se configura aqui (modulo compartido) para que cualquier
# UI que importe el motor quede cubierta.
if IS_MAC:
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass

# Empaquetada (PyInstaller): las carpetas del usuario (plantillas, texturas,
# config) van junto al ejecutable para que sean faciles de encontrar y editar.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

TEMPLATES_DIR = os.path.join(APP_DIR, "plantillas")
TEXTURES_DIR = os.path.join(APP_DIR, "texturas")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

# Modos de fusion (nombres de Photoshop -> modo del filtro blend de ffmpeg)
BLEND_MODES = {
    "Normal": "normal",
    "Aclarar": "lighten",
    "Trama": "screen",
    "Multiplicar": "multiply",
    "Superponer": "overlay",
    "Luz suave": "softlight",
}

# Calidad/peso del video: veryfast comprime ~2x mejor que ultrafast casi a
# la misma velocidad. El tope de bitrate evita que el grano/textura disparen
# el peso sin limite (CRF solo, en escenas muy detalladas, puede pasar de
# 700 MB en un clip de 3 min) -- pero un tope muy ajustado (12 Mbps) ahoga
# al encoder en escenas con mucho movimiento o grano, perdiendo nitidez
# frente al original. Con 18 Mbps de techo y CRF 18 hay mas margen para esas
# escenas sin disparar el peso en el resto del video (que sigue dominado por
# CRF, no por el tope). El peso final tambien escala con la duracion de la
# cancion (el video hace loop hasta el final del beat) -- eso no lo controla
# ningun ajuste de codificacion.
VIDEO_QUALITY_ARGS = [
    "-preset", "veryfast", "-crf", "18",
    "-maxrate", "18M", "-bufsize", "36M",
]

# Para el preview del loop (request_loop_preview): se descarta apenas se ve,
# asi que no necesita la calidad de VIDEO_QUALITY_ARGS -- ultrafast es varias
# veces mas rapido que veryfast, y sin tope de bitrate el encoder no gasta
# tiempo en control de tasa que aca no importa. crf 28 se notaba borroneado
# (texto/overlays sobre todo) -- 24 se ve bastante mejor sin cambiar la
# velocidad de forma perceptible (el preset, no el crf, es lo que domina el
# tiempo de encode).
PREVIEW_QUALITY_ARGS = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "24"]

# NVENC (encoder por GPU, NVIDIA) para el mismo preview cuando hay una
# tarjeta que lo soporte -- Api._loop_preview_job prueba esto primero y cae
# a PREVIEW_QUALITY_ARGS (CPU) si falla. Bastante mas rapido que libx264
# incluso en su preset mas lento (dedicado en hardware, no compite por los
# mismos nucleos que el resto de la app), lo que deja margen para pedirle
# mejor calidad (cq mas bajo) sin perder el tiempo ganado.
PREVIEW_QUALITY_ARGS_NVENC = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "21"]


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


def template_canvas_box(template_path, box):
    """Convierte el rectangulo detectado (en pixeles de la plantilla) a
    coordenadas del lienzo 1920x1080. Una plantilla que no es 1920x1080 se
    escala para caber completa y se CENTRA (igual que la dibuja el
    filtergraph); su ventana transparente debe transformarse con la misma
    escala y el mismo desplazamiento para que el medio caiga donde toca."""
    with Image.open(template_path) as img:
        w, h = img.size
    if (w, h) == (MAX_WIDTH, MAX_HEIGHT):
        return box
    factor = min(MAX_WIDTH / w, MAX_HEIGHT / h)
    off_x = (MAX_WIDTH - int(round(w * factor))) // 2
    off_y = (MAX_HEIGHT - int(round(h * factor))) // 2
    x, y, bw, bh = box
    new_w = max(16, int(round(bw * factor)) // 2 * 2)
    new_h = max(16, int(round(bh * factor)) // 2 * 2)
    new_x = max(0, min(MAX_WIDTH - new_w, off_x + int(round(x * factor))))
    new_y = max(0, min(MAX_HEIGHT - new_h, off_y + int(round(y * factor))))
    return new_x, new_y, new_w, new_h


def probe_media(ffmpeg_exe, media_path):
    """Lee la cabecera del archivo y devuelve un dict con duration,
    audio_codec, sample_rate, video_size (w, h)."""
    proc = subprocess.run(
        [ffmpeg_exe, "-i", media_path],
        stdin=subprocess.DEVNULL,
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
        stdin=subprocess.DEVNULL,
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
        stdin=subprocess.DEVNULL,
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
        stdin=subprocess.DEVNULL,
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
#
# peak_db (opcional, el mismo valor que measure_peak_db ya calcula para el
# aviso de "puede clipear" en la UI): si el pico del ORIGEN pasa de la
# franja segura, se agrega un "-af volume=Xdb" que baja el volumen entero
# lo justo para que el pico mas alto quede debajo de 0 dBFS ANTES de
# codificar. Sin esto, un audio con picos por encima de 0 dB (comun en wav
# flotante -- ahi no truena porque el float no tiene techo real) SI se
# aplasta contra el techo real de AAC al codificar: medido con un archivo
# real, un pico de origen a +3 dB perdia 2.4 dB de pico y el crest factor
# (la diferencia entre el golpe de un kick y el resto) se achicaba 2.3 dB
# -- exactamente lo que suena como "el kick quedo comprimido/aplastado".
# Bajar el volumen entero de forma pareja ANTES de codificar evita ese
# aplastamiento sin tocar la dinamica relativa (mismo crest factor, +-0.01
# dB, verificado) -- el resultado suena igual de punchy, solo mas bajito.
# No aplica a "copy": ahi no hay filtro ni recodificacion, se copian los
# bytes tal cual (si el archivo de origen ya viene aplastado, eso paso
# antes, en quien lo exporto por primera vez -- no es algo que este
# programa pueda arreglar sin alterar el archivo).
def audio_strategy_args(strategy, sample_rate, peak_db=None):
    if strategy == "copy":
        return ["-c:a", "copy"]
    if sample_rate in (44100, 48000):
        rate = sample_rate
    else:
        rate = 48000
    args = ["-c:a", strategy, "-b:a", "320k", "-ar", str(rate)]
    safety_margin_db = 1.0
    if peak_db is not None and peak_db > -safety_margin_db:
        gain_db = -(peak_db + safety_margin_db)
        args = ["-af", f"volume={gain_db:.2f}dB"] + args
    return args


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
    """Recorte manual de "Ajustar imagen", aplicado ANTES de todo lo demas.
    Recorta un CUADRADO de lado min(iw,ih)/zoom colocado con (focus_x,
    focus_y) en 0..1 -- exactamente el recuadro que dibuja la UI. Antes se
    recortaba iw/zoom x ih/zoom (proporcion de la foto) y luego el pipeline
    centraba el cuadrado: en fotos rectangulares eso impedia mover el
    recuadro fuera del centro sin hacer mucho zoom. zoom=1.0 y foco
    centrado -> no-op (mismo resultado que el recorte centrado de siempre),
    asi que no cambia nada para quien no toque el ajuste."""
    zoom, focus_x, focus_y = focus
    zoom = max(1.0, zoom)
    if zoom == 1.0 and focus_x == 0.5 and focus_y == 0.5:
        return ""
    side = f"min(iw\\,ih)/{zoom:.4f}"
    crop_x = f"(iw-({side}))*{focus_x:.4f}"
    crop_y = f"(ih-({side}))*{focus_y:.4f}"
    return f"crop={side}:{side}:{crop_x}:{crop_y},"


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
        # La plantilla se escala para caber completa en el lienzo y se
        # CENTRA (relleno transparente alrededor) -- una plantilla que no
        # es 1920x1080 antes quedaba pegada a la esquina superior
        # izquierda. template_canvas_box aplica la misma transformacion al
        # rectangulo de la ventana.
        parts.append(
            f"[{tpl_idx}:v]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
            f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black@0[tplfit]"
        )
        parts.append(f"[{last}][tplfit]overlay=0:0[tpld]")
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
                          deinterlace=False, template_path=None, textures=None, fast=False,
                          encoder="libx264"):
    """FASE 1 (solo clips de video): compone UNA sola vuelta del loop —
    recorte + velocidad + texturas + escala/bordes o plantilla — en un mp4
    corto sin audio, a 30 fps constantes y con GOP cerrado para que la
    fase 2 pueda repetirlo con copia directa sin glitches.

    fast=True (preview del loop, ver request_loop_preview) usa
    PREVIEW_QUALITY_ARGS en vez de VIDEO_QUALITY_ARGS -- el resultado se ve
    y se tira, no hace falta la calidad de la exportacion real. Con
    fast=True, encoder="h264_nvenc" pide PREVIEW_QUALITY_ARGS_NVENC (GPU)
    en vez del libx264 de siempre -- el caller (Api._loop_preview_job)
    decide esto probando si la GPU responde, no hay deteccion aca."""
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
    if fast:
        quality_args = PREVIEW_QUALITY_ARGS_NVENC if encoder == "h264_nvenc" else PREVIEW_QUALITY_ARGS
    else:
        quality_args = ["-c:v", "libx264", *VIDEO_QUALITY_ARGS]
    cmd += [
        "-filter_complex", fc, "-map", "[vout]",
        "-an",
        *quality_args, "-pix_fmt", "yuv420p",
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


def unique_output_path(folder, base, ext=".mp4"):
    """Devuelve una ruta que no exista todavia (agrega _2, _3, ...)."""
    candidate = os.path.join(folder, f"{base}{ext}")
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{base}_{counter}{ext}")
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
