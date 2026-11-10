param(
    [string]$ExePath,
    [string]$DocPath,
    [string]$ViewsPath,
    [string]$RunRoot
)
$ErrorActionPreference = 'Stop'
$packageRoot = Split-Path -Parent $PSScriptRoot
if (-not $ExePath) {
    $ExePath = Join-Path $packageRoot 'CodeAgent.exe'
    if (-not (Test-Path -LiteralPath $ExePath)) { $ExePath = Join-Path $packageRoot 'dist\CodeAgent.exe' }
}
if (-not $DocPath) {
    $DocPath = Join-Path $packageRoot 'input\Architecture_Documentation.md'
    if (-not (Test-Path -LiteralPath $DocPath)) { $DocPath = Join-Path (Split-Path -Parent $packageRoot) 'Task (1)\Architecture_Documentation.md' }
}
if (-not $ViewsPath) {
    $ViewsPath = Join-Path $packageRoot 'input\Architecture_View.md'
    if (-not (Test-Path -LiteralPath $ViewsPath)) { $ViewsPath = Join-Path (Split-Path -Parent $packageRoot) 'Task (1)\Architecture_View.md' }
}
foreach ($required in @($ExePath,$DocPath,$ViewsPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "File not found: $required" }
}
if (-not $RunRoot) { $RunRoot = Join-Path $PSScriptRoot ('offline_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff')) }
$RunRoot = [System.IO.Path]::GetFullPath($RunRoot)
if (Test-Path -LiteralPath $RunRoot) { throw "Use a new, empty run path: $RunRoot" }
$appDir = Join-Path $RunRoot 'app'
$inputDir = Join-Path $RunRoot 'input'
$outDir = Join-Path $RunRoot 'output'
$evidence = Join-Path $RunRoot 'evidence'
$tempDir = Join-Path $RunRoot 'temp'
foreach ($dir in @($appDir,$inputDir,$outDir,$evidence,$tempDir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$testExe = Join-Path $appDir 'CodeAgent.exe'
$testDoc = Join-Path $inputDir 'Architecture_Documentation.md'
$testViews = Join-Path $inputDir 'Architecture_View.md'
Copy-Item -LiteralPath $ExePath -Destination $testExe
Copy-Item -LiteralPath $DocPath -Destination $testDoc
Copy-Item -LiteralPath $ViewsPath -Destination $testViews
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $testExe
$startInfo.WorkingDirectory = $appDir
$startInfo.UseShellExecute = $false
$startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$startInfo.CreateNoWindow = $true
$argsForChild = @('--offline-self-test','--doc',$testDoc,'--views',$testViews,'--out',$outDir,'--evidence',$evidence)
# Win32 file names cannot contain a double quote; quoting protects spaces in paths.
$startInfo.Arguments = ($argsForChild | ForEach-Object { '"' + $_ + '"' }) -join ' '
$startInfo.EnvironmentVariables['PATH'] = "$env:SystemRoot\System32"
$startInfo.EnvironmentVariables['APPDATA'] = Join-Path $evidence 'profile'
$startInfo.EnvironmentVariables['LOCALAPPDATA'] = Join-Path $RunRoot 'local'
$startInfo.EnvironmentVariables['TEMP'] = $tempDir
$startInfo.EnvironmentVariables['TMP'] = $tempDir
foreach ($name in @('PYTHONHOME','PYTHONPATH','DEEPSEEK_API_KEY')) { $startInfo.EnvironmentVariables.Remove($name) }
$process = [System.Diagnostics.Process]::Start($startInfo)
$watch = [System.Diagnostics.Stopwatch]::StartNew()
try {
    while (-not $process.WaitForExit(1000)) {
        if ($watch.Elapsed.TotalSeconds -gt 120) { throw 'Offline acceptance timed out after 120 seconds.' }
    }
    $code = $process.ExitCode
} finally {
    if (-not $process.HasExited) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F | Out-Null
    }
    $process.Dispose()
}
$resultPath = Join-Path $evidence 'acceptance_result.json'
if (-not (Test-Path -LiteralPath $resultPath)) { throw "No acceptance result was produced (exit code $code)." }
$result = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
$bmpPath = Join-Path $evidence 'completed.bmp'
if (Test-Path -LiteralPath $bmpPath) {
    Add-Type -AssemblyName System.Drawing
    $picture = [System.Drawing.Image]::FromFile($bmpPath)
    try { $picture.Save((Join-Path $evidence 'completed.png'),[System.Drawing.Imaging.ImageFormat]::Png) }
    finally { $picture.Dispose() }
}
[pscustomobject]@{
    Status=$result.status
    Frozen=$result.frozen
    ExternalTools=$result.external_tools
    ApiUsed=$result.api_used
    Checks=$result.checks
    Output=$result.report.output_dir
    Evidence=$evidence
    ExitCode=$code
} | ConvertTo-Json -Depth 5
if ($code -ne 0 -or $result.status -ne 'passed') { throw "Offline acceptance failed; inspect $resultPath" }
