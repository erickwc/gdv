// Fondo animado con kawarp: la PORTADA MISMA deformada y difuminada, no una
// paleta de colores sacada de ella. Es la tecnica del fondo de Spotify/Apple
// Music, y la que usa la extension spicy-lyrics -- de ese proyecto no se tomo
// nada de codigo (es AGPL-3.0 y obligaria a licenciar toda esta app igual):
// solo se usa el motor que ellos tambien usan como dependencia, @kawarp/core,
// que es MIT e independiente (ver vendor/kawarp.js).
//
// Por que esto se ve mas "Spotify" que los manchones de
// spotify-style-background.js: ahi el fondo se armaba con 4 circulos pintados
// con los colores dominantes de la portada, asi que el resultado eran manchas
// de color plano. Aca el fondo ES la portada (deformada por domain warping y
// difuminada con Kawase blur), de modo que las formas y las mezclas de color
// salen del propio arte. Efecto secundario importante: es IMPOSIBLE que
// aparezca un color que no este en la portada -- el bug que se reporto como
// "un verde muy intenso que no existe en la portada" y que se reprodujo
// midiendo (un detalle verde de 1.9% de la imagen se llevaba 3 de los 4
// manchones).
//
// Mismo patron que los otros fondos: standalone, expone UNA funcion y la
// llaman los mismos lugares de siempre. Si este script no esta cargado, la
// funcion no existe y esos llamados no hacen nada.
import { Kawarp } from "./vendor/kawarp.js";

const CONTAINER_SEL = ".background";

// Reposo (sin medio cargado): los mismos dos colores que usaba el fondo de
// manchones, para que la app se vea igual antes de cargar nada.
const NEUTRAL_GRADIENT = ["#1B1A1A", "#0C0A5C"];

// Cada cuanto se le puede pasar un cuadro NUEVO. El ambilight muestrea 8 veces
// por segundo (ver AMBILIGHT_MS en live-preview.js) y cada loadImageElement
// rehace el desenfoque y arranca un crossfade: a ese ritmo seria trabajo
// constante de GPU para algo que se mueve muy despacio. Con un cuadro cada
// 900ms y el crossfade de kawarp (transitionDuration) el color acompana al
// video de forma continua, sin escalones.
const MIN_MS_ENTRE_CUADROS = 900;

let kawarp = null;
let canvas = null;
let capaEl = null;
let ultimoEnvio = 0;

// Las perillas del fondo viven en el CSS (ver las variables --fondo-* en
// .kawarp-bg-layer, styles.css) y no como constantes aca. El motivo: lo que
// dibuja este fondo es un canvas de WebGL, y CSS no puede tocar eso -- un
// filter puesto sobre el canvas se aplicaria ENCIMA del resultado, no a como
// se genera. Poniendo los numeros en variables CSS se ajustan igual desde
// styles.css (que es donde se buscan) y de paso quedan documentados al lado
// del resto del fondo.
const PERILLAS = {
  saturation: ["--fondo-saturacion", 1.6],
  blurPasses: ["--fondo-desenfoque", 12],
  warpIntensity: ["--fondo-deformacion", 0.85],
  animationSpeed: ["--fondo-velocidad", 0.35],
  scale: ["--fondo-zoom", 1.15],
};

// Lee las variables del CSS. Si una falta o no es un numero se usa el valor de
// respaldo de PERILLAS, asi borrar una variable del CSS no deja el fondo negro.
function leerPerillas() {
  const estilo = capaEl ? getComputedStyle(capaEl) : null;
  const opciones = {};
  for (const [opcion, [variable, respaldo]] of Object.entries(PERILLAS)) {
    const crudo = estilo ? estilo.getPropertyValue(variable).trim() : "";
    const n = parseFloat(crudo);
    opciones[opcion] = Number.isFinite(n) ? n : respaldo;
  }
  return opciones;
}

function init() {
  const container = document.querySelector(CONTAINER_SEL);
  if (!container) return;

  const capa = document.createElement("div");
  capa.className = "kawarp-bg-layer";

  canvas = document.createElement("canvas");
  canvas.className = "kawarp-bg-canvas";
  capa.appendChild(canvas);

  // Velo oscuro encima (ver styles.css): le da el arriba-claro/abajo-oscuro
  // que hace que el fondo se sienta profundo en vez de plano, y de paso
  // levanta el contraste del texto del panel.
  const velo = document.createElement("div");
  velo.className = "kawarp-bg-overlay";
  capa.appendChild(velo);

  container.appendChild(capa);
  capaEl = capa; // leerPerillas() necesita la capa YA en el DOM para poder
                 // resolver sus variables CSS con getComputedStyle

  ajustarTamano();
  try {
    kawarp = new Kawarp(canvas, {
      ...leerPerillas(),
      // Esta no es perilla: es el ruido que rompe el bandeado de los
      // degradados. No hay motivo para tocarlo desde el CSS.
      dithering: 0.008,
    });
    kawarp.loadGradient(NEUTRAL_GRADIENT, 160);
    kawarp.start();
  } catch (e) {
    // Sin WebGL (o si el contexto se pierde): se saca la capa y queda a la
    // vista el SVG de siempre, que es opaco -- igual que hacia el aurora viejo.
    console.error("[kawarp] no se pudo iniciar, se sigue con el fondo de siempre:", e);
    capa.remove();
    canvas = null;
    capaEl = null;
    kawarp = null;
    return;
  }

  // Con la capa montada, el SVG del fondo queda tapado: background-tint.js lo
  // consulta para no gastar en repintarle los manchones (cada fill obliga a
  // rehacer un feGaussianBlur de stdDeviation 175 sobre 2388x1932).
  window.kawarpFondoActivo = true;

  window.addEventListener("resize", () => {
    ajustarTamano();
    if (kawarp) kawarp.resize();
  });
}

// El canvas se dibuja a la resolucion REAL de pantalla: sin esto, en un monitor
// con escalado (125%/150%, lo normal en Windows) el fondo sale interpolado y se
// notan escalones en los degradados.
function ajustarTamano() {
  if (!canvas) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
  const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}

// Vuelve a leer las variables --fondo-* del CSS y las aplica AL INSTANTE, sin
// reabrir la app. Para ajustar a ojo desde DevTools:
//
//   document.querySelector(".kawarp-bg-layer").style.setProperty("--fondo-saturacion", 2.4);
//   releerPerillasDelFondo();
//
// Cuando el valor guste, se pasa a .kawarp-bg-layer en styles.css (lo escrito
// por DevTools se pierde al recargar). Devuelve las opciones que quedaron
// puestas, para poder verlas.
window.releerPerillasDelFondo = function () {
  if (!kawarp) return null;
  const opciones = leerPerillas();
  kawarp.setOptions(opciones);
  return opciones;
};

// source: <img>/<video>/<canvas> ya cargado, o null (sin medio -- vuelve al
// degradado de reposo). Lo llaman los mismos lugares que ya alimentan a
// background-tint.js y spotify-style-background.js.
window.setKawarpBackgroundImage = function (source) {
  if (!kawarp) return; // sin WebGL, o el DOM todavia no cargo
  if (!source) {
    ultimoEnvio = 0;
    kawarp.loadGradient(NEUTRAL_GRADIENT, 160);
    return;
  }
  // Una fuente sin pixeles todavia (video que no decodifico, img sin cargar)
  // dejaria el fondo en negro: se espera al proximo muestreo.
  if (!(source.naturalWidth || source.videoWidth || source.width)) return;
  const ahora = performance.now();
  if (ahora - ultimoEnvio < MIN_MS_ENTRE_CUADROS) return;
  ultimoEnvio = ahora;
  try {
    kawarp.loadImageElement(source);
  } catch (e) {
    // Un cuadro que no se pudo subir a la GPU no es motivo para tumbar el
    // fondo: se queda el anterior y se reintenta en el proximo muestreo.
  }
};
