"""Arma build/icon.ico con TODOS los tamanos que usa Windows.

La fuente es build/icon-source.png (el logo que paso Erick). Hace falta este
paso porque el .ico original traia un solo tamano, el de 256: Windows lo
reescala solo para la barra de titulo (16) y la barra de tareas (32), y ese
reescalado al vuelo se ve sucio. Guardando cada tamano dentro del .ico,
Windows elige el que ya esta listo en vez de improvisar.

Si no existe icon-source.png, cae al logo blanco del renderer y le pone el
gris oscuro de la app de fondo (sobre el fondo claro del explorador un logo
blanco sobre transparente seria invisible).
"""

import os
import sys

from PIL import Image, ImageDraw

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
FUENTE = os.path.join(AQUI, "icon-source.png")
LOGO_RESPALDO = os.path.join(RAIZ, "src", "renderer", "img", "app-logo-h.png")
SALIDA = os.path.join(AQUI, "icon.ico")

# Windows pide cada uno de estos segun donde muestre el icono: 16 en la barra
# de titulo, 32 en la barra de tareas, 48 en el explorador, 256 en vista de
# iconos extra grandes y en el instalador.
TAMANOS = [16, 24, 32, 48, 64, 128, 256]

FONDO = (27, 26, 26, 255)   # #1b1a1a, el gris de .glass-window
LADO = 1024
MARGEN = 0.16
RADIO = 0.185


def desde_logo_blanco():
    """Respaldo: el logo blanco del renderer sobre un cuadrado oscuro."""
    logo = Image.open(LOGO_RESPALDO).convert("RGBA")
    caja = logo.getchannel("A").getbbox()
    if caja:
        logo = logo.crop(caja)

    lienzo = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
    mascara = Image.new("L", (LADO, LADO), 0)
    ImageDraw.Draw(mascara).rounded_rectangle(
        [0, 0, LADO - 1, LADO - 1], radius=int(LADO * RADIO), fill=255)
    lienzo.paste(Image.new("RGBA", (LADO, LADO), FONDO), (0, 0), mascara)

    disponible = int(LADO * (1 - 2 * MARGEN))
    escala = min(disponible / logo.width, disponible / logo.height)
    nuevo = (max(1, int(logo.width * escala)), max(1, int(logo.height * escala)))
    logo = logo.resize(nuevo, Image.LANCZOS)
    lienzo.alpha_composite(logo, ((LADO - nuevo[0]) // 2, (LADO - nuevo[1]) // 2))
    return lienzo


def construir():
    if os.path.exists(FUENTE):
        base = Image.open(FUENTE).convert("RGBA")
        origen = "icon-source.png"
        # Cuadrado, por si la fuente no lo es: se centra sin deformar.
        if base.width != base.height:
            lado = max(base.size)
            cuadrado = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
            cuadrado.alpha_composite(
                base, ((lado - base.width) // 2, (lado - base.height) // 2))
            base = cuadrado
    else:
        base = desde_logo_blanco()
        origen = "app-logo-h.png (respaldo)"

    # Cada tamano se baja por separado desde el original grande, en vez de ir
    # encadenando reducciones: encadenar acumula el borroneo de cada paso.
    capas = [base.resize((s, s), Image.LANCZOS) for s in TAMANOS]
    capas[-1].save(SALIDA, format="ICO",
                   sizes=[(s, s) for s in TAMANOS], append_images=capas[:-1])

    base.resize((512, 512), Image.LANCZOS).save(
        os.path.join(AQUI, "icon.png"), format="PNG")
    print(f"[icon] {SALIDA}")
    print(f"[icon] fuente: {origen}  |  tamanos: {', '.join(map(str, TAMANOS))} px")


if __name__ == "__main__":
    if not os.path.exists(FUENTE) and not os.path.exists(LOGO_RESPALDO):
        sys.exit("[icon] no hay ni build/icon-source.png ni el logo del renderer")
    construir()
