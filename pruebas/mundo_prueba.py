# -*- coding: utf-8 -*-
"""Arma un mundo mínimo de Minecraft 1.21 (formato Anvil, 2 x 2 chunks) para probar el Ojo sin el juego.

Qué tiene:
- piedra hasta y=59, tierra hasta y=63 y pasto en y=64;
- un estanque de agua en y=64 (x 20..26, z 4..12);
- una casa de piedra labrada de 7 x 7 (x 4..10, z 4..10) con techo de tablones en y=69,
  la entrada abierta en (7, 65..66, 4) y una antorcha adentro en (7, 65, 7).

Uso: python mundo_prueba.py CARPETA
"""
import gzip
import os
import struct
import sys
import zlib

DATA_VERSION = 3955  # Minecraft Java 1.21.1


# --- NBT (solo los tipos que hacen falta) ---

def _nombre(texto):
    b = texto.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _valor(tipo, v):
    if tipo == 1:   # byte
        return struct.pack(">b", v)
    if tipo == 3:   # int
        return struct.pack(">i", v)
    if tipo == 8:   # texto
        return _nombre(v)
    if tipo == 9:   # lista: (tipo de los elementos, elementos)
        t, items = v
        return bytes([t]) + struct.pack(">i", len(items)) + b"".join(_valor(t, i) for i in items)
    if tipo == 10:  # compuesto: {nombre: (tipo, valor)}
        return b"".join(bytes([t]) + _nombre(k) + _valor(t, x) for k, (t, x) in v.items()) + b"\x00"
    if tipo == 12:  # arreglo de long
        return struct.pack(">i", len(v)) + b"".join(struct.pack(">q", x) for x in v)
    raise ValueError(tipo)


def _empacar(indices, n_paleta):
    """Índices de paleta en longs de 64 bits, como los guarda Minecraft desde la 1.16 (sin partir entre longs)."""
    bits = max(4, (n_paleta - 1).bit_length())
    por = 64 // bits
    longs = []
    for i in range(0, 4096, por):
        w = 0
        for k, v in enumerate(indices[i:i + por]):
            w |= v << (k * bits)
        longs.append(w - (1 << 64) if w >= 1 << 63 else w)
    return longs


# --- El mundo ---

def bloque(x, y, z):
    if y < 60:
        return "stone"
    if y < 64:
        return "dirt"
    if y == 64:
        return "water" if 20 <= x <= 26 and 4 <= z <= 12 else "grass_block"
    if 4 <= x <= 10 and 4 <= z <= 10 and 65 <= y <= 69:
        if y == 69:
            return "oak_planks"
        borde = x in (4, 10) or z in (4, 10)
        entrada = x == 7 and z == 4 and y in (65, 66)
        if borde and not entrada:
            return "cobblestone"
        if (x, y, z) == (7, 65, 7):
            return "torch"
    return "air"


def chunk(cx, cz):
    secciones = []
    for sy in range(3, 5):  # y 48..79; lo demás queda vacío
        paleta, indices = [], []
        for i in range(4096):
            y, z, x = sy * 16 + (i >> 8), cz * 16 + ((i >> 4) & 15), cx * 16 + (i & 15)
            b = bloque(x, y, z)
            if b not in paleta:
                paleta.append(b)
            indices.append(paleta.index(b))
        estados = {"palette": (9, (10, [{"Name": (8, "minecraft:" + p)} for p in paleta]))}
        if len(paleta) > 1:
            estados["data"] = (12, _empacar(indices, len(paleta)))
        secciones.append({"Y": (1, sy), "block_states": (10, estados),
                          "biomes": (10, {"palette": (9, (8, ["minecraft:plains"]))})})
    raiz = {"DataVersion": (3, DATA_VERSION), "xPos": (3, cx), "zPos": (3, cz), "yPos": (3, -4),
            "Status": (8, "minecraft:full"), "sections": (9, (10, secciones))}
    return b"\x0a" + _nombre("") + _valor(10, raiz)


def crear(destino):
    os.makedirs(os.path.join(destino, "region"), exist_ok=True)
    cabecera, cuerpo, sector = bytearray(8192), bytearray(), 2
    for cx in range(2):
        for cz in range(2):
            datos = zlib.compress(chunk(cx, cz))
            registro = struct.pack(">IB", len(datos) + 1, 2) + datos  # 2 = zlib
            registro += b"\x00" * (-len(registro) % 4096)
            n = len(registro) // 4096
            i = (cx + cz * 32) * 4
            cabecera[i:i + 4] = struct.pack(">I", (sector << 8) | n)
            cuerpo += registro
            sector += n
    with open(os.path.join(destino, "region", "r.0.0.mca"), "wb") as f:
        f.write(bytes(cabecera) + bytes(cuerpo))
    datos = {"LevelName": (8, "Ojo test world"), "DataVersion": (3, DATA_VERSION),
             "Version": (10, {"Id": (3, DATA_VERSION), "Name": (8, "1.21.1")}),
             "SpawnX": (3, 7), "SpawnY": (3, 65), "SpawnZ": (3, 7)}
    with open(os.path.join(destino, "level.dat"), "wb") as f:  # como el del juego: NBT comprimido con gzip
        f.write(gzip.compress(b"\x0a" + _nombre("") + _valor(10, {"Data": (10, datos)})))
    return destino


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print("Mundo de prueba en", crear(sys.argv[1]))
