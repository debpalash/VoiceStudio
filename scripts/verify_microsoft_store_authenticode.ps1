param(
  [Parameter(Mandatory = $true)][string]$Installer,
  [Parameter(Mandatory = $true)][string]$UnpackedDirectory,
  [Parameter(Mandatory = $true)][string]$ExpectedPublisher,
  [Parameter(Mandatory = $true)][string]$Output
)

$ErrorActionPreference = 'Stop'
$installerItem = Get-Item -LiteralPath $Installer
$unpackedItem = Get-Item -LiteralPath $UnpackedDirectory
$files = @($installerItem) + @(
  Get-ChildItem -LiteralPath $unpackedItem.FullName -Recurse -File |
    Where-Object { $_.Extension -in @('.exe', '.dll', '.node') }
)

if ($files.Count -lt 2) {
  throw 'Expected the installer and packaged PE files'
}

$records = foreach ($file in $files) {
  $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
  if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) {
    throw "Invalid Authenticode signature: $($file.FullName) ($($signature.Status))"
  }
  [ordered]@{
    file = if ($file.FullName -eq $installerItem.FullName) {
      $installerItem.Name
    } else {
      [IO.Path]::GetRelativePath($unpackedItem.FullName, $file.FullName).Replace('\', '/')
    }
    sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    status = $signature.Status.ToString()
    subject = $signature.SignerCertificate.Subject
    thumbprint = $signature.SignerCertificate.Thumbprint
  }
}

$installerSubject = $records[0].subject
$organizationMatch = [regex]::Match($installerSubject, '(?:^|,\s*)O=(?<organization>(?:\\.|[^,])*)(?:,|$)')
if (-not $organizationMatch.Success) {
  throw "Installer signer '$installerSubject' does not contain an organization (O) attribute"
}
$installerOrganization = $organizationMatch.Groups['organization'].Value
if ($installerOrganization -cne $ExpectedPublisher) {
  throw "Installer signer '$installerSubject' does not match '$ExpectedPublisher'"
}

[ordered]@{
  schemaVersion = 1
  verifiedAt = [DateTime]::UtcNow.ToString('o')
  expectedPublisher = $ExpectedPublisher
  installer = $installerItem.Name
  files = $records
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Output -Encoding utf8

Write-Host "PASS: $($files.Count) Authenticode signatures are valid; installer signer matches $ExpectedPublisher"
