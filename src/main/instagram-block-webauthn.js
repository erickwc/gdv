// Preload de la ventana oculta que renderiza Instagram (ver
// resolveInstagramPhotos en main.js) -- NO es el preload de la app (ese es
// preload.js, para la ventana principal). Corre antes que el JS de la
// pagina, con contextIsolation:true (comparte el DOM/navigator con la
// pagina, pero con su propio heap de variables -- por eso reescribir
// navigator.credentials aca SI lo tapa para el JS de Instagram tambien).
//
// Instagram (o el login de Meta que carga por debajo) llama a
// navigator.credentials.get() apenas la pagina abre, ofreciendo "iniciar
// sesion con una llave de acceso" -- eso dispara un dialogo NATIVO de
// Windows (Windows Hello / llave de seguridad), fuera de la ventana, aunque
// esta este oculta (show:false no lo frena: es un prompt del sistema
// operativo, no de la pagina). Sin sesion iniciada esta ventana no
// necesita ni puede usar ninguna credencial -- se tapa la API entera para
// que nunca llegue a pedirle nada al usuario.
if (window.navigator && window.navigator.credentials) {
  const bloqueado = () => Promise.reject(new DOMException("Bloqueado", "NotAllowedError"));
  try {
    Object.defineProperty(window.navigator.credentials, "get", { value: bloqueado, configurable: true });
    Object.defineProperty(window.navigator.credentials, "create", { value: bloqueado, configurable: true });
  } catch (e) {
    // Si el navegador no deja redefinir estas propiedades, no hay mucho mas
    // que hacer aca -- no vale la pena que esto tumbe toda la resolucion.
  }
}
