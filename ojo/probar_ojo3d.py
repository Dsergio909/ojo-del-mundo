"""Prueba de regresión del Ojo (4-nov): cada herramienta de imagen e interiores una vez, con los archivos del mundo de pruebas y sin save-all (no toca servidores). Uso: py -3.12 herramientas/probar_ojo3d.py"""
import os
import sys
import time
os.environ["NIEBLA_SRV"] = "servidor_pruebas"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
import ojo_del_mundo as O  # noqa: E402
O.guardar_mundo = lambda: None
pruebas = [
    ("vista_3d", {"radio": 50, "marcar": True, "seres": True}),
    ("vista_3d", {"casa": "taberna", "proyeccion": "perspectiva", "noche": True}),
    ("vista_vuelta", {"casa": "molino", "marcar": True}),
    ("vista_pisos", {"casa": "iglesia"}),
    ("revisar_seres", {"radio": 60}),
    ("isometrica", {"x1": -2140, "z1": 300, "x2": -2110, "z2": 320}),
    ("planta_interior", {"casa": "tavern"}),
    ("interior_isometrico", {"casa": "tavern"}),
    ("inventario_interior", {"casa": "granja"}),
    ("auditar_interiores", {}),
    ("planos", {"x1": -2135, "y1": 60, "z1": 296, "x2": -2115, "y2": 80, "z2": 316}),
    ("contar_bloques", {"casa": "taberna", "buscar": "lantern"}),
    ("contar_bloques", {"x": -2128, "z": 368, "radio": 60}),
    ("medir", {"x1": -2128, "y1": 69, "z1": 368, "x2": -2125, "y2": 64, "z2": 306}),
    ("vista_3d", {"casa": "taberna", "cuadricula": False}),
    ("vista_3d", {"camara_x": -2126.5, "camara_y": 68, "camara_z": 326.5, "giro": 180, "inclinacion": 8, "fov": 76, "ancho": 960, "alto": 540}),
    ("vista_3d", {"casa": "castillo", "seres": True, "marcar": True}),
    ("vista_3d", {"x": -2128, "z": 395, "radio": 30, "noche": True}),
    ("leer_textos", {}),
    ("inventario_interior", {"casa": "granja"}),
]
fallas = 0
for nombre, a in pruebas:
    t = time.time()
    try:
        c = O.llamar(nombre, a)
        print("OK   %-20s %5.1f s  %s" % (nombre, time.time() - t, "+imagen" if len(c) > 1 else "solo texto"))
    except Exception as e:
        fallas += 1
        print("FALLA %-20s %s: %s" % (nombre, type(e).__name__, e))
print("fallas:", fallas)
