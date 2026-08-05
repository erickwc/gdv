// Corre DENTRO de la ventana oculta que carga el post de Instagram (ver
// resolveInstagramPhotos en main.js, que lee este archivo como texto y se
// lo pasa tal cual a webContents.executeJavaScript). Va en su PROPIO
// archivo -- no como un template literal embebido en main.js -- porque un
// regex con \d adentro de un template literal se rompe en silencio: Node
// parsea ese string ANTES de mandarlo a executeJavaScript, y \d no es un
// escape valido en un string/template literal, asi que se come la barra y
// deja "d" suelta. Aca es un .js de verdad, leido como texto: el regex
// nunca pasa por ese segundo parseo.
//
// VERSION 2 -- ya no clickea el carrusel (ver historial de git para la
// primera version, que hacia eso): Instagram manda TODAS las fotos del
// carrusel, en varias resoluciones cada una, en un <script
// type="application/json"> que ya viene con la carga inicial de la pagina
// -- ni falta abrir/avanzar nada. Confirmado contra un post real de 20
// fotos: estan todas ahi desde el primer momento, incluidas las que el
// carrusel todavia ni monto en pantalla. Antes tardaba ~20-25s clickeando
// "Siguiente" one by one; esto tarda lo mismo que cargar la pagina.
(async () => {
  // Cada <script type=application/json> de Instagram es un bloque de
  // hidratacion; ninguno en particular es "el" bueno de forma confiable
  // (el orden/indice cambia), asi que se busca en TODOS juntos: tanto las
  // URLs de las fotos como las senales de "hay sesion" y "el post es un
  // video" salen de aca.
  const textoDeTodos = () =>
    [...document.querySelectorAll('script[type="application/json"]')]
      .map((s) => s.textContent)
      .join("\n");

  // Posts con restriccion de edad, privados, o de una cuenta que bloqueo al
  // visitante -- Instagram le muestra un muro de VERDAD ("contenido con
  // restriccion de edad... inicia sesion para continuar") a cualquiera sin
  // cuenta (probado con un post real). Ahi no hay carrusel que leer, ni
  // ahora ni con mas espera: se corta apenas se detecta.
  //
  // OJO con el texto exacto: la primera version buscaba solo "inicia
  // sesion" en toda la pagina, y eso rompio un post que SI funcionaba --
  // Instagram le muestra "Inicia sesion para indicar que te gusta o
  // comentar" a CUALQUIER visitante sin cuenta, en CUALQUIER post, tenga o
  // no restriccion. Ese texto no tiene nada que ver con el post estando
  // bloqueado, y el regex viejo lo agarraba igual (probado: confirmado con
  // el DEBUG_contexto de ese falso positivo). La frase de aca abajo
  // ("contenido... restring...") es la del MURO de verdad, especifica de
  // ese cartel y no de ningun otro lugar de la pagina -- no hace falta el
  // chequeo de <article> de antes (tampoco era confiable: a los 1.5s
  // todavia no habia montado en NINGUNO de los dos casos, el bloqueado y el
  // que si funciona).
  await new Promise((r) => setTimeout(r, 1500)); // que el muro (si lo hay) alcance a pintarse
  // El muro tambien sale en ingles ("Restricted content ... This content is
  // restricted based on your age or account settings") -- con sesion iniciada
  // el idioma lo manda la cuenta, no la app, asi que no alcanza con el texto
  // en espanol. Las dos frases de aca son del cartel del MURO; ninguna
  // aparece en el "Inicia sesion para indicar que te gusta" que Instagram le
  // muestra a CUALQUIER visitante sin sesion en CUALQUIER post (ese fue el
  // falso positivo de la primera version, ver el comentario de arriba).
  if (
    /contenido[^.]{0,20}restring|restricted\s+content|content\s+is\s+restricted/i.test(
      document.body.innerText || ""
    )
  ) {
    // Medido contra el post +18 que reporto el usuario: sin sesion NO hay
    // forma de leerlo, por ningun camino. En la pagina no viene ni una URL de
    // foto del post (solo fotos de perfil); /embed/captioned/ --que sin sesion
    // SI funciona en un post normal-- contesta "el enlace esta danado o se
    // elimino la publicacion"; y api/v1/media/<id>/info/, i.instagram.com,
    // api.instagram.com/oembed y la consulta doc_id anonima de instaloader
    // 4.15.3 (la misma que usa su Post._obtain_metadata, probada tal cual:
    // anda en un post normal y devuelve "execution error" en este) contestan
    // login_required o mandan al login. Las paginas externas que si lo bajan
    // no son anonimas: el logueado ahi es su servidor. Se decidio no pedirle
    // al usuario que inicie sesion, asi que este caso se corta aca y el
    // renderer avisa (ver startInstagramDownload en app.js).
    return { ok: false, needsLogin: true };
  }

  // Un <video> en la pagina NO alcanza para decir "este post es un video":
  // en un CARRUSEL MIXTO (fotos + video) tambien hay uno. La version anterior
  // cortaba aca y devolvia {ok:false} apenas veia el primero, asi que un
  // carrusel de 8 fotos + 1 video terminaba sin dar NI UNA foto (medido
  // adentro de la ventana de la app: 8 fotos limpias en el JSON, tiradas a la
  // basura, y el usuario recibiendo el recorte cuadrado del camino de yt-dlp).
  //
  // Lo que si distingue los dos casos es el TIPO de los medios que trae el
  // payload, medido en la ventana de la app contra los dos posts:
  //   - post de video suelto: ni un "...ImageMedia", y extraerFotos deja UNA
  //     sola foto limpia (la portada del video).
  //   - carrusel mixto: aparece "XIGPolarisImageMedia" y quedan 8.
  // Se piden las dos senales por separado (el typename Y el "hay 2 o mas
  // fotos") para no depender de un solo string interno de Instagram: si
  // mañana le cambian el nombre al typename, el conteo sigue rescatando los
  // carruseles. Y ojo: esto solo se evalua cuando HAY un <video> en la
  // pagina -- un post de fotos sin video no pasa nunca por aca.
  const hayVideoEnPagina = () => !!document.querySelector("video[src], video > source");
  const hayImagenDeVerdad = () => /"__typename"\s*:\s*"[^"]*ImageMedia"/.test(textoDeTodos());
  const esPostDeVideo = (fotos) =>
    hayVideoEnPagina() && !hayImagenDeVerdad() && fotos.length < 2;

  // Las URLs vienen con las barras escapadas (\/) adentro del JSON como
  // texto -- se decodifican despues de matchear, no antes (el regex tiene
  // que reconocer el patron TAL CUAL esta en el texto crudo).
  const URL_FOTO = /https:\\?\/\\?\/[a-z0-9.-]+\\?\/v\\?\/t51\.82787-15\\?\/[^"]*?\.(?:jpg|webp)\?[^"]*?"/g;
  // Cualquier variante con un tamano forzado en el nombre (_s640x640_,
  // _p720x720_, etc.) es una miniatura recortada -- mismo "stp" cuadrado
  // que rompe og:image (ver el comentario grande en resolveInstagramPhotos).
  // Las fotos DE VERDAD del post traen ademas una variante SIN ese sufijo.
  const esRecorte = (u) => /_[sp]\d+x\d+/.test(u);

  function extraerFotos() {
    const urls = [...textoDeTodos().matchAll(URL_FOTO)].map((m) =>
      m[0].replace(/\\\//g, "/").replace(/"$/, "")
    );
    const porId = new Map();
    for (const u of urls) {
      const id = (u.match(/\/(\d+)_\d+_\d+_n\./) || [])[1];
      if (!id) continue;
      if (!porId.has(id)) porId.set(id, []);
      porId.get(id).push(u);
    }
    const fotos = [];
    for (const variantes of porId.values()) {
      // Las de "sugeridos" (barra lateral, perfil) solo tienen la variante
      // recortada -- sin la limpia, no es una foto del post, se descarta.
      const limpia = variantes.find((u) => !esRecorte(u));
      if (limpia) fotos.push(limpia);
    }
    return fotos;
  }

  // Instagram es una SPA pesada: el HTML inicial no siempre trae el bloque
  // de hidratacion completo de una -- se sondea hasta 8s, igual que antes.
  const limite = Date.now() + 8000;
  while (Date.now() < limite) {
    const fotos = extraerFotos();
    if (fotos.length) {
      // Un reel/post de video sigue cayendo al camino de siempre (yt-dlp, que
      // baja el video de verdad) -- ver esPostDeVideo arriba.
      if (esPostDeVideo(fotos)) return { ok: false };
      return { ok: true, photos: fotos.map((src) => ({ src })) };
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  return { ok: false };
})()
