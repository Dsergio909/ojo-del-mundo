# -*- coding: utf-8 -*-
"""Vista 3D del Ojo del mundo (4-nov-2026, Sergio: «que pueda ver casi 100 bloques de todos los ángulos», mientras él
juega). Dibuja una zona de hasta 128 x 128 bloques desde cualquier ángulo, sin abrir el juego: lee los .mca y arma cada
bloque con su forma y su textura reales (modelos y texturas del juego y de los mods de cliente/mods).
- Proyección «orto» (sin perspectiva, buena para medir) o «perspectiva» (como una foto desde un dron o un jugador).
- Formas: los «elements» de cada modelo (losas, escaleras, cercas, muebles de los mods), con giros de la variante.
- Texturas: cada píxel sale de la textura real (16x16); hojas y vidrio dejan ver a través; pasto y hojas teñidos.
- Rápido con numpy: una zona de 100 x 100 en unos segundos. Corre con prioridad baja para no trabar el juego.
Solo lee: el .jar de vanilla (versions/1.21.1/1.21.1.jar dentro de la carpeta .minecraft) y los de cliente/mods."""
import ctypes
import glob
import json
import math
import os
import pickle
import struct
import sys
import time
import zipfile
import zlib

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.normpath(os.path.join(AQUI, ".."))


def _carpetas_minecraft():
    """Dónde instala el launcher oficial la carpeta del juego: Windows, Mac y Linux (4-nov: antes solo Windows)."""
    casa = os.path.expanduser("~")
    lista = []
    if os.environ.get("APPDATA"):
        lista.append(os.path.join(os.environ["APPDATA"], ".minecraft"))
    lista.append(os.path.join(casa, "Library", "Application Support", "minecraft"))
    lista.append(os.path.join(casa, ".minecraft"))
    return lista


def buscar_jar():
    """El .jar de Minecraft 1.21.1: el de OJO_JAR si está puesta; si no, el primero que exista en las carpetas normales."""
    if os.environ.get("OJO_JAR"):
        return os.environ["OJO_JAR"]
    rutas = [os.path.join(c, "versions", "1.21.1", "1.21.1.jar") for c in _carpetas_minecraft()]
    return next((r for r in rutas if os.path.isfile(r)), rutas[0])


JAR_VANILLA = buscar_jar()
MODS = os.environ.get("OJO_MODS") or os.path.join(RAIZ, "cliente", "mods")
CACHE = os.environ.get("OJO_CACHE") or os.path.join(AQUI, "ojo_vista3d_cache.pkl")  # índice de los .jar (se rehace solo)
CIELO_ARRIBA, CIELO_ABAJO = np.array([132, 172, 226], np.float32), np.array([206, 224, 244], np.float32)
DIRS6 = {"down": (0, -1, 0), "up": (0, 1, 0), "north": (0, 0, -1), "south": (0, 0, 1), "west": (-1, 0, 0), "east": (1, 0, 0)}
AIRE = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:light", "minecraft:barrier",
        "minecraft:structure_void"}
AIRE_CORTO = {a.split(":")[1] for a in AIRE}  # como los da Mundo.bloque() (sin «minecraft:»)
# tintes aproximados (bioma de llanura/bosque): pasto, follaje, agua
PASTO, FOLLAJE, AGUA = (124, 189, 90), (95, 160, 48), (63, 118, 228)
TINTES = {"spruce_leaves": (97, 153, 97), "birch_leaves": (128, 167, 85), "lily_pad": (32, 128, 48),
          "redstone_wire": (200, 30, 20), "pumpkin_stem": (170, 160, 40), "melon_stem": (170, 160, 40),
          "attached_pumpkin_stem": (180, 150, 40), "attached_melon_stem": (180, 150, 40)}


def prioridad_baja():
    """El Ojo trabaja de fondo: Windows le da la CPU al juego primero (BELOW_NORMAL)."""
    try:
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except Exception:
        pass


# --- PNG sin librerías (texturas de 16x16; también 32x32 o tiras animadas: se toma el primer cuadro) ---

def leer_png(datos):
    if datos[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, plte, trns = 8, [], None, None
    w = h = bd = ct = il = 0
    while pos < len(datos):
        n, tipo = struct.unpack(">I4s", datos[pos:pos + 8])
        cuerpo = datos[pos + 8:pos + 8 + n]
        if tipo == b"IHDR":
            w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", cuerpo)
        elif tipo == b"PLTE":
            plte = np.frombuffer(cuerpo, np.uint8).reshape(-1, 3)
        elif tipo == b"tRNS":
            trns = np.frombuffer(cuerpo, np.uint8)
        elif tipo == b"IDAT":
            idat.append(cuerpo)
        elif tipo == b"IEND":
            break
        pos += 12 + n
    if il or bd not in (1, 2, 4, 8) or ct not in (0, 2, 3, 4, 6) or (bd < 8 and ct != 3 and ct != 0):
        return None
    canales = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    crudo = zlib.decompress(b"".join(idat))
    bpp = max(1, canales * bd // 8)
    fila = (w * canales * bd + 7) // 8
    out = np.zeros((h, fila), np.uint8)
    prev = np.zeros(fila, np.int32)
    p = 0
    for y in range(h):
        f = crudo[p]
        lin = np.frombuffer(crudo[p + 1:p + 1 + fila], np.uint8).astype(np.int32)
        p += 1 + fila
        if f == 0:
            r = lin
        elif f == 2:
            r = (lin + prev) & 255
        else:
            r = np.zeros(fila, np.int32)
            for i in range(fila):
                a = r[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                if f == 1:
                    v = a
                elif f == 3:
                    v = (a + b) >> 1
                else:
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    v = a if pa <= pb and pa <= pc else b if pb <= pc else c
                r[i] = (lin[i] + v) & 255
        out[y] = r
        prev = r
    if bd < 8:  # paleta o gris de 1, 2 o 4 bits
        bits = np.unpackbits(out, axis=1).reshape(h, -1, bd)
        vals = np.zeros(bits.shape[:2], np.int32)
        for k in range(bd):
            vals = (vals << 1) | bits[:, :, k]
        idx = vals[:, :w]
        if ct == 0:
            g = (idx * (255 // ((1 << bd) - 1))).astype(np.uint8)
            return np.dstack([g, g, g, np.full_like(g, 255)])
    else:
        px = out.reshape(h, w, canales)
        if ct == 0:
            g = px[:, :, 0]
            return np.dstack([g, g, g, np.full_like(g, 255)])
        if ct == 4:
            g = px[:, :, 0]
            return np.dstack([g, g, g, px[:, :, 1]])
        if ct == 2:
            return np.dstack([px, np.full((h, w), 255, np.uint8)])
        if ct == 6:
            return px.copy()
        idx = px[:, :, 0].astype(np.int32)
    if plte is None:
        return None
    rgb = plte[np.clip(idx, 0, len(plte) - 1)]
    a = np.full(idx.shape, 255, np.uint8)
    if trns is not None:
        t = np.full(256, 255, np.uint8)
        t[:len(trns)] = trns
        a = t[np.clip(idx, 0, 255)]
    return np.dstack([rgb, a])


def escribir_png(ruta, img):
    """img: (alto, ancho, 3) uint8."""
    h, w, _ = img.shape
    crudo = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def bloque(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(ruta, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + bloque(b"IDAT", zlib.compress(crudo, 6)) + bloque(b"IEND", b""))


# --- Recursos: modelos, estados y texturas del juego y de los mods ---

class Recursos:
    def __init__(self):
        jars = ([JAR_VANILLA] if os.path.isfile(JAR_VANILLA) else []) + sorted(glob.glob(os.path.join(MODS, "*.jar")))
        firma = [("v3-seres", 0)] + [(j, os.path.getmtime(j)) for j in jars]
        self.indice = None
        if os.path.isfile(CACHE):
            try:
                c = pickle.load(open(CACHE, "rb"))
                if c.get("firma") == firma:
                    self.indice = c["indice"]
            except Exception:
                pass
        if self.indice is None:
            self.indice = {}
            for j in jars:  # el primero que tenga el archivo gana: vanilla primero, después los mods
                try:
                    for n in zipfile.ZipFile(j).namelist():
                        if n.startswith("assets/") and (n.endswith(".json") or n.endswith(".png")) and \
                                ("/blockstates/" in n or "/models/" in n or "/textures/block" in n or "/textures/entity" in n
                                 or "/textures/colormap/" in n or "/textures/painting/" in n or "/textures/item/" in n):
                            self.indice.setdefault(n, j)
                        elif n.startswith("data/") and ("/worldgen/biome/" in n or "/painting_variant/" in n) and n.endswith(".json") \
                                and "/tags/" not in n:
                            self.indice.setdefault(n, j)
                except Exception:
                    continue
            try:
                pickle.dump({"firma": firma, "indice": self.indice}, open(CACHE, "wb"))
            except Exception:
                pass
        self.zips, self.modelos, self.texturas, self.tex_id, self.geo = {}, {}, [], {}, {}
        self.textura("minecraft:block/missing__")  # índice 0: textura vacía (magenta)

    def leer(self, ruta):
        j = self.indice.get(ruta)
        if not j:
            return None
        if j not in self.zips:
            self.zips[j] = zipfile.ZipFile(j)
        try:
            return self.zips[j].read(ruta)
        except Exception:
            return None

    def json(self, ruta):
        d = self.leer(ruta)
        try:
            return json.loads(d.decode("utf-8-sig")) if d else None
        except Exception:
            return None

    @staticmethod
    def ns(ident, carpeta):
        ident = ident.split("#")[0]
        n, _, p = ident.rpartition(":")
        return "assets/%s/%s/%s" % (n or "minecraft", carpeta, p)

    def textura(self, ident):
        """Índice de la textura en el atlas (16x16 RGBA). 0 = falta."""
        if ident in self.tex_id:
            return self.tex_id[ident]
        img = None
        d = self.leer(self.ns(ident, "textures") + ".png") if not ident.endswith("missing__") else None
        if d:
            try:
                img = leer_png(d)
            except Exception:
                img = None
        if img is not None:
            h, w = img.shape[:2]
            if h > w:  # textura animada: el primer cuadro
                img = img[:w]
            if w != 16:  # 32x32 o 64x64: se reduce tomando un píxel de cada tanto (o se agranda si es chica)
                ys = (np.arange(16) * img.shape[0] // 16)
                xs = (np.arange(16) * img.shape[1] // 16)
                img = img[ys][:, xs]
        else:
            img = np.zeros((16, 16, 4), np.uint8)
            img[:, :] = (200, 0, 200, 255)
            if ident.endswith("missing__"):
                img[:, :, 3] = 255
        self.tex_id[ident] = len(self.texturas)
        self.texturas.append(img.astype(np.uint8))
        return self.tex_id[ident]

    def textura_trozo(self, ident, i, j, w, h):
        """Un pedazo de 1x1 bloque (fila j, columna i) de una textura de w x h bloques (los cuadros)."""
        clave = "%s#%d,%d" % (ident, i, j)
        if clave in self.tex_id:
            return self.tex_id[clave]
        d = self.leer(self.ns(ident, "textures") + ".png")
        img = leer_png(d) if d else None
        if img is None:
            return self.textura(ident)
        ph, pw = img.shape[0] // max(1, h), img.shape[1] // max(1, w)
        t = img[j * ph:(j + 1) * ph, i * pw:(i + 1) * pw]
        t = t[(np.arange(16) * t.shape[0] // 16)][:, (np.arange(16) * t.shape[1] // 16)]
        self.tex_id[clave] = len(self.texturas)
        self.texturas.append(t.astype(np.uint8))
        return self.tex_id[clave]

    def imagen(self, ident):
        """Textura completa (RGBA, su tamaño real), o None."""
        if not hasattr(self, "_imgs"):
            self._imgs = {}
        if ident not in self._imgs:
            d = self.leer(self.ns(ident, "textures") + ".png")
            self._imgs[ident] = leer_png(d) if d else None
        return self._imgs[ident]

    def textura_de(self, clave, arr, transponer=False):
        """Registra un pedazo de imagen como textura de 16x16 (se agranda o achica)."""
        if clave in self.tex_id:
            return self.tex_id[clave]
        if transponer:
            arr = arr.transpose(1, 0, 2)
        arr = arr[(np.arange(16) * arr.shape[0] // 16)][:, (np.arange(16) * arr.shape[1] // 16)]
        self.tex_id[clave] = len(self.texturas)
        self.texturas.append(arr.astype(np.uint8))
        return self.tex_id[clave]

    def recorte(self, ident, x0, y0, x1, y1, transponer=False):
        """Clave de textura de un rectángulo (en píxeles de una textura de 64x64) de una textura de entidad."""
        clave = "ojo:recorte/%s/%d_%d_%d_%d%s" % (ident, x0, y0, x1, y1, "_t" if transponer else "")
        if clave not in self.tex_id:
            img = self.imagen(ident)
            if img is None:
                return None
            k = img.shape[1] / 64.0
            self.textura_de(clave, img[int(y0 * k):int(y1 * k), int(x0 * k):int(x1 * k)], transponer)
        return clave

    def blanca(self):
        if "ojo:blanco" not in self.tex_id:
            self.tex_id["ojo:blanco"] = len(self.texturas)
            self.texturas.append(np.full((16, 16, 4), 255, np.uint8))
        return self.tex_id["ojo:blanco"]

    def modelo(self, ident):
        """Modelo con sus padres resueltos: {"textures": {...}, "elements": [...] o None, "padres": [...]}."""
        if ident in self.modelos:
            return self.modelos[ident]
        tex, elems, padres, cur = {}, None, [], ident
        for _ in range(12):
            if not cur or cur.startswith("builtin/"):
                padres.append(cur or "")
                break
            d = self.json(self.ns(cur, "models") + ".json")
            padres.append(cur)
            if not d:
                break
            for k, v in (d.get("textures") or {}).items():
                tex.setdefault(k, v)
            if elems is None and d.get("elements"):
                elems = d["elements"]
            cur = d.get("parent")
        self.modelos[ident] = {"textures": tex, "elements": elems, "padres": padres}
        return self.modelos[ident]


def _cumple(cond, props):
    if not cond:
        return True
    if "OR" in cond:
        return any(_cumple(c, props) for c in cond["OR"])
    if "AND" in cond:
        return all(_cumple(c, props) for c in cond["AND"])
    return all(str(props.get(k, "")) in str(v).split("|") for k, v in cond.items())


def _girar(p, eje, ang, centro):
    """Gira puntos (N, 3) en 0..16 alrededor de centro, ángulo en grados (sentido de Minecraft)."""
    a = math.radians(ang)
    c, s = math.cos(a), math.sin(a)
    q = p - centro
    x, y, z = q[:, 0].copy(), q[:, 1].copy(), q[:, 2].copy()
    if eje == "y":
        q[:, 0], q[:, 2] = x * c + z * s, -x * s + z * c
    elif eje == "x":
        q[:, 1], q[:, 2] = y * c - z * s, y * s + z * c
    else:
        q[:, 0], q[:, 1] = x * c - y * s, x * s + y * c
    return q + centro


# vértices de cada cara en el orden de Minecraft (FaceInfo): uv (u0,v0), (u0,v1), (u1,v1), (u1,v0)
def _vertices(cara, a, b):
    x0, y0, z0 = a
    x1, y1, z1 = b
    return {"down": [(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)],
            "up": [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)],
            "north": [(x1, y1, z0), (x1, y0, z0), (x0, y0, z0), (x0, y1, z0)],
            "south": [(x0, y1, z1), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1)],
            "west": [(x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)],
            "east": [(x1, y1, z1), (x1, y0, z1), (x1, y0, z0), (x1, y1, z0)]}[cara]


def _uv_defecto(cara, a, b):
    x0, y0, z0 = a
    x1, y1, z1 = b
    return {"down": (x0, 16 - z1, x1, 16 - z0), "up": (x0, z0, x1, z1), "north": (16 - x1, 16 - y1, 16 - x0, 16 - y0),
            "south": (x0, 16 - y1, x1, 16 - y0), "west": (z0, 16 - y1, z1, 16 - y0), "east": (16 - z1, 16 - y1, 16 - z0, 16 - y0)}[cara]


def _tinte(nombre):
    """(tipo, color por defecto): tipo «pasto», «follaje» o «agua» se cambia por el color del bioma; «fijo» no."""
    c = nombre.split(":")[-1]
    if c in TINTES:
        return "fijo", TINTES[c]
    if c in ("water", "bubble_column") or "water" in c:
        return "agua", AGUA
    if "leaves" in c or c in ("vine", "jungle_vine") or "vine" in c:
        return "follaje", FOLLAJE
    return "pasto", PASTO  # pasto, helechos, caña, bloque de pasto


def colores_bioma(rec, nombre):
    """{pasto, follaje, agua} de un bioma, como los calcula el juego (mapa de colores según temperatura y lluvia)."""
    if not hasattr(rec, "_biomas"):
        rec._biomas = {}
        rec._mapas = {}
        for k in ("grass", "foliage"):
            d = rec.leer("assets/minecraft/textures/colormap/%s.png" % k)
            rec._mapas[k] = leer_png(d) if d else None
    if nombre in rec._biomas:
        return rec._biomas[nombre]
    ns, _, b = nombre.rpartition(":")
    d = rec.json("data/%s/worldgen/biome/%s.json" % (ns or "minecraft", b)) or {}
    ef = d.get("effects") or {}
    t = max(0.0, min(1.0, float(d.get("temperature", 0.8))))
    h = max(0.0, min(1.0, float(d.get("downfall", 0.4)))) * t

    def mapa(k, defecto):
        mp = rec._mapas.get(k)
        if mp is None:
            return defecto
        x, y = int((1 - t) * 255), int((1 - h) * 255)
        return tuple(int(v) for v in mp[min(y, mp.shape[0] - 1), min(x, mp.shape[1] - 1), :3])

    def rgb(n):
        return ((n >> 16) & 255, (n >> 8) & 255, n & 255)
    pasto = rgb(ef["grass_color"]) if "grass_color" in ef else mapa("grass", PASTO)
    if ef.get("grass_color_modifier") == "dark_forest":
        n = (pasto[0] << 16) | (pasto[1] << 8) | pasto[2]
        pasto = rgb(((n & 0xFEFEFE) + 0x28340A) >> 1)
    elif ef.get("grass_color_modifier") == "swamp":
        pasto = rgb(0x6A7039)
    follaje = rgb(ef["foliage_color"]) if "foliage_color" in ef else mapa("foliage", FOLLAJE)
    agua = rgb(ef["water_color"]) if "water_color" in ef else AGUA
    rec._biomas[nombre] = {"pasto": pasto, "follaje": follaje, "agua": agua}
    return rec._biomas[nombre]


def volumen_bioma(m, x1, x2, y1, y2, z1, z2):
    """Bioma de cada bloque (X, Y, Z) como índice en la lista de nombres (los chunks guardan uno cada 4x4x4)."""
    X, Y, Z = x2 - x1 + 1, y2 - y1 + 1, z2 - z1 + 1
    vb = np.zeros((X, Y, Z), np.int16)
    nombres, ids = ["minecraft:plains"], {"minecraft:plains": 0}
    xs, ys, zs = np.arange(x1, x2 + 1), np.arange(y1, y2 + 1), np.arange(z1, z2 + 1)
    for cx in range(x1 >> 4, (x2 >> 4) + 1):
        for cz in range(z1 >> 4, (z2 >> 4) + 1):
            ch = m.chunk(cx, cz)
            if ch is None:
                continue
            ax, bx, az, bz = max(x1, cx * 16), min(x2, cx * 16 + 15), max(z1, cz * 16), min(z2, cz * 16 + 15)
            for sy in range(y1 >> 4, (y2 >> 4) + 1):
                bi = (ch[2].get(sy) or {}).get("biomes") or {}
                pal = bi.get("palette") or []
                if not pal:
                    continue
                look = np.array([ids.setdefault(p, len(ids)) for p in pal], np.int16)
                for p in pal:
                    if p not in nombres:
                        nombres.append(p)
                data = bi.get("data")
                if len(pal) == 1 or not data:
                    celdas = np.zeros(64, np.int64)
                else:
                    bits = max(1, (len(pal) - 1).bit_length())
                    por = 64 // bits
                    arr = np.array([int(w) & ((1 << 64) - 1) for w in data], dtype=np.uint64)
                    celdas = np.empty(len(arr) * por, np.int64)
                    for k in range(por):
                        celdas[k::por] = ((arr >> np.uint64(k * bits)) & np.uint64((1 << bits) - 1)).astype(np.int64)
                    celdas = np.clip(celdas[:64], 0, len(pal) - 1)
                cub = look[celdas].reshape(4, 4, 4)  # (y, z, x) de 4 en 4
                ay, by = max(y1, sy << 4), min(y2, (sy << 4) + 15)
                xx = ((np.arange(ax, bx + 1) & 15) >> 2)
                yy = ((np.arange(ay, by + 1) & 15) >> 2)
                zz = ((np.arange(az, bz + 1) & 15) >> 2)
                vb[ax - x1:bx - x1 + 1, ay - y1:by - y1 + 1, az - z1:bz - z1 + 1] = \
                    cub[yy][:, zz][:, :, xx].transpose(2, 0, 1)
    return vb, nombres


# cajas de reemplazo para bloques que el juego dibuja aparte (cofres, camas, carteles, calaveras...): (desde, hasta, textura)
ESPECIALES = [
    ("_bed", (0, 0, 0), (16, 9, 16), "COLOR_wool"),
    ("chest", (1, 0, 1), (15, 14, 15), "oak_planks"),
    ("shulker_box", (0, 0, 0), (16, 16, 16), "COLOR_wool"),
    ("wall_banner", (0, 0, 14), (16, 16, 16), "COLOR_wool"),
    ("banner", (7, 0, 7), (9, 16, 9), "COLOR_wool"),
    ("wall_hanging_sign", (1, 6, 7), (15, 14, 9), "PLANKS"),
    ("hanging_sign", (1, 0, 7), (15, 10, 9), "PLANKS"),
    ("wall_sign", (0, 4, 14), (16, 12, 16), "PLANKS"),
    ("_sign", (7, 0, 7), (9, 16, 9), "PLANKS"),
    ("wall_skull", (4, 4, 8), (12, 12, 16), "bone_block_side"),
    ("wall_head", (4, 4, 8), (12, 12, 16), "HEAD"),
    ("skull", (4, 0, 4), (12, 8, 12), "bone_block_side"),
    ("_head", (4, 0, 4), (12, 8, 12), "HEAD"),
    ("decorated_pot", (1, 0, 1), (15, 16, 15), "terracotta"),
    ("bell", (4, 4, 4), (12, 13, 12), "gold_block"),
    ("conduit", (5, 5, 5), (11, 11, 11), "prismarine"),
]
COLORES = ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan", "purple", "blue",
           "brown", "green", "red", "black")


class Geometria:
    """Caras (quads) de cada estado de bloque, en coordenadas 0..1 del bloque, con su textura, uv, tinte y si tapa."""

    def __init__(self, rec):
        self.r = rec
        self.cache = {}

    def caras(self, nombre, props):
        clave = (nombre, tuple(sorted(props.items())))
        if clave in self.cache:
            return self.cache[clave]
        try:
            res = self._armar(nombre, props)
        except Exception:
            res = self._caja_simple(nombre, (0, 0, 0), (16, 16, 16), None)
        self.cache[clave] = res
        return res

    def _tex_bloque(self, base):
        for t in ("block/" + base, "block/" + base + "_side", "block/" + base + "_top"):
            if self.r.leer(self.r.ns("minecraft:" + t, "textures") + ".png"):
                return "minecraft:" + t
        return None

    def _caja_simple(self, nombre, a, b, tex, rot_y=0):
        ns, _, c = nombre.rpartition(":")
        if tex is None:
            tex = self._tex_bloque(c) or "minecraft:block/stone"
        elems = [{"from": list(a), "to": list(b), "faces": {k: {"texture": "#t"} for k in DIRS6}}]
        return self._de_elementos(nombre, elems, {"t": tex}, 0, rot_y, especial=True)

    def _entidad_real(self, nombre, props):
        """Cofres y camas con las cajas y texturas de entidad del juego. None si no aplica."""
        c = nombre.split(":")[-1]
        rot = {"north": 0, "east": 90, "south": 180, "west": 270}.get(props.get("facing", "north"), 0)
        if c in ("chest", "trapped_chest", "ender_chest", "lootr_chest", "lootr_trapped_chest"):
            ident = "minecraft:entity/chest/%s" % ("ender" if "ender" in c else "trapped" if "trapped" in c else "normal")
            r = self.r
            t = {k: r.recorte(ident, *v) for k, v in {
                "lu": (14, 0, 28, 14), "lf": (14, 14, 28, 19), "lw": (0, 14, 14, 19), "le": (28, 14, 42, 19), "lb": (42, 14, 56, 19),
                "bf": (14, 33, 28, 43), "bw": (0, 33, 14, 43), "be": (28, 33, 42, 43), "bb": (42, 33, 56, 43), "bd": (28, 19, 42, 33),
                "k": (1, 1, 3, 5)}.items()}
            if None in t.values():
                return None
            cara = lambda k: {"texture": "#" + k, "uv": [0, 0, 16, 16]}  # noqa: E731
            elems = [{"from": [1, 0, 1], "to": [15, 10, 15], "faces": {"north": cara("bf"), "south": cara("bb"), "west": cara("bw"),
                                                                      "east": cara("be"), "down": cara("bd")}},
                     {"from": [1, 9, 1], "to": [15, 14, 15], "faces": {"north": cara("lf"), "south": cara("lb"), "west": cara("lw"),
                                                                      "east": cara("le"), "up": cara("lu")}},
                     {"from": [7, 7, 0], "to": [9, 11, 1], "faces": {k: cara("k") for k in ("north", "west", "east", "up", "down")}}]
            return self._de_elementos(nombre, elems, t, 0, rot, especial=True)
        if c.endswith("_bed") and nombre.startswith("minecraft:"):
            color = c[:-4]
            ident = "minecraft:entity/bed/%s" % color
            r = self.r
            cab = props.get("part", "head") == "head"
            arriba = r.recorte(ident, 6, 6, 22, 22) if cab else r.recorte(ident, 6, 28, 22, 44)
            lado = r.recorte(ident, 22, 6, 28, 22, True) if cab else r.recorte(ident, 22, 28, 28, 44, True)
            pata = r.recorte(ident, 50, 6, 53, 9)
            if None in (arriba, lado, pata):
                return None
            t = {"t": arriba, "s": lado, "l": pata}
            cara = lambda k: {"texture": "#" + k, "uv": [0, 0, 16, 16]}  # noqa: E731
            elems = [{"from": [0, 3, 0], "to": [16, 9, 16], "faces": {"up": cara("t"), "north": cara("s"), "south": cara("s"),
                                                                     "west": cara("s"), "east": cara("s"), "down": cara("s")}}]
            zs = (0, 3) if cab else (13, 16)  # las patas van en la punta de cada mitad
            for x0 in (0, 13):
                elems.append({"from": [x0, 0, zs[0]], "to": [x0 + 3, 3, zs[1]], "faces": {k: cara("l") for k in DIRS6}})
            return self._de_elementos(nombre, elems, t, 0, rot, especial=True)
        if c.endswith("banner"):
            return []  # se dibuja con su patrón en extras_estandartes
        return None

    def _especial(self, nombre, props):
        real = self._entidad_real(nombre, props)
        if real is not None:
            return real
        c = nombre.split(":")[-1]
        for clave, a, b, tex in ESPECIALES:
            if c.endswith(clave) or (clave in ("chest",) and "chest" in c and ":" not in nombre.replace("minecraft:", "")):
                color = next((k for k in sorted(COLORES, key=len, reverse=True) if c.startswith(k + "_")), "red")
                madera = c.split("_wall")[0].split("_hanging")[0].replace("_sign", "")
                t = {"COLOR_wool": "minecraft:block/%s_wool" % color,
                     "PLANKS": "minecraft:block/%s_planks" % (madera if madera else "oak"),
                     "HEAD": "minecraft:block/%s" % ("green_concrete" if "creeper" in c else "moss_block" if "zombie" in c else
                                                     "coal_block" if "wither" in c else "dirt" if "piglin" in c else "bone_block_side")
                     }.get(tex, "minecraft:block/" + tex)
                if "ender_chest" in c:
                    t = "minecraft:block/obsidian"
                elif "trapped_chest" in c or c == "chest":
                    t = "minecraft:block/oak_planks"
                rot = {"north": 0, "east": 90, "south": 180, "west": 270}.get(props.get("facing", "north"), 0)
                if "_bed" in c:  # la cama ocupa la mitad del lado del pie y la cabecera: caja entera de 9/16
                    rot = 0
                return self._caja_simple(nombre, a, b, t, rot)
        return None

    def _armar(self, nombre, props):
        n, _, c = nombre.rpartition(":")
        n = n or "minecraft"
        if c in ("water", "lava", "bubble_column"):
            return self._fluido(c, props)
        bs = self.r.json("assets/%s/blockstates/%s.json" % (n, c))
        partes = []
        if bs and "variants" in bs:
            mejor = None
            for k, v in bs["variants"].items():
                pares = [p.split("=") for p in k.split(",") if "=" in p]
                if all(str(props.get(a, "")) == b for a, b in pares):
                    if mejor is None or len(pares) > mejor[0]:
                        mejor = (len(pares), v)
            if mejor:
                partes.append(mejor[1][0] if isinstance(mejor[1], list) else mejor[1])
        elif bs and "multipart" in bs:
            for p in bs["multipart"]:
                if _cumple(p.get("when"), props):
                    a = p.get("apply")
                    partes.append(a[0] if isinstance(a, list) else a)
        quads = []
        for p in partes:
            if not isinstance(p, dict) or "model" not in p:
                continue
            md = self.r.modelo(p["model"])
            if not md["elements"]:
                continue
            quads += self._de_elementos(nombre, md["elements"], md["textures"], p.get("x", 0), p.get("y", 0))
        if not quads:
            esp = self._especial(nombre, props)
            if esp is not None:
                return esp
            md = self.r.modelo(partes[0]["model"]) if partes and isinstance(partes[0], dict) and "model" in partes[0] else None
            tex = None
            if md:
                t = md["textures"]
                tex = t.get("particle") or next(iter(t.values()), None)
                for _ in range(8):
                    if tex and tex.startswith("#"):
                        tex = t.get(tex[1:])
                if tex and tex.startswith("#"):
                    tex = None
            if tex is None and not bs:
                return self._caja_simple(nombre, (0, 0, 0), (16, 16, 16), None)
            if tex is None:
                return []
            if ":" not in tex:
                tex = "minecraft:" + tex
            return self._caja_simple(nombre, (1, 0, 1), (15, 15, 15), tex)
        return quads

    def _fluido(self, c, props):
        nivel = int(props.get("level", 0) or 0)
        alto = 14 if nivel == 0 else max(2, 14 - 2 * min(nivel, 7))
        tex = "minecraft:block/water_still" if c != "lava" else "minecraft:block/lava_still"
        elems = [{"from": [0, 0, 0], "to": [16, alto, 16],
                  "faces": {k: {"texture": "#t", "cullface": k, "tintindex": 0 if c != "lava" else None} for k in DIRS6}}]
        q = self._de_elementos("minecraft:" + c, elems, {"t": tex}, 0, 0)
        for x in q:
            x["fluido"] = True
        return q

    def _de_elementos(self, nombre, elems, texturas, rx, ry, especial=False):
        out = []
        for el in elems:
            a, b = el.get("from", [0, 0, 0]), el.get("to", [16, 16, 16])
            rot = el.get("rotation")
            for cara, f in (el.get("faces") or {}).items():
                if cara not in DIRS6:
                    continue
                t = f.get("texture", "")
                for _ in range(8):
                    if t.startswith("#"):
                        t = texturas.get(t[1:], "")
                    else:
                        break
                if not t or t.startswith("#"):
                    continue
                if ":" not in t:
                    t = "minecraft:" + t
                v = np.array(_vertices(cara, a, b), np.float64)
                u = f.get("uv") or _uv_defecto(cara, a, b)
                uvs = [(u[0], u[1]), (u[0], u[3]), (u[2], u[3]), (u[2], u[1])]
                r = int(f.get("rotation", 0) or 0) // 90 % 4
                uvs = uvs[r:] + uvs[:r]
                if rot:
                    v = _girar(v, rot.get("axis", "y"), float(rot.get("angle", 0)),
                               np.array(rot.get("origin", [8, 8, 8]), np.float64))
                cull = f.get("cullface")
                if rx:
                    v = _girar(v, "x", -rx, np.array([8.0, 8.0, 8.0]))
                    cull = _girar_dir(cull, "x", rx)
                if ry:
                    v = _girar(v, "y", -ry, np.array([8.0, 8.0, 8.0]))
                    cull = _girar_dir(cull, "y", ry)
                n = np.cross(v[1] - v[0], v[3] - v[0])
                ln = np.linalg.norm(n)
                if ln < 1e-9:
                    continue
                n = n / ln
                tipo_t, tinte = _tinte(nombre) if f.get("tintindex") is not None else (None, None)
                out.append({"v": v / 16.0, "uv": np.array(uvs, np.float64), "tex": self.r.textura(t), "cull": cull, "n": n,
                            "tinte": tinte, "tinte_tipo": tipo_t, "lleno": (not rot) and tuple(a) == (0, 0, 0) and tuple(b) == (16, 16, 16) and not especial})
        return out


def _girar_dir(d, eje, ang):
    if not d:
        return d
    v = np.array([DIRS6[d]], np.float64) * 8 + 8
    v = _girar(v, eje, -ang, np.array([8.0, 8.0, 8.0]))[0] - 8
    v = np.round(v / 8).astype(int)
    for k, x in DIRS6.items():
        if tuple(v) == x:
            return k
    return d


_REC = [None]


def recursos():
    if _REC[0] is None:
        prioridad_baja()
        r = Recursos()
        _REC[0] = (r, Geometria(r))
    return _REC[0]


# --- Volumen de bloques desde los .mca ---

def volumen(m, decodificar, x1, x2, y1, y2, z1, z2):
    """ids (X, Y, Z) de estados de bloque + lista de estados [(nombre, props)]. 0 = aire; -1 = chunk sin generar."""
    X, Y, Z = x2 - x1 + 1, y2 - y1 + 1, z2 - z1 + 1
    vol = np.zeros((X, Y, Z), np.int32)
    estados, ids = [("minecraft:air", {})], {("minecraft:air", ()): 0}
    falta = 0
    for cx in range(x1 >> 4, (x2 >> 4) + 1):
        for cz in range(z1 >> 4, (z2 >> 4) + 1):
            ox, oz = cx * 16, cz * 16
            ax, bx = max(x1, ox), min(x2, ox + 15)
            az, bz = max(z1, oz), min(z2, oz + 15)
            ch = m.chunk(cx, cz)
            if ch is None:
                vol[ax - x1:bx - x1 + 1, :, az - z1:bz - z1 + 1] = -1
                falta += 1
                continue
            for sy, sec in ch[2].items():
                if sy is None or (sy << 4) + 15 < y1 or (sy << 4) > y2:
                    continue
                bs = sec.get("block_states") or {}
                pal = bs.get("palette")
                if not pal:
                    continue
                look = np.zeros(len(pal), np.int32)
                for i, p in enumerate(pal):
                    nom = p.get("Name", "minecraft:air")
                    if nom in AIRE:
                        continue
                    pr = p.get("Properties") or {}
                    k = (nom, tuple(sorted(pr.items())))
                    if k not in ids:
                        ids[k] = len(estados)
                        estados.append((nom, dict(pr)))
                    look[i] = ids[k]
                idx = decodificar(len(pal), bs.get("data"))
                if idx is None:
                    bloque = np.full((16, 16, 16), look[0], np.int32)
                else:
                    bloque = look[np.asarray(idx, np.int64)].reshape(16, 16, 16)  # (y, z, x)
                bloque = bloque.transpose(2, 0, 1)  # (x, y, z)
                ay, by = max(y1, sy << 4), min(y2, (sy << 4) + 15)
                vol[ax - x1:bx - x1 + 1, ay - y1:by - y1 + 1, az - z1:bz - z1 + 1] = \
                    bloque[ax - ox:bx - ox + 1, ay - (sy << 4):by - (sy << 4) + 1, az - oz:bz - oz + 1]
    return vol, estados, falta


CERCA = 0.05  # lo más cerca que se dibuja (como el plano cercano de Minecraft); antes 0,3 y se perdía lo pegado a la cámara
# luz, ajustable con fotos reales (calibrar contra el juego)
# 4-nov, calibrado con 7 fotos reales del cliente Prueba (con los shaders de Sergio y sus amigos: Complementary +
# Euphoria): de día bajo tierra el juego se ve más claro que la curva pura (0,75 en vez del 0,5 del menú); de noche, la luna
# cuenta 0,08 y la curva es recta. Con eso el brillo medio de noche calza 1,00 con la foto y el de la cripta 0,91.
GAMMA = 0.75       # brillo (0 = Moody, 0,5 = Moderado del menú, 1 = Brillante); 0,75 calza con los shaders
LUNA = 0.08        # de noche, cuánto cuenta la luz del cielo (la luna)
NOCHE_MIN, NOCHE_EXP = 0.07, 1.0


def brillo_mc(nivel):
    """Brillo de una cara según su luz (0-15), con la curva de Minecraft y el brillo del juego (GAMMA)."""
    b = np.asarray(nivel, np.float32) / 15.0
    f_ = b / (4.0 - 3.0 * b)
    f_ = f_ * (1 - GAMMA) + (1.0 - (1.0 - f_) ** 4) * GAMMA
    return 0.04 + 0.96 * f_


def _medio_byte(b):
    a = np.frombuffer(bytes(b), np.uint8)
    out = np.empty(4096, np.uint8)
    out[0::2], out[1::2] = a & 15, a >> 4
    return out.reshape(16, 16, 16).transpose(2, 0, 1)  # (x, y, z)


def volumen_luz(m, x1, x2, y1, y2, z1, z2, cielo=0.15, minimo=0.0):
    """Luz (X, Y, Z) de noche: la de los bloques (antorchas, faroles) o la de la luna (15 % del cielo), de 0 a 15."""
    X, Y, Z = x2 - x1 + 1, y2 - y1 + 1, z2 - z1 + 1
    luz = np.zeros((X, Y, Z, 2), np.float32)  # [..., 0] = la luz que cuenta; [..., 1] = solo la de bloques (antorchas, faroles)
    for cx in range(x1 >> 4, (x2 >> 4) + 1):
        for cz in range(z1 >> 4, (z2 >> 4) + 1):
            ox, oz = cx * 16, cz * 16
            ax, bx, az, bz = max(x1, ox), min(x2, ox + 15), max(z1, oz), min(z2, oz + 15)
            ch = m.chunk(cx, cz)
            if ch is None:
                continue
            tope = max([sy for sy, sec in ch[2].items() if sy is not None and (sec.get("block_states") or {}).get("palette")
                        and len(sec["block_states"]["palette"]) > 1] or [0])
            for sy in range((y1 >> 4), (y2 >> 4) + 1):
                sec = ch[2].get(sy) or {}
                bl = _medio_byte(sec["BlockLight"]).astype(np.float32) if sec.get("BlockLight") else np.zeros((16, 16, 16), np.float32)
                if sec.get("SkyLight"):
                    sk = _medio_byte(sec["SkyLight"]).astype(np.float32)
                else:
                    sk = np.full((16, 16, 16), 15.0 if sy > tope else 0.0, np.float32)
                l = np.maximum(np.maximum(bl, sk * cielo), minimo)  # de día el cielo cuenta entero; de noche, la luna (15 %)
                ay, by = max(y1, sy << 4), min(y2, (sy << 4) + 15)
                luz[ax - x1:bx - x1 + 1, ay - y1:by - y1 + 1, az - z1:bz - z1 + 1, 0] = \
                    l[ax - ox:bx - ox + 1, ay - (sy << 4):by - (sy << 4) + 1, az - oz:bz - oz + 1]
                luz[ax - x1:bx - x1 + 1, ay - y1:by - y1 + 1, az - z1:bz - z1 + 1, 1] = \
                    bl[ax - ox:bx - ox + 1, ay - (sy << 4):by - (sy << 4) + 1, az - oz:bz - oz + 1]
    return luz


# --- Cámara y dibujo ---

def camara(giro, inclinacion):
    """giro = yaw de Minecraft (0 mira al sur, 90 al oeste, 180 al norte, -90 al este); inclinación = pitch (90 = desde arriba)."""
    y, p = math.radians(giro), math.radians(max(-89.0, min(89.9, inclinacion)))
    f = np.array([-math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p)])
    r = np.cross(f, [0.0, 1.0, 0.0])
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return f, r, u


DESDE = {"sur": 180, "suroeste": -135, "oeste": -90, "noroeste": -45, "norte": 0, "noreste": 45, "este": 90, "sureste": 135}
# desde dónde mira la cámara → hacia dónde mira (yaw): «desde el sur» mira al norte (yaw 180)


COLOR_SER = {"aldeano": (40, 110, 255), "monstruo": (235, 30, 30), "animal": (250, 210, 40), "armadura": (190, 190, 200),
             "vehiculo": (150, 90, 40), "otro": (230, 120, 230), "marco": (170, 90, 255), "cuadro": (255, 150, 50)}


def dibujar(rec, geo, vol, estados, origen, f, r, u, ancho, alto, persp=None, sin_hojas=False, marcas=(), luz=None, seres=(), nether=False,
            resaltar=None, etiquetas=(), regla=None, noche_=False, biomas=None, extras=()):
    """Devuelve la imagen (alto, ancho, 3) uint8 y la función que proyecta un punto del mundo a píxeles.
    persp = (posición de la cámara, foco en píxeles) o None para ortográfica (se ajusta sola a la zona)."""
    X, Y, Z = vol.shape
    n_est = len(estados)
    quads = [geo.caras(*e) if i else [] for i, e in enumerate(estados)]
    if sin_hojas:  # para ver casas y caminos debajo de los árboles
        for i, (nom, _) in enumerate(estados):
            if "leaves" in nom:
                quads[i] = []
    texs = np.stack(rec.texturas)  # (T, 16, 16, 4)
    opaco = np.zeros(n_est + 1, bool)  # índice n_est = sin generar (cuenta como no opaco)
    for i, qs in enumerate(quads):
        tapas = {q["cull"] for q in qs if q["lleno"] and q["cull"] and texs[q["tex"], :, :, 3].min() >= 250}
        if len(tapas) == 6 and all(q["lleno"] or texs[q["tex"], :, :, 3].min() < 250 for q in qs):
            opaco[i] = True
    v2 = np.where(vol < 0, n_est, vol)
    O = opaco[v2]
    # celdas con algo que se pueda ver: no aire y con algún vecino no opaco (o en el borde de la zona)
    pad = np.pad(O, 1, constant_values=False)
    rodeado = pad[2:, 1:-1, 1:-1] & pad[:-2, 1:-1, 1:-1] & pad[1:-1, 2:, 1:-1] & pad[1:-1, :-2, 1:-1] & pad[1:-1, 1:-1, 2:] & pad[1:-1, 1:-1, :-2]
    visibles = (v2 > 0) & (v2 < n_est) & ~(O & rodeado)
    cel = np.argwhere(visibles)
    ids = v2[cel[:, 0], cel[:, 1], cel[:, 2]]
    orden = np.argsort(ids, kind="stable")
    cel, ids = cel[orden], ids[orden]
    cortes = np.flatnonzero(np.diff(ids)) + 1
    grupos_ = np.split(np.arange(len(ids)), cortes)
    V, UV, TX, TI, SH, AO, RC, RA, AG, CA = [], [], [], [], [], [], [], [], [], []
    resaltar = resaltar or {}
    hacia_cam = -f
    FACT_AO = np.array([1.0, 0.8, 0.66, 0.52], np.float32)

    def opaco_en(p):  # (k, 3) celdas -> opaco (fuera de la zona: no)
        fuera = (p[:, 0] < 0) | (p[:, 0] >= X) | (p[:, 1] < 0) | (p[:, 1] >= Y) | (p[:, 2] < 0) | (p[:, 2] >= Z)
        pc = np.clip(p, 0, [X - 1, Y - 1, Z - 1])
        return opaco[v2[pc[:, 0], pc[:, 1], pc[:, 2]]] & ~fuera
    for g in grupos_:
        if not len(g):
            continue
        sid = ids[g[0]]
        cs = cel[g]
        for q in quads[sid]:
            n = q["n"]
            if persp is None and np.dot(n, hacia_cam) <= 1e-6:
                continue
            m_ = np.ones(len(cs), bool)
            if q["cull"]:
                d = np.array(DIRS6[q["cull"]])
                nb = cs + d
                fuera = (nb[:, 0] < 0) | (nb[:, 0] >= X) | (nb[:, 1] < 0) | (nb[:, 1] >= Y) | (nb[:, 2] < 0) | (nb[:, 2] >= Z)
                nbc = np.clip(nb, 0, [X - 1, Y - 1, Z - 1])
                vec = v2[nbc[:, 0], nbc[:, 1], nbc[:, 2]]
                tapa = opaco[vec] & ~fuera
                if q.get("fluido"):  # agua junto a agua: sin cara
                    tapa |= np.isin(vec, [i for i, e in enumerate(estados) if e[0] == estados[sid][0]]) & ~fuera
                m_ &= ~tapa
            if persp is not None:
                centro = cs + origen + q["v"].mean(axis=0)
                m_ &= ((centro - persp[0]) @ n) < 0
            if not m_.any():
                continue
            base = cs[m_] + origen
            V.append(base[:, None, :] + q["v"][None, :, :])
            k = m_.sum()
            UV.append(np.broadcast_to(q["uv"], (k, 4, 2)))
            TX.append(np.full(k, q["tex"], np.int32))
            if biomas is not None and q.get("tinte_tipo") in ("pasto", "follaje", "agua"):  # color del bioma de cada bloque
                vb_, tabla = biomas
                cc = cs[m_]
                TI.append(tabla[q["tinte_tipo"]][vb_[cc[:, 0], cc[:, 1], cc[:, 2]]])
            else:
                ti = np.array(q["tinte"] if q["tinte"] else (255, 255, 255), np.float32) / 255.0
                TI.append(np.broadcast_to(ti, (k, 3)))
            rc = resaltar.get(int(sid))  # mueble resaltado: se tiñe con el color de su tipo
            RC.append(np.broadcast_to(np.array(rc if rc else (0, 0, 0), np.float32), (k, 3)))
            RA.append(np.full(k, 0.5 if rc else 0.0, np.float32))
            nx, ny, nz = n
            SH.append(np.full(k, nx * nx * 0.6 + ny * ny * (1.0 if ny > 0 else 0.5) + nz * nz * 0.8, np.float32))
            ao = np.ones((k, 4), np.float32)
            eje = int(np.argmax(np.abs(n)))
            if abs(n[eje]) > 0.999:  # cara recta: cada esquina se oscurece según los bloques que la rodean
                lado = 1 if n[eje] > 0 else 0
                if np.allclose(q["v"][:, eje], lado):
                    d = np.zeros(3, int)
                    d[eje] = 1 if n[eje] > 0 else -1
                    capa = cs[m_] + d
                    otros = [a for a in range(3) if a != eje]
                    for vi in range(4):
                        e1, e2 = np.zeros(3, int), np.zeros(3, int)
                        e1[otros[0]] = 1 if q["v"][vi, otros[0]] > 0.5 else -1
                        e2[otros[1]] = 1 if q["v"][vi, otros[1]] > 0.5 else -1
                        a1, a2, a3 = opaco_en(capa + e1), opaco_en(capa + e2), opaco_en(capa + e1 + e2)
                        occ = np.where(a1 & a2, 3, a1.astype(int) + a2 + a3)
                        ao[:, vi] = FACT_AO[occ]
            if luz is not None:  # la luz del aire que toca la cara: de noche (luz 0 = casi negro) o de día (bajo techo, más oscuro)
                dn = np.round(n).astype(int)
                pz0 = cs[m_] + dn
                afuera_ = (pz0 < 0).any(axis=1) | (pz0 >= [X, Y, Z]).any(axis=1)  # cara que da afuera de la zona leída
                pz = np.clip(pz0, 0, [X - 1, Y - 1, Z - 1])
                lv = np.maximum(luz[pz[:, 0], pz[:, 1], pz[:, 2], 0], luz[cs[m_][:, 0], cs[m_][:, 1], cs[m_][:, 2], 0])
                lb = np.maximum(luz[pz[:, 0], pz[:, 1], pz[:, 2], 1], luz[cs[m_][:, 0], cs[m_][:, 1], cs[m_][:, 2], 1])
                lv = np.where(afuera_, 15.0 * (0.15 if noche_ else 1.0), lv)
                # luz cálida: donde manda la de antorchas y faroles, tono naranja (como el mapa de luz del juego)
                w_ = np.clip(lb / 15.0, 0, 1) * (lb >= lv - 0.5) * ~afuera_
                calido = np.stack([np.ones_like(w_), 1 - 0.10 * w_, 1 - 0.30 * w_], axis=1).astype(np.float32)
                if noche_:
                    ao *= (NOCHE_MIN + (1 - NOCHE_MIN) * (lv / 15.0) ** NOCHE_EXP)[:, None]
                else:
                    ao *= brillo_mc(lv)[:, None]
            AO.append(ao)
            CA.append(calido if luz is not None else np.ones((k, 3), np.float32))
            AG.append(np.full(k, bool(q.get("fluido")) and "lava" not in estados[sid][0]))
    img = np.zeros((alto, ancho, 3), np.float32)
    t = np.linspace(0, 1, alto, dtype=np.float32)[:, None]
    img[:] = (np.array([70, 18, 16], np.float32)[None, :] * (1.2 - 0.4 * t) if nether else
              (CIELO_ARRIBA * (1 - t) + CIELO_ABAJO * t))[:, None, :] * (0.16 if noche_ else 1.0)  # en el Nether, rojo oscuro
    zbuf = np.full(alto * ancho, np.inf, np.float32)
    for q in extras:  # seres de verdad (cajas, cuadros, marcos)
        n = q["n"]
        if persp is None and np.dot(n, hacia_cam) <= 1e-6:
            continue
        if persp is not None and ((q["v"].mean(axis=0) - persp[0]) @ n) >= 0:
            continue
        V.append(q["v"][None, :, :])
        UV.append(q["uv"][None, :, :])
        TX.append(np.array([q["tex"]], np.int32))
        TI.append((np.array(q["tinte"] if q["tinte"] else (255, 255, 255), np.float32) / 255.0)[None, :])
        nx, ny, nz = n
        SH.append(np.array([nx * nx * 0.6 + ny * ny * (1.0 if ny > 0 else 0.5) + nz * nz * 0.8], np.float32))
        AO.append(np.ones((1, 4), np.float32))
        CA.append(np.ones((1, 3), np.float32))
        RC.append(np.zeros((1, 3), np.float32))
        RA.append(np.zeros(1, np.float32))
        AG.append(np.zeros(1, bool))
    if not V:
        return img.astype(np.uint8), None, 0, []
    V = np.concatenate([np.asarray(v, np.float32) for v in V])
    UV = np.concatenate(UV).astype(np.float32)
    TX, TI, SH = np.concatenate(TX), np.concatenate(TI).astype(np.float32), np.concatenate(SH)
    RC, RA = np.concatenate(RC), np.concatenate(RA)
    AO = np.concatenate(AO)
    CA = np.concatenate(CA)
    AG = np.concatenate(AG)

    if persp is None:  # ortográfica: escala y centro para que entre la zona
        pu, pv = V.reshape(-1, 3) @ r, -(V.reshape(-1, 3) @ u)
        umin, umax, vmin, vmax = pu.min(), pu.max(), pv.min(), pv.max()
        esc = min((ancho - 16) / max(umax - umin, 1e-3), (alto - 16) / max(vmax - vmin, 1e-3))
        cu, cv = (umin + umax) / 2, (vmin + vmax) / 2
        dmin = float((V.reshape(-1, 3) @ f).min()) - 1.0  # la profundidad tiene que salir positiva

        def proy(P):
            return (P @ r - cu) * esc + ancho / 2, (-(P @ u) - cv) * esc + alto / 2, P @ f - dmin
    else:
        esc = None
        C, foco = persp

        def proy(P):
            q = P - C
            d = np.maximum(q @ f, 1e-3)
            return (q @ r) / d * foco + ancho / 2, -(q @ u) / d * foco + alto / 2, d
    # muestras por cara según su tamaño en pantalla
    s0, t0, _ = proy(V[:, 0])
    s3, t3, d0 = proy(V[:, 3])
    s1, t1, _ = proy(V[:, 1])
    if persp is not None:
        dv = (V - persp[0][None, None, :].astype(np.float32)) @ f.astype(np.float32)  # profundidad de las 4 esquinas
        cruza = (dv.min(axis=1) < CERCA) & (dv.max(axis=1) > CERCA)
        ok = (np.isfinite(s0) & (d0 > CERCA)) | cruza
    else:
        cruza = np.zeros(len(V), bool)
        ok = np.isfinite(s0) & (d0 > CERCA)
    ESCALONES = np.array([1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 22, 27, 33, 40, 50, 62, 77, 95, 118, 146, 180, 222, 275, 340, 400])
    ks = np.clip(np.ceil(np.hypot(s3 - s0, t3 - t0) * 1.4) + 1, 1, 400)
    kt = np.clip(np.ceil(np.hypot(s1 - s0, t1 - t0) * 1.4) + 1, 1, 400)
    ks = ESCALONES[np.searchsorted(ESCALONES, ks)].astype(np.int32)  # pocos tamaños de tanda: rápido aunque haya caras enormes
    kt = ESCALONES[np.searchsorted(ESCALONES, kt)].astype(np.int32)
    lejos = float(np.percentile(d0[ok], 99)) if persp is not None and ok.any() else None
    total = [0]

    def componer(pix, dz, cara, S_, T_, Pw, col, ka, kb, trasl=False):
        """Prueba de profundidad y color final de unas muestras (de las dos maneras de dibujar)."""
        o = np.lexsort((dz, pix))
        pix_o = pix[o]
        prim = np.ones(len(o), bool)
        prim[1:] = pix_o[1:] != pix_o[:-1]
        o = o[prim]
        pix_o = pix[o]
        gana = dz[o] < zbuf[pix_o]
        o, pix_o = o[gana], pix_o[gana]
        if not len(o):
            return
        detras = zbuf[pix_o].copy()
        zbuf[pix_o] = dz[o]
        cf, so, to = cara[o], S_[o], T_[o]
        a4 = AO[cf]
        aom = (1 - so) * (1 - to) * a4[:, 0] + so * (1 - to) * a4[:, 3] + (1 - so) * to * a4[:, 1] + so * to * a4[:, 2]
        ka_ = ka[o] if np.ndim(ka) else ka
        kb_ = kb[o] if np.ndim(kb) else kb
        bord = (np.minimum(so, 1 - so) * ka_ < 0.55) | (np.minimum(to, 1 - to) * kb_ < 0.55)
        c = col[o, :3].astype(np.float32) * TI[cf] * SH[cf][:, None]
        ra = RA[cf][:, None]
        c = c * (1 - ra) + RC[cf] * SH[cf][:, None] * ra
        c *= (np.where(bord, 0.88, 1.0) * aom)[:, None] * CA[cf]
        if regla:  # cuadrícula: líneas rojas donde x es múltiplo del paso, azules donde lo es z (sobre lo que mira arriba)
            cada = regla["paso"]
            Pv = Pw[o]
            arriba = SH[cf] > 0.999
            grosor = (1.3 / esc) if esc else 1.3 * dz[o] / persp[1]
            lx = arriba & (np.abs(Pv[:, 0] - np.round(Pv[:, 0] / cada) * cada) < grosor)
            lz = arriba & (np.abs(Pv[:, 2] - np.round(Pv[:, 2] / cada) * cada) < grosor) & ~lx
            c[lx] = c[lx] * 0.3 + np.array([235, 40, 40], np.float32) * 0.7
            c[lz] = c[lz] * 0.3 + np.array([40, 100, 245], np.float32) * 0.7
        if lejos and not noche_:
            niebla = np.clip((dz[o] - lejos * 0.7) / (lejos * 0.6), 0, 0.4)[:, None]
            c = c * (1 - niebla) + CIELO_ABAJO * niebla
        if trasl:  # agua: se ve el fondo; cuanto más honda, más tapa
            hondo = np.where(np.isfinite(detras), detras - dz[o], 40.0)
            alfa = np.clip(0.42 + hondo * 0.07, 0.42, 0.9)[:, None]
            c = c * alfa + img.reshape(-1, 3)[pix_o] * (1 - alfa)
        img.reshape(-1, 3)[pix_o] = c
        total[0] += len(o)

    # caras grandes en perspectiva (muy cerca de la cámara): rayo por píxel contra la cara, solo en su recuadro de pantalla
    grandes_ = ok & ((ks * kt > 1600) | cruza) if persp is not None else np.zeros(len(ok), bool)

    def pasada(mascara, trasl):
      grandes = grandes_ & mascara
      if grandes.any():
          Cc, foco_ = persp
          for qi in np.flatnonzero(grandes):
              v0, e1, e2 = V[qi, 0].astype(np.float64), (V[qi, 3] - V[qi, 0]).astype(np.float64), (V[qi, 1] - V[qi, 0]).astype(np.float64)
              n_ = np.cross(e1, e2)
              esq = np.array([V[qi, 0], V[qi, 1], V[qi, 2], V[qi, 3]], np.float64)
              dd = (esq - Cc) @ f
              if (dd < 0.05).all():
                  continue
              # recorte contra el plano de la cámara (lo de atrás no se proyecta): el recuadro sale de lo que queda delante
              pts, orden_ = [], [0, 1, 2, 3]
              for k_ in range(4):
                  pa, pb = esq[orden_[k_]], esq[orden_[(k_ + 1) % 4]]
                  da, db = dd[orden_[k_]], dd[orden_[(k_ + 1) % 4]]
                  if da >= 0.05:
                      pts.append(pa)
                  if (da >= 0.05) != (db >= 0.05):
                      tcorte = (0.05 - da) / (db - da)
                      pts.append(pa + (pb - pa) * tcorte)
              a_, b_, _ = proy(np.array(pts, np.float32))
              xa, xb = int(max(0, np.floor(a_.min()))), int(min(ancho - 1, np.ceil(a_.max())))
              ya, yb = int(max(0, np.floor(b_.min()))), int(min(alto - 1, np.ceil(b_.max())))
              if xa > xb or ya > yb:
                  continue
              gx, gy = np.meshgrid(np.arange(xa, xb + 1), np.arange(ya, yb + 1))
              gx, gy = gx.ravel(), gy.ravel()
              dirs = f[None, :] + ((gx + 0.5 - ancho / 2) / foco_)[:, None] * r[None, :] - ((gy + 0.5 - alto / 2) / foco_)[:, None] * u[None, :]
              den = dirs @ n_
              ok_ = np.abs(den) > 1e-9
              lam = np.where(ok_, ((v0 - Cc) @ n_) / np.where(ok_, den, 1), -1)
              Pw = Cc[None, :] + lam[:, None] * dirs
              dv = Pw - v0
              # coordenadas (s, t) en la cara con la base dual de sus dos lados
              ee = np.array([[e1 @ e1, e1 @ e2], [e1 @ e2, e2 @ e2]])
              try:
                  inv = np.linalg.inv(ee)
              except np.linalg.LinAlgError:
                  continue
              st = np.stack([dv @ e1, dv @ e2], axis=1) @ inv.T
              S_, T_ = st[:, 0], st[:, 1]
              dz = lam * (dirs @ f)
              dentro = ok_ & (lam > 0) & (S_ >= 0) & (S_ <= 1) & (T_ >= 0) & (T_ <= 1) & (dz > CERCA)
              if not dentro.any():
                  continue
              S_, T_, Pw, dz, gx, gy = S_[dentro], T_[dentro], Pw[dentro], dz[dentro], gx[dentro], gy[dentro]
              W = UV[qi, 0][None, :] + S_[:, None] * (UV[qi, 3] - UV[qi, 0])[None, :] + T_[:, None] * (UV[qi, 1] - UV[qi, 0])[None, :]
              col = texs[TX[qi], np.clip(W[:, 1].astype(np.int32), 0, 15), np.clip(W[:, 0].astype(np.int32), 0, 15)]
              vis_ = col[:, 3] >= 128
              if not vis_.any():
                  continue
              cara = np.full(int(vis_.sum()), qi)
              componer((gy * ancho + gx)[vis_], dz[vis_].astype(np.float32), cara, S_[vis_].astype(np.float32),
                       T_[vis_].astype(np.float32), Pw[vis_].astype(np.float32), col[vis_], float(ks[qi]), float(kt[qi]), trasl)
      claves = ks * 1000 + kt
      for clave in np.unique(claves[ok & ~grandes & mascara]):
          sel = np.flatnonzero(ok & ~grandes & mascara & (claves == clave))
          a_, b_ = int(clave // 1000), int(clave % 1000)
          ss = ((np.arange(a_) + 0.5) / a_).astype(np.float32)
          tt = ((np.arange(b_) + 0.5) / b_).astype(np.float32)
          S, T = np.meshgrid(ss, tt, indexing="ij")
          S, T = S.ravel(), T.ravel()
          paso = max(1, 3_000_000 // max(1, len(S)))
          for i0 in range(0, len(sel), paso):
              qi = sel[i0:i0 + paso]
              v0, v1, v3 = V[qi, 0][:, None, :], V[qi, 1][:, None, :], V[qi, 3][:, None, :]
              P = (v0 + S[None, :, None] * (v3 - v0) + T[None, :, None] * (v1 - v0)).reshape(-1, 3)
              uv0, uv1, uv3 = UV[qi, 0][:, None, :], UV[qi, 1][:, None, :], UV[qi, 3][:, None, :]
              W = (uv0 + S[None, :, None] * (uv3 - uv0) + T[None, :, None] * (uv1 - uv0)).reshape(-1, 2)
              px, py, dz = proy(P)
              ix, iy = px.astype(np.int32), py.astype(np.int32)
              cara = np.repeat(qi, len(S))
              col = texs[TX[cara], np.clip(W[:, 1].astype(np.int32), 0, 15), np.clip(W[:, 0].astype(np.int32), 0, 15)]
              vale = (ix >= 0) & (ix < ancho) & (iy >= 0) & (iy < alto) & (col[:, 3] >= 128) & (dz > CERCA)
              if not vale.any():
                  continue
              componer((iy * ancho + ix)[vale], dz[vale], cara[vale], np.tile(S, len(qi))[vale], np.tile(T, len(qi))[vale], P[vale],
                       col[vale], float(a_), float(b_), trasl)

    pasada(~AG, False)  # primero lo sólido
    if AG.any():
        pasada(AG, True)  # después el agua, transparente
    total = total[0]
    # seres (aldeanos, monstruos, animales, soportes de armadura): un palito de color del alto de un jugador; si algo
    # lo tapa se dibuja más apagado
    for e in seres:
        if extras and e["tipo"] in ("aldeano", "monstruo", "animal", "armadura", "marco", "cuadro"):
            a0, b0, d0 = proy(np.array([[e["x"] + 0.5, e["y"] + 1.0, e["z"] + 0.5]], np.float32))
            xx, yy = int(a0[0]), int(b0[0])
            if np.isfinite(a0[0]) and d0[0] > 0.3 and 2 <= xx < ancho - 2 and 2 <= yy < alto - 2 and zbuf[yy * ancho + xx] < d0[0] - 0.8:
                img[yy - 2:yy + 3, xx - 2:xx + 3] = np.array(COLOR_SER.get(e["tipo"], COLOR_SER["otro"]), np.float32) * 0.6
            continue
        col = np.array(COLOR_SER.get(e["tipo"], COLOR_SER["otro"]), np.float32)
        pies = np.array([[e["x"] + 0.5, e["y"], e["z"] + 0.5]], np.float32)
        cabeza = pies + np.array([[0, 0.5 if e["tipo"] in ("marco", "cuadro") else 1.8, 0]], np.float32)  # marcos y cuadros: corto
        a0, b0, d0 = proy(pies)
        a1, b1, d1 = proy(cabeza)
        if not (np.isfinite(a0[0]) and d0[0] > 0.3):
            continue
        n_ = max(2, int(abs(b1[0] - b0[0]) + abs(a1[0] - a0[0])))
        grosor = max(1, min(4, n_ // 8))
        for k in range(n_ + 1):
            tk = k / n_
            xx, yy = int(a0[0] + (a1[0] - a0[0]) * tk), int(b0[0] + (b1[0] - b0[0]) * tk)
            dd = d0[0] + (d1[0] - d0[0]) * tk
            for w in range(-grosor, grosor + 1):
                if 0 <= xx + w < ancho and 0 <= yy < alto:
                    tapado = zbuf[yy * ancho + xx + w] < dd - 0.6
                    img[yy, xx + w] = col * (0.45 if tapado else 1.0) + (img[yy, xx + w] * 0.55 if tapado else 0)
    for (mx, my, mz) in marcas:  # marcas rojas: un anillo que se ve aunque el punto quede tapado
        a, b, _ = proy(np.array([[mx + 0.5, my + 0.5, mz + 0.5]], np.float32))
        a, b = int(a[0]), int(b[0])
        for ang in np.linspace(0, 2 * math.pi, 48):
            for rad in (6, 7):
                xx, yy = int(a + rad * math.cos(ang)), int(b + rad * math.sin(ang))
                if 0 <= xx < ancho and 0 <= yy < alto:
                    img[yy, xx] = (230, 20, 20)
    # píxeles sueltos sin pintar entre dos caras (se veía el cielo como puntitos): se rellenan con sus vecinos
    zb = zbuf.reshape(alto, ancho)
    hueco = ~np.isfinite(zb)
    hueco[0, :] = hueco[-1, :] = hueco[:, 0] = hueco[:, -1] = False
    vec = [(0, 1), (0, -1), (1, 0), (-1, 0)]
    llenos = sum(np.isfinite(np.roll(zb, d, axis=(0, 1))) for d in vec)
    tapar = hueco & (llenos >= 3)
    if tapar.any():
        suma = np.zeros_like(img)
        for d in vec:
            ok_ = np.isfinite(np.roll(zb, d, axis=(0, 1)))[..., None]
            suma += np.roll(img, d, axis=(0, 1)) * ok_
        img[tapar] = (suma / np.maximum(llenos, 1)[..., None])[tapar]
    # rosa de los vientos (arriba a la derecha): la flecha apunta al norte de la imagen
    cxp, cyp = ancho - 42, 42
    a0, b0, _ = proy(np.array([[origen[0] + X / 2, origen[1] + Y / 2, origen[2] + Z / 2]], np.float32))
    a1, b1, _ = proy(np.array([[origen[0] + X / 2, origen[1] + Y / 2, origen[2] + Z / 2 - 4]], np.float32))
    dx, dy = float(a1[0] - a0[0]), float(b1[0] - b0[0])
    if persp is not None:  # en perspectiva: el norte según hacia dónde mira la cámara (adelante = arriba de la rosa)
        fh = np.array([f[0], 0.0, f[2]])
        fh = fh / max(1e-9, np.linalg.norm(fh))
        norte = np.array([0.0, 0.0, -1.0])
        dx, dy = float(norte @ r), float(-(norte @ fh))
    ln = math.hypot(dx, dy)
    img[cyp - 30:cyp + 31, cxp - 30:cxp + 31] = img[cyp - 30:cyp + 31, cxp - 30:cxp + 31] * 0.45 + 140
    if ln > 1e-6:
        dx, dy = dx / ln, dy / ln
        for k in range(-18, 22):
            for w in (-1, 0, 1):
                xx, yy = int(cxp + dx * k - dy * w), int(cyp + dy * k + dx * w)
                img[yy, xx] = (200, 20, 20) if k > 0 else (40, 40, 40)
        nx_, ny_ = int(cxp + dx * 27), int(cyp + dy * 27)
        for fi, fila in enumerate(("#...#", "##..#", "#.#.#", "#..##", "#...#")):
            for ci, ch in enumerate(fila):
                if ch == "#":
                    img[ny_ - 5 + fi * 2:ny_ - 3 + fi * 2, nx_ - 5 + ci * 2:nx_ - 3 + ci * 2] = (200, 20, 20)
    # números de la cuadrícula en los dos bordes del lado de la cámara: x en rojo, z en azul
    if regla:
        paso, y_ = regla["paso"], regla["y"] + 1.0
        borde_z = regla["z2"] + 1 if f[2] < 0 else regla["z1"]
        borde_x = regla["x2"] + 1 if f[0] < 0 else regla["x1"]
        for eje, desde_, hasta_, col in (("x", regla["x1"], regla["x2"], (200, 30, 30)), ("z", regla["z1"], regla["z2"], (30, 80, 220))):
            v = -(-desde_ // paso) * paso
            while v <= hasta_:
                P0 = np.array([[v, y_, borde_z] if eje == "x" else [borde_x, y_, v]], np.float32)
                a, b, d = proy(P0)
                a, b = int(a[0]), int(b[0])
                txt_ = str(int(v))
                w_ = len(txt_) * 8 + 4
                if d[0] > 0.3 and 0 <= a - w_ // 2 and a + w_ // 2 < ancho and 0 <= b - 6 and b + 7 < alto:
                    img[b - 6:b + 7, a - w_ // 2:a - w_ // 2 + w_] = np.array(col, np.float32)
                    letrero_crudo(img, a - w_ // 2 + 2, b - 5, txt_, 2, (255, 255, 255))
                v += paso
    # etiquetas numeradas: un cuadrito con el número sobre cada cosa; con borde punteado si algo la tapa
    vis = []
    zb = zbuf.reshape(alto, ancho)
    ocupado = []
    for et in etiquetas:
        a, b, d = proy(np.array([[et["x"] + 0.5, et["y"] + 0.6, et["z"] + 0.5]], np.float32))
        a, b, d = float(a[0]), float(b[0]), float(d[0])
        if not (np.isfinite(a) and d > 0.3 and 4 <= a < ancho - 4 and 4 <= b < alto - 4):
            vis.append(None)
            continue
        ys, xs = slice(max(0, int(b) - 2), int(b) + 3), slice(max(0, int(a) - 2), int(a) + 3)
        visible = bool((zb[ys, xs] >= d - 1.3).any())
        vis.append(visible)
        txt_ = str(et["n"])
        w_, h_ = len(txt_) * 8 + 4, 13
        bx, by = int(a) - w_ // 2, int(b) - h_ - 4
        for (ox, oy) in ocupado:  # no encimar dos etiquetas: la nueva se corre hacia arriba
            if abs(ox - bx) < w_ and abs(oy - by) < h_:
                by = oy - h_ - 1
        bx, by = max(0, min(ancho - w_, bx)), max(0, min(alto - h_, by))
        ocupado.append((bx, by))
        col = np.array(et.get("color", (255, 255, 255)), np.float32)
        img[by:by + h_, bx:bx + w_] = col * (1.0 if visible else 0.5)
        if not visible:
            img[by:by + h_:2, bx] = img[by:by + h_:2, bx + w_ - 1] = 255
        img[by + h_:min(alto, int(b)), int(a)] = col  # palito hasta el punto
        letrero_crudo(img, bx + 2, by + 1, txt_, 2, (0, 0, 0) if sum(col) > 380 else (255, 255, 255))
    return np.clip(img, 0, 255).astype(np.uint8), proy, total, vis


def rumbo(v):
    """Hacia qué punto cardinal apunta un vector horizontal (x, z)."""
    ang = math.degrees(math.atan2(v[0], -v[1])) % 360  # 0 = norte, 90 = este
    return ["norte", "noreste", "este", "sureste", "sur", "suroeste", "oeste", "noroeste"][int((ang + 22.5) // 45) % 8]


def vista(m, decodificar, cx, cz, radio=50, y_min=None, y_max=None, giro=None, desde="sureste", inclinacion=35.0,
          proyeccion="orto", distancia=None, ancho=1100, alto=760, sin_hojas=False, marcas=(), cy=None, noche=False,
          caja=None, seres=False, resaltar_muebles=False, categoria=None, contenido_fn=None, cuadricula=True,
          camara_en=None, fov=70.0, luz_dia=True):
    """Arma la zona (2·radio+1 de lado) alrededor de (cx, cz) y la dibuja. Devuelve (imagen, texto)."""
    t0 = time.time()
    rec, geo = recursos()
    # en perspectiva (dron o jugador) se ve lejos: hasta 128 a la redonda; desde arriba u ortográfica, 64 (más no entra en la imagen)
    lejos_ok = camara_en is not None or str(proyeccion).startswith("persp")
    radio = int(max(4, min(radio, 128 if lejos_ok else 64)))
    if camara_en is not None:  # cámara de jugador: la zona va delante de los ojos, hacia donde mira
        yaw_ = math.radians(float(giro if giro is not None else DESDE.get(desde, 135)))
        cx = int(math.floor(camara_en[0] - math.sin(yaw_) * radio * 0.75))
        cz = int(math.floor(camara_en[2] + math.cos(yaw_) * radio * 0.75))
    x1, x2, z1, z2 = cx - radio, cx + radio, cz - radio, cz + radio
    if caja:  # una casa del plano: su caja y 3 bloques alrededor
        x1, x2, z1, z2 = caja["x0"] - 3, caja["x1"] + 3, caja["z0"] - 3, caja["z1"] + 3
        cx, cz, radio = (x1 + x2) // 2, (z1 + z2) // 2, max(x2 - x1, z2 - z1) // 2 + 1
    tops, suelos = [], []
    nether = "DIM-1" in getattr(m.g, "carpeta", "")
    for x in range(x1, x2 + 1, 4):
        for z in range(z1, z2 + 1, 4):
            if nether:  # el techo de piedra base taparía todo: el piso es el primer sólido con aire encima bajo y 118
                arriba = m.bloque(x, 118, z)
                if arriba is None:
                    continue
                for yy in range(117, 0, -1):
                    b = m.bloque(x, yy, z)
                    if b not in AIRE_CORTO and arriba in AIRE_CORTO:
                        suelos.append(yy)
                        tops.append(min(117, yy + 12))
                        break
                    arriba = b
                continue
            t = m.tope(x, z)[1]
            if t is not None:
                tops.append(t)
                s_ = m.suelo(x, z)[1]
                if s_ is not None:
                    suelos.append(s_)
    if not tops:
        return None, "No hay chunks generados en esa zona."
    suelos.sort()
    hondo = 6 if radio >= 30 else 3
    y_min_pedido = y_min is not None
    y_min = (suelos[len(suelos) // 10] - hondo if suelos else min(tops) - hondo) if y_min is None else y_min
    y_max = min(max(tops) + 1, y_min + 140) if y_max is None else y_max
    if camara_en is not None:  # que entre lo que está a la altura de los ojos (y un poco más) y lo de abajo
        y_max = max(y_max, int(camara_en[1]) + 8)
        y_min = min(y_min, int(camara_en[1]) - 30)
    if y_min > y_max:  # al revés: se dan vuelta
        y_min, y_max = y_max, y_min
    if y_max - y_min < 4 and not y_min_pedido:  # se pidió ver algo bajo tierra (cripta, sótano): el corte de abajo baja con el de arriba
        y_min = y_max - 14
    vol, estados, falta = volumen(m, decodificar, x1, x2, y_min, y_max, z1, z2)
    yaw = float(giro) if giro is not None else float(DESDE.get(desde, 135))
    f, r, u = camara(yaw, inclinacion)
    persp = None
    if camara_en is not None:  # como en el juego: ojos en camara_en, campo de visión VERTICAL fov (Minecraft: 70 + 40·fov)
        persp = (np.array(camara_en, np.float64), (alto / 2) / math.tan(math.radians(fov / 2)))
        proyeccion = "perspectiva"
    elif proyeccion.startswith("persp"):
        piso = suelos[len(suelos) // 2] if suelos else y_min + 6
        objetivo = np.array([cx + 0.5, (cy if cy is not None else piso + 4) + 0.5, cz + 0.5])
        dist = float(distancia) if distancia else radio * (2.1 if caja else 1.55)
        persp = (objetivo - f * dist, (ancho / 2) / math.tan(math.radians(35)))
    sin_cielo = "DIM-1" in getattr(m.g, "carpeta", "") or "DIM1" in getattr(m.g, "carpeta", "")
    luz = volumen_luz(m, x1, x2, y_min, y_max, z1, z2, 0.0 if sin_cielo else (LUNA if noche else 1.0),
                      minimo=(9.0 if not noche else 6.0) if sin_cielo else 0.0) if (noche or luz_dia) else None
    biomas = None
    try:  # colores de bioma (pasto, follaje, agua) como en el juego
        vb_, nombres_b = volumen_bioma(m, x1, x2, y_min, y_max, z1, z2)
        tabla = {k: np.array([colores_bioma(rec, nb)[k] for nb in nombres_b], np.float32) / 255.0 for k in ("pasto", "follaje", "agua")}
        biomas = (vb_, tabla)
    except Exception:
        biomas = None
    lista = []
    if seres:
        try:
            lista = [e for e in m.seres(x1, z1, x2, z2) if e["tipo"] in COLOR_SER and y_min <= e["y"] <= y_max]
        except Exception:
            lista = []
    # muebles y seres con nombre: tinte por tipo y etiquetas numeradas (ronda 1, 4-nov)
    resaltar, etiquetas, cuenta_cat, info_et = {}, [], {}, []
    if resaltar_muebles and categoria is not None:
        resaltar, etiquetas, cuenta_cat, info_et = marcar_muebles(vol, estados, (x1, y_min, z1), categoria,
                                                                   chica=(x2 - x1) <= 44)
        if contenido_fn is not None and etiquetas:  # qué tiene cada cofre (ronda 4): sus objetos, botín sin abrir o vacío
            try:
                cont = contenido_fn(m, x1, z1, x2, z2)
            except Exception:
                cont = {}
            for i, et in enumerate(etiquetas):
                if not info_et[i].startswith("cofre"):
                    continue
                cs = [cont[p] for p in et.get("celdas", [(et["x"], et["y"], et["z"])]) if p in cont]
                if len(cs) == 1:
                    info_et[i] += " → " + cs[0]
                elif cs:  # un grupo (fila de barriles, sótano de cofres): cuántos vacíos, con botín o con cosas
                    vac = sum(1 for c_ in cs if c_ == "VACÍO")
                    bot = [c_ for c_ in cs if c_.startswith("botín")]
                    con = [c_ for c_ in cs if c_[0].isdigit()]
                    info_et[i] += " → %d contenedores: %s" % (len(cs), ", ".join(x_ for x_ in (
                        ("%d con botín sin abrir" % len(bot)) if bot else "", ("%d con cosas (%s)" % (len(con), con[0])) if con else "",
                        ("%d VACÍOS" % vac) if vac else "") if x_))
    if seres and resaltar_muebles:
        for e in lista:
            etiquetas.append({"x": e["x"], "y": e["y"] + 1, "z": e["z"], "n": len(etiquetas) + 1, "color": COLOR_SER[e["tipo"]]})
            info_et.append("%s %s%s%s" % (e["tipo"], e["id"].split(":")[-1], (" «%s»" % e["nombre"]) if e.get("nombre") else "",
                                       (" (%s)" % e["detalle"]) if e.get("detalle") else "") + " en (%d, %d, %d)" % (e["x"], e["y"], e["z"]))
    regla = None
    if cuadricula:
        paso = 5 if (x2 - x1) <= 40 else 10
        piso_r = suelos[len(suelos) // 2] if suelos else y_min
        regla = {"paso": paso, "x1": x1, "x2": x2, "z1": z1, "z2": z2, "y": min(max(piso_r, y_min), y_max)}
    extras = []
    try:
        extras += extras_estandartes(rec, m, x1, z1, x2, z2, y_min, y_max)
    except Exception:
        pass
    if True:  # cuadros y marcos siempre (son adorno de la casa); las cajas de los mobs, solo con seres
        if not seres:
            try:
                m.seres(x1, z1, x2, z2)  # carga las entidades guardadas de la zona
            except Exception:
                pass
        tipo_de = {"aldeano": COLOR_SER["aldeano"], "monstruo": COLOR_SER["monstruo"], "animal": COLOR_SER["animal"],
                   "armadura": COLOR_SER["armadura"]}

        def color_ser(e):
            i = str(e.get("id", ""))
            if "armor_stand" in i:
                return tipo_de["armadura"]
            if "villager" in i and "zombie" not in i:
                return tipo_de["aldeano"]
            return tipo_de["monstruo"] if any(k in i for k in ("zombie", "skeleton", "creeper", "spider", "witch", "pillager",
                                                                    "vindicator", "enderman", "slime", "phantom", "drowned", "husk",
                                                                    "stray", "wither", "ghast", "blaze", "evoker", "ravager")) else tipo_de["animal"]
        try:
            extras += extras_seres(rec, m, x1, z1, x2, z2, y_min, y_max, color_ser, mobs=seres)
        except Exception:
            pass
    img, proy, n, vis = dibujar(rec, geo, vol, estados, np.array([x1, y_min, z1]), f, r, u, ancho, alto, persp, sin_hojas, marcas,
                                luz, lista, nether, resaltar, etiquetas, regla, bool(noche), biomas, extras)
    # puntos de referencia: dónde caen en la imagen las esquinas y el centro (a la altura del suelo)
    ref = []
    if proy is not None:
        piso = suelos[len(suelos) // 2] if suelos else y_min
        for nom, (px, pz) in (("centro", (cx, cz)), ("esquina NO", (x1, z1)), ("esquina NE", (x2, z1)),
                               ("esquina SE", (x2, z2)), ("esquina SO", (x1, z2))):
            a, b, d = proy(np.array([[px + 0.5, piso + 1.0, pz + 0.5]], np.float32))
            if d[0] > 0 and 0 <= a[0] < ancho and 0 <= b[0] < alto:
                ref.append("%s (%d, %d, %d) → píxel (%d, %d)" % (nom, px, piso + 1, pz, a[0], b[0]))
    derecha = rumbo(r[[0, 2]])
    arriba = rumbo(u[[0, 2]]) if abs(inclinacion) > 60 else "cielo"
    txt = ("Vista 3D %s de (%d..%d, %d..%d), y %d a %d, %d x %d bloques. Cámara mirando hacia el %s (giro %.0f), "
           "inclinación %.0f°%s. En la imagen, la derecha es el %s%s.\n%s%s\n%d estados de bloque, %.1f s.") % (
        "en perspectiva" if persp is not None else "ortográfica", x1, x2, z1, z2, y_min, y_max, x2 - x1 + 1, z2 - z1 + 1,
        rumbo(f[[0, 2]]) if abs(inclinacion) < 85 else "suelo", yaw, inclinacion,
        ((", ojos en (%.1f, %.1f, %.1f), campo de visión %.0f°" % (camara_en[0], camara_en[1], camara_en[2], fov)) if camara_en is not None
         else (", desde %.0f bloques" % (distancia or radio * 1.55)) if persp is not None else ""), derecha,
        "" if arriba == "cielo" else " y arriba es el " + arriba,
        ("Referencias: " + "; ".join(ref) if ref else "") + ("\nDe noche: cada cara con la luz real guardada (antorchas y "
                                                            "faroles; la luna da poco). Lo negro es luz 0: ahí aparecen monstruos." if noche else ""), ("\nOjo: %d chunks sin generar (negro)." % falta) if falta else "",
        len(estados), time.time() - t0)
    if regla:
        txt += ("\nCuadrícula cada %d bloques sobre el suelo y los techos: líneas ROJAS = x múltiplo de %d (su número en rojo, en el "
                "borde %s), líneas AZULES = z múltiplo de %d (número en azul, en el borde %s). Una casilla = %d x %d bloques: "
                "sirve para contar y ubicar cualquier cosa.") % (regla["paso"], regla["paso"], "sur" if f[2] < 0 else "norte",
                                                                   regla["paso"], "este" if f[0] < 0 else "oeste", regla["paso"], regla["paso"])
    if resaltar_muebles:
        txt += ("\nMuebles teñidos por tipo: cama rojo, cofre naranja, mesa de oficio azul acero, mesa café, asiento morado, "
                "estante magenta, planta verde, luz amarillo, adorno rosa, puerta café oscuro, otro turquesa.")
        if cuenta_cat:
            txt += "\nEn la zona: " + ", ".join("%d %s" % (v, k) for k, v in sorted(cuenta_cat.items(), key=lambda t: -t[1]))
        if etiquetas:
            txt += "\nEtiquetas (número en la imagen; «tapado» = detrás de algo, cuadrito apagado):\n" + "\n".join(
                "  %d %s%s" % (i + 1, info_et[i], "" if vis[i] else (" — tapado" if vis[i] is False else " — fuera de la imagen"))
                for i in range(len(etiquetas)))
    if seres:
        cuenta = {}
        for e in lista:
            cuenta[e["tipo"]] = cuenta.get(e["tipo"], 0) + 1
        txt += ("\nSeres (palitos de color; apagados si algo los tapa): " +
                (", ".join("%d %s%s" % (v, k, "s" if v > 1 else "") for k, v in sorted(cuenta.items())) or "ninguno") +
                ". Azul = aldeano, rojo = monstruo, amarillo = animal, gris = soporte de armadura, violeta corto = marco, "
                "naranja corto = cuadro. Es lo último que guardó el servidor.")
    return img, txt


ETIQUETAS = {"N": ("#...#", "##..#", "#.#.#", "#..##", "#...#"), "S": (".####", "#....", ".###.", "....#", "####."),
             "E": ("#####", "#....", "####.", "#....", "#####"), "O": (".###.", "#...#", "#...#", "#...#", ".###."),
             "A": (".###.", "#...#", "#####", "#...#", "#...#"), "R": ("####.", "#...#", "####.", "#..#.", "#...#"),
             "B": ("####.", "#...#", "####.", "#...#", "####."), "I": ("#####", "..#..", "..#..", "..#..", "#####"),
             "D": ("####.", "#...#", "#...#", "#...#", "####."), "P": ("####.", "#...#", "####.", "#....", "#...."), " ": (".....",) * 5}


def letrero(img, x, y, texto, esc=3, color=(255, 255, 255)):
    """Texto en mayúsculas con letras de 5x5 (solo las que hacen falta: DESDE, ARRIBA y los puntos cardinales)."""
    ancho = len(texto) * 6 * esc + 2 * esc
    img[y:y + 7 * esc, x:x + ancho] = img[y:y + 7 * esc, x:x + ancho] // 3
    for i, ch in enumerate(texto.upper()):
        for fi, fila in enumerate(ETIQUETAS.get(ch, ETIQUETAS[" "])):
            for ci, v in enumerate(fila):
                if v == "#":
                    yy, xx = y + esc + fi * esc, x + esc + (i * 6 + ci) * esc
                    img[yy:yy + esc, xx:xx + esc] = color


def vuelta(m, decodificar, cx, cz, radio=50, inclinacion=35.0, ancho=1500, con_arriba=True, **kw):
    """La misma zona desde los 4 rincones (y desde arriba) en una sola imagen: cuesta como una foto grande."""
    vistas = [("NO", "noroeste"), ("NE", "noreste"), ("SO", "suroeste"), ("SE", "sureste")]
    cols = 3 if con_arriba else 2
    w = ancho // cols
    h = int(w * 0.72)
    paneles, textos = [], []
    for et, d in vistas:
        img, txt = vista(m, decodificar, cx, cz, radio=radio, desde=d, inclinacion=inclinacion, ancho=w, alto=h, **kw)
        if img is None:
            return None, txt
        letrero(img, 8, 8, "DESDE " + et, 2)
        paneles.append(img)
        textos.append("Desde el %s: %s" % (d, txt.split("\n")[0]))
    lienzo = np.zeros((2 * h, cols * w, 3), np.uint8)
    for i, p in enumerate(paneles):
        lienzo[(i // 2) * h:(i // 2 + 1) * h, (i % 2) * w:(i % 2 + 1) * w] = p
    if con_arriba:
        img, txt = vista(m, decodificar, cx, cz, radio=radio, desde="sur", inclinacion=90, ancho=w, alto=2 * h, **kw)
        letrero(img, 8, 8, "ARRIBA", 2)
        textos.append("Desde arriba: " + txt.split("\n")[0])
        lienzo[:, 2 * w:3 * w] = img
        lienzo[:, 2 * w - 1:2 * w + 1] = 0
    lienzo[h - 1:h + 1, :2 * w] = 0
    lienzo[:, w - 1:w + 1] = 0
    return lienzo, ("Vuelta completa en una imagen: arriba a la izquierda desde el noroeste, arriba al medio desde el noreste, "
                    "abajo a la izquierda desde el suroeste y abajo al medio desde el sureste%s. Cada panel tiene su letrero y su "
                    "rosa de los vientos (la flecha roja con N apunta al norte).\n" %
                    (", y a la derecha desde arriba (norte arriba)" if con_arriba else "")) + "\n".join(textos)


DIGITOS = {"0": ("###", "#.#", "#.#", "#.#", "###"), "1": (".#.", "##.", ".#.", ".#.", "###"), "2": ("###", "..#", "###", "#..", "###"),
           "3": ("###", "..#", "###", "..#", "###"), "4": ("#.#", "#.#", "###", "..#", "..#"), "5": ("###", "#..", "###", "..#", "###"),
           "6": ("###", "#..", "###", "#.#", "###"), "7": ("###", "..#", "..#", "..#", "..#"), "8": ("###", "#.#", "###", "#.#", "###"),
           "9": ("###", "#.#", "###", "..#", "###"), "-": ("...", "...", "###", "...", "...")}


def letrero_crudo(img, x, y, texto, esc=2, color=(255, 255, 255)):
    """Números de 3x5 sin fondo (para las etiquetas)."""
    for i, ch in enumerate(texto):
        for fi, fila in enumerate(DIGITOS.get(ch, ("...",) * 5)):
            for ci, v in enumerate(fila):
                if v == "#":
                    yy, xx = y + fi * esc, x + (i * 4 + ci) * esc
                    img[yy:yy + esc, xx:xx + esc] = color


COLOR_CAT = {"cama": (210, 40, 50), "cofre": (240, 150, 20), "oficio": (70, 110, 170), "mesa": (150, 90, 40),
             "asiento": (120, 60, 180), "estante": (190, 60, 150), "planta": (40, 170, 50), "luz": (255, 225, 40),
             "adorno": (240, 130, 170), "puerta": (110, 65, 25), "otro": (0, 180, 190), "escalera": (170, 125, 70)}
ORDEN_ETIQUETA = ["cofre", "cama", "oficio", "mesa", "asiento", "estante", "otro"]  # las luces se tiñen y se cuentan, sin número


def marcar_muebles(vol, estados, origen, categoria, chica=True, tope=60):
    """Tinte por estado (muebles) y etiquetas para los importantes: cada grupo de bloques vecinos del mismo tipo (una
    cama de 2, una fila de mostradores) lleva un solo número. categoria(nombre, props) es la del Ojo (ojo_del_mundo)."""
    cats = {}
    for i, (nom, pr) in enumerate(estados):
        if i == 0:
            continue
        try:
            c = categoria(nom[10:] if nom.startswith("minecraft:") else nom, pr)
        except Exception:
            c = None
        if c in COLOR_CAT and c != "alfombra":
            cats[i] = c
    resaltar = {i: COLOR_CAT[c] for i, c in cats.items() if c not in ("puerta", "escalera")}
    cuenta = {}
    marcar = set(ORDEN_ETIQUETA if chica else ["cofre", "cama", "oficio"])
    ids = [i for i, c in cats.items() if c in marcar]
    if not ids:
        return resaltar, [], cuenta, []
    celdas = np.argwhere(np.isin(vol, ids))
    por = {}
    for x, y, z in celdas:
        por[(int(x), int(y), int(z))] = int(vol[x, y, z])
    vistos, grupos_ = set(), []
    for p, sid in por.items():
        if p in vistos:
            continue
        cat = cats[sid]
        pila, g = [p], []
        vistos.add(p)
        while pila:
            q = pila.pop()
            g.append(q)
            for d in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0)):
                v = (q[0] + d[0], q[1] + d[1], q[2] + d[2])
                if v in por and v not in vistos and cats[por[v]] == cat and (cat != "luz" or len(g) < 1):
                    vistos.add(v)
                    pila.append(v)
        grupos_.append((cat, g, sid))
    for cat, g, sid in grupos_:
        cuenta[cat] = cuenta.get(cat, 0) + 1
    grupos_.sort(key=lambda t: (ORDEN_ETIQUETA.index(t[0]) if t[0] in ORDEN_ETIQUETA else 99, t[1][0][1], t[1][0][0], t[1][0][2]))
    etiquetas, info = [], []
    for cat, g, sid in grupos_[:tope]:
        x, y, z = g[0]
        nom = estados[sid][0].split(":")[-1]
        etiquetas.append({"x": x + origen[0], "y": y + origen[1], "z": z + origen[2], "n": len(etiquetas) + 1, "color": COLOR_CAT[cat],
                          "celdas": [(a + origen[0], b + origen[1], c + origen[2]) for a, b, c in g]})
        info.append("%s: %s%s en (%d, %d, %d)" % (cat, nom, (" ×%d bloques" % len(g)) if len(g) > 1 else "",
                                                 x + origen[0], y + origen[1], z + origen[2]))
    return resaltar, etiquetas, cuenta, info


# tamaño (ancho, alto) en bloques de los seres más comunes; los de mods sin dato: 0,6 x 1,8
TAM_SER = {"villager": (0.6, 1.95), "zombie": (0.6, 1.95), "skeleton": (0.6, 1.99), "creeper": (0.6, 1.7), "spider": (1.4, 0.9),
           "cave_spider": (0.7, 0.5), "enderman": (0.6, 2.9), "witch": (0.6, 1.95), "pillager": (0.6, 1.95), "vindicator": (0.6, 1.95),
           "iron_golem": (1.4, 2.7), "cow": (0.9, 1.4), "mooshroom": (0.9, 1.4), "sheep": (0.9, 1.3), "pig": (0.9, 0.9),
           "chicken": (0.4, 0.7), "horse": (1.4, 1.6), "donkey": (1.4, 1.5), "mule": (1.4, 1.6), "llama": (0.9, 1.87),
           "wolf": (0.6, 0.85), "cat": (0.6, 0.7), "fox": (0.6, 0.7), "rabbit": (0.4, 0.5), "goat": (0.9, 1.3),
           "camel": (1.7, 2.375), "bee": (0.7, 0.6), "parrot": (0.5, 0.9), "bat": (0.5, 0.9), "armor_stand": (0.5, 1.975),
           "slime": (1.0, 1.0), "frog": (0.5, 0.5), "turtle": (1.2, 0.4), "polar_bear": (1.4, 1.4), "panda": (1.3, 1.25),
           "elephant": (2.4, 3.0), "grizzly_bear": (1.6, 1.8), "deer": (0.9, 1.5), "crow": (0.5, 0.5), "magpie": (0.5, 0.5),
           "warden": (0.9, 2.9), "ravager": (1.95, 2.2), "dragon": (4.0, 3.0), "wither": (0.9, 3.5), "ghast": (4.0, 4.0),
           "piglin": (0.6, 1.95), "zombified_piglin": (0.6, 1.95), "piglin_brute": (0.6, 1.95), "hoglin": (1.4, 1.4),
           "zoglin": (1.4, 1.4), "skeleton_horse": (1.4, 1.6), "zombie_horse": (1.4, 1.6), "wither_skeleton": (0.7, 2.4),
           "drowned": (0.6, 1.95), "husk": (0.6, 1.95), "stray": (0.6, 1.99), "villager_golem": (1.4, 2.7)}
CARAS_PARED = {0: (0, -1, 0), 1: (0, 1, 0), 2: (0, 0, -1), 3: (0, 0, 1), 4: (-1, 0, 0), 5: (1, 0, 0)}


def _cuadro_plano(C, n, w, h, tex, rec, tinte=None):
    """Quad plano centrado en C mirando hacia n (cuadros, marcos), en el orden de vértices del dibujo."""
    n = np.array(n, np.float64)
    arriba = np.array([0.0, 1.0, 0.0]) if abs(n[1]) < 0.5 else np.array([0.0, 0.0, -1.0 if n[1] > 0 else 1.0])
    der = np.cross(-n, arriba)
    der /= max(1e-9, np.linalg.norm(der))
    C = np.array(C, np.float64)
    v = np.array([C - der * w / 2 + arriba * h / 2, C - der * w / 2 - arriba * h / 2, C + der * w / 2 - arriba * h / 2,
                  C + der * w / 2 + arriba * h / 2])
    return {"v": v, "uv": np.array([(0, 0), (0, 16), (16, 16), (16, 0)], np.float64), "tex": tex, "n": n, "tinte": tinte}


def extras_seres(rec, m, x1, z1, x2, z2, y1, y2, colores, mobs=True):
    """Caras extra para dibujar los seres de verdad: cajas del tamaño de cada mob (con su color de tipo), cuadros con su
    dibujo, marcos con el objeto que muestran. Lee las entidades crudas que m.seres() ya cargó."""
    out = []
    blanca = rec.blanca()
    for lista in getattr(m, "chunks_e", {}).values():
        for e in lista:
            pos = e.get("Pos") or None
            if not pos:
                continue
            px, py, pz = float(pos[0]), float(pos[1]), float(pos[2])
            if not (x1 <= px <= x2 + 1 and z1 <= pz <= z2 + 1 and y1 - 1 <= py <= y2 + 2):
                continue
            eid = str(e.get("id", ""))
            corto = eid.split(":")[-1]
            if corto == "painting":
                var = str(e.get("variant") or e.get("Motive") or "minecraft:kebab")
                ns, _, nb = var.rpartition(":")
                d = rec.json("data/%s/painting_variant/%s.json" % (ns or "minecraft", nb)) or {}
                w, h = int(d.get("width", 1)), int(d.get("height", 1))
                aid = str(d.get("asset_id", var))
                an, _, ab = aid.rpartition(":")
                f2d = int(e.get("facing", e.get("Facing", 0)) or 0)  # 2D: 0 sur, 1 oeste, 2 norte, 3 este
                face = CARAS_PARED.get({0: 3, 1: 4, 2: 2, 3: 5}.get(f2d, 3), (0, 0, 1))
                n = np.array(face, np.float64)
                arriba = np.array([0.0, 1.0, 0.0])
                der = np.cross(-n, arriba)
                for i in range(w):
                    for j in range(h):
                        C = np.array([px, py, pz]) + n * 0.04 + der * (i - (w - 1) / 2) + arriba * ((h - 1) / 2 - j)
                        out.append(_cuadro_plano(C, n, 1.0, 1.0, rec.textura_trozo("%s:painting/%s" % (an or "minecraft", ab), i, j, w, h), rec))
            elif corto in ("item_frame", "glow_item_frame"):
                n = np.array(CARAS_PARED.get(int(e.get("Facing", 3)), (0, 0, 1)), np.float64)
                C = np.array([px, py, pz]) + n * 0.01
                out.append(_cuadro_plano(C, n, 0.75, 0.75, rec.textura("minecraft:block/%s" % ("glow_item_frame" if "glow" in corto else "item_frame")), rec))
                it = str((e.get("Item") or {}).get("id", ""))
                if it:
                    ins, _, inb = it.rpartition(":")
                    t = rec.textura("%s:item/%s" % (ins or "minecraft", inb))
                    if t == 0:  # objeto que es un bloque: su textura de bloque
                        t = rec.textura("%s:block/%s" % (ins or "minecraft", inb))
                    out.append(_cuadro_plano(C + n * 0.02, n, 0.5, 0.5, t, rec))
            elif mobs and ("Health" in e or corto == "armor_stand") and corto not in ("item", "experience_orb", "arrow", "trident"):
                tipo = corto if corto in TAM_SER else next((k for k in sorted(TAM_SER, key=len, reverse=True) if k in corto), None)
                w, h = TAM_SER.get(tipo, (0.6, 1.8))
                if (e.get("Age") or 0) < 0 or e.get("IsBaby"):
                    w, h = w * 0.5, h * 0.5
                col = colores(e)
                a = np.array([px - w / 2, py, pz - w / 2])
                b = np.array([px + w / 2, py + h, pz + w / 2])
                for cara in DIRS6:
                    v = np.array(_vertices(cara, a, b), np.float64)
                    nn = np.array(DIRS6[cara], np.float64)
                    out.append({"v": v, "uv": np.array([(0, 0), (0, 16), (16, 16), (16, 0)], np.float64), "tex": blanca, "n": nn,
                                "tinte": col})
    return out


COLOR_TINTE = {"white": 0xF9FFFE, "orange": 0xF9801D, "magenta": 0xC74EBD, "light_blue": 0x3AB3DA, "yellow": 0xFED83D,
               "lime": 0x80C71F, "pink": 0xF38BAA, "gray": 0x474F52, "light_gray": 0x9D9D97, "cyan": 0x169C9C, "purple": 0x8932B8,
               "blue": 0x3C44AA, "brown": 0x835432, "green": 0x5E7C16, "red": 0xB02E26, "black": 0x1D1D21}


def _rgb(c):
    n = COLOR_TINTE.get(c, 0xF9FFFE)
    return np.array([(n >> 16) & 255, (n >> 8) & 255, n & 255], np.float32)


def bandera(rec, base, patrones):
    """La tela del estandarte (40 x 20 píxeles, RGBA) con su color de fondo y sus patrones, como la arma el juego."""
    def capa(nombre):
        img = rec.imagen("minecraft:entity/banner/%s" % nombre)
        if img is None:
            return None
        k = img.shape[1] / 64.0
        return img[int(1 * k):int(41 * k), int(1 * k):int(21 * k)].astype(np.float32)
    fondo = capa("base")
    if fondo is None:
        return None
    gris = fondo[:, :, :3].mean(axis=2, keepdims=True) / 255.0
    out = np.zeros(fondo.shape, np.float32)
    out[:, :, :3] = gris * _rgb(base)
    out[:, :, 3] = fondo[:, :, 3]
    for p in patrones or []:
        nom = str(p.get("pattern", "")).split(":")[-1]
        cp = capa(nom)
        if cp is None or cp.shape != out.shape:
            continue
        a = cp[:, :, 3:4] / 255.0
        out[:, :, :3] = out[:, :, :3] * (1 - a) + (cp[:, :, :3].mean(axis=2, keepdims=True) / 255.0) * _rgb(p.get("color", "white")) * a
    return out.astype(np.uint8)


def extras_estandartes(rec, m, x1, z1, x2, z2, y1, y2):
    """Caras de los estandartes de la zona (de pared o de pie), con su patrón de verdad."""
    out = []
    for e in m.entidades(x1, z1, x2, z2):
        if "banner" not in str(e.get("id", "")):
            continue
        x, y, z = e.get("x"), e.get("y"), e.get("z")
        if not (y1 - 2 <= y <= y2 + 2):
            continue
        nom, pr = m.estado(x, y, z)
        if not nom or "banner" not in nom:
            continue
        base = next((k for k in sorted(COLOR_TINTE, key=len, reverse=True) if nom.startswith(k + "_")), "white")
        tela = bandera(rec, base, e.get("patterns"))
        if tela is None:
            continue
        clave = "ojo:bandera/%s/%s" % (base, repr(e.get("patterns")))
        arriba_t = rec.textura_de(clave + "/1", tela[:20])
        abajo_t = rec.textura_de(clave + "/2", tela[20:40])
        ancho = 0.875
        if "wall_banner" in nom:
            f = np.array(DIRS6.get(pr.get("facing", "north"), (0, 0, -1)), np.float64)
            C = np.array([x + 0.5, y + 0.5, z + 0.5]) - f * 0.44
            centros = (C + np.array([0, 0.45, 0]), C + np.array([0, 0.45 - ancho, 0]))
        else:
            r_ = int(pr.get("rotation", 0)) % 16
            f = np.array({0: (0, 0, 1), 1: (0, 0, 1), 2: (0, 0, 1), 3: (-1, 0, 0), 4: (-1, 0, 0), 5: (-1, 0, 0), 6: (-1, 0, 0),
                          7: (0, 0, -1), 8: (0, 0, -1), 9: (0, 0, -1), 10: (0, 0, -1), 11: (1, 0, 0), 12: (1, 0, 0), 13: (1, 0, 0),
                          14: (1, 0, 0), 15: (0, 0, 1)}[r_], np.float64)
            C = np.array([x + 0.5, y + 0.5, z + 0.5]) - f * 0.08
            centros = (C + np.array([0, 1.6, 0]), C + np.array([0, 1.6 - ancho, 0]))
            out.append(_cuadro_plano(np.array([x + 0.5, y + 1.0, z + 0.5]), f, 0.125, 2.0, rec.textura("minecraft:block/oak_planks"), rec))
        for C_, t in zip(centros, (arriba_t, abajo_t)):
            out.append(_cuadro_plano(C_, f, ancho, ancho, t, rec))
            out.append(_cuadro_plano(C_ - f * 0.01, -f, ancho, ancho, t, rec))  # el revés de la tela
    return out
