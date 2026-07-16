# Generador de Video (Electron)

Reescritura del shell de UI en Electron (fondo Mica/Acrylic nativo de Windows 11).
La logica de ffmpeg (`python/engine.py`) y la mayor parte de `python/api.py` son
las mismas que en la version pywebview (`..\Programa de video`), sin tocar.

## Primera vez (instalar dependencias)

```powershell
# 1. Dependencias de Electron
npm install

# 2. Entorno de Python para el sidecar
cd python
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
cd ..
```

## Correr en desarrollo

```powershell
npm run dev
```

Esto abre la ventana principal (Mica) y arranca automaticamente el proceso
Python (`python/sidecar.py`) que Electron controla por stdio.

## Estructura

```
src/main/        proceso principal de Electron (ventana, IPC, sidecar, dialogos)
src/renderer/    UI (copiada de webapp/ui/ del proyecto pywebview) + shims de compatibilidad
python/          engine.py (sin tocar) + api.py (adaptado) + sidecar.py (nuevo)
```

## Que falta para paridad completa con la version pywebview

- **Empaquetado final** (PyInstaller para el sidecar + electron-builder para la
  app) -- hoy solo corre en modo desarrollo (`npm run dev`), con el venv de
  Python al lado del proyecto. Instalar en una maquina sin Python requeriria
  empaquetar el sidecar como ejecutable standalone.
- **Verificacion visual manual** de los flujos que abren dialogos nativos
  (Contenido del video, Plantilla, Textura, "Guardar como") y del
  drag&drop real arrastrando archivos desde el Explorador -- la logica que
  disparan (`ingest_paths`, `set_template`, `add_texture_layer`,
  `set_chosen_output`) ya se probo end-to-end por RPC directo, pero el click
  humano sobre el dialogo de Windows y el arrastre real desde el Explorador
  no se pudieron automatizar de forma segura en este entorno.
- **Ventana de previsualizacion aparte** ("Agrandar"): la logica esta
  implementada (`previewWindow.js` + shim de `show_preview_window`) pero, por
  la misma razon de arriba, falta un clic real de confirmacion visual.
- **Portapapeles (Ctrl+V) y descarga por link**: el codigo Python
  (`paste_from_clipboard`, `download_from_link`) es identico al de la version
  pywebview y ya funcionaba alli; no se volvio a probar aca en vivo para no
  tocar el portapapeles real del usuario ni disparar una descarga de red
  durante las pruebas.

## Lo que si se verifico de punta a punta (RPC real contra el sidecar)

- Ingesta de imagen/video, sondeo de video (duracion/tamano/entrelazado) en
  segundo plano.
- Generacion completa imagen+audio -> mp4 y video+audio -> mp4 (loop de 2
  fases: compose + concat/mux), con el resultado real inspeccionado con
  ffprobe.
- Previsualizador en vivo (`request_preview` -> frame real compuesto con
  plantilla/texturas -> evento `onPreviewReady`).
- Plantilla (`set_template`, deteccion de ventana transparente),
  texturas (`add_texture_layer`/`register_texture_path`), presets
  (guardar/aplicar/borrar), tema claro/oscuro, salida elegida a mano
  (`set_chosen_output`).
- La ventana Electron con `backgroundMaterial: 'mica'` abre y compone con el
  escritorio real (confirmado por captura de pantalla de la ventana).
