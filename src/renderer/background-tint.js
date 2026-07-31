"use strict";

// Retinta los 2 blobs de color del fondo (#bg-blob-1/#bg-blob-2 en
// index.html, dentro de .background) al matiz dominante de la
// imagen/video cargado -- llamado desde app.js (Preview.onReady) cada vez
// que llega un fotograma nuevo, igual que hacia tintBackgroundFromImage en
// el aurora WebGL viejo (silk-aurora-background.js, ver historial de git).
// Sin imagen (o sin color util) vuelve a los 2 colores originales del
// diseno de Figma (violeta/lavanda).

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

// Colores originales del diseno de Figma (#5500FF / #C3A7FA) -- ambos
// caen ya en el mismo matiz (~260deg), solo cambia saturacion/luminosidad
// (blob1 vivo y oscuro, blob2 palido y claro). deriveBlobColors() reusa
// esas mismas 2 combinaciones de s/l, rotando solo el matiz al de la
// imagen cargada -- asi el fondo siempre "se siente" del mismo diseno,
// nomas con otro color.
const NEUTRAL_BLOB_COLORS = { blob1: "#5500FF", blob2: "#C3A7FA" };

function deriveBlobColors(hue) {
  return {
    blob1: hslToHex(hue, 1.0, 0.5),
    blob2: hslToHex(hue, 0.89, 0.817),
  };
}

let blob1El = null;
let blob2El = null;

function ensureBlobEls() {
  if (!blob1El) blob1El = document.getElementById("bg-blob-1");
  if (!blob2El) blob2El = document.getElementById("bg-blob-2");
}

window.tintBackgroundFromImage = function (img) {
  ensureBlobEls();
  if (!blob1El || !blob2El) return;
  const hue = img ? extractImageHue(img) : null;
  const colors = hue == null ? NEUTRAL_BLOB_COLORS : deriveBlobColors(hue);
  blob1El.setAttribute("fill", colors.blob1);
  blob2El.setAttribute("fill", colors.blob2);
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
// feGaussianBlur de stdDeviation 80 sobre un area de 2000x1550 (ver el <svg> del
// fondo en index.html), asi que cambiarlo obliga a rehacer los dos desenfoques,
// y de paso --accent en :root invalida el estilo de TODO el documento. Con el
// ambilight muestreando a 8 por segundo eso era un parpadeo de trabajo constante
// mientras el video corria, para mover el color un cuarto de grado. Un grado y
// medio de matiz sobre un manchon desenfocado no lo ve nadie; el video a los
// tirones, si.
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
  ensureBlobEls();
  const colors = hue == null ? NEUTRAL_BLOB_COLORS : deriveBlobColors(hue);
  if (blob1El) blob1El.setAttribute("fill", colors.blob1);
  if (blob2El) blob2El.setAttribute("fill", colors.blob2);
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
