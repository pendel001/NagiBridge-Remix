param([string]$Key = "F5")

$proc = Get-Process StardewModdingAPI -ErrorAction SilentlyContinue
if (-not $proc) { Write-Output "Game not found"; exit 1 }

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class SendKeyHelper {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
"@

$vkMap = @{
    "F1"=0x70;"F2"=0x71;"F3"=0x72;"F4"=0x73;"F5"=0x74;"F6"=0x75;
    "F7"=0x76;"F8"=0x77;"F9"=0x78;"F10"=0x79;"F11"=0x7A;"F12"=0x7B;
}

$vk = $vkMap[$Key]
if (-not $vk) { Write-Output "Unknown key: $Key"; exit 1 }

[SendKeyHelper]::SetForegroundWindow($proc.MainWindowHandle)
Start-Sleep -Milliseconds 300
[SendKeyHelper]::keybd_event($vk, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 100
[SendKeyHelper]::keybd_event($vk, 0, 2, [UIntPtr]::Zero)
Write-Output "$Key sent"
