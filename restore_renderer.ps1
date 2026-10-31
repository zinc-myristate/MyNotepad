# Roll the packaged app's renderer back to the backup taken before a hot update.
#   powershell -ExecutionPolicy Bypass -File restore_renderer.ps1 -Backup dist\_renderer-backup-<name> [-VerifyOnly]
# Every entry is checked before anything is written, so a refusal leaves all files untouched.
# Re-running after a partial failure is safe: entries already back at the original are skipped.
param(
    [Parameter(Mandatory=$true)][string]$Backup,
    [switch]$VerifyOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$distRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'dist'))
$bundleRoot = [IO.Path]::GetFullPath((Join-Path $distRoot 'MyNotepad\_internal\renderer'))
$backupRoot = (Resolve-Path -LiteralPath $Backup).Path
if (!$backupRoot.StartsWith($distRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Backup must be inside this project dist directory.'
}
$manifest = Get-Content -LiteralPath (Join-Path $backupRoot 'manifest.json') -Raw | ConvertFrom-Json
if (!$manifest) { throw 'Empty backup manifest.' }
$checked = 0
$operations = foreach ($entry in $manifest) {
    $checked++
    $target = [IO.Path]::GetFullPath((Join-Path $bundleRoot $entry.Path))
    $original = [IO.Path]::GetFullPath((Join-Path $backupRoot $entry.Path))
    if (!$target.StartsWith($bundleRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        !$original.StartsWith($backupRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Manifest path escapes renderer or backup directory.'
    }
    $current = if (Test-Path -LiteralPath $target -PathType Leaf) { (Get-FileHash -LiteralPath $target).Hash } else { $null }
    if ($current -eq $entry.OriginalHash) { continue }
    if ($current -ne $entry.InstalledHash) {
        throw "Installed file changed since update; refusing to overwrite: $($entry.Path)"
    }
    if ($entry.OriginalHash -and (!(Test-Path -LiteralPath $original -PathType Leaf) -or
        (Get-FileHash -LiteralPath $original).Hash -ne $entry.OriginalHash)) {
        throw "Backup verification failed: $($entry.Path)"
    }
    [pscustomobject]@{ Target=$target; Original=$original; OriginalHash=$entry.OriginalHash }
}
if ($VerifyOnly) {
    Write-Output "Verified $checked renderer files; no files changed."
    return
}
if (Get-Process -Name MyNotepad -ErrorAction SilentlyContinue) {
    throw 'Close MyNotepad before restoring the renderer.'
}
foreach ($operation in $operations) {
    if ($operation.OriginalHash) {
        Copy-Item -LiteralPath $operation.Original -Destination $operation.Target
        if ((Get-FileHash -LiteralPath $operation.Target).Hash -ne $operation.OriginalHash) {
            throw "Restore verification failed: $($operation.Target)"
        }
    } else {
        # A single verified file introduced by the update; never remove directories or note data.
        Remove-Item -LiteralPath $operation.Target
    }
}
Write-Output "Restored $(@($operations).Count) renderer files. Note data was not changed."
