# -*- coding: utf-8 -*-
"""Where Ojo del mundo keeps its cache (texture index) and the images it draws: the user's cache folder, never the
folder where the code is installed. Set before importing ojo_vista3d."""
import os
import sys
import tempfile


def carpeta_cache():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    ruta = os.path.join(base, "ojo-del-mundo")
    try:
        os.makedirs(ruta, exist_ok=True)
    except OSError:
        ruta = os.path.join(tempfile.gettempdir(), "ojo-del-mundo")
        os.makedirs(ruta, exist_ok=True)
    return ruta


def preparar():
    """Point the texture index at the user cache (unless OJO_CACHE is set) and return the cache folder."""
    ruta = carpeta_cache()
    os.environ.setdefault("OJO_CACHE", os.path.join(ruta, "texture_index.pkl"))
    return ruta
