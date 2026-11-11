# -*- coding: utf-8 -*-
"""«Ojo del mundo»: conector MCP (stdio, Python puro, sin instalar nada) para que Claude vea y audite el mundo del
modpack sin depender de capturas. Lee los .mca del servidor (mapa_aldea.py), usa RCON (clave de server.properties) y,
solo si Sergio lo autoriza, el cliente espectador «Prueba» para sacar fotos.
Todo es de solo lectura: los comandos RCON que cambian el mundo piden confirmar=true.
Registro: claude mcp add --scope project ojo-del-mundo -- py -3.12 herramientas/ojo_del_mundo.py
Prueba rápida sin MCP: py -3.12 ojo_del_mundo.py --probar"""
import base64
import contextlib
import io
import json
import math
import os
import re
import socket
import struct
import subprocess
import sys
import time
import zlib
import gzip

try:  # 29-oct (banco_ojo): numpy acelera la lectura de secciones; sin él todo funciona igual, más lento
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mapa_aldea as MA  # noqa: E402
import rcon as R  # noqa: E402

RAIZ = R.RAIZ
SALIDA = os.path.join(RAIZ, "auditoria_aldea", "ojo")
X0, Y0, Z0 = -2128, 69, 368  # fogata (centro del pueblo)
BLUEMAP = "http://localhost:8100"
_ultimo_guardado = [0.0]

# --- RCON (devuelve texto; nunca imprime en stdout porque stdout es el canal MCP) ---


def rcon(*comandos):
    p = R.propiedades()
    s = socket.create_connection(("127.0.0.1", int(p.get("rcon.port", 25575))), timeout=30)
    try:
        s.sendall(R.paquete(1, 3, p["rcon.password"]))
        rid, _ = R.leer(s)
        if rid == -1:
            raise RuntimeError("clave RCON incorrecta")
        salida = []
        for i, c in enumerate(comandos, start=2):
            s.sendall(R.paquete(i, 2, c))
            salida.append(R.leer(s)[1])
        return salida
    finally:
        s.close()


def servidor_abierto():
    try:
        socket.create_connection(("127.0.0.1", int(R.propiedades().get("rcon.port", 25575))), timeout=2).close()
        return True
    except OSError:
        return False


def guardar_mundo():
    """save-all para que los .mca tengan lo último (como mucho cada 30 s). No cambia el mundo, solo lo escribe a disco."""
    if time.time() - _ultimo_guardado[0] < 30 or not servidor_abierto():
        return
    rcon("save-all")
    _ultimo_guardado[0] = time.time()
    time.sleep(1.5)


LECTURA = ("list", "data get ", "locate ", "time query ", "seed", "forceload query", "scoreboard players list",
           "scoreboard objectives list", "worldborder get", "save-all", "help", "bluemap")
BLUEMAP_PROHIBIDO = ("purge", "stop", "freeze", "unfreeze")


def es_lectura(cmd):
    c = cmd.strip().lstrip("/").lower()
    if c.startswith(("execute if ", "execute unless ")):
        return " run " not in c
    if c.startswith("gamerule "):
        return len(c.split()) == 2
    if c.startswith("bluemap"):
        return not any(p in c for p in BLUEMAP_PROHIBIDO)
    return c == "list" or c.startswith(LECTURA)


# --- Lectura de bloques desde los .mca ---


def carpeta_region():
    # 29-oct: también el Nether y el End (para ver el Castillo del Rey Demonio): NIEBLA_DIM = the_nether / the_end
    sub = {"the_nether": "DIM-1", "the_end": "DIM1"}.get(os.environ.get("NIEBLA_DIM", ""), "")
    return os.path.join(RAIZ, os.environ.get("NIEBLA_SRV", "servidor"), R.propiedades().get("level-name", "world"), sub, "region")


def t_usar_dimension(dimension="overworld"):
    """Qué dimensión leen las vistas desde los archivos (vista_cenital, isometrica, corte_lateral, bloques, buscar_problemas)."""
    if dimension in ("the_nether", "the_end"):
        os.environ["NIEBLA_DIM"] = dimension
    else:
        os.environ.pop("NIEBLA_DIM", None)
    return "Las vistas ahora leen: " + carpeta_region()


def decodificar_seccion(n_pal, data):
    """Índices de paleta (4096) de una sección, o None si toda la sección es el bloque 0 de la paleta."""
    if n_pal == 1 or not data:
        return None
    bits = max(4, (n_pal - 1).bit_length())
    por, mask = 64 // bits, (1 << bits) - 1
    if _np is not None:
        try:
            arr = _np.array(data, dtype=_np.int64).view(_np.uint64)
            idx = _np.empty(len(arr) * por, dtype=_np.int64)
            for k in range(por):
                idx[k::por] = ((arr >> _np.uint64(k * bits)) & _np.uint64(mask)).astype(_np.int64)
            return idx[:4096]
        except Exception:
            pass
    out = []
    for w in data:
        for _ in range(por):
            out.append(w & mask)
            w >>= bits
    return out[:4096]


class Mundo:
    """Bloques y entidades de bloque (cofres) leídos de los .mca, con caché por chunk."""

    def __init__(self, region=None):
        self.g = MA.GestorRegiones(region or carpeta_region())
        self.chunks = {}

    def chunk(self, cx, cz):
        k = (cx, cz)
        if k in self.chunks:
            return self.chunks[k]
        res = None
        datos = self.g._obtener_region(cx >> 5, cz >> 5)
        if datos:
            i = ((cx & 31) + ((cz & 31) << 5)) * 4
            off = ((datos[i] << 16) | (datos[i + 1] << 8) | datos[i + 2]) * 4096
            if off and off + 5 <= len(datos):
                n = struct.unpack_from(">I", datos, off)[0]
                tipo = datos[off + 4]
                bruto = datos[off + 5:off + 4 + n]
                try:
                    nbt = MA.leer_nbt(zlib.decompress(bruto) if tipo == 2 else gzip.decompress(bruto) if tipo == 1 else bruto)
                    secciones = {s.get("Y"): s for s in nbt.get("sections") or []}
                    res = (MA.ChunkCacheado(nbt), nbt.get("block_entities") or [], secciones)
                except Exception:
                    res = None
        self.chunks[k] = res
        return res

    def estado(self, x, y, z):
        """(nombre, propiedades) con TODAS las propiedades del bloque (cama: part/facing, farol: hanging, vela: lit...).
        bloque() solo guarda las de las puertas. Nombre None si el chunk no está generado."""
        ch = self.chunk(x >> 4, z >> 4)
        if ch is None:
            return None, {}
        bs = (ch[2].get(y >> 4) or {}).get("block_states") or {}
        pal = bs.get("palette")
        if not pal:
            return "air", {}
        data = bs.get("data")
        if len(pal) == 1 or not data:
            p = pal[0]
        else:
            bits = max(4, (len(pal) - 1).bit_length())
            por = 64 // bits
            i = ((y & 15) << 8) | ((z & 15) << 4) | (x & 15)
            j = (data[i // por] >> ((i % por) * bits)) & ((1 << bits) - 1)
            p = pal[j] if j < len(pal) else {"Name": "air"}
        nom = p.get("Name", "air")
        return (nom[10:] if nom.startswith("minecraft:") else nom), dict(p.get("Properties") or {})

    def escanear(self, x1, z1, x2, z2, pred, y_min=-64, y_max=320):
        """29-oct (banco_ojo): todos los bloques de la caja cuyo nombre cumple pred(nombre) -> [(x, y, z, nombre, props)].
        Lee la paleta de cada sección y solo decodifica las que tienen algo que buscar (rápido para hojas, flores, mods)."""
        x1, x2, z1, z2 = min(x1, x2), max(x1, x2), min(z1, z2), max(z1, z2)
        out = []
        for cx in range(x1 >> 4, (x2 >> 4) + 1):
            for cz in range(z1 >> 4, (z2 >> 4) + 1):
                ch = self.chunk(cx, cz)
                if ch is None:
                    continue
                for sy, sec in ch[2].items():
                    if sy is None or (sy << 4) + 15 < y_min or (sy << 4) > y_max:
                        continue
                    bs = sec.get("block_states") or {}
                    pal = bs.get("palette")
                    if not pal:
                        continue
                    nombres = [(p.get("Name", "air")[10:] if p.get("Name", "air").startswith("minecraft:") else p.get("Name", "air")) for p in pal]
                    hit = [i for i, n in enumerate(nombres) if pred(n)]
                    if not hit:
                        continue
                    idx = decodificar_seccion(len(pal), bs.get("data"))
                    if idx is None:
                        celdas = range(4096)
                        de = lambda i: 0  # noqa: E731
                    else:
                        if _np is not None:
                            celdas = _np.nonzero(_np.isin(idx, hit))[0].tolist()
                        else:
                            hs = set(hit)
                            celdas = [i for i in range(4096) if idx[i] in hs]
                        de = lambda i: int(idx[i])  # noqa: E731
                    for i in celdas:
                        x, z, y = (cx << 4) + (i & 15), (cz << 4) + ((i >> 4) & 15), (sy << 4) + (i >> 8)
                        if x1 <= x <= x2 and z1 <= z <= z2 and y_min <= y <= y_max:
                            j = de(i)
                            out.append((x, y, z, nombres[j], pal[j].get("Properties") or {}))
        return out

    def luz(self, x, y, z):
        """Luz de bloques (0-15, sin contar el sol) que el servidor guardó en el chunk. Con 0 aparecen monstruos."""
        ch = self.chunk(x >> 4, z >> 4)
        if ch is None:
            return None
        bl = (ch[2].get(y >> 4) or {}).get("BlockLight")
        if not bl:
            return 0
        i = ((y & 15) << 8) | ((z & 15) << 4) | (x & 15)
        v = bl[i >> 1] & 0xFF
        return (v >> 4) if i & 1 else (v & 15)

    def bloque(self, x, y, z):
        ch = self.chunk(x >> 4, z >> 4)
        if ch is None:
            return None  # chunk sin generar
        c = ch[0]
        sy = y >> 4
        if sy not in c.secciones_raw:
            return "air"
        sec = c.obtener_seccion(sy)
        return sec if isinstance(sec, str) else sec[((y & 15) << 8) | ((z & 15) << 4) | (x & 15)]

    def tope(self, x, z):
        ch = self.chunk(x >> 4, z >> 4)
        return (None, None) if ch is None else ch[0].bloque_mas_alto(x & 15, z & 15)

    def suelo(self, x, z):
        """(bloque, y) del terreno: el bloque más alto sin contar plantas, árboles ni adornos."""
        nom, y = self.tope(x, z)
        while nom is not None and (nom in MA.AIRE or es_planta(nom)) and y > -64:
            y -= 1
            nom = self.bloque(x, y, z)
        return nom, y

    def seres(self, x1, z1, x2, z2):
        """29-oct (ojo-vision): entidades de la carpeta entities/ (cuadros, marcos, soportes de armadura, aldeanos,
        animales, monstruos, objetos tirados), resumidas con ver_entidad(). Lo último que el servidor guardó."""
        if not hasattr(self, "ge"):
            self.ge = MA.GestorRegiones(os.path.join(os.path.dirname(self.g.carpeta), "entities"))
            self.chunks_e = {}
        out = []
        for cx in range(min(x1, x2) >> 4, (max(x1, x2) >> 4) + 1):
            for cz in range(min(z1, z2) >> 4, (max(z1, z2) >> 4) + 1):
                if (cx, cz) not in self.chunks_e:
                    self.chunks_e[(cx, cz)] = leer_chunk(self.ge, cx, cz).get("Entities") or []
                for e in self.chunks_e[(cx, cz)]:
                    v = ver_entidad(e)
                    if v and min(x1, x2) <= v["x"] <= max(x1, x2) and min(z1, z2) <= v["z"] <= max(z1, z2):
                        out.append(v)
        return out

    def entidades(self, x1, z1, x2, z2):
        out = []
        for cx in range(min(x1, x2) >> 4, (max(x1, x2) >> 4) + 1):
            for cz in range(min(z1, z2) >> 4, (max(z1, z2) >> 4) + 1):
                ch = self.chunk(cx, cz)
                if ch:
                    out += [e for e in ch[1] if min(x1, x2) <= e.get("x", 0) <= max(x1, x2) and min(z1, z2) <= e.get("z", 0) <= max(z1, z2)]
        return out


def leer_chunk(gestor, cx, cz):
    """NBT de un chunk de cualquier carpeta de .mca (region/ o entities/). {} si no existe."""
    datos = gestor._obtener_region(cx >> 5, cz >> 5)
    if not datos:
        return {}
    i = ((cx & 31) + ((cz & 31) << 5)) * 4
    off = ((datos[i] << 16) | (datos[i + 1] << 8) | datos[i + 2]) * 4096
    if not off or off + 5 > len(datos):
        return {}
    n = struct.unpack_from(">I", datos, off)[0]
    tipo, bruto = datos[off + 4], datos[off + 5:off + 4 + n]
    try:
        return MA.leer_nbt(zlib.decompress(bruto) if tipo == 2 else gzip.decompress(bruto) if tipo == 1 else bruto) or {}
    except Exception:
        return {}


def _texto_nombre(n):
    """CustomName viene como JSON de texto ('{"text":"Bruno, el Herrero","color":"red"}') o como texto suelto."""
    if not n:
        return None
    if isinstance(n, str) and n.startswith(("{", "[", '"')):
        try:
            j = json.loads(n)
            if isinstance(j, dict):
                return (j.get("text") or "") + "".join(x.get("text", "") for x in j.get("extra") or [] if isinstance(x, dict)) or None
            if isinstance(j, list):
                return "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in j) or None
            return str(j)
        except ValueError:
            pass
    return str(n)


MONSTRUO = ("zombie", "skeleton", "creeper", "spider", "vindicator", "evoker", "pillager", "illusioner", "witch", "enderman",
            "ravager", "vex", "phantom", "drowned", "husk", "stray", "wither", "blaze", "ghast", "piglin", "hoglin", "slime",
            "warden", "silverfish", "endermite", "guardian", "shulker", "basher", "provoker", "necromancer", "archivist",
            "invoker", "sorcerer", "marauder", "mimic", "weeping_angel", "wraith", "nightmare", "ghoul", "revenant", "lich",
            "maledictus", "ignis", "harbinger", "leviathan", "netherite_monstrosity", "wroughtnaut", "frostmaw", "umvuthi",
            "naga", "hydra", "dragon", "cyclops", "gorgon", "siren", "troll", "dread", "cave_dweller", "herobrine", "hollow",
            "centipede", "devourer", "guzzler", "born_in_chaos", "mutant", "sons_of_sins", "graveyard:")
VEHICULO = ("minecart", "boat", "raft")


def ver_entidad(e):
    """Resumen de una entidad guardada: tipo (cuadro, marco, armadura, aldeano, animal, monstruo, objeto, vehiculo,
    display), posición en bloques, nombre y lo que lleva. None para marcadores invisibles y partes de jefes."""
    eid = str(e.get("id", ""))
    corto = eid.split(":")[-1]
    pos = e.get("Pos") or [0, 0, 0]
    x, y, z = (e["TileX"], e["TileY"], e["TileZ"]) if "TileX" in e else (math.floor(pos[0]), math.floor(pos[1]), math.floor(pos[2]))
    v = {"id": eid, "x": x, "y": y, "z": z, "nombre": _texto_nombre(e.get("CustomName")), "detalle": "", "tags": e.get("Tags") or []}
    item = lambda d: (d or {}).get("id", "").replace("minecraft:", "")  # noqa: E731
    if corto in ("marker", "interaction", "area_effect_cloud", "experience_orb", "lightning_bolt") or "_part" in corto:
        return None
    if corto == "painting":
        v["tipo"], v["detalle"] = "cuadro", str(e.get("variant") or e.get("Motive") or "").replace("minecraft:", "")
    elif corto in ("item_frame", "glow_item_frame"):
        v["tipo"], v["detalle"] = "marco", item(e.get("Item")) or "vacío"
    elif corto == "armor_stand":
        cosas = [item(i) for i in (e.get("ArmorItems") or []) + (e.get("HandItems") or []) if item(i)]
        v["tipo"], v["detalle"] = "armadura", ", ".join(cosas) or "sin nada puesto"
    elif "villager" in corto and "zombie" not in corto:
        prof = str((e.get("VillagerData") or {}).get("profession", "")).split(":")[-1]
        v["tipo"], v["detalle"] = "aldeano", "sin oficio" if prof in ("none", "") else prof
    elif corto == "item":
        it = e.get("Item") or {}
        v["tipo"], v["detalle"] = "objeto", "%s×%d" % (item(it), it.get("count", 1))
    elif corto.endswith("_display"):
        v["tipo"] = "display"
        v["detalle"] = item(e.get("item")) or str((e.get("block_state") or {}).get("Name", "")).replace("minecraft:", "")
    elif any(k in corto for k in VEHICULO):
        v["tipo"] = "vehiculo"
    elif any(k in eid for k in MONSTRUO):
        v["tipo"] = "monstruo"
    elif "Health" in e:
        v["tipo"] = "animal"
    else:
        v["tipo"] = "otro"
    if v["tipo"] in ("monstruo", "animal") and "Health" in e:  # 4-nov (ronda 4): vida y lo que lleva puesto o en la mano
        lleva = [item(i) for i in (e.get("HandItems") or []) + (e.get("ArmorItems") or []) if item(i)]
        v["detalle"] = "vida %d%s%s" % (round(float(e.get("Health") or 0)), (", lleva " + ", ".join(lleva)) if lleva else "",
                                        ", no se va (PersistenceRequired)" if e.get("PersistenceRequired") else "")
    return v


PLANTA = ("leaves", "_log", "_wood", "stem", "hyphae", "flower", "fern", "vine", "sapling", "bush", "mushroom", "tall_grass",
          "short_grass", "sugar_cane", "lily", "carpet", "torch", "lantern", "chain", "cactus", "bamboo", "kelp", "seagrass",
          "berry", "propagule", "roots", "dripleaf", "azalea", "tulip", "orchid", "allium", "bluet", "daisy", "poppy",
          "dandelion", "cornflower", "lily_of_the_valley", "rose", "peony", "lilac", "sunflower", "pitcher", "glow_lichen",
          "moss_carpet", "pink_petals", "cobweb", "pumpkin", "melon", "hay_block", "bee_nest")
TIERRA = {"dirt", "grass_block", "coarse_dirt", "rooted_dirt", "podzol", "mud", "mycelium", "farmland", "dirt_path",
          "gravel", "sand", "red_sand", "clay", "packed_mud"}
NATURAL = TIERRA | {"stone", "andesite", "diorite", "granite", "deepslate", "tuff", "calcite", "terracotta", "water",
                    "snow_block", "sandstone", "red_sandstone", "moss_block"}


ESTRUCTURA = ("planks", "_log", "_wood", "stairs", "slab", "brick", "stone", "glass", "wool", "_wall", "cobble", "terracotta",
              "concrete", "door", "dirt", "grass_block", "vine", "leaves", "sandstone", "deepslate", "tuff", "andesite", "diorite",
              "granite", "calcite", "mud", "beam", "pillar", "plaster", "gravel", "sand", "torch", "ladder", "fence")


def es_planta(n):
    return n != "grass_block" and (n == "snow" or any(k in n for k in PLANTA))


def es_estructura(n):
    """Paredes, pisos, techos, luces de pared y escaleras de mano: lo que no cuenta como mueble."""
    return n is None or n in MA.AIRE or any(k in n for k in ESTRUCTURA)


# --- Imágenes ---


def png_base64(ruta):
    return base64.b64encode(open(ruta, "rb").read()).decode("ascii")


def nombre_salida(prefijo):
    os.makedirs(SALIDA, exist_ok=True)
    ruta = os.path.join(SALIDA, "%s_%s.png" % (prefijo, time.strftime("%Y%m%d_%H%M%S")))
    k = 2
    while os.path.exists(ruta):  # varias imágenes en el mismo segundo: no se pisan
        ruta = os.path.join(SALIDA, "%s_%s_%d.png" % (prefijo, time.strftime("%Y%m%d_%H%M%S"), k))
        k += 1
    return ruta


CIELO, VACIO, MARCA = (205, 225, 245), (25, 25, 30), (220, 30, 30)

# --- Herramientas ---


def t_bloques(x, y, z, radio=2):
    radio = max(0, min(int(radio), 5))
    guardar_mundo()
    m = Mundo()
    letras, leyenda, capas = {}, [], []
    simbolos = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789#$%&*+=?@"
    for yy in range(y + radio, y - radio - 1, -1):
        filas = []
        for zz in range(z - radio, z + radio + 1):
            fila = ""
            for xx in range(x - radio, x + radio + 1):
                b = m.bloque(xx, yy, zz)
                if b is None:
                    fila += "?"
                elif b in MA.AIRE:
                    fila += "."
                else:
                    if b not in letras:
                        letras[b] = simbolos[len(letras) % len(simbolos)]
                        leyenda.append("%s = %s" % (letras[b], b))
                    fila += letras[b]
            filas.append(fila)
        capas.append("y=%d%s\n%s" % (yy, "  (centro)" if yy == y else "", "\n".join(filas)))
    return ("Bloques de (%d,%d,%d) a (%d,%d,%d). Cada capa: filas = z de norte (arriba) a sur, columnas = x de oeste a este.\n"
            ". = aire, ? = chunk sin generar\n%s\n\n%s") % (x - radio, y - radio, z - radio, x + radio, y + radio, z + radio,
                                                          "\n".join(leyenda), "\n\n".join(capas))


def t_corte_lateral(x1, z1, x2, z2, y_min=None, y_max=None, escala=5):
    guardar_mundo()
    m = Mundo()
    n = max(abs(x2 - x1), abs(z2 - z1))
    pts = [(round(x1 + (x2 - x1) * i / max(n, 1)), round(z1 + (z2 - z1) * i / max(n, 1))) for i in range(n + 1)]
    suelos = [m.suelo(px, pz) for px, pz in pts]
    alturas = [s[1] for s in suelos if s[1] is not None]
    if not alturas:
        return "No hay chunks generados en esa línea.", None
    y_min = min(alturas) - 8 if y_min is None else y_min
    y_max = max(m.tope(px, pz)[1] or 0 for px, pz in pts) + 3 if y_max is None else y_max
    y_max = min(y_max, y_min + 160)
    img = []
    for yy in range(y_max, y_min - 1, -1):
        fila = []
        for i, (px, pz) in enumerate(pts):
            b = m.bloque(px, yy, pz)
            c = VACIO if b is None else CIELO if b in MA.AIRE else MA.color_de_bloque(b)
            if yy % 10 == 0 and b in MA.AIRE:
                c = (185, 205, 225)  # rayas de altura cada 10 bloques
            if i % 10 == 0 and b in MA.AIRE:
                c = (175, 195, 215)  # rayas verticales cada 10 bloques de recorrido
            fila.append(c)
        img.append(fila)
    ruta = nombre_salida("corte")
    MA.escribir_png(ruta, img, escala=max(1, min(int(escala), 10)))
    # escalones y paredes: saltos de 3 o más entre columnas vecinas
    saltos = []
    for i in range(1, len(pts)):
        a, b = suelos[i - 1][1], suelos[i][1]
        if a is not None and b is not None and abs(a - b) >= 3:
            alto = pts[i - 1] if a > b else pts[i]
            cara = [m.bloque(alto[0], yy, alto[1]) for yy in range(min(a, b) + 1, max(a, b) + 1)]
            tierra = sum(c in TIERRA for c in cara)
            saltos.append("  paso %d (%d, %d): salto de %d bloques (%d→%d), cara: %d de tierra, %s" % (
                i, pts[i][0], pts[i][1], abs(a - b), a, b, tierra, ", ".join(sorted(set(cara)))[:120]))
    txt = ("Corte de (%d, %d) a (%d, %d): %d columnas, y de %d (abajo) a %d (arriba). Escala %d px por bloque.\n"
           "Izquierda = inicio. Rayas claras cada 10 bloques (altura y recorrido). Negro = sin generar.\n"
           "Suelo: mín %d, máx %d.\nSaltos de 3+ bloques entre columnas vecinas: %d\n%s\nArchivo: %s") % (
        x1, z1, x2, z2, len(pts), y_min, y_max, escala, min(alturas), max(alturas), len(saltos), "\n".join(saltos[:40]), ruta)
    return txt, ruta


def t_vista_cenital(x1, z1, x2, z2, escala=2, marcas=None):
    if abs(x2 - x1) > 600 or abs(z2 - z1) > 600:
        return "Zona demasiado grande (máximo 600 x 600 bloques).", None
    guardar_mundo()
    ruta = nombre_salida("cenital")
    arch = None
    if marcas:
        arch = ruta.replace(".png", "_marcas.json")
        json.dump([{"x": mk[0], "z": mk[1], "color": [255, 0, 0]} for mk in marcas], open(arch, "w"))
    MA.generar_mapa(carpeta_region(), x1, z1, x2, z2, ruta, escala=max(1, min(int(escala), 6)), archivo_marcas=arch)
    return ("Vista desde arriba de (%d, %d) a (%d, %d). Norte arriba, este a la derecha. Más claro = más alto que el vecino "
            "del norte.\nArchivo: %s") % (x1, z1, x2, z2, ruta), ruta


def cajas_casas():
    """Cajas de los edificios del plano del pueblo, en coordenadas absolutas. [] si no se puede leer el plano."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            import validar_plan as V
        plan = json.load(open(os.path.join(RAIZ, "planos", "plan_aldea.json"), encoding="utf-8"))
        return [(n, {"x0": X0 + c["x0"], "x1": X0 + c["x1"], "z0": Z0 + c["z0"], "z1": Z0 + c["z1"]})
                for n, c in V.cajas_del_plan(plan) if c]
    except Exception:
        return []


# 29-oct (ojo-vision): tamaño de los adornos del plano (medio ancho en x, medio fondo en z, ya con 1 de holgura). Las
# carretas, carros, puestos y bancas llevan escaleras, losas y toldos en el aire a propósito: no son caminos ni cosas
# flotando (falsas alarmas del 28-oct).
ADORNO_TAM = {"carreta": (5, 3), "carro": (4, 3), "puesto": (4, 4), "banca": (2, 2), "pozo": (3, 3), "barriles": (2, 2),
              "heno": (2, 2), "farol": (1, 1), "huerto": (4, 4), "campo": (7, 7), "jardin": (3, 3)}


def cajas_adornos(tipos=None):
    """Cajas de los adornos del plano (carretas, puestos, bancas...) en coordenadas absolutas."""
    try:
        plan = json.load(open(os.path.join(RAIZ, "planos", "plan_aldea.json"), encoding="utf-8"))
    except Exception:
        return []
    out = []
    for a in plan.get("adornos") or []:
        t = a.get("tipo")
        if t not in ADORNO_TAM or (tipos and t not in tipos):
            continue
        dx, dz = ADORNO_TAM[t]
        x, z = X0 + a["x"], Z0 + a["z"]
        out.append(("adorno " + t, {"x0": x - dx, "x1": x + dx, "z0": z - dz, "z1": z + dz}))
    return out


def dentro(cajas, x, z, margen=0):
    return any(c["x0"] - margen <= x <= c["x1"] + margen and c["z0"] - margen <= z <= c["z1"] + margen for _, c in cajas)


def grupos(celdas, salto=1):
    """Agrupa celdas (x, z) vecinas (distancia <= salto). Devuelve listas."""
    resto = set(celdas)
    out = []
    while resto:
        pila = [resto.pop()]
        g = []
        while pila:
            c = pila.pop()
            g.append(c)
            for dx in range(-salto, salto + 1):
                for dz in range(-salto, salto + 1):
                    v = (c[0] + dx, c[1] + dz)
                    if v in resto:
                        resto.remove(v)
                        pila.append(v)
        out.append(g)
    return sorted(out, key=len, reverse=True)


def resumen_grupo(g, alt=None):
    xs, zs = [c[0] for c in g], [c[1] for c in g]
    cx, cz = sorted(g, key=lambda c: (c[0] - sum(xs) / len(xs)) ** 2 + (c[1] - sum(zs) / len(zs)) ** 2)[0]
    y = "" if alt is None else ", %d" % alt.get((cx, cz), 0)
    return "(%d%s, %d) caja x %d..%d z %d..%d, %d bloques" % (cx, y, cz, min(xs), max(xs), min(zs), max(zs), len(g))


def sostenido(m, x, y, z, limite=2500):
    """True si el bloque (x, y, z) se apoya, a través de bloques pegados (caras), en algo que baja hasta el suelo:
    llega 4 o más capas más abajo (pasó el hueco de aire) o la pieza es tan grande que es una construcción."""
    vistos, pila = {(x, y, z)}, [(x, y, z)]
    while pila:
        a, b, c = pila.pop()
        if b <= y - 4 or len(vistos) > limite:
            return True
        for v in ((a + 1, b, c), (a - 1, b, c), (a, b + 1, c), (a, b - 1, c), (a, b, c + 1), (a, b, c - 1)):
            if v in vistos or abs(v[0] - x) > 24 or abs(v[2] - z) > 24 or v[1] > y + 12:
                continue
            n = m.bloque(*v)
            if n is None or n in MA.AIRE or n.split("[")[0] in ("water", "lava"):
                continue
            vistos.add(v)
            pila.append(v)
    return False


TIPOS = ("paredes", "bordes_rectos", "flotantes", "hojas", "pilares", "cofres", "escaleras", "pisos", "puertas", "zanjas",
         "caminos_hundidos", "enterradas", "adornos_enterrados", "disenos_incompletos", "rectas", "alegres", "entidades", "mods", "restos",
         "accesos", "hoyos", "encierros")

TITULOS = {  # una línea corta por tipo (el texto largo de cada uno va solo con detalle=true)
    "paredes": "Paredes de tierra de 3+ bloques fuera de las casas",
    "bordes_rectos": "Bordes de terreno rectos de 8+ bloques (estilo)",
    "flotantes": "Cosas flotando de verdad (bloques sueltos, letreros, faroles y estandartes sin apoyo)",
    "hojas": "Hojas o árboles sin tronco ni apoyo (copas, hojas que se caen solas)",
    "pilares": "Pilares o pinchos de terreno (2+ más altos que todo alrededor)",
    "cofres": "Cofres y barriles vacíos / casas sin cofre",
    "escaleras": "Caminos de escaleras",
    "pisos": "Cuartos vacíos o pobres",
    "puertas": "Puertas que no dejan entrar",
    "zanjas": "Zanjas y hoyos",
    "caminos_hundidos": "Caminos hundidos entre paredes de tierra",
    "enterradas": "Casas enterradas",
    "adornos_enterrados": "Adornos del plano tapados, enterrados o pisados por otra cosa",
    "disenos_incompletos": "Adornos con el diseño incompleto (falta más del 20 %)",
    "rectas": "Bordes de camino rectos de 8+ bloques (estilo)",
    "alegres": "Colores o plantas alegres (cerezo, lana rosa, flores) que no estaban en el terreno natural",
    "entidades": "Entidades raras (monstruos en el pueblo, armaduras flotando, seres dentro de bloques)",
    "mods": "Bloques de mods sueltos fuera de casas y adornos",
    "restos": "Restos de construcción (bloques técnicos, camas o plantas altas partidas, antorchas de pared sin pared)",
    "accesos": "Casas sin acceso caminable desde la plaza (saltos de más de 1 bloque, puertas sin suelo)",
    "hoyos": "Hoyos trampa (hueco de 1x1 o 1x2 y 2+ de hondo en el suelo del pueblo)",
    "encierros": "Encierros: sitios a los que se cae desde la plaza y de los que no se vuelve (en modo aventura no se puede picar)",
}

ORIGINAL = os.path.join(RAIZ, "servidor", "niebla_original", "region")  # terreno natural (misma semilla), 28-oct-2026
CAMINO = {"dirt_path", "gravel", "cobblestone", "mossy_cobblestone", "coarse_dirt", "packed_mud", "mud_bricks", "andesite",
          "polished_andesite", "stone_bricks", "mossy_stone_bricks", "cracked_stone_bricks", "podzol"}
V6 = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
V4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
MOD_PERMITIDO = ("mcwpaths:", "lootr:")  # caminos de mods (suelo) y cofres de botín de estructuras: no son «sueltos»
AVES = ("magpie", "raven", "crow", "owl", "bat", "ambient")  # los «graveyard:» que son pájaros de adorno, no monstruos
MOD_EXTERIOR_OK = ("lantern", "lamp", "torch", "sign", "banner", "flag", "rope", "chain", "candle", "pot", "ash", "bell", "hanging")


def _it(tipo, txt, x, y, z, peso=1.0, pat=None, bbox=None, n=1, c=""):
    """Un aviso del Ojo: tipo, texto, posición, peso (para elegir los peores), patrón (para agrupar) y caja (x0, z0, x1, z1)."""
    return {"tipo": tipo, "txt": txt, "x": x, "y": y, "z": z, "peso": round(float(peso), 1), "pat": pat or tipo,
            "bbox": list(bbox) if bbox else [x, z, x, z], "n": n, "c": c}


def _bb(g):
    xs, zs = [c[0] for c in g], [c[1] for c in g]
    return (min(xs), min(zs), max(xs), max(zs))


def es_camino(n):
    return n in CAMINO or (n or "").startswith("mcwpaths:")


def _es_tronco(n):
    return n.endswith(("_log", "_wood", "_stem", "_hyphae"))


def _es_hoja(n):
    return n.endswith("_leaves")


def razon_alegre(nombre):
    """Por qué un bloque es «alegre» (cerezo rosado, color vivo, flor de fiesta) según la tabla DESENTONA, o None."""
    corto = nombre.split("[")[0].split(":")[-1]
    if any(k in corto for k in NO_DESENTONA) or corto.endswith("_candle"):
        return None
    for claves, por, grav in DESENTONA:
        if grav != "alegre":
            continue
        if any((corto.startswith(k) or "_" + k in corto) if k.endswith("_") else k in corto for k in claves):
            if por == "color alegre" and categoria(nombre.split("[")[0]) == "muro" and not _tiene(corto, "wool", "bed", "banner", "carpet", "concrete"):
                return None  # un ladrillo o madera amarilla del muro no se marca
            return por
    return None


def arboles_rotos(m, x1, z1, x2, z2, mo=None):
    """Árboles que quedaron sin tronco o sin apoyo (29-oct, banco_ojo; el Ojo decía «flotantes: 0» con 526 hojas en el aire).
    Junta troncos y hojas pegados (6 caras). Una pieza está SUELTA si ningún bloque suyo se apoya en algo sólido (abajo o
    al lado). Además, las hojas que no son persistentes y no tienen tronco a 6 bloques de hojas (distance=7) se caen solas.
    Devuelve (sueltas, caeran): listas de dicts con celdas, caja y conteos."""
    ancho = 8
    cel = {}
    for x, y, z, n, p in m.escanear(x1 - ancho, z1 - ancho, x2 + ancho, z2 + ancho, lambda n: _es_hoja(n) or _es_tronco(n)):
        cel[(x, y, z)] = "G" if _es_tronco(n) else ("P" if p.get("persistent") == "true" else "N")
    dist, fr = {}, [c for c, t in cel.items() if t == "G"]
    for c in fr:
        dist[c] = 0
    d = 0
    while fr and d < 6:
        d += 1
        nx = []
        for (x, y, z) in fr:
            for a, b, c in V6:
                v = (x + a, y + b, z + c)
                if v in cel and v not in dist and cel[v] != "G":
                    dist[v] = d
                    nx.append(v)
        fr = nx

    def solido(b):
        return bool(b) and b not in MA.AIRE and b.split("[")[0] not in ("water", "lava") and not es_planta(b)

    vistos, sueltas, en_suelta = set(), [], set()
    for c0 in cel:
        if c0 in vistos:
            continue
        comp, pila = [], [c0]
        vistos.add(c0)
        while pila:
            c = pila.pop()
            comp.append(c)
            for a, b, e in V6:
                v = (c[0] + a, c[1] + b, c[2] + e)
                if v in cel and v not in vistos:
                    vistos.add(v)
                    pila.append(v)
        if any(c[0] <= x1 - ancho + 2 or c[0] >= x2 + ancho - 2 or c[2] <= z1 - ancho + 2 or c[2] >= z2 + ancho - 2 for c in comp):
            continue  # toca el borde de lo leído: no se sabe si algo más lejos la sostiene
        apoyada = False
        for (x, y, z) in comp:
            if (x, y - 1, z) not in cel and solido(m.bloque(x, y - 1, z)):
                apoyada = True
                break
        if not apoyada and any(cel[c] == "G" for c in comp):  # un tronco metido en un estanque se apoya en el fondo
            apoyada = any((x, y - 1, z) not in cel and (m.bloque(x, y - 1, z) or "").split("[")[0] == "water" for (x, y, z) in comp)
        if not apoyada:
            for (x, y, z) in comp:
                if any((x + a, y, z + b) not in cel and solido(m.bloque(x + a, y, z + b)) for a, b in V4):
                    apoyada = True
                    break
        if apoyada:
            continue
        if mo is not None and sum(1 for c in comp if mo.bloque(*c) == m.bloque(*c)) >= 0.9 * len(comp):
            continue  # ya era así en el terreno natural
        ys = [c[1] for c in comp]
        sueltas.append({"celdas": comp, "caja": _bb([(c[0], c[2]) for c in comp]), "y": (min(ys), max(ys)),
                        "troncos": sum(1 for c in comp if cel[c] == "G"), "hojas": sum(1 for c in comp if cel[c] != "G"),
                        "persist": sum(1 for c in comp if cel[c] == "P")})
        en_suelta.update(comp)
    caen = {c for c, t in cel.items() if t == "N" and c not in dist and c not in en_suelta and x1 <= c[0] <= x2 and z1 <= c[2] <= z2}
    if mo is not None:
        caen = {c for c in caen if mo.bloque(*c) != m.bloque(*c)}
    caeran, vis = [], set()
    for c0 in caen:
        if c0 in vis:
            continue
        comp, pila = [], [c0]
        vis.add(c0)
        while pila:
            c = pila.pop()
            comp.append(c)
            for a, b, e in V6:
                v = (c[0] + a, c[1] + b, c[2] + e)
                if v in caen and v not in vis:
                    vis.add(v)
                    pila.append(v)
        if len(comp) >= 3:
            ys = [c[1] for c in comp]
            caeran.append({"celdas": comp, "caja": _bb([(c[0], c[2]) for c in comp]), "y": (min(ys), max(ys)), "troncos": 0,
                           "hojas": len(comp), "persist": 0})
    return sueltas, caeran


def diseno_de_adorno(a):
    """(nombre del diseño, mira) con el que el generador construye un adorno del plano, o None si no tiene diseño."""
    if a.get("diseno"):
        return a["diseno"], a.get("mira", "N").upper()
    t = a.get("tipo")
    if t == "puesto":
        c = a.get("color", "red")
        cs = ("red", "blue", "yellow", "green")
        return ("puesto_mercado_rojo" if c in ("red", "yellow") else "puesto_mercado_verde"), ("NSEW"[cs.index(c)] if c in cs else "N")
    if t == "banca":
        return "banca_jardin", a.get("mira", "N").upper()
    return {"carro": ("carreta_flores", "N"), "huerto": ("rincon_huerto", "N"), "pozo": ("pozo_techado", "N")}.get(t)


def bloques_esperados(nombre, mira):
    """Los bloques que el diseño pone, con posición relativa a (x, z del adorno, y del suelo): [(dx, dy, dz, nombre)], (ancho, fondo, alto)."""
    import disenos as DS
    w, d = DS.tamano(nombre, mira)
    ox, oz = -(w // 2), -(d // 2)
    out = []
    for l in DS.comandos(nombre, mira):
        mm = re.match(r"setblock ~(-?\d+) ~(-?\d+) ~(-?\d+) (\S+)", l)
        if not mm:
            continue
        nom = mm.group(4).split("[")[0]
        nom = nom[10:] if nom.startswith("minecraft:") else nom
        if nom not in MA.AIRE:
            out.append((int(mm.group(1)) + ox, int(mm.group(2)), int(mm.group(3)) + oz, nom))
    return out, (w, d, DS.D[nombre]["tam"][1])


def revisar_adorno(m, a, cajas, centro_abs=None):
    """Compara un adorno del plano con lo construido. Devuelve dict: estado (ok / incompleto / ausente), cobertura (0..1),
    y0, faltan (por capa), intrusos (bloques ajenos dentro del volumen del adorno) y reemplazados (bloques del diseño que el
    terreno se comió)."""
    dm = diseno_de_adorno(a)
    if dm is None:
        return None
    esp, (w, d, alto) = bloques_esperados(*dm)
    ax, az = X0 + a["x"], Z0 + a["z"]
    ox, oz = -(w // 2), -(d // 2)
    anillo = [m.suelo(ax + ox + i, az + oz + j)[1] for i in range(-1, w + 1) for j in range(-1, d + 1)
              if i in (-1, w) or j in (-1, d)]
    anillo = sorted(y for y in anillo if y is not None)
    if not anillo:
        return {"estado": "sin_chunk"}
    est = anillo[len(anillo) // 2]
    mejor = (-1, est)
    for y0 in sorted(range(est - 4, est + 5), key=lambda v: abs(v - est)):
        ok = sum(1 for dx, dy, dz, n in esp if (m.bloque(ax + dx, y0 + dy, az + dz) or "").split("[")[0] == n)
        if ok > mejor[0]:
            mejor = (ok, y0)
    ok, y0 = mejor
    total = len(esp)
    cob = ok / max(1, total)
    r = {"estado": "ok" if cob >= 0.8 else ("incompleto" if cob >= 0.2 else "ausente"), "cob": cob, "ok": ok, "total": total, "y0": y0,
         "dm": dm, "ax": ax, "az": az, "w": w, "d": d, "alto": alto, "faltan": {}, "intrusos": [], "reemplazados": 0, "solapa": None}
    puestos = {(dx, dy, dz): n for dx, dy, dz, n in esp}
    falt_n = {}
    for dx, dy, dz, n in esp:
        b = (m.bloque(ax + dx, y0 + dy, az + dz) or "").split("[")[0]
        if b != n:
            r["faltan"][dy] = r["faltan"].get(dy, 0) + 1
            falt_n[n] = falt_n.get(n, 0) + 1
            if dy >= 1 and (b in NATURAL or b in TIERRA):
                r["reemplazados"] += 1
    r["faltan_nombres"] = sorted(falt_n.items(), key=lambda t: -t[1])[:3]
    if r["estado"] != "ausente":
        for i in range(w):
            for j in range(d):
                x, z = ax + ox + i, az + oz + j
                for dy in range(1, alto + 5):
                    if (ox + i, dy, oz + j) in puestos:
                        continue
                    b = m.bloque(x, y0 + dy, z)
                    if b and b not in MA.AIRE and b.split("[")[0] not in ("water", "lava") and not es_planta(b):
                        r["intrusos"].append((x, y0 + dy, z, b.split("[")[0]))
    for n_, c in cajas:  # el plano puso el adorno encima de una casa
        if n_ != "parque" and not (ax + ox + w - 1 < c["x0"] or ax + ox > c["x1"] or az + oz + d - 1 < c["z0"] or az + oz > c["z1"]):
            r["solapa"] = n_
            break
    return r


def revisar_alegres(m, mo, x1, z1, x2, z2):
    """Bloques alegres (tabla DESENTONA) que NO estaban en el terreno natural. Devuelve avisos agrupados por nombre y cercanía."""
    por = {}

    def pred(n):
        if n not in por:
            por[n] = razon_alegre(n)
        return por[n] is not None
    hallados = m.escanear(x1, z1, x2, z2, pred)
    if mo is not None and hallados:
        orig = {(x, y, z) for x, y, z, n, p in mo.escanear(x1, z1, x2, z2, pred)}
        hallados = [h for h in hallados if (h[0], h[1], h[2]) not in orig]
    por_cat = {}
    for x, y, z, n, p in hallados:
        por_cat.setdefault(por[n], []).append((x, y, z, n))
    out = []
    for cat, pts in por_cat.items():
        for g in grupos([(x, z) for x, y, z, n in pts], 10):
            gs = set(g)
            en = [c for c in pts if (c[0], c[2]) in gs]
            cuenta = {}
            for c in en:
                cuenta[c[3].split(":")[-1]] = cuenta.get(c[3].split(":")[-1], 0) + 1
            top = ", ".join("%s×%d" % t for t in sorted(cuenta.items(), key=lambda t: -t[1])[:3])
            ej = en[0]
            out.append(_it("alegres", "(%d, %d, %d) %s: %d bloques (%s)" % (ej[0], ej[1], ej[2], cat, len(en), top), ej[0], ej[1], ej[2],
                           peso=len(en) * (3 if cat.startswith("madera") else 1), pat=cat, bbox=_bb(g), n=len(en), c=top))
    return out


def piezas_del_plano(centro_x=X0, centro_z=Z0, radio=150):
    """30-oct: las piezas del plano con su caja real, la puerta (borde de la caja hacia donde mira) y la dirección, en
    coordenadas absolutas: [{nombre, caja, puerta=(x, z), mira, etapa}]. [] si no se puede leer el plano."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            import reubicar_plan as RP
        plan = json.load(open(os.path.join(RAIZ, "planos", "plan_aldea.json"), encoding="utf-8"))
        out = []
        caminos_de = {}  # índice de pieza -> centros (x, z) absolutos del camino del plano que sale de su puerta
        for cam in plan.get("caminos", []):
            q = str(cam.get("para", "")).split(":")
            if len(q) == 2 and q[0].isdigit():
                caminos_de[int(q[0])] = [(X0 + a, Z0 + b) for a, b in cam["puntos"]]
        for k, p in enumerate(plan["piezas"]):
            with contextlib.redirect_stdout(io.StringIO()):
                c = RP.caja(p)
            caja = {"x0": X0 + c["x0"], "x1": X0 + c["x1"], "z0": Z0 + c["z0"], "z1": Z0 + c["z1"]}
            if abs((caja["x0"] + caja["x1"]) / 2 - centro_x) > radio or abs((caja["z0"] + caja["z1"]) / 2 - centro_z) > radio:
                continue
            out.append({"nombre": "%s (%d,%d)" % (p["plantilla"], X0 + p["x"], Z0 + p["z"]), "caja": caja, "puerta": (X0 + p["x"], Z0 + p["z"]),
                        "mira": p.get("mira", "S"), "etapa": p.get("etapa", 0), "camino": caminos_de.get(k)})
        return out
    except Exception:
        return []


def auditar(centro_x=X0, centro_z=Z0, radio=150, tipos=None, solo_pueblo=False, adornos_extra=None, cajas_extra=None, piezas_extra=None, mundo=None):
    """Núcleo de buscar_problemas: devuelve {"items": {tipo: [aviso]}, "seg": segundos, "sin_plano": bool, ...}.
    adornos_extra = adornos que no están en el plano (formato del plano, x y z relativos a la fogata): lo usa banco_ojo.
    piezas_extra = piezas sueltas para accesos ({nombre, caja, puerta, mira, red}); mundo = un Mundo ya armado (el sintético del
    banco, sin archivos ni servidor)."""
    tipos = [t for t in (tipos or TIPOS) if t in TIPOS]
    radio = max(8, min(int(radio), 300))  # 30-oct: antes 220; Sergio pidió medir más zona
    if mundo is None:
        guardar_mundo()
    m = mundo if mundo is not None else Mundo()
    natural = None
    mo = Mundo(ORIGINAL) if (mundo is None and os.path.isdir(ORIGINAL)) else None
    if solo_pueblo and mo is not None:  # columnas idénticas al terreno natural: no son problema del pueblo
        natural = set()
        for x in range(centro_x - radio - 2, centro_x + radio + 3):
            for z in range(centro_z - radio - 2, centro_z + radio + 3):
                if mo.suelo(x, z) == m.suelo(x, z):
                    natural.add((x, z))
    es_nat = (lambda c: all((c[0] + a, c[1] + b) in natural for a in (-1, 0, 1) for b in (-1, 0, 1))) if natural is not None else (lambda c: False)
    cajas = cajas_casas() + list(cajas_extra or [])
    t0 = time.time()
    x1, x2, z1, z2 = centro_x - radio, centro_x + radio, centro_z - radio, centro_z + radio
    alt, sup = {}, {}
    for x in range(x1 - 1, x2 + 2):
        for z in range(z1 - 1, z2 + 2):
            nom, y = m.suelo(x, z)
            if nom is not None:
                alt[(x, z)], sup[(x, z)] = y, nom
    rep = {t: [] for t in tipos}
    est_acc = {}  # estadística de accesos (piezas medidas, con acceso, sin acceso)
    adornos_caja = cajas_adornos()
    adornos_plan = []
    try:
        adornos_plan = list(json.load(open(os.path.join(RAIZ, "planos", "plan_aldea.json"), encoding="utf-8")).get("adornos") or [])
    except Exception:
        pass
    adornos_plan += list(adornos_extra or [])
    for a in adornos_extra or []:  # su caja también cuenta como «adorno» (para no llamarlos flotantes ni pilares)
        dm = diseno_de_adorno(a)
        if dm:
            try:
                _, (w_, d_, _a) = bloques_esperados(*dm)
                adornos_caja.append(("adorno extra", {"x0": X0 + a["x"] - w_, "x1": X0 + a["x"] + w_, "z0": Z0 + a["z"] - d_, "z1": Z0 + a["z"] + d_}))
            except Exception:
                pass

    if "paredes" in tipos or "bordes_rectos" in tipos:
        paredes, pasos_x, pasos_z = set(), {}, {}
        for (x, z), h in alt.items():
            if not (x1 <= x <= x2 and z1 <= z <= z2) or dentro(cajas, x, z, 1):
                continue
            for dx, dz in V4:
                hb = alt.get((x + dx, z + dz))
                if hb is None:
                    continue
                d = h - hb
                if d >= 3 and sum(m.bloque(x, yy, z) in TIERRA for yy in range(hb + 1, h + 1)) >= 3:
                    paredes.add((x, z))
                if d >= 2 and sup.get((x, z)) in NATURAL and sup.get((x + dx, z + dz)) in NATURAL and not dentro(cajas, x + dx, z + dz, 1):
                    if dx:
                        pasos_x.setdefault(x if dx > 0 else x - 1, set()).add(z)
                    else:
                        pasos_z.setdefault(z if dz > 0 else z - 1, set()).add(x)
        if natural is not None:
            paredes = {c for c in paredes if not es_nat(c)}
            pasos_x = {k: {z for z in v if not es_nat((k, z))} for k, v in pasos_x.items()}
            pasos_z = {k: {x for x in v if not es_nat((x, k))} for k, v in pasos_z.items()}
            pasos_x = {k: v for k, v in pasos_x.items() if v}
            pasos_z = {k: v for k, v in pasos_z.items() if v}
        if "paredes" in tipos:
            for g in grupos(paredes):
                if len(g) < 3:
                    continue
                alto = max(alt[c] - min(alt.get((c[0] + a, c[1] + b), alt[c]) for a, b in V4) for c in g)
                cx, cz = sorted(g, key=lambda c: (c[0] - sum(q[0] for q in g) / len(g)) ** 2 + (c[1] - sum(q[1] for q in g) / len(g)) ** 2)[0]
                rep["paredes"].append(_it("paredes", "%s, alto máx %d" % (resumen_grupo(g, alt), alto), cx, alt[(cx, cz)], cz,
                                          peso=alto + len(g) / 20, pat="pared de tierra", bbox=_bb(g), n=len(g), c="alto %d, %d bl" % (alto, len(g))))
        if "bordes_rectos" in tipos:
            for eje, pasos in (("x", pasos_x), ("z", pasos_z)):
                for linea, valores in pasos.items():
                    vs = sorted(valores)
                    ini = prev = vs[0]
                    for v in vs[1:] + [None]:
                        if v is not None and v - prev <= 1:
                            prev = v
                            continue
                        if prev - ini + 1 >= 8:  # «más de 7 bloques en línea recta» (regla de naturalidad de Minecraft)
                            L = prev - ini + 1
                            if eje == "x":
                                rep["bordes_rectos"].append(_it("bordes_rectos", "línea x=%d entre z %d y %d (%d bloques)" % (linea, ini, prev, L),
                                                                linea, alt.get((linea, ini), 0), (ini + prev) // 2, peso=L, pat="borde de terreno recto",
                                                                bbox=(linea, ini, linea, prev), n=L, c="x=%d z%d..%d" % (linea, ini, prev)))
                            else:
                                rep["bordes_rectos"].append(_it("bordes_rectos", "línea z=%d entre x %d y %d (%d bloques)" % (linea, ini, prev, L),
                                                                (ini + prev) // 2, alt.get((ini, linea), 0), linea, peso=L, pat="borde de terreno recto",
                                                                bbox=(ini, linea, prev, linea), n=L, c="z=%d x%d..%d" % (linea, ini, prev)))
                        if v is not None:
                            ini = prev = v

    if "flotantes" in tipos:
        flot = set()
        for x in range(x1, x2 + 1):
            for z in range(z1, z2 + 1):
                nom, y = m.tope(x, z)
                if nom is None or es_planta(nom) or dentro(cajas, x, z, 2) or dentro(adornos_caja, x, z):
                    continue
                if all(m.bloque(x, y - k, z) in MA.AIRE for k in (1, 2, 3)) and not es_nat((x, z)):
                    flot.add((x, z))
        for g in grupos(flot, 2):
            # un toldo pegado a la pared, el techo de un puesto sobre postes o un puente con pilares están sostenidos
            if any(sostenido(m, c[0], m.tope(*c)[1], c[1]) for c in g[:4]):
                continue
            t_ = m.tope(*g[0])
            rep["flotantes"].append(_it("flotantes", resumen_grupo(g) + ", y %d, %s" % (t_[1], t_[0]), g[0][0], t_[1], g[0][1], peso=len(g),
                                        pat="flotando: " + t_[0].split(":")[-1], bbox=_bb(g), n=len(g), c="%d bl de %s" % (len(g), t_[0].split(":")[-1])))

    if "flotantes" in tipos:  # 30-oct: letreros, faroles, estandartes, velas, macetas y antorchas sin apoyo
        import ojo_accesos as AC
        for (x, y, z, nom, motivo) in AC.apoyos(m, x1, z1, x2, z2):
            if es_nat((x, z)):
                continue
            rep["flotantes"].append(_it("flotantes", "(%d, %d, %d) %s sin apoyo: %s" % (x, y, z, nom, motivo), x, y, z, peso=2,
                                        pat="adorno sin apoyo: " + ("letrero" if "sign" in nom else "farol" if "lantern" in nom else
                                                                    "maceta" if "pot" in nom else "estandarte" if "banner" in nom else nom),
                                        c="%s: %s" % (nom, motivo[:40])))

    if "accesos" in tipos or "hoyos" in tipos or "encierros" in tipos:  # 30-oct: casas sin acceso, hoyos trampa y encierros
        import ojo_accesos as AC
        xa, xb, za, zb = x1 - 10, x2 + 10, z1 - 10, z2 + 10
        den = AC.construir(m, xa, za, xb, zb)
        if "accesos" in tipos:
            rej = AC.Rejilla(den)
            av, est = AC.accesos(m, rej, piezas_del_plano(centro_x, centro_z, radio) + list(piezas_extra or []), (X0, Z0), 2 * 68 + 2)
            if not est["semillas"]:
                rep["accesos"].append(_it("accesos", "no encontré la plaza (nodos caminables junto a la fogata): no pude medir accesos", X0, Y0, Z0, peso=0, pat="sin plaza"))
            for a in av:
                rep["accesos"].append(_it("accesos", a["texto"], a["x"], a["y"], a["z"], peso=3 + a.get("salto", 0),
                                          pat={"sin_acceso": "casa sin acceso", "camino_roto": "camino de la casa con un corte"}.get(a["tipo"], "puerta sin suelo"),
                                          c="%s salto %s" % (a["nombre"].split(" (")[0][:28], ("%g" % a["salto"]) if a.get("salto") not in (None, 99) else "?")))
            est_acc.update(est)
        if "hoyos" in tipos:
            red_ = set()  # celdas de caminos del plano y frentes de puerta: ahí un hoyo atrapa aldeanos y jugadores
            for p_ in piezas_del_plano(centro_x, centro_z, radio + 40):
                for (qx, qz) in p_.get("camino") or []:
                    red_.update((qx + a, qz + b) for a in range(-3, 4) for b in range(-3, 4))
            for h_ in AC.hoyos(m, den, cajas + adornos_caja, (centro_x, centro_z), radio, mo):
                junto = (h_["x"], h_["z"]) in red_
                if junto:
                    h_["texto"] += " [JUNTO A UN CAMINO O PUERTA]"
                rep["hoyos"].append(_it("hoyos", h_["texto"], h_["x"], h_["y"], h_["z"], peso=h_["borde"] - h_["fondo"] + 2 + (5 if junto else 0),
                                        pat="hoyo trampa junto a camino o puerta" if junto else "hoyo trampa",
                                        bbox=(h_["x"], h_["z"], h_["x"], h_["z"]), n=h_["n"], c="%dx1 hondo %d" % (h_["n"], h_["borde"] - h_["fondo"])))
        if "encierros" in tipos:  # 30-oct (Sergio murió en el muelle: «hay pedazos del pueblo que uno queda encerrado»)
            enc, _est = AC.encierros(m, den, (X0, Z0), radio)
            for e_ in enc:
                if e_["celdas"] < 2:
                    continue  # un solo lugar suele ser un mueble (taburete) o un rincón: ver con detalle
                bo = e_["boca"][0] if e_["boca"] else e_["muestra"]
                rep["encierros"].append(_it("encierros", "encierro de %d celdas%s en x %d..%d, z %d..%d, y %d..%d: se cae desde (%d, %d, %d) y no se vuelve a la plaza"
                                            % (e_["celdas"], " (%d de agua)" % e_["agua"] if e_["agua"] else "", e_["x0"], e_["x1"], e_["z0"], e_["z1"],
                                               e_["y_min"], e_["y_max"], bo[0], bo[1], bo[2]),
                                            e_["muestra"][0], e_["muestra"][1], e_["muestra"][2], peso=min(20, 3 + e_["celdas"] // 4),
                                            pat="encierro sin salida", bbox=(e_["x0"], e_["z0"], e_["x1"], e_["z1"]), n=e_["celdas"],
                                            c="%d celdas" % e_["celdas"]))

    if "hojas" in tipos:
        sueltas, caeran = arboles_rotos(m, x1, z1, x2, z2, mo)
        for s in sueltas:
            cx, cz = (s["caja"][0] + s["caja"][2]) // 2, (s["caja"][1] + s["caja"][3]) // 2
            if s["troncos"] and s["hojas"]:
                pat, que = "árbol entero suelto en el aire", "árbol flotando"
            elif s["troncos"]:
                pat, que = "tronco suelto en el aire", "tronco flotando"
            elif s["persist"] == s["hojas"]:  # todas persistentes: casi siempre adorno puesto a propósito (camuflaje, seto)
                pat, que = "hojas persistentes sin apoyo (¿adorno?)", "hojas persistentes sin apoyo"
            else:
                pat, que = "copa de hojas sin tronco", "copa de hojas sin tronco"
            rep["hojas"].append(_it("hojas", "(%d, %d, %d) %s: %d hojas y %d troncos, y %d..%d, caja x %d..%d z %d..%d" % (
                cx, s["y"][0], cz, que, s["hojas"], s["troncos"], s["y"][0], s["y"][1], s["caja"][0], s["caja"][2], s["caja"][1], s["caja"][3]),
                cx, s["y"][0], cz, peso=(s["hojas"] + s["troncos"]) / (4 if pat.startswith("hojas persistentes") else 1), pat=pat, bbox=s["caja"], n=s["hojas"] + s["troncos"], c="%d hojas, %d troncos" % (s["hojas"], s["troncos"])))
        for s in caeran:
            cx, cz = (s["caja"][0] + s["caja"][2]) // 2, (s["caja"][1] + s["caja"][3]) // 2
            rep["hojas"].append(_it("hojas", "(%d, %d, %d) %d hojas sin tronco a 6 bloques (se caen solas), caja x %d..%d z %d..%d" % (
                cx, s["y"][0], cz, s["hojas"], s["caja"][0], s["caja"][2], s["caja"][1], s["caja"][3]),
                cx, s["y"][0], cz, peso=s["hojas"] / 2, pat="hojas sin tronco que se caerán solas", bbox=s["caja"], n=s["hojas"], c="%d hojas" % s["hojas"]))

    if "pilares" in tipos:
        visto = set()
        for (x, z), h in alt.items():
            if (x, z) in visto or not (x1 <= x <= x2 and z1 <= z <= z2) or sup.get((x, z)) not in NATURAL or sup.get((x, z)) == "water":
                continue
            if dentro(cajas, x, z, 2) or dentro(adornos_caja, x, z) or es_nat((x, z)):
                continue
            comp, pila, vis, ok = [(x, z)], [(x, z)], {(x, z)}, True
            while pila and ok:
                c = pila.pop()
                for dx, dz in V4:
                    v = (c[0] + dx, c[1] + dz)
                    if v not in vis and alt.get(v) == h:
                        vis.add(v)
                        comp.append(v)
                        pila.append(v)
                        if len(comp) > 6:
                            ok = False
                            break
            visto |= vis
            if not ok:
                continue
            borde = {(c[0] + dx, c[1] + dz) for c in comp for dx, dz in V4} - vis
            if any(v not in alt or sup.get(v) == "water" for v in borde):
                continue
            prof = h - max(alt[v] for v in borde)
            if prof >= 2:
                cx, cz = comp[0]
                rep["pilares"].append(_it("pilares", "(%d, %d, %d) pilar de %d bloque%s de %s, sobresale %d" % (
                    cx, h, cz, len(comp), "" if len(comp) == 1 else "s", sup[(cx, cz)], prof), cx, h, cz, peso=prof * 2 + len(comp) / 3,
                    pat="pilar o pincho de terreno", bbox=_bb(comp), n=len(comp), c="%d bl, sobresale %d" % (len(comp), prof)))

    if "cofres" in tipos:
        llenos, con_premio = 0, set()
        for e in m.entidades(x1, z1, x2, z2):
            eid = str(e.get("id", ""))
            if not any(k in eid for k in ("chest", "barrel", "shulker")):
                continue
            if e.get("Items") or e.get("LootTable"):
                llenos += 1
                for n, c in cajas:
                    if c["x0"] <= e["x"] <= c["x1"] and c["z0"] <= e["z"] <= c["z1"]:
                        con_premio.add(n)
            else:
                k = eid.replace("minecraft:", "")
                rep["cofres"].append(_it("cofres", "(%d, %d, %d) %s vacío" % (e["x"], e["y"], e["z"], k), e["x"], e["y"], e["z"], peso=1, pat="cofre o barril vacío", c=k))
        sin = [n for n, c in cajas if n != "parque" and abs((c["x0"] + c["x1"]) / 2 - centro_x) <= radio
               and abs((c["z0"] + c["z1"]) / 2 - centro_z) <= radio and n not in con_premio]
        if cajas and sin:
            rep["cofres"].append(_it("cofres", "CASAS SIN NINGÚN COFRE CON ALGO: %d → %s" % (len(sin), ", ".join(sin[:20])), centro_x, 0, centro_z,
                                     peso=len(sin) * 3, pat="casas sin cofre", n=len(sin), c="%d casas: %s" % (len(sin), ", ".join(sin[:3]))))

    if "escaleras" in tipos:
        suelo_ok = NATURAL | {"dirt_path", "cobblestone", "mossy_cobblestone"}
        esc = {(x, z) for (x, z), n in sup.items() if n.endswith("_stairs") and x1 <= x <= x2 and z1 <= z <= z2
               and not dentro(cajas, x, z, 1) and not dentro(adornos_caja, x, z)
               and (m.bloque(x, alt[(x, z)] - 1, z) or "").split("[")[0] in suelo_ok}
        for g in grupos(esc):
            if len(g) < 3:
                continue
            hs = [alt[c] for c in g]
            plano = max(hs) - min(hs) <= 1
            rep["escaleras"].append(_it("escaleras", "%s, %s, %s" % (resumen_grupo(g, alt), sup[g[0]], "EN TERRENO PLANO (no hacen falta)" if plano else
                                                                    "baja %d bloques" % (max(hs) - min(hs))), g[0][0], alt[g[0]], g[0][1],
                                        peso=len(g), pat="escaleras en terreno plano" if plano else "escaleras de camino", bbox=_bb(g), n=len(g), c="%d esc." % len(g)))

    if "pisos" in tipos:
        for n, c in cajas:
            if n == "parque" or n.endswith((":wall", ":gate")) or abs((c["x0"] + c["x1"]) / 2 - centro_x) > radio or abs((c["z0"] + c["z1"]) / 2 - centro_z) > radio:
                continue
            casa = Casa(m, c, n)
            if casa.vacia:
                continue
            for cu in casa.cuartos:
                if cu["porche"] or cu.get("sin_acceso"):
                    continue
                for a in cu["avisos"]:
                    if a.startswith(("VACÍO", "muy pobre")):
                        rep["pisos"].append(_it("pisos", "%s: cuarto %s (piso y %d, x %d..%d, z %d..%d) %s" % (
                            n, cu["id"], cu["y"], cu["x"][0], cu["x"][1], cu["z"][0], cu["z"][1], a),
                            cu["x"][0], cu["y"], cu["z"][0], peso=2 if a.startswith("VACÍO") else 1, pat="cuarto " + a.split(":")[0],
                            bbox=(cu["x"][0], cu["z"][0], cu["x"][1], cu["z"][1]), c="%s %s" % (n.split(":")[-1], a.split(":")[0])))

    def props(n):
        return dict(kv.split("=", 1) for kv in n[n.index("[") + 1:-1].split(",")) if n and "[" in n else {}

    def tapa(n):  # bloque que no deja pasar
        return bool(n) and n not in MA.AIRE and n.split("[")[0] not in ("water", "lava") and not es_planta(n) and not any(k in n for k in (
            "door", "carpet", "torch", "sign", "banner", "ladder", "vine", "button", "plate", "rail", "snow", "lantern", "flower", "candle"))

    if "puertas" in tipos:
        vistas = set()
        for n, c in cajas:
            if abs((c["x0"] + c["x1"]) / 2 - centro_x) > radio or abs((c["z0"] + c["z1"]) / 2 - centro_z) > radio:
                continue
            for xx in range(c["x0"] - 1, c["x1"] + 2):
                for zz in range(c["z0"] - 1, c["z1"] + 2):
                    for yy in range(Y0 - 8, Y0 + 30):
                        b_ = m.bloque(xx, yy, zz)
                        if not b_ or "_door[" not in b_ or (xx, yy, zz) in vistas:
                            continue
                        pa = props(b_)
                        if pa.get("half") != "lower":
                            continue
                        vistas.add((xx, yy, zz))
                        arriba = m.bloque(xx, yy + 1, zz) or ""
                        pb = props(arriba)
                        mal = []
                        if "_door[" not in arriba:
                            mal.append("le falta la mitad de arriba")
                        elif any(pa.get(k) != pb.get(k) for k in ("facing", "open", "hinge")):
                            mal.append("las dos mitades no coinciden (abajo %s/%s, arriba %s/%s)" % (pa.get("facing"), pa.get("open"), pb.get("facing"), pb.get("open")))
                        sol = lambda q: tapa(m.bloque(q[0], yy, q[1])) and tapa(m.bloque(q[0], yy + 1, q[1]))  # noqa: E731
                        mx, mz = sol((xx - 1, zz)) and sol((xx + 1, zz)), sol((xx, zz - 1)) and sol((xx, zz + 1))
                        if mx != mz:
                            eje = "z" if mx else "x"
                            if {"north": "z", "south": "z", "east": "x", "west": "x"}.get(pa.get("facing")) != eje:
                                mal.append("mira de lado al hueco del muro (al abrirse tapa la entrada)")
                            delante = [(xx, zz - 1), (xx, zz + 1)] if eje == "z" else [(xx - 1, zz), (xx + 1, zz)]
                            for q in delante:
                                if tapa(m.bloque(q[0], yy, q[1])) or tapa(m.bloque(q[0], yy + 1, q[1])):
                                    mal.append("tapada por %s en %s" % (m.bloque(q[0], yy, q[1]) if tapa(m.bloque(q[0], yy, q[1])) else m.bloque(q[0], yy + 1, q[1]), q))
                        if mal:
                            rep["puertas"].append(_it("puertas", "(%d, %d, %d) %s de %s: %s" % (xx, yy, zz, b_.split("[")[0], n, "; ".join(mal)),
                                                      xx, yy, zz, peso=2, pat="puerta: " + mal[0].split(" (")[0].split(" por")[0], c=mal[0][:45]))

    solo_camino = "caminos_hundidos" in tipos
    if "zanjas" in tipos:
        hund = []
        for xx in range(x1, x2 + 1):
            for zz in range(z1, z2 + 1):
                if dentro(cajas, xx, zz, 1) or es_nat((xx, zz)) or sup.get((xx, zz)) in ("water", "ice") or (xx, zz) not in alt:
                    continue
                if solo_camino and es_camino(sup.get((xx, zz))):
                    continue  # los caminos hundidos tienen su propio aviso
                y_ = alt[(xx, zz)]
                if y_ <= 62:  # al nivel del agua: arroyo u orilla
                    continue
                for (ax_, az_), (bx_, bz_) in (((-1, 0), (1, 0)), ((0, -1), (0, 1))):
                    pa_ = [alt.get((xx + ax_ * k, zz + az_ * k)) for k in (1, 2)]
                    pb_ = [alt.get((xx + bx_ * k, zz + bz_ * k)) for k in (1, 2)]
                    if None not in pa_ + pb_ and min(pa_) >= y_ + 2 and min(pb_) >= y_ + 2:
                        hund.append((xx, zz))
                        break
        for g in grupos(hund):
            fondo = min(alt[c] for c in g)
            rep["zanjas"].append(_it("zanjas", "%s, fondo y %d" % (resumen_grupo(g), fondo), g[0][0], fondo, g[0][1], peso=len(g),
                                     pat="zanja u hoyo", bbox=_bb(g), n=len(g), c="%d bl, fondo y%d" % (len(g), fondo)))

    if solo_camino:
        hund = set()
        for (xx, zz), n in sup.items():
            if not (x1 <= xx <= x2 and z1 <= zz <= z2) or not es_camino(n) or alt[(xx, zz)] <= 62:
                continue
            if dentro(cajas, xx, zz, 1) or dentro(adornos_caja, xx, zz) or es_nat((xx, zz)):
                continue
            y_ = alt[(xx, zz)]
            for (ax_, az_), (bx_, bz_) in (((-1, 0), (1, 0)), ((0, -1), (0, 1))):
                la, lb = (xx + ax_, zz + az_), (xx + bx_, zz + bz_)
                if alt.get(la, -999) >= y_ + 2 and alt.get(lb, -999) >= y_ + 2 and sup.get(la) in NATURAL and sup.get(lb) in NATURAL:
                    hund.add((xx, zz))
                    break
        for g in grupos(hund):
            if len(g) < 3:
                continue
            fondo = min(alt[c] for c in g)
            pared = max(min(alt.get((c[0] + a, c[1] + b), 0) for a, b in V4) for c in g)
            rep["caminos_hundidos"].append(_it("caminos_hundidos", "%s, camino a y %d con paredes de tierra 2+ más altas a los dos lados" % (resumen_grupo(g, alt), fondo),
                                               g[0][0], fondo, g[0][1], peso=len(g), pat="camino hundido", bbox=_bb(g), n=len(g), c="%d bl, a y%d" % (len(g), fondo)))

    if "enterradas" in tipos:
        for n, c in cajas:
            if n == "parque" or abs((c["x0"] + c["x1"]) / 2 - centro_x) > radio or abs((c["z0"] + c["z1"]) / 2 - centro_z) > radio:
                continue
            if es_nat(((c["x0"] + c["x1"]) // 2, (c["z0"] + c["z1"]) // 2)):
                continue  # casa de una etapa que este mundo todavía no construyó
            anillo = [(x_, z_) for x_ in range(c["x0"] - 2, c["x1"] + 3) for z_ in range(c["z0"] - 2, c["z1"] + 3)
                      if not (c["x0"] - 1 <= x_ <= c["x1"] + 1 and c["z0"] - 1 <= z_ <= c["z1"] + 1)]
            patio = sorted(alt[(x_, z_)] for x_ in range(c["x0"] - 1, c["x1"] + 2) for z_ in range(c["z0"] - 1, c["z1"] + 2)
                           if not (c["x0"] <= x_ <= c["x1"] and c["z0"] <= z_ <= c["z1"]) and (x_, z_) in alt)
            if not patio:
                continue
            base = patio[len(patio) // 2]
            altos = [q for q in anillo if alt.get(q, -999) >= base + 3]
            if len(altos) >= 0.3 * len(anillo):
                rep["enterradas"].append(_it("enterradas", "%s: %d%% del contorno está 2+ bloques sobre su patio (y %d)" % (n, 100 * len(altos) // len(anillo), base),
                                             (c["x0"] + c["x1"]) // 2, base, (c["z0"] + c["z1"]) // 2, peso=100 * len(altos) // len(anillo), pat="casa enterrada",
                                             bbox=(c["x0"], c["z0"], c["x1"], c["z1"]), c="%s %d%%" % (n, 100 * len(altos) // len(anillo))))

    if "adornos_enterrados" in tipos or "disenos_incompletos" in tipos:
        ausentes = 0
        for a in adornos_plan:
            ax, az = X0 + a.get("x", 0), Z0 + a.get("z", 0)
            if abs(ax - centro_x) > radio or abs(az - centro_z) > radio or diseno_de_adorno(a) is None:
                continue
            try:
                r = revisar_adorno(m, a, cajas)
            except Exception as e:  # un diseño raro no tumba la auditoría
                rep.setdefault("disenos_incompletos", []).append(_it("disenos_incompletos", "no pude revisar %s: %s" % (a.get("tipo"), e), ax, 0, az, peso=0, pat="no revisado"))
                continue
            if not r or r["estado"] == "sin_chunk":
                continue
            nombre = "%s %s" % (a.get("tipo"), a.get("color", a.get("mira", "")))
            caja = (r["ax"] - r["w"] // 2, r["az"] - r["d"] // 2, r["ax"] - r["w"] // 2 + r["w"] - 1, r["az"] - r["d"] // 2 + r["d"] - 1)
            if r["estado"] == "ausente":
                ausentes += 1
            elif r["estado"] == "incompleto" and "disenos_incompletos" in tipos:
                capas = ", ".join("capa +%d: %d" % (k, v) for k, v in sorted(r["faltan"].items(), reverse=True)[:3])
                rep["disenos_incompletos"].append(_it(
                    "disenos_incompletos", "(%d, %d, %d) %s: faltan %d de %d bloques (%d%%); %s; sobre todo %s" % (
                        r["ax"], r["y0"], r["az"], nombre, r["total"] - r["ok"], r["total"], 100 - int(100 * r["cob"]), capas,
                        ", ".join("%s×%d" % (n.split(":")[-1], k) for n, k in r["faltan_nombres"])),
                    r["ax"], r["y0"], r["az"], peso=100 - 100 * r["cob"], pat="diseño incompleto: " + str(r["dm"][0]), bbox=caja, n=r["total"] - r["ok"], c="%s faltan %d/%d (%s)" % (nombre, r["total"] - r["ok"], r["total"], capas)))
            if "adornos_enterrados" in tipos and r["estado"] != "ausente":
                partes = []
                if r["solapa"]:
                    partes.append("el plano lo puso encima de la casa %s" % r["solapa"])
                fuertes = [q for q in r["intrusos"] if q[3] in NATURAL or q[3] in TIERRA]
                cols = {(q[0], q[2]) for q in r["intrusos"]}
                if len(fuertes) >= 5 or (len(r["intrusos"]) >= 6 and len(cols) >= 0.4 * r["w"] * r["d"]):
                    cuenta = {}
                    for q in r["intrusos"]:
                        cuenta[q[3]] = cuenta.get(q[3], 0) + 1
                    partes.append("%d bloques sólidos ajenos dentro o encima (%s; el más alto en y %d)" % (
                        len(r["intrusos"]), ", ".join("%s×%d" % (k.split(":")[-1], v) for k, v in sorted(cuenta.items(), key=lambda t: -t[1])[:2]),
                        max(q[1] for q in r["intrusos"])))
                if r["reemplazados"] >= 3:
                    partes.append("%d bloques suyos reemplazados por tierra o piedra (hundido en el terreno)" % r["reemplazados"])
                if partes:
                    rep["adornos_enterrados"].append(_it("adornos_enterrados", "(%d, %d, %d) %s: %s" % (r["ax"], r["y0"], r["az"], nombre, "; ".join(partes)),
                                                         r["ax"], r["y0"], r["az"], peso=len(r["intrusos"]) / 4 + r["reemplazados"] / 3 + (5 if r["solapa"] else 0),
                                                         pat="adorno tapado o enterrado", bbox=caja, c="%s: %s" % (nombre, "; ".join(partes)[:60])))
        if ausentes and "disenos_incompletos" in tipos:
            rep["disenos_incompletos"].append(_it("disenos_incompletos", "%d adornos con diseño que no están construidos (menos del 20 %%): ¿etapa sin construir?" % ausentes,
                                                  centro_x, 0, centro_z, peso=0.1, pat="adornos ausentes (etapa sin construir)", n=ausentes))

    if "rectas" in tipos:
        P = {c for c, n in sup.items() if es_camino(n) and x1 <= c[0] <= x2 and z1 <= c[1] <= z2 and not dentro(cajas, c[0], c[1], 1)
             and not dentro(adornos_caja, c[0], c[1]) and not es_nat(c) and alt[c] > 62}
        bordes = {"oeste": {}, "este": {}, "norte": {}, "sur": {}}
        for (x, z) in P:
            if (x - 1, z) not in P and sup.get((x - 1, z)) in NATURAL:
                bordes["oeste"].setdefault(x, []).append(z)
            if (x + 1, z) not in P and sup.get((x + 1, z)) in NATURAL:
                bordes["este"].setdefault(x, []).append(z)
            if (x, z - 1) not in P and sup.get((x, z - 1)) in NATURAL:
                bordes["norte"].setdefault(z, []).append(x)
            if (x, z + 1) not in P and sup.get((x, z + 1)) in NATURAL:
                bordes["sur"].setdefault(z, []).append(x)
        for lado, lineas in bordes.items():
            for linea, vals in lineas.items():
                vs = sorted(vals)
                ini = prev = vs[0]
                for v in vs[1:] + [None]:
                    if v is not None and v - prev == 1:
                        prev = v
                        continue
                    L = prev - ini + 1
                    if L >= 8:
                        if lado in ("oeste", "este"):
                            rep["rectas"].append(_it("rectas", "borde %s del camino recto: x=%d entre z %d y %d (%d bloques)" % (lado, linea, ini, prev, L),
                                                     linea, alt.get((linea, ini), 0), (ini + prev) // 2, peso=L, pat="borde de camino recto", bbox=(linea, ini, linea, prev), n=L, c="x=%d z%d..%d" % (linea, ini, prev)))
                        else:
                            rep["rectas"].append(_it("rectas", "borde %s del camino recto: z=%d entre x %d y %d (%d bloques)" % (lado, linea, ini, prev, L),
                                                     (ini + prev) // 2, alt.get((ini, linea), 0), linea, peso=L, pat="borde de camino recto", bbox=(ini, linea, prev, linea), n=L, c="z=%d x%d..%d" % (linea, ini, prev)))
                    if v is not None:
                        ini = prev = v

    if "alegres" in tipos:
        rep["alegres"] = revisar_alegres(m, mo, x1, z1, x2, z2)

    if "entidades" in tipos:
        for s in m.seres(x1, z1, x2, z2):
            tags = [str(t) for t in s.get("tags") or []]
            lejos = math.hypot(s["x"] - X0, s["z"] - Z0)
            corto = s["id"].split(":")[-1]
            if s["tipo"] == "monstruo" and corto not in AVES and lejos <= 110 and s["y"] >= Y0 - 12 and "jefe_mazmorra" not in tags and "hollow" not in s["id"] and "herobrine" not in s["id"]:
                rep["entidades"].append(_it("entidades", "(%d, %d, %d) %s%s dentro del pueblo" % (s["x"], s["y"], s["z"], s["id"], " «%s»" % s["nombre"] if s["nombre"] else ""),
                                            s["x"], s["y"], s["z"], peso=3, pat="monstruo dentro del pueblo", c=corto))
            if s["tipo"] == "armadura" and not dentro(cajas, s["x"], s["z"], 0):
                if all(m.bloque(s["x"], s["y"] - k, s["z"]) in MA.AIRE for k in (0, 1, 2)):
                    rep["entidades"].append(_it("entidades", "(%d, %d, %d) soporte de armadura flotando en el aire" % (s["x"], s["y"], s["z"]),
                                                s["x"], s["y"], s["z"], peso=2, pat="soporte de armadura flotando", c="armor_stand"))
            if s["tipo"] in ("aldeano", "animal", "monstruo", "armadura"):
                b0, b1 = m.bloque(s["x"], s["y"], s["z"]), m.bloque(s["x"], s["y"] + 1, s["z"])
                if b0 and b1 and tapa(b0) and tapa(b1) and "armor_stand" not in s["id"]:
                    rep["entidades"].append(_it("entidades", "(%d, %d, %d) %s metido dentro de bloques (%s)" % (s["x"], s["y"], s["z"], corto, b0.split("[")[0]),
                                                s["x"], s["y"], s["z"], peso=3, pat="ser dentro de un bloque", c="%s en %s" % (corto, b0.split("[")[0])))

    if "mods" in tipos:
        def pred_mod(n):
            return ":" in n and not n.startswith(MOD_PERMITIDO) and not any(k in n.split(":")[-1] for k in MOD_EXTERIOR_OK)
        hall = m.escanear(x1, z1, x2, z2, pred_mod, y_min=Y0 - 12)
        if mo is not None and hall:
            orig = {(x, y, z) for x, y, z, n, p in mo.escanear(x1, z1, x2, z2, pred_mod)}
            hall = [h for h in hall if (h[0], h[1], h[2]) not in orig]
        por = {}
        for x, y, z, n, p in hall:
            if dentro(cajas, x, z, 4) or dentro(adornos_caja, x, z, 3) or es_nat((x, z)):
                continue
            por.setdefault(n, []).append((x, y, z))
        for n, pts in por.items():
            for g in grupos([(x, z) for x, y, z in pts], 6):
                gs = set(g)
                ej = next(c for c in pts if (c[0], c[2]) in gs)
                k = sum(1 for c in pts if (c[0], c[2]) in gs)
                rep["mods"].append(_it("mods", "(%d, %d, %d) %s ×%d suelto fuera de casas y adornos" % (ej[0], ej[1], ej[2], n, k), ej[0], ej[1], ej[2],
                                       peso=k, pat="mod suelto", bbox=_bb(g), n=k, c="%s×%d" % (n, k)))

    if "restos" in tipos:
        TEC = {"structure_block", "jigsaw", "command_block", "chain_command_block", "repeating_command_block"}
        ALTAS = ("tall_grass", "large_fern", "rose_bush", "lilac", "peony", "sunflower", "tall_seagrass", "pitcher_plant")
        DIRS = DIRS_OJO

        def pred_resto(n):
            c = n.split(":")[-1]
            return c in TEC or c.endswith("_bed") or c in ("wall_torch", "soul_wall_torch", "redstone_wall_torch") or \
                c.endswith("_door") or c in ALTAS
        for x, y, z, n, p in m.escanear(x1, z1, x2, z2, pred_resto, y_min=Y0 - 30):
            c = n.split(":")[-1]
            if (dentro(adornos_caja, x, z, 0) and c in ALTAS) or (mo is not None and mo.estado(x, y, z) == (n, p)):
                continue  # adorno del plano, o ya era así en el terreno natural
            if c in TEC:
                rep["restos"].append(_it("restos", "(%d, %d, %d) bloque técnico %s olvidado" % (x, y, z, c), x, y, z, peso=5, pat="bloque técnico olvidado", c=c))
            elif c.endswith("_bed"):
                dx, dz = DIRS.get(p.get("facing"), (0, 0))
                ox, oz = (x + dx, z + dz) if p.get("part") == "foot" else (x - dx, z - dz)
                n2, p2 = m.estado(ox, y, oz)
                if not (n2 or "").endswith("_bed") or p2.get("part") == p.get("part"):
                    rep["restos"].append(_it("restos", "(%d, %d, %d) cama partida: falta la mitad (%s)" % (x, y, z, p.get("part")), x, y, z, peso=3, pat="cama partida", c="falta la otra mitad"))
            elif "wall_torch" in c:
                dx, dz = DIRS.get(p.get("facing"), (0, 0))
                if not tapa_vista(m.bloque(x - dx, y, z - dz)):
                    rep["restos"].append(_it("restos", "(%d, %d, %d) antorcha de pared sin pared detrás" % (x, y, z), x, y, z, peso=2, pat="antorcha de pared flotando", c=c))
            elif c.endswith("_door") or c in ALTAS:
                if dentro(cajas, x, z, 1) and c.endswith("_door"):
                    continue  # las puertas de las casas ya las revisa «puertas»
                mitad, par = p.get("half"), (y + 1 if p.get("half") == "lower" else y - 1)
                n2, p2 = m.estado(x, par, z)
                if n2 != n or p2.get("half") == mitad:
                    rep["restos"].append(_it("restos", "(%d, %d, %d) %s partida: falta la mitad %s" % (x, y, z, c, "de arriba" if mitad == "lower" else "de abajo"),
                                             x, y, z, peso=2, pat="planta o puerta partida", c=c))
    return {"items": rep, "seg": time.time() - t0, "sin_plano": not cajas, "centro": (centro_x, centro_z), "radio": radio, "tipos": tipos, "acc": est_acc}


def _rango(its):
    b = [min(i["bbox"][0] for i in its), min(i["bbox"][1] for i in its), max(i["bbox"][2] for i in its), max(i["bbox"][3] for i in its)]
    return "x%d..%d z%d..%d" % (b[0], b[2], b[1], b[3])


def _corto(i):
    """Coordenadas y lo esencial (campo c); si el aviso no trae c, su texto recortado."""
    if i.get("c"):
        return ("(%d,%d,%d) %s" % (i["x"], i["y"], i["z"], i["c"]))[:72]
    t = re.sub(r" caja x -?\d+\.\.-?\d+ z -?\d+\.\.-?\d+,", ",", i["txt"])
    return t if len(t) <= 80 else t[:77] + "..."


def resumir(res, max_peores=2, max_patrones=3):
    """Informe corto: un renglón con los conteos de todos los tipos y, por cada tipo con avisos, una línea por patrón (avisos
    parecidos juntos: cuántos, dónde y los peores con coordenadas). El detalle completo va en el .json."""
    its = res["items"]
    con = [t for t in res["tipos"] if its.get(t)]
    sin = [t for t in res["tipos"] if not its.get(t)]
    lin = ["Ojo (%d,%d) r%d %.0fs: %s%s." % (res["centro"][0], res["centro"][1], res["radio"], res["seg"],
                                              " · ".join("%s %d" % (t, len(its[t])) for t in con) or "sin problemas",
                                              ("  | OK: " + ", ".join(sin)) if sin and con else "")]
    if res.get("sin_plano"):
        lin.append("Ojo: no se pudo leer el plano del pueblo; no se excluyen las casas.")
    if res.get("acc", {}).get("piezas") is not None and "accesos" in res["tipos"]:
        e_ = res["acc"]
        lin.append("Accesos: %d piezas medidas, %d con acceso, %d sin acceso, %d sin construir." % (e_["piezas"], e_["con_acceso"], e_["sin_acceso"], e_["sin_construir"]))
    for t in con:
        pats = {}
        for i in sorted(its[t], key=lambda i: -i["peso"]):
            pats.setdefault(i["pat"], []).append(i)
        orden = sorted(pats.items(), key=lambda kv: -sum(i["peso"] for i in kv[1]))
        for k, (pat, g) in enumerate(orden):
            if k >= max_patrones:
                lin.append("  (%s: +%d patrones, %d avisos más en el json)" % (t, len(orden) - max_patrones, sum(len(x[1]) for x in orden[max_patrones:])))
                break
            if len(g) == 1:
                lin.append("%s: %s" % (t, _corto(g[0]) if g[0].get("c") else g[0]["txt"][:100]))
            else:
                lin.append("%s: %d× %s [%s] p. ej. %s" % (t, len(g), pat, _rango(g), "; ".join(_corto(i) for i in g[:max_peores])))
    return "\n".join(lin)


def detalle_largo(res, max_por_tipo=15):
    """El informe de antes (una lista por tipo)."""
    its = res["items"]
    txt = ["Auditoría de (%d, %d) radio %d — %.0f s. Coordenadas absolutas (sirven para /tp)." % (res["centro"][0], res["centro"][1], res["radio"], res["seg"])]
    if res.get("sin_plano"):
        txt.append("Ojo: no se pudo leer el plano del pueblo; no se excluyen las casas.")
    for t in res["tipos"]:
        lista = sorted(its.get(t, []), key=lambda i: -i["peso"])
        txt.append("\n## %s: %d\n%s" % (TITULOS.get(t, t), len(lista), "\n".join("  - " + i["txt"] for i in lista[:max_por_tipo])))
        if len(lista) > max_por_tipo:
            txt.append("  ... y %d más" % (len(lista) - max_por_tipo))
    return "\n".join(txt)


def t_buscar_problemas(centro_x=X0, centro_z=Z0, radio=150, tipos=None, max_por_tipo=None, solo_pueblo=False, detalle=False):
    """Auditoría en texto. Informe CORTO por defecto (resumen por tipo, avisos parecidos agrupados, 3 peores con coordenadas);
    detalle=true da las listas largas. La lista completa siempre queda en el .json."""
    res = auditar(centro_x, centro_z, radio, tipos, solo_pueblo)
    os.makedirs(SALIDA, exist_ok=True)
    ruta = os.path.join(SALIDA, "problemas_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    json.dump({"resumen": {t: len(v) for t, v in res["items"].items()}, "avisos": res["items"]}, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cuerpo = detalle_largo(res, max_por_tipo or 15) if detalle else resumir(res, max_por_tipo or 2)
    return cuerpo + "\nLista completa: " + ruta


def t_comando(comando, confirmar=False):
    if not es_lectura(comando) and not confirmar:
        return ("BLOQUEADO: «%s» puede cambiar el mundo. Solo se manda con confirmar=true, y solo si Sergio lo pidió o lo "
                "aprobó en el chat.") % comando
    if not servidor_abierto():
        return "El servidor está apagado."
    return rcon(comando.strip().lstrip("/"))[0] or "(sin respuesta)"


def procesos_prueba():
    if os.name != "nt":  # 4-nov: el cliente de prueba solo existe en Windows; en Mac o Linux no hay ninguno abierto
        return []
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='javaw.exe'\" | Where-Object { $_.CommandLine -like '*juego_prueba*' -or $_.CommandLine -like '*--username Prueba*' }"
                        " | ForEach-Object { $_.ProcessId }"], capture_output=True, text=True)
    pids = [int(p) for p in r.stdout.split() if p.isdigit()]
    if not pids:  # la línea de comandos a veces sale recortada: si «Prueba» está conectado, el cliente está abierto
        try:
            if "Prueba" in "".join(rcon("list")) and servidor_abierto():
                otros = subprocess.run(["powershell", "-NoProfile", "-Command",
                                        "(Get-CimInstance Win32_Process -Filter \"Name='javaw.exe'\").ProcessId"],
                                       capture_output=True, text=True).stdout.split()
                pids = [int(p) for p in otros if p.isdigit()][-1:]
        except Exception:
            pass
    return pids


def t_abrir_cliente(autorizado=False):
    if not autorizado:
        return "No abierto: el cliente de prueba solo se abre si Sergio lo dice en el chat (gasta RAM y tokens)."
    if procesos_prueba():
        return "El cliente Prueba ya está abierto."
    subprocess.Popen([sys.executable, os.path.join(RAIZ, "herramientas", "cliente_prueba.py"), "Prueba", "--ram=3", "--res=1600x900"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return "Abriendo el cliente Prueba (tarda 1 a 3 minutos en entrar al servidor). Revisa con estado()."


def t_cerrar_cliente():
    pids = procesos_prueba()
    for p in pids:
        subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"], capture_output=True)
    return "Cliente Prueba cerrado." if pids else "El cliente Prueba no estaba abierto."


def pasar_mca(pid):
    """La pantalla «¿Quién eres?» de MCA sale cada vez que el cliente entra a un mundo nuevo: Aceptar y luego Hecho.
    Solo si Sergio no está jugando (los clics mueven el mouse); ver memoria feedback-ojo-pantalla."""
    ps = os.path.join(RAIZ, "herramientas", "clic_prueba.ps1")
    for x_, y_ in ((800, 725), (535, 773)):
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps, "-id", str(pid), "-x", str(x_), "-y", str(y_)],
                       capture_output=True)
        time.sleep(2)


# --- Foto sin sorpresas (29-oct, banco_ojo): detectar la pantalla de MCA y la cámara metida en hojas o techos ---

MCA_REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ojo_mca_ref.npy")
_mca_ref = []


def _gris_chico(ruta, w=64, h=36):
    """La imagen reducida a 64x36 en grises (matriz numpy), o None si no se puede (sin ffmpeg o sin numpy)."""
    if _np is None:
        return None
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", ruta, "-vf", "scale=%d:%d:flags=area" % (w, h),
                            "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=30)
    except Exception:
        return None
    if len(r.stdout) != w * h:
        return None
    return _np.frombuffer(r.stdout, dtype=_np.uint8).reshape(h, w).astype(float)


def es_pantalla_mca(ruta, umbral=0.8):
    """(es_menu, parecido). Compara la foto, reducida a 64x36, con la pantalla «¿Quién eres?» de MCA (promedio de 3 capturas reales,
    ojo_mca_ref.npy). Probado con las 26 fotos de entrenamiento: las pantallas de MCA dan 0.98-0.99 y todo lo demás menos de 0.40."""
    if not _mca_ref:
        try:
            _mca_ref.append(_np.load(MCA_REF))
        except Exception:
            return False, 0.0
    a = _gris_chico(ruta)
    if a is None or a.std() < 1e-6:
        return False, 0.0
    r = float(_np.corrcoef(a.ravel(), _mca_ref[0].ravel())[0, 1])
    return r >= umbral, r


DELGADO = ("grass", "fern", "flower", "tulip", "orchid", "allium", "bluet", "daisy", "poppy", "dandelion", "cornflower", "lily", "rose",
           "peony", "lilac", "sunflower", "sapling", "torch", "carpet", "sign", "banner", "button", "lever", "rail", "vine", "cobweb",
           "kelp", "seagrass", "lantern", "chain", "candle", "pressure_plate", "glass", "pane", "bars", "snow", "water", "lava", "fire")


def tapa_vista(n):
    """True si un bloque tapa la vista de una cámara (muro, techo, hojas, tronco...). Plantas bajas, vidrio y agua no."""
    if n is None or n in MA.AIRE:
        return False
    b = n.split("[")[0]
    if b.endswith("_leaves") or _es_tronco(b):
        return True
    return not any(k in b for k in DELGADO)


def _bloque_en(m, x, y, z):
    return m.bloque(int(math.floor(x)), int(math.floor(y)), int(math.floor(z)))


def primer_choque(m, x, y, z, yaw, pitch, dist=40.0, paso=0.5):
    """Distancia (en bloques) hasta el primer bloque que tapa la vista, mirando desde (x, y, z) con yaw/pitch de Minecraft. None si no hay."""
    dx = -math.sin(math.radians(yaw)) * math.cos(math.radians(pitch))
    dz = math.cos(math.radians(yaw)) * math.cos(math.radians(pitch))
    dy = -math.sin(math.radians(pitch))
    d = 0.0
    while d <= dist:
        if tapa_vista(_bloque_en(m, x + dx * d, y + dy * d, z + dz * d)):
            return d
        d += paso
    return None


def segmento_libre(m, a, b, margen_final=1.5, paso=0.5):
    """True si entre a y b (puntos x, y, z) no hay bloques que tapen la vista (se ignora lo que está a menos de margen_final de b)."""
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    L = math.sqrt(dx * dx + dy * dy + dz * dz)
    d = 0.0
    while d < L - margen_final:
        t = d / L if L else 0
        if tapa_vista(_bloque_en(m, a[0] + dx * t, a[1] + dy * t, a[2] + dz * t)):
            return False
        d += paso
    return True


def yaw_pitch(a, b):
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    return math.degrees(math.atan2(-dx, dz)), -math.degrees(math.atan2(dy, math.hypot(dx, dz)))


def acomodar_camara(m, x, y, z, yaw, pitch, objetivo=None, min_libre=3.0):
    """Mueve la cámara (solo hacia arriba y hacia atrás) si está metida en un bloque (hojas, techo) o pegada a uno que tapa la
    vista. Con objetivo (x, y, z): la línea cámara-objetivo debe quedar libre (si no, sube y se echa para atrás y vuelve a apuntar).
    Sin objetivo: el primer bloque delante tiene que estar a min_libre bloques o más. Devuelve (x, y, z, yaw, pitch, nota).
    30-oct (prueba con el cliente): el objetivo suele ser un punto del suelo, y la línea siempre chocaba con ese mismo bloque;
    ahora se mira 1,5 bloques por encima del objetivo, y si no hay vista directa se usa el sitio libre más alto (no se rinde)."""
    if objetivo is not None:
        objetivo = (objetivo[0], objetivo[1] + 1.5, objetivo[2])

    def bueno(cx, cy, cz, yw, pt):
        if tapa_vista(_bloque_en(m, cx, cy, cz)) or tapa_vista(_bloque_en(m, cx, cy + 1, cz)):
            return False
        if objetivo is not None:
            return segmento_libre(m, (cx, cy, cz), objetivo)
        ch = primer_choque(m, cx, cy, cz, yw, pt, 40.0)
        return ch is None or ch >= min_libre
    if bueno(x, y, z, yaw, pitch):
        return x, y, z, yaw, pitch, ""
    if objetivo is not None:
        ox, oz = x - objetivo[0], z - objetivo[2]
    else:
        ox, oz = math.sin(math.radians(yaw)), -math.cos(math.radians(yaw))  # hacia atrás de la mirada
    norma = math.hypot(ox, oz) or 1.0
    ox, oz = ox / norma, oz / norma
    for atras in (0, 2, 4, 7):
        for arriba in (0, 2, 4, 6, 9, 12, 16, 22):
            if (atras, arriba) == (0, 0):
                continue
            cx, cy, cz = x + ox * atras, y + arriba, z + oz * atras
            yw, pt = (yaw_pitch((cx, cy, cz), objetivo) if objetivo is not None else (yaw, pitch))
            if bueno(cx, cy, cz, yw, pt):
                return cx, cy, cz, yw, pt, "cámara movida %d arriba y %d atrás porque había bloques tapando la vista" % (arriba, atras)
    if objetivo is not None:  # sin vista directa: el sitio más alto con la cámara en el aire, mirando al objetivo
        for arriba in (22, 16, 12, 9, 6):
            cx, cy, cz = x + ox * 4, y + arriba, z + oz * 4
            if not tapa_vista(_bloque_en(m, cx, cy, cz)) and not tapa_vista(_bloque_en(m, cx, cy + 1, cz)):
                yw, pt = yaw_pitch((cx, cy, cz), objetivo)
                return cx, cy, cz, yw, pt, "sin vista directa al objetivo: cámara subida %d y 4 atrás, mirando hacia él" % arriba
    return x, y, z, yaw, pitch, "AVISO: no encontré un lugar libre para la cámara; la foto puede salir tapada"


def _region_de(dimension):
    sub = {"the_nether": "DIM-1", "the_end": "DIM1"}.get(dimension, "")
    return os.path.join(RAIZ, os.environ.get("NIEBLA_SRV", "servidor"), R.propiedades().get("level-name", "world"), sub, "region")


def t_foto(x, y, z, yaw=0.0, pitch=20.0, autorizado=False, espera=7, ancho=640, dimension="overworld",
           objetivo_x=None, objetivo_y=None, objetivo_z=None, acomodar=True):
    """Foto real del juego. 29-oct (banco_ojo): 640 de ancho por defecto (cuesta menos); si salió la pantalla de MCA «¿Quién eres?»
    pasa los clics y repite (hasta 3 veces); y antes de tomarla comprueba en los .mca que la cámara no esté metida en hojas o
    techos (con objetivo_x/y/z, además mira que nada tape la línea hasta lo que se quiere ver; yaw y pitch se calculan solos)."""
    if not autorizado:
        return "Sin foto: solo se saca si Sergio lo autorizó en el chat. Mientras tanto usa corte_lateral, vista_cenital, planos o BlueMap.", None
    pids = procesos_prueba()
    if not pids:
        return "El cliente Prueba no está abierto. Ábrelo con abrir_cliente(autorizado=true) y espera a que entre.", None
    objetivo = None if objetivo_x is None else (float(objetivo_x), float(objetivo_y if objetivo_y is not None else y), float(objetivo_z))
    notas = []
    if objetivo is not None:
        yaw, pitch = yaw_pitch((x, y, z), objetivo)
    if acomodar:
        try:
            guardar_mundo()
            x, y, z, yaw, pitch, nota = acomodar_camara(Mundo(_region_de(dimension)), x, y, z, yaw, pitch, objetivo)
            if nota:
                notas.append(nota)
        except Exception as e:  # el chequeo es un extra: si falla, se saca la foto igual
            notas.append("(no se pudo revisar la cámara: %s)" % e)
    r = rcon("gamemode spectator Prueba", "execute in minecraft:%s run tp Prueba %.1f %.1f %.1f %.1f %.1f" % (dimension, x, y, z, yaw, pitch))
    if "No player" in r[1] or "No entity" in r[1]:
        return "Prueba todavía no está conectado al servidor: %s" % r[1], None
    time.sleep(max(2, min(int(espera), 30)))
    ps = os.path.join(RAIZ, "herramientas", "ojo_ventana.ps1")
    ruta = None
    for intento in range(4):
        zona = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps, "arriba", "-id", str(pids[0])],
                              capture_output=True, text=True).stdout.split()
        if len(zona) != 4:
            return "No encontré la ventana de Minecraft para la foto.", None
        ruta = nombre_salida("foto")
        try:
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "gdigrab", "-offset_x", zona[0],
                            "-offset_y", zona[1], "-video_size", "%sx%s" % (zona[2], zona[3]), "-draw_mouse", "0", "-i", "desktop",
                            "-frames:v", "1", "-vf", "scale=%d:-2" % int(ancho), ruta], check=True, capture_output=True)
        finally:
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps, "soltar", "-id", str(pids[0])], capture_output=True)
        menu, parecido = es_pantalla_mca(ruta)
        if not menu:
            break
        if intento == 3:
            notas.append("AVISO: la foto sigue tapada por la pantalla de MCA (parecido %.2f) después de 3 intentos de pasarla" % parecido)
            break
        notas.append("salió la pantalla de MCA (parecido %.2f): la paso y repito" % parecido)
        pasar_mca(pids[0])
        time.sleep(3)
    try:  # 4-nov: la pose queda junto a la foto, para que comparar_foto dibuje la misma vista con el Ojo
        fov = 70.0
        op = os.path.join(RAIZ, "juego_prueba", "options.txt")
        for l_ in open(op, encoding="utf-8", errors="replace"):
            if l_.startswith("fov:"):
                fov = 70.0 + 40.0 * float(l_.split(":", 1)[1])
        efecto = 1.0
        for l_ in open(op, encoding="utf-8", errors="replace"):
            if l_.startswith("fovEffectScale:"):
                efecto = float(l_.split(":", 1)[1])
        # en espectador se vuela, y Minecraft agranda el campo de visión un 10 % × fovEffectScale (medido: 76° → 83,6°)
        hora_juego = None
        try:  # la hora del juego (de día o de noche), para que comparar_foto dibuje con la misma luz
            import re as _re
            mh = _re.search(r"(\d+)", rcon("time query daytime")[0] or "")
            hora_juego = int(mh.group(1)) % 24000 if mh else None
        except Exception:
            pass
        json.dump({"x": x, "y": y, "z": z, "yaw": yaw, "pitch": pitch, "fov": fov, "fov_efectivo": fov * (1 + 0.1 * efecto),
                   "hora_juego": hora_juego,
                   "dimension": dimension,
                   "servidor": os.environ.get("NIEBLA_SRV", "servidor"), "hora": time.strftime("%Y-%m-%d %H:%M:%S")},
                  open(ruta[:-4] + ".json", "w", encoding="utf-8"))
    except Exception:
        pass
    return "Foto desde (%.1f, %.1f, %.1f) mirando yaw %.1f, pitch %.1f, %d px de ancho.%s\nArchivo: %s" % (
        x, y, z, yaw, pitch, int(ancho), ("\n" + "\n".join(notas)) if notas else "", ruta), ruta


def t_comparar_foto(ruta, radio=64):
    """4-nov: pone lado a lado una foto real (con su .json de pose, que guarda t_foto) y la misma vista dibujada por el Ojo,
    y mide cuánto se parecen (formas y colores). Sirve para calibrar el Ojo contra el juego."""
    import numpy as _n
    import ojo_vista3d as V3
    pose_ruta = ruta[:-4] + ".json"
    if not os.path.isfile(pose_ruta):
        return "La foto no tiene su pose (%s). Solo las fotos sacadas desde el 4-nov la guardan." % os.path.basename(pose_ruta), None
    pose = json.load(open(pose_ruta, encoding="utf-8"))
    foto = V3.leer_png(open(ruta, "rb").read())
    if foto is None:
        return "No pude leer la foto.", None
    foto = foto[:, :, :3]
    alto, ancho = foto.shape[:2]
    viejo = os.environ.get("NIEBLA_SRV")
    os.environ["NIEBLA_SRV"] = pose.get("servidor") or "servidor"  # siempre el mundo de la foto, no el que esté elegido
    try:
        m = Mundo(_region_de(pose.get("dimension", "overworld")))
        img, txt = V3.vista(m, decodificar_seccion, 0, 0, radio=radio, giro=pose["yaw"], inclinacion=pose["pitch"],
                            camara_en=(pose["x"], pose["y"] + 1.62, pose["z"]),
                            fov=pose.get("fov_efectivo", pose.get("fov", 70.0) * 1.1), ancho=ancho, alto=alto,
                            noche=13000 <= (pose.get("hora_juego") or 6000) <= 23000,
                            cuadricula=False)
    finally:
        if viejo is None:
            os.environ.pop("NIEBLA_SRV", None)
        else:
            os.environ["NIEBLA_SRV"] = viejo
    if img is None:
        return txt, None

    def chico(a):  # gris de 96 x 54 y sus bordes
        h, w = a.shape[:2]
        ys, xs = (_n.arange(54) * h // 54), (_n.arange(96) * w // 96)
        g = a[ys][:, xs].astype(_n.float32).mean(axis=2)
        b = _n.abs(_n.diff(g, axis=0))[:, :-1] + _n.abs(_n.diff(g, axis=1))[:-1, :]
        return g, b
    g1, b1 = chico(foto)
    g2, b2 = chico(img)
    color = 1 - float(_n.abs(foto[::8, ::8].astype(int) - img[::8, ::8].astype(int)).mean()) / 255
    bordes = float(_n.corrcoef(b1.ravel(), b2.ravel())[0, 1])
    lienzo = _n.concatenate([foto, _n.zeros((alto, 6, 3), _n.uint8), img], axis=1)
    salida = nombre_salida("comparar")
    V3.escribir_png(salida, lienzo)
    return ("Comparación: a la izquierda la foto real, a la derecha el Ojo desde la misma pose (%s).\nParecido de colores %.0f %%, "
            "de formas (bordes) %.2f (1 = iguales; más de 0,5 es buen calce).\n%s\nArchivo: %s") % (
        pose.get("hora", "?"), color * 100, bordes, txt.split("\n")[0], salida), salida


def t_mirar_hacia(x, y, z, objetivo_x, objetivo_y, objetivo_z):
    dx, dy, dz = objetivo_x - x, objetivo_y - y, objetivo_z - z
    return "yaw %.1f, pitch %.1f" % (math.degrees(math.atan2(-dx, dz)), -math.degrees(math.atan2(dy, math.hypot(dx, dz))))


def t_bluemap(x=X0, y=Y0, z=Z0, distancia=120, giro=0.0, inclinacion=0.9, vista="perspective"):
    url = "%s/#niebla_final:%d:%d:%d:%d:%.2f:%.2f:0:0:%s" % (BLUEMAP, x, y, z, distancia, giro, inclinacion, vista)
    try:
        import urllib.request
        urllib.request.urlopen(BLUEMAP, timeout=3).close()
        estado = "BlueMap responde."
    except Exception:
        estado = "BlueMap NO responde (¿servidor apagado?)."
    return ("%s\nAbre esta dirección en el navegador integrado: %s\n(distancia = zoom; giro en radianes; inclinación 0 = desde "
            "arriba, 1.5 = horizontal; vista: perspective, flat o free)") % (estado, url)


def t_estado():
    lineas = []
    if servidor_abierto():
        r = rcon("list", "bluemap")
        lineas += ["Servidor: abierto. " + r[0].strip(), "BlueMap: " + " ".join(r[1].split())[:400]]
    else:
        lineas.append("Servidor: apagado (la lectura de bloques sigue funcionando con lo último guardado).")
    lineas.append("Cliente Prueba: %s" % ("abierto" if procesos_prueba() else "cerrado"))
    lineas.append("Mundo: " + carpeta_region())
    return "\n".join(lineas)


PREGUNTAS_FOTO = ["¿Hay bloques de pasto sueltos en fila con huecos entre ellos (como dientes)?",
                  "¿Hay una casa o puesto más bajo que el terreno de al lado (hundido)?",
                  "¿Hay algún bloque flotando en el aire sin nada debajo?",
                  "¿Hay un camino o una puerta tapada por bloques?",
                  "¿Hay zanjas o hoyos en el pasto?",
                  "¿La escena está muy oscura?"]


def t_revisar_foto_local(ruta, preguntas=None):
    """29-oct (Sergio: «sácale más potencial a la IA local»): la IA local (qwen3.5:9b, que ve imágenes, en la RTX 5060)
    revisa la foto con una lista de preguntas de sí/no. Gasta 0 tokens de Claude; tarda ~3 min. Probada: encuentra
    dientes de pasto y casas hundidas, pero a veces marca de más (banderas como «flotantes"): Claude abre solo las
    fotos con algún SÍ. Una tarea de GPU a la vez: si ComfyUI o un render están corriendo, no usarla."""
    import base64
    import urllib.request
    # Las preguntas que mejor acertaron en el entrenamiento (ojo_entrenar.py) quedan en ojo_prompt.json.
    intro = "Foto de Minecraft. Responde cada pregunta con SÍ o NO y dónde (arriba/abajo/izquierda/derecha/centro):"
    archivo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ojo_prompt.json")
    modelo = "qwen3.5:9b"
    if os.path.isfile(archivo):
        aprendido = json.load(open(archivo, encoding="utf-8"))
        intro = aprendido["intro"]
        modelo = aprendido.get("modelo", modelo)  # 29-oct: el modelo que ganó con los casos propios
        preguntas = preguntas or list(aprendido["preguntas"].values())
    preguntas = preguntas or PREGUNTAS_FOTO
    texto = intro + "\n" + "\n".join("%d. %s" % (i + 1, q) for i, q in enumerate(preguntas))
    img = base64.b64encode(open(ruta, "rb").read()).decode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"model": modelo, "prompt": texto, "images": [img], "stream": False,
                                                  "think": True, "keep_alive": "2m", "options": {"num_ctx": 8192}}).encode())
    r = json.load(urllib.request.urlopen(req, timeout=900))
    texto_r = r.get("response", "").strip()
    resumen = ""
    try:  # 29-oct (ojo-vision): resumen con el mismo lector del entrenamiento, para no leer todo el texto
        import ojo_entrenar
        claves = list(aprendido["preguntas"]) if os.path.isfile(archivo) and preguntas == list(aprendido["preguntas"].values()) \
            else ["%d" % (i + 1) for i in range(len(preguntas))]
        res = ojo_entrenar.leer_respuestas(texto_r, len(preguntas))
        si = [k for k, v in zip(claves, res) if v == "SÍ"]
        resumen = "Resumen: %s%s\n" % ("SÍ en " + ", ".join(si) if si else "todo NO",
                                       " (no se pudo leer: %s)" % ", ".join(k for k, v in zip(claves, res) if v == "?") if "?" in res else "")
    except Exception:
        pass
    return "Revisión local de %s (%s):\n%s%s" % (os.path.basename(ruta), modelo, resumen, texto_r)


def t_isometrica(x1, z1, x2, z2, y_min=None, y_max=None, desde="sureste", tam=4):
    """Vista 3D isométrica de una zona (máx. 96 x 96) dibujada desde los .mca: sin cliente, sin pantalla, sin RAM extra.
    desde = sureste | suroeste | noroeste | noreste (de dónde mira la cámara). 29-oct, Sergio: «que me puedas mostrar desde
    diferentes ángulos gastando menos»."""
    if abs(x2 - x1) > 96 or abs(z2 - z1) > 96:
        return "Zona demasiado grande para la isométrica (máximo 96 x 96).", None
    guardar_mundo()
    m = Mundo()
    x1, x2, z1, z2 = min(x1, x2), max(x1, x2), min(z1, z2), max(z1, z2)
    if y_min is None or y_max is None:
        tops = [m.tope(x, z)[1] for x in range(x1, x2 + 1, 4) for z in range(z1, z2 + 1, 4)]
        y_min = min(tops) - 4 if y_min is None else y_min
        y_max = max(tops) + 2 if y_max is None else y_max

    def color(x, y, z, *_):
        n = m.bloque(x, y, z)
        return None if n is None or n in MA.AIRE else MA.color_de_bloque(n.split("[")[0]) or (128, 128, 128)
    ruta = nombre_salida("iso")
    pintar_iso(ruta, x1, z1, x2, z2, y_min, y_max, desde, max(2, min(int(tam), 8)), color)
    return ("Vista isométrica de (%d, %d) a (%d, %d), y %d a %d, mirando desde el %s.\nArchivo: %s"
            % (x1, z1, x2, z2, y_min, y_max, desde, ruta)), ruta


def giro_iso(W, D, desde):
    """Giro de la zona para que la cámara siempre mire «desde abajo a la derecha» de la imagen: (i, k) → (a, b), y A, B."""
    giro = {"sureste": lambda i, k: (i, k), "suroeste": lambda i, k: (D - 1 - k, i),
            "noroeste": lambda i, k: (W - 1 - i, D - 1 - k), "noreste": lambda i, k: (k, W - 1 - i)}[desde]
    return (giro,) + ((W, D) if desde in ("sureste", "noroeste") else (D, W))


def pintar_iso(ruta, x1, z1, x2, z2, y_min, y_max, desde, T, color):
    """Igual que pintar_iso_img pero guarda un PNG. Devuelve (A, B)."""
    img, A, B = pintar_iso_img(x1, z1, x2, z2, y_min, y_max, desde, T, color)
    MA.escribir_png(ruta, img, escala=1)
    return A, B


def pintar_iso_img(x1, z1, x2, z2, y_min, y_max, desde, T, color):
    """Dibuja cubos isométricos; color(x, y, z, a, b) da el RGB de cada bloque o None si no se dibuja (aire, muro
    recortado). a y b crecen hacia la cámara (a = A-1 o b = B-1: la orilla de adelante). Devuelve (matriz RGB, A, B)."""
    W, D = x2 - x1 + 1, z2 - z1 + 1
    giro, A, B = giro_iso(W, D, desde)
    H = y_max - y_min + 1
    ancho, alto = (A + B) * T + 2, (A + B) * T // 2 + H * T + 2
    img = [[CIELO] * ancho for _ in range(alto)]

    def tono(c, f):
        return tuple(max(0, min(255, int(v * f))) for v in c)

    def pintar(px, py, c):
        if 0 <= px < ancho and 0 <= py < alto:
            img[py][px] = c
    # orden de dibujo: de atrás hacia adelante (a + b crece) y de abajo hacia arriba
    celdas = []
    for i in range(W):
        for k in range(D):
            a, b = giro(i, k)
            celdas.append((a + b, a, b, x1 + i, z1 + k))
    celdas.sort()
    for _, a, b, x, z in celdas:
        for y in range(y_min, y_max + 1):
            c = color(x, y, z, a, b)
            if c is None:
                continue
            ox = (a - b) * T + B * T          # esquina izquierda del rombo de arriba
            oy = (a + b) * T // 2 + (y_max - y) * T
            for dy in range(T):                # cara de arriba (rombo)
                w = (dy + 1) * 2 if dy < T // 2 else (T - dy) * 2
                for dx in range(T - w // 2, T + w // 2):
                    pintar(ox + dx, oy + dy, tono(c, 1.0))
            for dy in range(T):                # caras de los lados (izquierda más clara, derecha más oscura)
                for dx in range(T):
                    pintar(ox + dx, oy + T // 2 + dy + dx // 2, tono(c, 0.78))
                    pintar(ox + T + dx, oy + T // 2 + dy + (T - 1 - dx) // 2, tono(c, 0.6))
    return img, A, B


def _zona_3d(x, z, casa):
    """Centro de la vista 3D: una casa del plano (su caja) o (x, z); por defecto la fogata del pueblo."""
    if casa is not None:
        return resolver_casa(casa)[1], X0, Z0
    return None, (X0 if x is None else int(x)), (Z0 if z is None else int(z))


def t_vista_3d(x=None, z=None, y=None, radio=50, desde="sureste", giro=None, inclinacion=35, proyeccion="orto",
               distancia=None, casa=None, noche=False, sin_hojas=False, seres=False, y_min=None, y_max=None, ancho=1100,
               alto=760, marcas=None, marcar=False, cuadricula=True, camara_x=None, camara_y=None, camara_z=None, fov=70):
    """4-nov-2026 (Sergio: «que pueda ver casi 100 bloques de todos los ángulos»): vista 3D con las formas y texturas
    reales de cada bloque (vanilla y mods), desde cualquier ángulo, sin abrir el juego (ojo_vista3d.py)."""
    import ojo_vista3d as V3
    guardar_mundo()
    caja, cx, cz = _zona_3d(x, z, casa)
    img, txt = V3.vista(Mundo(), decodificar_seccion, cx, cz, radio=radio, y_min=y_min, y_max=y_max, giro=giro, desde=desde,
                        inclinacion=inclinacion, proyeccion=proyeccion, distancia=distancia,
                        ancho=max(320, min(int(ancho), 1800)), alto=max(240, min(int(alto), 1200)), sin_hojas=sin_hojas,
                        marcas=[tuple(mk) for mk in (marcas or [])], cy=y, noche=noche, caja=caja, seres=seres,
                        resaltar_muebles=marcar, categoria=categoria, contenido_fn=contenidos, cuadricula=cuadricula,
                        camara_en=None if camara_x is None else (float(camara_x), float(camara_y) + 1.62, float(camara_z)),
                        fov=float(fov))
    if img is None:
        return txt, None
    ruta = nombre_salida("vista3d")
    V3.escribir_png(ruta, img)
    return txt + "\nArchivo: " + ruta, ruta


def t_vista_vuelta(x=None, z=None, radio=50, inclinacion=35, casa=None, noche=False, sin_hojas=False, seres=False,
                   y_max=None, con_arriba=True, ancho=1500, marcar=False):
    """La misma zona desde los 4 rincones y desde arriba, en UNA imagen (cuesta como una foto grande)."""
    import ojo_vista3d as V3
    guardar_mundo()
    caja, cx, cz = _zona_3d(x, z, casa)
    img, txt = V3.vuelta(Mundo(), decodificar_seccion, cx, cz, radio=radio, inclinacion=inclinacion, con_arriba=con_arriba,
                         ancho=max(600, min(int(ancho), 2000)), noche=noche, sin_hojas=sin_hojas, seres=seres, y_max=y_max,
                         caja=caja, resaltar_muebles=marcar, categoria=categoria)
    if img is None:
        return txt, None
    ruta = nombre_salida("vuelta3d")
    V3.escribir_png(ruta, img)
    return txt + "\nArchivo: " + ruta, ruta


def t_vista_pisos(casa, desde="sureste", inclinacion=60, seres=True, marcar=True, noche=False, ancho=1500):
    """4-nov (ronda 2): la casa piso por piso, como con rayos X. Cada piso se dibuja en 3D cortado a 2 bloques sobre su
    piso (se ven muebles, camas, cofres y seres sin que el piso de arriba los tape), con su resumen debajo en el texto."""
    import numpy as _n
    import ojo_vista3d as V3
    guardar_mundo()
    nombre, caja = resolver_casa(casa)
    m = Mundo()
    c = Casa(m, caja, nombre)
    if c.vacia or not c.alturas:
        return "%s: sin pisos techados que mostrar." % nombre, None
    alturas = []
    for y in c.alturas:  # pisos a 1 o 2 bloques del anterior (medio escalón, entrepiso) se ven en el mismo corte
        if not alturas or y - alturas[-1] >= 3:
            alturas.append(y)
    alturas = alturas[:6]
    cols = min(3, len(alturas))
    filas = (len(alturas) + cols - 1) // cols
    w = ancho // cols
    h = int(w * 0.75)
    lienzo = _n.zeros((filas * h, cols * w, 3), _n.uint8)
    partes = []
    for i, y in enumerate(alturas):
        img, txt = V3.vista(m, decodificar_seccion, 0, 0, caja=caja, y_min=y - 2, y_max=y + 1, desde=desde,
                            inclinacion=inclinacion, ancho=w, alto=h, seres=seres, resaltar_muebles=marcar,
                            categoria=categoria, noche=noche, contenido_fn=contenidos)
        if img is None:
            continue
        V3.letrero(img, 8, 8, "PISO", 2)
        V3.letrero_crudo(img, 70, 10, str(i + 1), 2, (255, 255, 255))
        lienzo[(i // cols) * h:(i // cols + 1) * h, (i % cols) * w:(i % cols + 1) * w] = img
        cuartos = [cu for cu in c.cuartos if y <= cu["y"] < (alturas[i + 1] if i + 1 < len(alturas) else y + 3)]
        avisos = [a for cu in cuartos for a in cu["avisos"] if not a.startswith("nota")]
        resumen = "; ".join("%s %s (%d bloques, luz media %.0f)" % (cu["id"], c.tipo_cuarto(cu), len(cu["celdas"]), cu["luz_media"])
                            for cu in cuartos)
        lineas = [l for l in txt.split("\n") if l.startswith(("En la zona", "Etiquetas", "  ", "Seres"))]
        partes.append("## Piso %d (pies en y %d; se dibuja de y %d a %d)\nCuartos: %s%s\n%s" % (
            i + 1, y, y - 2, y + 1, resumen or "ninguno adentro (porche o techado)",
            ("\n⚠ " + "\n⚠ ".join(avisos)) if avisos else "", "\n".join(lineas)))
    for k in range(1, cols):
        lienzo[:, k * w - 1:k * w + 1] = 0
    for k in range(1, filas):
        lienzo[k * h - 1:k * h + 1, :] = 0
    ruta = nombre_salida("pisos3d")
    V3.escribir_png(ruta, lienzo)
    return ("%s piso por piso (de izquierda a derecha y de arriba a abajo), vistos desde el %s, cortados a la altura de la cabeza (1 bloque sobre los pies) de "
            "cada piso. Muebles teñidos por tipo, números = lista de abajo; palitos de color = seres.\n\n%s\nArchivo: %s") % (
        nombre, desde, "\n\n".join(partes), ruta), ruta


def t_contar_bloques(x1=None, y1=None, z1=None, x2=None, y2=None, z2=None, casa=None, x=None, z=None, radio=None,
                     buscar=None, max_lista=40):
    """Cuenta los bloques de una caja (o casa, o radio alrededor de un punto): cuántos hay de cada tipo, la caja que
    ocupan de verdad (largo, ancho, alto) y, con buscar, dónde está cada uno de los que coinciden."""
    import numpy as _n
    import ojo_vista3d as V3
    guardar_mundo()
    m = Mundo()
    if casa is not None:
        nombre, c = resolver_casa(casa)
        x1, z1, x2, z2 = c["x0"], c["z0"], c["x1"], c["z1"]
        titulo = nombre
    elif radio is not None:
        cx, cz, rr = (X0 if x is None else int(x)), (Z0 if z is None else int(z)), int(radio)
        x1, z1, x2, z2 = cx - rr, cz - rr, cx + rr, cz + rr
        titulo = "radio %d alrededor de (%d, %d)" % (rr, cx, cz)
    else:
        if None in (x1, z1, x2, z2):
            return "Falta la zona: x1, z1, x2, z2 (y si quieres y1, y2), o casa, o x, z y radio."
        titulo = "caja"
    x1, x2, z1, z2 = min(x1, x2), max(x1, x2), min(z1, z2), max(z1, z2)
    if (x2 - x1 + 1) * (z2 - z1 + 1) * 64 > 25_000_000 and (y1 is None or y2 is None or
                                                          (x2 - x1 + 1) * (z2 - z1 + 1) * (abs(y2 - y1) + 1) > 25_000_000):
        return "Zona demasiado grande (%d x %d bloques de lado). Achícala o usa un radio menor (hasta unos 600 x 600 con poca altura)." % (
            x2 - x1 + 1, z2 - z1 + 1)
    if "DIM-1" in m.g.carpeta and (y1 is None or y2 is None):  # Nether: el techo de piedra base engaña; todo el alto de adentro
        y1, y2 = (0 if y1 is None else y1), (127 if y2 is None else y2)
    auto_y = y1 is None or y2 is None
    if auto_y:  # alto: del terreno natural más bajo al techo más alto de la zona
        cols = [(a, b) for a in range(x1, x2 + 1, 2) for b in range(z1, z2 + 1, 2)]
        tops = [t for t in (m.tope(a, b)[1] for a, b in cols) if t is not None]
        if not tops:
            return "No hay chunks generados en esa zona."
        piso = max(tops) - 64  # tope: no bajar más de 64 bloques bajo el techo más alto

        def _natural(a, b):  # baja por aire y bloques construidos (techos, pisos, muebles) hasta tierra o piedra
            nom, y = m.tope(a, b)
            while y is not None and y > piso and nom is not None and nom not in NATURAL:
                y -= 1
                nom = m.bloque(a, y, b)
            return y
        suelos = [t for t in (_natural(a, b) for a, b in cols) if t is not None]
        y1 = (max(min(suelos), piso) - 3) if y1 is None else y1
        y2 = (max(tops)) if y2 is None else y2
    y1, y2 = min(y1, y2), max(y1, y2)
    celdas = (x2 - x1 + 1) * (y2 - y1 + 1) * (z2 - z1 + 1)
    if celdas > 25_000_000:
        return "Zona demasiado grande (%d bloques; máximo 25 millones). Achícala o usa un radio menor." % celdas
    vol, estados, falta = V3.volumen(m, decodificar_seccion, x1, x2, y1, y2, z1, z2)
    nombres = [e[0].replace("minecraft:", "") for e in estados]
    ids, cuenta = _n.unique(vol[vol > 0], return_counts=True)
    por = {}
    for i, k in zip(ids, cuenta):
        por[nombres[i]] = por.get(nombres[i], 0) + int(k)
    total = int(sum(por.values()))
    lin = ["Bloques de %s: x %d..%d, y %d..%d, z %d..%d (%d x %d x %d = %d lugares). %d bloques que no son aire, de %d tipos.%s" % (
        titulo, x1, x2, y1, y2, z1, z2, x2 - x1 + 1, y2 - y1 + 1, z2 - z1 + 1, celdas, total, len(por),
        (" Ojo: %d chunks sin generar." % falta) if falta else "")]
    hay = _n.argwhere(vol > 0)
    if len(hay):
        a, b = hay.min(axis=0), hay.max(axis=0)
        lin.append("Lo que hay ocupa de x %d a %d, de y %d a %d y de z %d a %d: %d de largo (este-oeste), %d de ancho (norte-sur) "
                   "y %d de alto." % (a[0] + x1, b[0] + x1, a[1] + y1, b[1] + y1, a[2] + z1, b[2] + z1, b[0] - a[0] + 1,
                                      b[2] - a[2] + 1, b[1] - a[1] + 1))
    if auto_y:
        lin.append("La altura (y %d..%d) se eligió sola, del terreno natural al techo más alto de la zona; si buscas algo más "
                   "arriba o más abajo, fíjala con y1 e y2." % (y1, y2))
    lin.append("\nDe cada tipo (de más a menos):")
    orden = sorted(por.items(), key=lambda t: -t[1])
    max_lista = max(5, int(max_lista))
    for n, k in orden[:max_lista]:
        lin.append("  %6d  %s" % (k, n))
    if len(orden) > max_lista:
        lin.append("  … y %d tipos más (%d bloques)" % (len(orden) - max_lista, sum(k for _, k in orden[max_lista:])))
    if buscar:
        claves = [b.strip().lower() for b in str(buscar).split(",") if b.strip()]
        ok = [i for i, n in enumerate(nombres) if i and any(c in n.lower() for c in claves)]
        pos = _n.argwhere(_n.isin(vol, ok)) if ok else _n.zeros((0, 3), int)
        lin.append("\nBuscar «%s»: %d bloques%s" % (buscar, len(pos), ":" if len(pos) else "."))
        for (a, b, c) in pos[:60]:
            lin.append("  (%d, %d, %d) %s" % (a + x1, b + y1, c + z1, nombres[vol[a, b, c]]))
        if len(pos) > 60:
            lin.append("  … y %d más" % (len(pos) - 60))
    return "\n".join(lin)


def t_medir(x1, y1, z1, x2, y2, z2):
    """Distancia entre dos puntos en bloques: en línea recta, por el suelo (sin contar la altura) y hacia dónde queda."""
    import ojo_vista3d as V3
    dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
    recta, plano = math.sqrt(dx * dx + dy * dy + dz * dz), math.hypot(dx, dz)
    rumbo = V3.rumbo((dx, dz)) if (dx or dz) else "el mismo lugar"
    return ("De (%d, %d, %d) a (%d, %d, %d):\n  en línea recta %.1f bloques; por el suelo (sin la altura) %.1f; caminando en "
            "cuadrícula (sin diagonales) %d.\n  Diferencias: x %+d (%s), z %+d (%s), y %+d (%s).\n  Queda hacia el %s del primer "
            "punto.") % (x1, y1, z1, x2, y2, z2, recta, plano, abs(dx) + abs(dz), dx, "este" if dx > 0 else "oeste" if dx < 0 else "igual",
                         dz, "sur" if dz > 0 else "norte" if dz < 0 else "igual", dy, "sube" if dy > 0 else "baja" if dy < 0 else "igual",
                         rumbo)


def t_fotos_angulos(x, y, z, distancia=14, altura=7, autorizado=False, ancho=480, dimension="overworld"):
    """4 fotos reales del juego alrededor de un punto (norte, este, sur, oeste) juntadas en UNA imagen de 2 x 2: cuesta como
    una sola foto. Necesita el cliente Prueba abierto."""
    if not autorizado:
        return "Sin fotos: solo si Sergio lo autorizó en el chat.", None
    rutas = []
    for nombre, (dx, dz) in (("norte", (0, -1)), ("este", (1, 0)), ("sur", (0, 1)), ("oeste", (-1, 0))):
        cx, cz = x + dx * distancia, z + dz * distancia
        ddx, ddy, ddz = x - cx, -altura, z - cz
        yaw, pitch = math.degrees(math.atan2(-ddx, ddz)), -math.degrees(math.atan2(ddy, math.hypot(ddx, ddz)))
        txt, r = t_foto(cx, y + altura, cz, yaw, pitch, True, 4, ancho, dimension, objetivo_x=x, objetivo_y=y, objetivo_z=z)
        if not r:
            return txt, None
        rutas.append(r)
    ruta = nombre_salida("angulos")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + sum([["-i", r] for r in rutas], []) +
                   ["-filter_complex", "[0][1]hstack[a];[2][3]hstack[b];[a][b]vstack", ruta], check=True)
    return ("4 ángulos de (%.0f, %.0f, %.0f): arriba norte | este, abajo sur | oeste.\nArchivo: %s" % (x, y, z, ruta)), ruta


def t_usar_servidor(cual="sergio"):
    """29-oct (Sergio: «si al mcp le falta algo para ver de verdad, lo mejoramos»): el Ojo trabaja con el servidor de Sergio
    o con el de pruebas (aldea completa). Cambia RCON, los .mca que lee y a qué servidor entra el cliente Prueba."""
    if cual == "pruebas":
        os.environ["NIEBLA_SRV"] = "servidor_pruebas"
    else:
        os.environ.pop("NIEBLA_SRV", None)
    return "Ahora el Ojo usa el servidor %s. Mundo: %s. Si el cliente Prueba ya estaba abierto, ciérralo y ábrelo de nuevo para que entre a este." % (
        "de pruebas (25566)" if cual == "pruebas" else "de Sergio (25565)", carpeta_region())


# --- Interiores (29-oct-2026, Sergio: «el Ojo necesita más potencia para ver la decoración por dentro») ---
# Todo sale de los .mca, sin abrir el juego. Un cuarto es un piso con techo y paredes dentro de la caja de una casa del
# plano; sus muebles son lo que no es muro. Reconoce vanilla, mcw-furniture, handcrafted, another_furniture y
# supplementaries. La luz es la que el servidor guardó en cada chunk (BlockLight): con 0 aparecen monstruos.
# Desde el 29-oct (ojo-vision) también ve las entidades de entities/ (cuadros, marcos, soportes de armadura, vecinos,
# animales, monstruos, objetos tirados) y lo que hay dentro de cada cofre.

# (clave, nombre para el informe, color, ícono 5x5). El orden decide qué ícono gana cuando una celda tiene varias cosas.
CATEGORIAS = [
    ("puerta", "puertas", (125, 75, 30), (".###.", ".#.#.", ".#.##", ".#.#.", ".###.")),
    ("cama", "camas", (200, 35, 45), (".....", "##...", "#####", "#####", "#...#")),
    ("cofre", "cofres y cajones", (215, 145, 25), ("#####", "#...#", "##.##", "#...#", "#####")),
    ("oficio", "mesas de oficio", (85, 85, 105), ("#####", "#.#.#", "#####", ".#.#.", "##.##")),
    ("mesa", "mesas", (140, 85, 40), ("#####", "#####", ".#.#.", ".#.#.", ".#.#.")),
    ("asiento", "asientos", (95, 55, 150), ("#....", "#....", "#####", "#...#", "#...#")),
    ("estante", "estantes y libros", (150, 60, 120), ("#####", "#.#.#", "#####", "#.#.#", "#####")),
    ("planta", "plantas y macetas", (40, 150, 45), ("#.#.#", ".###.", "..#..", ".###.", ".###.")),
    ("luz", "luces", (255, 215, 30), ("..#..", ".###.", "#####", ".###.", "..#..")),
    ("adorno", "adornos", (230, 110, 150), (".....", ".###.", ".###.", ".###.", ".....")),
    ("escalera", "escaleras de mano", (165, 120, 65), ("#...#", "#####", "#...#", "#####", "#...#")),
    ("alfombra", "alfombras", (190, 70, 70), None),
    ("otro", "otros muebles", (0, 165, 175), (".....", ".#.#.", "..#..", ".#.#.", ".....")),
    # 29-oct (ojo-vision): entidades (viven en entities/, no son bloques)
    ("armadura", "soportes de armadura", (120, 130, 150), ("..#..", "#####", "..#..", ".#.#.", ".#.#.")),
    ("cuadro", "cuadros y marcos", (175, 120, 60), ("#####", "#.#.#", "##.##", "#.#.#", "#####")),
    ("aldeano", "vecinos", (30, 110, 230), (".###.", ".###.", "..#..", "#####", ".#.#.")),
]
CAT = {c[0]: c for c in CATEGORIAS}
MUEBLES = [c[0] for c in CATEGORIAS if c[0] not in ("puerta", "alfombra", "escalera", "aldeano")]
SER_CAT = {"cuadro": "cuadro", "marco": "cuadro", "armadura": "armadura", "aldeano": "aldeano"}
MODS_MUEBLE = ("handcrafted:", "another_furniture:", "mcwfurnitures:", "supplementaries:", "mcwlights:")
PASA = ("carpet", "doormat", "torch", "_sign", "banner", "button", "vine", "rail", "redstone_wire", "grass", "fern", "flower",
        "tulip", "orchid", "allium", "bluet", "daisy", "cornflower", "lily_of_the_valley", "poppy", "dandelion", "lever",
        "tripwire", "petals", "dead_bush", "sapling", "cobweb", "ladder", "scaffolding", "wall_head", "wall_skull", "lichen",
        "pressure_plate")


def _tiene(n, *claves):
    return any(k in n for k in claves)


def categoria(nombre, props=None):
    """Qué es un bloque dentro de una casa: una clave de CATEGORIAS, «muro» (paredes, pisos, techos, ventanas) o None
    (aire o chunk sin generar). Una vela o fogata apagada cuenta como adorno, no como luz."""
    if nombre is None or nombre in MA.AIRE:
        return None
    n = nombre.split("[")[0]
    c = n.split(":")[-1]
    props = props or {}
    if c.endswith("_door") or "big_door" in c:
        return "puerta"
    if c in ("water", "lava", "bubble_column"):
        return "muro"
    if c.endswith("bed") and c != "bedrock":
        return "cama"
    if "pressure_plate" in c:
        return "muro"
    if (_tiene(c, "lantern", "torch", "lamp", "candle", "campfire", "glowstone", "shroomlight", "chandelier", "froglight", "end_rod",
               "sconce") or n.startswith("mcwlights:") or c in ("fire", "soul_fire")):
        return "adorno" if props.get("lit") == "false" else "luz"
    if _tiene(c, "chest", "barrel", "drawer", "cupboard", "cabinet", "wardrobe", "storage", "container", "crate", "safe",
              "shulker_box", "sack", "trunk"):
        return "cofre"
    if _tiene(c, "crafting_table", "furnace", "smoker", "anvil", "grindstone", "loom", "cartography", "fletching", "smithing",
              "stonecutter", "brewing_stand", "cauldron", "lectern", "composter", "enchanting", "oven", "sink", "stove", "kitchen",
              "beehive", "bell", "jukebox", "note_block", "bellows"):
        return "oficio"
    if _tiene(c, "table", "desk", "counter", "nightstand"):
        return "mesa"
    if _tiene(c, "chair", "stool", "bench", "sofa", "couch", "cushion", "seat", "throne"):
        return "asiento"
    if _tiene(c, "bookshelf", "shelf", "book", "notice_board", "blackboard", "globe"):
        return "estante"
    if c.startswith("potted_") or _tiene(c, "flower_pot", "flower_box", "planter", "thick_pot", "vase"):
        return "planta"
    if _tiene(c, "carpet", "doormat", "rug"):
        return "alfombra"
    if _tiene(c, "ladder", "scaffolding"):
        return "escalera"
    if _tiene(c, "banner", "skull", "_head", "cobweb", "_sign", "jar", "cake", "goblet", "pancake", "basket", "decorated_pot",
              "dragon_egg", "hay_block", "amethyst", "sea_pickle", "plate", "cup", "bowl", "hourglass", "clock", "statue",
              "pedestal", "urn", "cage", "soap", "target", "faucet", "candle_holder", "cannon"):
        return "adorno"
    if (es_estructura(n) or _tiene(c, "shutter", "awning", "wicker_fence", "rope", "path", "flagstone", "iron_bars", "pane", "chain",
                                   "button", "lever", "tripwire", "rail", "redstone", "piston", "dispenser", "daylight",
                                   "lightning_rod", "fence", "_wall", "copper", "_block", "_ore", "concrete", "glass", "quartz",
                                   "basalt", "blackstone", "prismarine", "purpur", "netherrack", "obsidian", "bedrock")):
        return "otro" if c in ("note_block", "honey_block", "slime_block") else "muro"
    if es_planta(n):
        return "planta"
    return "otro"


def se_pasa(nombre, props=None):
    """True si se puede caminar a través (aire, alfombra, antorcha, letrero, puerta...)."""
    if nombre is None:
        return False
    if nombre in MA.AIRE:
        return True
    c = nombre.split("[")[0].split(":")[-1]
    if c.endswith("_door") or "big_door" in c or c.endswith("fence_gate"):
        return True
    if c == "snow":
        return int((props or {}).get("layers", 1)) <= 1
    if _tiene(c, "pot", "box", "_block") or c.endswith("_bed"):
        return False
    return _tiene(c, *PASA)


ALIAS_CASA = {"castillo": "medieval_castle", "ciudadela": "medieval_castle", "iglesia": "church", "taberna": "tavern",
              "panaderia": "bakery", "herreria": "blacksmith", "molino": "windmill", "establo": "stable", "granja": "farm_house",
              "fortaleza": "villagefortress", "aserradero": "lumbermill", "torre": "guard_tower", "templo": "temple",
              "biblioteca": "library", "porton": "gate"}


def resolver_casa(casa):
    """(nombre, caja) de una casa del plano por número («0»), nombre («tavern», «0:tavern») o parte del nombre."""
    cajas = [(n, c) for n, c in cajas_casas() if n != "parque"]
    s = str(casa).strip().lower()
    s = ALIAS_CASA.get(s.replace("á", "a").replace("í", "i").replace("é", "e"), s)  # 4-nov: nombres en español
    for n, c in cajas:
        if s == n.split(":")[0] or s == n.lower():
            return n, c
    for n, c in cajas:
        if s == n.split(":", 1)[-1].lower() or s in n.lower():
            return n, c
    raise ValueError("No encuentro la casa «%s». Casas del plano: %s" % (casa, ", ".join(n for n, _ in cajas)))


def _caja(casa, x1, z1, x2, z2):
    if casa is not None:
        return resolver_casa(casa)
    if None in (x1, z1, x2, z2):
        raise ValueError("Falta la casa (casa=número o nombre del plano) o la zona (x1, z1, x2, z2).")
    return "zona", {"x0": min(x1, x2), "x1": max(x1, x2), "z0": min(z1, z2), "z1": max(z1, z2)}


class Casa:
    """Cuartos, muebles, luz, puertas y paso de una caja (x0..x1, z0..z1), leídos de los .mca con todas las propiedades."""

    def __init__(self, m, caja, nombre="zona"):
        self.m, self.nombre = m, nombre
        self.x0, self.x1, self.z0, self.z1 = caja["x0"], caja["x1"], caja["z0"], caja["z1"]
        self.cache, self.techos = {}, {}
        tops = [m.tope(x, z)[1] for x in range(self.x0, self.x1 + 1) for z in range(self.z0, self.z1 + 1)]
        tops = [t for t in tops if t is not None]
        anillo = [m.suelo(x, z)[1] for x in range(self.x0 - 2, self.x1 + 3) for z in (self.z0 - 2, self.z1 + 2)] + \
                 [m.suelo(x, z)[1] for z in range(self.z0 - 2, self.z1 + 3) for x in (self.x0 - 2, self.x1 + 2)]
        anillo = sorted(a for a in anillo if a is not None)
        self.vacia = not tops or not anillo
        if self.vacia:
            return
        self.y_suelo = anillo[len(anillo) // 2]  # el terreno de afuera (mediana del contorno)
        self.y_bajo, self.y_alto = min(anillo[0], self.y_suelo) - 10, max(tops) + 1
        self._cuartos()
        self._seres()
        self._puertas()
        self._paso()
        self._revisar()

    # --- lectura con caché ---
    def e(self, x, y, z):
        k = (x, y, z)
        if k not in self.cache:
            n, p = self.m.estado(x, y, z)
            self.cache[k] = (n, p, categoria(n, p), se_pasa(n, p))
        return self.cache[k][:2]

    def cat(self, x, y, z):
        self.e(x, y, z)
        return self.cache[(x, y, z)][2]

    def pasa(self, x, y, z):
        self.e(x, y, z)
        return self.cache[(x, y, z)][3]

    def apoyo(self, x, y, z):  # se puede pararse encima
        n = self.e(x, y, z)[0]
        return n is not None and not se_pasa(n, self.e(x, y, z)[1])

    def de_pie(self, x, y, z):
        return self.pasa(x, y, z) and self.pasa(x, y + 1, z) and self.apoyo(x, y - 1, z) and self.cat(x, y, z) != "puerta"

    def techado(self, x, y, z):
        if (x, z) not in self.techos:
            self.techos[(x, z)] = [yy for yy in range(self.y_bajo, self.y_alto + 16)
                                   if self.cat(x, yy, z) == "muro" and not self.pasa(x, yy, z)]
        return any(y + 2 <= t <= y + 14 for t in self.techos[(x, z)])

    def dentro(self, x, z, margen=0):
        return self.x0 - margen <= x <= self.x1 + margen and self.z0 - margen <= z <= self.z1 + margen

    # --- cuartos: por cada altura, celdas con techo, piso de muro abajo y sin muro a la altura de la cabeza ---
    def _hueco(self, x, y, z):
        c0, c1 = self.cat(x, y, z), self.cat(x, y + 1, z)
        if c0 == "puerta" or c1 == "puerta" or self.cat(x, y - 1, z) not in ("muro", "cofre") or self.pasa(x, y - 1, z):
            return False  # el piso es muro (o un cofre hundido en el piso)
        libre = lambda c, yy: self.pasa(x, yy, z) or c not in ("muro", None)  # noqa: E731
        return libre(c0, y) and libre(c1, y + 1) and self.techado(x, y, z)

    def _encerrado(self, x, y, z):
        """Adentro de verdad: mirando a la altura de la cabeza, 3 de los 4 lados chocan con pared o puerta (un porche o
        un alero da al aire por 2 o más lados; los postes de cerca no cuentan como pared)."""
        choques = 0
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            for k in range(1, 17):
                a, b = x + dx * k, z + dz * k
                if not self.dentro(a, b, 1) or not self.techado(a, y, b) and self.pasa(a, y + 1, b):
                    break
                if self._pared(a, y, b):
                    choques += 1
                    break
        return choques >= 3

    def _pared(self, x, y, z):
        """Hay pared (o puerta) en (x, z) a la altura de los pies o de la cabeza. Una ventana sin vidrio sobre un muro
        bajo sigue siendo pared; los postes de cerca, muros finos y rejas de un porche no cuentan."""
        for yy in (y, y + 1):
            n = self.e(x, yy, z)[0] or ""
            if self.cat(x, yy, z) == "puerta" or (self.cat(x, yy, z) == "muro" and not self.pasa(x, yy, z)
                                                  and not _tiene(n, "fence", "_wall", "iron_bars", "chain")):
                return True
        return False

    def _abierto(self, y, comp, adentro):
        """Parte del borde de un cuarto que da a una zona que no está encerrada (sin pared, puerta ni mueble)."""
        cs = set(comp)
        abierto = total = 0
        for x, z in comp:
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                v = (x + dx, z + dz)
                if v not in cs:
                    total += 1
                    abierto += v not in adentro and not self._pared(v[0], y, v[1]) and self.pasa(v[0], y + 1, v[1])
        return abierto / max(1, total)

    def _cuartos(self):
        self.cuartos = []
        for y in range(self.y_bajo, self.y_alto):
            huecos = {(x, z) for x in range(self.x0, self.x1 + 1) for z in range(self.z0, self.z1 + 1) if self._hueco(x, y, z)}
            adentro = {c for c in huecos if self._encerrado(c[0], y, c[1])}
            todos_adentro = set(adentro)
            for celdas, porche in ((adentro, False), (huecos - adentro, True)):
                while celdas:
                    pila, comp = [celdas.pop()], []
                    while pila:
                        c = pila.pop()
                        comp.append(c)
                        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            v = (c[0] + dx, c[1] + dz)
                            if v in celdas:
                                celdas.remove(v)
                                pila.append(v)
                    libres = [c for c in comp if self.de_pie(c[0], y, c[1])]
                    if not porche and self._abierto(y, comp, todos_adentro) > 0.4:
                        porche = True  # pasillo exterior entre pilares: casi todo su borde da al aire
                    xs, zs = [q[0] for q in comp], [q[1] for q in comp]
                    if len(comp) <= 6 and min(max(xs) - min(xs), max(zs) - min(zs)) == 0 and not any(
                            self.cat(q[0], yy, q[1]) in MUEBLES and self.cat(q[0], yy, q[1]) != "luz"
                            for q in comp for yy in (y, y + 1, y + 2)):
                        continue  # nicho de ventana o hueco en un muro grueso (1 de ancho, sin muebles): no es un cuarto
                    if libres and len(comp) >= 3 and (not porche or len(comp) >= 6):
                        self.cuartos.append(self._cuarto(y, comp, libres, porche))
        # pisos: cada altura con algún cuarto de adentro; el más bajo bajo tierra es sótano
        alturas = sorted({c["y"] for c in self.cuartos if not c["porche"]})
        for c in self.cuartos:
            c["piso"] = alturas.index(c["y"]) + 1 if c["y"] in alturas else 0
        self.alturas = alturas
        letras = {}
        for c in sorted(self.cuartos, key=lambda c: (c["y"], c["x"][0], c["z"][0])):
            letras[c["y"]] = letras.get(c["y"], 0) + 1
            c["id"] = "y%d-%s" % (c["y"], "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[(letras[c["y"]] - 1) % 26])

    def _cuarto(self, y, comp, libres, porche):
        cosas, altos = [], []
        for x, z in comp:
            h = 0
            while h < 6 and not (self.cat(x, y + h, z) == "muro" and not self.pasa(x, y + h, z)):
                h += 1
            altos.append(h)
            for yy in range(y - 1, y + h):
                n, p = self.e(x, yy, z)
                k = categoria(n, p)
                if k in (None, "muro", "puerta") or (yy == y - 1 and k != "cofre"):
                    continue
                cosas.append({"x": x, "y": yy, "z": z, "n": n, "p": p, "cat": k, "hundido": yy < y})
        xs, zs = [c[0] for c in comp], [c[1] for c in comp]
        luces = [self.m.luz(x, y, z) for x, z in libres]
        luces = [v for v in luces if v is not None]
        aparecen = [(x, z) for x, z in libres if self.m.luz(x, y, z) == 0 and self.e(x, y, z)[0] in MA.AIRE
                    and self.e(x, y + 1, z)[0] in MA.AIRE and piso_entero(*self.e(x, y - 1, z))]
        return {"y": y, "celdas": comp, "libres": libres, "x": (min(xs), max(xs)), "z": (min(zs), max(zs)), "cosas": cosas,
                "techo": sorted(altos)[len(altos) // 2], "porche": porche,
                "luz_media": round(sum(luces) / len(luces), 1) if luces else 0, "luz_min": min(luces) if luces else 0,
                "aparecen": aparecen, "avisos": []}

    # --- entidades y contenido de cofres (29-oct, ojo-vision) ---
    def _seres(self):
        """Cuadros, marcos, soportes de armadura y vecinos de la casa, cada uno en su cuarto; y lo que hay en cada cofre
        (block_entities de los .mca: Items, o LootTable si nunca se abrió)."""
        self.be = {(b.get("x"), b.get("y"), b.get("z")): b for b in self.m.entidades(self.x0, self.z0, self.x1, self.z1)}
        self.seres = [s for s in self.m.seres(self.x0, self.z0, self.x1, self.z1) if self.y_bajo <= s["y"] <= self.y_alto + 2]
        self.seres_sueltos = []
        for s in self.seres:
            k = SER_CAT.get(s["tipo"])
            cu = self.cuarto_de(s["x"], s["y"], s["z"])
            s["cuarto"] = cu["id"] if cu else None
            if cu is None:
                self.seres_sueltos.append(s)
                continue
            cu.setdefault("seres", []).append(s)
            if k:
                cu["cosas"].append({"x": s["x"], "y": s["y"], "z": s["z"], "n": s["id"], "p": {}, "cat": k, "hundido": False,
                                    "ent": True, "detalle": s["detalle"], "nombre": s["nombre"]})
        for cu in self.cuartos:
            for o in cu["cosas"]:
                if o["cat"] == "cofre" and not o.get("ent"):
                    o["contenido"] = self.contenido(o["x"], o["y"], o["z"])

    def cuarto_de(self, x, y, z):
        """El cuarto en el que está (x, y, z): misma columna (o pegada a la pared) y entre el piso y el techo."""
        mejor = None
        for cu in self.cuartos:
            if not (cu["y"] - 1 <= y <= cu["y"] + max(2, cu["techo"])):
                continue
            cs = set(cu["celdas"])
            if (x, z) in cs:
                return cu
            if mejor is None and any((x + dx, z + dz) in cs for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                mejor = cu
        return mejor

    def contenido(self, x, y, z):
        """{'items': {id: cantidad}, 'botin': tabla o None, 'vacio': bool} de un cofre, barril o cajón."""
        b = self.be.get((x, y, z))
        if b is None:
            return None
        items = {}
        for it in b.get("Items") or []:
            iid = str(it.get("id", "?")).replace("minecraft:", "")
            items[iid] = items.get(iid, 0) + int(it.get("count", it.get("Count", 1)) or 1)
        botin = b.get("LootTable")
        return {"items": items, "botin": str(botin).replace("minecraft:", "") if botin else None, "vacio": not items and not botin}

    # --- puertas ---
    def _puertas(self):
        self.puertas = []
        for x in range(self.x0 - 1, self.x1 + 2):
            for z in range(self.z0 - 1, self.z1 + 2):
                for y in range(self.y_bajo, self.y_alto):
                    n, p = self.e(x, y, z)
                    if categoria(n, p) != "puerta" or p.get("half", "lower") != "lower" or self.cat(x, y - 1, z) == "puerta":
                        continue
                    dx, dz = DIRS_OJO.get(p.get("facing"), (0, 0))
                    mal, adorno = [], []
                    for sx, sz in ((x + dx, z + dz), (x - dx, z - dz)):
                        if (dx, dz) == (0, 0):
                            break
                        n0, n1 = self.e(sx, y, sz)[0], self.e(sx, y + 1, sz)[0]
                        k0, k1 = self.cat(sx, y, sz), self.cat(sx, y + 1, sz)
                        escalon = k0 == "muro" and self.pasa(sx, y + 1, sz) and self.pasa(sx, y + 2, sz)
                        if self.pasa(sx, y, sz) and self.pasa(sx, y + 1, sz) or escalon:
                            continue
                        quien = n0 if not self.pasa(sx, y, sz) else n1
                        tipo = k0 if not self.pasa(sx, y, sz) else k1
                        # 29-oct: puerta de adorno pegada al muro (hay muro o nada también 2 bloques más allá: no
                        # esconde ningún cuarto). Viene así en la plantilla original del castillo.
                        tx, tz = sx + (sx - x), sz + (sz - z)
                        if tipo == "muro" and (quien or "").split(":")[-1] not in NATURAL and \
                                not (self.pasa(tx, y, tz) and self.pasa(tx, y + 1, tz)):
                            adorno.append("da contra el muro en (%d, %d, %d)" % (sx, y, sz))
                            continue
                        if "trapdoor" in (quien or "") and "iron" not in quien:
                            # trampillas de madera delante de la puerta (armario o postigo): se abren con la mano
                            adorno.append("trampilla de madera delante en (%d, %d, %d): se abre con clic" % (sx, y, sz))
                            continue
                        mal.append("%s en (%d, %d, %d)" % ("pared" if tipo == "muro" else quien.split(":")[-1], sx,
                                                           y if not self.pasa(sx, y, sz) else y + 1, sz))
                    self.puertas.append({"x": x, "y": y, "z": z, "n": n, "mal": mal, "adorno": adorno})

    # --- paso: desde afuera, caminando (saltos de 1, caídas de hasta 3, escaleras de mano y trampillas) ---
    def _nodo(self, x, y, z):
        if not self.dentro(x, z, 2) or not (self.y_bajo - 1 <= y <= self.y_alto + 1):
            return False
        c = self.e(x, y, z)[0] or ""
        return self.de_pie(x, y, z) or self.cat(x, y, z) in ("puerta", "escalera") or "vine" in c or \
            ("trapdoor" in c and (self.cat(x, y - 1, z) == "escalera" or self.cat(x, y + 1, z) == "escalera"))

    def _paso(self):
        inicio = set()
        for x in range(self.x0 - 2, self.x1 + 3):
            for z in range(self.z0 - 2, self.z1 + 3):
                for y in range(self.y_bajo, self.y_alto):
                    if (not self.dentro(x, z) or not self.techado(x, y, z)) and self.de_pie(x, y, z):
                        inicio.add((x, y, z))
        visto = set(inicio)
        cola = list(inicio)
        while cola:
            x, y, z = cola.pop()
            trepa = self.cat(x, y, z) == "escalera" or "vine" in (self.e(x, y, z)[0] or "") or "trapdoor" in (self.e(x, y, z)[0] or "")
            cand = []
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                cand += [(x + dx, y, z + dz), (x + dx, y + 1, z + dz)]  # subir 1 (escalón o salto; el destino ya pide 2 de aire)
                for k in (1, 2, 3):
                    if all(self.pasa(x + dx, y - j + 1, z + dz) for j in range(1, k + 1)):
                        cand.append((x + dx, y - k, z + dz))
            if trepa:
                cand += [(x, y + 1, z), (x, y - 1, z)]
            elif self.cat(x, y - 1, z) == "escalera" or "trapdoor" in (self.e(x, y - 1, z)[0] or ""):
                cand.append((x, y - 1, z))  # bajar a una escalera de mano o abrir la trampilla del piso
            for v in cand:
                if v not in visto and self._nodo(*v):
                    visto.add(v)
                    cola.append(v)
        self.alcanzado = visto

    def _rellano(self, c):
        """Cuarto chico (12 bloques o menos) pegado a una escalera (4+ escalones alrededor) o con 2+ puertas: un paso."""
        if len(c["celdas"]) > 12:
            return False
        y, cs = c["y"], set(c["celdas"])
        esc = sum(1 for x, z in cs for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)) for yy in (y - 1, y, y + 1)
                  if (x + dx, z + dz) not in cs and "stairs" in (self.e(x + dx, yy, z + dz)[0] or ""))
        puertas = sum(1 for d in self.puertas if d["y"] == y and any(abs(q[0] - d["x"]) + abs(q[1] - d["z"]) == 1 for q in cs))
        return esc >= 4 or puertas >= 2

    # --- defectos de cada cuarto ---
    def _revisar(self):
        for c in self.cuartos:
            y = c["y"]
            av = c["avisos"]
            n_muebles = sum(1 for o in c["cosas"] if o["cat"] in MUEBLES and o["cat"] != "luz")
            area = len(c["celdas"])
            sin = [q for q in c["libres"] if (q[0], y, q[1]) not in self.alcanzado]
            hueco = sin and len(sin) == len(c["libres"]) and not n_muebles and not any(o["cat"] == "luz" for o in c["cosas"])
            c["sin_acceso"] = bool(hueco)
            if hueco:
                if not c["porche"]:  # altillo, entretecho o hueco entre vigas: no se usa, no es un defecto
                    av.append("nota: hueco sin acceso (altillo o entretecho vacío, %d bloques)" % len(sin))
            elif c["porche"]:
                if len(c["aparecen"]) >= 3:
                    av.append("afuera techado OSCURO: %d bloques con luz 0 junto a la casa (aparecen monstruos; p. ej. %s)" % (
                        len(c["aparecen"]), ", ".join("(%d, %d, %d)" % (x, y, z) for x, z in c["aparecen"][:3])))
            elif self._rellano(c):
                c["rellano"] = True  # 29-oct: descanso de escalera o paso entre puertas: no tiene por qué tener muebles
                if c["aparecen"]:
                    av.append("OSCURO: %d bloque%s de piso con luz 0 en el rellano (p. ej. %s)" % (
                        len(c["aparecen"]), "" if len(c["aparecen"]) == 1 else "s",
                        ", ".join("(%d, %d, %d)" % (x, y, z) for x, z in c["aparecen"][:3])))
            else:
                if n_muebles < 2 and area >= 6:
                    av.append("VACÍO: %d mueble%s en %d bloques de piso" % (n_muebles, "" if n_muebles == 1 else "s", area))
                elif area >= 20 and n_muebles < 4:
                    av.append("muy pobre: %d muebles en %d bloques de piso" % (n_muebles, area))
                if c["aparecen"]:
                    av.append("OSCURO: %d bloque%s de piso con luz 0 donde pueden aparecer monstruos (p. ej. %s)" % (
                        len(c["aparecen"]), "" if len(c["aparecen"]) == 1 else "s",
                        ", ".join("(%d, %d, %d)" % (x, y, z) for x, z in c["aparecen"][:3])))
                elif c["luz_media"] < 6 and area >= 4:
                    av.append("en penumbra: luz media %.1f" % c["luz_media"])
            if hueco:
                pass
            elif sin and len(sin) == len(c["libres"]) and not c["porche"]:  # macetas de balcón: no es defecto
                av.append("NO SE LLEGA caminando desde afuera (%d bloques de piso, con muebles o luces)" % len(sin))
            elif sin and not c["porche"]:
                av.append("%d bloque%s de piso no se alcanza%s (algo corta el paso; p. ej. %s)" % (
                    len(sin), "" if len(sin) == 1 else "s", "" if len(sin) == 1 else "n",
                    ", ".join("(%d, %d, %d)" % (x, y, z) for x, z in sin[:3])))
            for o in c["cosas"]:
                x, yy, z, n, p = o["x"], o["y"], o["z"], o["n"], o["p"]
                corto = n.split(":")[-1]
                if o["cat"] == "cama" and p.get("part", "head") == "head":
                    dx, dz = DIRS_OJO.get(p.get("facing"), (0, 0))
                    pie = (x - dx, z - dz)
                    if not self.pasa(x, yy + 1, z):
                        av.append("cama tapada por arriba en (%d, %d, %d): %s" % (x, yy + 1, z, self.e(x, yy + 1, z)[0]))
                    lados = [(a + ex, b + ez) for a, b in ((x, z), pie) for ex, ez in ((1, 0), (-1, 0), (0, 1), (0, -1))
                             if (a + ex, b + ez) not in ((x, z), pie)]
                    if not any(self.de_pie(a, yy, b) or self.de_pie(a, yy + 1, b) for a, b in lados):
                        av.append("cama sin espacio para acercarse en (%d, %d, %d)" % (x, yy, z))
                if yy < y or o["cat"] in ("alfombra", "escalera") or o.get("ent"):
                    continue
                if self.e(x, yy - 1, z)[0] in MA.AIRE:
                    colgante = _tiene(corto, "wall_", "hanging", "shelf", "sconce", "chandelier", "banner", "chain", "cobweb",
                                      "vine", "notice_board", "blackboard", "_sign", "lantern", "awning") or p.get("hanging") == "true" or \
                        p.get("face") in ("wall", "ceiling") or p.get("attachment") in ("ceiling", "wall")
                    vecinos = [(x + 1, yy, z), (x - 1, yy, z), (x, yy, z + 1), (x, yy, z - 1), (x, yy + 1, z)]
                    pegado = any(not self.pasa(*v) for v in vecinos)
                    # 29-oct: metido entre 2 o más bloques de su misma capa = parte de un techo o una viga (el techo de
                    # troncos del castillo alterna fogatas apagadas con losas), no un mueble flotando
                    en_techo = sum(not self.pasa(*v) for v in vecinos[:4]) >= 2
                    if not en_techo and (not colgante or not pegado):
                        av.append("flotando: %s en (%d, %d, %d)" % (corto, x, yy, z))
        for d in self.puertas:
            if d["mal"]:
                for c in self.cuartos:
                    if c["y"] == d["y"] and any(abs(q[0] - d["x"]) + abs(q[1] - d["z"]) == 1 for q in c["celdas"]):
                        c["avisos"].append("puerta tapada en (%d, %d, %d): %s" % (d["x"], d["y"], d["z"], "; ".join(d["mal"])))
                        break

    # --- resumen ---
    def tipo_cuarto(self, c):
        k = {cat: 0 for cat, *_ in CATEGORIAS}
        for o in c["cosas"]:
            k[o["cat"]] += 1 if o["cat"] != "cama" else (o["p"].get("part", "head") == "head")
        if c["porche"]:
            return "afuera techado (porche o cobertizo)"
        if c.get("rellano") and not k["cama"]:
            return "rellano o paso"
        if k["cama"]:
            return "dormitorio"
        nombres = " ".join(o["n"] for o in c["cosas"])
        if _tiene(nombres, "oven", "smoker", "sink", "kitchen"):
            return "cocina"
        if k["armadura"]:
            return "armería o cuarto de guardia"
        if k["asiento"] >= 8:
            return "nave o salón"
        if k["mesa"] >= 2 and k["asiento"] >= 2:
            return "comedor o sala"
        if k["oficio"]:
            return "taller"
        if k["estante"] >= 3:
            return "biblioteca o estudio"
        if k["mesa"] and k["asiento"]:
            return "comedor o sala"
        if k["cofre"] >= 3:
            return "despensa o almacén"
        return "sin uso claro"

    def conteo(self, cosas):
        k = {cat: 0 for cat, *_ in CATEGORIAS}
        nombres = {}
        for o in cosas:
            if o["cat"] == "cama" and o["p"].get("part", "head") != "head":
                continue
            k[o["cat"]] += 1
            nm = o["n"].split(":")[-1] + (" (hundido en el piso)" if o["hundido"] else "")
            nombres[nm] = nombres.get(nm, 0) + 1
        return k, nombres

    def texto(self, detalle=True):
        if self.vacia:
            return "## %s: sin chunks generados." % self.nombre
        dentro = [c for c in self.cuartos if not c["porche"]]
        todos = [o for c in self.cuartos for o in c["cosas"]]
        k, _ = self.conteo(todos)
        avisos = sum(len(c["avisos"]) for c in self.cuartos) + sum(1 for d in self.puertas if d["mal"])
        lin = ["## %s (x %d..%d, z %d..%d; terreno y %d) — %d piso%s, %d cuarto%s%s" % (
            self.nombre, self.x0, self.x1, self.z0, self.z1, self.y_suelo, len(self.alturas), "" if len(self.alturas) == 1 else "s",
            len(dentro), "" if len(dentro) == 1 else "s",
            ", %d afuera techado" % (len(self.cuartos) - len(dentro)) if len(self.cuartos) > len(dentro) else ""),
            "Total: " + " · ".join("%s %d" % (CAT[cat][1], k[cat]) for cat in ("cama", "cofre", "mesa", "asiento", "oficio",
                                                                               "estante", "planta", "luz", "adorno", "otro")) +
            " · cuadros %d · armaduras %d · vecinos %d" % (k["cuadro"], k["armadura"], sum(1 for s in self.seres if s["tipo"] == "aldeano")) +
            " · puertas %d%s · avisos %d" % (len(self.puertas), " (%d de adorno o tras trampilla de madera)" % sum(1 for d in self.puertas if d.get("adorno") and not d["mal"])
                                            if any(d.get("adorno") and not d["mal"] for d in self.puertas) else "", avisos)]
        if not detalle:
            return "\n".join(lin)
        for c in sorted(self.cuartos, key=lambda c: (c["y"], c["id"])):
            kc, nombres = self.conteo(c["cosas"])
            nom = ", ".join("%s%s" % (n, "×%d" % v if v > 1 else "") for n, v in sorted(nombres.items(), key=lambda t: -t[1]))
            piso = "piso %d" % c["piso"] if c["piso"] else "afuera"
            if c["piso"] == 1 and c["y"] < self.y_suelo - 2:
                piso = "sótano"
            lin.append("\n### Cuarto %s · %s · %s (x %d..%d, z %d..%d; %d de piso, %d libres; techo a %d)" % (
                c["id"], piso, self.tipo_cuarto(c), c["x"][0], c["x"][1], c["z"][0], c["z"][1], len(c["celdas"]), len(c["libres"]),
                c["techo"]))
            if any(kc.values()):
                lin.append("  " + " · ".join("%s %d" % (CAT[cat][1], kc[cat]) for cat, *_ in CATEGORIAS if kc[cat] and cat != "puerta"))
            if nom:
                lin.append("  " + (nom if len(nom) < 600 else nom[:600] + "…"))
            # 29-oct (ojo-vision): lo que hay en los cofres y las entidades del cuarto
            cts = [o["contenido"] for o in c["cosas"] if o.get("contenido")]
            if cts:
                items = {}
                for ct in cts:
                    for i, v in ct["items"].items():
                        items[i] = items.get(i, 0) + v
                lin.append("  cofres: %d con cosas, %d con botín sin abrir, %d vacíos%s" % (
                    sum(1 for ct in cts if ct["items"]), sum(1 for ct in cts if ct["botin"] and not ct["items"]),
                    sum(1 for ct in cts if ct["vacio"]),
                    ("; adentro: " + ", ".join("%s×%d" % t for t in sorted(items.items(), key=lambda t: -t[1])[:10])) if items else ""))
            ss = c.get("seres") or []
            if ss:
                lin.append("  entidades: " + "; ".join("%s%s%s" % (s["tipo"], " «%s»" % s["nombre"] if s["nombre"] else "",
                                                                   " (%s)" % s["detalle"] if s["detalle"] else "") for s in ss[:12]))
            if not hasattr(self, "_cont"):
                self._cont = contenidos(self.m, self.x0, self.z0, self.x1, self.z1)
            conts = ["%s (%d, %d, %d): %s" % (o["n"].split(":")[-1], o["x"], o["y"], o["z"], self._cont[(o["x"], o["y"], o["z"])])
                     for o in c["cosas"] if o["cat"] == "cofre" and (o["x"], o["y"], o["z"]) in self._cont]
            if conts:
                lin.append("  adentro: " + "; ".join(conts[:8]) + (" …" if len(conts) > 8 else ""))
            lin.append("  luz media %.1f, mínima %d; %d bloque%s donde pueden aparecer monstruos" % (
                c["luz_media"], c["luz_min"], len(c["aparecen"]), "" if len(c["aparecen"]) == 1 else "s"))
            for a in c["avisos"]:
                lin.append("  ⚠ " + a)
        try:
            ts = textos(self.m, self.x0, self.z0, self.x1, self.z1)
        except Exception:
            ts = []
        if ts:
            lin.append("\n### Textos de la casa")
            for x_, y_, z_, tipo, tit, tx in ts[:20]:
                if tipo == "cartel":
                    lin.append("  cartel (%d, %d, %d): %s" % (x_, y_, z_, tx[:120] if tx else "EN BLANCO"))
                else:
                    lin.append("  %s (%d, %d, %d): «%s», %d páginas — %s" % (tipo, x_, y_, z_, tit, len(tx), (tx[0] if tx else "")[:100].replace("\n", " ")))
        sueltas = [d for d in self.puertas if d["mal"] and not any(d["x"] in range(c["x"][0] - 1, c["x"][1] + 2) and d["y"] == c["y"]
                                                                   and d["z"] in range(c["z"][0] - 1, c["z"][1] + 2) for c in self.cuartos)]
        for d in sueltas:
            lin.append("  ⚠ puerta tapada en (%d, %d, %d): %s" % (d["x"], d["y"], d["z"], "; ".join(d["mal"])))
        return "\n".join(lin)

    def datos(self):
        """Resumen en dict (para el JSON de auditar_interiores)."""
        if self.vacia:
            return {"casa": self.nombre, "vacia": True}
        out = {"casa": self.nombre, "caja": [self.x0, self.z0, self.x1, self.z1], "alturas": self.alturas, "cuartos": []}
        for c in self.cuartos:
            kc, nombres = self.conteo(c["cosas"])
            out["cuartos"].append({"id": c["id"], "piso": c["piso"], "tipo": self.tipo_cuarto(c), "x": c["x"], "z": c["z"],
                                   "area": len(c["celdas"]), "libres": len(c["libres"]), "conteo": {a: b for a, b in kc.items() if b},
                                   "muebles": nombres, "luz_media": c["luz_media"], "luz_min": c["luz_min"],
                                   "aparecen": len(c["aparecen"]), "avisos": c["avisos"],
                                   "seres": [{k: s[k] for k in ("tipo", "id", "nombre", "detalle", "x", "y", "z")} for s in c.get("seres") or []],
                                   "cofres": [dict(o["contenido"], pos=(o["x"], o["y"], o["z"])) for o in c["cosas"] if o.get("contenido")]})
        out["puertas_tapadas"] = [(d["x"], d["y"], d["z"], d["mal"]) for d in self.puertas if d["mal"]]
        out["puertas_adorno"] = [(d["x"], d["y"], d["z"]) for d in self.puertas if d.get("adorno") and not d["mal"]]
        return out


DIRS_OJO = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}


def piso_entero(nombre, props):
    """El bloque de abajo deja aparecer monstruos encima (bloque entero; losa de arriba o doble)."""
    if nombre is None or se_pasa(nombre, props) or categoria(nombre, props) != "muro":
        return False
    c = nombre.split(":")[-1]
    if "slab" in c:
        return props.get("type") in ("top", "double")
    return not _tiene(c, "stairs", "fence", "_wall", "pane", "glass", "leaves", "trapdoor", "iron_bars", "chain", "shutter",
                      "awning", "path", "flagstone", "rope", "lightning_rod", "end_rod", "daylight")


CONTENEDOR = ("chest", "barrel", "container", "storage", "drawer", "sack", "shulker", "cupboard", "cabinet", "crate", "basket")


def contenido(be):
    """Qué tiene un cofre, barril o cajón (entidad de bloque): sus objetos, su tabla de botín sin abrir, o «vacío»."""
    items = be.get("Items") or []
    tabla = be.get("LootTable") or (be.get("lootr") or {}).get("LootTable") if isinstance(be.get("lootr"), dict) else be.get("LootTable")
    if not items and not tabla:
        for k, v in be.items():  # Lootr y otros mods guardan la tabla con otro nombre
            if "loot" in k.lower() and isinstance(v, str):
                tabla = v
    if items:
        cuenta = {}
        for it in items:
            n = str(it.get("id", "?")).replace("minecraft:", "")
            cuenta[n] = cuenta.get(n, 0) + int(it.get("count", it.get("Count", 1)) or 1)
        lista = sorted(cuenta.items(), key=lambda t: -t[1])
        return "%d objetos: %s%s" % (sum(cuenta.values()), ", ".join("%s×%d" % kv for kv in lista[:6]), " …" if len(lista) > 6 else "")
    if tabla:
        return "botín sin abrir (%s)" % str(tabla).replace("minecraft:", "")
    return "VACÍO"


def contenidos(m, x1, z1, x2, z2):
    """{(x, y, z): texto} de todos los contenedores de la zona."""
    return {(e.get("x"), e.get("y"), e.get("z")): contenido(e) for e in m.entidades(x1, z1, x2, z2)
            if any(k in str(e.get("id", "")) for k in CONTENEDOR)}


def _plano(t):
    """Texto JSON de Minecraft ('"hola"', '{"text": ...}') a texto plano."""
    if isinstance(t, dict):
        t = t.get("raw", t.get("text", ""))
    return _texto_nombre(t) or ""


def _libro(it):
    """(título, autor, [páginas]) de un libro escrito o de un libro y pluma; None si no es un libro."""
    i = str(it.get("id", ""))
    comp = it.get("components") or {}
    if "written_book" in i:
        c = comp.get("minecraft:written_book_content") or {}
        return _plano(c.get("title")) or "(sin título)", str(c.get("author") or "?"), [_plano(pg) for pg in c.get("pages") or []]
    if "writable_book" in i:
        c = comp.get("minecraft:writable_book_content") or {}
        return "(libro y pluma sin firmar)", "?", [_plano(pg) for pg in c.get("pages") or []]
    return None


def textos(m, x1, z1, x2, z2):
    """[(x, y, z, tipo, título, texto)] de carteles, atriles con libro y libros guardados en cofres o barriles."""
    out = []
    for e in m.entidades(x1, z1, x2, z2):
        i = str(e.get("id", ""))
        x, y, z = e.get("x"), e.get("y"), e.get("z")
        if "sign" in i:
            lados = []
            for lado in ("front_text", "back_text"):
                msj = [_plano(t).strip() for t in ((e.get(lado) or {}).get("messages") or [])]
                if any(msj):
                    lados.append(" / ".join(t for t in msj if t))
            out.append((x, y, z, "cartel", None, "  ||  ".join(lados) if lados else ""))
        elif "lectern" in i and e.get("Book"):
            lb = _libro(e["Book"])
            if lb:
                out.append((x, y, z, "libro en atril", lb[0] + " — " + lb[1], lb[2]))
        for it in e.get("Items") or []:
            lb = _libro(it)
            if lb:
                out.append((x, y, z, "libro en " + i.split(":")[-1], lb[0] + " — " + lb[1], lb[2]))
    return out


def t_leer_textos(casa=None, x=None, z=None, radio=None, x1=None, z1=None, x2=None, z2=None, completo=False):
    """Lo que dicen los carteles y los libros de una zona (o casa, o radio alrededor de un punto)."""
    guardar_mundo()
    m = Mundo()
    if casa is not None:
        nombre, c = resolver_casa(casa)
        x1, z1, x2, z2 = c["x0"] - 2, c["z0"] - 2, c["x1"] + 2, c["z1"] + 2
    elif radio is not None or None in (x1, z1, x2, z2):
        cx, cz, rr = (X0 if x is None else int(x)), (Z0 if z is None else int(z)), int(radio or 150)
        x1, z1, x2, z2 = cx - rr, cz - rr, cx + rr, cz + rr
    ts = textos(m, min(x1, x2), min(z1, z2), max(x1, x2), max(z1, z2))
    carteles = [t for t in ts if t[3] == "cartel"]
    libros = [t for t in ts if t[3] != "cartel"]
    vacios = [t for t in carteles if not t[5]]
    lin = ["Textos en x %d..%d, z %d..%d: %d carteles (%d en blanco) y %d libros." % (min(x1, x2), max(x1, x2), min(z1, z2), max(z1, z2),
                                                                                  len(carteles), len(vacios), len(libros))]
    if libros:
        lin.append("\n## Libros")
        for x_, y_, z_, tipo, tit, pags in libros:
            lin.append("- «%s» (%s) en (%d, %d, %d), %d páginas" % (tit, tipo, x_, y_, z_, len(pags)))
            for k, pg in enumerate(pags if completo else pags[:1]):
                lin.append("    p%d: %s" % (k + 1, pg.replace("\n", " ").strip()[:400 if completo else 160]))
    escritos = [t for t in carteles if t[5]]
    if escritos:
        lin.append("\n## Carteles con texto")
        for x_, y_, z_, _, _, tx in escritos[:80]:
            lin.append("- (%d, %d, %d): %s" % (x_, y_, z_, tx[:200]))
    if vacios:
        lin.append("\n## Carteles en blanco: %d (p. ej. %s)" % (len(vacios), ", ".join("(%d, %d, %d)" % t[:3] for t in vacios[:6])))
    return "\n".join(lin)


def t_inventario_interior(casa=None, x1=None, z1=None, x2=None, z2=None):
    """Cuenta por cuarto los muebles y las luces de una casa y avisa de los defectos."""
    guardar_mundo()
    nombre, caja = _caja(casa, x1, z1, x2, z2)
    return Casa(Mundo(), caja, nombre).texto()


def t_auditar_interiores(detalle=False, max_avisos=6):
    """Todas las casas del plano: una línea por casa y sus avisos. Guarda el detalle completo en un JSON."""
    guardar_mundo()
    m = Mundo()
    t0 = time.time()
    lin, datos = [], []
    for n, c in cajas_casas():
        if n == "parque" or n.endswith((":wall", ":gate")):
            continue
        casa = Casa(m, c, n)
        datos.append(casa.datos())
        if casa.vacia or not casa.cuartos:
            lin.append("## %s: %s" % (n, "sin chunks generados" if casa.vacia else "sin cuartos techados (todavía sin construir o al aire libre)"))
            continue
        if detalle:
            lin.append(casa.texto())
            continue
        lin.append(casa.texto(detalle=False))
        avs = ["%s: %s" % (cu["id"], a) for cu in casa.cuartos for a in cu["avisos"]]
        for a in avs[:max_avisos]:
            lin.append("  ⚠ " + a)
        if len(avs) > max_avisos:
            lin.append("  ... y %d avisos más" % (len(avs) - max_avisos))
    os.makedirs(SALIDA, exist_ok=True)
    ruta = os.path.join(SALIDA, "interiores_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    json.dump(datos, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return "Interiores de %d casas (%.0f s). Mundo: %s\n\n%s\n\nDetalle completo: %s" % (
        len(datos), time.time() - t0, carpeta_region(), "\n".join(lin), ruta)


# --- Decoración con sentido (29-oct-2026, ojo-vision; Sergio: «terror y aventura medieval, NADA gracioso») ---
# Por el nombre de la plantilla del plano se sabe qué es cada casa; cada tipo pide sus muebles de oficio. Además se
# buscan cosas que desentonan: tortas, sofás modernos, colores alegres, flores de fiesta, cuadros chistosos y animales
# de broma. Las claves se buscan como parte del nombre del bloque o de la entidad (vanilla y mods de muebles).

TIPO_CASA = [  # (parte del nombre de la plantilla, tipos)
    ("blacksmith", ["herreria"]), ("toolsmith_and_weaponsmith", ["herreria"]), ("butcher_and_armorer", ["carniceria", "armeria"]),
    ("armorer_and_leatherworker", ["armeria", "curtidor"]), ("butcher_and_mason", ["carniceria", "canteria"]),
    ("cartographer_and_fletcher", ["cartografo", "flechero"]), ("cartographer_and_library", ["cartografo", "biblioteca"]),
    ("church", ["iglesia"]), ("temple", ["iglesia"]), ("tavern", ["taberna"]), ("bakery", ["panaderia"]),
    ("stable", ["establo"]), ("windmill", ["molino"]), ("farm_house", ["granja"]), ("farmer", ["granja"]),
    ("lumbermill", ["aserradero"]), ("fisher", ["pescador"]), ("shepherd", ["pastor"]), ("mason", ["canteria"]),
    ("guard_tower", ["torre"]), ("castle", ["castillo"]), ("fortress", ["fortaleza"]),
    ("casa_tobias", ["ermitano"]), ("casa_ezequiel", ["clerigo"]), ("casa_mara", ["domadora"]), ("casa_kai", ["maestro"]),
    ("casa_lucia", ["cazadora"]), ("casa_elias", ["artesano"]),
    ("house", ["vivienda"]),
]
NOMBRE_TIPO = {"herreria": "herrería", "carniceria": "carnicería", "armeria": "armería", "curtidor": "curtiduría",
               "canteria": "cantería", "cartografo": "taller de cartógrafo", "flechero": "taller de flechero",
               "biblioteca": "biblioteca", "iglesia": "iglesia o templo", "taberna": "taberna", "panaderia": "panadería",
               "establo": "establo", "molino": "molino", "granja": "granja", "aserradero": "aserradero",
               "pescador": "casa de pescador", "pastor": "casa de pastor", "torre": "torre de guardia", "castillo": "castillo",
               "fortaleza": "fortaleza", "ermitano": "casa de Tobías (ermitaño)", "clerigo": "casa del Hermano Ezequiel",
               "domadora": "casa de Mara (domadora)", "maestro": "casa de Kai (maestro de armas)",
               "cazadora": "casa de Lucía (cazadora)", "artesano": "casa de Maese Elías (artesano)", "vivienda": "vivienda"}
REQUIERE = {  # tipo: [(qué falta, claves; basta una)]
    "herreria": [("fragua u horno", ("blast_furnace", "furnace", "forge", "bellows")), ("yunque", ("anvil",)),
                 ("mesa de herrería o afilador", ("smithing_table", "grindstone")), ("agua para templar (caldero o barril)", ("cauldron", "barrel"))],
    "armeria": [("armaduras o armas a la vista (soporte, estante de armas)", ("armor_stand", "weapon", "rack", "sword", "target")),
                ("yunque o mesa de herrería", ("anvil", "smithing_table", "blast_furnace"))],
    "carniceria": [("ahumador", ("smoker",)), ("mesa o tajo", ("table", "counter", "chopping"))],
    "curtidor": [("caldero", ("cauldron",))],
    "canteria": [("cortapiedras", ("stonecutter",))],
    "cartografo": [("mesa de cartógrafo", ("cartography_table",)), ("libros o mapas", ("bookshelf", "book", "lectern", "map", "globe"))],
    "flechero": [("mesa de flechero", ("fletching_table",)), ("diana o arcos", ("target", "bow", "arrow"))],
    "biblioteca": [("librerías", ("bookshelf",)), ("atril", ("lectern",))],
    "iglesia": [("altar o atril", ("lectern", "altar", "enchanting_table", "pedestal", "statue")), ("velas", ("candle",)),
                ("bancas", ("bench", "pew", "chair", "seat", "stool"))],
    "taberna": [("barriles", ("barrel",)), ("mesas", ("table", "counter")), ("asientos", ("chair", "stool", "bench")),
                ("fogón o cocina", ("smoker", "furnace", "campfire", "oven", "stove"))],
    "panaderia": [("horno", ("furnace", "smoker", "oven")), ("despensa (barril o cofre)", ("barrel", "chest")),
                  ("mesa para amasar", ("table", "crafting_table", "counter"))],
    "establo": [("heno", ("hay_block",)), ("abrevadero (caldero o agua)", ("cauldron", "water"))],
    "molino": [("heno o sacos de grano", ("hay_block", "sack", "composter")), ("almacén (barril o cofre)", ("barrel", "chest"))],
    "granja": [("compostador", ("composter",)), ("heno", ("hay_block",))],
    "aserradero": [("sierra (cortapiedras)", ("stonecutter", "saw")), ("troncos apilados", ("_log",))],
    "pescador": [("barril", ("barrel",))],
    "pastor": [("telar", ("loom",)), ("lana", ("wool",))],
    "torre": [("armas o armadura (soporte, estante, diana)", ("armor_stand", "weapon", "rack", "target")),
              ("cama de guardia", ("_bed",)), ("barril o cofre de provisiones", ("barrel", "chest"))],
    "castillo": [("trono o asiento señorial", ("throne", "chair")), ("estandartes", ("banner",)), ("armaduras", ("armor_stand",))],
    "fortaleza": [("armas o armaduras", ("armor_stand", "weapon", "rack", "target")), ("estandartes", ("banner",))],
    "ermitano": [("fogata o chimenea", ("campfire", "furnace")), ("libros", ("book",))],
    "clerigo": [("velas", ("candle",)), ("libros o atril", ("book", "lectern")), ("soporte de pociones", ("brewing_stand",))],
    "domadora": [("heno", ("hay_block",)), ("mesa de cartógrafo", ("cartography_table",))],
    "maestro": [("libros", ("bookshelf", "book")), ("diana o armadura de práctica", ("target", "armor_stand"))],
    "cazadora": [("mesa de flechero", ("fletching_table",)), ("trofeos de caza (cráneos, cabezas)", ("skull", "_head", "trophy"))],
    "artesano": [("mesa de trabajo", ("crafting_table",)), ("taller (mesa de herrería, telar o cortapiedras)", ("smithing_table", "loom", "stonecutter"))],
    "vivienda": [],
}
GUARDAR = ("chest", "barrel", "trunk", "drawer", "wardrobe", "cupboard", "cabinet", "crate", "sack", "nightstand")
# lo que desentona con el terror medieval: (claves, por qué, gravedad «gracioso» o «alegre»)
DESENTONA = [
    (("candle_cake", "cake"), "torta (de fiesta)", "gracioso"),
    (("pancake", "cookie", "donut", "cupcake"), "dulces de desayuno o fiesta", "alegre"),
    (("couch", "sofa", "chaise", "coffee_table"), "sofá o mesa ratona (moderno)", "gracioso"),
    (("modern_", "striped_chair", "glass_table", "glass_kitchen_cabinet", "kitchen_hood", "service_bell"), "mueble moderno", "gracioso"),
    (("balloon", "gift", "present", "party", "plush", "teddy", "gnome", "flamingo", "rubber_duck", "toy"), "juguete o adorno de fiesta", "gracioso"),
    (("pufferfish_trophy", "tropical_fish_trophy"), "trofeo de pez de colores", "gracioso"),
    (("cherry_",), "madera de cerezo (rosada)", "alegre"),
    (("pink_", "magenta_", "lime_", "light_blue_", "yellow_", "orange_"), "color alegre", "alegre"),
    (("tulip", "allium", "azure_bluet", "oxeye_daisy", "cornflower", "dandelion", "sunflower", "lilac", "peony", "pink_petals",
      "flowering_azalea", "blue_orchid", "lily_of_the_valley", "torchflower", "pitcher_plant"), "flor alegre", "alegre"),
]
NO_DESENTONA = ("glazed_terracotta",)  # una vela de color o una baldosa vidriada se acepta: no se marcan
CUADROS_ALEGRES = {"kebab", "pool", "pigscene", "donkey_kong", "creebet", "sunflowers", "bouquet", "owlemons", "backyard",
                   "unpacked", "prairie_ride", "aztec", "aztec2", "plant", "meditative", "humble", "pond", "fern", "tides"}
ITEMS_GRACIOSOS = ("cake", "cookie", "firework", "candy", "balloon", "party", "pink_dye", "music_disc_cat", "music_disc_chirp")


def animales_de_broma():
    """Los animales de Alex's Mobs con aparición 0 en config/amc.json (los «de broma» que el principal apagó el 29-oct)."""
    try:
        d = json.load(open(os.path.join(RAIZ, os.environ.get("NIEBLA_SRV", "servidor"), "config", "amc.json"), encoding="utf-8"))
    except Exception:
        return set()
    out = set()

    def mirar(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k.endswith("SpawnWeight") and v == 0:
                    base = k[:-len("SpawnWeight")]
                    out.add("alexsmobs:" + "".join("_" + ch.lower() if ch.isupper() else ch for ch in base))
                mirar(v)
    mirar(d)
    return out


def tipos_de_casa(nombre):
    n = nombre.split(":", 1)[-1].lower()
    for k, t in TIPO_CASA:
        if k in n:
            return t
    return ["vivienda"]


def revisar_decoracion(casa, broma=None):
    """Revisa si la decoración de una Casa tiene sentido para lo que es y si algo desentona. Devuelve un dict."""
    broma = animales_de_broma() if broma is None else broma
    tipos = tipos_de_casa(casa.nombre)
    res = {"casa": casa.nombre, "tipos": tipos, "faltan": [], "desentona": [], "dormitorios_sin_baul": [], "cofres": {},
           "seres": {}, "avisos": []}
    if casa.vacia or not casa.cuartos:
        res["avisos"].append("sin cuartos para revisar")
        return res
    # todos los bloques de la casa (no solo los de los cuartos: el heno del establo o los troncos del aserradero cuentan)
    todos, donde = {}, {}
    for x in range(casa.x0, casa.x1 + 1):
        for z in range(casa.z0, casa.z1 + 1):
            for y in range(casa.y_bajo, casa.y_alto + 1):
                b = casa.m.bloque(x, y, z)
                if not b or b in MA.AIRE:
                    continue
                b = b.split("[")[0]
                todos[b] = todos.get(b, 0) + 1
                donde.setdefault(b, (x, y, z))
    seres = casa.seres
    textos = list(todos) + [s["id"] for s in seres] + [s["detalle"] for s in seres if s["detalle"]]
    # bancas hechas con escaleras (muy común en iglesias): escalón de abajo a la altura del piso de un cuarto, con aire
    # encima y pegado al cuarto
    bancas = set()
    for cu in casa.cuartos:
        if cu["porche"]:
            continue
        cs = set(cu["celdas"])
        for x, z in cs:
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (0, 0)):
                n, p = casa.e(x + dx, cu["y"], z + dz)
                if n and n.endswith("_stairs") and p.get("half", "bottom") == "bottom" and casa.pasa(x + dx, cu["y"] + 1, z + dz):
                    bancas.add((x + dx, z + dz))
    if len(bancas) >= 4:
        textos.append("bench_de_escaleras")
    for t in tipos:
        for que, claves in REQUIERE.get(t, []):
            if not any(k in tx for tx in textos for k in claves):
                res["faltan"].append("%s: falta %s" % (NOMBRE_TIPO.get(t, t), que))
    # dormitorio con cama y baúl
    for cu in casa.cuartos:
        if cu["porche"] or not any(o["cat"] == "cama" for o in cu["cosas"]):
            continue
        if not any(o["cat"] == "cofre" or _tiene(o["n"], *GUARDAR) for o in cu["cosas"]):
            res["dormitorios_sin_baul"].append("cuarto %s (piso y %d): cama sin baúl, cofre ni cajonera" % (cu["id"], cu["y"]))
    # lo que desentona
    for b, n in sorted(todos.items(), key=lambda t: -t[1]):
        corto = b.split(":")[-1]
        if any(k in corto for k in NO_DESENTONA) or corto.endswith("_candle"):
            continue  # una vela de color o una baldosa vidriada se aceptan
        for claves, por, grav in DESENTONA:
            if por.startswith("torta") and "pancake" in corto:
                continue
            if any((corto.startswith(k) or "_" + k in corto) if k.endswith("_") else k in corto for k in claves):
                if por == "color alegre" and categoria(b) == "muro" and not _tiene(corto, "wool", "bed", "banner", "carpet", "concrete"):
                    break  # un ladrillo o madera amarilla del muro no se marca; una alfombra o cama sí
                res["desentona"].append({"que": corto, "por": por, "gravedad": grav, "cuantos": n, "ejemplo": donde[b]})
                break
    for s in seres:
        if s["tipo"] == "cuadro" and s["detalle"] in CUADROS_ALEGRES:
            res["desentona"].append({"que": "cuadro «%s»" % s["detalle"], "por": "cuadro alegre o chistoso", "gravedad": "gracioso",
                                     "cuantos": 1, "ejemplo": (s["x"], s["y"], s["z"])})
        if s["id"] in broma:
            res["desentona"].append({"que": s["id"].split(":")[-1], "por": "animal de broma (apagado en amc.json, pero sigue vivo)",
                                     "gravedad": "gracioso", "cuantos": 1, "ejemplo": (s["x"], s["y"], s["z"])})
    # cofres
    con, vac, bot, raros, items = 0, 0, 0, [], {}
    for cu in casa.cuartos:
        for o in cu["cosas"]:
            ct = o.get("contenido")
            if o["cat"] != "cofre" or ct is None:
                continue
            if ct["vacio"]:
                vac += 1
            elif ct["botin"] and not ct["items"]:
                bot += 1
            else:
                con += 1
            for i, k in ct["items"].items():
                items[i] = items.get(i, 0) + k
                if any(g in i for g in ITEMS_GRACIOSOS):
                    raros.append("%s×%d en (%d, %d, %d)" % (i, k, o["x"], o["y"], o["z"]))
    res["cofres"] = {"con_cosas": con, "vacios": vac, "botin_sin_abrir": bot,
                     "lo_mas_comun": sorted(items.items(), key=lambda t: -t[1])[:8], "raros": raros}
    for r in raros:
        res["desentona"].append({"que": r, "por": "objeto de fiesta dentro de un cofre", "gravedad": "gracioso", "cuantos": 1,
                                 "ejemplo": None})
    # entidades
    por_tipo = {}
    for s in seres:
        por_tipo.setdefault(s["tipo"], []).append(s)
    res["seres"] = {t: ["%s%s en (%d, %d, %d)%s" % (s["nombre"] + " · " if s["nombre"] else "", (s["detalle"] or s["id"].split(":")[-1]),
                                                   s["x"], s["y"], s["z"], "" if s.get("cuarto") else " (fuera de los cuartos)")
                        for s in ls] for t, ls in por_tipo.items()}
    for s in por_tipo.get("monstruo", []):
        if s.get("cuarto"):
            res["avisos"].append("monstruo dentro de la casa: %s en (%d, %d, %d)" % (s["id"], s["x"], s["y"], s["z"]))
    if por_tipo.get("objeto"):
        res["avisos"].append("%d objeto(s) tirado(s) en el piso: %s" % (len(por_tipo["objeto"]), ", ".join(s["detalle"] for s in por_tipo["objeto"][:5])))
    return res


def texto_decoracion(r):
    lin = ["## %s — %s" % (r["casa"], " + ".join(NOMBRE_TIPO.get(t, t) for t in r["tipos"]))]
    for f in r["faltan"]:
        lin.append("  ✗ " + f)
    for d in r["dormitorios_sin_baul"]:
        lin.append("  ✗ " + d)
    for d in r["desentona"]:
        if d["gravedad"] == "gracioso":
            ej = " (p. ej. %s)" % (tuple(d["ejemplo"]),) if d.get("ejemplo") else ""
            lin.append("  ☹ GRACIOSO %s: %s ×%d%s" % (d["por"], d["que"], d["cuantos"], ej))
    alegres = [d for d in r["desentona"] if d["gravedad"] != "gracioso"]
    if alegres:  # en una sola línea: son muchos y pesan menos que lo gracioso
        lin.append("  ☀ alegre (%d): %s" % (sum(d["cuantos"] for d in alegres), ", ".join(
            "%s×%d" % (d["que"].replace("potted_", "maceta "), d["cuantos"]) for d in alegres[:10]) + (" …" if len(alegres) > 10 else "")))
    c = r.get("cofres") or {}
    if c:
        lin.append("  cofres: %d con cosas, %d con botín sin abrir, %d vacíos%s" % (
            c["con_cosas"], c["botin_sin_abrir"], c["vacios"],
            "; lo más común: " + ", ".join("%s×%d" % t for t in c["lo_mas_comun"][:6]) if c["lo_mas_comun"] else ""))
    nombres = {"cuadro": "cuadros", "marco": "marcos", "armadura": "soportes de armadura", "aldeano": "vecinos",
               "animal": "animales", "monstruo": "monstruos", "objeto": "objetos tirados", "vehiculo": "vehículos", "display": "piezas animadas"}
    for t, ls in (r.get("seres") or {}).items():
        if t in nombres:
            lin.append("  %s %d: %s" % (nombres[t], len(ls), "; ".join(ls[:6]) + (" …" if len(ls) > 6 else "")))
    for a in r["avisos"]:
        lin.append("  ⚠ " + a)
    if not (r["faltan"] or r["dormitorios_sin_baul"] or r["desentona"]):
        lin.append("  ✓ la decoración tiene sentido y no hay nada que desentone")
    return "\n".join(lin)


def t_revisar_decoracion(casa=None, solo_problemas=False):
    """Decoración con sentido por tipo de casa (todas o una) y lo que desentona con el terror medieval."""
    guardar_mundo()
    m = Mundo()
    broma = animales_de_broma()
    lista = [resolver_casa(casa)] if casa is not None else [(n, c) for n, c in cajas_casas()
                                                             if n != "parque" and not n.endswith((":wall", ":gate"))]
    t0 = time.time()
    res, lin = [], []
    tot = {"faltan": 0, "dormitorios_sin_baul": 0, "gracioso": 0, "alegre": 0}
    for n, c in lista:
        r = revisar_decoracion(Casa(m, c, n), broma)
        res.append(r)
        tot["faltan"] += len(r["faltan"])
        tot["dormitorios_sin_baul"] += len(r["dormitorios_sin_baul"])
        for d in r["desentona"]:
            tot[d["gravedad"]] += 1
        if solo_problemas and not (r["faltan"] or r["dormitorios_sin_baul"] or r["desentona"] or r["avisos"]):
            continue
        lin.append(texto_decoracion(r))
    os.makedirs(SALIDA, exist_ok=True)
    ruta = os.path.join(SALIDA, "decoracion_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    json.dump(res, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=list)
    return ("Decoración de %d casas (%.0f s). Faltan %d muebles de oficio, %d dormitorios sin baúl, %d cosas graciosas y %d "
            "alegres.\n✗ = falta algo que esa casa debería tener · ☹ = gracioso o moderno (romper el tono) · ☀ = alegre "
            "(suaviza el terror)\n\n%s\n\nDetalle: %s") % (len(res), time.time() - t0, tot["faltan"], tot["dormitorios_sin_baul"],
                                                        tot["gracioso"], tot["alegre"], "\n".join(lin), ruta)


RELLENAS = os.path.join(RAIZ, "planos", "convertidos", "rellenas")


class MundoPlantilla:
    """Una plantilla .nbt (planos/convertidos/rellenas) vista como si fuera el mundo: la casa en x 0..W-1, z 0..D-1,
    y 0..H-1, sobre un llano de pasto en y -1. Sirve para revisar lo que se va a construir ANTES de construirlo
    (29-oct, ojo-vision). La luz no se sabe (sin servidor): se toma 15 para no inventar cuartos oscuros."""

    def __init__(self, ruta):
        d = MA.leer_nbt(gzip.decompress(open(ruta, "rb").read()))
        pal = [(p.get("Name", "air").replace("minecraft:", ""), dict(p.get("Properties") or {})) for p in d["palette"]]
        self.W, self.H, self.D = d["size"]
        self.b, self.be = {}, []
        for bl in d["blocks"]:
            x, y, z = bl["pos"]
            n, p = pal[bl["state"]]
            if n == "structure_void":
                continue
            self.b[(x, y, z)] = (n, p)
            if categoria(n, p) == "cofre":  # sin nbt = cofre vacío
                self.be.append(dict(bl.get("nbt") or {}, x=x, y=y, z=z))
        self.ents = []
        for e in d.get("entities") or []:
            nbt = dict(e.get("nbt") or {})
            nbt["Pos"] = e.get("pos") or [0, 0, 0]
            nbt.pop("TileX", None)
            self.ents.append(nbt)

    def adentro(self, x, z):
        return 0 <= x < self.W and 0 <= z < self.D

    def estado(self, x, y, z):
        if (x, y, z) in self.b:
            return self.b[(x, y, z)]
        if y == -1:
            return ("grass_block", {}) if not self.adentro(x, z) else ("dirt", {})
        return ("dirt", {}) if y < -1 else ("air", {})

    def bloque(self, x, y, z):
        n, p = self.estado(x, y, z)
        return n + "[" + ",".join("%s=%s" % kv for kv in sorted(p.items())) + "]" if p and n.endswith("_door") else n

    def luz(self, x, y, z):
        return 15

    def tope(self, x, z):
        for y in range(self.H, -2, -1):
            if self.estado(x, y, z)[0] not in MA.AIRE:
                return self.estado(x, y, z)[0], y
        return "grass_block", -1

    def suelo(self, x, z):
        nom, y = self.tope(x, z)
        while (nom in MA.AIRE or es_planta(nom)) and y > -1:
            y -= 1
            nom = self.estado(x, y, z)[0]
        return nom, y

    def entidades(self, x1, z1, x2, z2):
        return list(self.be)

    def seres(self, x1, z1, x2, z2):
        return [v for v in (ver_entidad(e) for e in self.ents) if v]


def t_revisar_plantillas(plantilla=None, solo_problemas=True):
    """Revisa la decoración de las plantillas que se van a construir (planos/convertidos/rellenas), no del mundo."""
    nombres = sorted(f[:-4] for f in os.listdir(RELLENAS) if f.endswith(".nbt"))
    if plantilla:
        nombres = [n for n in nombres if plantilla.lower() in n.lower()] or nombres[:0]
        if not nombres:
            return "No hay ninguna plantilla que se llame «%s» en %s." % (plantilla, RELLENAS)
    broma = animales_de_broma()
    t0 = time.time()
    res, lin = [], []
    tot = {"faltan": 0, "dormitorios_sin_baul": 0, "gracioso": 0, "alegre": 0}
    for n in nombres:
        mp = MundoPlantilla(os.path.join(RELLENAS, n + ".nbt"))
        r = revisar_decoracion(Casa(mp, {"x0": 0, "x1": mp.W - 1, "z0": 0, "z1": mp.D - 1}, "plantilla:" + n), broma)
        res.append(r)
        tot["faltan"] += len(r["faltan"])
        tot["dormitorios_sin_baul"] += len(r["dormitorios_sin_baul"])
        for d in r["desentona"]:
            tot[d["gravedad"]] += 1
        if solo_problemas and not (r["faltan"] or r["dormitorios_sin_baul"] or r["desentona"]):
            continue
        lin.append(texto_decoracion(r))
    os.makedirs(SALIDA, exist_ok=True)
    ruta = os.path.join(SALIDA, "decoracion_plantillas_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    json.dump(res, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=list)
    return ("Plantillas revisadas: %d (%.0f s), coordenadas DE LA PLANTILLA (0,0,0 = esquina). Faltan %d muebles de oficio, "
            "%d dormitorios sin baúl, %d cosas graciosas y %d alegres.\n\n%s\n\nDetalle: %s") % (
        len(res), time.time() - t0, tot["faltan"], tot["dormitorios_sin_baul"], tot["gracioso"], tot["alegre"], "\n".join(lin), ruta)


VUELAN = ("bat", "phantom", "ghast", "vex", "allay", "bee", "parrot", "blaze", "dragon", "wither", "bird", "crow", "moth",
          "butterfly", "fly", "wraith", "spirit", "ghost", "squid", "fish", "dolphin", "turtle", "axolotl", "frog", "hydra")


def revisar_seres(m, x1, z1, x2, z2, cajas=None):
    """Problemas de las entidades guardadas en la zona (4-nov, ronda 3). Devuelve [(gravedad, texto, (x, y, z))]:
    monstruos dentro de una casa, aldeanos o animales metidos en bloques o flotando, aldeanos con el mismo nombre,
    objetos tirados, marcos vacíos, armaduras sin nada puesto y animales dentro de las casas."""
    cajas = cajas_casas() if cajas is None else cajas
    out = []

    def casa_de(x, z):
        return next((n for n, c in cajas if n != "parque" and c["x0"] <= x <= c["x1"] and c["z0"] <= z <= c["z1"]), None)

    def techado(x, y, z):
        return any(categoria(*m.estado(x, y + k, z)) == "muro" and not se_pasa(*m.estado(x, y + k, z)) for k in range(2, 9))

    def macizo(x, y, z):  # bloque entero en el que nadie puede estar parado adentro
        n, pr = m.estado(x, y, z)
        return n is not None and n not in ("water", "lava") and not se_pasa(n, pr) and piso_entero(n, pr) and "slab" not in n

    nombres = {}
    for e in m.seres(x1, z1, x2, z2):
        x, y, z, t = e["x"], e["y"], e["z"], e["tipo"]
        corto = e["id"].split(":")[-1]
        quien = "%s%s" % (corto, (" «%s»" % e["nombre"]) if e.get("nombre") else "")
        casa = casa_de(x, z)
        if y < -64 or y > 320:  # se cayó del mundo y sigue cayendo (gasta servidor y nunca se borra sola)
            out.append((2, "entidad CAYENDO EN EL VACÍO: %s" % corto, (x, max(-64, min(320, y)), z)))
            continue
        if casa and y < (m.suelo(x, z)[1] or y) - 8:
            casa = None  # muy por debajo de la casa: es una cueva, no la casa
        if t == "monstruo" and any(k in corto for k in ("magpie", "crow", "bird")):
            t = "animal"
        if t in ("aldeano", "animal", "monstruo", "armadura"):
            if macizo(x, y, z) and macizo(x, y + 1, z):
                out.append((3, "%s METIDO en un bloque (%s)%s" % (quien, m.estado(x, y, z)[0], " en " + casa if casa else ""), (x, y, z)))
            elif not any(v in corto for v in VUELAN) and all(m.estado(x, y - k, z)[0] in MA.AIRE for k in (1, 2, 3)):
                out.append((2, "%s FLOTANDO (3 bloques de aire debajo)%s" % (quien, " en " + casa if casa else ""), (x, y, z)))
        if t == "monstruo" and casa and techado(x, y, z):
            out.append((3, "MONSTRUO DENTRO de %s: %s" % (casa, quien), (x, y, z)))
        if t == "animal" and casa and techado(x, y, z) and casa and not any(k in casa for k in ("stable", "farm", "shepherd")):
            out.append((1, "animal dentro de %s: %s" % (casa, quien), (x, y, z)))
        if t == "aldeano" and e.get("nombre"):
            nombres.setdefault(e["nombre"], []).append((x, y, z))
        if t == "objeto":
            out.append((1, "objeto tirado en el suelo: %s%s" % (e["detalle"], " en " + casa if casa else ""), (x, y, z)))
        if t == "marco" and e["detalle"] == "vacío":
            out.append((1, "marco vacío%s" % (" en " + casa if casa else ""), (x, y, z)))
        if t == "armadura" and e["detalle"] == "sin nada puesto":
            out.append((1, "soporte de armadura sin nada puesto%s" % (" en " + casa if casa else ""), (x, y, z)))
    for n, pos in nombres.items():
        if len(pos) > 1:
            out.append((2, "aldeano REPETIDO: %d con el nombre «%s» en %s" % (len(pos), n, ", ".join("(%d, %d, %d)" % p_ for p_ in pos)), pos[0]))
    return sorted(out, key=lambda t: (-t[0], t[2]))


def t_revisar_seres(x=None, z=None, radio=120, casa=None, imagen=False):
    """Revisión de las entidades de una zona o casa (lo último que guardó el servidor), con imagen 3D opcional."""
    guardar_mundo()
    m = Mundo()
    if casa is not None:
        nombre, c = resolver_casa(casa)
        x1, z1, x2, z2 = c["x0"] - 2, c["z0"] - 2, c["x1"] + 2, c["z1"] + 2
    else:
        x, z, radio = (X0 if x is None else int(x)), (Z0 if z is None else int(z)), max(8, min(int(radio), 300))
        x1, z1, x2, z2 = x - radio, z - radio, x + radio, z + radio
    prob = revisar_seres(m, x1, z1, x2, z2)
    nivel = {3: "GRAVE", 2: "revisar", 1: "detalle"}
    lin = ["Revisión de seres en x %d..%d, z %d..%d: %d avisos (%d graves). Lo último que guardó el servidor." % (
        x1, x2, z1, z2, len(prob), sum(1 for p_ in prob if p_[0] == 3))]
    por = {}
    for g, txt, pos in prob:
        clave = txt.rsplit(": ", 1)[0] if g == 1 and ": " in txt else txt
        por.setdefault((g, clave), []).append(pos)
    for (g, clave), poss in sorted(por.items(), key=lambda t: (-t[0][0], t[0][1])):
        lin.append("  [%s] %s%s" % (nivel[g], clave, (" ×%d: " % len(poss) + ", ".join("(%d, %d, %d)" % p_ for p_ in poss[:6]) +
                                                      (" …" if len(poss) > 6 else "")) if len(poss) > 1 else " en (%d, %d, %d)" % poss[0]))
    ruta = None
    if imagen and prob:
        import ojo_vista3d as V3
        graves = [p_ for p_ in prob if p_[0] >= 2] or prob
        xs, zs = [p_[2][0] for p_ in graves], [p_[2][2] for p_ in graves]
        cx, cz = (min(xs) + max(xs)) // 2, (min(zs) + max(zs)) // 2
        r = max(12, min(64, max(max(xs) - min(xs), max(zs) - min(zs)) // 2 + 6))
        img, txt3 = V3.vista(m, decodificar_seccion, cx, cz, radio=r, desde="sureste", inclinacion=45, seres=True,
                             marcas=[p_[2] for p_ in graves[:40]])
        if img is not None:
            ruta = nombre_salida("seres3d")
            V3.escribir_png(ruta, img)
            lin.append("\nImagen 3D: los avisos van con anillo rojo; palitos de color = seres (azul aldeano, rojo monstruo).\n" +
                       txt3.split("\n")[0] + "\nArchivo: " + ruta)
    return "\n".join(lin), ruta


def t_seres(x1=None, z1=None, x2=None, z2=None, casa=None, tipos=None):
    """Entidades guardadas en una zona o casa: cuadros, marcos, soportes de armadura, vecinos, animales, monstruos,
    objetos tirados y vehículos."""
    guardar_mundo()
    if casa is not None:
        nombre, c = resolver_casa(casa)
        x1, z1, x2, z2 = c["x0"], c["z0"], c["x1"], c["z1"]
    elif None in (x1, z1, x2, z2):
        x1, z1, x2, z2 = X0 - 150, Z0 - 150, X0 + 150, Z0 + 150
    if abs(x2 - x1) > 640 or abs(z2 - z1) > 640:
        return "Zona demasiado grande (máximo 640 x 640)."
    m = Mundo()
    broma = animales_de_broma()
    cajas = cajas_casas()
    ss = [s for s in m.seres(x1, z1, x2, z2) if not tipos or s["tipo"] in tipos]
    por = {}
    for s in ss:
        por.setdefault(s["tipo"], []).append(s)
    lin = ["Entidades guardadas en x %d..%d, z %d..%d: %d (lo último que guardó el servidor)." % (min(x1, x2), max(x1, x2), min(z1, z2), max(z1, z2), len(ss))]
    for t in ("aldeano", "cuadro", "marco", "armadura", "monstruo", "animal", "objeto", "vehiculo", "display", "otro"):
        if t not in por:
            continue
        lin.append("\n## %s: %d" % (t, len(por[t])))
        cuenta = {}
        for s in por[t]:
            cuenta[s["id"]] = cuenta.get(s["id"], 0) + 1
        if t in ("animal", "monstruo", "objeto", "vehiculo", "display", "otro"):
            lin.append("  " + ", ".join("%s×%d" % (k.split(":")[-1] if k.startswith("minecraft:") else k, v)
                                        for k, v in sorted(cuenta.items(), key=lambda t: -t[1])))
        for s in por[t][:40]:
            casa_ = next((n for n, c in cajas if c["x0"] <= s["x"] <= c["x1"] and c["z0"] <= s["z"] <= c["z1"]), "afuera")
            marca = " ☹ DE BROMA" if s["id"] in broma else ""
            if t in ("aldeano", "cuadro", "marco", "armadura") or marca or s["y"] < Y0 - 20 and t == "monstruo":
                lin.append("  - (%d, %d, %d) %s%s%s · %s%s" % (s["x"], s["y"], s["z"], s["nombre"] + " · " if s["nombre"] else "",
                                                           s["id"].split(":")[-1], " · " + s["detalle"] if s["detalle"] else "", casa_, marca))
    return "\n".join(lin)


# --- Dibujos de interiores ---
FUENTE = {  # letras de 3x5 para las etiquetas de las imágenes
    "0": "####.##.##.####", "1": ".#.##..#..#.###", "2": "###..#####..###", "3": "###..####..####", "4": "#.##.####..#..#",
    "5": "####..###..####", "6": "####..####.####", "7": "###..#..#..#..#", "8": "####.#####.####", "9": "####.####..####",
    "-": "......###......", "y": "#.##.#.#..#..#.", "N": "#.#####.####.#.", " ": "." * 15, "^": ".#.#.#.........",
}


def _texto_png(img, x, y, texto, color=(20, 20, 20), esc=2):
    for i, ch in enumerate(texto):
        f = FUENTE.get(ch, FUENTE[" "])
        for k, v in enumerate(f):
            if v == "#":
                for a in range(esc):
                    for b in range(esc):
                        py, px = y + (k // 3) * esc + a, x + i * 4 * esc + (k % 3) * esc + b
                        if 0 <= py < len(img) and 0 <= px < len(img[0]):
                            img[py][px] = color


def _mezcla(a, b, f):
    return tuple(int(a[i] * (1 - f) + b[i] * f) for i in range(3))


def _panel_planta(casa, y, T):
    """Matriz RGB de un piso visto desde arriba (caja + 1 de margen)."""
    x0, x1, z0, z1 = casa.x0 - 1, casa.x1 + 1, casa.z0 - 1, casa.z1 + 1
    cab = 14
    img = [[(250, 250, 250)] * ((x1 - x0 + 1) * T) for _ in range((z1 - z0 + 1) * T + cab)]
    _texto_png(img, 2, 2, "y%d" % y, esc=2)
    _texto_png(img, len(img[0]) - 16, 2, "N^", esc=2)
    cuarto_de = {}
    for c in casa.cuartos:
        if c["y"] == y:
            for q in c["celdas"]:
                cuarto_de[q] = c
    aparecen = {q for c in casa.cuartos if c["y"] == y for q in c["aparecen"]}
    seres_aqui = {}
    for c in casa.cuartos:
        if c["y"] == y:
            for s in c.get("seres") or []:
                if s["tipo"] in SER_CAT:
                    seres_aqui.setdefault((s["x"], s["z"]), []).append(SER_CAT[s["tipo"]])
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            px, py = (x - x0) * T, (z - z0) * T + cab
            n1, p1 = casa.e(x, y + 1, z)
            k0, k1 = casa.cat(x, y, z), categoria(n1, p1)
            piso = casa.e(x, y - 1, z)[0]
            # fondo: pared, ventana, piso, hueco o afuera
            if k1 == "muro" and not se_pasa(n1, p1):
                c1 = n1.split(":")[-1]
                fondo = (150, 200, 235) if _tiene(c1, "glass", "pane") else _mezcla(MA.color_de_bloque(c1) or (90, 90, 90), (0, 0, 0), 0.55)
            elif piso is None:
                fondo = VACIO
            elif casa.pasa(x, y - 1, z) and k0 not in MUEBLES:
                fondo = (45, 45, 55)  # hueco: escalera, sótano o caída
            else:
                fondo = _mezcla(MA.color_de_bloque(piso.split(":")[-1]) or (150, 150, 150), (255, 255, 255), 0.55)
                if not casa.dentro(x, z) or (x, z) not in cuarto_de and not casa.techado(x, y, z):
                    fondo = _mezcla(fondo, (170, 215, 160), 0.5)  # afuera o patio sin techo
            # alfombra: tiñe el piso
            col = [casa.e(x, yy, z) for yy in range(y - 1, y + 4)]
            cats = [categoria(n, p) for n, p in col]
            if "alfombra" in cats and fondo not in ((45, 45, 55),):
                n = col[cats.index("alfombra")][0].split(":")[-1]
                fondo = _mezcla(fondo, MA.color_de_bloque(n) or CAT["alfombra"][2], 0.6)
            if (x, z) in aparecen:
                fondo = _mezcla(fondo, (235, 40, 40), 0.55)
            for a in range(T):
                for b in range(T):
                    img[py + b][px + a] = fondo
            # ícono de lo más importante de la columna (el cofre hundido del piso también cuenta)
            presentes = [k for i, k in enumerate(cats) if k and (k != "muro") and (i > 0 or k == "cofre") and (i < 4 or k in MUEBLES)]
            ganador = next((k for k, *_ in CATEGORIAS if k in presentes and CAT[k][3]), None)
            if ganador:
                ic, colr = CAT[ganador][3], CAT[ganador][2]
                m_ = max(1, T // 7)
                paso = (T - 2 * m_) / 5.0
                for fi, fila in enumerate(ic):
                    for ci, v in enumerate(fila):
                        if v == "#":
                            for a in range(int(m_ + ci * paso), int(m_ + (ci + 1) * paso)):
                                for b in range(int(m_ + fi * paso), int(m_ + (fi + 1) * paso)):
                                    img[py + b][px + a] = colr
            if "luz" in presentes and ganador != "luz":  # luz además de otra cosa: puntito amarillo en la esquina
                for a in range(T - max(2, T // 4), T):
                    for b in range(0, max(2, T // 4)):
                        img[py + b][px + a] = CAT["luz"][2]
            for s in seres_aqui.get((x, z), []):  # 29-oct: cuadro, armadura o vecino: cuadrito con su color abajo a la izquierda
                colr, med = CAT[s][2], max(3, T // 2)
                for a in range(1, med):
                    for b in range(T - med, T - 1):
                        img[py + b][px + a] = colr if (a in (1, med - 1) or b in (T - med, T - 2)) or s == "aldeano" else _mezcla(colr, (255, 255, 255), 0.4)
            if (x, z) in cuarto_de and (x, z) in {q for q in cuarto_de[(x, z)]["libres"]} and (x, y, z) not in casa.alcanzado:
                for a in range(0, T, 3):  # rayas: piso libre al que no se llega
                    img[py + a][px + a] = (120, 0, 160)
    # cuadrícula: fina cada bloque, gruesa cada 5 (coordenadas múltiplos de 5)
    for x in range(x0, x1 + 2):
        for py in range(cab, len(img)):
            px = (x - x0) * T
            if px < len(img[0]):
                img[py][px] = (60, 60, 60) if x % 5 == 0 else _mezcla(img[py][px], (0, 0, 0), 0.18)
    for z in range(z0, z1 + 2):
        py = (z - z0) * T + cab
        if py < len(img):
            for px in range(len(img[0])):
                img[py][px] = (60, 60, 60) if z % 5 == 0 else _mezcla(img[py][px], (0, 0, 0), 0.18)
    return img


def t_planta_interior(casa=None, x1=None, z1=None, x2=None, z2=None, y=None, tam=14):
    """Plano de cada piso visto desde arriba, con un ícono por tipo de mueble. y = un solo piso; sin y, todos."""
    guardar_mundo()
    nombre, caja = _caja(casa, x1, z1, x2, z2)
    c = Casa(Mundo(), caja, nombre)
    if c.vacia:
        return "Sin chunks generados en esa zona.", None
    alturas = [y] if y is not None else (c.alturas or [c.y_suelo + 1])
    alturas = alturas[:8]
    ancho = (c.x1 - c.x0 + 3)
    cols = min(len(alturas), 3 if ancho > 20 else 4)
    T = max(6, min(int(tam), 24, 2400 // max(1, ancho * cols)))
    paneles = [_panel_planta(c, yy, T) for yy in alturas]
    h, w = len(paneles[0]), len(paneles[0][0])
    filas = (len(paneles) + cols - 1) // cols
    sep = 8
    img = [[(255, 255, 255)] * (cols * w + (cols - 1) * sep) for _ in range(filas * h + (filas - 1) * sep)]
    for i, p in enumerate(paneles):
        ox, oy = (i % cols) * (w + sep), (i // cols) * (h + sep)
        for r in range(h):
            img[oy + r][ox:ox + w] = p[r]
    ruta = nombre_salida("planta")
    MA.escribir_png(ruta, img, escala=1)
    leyenda = ("Leyenda (norte arriba; %d px por bloque; línea gruesa cada 5 coordenadas): pared = oscuro, ventana = celeste, "
               "piso = color claro de su bloque, hueco/escalera = gris muy oscuro, afuera = verdoso, ROJO = luz 0 (aparecen "
               "monstruos), rayas moradas = piso al que no se llega. Íconos: cama rojo, cofre naranja, oficio gris, mesa café, "
               "asiento morado, estante magenta, planta verde, luz amarillo (puntito amarillo = luz además de otra cosa), "
               "adorno rosa, escalera de mano marrón, puerta café oscuro, otros turquesa; alfombra tiñe el piso. Cuadrito "
               "abajo a la izquierda = entidad: café = cuadro o marco, gris azulado = soporte de armadura, azul lleno = vecino.") % T
    return "%s\nPisos dibujados (izquierda a derecha, arriba a abajo): %s\n\n%s\nArchivo: %s" % (
        c.texto(detalle=False), ", ".join("y %d" % a for a in alturas), leyenda, ruta), ruta


def t_interior_isometrico(casa=None, x1=None, z1=None, x2=None, z2=None, y=None, desde="sureste", tam=12, alto=4):
    """La casa en 3D sin techo, con los muros de adelante recortados a la altura de los tobillos: se ven los muebles."""
    guardar_mundo()
    nombre, caja = _caja(casa, x1, z1, x2, z2)
    c = Casa(Mundo(), caja, nombre)
    if c.vacia:
        return "Sin chunks generados en esa zona.", None
    if y is None:  # el piso con más muebles (el más bajo suele ser un sótano o la losa de abajo)
        dentro = [cu for cu in c.cuartos if not cu["porche"]]
        y = max(dentro, key=lambda cu: len(cu["cosas"]))["y"] if dentro else c.y_suelo + 1
    alto = max(2, min(int(alto), 8))
    xa, xb, za, zb = c.x0 - 1, c.x1 + 1, c.z0 - 1, c.z1 + 1
    if xb - xa > 96 or zb - za > 96:
        return "Zona demasiado grande (máximo 96 x 96).", None

    # muros del fondo: los que quedan detrás de todos los cuartos de este piso, mirando desde la cámara
    giro = giro_iso(xb - xa + 1, zb - za + 1, desde)[0]
    min_a, min_b = {}, {}
    for cu in c.cuartos:
        if cu["y"] == y and not cu["porche"]:
            for qx, qz in cu["celdas"]:
                a, b = giro(qx - xa, qz - za)
                min_a[b], min_b[a] = min(min_a.get(b, a), a), min(min_b.get(a, b), b)

    def color(x, yy, z, a, b):
        n, p = c.e(x, yy, z)
        k = categoria(n, p)
        if k is None:
            return None
        base = MA.color_de_bloque(n.split(":")[-1]) or (128, 128, 128)
        if not c.dentro(x, z):
            return _mezcla(base, (60, 60, 60), 0.4) if yy < y else None  # alrededor: solo el suelo, apagado
        if k in ("muro", "puerta"):
            if yy < y:
                return _mezcla(base, (60, 60, 60), 0.35)  # piso apagado para que resalten los muebles
            if a < min_a.get(b, -1) or b < min_b.get(a, -1):
                return base  # muro del fondo: entero
            return _mezcla(base, (0, 0, 0), 0.25) if yy == y and k == "muro" else None  # los demás, a 1 bloque
        if k == "luz":
            return (255, 225, 60)
        if k == "alfombra":
            return base
        return _mezcla(base, CAT[k][2], 0.6)
    ruta = nombre_salida("interior")
    pintar_iso(ruta, xa, za, xb, zb, y - 1, y + alto - 1, desde, max(3, min(int(tam), 20)), color)
    otros = ", ".join("y %d" % a for a in c.alturas if a != y)
    return ("Interior de %s, piso y %d (se dibuja de y %d a %d) mirando desde el %s. Sin techo; los muros del fondo enteros "
            "y los demás recortados a 1 bloque. Muebles teñidos por tipo (cama rojo, cofre naranja, mesa café, asiento morado, "
            "estante magenta, planta verde, luz amarillo, oficio gris, otros turquesa).%s\nArchivo: %s") % (
        nombre, y, y - 1, y + alto - 1, desde, (" Otros pisos: " + otros + ".") if otros else "", ruta), ruta


# --- Vistas con planos X, Y, Z (29-oct, pedido de Sergio: «3D con planos para cualquier zona, con pocos tokens») ---

COLOR_SER = {"monstruo": (230, 30, 30), "aldeano": (40, 120, 255), "armadura": (255, 255, 255), "animal": (255, 170, 30),
             "cuadro": (200, 140, 60), "marco": (200, 140, 60), "objeto": (255, 255, 0), "vehiculo": (0, 220, 220),
             "display": (200, 0, 255), "otro": (255, 0, 255)}


def color_bloque_ojo(n):
    """RGB de un bloque con su id real. Vanilla: la tabla de mapa_aldea. Mods: por palabras clave del nombre o, si no se
    reconoce, un color estable sacado del id (así cada mod distinto se ve distinto)."""
    base = n.split("[")[0]
    corto = base.split(":")[-1]
    if ":" not in base or base.startswith("minecraft:"):
        return MA.color_de_bloque(corto) or (128, 128, 128)
    c = MA.COLORES_BLOQUE.get(corto)
    if c:
        return c
    for k, rgb in (("dark_oak", (65, 45, 25)), ("oak", (160, 130, 80)), ("spruce", (110, 80, 50)), ("birch", (200, 190, 140)),
                   ("jungle", (150, 110, 70)), ("acacia", (170, 90, 50)), ("cherry", (230, 170, 190)), ("mangrove", (120, 50, 50)),
                   ("stone", (125, 125, 125)), ("brick", (150, 90, 80)), ("iron", (190, 190, 195)), ("gold", (240, 200, 60)),
                   ("lantern", (255, 225, 60)), ("lamp", (255, 225, 60)), ("candle", (240, 230, 190)), ("glass", (195, 225, 235)),
                   ("wool", (230, 230, 230)), ("hay", (200, 170, 40)), ("sack", (190, 160, 110)), ("barrel", (140, 100, 60)),
                   ("chest", (160, 110, 40)), ("drawer", (150, 100, 55)), ("table", (150, 100, 55)), ("chair", (140, 95, 50))):
        if k in corto:
            return rgb
    h = zlib.crc32(base.encode("utf-8"))
    return (60 + (h & 0xFF) * 3 // 4, 60 + ((h >> 8) & 0xFF) * 3 // 4, 60 + ((h >> 16) & 0xFF) * 3 // 4)


def es_mod(n):
    return ":" in n and not n.startswith("minecraft:")


def _marca(img, cx, cy, c, r=2):
    """Cuadrito con borde negro centrado en (cx, cy)."""
    for dy in range(-r - 1, r + 2):
        for dx in range(-r - 1, r + 2):
            px, py = cx + dx, cy + dy
            if 0 <= py < len(img) and 0 <= px < len(img[0]):
                img[py][px] = (0, 0, 0) if max(abs(dx), abs(dy)) == r + 1 else c


def t_planos(x1, y1, z1, x2, y2, z2, tam=None, desde="sureste", marcar_entidades=True):
    """UNA imagen chica con 4 vistas de una zona, desde los .mca: planta (X-Z, desde arriba), isométrica 3D, alzado (X-Y, de
    frente, mirando al norte) y perfil (Z-Y, de lado, mirando al este). Ejes con coordenadas absolutas cada 5 bloques, colores
    por bloque (tira de leyenda numerada abajo; el texto da el id real de cada número, con «(mod)» en los de mods), cuadritos de
    colores para las entidades guardadas (rojo = monstruo, azul = vecino, blanco = soporte de armadura...)."""
    x1, x2, y1, y2, z1, z2 = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2), min(z1, z2), max(z1, z2)
    W, H, D = x2 - x1 + 1, y2 - y1 + 1, z2 - z1 + 1
    if W > 100 or D > 100 or H > 80:
        return "Zona demasiado grande para planos (máximo 100 x 80 x 100 bloques).", None
    guardar_mundo()
    m = Mundo()
    vol, cuenta = {}, {}
    for x in range(x1, x2 + 1):
        for z in range(z1, z2 + 1):
            for y in range(y1, y2 + 1):
                b = m.bloque(x, y, z)
                if b is None or b in MA.AIRE:
                    continue
                b = b.split("[")[0]
                vol[(x, y, z)] = b
                cuenta[b] = cuenta.get(b, 0) + 1
    seres = [s for s in m.seres(x1, z1, x2, z2) if y1 <= s["y"] <= y2] if marcar_entidades else []
    orden = sorted(cuenta, key=lambda n: -cuenta[n])
    mods = [n for n in orden if es_mod(n)]
    resto = [n for n in orden if not es_mod(n)]
    leyenda = (resto[:max(8, 26 - len(mods))] + mods)[:30]
    num = {n: i + 1 for i, n in enumerate(leyenda)}
    col = {n: color_bloque_ojo(n) for n in cuenta}
    T = tam or max(2, min(14, 400 // max(W, D, H * 2, 1)))
    Ti = max(2, min(8, (W * T) // max(1, (W + D))))
    MI, MS = 22, 10  # margen izquierdo (números de la vertical) y superior (números de la horizontal)

    def lienzo(w, h):
        return [[(245, 245, 245)] * (w * T + MI) for _ in range(h * T + MS)]

    def pixel(img, i, j, c):
        for dy in range(T):
            for dx in range(T):
                img[MS + j * T + dy][MI + i * T + dx] = c

    def tono(c, f):
        return tuple(max(0, min(255, int(v * f))) for v in c)

    pl = lienzo(W, D)
    for x in range(x1, x2 + 1):
        for z in range(z1, z2 + 1):
            for y in range(y2, y1 - 1, -1):
                n = vol.get((x, y, z))
                if n:
                    pixel(pl, x - x1, z - z1, tono(col[n], 0.8 + 0.4 * (y - y1) / max(1, H - 1)))
                    if es_mod(n):  # contorno magenta: bloque de mod
                        for k in range(T):
                            for (a, b) in ((k, 0), (k, T - 1), (0, k), (T - 1, k)):
                                pl[MS + (z - z1) * T + b][MI + (x - x1) * T + a] = (255, 0, 255)
                    break
    al = lienzo(W, H)  # mirando al norte desde el sur: lo más cercano es la z mayor
    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            for z in range(z2, z1 - 1, -1):
                n = vol.get((x, y, z))
                if n:
                    pixel(al, x - x1, y2 - y, tono(col[n], 0.75 + 0.35 * (z - z1) / max(1, D - 1)))
                    if es_mod(n):
                        al[MS + (y2 - y) * T][MI + (x - x1) * T] = (255, 0, 255)
                    break
    pf = lienzo(D, H)  # mirando al este desde el oeste: lo más cercano es la x menor
    for z in range(z1, z2 + 1):
        for y in range(y1, y2 + 1):
            for x in range(x1, x2 + 1):
                n = vol.get((x, y, z))
                if n:
                    pixel(pf, z - z1, y2 - y, tono(col[n], 0.75 + 0.35 * (x2 - x) / max(1, W - 1)))
                    if es_mod(n):
                        pf[MS + (y2 - y) * T][MI + (z - z1) * T] = (255, 0, 255)
                    break
    paso = 5 if 5 * T >= 22 else 10

    def rejilla(img, c0, c1, v0, v1, vertical):
        for c in range(c0, c1 + 1):
            if c % paso == 0:
                px = MI + (c - c0) * T
                for j in range(MS, len(img)):
                    img[j][px] = _mezcla(img[j][px], (0, 0, 0), 0.35)
                for k in range(MS - 3, MS):
                    img[k][px] = (40, 40, 40)
                _texto_png(img, max(0, px - 2 * len(str(c))), 1, str(c), color=(20, 20, 20), esc=1)
        for c in range(v0, v1 + 1):
            if c % paso == 0:
                py = MS + ((v1 - c) if vertical == "y" else (c - v0)) * T
                if py >= len(img):
                    continue
                for i in range(MI, len(img[0])):
                    img[py][i] = _mezcla(img[py][i], (0, 0, 0), 0.35)
                for k in range(MI - 3, MI):
                    img[py][k] = (40, 40, 40)
                s_ = str(c)
                _texto_png(img, 0, max(0, py - 2), s_[-4:], color=(20, 20, 20), esc=1)
    rejilla(pl, x1, x2, z1, z2, "z")
    rejilla(al, x1, x2, y1, y2, "y")
    rejilla(pf, z1, z2, y1, y2, "y")
    r = 1 if T < 5 else 2
    for s in seres:
        c = COLOR_SER.get(s["tipo"], (255, 0, 255))
        if x1 <= s["x"] <= x2 and z1 <= s["z"] <= z2:
            _marca(pl, MI + (s["x"] - x1) * T + T // 2, MS + (s["z"] - z1) * T + T // 2, c, r)
            _marca(al, MI + (s["x"] - x1) * T + T // 2, MS + (y2 - s["y"]) * T + T // 2, c, r)
            _marca(pf, MI + (s["z"] - z1) * T + T // 2, MS + (y2 - s["y"]) * T + T // 2, c, r)

    def color_iso(x, y, z, *_):
        n = vol.get((x, y, z))
        if n is None or not any(vol.get((x + a, y + b, z + c_)) is None for a, b, c_ in V6):
            return None
        return col[n]
    iso, A, B = pintar_iso_img(x1, z1, x2, z2, y1, y2, desde, Ti, color_iso)
    giro = giro_iso(W, D, desde)[0]
    for s in seres:
        if x1 <= s["x"] <= x2 and z1 <= s["z"] <= z2:
            a, b = giro(s["x"] - x1, s["z"] - z1)
            _marca(iso, (a - b) * Ti + B * Ti + Ti, (a + b) * Ti // 2 + (y2 - s["y"]) * Ti + Ti // 2, COLOR_SER.get(s["tipo"], (255, 0, 255)), 2)
    _texto_png(pl, len(pl[0]) - 14, 1, "N^", color=(200, 0, 0), esc=1)
    blanco = (255, 255, 255)

    def pegar(paneles):
        h = max(len(p) for p in paneles)
        out = [[] for _ in range(h)]
        for p in paneles:
            for j in range(h):
                out[j] += (p[j] if j < len(p) else [blanco] * len(p[0])) + [blanco] * 6
        return out
    fila1, fila2 = pegar([pl, iso]), pegar([al, pf])
    w = max(len(fila1[0]), len(fila2[0]))
    fila1 = [f + [blanco] * (w - len(f)) for f in fila1]
    fila2 = [f + [blanco] * (w - len(f)) for f in fila2]
    sw = 30
    cols = max(1, min(len(leyenda), w // sw))
    filas_ley = (len(leyenda) + cols - 1) // cols
    tira = [[blanco] * w for _ in range(filas_ley * 16 + 2)]
    for k, n in enumerate(leyenda):
        cx, cy = (k % cols) * sw + 1, (k // cols) * 16 + 1
        borde = (255, 0, 255) if es_mod(n) else (0, 0, 0)
        for j in range(12):
            for i in range(10):
                tira[cy + j][cx + i] = borde if (j in (0, 11) or i in (0, 9)) else col[n]
        _texto_png(tira, cx + 12, cy + 3, str(k + 1), color=(0, 0, 0), esc=1)
    img = fila1 + [[blanco] * w] * 6 + fila2 + [[blanco] * w] * 6 + tira
    ruta = nombre_salida("planos")
    MA.escribir_png(ruta, img, escala=1)
    lin = ["Planos de x %d..%d, y %d..%d, z %d..%d (%d x %d x %d). Arriba izquierda: PLANTA (X derecha, Z abajo, norte arriba); arriba derecha: "
           "ISOMÉTRICA desde el %s; abajo izquierda: ALZADO (X derecha, Y arriba, mirando al norte); abajo derecha: PERFIL (Z derecha, Y arriba, "
           "mirando al este). Números = coordenadas absolutas cada %d bloques. Contorno magenta = bloque de mod. %d px por bloque." % (
               x1, x2, y1, y2, z1, z2, W, H, D, desde, paso, T)]
    lin.append("Leyenda (número de la tira = id real · cuántos): " + "; ".join(
        "%d=%s%s ×%d" % (num[n], n, " (mod)" if es_mod(n) else "", cuenta[n]) for n in leyenda))
    otros = len(cuenta) - len(leyenda)
    if otros > 0:
        lin.append("(%d ids más sin número, poco comunes; %d de mods en total)" % (otros, sum(1 for n in cuenta if es_mod(n))))
    if seres:
        por = {}
        for s in seres:
            por.setdefault((s["tipo"], s["id"]), []).append(s)
        lin.append("Entidades guardadas en la zona (%d; cuadritos: rojo monstruo, azul vecino, blanco armadura, naranja animal, amarillo objeto): " % len(seres) + "; ".join(
            "%s %s×%d en %s" % (t, i.replace("minecraft:", ""), len(v), ", ".join("(%d,%d,%d)" % (q["x"], q["y"], q["z"]) for q in v[:3]) + ("…" if len(v) > 3 else ""))
            for (t, i), v in sorted(por.items(), key=lambda kv: -len(kv[1]))[:14]))
    lin.append("Archivo: " + ruta)
    return "\n".join(lin), ruta


# --- Protocolo MCP (JSON-RPC por stdin/stdout, una línea por mensaje) ---

N, E, S = {"type": "number"}, {"type": "integer"}, {"type": "string"}
HERRAMIENTAS = [
    ("estado", "Estado del servidor, jugadores conectados, BlueMap y cliente de prueba.", {}, []),
    ("bloques", "Bloques alrededor de un punto (radio máx 5), capa por capa como cuadrícula de letras con leyenda. Lee los .mca.",
     {"x": E, "y": E, "z": E, "radio": E}, ["x", "y", "z"]),
    ("corte_lateral", "Imagen de perfil del terreno a lo largo de una línea (x1,z1)→(x2,z2): muestra paredes de tierra, escalones y "
     "huecos. Devuelve la imagen y la lista de saltos de 3+ bloques.",
     {"x1": E, "z1": E, "x2": E, "z2": E, "y_min": E, "y_max": E, "escala": E}, ["x1", "z1", "x2", "z2"]),
    ("vista_cenital", "Mapa visto desde arriba de una zona (máx 600x600). marcas = lista de [x, z] para marcar en rojo.",
     {"x1": E, "z1": E, "x2": E, "z2": E, "escala": E, "marcas": {"type": "array", "items": {"type": "array", "items": E}}},
     ["x1", "z1", "x2", "z2"]),
    ("buscar_problemas", "Auditoría en texto (sin imágenes) alrededor de un centro (por defecto la fogata del pueblo): paredes de "
     "tierra, bordes rectos, cosas flotando, cofres vacíos, caminos de escaleras, pisos vacíos, puertas que no dejan entrar, "
     "zanjas y casas enterradas; y desde el 30-oct tipo «accesos» (¿se llega a pie desde la plaza a la puerta de cada casa de TODA "
     "la aldea, con pasos de 1 bloque como máximo y 2 de aire? da el desnivel y el sitio del salto, y si el camino del plano "
     "tiene un corte), «hoyos» (hoyo trampa de 1x1 o 1x2 y 2+ de hondo) y letreros, faroles, estandartes y macetas sin apoyo "
     "dentro de «flotantes». Radio máx 300. "
     "solo_pueblo=true compara con el terreno natural (servidor/niebla_original) y deja solo lo que cambió el pueblo.",
     {"centro_x": E, "centro_z": E, "radio": E, "max_por_tipo": E, "solo_pueblo": {"type": "boolean"}, "detalle": {"type": "boolean"},
      "tipos": {"type": "array", "items": {"type": "string", "enum": list(TIPOS)}}}, []),
    ("comando", "Comando RCON. Los de lectura (list, data get, execute if/unless sin run, locate, bluemap...) van directo; "
     "cualquier otro queda BLOQUEADO salvo confirmar=true, que solo se usa si Sergio lo pidió o aprobó en el chat.",
     {"comando": S, "confirmar": {"type": "boolean"}}, ["comando"]),
    ("bluemap", "Dirección de BlueMap (mapa 3D, puerto 8100) apuntando a un lugar, para abrirla en el navegador integrado.",
     {"x": E, "y": E, "z": E, "distancia": N, "giro": N, "inclinacion": N, "vista": {"type": "string", "enum": ["perspective", "flat", "free"]}}, []),
    ("mirar_hacia", "Calcula yaw y pitch para que la cámara en (x,y,z) mire a un objetivo. Útil antes de foto.",
     {"x": N, "y": N, "z": N, "objetivo_x": N, "objetivo_y": N, "objetivo_z": N}, ["x", "y", "z", "objetivo_x", "objetivo_y", "objetivo_z"]),
    ("abrir_cliente", "Abre el Minecraft de prueba «Prueba» (espectador). SOLO con autorizado=true si Sergio lo dijo en el chat.",
     {"autorizado": {"type": "boolean"}}, []),
    ("foto", "Foto real del juego: teletransporta a Prueba (espectador) y captura su ventana. SOLO con autorizado=true si Sergio "
     "lo dijo en el chat. Necesita el cliente abierto. Ancho 640 por defecto; repite si sale la pantalla de MCA; mueve la cámara si "
     "está metida en hojas o techos; con objetivo_x/y/z apunta sola y comprueba que nada tape la línea.",
     {"x": N, "y": N, "z": N, "yaw": N, "pitch": N, "autorizado": {"type": "boolean"}, "espera": E, "ancho": E,
      "objetivo_x": N, "objetivo_y": N, "objetivo_z": N, "acomodar": {"type": "boolean"},
      "dimension": {"type": "string", "enum": ["overworld", "the_nether", "the_end"]}}, ["x", "y", "z"]),
    ("cerrar_cliente", "Cierra el Minecraft de prueba (solo el de juego_prueba, nunca el de Sergio).", {}, []),
    ("revisar_foto_local", "La IA local (qwen3.5:9b con visión, en la GPU) revisa una foto con preguntas de sí/no y devuelve "
     "texto: 0 tokens de imagen para Claude. Úsala antes de abrir una foto; abre solo las que tengan algún SÍ. Tarda ~3 min.",
     {"ruta": S, "preguntas": {"type": "array", "items": S}}, ["ruta"]),
    ("isometrica", "Vista 3D isométrica de una zona (máx 96x96) dibujada desde los archivos del mundo: sin cliente ni pantalla. "
     "desde = sureste|suroeste|noroeste|noreste. Barata: úsala antes que las fotos para ver formas y alturas.",
     {"x1": E, "z1": E, "x2": E, "z2": E, "y_min": E, "y_max": E, "desde": {"type": "string", "enum": ["sureste", "suroeste", "noroeste", "noreste"]},
      "tam": E}, ["x1", "z1", "x2", "z2"]),
    ("fotos_angulos", "4 fotos reales (norte, este, sur, oeste) de un punto juntadas en UNA imagen 2x2 (cuesta como una foto). "
     "SOLO con autorizado=true si Sergio lo dijo. Necesita el cliente abierto.",
     {"x": N, "y": N, "z": N, "distancia": N, "altura": N, "autorizado": {"type": "boolean"}, "ancho": E}, ["x", "y", "z"]),
    ("usar_servidor", "Elige con qué servidor trabaja el Ojo: «sergio» (su mundo, 25565) o «pruebas» (servidor_pruebas, 25566, "
     "aldea completa). Afecta RCON, la lectura de bloques y a qué servidor entra el cliente Prueba.",
     {"cual": {"type": "string", "enum": ["sergio", "pruebas"]}}, ["cual"]),
    ("usar_dimension", "Qué dimensión leen las vistas desde los archivos (cenital, isométrica, corte, bloques, problemas): "
     "overworld, the_nether o the_end.",
     {"dimension": {"type": "string", "enum": ["overworld", "the_nether", "the_end"]}}, ["dimension"]),
    ("planta_interior", "Plano de los pisos de una casa visto desde arriba (sin techo), con un ícono por tipo de mueble (camas, "
     "cofres, mesas, asientos, estantes, plantas, luces, oficios, puertas), las paredes, ventanas, huecos, el piso con luz 0 "
     "en rojo y el piso al que no se llega rayado. casa = número o nombre del plano (o una zona x1,z1,x2,z2). Sin y dibuja "
     "todos los pisos. Lee los archivos del mundo: sin cliente.",
     {"casa": {"type": "string", "description": "número o nombre de la casa en planos/plan_aldea.json (p. ej. «0» o «tavern»)"},
      "x1": E, "z1": E, "x2": E, "z2": E, "y": E, "tam": E}, []),
    ("interior_isometrico", "La casa en 3D sin techo y con los muros de adelante recortados: se ven los muebles por dentro, "
     "teñidos por tipo. y = el piso (por defecto el que tiene más muebles); alto = cuántos bloques sobre el piso (4); desde = de dónde "
     "mira la cámara.",
     {"casa": {"type": "string", "description": "número o nombre de la casa en planos/plan_aldea.json (p. ej. «0» o «tavern»)"},
      "x1": E, "z1": E, "x2": E, "z2": E, "y": E, "alto": E, "tam": E,
      "desde": {"type": "string", "enum": ["sureste", "suroeste", "noroeste", "noreste"]}}, []),
    ("inventario_interior", "Texto: por piso y cuarto, cuántas camas, cofres, mesas, asientos, luces, plantas, estantes, oficios "
     "y adornos tiene una casa (con sus nombres, también de mcw-furniture, handcrafted, another_furniture y supplementaries), "
     "la luz media y mínima, y los defectos: cuartos vacíos u oscuros (luz 0, aparecen monstruos), puertas tapadas, muebles "
     "flotando, piso al que no se llega y camas tapadas o sin espacio. También lo que hay dentro de los cofres y las "
     "entidades de cada cuarto (cuadros, marcos, soportes de armadura, vecinos).",
     {"casa": {"type": "string", "description": "número o nombre de la casa en planos/plan_aldea.json (p. ej. «0» o «tavern»)"},
      "x1": E, "z1": E, "x2": E, "z2": E}, []),
    ("revisar_decoracion", "Decoración con sentido por tipo de casa (según la plantilla del plano): herrería con fragua y "
     "yunque, iglesia con altar, velas y bancas, dormitorio con cama y baúl, etc. Avisa lo que desentona con el terror "
     "medieval (tortas, sofás o muebles modernos, colores y flores alegres, cuadros chistosos, animales de broma, objetos "
     "de fiesta en cofres) y resume cofres (con cosas, vacíos, botín sin abrir), cuadros, armaduras y vecinos. Sin casa = "
     "todas; solo_problemas=true oculta las que están bien.",
     {"casa": {"type": "string", "description": "número o nombre de la casa en planos/plan_aldea.json"},
      "solo_problemas": {"type": "boolean"}}, []),
    ("revisar_plantillas", "Lo mismo que revisar_decoracion, pero sobre las PLANTILLAS que se van a construir "
     "(planos/convertidos/rellenas/*.nbt), sin servidor: sirve para ver si un arreglo de interiores quedó bien antes de "
     "regenerar el pueblo. Coordenadas de la plantilla. plantilla = parte del nombre (sin ella, todas).",
     {"plantilla": S, "solo_problemas": {"type": "boolean"}}, []),
    ("seres", "Entidades guardadas en una zona o casa (lee entities/ del mundo, sin RCON): vecinos con nombre y oficio, "
     "cuadros, marcos y lo que tienen, soportes de armadura y lo que llevan, monstruos, animales (marca los de broma), "
     "objetos tirados y vehículos. Sin zona = 150 alrededor de la fogata.",
     {"x1": E, "z1": E, "x2": E, "z2": E, "casa": S,
      "tipos": {"type": "array", "items": {"type": "string", "enum": ["aldeano", "cuadro", "marco", "armadura", "monstruo",
                                                                      "animal", "objeto", "vehiculo", "display", "otro"]}}}, []),
    ("planos", "UNA imagen chica con 4 vistas de cualquier zona (máx 100x80x100), leída de los .mca: planta (X-Z), isométrica, alzado (X-Y) y "
     "perfil (Z-Y), con coordenadas absolutas cada 5 bloques, colores por bloque, contorno magenta en los bloques de MODS, cuadritos de "
     "colores en las entidades (monstruos, vecinos, armaduras) y leyenda numerada con el id real de cada bloque. Barata en tokens: úsala "
     "para ver una zona entera de un vistazo.",
     {"x1": E, "y1": E, "z1": E, "x2": E, "y2": E, "z2": E, "tam": E,
      "desde": {"type": "string", "enum": ["sureste", "suroeste", "noroeste", "noreste"]}, "marcar_entidades": {"type": "boolean"}},
     ["x1", "y1", "z1", "x2", "y2", "z2"]),
    ("auditar_interiores", "Inventario corto de TODAS las casas del plano (una línea por casa con sus avisos) y un JSON con el "
     "detalle completo. detalle=true devuelve el inventario completo de cada casa (largo).",
     {"detalle": {"type": "boolean"}, "max_avisos": E}, []),
    ("vista_3d", "Vista 3D como en el juego, sin abrirlo (camara_x/y/z = parado ahí como un jugador, con giro, inclinacion y fov): cada bloque con su forma y textura reales (vanilla y mods), hasta 129x129 "
     "bloques (radio máx 64; por defecto 50 = casi 100 bloques), desde CUALQUIER ángulo. desde = norte|noreste|este|sureste|sur|"
     "suroeste|oeste|noroeste (o giro = yaw de Minecraft); inclinacion 0-90 (90 = desde arriba). proyeccion orto (para medir) o "
     "perspectiva (como foto de dron; distancia = bloques de la cámara al centro). casa = número o nombre del plano. noche=true "
     "usa la luz real guardada (lo negro es luz 0: ahí aparecen monstruos). sin_hojas=true quita las hojas de los árboles. "
     "seres=true marca aldeanos (azul), monstruos (rojo), animales (amarillo) y armaduras (gris). y_max corta los techos para ver "
     "adentro. marcas = [[x,y,z],...] en rojo. Devuelve dónde caen en la imagen el centro y las esquinas. 2 a 6 s.",
     {"x": E, "z": E, "y": E, "radio": E, "casa": S,
      "desde": {"type": "string", "enum": ["norte", "noreste", "este", "sureste", "sur", "suroeste", "oeste", "noroeste"]},
      "giro": N, "inclinacion": N, "proyeccion": {"type": "string", "enum": ["orto", "perspectiva"]}, "distancia": N,
      "noche": {"type": "boolean"}, "sin_hojas": {"type": "boolean"}, "seres": {"type": "boolean"}, "y_min": E, "y_max": E,
      "ancho": E, "alto": E, "marcas": {"type": "array", "items": {"type": "array", "items": E}},
      "camara_x": N, "camara_y": N, "camara_z": N, "fov": N,
      "cuadricula": {"type": "boolean", "description": "líneas de coordenadas sobre el suelo cada 10 bloques (5 en zonas chicas), "
                     "rojas = x, azules = z, con sus números en el borde; por defecto sí"},
      "marcar": {"type": "boolean", "description": "tiñe cada mueble por tipo y numera cofres, camas, oficios, mesas, asientos, "
                 "estantes y luces (y los seres si seres=true), con la lista y coordenadas en el texto"}}, []),
    ("vista_pisos", "Una casa del plano PISO POR PISO, como con rayos X: cada piso en 3D (texturas reales) cortado a 2 bloques "
     "sobre su piso, con muebles teñidos y numerados y los seres (aldeanos, monstruos) marcados, en UNA imagen. El texto trae por "
     "piso sus cuartos, la luz, los avisos (vacío, oscuro, puertas tapadas...) y la lista numerada con coordenadas. noche=true "
     "usa la luz real.",
     {"casa": S, "desde": {"type": "string", "enum": ["norte", "noreste", "este", "sureste", "sur", "suroeste", "oeste", "noroeste"]},
      "inclinacion": N, "seres": {"type": "boolean"}, "marcar": {"type": "boolean"}, "noche": {"type": "boolean"}, "ancho": E},
     ["casa"]),
    ("revisar_seres", "Revisión de las ENTIDADES de una zona (radio por defecto 120 alrededor de la fogata) o casa: monstruos "
     "dentro de las casas, aldeanos/animales metidos en bloques o flotando, aldeanos con el mismo nombre, objetos tirados, marcos "
     "vacíos, armaduras sin nada y animales dentro de casas. Lista por gravedad con coordenadas; imagen=true agrega una vista 3D "
     "con anillos rojos en los avisos.",
     {"x": E, "z": E, "radio": E, "casa": S, "imagen": {"type": "boolean"}}, []),
    ("contar_bloques", "CUENTA los bloques de una zona leyendo los archivos del mundo: cuántos hay de cada tipo (de más a menos), "
     "cuántos no son aire, y el largo, ancho y alto de lo que hay. Zona = caja (x1,z1,x2,z2 y si quieres y1,y2), casa (número o "
     "nombre, también en español: castillo, iglesia…) o x, z y radio. buscar = parte del nombre (o varias separadas por coma, p. ej. "
     "«lantern,torch») para listar dónde está cada uno con sus coordenadas. Máximo 25 millones de lugares.",
     {"x1": E, "y1": E, "z1": E, "x2": E, "y2": E, "z2": E, "casa": S, "x": E, "z": E, "radio": E, "buscar": S, "max_lista": E}, []),
    ("comparar_foto", "Pone lado a lado una foto REAL del juego (sacada con foto, que guarda su pose en un .json) y la misma vista "
     "dibujada por el Ojo, y mide el parecido de colores y de formas. Para calibrar el Ojo contra el juego.",
     {"ruta": S, "radio": E}, ["ruta"]),
    ("leer_textos", "Lo que DICEN los carteles (frente y dorso), los libros de los atriles y los libros escritos guardados en "
     "cofres o barriles de una zona, casa (también en español) o radio (por defecto 150 alrededor de la fogata): título, autor, "
     "páginas (la primera, o todas con completo=true) y carteles en blanco.",
     {"casa": S, "x": E, "z": E, "radio": E, "x1": E, "z1": E, "x2": E, "z2": E, "completo": {"type": "boolean"}}, []),
    ("medir", "Distancia en bloques entre dos puntos: en línea recta, por el suelo, en cuadrícula, las diferencias en x, y, z y "
     "hacia qué punto cardinal queda.", {"x1": E, "y1": E, "z1": E, "x2": E, "y2": E, "z2": E}, ["x1", "y1", "z1", "x2", "y2", "z2"]),
    ("vista_vuelta", "La misma zona (por defecto radio 50) desde los 4 rincones (NO, NE, SO, SE) y desde arriba, en UNA sola imagen "
     "con letreros y rosa de los vientos: ver algo desde todos los ángulos gastando como una foto. Mismas opciones que vista_3d "
     "(casa, noche, sin_hojas, seres, y_max). Unos 7 s.",
     {"x": E, "z": E, "radio": E, "inclinacion": N, "casa": S, "noche": {"type": "boolean"}, "sin_hojas": {"type": "boolean"},
      "seres": {"type": "boolean"}, "y_max": E, "con_arriba": {"type": "boolean"}, "ancho": E, "marcar": {"type": "boolean"}}, []),
]
FUNCIONES = {"estado": t_estado, "bloques": t_bloques, "corte_lateral": t_corte_lateral, "vista_cenital": t_vista_cenital,
             "buscar_problemas": t_buscar_problemas, "comando": t_comando, "bluemap": t_bluemap, "mirar_hacia": t_mirar_hacia,
             "abrir_cliente": t_abrir_cliente, "foto": t_foto, "cerrar_cliente": t_cerrar_cliente,
             "usar_servidor": t_usar_servidor, "revisar_foto_local": t_revisar_foto_local,
             "isometrica": t_isometrica, "fotos_angulos": t_fotos_angulos, "usar_dimension": t_usar_dimension,
             "planta_interior": t_planta_interior, "interior_isometrico": t_interior_isometrico,
             "inventario_interior": t_inventario_interior, "auditar_interiores": t_auditar_interiores,
             "revisar_decoracion": t_revisar_decoracion, "seres": t_seres, "revisar_plantillas": t_revisar_plantillas,
             "planos": t_planos, "vista_3d": t_vista_3d, "vista_vuelta": t_vista_vuelta, "vista_pisos": t_vista_pisos, "revisar_seres": t_revisar_seres, "contar_bloques": t_contar_bloques,
             "medir": t_medir, "comparar_foto": t_comparar_foto, "leer_textos": t_leer_textos}


def llamar(nombre, args):
    res = FUNCIONES[nombre](**(args or {}))
    texto, ruta = res if isinstance(res, tuple) else (res, None)
    contenido = [{"type": "text", "text": texto}]
    if ruta:
        contenido.append({"type": "image", "data": png_base64(ruta), "mimeType": "image/png"})
    return contenido


def responder(salida, msg):
    salida.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    salida.flush()


def servir():
    salida = sys.stdout.buffer
    sys.stdout = sys.stderr  # cualquier print suelto va a stderr y no rompe el protocolo
    for linea in sys.stdin.buffer:
        if not linea.strip():
            continue
        try:
            msg = json.loads(linea.decode("utf-8"))
        except ValueError:
            continue
        mid, metodo = msg.get("id"), msg.get("method")
        if mid is None:
            continue  # notificaciones (initialized, cancelled...)
        try:
            if metodo == "initialize":
                version = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
                res = {"protocolVersion": version, "capabilities": {"tools": {}},
                       "serverInfo": {"name": "ojo-del-mundo", "version": "1.0"}}
            elif metodo == "ping":
                res = {}
            elif metodo == "tools/list":
                res = {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r}}
                                 for n, d, p, r in HERRAMIENTAS]}
            elif metodo == "tools/call":
                p = msg.get("params") or {}
                try:
                    res = {"content": llamar(p.get("name"), p.get("arguments")), "isError": False}
                except Exception as e:
                    res = {"content": [{"type": "text", "text": "Error en %s: %s: %s" % (p.get("name"), type(e).__name__, e)}],
                           "isError": True}
            else:
                responder(salida, {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "método desconocido: %s" % metodo}})
                continue
            responder(salida, {"jsonrpc": "2.0", "id": mid, "result": res})
        except Exception as e:
            responder(salida, {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    if "--probar" in sys.argv:
        sys.stdout.reconfigure(encoding="utf-8")
        print(t_estado())
        print(t_bloques(X0, Y0, Z0, 1))
        print(t_corte_lateral(X0, Z0, X0 + 160, Z0)[0])
    else:
        servir()
