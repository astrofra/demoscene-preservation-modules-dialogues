param(
  [Parameter(Mandatory = $true)]
  [string]$Name,

  [Parameter(Mandatory = $true)]
  [string[]]$Pattern
)

$matches = Get-CimInstance Win32_Process | Where-Object {
  if ($_.Name -ne $Name) {
    return $false
  }

  $commandLine = $_.CommandLine
  if (-not $commandLine) {
    return $false
  }

  foreach ($item in $Pattern) {
    if ($commandLine -notmatch $item) {
      return $false
    }
  }

  return $true
}

if ($matches) {
  exit 0
}

exit 1
