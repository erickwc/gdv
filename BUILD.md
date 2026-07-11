# Compilar en Mac

Instrucciones para descargar el repo y compilar el Generador de Video como
app de macOS. Pensado para que una sesión de Claude Code nueva (sin
contexto de conversaciones anteriores) pueda seguirlas solo con esto.

## 1. Clonar el repo

```bash
git clone https://github.com/erickwc/gdv.git
cd gdv
```

## 2. Entorno y dependencias

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
```

`requirements.txt` instala `tkinterdnd2`, `Pillow`, `imageio-ffmpeg`,
`customtkinter`, `yt-dlp` y `certifi` (en Mac, el Python de python.org no
trae certificados SSL configurados y sin certifi la descarga por link
falla con CERTIFICATE_VERIFY_FAILED). `imageio-ffmpeg` descarga su propio
binario de ffmpeg la primera vez que se usa
(`imageio_ffmpeg.get_ffmpeg_exe()`) — hay que empaquetar ese binario
junto con la app (ver paso 4).

## 3. Probar antes de compilar

```bash
python app.py
```

Confirma que la ventana abre y que arrastrar una imagen/video + audio
genera un MP4 sin errores antes de empaquetar.

## 4. Compilar con PyInstaller

```bash
FFMPEG_BIN="$(python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"

pyinstaller --windowed --onedir --name "GeneradorDeVideo" \
  --add-data "fonts:fonts" \
  --add-binary "${FFMPEG_BIN}:." \
  --add-data "venv/lib/python3.14/site-packages/tkinterdnd2/tkdnd/osx-arm64-tcl9:tkinterdnd2/tkdnd/osx-arm64-tcl9" \
  --noconfirm \
  app.py
```

El resultado queda en `dist/GeneradorDeVideo.app`.

**El `--add-data` de `tkdnd/osx-arm64-tcl9` es OBLIGATORIO** con el
Python 3.14 de python.org (que trae Tcl/Tk 9): el hook de PyInstaller
para tkinterdnd2 solo empaqueta la variante de Tcl 8.6 y sin esta línea
la app compilada crashea al abrir con "this extension is compiled for
Tcl 8.6". Ajustes según el entorno:

- Cambia `python3.14` en la ruta si usas otra versión de Python.
- En Macs Intel la carpeta es `osx-x64-tcl9` en lugar de `osx-arm64-tcl9`.
- Si `python app.py` (paso 3) funciona pero quieres confirmar qué carpeta
  tienes: `ls venv/lib/python*/site-packages/tkinterdnd2/tkdnd/`.

Notas:
- `plantillas/`, `texturas/` y `config.json` NO se empaquetan dentro del
  `.app` — el código los busca junto al ejecutable (`APP_DIR`, ver
  `app.py` líneas ~44-56) para que el usuario pueda editarlos sin
  reabrir el paquete. Cópialos junto a `GeneradorDeVideo.app` al
  distribuir (o dentro de la misma carpeta `dist/`).
- Si macOS bloquea la app por no estar firmada/notarizada, hay que
  firmarla (`codesign`) o el usuario debe permitirla manualmente en
  Ajustes del Sistema → Privacidad y Seguridad.

## 5. Verificar

Ejecuta `dist/GeneradorDeVideo.app`, repite la prueba del paso 3
(imagen/video + audio → MP4) y confirma que no falta ningún recurso
(fuentes, ffmpeg). Prueba también lo específico de Mac: scroll con dos
dedos en el trackpad, Cmd+V para pegar imágenes/audios/archivos copiados,
cambio de tema claro/oscuro y descarga por link.

## Nota sobre compatibilidad Mac/Windows

`app.py` es el mismo código para los dos sistemas. Todo lo específico de
Mac está condicionado con `IS_MAC` (y lo de Windows con `os.name == "nt"`),
así que los cambios hechos en un sistema no alteran el comportamiento en
el otro: fuentes privadas GDI y codificador `aac_mf` solo en Windows;
certificados de certifi, Cmd+V, portapapeles nativo (NSPasteboard) y
handlers de scroll para Tcl/Tk 9 solo en Mac.
