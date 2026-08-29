# VisTest — запуск на Windows.
#   .\run.ps1            поднять UI
#   .\run.ps1 test       прогнать тесты
#   .\run.ps1 doctor     диагностика
# Если PowerShell ругается на политику выполнения:
#   powershell -ExecutionPolicy Bypass -File .\run.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Find-Python {
    foreach ($c in @("py -3", "python", "python3")) {
        $exe, $arg = $c.Split(" ", 2)
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            try {
                $v = & $exe $arg --version 2>&1
                if ($LASTEXITCODE -eq 0) { return @{ Exe = $exe; Arg = $arg } }
            } catch {}
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host "Python 3.10+ не найден. Скачайте: https://www.python.org/downloads/" -ForegroundColor Red
    Read-Host "Enter для выхода"
    exit 1
}

if ($py.Arg) { & $py.Exe $py.Arg run.py @args } else { & $py.Exe run.py @args }
$code = $LASTEXITCODE

if ($args.Count -eq 0 -and $code -ne 0) { Read-Host "Enter для выхода" }
exit $code
