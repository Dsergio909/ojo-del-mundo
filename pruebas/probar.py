# -*- coding: utf-8 -*-
"""Pruebas automáticas del Ojo del mundo, sin Minecraft instalado (corren en GitHub en cada cambio).

1. Arma el mundo de pruebas/mundo_prueba.py y lee bloques conocidos desde sus .mca.
2. Dibuja ese mundo con ver_mundo.py (sin el .jar del juego) y revisa que salga una imagen y el aviso del .jar.
3. Arranca el servidor MCP del modpack y revisa que responda con sus 32 herramientas.
4. Arranca el servidor MCP general (ojo_mcp.py), abre el mundo de prueba y usa sus herramientas como lo haría Claude.
5. Revisa que se elija bien el .jar del juego (1.21.1 o la versión más nueva desde la 1.18).

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
            revisar("avisa que falta el .jar del juego", "no Minecraft .jar found" in r.stderr, r.stderr[-200:])
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

    print("4. Servidor MCP general (ojo_mcp.py)")
    llamadas = [("initialize", {"protocolVersion": "2025-06-18"}), ("tools/list", {}),
                ("tools/call", {"name": "count_blocks", "arguments": {"x": 7, "z": 7, "radius": 3}}),
                ("tools/call", {"name": "open_world", "arguments": {"path": mundo}}),
                ("tools/call", {"name": "count_blocks", "arguments": {"x1": 0, "z1": 0, "x2": 31, "z2": 31, "y1": 60, "y2": 70,
                                                                       "find": "torch"}}),
                ("tools/call", {"name": "view_3d", "arguments": {"x": 12, "z": 12, "radius": 16, "width": 400, "height": 300}}),
                ("tools/call", {"name": "measure", "arguments": {"x1": 0, "y1": 64, "z1": 0, "x2": 3, "y2": 64, "z2": 4}}),
                ("tools/call", {"name": "use_dimension", "arguments": {"dimension": "the_nether"}}),
                ("tools/call", {"name": "no_existe", "arguments": {}})]
    entrada = "".join(json.dumps({"jsonrpc": "2.0", "id": i + 1, "method": m, "params": p}) + "\n"
                      for i, (m, p) in enumerate(llamadas))
    r = subprocess.run([sys.executable, os.path.join(OJO, "ojo_mcp.py")], input=entrada, capture_output=True, text=True,
                       encoding="utf-8", cwd=tmp, timeout=300, env=entorno)
    res = {}
    for linea in r.stdout.splitlines():
        try:
            d = json.loads(linea)
            res[d.get("id")] = d.get("result") or {}
        except ValueError:
            pass
    texto = lambda i: ((res.get(i, {}).get("content") or [{}])[0].get("text") or "")
    nombres = [h["name"] for h in res.get(2, {}).get("tools") or []]
    revisar("tiene 14 herramientas con nombre en inglés", len(nombres) == 14 and "open_world" in nombres and "view_3d" in nombres,
            str(nombres))
    revisar("sin mundo abierto pide open_world (y no se cae)", "open_world" in texto(3) and res.get(3, {}).get("isError") is False, texto(3)[:150])
    revisar("open_world lee level.dat: nombre, versión 1.21.1 y spawn",
            "Ojo test world" in texto(4) and "1.21.1" in texto(4) and "Spawn: x 7" in texto(4), texto(4)[:300])
    revisar("count_blocks encuentra la antorcha en (7, 65, 7)", "(7, 65, 7) torch" in texto(5), texto(5)[-300:])
    revisar("view_3d devuelve una imagen PNG", len(res.get(6, {}).get("content") or []) == 2
            and res[6]["content"][1].get("mimeType") == "image/png", texto(6)[:150])
    revisar("measure: 5 bloques en línea recta", "en línea recta 5.0 bloques" in texto(7), texto(7)[:150])
    revisar("use_dimension sin Nether avisa y no cambia de mundo", "DIM-1" in texto(8), texto(8)[:150])
    revisar("herramienta desconocida devuelve error sin caerse", res.get(9, {}).get("isError") is True, str(res.get(9))[:150])

print("5. Elegir el .jar del juego")
import importlib  # noqa: E402
with tempfile.TemporaryDirectory() as casa:
    for v in ["1.20.4", "1.21.3", "1.19.2", "24w10a", "1.16.5", "fabric-loader-1.21"]:
        os.makedirs(os.path.join(casa, ".minecraft", "versions", v))
        open(os.path.join(casa, ".minecraft", "versions", v, v + ".jar"), "w").close()
    antes = {k: os.environ.get(k) for k in ("HOME", "APPDATA", "OJO_JAR", "USERPROFILE")}
    os.environ.update(HOME=casa, USERPROFILE=casa)
    for k in ("APPDATA", "OJO_JAR"):
        os.environ.pop(k, None)
    import ojo_vista3d as V  # noqa: E402
    elegido = V.buscar_jar()
    revisar("sin la 1.21.1 elige la versión más nueva desde la 1.18 (1.21.3)", elegido.endswith(os.path.join("1.21.3", "1.21.3.jar")), elegido)
    os.makedirs(os.path.join(casa, ".minecraft", "versions", "1.21.1"))
    open(os.path.join(casa, ".minecraft", "versions", "1.21.1", "1.21.1.jar"), "w").close()
    elegido = V.buscar_jar()
    revisar("con la 1.21.1 instalada prefiere la 1.21.1 (la probada)", elegido.endswith(os.path.join("1.21.1", "1.21.1.jar")), elegido)
    for k, v in antes.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

print()
if fallas:
    sys.exit("%d prueba(s) fallaron: %s" % (len(fallas), ", ".join(fallas)))
print("Todas las pruebas pasaron.")
