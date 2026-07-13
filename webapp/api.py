"""
Puente Python <-> JS para la nueva UI (pywebview). Reproduce el estado que
hoy vive en la clase App de app.py (plantilla, capas de textura, presets,
nombre de salida) y expone metodos llamables desde JS
(`pywebview.api.metodo(...)`) que devuelven datos JSON en vez de tocar
widgets. Los metodos que hacen trabajo pesado (sondear medios, generar el
video) se agregan en fases posteriores junto con el resto del flujo de UI
que los dispara.
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
from io import BytesIO

import imageio_ffmpeg
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


class Api:
    def __init__(self):
        # OJO: prefijo "_" a proposito -- pywebview recorre recursivamente
        # los atributos del objeto expuesto a JS buscando "sub-APIs"
        # anidadas, y ese recorrido revienta al toparse con los objetos
        # COM/.NET internos de un webview.Window real. El prefijo "_" hace
        # que pywebview ignore este atributo en esa introspeccion.
        self._window = None  # se setea con set_window() despues de crear la ventana

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

        self.output_path = None
        self.user_chose_output = False
        self.custom_output_name = ""

        self._template_paths = {}
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

        self._restore_template_from_config()
        self._restore_textures_from_config()

    def set_window(self, window):
        self._window = window
        window.events.loaded += self._setup_dom_events

    def _notify_state_changed(self):
        """Empuja el estado actual a JS -- reemplazo de self.after(0, ...)
        para los resultados de trabajo en threads (sondeo de video/audio,
        descargas, generacion)."""
        if self._window is None:
            return
        self._window.evaluate_js(
            f"window.onStateChanged && window.onStateChanged({json.dumps(self.get_state())})"
        )

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
            "appearance": self.config_data.get("appearance", "dark"),
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

    def choose_output_path(self):
        """Abre el dialogo nativo 'Guardar como' (window.create_file_dialog)."""
        if self._window is None:
            return self.output_path
        import webview
        initial = self.output_path or "video.mp4"
        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG,
            directory=os.path.dirname(initial) if os.path.dirname(initial) else "",
            save_filename=os.path.basename(initial),
            file_types=("Video MP4 (*.mp4)",),
        )
        if result:
            path = result if isinstance(result, str) else result[0]
            self.output_path = path
            self.user_chose_output = True
        return self.output_path

    # ----------------------------------------------------------- plantilla

    def _refresh_template_list(self):
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
        active = None
        if self.template_path:
            active = next((d for d, p in self._template_paths.items() if p == self.template_path), None)
        return {
            "templates": [{"name": d, "path": p} for d, p in sorted(self._template_paths.items())],
            "active": active,
        }

    def _restore_template_from_config(self):
        saved = self.config_data.get("template")
        if saved and os.path.exists(saved):
            self.set_template(saved)

    def browse_template(self):
        """Abre el dialogo nativo para elegir una plantilla PNG y la activa
        directo -- comparte con la opcion "Buscar archivo..." del select."""
        if self._window is None:
            return {"ok": False, "error": "La ventana todavía no está lista"}
        import webview
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Plantilla PNG (*.png)",),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        out = self.set_template(path)
        out["state"] = self.get_state()
        return out

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

    def delete_template(self):
        """Borra el archivo (si vive en la carpeta administrada) y desactiva
        la plantilla. La confirmacion 'seguro que quieres borrar' vive en la
        UI (modal HTML), no aca."""
        if not self.template_path:
            return {"ok": False, "error": "No hay plantilla activa"}
        path = self.template_path
        if os.path.dirname(path) == engine.TEMPLATES_DIR:
            try:
                os.remove(path)
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
        display = next((d for d, p in self._template_paths.items() if p == path), None)
        if display:
            self._template_paths.pop(display, None)
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
        return [{"name": d, "path": p} for d, p in sorted(self._texture_paths.items())]

    def browse_texture_file(self):
        """Abre el dialogo nativo para elegir un archivo de imagen --
        comparte con "+ Agregar textura" y con "Buscar archivo..." del
        select de cada capa. Devuelve la ruta elegida (o None)."""
        if self._window is None:
            return None
        import webview
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Imagen (%s)" % ";".join(f"*{e}" for e in sorted(engine.IMAGE_EXTS)),),
        )
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
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

    # ----------------------------------------------------------- apariencia

    def set_appearance(self, mode):
        """Tema claro/oscuro de la UI -- misma clave de config que usaba la
        version de customtkinter, asi la preferencia sobrevive el cambio."""
        self.config_data["appearance"] = "dark" if mode == "dark" else "light"
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

    def browse_media(self):
        """Abre el dialogo nativo 'Abrir' para elegir imagen/video/audio
        (multi-seleccion, igual que el click sobre el dropzone de hoy)."""
        if self._window is None:
            return {"ok": False, "error": "La ventana todavía no está lista"}
        import webview
        all_media = engine.IMAGE_EXTS | engine.VIDEO_EXTS | engine.AUDIO_EXTS
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=(
                # pywebview valida el filtro con una regex que NO permite
                # comas en la descripcion (solo \w y espacios) -- ver
                # parse_file_type en webview/util.py.
                "Imagen video o audio (%s)" % ";".join(f"*{e}" for e in sorted(all_media)),
                "Todos los archivos (*.*)",
            ),
        )
        if not result:
            return {"ok": True, "ignored": [], "state": self.get_state()}
        return self.ingest_paths(list(result))

    def ingest_paths(self, paths):
        """Reparte archivos por extension: imagen/video -> medio principal,
        audio -> pista de audio. Los que no matchean ninguna se ignoran."""
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
        parts.append("loop hasta el final del beat")
        self.media_kind_text = " · ".join(parts)
        self._notify_state_changed()

    def _set_audio(self, path):
        self.audio_path = path
        self.audio_kind_text = "Audio · analizando..."
        self.audio_clip_warning = None
        self._update_default_output()
        threading.Thread(target=self._measure_peak_job, args=(path,), daemon=True).start()

    def _measure_peak_job(self, path):
        info = engine.probe_media(self.ffmpeg_exe, path)
        peak = engine.measure_peak_db(self.ffmpeg_exe, path)
        if path != self.audio_path:
            return  # el usuario ya cambio de audio
        prefix = "Audio"
        formatted = engine.format_duration(info["duration"])
        if formatted:
            prefix = f"Audio · {formatted}"
        if peak is None:
            self.audio_kind_text = prefix
            self.audio_clip_warning = None
        elif peak > 0:
            self.audio_kind_text = f"{prefix} · pico máx +{peak:.1f} dB ⚠ pasa de 0, puede clipear"
            self.audio_clip_warning = True
        else:
            self.audio_kind_text = f"{prefix} · pico máx {peak:.1f} dB ✔ no clipea"
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
        self.media_kind_text = f"Imagen pegada del portapapeles · {self.media_size[0]}x{self.media_size[1]}"
        return {"ok": True, "pasted_image": True, "state": self.get_state()}

    # ------------------------------------------------------- arrastrar y soltar

    def _setup_dom_events(self):
        """Se llama cuando la pagina termina de cargar (window.events.loaded).
        Registra los handlers de drag-and-drop DESDE PYTHON, escopados por
        seccion (dropzone general / Plantilla / Texturas) -- pywebview
        inyecta pywebviewFullPath en cada archivo soltado, asi que no hace
        falta ningun puente extra en JS para esto (a diferencia de
        tkinterdnd2, que si lo necesitaba con un parametro "skip" manual)."""
        # El body entero acepta medios (como la app vieja: soltar en
        # CUALQUIER parte de la ventana carga imagen/video/audio) -- las
        # secciones especificas cortan la propagacion, asi que un drop
        # sobre Plantilla/Texturas no llega al body. Ademas, sin un
        # preventDefault global, soltar un archivo fuera de una zona
        # registrada hacia que el webview NAVEGARA al archivo (la UI
        # desaparecia y quedaba la imagen suelta).
        self._bind_drop_zone("body", self._on_dropzone_drop)
        self._bind_drop_zone("#dropzone", self._on_dropzone_drop)
        self._bind_drop_zone("#template-section", self._on_template_drop)
        self._bind_drop_zone("#texture-section", self._on_texture_drop)

    def _bind_drop_zone(self, selector, on_drop):
        # OJO: se usa Element.on(...) en vez de el.events.<nombre> += ... --
        # el contenedor .events depende de una introspeccion JS
        # (__generate_events) que resulto poco confiable: a veces no
        # detecta "dragenter" en un elemento recien obtenido con
        # get_element() y tira AttributeError. Element.on() registra el
        # listener directo (addEventListener) sin pasar por esa deteccion.
        from webview.dom import DOMEventHandler
        el = self._window.dom.get_element(selector)
        if el is None:
            return

        def handle_drop(event):
            self._toggle_drag_active(selector, False)
            on_drop(event)

        el.on("dragenter", DOMEventHandler(
            lambda e: self._toggle_drag_active(selector, True), True, True))
        el.on("dragover", DOMEventHandler(lambda e: None, True, True))
        el.on("dragleave", DOMEventHandler(
            lambda e: self._toggle_drag_active(selector, False), True, True))
        el.on("drop", DOMEventHandler(handle_drop, True, True))

    def _toggle_drag_active(self, selector, active):
        if self._window is None:
            return
        action = "add" if active else "remove"
        self._window.evaluate_js(
            f"document.querySelector({json.dumps(selector)}).classList.{action}('drag-active')"
        )

    @staticmethod
    def _dropped_paths(event):
        files = (event.get("dataTransfer") or {}).get("files") or []
        return [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]

    def _on_dropzone_drop(self, event):
        paths = self._dropped_paths(event)
        if not paths:
            return
        self.ingest_paths(paths)
        self._notify_state_changed()

    def _on_template_drop(self, event):
        """Soltar un PNG sobre la seccion de Plantilla lo usa directo, sin
        pasar por el buscador de archivos."""
        png = next((p for p in self._dropped_paths(event) if p.lower().endswith(".png")), None)
        if png is None:
            return
        self.set_template(png)
        self._notify_state_changed()

    def _on_texture_drop(self, event):
        """Soltar una o mas imagenes sobre la seccion de Texturas (fuera de
        una capa existente) agrega una capa nueva por archivo valido."""
        added = False
        for path in self._dropped_paths(event):
            if os.path.splitext(path)[1].lower() not in engine.IMAGE_EXTS:
                continue
            try:
                with Image.open(path):
                    pass
            except Exception:
                continue
            result = self.add_texture_layer(path)
            added = added or result.get("ok", False)
        if added:
            self._notify_state_changed()

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

    # ---------------------------------------------------------- generacion

    def output_would_overwrite(self):
        return bool(self.output_path and os.path.exists(self.output_path))

    def start_generation(self):
        if not self.media_path or not self.audio_path:
            return {"ok": False, "error": "Falta imagen/video o audio"}
        if self.media_is_video and not self.media_size:
            return {"ok": False, "error": "Todavía analizando el clip, intenta en un segundo..."}

        self.trim_range = None
        if self.media_is_video:
            start, end = self.trim_start, self.trim_end
            duration = self.media_duration
            # Solo recortamos si el rango es mas angosto que el clip completo
            if start > 0.05 or (duration and end < duration - 0.35):
                self.trim_range = (start, end)

        if not self.output_path:
            self._update_default_output()

        self.cancel_requested = False
        threading.Thread(target=self._run_ffmpeg_job, daemon=True).start()
        return {"ok": True}

    def _run_ffmpeg_job(self):
        temp_unit = None
        temp_list = None
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

                temp_unit = os.path.join(tempfile.gettempdir(), "genvideo_loop.mp4")
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
                temp_list = os.path.join(tempfile.gettempdir(), "genvideo_concat.txt")
                engine.write_concat_list(temp_unit, repeats, temp_list)

                self._push_status("Generando video (loop + beat)...")
                returncode = -1
                for strategy in strategies:
                    audio_args = engine.audio_strategy_args(strategy, info["sample_rate"])
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
                    audio_args = engine.audio_strategy_args(strategy, info["sample_rate"])
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
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=engine.CREATE_NO_WINDOW,
        )
        for line in self.process.stdout:
            line = line.strip()
            if line.startswith("out_time=") and duration:
                seconds = engine.parse_out_time(line)
                if seconds is not None:
                    frac = max(0.0, min(1.0, seconds / duration))
                    self._push_progress(frac)
        return self.process.wait()

    def _push_status(self, text, color=None):
        if self._window is None:
            return
        self._window.evaluate_js(
            f"window.onStatus && window.onStatus({json.dumps({'text': text, 'color': color})})"
        )

    def _push_progress(self, frac):
        if self._window is None:
            return
        self._window.evaluate_js(f"window.onProgress && window.onProgress({frac})")

    def _on_job_done(self, returncode):
        self.process = None
        if returncode == 0:
            payload = {"ok": True, "message": f"Listo: {os.path.basename(self.output_path)}"}
        elif self.cancel_requested:
            payload = {"ok": False, "cancelled": True, "message": "Generación cancelada."}
        else:
            payload = {"ok": False, "message": f"Error al generar el video (código {returncode})."}
        if self._window is not None:
            self._window.evaluate_js(f"window.onJobDone && window.onJobDone({json.dumps(payload)})")

    def _on_job_error(self, message):
        self.process = None
        if self._window is not None:
            self._window.evaluate_js(
                f"window.onJobError && window.onJobError({json.dumps({'message': message})})"
            )

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
                    self._push_status(f"Descargando video... {pct}")
                elif d.get("status") == "finished":
                    self._push_status("Procesando la descarga...")

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

            self._set_video(path)
            self.media_display_name = title
            self._notify_state_changed()
            self._push_download_done(True, "Video descargado ✔ listo para el loop")
        except Exception as exc:
            message = str(exc)
            if len(message) > 140:
                message = message[:140] + "..."
            self._push_download_done(False, f"No se pudo descargar: {message}")

    def _push_download_done(self, ok, message):
        if self._window is None:
            return
        self._window.evaluate_js(
            f"window.onDownloadDone && window.onDownloadDone({json.dumps({'ok': ok, 'message': message})})"
        )

    # ------------------------------------------------------- tamano de ventana

    def resize_window(self, content_height):
        """JS llama esto despues de cada render() con la altura real del
        contenido (document.documentElement.scrollHeight) para que la
        ventana se ajuste vertical -- se siente como un dialogo/modal que
        se acomoda a lo que hay adentro, en vez de una ventana de tamano
        fijo con scroll interno. El ancho no se toca."""
        if self._window is None:
            return {"ok": True}
        import webview
        try:
            screens = webview.screens()
            max_h = max(400, int(screens[0].height * 0.92)) if screens else 900
        except Exception:
            max_h = 900
        height = max(420, min(int(content_height), max_h))
        self._window.resize(self._window.width, height)
        return {"ok": True}
