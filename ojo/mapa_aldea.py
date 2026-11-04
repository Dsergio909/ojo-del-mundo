#!/usr/bin/env python3
"""Generador de mapa cenital PNG desde archivos de región Anvil (.mca) de Minecraft 1.21.1."""

import argparse
import gzip
import io
import json
import math
import os
import struct
import zlib

# Bloques transparentes/aire ignorados al buscar la superficie
AIRE = {
    "air",
    "cave_air",
    "void_air",
    "light",
    "barrier",
    "structure_void",
}

# Colores base para los 16 tintes de Minecraft
COLORES_TINTE = {
    "white": (235, 235, 235),
    "orange": (235, 125, 35),
    "magenta": (190, 70, 180),
    "light_blue": (95, 175, 220),
    "yellow": (240, 200, 45),
    "lime": (115, 195, 40),
    "pink": (235, 145, 175),
    "gray": (70, 75, 78),
    "light_gray": (150, 150, 145),
    "cyan": (25, 145, 155),
    "purple": (130, 50, 180),
    "blue": (50, 65, 175),
    "brown": (115, 75, 45),
    "green": (85, 115, 35),
    "red": (175, 45, 40),
    "black": (28, 28, 32),
}

# Tonos de madera por especie (tablones y troncos)
MADERAS = {
    "oak": ((162, 130, 78), (108, 86, 52)),
    "spruce": ((114, 84, 48), (60, 42, 24)),
    "birch": ((192, 175, 121), (210, 205, 195)),
    "jungle": ((160, 115, 80), (88, 68, 38)),
    "acacia": ((168, 90, 50), (105, 98, 90)),
    "dark_oak": ((66, 43, 20), (52, 38, 22)),
    "mangrove": ((117, 54, 48), (84, 66, 42)),
    "cherry": ((226, 178, 172), (58, 36, 44)),
    "bamboo": ((194, 175, 82), (140, 160, 60)),
    "crimson": ((101, 48, 70), (85, 35, 55)),
    "warped": ((43, 104, 99), (35, 85, 80)),
}


def _construir_diccionario_colores():
    """Construye un diccionario amplio de colores RGB por nombre de bloque."""
    col = {
        # Pasto y vegetación verde
        "grass_block": (105, 165, 65),
        "short_grass": (95, 155, 55),
        "grass": (95, 155, 55),
        "tall_grass": (90, 150, 52),
        "fern": (85, 145, 50),
        "large_fern": (82, 140, 48),
        "moss_block": (90, 140, 50),
        "moss_carpet": (90, 140, 50),
        "podzol": (115, 85, 50),
        "mycelium": (115, 105, 115),
        "sugar_cane": (115, 185, 70),
        "cactus": (65, 135, 45),
        "bamboo": (95, 165, 50),
        "vine": (50, 110, 40),
        "glow_lichen": (95, 130, 110),
        "lily_pad": (35, 120, 45),
        # Hojas (verde oscuro)
        "oak_leaves": (45, 105, 35),
        "spruce_leaves": (35, 80, 45),
        "birch_leaves": (65, 115, 50),
        "jungle_leaves": (42, 110, 32),
        "acacia_leaves": (48, 102, 35),
        "dark_oak_leaves": (38, 92, 28),
        "mangrove_leaves": (50, 108, 36),
        "azalea_leaves": (52, 108, 38),
        "flowering_azalea_leaves": (75, 115, 65),
        "cherry_leaves": (235, 165, 195),
        # Agua (azul)
        "water": (45, 95, 215),
        "flowing_water": (45, 95, 215),
        "bubble_column": (55, 105, 225),
        "kelp": (40, 110, 165),
        "kelp_plant": (40, 110, 165),
        "seagrass": (42, 105, 175),
        "tall_seagrass": (42, 105, 175),
        # Piedra (gris)
        "stone": (125, 125, 125),
        "infested_stone": (125, 125, 125),
        "stone_stairs": (125, 125, 125),
        "stone_slab": (125, 125, 125),
        "smooth_stone": (158, 158, 158),
        "smooth_stone_slab": (158, 158, 158),
        "andesite": (132, 132, 132),
        "polished_andesite": (136, 136, 136),
        "andesite_stairs": (132, 132, 132),
        "andesite_slab": (132, 132, 132),
        "andesite_wall": (132, 132, 132),
        "deepslate": (85, 85, 90),
        "polished_deepslate": (88, 88, 94),
        "deepslate_bricks": (82, 82, 88),
        "deepslate_tiles": (72, 72, 78),
        "tuff": (108, 110, 102),
        "bedrock": (55, 55, 55),
        # Ladrillos de piedra (gris claro)
        "stone_bricks": (150, 150, 150),
        "cracked_stone_bricks": (146, 146, 146),
        "chiseled_stone_bricks": (152, 152, 152),
        "stone_brick_stairs": (150, 150, 150),
        "stone_brick_slab": (150, 150, 150),
        "stone_brick_wall": (150, 150, 150),
        "infested_stone_bricks": (150, 150, 150),
        "mossy_stone_bricks": (130, 145, 125),
        "mossy_stone_brick_stairs": (130, 145, 125),
        "mossy_stone_brick_slab": (130, 145, 125),
        "mossy_stone_brick_wall": (130, 145, 125),
        # Cobblestone (gris medio)
        "cobblestone": (110, 110, 110),
        "cobblestone_stairs": (110, 110, 110),
        "cobblestone_slab": (110, 110, 110),
        "cobblestone_wall": (110, 110, 110),
        "infested_cobblestone": (110, 110, 110),
        "mossy_cobblestone": (95, 115, 95),
        "mossy_cobblestone_stairs": (95, 115, 95),
        "mossy_cobblestone_slab": (95, 115, 95),
        "mossy_cobblestone_wall": (95, 115, 95),
        "cobbled_deepslate": (75, 75, 80),
        # Grava (gris rosado), granito y diorita
        "gravel": (145, 132, 132),
        "suspicious_gravel": (145, 132, 132),
        "granite": (155, 110, 95),
        "polished_granite": (160, 114, 98),
        "granite_stairs": (155, 110, 95),
        "granite_slab": (155, 110, 95),
        "granite_wall": (155, 110, 95),
        "diorite": (190, 190, 192),
        "polished_diorite": (195, 195, 198),
        "diorite_stairs": (190, 190, 192),
        "diorite_slab": (190, 190, 192),
        "diorite_wall": (190, 190, 192),
        # Tierra y caminos (café)
        "dirt": (134, 96, 67),
        "coarse_dirt": (122, 86, 58),
        "rooted_dirt": (130, 92, 64),
        "dirt_path": (158, 125, 80),
        "grass_path": (158, 125, 80),
        "farmland": (115, 75, 45),
        "mud": (65, 60, 65),
        "packed_mud": (140, 108, 82),
        "mud_bricks": (142, 110, 85),
        "mud_brick_stairs": (142, 110, 85),
        "mud_brick_slab": (142, 110, 85),
        "mud_brick_wall": (142, 110, 85),
        "clay": (158, 164, 176),
        "soul_sand": (85, 65, 52),
        "soul_soil": (78, 58, 46),
        # Ladrillos de arcilla (rojizo)
        "bricks": (155, 75, 60),
        "brick_stairs": (155, 75, 60),
        "brick_slab": (155, 75, 60),
        "brick_wall": (155, 75, 60),
        "flower_pot": (155, 75, 60),
        "decorated_pot": (155, 75, 60),
        "terracotta": (152, 94, 68),
        "nether_bricks": (48, 24, 28),
        "red_nether_bricks": (75, 18, 22),
        # Bloques de aldea y utilidades de madera
        "crafting_table": (145, 105, 62),
        "cartography_table": (135, 98, 58),
        "fletching_table": (180, 162, 110),
        "smithing_table": (75, 55, 48),
        "loom": (155, 122, 78),
        "barrel": (138, 102, 60),
        "chest": (162, 120, 52),
        "trapped_chest": (162, 120, 52),
        "bookshelf": (150, 115, 68),
        "chiseled_bookshelf": (150, 115, 68),
        "lectern": (155, 120, 72),
        "composter": (130, 92, 50),
        "note_block": (125, 85, 55),
        "jukebox": (125, 85, 55),
        "beehive": (175, 140, 82),
        "bee_nest": (185, 150, 65),
        "ladder": (150, 118, 68),
        "scaffolding": (185, 160, 95),
        "hay_block": (195, 165, 45),
        "bell": (235, 205, 65),
        # Flores (colores vivos) y cultivos
        "dandelion": (250, 225, 35),
        "sunflower": (250, 215, 25),
        "poppy": (230, 35, 35),
        "rose_bush": (220, 30, 35),
        "red_tulip": (225, 40, 40),
        "orange_tulip": (245, 130, 30),
        "white_tulip": (240, 245, 240),
        "pink_tulip": (240, 145, 190),
        "blue_orchid": (55, 155, 245),
        "cornflower": (55, 105, 235),
        "allium": (185, 105, 230),
        "lilac": (195, 135, 215),
        "peony": (240, 155, 200),
        "pink_petals": (240, 155, 195),
        "azure_bluet": (230, 235, 240),
        "oxeye_daisy": (235, 240, 230),
        "lily_of_the_valley": (240, 245, 240),
        "wither_rose": (35, 30, 35),
        "torchflower": (245, 140, 30),
        "pitcher_plant": (95, 145, 210),
        "sweet_berry_bush": (65, 125, 55),
        "wheat": (210, 185, 65),
        "carrots": (115, 175, 55),
        "potatoes": (95, 165, 50),
        "beetroots": (145, 65, 60),
        "pumpkin": (215, 125, 30),
        "carved_pumpkin": (215, 125, 30),
        "melon": (125, 165, 40),
        "attached_pumpkin_stem": (140, 165, 60),
        "attached_melon_stem": (140, 165, 60),
        "pumpkin_stem": (140, 165, 60),
        "melon_stem": (140, 165, 60),
        # Lava y fuego (naranja)
        "lava": (245, 105, 20),
        "flowing_lava": (245, 105, 20),
        "magma_block": (210, 80, 25),
        "fire": (250, 130, 25),
        "campfire": (230, 115, 35),
        "soul_fire": (65, 200, 215),
        "soul_campfire": (65, 200, 215),
        # Arena (beige)
        "sand": (220, 210, 160),
        "suspicious_sand": (220, 210, 160),
        "sandstone": (215, 202, 150),
        "sandstone_stairs": (215, 202, 150),
        "sandstone_slab": (215, 202, 150),
        "sandstone_wall": (215, 202, 150),
        "smooth_sandstone": (220, 206, 155),
        "smooth_sandstone_stairs": (220, 206, 155),
        "smooth_sandstone_slab": (220, 206, 155),
        "cut_sandstone": (218, 205, 152),
        "cut_sandstone_slab": (218, 205, 152),
        "chiseled_sandstone": (215, 202, 150),
        "red_sand": (190, 102, 42),
        "red_sandstone": (185, 98, 38),
        "red_sandstone_stairs": (185, 98, 38),
        "red_sandstone_slab": (185, 98, 38),
        "red_sandstone_wall": (185, 98, 38),
        # Nieve (blanco) y hielo
        "snow": (245, 250, 252),
        "snow_block": (245, 250, 252),
        "powder_snow": (242, 248, 250),
        "ice": (150, 190, 245),
        "frosted_ice": (150, 190, 245),
        "packed_ice": (135, 175, 240),
        "blue_ice": (110, 160, 245),
        "quartz_block": (232, 228, 222),
        "quartz_stairs": (232, 228, 222),
        "quartz_slab": (232, 228, 222),
        "calcite": (225, 225, 220),
        # Mena de carbón (NEGRO) y otros minerales/metales
        "coal_ore": (12, 12, 12),
        "deepslate_coal_ore": (10, 10, 10),
        "coal_block": (8, 8, 8),
        "obsidian": (22, 16, 30),
        "crying_obsidian": (32, 18, 48),
        "blackstone": (35, 32, 38),
        "basalt": (65, 65, 70),
        "iron_ore": (155, 140, 130),
        "deepslate_iron_ore": (135, 120, 112),
        "raw_iron_block": (165, 145, 130),
        "iron_block": (200, 200, 205),
        "iron_bars": (175, 175, 180),
        "iron_door": (190, 190, 195),
        "iron_trapdoor": (190, 190, 195),
        "anvil": (80, 80, 85),
        "chipped_anvil": (80, 80, 85),
        "damaged_anvil": (80, 80, 85),
        "cauldron": (75, 75, 80),
        "water_cauldron": (65, 85, 150),
        "lava_cauldron": (180, 85, 35),
        "chain": (70, 72, 78),
        "hopper": (85, 85, 90),
        "furnace": (118, 118, 118),
        "blast_furnace": (110, 110, 115),
        "smoker": (112, 105, 98),
        "stonecutter": (130, 125, 120),
        "grindstone": (135, 130, 125),
        "brewing_stand": (160, 135, 75),
        "copper_ore": (175, 110, 85),
        "deepslate_copper_ore": (155, 95, 75),
        "copper_block": (190, 108, 78),
        "gold_ore": (225, 195, 70),
        "deepslate_gold_ore": (205, 175, 60),
        "gold_block": (245, 210, 50),
        "diamond_ore": (95, 225, 220),
        "deepslate_diamond_ore": (80, 205, 200),
        "diamond_block": (90, 235, 230),
        "emerald_ore": (45, 205, 85),
        "deepslate_emerald_ore": (40, 185, 75),
        "emerald_block": (35, 215, 80),
        "lapis_ore": (45, 80, 185),
        "deepslate_lapis_ore": (40, 70, 165),
        "lapis_block": (35, 70, 180),
        "redstone_ore": (180, 40, 40),
        "deepslate_redstone_ore": (160, 35, 35),
        "redstone_block": (195, 25, 25),
        "redstone_wire": (175, 25, 25),
        # Farolas / linternas / antorchas (amarillo intenso)
        "lantern": (255, 230, 35),
        "torch": (255, 235, 45),
        "wall_torch": (255, 235, 45),
        "glowstone": (255, 225, 65),
        "shroomlight": (250, 190, 60),
        "jack_o_lantern": (255, 215, 40),
        "ochre_froglight": (255, 235, 95),
        "verdant_froglight": (210, 250, 180),
        "pearlescent_froglight": (245, 210, 235),
        "end_rod": (245, 240, 210),
        "redstone_lamp": (215, 165, 75),
        "sea_lantern": (175, 235, 225),
        "soul_lantern": (100, 225, 235),
        "soul_torch": (100, 225, 235),
        "soul_wall_torch": (100, 225, 235),
        # Vidrio
        "glass": (195, 225, 235),
        "glass_pane": (195, 225, 235),
        "tinted_glass": (65, 55, 70),
    }

    # Generar variantes de madera por especie
    sufijos_tablones = (
        "planks",
        "stairs",
        "slab",
        "fence",
        "fence_gate",
        "door",
        "trapdoor",
        "pressure_plate",
        "button",
        "sign",
        "wall_sign",
        "hanging_sign",
        "wall_hanging_sign",
    )
    for esp, (c_planks, c_log) in MADERAS.items():
        for suf in sufijos_tablones:
            col[f"{esp}_{suf}"] = c_planks
        col[f"{esp}_log"] = c_log
        col[f"{esp}_wood"] = c_log
        col[f"{esp}_stem"] = c_log
        col[f"{esp}_hyphae"] = c_log
        col[f"stripped_{esp}_log"] = c_planks
        col[f"stripped_{esp}_wood"] = c_planks
        col[f"stripped_{esp}_stem"] = c_planks
        col[f"stripped_{esp}_hyphae"] = c_planks

    # Generar lana, terracota, concreto, camas, alfombras y vidrio por color
    for nombre_color, (r, g, b) in COLORES_TINTE.items():
        for suf in (
            "wool",
            "carpet",
            "bed",
            "concrete",
            "concrete_powder",
            "glazed_terracotta",
            "banner",
            "wall_banner",
            "stained_glass",
            "stained_glass_pane",
            "shulker_box",
            "candle",
        ):
            col[f"{nombre_color}_{suf}"] = (r, g, b)
        # La terracota teñida tiene un tono más cálido/terroso
        col[f"{nombre_color}_terracotta"] = (
            (r * 2 + 152) // 3,
            (g * 2 + 94) // 3,
            (b * 2 + 68) // 3,
        )

    return col


COLORES_BLOQUE = _construir_diccionario_colores()
_CACHE_COLOR_NOMBRE = {}


def color_de_bloque(nombre):
    """Obtiene el color RGB base para un nombre de bloque (sin 'minecraft:')."""
    if nombre in COLORES_BLOQUE:
        return COLORES_BLOQUE[nombre]
    if nombre in _CACHE_COLOR_NOMBRE:
        return _CACHE_COLOR_NOMBRE[nombre]

    # Búsqueda por palabras clave para bloques no listados explícitamente
    c = None
    if "coal" in nombre:
        c = (12, 12, 12)
    elif any(k in nombre for k in ("lantern", "torch", "glowstone", "froglight", "shroomlight")):
        c = (255, 230, 35)
    elif any(k in nombre for k in ("lava", "magma", "fire")):
        c = (245, 105, 20)
    elif any(k in nombre for k in ("water", "kelp", "seagrass", "bubble_column")):
        c = (45, 95, 215)
    elif "snow" in nombre:
        c = (245, 250, 252)
    elif "ice" in nombre:
        c = (145, 185, 245)
    elif "sand" in nombre:
        c = (190, 102, 42) if "red" in nombre else (220, 210, 160)
    elif any(k in nombre for k in ("leaves", "vine", "azalea", "lily_pad")):
        c = (45, 105, 35)
    elif any(k in nombre for k in ("grass", "fern", "moss", "bamboo", "kelp")):
        c = (100, 160, 60)
    else:
        # Verificar si contiene un color de tinte conocido
        for col_key, rgb in COLORES_TINTE.items():
            if nombre.startswith(col_key + "_"):
                if "terracotta" in nombre and "glazed" not in nombre:
                    c = ((rgb[0] * 2 + 152) // 3, (rgb[1] * 2 + 94) // 3, (rgb[2] * 2 + 68) // 3)
                else:
                    c = rgb
                break

    if c is None:
        if "terracotta" in nombre:
            c = (152, 94, 68)
        elif any(k in nombre for k in ("wool", "carpet", "bed")):
            c = (230, 230, 230)
        elif "glass" in nombre:
            c = (195, 225, 235)
        elif "brick" in nombre:
            c = (150, 150, 150) if ("stone" in nombre or "deepslate" in nombre) else (155, 75, 60)
        elif "cobble" in nombre:
            c = (110, 110, 110)
        elif any(k in nombre for k in ("stone", "deepslate", "andesite", "tuff", "basalt", "ore")):
            c = (125, 125, 125)
        elif any(k in nombre for k in ("gravel", "granite")):
            c = (145, 132, 132)
        elif "diorite" in nombre or "quartz" in nombre:
            c = (210, 210, 210)
        elif any(k in nombre for k in ("dirt", "mud", "podzol", "farmland", "path")):
            c = (134, 96, 67)
        elif any(
            k in nombre
            for k in (
                "wood",
                "planks",
                "log",
                "stem",
                "hyphae",
                "stairs",
                "slab",
                "fence",
                "door",
                "trapdoor",
                "sign",
                "chest",
                "barrel",
                "table",
            )
        ):
            c = (152, 118, 72)
        elif any(k in nombre for k in ("flower", "tulip", "rose", "daisy", "orchid", "allium", "lilac")):
            c = (235, 95, 120)
        else:
            # Gris determinista por hash CRC32 del nombre
            g = 80 + (zlib.crc32(nombre.encode("utf-8")) % 110)
            c = (g, g, g)

    _CACHE_COLOR_NOMBRE[nombre] = c
    return c


# --- Lector NBT propio (big-endian, tipos 0-12) ---


def _leer_payload_nbt(buf, pos, tag_type):
    """Lee el payload de un tag NBT en big-endian desde buf[pos:]."""
    if tag_type == 1:  # TAG_Byte
        return struct.unpack_from(">b", buf, pos)[0], pos + 1
    if tag_type == 2:  # TAG_Short
        return struct.unpack_from(">h", buf, pos)[0], pos + 2
    if tag_type == 3:  # TAG_Int
        return struct.unpack_from(">i", buf, pos)[0], pos + 4
    if tag_type == 4:  # TAG_Long
        return struct.unpack_from(">q", buf, pos)[0], pos + 8
    if tag_type == 5:  # TAG_Float
        return struct.unpack_from(">f", buf, pos)[0], pos + 4
    if tag_type == 6:  # TAG_Double
        return struct.unpack_from(">d", buf, pos)[0], pos + 8
    if tag_type == 7:  # TAG_Byte_Array
        n = struct.unpack_from(">i", buf, pos)[0]
        pos += 4
        return buf[pos : pos + n], pos + n
    if tag_type == 8:  # TAG_String
        n = struct.unpack_from(">H", buf, pos)[0]
        pos += 2
        return bytes(buf[pos : pos + n]).decode("utf-8", errors="replace"), pos + n
    if tag_type == 9:  # TAG_List
        elem_type = buf[pos]
        n = struct.unpack_from(">i", buf, pos + 1)[0]
        pos += 5
        if n <= 0:
            return [], pos
        lista = [None] * n
        for i in range(n):
            lista[i], pos = _leer_payload_nbt(buf, pos, elem_type)
        return lista, pos
    if tag_type == 10:  # TAG_Compound
        comp = {}
        while True:
            t = buf[pos]
            pos += 1
            if t == 0:  # TAG_End
                break
            name_len = struct.unpack_from(">H", buf, pos)[0]
            pos += 2
            name = bytes(buf[pos : pos + name_len]).decode("utf-8", errors="replace")
            pos += name_len
            comp[name], pos = _leer_payload_nbt(buf, pos, t)
        return comp, pos
    if tag_type == 11:  # TAG_Int_Array
        n = struct.unpack_from(">i", buf, pos)[0]
        pos += 4
        return struct.unpack_from(f">{n}i", buf, pos), pos + 4 * n
    if tag_type == 12:  # TAG_Long_Array (unsigned 64-bit para facilitar shifts)
        n = struct.unpack_from(">i", buf, pos)[0]
        pos += 4
        return struct.unpack_from(f">{n}Q", buf, pos), pos + 8 * n
    raise ValueError(f"Tipo NBT desconocido: {tag_type}")


def leer_nbt(datos_bytes):
    """Parsea un documento NBT completo y devuelve el diccionario raíz."""
    mv = memoryview(datos_bytes)
    root_type = mv[0]
    if root_type == 0:
        return {}
    name_len = struct.unpack_from(">H", mv, 1)[0]
    pos = 3 + name_len
    raiz, _ = _leer_payload_nbt(mv, pos, root_type)
    return raiz


# --- Lector de Región (.mca) y Chunks 1.21 con caché ---


class ChunkCacheado:
    """Almacena un chunk con sus secciones decodificadas bajo demanda una sola vez."""

    def __init__(self, nbt_raiz):
        hm = nbt_raiz.get("Heightmaps") or {}
        self.world_surface = hm.get("WORLD_SURFACE")
        self.secciones_raw = {}
        self.secciones_decodificadas = {}

        for sec in nbt_raiz.get("sections") or []:
            sec_y = sec.get("Y")
            bs = sec.get("block_states")
            if sec_y is None or not bs:
                continue
            palette = bs.get("palette")
            if not palette:
                continue
            nombres = []
            todo_aire = True
            for p in palette:
                nom = p.get("Name", "air")
                if nom.startswith("minecraft:"):
                    nom = nom[10:]
                if nom.endswith("_door"):  # el Ojo revisa puertas trabadas: guarda su estado (29-oct)
                    pr = p.get("Properties") or {}
                    nom += "[%s]" % ",".join("%s=%s" % (k, pr.get(k, "")) for k in ("facing", "half", "open", "hinge"))
                nombres.append(nom)
                if nom not in AIRE:
                    todo_aire = False
            if todo_aire:
                continue
            self.secciones_raw[sec_y] = (nombres, bs.get("data"))

        # Lista de índices Y de secciones no vacías de arriba hacia abajo
        self.ys_ordenados = sorted(self.secciones_raw.keys(), reverse=True)

    def obtener_seccion(self, sec_y):
        """Decodifica la sección sec_y una sola vez y la guarda en caché."""
        if sec_y in self.secciones_decodificadas:
            return self.secciones_decodificadas[sec_y]

        nombres, data = self.secciones_raw[sec_y]
        if not data or len(nombres) == 1:
            # Si no hay data, toda la sección es palette[0]
            res = nombres[0]
            self.secciones_decodificadas[sec_y] = res
            return res

        n_pal = len(nombres)
        bits = max(4, math.ceil(math.log2(n_pal)))
        vals_por_long = 64 // bits
        mask = (1 << bits) - 1

        bloques = [None] * 4096
        idx = 0
        for word in data:
            for _ in range(vals_por_long):
                if idx >= 4096:
                    break
                p_idx = word & mask
                bloques[idx] = nombres[p_idx] if p_idx < n_pal else "air"
                word >>= bits
                idx += 1
            if idx >= 4096:
                break
        if idx < 4096:
            relleno = nombres[0]
            for i in range(idx, 4096):
                bloques[i] = relleno

        self.secciones_decodificadas[sec_y] = bloques
        return bloques

    def _buscar_desde(self, lx, lz, sec_y_max):
        """Busca el bloque más alto no-aire en (lx, lz) desde sec_y_max hacia abajo."""
        base_xz = (lz << 4) | lx
        for sec_y in self.ys_ordenados:
            if sec_y > sec_y_max:
                continue
            sec = self.obtener_seccion(sec_y)
            if isinstance(sec, str):
                # Toda la sección es del mismo bloque no-aire
                return sec, (sec_y << 4) + 15
            for ly in range(15, -1, -1):
                nom = sec[(ly << 8) | base_xz]
                if nom not in AIRE:
                    return nom, (sec_y << 4) + ly
        return None, None

    def bloque_mas_alto(self, lx, lz):
        """Obtiene (nombre_bloque, altura_y) más alto no-aire en la columna (lx, lz)."""
        if not self.ys_ordenados:
            return None, None

        # Intentar empezar cerca usando Heightmaps/WORLD_SURFACE (9 bits por valor, minY = -64)
        ws = self.world_surface
        if ws and len(ws) >= 37:
            col_idx = (lz << 4) | lx
            long_idx = col_idx // 7
            bit_offset = (col_idx % 7) * 9
            val = (ws[long_idx] >> bit_offset) & 0x1FF
            if val > 0:
                y_est = val - 64
                sec_start = (y_est >> 4) + 1
                nom, y_pos = self._buscar_desde(lx, lz, sec_start)
                if nom is not None:
                    return nom, y_pos

        # Si no hay heightmap o falló, recorrer todo de arriba hacia abajo
        return self._buscar_desde(lx, lz, self.ys_ordenados[0])


class GestorRegiones:
    """Gestiona la apertura bajo demanda de archivos .mca y caché de chunks."""

    def __init__(self, carpeta_region):
        self.carpeta = carpeta_region
        self.regiones = {}  # (rx, rz) -> bytes de la región o None
        self.chunks = {}  # (cx, cz) -> ChunkCacheado o None

    def _ruta_mca(self, rx, rz):
        nombre = f"r.{rx}.{rz}.mca"
        if os.path.isfile(self.carpeta) and self.carpeta.endswith(".mca"):
            base = os.path.dirname(self.carpeta) or "."
            return os.path.join(base, nombre)
        r1 = os.path.join(self.carpeta, nombre)
        if os.path.isfile(r1):
            return r1
        r2 = os.path.join(self.carpeta, "region", nombre)
        return r2

    def _obtener_region(self, rx, rz):
        key = (rx, rz)
        if key not in self.regiones:
            ruta = self._ruta_mca(rx, rz)
            if os.path.isfile(ruta):
                with open(ruta, "rb") as f:
                    datos = f.read()
                self.regiones[key] = datos if len(datos) >= 4096 else None
            else:
                self.regiones[key] = None
        return self.regiones[key]

    def obtener_chunk(self, cx, cz):
        """Lee, descomprime y cachea un chunk (cx, cz) una sola vez."""
        key = (cx, cz)
        if key in self.chunks:
            return self.chunks[key]

        rx = cx >> 5
        rz = cz >> 5
        datos_reg = self._obtener_region(rx, rz)
        if not datos_reg:
            self.chunks[key] = None
            return None

        # Cabecera de 4096 bytes: 3 bytes sector + 1 byte cantidad
        idx_cabecera = ((cx & 31) + ((cz & 31) << 5)) * 4
        b0, b1, b2, cantidad = datos_reg[idx_cabecera : idx_cabecera + 4]
        sector_offset = (b0 << 16) | (b1 << 8) | b2
        if sector_offset == 0 or cantidad == 0:
            self.chunks[key] = None
            return None

        byte_offset = sector_offset * 4096
        if byte_offset + 5 > len(datos_reg):
            self.chunks[key] = None
            return None

        longitud = struct.unpack_from(">I", datos_reg, byte_offset)[0]
        tipo_comp = datos_reg[byte_offset + 4]
        if longitud <= 1 or byte_offset + 4 + longitud > len(datos_reg):
            self.chunks[key] = None
            return None

        payload = datos_reg[byte_offset + 5 : byte_offset + 4 + longitud]
        try:
            if tipo_comp == 2:
                raw_nbt = zlib.decompress(payload)
            elif tipo_comp == 1:
                raw_nbt = gzip.decompress(payload)
            elif tipo_comp == 3:
                raw_nbt = payload
            else:
                self.chunks[key] = None
                return None
            nbt_raiz = leer_nbt(raw_nbt)
            chunk = ChunkCacheado(nbt_raiz)
        except Exception:
            chunk = None

        self.chunks[key] = chunk
        return chunk


# --- Escritor PNG propio (RGB 8 bits, filtro 0, zlib) y utilidades de marcas ---


def _chunk_png(tipo_bytes, datos):
    """Empaqueta un bloque estándar PNG con longitud y CRC32."""
    crc = zlib.crc32(tipo_bytes + datos) & 0xFFFFFFFF
    return struct.pack(">I", len(datos)) + tipo_bytes + datos + struct.pack(">I", crc)


def escribir_png(ruta_salida, matriz_rgb, escala=1):
    """Escribe una matriz [alto][ancho] de tuplas (R, G, B) como PNG con factor de escala."""
    escala = max(1, int(escala))
    alto_bloques = len(matriz_rgb)
    ancho_bloques = len(matriz_rgb[0]) if alto_bloques > 0 else 0
    ancho_px = ancho_bloques * escala
    alto_px = alto_bloques * escala

    # Cabecera PNG e IHDR (8 bits, color_type=2 RGB)
    ihdr = struct.pack(">IIBBBBB", ancho_px, alto_px, 8, 2, 0, 0, 0)

    # Construir filas con filtro 0 al inicio de cada fila de píxeles
    filas_bytes = []
    for fila in matriz_rgb:
        if escala == 1:
            buf = bytearray(1 + ancho_bloques * 3)
            pos = 1
            for r, g, b in fila:
                buf[pos] = r
                buf[pos + 1] = g
                buf[pos + 2] = b
                pos += 3
            filas_bytes.append(bytes(buf))
        else:
            partes = [b"\x00"]
            for r, g, b in fila:
                partes.append(bytes((r, g, b)) * escala)
            fila_escalada = b"".join(partes)
            filas_bytes.append(fila_escalada * escala)

    idat_comprimido = zlib.compress(b"".join(filas_bytes), level=6)

    with open(ruta_salida, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk_png(b"IHDR", ihdr))
        f.write(_chunk_png(b"IDAT", idat_comprimido))
        f.write(_chunk_png(b"IEND", b""))


def parsear_color_marca(c):
    """Convierte el campo 'color' de una marca JSON a una tupla (R, G, B)."""
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return (int(c[0]) & 255, int(c[1]) & 255, int(c[2]) & 255)
    if isinstance(c, dict):
        return (int(c.get("r", 255)) & 255, int(c.get("g", 0)) & 255, int(c.get("b", 0)) & 255)
    if isinstance(c, int):
        return ((c >> 16) & 255, (c >> 8) & 255, c & 255)
    if isinstance(c, str):
        s = c.strip().lower()
        if s.startswith("#"):
            s = s[1:]
        elif s.startswith("0x"):
            s = s[2:]
        if len(s) == 6 and all(ch in "0123456789abcdef" for ch in s):
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        if len(s) == 3 and all(ch in "0123456789abcdef" for ch in s):
            return (int(s[0] * 2, 16), int(s[1] * 2, 16), int(s[2] * 2, 16))
        nombres = {
            "red": (255, 40, 40),
            "rojo": (255, 40, 40),
            "green": (40, 220, 40),
            "verde": (40, 220, 40),
            "blue": (40, 100, 255),
            "azul": (40, 100, 255),
            "yellow": (255, 235, 30),
            "amarillo": (255, 235, 30),
            "white": (255, 255, 255),
            "blanco": (255, 255, 255),
            "black": (0, 0, 0),
            "negro": (0, 0, 0),
            "orange": (255, 140, 20),
            "naranja": (255, 140, 20),
            "purple": (170, 50, 230),
            "morado": (170, 50, 230),
            "magenta": (240, 50, 220),
            "cyan": (30, 220, 230),
            "cian": (30, 220, 230),
            "pink": (255, 130, 190),
            "rosa": (255, 130, 190),
            "gray": (128, 128, 128),
            "gris": (128, 128, 128),
            "brown": (140, 85, 45),
            "cafe": (140, 85, 45),
            "marron": (140, 85, 45),
            "lime": (120, 245, 40),
            "lima": (120, 245, 40),
        }
        if s in nombres:
            return nombres[s]
    return (255, 0, 0)


# --- Generación del mapa y CLI ---


def generar_mapa(carpeta_region, x1, z1, x2, z2, salida_png, escala=3, archivo_marcas=None):
    """Genera el mapa visto desde arriba de [x1..x2] x [z1..z2] y guarda el PNG."""
    xmin, xmax = min(x1, x2), max(x1, x2)
    zmin, zmax = min(z1, z2), max(z1, z2)
    ancho = xmax - xmin + 1
    alto = zmax - zmin + 1

    gestor = GestorRegiones(carpeta_region)

    # Guardamos también zmin - 1 para calcular el sombreado respecto al vecino del norte
    bloques = [[None] * ancho for _ in range(alto)]
    alturas = [[None] * ancho for _ in range(alto + 1)]  # índice 0 corresponde a zmin - 1

    # Recorrer por chunks para aprovechar al máximo la caché de chunks y secciones
    cx_min, cx_max = xmin >> 4, xmax >> 4
    cz_min, cz_max = (zmin - 1) >> 4, zmax >> 4

    for cz in range(cz_min, cz_max + 1):
        z_ini = max(zmin - 1, cz << 4)
        z_fin = min(zmax, (cz << 4) + 15)
        for cx in range(cx_min, cx_max + 1):
            chunk = gestor.obtener_chunk(cx, cz)
            if chunk is None:
                continue
            x_ini = max(xmin, cx << 4)
            x_fin = min(xmax, (cx << 4) + 15)
            for z in range(z_ini, z_fin + 1):
                lz = z & 15
                fila_h = alturas[z - (zmin - 1)]
                fila_b = bloques[z - zmin] if z >= zmin else None
                for x in range(x_ini, x_fin + 1):
                    nom, y_pos = chunk.bloque_mas_alto(x & 15, lz)
                    if nom is not None:
                        ix = x - xmin
                        fila_h[ix] = y_pos
                        if fila_b is not None:
                            fila_b[ix] = nom

    # Colorear cada bloque y sombrear según diferencia de altura con el vecino del norte (z - 1)
    imagen = [[(0, 0, 0)] * ancho for _ in range(alto)]
    for iz in range(alto):
        fila_b = bloques[iz]
        fila_h_actual = alturas[iz + 1]
        fila_h_norte = alturas[iz]
        fila_img = imagen[iz]
        for ix in range(ancho):
            nom = fila_b[ix]
            if nom is None:
                continue
            r, g, b = color_de_bloque(nom)
            y_act = fila_h_actual[ix]
            y_nor = fila_h_norte[ix]
            if y_act is not None and y_nor is not None:
                diff = y_act - y_nor
                if diff > 0:
                    delta = min(45, diff * 15)
                elif diff < 0:
                    delta = max(-45, diff * 15)
                else:
                    delta = 0
                if delta != 0:
                    r = max(0, min(255, r + delta))
                    g = max(0, min(255, g + delta))
                    b = max(0, min(255, b + delta))
            fila_img[ix] = (r, g, b)

    # Dibujar marcas opcionales de 3x3 bloques encima
    if archivo_marcas:
        with open(archivo_marcas, "r", encoding="utf-8") as f:
            datos_marcas = json.load(f)
        lista_marcas = (
            datos_marcas
            if isinstance(datos_marcas, list)
            else datos_marcas.get("marcas", datos_marcas.get("markers", []))
        )
        for m in lista_marcas:
            mx = int(m["x"])
            mz = int(m["z"])
            color_rgb = parsear_color_marca(m.get("color", [255, 0, 0]))
            for dz in (-1, 0, 1):
                iz = mz + dz - zmin
                if 0 <= iz < alto:
                    for dx in (-1, 0, 1):
                        ix = mx + dx - xmin
                        if 0 <= ix < ancho:
                            imagen[iz][ix] = color_rgb

    escribir_png(salida_png, imagen, escala=escala)


def main():
    parser = argparse.ArgumentParser(
        description="Dibuja un mapa PNG cenital de Minecraft 1.21.1 desde archivos .mca"
    )
    parser.add_argument("carpeta_region", help="Carpeta con los archivos r.X.Z.mca")
    parser.add_argument("x1", type=int, help="Coordenada X inicial del mundo")
    parser.add_argument("z1", type=int, help="Coordenada Z inicial del mundo")
    parser.add_argument("x2", type=int, help="Coordenada X final del mundo")
    parser.add_argument("z2", type=int, help="Coordenada Z final del mundo")
    parser.add_argument("salida", help="Archivo PNG de salida")
    parser.add_argument("--escala", type=int, default=3, help="Píxeles por bloque (defecto: 3)")
    parser.add_argument("--marcas", default=None, help="Archivo JSON opcional con marcas [{x, z, color}]")

    args = parser.parse_args()
    generar_mapa(
        args.carpeta_region,
        args.x1,
        args.z1,
        args.x2,
        args.z2,
        args.salida,
        escala=args.escala,
        archivo_marcas=args.marcas,
    )


if __name__ == "__main__":
    main()
