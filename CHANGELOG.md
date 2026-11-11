# Changelog

## 1.1.0 (2026-11-11)
- New general MCP server `ojo-mcp` (`ojo/ojo_mcp.py`): any world, Windows, macOS and Linux, 14 tools with English names, world chosen with `--world`, `OJO_WORLD` or the `open_world` tool. Read-only.
- Installs with `pip install git+https://github.com/Dsergio909/ojo-del-mundo` (or `uvx`); commands `ojo-mcp` and `ojo-view`.
- Textures from 1.21.1 if installed, otherwise the newest 1.18+ version the launcher installed.
- `open_world` reads `level.dat`: world name, Minecraft version, spawn, and a warning for worlds older than 1.18.
- `ojo-view` accepts English options (`--world`, `--radius`, `--yaw`, `--pitch`, `--out`...); the Spanish ones still work.
- Texture index and images go to the user's cache folder, not next to the code.
- README in English first; tests for the new server and the `.jar` choice; CI on Windows, macOS and Linux.
- `count_blocks` without `y1`/`y2` now goes down to the natural ground, so it also counts what is under roofs (before, a torch inside a house gave 0).
- The MCP server checks its input: bad messages, wrong argument types, unknown options or unknown arguments get a clear error instead of a crash; failures come with `isError: true`; the protocol version is negotiated.
- Every tool is marked read-only (`readOnlyHint`) and has a title.
- `open_world` accepts the `region` folder too. `count_blocks` no longer falls back to the author's coordinates when only `radius` is given.
- Textures: year-numbered versions (26.1 and later) are recognised; the Minecraft folder is searched in the right order for each OS.
- `ojo-view` no longer fails on Windows consoles that are not UTF-8, and creates the output folder if needed.
- `python -m ojo` runs the MCP server (useful when `ojo-mcp` is not on PATH).
- README: security and privacy, troubleshooting, Windows paths for Claude Desktop.

## 1.0.0 (2026-11-04)
- First public version: 3D views from the save files and the MCP server used with the author's modpack.
