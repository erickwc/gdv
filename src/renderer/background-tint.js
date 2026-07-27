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
function extractImageHue(img) {
  if (!img || !img.naturalWidth) return null;
  const size = 32;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
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
