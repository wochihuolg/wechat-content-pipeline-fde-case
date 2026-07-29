param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "skill\wechat-content-pipeline"
$codexRoot = if ($env:CODEX_HOME) {
    $env:CODEX_HOME
} else {
    Join-Path $env:USERPROFILE ".codex"
}
$skillsRoot = Join-Path $codexRoot "skills"
$target = Join-Path $skillsRoot "wechat-content-pipeline"

if (Test-Path -LiteralPath $target) {
    if (-not $Force) {
        throw "Skill already exists at $target. Rerun with -Force to update it."
    }
    Copy-Item -Path (Join-Path $source "*") -Destination $target -Recurse -Force
} else {
    New-Item -ItemType Directory -Force $skillsRoot | Out-Null
    Copy-Item -LiteralPath $source -Destination $skillsRoot -Recurse
}

Write-Host "Installed: $target"
