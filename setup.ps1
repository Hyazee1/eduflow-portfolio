param([string]$Python312 = "", [switch]$SkipContainers)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $projectPython)) {
    if ($Python312) {
        & $Python312 -m venv .venv
    } elseif (Test-Path -LiteralPath "$env:USERPROFILE\miniconda3\envs\py312\python.exe") {
        & "$env:USERPROFILE\miniconda3\envs\py312\python.exe" -m venv .venv
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv .venv
    } else {
        throw "请安装 Python 3.12，再执行 .\setup.ps1 -Python312 '解释器绝对路径'。"
    }
    if ($LASTEXITCODE -ne 0) { throw "创建 Python 3.12 虚拟环境失败" }
}
$projectVersion = & $projectPython -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
if ($projectVersion -ne "3.12") { throw "当前项目虚拟环境不是 Python 3.12，请按部署说明重建。" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "请先安装 Node.js（项目已验证 Node 26）。" }
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw "请先安装包含 npm 的 Node.js。" }
Write-Host "安装项目锁定的 Python、npm 和测试依赖…"
& $projectPython -m pip install --require-hashes --only-binary=:all: -r backend/tooling.lock.txt
if ($LASTEXITCODE -ne 0) { throw "项目安装工具更新失败" }
& $projectPython -m pip install -r backend/requirements-dev.lock.txt
if ($LASTEXITCODE -ne 0) { throw "Python 项目依赖安装失败" }
Push-Location -LiteralPath (Join-Path $PSScriptRoot "frontend")
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw "npm 项目依赖安装失败" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }
} finally { Pop-Location }
if (-not (Test-Path -LiteralPath ".env")) {
    $configScript = @'
from pathlib import Path
import secrets
password = secrets.token_hex(24)
text = Path(".env.example").read_text(encoding="utf-8")
text = text.replace("eduflow_local", password)
text = text.replace("replace-with-at-least-32-random-characters", secrets.token_urlsafe(48))
with Path(".env").open("x", encoding="utf-8") as target:
    target.write(text)
print("已生成本地 .env，未输出任何密钥。")
'@
    $configScript | & $projectPython -
    if ($LASTEXITCODE -ne 0) { throw "生成环境配置失败" }
}
if (-not $SkipContainers) {
    & $projectPython run.py --prepare-only
    if ($LASTEXITCODE -ne 0) { throw "容器与数据库初始化未完成，请查看上方提示" }
}
Write-Host "项目依赖与前端已准备完成。执行 python run.py 或双击 run.bat。"
