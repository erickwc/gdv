// Fases 3-8: flujo de medios, ajustar imagen, plantilla, texturas, recorte
// del loop, velocidad, escala, presets, nombre de archivo/guardar como y
// generacion. El link de YouTube/Pinterest (yt-dlp) queda pendiente.

function $(sel) {
  return document.querySelector(sel);
}
function $$(sel) {
  return Array.from(document.querySelectorAll(sel));
}

let lastState = null;
let templatesCache = { templates: [], active: null };
let availableTextures = [];

const BLEND_MODE_OPTIONS = ["Aclarar", "Trama", "Multiplicar", "Superponer", "Luz suave", "Normal"];

function formatDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// --------------------------------------------------------------- render

function render(state) {
  lastState = state;
  renderChips(state);
  FocusPicker.applyState(state);
  renderTemplate(state);
  renderTextures(state);
  LoopSlider.applyState(state);
  renderSpeedControl(state);
  renderScale(state);
  renderPresets(state);
  renderOutput(state);
  fitWindowToContent();
}

// La ventana se ajusta a la altura real del contenido (en vez de tamano
// fijo con scroll interno) -- se siente mas como un dialogo que se
// acomoda a lo que hay adentro. requestAnimationFrame espera a que el
// layout ya este actualizado (secciones recien mostradas/ocultadas, etc.)
// antes de medir. El ancho no se toca, solo el alto.
function fitWindowToContent() {
  if (typeof pywebview === "undefined") return;
  requestAnimationFrame(() => {
    pywebview.api.resize_window(document.documentElement.scrollHeight);
  });
}

function refresh() {
  return pywebview.api.get_state().then(render);
}

function refreshTemplates() {
  return pywebview.api.list_templates().then((data) => {
    templatesCache = data;
    if (lastState) renderTemplate(lastState);
  });
}

function refreshAvailableTextures() {
  return pywebview.api.list_available_textures().then((list) => {
    availableTextures = list;
    if (lastState) renderTextures(lastState);
  });
}

function renderChips(state) {
  const mediaChip = $("#media-chip");
  if (state.media_path) {
    mediaChip.hidden = false;
    mediaChip.querySelector(".chip-name").textContent = state.media_filename;
    mediaChip.querySelector(".chip-kind").textContent = state.media_kind_text || "";
    const thumb = mediaChip.querySelector(".chip-thumb");
    thumb.style.backgroundImage = state.media_thumb ? `url(${state.media_thumb})` : "";
    thumb.style.backgroundSize = "cover";
  } else {
    mediaChip.hidden = true;
  }

  const audioChip = $("#audio-chip");
  if (state.audio_path) {
    audioChip.hidden = false;
    audioChip.querySelector(".chip-name").textContent = state.audio_filename;
    const kind = audioChip.querySelector(".chip-kind");
    kind.textContent = state.audio_kind_text || "";
    kind.style.color = state.audio_clip_warning ? "var(--red)" : "";
  } else {
    audioChip.hidden = true;
  }

  $("#focus-section").hidden = !state.show_focus;
  $("#trim-section").hidden = !state.show_trim;
  $("#speed-section").hidden = !state.show_speed;
  $("#scale-section").hidden = !state.show_scale;

  $("#generate-btn").disabled = !state.ready;
}

function handleResult(result) {
  if (result && result.state) {
    render(result.state);
  }
  if (result && result.ignored && result.ignored.length) {
    $("#status-text").textContent =
      "Ignorado (no es imagen, video ni audio): " + result.ignored.join(", ");
  }
}

// ----------------------------------------------------------- plantilla

function renderTemplate(state) {
  const select = $("#template-select");
  select.innerHTML = "";
  select.appendChild(new Option("Sin plantilla", ""));
  templatesCache.templates.forEach((t) => select.appendChild(new Option(t.name, t.path)));
  select.appendChild(new Option("Buscar archivo...", "__browse__"));
  select.value = state.template_path || "";

  $("#template-delete").disabled = !state.template_path;
  if (state.template_path && state.template_box) {
    const [x, y, w, h] = state.template_box;
    $("#template-info").textContent =
      `Ventana transparente detectada: ${w}x${h} en (${x}, ${y}) · salida 1920x1080`;
  } else {
    $("#template-info").textContent = "arrastra un .png aquí para usarlo de plantilla";
  }
}

// ------------------------------------------------------------ texturas

function renderTextures(state) {
  const container = $("#texture-layers");
  container.innerHTML = "";
  const layers = state.texture_layers || [];
  $("#texture-empty-hint").hidden = layers.length > 0;
  layers.forEach((layer, index) => container.appendChild(buildTextureLayerRow(layer, index)));

  const collapsed = !!state.textures_collapsed;
  $("#texture-arrow").textContent = collapsed ? "▶" : "▼";
  $("#texture-body").style.display = collapsed ? "none" : "";
  $("#texture-toggle").querySelector("h2").textContent =
    collapsed && layers.length ? `Texturas (${layers.length})` : "Texturas";
}

function buildTextureLayerRow(layer, index) {
  const row = document.createElement("div");
  row.className = "texture-layer";

  const fileSelect = document.createElement("select");
  fileSelect.className = "select select-sm";
  availableTextures.forEach((t) => fileSelect.appendChild(new Option(t.name, t.path)));
  fileSelect.appendChild(new Option("Buscar archivo...", "__browse__"));
  fileSelect.value = layer.path;
  fileSelect.addEventListener("change", (e) => {
    const value = e.target.value;
    if (value === "__browse__") {
      pywebview.api.browse_texture_file().then((path) => {
        if (!path) {
          renderTextures(lastState);
          return;
        }
        pywebview.api.update_texture_layer(index, { path }).then(() => {
          refreshAvailableTextures().then(refresh);
        });
      });
      return;
    }
    pywebview.api.update_texture_layer(index, { path: value }).then(() => refresh());
  });

  const blendSelect = document.createElement("select");
  blendSelect.className = "select select-sm";
  BLEND_MODE_OPTIONS.forEach((m) => blendSelect.appendChild(new Option(m, m)));
  blendSelect.value = layer.blend;
  blendSelect.addEventListener("change", (e) => {
    pywebview.api.update_texture_layer(index, { blend: e.target.value });
  });

  const opacityRow = buildSliderRow("Opacidad", layer.opacity, 0, 100, (v) => {
    pywebview.api.update_texture_layer(index, { opacity: v });
  });
  const scaleRow = buildSliderRow("Escala", layer.scale, 10, 300, (v) => {
    pywebview.api.update_texture_layer(index, { scale: v });
  });

  const deleteFileBtn = document.createElement("button");
  deleteFileBtn.className = "btn btn-icon";
  deleteFileBtn.title = "Eliminar archivo";
  deleteFileBtn.textContent = "🗑";
  deleteFileBtn.addEventListener("click", () => {
    const name = layer.path.split(/[\\/]/).pop();
    if (!confirm(`¿Eliminar "${name}" de la carpeta texturas? Se borra del disco y de cualquier capa que la esté usando.`)) return;
    pywebview.api.delete_texture_file(layer.path).then((r) => {
      if (!r.ok) {
        $("#status-text").textContent = r.error || "No se pudo eliminar la textura.";
        return;
      }
      refreshAvailableTextures().then(refresh);
    });
  });

  const removeBtn = document.createElement("button");
  removeBtn.className = "btn btn-icon";
  removeBtn.title = "Quitar capa";
  removeBtn.textContent = "✕";
  removeBtn.addEventListener("click", () => {
    pywebview.api.remove_texture_layer(index).then(() => refresh());
  });

  row.append(fileSelect, blendSelect, opacityRow, scaleRow, deleteFileBtn, removeBtn);
  return row;
}

// Fila generica slider+numero -- se usa para opacidad/escala de cada capa
// de textura. El cambio en vivo (arrastrar) NO dispara un refresh() de
// pantalla completa (eso reconstruiria el <input> a medio arrastre y
// cortaria el gesto) -- solo se persiste en Python.
function buildSliderRow(label, value, min, max, onChange) {
  const wrap = document.createElement("div");
  wrap.className = "control-row control-row-compact";

  const lbl = document.createElement("label");
  lbl.className = "control-label";
  lbl.textContent = label;

  const slider = document.createElement("input");
  slider.type = "range";
  slider.className = "slider";
  slider.min = min;
  slider.max = max;
  slider.value = value;

  const entry = document.createElement("input");
  entry.type = "text";
  entry.className = "num-entry";
  entry.value = value;

  const unit = document.createElement("span");
  unit.className = "unit";
  unit.textContent = "%";

  const commit = (v) => {
    v = Math.max(min, Math.min(max, Math.round(Number(v) || 0)));
    slider.value = v;
    entry.value = v;
    onChange(v);
  };
  slider.addEventListener("input", (e) => commit(e.target.value));
  entry.addEventListener("change", (e) => commit(e.target.value));
  entry.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit(e.target.value);
  });

  wrap.append(lbl, slider, entry, unit);
  return wrap;
}

// --------------------------------------------------------------- escala

function scaleLabel(pct) {
  if (pct === 100) return "100% · tamaño natural";
  if (pct < 100) return `${pct}% · con bordes`;
  return `${pct}% · ampliada`;
}

function renderScale(state) {
  $("#scale-slider").value = state.scale_pct;
  $("#scale-entry").value = state.scale_pct;
  $("#scale-value-label").textContent = scaleLabel(state.scale_pct);
}

function commitScale(v) {
  v = Math.max(40, Math.min(200, Math.round(Number(v) || 100)));
  $("#scale-slider").value = v;
  $("#scale-entry").value = v;
  $("#scale-value-label").textContent = scaleLabel(v);
  FocusPicker.setScalePct(v);
  pywebview.api.set_scale_pct(v);
}

// ----------------------------------------------------------- velocidad

function renderSpeedControl(state) {
  $$(".segmented-item").forEach((btn) => {
    btn.classList.toggle("segmented-active", btn.dataset.value === state.speed);
  });
}

// -------------------------------------------------------------- presets

function renderPresets(state) {
  const select = $("#preset-select");
  select.innerHTML = "";
  const names = state.presets || [];
  if (!names.length) {
    select.appendChild(new Option("(sin presets guardados)", ""));
  } else {
    names.forEach((n) => select.appendChild(new Option(n, n)));
  }
  $("#preset-rename").disabled = !names.length;
  $("#preset-delete").disabled = !names.length;
}

// ------------------------------------------------------- nombre / salida

function renderOutput(state) {
  if (document.activeElement !== $("#filename-entry")) {
    $("#filename-entry").value = state.custom_output_name || "";
  }
  $("#output-label").textContent = state.output_path || "(se definirá al elegir el audio)";
}

// ---------------------------------------------------------- generacion

function setGeneratingUI(active) {
  $("#generate-btn").disabled = active || !(lastState && lastState.ready);
  $("#output-browse").disabled = active;
  $("#cancel-btn").hidden = !active;
  if (active) {
    $("#open-video-btn").hidden = true;
    $("#open-folder-btn").hidden = true;
    $("#progress-bar").hidden = false;
    $("#progress-fill").style.width = "0%";
  }
}

function beginGeneration() {
  pywebview.api.start_generation().then((r) => {
    if (!r.ok) {
      $("#status-text").textContent = r.error || "No se pudo iniciar la generación.";
      return;
    }
    setGeneratingUI(true);
    $("#status-text").textContent = "Preparando...";
    $("#status-text").style.color = "";
  });
}

window.onProgress = (frac) => {
  $("#progress-fill").style.width = `${Math.round(frac * 100)}%`;
};

window.onStatus = (payload) => {
  $("#status-text").textContent = payload.text;
};

window.onJobDone = (payload) => {
  setGeneratingUI(false);
  $("#status-text").textContent = payload.message;
  $("#status-text").style.color = payload.ok ? "var(--accent)" : (payload.cancelled ? "" : "var(--red)");
  if (payload.ok) {
    $("#open-video-btn").hidden = false;
    $("#open-folder-btn").hidden = false;
    $("#progress-fill").style.width = "100%";
  } else {
    $("#progress-bar").hidden = true;
  }
};

window.onJobError = (payload) => {
  setGeneratingUI(false);
  $("#progress-bar").hidden = true;
  $("#status-text").textContent = "Error inesperado.";
  $("#status-text").style.color = "var(--red)";
  alert(payload.message);
};

// Python empuja el estado cuando termina un trabajo en segundo plano
// (sondeo de video/audio, etc.) -- reemplazo de self.after(0, ...).
window.onStateChanged = render;

// ------------------------------------------------------- link (yt-dlp)

function startDownload(url) {
  if (!/^https?:\/\//i.test(url)) {
    $("#status-text").textContent = "Pega un link válido (que empiece con https://).";
    $("#status-text").style.color = "var(--red)";
    return;
  }
  $("#link-btn").disabled = true;
  $("#link-btn").textContent = "Bajando...";
  $("#status-text").textContent = "Obteniendo información del video...";
  $("#status-text").style.color = "";
  pywebview.api.download_from_link(url).then((r) => {
    if (!r.ok) {
      $("#link-btn").disabled = false;
      $("#link-btn").textContent = "Descargar";
      $("#status-text").textContent = r.error;
      $("#status-text").style.color = "var(--red)";
    }
  });
}

window.onDownloadDone = (payload) => {
  $("#link-btn").disabled = false;
  $("#link-btn").textContent = "Descargar";
  if (payload.ok) {
    $("#link-input").value = "";
    refresh();
  }
  $("#status-text").textContent = payload.message;
  $("#status-text").style.color = payload.ok ? "var(--accent)" : "var(--red)";
};

// ---------------------------------------------------------------- init

window.addEventListener("pywebviewready", () => {
  FocusPicker.init();
  LoopSlider.init();

  Promise.all([refreshTemplates(), refreshAvailableTextures()]).then(refresh);

  // ------------------------------------------------------- archivos
  $("#dropzone").addEventListener("click", () => {
    pywebview.api.browse_media().then(handleResult);
  });
  $("#media-chip").querySelector(".btn-icon").addEventListener("click", () => {
    pywebview.api.remove_media().then((r) => render(r.state));
  });
  $("#audio-chip").querySelector(".btn-icon").addEventListener("click", () => {
    pywebview.api.remove_audio().then((r) => render(r.state));
  });

  // El evento "paste" (no keydown) da acceso directo a clipboardData: si
  // hay texto y es un link, se descarga sin pasar por Python; si no, se
  // delega en Python (imagen/archivos, via PIL.ImageGrab / NSPasteboard).
  document.addEventListener("paste", (e) => {
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    const text = ((e.clipboardData && e.clipboardData.getData("text/plain")) || "").trim();
    if (/^https?:\/\//i.test(text)) {
      $("#link-input").value = text;
      startDownload(text);
      return;
    }
    pywebview.api.paste_from_clipboard().then((result) => {
      if (result && result.empty) {
        $("#status-text").textContent = "El portapapeles no tiene una imagen ni archivos.";
        return;
      }
      if (result && !result.ok) {
        $("#status-text").textContent = result.error || "No se pudo pegar.";
        return;
      }
      handleResult(result);
    });
  });

  // ------------------------------------------------------- link (yt-dlp)
  $("#link-btn").addEventListener("click", () => {
    startDownload($("#link-input").value.trim());
  });
  $("#link-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") startDownload($("#link-input").value.trim());
  });

  // ------------------------------------------------------- plantilla
  $("#template-select").addEventListener("change", (e) => {
    const value = e.target.value;
    if (value === "__browse__") {
      pywebview.api.browse_template().then((result) => {
        if (!result.ok && !result.cancelled) {
          $("#status-text").textContent = result.error || "No se pudo usar esa plantilla.";
        }
        refreshTemplates();
        refresh();
      });
      return;
    }
    if (!value) {
      pywebview.api.clear_template().then(() => {
        refreshTemplates();
        refresh();
      });
      return;
    }
    pywebview.api.set_template(value).then((result) => {
      if (!result.ok) {
        $("#status-text").textContent = result.error || "No se pudo usar esa plantilla.";
      }
      refreshTemplates();
      refresh();
    });
  });
  $("#template-delete").addEventListener("click", () => {
    if (!confirm("¿Eliminar la plantilla activa?")) return;
    pywebview.api.delete_template().then(() => {
      refreshTemplates();
      refresh();
    });
  });

  // -------------------------------------------------------- texturas
  $("#texture-add").addEventListener("click", () => {
    pywebview.api.browse_texture_file().then((path) => {
      if (!path) return;
      pywebview.api.add_texture_layer(path).then(() => {
        refreshAvailableTextures().then(refresh);
      });
    });
  });
  $("#texture-toggle").addEventListener("click", (e) => {
    if (e.target.closest("#texture-add")) return; // no plegar al usar el boton
    const collapsed = !(lastState && lastState.textures_collapsed);
    pywebview.api.set_textures_collapsed(collapsed).then(() => refresh());
  });

  // ------------------------------------------------------- velocidad
  $("#speed-control").addEventListener("click", (e) => {
    const btn = e.target.closest(".segmented-item");
    if (!btn) return;
    pywebview.api.set_speed(btn.dataset.value).then(() => refresh());
  });

  // ---------------------------------------------------------- escala
  $("#scale-slider").addEventListener("input", (e) => commitScale(e.target.value));
  $("#scale-entry").addEventListener("change", (e) => commitScale(e.target.value));
  $("#scale-entry").addEventListener("keydown", (e) => {
    if (e.key === "Enter") commitScale(e.target.value);
  });

  // --------------------------------------------------------- presets
  $("#preset-select").addEventListener("change", (e) => {
    const name = e.target.value;
    if (!name) return;
    pywebview.api.apply_preset(name).then((r) => {
      if (!r.ok) {
        $("#status-text").textContent = r.error || "No se pudo aplicar el preset.";
        return;
      }
      render(r.state);
      refreshTemplates();
      refreshAvailableTextures();
      $("#status-text").textContent = r.missing && r.missing.length
        ? `Preset "${name}" aplicado — no se encontró la ${r.missing.join("/")} guardada`
        : `Preset aplicado: ${name}`;
      $("#status-text").style.color = r.missing && r.missing.length ? "var(--red)" : "var(--accent)";
    });
  });
  $("#preset-save").addEventListener("click", () => {
    const name = (prompt("Nombre del preset:") || "").trim();
    if (!name) return;
    if (lastState.presets.includes(name) &&
        !confirm(`Ya existe un preset llamado "${name}". ¿Sobrescribirlo?`)) return;
    pywebview.api.save_preset(name).then((r) => {
      if (!r.ok) {
        $("#status-text").textContent = r.error;
        return;
      }
      $("#status-text").textContent = `Preset guardado: ${name}`;
      $("#status-text").style.color = "var(--accent)";
      refresh();
    });
  });
  $("#preset-rename").addEventListener("click", () => {
    const oldName = $("#preset-select").value;
    if (!oldName) return;
    const name = (prompt(`Nuevo nombre para "${oldName}":`, oldName) || "").trim();
    if (!name || name === oldName) return;
    pywebview.api.rename_preset(oldName, name).then((r) => {
      if (!r.ok) {
        $("#status-text").textContent = r.error;
        return;
      }
      refresh();
    });
  });
  $("#preset-delete").addEventListener("click", () => {
    const name = $("#preset-select").value;
    if (!name) return;
    if (!confirm(`¿Eliminar el preset "${name}"?`)) return;
    pywebview.api.delete_preset(name).then(() => refresh());
  });

  // --------------------------------------- nombre de archivo / guardar como
  $("#filename-entry").addEventListener("input", (e) => {
    pywebview.api.set_output_name(e.target.value).then((path) => {
      $("#output-label").textContent = path || "(se definirá al elegir el audio)";
    });
  });
  $("#output-browse").addEventListener("click", () => {
    pywebview.api.choose_output_path().then((path) => {
      $("#output-label").textContent = path || "(se definirá al elegir el audio)";
    });
  });

  // --------------------------------------------------------- generacion
  $("#generate-btn").addEventListener("click", () => {
    pywebview.api.output_would_overwrite().then((wouldOverwrite) => {
      if (wouldOverwrite) {
        const name = $("#output-label").textContent;
        if (!confirm(`${name} ya existe. ¿Deseas reemplazarlo?`)) return;
      }
      beginGeneration();
    });
  });
  $("#cancel-btn").addEventListener("click", () => {
    pywebview.api.cancel_generation();
    $("#status-text").textContent = "Cancelando...";
  });
  $("#open-video-btn").addEventListener("click", () => pywebview.api.open_video());
  $("#open-folder-btn").addEventListener("click", () => pywebview.api.open_output_folder());
});

// ------------------------------------------------------- ajustar imagen
//
// Reimplementacion en canvas del ImageFocusPicker de Tk (app.py): mismo
// algoritmo pixel a pixel (ver _focus_rect/_final_rect alla), asi que el
// recuadro que se ve aca coincide exactamente con lo que exporta ffmpeg.
// La geometria vive en JS; Python solo guarda el resultado (set_focus).

const FocusPicker = (() => {
  const W = 352, H = 200; // debe coincidir con FOCUS_PICKER_W/H en api.py
  let canvas, ctx;
  let image = null;
  let imgW = W, imgH = H; // tamano ajustado (fit) de la imagen dentro del canvas
  let zoom = 100, focusX = 0.5, focusY = 0.5;
  let scalePct = 100; // sincronizado con la seccion Escala
  let lastPreviewUri = null;
  let dragging = false;

  function clamp01(v) {
    return Math.max(0, Math.min(1, v));
  }

  function focusRect() {
    const frac = 100 / zoom;
    const cw = imgW * frac, ch = imgH * frac;
    const ox = (W - imgW) / 2, oy = (H - imgH) / 2;
    const cx = ox + (imgW - cw) * focusX;
    const cy = oy + (imgH - ch) * focusY;
    return [cx, cy, cx + cw, cy + ch];
  }

  function scaleCropFracs() {
    const s = scalePct / 100;
    const cover = Math.max(s, 1.0);
    return [s / cover, 1.0 / cover];
  }

  function finalRect() {
    const [fx0, fy0, fx1, fy1] = focusRect();
    const fw = fx1 - fx0, fh = fy1 - fy0;
    const side = Math.min(fw, fh);
    const sx0 = fx0 + (fw - side) / 2;
    const sy0 = fy0 + (fh - side) / 2;
    const sx1 = sx0 + side, sy1 = sy0 + side;
    const [wFrac, hFrac] = scaleCropFracs();
    const shrinkX = side * (1 - wFrac) / 2;
    const shrinkY = side * (1 - hFrac) / 2;
    return [sx0 + shrinkX, sy0 + shrinkY, sx1 - shrinkX, sy1 - shrinkY];
  }

  function redraw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, W, H);
    if (!image) {
      ctx.fillStyle = "#8b8b92";
      ctx.font = "12px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("Carga una imagen", W / 2, H / 2);
      return;
    }
    const ox = (W - imgW) / 2, oy = (H - imgH) / 2;
    ctx.drawImage(image, ox, oy, imgW, imgH);
    const [cx0, cy0, cx1, cy1] = finalRect();
    if (cx0 > ox + 0.5 || cy0 > oy + 0.5 || cx1 < ox + imgW - 0.5 || cy1 < oy + imgH - 0.5) {
      ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
      ctx.fillRect(ox, oy, imgW, cy0 - oy);
      ctx.fillRect(ox, cy1, imgW, oy + imgH - cy1);
      ctx.fillRect(ox, cy0, cx0 - ox, cy1 - cy0);
      ctx.fillRect(cx1, cy0, ox + imgW - cx1, cy1 - cy0);
    }
    ctx.strokeStyle = "#19a866";
    ctx.lineWidth = 2;
    ctx.strokeRect(cx0, cy0, cx1 - cx0, cy1 - cy0);
  }

  function notify() {
    pywebview.api.set_focus(zoom, focusX, focusY);
  }

  function syncZoomControls() {
    $("#focus-zoom").value = zoom;
    $("#focus-zoom-entry").value = zoom;
  }

  function setZoom(value, { notify: shouldNotify = true } = {}) {
    zoom = Math.max(100, Math.min(300, Math.round(value)));
    syncZoomControls();
    redraw();
    if (shouldNotify) notify();
  }

  function setScalePct(pct) {
    scalePct = pct;
    redraw();
  }

  function loadImage(dataUri) {
    if (dataUri === lastPreviewUri) return;
    lastPreviewUri = dataUri;
    if (!dataUri) {
      image = null;
      redraw();
      return;
    }
    const img = new Image();
    img.onload = () => {
      let dispW = W, dispH = Math.round((W * img.naturalHeight) / img.naturalWidth);
      if (dispH > H) {
        dispH = H;
        dispW = Math.round((H * img.naturalWidth) / img.naturalHeight);
      }
      image = img;
      imgW = dispW;
      imgH = dispH;
      redraw();
    };
    img.src = dataUri;
  }

  function applyState(state) {
    zoom = state.focus_zoom_pct || 100;
    focusX = state.focus_x ?? 0.5;
    focusY = state.focus_y ?? 0.5;
    scalePct = state.scale_pct || 100;
    syncZoomControls();
    loadImage(state.show_focus ? state.media_focus_preview : null);
    redraw();
  }

  function init() {
    canvas = document.getElementById("focus-picker");
    canvas.width = W;
    canvas.height = H;
    ctx = canvas.getContext("2d");

    canvas.addEventListener("pointerdown", (e) => {
      if (!image) return;
      dragging = true;
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      // Movimiento RELATIVO (movementX/Y) en vez de una posicion absoluta
      // (offsetX/Y) medida contra un origen fijo: offsetX/Y se vuelve
      // poco confiable en cuanto el cursor sale del area del canvas (algo
      // que pasa seguido, porque mide solo 352x200), y con eso el
      // arrastre se quedaba corto o se disparaba de mas cerca de los
      // bordes. El delta relativo no depende de estar "dentro" del canvas.
      const frac = 100 / zoom;
      const rangeX = Math.max(1, imgW * (1 - frac));
      const rangeY = Math.max(1, imgH * (1 - frac));
      focusX = clamp01(focusX + e.movementX / rangeX);
      focusY = clamp01(focusY + e.movementY / rangeY);
      redraw();
    });
    const endDrag = () => {
      if (!dragging) return;
      dragging = false;
      notify();
    };
    // OJO: NO usar pointerleave para terminar el arrastre -- el canvas
    // mide solo 200px de alto, asi que al arrastrar hacia arriba/abajo el
    // cursor sale de esos limites antes de llegar al tope real de la
    // imagen, y pointerleave cortaria el drag ahi. Con setPointerCapture
    // activo, pointermove sigue llegando aunque el cursor salga del
    // canvas -- pointerup (+ pointercancel) es lo unico que debe cerrarlo.
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);

    $("#focus-zoom").addEventListener("input", (e) => setZoom(e.target.value));
    $("#focus-zoom-entry").addEventListener("change", (e) => setZoom(e.target.value));
    $("#focus-zoom-entry").addEventListener("keydown", (e) => {
      if (e.key === "Enter") setZoom(e.target.value);
    });
    $("#focus-reset").addEventListener("click", () => {
      pywebview.api.reset_focus().then((r) => applyState(r.state));
    });
  }

  return { init, applyState, setScalePct };
})();

// --------------------------------------------------------- recorte del loop
//
// Slider de doble asa: reimplementa RangeSlider (app.py) en HTML/CSS/JS.
// A diferencia del canvas de arriba, aca SI conviene usar coordenadas
// absolutas (getBoundingClientRect + clientX) en vez de movimiento
// relativo, porque el gesto real es "click en cualquier punto del riel
// mueve la asa mas cercana ahi", no "arrastrar para desplazar" -- y
// clientX no depende de que el cursor siga dentro del elemento.

const LoopSlider = (() => {
  const PAD = 8;
  const MIN_GAP = 0.5;
  let container, fill, handleStart, handleEnd;
  let duration = 1.0, start = 0.0, end = 1.0;
  let grabbed = null;

  function secToPct(sec) {
    return duration > 0 ? Math.max(0, Math.min(100, (sec / duration) * 100)) : 0;
  }

  function redraw() {
    const startPct = secToPct(start);
    const endPct = secToPct(end);
    handleStart.style.left = `${startPct}%`;
    handleEnd.style.left = `${endPct}%`;
    fill.style.left = `${startPct}%`;
    fill.style.right = `${100 - endPct}%`;
  }

  function updateReadout() {
    $("#trim-readout").textContent =
      `${formatDuration(start)} – ${formatDuration(end)} · ${formatDuration(end - start)} de loop`;
  }

  function xToSec(clientX) {
    const rect = container.getBoundingClientRect();
    const usable = Math.max(1, rect.width - 2 * PAD);
    const frac = (clientX - rect.left - PAD) / usable;
    return Math.max(0, Math.min(1, frac)) * duration;
  }

  function applyDrag(sec) {
    if (grabbed === "start") {
      start = Math.max(0, Math.min(sec, end - MIN_GAP));
    } else {
      end = Math.min(duration, Math.max(sec, start + MIN_GAP));
    }
    redraw();
    updateReadout();
  }

  function notify() {
    pywebview.api.set_trim(start, end);
  }

  function applyState(state) {
    if (!state.show_trim) return;
    duration = Math.max(MIN_GAP, state.media_duration || 1.0);
    start = state.trim_start ?? 0;
    end = state.trim_end ?? duration;
    redraw();
    updateReadout();
  }

  function init() {
    container = $("#loop-slider");
    fill = container.querySelector(".range-fill");
    handleStart = document.getElementById("loop-handle-start");
    handleEnd = document.getElementById("loop-handle-end");

    container.addEventListener("pointerdown", (e) => {
      const sec = xToSec(e.clientX);
      grabbed = Math.abs(sec - start) <= Math.abs(sec - end) ? "start" : "end";
      container.setPointerCapture(e.pointerId);
      applyDrag(sec);
    });
    container.addEventListener("pointermove", (e) => {
      if (!grabbed) return;
      applyDrag(xToSec(e.clientX));
    });
    const endDrag = () => {
      if (!grabbed) return;
      grabbed = null;
      notify();
    };
    container.addEventListener("pointerup", endDrag);
    container.addEventListener("pointercancel", endDrag);
  }

  return { init, applyState };
})();
