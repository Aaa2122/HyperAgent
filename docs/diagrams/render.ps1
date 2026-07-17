$ErrorActionPreference = "Stop"

$diagramDirectory = $PSScriptRoot
$assetDirectory = Join-Path (Split-Path $diagramDirectory -Parent) "assets"
$renderer = "https://kroki.io/plantuml/png"

Get-ChildItem $diagramDirectory -Filter "*.puml" | ForEach-Object {
    $output = Join-Path $assetDirectory ($_.BaseName + ".png")
    Write-Host "Rendering $($_.Name) -> $output"
    curl.exe `
        --fail `
        --silent `
        --show-error `
        --retry 3 `
        --retry-all-errors `
        --connect-timeout 10 `
        --max-time 45 `
        -X POST `
        -H "Content-Type: text/plain; charset=utf-8" `
        --data-binary "@$($_.FullName)" `
        $renderer `
        --output $output
}

Write-Host "PlantUML diagrams rendered successfully."
