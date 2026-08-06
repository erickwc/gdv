"use strict";

// Retinta los 2 blobs de color del fondo (#bg-blob-1/#bg-blob-2 en
// index.html, dentro de .background) al matiz dominante de la
// imagen/video cargado -- llamado desde app.js (Preview.onReady) cada vez
// que llega un fotograma nuevo, igual que hacia tintBackgroundFromImage en
// el aurora WebGL viejo (silk-aurora-background.js, ver historial de git).
// Sin imagen (o sin color util) vuelve a los 2 colores originales del
// diseno de Figma (azul/lavanda).

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  switch (max) {
    case r: h = (g - b) / d + (g < b ? 6 : 0); break;
    case g: h = (b - r) / d + 2; break;
    default: h = (r - g) / d + 4;
  }
  return [h * 60, s, l];
}

function hslToHex(h, s, l) {
  h = (((h % 360) + 360) % 360) / 360;
  let r, g, b;
  if (s === 0) {
    r = g = b = l;
  } else {
    const hue2rgb = (p, q, t) => {
      if (t < 0) t += 1;
      if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1 / 3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1 / 3);
  }
  const toHex = (v) => Math.round(v * 255).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

// Mismo criterio que el aurora viejo: downscalea a un canvas chico y
// favorece pixeles saturados de brillo medio (ni negros ni blancos puros,
// casi siempre letterbox/fondo horneado por ffmpeg, no "el color de la
// foto"), mezclados con el promedio general para no quedar pegado a un
// solo pixel ruidoso.
// Lienzo de muestreo, uno solo para toda la app: el ambilight llama a esto
// varias veces por segundo y crear un <canvas> nuevo cada vez es basura para el
// recolector, ademas de perder el contexto ya configurado.
const SAMPLE_SIZE = 32;
let sampleCtx = null;

function extractImageHue(img) {
  // naturalWidth es de las <img>; videoWidth, de los <video> -- el medio en vivo
  // (ver ambilightFromSource abajo, y arrancarAmbilight en live-preview.js).
  // width cubre un <canvas>. drawImage acepta los tres por igual.
  if (!img || !(img.naturalWidth || img.videoWidth || img.width)) return null;
  const size = SAMPLE_SIZE;
  if (!sampleCtx) {
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    sampleCtx = canvas.getContext("2d", { willReadFrequently: true });
  }
  const ctx = sampleCtx;
  let data;
  try {
    ctx.drawImage(img, 0, 0, size, size);
    data = ctx.getImageData(0, 0, size, size).data;
  } catch (e) {
    return null;
  }
  let sumR = 0, sumG = 0, sumB = 0, count = 0;
  let bestScore = -1, bestColor = null;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 200) continue;
    const r = data[i], g = data[i + 1], b = data[i + 2];
    const [, s, l] = rgbToHsl(r, g, b);
    if (l < 0.08 || l > 0.92) continue;
    sumR += r; sumG += g; sumB += b; count++;
    const score = s * (1 - Math.abs(l - 0.5) * 2);
    if (score > bestScore) {
      bestScore = score;
      bestColor = [r, g, b];
    }
  }
  if (!count || !bestColor) return null;
  const avg = [sumR / count, sumG / count, sumB / count];
  const mixed = bestColor.map((v, i) => v * 0.6 + avg[i] * 0.4);
  return rgbToHsl(mixed[0], mixed[1], mixed[2])[0];
}

// Los 3 manchones de color del SVG del fondo (index.html). De cada uno se
// conserva la saturacion y el brillo que le puso el diseno y se rota SOLO el
// matiz al de la portada, asi el fondo siempre se siente el mismo diseno
// nomas que en otro color. Los tres caen ya de fabrica en el mismo matiz
// (~242-255deg); lo que los distingue es la s/l: el 1 vivo y medio, el 2
// palido y claro, el 3 profundo y oscuro.
//
// Estos numeros NO son decorativos: son los fill que trae el SVG pasados a
// HSL, y son lo que ata el modo adaptativo al fondo dibujado. Si se cambia
// el fondo por otro diseno hay que recalcularlos, o pasa esto: la app
// arranca con el fondo nuevo, pero al cargar el primer video los manchones
// saltan a la saturacion/brillo del diseno VIEJO y no vuelven nunca -- se ve
// como si el fondo se hubiera cambiado solo.
//
// El 3 estuvo un tiempo fuera de esta lista (el <path> no tenia id), y por
// eso se quedaba con su azul fijo mientras los otros dos seguian a la
// portada: con una portada calida quedaba una esquina morada que no pegaba
// con nada. Si se agrega un manchon nuevo al SVG, va aca tambien.
const BLOBS = [
  { id: "bg-blob-1", neutro: "#0900FF", s: 1.0, l: 0.5 },
  { id: "bg-blob-2", neutro: "#9978FA", s: 0.929, l: 0.7255 },
  { id: "bg-blob-3", neutro: "#0C0A5C", s: 0.804, l: 0.2 },
];

let blobEls = null;

// Devuelve los elementos solo si estan TODOS: a medio pintar (unos con el
// color de la portada y otros con el del diseno) se ve peor que sin retintar.
function ensureBlobEls() {
  if (!blobEls) {
    const els = BLOBS.map((b) => document.getElementById(b.id));
    if (els.every(Boolean)) blobEls = els;
  }
  return blobEls;
}

// Aplica el matiz al fondo que se este viendo de verdad, que son dos casos
// distintos y excluyentes:
//
//   - si esta activo el fondo animado (silk-aurora-background.js, hoy
//     comentado en index.html) monta su canvas DENTRO de .background y tapa
//     el SVG entero, que es opaco. Ahi el color va por setAuroraHue y NO se
//     tocan los manchones: seria trabajo invisible, y del caro -- cada
//     escritura de fill obliga a rehacer los desenfoques (ver MIN_DELTA_HUE
//     mas abajo);
//   - si no (el caso de hoy, o cuando el aurora se sale por falta de WebGL)
//     setAuroraHue nunca se define y el fondo visible es el SVG: ahi si hay
//     que pintarle los manchones.
//
// hue null = sin medio (o sin color util): cada fondo vuelve a su reposo.
function pintarFondo(hue) {
  if (window.setAuroraHue) {
    window.setAuroraHue(hue);
    return;
  }
  // Con el fondo de kawarp montado pasa lo mismo que con el aurora: su canvas
  // cubre el SVG entero, asi que escribirle los fill a los manchones seria
  // trabajo invisible -- y del caro (ver MIN_DELTA_HUE mas abajo: cada fill
  // obliga a rehacer un feGaussianBlur de stdDeviation 175 sobre 2388x1932).
  // Ojo: se corta solo el pintado del SVG, NO el --accent de :root, que lo
  // sigue necesitando la UI (ver aplicarMatiz) -- por eso el return va aca y
  // no mas arriba, en aplicarMatiz.
  if (window.kawarpFondoActivo) return;
  const els = ensureBlobEls();
  if (!els) return;
  els.forEach((el, i) => {
    const b = BLOBS[i];
    el.setAttribute("fill", hue == null ? b.neutro : hslToHex(hue, b.s, b.l));
  });
}

window.tintBackgroundFromImage = function (img) {
  pintarFondo(img ? extractImageHue(img) : null);
};

// Gris neutro (sin imagen/video cargado) para el foco de inputs y demas
// usos de var(--accent) en styles.css -- mismo mecanismo que el fondo,
// pero como variables CSS en :root en vez de fill de SVG.
const NEUTRAL_UI_ACCENT = "#9a97a3";
const NEUTRAL_UI_ACCENT_HOVER = "#88858f";
const NEUTRAL_UI_ACCENT_GLOW = "rgba(154, 151, 163, 0.18)";

function hexToRgbaString(hex, alpha) {
  const normalized = hex.replace("#", "");
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

window.setAdaptiveAccent = function (img) {
  const hue = img ? extractImageHue(img) : null;
  const root = document.documentElement.style;
  if (hue == null) {
    root.setProperty("--accent", NEUTRAL_UI_ACCENT);
    root.setProperty("--accent-hover", NEUTRAL_UI_ACCENT_HOVER);
    root.setProperty("--accent-glow", NEUTRAL_UI_ACCENT_GLOW);
    return;
  }
  const accent = hslToHex(hue, 0.4, 0.62);
  const accentHover = hslToHex(hue, 0.4, 0.52);
  root.setProperty("--accent", accent);
  root.setProperty("--accent-hover", accentHover);
  root.setProperty("--accent-glow", hexToRgbaString(accent, 0.22));
};

// -------------------------------------------------------------- ambilight
//
// El color del fondo SIGUE al video mientras se reproduce, como esas tiras LED
// detras del televisor. Dos cosas lo hacen posible:
//
//   1. se muestrea el cuadro que se esta viendo de verdad -- el canvas del
//      previsualizador en vivo (live-preview.js), no un fotograma que armo
//      ffmpeg cuando se toco algo;
//   2. el matiz aplicado se ACERCA de a poco al del cuadro en vez de saltar.
//
// Sin lo segundo, cada cambio (agrandar la escala, mover un borde) pegaba un
// salto al color que hubiera justo en ese instante y ahi se quedaba clavado.
let hueActual = null;

function acercarMatiz(actual, objetivo, factor) {
  // Por el camino corto del circulo: de 350 a 10 pasa por 0, no al reves dando
  // la vuelta entera por todos los colores del medio.
  const d = ((objetivo - actual + 540) % 360) - 180;
  return (actual + d * factor + 360) % 360;
}

// Ultimo matiz que se ESCRIBIO en el DOM (distinto de hueActual, que avanza en
// cada muestreo aunque el cambio sea invisible).
let hueAplicado = null;

// Cuanto tiene que moverse el matiz para que valga la pena tocar el DOM. Escribir
// el fill de los blobs no es gratis ni de lejos: cada uno vive dentro de un
// feGaussianBlur de stdDeviation 175 sobre un area de 2388x1932 (ver el <svg>
// del fondo en index.html), asi que cambiarlo obliga a rehacer los desenfoques,
// y de paso --accent en :root invalida el estilo de TODO el documento. Con el
// ambilight muestreando a 8 por segundo eso era un parpadeo de trabajo constante
// mientras el video corria, para mover el color un cuarto de grado. Un grado y
// medio de matiz sobre un manchon desenfocado no lo ve nadie; el video a los
// tirones, si.
//
// Si se vuelve a activar el aurora (ver los <script> comentados en index.html)
// la mitad cara desaparece: setAuroraHue solo mueve 4 colores destino y el
// shader ya redibujaba igual cada cuadro. El freno igual conviene dejarlo por
// lo otro, que --accent sigue invalidando el documento entero, y ahi no cuesta
// suavidad ninguna: el aurora interpola su paleta a 0.03 por cuadro, un grado
// y medio ni llega a distinguirse dentro de esa transicion.
const MIN_DELTA_HUE = 1.5;

function aplicarMatiz(hue) {
  if (hue == null) {
    hueAplicado = null;
  } else {
    if (hueAplicado != null) {
      const d = Math.abs(((hue - hueAplicado + 540) % 360) - 180);
      if (d < MIN_DELTA_HUE) return;
    }
    hueAplicado = hue;
  }
  pintarFondo(hue);
  const root = document.documentElement.style;
  if (hue == null) {
    root.setProperty("--accent", NEUTRAL_UI_ACCENT);
    root.setProperty("--accent-hover", NEUTRAL_UI_ACCENT_HOVER);
    root.setProperty("--accent-glow", NEUTRAL_UI_ACCENT_GLOW);
    return;
  }
  const accent = hslToHex(hue, 0.4, 0.62);
  root.setProperty("--accent", accent);
  root.setProperty("--accent-hover", hslToHex(hue, 0.4, 0.52));
  root.setProperty("--accent-glow", hexToRgbaString(accent, 0.22));
}

// src null = sin medio: vuelve al violeta del diseno y olvida el matiz, para que
// el proximo video no arranque interpolando desde el color del anterior.
window.ambilightFromSource = function (src, factor = 0.12) {
  if (!src) {
    hueActual = null;
    aplicarMatiz(null);
    return;
  }
  const objetivo = extractImageHue(src);
  // Cuadro sin color util (un fundido a negro, por ejemplo): se queda el de
  // antes en vez de irse al violeta de "sin medio" y volver.
  if (objetivo == null) return;
  hueActual = hueActual == null ? objetivo : acercarMatiz(hueActual, objetivo, factor);
  aplicarMatiz(hueActual);
};
