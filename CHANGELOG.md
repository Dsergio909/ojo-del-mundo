# Changelog

## 1.1.0 (2026-11-11)
- New general MCP server `ojo-mcp` (`ojo/ojo_mcp.py`): any world, Windows, macOS and Linux, 14 tools with English names, world chosen with `--world`, `OJO_WORLD` or the `open_world` tool. Read-only.
- Installs with `pip install git+https://github.com/Dsergio909/ojo-del-mundo` (or `uvx`); commands `ojo-mcp` and `ojo-view`.
- Textures from 1.21.1 if installed, otherwise the newest 1.18+ version the launcher installed.
- `open_world` reads `level.dat`: world name, Minecraft version, spawn, and a warning for worlds older than 1.18.
- `ojo-view` accepts English options (`--world`, `--radius`, `--yaw`, `--pitch`, `--out`...); the Spanish ones still work.
- Texture index and images go to the user's cache folder, not next to the code.
- README in English first; tests for the new server and the `.jar` choice; CI on Windows, macOS and Linux.

## 1.0.0 (2026-11-04)
- First public version: 3D views from the save files and the MCP server used with the author's modpack.
