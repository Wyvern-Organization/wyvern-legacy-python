param(
  [switch]$NoTunnel
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'wyvern-backend'
$healthUrl = 'http://127.0.0.1:8009/health'
$cloudflaredCandidates = @(
  'C:\Program Files (x86)\cloudflared\cloudflared.exe',
  'C:\Program Files\cloudflared\cloudflared.exe'
)

function Test-ProcessMatches {
  param(
    [string]$Pattern
  )

  $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
  return $null -ne ($processes | Where-Object {
    $_.CommandLine -and $_.CommandLine -match $Pattern
  } | Select-Object -First 1)
}

Write-Host 'Starting ASPC backend...'
Set-Location $backendDir
docker compose up -d --build

for ($i = 0; $i -lt 30; $i++) {
  try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    if ($health.ok) {
      break
    }
  } catch {
    Start-Sleep -Seconds 2
    continue
  }

  Start-Sleep -Seconds 2
}

if (-not $NoTunnel) {
  $cloudflaredExe = $null
  foreach ($candidate in $cloudflaredCandidates) {
    if (Test-Path $candidate) {
      $cloudflaredExe = $candidate
      break
    }
  }

  if (-not $cloudflaredExe) {
    $command = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($command) {
      $cloudflaredExe = $command.Source
    }
  }

  if ($cloudflaredExe) {
    $pattern = '127\.0\.0\.1:8009'
    if (-not (Test-ProcessMatches -Pattern $pattern)) {
      Start-Process -WindowStyle Hidden -FilePath $cloudflaredExe -ArgumentList @(
        'tunnel',
        '--url', 'http://127.0.0.1:8009',
        '--no-autoupdate'
      )
      Write-Host 'Started cloudflared tunnel for ASPC.'
    } else {
      Write-Host 'cloudflared tunnel for ASPC is already running.'
    }
  } else {
    Write-Warning 'cloudflared was not found on this machine.'
  }
}

Write-Host 'ASPC is ready.'
Write-Host 'Health: http://127.0.0.1:8009/health'
