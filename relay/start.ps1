# herdr-outpost relay launcher for Windows PowerShell

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "   herdr-outpost relay daemon (Windows)" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# Find and load config.env if available
$ConfigPaths = @(
    $env:HERDR_OUTPOST_CONFIG,
    "$env:USERPROFILE\.config\herdr-outpost\config.env",
    "$env:USERPROFILE\.config\herdr-remote\config.env"
)

foreach ($cfg in $ConfigPaths) {
    if ($cfg -and (Test-Path $cfg)) {
        Write-Host "Loading config: $cfg" -ForegroundColor Green
        Get-Content $cfg | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
                $parts = $line.Split("=", 2)
                $key = $parts[0].Trim()
                $val = $parts[1].Trim().Trim('"').Trim("'")
                [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
            }
        }
        break
    }
}

$Port = if ($env:HERDR_OUTPOST_RELAY_PORT) { $env:HERDR_OUTPOST_RELAY_PORT } elseif ($env:HERDR_RELAY_PORT) { $env:HERDR_RELAY_PORT } else { "8375" }
Write-Host "Starting relay on port :$Port..." -ForegroundColor Cyan

# Check for uv or python
$Runner = $null
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $Runner = "uv"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Runner = "python"
} else {
    Write-Error "Neither 'uv' nor 'python' was found in PATH."
    exit 1
}

$RelayJob = $null
$TunnelJob = $null

try {
    if ($Runner -eq "uv") {
        $RelayJob = Start-Process -FilePath "uv" -ArgumentList "run", "--directory", "$ScriptDir", "python", "$ScriptDir\herdr_relay.py" -PassThru -NoNewWindow
    } else {
        $RelayJob = Start-Process -FilePath "python" -ArgumentList "$ScriptDir\herdr_relay.py" -PassThru -NoNewWindow
    }
    Write-Host "Relay running (PID $($RelayJob.Id))" -ForegroundColor Green

    $TunnelMode = if ($env:HERDR_OUTPOST_TUNNEL_MODE) { $env:HERDR_OUTPOST_TUNNEL_MODE } else { $env:HERDR_TUNNEL_MODE }
    $TunnelName = if ($env:HERDR_OUTPOST_TUNNEL_NAME) { $env:HERDR_OUTPOST_TUNNEL_NAME } else { $env:HERDR_TUNNEL_NAME }

    if ($TunnelMode -eq "named" -and $TunnelName) {
        if (Get-Command cloudflared -ErrorAction SilentlyContinue) {
            Write-Host "Starting named Cloudflare tunnel ($TunnelName)..." -ForegroundColor Cyan
            $TunnelJob = Start-Process -FilePath "cloudflared" -ArgumentList "tunnel", "run", $TunnelName -PassThru -NoNewWindow
            Write-Host "Tunnel running (PID $($TunnelJob.Id))" -ForegroundColor Green
        } else {
            Write-Warning "'cloudflared' command not found, skipping tunnel."
        }
    }

    Write-Host "Ready. Press Ctrl+C to stop." -ForegroundColor Yellow
    $RelayJob.WaitForExit()
}
finally {
    Write-Host "`nStopping herdr-outpost services..." -ForegroundColor Yellow
    if ($RelayJob -and -not $RelayJob.HasExited) {
        Stop-Process -Id $RelayJob.Id -Force -ErrorAction SilentlyContinue
    }
    if ($TunnelJob -and -not $TunnelJob.HasExited) {
        Stop-Process -Id $TunnelJob.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host "herdr-outpost relay stopped." -ForegroundColor Green
}
