# starts the public tunnel and points candidate links at it.
# run from the repository root: powershell -File scripts\go-public.ps1
# a quick tunnel gets a new address every time it starts, so run this again after a restart

$ErrorActionPreference = 'Stop'

docker compose --profile public up -d tunnel | Out-Null

$url = $null
for ($i = 0; $i -lt 30 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    $m = docker compose logs tunnel 2>&1 | Select-String -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches
    if ($m) { $url = $m[-1].Matches[-1].Value }
}
if (-not $url) { throw 'The tunnel did not report an address. See: docker compose logs tunnel' }

$env_path = Join-Path (Get-Location) '.env'
$lines = Get-Content $env_path
$current = ($lines | Where-Object { $_ -like 'PUBLIC_BASE_URL=*' }) -replace '^PUBLIC_BASE_URL=', ''
if ($current -ne $url) {
    $lines = $lines -replace '^PUBLIC_BASE_URL=.*', "PUBLIC_BASE_URL=$url"
    # no bom (compose would read it as part of the first name) and keep the lf endings
    $text = ($lines -join "`n") + "`n"
    [IO.File]::WriteAllText($env_path, $text, (New-Object Text.UTF8Encoding($false)))
    # compose only reads .env when a container is created
    docker compose up -d --force-recreate backend worker | Out-Null
}

Write-Host ''
Write-Host "Public address:  $url"
Write-Host "LiveKit webhook: $url/api/v1/livekit/webhook"
Write-Host ''
Write-Host 'Links already sent point at the old address. Reschedule to send a new one.'
