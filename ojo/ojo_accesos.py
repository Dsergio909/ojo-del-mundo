# -*- coding: utf-8 -*-
"""Accesos, hoyos trampa y apoyos del Ojo del mundo (30-oct-2026, pedido de Sergio: «dejas casas sin un acceso natural»,
hoyo con un aldeano atrapado junto a la taberna y un letrero de la cripta flotando).

Todo se calcula leyendo los bloques (de los .mca o de un mundo sintético del banco): nada se cambia.
- construir(m, x1, z1, x2, z2): arreglo denso de clases de bloque (PASA, SOLIDO, LOSA, BLOQUEA, LIQUIDO, ARBOL) por [y, z, x].
- Rejilla: los lugares donde un jugador PUEDE PARARSE (suelo firme y 2 de aire encima) y la caminata entre ellos. Un paso de 1
  bloque de alto como máximo (las losas y las escaleras de abajo valen medio bloque; saltar un bloque se permite, 2 no).
- accesos(): para cada pieza del plano, ¿se llega a su puerta desde la plaza (o desde la red que se dé)? Si no, dónde está el salto.
- hoyos(): huecos de 1x1 o 1x2 y 2 o más de hondo en el suelo del pueblo.
- apoyos(): letreros, estandartes, faroles, antorchas y velas que no tienen en qué apoyarse (flotando)."""
import heapq
import json
import math
import os

import numpy as np

import mapa_aldea as MA

PASA, SOLIDO, LOSA, BLOQUEA, LIQUIDO, DESCONOCIDO, ARBOL = 0, 1, 2, 3, 4, 5, 6
NOMBRE_CLASE = {0: "pasa", 1: "sólido", 2: "losa", 3: "bloquea", 4: "líquido", 5: "?", 6: "árbol"}

# lo que no estorba al caminar ni sostiene a nadie
_PASA = ("grass", "fern", "flower", "tulip", "orchid", "allium", "bluet", "daisy", "poppy", "dandelion", "cornflower",
         "lily", "peony", "lilac", "sunflower", "pitcher", "sapling", "vine", "carpet", "torch", "sign", "banner", "button",
         "pressure_plate", "rail", "ladder", "lantern", "candle", "web", "string", "tripwire", "redstone_wire", "lever",
         "potted_", "flower_pot", "sugar_cane", "bamboo", "dead_bush", "berry", "pink_petals", "glow_lichen", "hanging_roots",
         "spore_blossom", "dripleaf", "door", "trapdoor", "fence_gate", "skull", "_head", "chain", "rope", "end_rod",
         "lightning_rod", "fire", "azalea", "roots", "rose", "bush", "wheat", "carrots", "potatoes", "beetroots",
         "nether_wart", "scaffolding", "petals", "mushroom", "moss_carpet", "leaf_litter", "sprouts", "fungus", "seeds",
         "item_frame", "bell")
_NO_PASA = ("mushroom_block", "mushroom_stem", "snow_block", "powder_snow", "grass_block", "lily_block", "flower_block",
            "_door_frame")
_PLANTAS_TALLO = ("melon_stem", "pumpkin_stem", "attached_melon_stem", "attached_pumpkin_stem")
_AGUA = ("water", "lava", "bubble_column", "seagrass", "kelp", "tall_seagrass")

_CACHE = {}


def clase_bloque(nombre, props=None):
    """Clase de un bloque para caminar: PASA, SOLIDO, LOSA (medio bloque), BLOQUEA (no se pisa ni se cruza), LIQUIDO, ARBOL."""
    k = (nombre, tuple(sorted((props or {}).items())))
    if k in _CACHE:
        return _CACHE[k]
    n = nombre.split(":")[-1].split("[")[0]
    p = props or {}
    if n in MA.AIRE:
        c = PASA
    elif n in _AGUA or n.startswith(("water", "lava")):
        c = LIQUIDO
    elif n.endswith("_leaves") or n.endswith(("_log", "_wood", "_hyphae")) or (n.endswith("_stem") and n not in _PLANTAS_TALLO and "mushroom" not in n):
        c = ARBOL
    elif n in _PLANTAS_TALLO:
        c = PASA
    elif any(q in n for q in _NO_PASA):
        c = SOLIDO
    elif n.endswith("_stairs"):
        c = LOSA if p.get("half", "bottom") == "bottom" else SOLIDO
    elif n.endswith("_slab"):
        c = LOSA if p.get("type", "bottom") == "bottom" else SOLIDO
    elif any(q in n for q in _PASA):
        c = PASA
    elif n.endswith(("_fence", "_wall", "_bars", "_pane")) or n in ("iron_bars", "cactus", "campfire", "soul_campfire") or "glass_pane" in n:
        c = BLOQUEA
    elif n.endswith("_bed"):
        c = LOSA
    else:
        c = SOLIDO
    _CACHE[k] = c
    return c


class Denso:
    """Clases de bloque de una caja, índice [y - y0, z - z1, x - x1]."""

    def __init__(self, x1, z1, x2, z2, y0, y1):
        self.x1, self.z1, self.x2, self.z2, self.y0, self.y1 = x1, z1, x2, z2, y0, y1
        self.ancho, self.fondo, self.alto = x2 - x1 + 1, z2 - z1 + 1, y1 - y0 + 1
        self.cls = np.full((self.alto, self.fondo, self.ancho), PASA, dtype=np.uint8)
        self.sin_chunk = np.zeros((self.fondo, self.ancho), dtype=bool)

    def c(self, x, y, z):
        if not (self.x1 <= x <= self.x2 and self.z1 <= z <= self.z2 and self.y0 <= y <= self.y1):
            return DESCONOCIDO
        if self.sin_chunk[z - self.z1, x - self.x1]:
            return DESCONOCIDO
        return int(self.cls[y - self.y0, z - self.z1, x - self.x1])


def construir(m, x1, z1, x2, z2, y0=40, y1=110):
    """Lee la caja (x1..x2, z1..z2, y0..y1) de un Mundo (o del mundo sintético del banco) y devuelve un Denso."""
    import ojo_del_mundo as O
    d = Denso(x1, z1, x2, z2, y0, y1)
    for cx in range(x1 >> 4, (x2 >> 4) + 1):
        for cz in range(z1 >> 4, (z2 >> 4) + 1):
            ch = m.chunk(cx, cz)
            bx0, bz0 = (cx << 4) - x1, (cz << 4) - z1
            ax0, ax1 = max(0, bx0), min(d.ancho, bx0 + 16)
            az0, az1 = max(0, bz0), min(d.fondo, bz0 + 16)
            if ch is None:
                d.sin_chunk[az0:az1, ax0:ax1] = True
                continue
            for sy, sec in ch[2].items():
                if sy is None or ((sy << 4) + 15 < y0) or ((sy << 4) > y1):
                    continue
                bs = sec.get("block_states") or {}
                pal = bs.get("palette")
                if not pal:
                    continue
                tabla = np.array([clase_bloque(q.get("Name", "air"), q.get("Properties")) for q in pal], dtype=np.uint8)
                idx = O.decodificar_seccion(len(pal), bs.get("data"))
                if idx is None:
                    sec16 = np.full((16, 16, 16), tabla[0], dtype=np.uint8)
                else:
                    sec16 = tabla[np.asarray(idx, dtype=np.int64)].reshape(16, 16, 16)  # [y, z, x]
                ya, yb = max(y0, sy << 4), min(y1, (sy << 4) + 15)
                d.cls[ya - y0:yb - y0 + 1, az0:az1, ax0:ax1] = sec16[ya - (sy << 4):yb - (sy << 4) + 1, az0 - bz0:az1 - bz0, ax0 - bx0:ax1 - bx0]
    return d


class Rejilla:
    """Dónde se puede estar de pie y los pasos entre columnas vecinas.
    nivel = 2 * (y del bloque que se pisa) + 2 si es un bloque entero, + 1 si es medio bloque (losa o escalera de abajo)."""

    def __init__(self, d):
        self.d = d
        c = d.cls
        sup = (c == SOLIDO) | (c == LOSA) | (c == ARBOL)
        libre = (c == PASA)
        arriba1 = np.ones_like(libre)
        arriba2 = np.ones_like(libre)
        arriba1[:-1] = libre[1:]
        arriba2[:-2] = libre[2:]
        pisa = sup & arriba1 & arriba2
        yy, zz, xx = np.nonzero(pisa)
        self.col = {}
        for y, z, x in zip(yy.tolist(), zz.tolist(), xx.tolist()):
            yb = y + d.y0
            self.col.setdefault((x + d.x1, z + d.z1), []).append((2 * yb + 2 - (1 if c[y, z, x] == LOSA else 0), yb))
        for v in self.col.values():
            v.sort()

    def nodos(self, x, z):
        return self.col.get((x, z), [])

    def vecinos(self, nodo, paso=2):
        """Nodos (x, z, nivel, y) a un paso válido: diferencia de nivel de 2 medios bloques como máximo y, si se baja,
        sin techo bajo en la otra columna."""
        x, z, lv, yb = nodo
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            for lv2, yb2 in self.col.get((x + dx, z + dz), ()):
                if abs(lv2 - lv) > paso:
                    continue
                if yb2 < yb and self.d.c(x + dx, yb + 2, z + dz) not in (PASA,):
                    continue  # al bajar, la cabeza pasa por un hueco que no está libre
                yield (x + dx, z + dz, lv2, yb2)

    def alcanzar(self, semillas):
        """Todo lo que se alcanza caminando desde los nodos semilla (x, z, nivel, y). Devuelve el conjunto de nodos."""
        vistos = set(semillas)
        pila = list(semillas)
        while pila:
            n = pila.pop()
            for v in self.vecinos(n):
                if v not in vistos:
                    vistos.add(v)
                    pila.append(v)
        return vistos

    def camino_relajado(self, inicio, meta, max_salto=14):
        """Camino de costo mínimo desde los nodos 'inicio' hasta cualquier nodo de 'meta' dejando pasos imposibles (con costo
        alto). Devuelve la lista de nodos o None."""
        cola, previo, costo = [], {}, {}
        for n in inicio:
            costo[n] = 0
            heapq.heappush(cola, (0, n))
            previo[n] = None
        while cola:
            c0, n = heapq.heappop(cola)
            if c0 > costo.get(n, 1e9):
                continue
            if n in meta:
                ruta = []
                while n is not None:
                    ruta.append(n)
                    n = previo[n]
                return ruta[::-1]
            x, z, lv, yb = n
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                for lv2, yb2 in self.col.get((x + dx, z + dz), ()):
                    dif = abs(lv2 - lv)
                    if dif > max_salto:
                        continue
                    ok = dif <= 2 and not (yb2 < yb and self.d.c(x + dx, yb + 2, z + dz) != PASA)
                    c1 = c0 + 1 + (0 if ok else 25 + 5 * dif)
                    v = (x + dx, z + dz, lv2, yb2)
                    if c1 < costo.get(v, 1e9):
                        costo[v] = c1
                        previo[v] = n
                        heapq.heappush(cola, (c1, v))
                # tapado: pasar por encima de UNA columna sin nada pisable (un muro, una baranda) cuesta 40
                for lv2, yb2 in self.col.get((x + 2 * dx, z + 2 * dz), ()):
                    if abs(lv2 - lv) <= 2:
                        c1 = c0 + 40
                        v = (x + 2 * dx, z + 2 * dz, lv2, yb2)
                        if c1 < costo.get(v, 1e9):
                            costo[v] = c1
                            previo[v] = n
                            heapq.heappush(cola, (c1, v))
        return None


VEC = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}


def semillas_cerca(rej, x, z, radio, nivel=None, tol=4):
    """Nodos de la rejilla a 'radio' o menos de (x, z) y, si se da, con nivel parecido (tol medios bloques)."""
    out = []
    for xx in range(x - radio, x + radio + 1):
        for zz in range(z - radio, z + radio + 1):
            if (xx - x) ** 2 + (zz - z) ** 2 > radio * radio:
                continue
            for lv, yb in rej.nodos(xx, zz):
                if nivel is None or abs(lv - nivel) <= tol:
                    out.append((xx, zz, lv, yb))
    return out


def camino_roto(rej, pts, radio=2):
    """¿Se puede recorrer a pie un camino del plano de punta a punta, quedándose sobre su franja (3 de ancho)? pts = centros
    (x, z) absolutos en orden. Se puede rodear un farol o una banca puestos en el centro. Devuelve None si sí; si no, un dict
    con el primer corte: {i, de, a, salto, texto}: la última posición alcanzada (x, y, z), el sitio siguiente del camino y el
    desnivel en bloques."""
    franja = set()
    for (x, z) in pts:
        for dx in range(-radio, radio + 1):
            for dz in range(-radio, radio + 1):
                franja.add((x + dx, z + dz))
    vec9 = [(a, b) for a in (-1, 0, 1) for b in (-1, 0, 1)]
    mejor = None
    for lv0, yb0 in rej.nodos(*pts[0]):
        ini = (pts[0][0], pts[0][1], lv0, yb0)
        vistos, pila = {ini}, [ini]
        while pila:
            n = pila.pop()
            for v in rej.vecinos(n):
                if (v[0], v[1]) in franja and v not in vistos:
                    vistos.add(v)
                    pila.append(v)
        cols = {(n[0], n[1]) for n in vistos}
        idx = 0
        for i, q in enumerate(pts):
            if any((q[0] + a, q[1] + b) in cols for a, b in vec9):
                idx = i
        if idx >= len(pts) - 1:
            return None
        if mejor is None or idx > mejor[0]:
            mejor = (idx, vistos)
    if mejor is None:
        return {"i": 0, "de": None, "a": None, "texto": "el camino empieza en (%d, %d) donde no hay suelo firme" % pts[0], "salto": 99}
    idx, vistos = mejor
    q = tuple(pts[idx])
    ult = min((n for n in vistos if abs(n[0] - q[0]) <= 1 and abs(n[1] - q[1]) <= 1), key=lambda n: (abs(n[0] - q[0]) + abs(n[1] - q[1]), -n[2]))
    sig = tuple(pts[idx + 1])
    cand = [(abs(lv - ult[2]), x, z, lv, yb) for (x, z) in [(sig[0] + a, sig[1] + b) for a, b in vec9] if (x, z) in franja
            for lv, yb in rej.nodos(x, z) if (x, z, lv, yb) not in vistos]
    if not cand:
        return {"i": idx, "de": (ult[0], ult[3], ult[1]), "a": None, "salto": 99,
                "texto": "el camino se corta en (%d, %d): el siguiente punto (%d, %d) no tiene suelo caminable (¿agua o algo que tapa?)" % (ult[0], ult[1], sig[0], sig[1])}
    cand.sort()
    _, x, z, lv, yb = cand[0]
    dif = abs(lv - ult[2]) / 2.0
    return {"i": idx, "de": (ult[0], ult[3], ult[1]), "a": (x, yb, z), "salto": dif,
            "texto": "el camino tiene un corte entre (%d, %d, y %d) y (%d, %d, y %d): %s" % (
                ult[0], ult[1], ult[3], x, z, yb, ("desnivel de %g bloques sin escalera ni rampa" % dif) if dif > 1 else
                "algo tapa la franja del camino (mueble, seto o muro) o el desnivel es de %g bloque%s" % (dif, "" if dif == 1 else "s"))}


def accesos(m, rej, piezas, plaza=(0, 0), nivel_plaza=138, log=None):
    """Para cada pieza {nombre, caja, puerta=(x, z), mira, red=(x, z) opcional}: ¿hay camino caminable (pasos de 1 bloque como
    máximo) desde su puerta hasta la plaza (o hasta 'red')? Devuelve (avisos, estadística). Cada aviso es un dict con:
    nombre, tipo ('sin_acceso', 'sin_suelo', 'sin_puerta'), x, y, z, texto, salto."""
    import ojo_del_mundo as O
    d = rej.d
    semillas = semillas_cerca(rej, plaza[0], plaza[1], 9, nivel_plaza, 4)
    for p in piezas:
        if p.get("red"):
            semillas += semillas_cerca(rej, p["red"][0], p["red"][1], 0, None)
    alcanzados = rej.alcanzar(semillas) if semillas else set()
    puertas_mundo = {}
    for (x, y, z, n, pr) in m.escanear(d.x1, d.z1, d.x2, d.z2, lambda n: n.endswith("_door"), y_min=d.y0, y_max=d.y1):
        if pr.get("half", "lower") == "lower":
            puertas_mundo[(x, y, z)] = n
    avisos, est = [], {"piezas": 0, "con_acceso": 0, "sin_acceso": 0, "sin_construir": 0, "semillas": len(semillas), "alcanzados": len(alcanzados)}
    for p in piezas:
        c = p["caja"]
        px, pz = p["puerta"]
        cerca = [(max(abs(x - px), abs(z - pz)), x, y, z) for (x, y, z) in puertas_mundo
                 if c["x0"] - 2 <= x <= c["x1"] + 2 and c["z0"] - 2 <= z <= c["z1"] + 2]
        if not cerca:
            est["sin_construir"] += 1
            continue
        est["piezas"] += 1
        if p.get("camino"):  # el camino del plano que sale de su puerta, ¿se puede recorrer entero?
            cr = camino_roto(rej, p["camino"])
            est["caminos"] = est.get("caminos", 0) + 1
            if cr:
                est["caminos_rotos"] = est.get("caminos_rotos", 0) + 1
                ref = cr["de"] or (cerca[0][1], cerca[0][2], cerca[0][3])
                avisos.append({"nombre": p["nombre"], "tipo": "camino_roto", "x": ref[0], "y": ref[1] + 1, "z": ref[2], "salto": cr["salto"],
                               "de": cr["de"], "a": cr["a"], "texto": "%s: %s" % (p["nombre"], cr["texto"])})
        cerca.sort()
        nodos_puerta = []
        for _, x, y, z in cerca:
            for lv, yb in rej.nodos(x, z):
                if abs(yb - (y - 1)) <= 1:
                    nodos_puerta.append((x, z, lv, yb))
        if any(n in alcanzados for n in nodos_puerta):
            est["con_acceso"] += 1
            continue
        est["sin_acceso"] += 1
        _, x0, y0, z0 = cerca[0]
        if not nodos_puerta:
            avisos.append({"nombre": p["nombre"], "tipo": "sin_suelo", "x": x0, "y": y0, "z": z0, "salto": 0,
                           "texto": "%s: la puerta (%d, %d, %d) no tiene suelo firme donde pararse" % (p["nombre"], x0, y0, z0)})
            continue
        ruta = rej.camino_relajado(nodos_puerta, alcanzados) if alcanzados else None
        if not ruta:
            isla = rej.alcanzar(nodos_puerta)
            corte = _corte(m, rej, isla, alcanzados)
            avisos.append({"nombre": p["nombre"], "tipo": "sin_acceso", "x": corte[0][0] if corte else x0, "y": (corte[0][3] + 1) if corte else y0,
                           "z": corte[0][1] if corte else z0, "salto": corte[2] if corte else 99, "saltos": 1,
                           "texto": "%s: sin acceso desde la puerta (%d, %d, %d): su zona (%d lugares) queda cortada de la plaza. %s" % (
                               p["nombre"], x0, y0, z0, len(isla), corte[3] if corte else "No hay ningún lugar caminable cerca de la red.")})
            continue
        saltos = []
        for a, b in zip(ruta, ruta[1:]):
            ok = max(abs(a[0] - b[0]), abs(a[1] - b[1])) == 1 and abs(a[2] - b[2]) <= 2 and not (b[3] < a[3] and d.c(b[0], a[3] + 2, b[1]) != PASA)
            if not ok:
                saltos.append((a, b))
        if not saltos:  # (no debería pasar) ruta válida entera
            continue

        def describe(par):
            a_, b_ = par
            if max(abs(a_[0] - b_[0]), abs(a_[1] - b_[1])) > 1:  # algo tapa el paso entre las dos
                qx, qz = (a_[0] + b_[0]) // 2, (a_[1] + b_[1]) // 2
                tapa = [m.estado(qx, yy, qz)[0] for yy in (max(a_[3], b_[3]) + 1, max(a_[3], b_[3]) + 2)]
                tapa = [t for t in tapa if t and clase_bloque(t, {}) != PASA]
                return "en (%d, %d, %d) %s tapa el paso" % (qx, max(a_[3], b_[3]) + 1, qz, tapa[0] if tapa else "algo")
            return "en (%d, %d)->(%d, %d): de y %d a y %d (%g bloques)" % (a_[0], a_[1], b_[0], b_[1], a_[3], b_[3], abs(a_[2] - b_[2]) / 2.0)
        a, b = saltos[0]
        alto = abs(a[2] - b[2]) / 2.0
        mayor = max(saltos, key=lambda s: abs(s[0][2] - s[1][2]))
        txt = "%s: sin acceso. Desde la puerta (%d, %d, %d) el camino a la plaza tiene %d corte%s; el primero, %s" % (
            p["nombre"], x0, y0, z0, len(saltos), "" if len(saltos) == 1 else "s", describe(saltos[0]))
        if mayor is not saltos[0]:
            txt += "; el mayor, " + describe(mayor)
        avisos.append({"nombre": p["nombre"], "tipo": "sin_acceso", "x": a[0], "y": a[3] + 1, "z": a[1], "salto": alto, "saltos": len(saltos),
                       "texto": txt, "de": (a[0], a[3], a[1]), "a": (b[0], b[3], b[1])})
    return avisos, est


def _corte(m, rej, isla, alcanzados, radio=3):
    """El lugar donde la 'isla' (lo que se alcanza desde la puerta) queda más cerca de la red alcanzada desde la plaza sin poder
    pasar: devuelve (nodo de la isla, nodo de la red, salto en bloques, texto) o None."""
    cols = {}
    for n in alcanzados:
        cols.setdefault((n[0], n[1]), []).append(n)
    mejor = None
    for n in isla:
        for dx in range(-radio, radio + 1):
            for dz in range(-radio, radio + 1):
                if dx == 0 and dz == 0:
                    continue
                for r in cols.get((n[0] + dx, n[1] + dz), ()):
                    dist = max(abs(dx), abs(dz))
                    clave = (dist, abs(r[2] - n[2]))
                    if mejor is None or clave < mejor[0]:
                        mejor = (clave, n, r)
    if mejor is None:
        return None
    _, n, r = mejor
    dif = abs(r[2] - n[2]) / 2.0
    dist = max(abs(r[0] - n[0]), abs(r[1] - n[1]))
    if dist == 1 and dif > 1:
        txt = "El punto más cercano a la red: (%d, %d, %d) y (%d, %d, %d), un salto de %g bloques (hace falta escalera o rampa)." % (n[0], n[3], n[1], r[0], r[3], r[1], dif)
    else:
        tapa = []
        for qx, qz in {(n[0] + (r[0] - n[0]) * k // dist, n[1] + (r[1] - n[1]) * k // dist) for k in range(1, dist)} | {(r[0], r[1])}:
            for yy in (max(n[3], r[3]) + 1, max(n[3], r[3]) + 2, min(n[3], r[3]) + 1):
                nom = m.estado(qx, yy, qz)[0]
                if nom and clase_bloque(nom, {}) not in (PASA,):
                    tapa.append("%s en (%d, %d, %d)" % (nom, qx, yy, qz))
        txt = "El punto más cercano a la red: (%d, %d, %d) y (%d, %d, %d)%s." % (
            n[0], n[3], n[1], r[0], r[3], r[1], (", tapado por " + ", ".join(sorted(set(tapa))[:3])) if tapa else (", salto de %g bloques" % dif))
    return n, r, dif, txt


def hoyos(m, d, cajas_excluir, centro, radio, mo=None, profundidad=2):
    """Huecos de 1x1 o 1x2 columnas, de 'profundidad' o más bloques, en el suelo (sin contar árboles). Un hoyo con agua, dentro
    de una casa o adorno (cajas_excluir) o que ya era así en el mundo natural (mo) no cuenta. Devuelve avisos
    {x, y, z, fondo, borde, n, texto}."""
    import ojo_del_mundo as O
    c = d.cls
    # altura del suelo de cada columna: el bloque más alto que es sólido/losa/bloquea (no árbol, no planta); -999 sin suelo
    firme = (c == SOLIDO) | (c == LOSA) | (c == BLOQUEA)
    liq = (c == LIQUIDO)
    alto = d.alto
    ys = np.arange(alto)[:, None, None]
    top_f = np.where(firme, ys, -1).max(axis=0)
    top_l = np.where(liq, ys, -1).max(axis=0)
    G = np.where(top_f >= 0, top_f + d.y0, -999)
    agua = (top_l > top_f) | (top_f < 0)
    # un bloque de árbol (copa) en medio no cambia el suelo; pero si el hueco está bajo hojas es raro: se deja
    out = []
    X, Z = d.ancho, d.fondo

    def g(x, z):
        if 0 <= x < X and 0 <= z < Z and not d.sin_chunk[z, x] and not agua[z, x]:
            return int(G[z, x])
        return None
    vistos = set()
    zs, xs = np.nonzero(~agua & (G > -999))
    for z, x in zip(zs.tolist(), xs.tolist()):
        if (x, z) in vistos:
            continue
        gv = int(G[z, x])
        vec = [g(x + a, z + b) for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        if all(v is not None and v >= gv + profundidad for v in vec):
            comp = [(x, z)]
        else:
            # 1x2: una vecina a la misma altura (o 1 de diferencia) y todo el contorno de las dos 2+ más alto
            comp = None
            for a, b in ((1, 0), (0, 1)):
                gv2 = g(x + a, z + b)
                if gv2 is None or abs(gv2 - gv) > 1:
                    continue
                fondo = max(gv, gv2)
                # contorno: las 6 columnas que tocan el par por los lados
                lados = [(x - a, z - b), (x + 2 * a, z + 2 * b)] + [(x + s * (b), z + s * (a)) for s in (-1, 1)] + \
                        [(x + a + s * (b), z + b + s * (a)) for s in (-1, 1)]
                bv = [g(q[0], q[1]) for q in lados]
                if all(v is not None and v >= fondo + profundidad for v in bv):
                    comp = [(x, z), (x + a, z + b)]
                    break
        if not comp:
            continue
        for q in comp:
            vistos.add(q)
        wx, wz = comp[0][0] + d.x1, comp[0][1] + d.z1
        if O.dentro(cajas_excluir, wx, wz, 1) or math.hypot(wx - centro[0], wz - centro[1]) > radio:
            continue
        fondo = max(int(G[q[1], q[0]]) for q in comp)
        lados = []
        for q in comp:
            for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                r = (q[0] + a, q[1] + b)
                if r not in comp:
                    v = g(r[0], r[1])
                    if v is not None:
                        lados.append(v)
        borde = min(lados) if lados else fondo + profundidad
        # con escalera de mano (pozos de los cuartos secretos) o tapado a medias por hojas o algo sólido, no es trampa
        salida = False
        for q in comp:
            for yy in range(fondo + 1, borde):
                if d.c(q[0] + d.x1, yy, q[1] + d.z1) != PASA:
                    salida = True
                elif "ladder" in (m.estado(q[0] + d.x1, yy, q[1] + d.z1)[0] or ""):
                    salida = True
        if salida:
            continue
        if mo is not None:  # ya era un hoyo en el terreno natural: no es del pueblo
            nat = []
            for q in comp:
                nat.append(mo.suelo(q[0] + d.x1, q[1] + d.z1)[1])
            lados_n = [mo.suelo(q[0] + d.x1 + a, q[1] + d.z1 + b)[1] for q in comp for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1))
                       if (q[0] + a, q[1] + b) not in comp]
            # (fuera del terreno natural guardado, p. ej. en el Nether o lejos del pueblo, el suelo natural no se conoce: None)
            nat, lados_n = [v for v in nat if v is not None], [v for v in lados_n if v is not None]
            if lados_n and nat and min(lados_n) - max(nat) >= profundidad:
                continue
        out.append({"x": wx, "y": fondo, "z": wz, "fondo": fondo, "borde": borde, "n": len(comp),
                    "texto": "(%d, %d, %d) hoyo de %dx1 y %d de hondo (el suelo de abajo en y %d, el borde en y %d): trampa para quien caiga" % (
                        wx, fondo, wz, len(comp), borde - fondo, fondo, borde)})
    return out


_APOYADOS = ("sign", "banner", "lantern", "torch", "candle", "skull", "_head", "flower_pot", "potted_")
_VEC4 = {"north": (0, 0, -1), "south": (0, 0, 1), "east": (1, 0, 0), "west": (-1, 0, 0)}
# lo que NO sostiene nada (plantas finas y cosas sueltas de Minecraft); todo lo demás (trampillas, alfombras, macetas, muebles de
# mods...) sí
_NO_SOSTIENE = {"short_grass", "tall_grass", "grass", "fern", "large_fern", "dandelion", "poppy", "blue_orchid", "allium", "azure_bluet",
                "red_tulip", "orange_tulip", "white_tulip", "pink_tulip", "oxeye_daisy", "cornflower", "lily_of_the_valley",
                "wither_rose", "sunflower", "lilac", "rose_bush", "peony", "torchflower", "pitcher_plant", "vine", "rail",
                "powered_rail", "detector_rail", "activator_rail", "redstone_wire", "string", "cobweb", "tripwire", "sugar_cane",
                "dead_bush", "sweet_berry_bush", "pink_petals", "glow_lichen", "hanging_roots", "wheat", "carrots", "potatoes",
                "beetroots", "seagrass", "tall_seagrass", "kelp", "kelp_plant", "water", "lava", "fire", "soul_fire", "snow",
                "lily_pad", "moss_carpet", "red_mushroom", "brown_mushroom", "oak_sapling", "spruce_sapling", "birch_sapling",
                "dark_oak_sapling", "jungle_sapling", "acacia_sapling", "cherry_sapling", "light", "structure_void"}


def apoyos(m, x1, z1, x2, z2, y_min=40, y_max=120):
    """Letreros, estandartes, faroles, antorchas, velas, macetas y cabezas (de Minecraft, no de mods) SIN apoyo, o sea flotando.
    Cualquier bloque cuenta como apoyo (trampilla, alfombra, mueble de mod...) menos aire, agua y plantas finas.
    Devuelve [(x, y, z, nombre, motivo)]."""
    def es_apoyo(x, y, z, cadena=False):
        n, p = m.estado(x, y, z)
        if n is None:
            return True  # chunk sin generar: no se sabe
        c = n.split(":")[-1]
        if c in MA.AIRE:
            return False
        if ":" in n and not n.startswith("minecraft:"):
            return True  # bloque de un mod (mueble, adorno): se supone que sostiene
        if cadena and ("chain" in c or c.endswith("bars")):
            return True
        return c not in _NO_SOSTIENE

    out = []
    for x, y, z, n, p in m.escanear(x1, z1, x2, z2, lambda n: ":" not in n and any(k in n for k in _APOYADOS), y_min=y_min, y_max=y_max):
        c = n
        if "wall_hanging_sign" in c:
            ok = es_apoyo(x, y + 1, z, True)
            f = p.get("facing")
            if not ok and f:
                dx, _, dz = _VEC4[f]
                ok = es_apoyo(x + dz, y, z + dx) or es_apoyo(x - dz, y, z - dx)
            motivo = "letrero colgante de pared sin muro a los lados ni nada arriba"
        elif "hanging_sign" in c:
            ok, motivo = es_apoyo(x, y + 1, z, True), "letrero colgante sin nada arriba"
        elif c.endswith(("wall_sign", "wall_banner", "wall_torch", "wall_head", "wall_skull")) or "wall_" in c and c.endswith(("sign", "banner", "torch")):
            f = p.get("facing")
            if f not in _VEC4:
                continue
            dx, _, dz = _VEC4[f]
            ok, motivo = es_apoyo(x - dx, y, z - dz), "pegado a una pared (mira al %s) pero detrás no hay bloque" % f
        elif "lantern" in c:
            if p.get("hanging") == "true":
                ok, motivo = es_apoyo(x, y + 1, z, True), "farol colgando de nada"
            else:
                ok, motivo = es_apoyo(x, y - 1, z), "farol de pie sobre aire"
        elif c.endswith(("_sign", "_banner", "_torch", "_candle", "candle", "_skull", "_head")) or c.startswith(("potted_", "flower_pot")) or c == "torch":
            ok, motivo = es_apoyo(x, y - 1, z), "sin bloque debajo"
        else:
            continue
        if not ok:
            out.append((x, y, z, n.split(":")[-1], motivo))
    return out


# --- Encierros (30-oct-2026, Sergio murió en el muelle de tablones de (-2115, 62, 365): «hay pedazos del pueblo que uno queda
# encerrado»). Dentro de la zona segura se juega en modo aventura: no se puede picar ni poner bloques, así que un sitio al que se
# CAE pero del que no se puede SUBIR es una trampa. Se arma un grafo dirigido de lugares donde se está de pie (y superficie del
# agua), con: pasos de ±1 bloque (como Rejilla), caídas de cualquier altura (solo hacia abajo), escaleras de mano, enredaderas y
# andamios (suben) y el agua (se nada; se sale a un bloque que esté como mucho 1 más arriba). Encierro = lo que se alcanza desde la
# plaza y desde donde ya no se vuelve a la plaza.
_TREPA = ("ladder", "vine", "scaffolding", "twisting_vines", "weeping_vines", "cave_vines")


def _trepa(nombre):
    n = nombre.split(":")[-1]
    return any(t in n for t in _TREPA) and "trapdoor" not in n


def construir_trepa(m, d):
    """Arreglo booleano [y, z, x] de las celdas por las que se trepa (escaleras de mano, enredaderas, andamios)."""
    t = np.zeros_like(d.cls, dtype=bool)
    for (x, y, z, n, _p) in m.escanear(d.x1, d.z1, d.x2, d.z2, _trepa, y_min=d.y0, y_max=d.y1):
        t[y - d.y0, z - d.z1, x - d.x1] = True
    return t


def encierros(m, d, centro, radio, nivel_plaza=138, trepa=None, radio_plaza=9, min_celdas=1):
    """Zonas caminables a las que se cae desde la plaza y de las que no se vuelve. d = construir(...) que cubra centro ± radio.
    Devuelve (lista de encierros, estadística). Cada encierro: celdas (x, z), y_min/y_max, bbox, boca (desde dónde se cae), muestra."""
    cx0, cz0 = centro
    trepa = construir_trepa(m, d) if trepa is None else trepa
    c = d.cls
    rej = Rejilla(d)
    dentro = lambda x, z: (x - cx0) ** 2 + (z - cz0) ** 2 <= radio * radio  # noqa: E731
    # nodos: (x, z, nivel, y) de pie; el agua es (x, z, nivel, y, "a") con nivel = 2 * y_agua + 2
    nodos = set()
    for (x, z), v in rej.col.items():
        if dentro(x, z):
            for lv, yb in v:
                nodos.add((x, z, lv, yb))
    liq = (c == LIQUIDO)
    libre = (c == PASA)
    sup_agua = np.zeros_like(liq)
    sup_agua[:-1] = liq[:-1] & libre[1:]
    yy, zz, xx = np.nonzero(sup_agua)
    agua = {}
    for y, z, x in zip(yy.tolist(), zz.tolist(), xx.tolist()):
        X, Z, Y = x + d.x1, z + d.z1, y + d.y0
        if dentro(X, Z):
            agua.setdefault((X, Z), []).append(Y)
    for (X, Z), ys in agua.items():
        for Y in ys:
            nodos.add((X, Z, 2 * Y + 2, Y, "a"))
    por_col = {}
    for n in nodos:
        por_col.setdefault((n[0], n[1]), []).append(n)
    for v in por_col.values():
        v.sort(key=lambda n: n[2])

    def cl(x, y, z):
        return d.c(x, y, z)

    def tr(x, y, z):
        if not (d.x1 <= x <= d.x2 and d.z1 <= z <= d.z2 and d.y0 <= y <= d.y1):
            return False
        return bool(trepa[y - d.y0, z - d.z1, x - d.x1])

    def salidas(n):
        x, z, lv, yb = n[:4]
        es_agua = len(n) > 4
        pies = yb + 1 if not es_agua else yb  # en el agua, la cabeza va en y_agua + 1
        out = []
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (x + dx, z + dz)
            for m2 in por_col.get(q, ()):
                lv2 = m2[2]
                if lv2 - lv > 2:
                    continue  # más de un bloque hacia arriba: no se sube
                if lv2 >= lv - 2:
                    if lv2 < lv and cl(q[0], yb + 2, q[1]) != PASA and not es_agua:
                        continue
                    out.append(m2)
                    continue
                # caída: la columna vecina tiene que estar libre desde la altura de los pies hasta el lugar donde cae
                y_dest = m2[3] + (0 if len(m2) > 4 else 1)  # pies en destino
                if all(cl(q[0], yy_, q[1]) in (PASA, LIQUIDO) for yy_ in range(y_dest, pies + 2)):
                    mas_alto = max((k for k in por_col.get(q, ()) if k[2] < lv - 2 and
                                    all(cl(q[0], yy_, q[1]) in (PASA, LIQUIDO) for yy_ in range(k[3] + (0 if len(k) > 4 else 1), pies + 2))),
                                   key=lambda k: k[2], default=None)
                    if mas_alto is m2:
                        out.append(m2)
        # trepar: desde una celda de escalera de mano a la altura de los pies (o una al lado), se puede subir por toda la columna
        # y bajarse a cualquier lugar de pie junto a ella o encima
        for (tx, tz) in ((x, z), (x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
            if not tr(tx, pies, tz):
                continue
            top = pies
            while tr(tx, top + 1, tz):
                top += 1
            bot = pies
            while tr(tx, bot - 1, tz):
                bot -= 1
            for (ux, uz) in ((tx, tz), (tx + 1, tz), (tx - 1, tz), (tx, tz + 1), (tx, tz - 1)):
                for m2 in por_col.get((ux, uz), ()):
                    p2 = m2[3] + (0 if len(m2) > 4 else 1)
                    if bot - 1 <= p2 <= top + 2:  # arriba de la escalera se salta 1 bloque más (así se sale por una trampilla)
                        out.append(m2)
        return out

    sem = [n for n in nodos if (n[0] - cx0) ** 2 + (n[1] - cz0) ** 2 <= radio_plaza ** 2 and abs(n[2] - nivel_plaza) <= 4 and len(n) == 4]
    if not sem:
        return [], {"semillas": 0}
    ady, inv = {}, {}
    adelante = set(sem)
    pila = list(sem)
    while pila:
        n = pila.pop()
        s = salidas(n)
        ady[n] = s
        for v in s:
            inv.setdefault(v, []).append(n)
            if v not in adelante:
                adelante.add(v)
                pila.append(v)
    # hacia atrás: desde qué nodos (de los alcanzados) se llega a la plaza
    vuelve = set(sem)
    pila = list(sem)
    while pila:
        n = pila.pop()
        for u in inv.get(n, ()):
            if u not in vuelve:
                vuelve.add(u)
                pila.append(u)
    trampa = adelante - vuelve
    # grupos: nodos atrapados unidos por sus propias salidas (en las dos direcciones)
    grupos, visto = [], set()
    for n in trampa:
        if n in visto:
            continue
        g, pila = [], [n]
        visto.add(n)
        while pila:
            a = pila.pop()
            g.append(a)
            for b in ady.get(a, []) + inv.get(a, []):
                if b in trampa and b not in visto:
                    visto.add(b)
                    pila.append(b)
        celdas = {(a[0], a[1]) for a in g}
        if len(celdas) < min_celdas:
            continue
        boca = [u for a in g for u in inv.get(a, []) if u in vuelve]
        xs, zs, ys = [a[0] for a in g], [a[1] for a in g], [a[3] for a in g]
        grupos.append({"celdas": len(celdas), "nodos": len(g), "agua": sum(1 for a in g if len(a) > 4),
                       "x0": min(xs), "x1": max(xs), "z0": min(zs), "z1": max(zs), "y_min": min(ys), "y_max": max(ys),
                       "muestra": (g[0][0], g[0][3] + 1, g[0][1]), "lista": sorted({(a[0], a[3], a[1]) for a in g}),
                       "boca": sorted({(u[0], u[3] + 1, u[1]) for u in boca})[:6]})
    grupos.sort(key=lambda e: -e["celdas"])
    return grupos, {"semillas": len(sem), "alcanzados": len(adelante), "vuelven": len(vuelve), "atrapados": len(trampa)}
