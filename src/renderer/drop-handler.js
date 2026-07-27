"use strict";

// Reemplaza el drag&drop que en la version pywebview manejaba Python
// (Api._setup_dom_events/_bind_drop_zone, leyendo pywebviewFullPath). Aca el
// "drop" real se maneja en el renderer con window.api.getPathForFile(file)
// (preload.js envuelve webUtils.getPathForFile) para obtener la ruta real
// del archivo soltado. El resaltado visual (dragenter/dragover/dragleave) ya
// lo maneja app.js (setupDragHighlight) sin cambios -- esto solo agrega el
// "drop" que faltaba.
(function () {
  // Mismas listas que engine.py (IMAGE_EXTS/VIDEO_EXTS). Las texturas
  // aceptan las dos: hay texturas que son clips (grano de pelicula, fugas de
  // luz) para poner encima del medio y debajo de la plantilla. Ninguna de las
  // dos necesita transparencia -- eso es cosa de la plantilla.
  const IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"];
  const VIDEO_EXTS = [".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".gif"];
  const TEXTURE_EXTS = [...IMAGE_EXTS, ...VIDEO_EXTS];

  function showStatus(text) {
    const status = document.getElementById("status-text");
    if (status) status.textContent = text;
  }

  function pathsFromDrop(e) {
    const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
    return files.map((f) => window.api.getPathForFile(f)).filter(Boolean);
  }

  function pushState(state) {
    if (state && typeof window.onStateChanged === "function") window.onStateChanged(state);
  }

  function refreshState() {
    window.api.call("get_state").then(pushState);
  }

  function bind(selector, onDrop) {
    const el = document.querySelector(selector);
    if (!el) return;
    el.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      onDrop(e);
    });
  }

  function ingestGeneric(e) {
    const paths = pathsFromDrop(e);
    if (!paths.length) return;
    window.api.call("ingest_paths", paths).then((result) => pushState(result.state));
  }

  function init() {
    // El body entero acepta medios (soltar en CUALQUIER parte de la
    // ventana carga imagen/video/audio), igual que en la version pywebview.
    bind("body", ingestGeneric);
    bind("#preview-dropzone", ingestGeneric);

    bind("#template-section", (e) => {
      const png = pathsFromDrop(e).find((p) => p.toLowerCase().endsWith(".png"));
      // Mismo caso que en #texture-section: sin este aviso, soltar un JPG
      // aca no hacia absolutamente nada.
      if (!png) {
        showStatus("La plantilla tiene que ser un PNG con zona transparente.");
        return;
      }
      window.api.call("set_template", png).then((result) => {
        // set_template rechaza cualquier PNG sin zona transparente (asi
        // funciona una plantilla) -- sin esto, arrastrar una foto comun
        // no hacia nada visible y parecia que el drop estaba roto.
        if (result && !result.ok) {
          const status = document.getElementById("status-text");
          if (status) status.textContent = result.error || "No se pudo usar esa plantilla.";
        }
        refreshState();
      });
    });

    bind("#texture-section", (e) => {
      const dropped = pathsFromDrop(e);
      const paths = dropped.filter((p) =>
        TEXTURE_EXTS.includes(p.slice(p.lastIndexOf(".")).toLowerCase())
      );
      // Antes un archivo que no entraba en la lista se descartaba sin decir
      // nada (el drop de la seccion corta la propagacion, asi que tampoco
      // llegaba al del body) -- parecia que la app lo habia ignorado por
      // gusto.
      if (!paths.length) {
        if (dropped.length) showStatus("Esa textura tiene que ser una imagen o un video.");
        return;
      }
      let lastAdded = null;
      const adds = paths.map((path) =>
        window.api.call("add_texture_layer", path).then((result) => {
          if (result.ok) lastAdded = path;
        })
      );
      Promise.all(adds).then(() => {
        if (!lastAdded) return;
        // Igual que Api._on_texture_drop en la version pywebview: la
        // tarjeta recien soltada queda seleccionada con sus sliders
        // visibles, no solo activa.
        if (typeof window.onTextureAdded === "function") window.onTextureAdded(lastAdded);
        refreshState();
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
