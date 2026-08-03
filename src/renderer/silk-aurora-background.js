"use strict";

// Fondo animado "Silk Aurora" (WebGL + GLSL puro, shader tomado tal cual de
// https://componentry.dev/docs/components/silk-aurora) -- portado a JS
// plano sin React: el componente original solo lo usa para el ciclo de
// vida (montar/desmontar el canvas), nada que dependa de React en si. Se
// monta dentro de .background (ver styles.css), encima del fondo estatico
// -- si WebGL no esta disponible se sale en silencio y queda ese fondo de
// siempre en vez de una pantalla rota.

const VERTEX_SHADER = `
attribute vec2 position;

void main() {
  gl_Position = vec4(position, 0.0, 1.0);
}
`;

const FRAGMENT_SHADER = `
precision highp float;

uniform vec2 u_res;
uniform vec2 u_mouse;
uniform float u_time;
uniform float u_speed;
uniform float u_intensity;
uniform float u_grain;
uniform float u_vignette;
uniform float u_mouseInfluence;
uniform vec3 u_base;
uniform vec3 u_mid;
uniform vec3 u_sheen;
uniform vec3 u_accent;

float hash(vec2 p) {
  return fract(sin(dot(p, vec2(41.93, 289.17))) * 43758.5453123);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);

  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));

  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
  float value = 0.0;
  float amp = 0.5;
  mat2 rot = mat2(0.82, 0.57, -0.57, 0.82);

  for (int i = 0; i < 5; i++) {
    value += amp * noise(p);
    p = rot * p * 2.03;
    amp *= 0.5;
  }

  return value;
}

float ribbon(vec2 p, float offset, float width, float softness) {
  float y = p.y + sin(p.x * 1.8 + offset) * 0.18;
  y += sin(p.x * 4.2 - offset * 0.7) * 0.045;
  return smoothstep(width + softness, width, abs(y));
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_res;
  float aspect = u_res.x / max(u_res.y, 1.0);
  vec2 p = (uv - 0.5) * vec2(aspect, 1.0);

  vec2 mouse = (u_mouse - 0.5) * vec2(aspect, 1.0);
  float t = u_time * 0.12 * u_speed;
  float pointerFalloff = smoothstep(0.72, 0.0, length(p - mouse));
  p += (mouse - p) * pointerFalloff * 0.05 * u_mouseInfluence;

  vec2 silk = p;
  silk.x += fbm(p * 1.6 + vec2(t * 0.8, -t * 0.35)) * 0.16;
  silk.y += fbm(p * 2.2 + vec2(-t * 0.25, t * 0.7)) * 0.10;

  float veilA = ribbon(silk + vec2(-0.18, 0.08), t * 2.1, 0.055, 0.22);
  float veilB = ribbon(silk * vec2(0.86, 1.18) + vec2(0.2, -0.14), -t * 2.8 + 1.7, 0.038, 0.18);
  float veilC = ribbon(silk * vec2(1.18, 0.9) + vec2(-0.08, 0.24), t * 1.4 - 2.1, 0.03, 0.16);

  float atmosphere = fbm(p * 1.35 + vec2(t * 0.22, -t * 0.1));
  float pearlescent = pow(max(0.0, sin((p.x - p.y) * 7.5 + atmosphere * 4.0 - t * 2.5)), 5.0);
  float glint = pow(max(0.0, noise(gl_FragCoord.xy * 0.065 + t * 18.0) - 0.72), 5.0);

  vec3 col = u_base;
  col = mix(col, u_mid, smoothstep(-0.45, 0.75, p.y + atmosphere * 0.75));
  col += u_accent * veilA * 0.72 * u_intensity;
  col += u_sheen * veilB * 0.64 * u_intensity;
  col += mix(u_sheen, u_accent, 0.35) * veilC * 0.42 * u_intensity;
  col += u_sheen * pearlescent * 0.075 * u_intensity;
  col += vec3(1.0, 0.93, 0.82) * glint * 0.22 * u_intensity;
  col += u_sheen * pointerFalloff * 0.08 * u_mouseInfluence;

  float vignette = smoothstep(1.25, 0.22, length(p));
  col *= mix(1.0 - u_vignette * 0.42, 1.06, vignette);

  float grain = (hash(gl_FragCoord.xy + t * 90.0) - 0.5) * 0.08 * u_grain;
  col += grain;

  gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
`;

function hexToRgb01(hex) {
  const normalized = hex.replace("#", "");
  return [
    parseInt(normalized.slice(0, 2), 16) / 255,
    parseInt(normalized.slice(2, 4), 16) / 255,
    parseInt(normalized.slice(4, 6), 16) / 255,
  ];
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

// A partir de UN matiz arma las 4 variables del shader, todas oscuras/poco
// saturadas -- que la paleta completa la maneje esta funcion (no la foto en
// si) es lo que evita que una imagen con un color demasiado intenso o
// demasiado clara rompa el aspecto oscuro parejo de la app.
function deriveAuroraPalette(hue) {
  return {
    baseColor: hslToHex(hue, 0.35, 0.02),
    midColor: hslToHex(hue, 0.22, 0.09),
    sheenColor: hslToHex(hue, 0.22, 0.8),
    accentColor: hslToHex(hue, 0.42, 0.56),
  };
}

function compileShader(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

// settings: mismos parametros que los props del componente original
// (baseColor/midColor/sheenColor/accentColor en hex, speed/intensity/
// grain/vignette/mouseInfluence en 0..~1-2, interactive bool).
function initSilkAurora(container, settings) {
  const canvas = document.createElement("canvas");
  canvas.setAttribute("aria-hidden", "true");
  canvas.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:none;";
  container.appendChild(canvas);

  const gl = canvas.getContext("webgl", { antialias: false, alpha: false });
  if (!gl) return null; // sin WebGL -- se queda el fondo estatico de siempre

  const vertexShader = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
  const fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
  if (!vertexShader || !fragmentShader) return null;

  const program = gl.createProgram();
  gl.attachShader(program, vertexShader);
  gl.attachShader(program, fragmentShader);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  gl.useProgram(program);

  const position = gl.getAttribLocation(program, "position");
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);

  const uRes = gl.getUniformLocation(program, "u_res");
  const uMouse = gl.getUniformLocation(program, "u_mouse");
  const uTime = gl.getUniformLocation(program, "u_time");
  const uSpeed = gl.getUniformLocation(program, "u_speed");
  const uIntensity = gl.getUniformLocation(program, "u_intensity");
  const uGrain = gl.getUniformLocation(program, "u_grain");
  const uVignette = gl.getUniformLocation(program, "u_vignette");
  const uMouseInfluence = gl.getUniformLocation(program, "u_mouseInfluence");
  const uBase = gl.getUniformLocation(program, "u_base");
  const uMid = gl.getUniformLocation(program, "u_mid");
  const uSheen = gl.getUniformLocation(program, "u_sheen");
  const uAccent = gl.getUniformLocation(program, "u_accent");

  const resize = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const { width, height } = container.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform2f(uRes, canvas.width, canvas.height);
  };
  resize();
  new ResizeObserver(resize).observe(container);

  // "current" es lo que se sube a la GPU cada frame; "target" es a donde
  // va -- setPalette() (llamado desde tintBackgroundFromImage, mas abajo)
  // solo mueve target, el lerp de mas abajo hace la transicion suave en
  // vez de saltar de golpe al color nuevo.
  const current = {
    base: hexToRgb01(settings.baseColor),
    mid: hexToRgb01(settings.midColor),
    sheen: hexToRgb01(settings.sheenColor),
    accent: hexToRgb01(settings.accentColor),
  };
  const target = {
    base: current.base.slice(),
    mid: current.mid.slice(),
    sheen: current.sheen.slice(),
    accent: current.accent.slice(),
  };

  // El fondo esta detras de TODA la UI (z-index:-1, ver .background) --
  // container nunca recibe pointermove directo (la UI lo tapa entero), asi
  // que se escucha en window y se recalcula contra el rect del contenedor,
  // para que el movimiento del mouse sobre la app en general igual mueva
  // un poco el aurora por debajo.
  const mouse = { x: 0.5, y: 0.5 };
  const targetMouse = { x: 0.5, y: 0.5 };
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const onPointerMove = (event) => {
    if (!settings.interactive) return;
    const rect = container.getBoundingClientRect();
    targetMouse.x = (event.clientX - rect.left) / rect.width;
    targetMouse.y = 1 - (event.clientY - rect.top) / rect.height;
  };
  window.addEventListener("pointermove", onPointerMove);

  const start = performance.now();
  const render = (now) => {
    mouse.x += (targetMouse.x - mouse.x) * 0.045;
    mouse.y += (targetMouse.y - mouse.y) * 0.045;
    const elapsed = reducedMotion ? 8 : (now - start) / 1000;

    // Mismo lerp exponencial que el mouse, uno por canal -- a ~0.03/frame
    // la transicion a un color nuevo tarda 1-2s, se nota pero no es
    // brusca ni distrae de lo que se esta viendo en el preview.
    for (const key of ["base", "mid", "sheen", "accent"]) {
      for (let i = 0; i < 3; i++) {
        current[key][i] += (target[key][i] - current[key][i]) * 0.03;
      }
    }
    gl.uniform3f(uBase, current.base[0], current.base[1], current.base[2]);
    gl.uniform3f(uMid, current.mid[0], current.mid[1], current.mid[2]);
    gl.uniform3f(uSheen, current.sheen[0], current.sheen[1], current.sheen[2]);
    gl.uniform3f(uAccent, current.accent[0], current.accent[1], current.accent[2]);

    gl.uniform2f(uMouse, mouse.x, mouse.y);
    gl.uniform1f(uTime, elapsed);
    gl.uniform1f(uSpeed, reducedMotion ? 0 : settings.speed);
    gl.uniform1f(uIntensity, settings.intensity);
    gl.uniform1f(uGrain, settings.grain);
    gl.uniform1f(uVignette, settings.vignette);
    gl.uniform1f(uMouseInfluence, settings.interactive && !reducedMotion ? settings.mouseInfluence : 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

    requestAnimationFrame(render);
  };
  requestAnimationFrame(render);

  return {
    setPalette(palette) {
      target.base = hexToRgb01(palette.baseColor);
      target.mid = hexToRgb01(palette.midColor);
      target.sheen = hexToRgb01(palette.sheenColor);
      target.accent = hexToRgb01(palette.accentColor);
    },
  };
}

// Paleta neutra (grises, casi sin tinte) -- estado de reposo, sin medio
// cargado o mientras no se pudo sacar un color util del fotograma actual
// (ver tintBackgroundFromImage). La app carga fotos/videos de color
// impredecible, y un acento fijo y saturado terminaria chocando con el
// contenido tarde o temprano.
//
// Paleta morada/dorada anterior (por si se quiere volver a un acento fijo
// -- estos 4 valores, nada mas, para restaurarla tal cual estaba):
//   baseColor:   "#050309"
//   midColor:    "#14151d"
//   sheenColor:  "#f4dfb8"  (dorado)
//   accentColor: "#8b5cf6"  (--accent morado de la app, ver styles.css)
const NEUTRAL_AURORA_PALETTE = {
  baseColor: "#050505",
  midColor: "#17171a",
  sheenColor: "#cfcfd6",
  accentColor: "#46464e",
};

let auroraControl = null;

const mount = document.querySelector(".background");
if (mount) {
  auroraControl = initSilkAurora(mount, {
    ...NEUTRAL_AURORA_PALETTE,
    speed: 0.8,
    intensity: 0.55,
    grain: 0.45,
    vignette: 1,
    mouseInfluence: 0.7,
    interactive: true,
  });
}

// UNICA entrada de color del aurora. Recibe un matiz ya calculado (0-360) o
// null para volver a la paleta neutra; NO extrae color por su cuenta.
//
// Antes este archivo definia su propio tintBackgroundFromImage/
// setAdaptiveAccent, con una copia de extractImageHue que empezaba con
// `if (!img || !img.naturalWidth) return null` -- o sea, solo aceptaba
// <img>. Servia para el fotograma fijo que mandaba app.js (Preview.onReady),
// pero rechazaba en silencio el canvas del previsualizador en vivo, que es
// justo la fuente que sigue al video cuadro a cuadro. Ademas pisaba los
// globales del mismo nombre de background-tint.js segun el orden de los
// <script>, con dos extractores compitiendo.
//
// Ahora hay un solo dueno del color: background-tint.js muestrea la fuente
// (acepta <img>, <video> y <canvas>), suavemente acerca el matiz y llama
// aca -- ver aplicarMatiz/ambilightFromSource ahi. El shader hace su propio
// lerp de 0.03 por cuadro hacia la paleta nueva, asi que llamar seguido es
// barato: solo mueve 4 colores destino.
//
// Solo existe si el aurora llego a montar: sin WebGL queda undefined, y por
// ahi background-tint.js se da cuenta de que el fondo visible es el SVG y
// vuelve a pintarle los blobs.
if (auroraControl) {
  window.setAuroraHue = function (hue) {
    auroraControl.setPalette(hue == null ? NEUTRAL_AURORA_PALETTE : deriveAuroraPalette(hue));
  };
}
