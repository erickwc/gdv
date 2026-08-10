"""
Puente Python <-> JS para la UI de Electron. Es la misma clase Api que en la
version pywebview (webapp/api.py del proyecto original), adaptada solo en las
partes que dependian de una ventana de pywebview:

- Los dialogos nativos (abrir/guardar archivo) ahora los abre Electron (main
  process) -- los metodos que aca los disparaban (browse_media,
  browse_template, browse_texture_file, choose_output_path) se quitaron; en
  su lugar Electron llama directo a los metodos "puros" que ya existian
  (ingest_paths, set_template, add_texture_layer) o al nuevo
  set_chosen_output().
- El drag-and-drop (antes registrado en Python via window.dom, para leer
  pywebviewFullPath) ahora se maneja en el renderer con
  webUtils.getPathForFile, asi que _setup_dom_events y compania se quitaron.
- Los avisos hacia JS (antes window.evaluate_js) se mandan con self._emit(),
  que el sidecar (sidecar.py) conecta a una linea JSON por stdout.

Toda la logica de negocio (estado, ingesta de medios, texturas, presets,
generacion, previsualizacion, portapapeles, descarga por link) es identica a
la version pywebview.
"""

import base64
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from io import BytesIO

import imageio_ffmpeg
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402  (import despues del sys.path.insert de arriba)

NO_TEMPLATE = None  # en la UI vieja era el string "Sin plantilla" del dropdown

# Tamano de ajuste del canvas de "Ajustar imagen" -- el mismo que usaba el
# widget de Tk (ImageFocusPicker.DISPLAY_W/H). app.js replica esta misma
# geometria pixel a pixel, asi que el numero debe coincidir en ambos lados.
FOCUS_PICKER_W = 352
FOCUS_PICKER_H = 200


def _image_to_data_uri(img, fmt="PNG"):
    """Convierte una imagen PIL a data URI para mandarla a JS sin pasar por
    un archivo temporal ni por ImageTk (que no existe fuera de tkinter)."""
    buf = BytesIO()
    img.convert("RGB").save(buf, fmt)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return f"data:{mime};base64,{encoded}"


def _thumb_for(path, cache, ffmpeg_exe=None):
    """Miniatura chica (data URI) para una tarjeta de galeria, con cache en
    memoria por ruta -- list_templates()/list_available_textures() se
    llaman seguido (cada refresh) y releer+reescalar el archivo cada vez
    seria trabajo de disco de sobra.

    Una textura puede ser un video (ver add_texture_layer): PIL no los abre,
    asi que con ffmpeg_exe se saca el primer fotograma como miniatura."""
    cached = cache.get(path)
    if cached is not None:
        return cached
    uri = None
    try:
        if ffmpeg_exe and os.path.splitext(path)[1].lower() in engine.VIDEO_EXTS:
            frame = engine.extract_video_thumb(ffmpeg_exe, path, max_px=96)
            if frame is not None:
                uri = _image_to_data_uri(frame)
        else:
            with Image.open(path) as img:
                thumb = img.convert("RGB").copy()
            thumb.thumbnail((96, 96))
            uri = _image_to_data_uri(thumb)
    except Exception:
        uri = None
    cache[path] = uri
    return uri


class Api:
    def __init__(self, emit=None):
        # emit(event_name, data): callback que manda el evento a JS -- lo
        # conecta sidecar.py a una linea JSON por stdout. Sin sidecar (por
        # ejemplo, pruebas manuales) no hace nada.
        self._emit_fn = emit or (lambda event, data: None)

        self.config_data = engine.load_config()
        self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

        self.media_path = None
        self.media_is_video = False
        # Tamano COMO VIENE EL ARCHIVO. Lo que consume el resto de la app es
        # la propiedad media_size (mas abajo), que ya tiene el giro aplicado.
        self.media_size_raw = None
        # Girar/espejar son propiedades del proyecto, NO un archivo nuevo:
        # el click es instantaneo (no hay ffmpeg de por medio), el
        # previsualizador las aplica al dibujar (ver dibujarMedio en
        # live-preview.js) y se hornean recien al exportar (ver
        # build_transform_filters en engine.py), que ya recodifica igual.
        # Antes cada click recodificaba el clip entero y habia que esperarlo.
        self.media_rotation = 0       # 0/90/180/270, en sentido horario
        self.media_flip_h = False     # espejo izquierda-derecha
        self.media_flip_v = False     # espejo arriba-abajo
        self.media_video_codec = None  # ver CHEAP_DECODE_CODECS en engine.py
        self.media_duration = None
        self.media_interlaced = False
        self.media_thumb = None       # data URI o None
        self.media_kind_text = None   # texto descriptivo del chip
        # Copia escalada del video para el previsualizador (ver
        # build_preview_proxy_command en engine.py). None = se usa el original:
        # o el clip ya es chico, o la copia todavia se esta armando.
        self.preview_proxy_path = None
        # Handle del ffmpeg que arma esa copia, mientras esta corriendo (ver
        # _preview_proxy_job/_cancel_preview_proxy_process) -- para poder
        # matarlo si deja de tener sentido: cambio el medio de nuevo, o
        # arranco una exportacion real, que NUNCA usa esta copia (sale
        # siempre de media_path) y no tiene por que compartir CPU con ella.
        self._preview_proxy_process = None
        self.media_display_name = None  # titulo de yt-dlp, si se descargo por link
        self.trim_range = None        # (start, end) calculado al generar, o None (clip completo)
        self.trim_start = 0.0
        self.trim_end = 1.0

        # Ajustar imagen -- solo aplica a fotos. El recuadro elegido va como
        # rectangulo (x, y, ancho, alto) en fracciones 0..1 del original;
        # antes eran zoom + punto de foco, que solo sabia describir cuadrados.
        # crop_mode es cual de los dos cortes esta puesto y lo unico que hace
        # es decidir como se puede arrastrar el recuadro en la UI:
        #   "cuadrado" -- se mueve y se agranda, pero siempre cuadrado
        #   "vertical" -- la foto entera, sin recorte y sin nada que arrastrar
        self.media_focus_preview = None  # data URI de la miniatura para el canvas
        self.crop_mode = "cuadrado"
        self.crop_rect = [0.0, 0.0, 1.0, 1.0]

        self.audio_path = None
        self.audio_kind_text = None
        self.audio_clip_warning = None  # True/False/None (None = sin medir todavia)
        self.audio_peak_db = None  # pico real en dB -- ver audio_strategy_args en engine.py
        # Copia del beat en un formato que el <audio> del previsualizador pueda
        # tocar (ver needs_audio_preview_proxy en engine.py). None = se usa el
        # original: o el codec ya es compatible, o la copia se esta armando.
        self.audio_preview_proxy_path = None

        self.output_path = None
        self.user_chose_output = False
        self.custom_output_name = ""
        # True mientras el nombre de salida lo ponga la app (sale del beat, ver
        # _set_audio); pasa a False en cuanto el usuario escribe uno propio.
        self._output_name_is_auto = True

        self._template_paths = {}
        self._template_lock = threading.Lock()
        self.template_path = None
        self.template_box = None

        self._texture_paths = {}
        # Lock DISTINTO de _texture_lock (que protege _texture_cache, el
        # render tileado -- otro dato). Sin este, agregar una textura mientras
        # onStateChanged dispara list_available_textures() en otro hilo podia
        # perder la recien agregada: la reconstruccion de _refresh_available_
        # textures() terminaba DESPUES y pisaba el dict entero, exactamente el
        # bug que _template_lock ya evita del lado de plantillas (ver el
        # comentario de _refresh_template_list) -- a este lado nunca se le
        # habia sumado la misma proteccion.
        self._texture_paths_lock = threading.Lock()
        self.texture_layers = []  # lista de {"path","blend","opacity","scale"}
        self._texture_cache = {}
        self._texture_lock = threading.Lock()

        self.presets = [
            p for p in self.config_data.get("presets", [])
            if isinstance(p, dict) and p.get("name")
        ]

        self.scale_pct = 100
        self.speed = "1x"

        self.process = None
        self.cancel_requested = False
        self._generating = False  # ver start_generation -- evita clics repetidos disparando 2 generaciones a la vez
        self._downloading = False  # ver download_from_link -- mismo caso, con la descarga por link
        self._generation_counter = 0  # sufijo unico para los temporales de _run_ffmpeg_job (ver ahi)
        self._download_counter = 0  # sufijo unico para el archivo bajado (ver _download_video)
        self._last_progress_emit_ts = 0.0
        self._last_ffmpeg_error = None  # tail de stderr del ultimo fallo real (ver _run_ffmpeg)

        # Portada: aparece SOLO justo despues de exportar un video con
        # exito (se pone en True en _on_job_done) y se apaga en cuanto se
        # carga una imagen/video nueva (_set_image/_set_video/remove_media)
        # -- asi el boton de al lado de "Generar video" no se queda
        # ofreciendo la portada de un video que ya no corresponde a lo que
        # esta cargado ahora.
        self.cover_available = False

        # Previsualizador en vivo (fotograma real de ffmpeg)
        self._preview_token = None
        self._preview_counter = 0
        self._last_preview_data_uri = None

        # Previsualizador del loop (solo video) -- compone nada mas la
        # FASE 1 de la generacion real (una vuelta del loop, sin audio),
        # ver request_loop_preview.
        self._loop_preview_token = None
        self._loop_preview_counter = 0
        self._loop_preview_path = None
        # Sufijo unico para el PNG de cada imagen pegada como portada -- ver
        # clipboard_image_path (la ruta no puede repetirse).
        self._cover_paste_counter = 0
        # Mismo motivo que _cover_paste_counter, para paste_from_clipboard
        # (pegar una imagen como MEDIO principal, no como portada).
        self._media_paste_counter = 0
        # None = todavia no se probo: _loop_preview_job intenta NVENC (GPU)
        # primero y cae a libx264 si falla, y recuerda el resultado aca para
        # no volver a perder tiempo probando NVENC en cada preview si esta
        # PC no tiene una GPU que lo soporte.
        self._loop_preview_nvenc_available = None

        # Miniaturas de galeria (Plantilla/Texturas) -- cache en memoria
        # para no releer/reescalar el archivo en cada list_templates()/
        # list_available_textures() (se llaman seguido, en cada refresh).
        self._template_thumb_cache = {}
        self._texture_thumb_cache = {}

        self._restore_template_from_config()
        self._restore_textures_from_config()

    def _emit(self, event, data=None):
        """Empuja un evento a JS -- reemplazo de self._window.evaluate_js(...)
        de la version pywebview. sidecar.py conecta esto a una linea JSON por
        stdout; el preload/event-bridge del lado Electron lo redespacha como
        window.onXxx(data), igual que hacia pywebview."""
        self._emit_fn(event, data)

    def _notify_state_changed(self):
        """Empuja el estado actual a JS -- reemplazo de self.after(0, ...)
        para los resultados de trabajo en threads (sondeo de video/audio,
        descargas, generacion)."""
        self._emit("onStateChanged", self.get_state())

    # --------------------------------------------------- giro / espejo
    #
    # Ver media_rotation en __init__: son propiedades del proyecto, no una
    # copia girada del archivo.

    @property
    def media_size(self):
        """Tamano EFECTIVO, con el giro ya aplicado -- lo que ve el usuario.

        Es propiedad y no un atributo suelto a proposito: TODO el resto de
        la app (el layout de composicion, el recuadro de "Ajustar imagen",
        el texto del chip, la exportacion) razona sobre el medio como se
        ve, y asi ninguno de esos lugares puede olvidarse de aplicar el
        giro. Quien necesita el tamano crudo del archivo -- solo la copia
        liviana del previsualizador, que se hace SOBRE el archivo sin girar
        -- usa media_size_raw."""
        if not self.media_size_raw:
            return None
        w, h = self.media_size_raw
        if self.media_rotation % 180 == 90:
            return (h, w)
        return (w, h)

    def _transform_filters(self):
        """Prefijo de filtros ffmpeg para hornear el giro/espejo al exportar."""
        return engine.build_transform_filters(
            self.media_rotation, self.media_flip_h, self.media_flip_v)

    def _reset_transform(self):
        """Medio nuevo = giro/espejo de cero (los del anterior no aplican)."""
        self.media_rotation = 0
        self.media_flip_h = False
        self.media_flip_v = False

    def _refresh_after_transform(self):
        """Lo que hay que rehacer despues de girar/espejar: el rotulo del
        chip (dice el tamano, que con un giro se da vuelta), la miniatura
        del canvas de "Ajustar imagen" (tiene que verse como el medio que
        se esta encuadrando) y, si el giro cambio la proporcion, el
        recuadro de recorte -- el de antes quedaria mal proporcionado,
        igual que al cargar una foto nueva (ver _set_image)."""
        self._refresh_media_kind_text()
        if not self.media_is_video:
            self._refresh_focus_preview()
            self.crop_rect = self._centered_rect(self.crop_mode)
        self._notify_state_changed()

    def rotate_media(self):
        """Gira 90 grados en sentido horario. Instantaneo: solo mueve el
        angulo del proyecto -- el archivo no se toca."""
        if not self.media_path:
            return {"ok": False, "error": "No hay medio cargado"}
        self.media_rotation = (self.media_rotation + 90) % 360
        self._refresh_after_transform()
        return {"ok": True, "state": self.get_state()}

    def flip_media_horizontal(self):
        """Espejo izquierda-derecha. Instantaneo, igual que rotate_media."""
        if not self.media_path:
            return {"ok": False, "error": "No hay medio cargado"}
        self.media_flip_h = not self.media_flip_h
        self._refresh_after_transform()
        return {"ok": True, "state": self.get_state()}

    def flip_media_vertical(self):
        """Espejo arriba-abajo. Instantaneo, igual que rotate_media."""
        if not self.media_path:
            return {"ok": False, "error": "No hay medio cargado"}
        self.media_flip_v = not self.media_flip_v
        self._refresh_after_transform()
        return {"ok": True, "state": self.get_state()}

    # ------------------------------------------------------ estado hacia JS

    def _content_box(self):
        """(x, y, ancho, alto) del recuadro donde cae el medio, para get_state.
        None mientras no haya medio (todavia no hay layout que calcular)."""
        if not self.media_size:
            return None
        template_box = self.template_box if self.template_path else None
        layout, _, _ = engine.build_layout(
            self.media_size, self.media_is_video, self.scale_pct, template_box,
            crop_aspect=self.current_crop_aspect(),
        )
        return list(engine.content_box(layout))

    def get_state(self):
        return {
            "media_path": self.media_path,
            "media_filename": self.media_display_name or (
                os.path.basename(self.media_path) if self.media_path else None),
            "media_is_video": self.media_is_video,
            # Tamano EFECTIVO, con el giro aplicado (ver la propiedad).
            "media_size": self.media_size,
            # Giro/espejo del proyecto -- el previsualizador los aplica al
            # dibujar (ver dibujarMedio en live-preview.js). El archivo no
            # se toca: se hornean recien al exportar.
            "media_rotation": self.media_rotation,
            "media_flip_h": self.media_flip_h,
            "media_flip_v": self.media_flip_v,
            # Para que el previsualizador sepa si el codec de ESTE medio es
            # caro de decodificar (ver CHEAP_DECODE_CODECS en engine.py) --
            # el ambilight lo usa para saber si conviene pausarse mientras
            # no haya copia liviana lista, ver live-preview.js.
            "media_video_codec": self.media_video_codec,
            "media_duration": self.media_duration,
            "media_interlaced": self.media_interlaced,
            "media_thumb": self.media_thumb,
            "media_kind_text": self.media_kind_text,
            # De donde saca los fotogramas el previsualizador. Casi siempre es
            # el propio medio; con un clip grande (4K) es la copia liviana, que
            # se decodifica mucho mas barato -- ver _preview_proxy_job. La
            # EXPORTACION nunca la usa: sale siempre de media_path.
            "preview_path": self.preview_proxy_path or self.media_path,
            "media_focus_preview": self.media_focus_preview,
            "crop_mode": self.crop_mode,
            "crop_rect": list(self.crop_rect),
            "audio_path": self.audio_path,
            "audio_preview_path": self.audio_preview_proxy_path or self.audio_path,
            "audio_filename": os.path.basename(self.audio_path) if self.audio_path else None,
            "audio_kind_text": self.audio_kind_text,
            "audio_clip_warning": self.audio_clip_warning,
            "output_path": self.output_path,
            "custom_output_name": self.custom_output_name,
            "template_path": self.template_path,
            "template_box": self.template_box,
            # Tamano del lienzo de salida, para que la UI lo muestre en vez de
            # tenerlo escrito a mano (ver renderTemplateInfo en app.js): estaba
            # fijo en "1920x1080" y quedo mintiendo al pasar el lienzo a 1440p.
            "canvas_size": [engine.MAX_WIDTH, engine.MAX_HEIGHT],
            # Recuadro donde cae el medio (ver engine.content_box). Con
            # plantilla coincide con template_box; sin plantilla es el cuadro
            # centrado que dejan los bordes -- por eso la UI puede ofrecer
            # "solo el cuadro" siempre, no solo con plantilla.
            "content_box": self._content_box(),
            "texture_layers": list(self.texture_layers),
            "textures_collapsed": bool(self.config_data.get("textures_collapsed", False)),
            "presets": [p["name"] for p in self.presets],
            "scale_pct": self.scale_pct,
            "speed": self.speed,
            "trim_start": self.trim_start,
            "trim_end": self.trim_end,
            "cover_available": self.cover_available,
            # banderas derivadas -- equivalente a _refresh_ready_state()
            "ready": bool(self.media_path and self.audio_path),
            "show_focus": bool(self.media_path) and not self.media_is_video,
            "show_scale": bool(self.media_path) and not self.template_path,
            "show_trim": bool(self.media_path) and self.media_is_video,
            "show_speed": bool(self.media_path) and self.media_is_video,
        }

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
        self.output_path = engine.unique_output_path(folder, base)

    def set_output_name(self, name):
        """Se llama cada vez que el usuario escribe en 'Nombre del archivo'."""
        self.custom_output_name = name or ""
        # Escribio algo a mano: de aca en adelante ese nombre es suyo y cargar
        # otro beat no lo pisa. Si lo borra, vuelve a ser automatico.
        self._output_name_is_auto = not self.custom_output_name.strip()
        if self.user_chose_output and self.output_path:
            # Ya eligio carpeta con "Guardar como" -- esa carpeta se
            # respeta, pero el nombre se sigue actualizando con lo que
            # escriba (o el del audio si lo deja vacio).
            source = self.audio_path or self.media_path
            default_base = (
                os.path.splitext(os.path.basename(source))[0] if source
                else os.path.splitext(os.path.basename(self.output_path))[0]
            )
            base = self.custom_output_name.strip() or default_base
            self.output_path = engine.unique_output_path(os.path.dirname(self.output_path), base)
        else:
            self._update_default_output()
        return self.output_path

    def set_chosen_output(self, path):
        """Aplica la ruta elegida en el dialogo nativo 'Guardar como' -- el
        dialogo en si lo abre Electron (dialog.showSaveDialog); aca solo se
        guarda el resultado, igual que hacia choose_output_path() en la
        version pywebview despues de llamar a create_file_dialog.

        El dialogo nativo YA deja escribir un nombre de archivo ahi mismo
        -- sin esto, "Nombre del video" se quedaba con el nombre viejo
        (o vacio) aunque el usuario acabara de escribir uno nuevo al
        elegir donde guardar. Se sincroniza para que ambos campos
        muestren siempre el mismo nombre."""
        self.output_path = path
        self.user_chose_output = True
        self.custom_output_name = os.path.splitext(os.path.basename(path))[0]
        return self.output_path

    # ----------------------------------------------------------- plantilla

    def _refresh_template_list(self):
        # sidecar.py despacha cada llamada RPC en su propio hilo, y
        # onStateChanged llama list_templates() despues de CADA cambio de
        # estado -- sin este lock, una reconstruccion en vuelo (disparada
        # por un drop anterior) podia terminar despues de que set_template()
        # ya habia agregado la entrada nueva, y pisarla al reemplazar todo
        # el dict. Eso hacia que la plantilla recien soltada pareciera no
        # agregarse (hasta que, por suerte de timing, un intento ganaba la
        # carrera). Con el lock, refresh/set/delete quedan serializados.
        #
        # SIN os.path.exists() (a proposito): esto se llama en CADA refresh
        # (onStateChanged dispara list_templates() todo el tiempo), asi que
        # filtrar por existencia aca borraba una plantilla de disco externo
        # apenas el disco se desconectaba un instante -- y como este dict es
        # la fuente de template_library al guardar, la perdida quedaba
        # permanente. Si el archivo de verdad no esta, falla solo al leerlo
        # (la miniatura, o al exportar), sin tocar lo guardado.
        with self._template_lock:
            self._template_paths = {
                display: path for display, path in self._template_paths.items()
                if os.path.dirname(path) != engine.TEMPLATES_DIR
            }
            if os.path.isdir(engine.TEMPLATES_DIR):
                for name in sorted(os.listdir(engine.TEMPLATES_DIR)):
                    if name.lower().endswith(".png"):
                        display = os.path.splitext(name)[0]
                        self._template_paths[display] = os.path.join(engine.TEMPLATES_DIR, name)

    def list_templates(self):
        self._refresh_template_list()
        with self._template_lock:
            active = None
            if self.template_path:
                active = next((d for d, p in self._template_paths.items() if p == self.template_path), None)
            templates = [
                {"name": d, "path": p, "thumb": _thumb_for(p, self._template_thumb_cache)}
                for d, p in sorted(self._template_paths.items())
            ]
        return {"templates": templates, "active": active}

    def _restore_template_from_config(self):
        """A diferencia de las texturas (que reconstruyen _texture_paths a
        partir de texture_layers, la lista de capas activas), una plantilla
        sola puede estar puesta a la vez -- no hay una lista de "capas" de
        donde derivar que otras se agregaron antes. Sin guardar esa lista
        aparte (template_library), cambiar de A a B y cerrar la app hacia
        que A desapareciera de la galeria para siempre: nunca quedaba
        escrita en ningun lado, solo vivia en memoria mientras la app
        seguia abierta -- exactamente lo que el usuario reporto como "las
        plantillas no se quedan guardadas".

        NO se filtra por os.path.exists() aca: el usuario guarda sus
        plantillas/texturas en un disco externo, y justo despues de
        prender la Mac ese disco puede tardar en montarse -- si se
        descartara la entrada por "no existe" en ese momento, quedaba
        BORRADA para siempre en cuanto algo mas (agregar/quitar otra)
        volviera a guardar la galeria ya filtrada. os.path.exists() solo
        importa al usar el archivo de verdad (leer la miniatura, exportar);
        ahi ya falla solo, sin arruinar lo guardado. Sacar la entrada de la
        galeria es cosa del usuario, con el boton de eliminar -- nunca
        automatico."""
        library = self.config_data.get("template_library") or []
        with self._template_lock:
            for path in library:
                display = os.path.splitext(os.path.basename(path))[0]
                self._template_paths[display] = path
        saved = self.config_data.get("template")
        if saved:
            self.set_template(saved)

    def set_template(self, path):
        # Chequeo aparte ANTES de intentar leerla: sin esto, un archivo que
        # no existe (tipico de un disco externo desconectado) caia en el
        # except de abajo y mostraba la excepcion cruda de Python
        # ("[Errno 2] No such file or directory: ...") tal cual en la UI --
        # confuso para alguien que no programa. file_missing=True le avisa al
        # frontend que este mensaje puntual se puede hacer desaparecer solo
        # despues de un rato (ver toggleTemplate en app.js), a diferencia de
        # otros errores (PNG sin zona transparente, etc.) que se quedan hasta
        # que el usuario haga otra cosa.
        if not os.path.exists(path):
            return {
                "ok": False,
                "error": f"No se pudo encontrar tu archivo :(\nRuta: {path}",
                "file_missing": True,
            }
        try:
            box = engine.detect_template_window(path)
        except Exception:
            return {"ok": False, "error": "No se pudo leer la plantilla, intenta con una nueva (ɔ◔‿◔)"}
        if box is None:
            return {
                "ok": False,
                "error": "Esa plantilla no tiene zona transparente — el medio no se vería. "
                         "Usa un PNG con transparencia.",
            }
        display = os.path.splitext(os.path.basename(path))[0]
        with self._template_lock:
            self._template_paths[display] = path
            library = list(self._template_paths.values())
        self.template_path = path
        # box viene en pixeles de la plantilla; a coordenadas del lienzo
        # (plantillas que no son 1920x1080 se escalan y CENTRAN)
        self.template_box = engine.template_canvas_box(path, box)
        self.config_data["template"] = path
        # La galeria ENTERA, no solo la activa -- ver _restore_template_from_config.
        self.config_data["template_library"] = library
        engine.save_config(self.config_data)
        return {"ok": True, "template_path": path, "template_box": box}

    def clear_template(self):
        self.template_path = None
        self.template_box = None
        self.config_data["template"] = None
        engine.save_config(self.config_data)
        return {"ok": True}

    def delete_template_file(self, path):
        """Borra el archivo de plantilla (solo si vive en la carpeta
        administrada plantillas/) y la desactiva si era la que estaba
        puesta. Version por-ruta (a diferencia de la vieja delete_template,
        que solo borraba la plantilla ACTIVA) para poder borrar cualquier
        tarjeta de la galeria, no solo la seleccionada. La confirmacion
        'seguro que quieres borrar' vive en la UI (modal HTML), no aca."""
        if os.path.dirname(path) == engine.TEMPLATES_DIR:
            try:
                os.remove(path)
            except OSError:
                return {"ok": False, "error": "El archivo no se pudo borrar (ϑ`'-'´)ϑ"}
        with self._template_lock:
            display = next((d for d, p in self._template_paths.items() if p == path), None)
            if display:
                self._template_paths.pop(display, None)
            library = list(self._template_paths.values())
        self._template_thumb_cache.pop(path, None)
        self.config_data["template_library"] = library
        engine.save_config(self.config_data)
        if self.template_path == path:
            self.clear_template()
        return {"ok": True}

    # ------------------------------------------------------------ texturas

    def _refresh_available_textures(self):
        # SIN os.path.exists() (a proposito, ver el mismo comentario en
        # _refresh_template_list): esto corre en CADA refresh de estado, asi
        # que filtrar por existencia aca borraba una textura de disco
        # externo apenas el disco se desconectaba un instante -- y como este
        # dict alimenta la galeria (y potencialmente lo que se guarda al
        # tocar cualquier otra), la perdida quedaba permanente. Si el
        # archivo de verdad no esta, falla solo al leerlo (miniatura,
        # exportar), sin arruinar lo guardado.
        with self._texture_paths_lock:
            self._texture_paths = {
                display: path for display, path in self._texture_paths.items()
                if os.path.dirname(path) != engine.TEXTURES_DIR
            }
            if os.path.isdir(engine.TEXTURES_DIR):
                for name in sorted(os.listdir(engine.TEXTURES_DIR)):
                    # Imagenes Y videos: hay texturas que son clips (grano de
                    # pelicula, fugas de luz) -- ver _texture_is_video.
                    if os.path.splitext(name)[1].lower() in (engine.IMAGE_EXTS | engine.VIDEO_EXTS):
                        display = os.path.splitext(name)[0]
                        self._texture_paths[display] = os.path.join(engine.TEXTURES_DIR, name)

    def list_available_textures(self):
        self._refresh_available_textures()
        with self._texture_paths_lock:
            return [
                {"name": d, "path": p,
                 "thumb": _thumb_for(p, self._texture_thumb_cache, self.ffmpeg_exe)}
                for d, p in sorted(self._texture_paths.items())
            ]

    @staticmethod
    def _texture_is_video(path):
        return os.path.splitext(path)[1].lower() in engine.VIDEO_EXTS

    def _texture_is_readable(self, path):
        """True si el archivo sirve como textura: cualquier imagen que abra
        PIL (con transparencia o sin ella -- a diferencia de la PLANTILLA,
        que si necesita zona transparente, ver set_template) o cualquier
        video que ffmpeg pueda leer."""
        if self._texture_is_video(path):
            info = engine.probe_media(self.ffmpeg_exe, path)
            return bool(info.get("video_size"))
        try:
            with Image.open(path):
                pass
        except Exception:
            return False
        return True

    def register_texture_path(self, path):
        """Valida un archivo elegido en el dialogo nativo de Electron y lo
        registra como textura disponible -- equivalente a lo que hacia
        browse_texture_file() en la version pywebview antes de abrir el
        dialogo (que aca ya abrio Electron). Devuelve la ruta, o None si no
        es una imagen ni un video legible."""
        if not self._texture_is_readable(path):
            return None
        display = os.path.splitext(os.path.basename(path))[0]
        with self._texture_paths_lock:
            self._texture_paths[display] = path
        return path

    def set_textures_collapsed(self, collapsed):
        self.config_data["textures_collapsed"] = bool(collapsed)
        engine.save_config(self.config_data)
        return {"ok": True}


    def _restore_textures_from_config(self):
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
        # Sin repetidas: un config guardado antes de este arreglo puede traer la
        # misma textura dos veces, y esa copia de mas no se podia ni ver ni
        # apagar desde la galeria (ver _dedupe_layers). Asi se cura sola al
        # abrir la app.
        #
        # NO se filtra por os.path.exists() aca (a proposito, ver el mismo
        # comentario en _restore_template_from_config): un disco externo
        # recien montado en el boot de la Mac puede tardar en aparecer, y
        # filtrar en ese momento + guardar el resultado (como hacia esto
        # antes) borraba la textura de config.json PARA SIEMPRE -- exacto
        # lo que el usuario reporto ("apague la mac... las texturas... no
        # estaban"). Si el archivo de verdad no esta disponible, falla solo
        # al intentar usarlo (miniatura, exportar) sin arruinar lo guardado.
        utiles = [dict(state) for state in layers_data if state.get("path")]
        self.texture_layers = self._dedupe_layers(utiles)
        # Y se deja curado en el archivo, no solo en memoria: si no, el config
        # se quedaba con las repetidas hasta que algo mas lo reescribiera, y
        # leerlo confundia (mostraba dos capas donde la app usaba una). Esto
        # SI es seguro guardarlo de una: dedupe_layers solo saca copias
        # exactas de la MISMA ruta, nunca una que solo parezca faltar.
        if len(self.texture_layers) != len(layers_data):
            self._persist_texture_layers()
        # add_texture_layer() registra el archivo en _texture_paths (asi
        # aparece en la galeria, ver list_available_textures) -- restaurar
        # texture_layers directo del config, como arriba, se saltaba ese
        # registro. Los ajustes (opacidad/escala) igual se veian bien al
        # reabrir el preset porque esos salen de texture_layers, pero la
        # textura en si no aparecia en la galeria (ni la tarjeta activa)
        # porque list_available_textures() no la conocia todavia.
        with self._texture_paths_lock:
            for state in self.texture_layers:
                path = state["path"]
                display = os.path.splitext(os.path.basename(path))[0]
                self._texture_paths[display] = path

    def _persist_texture_layers(self):
        self.config_data["textures"] = self.texture_layers
        engine.save_config(self.config_data)

    @staticmethod
    def _same_file_key(path):
        """Clave para saber si dos capas son EL MISMO archivo. Comparar la
        cadena pelada no alcanza: la misma textura puede llegar escrita
        distinto -- "C:/Users/..." o "C:\\Users\\...", y en Windows ademas con
        otras mayusculas -- y asi se colaban dos capas del mismo archivo, que es
        justo lo que _dedupe_layers tiene que evitar."""
        return os.path.normcase(os.path.normpath(path))

    @staticmethod
    def _dedupe_layers(layers):
        """UNA capa por archivo. La galeria dibuja una tarjeta por textura
        disponible y la enciende buscando la PRIMERA capa con esa ruta (ver
        renderTextureGallery en app.js), asi que una segunda capa del mismo
        archivo quedaba invisible: la tarjeta no la representaba, apagarla
        quitaba solo una, y la textura seguia puesta -- de hecho aplicada DOS
        veces, sumando el efecto. Y como el panel de ajustes tambien va contra
        la primera, esa segunda copia no habia forma de controlarla.

        Se conserva la primera, que es la que tiene los ajustes que se vinieron
        tocando."""
        vistas = set()
        unicas = []
        for capa in layers:
            ruta = capa.get("path")
            if not ruta:
                continue
            clave = Api._same_file_key(ruta)
            if clave in vistas:
                continue
            vistas.add(clave)
            unicas.append(capa)
        return unicas

    def _layer_index(self, path):
        clave = self._same_file_key(path)
        return next((i for i, c in enumerate(self.texture_layers)
                     if c.get("path") and self._same_file_key(c["path"]) == clave), -1)

    def add_texture_layer(self, path):
        # Chequeo aparte ANTES de _texture_is_readable, mismo motivo que en
        # set_template: un archivo que no existe (disco externo
        # desconectado) daba el mismo "no se pudo leer" generico que un
        # archivo realmente corrupto, sin decir POR QUE. file_missing=True
        # avisa al frontend que este mensaje se puede borrar solo.
        if not os.path.exists(path):
            return {
                "ok": False,
                "error": f"No se pudo encontrar tu archivo :(\nRuta: {path}",
                "file_missing": True,
            }
        if not self._texture_is_readable(path):
            return {"ok": False, "error": "No se pudo leer esa textura (¿imagen o video válido?)."}
        display = os.path.splitext(os.path.basename(path))[0]
        with self._texture_paths_lock:
            self._texture_paths[display] = path
        # Ya puesta: no se agrega una segunda vez (ver _dedupe_layers). Devuelve
        # ok igual, asi soltar de nuevo una textura que ya estaba simplemente la
        # deja seleccionada en vez de no hacer nada visible.
        if self._layer_index(path) == -1:
            self.texture_layers.append(
                {"path": path, "blend": "Aclarar", "opacity": 47, "scale": 100})
            self._persist_texture_layers()
        return {"ok": True, "texture_layers": self.texture_layers}

    def update_texture_layer(self, index, fields):
        if not (0 <= index < len(self.texture_layers)):
            return {"ok": False, "error": "Indice de capa invalido"}
        self.texture_layers[index].update(fields)
        self._persist_texture_layers()
        return {"ok": True, "texture_layers": self.texture_layers}

    def remove_texture_layer(self, index):
        if not (0 <= index < len(self.texture_layers)):
            return {"ok": False, "error": "Indice de capa invalido"}
        self.texture_layers.pop(index)
        self._persist_texture_layers()
        return {"ok": True, "texture_layers": self.texture_layers}

    def delete_texture_file(self, path):
        """Quita la textura de la APP: sale de la lista de disponibles y de
        cualquier capa que la use. El archivo solo se borra del disco si es
        la copia administrada dentro de la carpeta texturas/ -- un archivo
        del usuario (Descargas, etc.) jamas se toca. Antes esto devolvia un
        error para archivos externos y el boton parecia no hacer nada."""
        if os.path.dirname(path) == engine.TEXTURES_DIR:
            try:
                os.remove(path)
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
        display = os.path.splitext(os.path.basename(path))[0]
        with self._texture_paths_lock:
            self._texture_paths.pop(display, None)
        self._texture_thumb_cache.pop(path, None)
        self.texture_layers = [layer for layer in self.texture_layers if layer["path"] != path]
        self._persist_texture_layers()
        return {"ok": True, "texture_layers": self.texture_layers}

    # ------------------------------------------------------------ presets

    def _find_preset(self, name):
        return next((p for p in self.presets if p["name"] == name), None)

    def _snapshot_preset(self, name):
        return {
            "name": name,
            "template": self.template_path,
            "textures": list(self.texture_layers),
            "scale_pct": self.scale_pct,
            "speed": self.speed,
        }

    def list_presets(self):
        return [p["name"] for p in self.presets]

    def save_preset(self, name):
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "El nombre no puede estar vacío"}
        snapshot = self._snapshot_preset(name)
        existing = self._find_preset(name)
        if existing:
            self.presets[self.presets.index(existing)] = snapshot
        else:
            self.presets.append(snapshot)
        self.config_data["presets"] = self.presets
        engine.save_config(self.config_data)
        return {"ok": True, "presets": self.list_presets(), "overwritten": bool(existing)}

    def apply_preset(self, name):
        preset = self._find_preset(name)
        if not preset:
            return {"ok": False, "error": f'No existe el preset "{name}"'}

        missing = []
        template = preset.get("template")
        if template and os.path.exists(template):
            self.set_template(template)
        else:
            self.clear_template()
            if template:
                missing.append("plantilla")

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
        # Se agregan TODAS (exista o no el archivo ahora mismo) -- lo unico
        # que os.path.exists() decide aca es si va al aviso de "faltantes".
        # Antes, la que no existiera en ESE instante se descartaba y la
        # linea de abajo (_persist_texture_layers) guardaba esa lista ya
        # recortada: aplicar un preset con el disco externo desconectado
        # borraba esa textura del preset para siempre en el primer guardado
        # que tocara texture_layers. Mismo caso que el de plantillas
        # (_restore_template_from_config) y el de arranque
        # (_restore_textures_from_config) -- si el archivo de verdad no
        # esta, falla solo al usarlo, sin arruinar lo guardado.
        self.texture_layers = []
        for state in textures_data:
            path = state.get("path")
            if not path:
                continue
            self.texture_layers.append(dict(state))
            if not os.path.exists(path):
                missing.append(os.path.basename(path))
        # Un preset guardado antes del arreglo de las repetidas puede traer la
        # misma textura dos veces -- ver _dedupe_layers.
        self.texture_layers = self._dedupe_layers(self.texture_layers)
        self._persist_texture_layers()

        if "scale_pct" in preset:
            self.scale_pct = preset["scale_pct"]
        elif "border_pct" in preset:
            # Presets guardados durante la version intermedia del control
            # (0% = sin borde, 80% = borde maximo) -- se convierte a la
            # escala actual (100% = natural, bidireccional)
            self.scale_pct = max(20, min(200, 100 - preset["border_pct"]))
        else:
            self.scale_pct = 100
        if preset.get("speed"):
            self.speed = preset["speed"]

        return {"ok": True, "state": self.get_state(), "missing": missing}

    def rename_preset(self, old_name, new_name):
        preset = self._find_preset(old_name)
        if not preset:
            return {"ok": False, "error": f'No existe el preset "{old_name}"'}
        new_name = (new_name or "").strip()
        if not new_name or new_name == old_name:
            return {"ok": False, "error": "Nombre invalido"}
        if self._find_preset(new_name):
            return {"ok": False, "error": f'Ya existe un preset llamado "{new_name}"'}
        preset["name"] = new_name
        self.config_data["presets"] = self.presets
        engine.save_config(self.config_data)
        return {"ok": True, "presets": self.list_presets()}

    def delete_preset(self, name):
        preset = self._find_preset(name)
        if not preset:
            return {"ok": False, "error": f'No existe el preset "{name}"'}
        self.presets.remove(preset)
        self.config_data["presets"] = self.presets
        engine.save_config(self.config_data)
        return {"ok": True, "presets": self.list_presets()}

    # -------------------------------------------------------- carga de medios

    def ingest_paths(self, paths):
        """Reparte archivos por extension: imagen/video -> medio principal,
        audio -> pista de audio. Los que no matchean ninguna se ignoran.
        Llamado tanto por el dialogo nativo de Electron (browse) como por el
        drop real en el renderer (via webUtils.getPathForFile) y por
        paste_from_clipboard."""
        ignored = []
        for path in paths:
            ext = os.path.splitext(path)[1].lower()
            if ext in engine.IMAGE_EXTS:
                if not self._set_image(path):
                    ignored.append(os.path.basename(path))
            elif ext in engine.VIDEO_EXTS:
                self._set_video(path)
            elif ext in engine.AUDIO_EXTS:
                self._set_audio(path)
            else:
                ignored.append(os.path.basename(path))
        return {"ok": True, "ignored": ignored, "state": self.get_state()}

    def _set_image(self, path):
        try:
            with Image.open(path) as img:
                width, height = img.size
                thumb_src = img.copy()
                focus_src = img.copy()
        except Exception:
            return False
        thumb_src.thumbnail((88, 88))
        # Miniatura para el canvas de "Ajustar imagen" -- mismo tamano de
        # ajuste (352x200) que usaba el widget de Tk, para que el
        # arrastre/zoom en JS reproduzca exactamente la misma geometria.
        focus_src.thumbnail((FOCUS_PICKER_W, FOCUS_PICKER_H), Image.LANCZOS)
        self._invalidate_loop_preview()
        self.media_path = path
        self.media_is_video = False
        self._cancel_preview_proxy_process()  # la del medio anterior ya no sirve para nada
        self.preview_proxy_path = None  # la del video anterior no sirve (ver _set_video)
        self.media_size_raw = (width, height)
        self._reset_transform()  # foto nueva, sin el giro/espejo de la anterior
        self.media_duration = None
        self.media_interlaced = False
        self.media_thumb = _image_to_data_uri(thumb_src)
        self.media_kind_text = f"Imagen · {width}x{height}"
        self.media_focus_preview = _image_to_data_uri(focus_src)
        # Foto nueva, encuadre limpio. El corte se queda como estaba (si venia
        # trabajando en Vertical, la siguiente foto sigue en Vertical), pero el
        # recuadro se recalcula: el cuadrado centrado depende de la proporcion
        # de ESTA foto, no de la anterior.
        self.crop_rect = self._centered_rect(self.crop_mode)
        self.cover_available = False
        self._update_default_output()
        return True

    def _invalidate_loop_preview(self):
        """Tira el fragmento del loop y cualquier trabajo en vuelo. Se llama al
        CAMBIAR DE MEDIO.

        _loop_preview_job descarta su resultado comparando contra
        _loop_preview_token, pero ese token solo cambiaba al pedir OTRO
        preview -- y cargar una IMAGEN no pide ninguno (request_loop_preview
        se va de largo si el medio no es video). Entonces, al pegar una foto
        encima de un video, el trabajo del video anterior terminaba, se daba
        por bueno, y la UI volvia a mostrar y REPRODUCIR ese loop encima del
        medio nuevo. Pasa igual entre dos videos si el primero no llego a
        terminar."""
        self._loop_preview_token = None
        viejo = self._loop_preview_path
        self._loop_preview_path = None
        if viejo:
            try:
                os.remove(viejo)
            except OSError:
                pass  # el <video> del renderer puede tenerlo abierto todavia

    def _set_video(self, path):
        self._invalidate_loop_preview()
        self.media_path = path
        self.media_is_video = True
        self._cancel_preview_proxy_process()  # la del clip anterior ya no sirve para nada
        self.preview_proxy_path = None  # la del clip anterior no sirve
        self.media_size_raw = None
        self._reset_transform()  # clip nuevo, sin el giro/espejo del anterior
        self.media_video_codec = None  # lo pone en firme _probe_video_job
        self.media_duration = None
        self.media_interlaced = False
        self.media_thumb = None
        self.media_kind_text = "Video · analizando..."
        self.media_display_name = None
        self.cover_available = False
        self._update_default_output()
        threading.Thread(target=self._probe_video_job, args=(path,), daemon=True).start()

    def _probe_video_job(self, path):
        """Solo la llama _set_video, con un archivo recien cargado. Girar o
        espejar NO pasa por aca: no tocan el archivo (son propiedades del
        proyecto, ver media_rotation en __init__), asi que no hay nada que
        volver a sondear -- el ancho/alto girado sale de la propiedad
        media_size y el paso mas caro de este metodo (detect_interlaced
        decodifica hasta 200 fotogramas enteros) no se repite nunca."""
        info = engine.probe_media(self.ffmpeg_exe, path)
        thumb_img = engine.extract_video_thumb(self.ffmpeg_exe, path)
        interlaced = engine.detect_interlaced(self.ffmpeg_exe, path)
        if path != self.media_path or not self.media_is_video:
            return  # el usuario ya cambio de medio
        self.media_size_raw = info["video_size"]
        self.media_video_codec = info["video_codec"]
        self.media_duration = info["duration"]
        self.media_interlaced = interlaced
        self.trim_start = 0.0
        self.trim_end = max(0.5, float(info["duration"] or 1.0))
        if thumb_img is not None:
            self.media_thumb = _image_to_data_uri(thumb_img)

        self._refresh_media_kind_text()
        self._notify_state_changed()
        self._maybe_start_preview_proxy(path)

    def _refresh_media_kind_text(self):
        """Rehace el rotulo del chip ("Video · 3:11 · 1920x1080" / "Imagen ·
        800x500"). Se llama al girar: el tamano que muestra es el EFECTIVO
        (media_size), asi que un giro lo da vuelta."""
        if not self.media_path:
            self.media_kind_text = None
            return
        size = self.media_size
        if not self.media_is_video:
            if size:
                self.media_kind_text = f"Imagen · {size[0]}x{size[1]}"
            return
        parts = ["Video"]
        formatted = engine.format_duration(self.media_duration)
        if formatted:
            parts.append(formatted)
        if size:
            parts.append(f"{size[0]}x{size[1]}")
        if self.media_interlaced:
            parts.append("entrelazado (se corregirá)")
        self.media_kind_text = " · ".join(parts)

    def _refresh_focus_preview(self):
        """Miniatura del canvas de "Ajustar imagen", CON el giro/espejo
        puestos -- ahi se elige el encuadre, asi que tiene que mostrar la
        foto como va a quedar. Solo aplica a fotos (con video no hay
        "Ajustar imagen", ver show_focus en get_state)."""
        if not self.media_path or self.media_is_video:
            return
        try:
            with Image.open(self.media_path) as img:
                vista = self._apply_transform_pil(img)
            vista.thumbnail((FOCUS_PICKER_W, FOCUS_PICKER_H), Image.LANCZOS)
            self.media_focus_preview = _image_to_data_uri(vista)
        except Exception:
            pass  # se queda con la miniatura anterior, no vale tirar el estado

    def _apply_transform_pil(self, img):
        """El mismo giro/espejo que build_transform_filters hace en ffmpeg y
        dibujarMedio en el lienzo, pero con PIL -- para las miniaturas y
        para la portada. MISMO orden: primero girar, despues espejar."""
        out = img
        if self.media_rotation == 90:
            out = out.transpose(Image.Transpose.ROTATE_270)   # PIL rota antihorario
        elif self.media_rotation == 180:
            out = out.transpose(Image.Transpose.ROTATE_180)
        elif self.media_rotation == 270:
            out = out.transpose(Image.Transpose.ROTATE_90)
        if self.media_flip_h:
            out = out.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if self.media_flip_v:
            out = out.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        return out

    # ------------------------------------------ copia liviana para el preview

    def _maybe_start_preview_proxy(self, path):
        """Arranca la copia si el clip es mas grande de lo que el
        previsualizador necesita, O si el codec es caro de decodificar aunque
        el tamano ya entre (ver CHEAP_DECODE_CODECS en engine.py -- un clip de
        YouTube en VP9/AV1 se tironea igual, tamano aparte). Sin ninguna de
        las dos cosas no se hace nada: el original se decodifica barato y una
        copia solo gastaria disco y tiempo (ademas de perder calidad al
        recomprimir)."""
        if not self.media_size:
            return
        # El tamano CRUDO: la copia se hace sobre el archivo tal cual esta en
        # disco (sin girar), y el giro lo pone el lienzo al dibujarla.
        destino = engine.preview_proxy_size(self.media_size_raw, self.media_video_codec)
        if not destino:
            return
        threading.Thread(target=self._preview_proxy_job, args=(path, destino),
                         daemon=True).start()

    def _preview_proxy_job(self, path, size):
        # El nombre lleva ruta + fecha de modificacion: si el usuario vuelve a
        # cargar el mismo clip la copia ya esta hecha y se usa al instante, y si
        # el archivo cambio (mismo nombre, otro contenido) el nombre cambia y no
        # se reusa una copia vieja.
        try:
            marca = int(os.path.getmtime(path))
        except OSError:
            marca = 0
        digest = hashlib.md5(f"{path}|{marca}".encode("utf-8")).hexdigest()[:12]
        destino = os.path.join(
            tempfile.gettempdir(),
            f"genvideo_preview_{digest}_{size[0]}x{size[1]}.mp4")

        if not os.path.exists(destino) or os.path.getsize(destino) == 0:
            self._limpiar_copias_viejas(destino)
            cmd = engine.build_preview_proxy_command(self.ffmpeg_exe, path, destino, size)
            try:
                # stdin=DEVNULL: sin esto ffmpeg puede quedarse esperando en la
                # tuberia de JSON-RPC hacia Electron (ver _run_ffmpeg).
                # LOW_PRIORITY_KWARGS (ver engine.py) no alcanzaba solo:
                # medido, exportar con esta copia corriendo a la par
                # tardaba 1.6x mas IGUAL (esta Mac tiene nucleos de sobra,
                # asi que bajarle la prioridad o los hilos no le hacia
                # ceder terreno de verdad). Popen (no run) para poder
                # matarlo de afuera -- ver _cancel_preview_proxy_process,
                # que start_generation llama ANTES de arrancar el export
                # real, que nunca usa esta copia.
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, **engine.LOW_PRIORITY_KWARGS)
                self._preview_proxy_process = proc
                proc.wait()
            except Exception:
                return  # sin copia: el previsualizador sigue con el original
            finally:
                if self._preview_proxy_process is proc:
                    self._preview_proxy_process = None
            # Matado a proposito (cambio de medio, exportacion real) o
            # fallo de verdad: en los dos casos el archivo a medio escribir
            # no sirve, mismo chequeo que ya habia.
            if proc.returncode != 0 or not os.path.exists(destino) or os.path.getsize(destino) == 0:
                return

        if path != self.media_path or not self.media_is_video:
            return  # el usuario ya cambio de medio mientras se armaba
        self.preview_proxy_path = destino
        self._notify_state_changed()

    def _cancel_preview_proxy_process(self):
        """Mata la copia liviana si todavia se esta armando. Se llama antes
        de arrancar una exportacion real (nunca la usa, no tiene por que
        competirle CPU) y al cambiar de medio (el resultado ya no va a
        servir para nada, terminar de armarlo es trabajo tirado). terminate()
        y no kill(): le da chance a ffmpeg de cerrar el archivo ordenado, y
        wait(timeout=..) por si no llega a reaccionar."""
        proc = self._preview_proxy_process
        if not proc or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    @staticmethod
    def _limpiar_copias_viejas(salvo, dias=7, prefijo="genvideo_preview_"):
        """Las copias se guardan para reusarlas al volver a cargar el mismo
        clip, pero no para siempre: cada una pesa lo suyo y viven en el temp del
        sistema. Se borran las que no se tocan hace una semana."""
        limite = time.time() - dias * 86400
        try:
            for nombre in os.listdir(tempfile.gettempdir()):
                if not nombre.startswith(prefijo):
                    continue
                viejo = os.path.join(tempfile.gettempdir(), nombre)
                if viejo == salvo:
                    continue
                try:
                    if os.path.getmtime(viejo) < limite:
                        os.remove(viejo)
                except OSError:
                    pass  # en uso por otra ventana, o ya no esta
        except OSError:
            pass

    # -------------------------------------------- copia liviana para el beat

    def _maybe_start_audio_preview_proxy(self, path):
        """Arranca la copia del beat SOLO si el codec original no lo puede
        tocar el <audio> del navegador (ver needs_audio_preview_proxy en
        engine.py) -- con un mp3/aac normal no hace falta nada, se sigue
        escuchando el original."""
        info = engine.probe_media(self.ffmpeg_exe, path)
        if not engine.needs_audio_preview_proxy(info.get("audio_codec")):
            return
        self._audio_preview_proxy_job(path)

    def _audio_preview_proxy_job(self, path):
        # Mismo criterio que la copia de video: el nombre lleva ruta + fecha
        # de modificacion, asi que cargar el mismo beat de nuevo usa la copia
        # ya hecha en vez de rearmarla.
        try:
            marca = int(os.path.getmtime(path))
        except OSError:
            marca = 0
        digest = hashlib.md5(f"{path}|{marca}".encode("utf-8")).hexdigest()[:12]
        # .wav y no .m4a: la copia se genera en pcm_s16le, no aac (ver el
        # comentario en build_audio_preview_proxy_command).
        destino = os.path.join(tempfile.gettempdir(), f"genvideo_audiopreview_{digest}.wav")

        if not os.path.exists(destino) or os.path.getsize(destino) == 0:
            self._limpiar_copias_viejas(destino, prefijo="genvideo_audiopreview_")
            cmd = engine.build_audio_preview_proxy_command(self.ffmpeg_exe, path, destino)
            try:
                subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                               creationflags=engine.CREATE_NO_WINDOW)
            except Exception:
                return  # sin copia: el beat sigue mudo, pero no rompe nada mas
            if not os.path.exists(destino) or os.path.getsize(destino) == 0:
                return

        if path != self.audio_path:
            return  # el usuario ya cambio de beat mientras se armaba
        self.audio_preview_proxy_path = destino
        self._notify_state_changed()

    def _set_audio(self, path):
        self.audio_path = path
        self.audio_kind_text = "Audio · analizando..."
        self.audio_clip_warning = None
        self.audio_peak_db = None
        self.audio_preview_proxy_path = None  # la del beat anterior no sirve
        # El nombre del beat pasa al campo "Nombre" de la UI, no solo a la ruta
        # de salida por dentro: el usuario casi nunca lo escribia (se generaba
        # solo y el campo se veia vacio con su "Sin titulo"), asi que ahora lo
        # ve escrito y puede corregirlo si quiere. Solo se pisa si el nombre
        # todavia es automatico -- si el usuario escribio algo a mano, cambiar
        # de beat no se lo borra (ver _output_name_is_auto en set_output_name).
        if self._output_name_is_auto:
            self.custom_output_name = os.path.splitext(os.path.basename(path))[0]
        self._update_default_output()
        threading.Thread(target=self._measure_peak_job, args=(path,), daemon=True).start()
        threading.Thread(target=self._maybe_start_audio_preview_proxy, args=(path,), daemon=True).start()

    def _measure_peak_job(self, path):
        peak = engine.measure_peak_db(self.ffmpeg_exe, path)
        if path != self.audio_path:
            return  # el usuario ya cambio de audio
        self.audio_peak_db = peak
        prefix = "Audio"
        if peak is None:
            self.audio_kind_text = prefix
            self.audio_clip_warning = None
        elif peak > 0:
            self.audio_kind_text = f"{prefix} · pico máx +{peak:.1f} dB, pasa de 0, puede clipear"
            self.audio_clip_warning = True
        else:
            self.audio_kind_text = f"{prefix} · pico máx {peak:.1f} dB, no clipea"
            self.audio_clip_warning = False
        self._notify_state_changed()

    def remove_media(self):
        self._invalidate_loop_preview()
        self.media_path = None
        self.media_is_video = False
        self.media_size_raw = None
        self._reset_transform()
        self.media_video_codec = None
        self.media_duration = None
        self.media_interlaced = False
        self.media_thumb = None
        self.media_kind_text = None
        self.media_display_name = None
        self.media_focus_preview = None
        self.crop_mode = "cuadrado"
        self.crop_rect = [0.0, 0.0, 1.0, 1.0]
        self.trim_start = 0.0
        self.trim_end = 1.0
        self.cover_available = False
        return {"ok": True, "state": self.get_state()}

    # -------------------------------------------------------- ajustar imagen

    CROP_MODES = ("cuadrado", "vertical", "completa")

    def _centered_rect(self, mode):
        """El recuadro con que arranca cada corte, centrado en la foto.

        "cuadrado" es el cuadrado mas grande que entra (lo que antes daba zoom
        100 con el foco al medio), asi que depende de la proporcion del
        archivo. "vertical" es la foto entera, que es lo que ES."""
        if mode != "cuadrado":
            return [0.0, 0.0, 1.0, 1.0]
        w, h = self.media_size or (0, 0)
        if w <= 0 or h <= 0:
            return [0.0, 0.0, 1.0, 1.0]
        if w >= h:
            fw = h / w
            return [(1.0 - fw) / 2, 0.0, fw, 1.0]
        fh = w / h
        return [0.0, (1.0 - fh) / 2, 1.0, fh]

    def _clamp_rect(self, x, y, w, h):
        w = max(0.01, min(1.0, float(w)))
        h = max(0.01, min(1.0, float(h)))
        return [max(0.0, min(1.0 - w, float(x))),
                max(0.0, min(1.0 - h, float(y))), w, h]

    def set_crop(self, mode, x, y, w, h):
        """Se llama en cada arrastre del canvas -- la geometria la calcula
        toda JS (app.js) y aca solo se guarda el resultado para usarlo al
        generar (build_focus_crop) y para dimensionar la caja de composicion
        (build_layout)."""
        if mode not in self.CROP_MODES:
            mode = "cuadrado"
        self.crop_mode = mode
        self.crop_rect = self._clamp_rect(x, y, w, h)
        return {"ok": True}

    def set_crop_mode(self, mode):
        """Cambiar de corte desde el desplegable del mini preview -- cada uno
        vuelve a su recuadro centrado."""
        if mode not in self.CROP_MODES:
            mode = "cuadrado"
        self.crop_mode = mode
        self.crop_rect = self._centered_rect(mode)
        return {"ok": True, "state": self.get_state()}

    def reset_focus(self):
        """El boton "Centrar" -- vuelve al recuadro de arranque del corte
        puesto, sin cambiar de corte."""
        self.crop_rect = self._centered_rect(self.crop_mode)
        return {"ok": True, "state": self.get_state()}

    def current_crop(self):
        """(x, y, ancho, alto) para build_focus_crop, o None si no aplica
        (video, o sin medio cargado) -- usado por la Fase 8."""
        if self.media_is_video or not self.media_path:
            return None
        return tuple(self.crop_rect)

    def current_crop_fit(self):
        """True = "contain" (build_filtergraph), la foto entra completa y el
        sobrante se rellena de negro -- el modo "Completa" del recorte.
        False = "cover" (de siempre), amplia hasta cubrir la caja y recorta
        el sobrante -- "Cuadrado" y "Vertical" comparten esta, que es la
        unica manera de tener un recuadro EXACTO (Cuadrado) o de que
        "Vertical" no deje franjas vacias cuando la caja calza con la
        proporcion de la foto (el caso de siempre, con Bordes en 100%).
        Video no tiene "Ajustar imagen" -- nunca contrae."""
        return not self.media_is_video and self.crop_mode == "completa"

    def current_crop_aspect(self):
        """Proporcion ancho/alto del recuadro EN PIXELES del original, que es
        la que tiene que tomar la caja de composicion para no recortar de
        nuevo lo que este recorte ya eligio (ver build_layout).

        Ojo con la cuenta: crop_rect son fracciones, y una fraccion cuadrada
        (0.5 x 0.5) NO es un cuadrado salvo que la foto lo sea -- hay que
        pasar por los pixeles reales. Sin medio, devuelve 1.0: la caja
        cuadrada de siempre.

        Con VIDEO no hay "Ajustar imagen" (show_focus en get_state es false
        para video, y crop_rect no aplica -- ver current_crop), pero antes
        esto igual devolvia 1.0 fijo para cualquier video, sin importar su
        proporcion real: un video panoramico (16:9, o mas ancho todavia,
        como un reel horizontal) quedaba metido a la fuerza en una caja
        cuadrada y perdia los costados, en el preview Y en la exportacion
        real (las dos pasan por esta misma funcion). Ahora la proporcion del
        VIDEO ORIGINAL hace de "recorte": la caja de composicion toma esa
        forma y el video entra completo, sin recortar nada -- mismo efecto
        que el modo "Vertical" de las fotos, pero automatico, porque video
        no tiene el selector de modo que las fotos si tienen."""
        if not self.media_path or not self.media_size:
            return 1.0
        w, h = self.media_size
        if self.media_is_video:
            return max(1.0, w) / max(1.0, h)
        _, _, fw, fh = self.crop_rect
        px_w = max(1.0, w * fw)
        px_h = max(1.0, h * fh)
        return px_w / px_h

    def remove_audio(self):
        self.audio_path = None
        self.audio_kind_text = None
        self.audio_clip_warning = None
        self.audio_peak_db = None
        self.audio_preview_proxy_path = None
        return {"ok": True, "state": self.get_state()}

    # ------------------------------------------ recorte del loop / velocidad / escala

    def set_trim(self, start, end):
        duration = self.media_duration or 1.0
        min_gap = 0.5
        start = max(0.0, min(float(start), duration - min_gap))
        end = max(start + min_gap, min(float(end), duration))
        self.trim_start = start
        self.trim_end = end
        return {"ok": True}

    def _effective_trim(self):
        """None si el recorte cubre el clip entero (nada que recortar),
        o (start, end) si el usuario angosto el rango. Usado tanto por la
        generacion real (start_generation) como por el preview del loop
        (request_loop_preview) para que compongan exactamente el mismo
        fragmento."""
        if not self.media_is_video:
            return None
        start, end = self.trim_start, self.trim_end
        duration = self.media_duration
        if start > 0.05 or (duration and end < duration - 0.35):
            return (start, end)
        return None

    def set_speed(self, value):
        self.speed = value
        return {"ok": True}

    def set_scale_pct(self, value):
        self.scale_pct = int(round(float(value)))
        return {"ok": True}


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

    def paste_from_clipboard(self):
        """Ctrl+V/Cmd+V: pega una imagen, uno o mas archivos, o (si el
        portapapeles trae texto con un link) lo detecta JS antes de llamar
        aca -- este metodo solo se ocupa de imagenes/archivos binarios."""
        if engine.IS_MAC:
            files = self._mac_clipboard_files()
            if files:
                return self.ingest_paths(files)
        try:
            from PIL import ImageGrab
            data = ImageGrab.grabclipboard()
        except Exception as exc:
            return {"ok": False, "error": f"No se pudo leer el portapapeles: {exc}"}
        if data is None:
            return {"ok": False, "empty": True}
        if isinstance(data, list):
            return self.ingest_paths([p for p in data if isinstance(p, str)])
        # Nombre NUEVO en cada pegada, no uno fijo (era "genvideo_imagen_pegada.png"
        # a secas): media_path termina en el src de <img>/<video> del
        # previsualizador (live-preview.js), que compara rutas para decidir si
        # hay que recargar -- con la ruta identica, pegar una segunda imagen
        # sobreescribia el archivo pero la preview (y la portada, que reusa ese
        # mismo fotograma) se quedaban con la primera. Exportar salia bien
        # igual porque ffmpeg lee el archivo del disco directo, sin pasar por
        # ahi -- mismo bug que _cover_paste_counter/_download_counter ya
        # arreglaban en sus propios flujos.
        self._media_paste_counter += 1
        path = os.path.join(
            tempfile.gettempdir(),
            f"genvideo_imagen_pegada_{self._media_paste_counter}.png",
        )
        try:
            data.convert("RGB").save(path, "PNG")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not self._set_image(path):
            return {"ok": False, "error": "No se pudo procesar la imagen pegada"}
        self.media_kind_text = f"Imagen pegada ({self.media_size[0]}x{self.media_size[1]})"
        return {"ok": True, "pasted_image": True, "state": self.get_state()}

    def clipboard_image_path(self):
        """Deja la imagen del portapapeles en un PNG temporal y devuelve su
        ruta, SIN tocar el medio cargado -- para pegar una portada propia en el
        modal de "Guardar portada" (ver setCoverImage en app.js).
        paste_from_clipboard hace algo parecido pero ademas la carga como medio
        de la app, que aca seria justo lo que no se quiere."""
        if engine.IS_MAC:
            for path in self._mac_clipboard_files():
                if os.path.splitext(path)[1].lower() in engine.IMAGE_EXTS:
                    return {"ok": True, "path": path}
        try:
            from PIL import ImageGrab
            data = ImageGrab.grabclipboard()
        except Exception as exc:
            return {"ok": False, "error": f"No se pudo leer el portapapeles: {exc}"}
        if data is None:
            return {"ok": False, "empty": True}
        if isinstance(data, list):
            # El portapapeles trae RUTAS (se copio el archivo en el explorador,
            # no la imagen en si): sirve la primera que sea una imagen.
            for path in data:
                if isinstance(path, str) and os.path.splitext(path)[1].lower() in engine.IMAGE_EXTS:
                    return {"ok": True, "path": path}
            return {"ok": False, "empty": True}
        # Nombre NUEVO en cada pegada, no uno fijo: la ruta termina en el src de
        # un <img> (ver setCoverImage en app.js) y asignarle a src la MISMA
        # cadena que ya tenia no dispara ninguna carga -- el navegador se queda
        # con la imagen anterior. Asi, pegar una segunda imagen sobreescribia el
        # archivo pero en pantalla seguia la primera (y peor: el encuadre se
        # calculaba con las medidas de la vieja).
        self._cover_paste_counter += 1
        path = os.path.join(
            tempfile.gettempdir(),
            f"genvideo_portada_pegada_{self._cover_paste_counter}.png",
        )
        try:
            data.convert("RGB").save(path, "PNG")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": path}

    # -------------------------------------------------- preparar texturas

    def _current_textures(self, canvas_w, canvas_h):
        """Prepara (con cache) cada capa activa y devuelve la lista lista
        para build_command/build_compose_command:
        [(ruta, modo, opacidad, escala_si_es_video)].

        El ultimo campo va en None para las imagenes: su escala ya quedo
        horneada en el mosaico de _prepare_texture. Una textura de VIDEO no
        se puede tilear con PIL, asi que va la ruta cruda y la escala viaja
        aparte para que ffmpeg la aplique como zoom (ver build_filtergraph)."""
        textures = []
        for state in self.texture_layers:
            path = state.get("path")
            if not path:
                continue
            scale = state.get("scale", 100)
            mode = engine.BLEND_MODES.get(state.get("blend", "Aclarar"), "lighten")
            opacity = state.get("opacity", 47) / 100.0
            if self._texture_is_video(path):
                textures.append((path, mode, opacity, scale))
                continue
            try:
                prepared = self._prepare_texture(path, scale, canvas_w, canvas_h)
            except Exception:
                prepared = path
            textures.append((prepared, mode, opacity, None))
        return textures

    def _prepare_texture(self, texture_path, scale_pct, canvas_w, canvas_h):
        """Genera (con cache) la textura tileada al tamano del lienzo: se
        escala a su tamano natural x el porcentaje elegido y se repite en
        mosaico, en vez de estirarla -- asi el grano queda fino. Un archivo
        por textura de origen (hash de la ruta) para que varias capas no se
        pisen el cache entre si."""
        scale = int(round(scale_pct))
        key = (texture_path, scale, canvas_w, canvas_h)
        with self._texture_lock:
            cached = self._texture_cache.get(texture_path)
            if cached and cached[0] == key and os.path.exists(cached[1]):
                return cached[1]
            # La escala y el lienzo van en el NOMBRE: el previsualizador en vivo
            # dibuja este mismo archivo (ver prepared_textures), y con un nombre
            # fijo el navegador se quedaba con la version anterior al cambiar la
            # escala -- asignarle a un <img> la misma URL no dispara recarga.
            digest = hashlib.md5(texture_path.encode("utf-8")).hexdigest()[:10]
            path = os.path.join(
                tempfile.gettempdir(),
                f"genvideo_textura_preparada_{digest}_{scale}_{canvas_w}x{canvas_h}.png")
            # La baldosa se mide RELATIVA al lienzo, no en pixeles absolutos. El
            # preview del loop se compone a resolucion reducida (0.5 o 0.7, ver
            # _build_loop_preview_cmd), y con un tamano absoluto la misma baldosa
            # ocupaba una fraccion MAS GRANDE de esa imagen mas chica: el grano
            # salia mucho mas grueso ahi que en la exportacion real -- y como el
            # panel de portada muestra justo ese fragmento, la portada parecia
            # armarse con otra escala de textura. Con el factor, la proporcion
            # baldosa/lienzo es la misma a cualquier resolucion.
            factor = canvas_w / engine.MAX_WIDTH
            with Image.open(texture_path) as tex:
                tex = tex.convert("RGB")
                tile_w = max(2, round(tex.width * scale / 100 * factor))
                tile_h = max(2, round(tex.height * scale / 100 * factor))
                tile = tex.resize((tile_w, tile_h), Image.LANCZOS)
            board = Image.new("RGB", (canvas_w, canvas_h))
            for y in range(0, canvas_h, tile_h):
                for x in range(0, canvas_w, tile_w):
                    board.paste(tile, (x, y))
            board.save(path)
            self._texture_cache[texture_path] = (key, path)
            return path

    def prepared_textures(self):
        """Los mosaicos ya preparados -- LOS MISMOS archivos que come ffmpeg --
        para que el previsualizador en vivo dibuje exactamente lo que va a salir
        exportado.

        Sin esto el canvas se armaba su propia baldosa redimensionando la
        textura en el navegador, que usa otro filtro que PIL: sobre un semitono
        fino los pixeles no caian igual y quedaba una diferencia contra el
        archivo final (medida: 21/255 en modo Normal, 9/255 en Multiplicar).
        Dibujando este mosaico no hay redimensionado propio y coinciden.

        Va aparte de get_state a proposito: preparar un mosaico nuevo cuesta
        (PIL lo tilea al lienzo entero) y get_state se llama todo el tiempo.
        Aca solo se paga cuando cambia la ESCALA de la textura; el resto de las
        veces sale del cache de _prepare_texture."""
        salida = []
        for capa in self.texture_layers:
            ruta = capa.get("path")
            if not ruta:
                continue
            preparado = None
            # Una textura de VIDEO no se tilea (PIL no la abre): esa el canvas la
            # dibuja como zoom del fotograma, igual que ffmpeg.
            if not self._texture_is_video(ruta):
                try:
                    preparado = self._prepare_texture(
                        ruta, capa.get("scale", 100), engine.MAX_WIDTH, engine.MAX_HEIGHT)
                except Exception:
                    preparado = None
            salida.append({"path": ruta, "prepared": preparado})
        return salida

    # -------------------------------------------------- previsualizador en vivo

    def request_preview(self):
        """Genera un fotograma compuesto (plantilla + texturas + ajustes)
        para el previsualizador -- mismo plan de composicion que la
        generacion real (build_layout + build_filtergraph), pero una sola
        pasada a PNG. El resultado llega a JS via el evento onPreviewReady
        (antes, evaluate_js directo; ahora, self._emit)."""
        if not self.media_path:
            return {"ok": False}
        if self.media_is_video and not self.media_size:
            return {"ok": False}  # el sondeo del clip sigue corriendo

        template_box = self.template_box if self.template_path else None
        layout, _, _ = engine.build_layout(
            self.media_size, self.media_is_video, self.scale_pct, template_box,
            crop_aspect=self.current_crop_aspect(),
        )
        # Fraccion del ancho del lienzo que ocupa el medio real (sin
        # plantilla, el resto son barras negras horneadas en el frame
        # compuesto -- un video vertical dentro del lienzo 16:9 siempre
        # las tiene). JS la usa para angostar los controles de abajo
        # (recorte/velocidad/generar) al ancho real del video, no al del
        # lienzo entero. Con plantilla el medio no queda centrado de forma
        # simetrica dentro de su ventana, asi que no aplica -- None = ancho
        # completo.
        content_width_frac = None if template_box else layout["inner"][0] / layout["canvas"][0]
        textures = self._current_textures(*layout["canvas"])

        cmd = [self.ffmpeg_exe, "-y"]
        if self.media_is_video:
            start = self.trim_start
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
        # Sin -stream_loop aca (a diferencia de build_command): esto saca UN
        # fotograma, asi que del video de textura alcanza con su primer frame.
        for path, mode, opacity, video_scale in textures:
            cmd += ["-i", path]
            tex_layers.append((idx, mode, opacity, video_scale))
            idx += 1
        fc = engine.build_filtergraph(
            layout, is_video=False, tpl_idx=tpl_idx, textures=tex_layers,
            focus=self.current_crop(), transform=self._transform_filters(),
            contain=self.current_crop_fit(),
        )
        self._preview_counter += 1
        png = os.path.join(tempfile.gettempdir(), f"genvideo_preview_{self._preview_counter}.png")
        cmd += ["-filter_complex", fc, "-map", "[vout]", "-frames:v", "1", png]

        token = object()
        self._preview_token = token
        threading.Thread(
            target=self._preview_job, args=(token, cmd, png, content_width_frac), daemon=True
        ).start()
        return {"ok": True}

    def _preview_job(self, token, cmd, png, content_width_frac):
        # stdin=DEVNULL: sin esto ffmpeg hereda el stdin del sidecar (la
        # tuberia viva de JSON-RPC hacia Electron) -- ver _run_ffmpeg, mismo
        # motivo por el que la generacion real se trababa.
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                               creationflags=engine.CREATE_NO_WINDOW)
        data_uri = None
        if proc.returncode == 0 and os.path.exists(png):
            try:
                with Image.open(png) as img:
                    data_uri = _image_to_data_uri(img.copy())
            except Exception:
                data_uri = None
            finally:
                try:
                    os.remove(png)
                except OSError:
                    pass
        if data_uri:
            self._last_preview_data_uri = data_uri
        if token is not self._preview_token:
            return  # el usuario siguio cambiando cosas -- este resultado ya no aplica
        self._emit("onPreviewReady", {"data_uri": data_uri, "content_width_frac": content_width_frac})

    def _frame_cover_image(self, source_image, hole_w, hole_h, focus):
        """Deja la imagen propia del tamano EXACTO del hueco donde va (la
        ventana de la plantilla, o el cuadro que dejan los bordes), aplicando el
        encuadre que se eligio a mano en el modal:

          zoom = 1  -> justo cubre el hueco (se recorta lo que sobra)
          zoom > 1  -> mas cerca
          zoom < 1  -> la imagen se ve COMPLETA y aparecen bordes negros, que es
                       la salida para cuando el recorte cuadrado corta los pies

        Se hace con PIL y no con filtros de ffmpeg a proposito: asi el encuadre
        tiene UNA sola implementacion (la misma cuenta que dibuja el preview,
        ver layoutCoverComposePreview en app.js) y el resto del pipeline no se
        entera de nada -- la imagen ya llega con la medida justa, asi que el
        scale/crop de build_filtergraph no la toca."""
        zoom, fx, fy = float(focus[0]), float(focus[1]), float(focus[2])
        with Image.open(source_image) as src:
            img = src.convert("RGB")
        # "Cubrir el hueco" es la referencia (zoom = 1).
        cubrir = max(hole_w / img.width, hole_h / img.height)
        k = cubrir * max(0.05, zoom)
        nueva = (max(1, round(img.width * k)), max(1, round(img.height * k)))
        img = img.resize(nueva, Image.LANCZOS)
        lienzo = Image.new("RGB", (hole_w, hole_h), (0, 0, 0))
        # Lo que sobra se reparte segun el foco; lo que falta queda centrado
        # (con bordes no hay nada que pasear).
        x = round((hole_w - nueva[0]) * (fx if nueva[0] > hole_w else 0.5))
        y = round((hole_h - nueva[1]) * (fy if nueva[1] > hole_h else 0.5))
        lienzo.paste(img, (x, y))
        out = os.path.join(tempfile.gettempdir(), "genvideo_portada_encuadrada.png")
        lienzo.save(out, "PNG")
        return out

    def save_cover(self, loop_time=None, mode="full", source_image=None,
                   with_template=True, with_textures=True, image_focus=None,
                   source_time=None):
        """Guarda uno o dos PNG con el fotograma compuesto (plantilla +
        medio + texturas) -- misma logica de composicion que
        request_preview. Va SIEMPRE junto al video recien exportado, sin
        preguntar donde -- solo esta disponible justo despues de exportar
        (ver cover_available en _on_job_done).

        loop_time: segundos DENTRO del loop, tal como lo ve el usuario en
        el previsualizador del loop (0 = trim_start) -- None usa
        trim_start (comportamiento de antes: fotos, o si no se eligio
        momento). Se pasa a tiempo real del archivo fuente multiplicando
        por la velocidad -- el loop preview ya sale mas corto/largo segun
        speed, asi que un segundo de loop no es un segundo de fuente.

        mode: "full" (lienzo 1920x1080 completo, con la plantilla encima),
        "empty" (solo el recuadro de la plantilla -- ahi la plantilla ya
        es transparente, asi que recortar el mismo frame compuesto da el
        mismo resultado sin rearmar el filtro) o "both". Cae a "full" si
        no hay plantilla cargada (no existe "parte vacia" sin plantilla).

        source_image: ruta de una imagen propia (la que se arrastra al modal)
        para usarla EN VEZ de un fotograma del video -- para cuando ningun
        momento del loop sirve como portada.

        with_template / with_textures: que se pone encima. Son dos
        interruptores SUELTOS y valen para las dos fuentes (fotograma del video
        o imagen propia) -- antes era un unico "compose" que ademas solo se
        miraba con imagen propia, asi que con un fotograma no habia forma de
        sacar la plantilla, ni de quedarse con la plantilla pero sin el grano.
        Apagarlos NO significa "copiar el archivo": la geometria es la misma
        (mismo recorte al cuadro de la portada), solo se dejan de dibujar esas
        capas.

        image_focus: (zoom, x, y) del encuadre que el usuario eligio a mano
        arrastrando la imagen en el modal (ver build_focus_crop en engine.py y
        layoutCoverComposePreview en app.js, que dibuja exactamente el mismo
        recorte).

        Ya NO exige cover_available (que solo se prende recien despues de
        exportar el video, ver _on_job_done): se pidio poder guardar la
        portada SIN exportar el video primero, con un boton aparte al lado
        de "Generar video" (ver #save-cover-standalone-btn en app.js). La
        composicion de aca abajo nunca dependio del video ya exportado --
        arma su propio comando de ffmpeg desde media_path/template_path
        igual que el previsualizador -- asi que lo unico que hacia falta
        soltar era este chequeo. output_path SI sigue haciendo falta:
        de ahi sale la carpeta y el nombre base del PNG, pero ya se calcula
        solo apenas hay medio o audio cargado (ver _update_default_output),
        mucho antes de exportar nada."""
        if not self.output_path:
            return {"ok": False, "error": "No hay portada disponible."}
        if source_image:
            try:
                with Image.open(source_image):
                    pass
            except Exception:
                return {"ok": False, "error": "Esa imagen no se pudo leer."}
        else:
            if not self.media_path:
                return {"ok": False, "error": "No hay portada disponible."}
            if self.media_is_video and not self.media_size:
                return {"ok": False, "error": "Todavía se está analizando el video."}

        folder = os.path.dirname(self.output_path)
        base = os.path.splitext(os.path.basename(self.output_path))[0]

        template_box = self.template_box if self.template_path else None
        # Ya NO cae a "full" cuando no hay plantilla: el cuadro existe igual
        # (los bordes negros lo dejan centrado, ver engine.content_box), asi
        # que "solo el cuadro" vale para cualquier combinacion.
        if mode not in ("full", "empty", "both"):
            mode = "full"

        layout, _, _ = engine.build_layout(
            self.media_size, self.media_is_video, self.scale_pct, template_box,
            crop_aspect=self.current_crop_aspect(),
        )
        textures = self._current_textures(*layout["canvas"])

        # Cada capa por separado, y con cualquier fuente: la geometria no cambia
        # (el recorte al cuadro de la portada se mantiene), solo se deja de
        # dibujar lo que se apago.
        overlay_template = self.template_path if with_template else None
        if not with_textures:
            textures = []

        cmd = [self.ffmpeg_exe, "-y"]
        if source_image:
            # El encuadre viene del propio modal (arrastrar + pinza sobre la
            # imagen), no del "Ajustar imagen" del panel, que es del medio
            # cargado. Se aplica ANTES, con PIL, dejando la imagen del tamano
            # del hueco -- asi tambien se puede encoger para que se vea
            # completa, que con el crop de ffmpeg no se podia (ver
            # _frame_cover_image). Sin encuadre elegido, el filtro la centra y
            # recorta para cubrir el hueco, igual que a una foto cualquiera.
            focus = None
            if image_focus:
                try:
                    source_image = self._frame_cover_image(
                        source_image, layout["inner"][0], layout["inner"][1], image_focus,
                    )
                except Exception:
                    pass  # si algo falla, sigue con la imagen cruda y el recorte de siempre
            cmd += ["-i", source_image]
        else:
            focus = self.current_crop()
            if self.media_is_video:
                start = self.trim_start
                if source_time is not None:
                    # Momento en tiempo del ARCHIVO, sin pasar por el loop: el
                    # selector del panel de portada recorre el video entero, no
                    # solo el pedazo recortado, asi que la portada puede salir de
                    # cualquier parte (ver setupCoverFramePicker en app.js).
                    start = max(0.0, float(source_time))
                elif loop_time is not None:
                    try:
                        speed = float(self.speed.rstrip("x"))
                    except ValueError:
                        speed = 1.0
                    start = self.trim_start + max(0.0, loop_time) * speed
                    start = min(start, max(self.trim_start, self.trim_end - 0.05))
                if self.media_duration:
                    start = min(start, max(0.0, self.media_duration - 0.5))
                if start > 0:
                    cmd += ["-ss", f"{start:.3f}"]
            cmd += ["-i", self.media_path]
        idx = 1
        tpl_idx = None
        if overlay_template:
            cmd += ["-i", overlay_template]
            tpl_idx = idx
            idx += 1
        tex_layers = []
        # Igual que en request_preview: una portada es un fotograma, el
        # primero del video de textura sirve y no hace falta repetirlo.
        for path, tex_mode, opacity, video_scale in textures:
            cmd += ["-i", path]
            tex_layers.append((idx, tex_mode, opacity, video_scale))
            idx += 1
        fc = engine.build_filtergraph(
            layout, is_video=False, tpl_idx=tpl_idx, textures=tex_layers,
            focus=focus, transform=self._transform_filters(),
            contain=self.current_crop_fit(),
        )

        outputs = []  # (etiqueta del filtro, ruta de salida)
        vout_label = "[vout]"
        if mode == "both":
            # [vout] no se puede mapear directo Y alimentar otro filtro a
            # la vez -- split lo duplica en dos salidas independientes.
            fc += ";[vout]split=2[voutfull][voutraw]"
            vout_label = "[voutfull]"
        if mode in ("full", "both"):
            full_path = engine.unique_output_path(folder, f"{base} portada", ext=".png")
            outputs.append((vout_label, full_path))
        if mode in ("empty", "both"):
            x, y, w, h = engine.content_box(layout)
            crop_src = "[voutraw]" if mode == "both" else "[vout]"
            fc += f";{crop_src}crop={w}:{h}:{x}:{y}[voutc]"
            empty_path = engine.unique_output_path(folder, f"{base} portada (vacia)", ext=".png")
            outputs.append(("[voutc]", empty_path))

        cmd += ["-filter_complex", fc]
        for label, path in outputs:
            cmd += ["-map", label, "-frames:v", "1", path]

        threading.Thread(
            target=self._save_cover_job, args=(cmd, [p for _, p in outputs]), daemon=True
        ).start()
        return {"ok": True}

    def _save_cover_job(self, cmd, cover_paths):
        # stdin=DEVNULL: mismo motivo que en _preview_job -- sin esto ffmpeg
        # hereda el stdin del sidecar (la tuberia JSON-RPC viva hacia
        # Electron) y se traba.
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                               creationflags=engine.CREATE_NO_WINDOW)
        saved = [p for p in cover_paths if os.path.exists(p)]
        if proc.returncode == 0 and saved:
            names = ", ".join(os.path.basename(p) for p in saved)
            self._push_status(f"Portada guardada: {names}")
        else:
            self._push_status("No se pudo guardar la portada.")

    @staticmethod
    def _scaled_layout(layout, factor):
        """Copia de un layout (ver engine.build_layout) con inner/canvas/pos
        escalados -- toda la matematica de build_filtergraph es proporcional
        a esos numeros, asi que esto compone el mismo encuadre a una
        resolucion mas chica (mismo recorte/posicion, solo menos pixeles).
        Ancho y alto se redondean a PAR (libx264 lo exige)."""
        def sc(v):
            return max(2, int(round(v * factor)) // 2 * 2)
        inner = (sc(layout["inner"][0]), sc(layout["inner"][1]))
        canvas = (sc(layout["canvas"][0]), sc(layout["canvas"][1]))
        pos = (sc(layout["pos"][0]), sc(layout["pos"][1])) if layout["pos"] else None
        # "natural" tambien: es un tamano en pixeles del lienzo (ver
        # build_layout), asi que a media resolucion tiene que encogerse
        # igual que el resto o el modo "Completa" compondria con la foto a
        # tamano completo dentro de una ventana a la mitad.
        nat = layout.get("natural")
        escalado = {"mode": layout["mode"], "inner": inner, "canvas": canvas, "pos": pos}
        if nat:
            escalado["natural"] = (sc(nat[0]), sc(nat[1]))
        return escalado

    def request_loop_preview(self):
        """Compone SOLO la unidad del loop -- exactamente la FASE 1 de la
        generacion real (ver _run_ffmpeg_job), recorte + plantilla +
        texturas + velocidad ya aplicados, pero SIN encadenar la FASE 2
        (repetir + mezclar con el audio completo, la parte lenta). Asi el
        usuario ve el fragmento que va a hacer loop sin esperar ni afectar
        el tiempo de la exportacion real."""
        if not self.media_path or not self.media_is_video:
            return {"ok": False}
        if not self.media_size:
            return {"ok": False}  # el sondeo del clip sigue corriendo

        template_box = self.template_box if self.template_path else None
        layout, _, _ = engine.build_layout(
            self.media_size, self.media_is_video, self.scale_pct, template_box,
            crop_aspect=self.current_crop_aspect(),
        )
        # content_width_frac es una proporcion (inner/canvas) -- no cambia
        # con la resolucion, asi que se calcula sobre el layout a tamano
        # real aunque el compose de abajo use uno mas chico.
        content_width_frac = None if template_box else layout["inner"][0] / layout["canvas"][0]
        try:
            speed = float(self.speed.rstrip("x"))
        except ValueError:
            speed = 1.0

        self._loop_preview_counter += 1
        temp_path = os.path.join(
            tempfile.gettempdir(), f"genvideo_loop_preview_{self._loop_preview_counter}.mp4"
        )

        token = object()
        self._loop_preview_token = token
        threading.Thread(
            target=self._loop_preview_job,
            # El medio viaja con el trabajo (no se lee al final) para que el
            # evento diga a QUE medio pertenece este fragmento -- ver la
            # comprobacion en onLoopPreviewReady (app.js).
            args=(token, layout, temp_path, content_width_frac, speed, self.media_path),
            daemon=True,
        ).start()
        return {"ok": True}

    def _build_loop_preview_cmd(self, layout, temp_path, speed, use_nvenc):
        # Este preview solo sirve para confirmar QUE PARTE hace loop, no la
        # calidad final -- componerlo al lienzo completo (1920x1080, igual
        # que la exportacion real) lo hacia sentir lento sin necesidad.
        # NVENC (GPU) tolera bastante mas resolucion sin perder el margen de
        # velocidad que da tenerlo disponible -- CPU (libx264 ultrafast) se
        # queda en el factor mas chico de siempre. La generacion real de
        # verdad sigue usando el layout completo, esto no la toca.
        scale_factor = 0.7 if use_nvenc else 0.5
        preview_layout = self._scaled_layout(layout, scale_factor)
        textures = self._current_textures(*preview_layout["canvas"])
        return engine.build_compose_command(
            self.ffmpeg_exe, self.media_path, temp_path, preview_layout,
            trim=self._effective_trim(), speed=speed, deinterlace=self.media_interlaced,
            template_path=self.template_path, textures=textures, fast=True,
            encoder="h264_nvenc" if use_nvenc else "libx264",
            transform=self._transform_filters(),
        )

    def _loop_preview_job(self, token, layout, temp_path, content_width_frac, speed,
                          media_path=None):
        # None (todavia no se sabe) o True (ya funciono antes) -> se
        # prueba GPU primero. False (ya fallo antes en esta sesion) -> ni
        # se intenta, directo a CPU -- reintentar NVENC en cada preview
        # cuando ya se sabe que esta PC no lo tiene solo suma un fallo mas
        # lento antes de caer al fallback, sin ningun beneficio.
        try_nvenc = self._loop_preview_nvenc_available is not False
        cmd = self._build_loop_preview_cmd(layout, temp_path, speed, try_nvenc)
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                               creationflags=engine.CREATE_NO_WINDOW)
        ok = proc.returncode == 0 and os.path.exists(temp_path)

        if try_nvenc:
            self._loop_preview_nvenc_available = ok
            if not ok:
                # Sin GPU NVIDIA (o sin el driver que trae NVENC) -- se
                # reintenta esta misma vuelta con libx264 para que el
                # usuario de todas formas consiga su preview, en vez de
                # solo mostrarle el error.
                cmd = self._build_loop_preview_cmd(layout, temp_path, speed, False)
                proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                                       creationflags=engine.CREATE_NO_WINDOW)
                ok = proc.returncode == 0 and os.path.exists(temp_path)

        if token is not self._loop_preview_token:
            # El usuario pidio otro preview mientras este corria -- se
            # descarta (y se borra, no queda flotando en el temp).
            if ok:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return
        old_path = self._loop_preview_path
        self._loop_preview_path = temp_path if ok else None
        if old_path and old_path != temp_path:
            try:
                os.remove(old_path)
            except OSError:
                pass
        error = None
        if not ok:
            # Las ultimas lineas de stderr de ffmpeg son el motivo real del
            # fallo (archivo raro, filtro que no cuadra con este medio en
            # particular, etc.) -- sin esto solo se veia "no se pudo" sin
            # forma de saber por que.
            stderr_tail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
            error = "\n".join(stderr_tail[-6:]) if stderr_tail else f"ffmpeg salio con codigo {proc.returncode}"
        self._emit("onLoopPreviewReady", {
            "path": temp_path if ok else None,
            "error": error,
            "content_width_frac": content_width_frac,
            # De que medio salio este fragmento: la UI lo compara con el que
            # tiene cargado y descarta el que llegue tarde (ver
            # _invalidate_loop_preview, que ya lo corta de este lado).
            "media_path": media_path,
        })

    # ---------------------------------------------------------- generacion

    def output_would_overwrite(self):
        return bool(self.output_path and os.path.exists(self.output_path))

    def start_generation(self):
        if self._generating:
            # self.process todavia no existe reciennacido a esta altura
            # (_run_ffmpeg_job hace trabajo previo -- probar el audio,
            # armar el layout -- antes de lanzar el primer subprocess), asi
            # que chequear eso dejaba pasar clics repetidos durante ese
            # hueco. Esta bandera se prende ACA MISMO, sincronico, antes de
            # devolver la respuesta -- sin ventana de carrera posible. Sin
            # esto, clics repetidos en "Generar video" (el boton solo se
            # deshabilita del lado de JS DESPUES de que esta llamada
            # responde) disparaban 2-3 generaciones a la vez, todas usando
            # los MISMOS nombres de archivo temporal
            # (genvideo_loop.mp4/genvideo_concat.txt) -- se pisaban entre
            # si a mitad de escritura/lectura y fallaban con codigos de
            # salida raros (ffmpeg leyendo un archivo que otro hilo ya
            # habia borrado o seguia escribiendo).
            return {"ok": False, "error": "Ya hay una generación en curso."}
        if not self.media_path or not self.audio_path:
            return {"ok": False, "error": "Falta imagen/video o audio"}
        if self.media_is_video and not self.media_size:
            return {"ok": False, "error": "Todavía analizando el clip, intenta en un segundo..."}

        self.trim_range = self._effective_trim()

        if not self.output_path:
            self._update_default_output()

        self.cancel_requested = False
        self._generating = True
        threading.Thread(target=self._run_ffmpeg_job, daemon=True).start()
        return {"ok": True}

    def _run_ffmpeg_job(self):
        # Antes que nada: la copia liviana del previsualizador nunca la usa
        # la exportacion (sale siempre de media_path), asi que si todavia se
        # estaba armando no tiene sentido que le siga compitiendo CPU real a
        # esta generacion -- medido, exportar con esa copia corriendo a la
        # par tardaba 1.6x mas. Va aca (hilo de fondo) y no en
        # start_generation: terminate()+wait(timeout=2) podria demorar la
        # respuesta del click "Generar video" hasta 2s.
        self._cancel_preview_proxy_process()
        temp_unit = None
        temp_list = None
        self._generation_counter += 1
        gen_id = self._generation_counter
        try:
            info = engine.probe_media(self.ffmpeg_exe, self.audio_path)
            duration = info["duration"]

            speed = 1.0
            if self.media_is_video:
                try:
                    speed = float(self.speed.rstrip("x"))
                except ValueError:
                    speed = 1.0

            template_path = self.template_path
            template_box = self.template_box
            speed_note = f" · {speed:g}x" if speed != 1.0 else ""
            tpl_note = " con plantilla" if template_path else ""

            layout, width, height = engine.build_layout(
                self.media_size, self.media_is_video,
                self.scale_pct, template_box if template_path else None,
                crop_aspect=self.current_crop_aspect(),
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

                temp_unit = os.path.join(tempfile.gettempdir(), f"genvideo_loop_{gen_id}.mp4")
                self._push_status(f"Componiendo el loop a {width}x{height}{tpl_note}{speed_note}...")
                returncode = self._run_ffmpeg(
                    engine.build_compose_command(
                        self.ffmpeg_exe, self.media_path, temp_unit, layout,
                        trim=trim, speed=speed, deinterlace=self.media_interlaced,
                        template_path=template_path, textures=textures,
                        transform=self._transform_filters(),
                    ),
                    unit_duration,
                )
                if self.cancel_requested or returncode != 0:
                    self._on_job_done(returncode)
                    return

                # FASE 2: encadenar el loop con concat + copia directa + beat
                unit_info = engine.probe_media(self.ffmpeg_exe, temp_unit)
                real_unit = unit_info["duration"] or unit_duration
                if duration and real_unit:
                    repeats = max(1, math.ceil(duration / real_unit) + 1)
                else:
                    repeats = 1
                temp_list = os.path.join(tempfile.gettempdir(), f"genvideo_concat_{gen_id}.txt")
                engine.write_concat_list(temp_unit, repeats, temp_list)

                self._push_status("Generando video (loop + beat)...")
                returncode = -1
                for strategy in strategies:
                    audio_args = engine.audio_strategy_args(strategy, info["sample_rate"], self.audio_peak_db)
                    cmd = engine.build_mux_command(
                        self.ffmpeg_exe, temp_list, self.audio_path,
                        self.output_path, duration, audio_args,
                    )
                    returncode = self._run_ffmpeg(cmd, duration)
                    if returncode == 0 or self.cancel_requested:
                        break
            elif any(t[3] is not None for t in textures):
                # Imagen fija con una textura de VIDEO encima. El fotograma
                # cambia (lo mueve la textura), pero se REPITE cada vuelta de
                # la textura, porque la foto no cambia nunca -- asi que se
                # puede componer UNA vuelta y repetirla por copia directa,
                # igual que un clip de video, en vez de encodear el beat de
                # punta a punta. Medido: 103s -> 5.8s en un beat de 3:00 a
                # 2560x1440, mismo peso y PSNR 51 dB contra el resultado de
                # una pasada (ver build_texture_unit_command).
                #
                # real_unit queda en None cuando esa vuelta no se puede usar
                # (mas de una textura de video, o no se pudo leer el largo de
                # la unidad) -- ahi se cae al camino de una pasada de siempre,
                # que no necesita saber ningun largo.
                real_unit = None
                if engine.texture_unit_is_possible(textures):
                    temp_unit = os.path.join(tempfile.gettempdir(), f"genvideo_tex_{gen_id}.mp4")
                    # El largo de la unidad lo decide la textura (ver
                    # build_texture_unit_command) -- se sondea solo para que la
                    # barra se mueva en esta fase. Es una ESTIMACION nomas: el
                    # largo que vale es el del archivo escrito, mas abajo.
                    tex_video = next(t[0] for t in textures if t[3] is not None)
                    unit_estimate = engine.probe_media(self.ffmpeg_exe, tex_video)["duration"]
                    self._push_status(f"Componiendo la imagen a {width}x{height}{tpl_note}...")
                    returncode = self._run_ffmpeg(
                        engine.build_texture_unit_command(
                            self.ffmpeg_exe, self.media_path, temp_unit, layout,
                            template_path=template_path, textures=textures,
                            focus=self.current_crop(), transform=self._transform_filters(),
                            contain=self.current_crop_fit(),
                        ),
                        unit_estimate,
                    )
                    if self.cancel_requested or returncode != 0:
                        self._on_job_done(returncode)
                        return
                    # El largo real sale del archivo ya escrito, igual que en
                    # los otros dos caminos de fase 1.
                    real_unit = engine.probe_media(self.ffmpeg_exe, temp_unit)["duration"]

                returncode = -1
                if real_unit:
                    repeats = max(1, math.ceil(duration / real_unit) + 1) if duration else 1
                    temp_list = os.path.join(tempfile.gettempdir(), f"genvideo_concat_{gen_id}.txt")
                    engine.write_concat_list(temp_unit, repeats, temp_list)
                    self._push_status("Generando video (imagen + beat)...")
                    for strategy in strategies:
                        audio_args = engine.audio_strategy_args(strategy, info["sample_rate"], self.audio_peak_db)
                        cmd = engine.build_mux_command(
                            self.ffmpeg_exe, temp_list, self.audio_path,
                            self.output_path, duration, audio_args,
                        )
                        returncode = self._run_ffmpeg(cmd, duration)
                        if returncode == 0 or self.cancel_requested:
                            break
                else:
                    self._push_status(f"Generando video a {width}x{height}{tpl_note}...")
                    for strategy in strategies:
                        audio_args = engine.audio_strategy_args(strategy, info["sample_rate"], self.audio_peak_db)
                        cmd = engine.build_command(
                            self.ffmpeg_exe, self.media_path,
                            self.audio_path, self.output_path, duration, audio_args,
                            layout, template_path=template_path, textures=textures,
                            focus=self.current_crop(), transform=self._transform_filters(),
                            contain=self.current_crop_fit(),
                        )
                        returncode = self._run_ffmpeg(cmd, duration)
                        if returncode == 0 or self.cancel_requested:
                            break
            else:
                # Imagen quieta: mismo truco que un clip de video -- se compone
                # UNA unidad corta y la fase 2 la repite por copia directa.
                # Encodear los 1460 fotogramas del beat era pasar el filtro por
                # cada uno para obtener siempre lo mismo (ver STILL_UNIT_SECONDS
                # en engine.py: medido 13.4s -> 2.8s y 100 MB -> 9.6 MB).
                unit_seconds = min(engine.STILL_UNIT_SECONDS, duration or engine.STILL_UNIT_SECONDS)
                temp_unit = os.path.join(tempfile.gettempdir(), f"genvideo_still_{gen_id}.mp4")
                self._push_status(f"Componiendo la imagen a {width}x{height}{tpl_note}...")
                returncode = self._run_ffmpeg(
                    engine.build_still_unit_command(
                        self.ffmpeg_exe, self.media_path, temp_unit, layout, unit_seconds,
                        template_path=template_path, textures=textures,
                        focus=self.current_crop(), transform=self._transform_filters(),
                        contain=self.current_crop_fit(),
                    ),
                    unit_seconds,
                )
                if self.cancel_requested or returncode != 0:
                    self._on_job_done(returncode)
                    return

                unit_info = engine.probe_media(self.ffmpeg_exe, temp_unit)
                real_unit = unit_info["duration"] or unit_seconds
                if duration and real_unit:
                    repeats = max(1, math.ceil(duration / real_unit) + 1)
                else:
                    repeats = 1
                temp_list = os.path.join(tempfile.gettempdir(), f"genvideo_concat_{gen_id}.txt")
                engine.write_concat_list(temp_unit, repeats, temp_list)

                self._push_status("Generando video (imagen + beat)...")
                returncode = -1
                for strategy in strategies:
                    audio_args = engine.audio_strategy_args(strategy, info["sample_rate"], self.audio_peak_db)
                    cmd = engine.build_mux_command(
                        self.ffmpeg_exe, temp_list, self.audio_path,
                        self.output_path, duration, audio_args,
                    )
                    returncode = self._run_ffmpeg(cmd, duration)
                    if returncode == 0 or self.cancel_requested:
                        break

            self._on_job_done(returncode)
        except Exception as exc:
            self._on_job_error(str(exc))
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
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=engine.CREATE_NO_WINDOW,
        )
        # -progress pipe:1 manda muchas lineas "clave=valor" (frame=,
        # fps=, out_time=, speed=, etc.) ademas del stderr real de ffmpeg
        # (mezclado por stderr=STDOUT) -- se descartan esas para quedarse
        # solo con las lineas de diagnostico real. Antes no se guardaba
        # nada de esto: un fallo solo dejaba el codigo de salida, sin
        # forma de saber la causa real (ver _on_job_done).
        tail = []
        for line in self.process.stdout:
            line = line.strip()
            if line.startswith("out_time=") and duration:
                seconds = engine.parse_out_time(line)
                if seconds is not None:
                    frac = max(0.0, min(1.0, seconds / duration))
                    self._push_progress(frac)
            elif line and not re.match(r"^[a-z_]+=", line):
                tail.append(line)
                del tail[:-12]
        returncode = self.process.wait()
        self._last_ffmpeg_error = "\n".join(tail) if returncode != 0 else None
        return returncode

    def _push_status(self, text, color=None):
        self._emit("onStatus", {"text": text, "color": color})

    # Evento aparte de _push_status/onStatus -- ese lo usa la generacion
    # real (barra de progreso de abajo, "Componiendo el loop...",
    # "Generando video..."), y el status de la descarga por link vive en
    # otro lugar de la UI (debajo de "Descargador de videos"). Reusar el
    # mismo canal mezclaba las dos cosas en el mismo texto compartido.
    def _push_download_status(self, text, color=None):
        self._emit("onDownloadStatus", {"text": text, "color": color})

    def _push_progress(self, frac):
        """ffmpeg puede escupir muchas lineas out_time= por segundo -- cada
        una antes disparaba un evento por el pipe stdio + IPC de Electron,
        mucho mas caro que la llamada directa en memoria que usaba pywebview
        (y ademas lo que probablemente saturaba el pipe y trababa la
        generacion). Con 10 avisos por segundo la barra sigue viendose
        fluida y se evita ese cuello de botella."""
        now = time.monotonic()
        if frac < 1.0 and now - self._last_progress_emit_ts < 0.1:
            return
        self._last_progress_emit_ts = now
        self._emit("onProgress", frac)

    def _on_job_done(self, returncode):
        self.process = None
        self._generating = False
        if returncode == 0:
            self.cover_available = True
            payload = {
                "ok": True,
                "message": f"Listo: {os.path.basename(self.output_path)}",
                # El nombre suelto, sin el "Listo:" de adelante: la tarjeta de
                # resultado del pie lo muestra tal cual (ver showResultCard en
                # app.js) en vez de tener que recortarle el prefijo al mensaje.
                "filename": os.path.basename(self.output_path),
                "cover_available": True,
            }
        elif self.cancel_requested:
            payload = {"ok": False, "cancelled": True, "message": "Generación cancelada."}
        else:
            # El detalle tecnico (codigo de salida, tail de ffmpeg) ya no se
            # muestra en la UI -- queda en _last_ffmpeg_error por si hace
            # falta mirarlo con mas detalle (consola/logs).
            payload = {"ok": False, "message": "Hubo un problema al exportar\ninténtalo de nuevo. ˘﹏˘"}
        self._emit("onJobDone", payload)

    def _on_job_error(self, message):
        self.process = None
        self._generating = False
        self._emit("onJobError", {"message": message})

    def cancel_generation(self):
        if self.process and self.process.poll() is None:
            self.cancel_requested = True
            self.process.terminate()
        return {"ok": True}

    def open_video(self):
        if self.output_path and os.path.exists(self.output_path):
            if os.name == "nt":
                os.startfile(self.output_path)
            else:
                subprocess.Popen(["open", self.output_path])
        return {"ok": True}

    def open_output_folder(self):
        if self.output_path and os.path.exists(self.output_path):
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(self.output_path)])
            else:
                subprocess.Popen(["open", "-R", self.output_path])
        return {"ok": True}

    # ------------------------------------------------------ descarga por link

    def download_from_link(self, url):
        url = (url or "").strip()
        if not re.match(r"https?://", url):
            return {"ok": False, "error": "Pega un link válido (que empiece con https://)."}
        if self._downloading:
            # Mismo caso que start_generation, con la descarga: el campo del
            # link se deshabilita del lado de JS DESPUES de que esta llamada
            # responde, y con el campo deshabilitado el foco se va al body
            # -- ahi el Ctrl+V global de app.js deja de abstenerse y un
            # segundo pegado arranca OTRA descarga. Las dos escriben el
            # mismo archivo temporal (genvideo_descarga.*) y encima cada
            # trabajo borra los parciales que encuentra al arrancar, asi que
            # se cortaban la descarga entre ellas; en la UI se veia como que
            # el porcentaje iba y venia (5% -> 3% -> 6%...), porque los dos
            # hilos empujaban su propio avance al mismo cartel. La bandera
            # se prende ACA MISMO, sincronico, antes de responder.
            return {"ok": False, "error": "Ya hay una descarga en curso."}
        self._downloading = True
        threading.Thread(target=self._download_job, args=(url,), daemon=True).start()
        return {"ok": True}

    def _download_job(self, url):
        try:
            self._download_job_inner(url)
        finally:
            # Pase lo que pase (incluso un error inesperado que no llegue a
            # avisar a la UI): sin esto la bandera se quedaba prendida y no
            # se podia descargar mas nada hasta reiniciar la app.
            self._downloading = False

    def _download_job_inner(self, url):
        tmpdir = tempfile.gettempdir()
        # "genvideo_descarga" a secas (no el "genvideo_descarga." de antes,
        # con el punto) para que tambien agarre los sufijos numerados de
        # _download_video (genvideo_descarga_2.mp4, etc.) -- sin esto los
        # de descargas viejas se quedaban tirados para siempre.
        for name in os.listdir(tmpdir):
            if name.startswith("genvideo_descarga"):
                try:
                    os.remove(os.path.join(tmpdir, name))
                except OSError:
                    pass

        try:
            self._download_video(url, tmpdir)
            return
        except Exception as exc:
            yt_dlp_error = str(exc)

        # yt-dlp solo sabe extraer VIDEO -- un link a una imagen suelta (un
        # pin de Pinterest, una foto de un sitio cualquiera) siempre le
        # falla con algo como "No video formats found!". En vez de mostrar
        # ese error tal cual (tecnico y confuso), se intenta sacar la
        # imagen principal de la pagina (etiqueta og:image, la misma que
        # usan las previsualizaciones de links en WhatsApp/Twitter/etc.) y
        # cargarla directo -- si la pagina no tiene ninguna, recien ahi se
        # avisa que ese tipo de link no se puede.
        try:
            image_path = self._try_download_page_image(url)
        except Exception:
            image_path = None

        if image_path and self._set_image(image_path):
            self.media_display_name = None
            self._notify_state_changed()
            self._push_download_done(True, "Imagen descargada ✔")
            return

        if len(yt_dlp_error) > 140:
            yt_dlp_error = yt_dlp_error[:140] + "..."
        looks_like_image_link = "no video formats found" in yt_dlp_error.lower()
        if looks_like_image_link:
            message = 'Acá no se puede insertar link de imágenes, debes de copiar la imagen y pegarla :)'
        else:
            message = "No se pudo descargar\nIntenta con un nuevo enlace"
        self._push_download_done(False, message)

    def _download_video(self, url, tmpdir):
        from yt_dlp import YoutubeDL

        # Nombre unico por descarga (antes fijo, "genvideo_descarga.<ext>"):
        # bajar un segundo video mientras el primero seguia cargado en el
        # previsualizador (SIN sacarlo antes) escribia otro archivo con el
        # MISMO nombre y por lo tanto la MISMA ruta -- self.media_path
        # cambiaba de contenido pero no de texto, y cargarFuente (live-
        # preview.js) compara rutas para decidir si hay que recargar; con
        # la ruta identica pensaba que ya tenia ese medio y nunca volvia a
        # leer el archivo. El video se quedaba trabado en el anterior hasta
        # sacarlo a mano y recien ahi descargar (eso SI dejaba la ruta en
        # None de por medio, que es lo que de casualidad lo arreglaba).
        self._download_counter += 1
        nombre_salida = f"genvideo_descarga_{self._download_counter}.%(ext)s"

        # El porcentaje que reportaba yt-dlp RETROCEDIA a mitad de la
        # descarga (se veia 5% y volvia a 3%, y asi todo el rato): los
        # formatos de YouTube arriba de 720p vienen en fragmentos (DASH) y
        # ahi no hay tamano total real, solo total_bytes_estimate, que
        # yt-dlp recalcula con cada fragmento -- si el estimado crece, los
        # mismos bytes bajados valen menos por ciento. Si encima un
        # fragmento se reintenta (retries/fragment_retries mas abajo),
        # downloaded_bytes puede arrancar de cero otra vez.
        # Solucion, en dos partes:
        #   1. contar por FRAGMENTOS cuando se sabe cuantos son -- ese
        #      indice solo avanza, no depende de ninguna estimacion;
        #   2. nunca mostrar un numero menor al ya mostrado (shown), que
        #      cubre el resto de los casos.
        shown = {"pct": -1}

        def percent_of(d):
            total = d.get("total_bytes")
            frag_count = d.get("fragment_count")
            if not total and frag_count:
                return (d.get("fragment_index") or 0) * 100.0 / frag_count
            total = total or d.get("total_bytes_estimate")
            if not total:
                return None
            return (d.get("downloaded_bytes") or 0) * 100.0 / total

        def hook(d):
            if d.get("status") == "downloading":
                pct = percent_of(d)
                if pct is None:
                    # Ni total ni fragmentos (streams sin tamano declarado):
                    # al menos avisar que algo esta pasando, una sola vez.
                    if shown["pct"] < 0:
                        shown["pct"] = 0
                        self._push_download_status("Descargando...")
                    return
                # El 100% queda reservado para el "finished" de abajo: llegar
                # a 100 y quedarse ahi un rato mientras yt-dlp cierra el
                # archivo se lee como que se colgo.
                pct = max(shown["pct"], min(99, int(pct)))
                if pct == shown["pct"]:
                    return  # mismo entero que la ultima vez: nada nuevo que mostrar
                shown["pct"] = pct
                self._push_download_status(f"Descargando {pct}%")
            elif d.get("status") == "finished":
                shown["pct"] = 100
                self._push_download_status("Procesando la descarga...")

        opts = {
            # Sin audio cuando se puede (el beat lo pone la app) y max 1440p.
            # Se excluye AV1 (vcodec!*=av01): decodifica por software en esta
            # Mac (sin aceleracion por hardware salvo chips M3+) y resulto
            # ~2.5x mas lento que VP9 a la MISMA resolucion en pruebas reales
            # -- YouTube casi siempre ofrece VP9 tambien, asi que evitarlo no
            # cuesta calidad. Sin filtro de ext=mp4 en el primer intento:
            # arriba de 1080p YouTube casi nunca da mp4 (vp9/av1 vienen en
            # webm), y forzarlo ahi bloqueaba por completo llegar a 1440p.
            # format_sort "res" prioriza resolucion real primero; H.264 como
            # desempate si compite en la misma resolucion (rara vez pasa de
            # 1080p, pero decodifica mas rapido cuando esta disponible).
            "format": (
                "bv*[vcodec!*=av01][height<=1440]/b[height<=1440]/b"
            ),
            "format_sort": ["res", "vcodec:h264"],
            "outtmpl": os.path.join(tmpdir, nombre_salida),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            # quiet=True NO alcanza para apagar la barra de progreso propia
            # de yt-dlp -- esa se imprime aparte, con \r (sin salto de
            # linea real) para reescribir la misma linea de terminal. El
            # sidecar habla JSON por linea con Electron (una linea = un
            # mensaje); sin esto, esa barra se pegaba a la MISMA linea que
            # nuestro propio evento onDownloadStatus (json.dumps + print),
            # el parser de pythonBridge.js fallaba al leerla como JSON, y
            # tiraba la linea entera -- por eso el porcentaje nunca llegaba
            # a la UI aunque este hook ya lo calculaba bien.
            "noprogress": True,
            "progress_hooks": [hook],
            # yt-dlp necesita un motor de JavaScript para resolver las firmas
            # de YouTube (los "sig"/"n challenge" que la pagina calcula en
            # JS). Sin ninguno descarta casi todos los formatos y el
            # extractor acaba pidiendo cookies -- el error que salia era
            # "Sign in to confirm you're not a bot", que despistaba porque el
            # problema no era la sesion sino el motor faltante (con -v se ve
            # el verdadero: "JS runtimes: none"). yt-dlp solo habilita deno
            # por defecto: se declaran los tres que soporta y usa el que este
            # instalado (aca node, que ya viene con el entorno de Electron).
            "js_runtimes": {"deno": {}, "node": {}, "bun": {}},
            "ffmpeg_location": self.ffmpeg_exe,
            "merge_output_format": "mp4",
            # Este video en particular (probado varias veces) corta la
            # conexion o tira 403 a mitad de la descarga -- mas reintentos
            # le dan chance de recuperarse solo en vez de dejar un archivo
            # a medio bajar.
            "retries": 10,
            "fragment_retries": 10,
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
        # yt-dlp no siempre lanza una excepcion cuando el rename final
        # (.part -> nombre real) o el merge fallan a mitad de camino (ver
        # "ERROR: Unable to rename file" en el log -- eso lo imprime yt-dlp
        # y sigue de largo, no frena la ejecucion) -- sin este chequeo la
        # app se quedaba con self.media_path apuntando a un archivo que
        # nunca se termino de escribir, y cualquier ffmpeg despues (loop
        # preview, generar) fallaba en silencio con "No such file or
        # directory" -- se veia como que la app se rompio, no como que la
        # descarga fallo.
        if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
            raise RuntimeError("La descarga se cortó a la mitad, probá de nuevo.")
        title = info.get("title") or "Video descargado"

        self._set_video(path)
        self.media_display_name = title
        self._notify_state_changed()
        self._push_download_done(True, "Video descargado ✔ listo para el loop")

    def _try_download_page_image(self, url):
        """Busca la etiqueta og:image de la pagina (la misma que usan las
        previsualizaciones de link de WhatsApp/Twitter/iMessage) y
        descarga esa imagen. Devuelve la ruta local, o None si la pagina
        no tiene una."""
        import urllib.request

        headers = {"User-Agent": "Mozilla/5.0 (compatible; GenVideo/1.0)"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read(500_000).decode("utf-8", errors="ignore")

        match = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html,
        ) or re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html,
        )
        if not match:
            return None
        image_url = match.group(1).replace("&amp;", "&")

        req = urllib.request.Request(image_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()

        ext = os.path.splitext(image_url.split("?", 1)[0])[1].lower()
        if ext not in engine.IMAGE_EXTS:
            ext = ".jpg"
        # Nombre unico -- mismo motivo que en _download_video: un nombre
        # fijo aca hacia que pegar dos links de imagen seguidos (sin sacar
        # el primero) dejara la ruta identica y el previsualizador nunca
        # recargaba el archivo nuevo.
        self._download_counter += 1
        path = os.path.join(
            tempfile.gettempdir(), f"genvideo_descarga_{self._download_counter}{ext}"
        )
        with open(path, "wb") as fh:
            fh.write(data)
        try:
            with Image.open(path):
                pass
        except Exception:
            return None
        return path

    def _push_download_done(self, ok, message):
        self._emit("onDownloadDone", {"ok": ok, "message": message})
