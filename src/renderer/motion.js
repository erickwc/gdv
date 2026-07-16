"use strict";

// Animaciones de apertura/cierre de los dropdown tipo "select" (presets,
// modo de mezcla de textura -- ver el comentario en app.js sobre por que
// no son <select> nativos) y de refresco de imagenes. Web Animations API
// nativa del navegador, sin ninguna libreria (mismo motivo que el fondo,
// silk-aurora-background.js, termino portado a JS plano: cero dependencia
// nueva, cero conexion a internet). Script clasico (no type="module"),
// comparte scope global con el resto -- se carga antes que app.js en
// index.html para que estas funciones ya existan cuando app.js las llama.

const SPRING_EASE = "cubic-bezier(0.16, 1, 0.3, 1)";

// Una animacion en curso por elemento -- si se abre/cierra muy rapido
// seguido (doble click en el trigger) cancela la anterior en vez de
// pisarla sin avisar (deja el opacity/transform en un estado raro a mitad
// de camino).
const runningDropdown = new WeakMap();

// El caller (togglePresetDropdown/toggleTextureBlendDropdown en app.js) ya
// se encarga de sacar el [hidden] y posicionar la lista ANTES de abrir
// (positionFloatingDropdown necesita el scrollHeight real, que solo existe
// con el elemento visible) -- esta funcion solo anima, y al cerrar recien
// pone [hidden] cuando la animacion termina.
function animateDropdown(el, opening) {
  if (!el) return;
  const prev = runningDropdown.get(el);
  if (prev) prev.cancel();

  const keyframes = [
    { opacity: 0, transform: "translateY(-6px) scale(0.97)" },
    { opacity: 1, transform: "translateY(0) scale(1)" },
  ];
  if (!opening) keyframes.reverse();

  const duration = opening ? 160 : 120;
  const anim = el.animate(keyframes, { duration, easing: SPRING_EASE, fill: "both" });
  runningDropdown.set(el, anim);

  let settled = false;
  const settle = () => {
    if (settled) return;
    settled = true;
    if (runningDropdown.get(el) === anim) runningDropdown.delete(el);
    if (!opening) el.hidden = true;
  };
  anim.finished.then(settle).catch(() => {}); // cancel() rechaza finished -- el siguiente animate() ya toma el control
  // Respaldo por si "finished" nunca resuelve (la ventana pierde foco o se
  // minimiza a mitad de animacion, el navegador puede pausar el timeline)
  // -- sin esto una lista cerrada podia quedar invisible pero interactiva
  // (opacity:0 sin [hidden], bloqueando clicks en esa zona para siempre).
  setTimeout(settle, duration + 150);
}

// "Flash" corto cada vez que el fotograma del preview (#preview-image) se
// actualiza -- no arranca desde opacity:0 (la imagen anterior ya esta
// visible casi siempre, seria un parpadeo feo), solo un dip suave que hace
// notar el refresco.
function animateImageRefresh(img) {
  if (!img) return;
  img.animate(
    [{ opacity: 0.4 }, { opacity: 1 }],
    { duration: 180, easing: SPRING_EASE }
  );
}
