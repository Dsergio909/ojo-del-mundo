# CLAUDE.md

## Antes de empezar: lee la memoria (cerebro)
La memoria de mis proyectos es mi repo **privado** `Dsergio909/cerebro` (copia diaria de mi Obsidian). Al empezar cualquier sesión, antes de proponer o cambiar nada:

1. Abre ese repo (en la nube: agrégalo a la sesión) y lee:
   - `Proyectos/Como trabajar con Sergio.md`: cómo me gusta trabajar, diseño, decisiones tomadas y privacidad.
   - `Como_actualizar_el_cerebro.md`
   - `Proyectos/Minecraft/MC_Herramientas_IAs_y_el_Ojo.md`: el Ojo del mundo, sus herramientas y lecciones.
   - `Proyectos/Modpack La Niebla de Herobrine.md`: el modpack para el que se hizo.
2. Si no puedes abrirlo o la nota todavía no existe, sigue las reglas mínimas de abajo y avísame.
3. Desde la nube el cerebro **solo se lee**. Al terminar con cambios, entrégame un paquete en Markdown con lo que hay que cambiar en cada nota y sección, para pasárselo a Claude Code en mi PC.

## Reglas mínimas (por si no se puede leer el cerebro)
- Háblame en español, simple y paso a paso.
- **Diseño profesional** en el README y en lo público: sin neón, pixel art de adorno ni emojis de adorno. Las imágenes del README salen del propio Ojo.
- **Antes de publicar algo visible** (diseño, textos, imágenes): muéstrame el antes y el después en capturas y espera mi «sí».
- Prueba y audita tú mismo; comprueba con datos reales. Sin cifras inventadas. Quita lo que no sirva.
- No vuelvas a proponer algo que ya se decidió: está en el cerebro.
- Nunca mi número de teléfono, claves, tokens, cédulas ni direcciones exactas. Nada interno de la empresa de mi práctica. Datos de ejemplo siempre ficticios.
- Nada de `push --force` ni borrar ramas o repos sin mi permiso explícito.
- Este repo no trae archivos de Mojang: texturas y modelos se leen del `.jar` del juego de cada quien. Nunca subas `ojo_vista3d_cache.pkl` ni nada sacado del `.jar`.
- Nada del modpack que no sea público: rutas de mi PC, nombres de jugadores, IPs, claves de RCON ni el `server.properties`.

## Estado del proyecto (4-nov-2026)
- Publicado en GitHub el 4-nov-2026 (MIT). `ver_mundo.py` funciona con cualquier mundo de Minecraft Java 1.21 en Windows, Mac y Linux.
- El servidor MCP (`ojo/ojo_del_mundo.py`, 32 herramientas) es el mismo que uso en el modpack, tal cual: espera las carpetas `servidor/` y `planos/` y está hecho para Windows.
- Pruebas: `python pruebas/probar.py` (mundo hecho desde cero, sin Minecraft). GitHub las corre en Windows y Linux en cada cambio. Si cambias cómo se leen o dibujan los bloques, córrelas antes de subir.
- Por confirmar con fuentes del PC: «7 fotos reales, 83 a 95 % de parecido» en el README.

## Registro de carrera
Al cerrar ("cierra el chat") o cuando diga "registra esto", revisa si pasó algo que cumpla al menos uno de estos criterios: terminé o entregué algo, tomé una decisión importante con su razón, obtuve un resultado concreto, aprendí una herramienta usándola de verdad, o algo falló y entendí por qué. Si nada cumple, dime "nada para registrar".

- En mi Google Drive, carpeta Cerebro: Carrera/REGISTRO.md (registro) y Carrera/00_Carrera.md (formato).
- Si aparece una copia que dice "Este cerebro se mudó" (por ejemplo, en la carpeta vieja `Cerebro_Carrera`), ignórala.

Puedes escribir la entrada tú mismo: agrégala al final de REGISTRO.md sin editar ni borrar las anteriores, y confírmame en una línea. Si tu herramienta no puede agregar al final sin reemplazar el archivo completo o crear uno nuevo (por ejemplo, el conector de Drive desde la nube), no lo hagas: entrégame la entrada en un bloque de código para que yo la pegue.

Si no puedes leer 00_Carrera.md, usa este formato:

```markdown
## AAAA-MM-DD · <Proyecto> · <título corto>
- Qué hice:
- Problema que resuelve:
- Herramientas:
- Resultado o evidencia: (si no hay cifra real, no se inventa)
- Qué aprendí o qué falló:
- ¿Lo puedo defender en entrevista?: sí / parcial (qué me falta)
- ¿Se puede mostrar en público?: sí / no / solo sin datos internos
```

Máximo 8 líneas. Sin cifras inventadas, sin datos de terceros, sin claves ni rutas privadas.
