# -*- coding: utf-8 -*-
"""Entrena la revisión de fotos de la IA local del Ojo (29-oct-2026, Sergio: «mejora el ojo con Ollama y Antigravity en
loop hasta que sepa leer Minecraft y no gaste tantos tokens»).

Ciclo: la IA local (qwen3.5:9b, con visión) contesta las preguntas sobre fotos cuya respuesta ya se sabe
(auditoria_aldea/ojo/entrenamiento/casos.json); se mide cuánto acierta; Antigravity (solo Flash) lee SOLO el texto de los
errores y propone otra forma de preguntar; si acierta más, se queda. Hasta acertar el 95 % o hacer 30 rondas.
Se pausa solo mientras Sergio juega (LoL o Minecraft) o si la GPU está ocupada con otra cosa.
Lo mejor queda en herramientas/ojo_prompt.json, que usa revisar_foto_local. Bitácora: entrenamiento/progreso.md
Uso: py -3.12 ojo_entrenar.py [rondas=30]
     py -3.12 ojo_entrenar.py medir [modelo|-] [prompt.json]   (29-oct, ojo-vision: solo mide un modelo con casos.json, sin Flash; sirve
                                                para comparar un modelo nuevo con qwen3.5:9b antes de cambiarlo)"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
CARP = os.path.join(AQUI, "..", "auditoria_aldea", "ojo", "entrenamiento")
PROMPT = os.path.join(AQUI, "ojo_prompt.json")
AGY = os.path.join(os.environ["USERPROFILE"], ".local", "bin", "agy.exe")
AISLADA = os.path.join(os.environ["USERPROFILE"], "agy_aislado", "ojo_entrenar")
JUEGOS = ("League of Legends.exe", "LeagueClientUx.exe", "javaw.exe")
MODELO = [json.load(open(PROMPT, encoding="utf-8")).get("modelo", "qwen3.5:9b") if os.path.isfile(PROMPT) else "qwen3.5:9b"]

INICIAL = {
    "intro": "Foto de Minecraft. Responde cada pregunta con SÍ o NO y dónde (arriba/abajo/izquierda/derecha/centro):",
    "preguntas": {
        "dientes": "¿Hay bloques de pasto sueltos en fila con huecos entre ellos (como dientes)?",
        "hundida": "¿Hay una casa o puesto más bajo que el terreno de al lado (hundido)?",
        "flotando": "¿Hay algún bloque flotando en el aire sin nada debajo?",
        "tapada": "¿Hay un camino o una puerta tapada por bloques?",
        "zanjas": "¿Hay zanjas o hoyos en el pasto?",
        "oscura": "¿La escena está muy oscura?",
        "hueco_aspas": "¿Hay un hueco de aire entre las aspas del molino y el edificio?",
    },
}


def anotar(t):
    with open(os.path.join(CARP, "progreso.md"), "a", encoding="utf-8") as f:
        f.write(time.strftime("%d-%m %H:%M ") + t + "\n")
    print(t, flush=True)


def esperar_libre():
    """No usar la GPU mientras Sergio juega ni si otra tarea la tiene (regla: una tarea de GPU a la vez)."""
    while True:
        procesos = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
        jugando = any(j in procesos for j in JUEGOS)
        gpu = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout.strip().splitlines()[1:]
        ocupada = any(MODELO[0] not in l for l in gpu if l.strip())
        if not jugando and not ocupada and "ComfyUI" not in procesos:
            return
        time.sleep(300)


def leer_respuestas(r, n):
    """SÍ/NO de cada pregunta numerada. 29-oct (ojo-vision): un tercio de los errores eran «?», respuestas que no se
    pudieron leer («Pregunta 1:», «**1.**», «Sí» al final de la línea, tablas o texto vacío). Se busca por número y,
    si no aparece, se toman las líneas con SÍ/NO en orden cuando hay justo una por pregunta."""
    sn = r"(S[IÍ]|NO)(?![A-Za-zÁÉÍÓÚáéíóúñ])"
    out = []
    for i in range(1, n + 1):
        m = re.search(r"(?m)^[\W_]*(?:pregunta\s*)?%d(?!\d)[^\n]*?(?<![A-Za-zÁÉÍÓÚáéíóúñ])%s" % (i, sn), r, re.I)
        out.append(None if not m else ("SÍ" if m.group(1).upper() in ("SI", "SÍ") else "NO"))
    if None in out:
        lineas = [re.search(r"(?<![A-Za-zÁÉÍÓÚáéíóúñ])" + sn, l, re.I) for l in r.splitlines()]
        lineas = [m.group(1).upper() for m in lineas if m]
        if len(lineas) != n:  # «SÍ (centro), NO, NO, NO» en una sola línea: todas las respuestas en orden
            lineas = [m.group(1).upper() for m in re.finditer(r"(?<![A-Za-zÁÉÍÓÚáéíóúñ])" + sn, r, re.I)]
        if len(lineas) == n:
            out = [o or ("SÍ" if v in ("SI", "SÍ") else "NO") for o, v in zip(out, lineas)]
    return [o or "?" for o in out]


def preguntar_local(foto, cfg, claves, intentos=2):
    texto = cfg["intro"] + "\n" + "\n".join("%d. %s" % (i + 1, cfg["preguntas"][k]) for i, k in enumerate(claves))
    img = base64.b64encode(open(foto, "rb").read()).decode()
    for intento in range(intentos):
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"model": MODELO[0], "prompt": texto, "images": [img], "stream": False,
                                                      "think": True, "keep_alive": "5m", "options": {"num_ctx": 8192}}).encode())
        r = json.load(urllib.request.urlopen(req, timeout=900)).get("response", "")
        lista = leer_respuestas(r, len(claves))
        if "?" not in lista:
            break
        with open(os.path.join(CARP, "crudo.log"), "a", encoding="utf-8") as f:  # para ver qué no se pudo leer
            f.write("%s %s intento %d:\n%s\n---\n" % (time.strftime("%d-%m %H:%M"), os.path.basename(foto), intento + 1, r))
    return dict(zip(claves, lista)), r


def evaluar(cfg, casos, por_pregunta=None):
    bien = total = 0
    errores = []
    for c in casos:
        esperar_libre()
        claves = list(c["esperado"])
        resp, crudo = preguntar_local(os.path.join(CARP, c["foto"]), cfg, claves)
        for k, v in c["esperado"].items():
            total += 1
            if por_pregunta is not None:
                pp = por_pregunta.setdefault(k, [0, 0])
                pp[1] += 1
                pp[0] += resp.get(k) == v
            if resp.get(k) == v:
                bien += 1
            else:
                errores.append("foto %s (%s): pregunta «%s» -> dijo %s, era %s" % (c["foto"], c.get("nota", ""), cfg["preguntas"][k], resp.get(k), v))
    subprocess.run(["ollama", "stop", MODELO[0]], capture_output=True)
    return bien / max(total, 1), errores


def pedir_mejora(cfg, puntaje, errores):
    os.makedirs(AISLADA, exist_ok=True)
    pedido = ("Eres experto en escribir instrucciones para modelos de visión pequeños (9B). Un modelo local revisa capturas de "
              "Minecraft contestando preguntas de SÍ/NO. Acierta el %d %%. Estas son las instrucciones actuales (JSON):\n%s\n\n"
              "Estos son sus errores:\n%s\n\nEscribe instrucciones mejores para que acierte más: más concretas, con pistas visuales de "
              "Minecraft (qué se ve cuando hay un hueco, una zanja, algo hundido u oscuro), sin cambiar las claves de 'preguntas'. "
              "Devuelve SOLO el JSON nuevo con las mismas claves 'intro' y 'preguntas', en español."
              % (int(puntaje * 100), json.dumps(cfg, ensure_ascii=False, indent=1), "\n".join(errores[:25])))
    r = subprocess.run([AGY, "-p", pedido, "--model", "gemini-3.8-flash-medium", "--output-format", "text", "--print-timeout", "900s"],
                       cwd=AISLADA, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1000)
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if not m:
        return None
    try:
        nuevo = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if set(nuevo.get("preguntas", {})) != set(cfg["preguntas"]) or not nuevo.get("intro"):
        return None
    return nuevo


def medir(modelo=None, prompt=None):
    """Solo mide (sin Flash): acierto total y por pregunta, con el prompt de ojo_prompt.json (u otro archivo)."""
    if modelo and modelo != "-":
        MODELO[0] = modelo
    casos = json.load(open(os.path.join(CARP, "casos.json"), encoding="utf-8"))
    prompt = prompt or PROMPT
    cfg = json.load(open(prompt, encoding="utf-8")) if os.path.isfile(prompt) else INICIAL
    pp = {}
    t0 = time.time()
    puntaje, errores = evaluar(cfg, casos, pp)
    anotar("medir %s (%s): %d %% de %d respuestas (%d fotos, %.0f min). Por pregunta: %s" % (
        MODELO[0], os.path.basename(prompt), round(puntaje * 100), sum(v[1] for v in pp.values()), len(casos), (time.time() - t0) / 60,
        ", ".join("%s %d/%d" % (k, v[0], v[1]) for k, v in sorted(pp.items()))))
    for e in errores:
        anotar("   error: " + e)
    return puntaje, pp


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "medir":
        medir(sys.argv[2] if len(sys.argv) > 2 else None, sys.argv[3] if len(sys.argv) > 3 else None)
        return
    rondas = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    casos = json.load(open(os.path.join(CARP, "casos.json"), encoding="utf-8"))
    mejor = json.load(open(PROMPT, encoding="utf-8")) if os.path.isfile(PROMPT) else INICIAL
    puntaje, errores = evaluar(mejor, casos)
    anotar("inicio: acierta %d %% (%d errores)" % (puntaje * 100, len(errores)))
    for k in range(rondas):
        if puntaje >= 0.95:
            anotar("listo: llegó al %d %%" % (puntaje * 100))
            break
        propuesta = pedir_mejora(mejor, puntaje, errores)
        if not propuesta:
            anotar("ronda %d: Flash no devolvió algo usable" % (k + 1))
            continue
        p2, e2 = evaluar(propuesta, casos)
        if p2 > puntaje:
            mejor, puntaje, errores = propuesta, p2, e2
            json.dump(mejor, open(PROMPT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            anotar("ronda %d: MEJORÓ a %d %% (guardado en ojo_prompt.json)" % (k + 1, p2 * 100))
        else:
            anotar("ronda %d: la propuesta acertó %d %%, no mejora (sigue %d %%)" % (k + 1, p2 * 100, puntaje * 100))
    json.dump(mejor, open(PROMPT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
