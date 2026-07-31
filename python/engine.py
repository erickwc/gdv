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

# Lienzo de salida. 1440p y no 1080p por como reencodea YouTube, que es el
# unico destino de estos videos: hasta 1080p te da AVC a ~5 Mbps; de 1440p
# para arriba pasa a VP9/AV1 y reparte MUCHO mas bitrate. Medido sobre el caso
# real (clip descargado por link + textura de grano + fondo oscuro saturado),
# el archivo local ya se veia bien a 1080p y aun asi subido se veia sucio: el
# cuello de botella no era el encode de aca sino el codec que elige YouTube.
#
# Ademas los clips que baja el descargador vienen a 1440p (ver el format de
# yt-dlp en api.py, height<=1440): a 1080p se estaba tirando resolucion que ya
# se tenia -- el recorte cuadrado pasaba de 1440x1440 a 1080x1080 antes de
# encodear.
#
# Costo: una plantilla de 1920x1080 se amplia al lienzo (template_canvas_box
# ya la escala y centra), asi que su texto ablanda un poco. Se arregla
# exportando la plantilla a 2560x1440 desde el editor, no hace falta tocar
# nada de codigo.
MAX_WIDTH = 2560
MAX_HEIGHT = 1440

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
# El tope se escala junto con el lienzo (ver MAX_WIDTH): a 1080p este crf daba
# ~11 Mbps, muy por debajo de los 18 permitidos -- o sea el tope no llegaba a
# actuar, que es justo su papel (frenar un archivo desbocado, no recortar
# contenido normal). A 1440p son 1.78x los pixeles y el MISMO material pide
# ~20 Mbps: dejando 18M el tope pasaria a morder siempre, metiendo a x264 en
# modo VBV y ahogandolo. 32M/64M mantiene el mismo margen relativo que habia.
VIDEO_QUALITY_ARGS = [
    "-preset", "veryfast", "-crf", "18",
    "-maxrate", "32M", "-bufsize", "64M",
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

# ---------------------------------------------------- copia liviana del medio
#
# El previsualizador en vivo (live-preview.js) decodifica el archivo ORIGINAL
# cuadro a cuadro. Con un clip 4K eso es descomprimir 8.3 megapixeles treinta
# veces por segundo para mostrarlos en un recuadro de unos 500 px de pantalla:
# medido, se pierden fotogramas (10 de 186 en un stock 4K de 30 fps contra 3 de
# 184 en el mismo material a 1080p) y el video se ve a los tirones. Antes no
# pasaba porque lo que se reproducia era el fragmento que componia ffmpeg, a
# 0.5-0.7 del lienzo.
#
# La copia se hace UNA vez por archivo, en segundo plano, y no cambia con el
# recorte, la velocidad ni las texturas -- todo eso lo compone el canvas encima.
# Mientras no este lista se sigue usando el original.
#
# El tamano lo decide preview_proxy_size: lo MINIMO que puede cubrir el lienzo
# sin que el previsualizador tenga que agrandar nada. La exportacion NO usa esta
# copia, sigue leyendo el archivo original.


def preview_proxy_size(media_size, canvas=(MAX_WIDTH, MAX_HEIGHT)):
    """Tamano de la copia, o None si no vale la pena hacerla.

    La regla es "lo mas chica posible SIN que haya que agrandarla despues". El
    medio se dibuja CUBRIENDO su caja, que como mucho es el lienzo entero, asi
    que la copia tiene que poder cubrir 2560x1440: el factor es el MAYOR de los
    dos lados, no el menor.

    Primero se probo con 1080 de alto fijo, pensando en que en pantalla se ve
    chico igual. Fue un error y se nota enseguida: la caja del lienzo mide hasta
    1440, asi que el medio pasaba de reducirse (nitido) a AGRANDARSE 1.33x, y el
    navegador agranda con un filtro pobre. Encima la textura y la plantilla se
    siguen dibujando a resolucion completa, asi que el medio blando al lado del
    grano nitido cantaba todavia mas."""
    w, h = media_size
    if not w or not h:
        return None
    factor = max(canvas[0] / w, canvas[1] / h)
    if factor >= 1:
        return None  # ya es igual o mas chico que lo que hace falta
    # Pares: yuv420p no admite lados impares.
    return (max(2, round(w * factor / 2) * 2), max(2, round(h * factor / 2) * 2))


def build_preview_proxy_command(ffmpeg_exe, media_path, temp_path, size):
    """Copia del video escalada para el previsualizador. Mismos fps y misma
    duracion que el original -- el previsualizador y el panel de portada
    trabajan con tiempos, y tienen que valer igual en los dos archivos."""
    return [
        ffmpeg_exe, "-y", "-i", media_path,
        "-vf", f"scale={size[0]}:{size[1]}:flags=bicubic",
        # Sin audio: el beat va por su lado y el <video> del preview esta mudo.
        "-an",
        # veryfast y crf 18, no mas rapido: esto se MIRA. ultrafast deja el
        # archivo antes (y se decodifica mas barato, porque no lleva CABAC) pero
        # apaga el filtro de deblocking, y los bloques se ven -- sobre todo en
        # degradados, que es la mitad del material que se usa aca. Medido sobre
        # un 4K de 20s, decodificando en un solo hilo, mejor de tres:
        #     4K original            10.60 s
        #     copia a 1440p veryfast  4.27 s   2.5x mas barata
        #     copia a 1080p veryfast  2.45 s   4.3x mas barata (pero hay que
        #                                      agrandarla: se ve blanda)
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        # Fotograma clave cada segundo: en esta copia se BUSCA mucho (la barra
        # del preview, el selector de fotograma de la portada), y con un GOP
        # largo cada salto obliga a decodificar desde muy atras.
        "-g", "30", "-keyint_min", "30",
        "-pix_fmt", "yuv420p",
        temp_path,
    ]


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
        # El alfa no siempre aparece en getbands(): un PNG INDEXADO (modo "P",
        # lo que exportan Figma/Photoshop como "PNG-8" y lo que dejan los
        # compresores tipo TinyPNG) guarda su transparencia en el chunk tRNS,
        # y ahi getbands() devuelve ("P",) a secas. Mirar solo las bandas daba
        # esas plantillas por opacas y las rechazaba con "no tiene zona
        # transparente" aunque SI tuvieran ventana. Se decide sobre el alfa ya
        # resuelto por convert("RGBA"), que aplica paleta y tRNS por igual.
        if "A" not in img.getbands() and "transparency" not in img.info:
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


def build_layout(media_size, is_video, scale_pct, template_box, crop_aspect=1.0):
    """Decide el plan de composicion: tamano interno del medio, lienzo final
    y posicion. Devuelve (layout, ancho_final, alto_final).

    crop_aspect es la proporcion (ancho/alto) del recuadro que dejo "Ajustar
    imagen" -- 1.0 con el recorte cuadrado de siempre, y de ahi el valor por
    defecto: con un cuadrado la cuenta da exactamente lo mismo que antes."""
    if template_box:
        x, y, w, h = template_box
        layout = {"mode": "template", "inner": (w, h), "canvas": (MAX_WIDTH, MAX_HEIGHT), "pos": (x, y)}
    else:
        # Sin plantilla el lienzo siempre es negro y la base (scale_pct=100)
        # tiene el ALTO completo del lienzo; el ancho sale de la proporcion
        # del recuadro de "Ajustar imagen". Con el recorte cuadrado de
        # siempre (crop_aspect=1) eso da el cuadrado fijo de toda la vida,
        # igual para cualquier archivo que se cargue, sin importar su tamano
        # ni su proporcion. Con un recuadro vertical la caja se angosta en la
        # misma medida, que es lo que hace que el modo "Vertical" conserve la
        # foto entera: si la caja siguiera siendo cuadrada, el scale de mas
        # abajo ampliaria para cubrirla y volveria a comerse el alto.
        #
        # La altura se queda fija siempre -- el control de escala solo mueve
        # el ANCHO (a partir del centro): por debajo de 100% encoge y deja
        # bordes SOLO a los lados; por encima de 100% amplia, recortando lo
        # que sobre (sin deformar) conforme se acerca a llenar el lienzo. Una
        # foto mas apaisada que el lienzo topa con MAX_WIDTH y ahi si pierde
        # los costados: mas ancho que el lienzo no hay.
        natural_h = MAX_HEIGHT
        natural_w = MAX_HEIGHT * max(0.01, crop_aspect)
        scale_factor = max(0.01, scale_pct / 100)
        inner_w = max(2, min(MAX_WIDTH, int(natural_w * scale_factor)) // 2 * 2)
        layout = {"mode": "bordered", "inner": (inner_w, natural_h),
                  "canvas": (MAX_WIDTH, MAX_HEIGHT), "pos": None}
    return layout, layout["canvas"][0], layout["canvas"][1]


def build_focus_crop(crop):
    """Recorte manual de "Ajustar imagen", aplicado ANTES de todo lo demas.

    crop es el recuadro que se ve en la UI, en fracciones 0..1 del original:
    (x, y, ancho, alto). Antes esto sabia de CUADRADOS y nada mas (lado
    min(iw,ih)/zoom mas un punto de foco), que es todo lo que el modo
    "Cuadrado" necesita; con "Vertical" el recuadro toma la proporcion de la
    foto, que puede ser cualquiera, asi que ahora se guarda el rectangulo
    completo y la cuenta se vuelve directa.

    La foto entera (0,0,1,1) es el modo "Vertical" y devuelve "": meter un
    crop que no recorta nada solo agrega trabajo al filtro.

    OJO: recortar aca no alcanza para que un recuadro no-cuadrado sobreviva.
    Lo que sigue en la cadena escala para CUBRIR la caja de composicion y
    recorta el sobrante, asi que si la caja no toma la misma proporcion que
    este recuadro, el alto que se acaba de respetar se pierde de nuevo -- de
    eso se encarga build_layout con crop_aspect."""
    x, y, w, h = crop
    w = max(0.01, min(1.0, w))
    h = max(0.01, min(1.0, h))
    x = max(0.0, min(1.0 - w, x))
    y = max(0.0, min(1.0 - h, y))
    if w >= 0.9999 and h >= 0.9999:
        return ""
    return f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"


def content_box(layout):
    """Rectangulo (x, y, ancho, alto) del lienzo donde cae el MEDIO, haya
    plantilla o no.

    Con plantilla es su ventana transparente (layout["pos"]). SIN plantilla el
    cuadro igual existe: es el que dejan los bordes negros, centrado -- el
    mismo centrado que hace el pad de build_filtergraph, de ahi la cuenta
    repetida. Antes esto solo se sabia para el caso con plantilla
    (template_box), y por eso guardar "solo el cuadro" no se podia ofrecer sin
    una: no habia de donde sacar el recorte. Lo usan save_cover para recortar
    y get_state para rotular la opcion en la UI."""
    inner_w, inner_h = layout["inner"]
    canvas_w, canvas_h = layout["canvas"]
    if layout["pos"]:
        x, y = layout["pos"]
    else:
        x = (canvas_w - inner_w) // 2
        y = (canvas_h - inner_h) // 2
    return x, y, inner_w, inner_h


def build_filtergraph(layout, is_video=False, speed=1.0, deinterlace=False,
                      tpl_idx=None, textures=None, focus=None):
    """Arma el filter_complex completo: medio (des-entrelazado + velocidad +
    ajuste manual de encuadre + escala/recorte/bordes) -> texturas mezcladas
    encima (una sobre otra, en el orden de la lista) -> plantilla encima.
    Los clips de video salen a 30 fps constantes para que el loop por copia
    directa sea perfectamente uniforme (sin glitches).

    textures: lista de (indice_de_entrada, modo_ffmpeg, opacidad_0_a_1,
    escala_si_es_video). El ultimo campo es None cuando la textura es una
    imagen: ahi la escala ya viene horneada en el mosaico que arma
    Api._prepare_texture. Cuando la textura es un VIDEO no hay mosaico
    posible (PIL no lo abre), asi que llega la ruta cruda y la escala se
    aplica aca como zoom sobre el fotograma."""
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
    for i, (tex_idx, tex_mode, tex_opacity, tex_video_scale) in enumerate(textures or []):
        texs_label = f"texs{i}"
        basef_label = f"basef{i}"
        out_label = f"textured{i}"
        if tex_video_scale is None:
            # Imagen: ya viene tileada al tamano del lienzo.
            tex_chain = (
                f"scale={inner_w}:{inner_h}:force_original_aspect_ratio=increase,"
                f"crop={inner_w}:{inner_h}"
            )
            # blend por defecto repite el ultimo fotograma del segundo
            # stream (repeatlast) -- por eso una imagen de un solo frame
            # alcanza para todo el clip.
            blend_extra = ""
        else:
            # Video: la escala es un zoom sobre el fotograma (no un mosaico),
            # y nunca por debajo del 100% -- mas chico que el lienzo dejaria
            # huecos, y repetir un video en mosaico no se puede con un solo
            # filtro. El fps fijo evita que un clip de 24 o 60 tenga que
            # ajustarlo framesync a mitad de la mezcla.
            zoom = max(100, int(round(tex_video_scale))) / 100.0
            zw, zh = int(round(inner_w * zoom)), int(round(inner_h * zoom))
            fps_part = "fps=30," if is_video else ""
            tex_chain = (
                f"{fps_part}scale={zw}:{zh}:force_original_aspect_ratio=increase,"
                f"crop={inner_w}:{inner_h}"
            )
            # La entrada viene con -stream_loop -1 (ver build_command /
            # build_compose_command): sin shortest la mezcla esperaria a que
            # termine un stream infinito y el encode no cerraria nunca.
            blend_extra = ":shortest=1"
        parts.append(f"[{tex_idx}:v]{tex_chain},format=gbrp[{texs_label}]")
        parts.append(f"[{last}]format=gbrp[{basef_label}]")
        # "Normal" es la excepcion entre los seis modos. En los otros cinco la
        # opacidad va del medio HACIA la mezcla (0 = medio solo, 1 = mezcla
        # entera) y coinciden con el previsualizador; en normal el filtro hace
        #     dst = A*opacidad + B*(1-opacidad)
        # o sea que la opacidad se le aplica a la PRIMERA entrada, que aca es el
        # medio. Con el control al 100% el video se exportaba SIN NADA de
        # textura, y subirlo la iba borrando en vez de marcarla. Pasando
        # 1-opacidad queda medio*(1-op) + textura*op, que es exactamente lo que
        # dibuja el canvas (globalAlpha = opacidad + source-over, ver
        # dibujarTextura en live-preview.js). Medido contra el previsualizador
        # en 0, 25, 47, 55, 60 y 100: diferencia 0.
        #
        # Lo obvio seria dar vuelta las entradas ([texs][basef]) y dejar la
        # opacidad como esta. NO se hace, por dos motivos, los dos medidos:
        #   - la PRIMERA entrada manda el tiempo (framesync) y una textura que
        #     es imagen tiene un solo fotograma: el video entero saldria de un
        #     cuadro (por eso el repeatlast del comentario de arriba).
        #   - en los otros cinco modos la primera entrada tambien hace de base
        #     de la formula -- overlay y softlight eligen la rama mirandola --
        #     asi que darla vuelta para todos los rompe: medido, la diferencia
        #     contra el previsualizador salta de ~0.2 a 39-67 sobre 255.
        blend_opacity = 1.0 - tex_opacity if tex_mode == "normal" else tex_opacity
        parts.append(
            f"[{basef_label}][{texs_label}]"
            f"blend=all_mode={tex_mode}:all_opacity={blend_opacity:.3f}{blend_extra}[{out_label}]"
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
    # Con una textura de VIDEO encima, la imagen deja de ser un fotograma
    # quieto: el conjunto tiene que correr a 30 fps o la textura avanzaria un
    # frame por segundo (se veria a tirones). Cuesta mas encodear, pero es lo
    # que hay: sin movimiento no tiene sentido usar un video de textura.
    has_video_texture = any(t[3] is not None for t in (textures or []))
    base_fps = "30" if has_video_texture else "1"
    out_fps = "30" if has_video_texture else "10"
    gop = "60" if has_video_texture else "20"
    cmd = [ffmpeg_exe, "-y", "-loop", "1", "-framerate", base_fps, "-i", media_path,
           "-i", audio_path]
    idx = 2
    tpl_idx = None
    if template_path:
        cmd += ["-i", template_path]
        tpl_idx = idx
        idx += 1
    tex_layers = []
    for path, mode, opacity, video_scale in (textures or []):
        # -stream_loop -1 va ANTES de su -i: repite la textura todo lo que
        # dure el video (los clips de textura suelen ser de pocos segundos).
        if video_scale is not None:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", path]
        tex_layers.append((idx, mode, opacity, video_scale))
        idx += 1
    fc = build_filtergraph(
        layout, is_video=False, tpl_idx=tpl_idx, textures=tex_layers, focus=focus,
    )
    cmd += [
        "-filter_complex", fc, "-map", "[vout]", "-map", "1:a:0",
        "-r", out_fps,
        "-c:v", "libx264", *VIDEO_QUALITY_ARGS,
        # Keyframe cada 2s -- ver el comentario igual en
        # build_compose_command sobre por que hace falta esto para YouTube,
        # aunque aca la imagen no cambie entre keyframes.
        "-g", gop, "-keyint_min", gop,
        "-pix_fmt", "yuv420p", "-tune", "stillimage",
        *audio_args,
        "-shortest",
    ]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", output_path]
    return cmd


# Una imagen quieta se exporta como una unidad corta repetida por COPIA
# directa (fase 1 + fase 2, igual que un clip de video) en vez de encodear el
# video entero. Medido sobre un beat de 2:26 a 2560x1440, con el mismo
# veryfast crf 18 en todos los casos:
#
#   una pasada, keyframe cada 2s (lo de antes): 13.4 s | 100.2 MB | 73 keyframes
#   una pasada, keyframe cada 25s:              10.3 s |  10.5 MB |  6 keyframes
#   unidad de 25s + copia, keyframe cada 25s:    2.8 s |   9.6 MB |  6 keyframes
#
# Son DOS palancas distintas y conviene no confundirlas (se confundieron una
# vez ya):
#
# 1. EL TIEMPO lo decide la unidad repetida, no el keyframe. Los 13 s se iban
#    en pasar el filtergraph -- escalar, recortar, plantilla, texturas -- por
#    los 1460 fotogramas de a uno. Procesando 250 y clonando el resto: 4.8x.
#    Bajar solo el keyframe, sin la unidad, apenas ahorraba 3 s.
# 2. EL PESO lo decide cada cuanto va un fotograma clave. Un keyframe es la
#    imagen entera comprimida; los del medio, con la foto quieta, son todos
#    "no cambio nada" y no ocupan practicamente nada. Los 73 keyframes de
#    1440p eran los 100 MB.
#
# El GOP largo no cambia NADA de lo que se ve: entre keyframes no hay nada que
# pueda cambiar, y decodificar fotogramas vacios es instantaneo. La guia de
# subida de YouTube pide uno cada 2s, pero eso apunta a material CON
# movimiento -- aca es plata tirada, y es decision explicita del usuario
# despues de ver estos numeros. Los clips de VIDEO no se tocan: siguen con su
# keyframe cada 2s en build_compose_command.
STILL_UNIT_SECONDS = 25.0
STILL_FPS = 10


def build_still_unit_command(ffmpeg_exe, media_path, temp_path, layout, seconds,
                             template_path=None, textures=None, focus=None):
    """FASE 1 para imagenes fijas: compone una unidad corta
    (STILL_UNIT_SECONDS) en un mp4 sin audio, con GOP cerrado y un solo
    fotograma-clave, lista para que la fase 2 la repita por copia directa
    hasta cubrir el beat."""
    cmd = [ffmpeg_exe, "-y", "-loop", "1", "-framerate", "1", "-i", media_path]
    idx = 1
    tpl_idx = None
    if template_path:
        cmd += ["-i", template_path]
        tpl_idx = idx
        idx += 1
    tex_layers = []
    for path, mode, opacity, video_scale in (textures or []):
        cmd += ["-i", path]
        tex_layers.append((idx, mode, opacity, video_scale))
        idx += 1
    fc = build_filtergraph(
        layout, is_video=False, tpl_idx=tpl_idx, textures=tex_layers, focus=focus,
    )
    # Un unico fotograma-clave por unidad: el GOP acompana a la unidad, asi
    # que el video final termina con uno cada STILL_UNIT_SECONDS.
    gop = str(max(1, int(round(seconds * STILL_FPS))))
    cmd += [
        "-filter_complex", fc, "-map", "[vout]", "-an",
        "-t", f"{seconds:.3f}", "-r", str(STILL_FPS),
        "-c:v", "libx264", *VIDEO_QUALITY_ARGS,
        "-g", gop, "-keyint_min", gop,
        "-pix_fmt", "yuv420p", "-tune", "stillimage",
        # GOP cerrado y misma escala de tiempos que build_compose_command,
        # para que el concat de la fase 2 no deje saltos entre repeticiones.
        "-flags", "+cgop",
        "-video_track_timescale", "15360",
        "-progress", "pipe:1", "-nostats",
        temp_path,
    ]
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
    for path, mode, opacity, video_scale in (textures or []):
        # Ver el comentario en build_command: -stream_loop -1 antes del -i
        # para que una textura de 3 segundos cubra un loop de 20.
        if video_scale is not None:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", path]
        tex_layers.append((idx, mode, opacity, video_scale))
        idx += 1
    fc = build_filtergraph(
        layout, is_video=True, speed=speed, deinterlace=deinterlace,
        tpl_idx=tpl_idx, textures=tex_layers,
    )
    if fast:
        quality_args = PREVIEW_QUALITY_ARGS_NVENC if encoder == "h264_nvenc" else PREVIEW_QUALITY_ARGS
        gop_args = []
    else:
        quality_args = ["-c:v", "libx264", *VIDEO_QUALITY_ARGS]
        # Keyframe cada 2s (60 frames a los 30 fps fijos de salida, ver
        # fps=30 en build_filtergraph) -- sin esto libx264 solo mete
        # keyframes por cambio de escena, que en un clip sin cortes puede
        # dejar 7-8s de por medio. YouTube reencodea TODO lo que subis (no
        # sirve tu bitrate/CRF tal cual), y su propia guia de subida pide
        # un keyframe cada 2s -- un GOP mas largo que eso le sale peor
        # incluso partiendo de un archivo local que ya se ve bien. Como la
        # fase 2 solo repite esta unidad por copia directa, el GOP de aca
        # es el del video final entero.
        gop_args = ["-g", "60", "-keyint_min", "60"]
    cmd += [
        "-filter_complex", fc, "-map", "[vout]",
        "-an",
        *quality_args, *gop_args, "-pix_fmt", "yuv420p",
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
