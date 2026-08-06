"use strict";

// Fondo animado "estilo Spotify" (el fondo dinamico de Spotify/Apple Music:
// manchones grandes y desenfocados, con los colores REALES de la portada,
// flotando solos de forma organica) -- implementacion propia, sin WebGL: en
// vez de un shader (como el aurora viejo, ver silk-aurora-background.js) son
// N <div> con blur de CSS moviendose por transform, mas barato y sin
// necesitar contexto WebGL.
//
// La diferencia de fondo con el sistema de siempre (background-tint.js, los
// 3 manchones del SVG en index.html) es que ese saca UN matiz dominante y
// rota los 3 colores del diseno a ese matiz -- acá se sacan VARIOS colores
// realmente distintos de la imagen (rojo+dorado+negro se ve rojo+dorado+
// negro, no 3 tonos del mismo matiz), que es lo que hace que el fondo de
// Spotify se sienta "la portada", no "un color ambiente".
//
// Mismo patron que halftone-background.js: modulo standalone que expone una
// sola funcion (setSpotifyBackgroundImage) y la llaman los mismos 3 lugares
// de siempre (app.js x2, live-preview.js) -- ver el comentario ahi. Si este
// script no esta en index.html, esa funcion no existe y esos 3 llamados no
// hacen nada.
//
// Todo el archivo va adentro de un IIFE: los <script> comunes (no-module)
// comparten UN mismo scope de nivel superior para const/let entre todos los
// hermanos del documento -- sin esto, "SAMPLE_SIZE"/"sampleCtx" chocaban con
// los mismos nombres de background-tint.js y tiraban SyntaxError, tumbando
// el archivo entero (ningun blob se llegaba a crear).
(function () {

const CONTAINER_SEL = ".background";
const BLOB_COUNT = 4;

// Paleta de reposo (sin medio cargado). Menos colores que manchones a
// proposito (2 contra BLOB_COUNT=4) -- se reparten via NEUTRAL_ASSIGNMENT,
// mas abajo.
const NEUTRAL_PALETTE = ["#1B1A1A", "#0C0A5C"];

// Que manchon usa que color de NEUTRAL_PALETTE, EN REPOSO -- pedido
// explicito: el oscuro (indice 0) mas abundante que el azul (indice 1), asi
// el azul queda como acento en vez de repartirse mitad y mitad. Un simple
// "i % NEUTRAL_PALETTE.length" (lo que se usaba antes) siempre da 2 y 2 con
// 4 manchones, sin importar el orden de la paleta -- por eso hace falta
// este mapeo aparte en vez de reordenar NEUTRAL_PALETTE nomas.
// Solo pesa ESTO, el estado sin medio: con una foto/video cargado
// extractPalette saca colores reales y este array no entra en juego.
const NEUTRAL_ASSIGNMENT = [0, 0, 0, 1]; // 3 manchones oscuros, 1 azul

// --------------------------------------------------------- extraccion de color

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

const SAMPLE_SIZE = 32;
let sampleCtx = null;

function ensureSampleCtx() {
  if (!sampleCtx) {
    const canvas = document.createElement("canvas");
    canvas.width = SAMPLE_SIZE;
    canvas.height = SAMPLE_SIZE;
    sampleCtx = canvas.getContext("2d", { willReadFrequently: true });
  }
  return sampleCtx;
}

// Histograma de matices: 12 casilleros de 30 grados. A cada pixel util (ni
// casi negro ni casi blanco -- eso casi siempre es letterbox/fondo horneado
// por ffmpeg, no "el color de la foto") le suma su peso (mas saturado y mas
// cercano a luminosidad media, mas pesa) al casillero de su matiz, y
// acumula el RGB pesado para poder promediar el color real de ese casillero
// despues -- no alcanza con "el centro del casillero", dos fotos que caen
// en el mismo rango de 30 grados pueden tener tonos bastante distintos
// adentro.
const HUE_BINS = 12;
const BIN_SPAN = 360 / HUE_BINS;

function extractPalette(source, count) {
  if (!source || !(source.naturalWidth || source.videoWidth || source.width)) return null;
  const ctx = ensureSampleCtx();
  let data;
  try {
    ctx.drawImage(source, 0, 0, SAMPLE_SIZE, SAMPLE_SIZE);
    data = ctx.getImageData(0, 0, SAMPLE_SIZE, SAMPLE_SIZE).data;
  } catch (e) {
    return null; // <video>/<canvas> con tainted origin, o fuente sin datos todavia
  }
  const binWeight = new Float64Array(HUE_BINS);
  const binR = new Float64Array(HUE_BINS);
  const binG = new Float64Array(HUE_BINS);
  const binB = new Float64Array(HUE_BINS);
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 200) continue;
    const r = data[i], g = data[i + 1], b = data[i + 2];
    const [h, s, l] = rgbToHsl(r, g, b);
    if (l < 0.08 || l > 0.92) continue;
    const peso = s * (1 - Math.abs(l - 0.5) * 2) + 0.05; // +0.05: un gris apagado tambien cuenta algo
    const bin = Math.min(HUE_BINS - 1, Math.floor(h / BIN_SPAN));
    binWeight[bin] += peso;
    binR[bin] += r * peso;
    binG[bin] += g * peso;
    binB[bin] += b * peso;
  }
  const orden = [...binWeight.keys()].sort((a, b) => binWeight[b] - binWeight[a]);
  const util = orden.filter((i) => binWeight[i] > 0);
  if (!util.length) return null; // imagen sin color util (todo negro/blanco, por ejemplo)

  // Cuanto peso tiene que juntar un matiz para MERECER un manchon propio.
  // Antes bastaba con aparecer (binWeight > 0) y los manchones se repartian
  // uno por matiz con "util[i % util.length]" -- o sea que el 1er, 2do, 3er y
  // 4to matiz se llevaban un manchon cada uno, sin importar si el 2do era la
  // mitad de la portada o cuatro pixeles perdidos.
  //
  // Medido con una portada 97.6% roja que tenia un detalle verde chico
  // (150x150 sobre 1000x1000): el rojo se llevaba UN manchon y el verde --1.9%
  // del peso-- se llevaba OTRO, o sea el 25% del fondo, empatado con el
  // dominante. Encima el saturate(2.2) de .spotify-bg-layer lo dejaba
  // fluorescente: es el "verde muy intenso que no existe en la portada" que se
  // reporto. Con este umbral ese verde (y el lima del 0.3%, y el naranja del
  // 0.1%) quedan afuera y los 4 manchones se los queda el rojo.
  const MIN_SHARE = 0.06;
  const pesoTotal = util.reduce((suma, i) => suma + binWeight[i], 0);
  const dominantes = util.filter((i) => binWeight[i] >= pesoTotal * MIN_SHARE);
  // util[0] es el mas pesado de todos, asi que siempre pasa el umbral -- pero
  // por si el redondeo deja la lista vacia, se garantiza el dominante.
  if (!dominantes.length) dominantes.push(util[0]);

  // Cuantos manchones le toca a cada matiz, PROPORCIONAL a su peso (metodo del
  // resto mayor). Asi una portada con un rojo dominante y un azul secundario
  // sale mayormente roja con un acento azul, como en Spotify, en vez de mitad
  // y mitad. Con un solo matiz dominante se lleva los 4, que es justo lo que
  // hace que el fondo se lea como "el color de la portada".
  const pesoDom = dominantes.map((i) => binWeight[i]);
  const sumaDom = pesoDom.reduce((a, b) => a + b, 0);
  const exacto = pesoDom.map((w) => (count * w) / sumaDom);
  const cupos = exacto.map(Math.floor);
  let sobran = count - cupos.reduce((a, b) => a + b, 0);
  // Los que quedaron con la fraccion mas alta se llevan los manchones que
  // sobran del redondeo hacia abajo.
  const porResto = exacto
    .map((e, k) => [k, e - Math.floor(e)])
    .sort((a, b) => b[1] - a[1]);
  for (let p = 0; sobran > 0; p = (p + 1) % porResto.length, sobran--) {
    cupos[porResto[p][0]]++;
  }

  // Profundidad: el mismo matiz repetido en 4 manchones identicos se ve como
  // un lavado plano de un solo color de borde a borde (lo que se reporto como
  // "parece una imagen de colores desenfocada"). Bajandole la intensidad a
  // unos y no a otros, los manchones se leen como MASAS distintas -- que es lo
  // que hace el fondo de Spotify incluso con una portada de un solo color.
  // Solo hacia abajo (nunca >1): con mix-blend-mode:screen subir el brillo
  // empuja la suma hacia el blanco y se lava.
  const PROFUNDIDAD = [1, 0.85, 0.7, 0.55];

  const colores = [];
  dominantes.forEach((bin, k) => {
    const w = binWeight[bin];
    const base = [binR[bin] / w, binG[bin] / w, binB[bin] / w];
    for (let n = 0; n < cupos[k]; n++) {
      const f = PROFUNDIDAD[colores.length % PROFUNDIDAD.length];
      colores.push(base.map((v) => Math.round(v * f)));
    }
  });
  return colores;
}

// -------------------------------------------------------------------- DOM

function hexToRgb(hex) {
  const n = hex.replace("#", "");
  return [parseInt(n.slice(0, 2), 16), parseInt(n.slice(2, 4), 16), parseInt(n.slice(4, 6), 16)];
}

// El "piso" de color (ver iniciarAnimacionColor mas abajo) y los manchones
// van en capas HERMANAS, no uno adentro del otro -- los dos necesitan su
// propio mix-blend-mode:screen contra el #000 de fondo (ver styles.css), y
// blend-mode no se hereda ni se "suma" entre padre e hijo, cada elemento
// mezcla por separado contra lo que tenga DEBAJO en el arbol.
function crearManchones(container) {
  const capa = document.createElement("div");
  capa.className = "spotify-bg-layer";

  const piso = document.createElement("div");
  piso.className = "spotify-bg-wash";
  capa.appendChild(piso);

  const blobs = [];
  for (let i = 0; i < BLOB_COUNT; i++) {
    const el = document.createElement("div");
    el.className = "spotify-bg-blob";
    capa.appendChild(el);
    blobs.push(el);
  }
  // Velo oscuro ENCIMA de los manchones (ultimo hijo = mas arriba, y sin
  // mix-blend-mode para que oscurezca de verdad en vez de sumarse como hacen
  // los manchones). Sin esto el color llegaba con la misma intensidad a los
  // cuatro bordes y el fondo se leia como una foto desenfocada y plana; el
  // gradiente le da el arriba-claro/abajo-oscuro que hace que se sienta
  // profundo, y de paso levanta el contraste del texto del panel.
  const velo = document.createElement("div");
  velo.className = "spotify-bg-overlay";
  capa.appendChild(velo);
  container.appendChild(capa);
  return { blobs, piso };
}

// ---------------------------------------------------------------- animacion
//
// Cada manchon flota solo con una suma de 2 senos por eje (2 frecuencias
// distintas, fuera de fase) -- con un solo seno por eje el recorrido es una
// elipse perfecta y se nota "mecanico"; sumando una segunda frecuencia mas
// rapida y de menor amplitud el camino deja de repetirse en un ciclo corto y
// se ve mas organico, sin necesitar ruido de verdad (simplex, perlin) ni
// ninguna libreria. Los periodos (28-51s) son todos primos entre si a
// proposito, para que la combinacion tarde varios minutos en notarse
// repetida.
const MOTION = [
  { ax1: 22, fx1: 2 * Math.PI / 37, px1: 0.0, ax2: 9, fx2: 2 * Math.PI / 13, px2: 1.1,
    ay1: 18, fy1: 2 * Math.PI / 29, py1: 2.3, ay2: 7, fy2: 2 * Math.PI / 11, py2: 0.4 },
  { ax1: 20, fx1: 2 * Math.PI / 41, px1: 1.7, ax2: 8, fx2: 2 * Math.PI / 17, px2: 3.0,
    ay1: 24, fy1: 2 * Math.PI / 33, py1: 0.6, ay2: 6, fy2: 2 * Math.PI / 19, py2: 2.1 },
  { ax1: 25, fx1: 2 * Math.PI / 47, px1: 3.4, ax2: 7, fx2: 2 * Math.PI / 23, px2: 0.9,
    ay1: 19, fy1: 2 * Math.PI / 39, py1: 1.4, ay2: 8, fy2: 2 * Math.PI / 15, py2: 3.1 },
  { ax1: 18, fx1: 2 * Math.PI / 43, px1: 2.2, ax2: 10, fx2: 2 * Math.PI / 19, px2: 0.2,
    ay1: 21, fy1: 2 * Math.PI / 31, py1: 3.5, ay2: 9, fy2: 2 * Math.PI / 13, py2: 1.6 },
];

// Posiciones de reposo (centro de cada manchon en % del contenedor) -- fijas
// para que los 4 queden repartidos y se solapen entre si (asi se mezclan los
// colores al desenfocar, como un mesh gradient) en vez de quedar cada uno en
// su rincon.
const CENTROS = [
  [28, 32], [72, 28], [30, 74], [76, 78],
];

// Multiplica el tiempo que ven las funciones seno de MOTION -- mas facil de
// tocar que reescribir los 16 periodos a mano, y mantiene la misma relacion
// entre ellos (siguen siendo primos entre si, la combinacion no se nota
// repetida antes). 2.2 = pedido explicito de que se sienta mas rapido.
const MOTION_SPEED = 2.2;

function iniciarAnimacionPosicion(blobs) {
  const t0 = performance.now();
  function paso(ahora) {
    const t = ((ahora - t0) / 1000) * MOTION_SPEED;
    for (let i = 0; i < blobs.length; i++) {
      const m = MOTION[i % MOTION.length];
      const dx = m.ax1 * Math.sin(t * m.fx1 + m.px1) + m.ax2 * Math.sin(t * m.fx2 + m.px2);
      const dy = m.ay1 * Math.sin(t * m.fy1 + m.py1) + m.ay2 * Math.sin(t * m.fy2 + m.py2);
      blobs[i].style.transform = `translate(${dx}%, ${dy}%)`;
    }
    requestAnimationFrame(paso);
  }
  requestAnimationFrame(paso);
}

// -------------------------------------------------------------- color, suave
//
// Igual que el ambilight de background-tint.js: la fuente se muestrea varias
// veces por segundo (ver los 3 llamadores de setSpotifyBackgroundImage), pero
// el color de cada manchon se ACERCA de a poco al nuevo en vez de saltar --
// sin esto, cada corte de escena en el video pegaba un salto de color feo.
// Interpolacion RGB lineal (no por matiz/HSL como el ambilight de un solo
// color): aca cada manchon YA tiene una identidad de color propia (el 1er
// mas dominante, el 2do el que sigue, etc.), asi que no hace falta el camino
// corto del circulo de matices -- ir derecho de un RGB a otro alcanza.
const LERP_FACTOR = 0.08;
// Para VIDEO (ver window.setSpotifyBackgroundImage, mas abajo): mismo pedido
// que AMBILIGHT_FACTOR_VIDEO en live-preview.js -- un corte de escena se
// sentia como un salto de color en vez de una transicion. Esto corre por su
// propio requestAnimationFrame (60/s) y no por el muestreo del ambilight
// (8/s), asi que con LERP_FACTOR normal convergia en menos de medio segundo;
// un factor mas chico hace que tarde varios segundos. Una foto es un cuadro
// fijo (sin cortes que suavizar), asi que sigue con LERP_FACTOR de siempre.
const LERP_FACTOR_VIDEO = 0.02;
let lerpFactorActual = LERP_FACTOR;
// Ver NEUTRAL_ASSIGNMENT mas arriba (el oscuro mas abundante que el azul).
let colorActual = Array.from({ length: BLOB_COUNT }, (_, i) => hexToRgb(NEUTRAL_PALETTE[NEUTRAL_ASSIGNMENT[i]]));
let colorObjetivo = colorActual.map((c) => c.slice());

// piso: el div .spotify-bg-wash (ver crearManchones) -- un degrade FIJO (no
// se mueve, no tiene posicion propia) con los mismos colores que los
// manchones, de piso permanente debajo de ellos. Sin esto, cuando el
// vaivien organico de iniciarAnimacionPosicion aleja a dos o tres manchones
// entre si a la vez (van a su aire, cada uno con su propio periodo -- ver
// MOTION) quedaban huecos sin ningun manchon encima, y ahi se veia el #000
// de base de .spotify-bg-layer a pleno -- el "a veces se pone oscuro en
// algunas partes" que se reporto. El piso no reemplaza a los manchones (esos
// siguen dando el brillo vivo, movido, con nucleo solido) -- solo se asegura
// de que NUNCA haya negro puro de fondo, cubriendo entero todo el rato.
function iniciarAnimacionColor(blobs, piso) {
  function paso() {
    const rgbs = [];
    for (let i = 0; i < blobs.length; i++) {
      const actual = colorActual[i], obj = colorObjetivo[i];
      for (let k = 0; k < 3; k++) actual[k] += (obj[k] - actual[k]) * lerpFactorActual;
      // 0-38% solido y no 0%: un radial-gradient que empieza a apagarse
      // desde el mismo centro se ve como un punto tenue una vez desenfocado
      // (18vmin de blur, ver .spotify-bg-blob en styles.css) -- sosteniendo
      // el color a pleno en el nucleo, el manchon se lee como una mancha de
      // luz llena en vez de un resplandor debil.
      const rgb = `${actual[0] | 0} ${actual[1] | 0} ${actual[2] | 0}`;
      rgbs.push(rgb);
      blobs[i].style.background =
        `radial-gradient(circle, rgb(${rgb}) 0%, rgb(${rgb}) 38%, transparent 88%)`;
    }
    // linear-gradient con los mismos 4 colores, en diagonal -- no hace falta
    // que se mueva de forma organica como los manchones, es solo el piso que
    // queda fijo detras de ellos.
    piso.style.background = `linear-gradient(125deg, ${rgbs.map((c) => `rgb(${c})`).join(", ")})`;
    requestAnimationFrame(paso);
  }
  requestAnimationFrame(paso);
}

// ------------------------------------------------------------------- init

let blobs = null;

function init() {
  const container = document.querySelector(CONTAINER_SEL);
  if (!container) return;
  const creados = crearManchones(container);
  blobs = creados.blobs;
  blobs.forEach((el, i) => {
    const [cx, cy] = CENTROS[i % CENTROS.length];
    el.style.left = `${cx}%`;
    el.style.top = `${cy}%`;
  });
  iniciarAnimacionPosicion(blobs);
  iniciarAnimacionColor(blobs, creados.piso);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}

// source: <img>/<video>/<canvas> ya cargado, o null (sin medio -- vuelve a
// la paleta de reposo). esVideo: ver LERP_FACTOR_VIDEO arriba -- solo lo
// manda live-preview.js (arrancarAmbilight, el unico llamador que sabe si la
// fuente es un video reproduciendose); los otros dos llamadores (app.js,
// fotograma estatico y "sin medio") ni lo pasan, asi que siguen con el
// LERP_FACTOR de siempre.
window.setSpotifyBackgroundImage = function (source, esVideo) {
  if (!blobs) return; // el DOM todavia no cargo (llamado muy temprano)
  lerpFactorActual = esVideo ? LERP_FACTOR_VIDEO : LERP_FACTOR;
  const paleta = source && extractPalette(source, BLOB_COUNT);
  // Ver NEUTRAL_ASSIGNMENT mas arriba (el oscuro mas abundante que el azul,
  // SOLO en reposo -- con medio cargado va por extractPalette, arriba).
  colorObjetivo = paleta
    ? paleta.map((c) => c.slice())
    : Array.from({ length: BLOB_COUNT }, (_, i) => hexToRgb(NEUTRAL_PALETTE[NEUTRAL_ASSIGNMENT[i]]));
};

})();
