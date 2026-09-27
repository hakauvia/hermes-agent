<#
.SYNOPSIS
    FORGE Evidence - Locate sanitized evidence for a run.

.DESCRIPTION
    Finds and displays evidence summary for a specific run or the latest run.

.EXAMPLE
    forge-evidence

.EXAMPLE
    forge-evidence -RunId "forge-hermes-activation-abc123"

.EXAMPLE
    forge-evidence -ControlPlaneRoot "C:\FORGE\control\hermes-control-plane"

.NOTES
    Part of M4 local operating commands (Workstream K).
    No model calls. Deterministic.
#>

param(
    [Parameter(Mandatory=$false)]
    [string]$RunId,
    
    [Parameter(Mandatory=$false)]
    [string]$ControlPlaneRoot = "C:\Users\mhakh\FORGE\control\hermes-control-plane",
    
    [Parameter(Mandatory=$false)]
    [string]$HermesBin = "hermes"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$pythonScript = Join-Path $scriptDir "forge_commands.py"

if (-not (Test-Path $pythonScript)) {
    Write-Error "forge_commands.py not found at $pythonScript"
    exit 1
}

$python = "python"
$command = "$python `"$pythonScript`" evidence"
if ($RunId) {
    $command += " `"$RunId`""
}

& $python $pythonScript evidence $RunId --control-plane-root $ControlPlaneRoot --hermes-bin $HermesBin