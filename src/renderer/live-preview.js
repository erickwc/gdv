"use strict";

// Previsualizador EN VIVO: compone en un <canvas> lo mismo que armaba ffmpeg
// (medio + texturas + plantilla) y lo dibuja cuadro a cuadro. Reemplaza los dos
// previews que hacia Python -- el fotograma estatico (request_preview) y el
// fragmento del loop (request_loop_preview) -- que costaban segundos de ffmpeg
// en CADA cambio y llenaban la pantalla de "Generando...". ffmpeg queda solo
// para la exportacion final, que es la unica que tiene que ser exacta.
//
// El orden de dibujo es el mismo del filter_complex (ver build_filtergraph en
// engine.py), y de ahi salen las cuentas de aca:
//
//   negro (todo el lienzo)
//   -> medio: recorte de "Ajustar imagen" y despues CUBRIR su caja
//   -> texturas encima, en orden, con su opacidad y modo de fusion, RECORTADAS
//      a la caja del medio (el borde negro nunca lleva grano, igual que alla:
//      el pad se agrega despues de mezclar)
//   -> plantilla al final, entera sobre el lienzo
//
// La caja del medio la calcula Python (content_box en get_state, que sale de
// engine.content_box) -- no se replica build_layout aca para que no haya dos
// versiones de la misma cuenta que se puedan desincronizar.
window.LivePreview = (() => {
  let canvas = null;
  let ctx = null;

  // Medio: el archivo ORIGINAL, no un mp4 compuesto. Cuando es video, este
  // elemento es el que manda -- reproduce, se busca y se pausa, y el canvas
  // solo lo dibuja. Asi los controles de la UI (barra, boton, beat) siguen
  // hablando con un <video> de verdad.
  let fuente = null;
  let esVideo = false;
  let rutaFuente = null;   // el archivo que se decodifica (puede ser la copia liviana)
  let rutaLogica = null;   // el medio de verdad, el que se va a exportar

  let estado = null;          // ultimo get_state conocido
  let capas = [];             // {el, esVideo, blend, opacity, scale, listo}
  let plantilla = null;       // {el, listo}
  let rutaPlantilla = null;

  let rafId = null;           // respaldo cuando no hay requestVideoFrameCallback
  let vfcId = null;           // un dibujo por cuadro REAL del video
  let dibujarPendiente = false;
  let plantillaHorneada = null;   // {lienzo, w, h} -- ver hornearPlantilla

  // Nombre del modo de fusion (los de Photoshop que usa la UI) al del canvas.
  // Los cinco existen tal cual en 2D, con la misma formula que aplica el filtro
  // blend de ffmpeg -- ver BLEND_MODES en engine.py.
  const FUSION = {
    "Normal": "source-over",
    "Aclarar": "lighten",
    "Trama": "screen",
    "Multiplicar": "multiply",
    "Superponer": "overlay",
    "Luz suave": "soft-light",
  };

  const VIDEO_EXTS = [".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".gif"];

  function esRutaDeVideo(ruta) {
    const punto = ruta.lastIndexOf(".");
    return punto !== -1 && VIDEO_EXTS.includes(ruta.slice(punto).toLowerCase());
  }

  // encodeURI() deja "#" y "?" SIN escapar a proposito -- son validos en una
  // URL ya armada (fragmento/query) -- pero aca lo que entra es una RUTA
  // CRUDA de archivo, no una URL: un nombre con "#" (una tonalidad musical,
  // "A#min", tipico en nombres de beats/proyectos de musica) cortaba la URL
  // justo ahi y el resto de la ruta se perdia como fragmento, sin avisar
  // ningun error -- el archivo quedaba "cargado" para la app (Python lee la
  // ruta cruda, sin este problema) pero mudo/negro en el previsualizador.
  // Se escapan los dos a mano, DESPUES de encodeURI() (no antes: haria
  // doble-escape del "%" que agrega).
  function urlDeArchivo(ruta) {
    let limpia = ruta.replace(/\\/g, "/");
    if (!limpia.startsWith("/")) limpia = `/${limpia}`;
    return `file://${encodeURI(limpia).replace(/#/g, "%23").replace(/\?/g, "%3F")}`;
  }

  // ------------------------------------------------------------ el medio

  // logica = el medio de verdad (media_path). ruta = de donde se sacan los
  // fotogramas, que puede ser la copia liviana que arma Python cuando el clip es
  // grande (ver preview_path en get_state y _preview_proxy_job en api.py).
  //
  // Girar/espejar no pasa por aca: no cambian de archivo (son propiedades
  // del proyecto, se aplican al dibujar -- ver dibujarMedio), asi que el
  // video ni se entera y sigue corriendo.
  function cargarFuente(ruta, mediaEsVideo, logica) {
    if (ruta === rutaFuente) return;
    // Mismo medio pero otro archivo: termino de armarse la copia liviana. Se
    // conserva el momento para que el cambio no se note -- de otro modo el
    // video saltaba al principio solo, a media reproduccion.
    const mismoMedio = mediaEsVideo && esVideo && !!logica && logica === rutaLogica;
    const tiempoPrevio = mismoMedio && fuente ? fuente.currentTime : 0;
    // Si se estaba reproduciendo, el medio NUEVO tambien arranca solo --
    // antes esto miraba mismoMedio (arriba), asi que cargar un archivo
    // DISTINTO por completo (otra descarga, soltar/pegar otro archivo) con
    // el preview andando lo dejaba pausado en el primer cuadro sin avisar
    // nada: quedaba clavado ahi hasta que el usuario pausara y volviera a
    // darle play a mano. La app esta pensada para armar mas de un video en
    // la misma sesion, asi que cambiar de medio en el medio de una
    // reproduccion no puede frenarla en seco. Con un medio distinto no
    // corresponde restaurar tiempoPrevio (es metraje de otro archivo, ver
    // mas abajo) -- play() arranca del principio del recorte, como toca.
    const veniaAndando = reproduciendo();
    rutaFuente = ruta;
    rutaLogica = logica || ruta;
    esVideo = mediaEsVideo;
    if (fuente) {
      pararCuadros(); // el pedido de cuadro es del elemento que se va
      if (fuente.pause) fuente.pause();
      fuente.removeAttribute("src");
      if (fuente.load) fuente.load();
      fuente.remove();
      fuente = null;
    }
    if (!ruta) return;

    if (mediaEsVideo) {
      fuente = document.createElement("video");
      fuente.muted = true;         // el beat va aparte, igual que antes
      fuente.playsInline = true;
      fuente.preload = "auto";
      // Sin loop nativo: la vuelta la controla dibujar(), que tiene que
      // respetar el recorte (trim_start..trim_end), no el archivo entero.
      fuente.loop = false;
      const el = fuente;
      // Buscar un momento con el video en pausa: el fotograma nuevo tarda en
      // estar listo, asi que el redibujo va cuando llega y no cuando se pidio
      // (ver dibujarCuandoLlegue, que es el camino principal -- este es el
      // respaldo).
      el.addEventListener("seeked", () => {
        if (el === fuente) pedirDibujo();
      });
      // Red de seguridad: si igual llega al final (el recorte tendria que dar la
      // vuelta un pelo antes), se vuelve al principio a mano. Sin fotogramas
      // nuevos requestVideoFrameCallback no vuelve a avisar, y el
      // previsualizador se quedaba plantado en el ultimo cuadro.
      el.addEventListener("ended", () => {
        if (el !== fuente) return; // es el elemento de un medio anterior
        el.currentTime = recorte().inicio;
        const p = el.play();
        if (p && p.catch) p.catch(() => {});
        programarCuadro();
      });
    } else {
      fuente = document.createElement("img");
    }
    // Fuera de pantalla pero en el DOM: un <video> suelto no siempre decodifica.
    fuente.style.position = "absolute";
    fuente.style.width = "1px";
    fuente.style.height = "1px";
    fuente.style.opacity = "0";
    fuente.style.pointerEvents = "none";
    fuente.setAttribute("aria-hidden", "true");
    document.body.appendChild(fuente);
    fuente.addEventListener(esVideo ? "loadeddata" : "load", () => pedirDibujo(), { once: true });
    // Restaurar tiempoPrevio NO puede depender de veniaAndando: antes solo
    // pasaba adentro del "arranca solo", asi que reemplazar la fuente con el
    // video en PAUSA (la copia liviana termina de armarse, o un giro, mientras
    // el usuario tenia el previsualizador detenido) perdia el lugar en
    // silencio -- el elemento nuevo arranca en el segundo 0 y ahi se quedaba
    // hasta que el usuario le daba play, momento en el que arrancaba desde el
    // principio en vez de seguir donde lo habia dejado.
    if (mismoMedio || veniaAndando) {
      const el = fuente;
      el.addEventListener("loadeddata", () => {
        if (el !== fuente) return;
        if (mismoMedio) el.currentTime = tiempoPrevio;
        if (veniaAndando) play();
      }, { once: true });
    }
    fuente.src = urlDeArchivo(ruta);
  }

  // ------------------------------------------------------- capas y plantilla

  function cargarCapas(layers) {
    const nuevas = [];
    (layers || []).forEach((capa) => {
      if (!capa || !capa.path) return;
      const previa = capas.find((c) => c.ruta === capa.path);
      let el = previa && previa.el;
      const capaEsVideo = esRutaDeVideo(capa.path);
      if (!el) {
        el = document.createElement(capaEsVideo ? "video" : "img");
        if (capaEsVideo) {
          el.muted = true;
          el.loop = true;
          el.playsInline = true;
          el.preload = "auto";
        }
        el.addEventListener(capaEsVideo ? "loadeddata" : "load", () => pedirDibujo(), { once: true });
        el.src = urlDeArchivo(capa.path);
      }
      nuevas.push({
        ruta: capa.path,
        el,
        // La baldosa ya armada se hereda: rearmarla cuesta (ver
        // patronDeBaldosa) y cada get_state pasa por aca. Si cambio la escala,
        // el tamano no coincide y se rearma sola.
        baldosa: previa && previa.baldosa,
        patronW: previa && previa.patronW,
        patronH: previa && previa.patronH,
        // El mosaico de Python tambien se hereda: si cambio la escala, su ruta
        // trae la escala nueva en el nombre y setPreparedTextures lo reemplaza.
        mosaico: previa && previa.mosaico,
        rutaMosaico: previa && previa.rutaMosaico,
        // La capa ya encajada en la caja tambien se hereda: es lo que se blitea
        // en cada cuadro (ver hornearTextura). Su clave lleva todo lo que la
        // define, asi que si algo cambio se rehornea sola.
        horneada: previa && previa.horneada,
        horneadaClave: previa && previa.horneadaClave,
        esVideo: capaEsVideo,
        blend: FUSION[capa.blend] || "source-over",
        // La UI guarda 0..100; el canvas quiere 0..1.
        opacity: Math.max(0, Math.min(100, Number(capa.opacity) || 0)) / 100,
        scale: Math.max(1, Number(capa.scale) || 100) / 100,
      });
    });
    capas = nuevas;
  }

  // Mosaicos preparados por Python (prepared_textures en api.py), uno por capa.
  // Llegan aparte del estado porque prepararlos cuesta: ver la explicacion alla.
  function setPreparedTextures(lista) {
    let algoNuevo = false;
    (lista || []).forEach((entrada) => {
      if (!entrada || !entrada.prepared) return;
      const capa = capas.find((c) => c.ruta === entrada.path);
      if (!capa || capa.rutaMosaico === entrada.prepared) return;
      capa.rutaMosaico = entrada.prepared;
      const el = document.createElement("img");
      el.addEventListener("load", () => pedirDibujo(), { once: true });
      el.src = urlDeArchivo(entrada.prepared);
      capa.mosaico = el;
      algoNuevo = true;
    });
    if (algoNuevo) pedirDibujo();
  }

  function cargarPlantilla(ruta) {
    if (ruta === rutaPlantilla) return;
    rutaPlantilla = ruta;
    plantilla = null;
    plantillaHorneada = null;
    if (!ruta) return;
    const el = document.createElement("img");
    el.addEventListener("load", () => pedirDibujo(), { once: true });
    el.src = urlDeArchivo(ruta);
    plantilla = { el };
  }

  // ------------------------------------------------------------- dibujo

  function medidasDe(el) {
    if (!el) return null;
    const w = el.videoWidth || el.naturalWidth;
    const h = el.videoHeight || el.naturalHeight;
    return w && h ? { w, h } : null;
  }

  function medidasFuente() {
    return medidasDe(fuente);
  }

  // "Cubrir" una caja con un origen, recortando lo que sobra por el centro --
  // el equivalente exacto de scale=...:force_original_aspect_ratio=increase
  // seguido de crop=... en el filtergraph.
  function recorteQueCubre(origenW, origenH, cajaW, cajaH) {
    const escala = Math.max(cajaW / origenW, cajaH / origenH);
    const w = cajaW / escala;
    const h = cajaH / escala;
    return { sx: (origenW - w) / 2, sy: (origenH - h) / 2, sw: w, sh: h };
  }

  // Modo "Completa" del recorte: en vez de recortar el origen para cubrir la
  // caja (arriba), lo encoge para que ENTRE completo -- el equivalente
  // exacto de scale=...:force_original_aspect_ratio=decrease seguido de
  // pad=... en el filtergraph. Devuelve el rectangulo DESTINO (dentro de la
  // caja) donde cae el origen entero, sin recortar nada -- el sobrante
  // (letterbox) lo pinta dibujarMedio.
  function rectanguloQueContiene(origenW, origenH, cajaW, cajaH) {
    const escala = Math.min(cajaW / origenW, cajaH / origenH);
    const w = origenW * escala;
    const h = origenH * escala;
    return { dx: (cajaW - w) / 2, dy: (cajaH - h) / 2, dw: w, dh: h };
  }

  // Giro/espejo del proyecto (ver media_rotation en api.py). Se aplican ACA,
  // al dibujar, y no rehaciendo el archivo: por eso el boton es instantaneo
  // hasta con un clip largo. La exportacion los hornea con los mismos filtros
  // y en el mismo orden -- girar y DESPUES espejar (ver
  // build_transform_filters en engine.py).
  function giro() {
    const g = (estado && estado.media_rotation) || 0;
    return ((g % 360) + 360) % 360;
  }

  // Rectangulo del fotograma YA GIRADO -> el mismo pedazo en coordenadas del
  // archivo, que es lo que sabe muestrear drawImage (el <video>/<img> decodifica
  // sin girar). crop_rect se guarda en coordenadas del fotograma girado -- el que
  // ve el usuario en "Ajustar imagen" -- igual que en ffmpeg, donde el transpose
  // va antes del crop.
  //
  // Se deshacen en el orden inverso al que se aplican: primero los espejos
  // (que van ultimos), despues el giro.
  function aCoordenadasDelArchivo(x, y, w, h, efW, efH) {
    if (estado && estado.media_flip_h) x = efW - x - w;
    if (estado && estado.media_flip_v) y = efH - y - h;
    switch (giro()) {
      case 90:  return { x: y, y: efW - x - w, w: h, h: w };
      case 180: return { x: efW - x - w, y: efH - y - h, w, h };
      case 270: return { x: efH - y - h, y: x, w: h, h: w };
      default:  return { x, y, w, h };
    }
  }

  function dibujarMedio(cd, medio, caja) {
    const med = medidasDe(medio);
    if (!med) return false;
    const g = giro();
    const deCostado = g === 90 || g === 270;
    // Tamano del fotograma tal como se VE (con el giro puesto) -- el mismo
    // que Python reporta en media_size y con el que calculo esta caja.
    const efW = deCostado ? med.h : med.w;
    const efH = deCostado ? med.w : med.h;
    // Recorte de "Ajustar imagen", en pixeles del fotograma girado (crop_rect
    // viene en fracciones 0..1, igual que en build_focus_crop).
    const r = (estado && estado.crop_rect) || [0, 0, 1, 1];
    const cx = r[0] * efW;
    const cy = r[1] * efH;
    const cw = Math.max(1, r[2] * efW);
    const chh = Math.max(1, r[3] * efH);

    // "Completa" (nunca estira el medio para llenar la ventana) vs
    // Cuadrado/Vertical (cubre, de siempre) -- mismo criterio, y misma
    // cuenta, que contain= en build_filtergraph. hw/hh es la huella visual
    // DENTRO de caja (todavia sin girar) y scx/scy/scw/sch el pedazo del
    // recorte que se llega a ver.
    const contiene = !!(estado && estado.crop_mode === "completa");
    let scx, scy, scw, sch, hw, hh;
    if (contiene && !(estado && estado.template_path)) {
      // El medio va SIEMPRE del mismo tamano -- el mas grande que entra
      // entero en el LIENZO -- y la caja (que el control de bordes abre y
      // cierra) solo hace de ventana: recorta lo que deja afuera en vez de
      // achicar la foto. Ver layout["natural"] en build_layout.
      const [lienzoW, lienzoH] = (estado && estado.canvas_size) || [2560, 1440];
      const escalaNat = Math.min(lienzoW / cw, lienzoH / chh);
      const natW = cw * escalaNat, natH = chh * escalaNat;
      hw = Math.min(caja.w, natW);
      hh = Math.min(caja.h, natH);
      // El pedazo visible del recorte, centrado (lo que la ventana deja ver).
      scw = cw * (hw / natW);
      sch = chh * (hh / natH);
      scx = cx + (cw - scw) / 2;
      scy = cy + (chh - sch) / 2;
    } else if (contiene) {
      // Con plantilla no hay control de bordes: la ventana ES el hueco y lo
      // que corresponde es que el medio entre entero ahi adentro.
      scx = cx; scy = cy; scw = cw; sch = chh;
      const d = rectanguloQueContiene(cw, chh, caja.w, caja.h);
      hw = d.dw; hh = d.dh;
    } else {
      const c = recorteQueCubre(cw, chh, caja.w, caja.h);
      scx = cx + c.sx; scy = cy + c.sy; scw = c.sw; sch = c.sh;
      hw = caja.w; hh = caja.h;
    }
    const src = aCoordenadasDelArchivo(scx, scy, scw, sch, efW, efH);

    // Letterbox de "Completa": el sobrante DENTRO de caja (fuera de la
    // huella hw x hh) queda negro, igual que el pad interno del filtergraph
    // -- se pinta antes de dibujar encima, sea cual sea el resto del camino.
    if (contiene && (hw < caja.w - 0.5 || hh < caja.h - 0.5)) {
      cd.fillStyle = "#000000";
      cd.fillRect(caja.x, caja.y, caja.w, caja.h);
    }
    if (!g && !(estado && (estado.media_flip_h || estado.media_flip_v))) {
      cd.drawImage(medio, src.x, src.y, src.w, src.h,
        caja.x + (caja.w - hw) / 2, caja.y + (caja.h - hh) / 2, hw, hh);
      return true;
    }
    cd.save();
    // Girar/espejar alrededor del CENTRO de la caja: la huella (hw x hh) ya
    // sale centrada en caja (huella completa en Cuadrado/Vertical, o
    // centrada por rectanguloQueContiene en Completa), asi que gira sobre
    // el mismo punto sin desplazarse. El lienzo aplica las transformaciones
    // de la ultima a la primera, asi que este orden (escala despues de
    // rotar en el codigo) dibuja: girar -> espejar.
    cd.translate(caja.x + caja.w / 2, caja.y + caja.h / 2);
    if (estado.media_flip_h || estado.media_flip_v) {
      cd.scale(estado.media_flip_h ? -1 : 1, estado.media_flip_v ? -1 : 1);
    }
    if (g) cd.rotate((g * Math.PI) / 180);
    // Estando de costado, la huella se dibuja con el ancho y el alto
    // cambiados: al girarla 90 grados termina midiendo hw x hh de verdad.
    const dw = deCostado ? hh : hw;
    const dh = deCostado ? hw : hh;
    cd.drawImage(medio, src.x, src.y, src.w, src.h, -dw / 2, -dh / 2, dw, dh);
    cd.restore();
    return true;
  }

  // Baldosa lista para repetir, con cache por tamano en la propia capa. Antes se
  // rearmaba en CADA cuadro (a 30 fps, redimensionar una textura de 3000px
  // sesenta veces por segundo) y ademas hacia falta recorrer sus pixeles, que
  // sin cache era imposible.
  function patronDeBaldosa(capa, w, h) {
    w = Math.max(1, w);
    h = Math.max(1, h);
    if (capa.baldosa && capa.patronW === w && capa.patronH === h) return capa.baldosa;

    const molde = document.createElement("canvas");
    molde.width = w;
    molde.height = h;
    const mx = molde.getContext("2d", { willReadFrequently: true });
    mx.imageSmoothingQuality = "high";
    mx.drawImage(capa.el, 0, 0, w, h);

    // Se DESCARTA el alfa de la textura, que es lo que hace Python al preparar
    // el mosaico para ffmpeg (Image.convert("RGB") se queda con el RGB crudo y
    // tira el canal alfa). Sin esto el preview mentia fuerte: una textura
    // exportada al 17% de opacidad -- alfa 43 de 255, muy comun en los PNG de
    // grano -- se dibujaba a 0.17 x la opacidad de la capa, o sea unas 6 veces
    // mas debil que en el video exportado. El grano se veia lindo y suave en la
    // app y salia marcado en el archivo final, sin forma de calibrarlo.
    try {
      const px = mx.getImageData(0, 0, w, h);
      const d = px.data;
      let transparente = false;
      for (let i = 3; i < d.length; i += 4) {
        if (d[i] !== 255) { transparente = true; d[i] = 255; }
      }
      if (transparente) mx.putImageData(px, 0, 0);
    } catch (e) {
      // Si el canvas queda "sucio" por seguridad no se puede leer: se sigue con
      // el alfa puesto (el preview queda mas suave, pero nada se rompe).
    }

    // Se guarda la BALDOSA (el lienzo), no el patron ya armado: el patron se
    // crea con el contexto donde se va a usar, que es el de la capa horneada y
    // se rehace cada vez que cambia la caja.
    capa.baldosa = molde;
    capa.patronW = w;
    capa.patronH = h;
    return molde;
  }

  // La capa YA ENCAJADA en la caja del medio, en un lienzo aparte. Se arma una
  // sola vez (mientras no cambien la caja, la escala ni el mosaico) y despues
  // cada cuadro es un blit 1:1, sin clip y sin redimensionar nada.
  //
  // Antes esto pasaba en CADA cuadro: recortar y redimensionar un mosaico de
  // 2560x1440 -- o rellenar con el patron -- con un clip puesto y encima un modo
  // de fusion, sesenta veces por segundo. Era el grueso del trabajo de dibujar()
  // y por eso el video se veia a los tirones. Los pixeles son exactamente los
  // mismos: cambia CUANDO se hace la cuenta, no la cuenta.
  function hornearTextura(capa, caja, lienzoW, lienzoH) {
    const el = capa.el;
    const w = el.naturalWidth;
    const h = el.naturalHeight;
    if (!w || !h) return null;

    const conMosaico = !!(capa.mosaico && capa.mosaico.naturalWidth);
    const clave = `${caja.w}x${caja.h}|${lienzoW}x${lienzoH}|${capa.scale}|` +
                  (conMosaico ? capa.rutaMosaico : `baldosa:${w}x${h}`);
    if (capa.horneada && capa.horneadaClave === clave) return capa.horneada;

    const lienzo = document.createElement("canvas");
    lienzo.width = caja.w;
    lienzo.height = caja.h;
    const cx = lienzo.getContext("2d");

    if (conMosaico) {
      // Camino exacto: el mosaico ya viene tileado al lienzo por Python -- es EL
      // MISMO archivo que come ffmpeg (ver prepared_textures en api.py). Solo
      // hay que encajarlo en la caja como lo hace el filtro: cubrir y recortar
      // al centro. Al no redimensionar la textura por nuestra cuenta, los
      // pixeles salen iguales a los del video exportado.
      const c = recorteQueCubre(capa.mosaico.naturalWidth, capa.mosaico.naturalHeight,
                                caja.w, caja.h);
      cx.drawImage(capa.mosaico, c.sx, c.sy, c.sw, c.sh, 0, 0, caja.w, caja.h);
    } else {
      // RESPALDO, mientras el mosaico de Python no llego todavia (o si fallo):
      // se arma la baldosa aca. Se ve igual de fuerte, pero los pixeles no son
      // exactamente los del export -- ver el camino de arriba.
      //
      // Imagen: mosaico a tamano natural x escala, y despues el conjunto CUBRE
      // la caja -- las dos cosas que hacen _prepare_texture (tilea al lienzo) y
      // el scale/crop del filtro (encaja ese mosaico en la caja). Se replica
      // con un patron: el mosaico virtual mide el lienzo, asi que la baldosa y
      // el desplazamiento se escalan por el mismo factor.
      const factor = Math.max(caja.w / lienzoW, caja.h / lienzoH);
      const baldosaW = Math.max(1, w * capa.scale * factor);
      const baldosaH = Math.max(1, h * capa.scale * factor);
      const desplX = (lienzoW * factor - caja.w) / 2;
      const desplY = (lienzoH * factor - caja.h) / 2;

      const molde = patronDeBaldosa(capa, Math.round(baldosaW), Math.round(baldosaH));
      const patron = molde && cx.createPattern(molde, "repeat");
      if (!patron) return null;
      cx.translate(-desplX, -desplY);
      cx.fillStyle = patron;
      cx.fillRect(desplX, desplY, caja.w, caja.h);
    }

    capa.horneada = lienzo;
    capa.horneadaClave = clave;
    return lienzo;
  }

  function dibujarTextura(cd, capa, caja, lienzoW, lienzoH) {
    // Textura de video: no se puede hornear (el fotograma cambia), asi que va
    // por el camino de siempre -- recortada a la caja, porque el zoom la hace
    // desbordar.
    if (capa.esVideo) {
      const w = capa.el.videoWidth;
      const h = capa.el.videoHeight;
      if (!w || !h) return;
      cd.save();
      cd.beginPath();
      cd.rect(caja.x, caja.y, caja.w, caja.h);
      cd.clip();
      cd.globalAlpha = capa.opacity;
      cd.globalCompositeOperation = capa.blend;
      // La escala es un zoom sobre el fotograma, nunca por debajo del 100%
      // (mas chico dejaria huecos) -- igual que en build_filtergraph.
      const zoom = Math.max(1, capa.scale);
      const c = recorteQueCubre(w, h, caja.w * zoom, caja.h * zoom);
      const dw = caja.w * zoom;
      const dh = caja.h * zoom;
      cd.drawImage(capa.el, c.sx, c.sy, c.sw, c.sh,
                   caja.x - (dw - caja.w) / 2, caja.y - (dh - caja.h) / 2, dw, dh);
      cd.restore();
      return;
    }

    // Imagen: ya viene encajada en la caja (ver hornearTextura), asi que esto es
    // un blit y nada mas. Sin clip: la capa horneada mide EXACTAMENTE la caja,
    // asi que el negro de alrededor sigue sin llevar grano (alla lo logra el
    // orden pad-despues-de-blend) y encima nos ahorramos el clip, que con un
    // modo de fusion puesto obliga al navegador a armar una capa aparte.
    const horneada = hornearTextura(capa, caja, lienzoW, lienzoH);
    if (!horneada) return;
    cd.globalAlpha = capa.opacity;
    cd.globalCompositeOperation = capa.blend;
    cd.drawImage(horneada, caja.x, caja.y);
    cd.globalAlpha = 1;
    cd.globalCompositeOperation = "source-over";
  }

  // La plantilla tambien se hornea: casi siempre entra 1:1, pero si el PNG no
  // mide lo mismo que el lienzo habria que redimensionarlo en cada cuadro.
  function hornearPlantilla(lienzoW, lienzoH) {
    if (!plantilla || !plantilla.el.naturalWidth) return null;
    if (plantillaHorneada && plantillaHorneada.w === lienzoW && plantillaHorneada.h === lienzoH) {
      return plantillaHorneada.lienzo;
    }
    const el = plantilla.el;
    const lienzo = document.createElement("canvas");
    lienzo.width = lienzoW;
    lienzo.height = lienzoH;
    // Cabe COMPLETA en el lienzo y centrada (decrease + pad centrado), igual
    // que el [tpl]scale/pad del filtergraph.
    const escala = Math.min(lienzoW / el.naturalWidth, lienzoH / el.naturalHeight);
    const w = el.naturalWidth * escala;
    const h = el.naturalHeight * escala;
    lienzo.getContext("2d").drawImage(el, (lienzoW - w) / 2, (lienzoH - h) / 2, w, h);
    plantillaHorneada = { lienzo, w: lienzoW, h: lienzoH };
    return lienzo;
  }

  // El negro va SOLO en el borde: el medio ya tapa su caja entera y repintar el
  // lienzo completo antes era rellenar 3.7 millones de pixeles al pedo en cada
  // cuadro. Se pintan las cuatro franjas siempre (no solo cuando cambia la
  // caja): si la caja se achico, eso es lo que borra lo que quedo afuera.
  function pintarBorde(cd, caja, lienzoW, lienzoH) {
    const derecha = caja.x + caja.w;
    const abajo = caja.y + caja.h;
    if (caja.y > 0) cd.fillRect(0, 0, lienzoW, caja.y);
    if (abajo < lienzoH) cd.fillRect(0, abajo, lienzoW, lienzoH - abajo);
    if (caja.x > 0) cd.fillRect(0, caja.y, caja.x, caja.h);
    if (derecha < lienzoW) cd.fillRect(derecha, caja.y, lienzoW - derecha, caja.h);
  }

  // Compone en CUALQUIER lienzo y con CUALQUIER fuente de medio, con el estado
  // (caja, texturas, plantilla) que hay ahora. El previsualizador la llama con
  // los suyos; el panel de portada, con su propio <video> y su propio lienzo,
  // para poder elegir un fotograma sin mover lo que se esta viendo.
  //
  // opciones.plantilla / opciones.texturas en false dejan esa capa afuera -- el
  // panel de portada las usa para mostrar de verdad lo que van a guardar sus
  // interruptores de "Incluir".
  function componerEn(cd, medio, opciones) {
    if (!cd || !estado) return false;
    const conTexturas = !opciones || opciones.texturas !== false;
    const conPlantilla = !opciones || opciones.plantilla !== false;
    const [lienzoW, lienzoH] = estado.canvas_size || [2560, 1440];
    if (cd.canvas.width !== lienzoW || cd.canvas.height !== lienzoH) {
      cd.canvas.width = lienzoW;
      cd.canvas.height = lienzoH;
    }
    cd.globalAlpha = 1;
    cd.globalCompositeOperation = "source-over";
    cd.fillStyle = "#000000";

    const cb = estado.content_box;
    // Redondeada: dibujar en coordenadas con coma le pone antialias a los bordes
    // de la capa horneada, y con un modo de fusion encima eso se nota.
    const caja = cb && {
      x: Math.round(cb[0]), y: Math.round(cb[1]),
      w: Math.round(cb[2]), h: Math.round(cb[3]),
    };
    if (!caja || !dibujarMedio(cd, medio, caja)) {
      // El archivo todavia no decodifico: negro parejo y nada mas.
      cd.fillRect(0, 0, lienzoW, lienzoH);
      return false;
    }
    pintarBorde(cd, caja, lienzoW, lienzoH);
    if (conTexturas) {
      capas.forEach((capa) => dibujarTextura(cd, capa, caja, lienzoW, lienzoH));
      cd.globalAlpha = 1;
      cd.globalCompositeOperation = "source-over";
    }
    if (conPlantilla) {
      const tpl = hornearPlantilla(lienzoW, lienzoH);
      if (tpl) cd.drawImage(tpl, 0, 0);
    }
    return true;
  }

  function dibujar() {
    if (!ctx) return;
    componerEn(ctx, fuente);
  }

  // Un dibujo por cuadro de pantalla como maximo: varios cambios seguidos
  // (arrastrar una perilla dispara decenas) se juntan en uno.
  function pedirDibujo() {
    if (dibujarPendiente) return;
    // Con el video corriendo no hace falta: el bucle ya redibuja en cada
    // fotograma, y agregar un dibujo mas seria componer el lienzo entero dos
    // veces para ver lo mismo.
    if (reproduciendo()) return;
    dibujarPendiente = true;
    const hacer = () => {
      if (!dibujarPendiente) return; // ya lo hizo el otro camino
      dibujarPendiente = false;
      dibujar();
    };
    requestAnimationFrame(hacer);
    // Respaldo por temporizador: con la ventana tapada, minimizada o sin foco,
    // Chromium NO corre requestAnimationFrame. Sin esto el pedido quedaba en la
    // cola, la bandera se quedaba encendida y todos los dibujos siguientes se
    // descartaban en silencio -- el canvas se congelaba con la composicion vieja
    // y solo se recuperaba al volver a mirar la ventana. El que llegue primero
    // dibuja; el otro se encuentra la bandera apagada y no hace nada.
    setTimeout(hacer, 100);
  }

  // ------------------------------------------------------- reproduccion

  function recorte() {
    const inicio = (estado && estado.trim_start) || 0;
    const fin = (estado && estado.trim_end) || (fuente && fuente.duration) || 0;
    return { inicio, fin: fin > inicio ? fin : (fuente && fuente.duration) || 0 };
  }

  // Pisa el recorte AL INSTANTE, sin esperar el viaje de ida y vuelta a
  // Python: set_trim (api.py) no avisa onStateChanged (a proposito, es un
  // slider de arrastre y notificar en cada frame seria carisimo), asi que
  // sin esto el previsualizador solo se enteraba cuando Preview.
  // schedulePreview() volvia a pedir get_state(), 80ms despues de soltar la
  // asa como minimo. bucle() ya relee recorte() en CADA cuadro (nunca lo
  // guarda en cache), asi que mutar el estado que ya tenemos alcanza para
  // que el PROXIMO cuadro salga con el corte nuevo -- pero eso solo cubre
  // el momento en que el video LLEGA al final del recorte y da la vuelta.
  // Si el punto donde esta ahora mismo el video quedo AFUERA del recorte
  // nuevo (por ejemplo, se angosto el recorte por el otro lado, o se corrio
  // lejos de donde esta el cabezal), nada lo hacia saltar hasta la proxima
  // vuelta -- se seguia viendo el fotograma viejo, fuera del recorte, hasta
  // pausar y volver a darle play (play() SI busca inicio si hace falta, ver
  // mas abajo). Se replica esa misma busqueda aca.
  function setTrim(inicio, fin) {
    if (!estado) return;
    estado.trim_start = inicio;
    estado.trim_end = fin;
    if (fuente && esVideo && (fuente.currentTime < inicio || fuente.currentTime >= fin - 0.02)) {
      fuente.currentTime = inicio;
      if (!reproduciendo()) dibujarCuandoLlegue();
    }
    if (!reproduciendo()) pedirDibujo();
  }

  function velocidad() {
    const txt = (estado && estado.speed) || "1x";
    const n = parseFloat(String(txt).replace("x", ""));
    return Number.isFinite(n) && n > 0 ? n : 1;
  }

  // Un dibujo por cuadro REAL del video, no por cuadro de PANTALLA. Un video de
  // 24 o 30 fps en una pantalla de 60 (o 144) Hz se estaba recomponiendo dos,
  // tres o cinco veces por cada fotograma nuevo: el lienzo entero armado de cero
  // -- medio, texturas con su modo de fusion, plantilla -- para terminar
  // mostrando exactamente lo mismo. requestVideoFrameCallback avisa cuando el
  // decodificador presenta un fotograma nuevo, asi que ahora se compone uno por
  // fotograma y ni uno mas.
  function bucle() {
    rafId = null;
    vfcId = null;
    if (!fuente || !esVideo) return;
    const { inicio, fin } = recorte();
    // La vuelta al principio del RECORTE la hace esto y no el loop nativo del
    // <video>, que solo sabe volver al segundo 0 del archivo.
    if (fin > inicio && fuente.currentTime >= fin - 0.02) {
      fuente.currentTime = inicio;
    }
    dibujar();
    dibujarPendiente = false; // quedo al dia: si habia un pedido en cola, sobra
    if (!fuente.paused) programarCuadro();
  }

  function programarCuadro() {
    if (!fuente || !esVideo) return;
    if (fuente.requestVideoFrameCallback) {
      if (vfcId === null) vfcId = fuente.requestVideoFrameCallback(bucle);
    } else if (rafId === null) {
      // Navegador sin requestVideoFrameCallback: se vuelve al cuadro de
      // pantalla, que es lo que habia antes.
      rafId = requestAnimationFrame(bucle);
    }
  }

  function pararCuadros() {
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    if (vfcId !== null && fuente && fuente.cancelVideoFrameCallback) {
      fuente.cancelVideoFrameCallback(vfcId);
    }
    vfcId = null;
  }

  function play() {
    if (!fuente || !esVideo) return Promise.resolve();
    const { inicio, fin } = recorte();
    if (fuente.currentTime < inicio || fuente.currentTime >= fin - 0.02) {
      fuente.currentTime = inicio;
    }
    fuente.playbackRate = velocidad();
    capas.forEach((c) => { if (c.esVideo) c.el.play().catch(() => {}); });
    const p = fuente.play();
    programarCuadro();
    return p || Promise.resolve();
  }

  function pause() {
    if (fuente && esVideo) fuente.pause();
    capas.forEach((c) => { if (c.esVideo) c.el.pause(); });
    pararCuadros();
    pedirDibujo();
  }

  function reproduciendo() {
    return !!(fuente && esVideo && !fuente.paused);
  }

  // Posicion 0..1 DENTRO del recorte (lo que espera la barra de la UI).
  function progreso() {
    if (!fuente || !esVideo) return 0;
    const { inicio, fin } = recorte();
    const largo = fin - inicio;
    return largo > 0 ? Math.max(0, Math.min(1, (fuente.currentTime - inicio) / largo)) : 0;
  }

  function buscar(frac) {
    if (!fuente || !esVideo) return;
    const { inicio, fin } = recorte();
    fuente.currentTime = inicio + Math.max(0, Math.min(1, frac)) * (fin - inicio);
    pedirDibujo();
    dibujarCuandoLlegue();
  }

  // Redibuja cuando el fotograma NUEVO este listo, no cuando se pidio. Buscando
  // un momento con el video en pausa, pedirDibujo() solo no alcanza: dibuja en
  // el cuadro siguiente (16 ms) y el decodificador tarda bastante mas, asi que
  // pintaba de nuevo el fotograma viejo y ahi se quedaba -- arrastrar la barrita
  // dejaba la imagen atrasada. requestVideoFrameCallback avisa justo cuando el
  // decodificador presenta el fotograma, tambien en pausa. (El evento "seeked"
  // de arriba queda de respaldo: llega casi siempre, pero Chromium se lo saltea
  // si le cae otra busqueda encima, que es lo que pasa arrastrando la barra.)
  function dibujarCuandoLlegue() {
    if (!fuente || !esVideo || reproduciendo()) return; // andando ya redibuja el bucle
    if (!fuente.requestVideoFrameCallback) return;
    const el = fuente;
    el.requestVideoFrameCallback(() => {
      if (el === fuente && !reproduciendo()) dibujar();
    });
  }

  // Buscar en SEGUNDOS del archivo, sin mirar el recorte: lo usa el panel de
  // portada, que recorre el video ENTERO y no solo el pedazo que hace loop.
  function buscarSegundos(t) {
    if (!fuente || !esVideo) return;
    const largo = fuente.duration || 0;
    fuente.currentTime = Math.max(0, largo ? Math.min(t, largo - 0.05) : t);
    pedirDibujo();
    dibujarCuandoLlegue();
  }

  function tiempoActual() {
    return fuente && esVideo ? fuente.currentTime : 0;
  }

  function duracion() {
    return fuente && esVideo ? fuente.duration || 0 : 0;
  }

  // ---------------------------------------------------------------- API

  // Ambilight: le pasa el cuadro que se esta viendo al fondo, seguido, para que
  // el color acompane al video en vivo (ver ambilightFromSource en
  // background-tint.js, que ademas suaviza el cambio). Va por temporizador y no
  // colgado de dibujar(): con el video en pausa no hay cuadros nuevos, pero la
  // transicion de color TIENE que seguir avanzando hasta llegar.
  //
  // Muestrea el MEDIO (el <video>/<img> original), no el lienzo compuesto. El
  // lienzo se dibuja en la placa de video, asi que leerle los pixeles obliga a
  // traerlo de vuelta a la memoria de la CPU -- 2560x1440 enteros -- y a esperar
  // a que la placa termine todo lo que tenia encolado. Ocho veces por segundo
  // eso solo era un tironeo constante mientras el video corria. Del medio se
  // saca el mismo matiz (es el que pone el color; la plantilla y el grano casi
  // no lo mueven) y leerlo es el camino barato de siempre.
  const AMBILIGHT_MS = 120;

  // Factor de mezcla para VIDEO (ver acercarMatiz/ambilightFromSource en
  // background-tint.js, que por defecto usa 0.12 -- lo que sigue usando una
  // FOTO, sin tocar). Pedido explicito: un corte de escena (verde a morado,
  // por ejemplo) se sentia como un salto brusco de color en vez de una
  // transicion. Con este factor, bastante mas chico, cada muestreo se acerca
  // mucho menos al matiz nuevo, asi que la mezcla tarda varios segundos en
  // llegar en vez de sentirse instantanea -- una foto no tiene cortes de
  // escena (es un solo cuadro fijo), asi que ahi no hay nada que suavizar de
  // mas.
  const AMBILIGHT_FACTOR_VIDEO = 0.035;

  // Mismos codecs que CHEAP_DECODE_CODECS en engine.py. El comentario de
  // arriba ("leerlo es el camino barato de siempre") vale para el TAMANO del
  // sample, pero no cubre esto: el drawImage(video,...) previo al
  // getImageData tiene que sacar el cuadro de la memoria del decodificador,
  // y contra un decodificador de SOFTWARE ya al limite (VP9/AV1 sin
  // aceleracion, ver _download_video en api.py) esa sincronizacion le hace
  // competencia real -- medido, un clip VP9 que solo rendia 24fps limpios
  // caia a ~16fps con el ambilight muestreando cada 120ms encima.
  //
  // Antes, mientras no hubiera copia liviana lista (preview_path todavia era
  // el original), esto apagaba el ambilight DEL TODO -- probado con un clip
  // VP9 real: el fondo quedaba clavado en el primer cuadro (a veces varios
  // minutos, lo que tarde la copia en armarse), aunque el video ya estuviera
  // mostrando otra escena de otro color por completo. Ahora en vez de
  // apagarlo sigue muestreando, solo que mucho mas espaciado (1 de cada 5
  // veces) -- 5x menos sincronizaciones con el decodificador de por medio,
  // que es lo que compite, asi que el costo medido de arriba baja a algo
  // bastante mas chico que 24->16fps y el fondo deja de quedarse pegado.
  const AMBILIGHT_MS_CODEC_CARO = AMBILIGHT_MS * 5;
  let ambilightTimer = null;

  const AMBILIGHT_CHEAP_CODECS = new Set(["h264", "mpeg4", "mjpeg"]);

  // true = ritmo normal (120ms); false = codec caro sin copia lista todavia,
  // ritmo espaciado (ver AMBILIGHT_MS_CODEC_CARO).
  function ambilightAlRitmoNormal() {
    if (!estado || !esVideo) return true; // imagen, o sin dato: nada que competir
    const codec = estado.media_video_codec;
    if (!codec || AMBILIGHT_CHEAP_CODECS.has(codec)) return true;
    return !!(estado.preview_path && estado.preview_path !== estado.media_path);
  }

  // setTimeout que se reprograma solo (no setInterval) para poder elegir la
  // demora del PROXIMO muestreo cada vez, segun si para ese momento ya hay
  // copia liviana o no -- con un intervalo fijo no hay forma de acelerar en
  // cuanto la copia este lista sin esperar a que expire el timer viejo.
  function programarAmbilight() {
    const demora = ambilightAlRitmoNormal() ? AMBILIGHT_MS : AMBILIGHT_MS_CODEC_CARO;
    ambilightTimer = setTimeout(() => {
      if (canvas && !canvas.hidden && medidasFuente()) {
        if (window.ambilightFromSource) {
          window.ambilightFromSource(fuente, esVideo ? AMBILIGHT_FACTOR_VIDEO : undefined);
        }
        if (window.setSpotifyBackgroundImage) window.setSpotifyBackgroundImage(fuente, esVideo);
        // kawarp se auto-limita por dentro (ver MIN_MS_ENTRE_CUADROS): recibe
        // este mismo muestreo de 8 por segundo pero solo sube un cuadro nuevo
        // a la GPU cada ~900ms.
        if (window.setKawarpBackgroundImage) window.setKawarpBackgroundImage(fuente);
      }
      programarAmbilight();
    }, demora);
  }

  function arrancarAmbilight() {
    if (ambilightTimer !== null) return;
    programarAmbilight();
  }

  function init() {
    canvas = document.getElementById("live-preview");
    if (!canvas) return;
    // alpha:false -- el lienzo es OPACO (siempre se pinta el negro debajo). Sin
    // esto el navegador le guarda canal alfa y lo mezcla con lo que hay detras
    // en cada cuadro, para nada.
    ctx = canvas.getContext("2d", { alpha: false });
    window.addEventListener("resize", pedirDibujo);
    arrancarAmbilight();
  }

  // Se llama con cada get_state: decide que hay que (re)cargar y redibuja.
  function applyState(nuevo) {
    estado = nuevo || null;
    if (!estado || !estado.media_path) {
      cargarFuente(null, false, null);
      cargarCapas([]);
      cargarPlantilla(null);
      if (canvas) canvas.hidden = true;
      return;
    }
    cargarFuente(estado.preview_path || estado.media_path,
                 !!estado.media_is_video, estado.media_path);
    cargarCapas(estado.texture_layers);
    cargarPlantilla(estado.template_path);
    if (canvas) canvas.hidden = false;
    if (fuente && esVideo) fuente.playbackRate = velocidad();
    pedirDibujo();
  }

  // Data URI del cuadro que se ve -- para lo que antes leia el <img> del
  // fotograma (el tinte del fondo, la ventana aparte, la portada de una foto).
  function dataUri() {
    if (!canvas || canvas.hidden) return null;
    try {
      return canvas.toDataURL("image/png");
    } catch (e) {
      return null;
    }
  }

  function elemento() {
    return canvas;
  }

  // Para el panel de portada: el archivo que se esta usando para los fotogramas
  // (la copia liviana si existe), para que pueda cargar el suyo aparte.
  function urlDeLaFuente() {
    return rutaFuente ? urlDeArchivo(rutaFuente) : null;
  }

  function componerEnLienzo(lienzoDestino, medio, opciones) {
    if (!lienzoDestino) return false;
    return componerEn(lienzoDestino.getContext("2d"), medio || fuente, opciones);
  }

  function listo() {
    return !!medidasFuente();
  }

  return {
    init, applyState, setPreparedTextures, play, pause, redraw: pedirDibujo,
    isPlaying: reproduciendo, progress: progreso, seek: buscar,
    seekSeconds: buscarSegundos, currentTime: tiempoActual, duration: duracion,
    dataUri, element: elemento, ready: listo, setTrim,
    sourceUrl: urlDeLaFuente, composeInto: componerEnLienzo,
  };
})();
