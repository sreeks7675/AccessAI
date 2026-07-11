# Activate venv and run pytest for the integration tests folder
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $repo
if (Test-Path .\venv\Scripts\Activate.ps1) {
    . .\venv\Scripts\Activate.ps1
}
$env:PYTHONPATH = "$repo"
pytest tests/tests_for_integration_branch -q
Pop-Location
