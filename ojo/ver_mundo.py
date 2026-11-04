# -*- coding: utf-8 -*-
"""Ver cualquier mundo de Minecraft Java 1.21 en 3D, sin abrir el juego (solo lee los archivos).

Uso:
    python ver_mundo.py --mundo "C:/ruta/a/saves/MiMundo" --x 0 --z 0 --radio 48 --salida vista.png
    python ver_mundo.py --mundo ... --x 100 --z -40 --proyeccion perspectiva --giro 135 --inclinacion 30
    python ver_mundo.py --mundo ... --x 100 --z -40 --camara 100 70 -60 --giro 0 --inclinacion 10   (vista de jugador)
    python ver_mundo.py --mundo ... --x 0 --z 0 --vuelta        (la zona desde las 4 esquinas y desde arriba)

Necesita el .jar de Minecraft 1.21.1 (texturas y modelos). Lo busca solo en la carpeta .minecraft de Windows, Mac
o Linux (versions/1.21.1/1.21.1.jar); otra ruta con la variable OJO_JAR. Mods (sus texturas): OJO_MODS.

Use any Minecraft Java 1.21 world: read-only, the game does not need to be open."""
import argparse
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import ojo_del_mundo as O  # noqa: E402
import ojo_vista3d as V  # noqa: E402


def main():
    a = argparse.ArgumentParser(description="Vista 3D de un mundo de Minecraft desde sus archivos / 3D view of a Minecraft world from its files")
    a.add_argument("--mundo", required=True, help="carpeta del mundo (la que tiene level.dat) / world folder")
    a.add_argument("--dimension", default="overworld", choices=["overworld", "the_nether", "the_end"])
    a.add_argument("--x", type=int, required=True)
    a.add_argument("--z", type=int, required=True)
    a.add_argument("--radio", type=int, default=48, help="bloques alrededor del centro (hasta 128) / radius in blocks")
    a.add_argument("--proyeccion", default="orto", choices=["orto", "perspectiva"])
    a.add_argument("--giro", type=float, default=None, help="hacia dónde mira, como el yaw del juego (0 = sur, 90 = oeste)")
    a.add_argument("--inclinacion", type=float, default=35.0, help="grados hacia abajo / pitch")
    a.add_argument("--camara", type=float, nargs=3, default=None, metavar=("X", "Y", "Z"), help="cámara en ese punto (vista de jugador)")
    a.add_argument("--fov", type=float, default=70.0)
    a.add_argument("--noche", action="store_true")
    a.add_argument("--seres", action="store_true", help="dibujar mobs y aldeanos / draw mobs")
    a.add_argument("--muebles", action="store_true", help="teñir y numerar los muebles / highlight furniture")
    a.add_argument("--sin_cuadricula", action="store_true")
    a.add_argument("--vuelta", action="store_true", help="4 esquinas y arriba en una imagen / 4 corners + top")
    a.add_argument("--ancho", type=int, default=1100)
    a.add_argument("--alto", type=int, default=760)
    a.add_argument("--salida", default="vista.png")
    p = a.parse_args()

    sub = {"the_nether": "DIM-1", "the_end": "DIM1"}.get(p.dimension, "")
    region = os.path.join(p.mundo, sub, "region")
    if not os.path.isdir(region):
        sys.exit("No encuentro %s / region folder not found" % region)
    if not os.path.isfile(V.JAR_VANILLA):
        print("Ojo: no encuentro Minecraft 1.21.1 en %s.\n"
              "     Sin ese .jar los bloques salen negros y morados (sin textura). Ábrelo una vez con el launcher en la\n"
              "     versión 1.21.1, o di dónde está con la variable OJO_JAR.\n"
              "Note: Minecraft 1.21.1 not found at %s. Without it blocks are drawn black and magenta (no textures).\n"
              "     Run 1.21.1 once from the launcher, or set OJO_JAR to the .jar path." % (V.JAR_VANILLA, V.JAR_VANILLA),
              file=sys.stderr)
    m = O.Mundo(region)
    if p.vuelta:
        img, texto = V.vuelta(m, O.decodificar_seccion, p.x, p.z, radio=p.radio, inclinacion=p.inclinacion, noche=p.noche, seres=p.seres)
    else:
        img, texto = V.vista(m, O.decodificar_seccion, p.x, p.z, radio=p.radio, giro=p.giro, inclinacion=p.inclinacion,
                             proyeccion=p.proyeccion, ancho=p.ancho, alto=p.alto, noche=p.noche, seres=p.seres,
                             resaltar_muebles=p.muebles, cuadricula=not p.sin_cuadricula,
                             camara_en=tuple(p.camara) if p.camara else None, fov=p.fov)
    if img is None:
        sys.exit(texto)
    V.escribir_png(p.salida, img)
    print(texto if isinstance(texto, str) else "")
    print("Imagen / image:", os.path.abspath(p.salida))


if __name__ == "__main__":
    main()
