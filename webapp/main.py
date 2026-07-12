"""
Punto de entrada de la nueva UI (pywebview). La Api (webapp/api.py) queda
expuesta a JS como `pywebview.api.*`.
"""

import os

import webview

from api import Api

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


def main():
    api = Api()
    window = webview.create_window(
        "Generador de Video",
        url=os.path.join(UI_DIR, "index.html"),
        js_api=api,
        # Ancho fijo, a juego con el max-width del contenido (.shell en
        # styles.css) -- el alto se ajusta solo al contenido real
        # (ver Api.resize_window / fitWindowToContent en app.js), asi la
        # ventana se siente como un dialogo que se acomoda a lo de adentro
        # en vez de una ventana de tamano arbitrario con scroll interno.
        width=740,
        height=700,
        min_size=(560, 420),
    )
    api.set_window(window)
    webview.start()


if __name__ == "__main__":
    main()
