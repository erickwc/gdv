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


def _thumb_for(path, cache):
    """Miniatura chica (data URI) para una tarjeta de galeria, con cache en
    memoria por ruta -- list_templates()/list_available_textures() se
    llaman seguido (cada refresh) y releer+reescalar el archivo cada vez
    seria trabajo de disco de sobra."""
    cached = cache.get(path)
    if cached is not None:
        return cached
    try:
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
        self.media_size = None
        self.media_duration = None
        self.media_interlaced = False
        self.media_thumb = None       # data URI o None
        self.media_kind_text = None   # texto descriptivo del chip
        self.media_display_name = None  # titulo de yt-dlp, si se descargo por link
        self.trim_range = None        # (start, end) calculado al generar, o None (clip completo)
        self.trim_start = 0.0
        self.trim_end = 1.0

        # Ajustar imagen (zoom + punto de interes) -- solo aplica a fotos.
        # zoom en % (100-300), focus_x/focus_y en fraccion 0..1.
        self.media_focus_preview = None  # data URI de la miniatura para el canvas
        self.focus_zoom_pct = 100
        self.focus_x = 0.5
        self.focus_y = 0.5

        self.audio_path = None
        self.audio_kind_text = None
        self.audio_clip_warning = None  # True/False/None (None = sin medir todavia)
        self.audio_peak_db = None  # pico real en dB -- ver audio_strategy_args en engine.py

        self.output_path = None
        self.user_chose_output = False
        self.custom_output_name = ""

        self._template_paths = {}
        self._template_lock = threading.Lock()
        self.template_path = None
        self.template_box = None

        self._texture_paths = {}
        self.texture_layers = []  # lista de {"path","blend","opacity","scale"}
        self._texture_cache = {}
        self._texture_lock = threading.Lock()

        self.presets = [
            p for p in self.config_data.get("presets", [])
            if isinstance(p, dict) and p.get("name")
        ]

        self.scale_pct = 100
        self.speed = "1x"
        self.focus = None  # (zoom, focus_x, focus_y) o None = sin ajuste

        self.process = None
        self.cancel_requested = False
        self._generating = False  # ver start_generation -- evita clics repetidos disparando 2 generaciones a la vez
        self._generation_counter = 0  # sufijo unico para los temporales de _run_ffmpeg_job (ver ahi)
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

    # ------------------------------------------------------ estado hacia JS

    def get_state(self):
        return {
            "media_path": self.media_path,
            "media_filename": self.media_display_name or (
                os.path.basename(self.media_path) if self.media_path else None),
            "media_is_video": self.media_is_video,
            "media_size": self.media_size,
            "media_duration": self.media_duration,
            "media_interlaced": self.media_interlaced,
            "media_thumb": self.media_thumb,
            "media_kind_text": self.media_kind_text,
            "media_focus_preview": self.media_focus_preview,
            "focus_zoom_pct": self.focus_zoom_pct,
            "focus_x": self.focus_x,
            "focus_y": self.focus_y,
            "audio_path": self.audio_path,
            "audio_filename": os.path.basename(self.audio_path) if self.audio_path else None,
            "audio_kind_text": self.audio_kind_text,
            "audio_clip_warning": self.audio_clip_warning,
            "output_path": self.output_path,
            "custom_output_name": self.custom_output_name,
            "template_path": self.template_path,
            "template_box": self.template_box,
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
        with self._template_lock:
            self._template_paths = {
                display: path for display, path in self._template_paths.items()
                if os.path.dirname(path) != engine.TEMPLATES_DIR and os.path.exists(path)
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
        saved = self.config_data.get("template")
        if saved and os.path.exists(saved):
            self.set_template(saved)

    def set_template(self, path):
        try:
            box = engine.detect_template_window(path)
        except Exception as exc:
            return {"ok": False, "error": f"No se pudo leer la plantilla: {exc}"}
        if box is None:
            return {
                "ok": False,
                "error": "Esa plantilla no tiene zona transparente — el medio no se vería. "
                         "Usa un PNG con transparencia.",
            }
        display = os.path.splitext(os.path.basename(path))[0]
        with self._template_lock:
            self._template_paths[display] = path
        self.template_path = path
        # box viene en pixeles de la plantilla; a coordenadas del lienzo
        # (plantillas que no son 1920x1080 se escalan y CENTRAN)
        self.template_box = engine.template_canvas_box(path, box)
        self.config_data["template"] = path
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
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
        with self._template_lock:
            display = next((d for d, p in self._template_paths.items() if p == path), None)
            if display:
                self._template_paths.pop(display, None)
        self._template_thumb_cache.pop(path, None)
        if self.template_path == path:
            self.clear_template()
        return {"ok": True}

    # ------------------------------------------------------------ texturas

    def _refresh_available_textures(self):
        self._texture_paths = {
            display: path for display, path in self._texture_paths.items()
            if os.path.dirname(path) != engine.TEXTURES_DIR and os.path.exists(path)
        }
        if os.path.isdir(engine.TEXTURES_DIR):
            for name in sorted(os.listdir(engine.TEXTURES_DIR)):
                if os.path.splitext(name)[1].lower() in engine.IMAGE_EXTS:
                    display = os.path.splitext(name)[0]
                    self._texture_paths[display] = os.path.join(engine.TEXTURES_DIR, name)

    def list_available_textures(self):
        self._refresh_available_textures()
        return [
            {"name": d, "path": p, "thumb": _thumb_for(p, self._texture_thumb_cache)}
            for d, p in sorted(self._texture_paths.items())
        ]

    def register_texture_path(self, path):
        """Valida un archivo de imagen elegido en el dialogo nativo de
        Electron y lo registra como textura disponible -- equivalente a lo
        que hacia browse_texture_file() en la version pywebview antes de
        abrir el dialogo (que aca ya abrio Electron). Devuelve la ruta o
        None si no es una imagen valida."""
        try:
            with Image.open(path):
                pass
        except Exception:
            return None
        display = os.path.splitext(os.path.basename(path))[0]
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
        self.texture_layers = [
            dict(state) for state in layers_data
            if state.get("path") and os.path.exists(state["path"])
        ]
        # add_texture_layer() registra el archivo en _texture_paths (asi
        # aparece en la galeria, ver list_available_textures) -- restaurar
        # texture_layers directo del config, como arriba, se saltaba ese
        # registro. Los ajustes (opacidad/escala) igual se veian bien al
        # reabrir el preset porque esos salen de texture_layers, pero la
        # textura en si no aparecia en la galeria (ni la tarjeta activa)
        # porque list_available_textures() no la conocia todavia.
        for state in self.texture_layers:
            path = state["path"]
            display = os.path.splitext(os.path.basename(path))[0]
            self._texture_paths[display] = path

    def _persist_texture_layers(self):
        self.config_data["textures"] = self.texture_layers
        engine.save_config(self.config_data)

    def add_texture_layer(self, path):
        try:
            with Image.open(path):
                pass
        except Exception as exc:
            return {"ok": False, "error": f"No se pudo leer la textura: {exc}"}
        display = os.path.splitext(os.path.basename(path))[0]
        self._texture_paths[display] = path
        layer = {"path": path, "blend": "Aclarar", "opacity": 47, "scale": 100}
        self.texture_layers.append(layer)
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
        self.texture_layers = []
        for state in textures_data:
            path = state.get("path")
            if path and os.path.exists(path):
                self.texture_layers.append(dict(state))
            elif path:
                missing.append(os.path.basename(path))
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
        self.media_path = path
        self.media_is_video = False
        self.media_size = (width, height)
        self.media_duration = None
        self.media_interlaced = False
        self.media_thumb = _image_to_data_uri(thumb_src)
        self.media_kind_text = f"Imagen · {width}x{height}"
        self.media_focus_preview = _image_to_data_uri(focus_src)
        self.focus_zoom_pct = 100
        self.focus_x = 0.5
        self.focus_y = 0.5
        self.cover_available = False
        self._update_default_output()
        return True

    def _set_video(self, path):
        self.media_path = path
        self.media_is_video = True
        self.media_size = None
        self.media_duration = None
        self.media_interlaced = False
        self.media_thumb = None
        self.media_kind_text = "Video · analizando..."
        self.media_display_name = None
        self.cover_available = False
        self._update_default_output()
        threading.Thread(target=self._probe_video_job, args=(path,), daemon=True).start()

    def _probe_video_job(self, path):
        info = engine.probe_media(self.ffmpeg_exe, path)
        thumb_img = engine.extract_video_thumb(self.ffmpeg_exe, path)
        interlaced = engine.detect_interlaced(self.ffmpeg_exe, path)
        if path != self.media_path or not self.media_is_video:
            return  # el usuario ya cambio de medio
        self.media_size = info["video_size"]
        self.media_duration = info["duration"]
        self.media_interlaced = interlaced
        self.trim_start = 0.0
        self.trim_end = max(0.5, float(info["duration"] or 1.0))
        if thumb_img is not None:
            self.media_thumb = _image_to_data_uri(thumb_img)

        parts = ["Video"]
        formatted = engine.format_duration(info["duration"])
        if formatted:
            parts.append(formatted)
        if info["video_size"]:
            parts.append(f"{info['video_size'][0]}x{info['video_size'][1]}")
        if interlaced:
            parts.append("entrelazado (se corregirá)")
        self.media_kind_text = " · ".join(parts)
        self._notify_state_changed()

    def _set_audio(self, path):
        self.audio_path = path
        self.audio_kind_text = "Audio · analizando..."
        self.audio_clip_warning = None
        self.audio_peak_db = None
        self._update_default_output()
        threading.Thread(target=self._measure_peak_job, args=(path,), daemon=True).start()

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
        self.media_path = None
        self.media_is_video = False
        self.media_size = None
        self.media_duration = None
        self.media_interlaced = False
        self.media_thumb = None
        self.media_kind_text = None
        self.media_display_name = None
        self.media_focus_preview = None
        self.focus_zoom_pct = 100
        self.focus_x = 0.5
        self.focus_y = 0.5
        self.trim_start = 0.0
        self.trim_end = 1.0
        self.cover_available = False
        return {"ok": True, "state": self.get_state()}

    # -------------------------------------------------------- ajustar imagen

    def set_focus(self, zoom_pct, focus_x, focus_y):
        """Se llama en cada arrastre/zoom del canvas -- la geometria
        (recuadro final, recorte real) se calcula toda en JS (app.js,
        mismo algoritmo que el ImageFocusPicker de Tk); aca solo se
        guarda el resultado para usarlo al generar (build_focus_crop)."""
        self.focus_zoom_pct = max(100, min(300, round(zoom_pct)))
        self.focus_x = max(0.0, min(1.0, focus_x))
        self.focus_y = max(0.0, min(1.0, focus_y))
        return {"ok": True}

    def reset_focus(self):
        self.focus_zoom_pct = 100
        self.focus_x = 0.5
        self.focus_y = 0.5
        return {"ok": True, "state": self.get_state()}

    def current_focus(self):
        """(zoom_fraccion, focus_x, focus_y) para build_focus_crop, o None
        si no aplica (video, o sin medio cargado) -- usado por la Fase 8."""
        if self.media_is_video or not self.media_path:
            return None
        return (self.focus_zoom_pct / 100.0, self.focus_x, self.focus_y)

    def remove_audio(self):
        self.audio_path = None
        self.audio_kind_text = None
        self.audio_clip_warning = None
        self.audio_peak_db = None
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
        path = os.path.join(tempfile.gettempdir(), "genvideo_imagen_pegada.png")
        try:
            data.convert("RGB").save(path, "PNG")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not self._set_image(path):
            return {"ok": False, "error": "No se pudo procesar la imagen pegada"}
        self.media_kind_text = f"Imagen pegada ({self.media_size[0]}x{self.media_size[1]})"
        return {"ok": True, "pasted_image": True, "state": self.get_state()}

    # -------------------------------------------------- preparar texturas

    def _current_textures(self, canvas_w, canvas_h):
        """Prepara (con cache) cada capa activa y devuelve la lista lista
        para build_command/build_compose_command: [(ruta, modo, opacidad)]."""
        textures = []
        for state in self.texture_layers:
            path = state.get("path")
            if not path:
                continue
            try:
                prepared = self._prepare_texture(path, state.get("scale", 100), canvas_w, canvas_h)
            except Exception:
                prepared = path
            mode = engine.BLEND_MODES.get(state.get("blend", "Aclarar"), "lighten")
            textures.append((prepared, mode, state.get("opacity", 47) / 100.0))
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
        for path, mode, opacity in textures:
            cmd += ["-i", path]
            tex_layers.append((idx, mode, opacity))
            idx += 1
        fc = engine.build_filtergraph(
            layout, is_video=False, tpl_idx=tpl_idx, textures=tex_layers,
            focus=self.current_focus(),
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

    def save_cover(self, loop_time=None, mode="full"):
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
        no hay plantilla cargada (no existe "parte vacia" sin plantilla)."""
        if not self.cover_available or not self.output_path or not self.media_path:
            return {"ok": False, "error": "No hay portada disponible."}
        if self.media_is_video and not self.media_size:
            return {"ok": False, "error": "Todavía se está analizando el video."}

        template_box = self.template_box if self.template_path else None
        if mode not in ("full", "empty", "both") or not template_box:
            mode = "full"

        layout, _, _ = engine.build_layout(
            self.media_size, self.media_is_video, self.scale_pct, template_box,
        )
        textures = self._current_textures(*layout["canvas"])

        cmd = [self.ffmpeg_exe, "-y"]
        if self.media_is_video:
            start = self.trim_start
            if loop_time is not None:
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
        if self.template_path:
            cmd += ["-i", self.template_path]
            tpl_idx = idx
            idx += 1
        tex_layers = []
        for path, tex_mode, opacity in textures:
            cmd += ["-i", path]
            tex_layers.append((idx, tex_mode, opacity))
            idx += 1
        fc = engine.build_filtergraph(
            layout, is_video=False, tpl_idx=tpl_idx, textures=tex_layers,
            focus=self.current_focus(),
        )

        folder = os.path.dirname(self.output_path)
        base = os.path.splitext(os.path.basename(self.output_path))[0]
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
            x, y, w, h = template_box
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
        return {"mode": layout["mode"], "inner": inner, "canvas": canvas, "pos": pos}

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
            args=(token, layout, temp_path, content_width_frac, speed), daemon=True
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
        )

    def _loop_preview_job(self, token, layout, temp_path, content_width_frac, speed):
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
            else:
                # Imagenes: una sola pasada (ya es rapida a 10 fps)
                self._push_status(f"Generando video a {width}x{height}{tpl_note}...")
                returncode = -1
                for strategy in strategies:
                    audio_args = engine.audio_strategy_args(strategy, info["sample_rate"], self.audio_peak_db)
                    cmd = engine.build_command(
                        self.ffmpeg_exe, self.media_path,
                        self.audio_path, self.output_path, duration, audio_args,
                        layout, template_path=template_path, textures=textures,
                        focus=self.current_focus(),
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
                "cover_available": True,
            }
        elif self.cancel_requested:
            payload = {"ok": False, "cancelled": True, "message": "Generación cancelada."}
        else:
            detail = ""
            if self._last_ffmpeg_error:
                # Ultimas 2 lineas alcanzan para el mensaje corto de la UI --
                # el resto del tail queda en _last_ffmpeg_error por si hace
                # falta mirarlo con mas detalle (consola/logs).
                detail = ": " + " / ".join(self._last_ffmpeg_error.splitlines()[-2:])
            payload = {"ok": False, "message": f"Error al generar el video (código {returncode}){detail}."}
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
        threading.Thread(target=self._download_job, args=(url,), daemon=True).start()
        return {"ok": True}

    def _download_job(self, url):
        tmpdir = tempfile.gettempdir()
        for name in os.listdir(tmpdir):
            if name.startswith("genvideo_descarga."):
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
            message = f"No se pudo descargar: {yt_dlp_error}"
        self._push_download_done(False, message)

    def _download_video(self, url, tmpdir):
        from yt_dlp import YoutubeDL

        def hook(d):
            if d.get("status") == "downloading":
                # _percent_str viene como "  45.2%" (con espacios de relleno
                # para alinear en terminal) -- se redondea a entero para un
                # texto mas simple en la UI ("Descargando 45%").
                raw = (d.get("_percent_str") or "").strip().rstrip("%")
                try:
                    pct = f"{round(float(raw))}%"
                except ValueError:
                    pct = ""
                self._push_download_status(f"Descargando {pct}".rstrip())
            elif d.get("status") == "finished":
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
            "outtmpl": os.path.join(tmpdir, "genvideo_descarga.%(ext)s"),
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

    @staticmethod
    def _try_download_page_image(url):
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
        path = os.path.join(tempfile.gettempdir(), f"genvideo_descarga{ext}")
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
