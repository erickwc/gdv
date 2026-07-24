"use strict";

// app.js asigna window.onStateChanged, window.onStatus, etc. y esperaba que
// pywebview los llamara via evaluate_js. Aca los llama el sidecar de Python
// via self._emit(), que llega como el evento IPC "py-event" (name, data) --
// este puente solo redespacha cada uno a su window.onXxx global.
(function () {
  const EVENTS = [
    "onStateChanged",
    "onStatus",
    "onProgress",
    "onJobDone",
    "onJobError",
    "onTextureAdded",
    "onDownloadDone",
    "onDownloadStatus",
    "onPreviewReady",
    "onLoopPreviewReady",
  ];

  window.api.onPyEvent((name, data) => {
    if (!EVENTS.includes(name)) return;
    if (typeof window[name] === "function") window[name](data);
  });
})();
