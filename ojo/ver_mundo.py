# -*- coding: utf-8 -*-
"""See any Minecraft Java world (1.18 or newer) in 3D from its save files, without opening the game. Read-only.
Ver cualquier mundo de Minecraft Java (1.18 o más nuevo) en 3D desde sus archivos, sin abrir el juego.

Usage / uso:
    ojo-view --world "/path/to/saves/MyWorld" --x 0 --z 0 --radius 48 --out view.png
    ojo-view --world ... --x 100 --z -40 --projection perspective --yaw 135 --pitch 30
    ojo-view --world ... --x 100 --z -40 --camera 100 70 -60 --yaw 0 --pitch 10      (player's eye view)
    ojo-view --world ... --x 0 --z 0 --around                                         (4 corners + top in one image)
(From a clone: python ojo/ver_mundo.py with the same options. The Spanish names --mundo, --radio, --salida... still work.)

Textures and models come from your own game .jar: 1.21.1 if installed, otherwise the newest 1.18+ version the
launcher installed, in the usual .minecraft folder on Windows, macOS or Linux. Another path: OJO_JAR. Mods: OJO_MODS."""
import argparse
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import rutas  # noqa: E402

rutas.preparar()  # texture index in the user's cache folder, before ojo_vista3d is imported
import ojo_del_mundo as O  # noqa: E402
import ojo_vista3d as V  # noqa: E402

PROYECCION = {"orto": "orto", "orthographic": "orto", "perspectiva": "perspectiva", "perspective": "perspectiva"}


def main(argv=None):
    a = argparse.ArgumentParser(prog="ojo-view", description="3D view of a Minecraft Java world from its files / "
                                "vista 3D de un mundo de Minecraft desde sus archivos")
    a.add_argument("--world", "--mundo", dest="mundo", metavar="PATH", required=True, help="world folder (the one with level.dat) / carpeta del mundo")
    a.add_argument("--dimension", default="overworld", choices=["overworld", "the_nether", "the_end"])
    a.add_argument("--x", type=int, required=True)
    a.add_argument("--z", type=int, required=True)
    a.add_argument("--radius", "--radio", dest="radio", metavar="N", type=int, default=48, help="blocks around the centre, up to 128 / radio")
    a.add_argument("--projection", "--proyeccion", dest="proyeccion", default="orthographic", choices=sorted(PROYECCION))
    a.add_argument("--yaw", "--giro", dest="giro", metavar="DEG", type=float, default=None, help="facing, like the in-game yaw (0 = south, 90 = west)")
    a.add_argument("--pitch", "--inclinacion", dest="inclinacion", metavar="DEG", type=float, default=35.0, help="degrees looking down")
    a.add_argument("--camera", "--camara", dest="camara", type=float, nargs=3, default=None, metavar=("X", "Y", "Z"),
                   help="put the camera at this point (player's eye view)")
    a.add_argument("--fov", type=float, default=70.0)
    a.add_argument("--night", "--noche", dest="noche", action="store_true")
    a.add_argument("--mobs", "--seres", dest="seres", action="store_true", help="draw mobs and villagers")
    a.add_argument("--furniture", "--muebles", dest="muebles", action="store_true", help="highlight and number furniture")
    a.add_argument("--no-grid", "--sin_cuadricula", dest="sin_cuadricula", action="store_true")
    a.add_argument("--around", "--vuelta", dest="vuelta", action="store_true", help="4 corners + top in one image")
    a.add_argument("--width", "--ancho", dest="ancho", metavar="PX", type=int, default=1100)
    a.add_argument("--height", "--alto", dest="alto", metavar="PX", type=int, default=760)
    a.add_argument("--out", "--salida", dest="salida", metavar="FILE", default="view.png")
    p = a.parse_args(argv)

    sub = {"the_nether": "DIM-1", "the_end": "DIM1"}.get(p.dimension, "")
    region = os.path.join(p.mundo, sub, "region")
    if not os.path.isdir(region):
        sys.exit("Region folder not found / no encuentro la carpeta: %s" % region)
    if not os.path.isfile(V.JAR_VANILLA):
        print("Note: no Minecraft .jar found (looked for %s).\n"
              "      Without it blocks are drawn black and magenta (no textures). Run 1.21.1, or any version from 1.18 on,\n"
              "      once from the launcher, or set OJO_JAR to a client .jar.\n"
              "Ojo: no encuentro el .jar de Minecraft. Abre una vez la 1.21.1 (o una versión desde la 1.18) con el\n"
              "      launcher, o di dónde está con la variable OJO_JAR." % V.JAR_VANILLA, file=sys.stderr)
    m = O.Mundo(region)
    if p.vuelta:
        img, texto = V.vuelta(m, O.decodificar_seccion, p.x, p.z, radio=p.radio, inclinacion=p.inclinacion, noche=p.noche, seres=p.seres)
    else:
        img, texto = V.vista(m, O.decodificar_seccion, p.x, p.z, radio=p.radio, giro=p.giro, inclinacion=p.inclinacion,
                             proyeccion=PROYECCION[p.proyeccion], ancho=p.ancho, alto=p.alto, noche=p.noche, seres=p.seres,
                             resaltar_muebles=p.muebles, cuadricula=not p.sin_cuadricula,
                             camara_en=tuple(p.camara) if p.camara else None, fov=p.fov)
    if img is None:
        sys.exit(texto)
    V.escribir_png(p.salida, img)
    print(texto if isinstance(texto, str) else "")
    print("Image / imagen:", os.path.abspath(p.salida))


if __name__ == "__main__":
    main()
