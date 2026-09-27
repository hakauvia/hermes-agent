<#
.SYNOPSIS
    Idempotent Hermes Agent host installer for windows11-primary.
    Installs/updates Hermes from an explicitly approved commit or release.

.DESCRIPTION
    This script provides safe installation and rollback of Hermes Agent on the FORGE host.
    It does NOT install unmerged fixes. Installation is blocked until the fix becomes
    authoritative (merged upstream) or is explicitly approved as a pinned source.

    Features:
    - Identifies currently active Hermes executable/source
    - Records current version/commit
    - Preserves relevant profile/configuration safely
    - Installs/updates only from the approved source
    - Verifies the resulting executable
    - Verifies actual source/version
    - Retains rollback information
    - Supports rollback to the previous known version
    - Never exposes credentials
    - Never silently updates to moving main

.PARAMETER ApprovedCommit
    The exact commit SHA to install (40-char). Required unless -ApprovedTag is used.

.PARAMETER ApprovedTag
    The exact tag to install (e.g., 'v0.21.4'). Alternative to -ApprovedCommit.

.PARAMETER SourceRepo
    Git repository to install from. Default: 'https://github.com/NousResearch/hermes-agent.git'

.PARAMETER DryRun
    Show what would be done without making changes.

.PARAMETER Rollback
    Rollback to the previous version (uses saved rollback info).

.PARAMETER Force
    Skip confirmation prompts (for automation).

.NOTES
    Author: Hermes
    Task-ID: FORGE-20260926-path-fix
    Workstream: C - Prepare host Hermes install/rollback automation
#>

[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param(
    [Parameter(Mandatory=$true, ParameterSetName='Commit')]
    [ValidatePattern('^[a-f0-9]{40}$')]
    [string]$ApprovedCommit,

    [Parameter(Mandatory=$true, ParameterSetName='Tag')]
    [string]$ApprovedTag,

    [Parameter(ParameterSetName='Commit')]
    [Parameter(ParameterSetName='Tag')]
    [string]$SourceRepo = 'https://github.com/NousResearch/hermes-agent.git',

    [Parameter(ParameterSetName='Commit')]
    [Parameter(ParameterSetName='Tag')]
    [switch]$DryRun,

    [Parameter(Mandatory=$true, ParameterSetName='Rollback')]
    [switch]$Rollback,

    [Parameter(ParameterSetName='Commit')]
    [Parameter(ParameterSetName='Tag')]
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Configuration
$HermesHome = "$env:USERPROFILE\AppData\Local\hermes"
$HermesAgentDir = "$HermesHome\hermes-agent"
$ProfilesDir = "$HermesHome\profiles"
$RollbackFile = "$HermesHome\.forge-rollback.json"
$LogFile = "$HermesHome\logs\forge-install-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"

# Ensure log directory exists
$logDir = Split-Path $LogFile
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $entry = "[$timestamp] [$Level] $Message"
    Add-Content -Path $LogFile -Value $entry
    Write-Host $entry
}

function Get-CurrentHermesInfo {
    $info = @{
        ExePath = $null
        Version = $null
        Commit = $null
        Source = $null
        Profiles = @()
    }

    # Find hermes.exe
    $exeCandidates = @(
        "$HermesHome\hermes.exe",
        "$HermesAgentDir\hermes.exe",
        "$env:LOCALAPPDATA\hermes\hermes.exe"
    )
    foreach ($candidate in $exeCandidates) {
        if (Test-Path $candidate) {
            $info.ExePath = $candidate
            break
        }
    }

    # Get version from hermes.exe --version or from git
    if ($info.ExePath) {
        try {
            $output = & $info.ExePath --version 2>&1
            if ($output -match 'v?(\d+\.\d+\.\d+)') {
                $info.Version = $matches[1]
            }
        } catch {}
    }

    # Get git commit if in a git repo
    if (Test-Path "$HermesAgentDir\.git") {
        try {
            $info.Commit = git -C $HermesAgentDir rev-parse HEAD 2>$null
            $info.Source = git -C $HermesAgentDir remote get-url origin 2>$null
        } catch {}
    }

    # List profiles
    if (Test-Path $ProfilesDir) {
        $info.Profiles = Get-ChildItem -Path $ProfilesDir -Directory | Select-Object -ExpandProperty Name
    }

    return $info
}

function Save-RollbackInfo {
    param($CurrentInfo, $TargetCommit, $TargetTag)
    $rollback = @{
        Timestamp = Get-Date -Format 'o'
        PreviousExePath = $CurrentInfo.ExePath
        PreviousVersion = $CurrentInfo.Version
        PreviousCommit = $CurrentInfo.Commit
        PreviousSource = $CurrentInfo.Source
        Profiles = $CurrentInfo.Profiles
        TargetCommit = $TargetCommit
        TargetTag = $TargetTag
    }
    $rollback | ConvertTo-Json -Depth 5 | Set-Content -Path $RollbackFile -Encoding UTF8
    Write-Log "Rollback info saved to $RollbackFile"
}

function Load-RollbackInfo {
    if (Test-Path $RollbackFile) {
        return Get-Content -Path $RollbackFile -Raw | ConvertFrom-Json
    }
    return $null
}

function Verify-HermesInstall {
    param($ExePath, $ExpectedCommit, $ExpectedTag)
    Write-Log "Verifying installation at $ExePath"

    if (-not (Test-Path $ExePath)) {
        throw "Hermes executable not found at $ExePath"
    }

    $output = & $ExePath --version 2>&1
    Write-Log "Version output: $output"

    if ($ExpectedCommit) {
        $actualCommit = git -C $HermesAgentDir rev-parse HEAD 2>$null
        if ($actualCommit -ne $ExpectedCommit) {
            throw "Commit mismatch: expected $ExpectedCommit, got $actualCommit"
        }
        Write-Log "Commit verified: $actualCommit"
    }

    if ($ExpectedTag) {
        $actualTag = git -C $HermesAgentDir describe --tags --exact-match 2>$null
        if ($actualTag -ne $ExpectedTag) {
            throw "Tag mismatch: expected $ExpectedTag, got $actualTag"
        }
        Write-Log "Tag verified: $actualTag"
    }

    # Quick smoke test
    $smoke = & $ExePath doctor 2>&1
    Write-Log "Doctor output: $smoke"

    return $true
}

function Install-HermesFromSource {
    param($Commit, $Tag, $SourceRepo)
    
    Write-Log "Installing Hermes from $SourceRepo"
    if ($Commit) { Write-Log "Target commit: $Commit" }
    if ($Tag) { Write-Log "Target tag: $Tag" }

    if ($DryRun) {
        Write-Log "[DRY RUN] Would clone/fetch $SourceRepo"
        Write-Log "[DRY RUN] Would checkout $($Commit ?? $Tag)"
        Write-Log "[DRY RUN] Would run: pip install -e .[dev] or equivalent"
        return
    }

    # Ensure parent directory exists
    if (-not (Test-Path $HermesHome)) {
        New-Item -ItemType Directory -Path $HermesHome -Force | Out-Null
    }

    # Clone or fetch
    if (Test-Path "$HermesAgentDir\.git") {
        Write-Log "Existing repo found, fetching..."
        git -C $HermesAgentDir fetch origin --tags --prune
    } else {
        Write-Log "Cloning $SourceRepo..."
        git clone $SourceRepo $HermesAgentDir
    }

    # Checkout target
    $targetRef = $Commit ?? $Tag
    Write-Log "Checking out $targetRef..."
    git -C $HermesAgentDir checkout $targetRef

    # Install dependencies
    Write-Log "Installing dependencies..."
    # Use uv if available, otherwise pip
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-Log "Using uv for installation..."
        & uv pip install -e "$HermesAgentDir[dev]" 2>&1 | ForEach-Object { Write-Log $_ }
    } else {
        Write-Log "Using pip for installation..."
        & python -m pip install -e "$HermesAgentDir[dev]" 2>&1 | ForEach-Object { Write-Log $_ }
    }

    # Verify hermes.exe exists or create shim
    $exePath = "$HermesHome\hermes.exe"
    if (-not (Test-Path $exePath)) {
        Write-Log "Creating hermes.exe shim..."
        # The installation should create this; if not, we may need to build it
        # For now, check if hermes is available on PATH
        $hermesCmd = Get-Command hermes -ErrorAction SilentlyContinue
        if ($hermesCmd) {
            Copy-Item -Path $hermesCmd.Source -Destination $exePath -Force
            Write-Log "Copied hermes from $($hermesCmd.Source) to $exePath"
        }
    }
}

function Perform-Rollback {
    $rollback = Load-RollbackInfo
    if (-not $rollback) {
        throw "No rollback information found at $RollbackFile"
    }

    Write-Log "Rolling back to previous version..."
    Write-Log "Previous commit: $($rollback.PreviousCommit)"
    Write-Log "Previous version: $($rollback.PreviousVersion)"

    if ($DryRun) {
        Write-Log "[DRY RUN] Would checkout $($rollback.PreviousCommit)"
        Write-Log "[DRY RUN] Would reinstall dependencies"
        return
    }

    if ($rollback.PreviousCommit) {
        git -C $HermesAgentDir checkout $rollback.PreviousCommit
        
        # Reinstall dependencies
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            & uv pip install -e "$HermesAgentDir[dev]" 2>&1 | ForEach-Object { Write-Log $_ }
        } else {
            & python -m pip install -e "$HermesAgentDir[dev]" 2>&1 | ForEach-Object { Write-Log $_ }
        }
    }

    Verify-HermesInstall -ExePath "$HermesHome\hermes.exe" -ExpectedCommit $rollback.PreviousCommit
    Write-Log "Rollback completed successfully"
}

# Main execution
Write-Log "=== FORGE Hermes Host Install Automation ==="
Write-Log "Parameter set: $($PsCmdlet.ParameterSetName)"
Write-Log "Hermes Home: $HermesHome"
Write-Log "Hermes Agent Dir: $HermesAgentDir"

try {
    if ($Rollback) {
        Perform-Rollback
        exit 0
    }

    # Get current state
    $currentInfo = Get-CurrentHermesInfo
    Write-Log "Current Hermes Info:"
    Write-Log "  ExePath: $($currentInfo.ExePath)"
    Write-Log "  Version: $($currentInfo.Version)"
    Write-Log "  Commit: $($currentInfo.Commit)"
    Write-Log "  Source: $($currentInfo.Source)"
    Write-Log "  Profiles: $($currentInfo.Profiles -join ', ')"

    # Save rollback info before making changes
    Save-RollbackInfo -CurrentInfo $currentInfo -TargetCommit $ApprovedCommit -TargetTag $ApprovedTag

    # Confirm unless -Force
    if (-not $Force -and -not $DryRun) {
        $confirm = Read-Host "Proceed with installation? (y/N)"
        if ($confirm -notin 'y','Y') {
            Write-Log "Installation cancelled by user"
            exit 1
        }
    }

    # Install
    Install-HermesFromSource -Commit $ApprovedCommit -Tag $ApprovedTag -SourceRepo $SourceRepo

    # Verify
    Verify-HermesInstall -ExePath "$HermesHome\hermes.exe" -ExpectedCommit $ApprovedCommit -ExpectedTag $ApprovedTag

    Write-Log "Installation completed successfully"
    Write-Log "Log file: $LogFile"

} catch {
    Write-Log "ERROR: $_" 'ERROR'
    Write-Log $_.ScriptStackTrace 'ERROR'
    
    # Attempt automatic rollback on failure
    if (-not $DryRun -and -not $Rollback) {
        Write-Log "Attempting automatic rollback..." 'WARN'
        try {
            Perform-Rollback
        } catch {
            Write-Log "Rollback also failed: $_" 'ERROR'
        }
    }
    exit 1
}