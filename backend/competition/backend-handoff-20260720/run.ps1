# 本地开发启动脚本 (PowerShell 原生版，无需 Git Bash / WSL)。
#
# 与 run.sh 等价行为：
#   - 不需要 MySQL / vLLM：默认 SQLite + DeepSeek API
#   - 自动补齐 Python/Node 依赖
#   - 前后端写 PID 到 .run/，下次启动时清理
#
# 用法（在 PowerShell 中）：
#   .\run.ps1         # 启动
#   .\run.ps1 stop    # 停止
#   .\run.ps1 deps    # 仅补依赖

[CmdletBinding()]
param(
    [string]$Cmd = "start"
)

$ErrorActionPreference = "Stop"

$Script:Root       = (Resolve-Path "$PSScriptRoot").Path
$Script:PidDir     = Join-Path $Script:Root ".run"
$Script:LogDir     = Join-Path $Script:Root ".run/logs"
$Script:BackendPid = Join-Path $Script:PidDir "backend.pid"
$Script:FrontPid   = Join-Path $Script:PidDir "frontend.pid"

New-Item -ItemType Directory -Force -Path $Script:PidDir | Out-Null
New-Item -ItemType Directory -Force -Path $Script:LogDir | Out-Null

function Stop-Existing {
    foreach ($pf in @($Script:BackendPid, $Script:FrontPid)) {
        $name = [System.IO.Path]::GetFileNameWithoutExtension($pf)
        if (Test-Path $pf) {
            $pid_v = (Get-Content $pf).Trim()
            $proc = Get-Process -Id $pid_v -ErrorAction SilentlyContinue
            if ($proc) {
                try { Stop-Process -Id $pid_v -Force } catch {}
                Write-Host "==> stopped $name (pid=$pid_v)"
            }
            Remove-Item -Force $pf
        }
    }
}

function Initialize-BackendDeps {
    $need = @("fastapi","uvicorn","sqlalchemy","langgraph","httpx","fastapi_mail","exa_py")
    $missing = @()
    foreach ($m in $need) {
        $check = python -c "import $m" 2>&1
        if ($LASTEXITCODE -ne 0) { $missing += $m }
    }
    if ($missing.Count -eq 0) { return }

    # 哪个 python 在干活，先打出来，避免 pip 装到错的环境里。
    $pyExe = (Get-Command python -ErrorAction Stop).Source
    Write-Host "==> 后端 Python 依赖缺失: $($missing -join ', ')"
    Write-Host "==> 准备安装到: $pyExe"

    # 中国大陆走清华镜像；若本机 pip config 已设过 index-url，把通用行 -i 抽掉
    # 留 pipconfig 默认的源，避开重复参数冲突。
    $globalIndex = ""
    try { $globalIndex = (python -m pip config get global.index-url 2>&1 | Out-String).Trim() }
    catch { $globalIndex = "" }

    if ($globalIndex -and $globalIndex -notmatch "(?i)error|warning") {
        Write-Host "==> 检测到 pip global.index-url=$globalIndex，沿用本地配置"
        $pipExtra = @()
    } else {
        Write-Host "==> 走清华 PyPI 镜像"
        $pipExtra = @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "--timeout", "30", "--retries", "2")
    }

    python -m pip install fastapi "uvicorn[standard]" sqlalchemy pymysql langgraph `
        "python-jose[cryptography]" "passlib[argon2]" fastapi-mail exa-py `
        python-multipart email-validator python-docx numpy httpx @pipExtra | Out-Null

    # 再核验一次
    $still = @()
    foreach ($m in $need) {
        $check = python -c "import $m" 2>&1
        if ($LASTEXITCODE -ne 0) { $still += $m }
    }
    if ($still.Count -gt 0) {
        throw "依赖安装后仍缺失: $($still -join ', ')。请手动跑：`python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi uvicorn sqlalchemy pymysql langgraph python-jose[cryptography] passlib[argon2] fastapi-mail exa-py python-multipart email-validator python-docx numpy httpx`"
    }
    Write-Host "==> 依赖安装完成"
}

function Initialize-FrontendDeps {
    if (Test-Path (Join-Path $Script:Root "frontend/llm/node_modules")) { return }
    Write-Host "==> 前端 node_modules 缺失，正在 npm install ..."
    Push-Location (Join-Path $Script:Root "frontend/llm")
    try { npm install | Out-Null } finally { Pop-Location }
}

function Write-Pid($pidVal, $path) {
    [int]$pidVal | Set-Content -Path $path -Encoding ASCII
}

function Start-Backend {
    Write-Host "==> starting backend (uvicorn APP.backend.main:app)"
    $proc = Start-Process -FilePath "python" `
        -ArgumentList @("-m","uvicorn","APP.backend.main:app","--host","0.0.0.0","--port","8000") `
        -WorkingDirectory $Script:Root `
        -RedirectStandardOutput (Join-Path $Script:LogDir "backend.log") `
        -RedirectStandardError  (Join-Path $Script:LogDir "backend.err") `
        -NoNewWindow -PassThru
    Write-Pid $proc.Id $Script:BackendPid
    Write-Host "    pid=$($proc.Id), log=$Script:LogDir\backend.log"
}

function Start-Frontend {
    Write-Host "==> starting frontend (vite dev)"
    Push-Location (Join-Path $Script:Root "frontend/llm")
    try {
        # npm 在 Windows 上是 npm.cmd；先查 PATH（兼容 PS 5.1，避免使用 ?.Source）。
        $npm = $null
        $cmd = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
        if ($cmd) { $npm = $cmd.Source }
        if (-not $npm) {
            $cmd = Get-Command "npm" -ErrorAction SilentlyContinue
            if ($cmd) { $npm = $cmd.Source }
        }
        if (-not $npm) { throw "npm 未安装或不在 PATH；先装 Node.js LTS" }

        $proc = Start-Process -FilePath $npm `
            -ArgumentList @("run","dev","--","--host") `
            -WorkingDirectory (Get-Location) `
            -RedirectStandardOutput (Join-Path $Script:LogDir "frontend.log") `
            -RedirectStandardError  (Join-Path $Script:LogDir "frontend.err") `
            -NoNewWindow -PassThru
    } finally {
        Pop-Location
    }
    Write-Pid $proc.Id $Script:FrontPid
    Write-Host "    pid=$($proc.Id), log=$Script:LogDir\frontend.log"
}

# ---------- 主流程 ----------

switch ($Cmd) {
    "stop" {
        Stop-Existing
        Write-Host "==> all stopped."
        return
    }
    "deps" {
        Initialize-BackendDeps
        Initialize-FrontendDeps
        Write-Host "==> 依赖就绪"
        return
    }
    "start" {
        Stop-Existing
    }
    default {
        Write-Host "用法: .\run.ps1 [start|stop|deps]"
        exit 1
    }
}

Set-Location $Script:Root
Initialize-BackendDeps
Initialize-FrontendDeps

Start-Backend
Start-Frontend

@"
启动完成：
  - 后端 Swagger UI : http://127.0.0.1:8000/docs
  - 前端界面        : http://127.0.0.1:5173
  - 默认管理员账号  : admin / Admin@123456

查看日志：
  Get-Content -Path "$Script:LogDir\backend.log" -Wait
  Get-Content -Path "$Script:LogDir\frontend.log" -Wait

停止服务：
  .\run.ps1 stop
"@

Write-Host "`n按 Ctrl+C 终止前台日志；关闭窗口也会结束本会话（后端/前端仍在后台）。"
Write-Host "如需彻底清理，请运行 .\run.ps1 stop`n"

# 保持前台会话，便于观察 PID/日志路径；用户 Ctrl+C 后进程仍在后台。
Read-Host "按 Enter 退出此会话（不会停服务）"
