param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot), [switch]$CreateEnvironment)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath
$principal = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$system = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')

function Assert-RegularPath([string]$Path) {
    $item = Get-Item -Force -LiteralPath $Path -ErrorAction SilentlyContinue
    if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Local configuration cannot use a reparse point.'
    }
}

function Set-PrivateAccess([string]$Path, [bool]$Directory) {
    Assert-RegularPath $Path
    if ($Directory) {
        $acl = [System.Security.AccessControl.DirectorySecurity]::new()
        $inherit = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    } else {
        $acl = [System.Security.AccessControl.FileSecurity]::new()
        $inherit = [System.Security.AccessControl.InheritanceFlags]::None
    }
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($identity in @($principal, $system)) {
        $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
            $identity, 'FullControl', $inherit, 'None', 'Allow'))
    }
    $item = Get-Item -Force -LiteralPath $Path
    # Persist only the DACL. Reading or replacing the audit ACL would require administrator privileges.
    if ($item.PSObject.Methods['SetAccessControl']) {
        $item.SetAccessControl($acl)
    } else {
        [IO.FileSystemAclExtensions]::SetAccessControl($item, $acl)
    }
}

$runtime = Join-Path $ProjectRoot '.runtime'
$private = Join-Path $runtime 'private'
Assert-RegularPath $runtime
Assert-RegularPath $private
[void][IO.Directory]::CreateDirectory($private)
Set-PrivateAccess $private $true
$envFile = Join-Path $ProjectRoot '.env'
Assert-RegularPath $envFile
if (Test-Path -LiteralPath $envFile -PathType Leaf) {
    Set-PrivateAccess $envFile $false
} elseif ($CreateEnvironment) {
    # Moving a protected file on the same NTFS volume publishes its complete contents and DACL together.
    $scratch = Join-Path $private ([IO.Path]::GetRandomFileName())
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $password = [byte[]]::new(24)
        $signingKey = [byte[]]::new(48)
        $rng.GetBytes($password)
        $rng.GetBytes($signingKey)
        $template = [IO.File]::ReadAllText((Join-Path $ProjectRoot '.env.example'))
        $template = $template.Replace('eduflow_local', [BitConverter]::ToString($password).Replace('-', ''))
        $template = $template.Replace('replace-with-at-least-32-random-characters', [BitConverter]::ToString($signingKey).Replace('-', ''))
        $stream = [IO.File]::Open($scratch, 'CreateNew', 'Write', 'None')
        $stream.Dispose()
        Set-PrivateAccess $scratch $false
        [IO.File]::WriteAllText($scratch, $template, [Text.UTF8Encoding]::new($false))
        try { [IO.File]::Move($scratch, $envFile) } catch [IO.IOException] {
            if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) { throw }
            Set-PrivateAccess $envFile $false
        }
    } finally {
        $rng.Dispose()
        if ([IO.File]::Exists($scratch)) { [IO.File]::Delete($scratch) }
    }
}
