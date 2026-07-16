"use strict";

// app.js (copiado de la version pywebview) llama pywebview.api.metodo(...args)
// y encadena .then(...) sobre el resultado -- este shim reproduce esa misma
// superficie como un Proxy que reenvia cada llamada a window.api.call(method,
// ...args) (expuesto por preload.js), que a su vez habla con el sidecar de
// Python por IPC. Asi app.js no necesita tocarse para las llamadas de ida.
(function () {
  // show_preview_window() ya no vive en Python (Api ya no tiene ventana
  // propia que agrandar) -- el data URI que hay que mostrar en la ventana
  // aparte es el que ya esta pintado en <img id="preview-image">, asi que
  // el shim lo toma del DOM y se lo pasa a Electron por IPC, sin ida y
  // vuelta a Python. app.js sigue llamando pywebview.api.show_preview_window()
  // sin argumentos, igual que con pywebview.
  const SPECIAL = {
    show_preview_window: () => {
      // Si el loop se esta reproduciendo (video visible), se agranda ESE
      // video -- no el ultimo fotograma estatico. video.src ya es el
      // file:///... armado por buildFileUrl() en app.js, se reusa tal cual.
      const video = document.getElementById("loop-preview-video");
      if (video && !video.hidden) {
        return window.api.showPreviewVideo(video.src);
      }
      const img = document.getElementById("preview-image");
      return window.api.showPreviewWindow(img ? img.src : null);
    },
  };

  const apiProxy = new Proxy(
    {},
    {
      get(_target, method) {
        if (SPECIAL[method]) return SPECIAL[method];
        return (...params) => window.api.call(method, ...params);
      },
    }
  );
  window.pywebview = { api: apiProxy };
})();
