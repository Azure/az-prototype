# postToolUse hook — runs Python type checking and linting after file modifications.
# Receives JSON input on stdin with tool execution details.

$Input = $input | Out-String

try {
    $data = $Input | ConvertFrom-Json
    $toolName = $data.toolName
} catch {
    $toolName = ""
}

# Only run on file-modifying tools
$fileTools = @("create_file", "replace_string_in_file", "multi_replace_string_in_file", "edit_notebook_file")
if ($toolName -notin $fileTools) {
    exit 0
}

# Activate venv if present
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & .venv\Scripts\Activate.ps1
}

$errors = 0

# --- Linting with ruff ---
if (Get-Command ruff -ErrorAction SilentlyContinue) {
    Write-Host "Running ruff linter..."
    $ruffOutput = ruff check azext_prototype\ tests\ --no-fix 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: Ruff found linting issues."
        Write-Host $ruffOutput
        $errors = 1
    } else {
        Write-Host "OK: Ruff clean."
    }
}

# --- Type checking with mypy ---
if (Get-Command mypy -ErrorAction SilentlyContinue) {
    Write-Host "Running mypy type checker..."
    $mypyOutput = mypy azext_prototype\ --ignore-missing-imports --no-error-summary 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: mypy found type issues."
        Write-Host $mypyOutput
        $errors = 1
    } else {
        Write-Host "OK: mypy clean."
    }
}

# --- Type checking with pyright (Pylance) ---
if (Get-Command pyright -ErrorAction SilentlyContinue) {
    Write-Host "Running pyright type checker..."
    $pyrightOutput = pyright azext_prototype\ 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: pyright found type issues."
        Write-Host $pyrightOutput
        $errors = 1
    } else {
        Write-Host "OK: pyright clean."
    }
}

if ($errors -ne 0) {
    Write-Host "postToolUse: issues found. Please review above."
    exit 1
}

Write-Host "All checks passed."
exit 0
