"use strict";

// Fondo animado "Halftone CMYK" (@paper-design/shaders -- la version
// vanilla del paquete, SIN @paper-design/shaders-react, asi que no hace
// falta React para nada: mismo motivo por el que silk-aurora-background.js
// termino portado a JS plano en vez de quedar como componente de React).
// Reemplaza ese fondo (que sigue en el repo por si se quiere volver, ver
// el comentario con la paleta morada/dorada ahi adentro) -- este SI es
// modulo ES (import de node_modules), asi que va como
// <script type="module"> en index.html, no <script> comun.
//
// El efecto trama la MISMA imagen que ya alimentaba el retinte de color
// del aurora (#preview-image, el fotograma del preview) -- no hace falta
// extraerle un color por separado como haciamos antes (extractImageHue):
// el halftone ya muestra los colores reales de la foto/video a traves de
// la separacion CMYK, no un tono abstracto derivado.

import {
  ShaderMount,
  halftoneCmykFragmentShader,
  HalftoneCmykTypes,
  getShaderColorFromString,
  getShaderNoiseTexture,
  defaultObjectSizing,
  ShaderFitOptions,
} from "../../node_modules/@paper-design/shaders/dist/index.js";

const container = document.querySelector(".background");
let mount = null;

if (container) {
  const uniforms = {
    u_image: undefined,
    u_noiseTexture: getShaderNoiseTexture(),
    // Fondo (papel) oscuro, no el blanco del ejemplo original -- esta app
    // es de tema oscuro, un colorBack claro se comeria todo el contraste
    // del resto de la UI encima.
    u_colorBack: getShaderColorFromString("#0a0a0c"),
    u_colorC: getShaderColorFromString("#00b3ff"),
    u_colorM: getShaderColorFromString("#fc4f9d"),
    u_colorY: getShaderColorFromString("#ffd900"),
    u_colorK: getShaderColorFromString("#e8e8ea"),
    u_size: 0.2,
    u_contrast: 1,
    u_softness: 1,
    u_grainSize: 0.5,
    u_grainMixer: 0,
    u_grainOverlay: 0,
    u_gridNoise: 0.2,
    u_floodC: 0.15,
    u_floodM: 0,
    u_floodY: 0,
    u_floodK: 0,
    u_gainC: 0.3,
    u_gainM: 0,
    u_gainY: 0.2,
    u_gainK: 0,
    u_type: HalftoneCmykTypes.ink,
    u_fit: ShaderFitOptions.cover,
    u_scale: defaultObjectSizing.scale,
    u_rotation: defaultObjectSizing.rotation,
    u_originX: defaultObjectSizing.originX,
    u_originY: defaultObjectSizing.originY,
    u_offsetX: defaultObjectSizing.offsetX,
    u_offsetY: defaultObjectSizing.offsetY,
    u_worldWidth: defaultObjectSizing.worldWidth,
    u_worldHeight: defaultObjectSizing.worldHeight,
  };
  try {
    mount = new ShaderMount(container, halftoneCmykFragmentShader, uniforms);
  } catch (e) {
    mount = null; // sin WebGL2 disponible -- se queda el fondo estatico de siempre
  }
}

// Llamado desde app.js (Preview.onReady/Preview.applyState) -- misma
// senal que antes usaba tintBackgroundFromImage. img=null (sin medio
// cargado) deja el fondo solo con colorBack, sin trama (no hay imagen que
// tramar).
window.setHalftoneBackgroundImage = function (img) {
  if (!mount) return;
  mount.setUniforms({ u_image: img || undefined });
};
