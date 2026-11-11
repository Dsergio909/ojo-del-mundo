# -*- coding: utf-8 -*-
"""Ojo del mundo: general MCP server. Lets an AI (Claude, or any MCP client) see and measure ANY Minecraft Java
world from its save files, without opening the game. Works on Windows, macOS and Linux. Read-only.

Run:
    ojo-mcp --world "/path/to/.minecraft/saves/MyWorld"        (after pip install)
    python ojo/ojo_mcp.py --world "/path/to/saves/MyWorld"      (from a clone)
The world can also come from the OJO_WORLD environment variable, or be chosen later with the open_world tool.

This file only adds a clean, world-agnostic front door. The heavy lifting (reading .mca files, the 3D renderer)
lives in ojo_del_mundo.py, mapa_aldea.py and ojo_vista3d.py. The modpack-specific server (RCON, test client photos,
village plans) is ojo_del_mundo.py itself and is not exposed here."""
import argparse
import gzip
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

VERSION = "1.1.0"
DIMENSIONES = {"overworld": "", "the_nether": "DIM-1", "the_end": "DIM1"}
# DataVersion of the first release that uses the current chunk format («sections» + «block_states»): 1.18
DATA_VERSION_MIN = 2860
NOMBRES_VERSION = [(4189, "1.21.4"), (4082, "1.21.3"), (3955, "1.21.1"), (3953, "1.21"), (3839, "1.20.6"),
                   (3700, "1.20.4"), (3465, "1.20.1"), (3463, "1.20"), (3337, "1.19.4"), (3120, "1.19.2"),
                   (3105, "1.19"), (2975, "1.18.2"), (2865, "1.18.1"), (2860, "1.18")]


import rutas  # noqa: E402

CACHE_DIR = rutas.preparar()

import ojo_del_mundo as O  # noqa: E402  (after OJO_CACHE is set)
import mapa_aldea as MA  # noqa: E402

ESTADO = {"mundo": None, "dimension": "overworld"}
SIN_MUNDO = ("No world open yet. Call open_world with the folder that has level.dat "
             "(for example .minecraft/saves/MyWorld), or start the server with --world.")


class ErrorDeUso(Exception):
    """Algo que el cliente puede arreglar (ruta mala, mundo sin abrir): se muestra solo el mensaje, como error."""


def _region():
    if not ESTADO["mundo"]:
        raise ErrorDeUso(SIN_MUNDO)
    return os.path.join(ESTADO["mundo"], DIMENSIONES[ESTADO["dimension"]], "region")


# The core functions read the world through these three hooks: point them at the chosen world, never at a modpack.
O.carpeta_region = _region
O.guardar_mundo = lambda: None          # no RCON here: we read what the game last saved
O.SALIDA = os.path.join(CACHE_DIR, "images")


def nombre_version(dv):
    for d, n in NOMBRES_VERSION:
        if dv >= d:
            return n if dv == d else n + "+" if d == NOMBRES_VERSION[0][0] else "about " + n
    return "older than 1.18"


def leer_level_dat(mundo):
    """Name, version, spawn and last played from level.dat (gzip NBT). Empty dict if it can't be read."""
    ruta = os.path.join(mundo, "level.dat")
    try:
        raiz = MA.leer_nbt(gzip.decompress(open(ruta, "rb").read()))
    except Exception:
        return {}
    data = raiz.get("Data", raiz)
    ver = data.get("Version") or {}
    return {"name": data.get("LevelName"), "data_version": data.get("DataVersion") or ver.get("Id"),
            "version": ver.get("Name"), "spawn": (data.get("SpawnX"), data.get("SpawnY"), data.get("SpawnZ"))}


def describir_mundo():
    m = ESTADO["mundo"]
    info = leer_level_dat(m)
    regiones = {d: len([f for f in os.listdir(os.path.join(m, s, "region")) if f.endswith(".mca")])
                for d, s in DIMENSIONES.items() if os.path.isdir(os.path.join(m, s, "region"))}
    lin = ["World: %s" % (info.get("name") or os.path.basename(os.path.normpath(m))), "Folder: %s" % m]
    dv = info.get("data_version")
    if dv:
        lin.append("Minecraft version: %s (DataVersion %s)" % (info.get("version") or nombre_version(dv), dv))
        if dv < DATA_VERSION_MIN:
            lin.append("WARNING: this world was saved by a version older than 1.18. Its chunks use an older format "
                       "that Ojo del mundo cannot read yet. Open it once in 1.18 or newer to convert it.")
    else:
        lin.append("Minecraft version: unknown (level.dat could not be read)")
    if info.get("spawn") and None not in info["spawn"]:
        lin.append("Spawn: x %s, y %s, z %s (a good place to start looking)" % info["spawn"])
    lin.append("Dimensions with saved chunks: %s" % (", ".join("%s (%d region files)" % kv for kv in regiones.items()) or "none"))
    lin.append("Reading now: %s" % ESTADO["dimension"])
    try:
        import ojo_vista3d as V
        lin.append("Textures: %s" % (V.JAR_VANILLA if os.path.isfile(V.JAR_VANILLA) else
                                     "Minecraft .jar NOT found: 3D views will draw black and magenta blocks. Install the game "
                                     "version once with the launcher, or set OJO_JAR to a client .jar."))
    except Exception as e:  # numpy missing, etc.
        lin.append("3D views unavailable: %s" % e)
    lin.append("Note: tool answers are written in Spanish for now; you can reply to the user in any language.")
    return "\n".join(lin)


# --- tools ------------------------------------------------------------------------------------------------------


def t_open_world(path, dimension="overworld"):
    ruta = os.path.abspath(os.path.expanduser(str(path).strip().strip('"')))
    if os.path.isfile(ruta) and os.path.basename(ruta) == "level.dat":
        ruta = os.path.dirname(ruta)
    if os.path.basename(ruta) == "region":  # dieron la carpeta region: subir a la del mundo
        ruta = os.path.dirname(ruta)
    if os.path.basename(ruta) in ("DIM-1", "DIM1"):  # dieron la carpeta de una dimensión
        ruta = os.path.dirname(ruta)
    if not isinstance(dimension, str) or dimension not in DIMENSIONES:
        raise ErrorDeUso("Unknown dimension %r. Use overworld, the_nether or the_end." % (dimension,))
    if not os.path.isdir(os.path.join(ruta, DIMENSIONES[dimension], "region")):
        raise ErrorDeUso("No %s folder with region files in %s. Give the world folder: the one that has level.dat and a "
                         "region folder (for example .minecraft/saves/MyWorld)." % (os.path.join(DIMENSIONES[dimension], "region") or "region", ruta))
    ESTADO["mundo"], ESTADO["dimension"] = ruta, dimension
    return describir_mundo()


def t_world_info():
    if not ESTADO["mundo"]:
        raise ErrorDeUso("No world open yet. Call open_world first.")
    return describir_mundo()


def _entero(v, nombre):
    """12, 12.0 o "12" -> 12; cualquier otra cosa: ValueError en inglés."""
    if isinstance(v, bool):
        raise ValueError("%s must be an integer" % nombre)
    if isinstance(v, int):
        return v
    try:
        f = float(v.strip() if isinstance(v, str) else v)
        if f == f and abs(f) != float("inf") and f == int(f):
            return int(f)
    except (TypeError, ValueError):
        pass
    raise ValueError("%s must be an integer (got %r)" % (nombre, v))


def _numero(v, nombre):
    """12, 12.5 o "12.5" -> float; cualquier otra cosa: ValueError en inglés."""
    if isinstance(v, bool):
        raise ValueError("%s must be a number" % nombre)
    try:
        f = float(v.strip() if isinstance(v, str) else v)
        if f == f and abs(f) != float("inf"):
            return f
    except (TypeError, ValueError):
        pass
    raise ValueError("%s must be a number (got %r)" % (nombre, v))


def _opcion(v, tabla, nombre):
    """Una de las claves de tabla (sin importar mayúsculas); si no, ValueError que lista las válidas."""
    if isinstance(v, str) and v.strip().lower() in tabla:
        return v.strip().lower()
    raise ValueError("%s must be one of: %s" % (nombre, ", ".join(tabla)))


def _opc(f, v, nombre):
    """Como f, pero None (argumento que no se dio) se queda None."""
    return None if v is None else f(v, nombre)


def _zona(x, z, radius, x1, z1, x2, z2, maximo):
    if None not in (x1, z1, x2, z2):  # la caja completa manda: se ignoran x, z y radius
        return _entero(x1, "x1"), _entero(z1, "z1"), _entero(x2, "x2"), _entero(z2, "z2")
    if x is None or z is None:
        raise ValueError("Give an area: x1, z1, x2, z2, or x, z and radius.")
    r = max(1, min(_entero(32 if radius is None else radius, "radius"), maximo))
    x, z = _entero(x, "x"), _entero(z, "z")
    return x - r, z - r, x + r, z + r


def t_blocks(x, y, z, radius=2):
    return O.t_bloques(_entero(x, "x"), _entero(y, "y"), _entero(z, "z"), _entero(radius, "radius"))


def t_top_down_map(x1, z1, x2, z2, scale=2):
    return O.t_vista_cenital(_entero(x1, "x1"), _entero(z1, "z1"), _entero(x2, "x2"), _entero(z2, "z2"),
                             escala=_entero(scale, "scale"))


def t_side_section(x1, z1, x2, z2, y_min=None, y_max=None, scale=5):
    y_min, y_max = _opc(_entero, y_min, "y_min"), _opc(_entero, y_max, "y_max")
    if None not in (y_min, y_max) and y_min > y_max:
        y_min, y_max = y_max, y_min
    return O.t_corte_lateral(_entero(x1, "x1"), _entero(z1, "z1"), _entero(x2, "x2"), _entero(z2, "z2"),
                             y_min=y_min, y_max=y_max, escala=_entero(scale, "scale"))


def t_isometric(x1, z1, x2, z2, y_min=None, y_max=None, from_corner="southeast", size=4):
    return O.t_isometrica(_entero(x1, "x1"), _entero(z1, "z1"), _entero(x2, "x2"), _entero(z2, "z2"),
                          y_min=_opc(_entero, y_min, "y_min"), y_max=_opc(_entero, y_max, "y_max"),
                          desde=ESQUINAS[_opcion(from_corner, ESQUINAS, "from_corner")], tam=_entero(size, "size"))


ESQUINAS = {"southeast": "sureste", "southwest": "suroeste", "northwest": "noroeste", "northeast": "noreste"}
RUMBOS = {"north": "norte", "northeast": "noreste", "east": "este", "southeast": "sureste", "south": "sur",
          "southwest": "suroeste", "west": "oeste", "northwest": "noroeste"}
PROYECCIONES = ["orthographic", "perspective"]


def _marcas(marks):
    if marks is None:
        return None
    if not isinstance(marks, list) or not all(isinstance(m, (list, tuple)) and len(m) == 3 for m in marks):
        raise ValueError("marks must be a list of [x, y, z] triples, for example [[10, 64, 20], [12, 65, 22]]")
    return [tuple(_numero(c, "marks") for c in m) for m in marks]


def t_view_3d(x, z, radius=48, y=None, from_direction="southeast", yaw=None, pitch=35, projection="orthographic",
              distance=None, night=False, no_leaves=False, mobs=False, y_min=None, y_max=None, width=1100, height=760,
              marks=None, grid=True, camera_x=None, camera_y=None, camera_z=None, fov=70):
    cam = [camera_x, camera_y, camera_z]
    if any(c is not None for c in cam) and any(c is None for c in cam):
        raise ValueError("camera_x, camera_y and camera_z must be given together (all three or none)")
    return O.t_vista_3d(x=_entero(x, "x"), z=_entero(z, "z"), y=_opc(_entero, y, "y"),
                        radio=max(4, min(_entero(radius, "radius"), 64)),
                        desde=RUMBOS[_opcion(from_direction, RUMBOS, "from_direction")], giro=_opc(_numero, yaw, "yaw"),
                        inclinacion=_numero(pitch, "pitch"),
                        proyeccion="perspectiva" if _opcion(projection, PROYECCIONES, "projection") == "perspective" else "orto",
                        distancia=_opc(_numero, distance, "distance"), noche=night, sin_hojas=no_leaves, seres=mobs,
                        y_min=_opc(_entero, y_min, "y_min"), y_max=_opc(_entero, y_max, "y_max"),
                        ancho=_entero(width, "width"), alto=_entero(height, "height"), marcas=_marcas(marks), cuadricula=grid,
                        camara_x=_opc(_numero, camera_x, "camera_x"), camara_y=_opc(_numero, camera_y, "camera_y"),
                        camara_z=_opc(_numero, camera_z, "camera_z"), fov=_numero(fov, "fov"))


def t_view_around(x, z, radius=48, pitch=35, night=False, no_leaves=False, mobs=False, y_max=None, include_top=True, width=1500):
    return O.t_vista_vuelta(x=_entero(x, "x"), z=_entero(z, "z"), radio=max(4, min(_entero(radius, "radius"), 64)),
                            inclinacion=_numero(pitch, "pitch"), noche=night, sin_hojas=no_leaves, seres=mobs,
                            y_max=_opc(_entero, y_max, "y_max"), con_arriba=include_top, ancho=_entero(width, "width"))


def t_four_views(x1, y1, z1, x2, y2, z2, size=None, from_corner="southeast", mark_entities=True):
    return O.t_planos(_entero(x1, "x1"), _entero(y1, "y1"), _entero(z1, "z1"), _entero(x2, "x2"), _entero(y2, "y2"),
                      _entero(z2, "z2"), tam=_opc(_entero, size, "size"),
                      desde=ESQUINAS[_opcion(from_corner, ESQUINAS, "from_corner")], marcar_entidades=mark_entities)


def t_count_blocks(x1=None, z1=None, x2=None, z2=None, y1=None, y2=None, x=None, z=None, radius=None, find=None, max_list=40):
    y1, y2 = _opc(_entero, y1, "y1"), _opc(_entero, y2, "y2")
    ayuda = "Give an area: x1, z1, x2, z2 (optionally y1, y2), or x, z and radius."
    if None not in (x1, z1, x2, z2):  # la caja completa manda: se ignoran x, z y radius
        return O.t_contar_bloques(x1=_entero(x1, "x1"), y1=y1, z1=_entero(z1, "z1"), x2=_entero(x2, "x2"), y2=y2,
                                  z2=_entero(z2, "z2"), buscar=find, max_lista=_entero(max_list, "max_list"))
    if x is None or z is None:
        raise ErrorDeUso(ayuda + (" radius needs x and z." if radius is not None else ""))
    # x, z sin radio: 32 por defecto, como read_texts y entities
    return O.t_contar_bloques(y1=y1, y2=y2, x=_entero(x, "x"), z=_entero(z, "z"),
                              radio=_entero(32 if radius is None else radius, "radius"), buscar=find,
                              max_lista=_entero(max_list, "max_list"))


def t_measure(x1, y1, z1, x2, y2, z2):
    return O.t_medir(_entero(x1, "x1"), _entero(y1, "y1"), _entero(z1, "z1"), _entero(x2, "x2"), _entero(y2, "y2"),
                     _entero(z2, "z2"))


def t_read_texts(x=None, z=None, radius=None, x1=None, z1=None, x2=None, z2=None, full=False):
    a, b, c, d = _zona(x, z, radius, x1, z1, x2, z2, 300)
    return O.t_leer_textos(x1=a, z1=b, x2=c, z2=d, completo=full)


TIPOS_SERES = ["aldeano", "cuadro", "marco", "armadura", "monstruo", "animal", "objeto", "vehiculo", "display", "otro"]
SERES_EN = dict(zip(["villager", "painting", "item_frame", "armor_stand", "monster", "animal", "item", "vehicle", "display", "other"],
                    TIPOS_SERES))


def t_entities(x=None, z=None, radius=None, x1=None, z1=None, x2=None, z2=None, kinds=None):
    a, b, c, d = _zona(x, z, radius, x1, z1, x2, z2, 320)
    if isinstance(kinds, str):
        kinds = [kinds]
    if kinds is not None and not isinstance(kinds, list):
        raise ValueError("kinds must be a string or a list of: %s" % ", ".join(SERES_EN))
    return O.t_seres(x1=a, z1=b, x2=c, z2=d, tipos=[SERES_EN[_opcion(k, SERES_EN, "kinds")] for k in kinds] if kinds else None)


def t_use_dimension(dimension="overworld"):
    if not ESTADO["mundo"]:
        raise ErrorDeUso("No world open yet. Call open_world first.")
    return t_open_world(ESTADO["mundo"], dimension)


I = {"type": "integer"}
N = {"type": "number"}
B = {"type": "boolean"}
S = {"type": "string"}
CORNER = {"type": "string", "enum": list(ESQUINAS)}
AREA = {"x": I, "z": I, "radius": I, "x1": I, "z1": I, "x2": I, "z2": I}

HERRAMIENTAS = [
    ("open_world", "Open a Minecraft Java world folder (the one with level.dat, e.g. .minecraft/saves/MyWorld). Read-only. "
     "Returns its name, version, spawn point and which dimensions have chunks. Call this first.",
     {"path": S, "dimension": {"type": "string", "enum": list(DIMENSIONES)}}, ["path"], t_open_world),
    ("world_info", "Which world and dimension are open, the Minecraft version, the spawn point and whether textures were found.",
     {}, [], t_world_info),
    ("use_dimension", "Switch the open world to overworld, the_nether or the_end.",
     {"dimension": {"type": "string", "enum": list(DIMENSIONES)}}, ["dimension"], t_use_dimension),
    ("view_3d", "A 3D picture of the world as in the game, drawn from the save files with each block's real shape and texture "
     "(vanilla and mods). Up to 129 x 129 blocks (radius up to 64). Any angle: from_direction or yaw, pitch 0-90 (90 = top-down); "
     "orthographic (to measure) or perspective (like a drone photo). camera_x/y/z puts the camera where a player would stand. "
     "night=true uses the saved light, no_leaves hides tree leaves, mobs marks mobs and villagers, y_max cuts roofs to look "
     "inside, marks = [[x,y,z],...] in red. 2 to 6 s.",
     {"x": I, "z": I, "radius": I, "y": I, "from_direction": {"type": "string", "enum": list(RUMBOS)}, "yaw": N, "pitch": N,
      "projection": {"type": "string", "enum": ["orthographic", "perspective"]}, "distance": N, "night": B, "no_leaves": B,
      "mobs": B, "y_min": I, "y_max": I, "width": I, "height": I, "marks": {"type": "array", "items": {"type": "array", "items": I}},
      "grid": B, "camera_x": N, "camera_y": N, "camera_z": N, "fov": N}, ["x", "z"], t_view_3d),
    ("view_around", "The same area from the 4 corners and from above, in ONE image: see something from every side for the cost "
     "of one picture. About 7 s.",
     {"x": I, "z": I, "radius": I, "pitch": N, "night": B, "no_leaves": B, "mobs": B, "y_max": I, "include_top": B, "width": I},
     ["x", "z"], t_view_around),
    ("four_views", "ONE small image with 4 drawings of an area (up to 100 x 80 x 100): plan, isometric, front and side, with "
     "coordinates every 5 blocks, a colour per block, mod blocks outlined and a numbered legend with each block id. Cheap in tokens.",
     {"x1": I, "y1": I, "z1": I, "x2": I, "y2": I, "z2": I, "size": I, "from_corner": CORNER, "mark_entities": B},
     ["x1", "y1", "z1", "x2", "y2", "z2"], t_four_views),
    ("top_down_map", "Flat map seen from above, one colour per block.", {"x1": I, "z1": I, "x2": I, "z2": I, "scale": I},
     ["x1", "z1", "x2", "z2"], t_top_down_map),
    ("side_section", "Vertical cut along a line from (x1, z1) to (x2, z2): what is underground and how tall things are.",
     {"x1": I, "z1": I, "x2": I, "z2": I, "y_min": I, "y_max": I, "scale": I}, ["x1", "z1", "x2", "z2"], t_side_section),
    ("isometric", "Simple isometric block view of an area up to 96 x 96, fast and without textures.",
     {"x1": I, "z1": I, "x2": I, "z2": I, "y_min": I, "y_max": I, "from_corner": CORNER, "size": I},
     ["x1", "z1", "x2", "z2"], t_isometric),
    ("blocks", "The exact blocks around a point (radius up to 5), layer by layer as text.",
     {"x": I, "y": I, "z": I, "radius": I}, ["x", "y", "z"], t_blocks),
    ("count_blocks", "COUNT the blocks in an area: how many of each type, how many are not air, and the real length, width and "
     "height of what is there. find = part of a block name, or several separated by commas (e.g. 'lantern,torch'), to list where "
     "each one is. Up to 25 million positions.",
     {"x1": I, "y1": I, "z1": I, "x2": I, "y2": I, "z2": I, "x": I, "z": I, "radius": I, "find": S, "max_list": I}, [],
     t_count_blocks),
    ("measure", "Distance in blocks between two points: straight line, along the ground and walking on the grid, plus direction.",
     {"x1": I, "y1": I, "z1": I, "x2": I, "y2": I, "z2": I}, ["x1", "y1", "z1", "x2", "y2", "z2"], t_measure),
    ("read_texts", "What the signs say (front and back), and the books on lecterns or inside chests and barrels in an area: "
     "title, author and pages (the first one, or all with full=true).",
     dict(AREA, full=B), [], t_read_texts),
    ("entities", "Saved entities in an area: villagers with name and job, paintings, item frames and what they hold, armor "
     "stands, mobs, animals, dropped items and vehicles.",
     dict(AREA, kinds={"type": "array", "items": {"type": "string", "enum": list(SERES_EN)}}), [], t_entities),
]
FUNCIONES = {h[0]: h[4] for h in HERRAMIENTAS}
PROPIEDADES = {h[0]: h[2] for h in HERRAMIENTAS}
TITULOS = {"open_world": "Open world", "world_info": "World info", "use_dimension": "Switch dimension", "view_3d": "3D view",
           "view_around": "View from all sides", "four_views": "Four views", "top_down_map": "Top-down map",
           "side_section": "Side section", "isometric": "Isometric view", "blocks": "Blocks around a point",
           "count_blocks": "Count blocks", "measure": "Measure distance", "read_texts": "Read signs and books",
           "entities": "Entities"}


def llamar(nombre, args):
    if nombre not in FUNCIONES:
        raise ValueError("unknown tool: %s" % nombre)
    args = args or {}
    for k in args:
        if k not in PROPIEDADES[nombre]:
            raise ValueError("unknown argument %r for %s" % (k, nombre))
    if not ESTADO["mundo"] and nombre not in ("open_world", "world_info", "measure"):
        raise ErrorDeUso("No world open yet. Call open_world with the folder that has level.dat "
                         "(for example .minecraft/saves/MyWorld), then try %s again." % nombre)
    res = FUNCIONES[nombre](**args)
    texto, ruta = res if isinstance(res, tuple) else (res, None)
    contenido = [{"type": "text", "text": texto}]
    if ruta:
        contenido.append({"type": "image", "data": O.png_base64(ruta), "mimeType": "image/png"})
    return contenido


def responder(salida, msg):
    salida.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    salida.flush()


def _error(salida, mid, codigo, mensaje):
    responder(salida, {"jsonrpc": "2.0", "id": mid, "error": {"code": codigo, "message": mensaje}})


VERSIONES_PROTOCOLO = ("2025-06-18", "2025-03-26", "2024-11-05")


def servir():
    salida = sys.stdout.buffer
    sys.stdout = sys.stderr  # stray prints go to stderr and never break the protocol
    for linea in sys.stdin.buffer:
        if not linea.strip():
            continue
        try:
            msg = json.loads(linea.decode("utf-8"))
        except ValueError:
            continue
        if not isinstance(msg, dict):
            _error(salida, None, -32600, "invalid request: a JSON-RPC message must be an object")
            continue
        mid, metodo = msg.get("id"), msg.get("method")
        if mid is None:
            continue  # notifications
        if "method" not in msg:
            continue  # a response from the client: nothing to answer
        try:
            params = msg.get("params")
            if metodo == "initialize":
                pedida = params.get("protocolVersion") if isinstance(params, dict) else None
                version = pedida if isinstance(pedida, str) and pedida in VERSIONES_PROTOCOLO else VERSIONES_PROTOCOLO[0]
                res = {"protocolVersion": version, "capabilities": {"tools": {}},
                       "serverInfo": {"name": "ojo-del-mundo", "version": VERSION},
                       "instructions": "Read-only views of a Minecraft Java world from its save files. Start with open_world "
                                       "(unless a world was given at startup), then world_info, view_3d, count_blocks..."}
            elif metodo == "ping":
                res = {}
            elif metodo == "tools/list":
                res = {"tools": [{"name": n, "title": TITULOS[n], "description": d,
                                  "inputSchema": {"type": "object", "properties": p, "required": r},
                                  "annotations": {"readOnlyHint": True, "openWorldHint": False}}
                                 for n, d, p, r, _ in HERRAMIENTAS]}
            elif metodo == "tools/call":
                if params is None:
                    params = {}
                if not isinstance(params, dict) or not isinstance(params.get("name"), str) \
                        or not (params.get("arguments") is None or isinstance(params.get("arguments"), dict)):
                    _error(salida, mid, -32602, "invalid params: tools/call needs a name (string) and arguments (object)")
                    continue
                try:
                    res = {"content": llamar(params["name"], params.get("arguments")), "isError": False}
                except (ErrorDeUso, ValueError) as e:
                    res = {"content": [{"type": "text", "text": str(e) if isinstance(e, ErrorDeUso)
                                        else "Error in %s: %s" % (params["name"], e)}], "isError": True}
                except Exception as e:
                    res = {"content": [{"type": "text", "text": "Error in %s: %s: %s" % (params["name"], type(e).__name__, e)}],
                           "isError": True}
            else:
                _error(salida, mid, -32601, "unknown method: %s" % metodo)
                continue
            responder(salida, {"jsonrpc": "2.0", "id": mid, "result": res})
        except Exception as e:
            _error(salida, mid, -32603, str(e))


def main(argv=None):
    a = argparse.ArgumentParser(prog="ojo-mcp", description="Ojo del mundo MCP server: see any Minecraft Java world from its files.")
    a.add_argument("--world", default=os.environ.get("OJO_WORLD"), help="world folder (the one with level.dat)")
    a.add_argument("--dimension", default="overworld", choices=list(DIMENSIONES))
    a.add_argument("--version", action="version", version="ojo-del-mundo " + VERSION)
    p = a.parse_args(argv)
    if p.world:
        try:
            t_open_world(p.world, p.dimension)
        except ErrorDeUso as e:
            print(e, file=sys.stderr)  # keep serving: the client can still call open_world with another path
    servir()


if __name__ == "__main__":
    main()
