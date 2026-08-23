$ErrorActionPreference = "Stop"
$dir = "C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley"
try {
  $asm = [System.Reflection.Assembly]::LoadFrom("$dir\Stardew Valley.dll")
  $t = $asm.GetType("StardewValley.Menus.MineElevatorMenu")
  if (-not $t) { Write-Output "type NULL"; $asm.GetTypes() | Where-Object FullName -match "Elevator|MineMenu" | ForEach-Object { Write-Output ("TYPE: " + $_.FullName) }; exit }
  Write-Output ("== MineElevatorMenu = " + $t.FullName)
  $flags = [System.Reflection.BindingFlags]"Public,NonPublic,Instance"
  $t.GetFields($flags) | ForEach-Object {
    Write-Output ("  FIELD: " + $_.FieldType.Name + "  " + $_.Name)
  }
  $t.GetProperties($flags) | ForEach-Object {
    Write-Output ("  PROP:  " + $_.PropertyType.Name + "  " + $_.Name)
  }
} catch { Write-Output ("ERR: " + $_.Exception.Message) }
