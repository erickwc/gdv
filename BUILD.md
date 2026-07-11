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
`customtkinter` y `yt-dlp`. `imageio-ffmpeg` descarga su propio binario de
ffmpeg la primera vez que se usa (`imageio_ffmpeg.get_ffmpeg_exe()`) — hay
que empaquetar ese binario junto con la app (ver paso 4).

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
  app.py
```

El resultado queda en `dist/GeneradorDeVideo/GeneradorDeVideo.app`.

Notas:
- `plantillas/`, `texturas/` y `config.json` NO se empaquetan dentro del
  `.app` — el código los busca junto al ejecutable (`APP_DIR`, ver
  `app.py` líneas ~44-56) para que el usuario pueda editarlos sin
  reabrir el paquete. Cópialos junto a `GeneradorDeVideo.app` al
  distribuir (o dentro de la misma carpeta `dist/GeneradorDeVideo/`).
- Si macOS bloquea la app por no estar firmada/notarizada, hay que
  firmarla (`codesign`) o el usuario debe permitirla manualmente en
  Ajustes del Sistema → Privacidad y Seguridad.

## 5. Verificar

Ejecuta `dist/GeneradorDeVideo/GeneradorDeVideo.app`, repite la prueba del
paso 3 (imagen/video + audio → MP4) y confirma que no falta ningún
recurso (fuentes, ffmpeg).
