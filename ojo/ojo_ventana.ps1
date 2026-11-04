# Para la herramienta foto del «Ojo del mundo». Solo toca la ventana del Minecraft de PRUEBA (el de juego_prueba), nunca el de Sergio.
#   arriba -> zona de juego 1600x900, ventana siempre encima; devuelve "x y ancho alto" de la zona de juego
#   soltar -> la ventana deja de estar siempre encima
param([string]$modo = "arriba", [int]$id = 0)
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class OjoVen {
  public struct R { public int L, T, Rr, B; } public struct P { public int X, Y; }
  [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h, int i, int v);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out R r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref P p);
  [DllImport("user32.dll")] public static extern bool AdjustWindowRect(ref R r, int s, bool m);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
}
"@
$ids = Get-CimInstance Win32_Process -Filter "Name='javaw.exe'" | Where-Object { $_.CommandLine -like '*juego_prueba*' } | ForEach-Object { $_.ProcessId }
if ($id -gt 0) { $ids = @($id) }   # el Ojo del mundo ya comprobó que es el cliente «Prueba» (conectado por RCON)
$h = (Get-Process -Id $ids -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1).MainWindowHandle
if (-not $h) { exit 1 }
if ($modo -eq "soltar") { $null = [OjoVen]::SetWindowPos($h, [IntPtr](-2), 0, 0, 0, 0, 0x0013); exit 0 }
$null = [OjoVen]::ShowWindow($h, 4)   # mostrar sin activar (SW_SHOWNOACTIVATE)
$estilo = 0x00CF0000 -bor 0x10000000   # WS_OVERLAPPEDWINDOW | WS_VISIBLE
$null = [OjoVen]::SetWindowLong($h, -16, $estilo)
# 28-oct-2026: Sergio juega en la pantalla principal; el Minecraft de prueba va en la OTRA pantalla (si hay), del tamaño que quepa
Add-Type -AssemblyName System.Windows.Forms
# Regla de Sergio (28-oct-2026): si él está jugando, la ventana va a la pantalla SECUNDARIA; si no, a la principal.
# «Jugando» = LoL abierto (cliente o partida), su propio Minecraft (otro javaw además del de Prueba) o el archivo
# herramientas\ojo_otra_pantalla.flag (para forzarlo a mano).
$jugando = (Test-Path "$PSScriptRoot\ojo_otra_pantalla.flag") -or
           [bool](Get-Process -Name "League of Legends", "LeagueClientUx" -ErrorAction SilentlyContinue) -or
           (@(Get-Process -Name javaw -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $id }).Count -gt 0)
$otra = $null
if ($jugando) { $otra = [System.Windows.Forms.Screen]::AllScreens | Where-Object { -not $_.Primary } | Select-Object -First 1 }
if ($otra) { $px = $otra.Bounds.X + 10; $py = $otra.Bounds.Y + 10; $cw = [int][Math]::Min([Math]::Min(1600, $otra.Bounds.Width - 40), ($otra.Bounds.Height - 90) * 16 / 9) } else { $px = 20; $py = 20; $cw = 1600 }
$ch = [int]($cw * 9 / 16)
$r = New-Object OjoVen+R; $r.L = 0; $r.T = 0; $r.Rr = $cw; $r.B = $ch; $null = [OjoVen]::AdjustWindowRect([ref]$r, $estilo, $false)
$null = [OjoVen]::SetWindowPos($h, [IntPtr](-1), $px, $py, $r.Rr - $r.L, $r.B - $r.T, 0x0070)   # HWND_TOPMOST, sin activar (no le quita el foco al juego de Sergio)
Start-Sleep -Milliseconds 900
$c = New-Object OjoVen+R; $null = [OjoVen]::GetClientRect($h, [ref]$c); $p = New-Object OjoVen+P; $null = [OjoVen]::ClientToScreen($h, [ref]$p)
"$($p.X) $($p.Y) $($c.Rr) $($c.B)"
