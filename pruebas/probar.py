# -*- coding: utf-8 -*-
"""Pruebas automáticas del Ojo del mundo, sin Minecraft instalado (corren en GitHub en cada cambio).

1. Arma el mundo de pruebas/mundo_prueba.py y lee bloques conocidos desde sus .mca.
2. Dibuja ese mundo con ver_mundo.py (sin el .jar del juego) y revisa que salga una imagen y el aviso del .jar.
3. Arranca el servidor MCP y revisa que responda con sus 32 herramientas.

Uso: python pruebas/probar.py
"""
import json
import os
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
OJO = os.path.join(os.path.dirname(AQUI), "ojo")
sys.path.insert(0, AQUI)
sys.path.insert(0, OJO)
import mundo_prueba  # noqa: E402

fallas = []


def revisar(nombre, ok, detalle=""):
    print(("  ok    " if ok else "  FALLA ") + nombre + ("" if ok else "  (" + detalle + ")"))
    if not ok:
        fallas.append(nombre)


with tempfile.TemporaryDirectory() as tmp:
    mundo = mundo_prueba.crear(os.path.join(tmp, "mundo"))

    print("1. Leer bloques desde los archivos")
    import ojo_del_mundo as O
    m = O.Mundo(os.path.join(mundo, "region"))
    esperado = {
        (4, 65, 4): "cobblestone",   # esquina de la casa
        (7, 65, 4): "air",           # la entrada está abierta
        (7, 65, 7): "torch",         # antorcha adentro
        (5, 69, 5): "oak_planks",    # techo
        (22, 64, 8): "water",        # estanque
        (0, 64, 0): "grass_block",
        (0, 63, 0): "dirt",
        (0, 50, 0): "stone",
        (0, 70, 0): "air",
        (40, 64, 40): None,          # chunk sin generar
    }
    for pos, bloque in esperado.items():
        leido = m.estado(*pos)[0]
        revisar("%s en %s" % (bloque, pos), leido == bloque, "leyó %s" % leido)

    print("2. Dibujar el mundo con ver_mundo.py")
    entorno = dict(os.environ, OJO_JAR=os.path.join(tmp, "no_existe.jar"), OJO_MODS=os.path.join(tmp, "sin_mods"),
                   OJO_CACHE=os.path.join(tmp, "indice.pkl"))  # no toca el índice de texturas de tu PC
    for nombre, extra in (("vista ortográfica", []), ("perspectiva", ["--proyeccion", "perspectiva", "--giro", "200"]),
                          ("de noche", ["--noche"]), ("desde un jugador", ["--camara", "7", "66", "-6", "--giro", "0"])):
        salida = os.path.join(tmp, "vista.png")
        if os.path.exists(salida):
            os.remove(salida)
        r = subprocess.run([sys.executable, os.path.join(OJO, "ver_mundo.py"), "--mundo", mundo, "--x", "12", "--z", "12",
                            "--radio", "16", "--ancho", "400", "--alto", "300", "--salida", salida] + extra,
                           capture_output=True, text=True, encoding="utf-8", env=entorno, cwd=tmp, timeout=300)
        hecho = r.returncode == 0 and os.path.isfile(salida) and open(salida, "rb").read(8) == b"\x89PNG\r\n\x1a\n"
        revisar(nombre + ": sale una imagen PNG", hecho, (r.stderr or r.stdout)[-300:])
        if nombre == "vista ortográfica":
            revisar("avisa que falta el .jar del juego", "no encuentro Minecraft 1.21.1" in r.stderr, r.stderr[-200:])
            revisar("el informe ubica el centro", "centro (12," in r.stdout, r.stdout[:200])

    print("3. Servidor MCP")
    pedidos = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
               {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    r = subprocess.run([sys.executable, os.path.join(OJO, "ojo_del_mundo.py")],
                       input="".join(json.dumps(p) + "\n" for p in pedidos),
                       capture_output=True, text=True, encoding="utf-8", cwd=tmp, timeout=120)
    respuestas = {}
    for linea in r.stdout.splitlines():
        try:
            d = json.loads(linea)
            respuestas[d.get("id")] = d
        except ValueError:
            pass
    revisar("responde a initialize", "serverInfo" in (respuestas.get(1, {}).get("result") or {}), r.stderr[-200:])
    herramientas = (respuestas.get(2, {}).get("result") or {}).get("tools") or []
    revisar("tiene 32 herramientas", len(herramientas) == 32, "tiene %d" % len(herramientas))
    revisar("cada herramienta trae nombre, descripción y esquema",
            all(h.get("name") and h.get("description") and h.get("inputSchema", {}).get("type") == "object"
                for h in herramientas))

print()
if fallas:
    sys.exit("%d prueba(s) fallaron: %s" % (len(fallas), ", ".join(fallas)))
print("Todas las pruebas pasaron.")
