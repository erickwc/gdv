"use strict";

// Reemplaza el drag&drop que en la version pywebview manejaba Python
// (Api._setup_dom_events/_bind_drop_zone, leyendo pywebviewFullPath). Aca el
// "drop" real se maneja en el renderer con window.api.getPathForFile(file)
// (preload.js envuelve webUtils.getPathForFile) para obtener la ruta real
// del archivo soltado. El resaltado visual (dragenter/dragover/dragleave) ya
// lo maneja app.js (setupDragHighlight) sin cambios -- esto solo agrega el
// "drop" que faltaba.
(function () {
  const IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"];

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
      if (!png) return;
      // set_template rechaza cualquier PNG sin zona transparente (asi
      // funciona una plantilla) -- una foto comun soltada aca no hace nada
      // visible, no es un drop roto.
      window.api.call("set_template", png).then(() => refreshState());
    });

    bind("#texture-section", (e) => {
      const paths = pathsFromDrop(e).filter((p) =>
        IMAGE_EXTS.includes(p.slice(p.lastIndexOf(".")).toLowerCase())
      );
      if (!paths.length) return;
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
