# -*- coding: utf-8 -*-
"""Envía comandos al servidor por RCON (solo en este PC). Lee la clave de servidor/server.properties.
Uso: python rcon.py "comando 1" "comando 2" ..."""
import os
import socket
import struct
import sys

RAIZ = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def propiedades():
    p = {}
    # NIEBLA_SRV=servidor_pruebas habla con el servidor de pruebas (puertos 25566/25576); por defecto, el de Sergio
    for linea in open(os.path.join(RAIZ, os.environ.get("NIEBLA_SRV", "servidor"), "server.properties"), encoding="utf-8"):
        if "=" in linea and not linea.startswith("#"):
            k, v = linea.strip().split("=", 1)
            p[k] = v
    return p


def paquete(rid, tipo, cuerpo):
    datos = struct.pack("<ii", rid, tipo) + cuerpo.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(datos)) + datos


def leer(s):
    n = struct.unpack("<i", s.recv(4))[0]
    datos = b""
    while len(datos) < n:
        trozo = s.recv(n - len(datos))
        if not trozo:  # el servidor cerró la conexión (p. ej. mientras arranca): antes se quedaba girando para siempre
            raise OSError("RCON: el servidor cerró la conexión")
        datos += trozo
    rid, _ = struct.unpack("<ii", datos[:8])
    return rid, datos[8:-2].decode("utf-8", "replace")


def enviar(comandos):
    """Manda los comandos y devuelve las respuestas como texto (para usarlo desde otros scripts sin abrir otro proceso)."""
    p = propiedades()
    s = socket.create_connection(("127.0.0.1", int(p.get("rcon.port", 25575))), timeout=30)
    try:
        s.sendall(paquete(1, 3, p["rcon.password"]))
        rid, _ = leer(s)
        if rid == -1:
            sys.exit("Clave RCON incorrecta")
        out = ""
        for i, c in enumerate(comandos, start=2):
            s.sendall(paquete(i, 2, c))
            _, r = leer(s)
            out += f"> {c}\n{r}\n"
        return out
    finally:
        s.close()


def main(comandos):
    print(enviar(comandos), end="")


if __name__ == "__main__":
    # 29-oct: desde la terminal a veces quedaba colgado sin hacer nada (40 min una vez): se corta solo a los 90 s
    import threading
    threading.Timer(90, lambda: os._exit(3)).start()
    main(sys.argv[1:])
    sys.stdout.flush()
    os._exit(0)
