# Segregator - probe 01: host hardware + census of the export folder.
# ВАЖНО: скрипт НЕ читает содержимое документов и НЕ пишет имена файлов.
# В отчёт попадают только: железо, установленный софт, счётчики и размеры.
#
# Порядок секций намеренный: перепись экспорта идёт ПЕРВОЙ из тяжёлых,
# потому что прошлый прогон оборвался на поиске моделей и до неё не дошёл.
# Поиск моделей по профилю теперь по явному ключу -ScanModels.
#
#   .\01_host_and_export.ps1              # быстро, с переписью экспорта
#   .\01_host_and_export.ps1 -ScanModels  # плюс получасовой обход профиля

[CmdletBinding()]
param(
  [switch]$ScanModels,
  [string]$ExportDir = 'C:\Users\Huawei\Downloads\Telegram Desktop\JDG',
  [string]$OutFile                       # по умолчанию probe/probe_result.txt
)

$ErrorActionPreference = 'SilentlyContinue'
# Консоль Windows тут в cp1250 — без этого финальные строки идут кракозябрами.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
if ($OutFile) { $out = $OutFile } else { $out = Join-Path $PSScriptRoot 'probe_result.txt' }
$export = $ExportDir

function W($s) { $s | Out-File -FilePath $out -Append -Encoding utf8 }
if (Test-Path $out) { Remove-Item $out }

W "SEGREGATOR PROBE 01"
W ("generated: " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
W ""

# ---------------------------------------------------------------- EXPORT ----
# Первой строкой — то, ради чего скрипт вообще существует.
W "== EXPORT CENSUS (только счётчики, без имён файлов) =="
if (-not (Test-Path $export)) { W ("НЕТ ПАПКИ: " + $export) }
else {
  W ("path        : " + $export)

  $rj = Join-Path $export 'result.json'
  $mh = Join-Path $export 'messages.html'
  $hasJson = Test-Path $rj
  $hasHtml = Test-Path $mh

  # Формат экспорта решает весь дизайн разбора, поэтому он идёт первым.
  if ($hasJson) { W ("format      : JSON  (result.json, {0:N2} MB)" -f ((Get-Item $rj).Length/1MB)) }
  elseif ($hasHtml) { W "format      : HTML  (messages.html, JSON-экспорта НЕТ)" }
  else { W "format      : НЕ ОПРЕДЕЛЁН (нет ни result.json, ни messages.html)" }
  if ($hasJson -and $hasHtml) { W "              (есть и messages.html — берём JSON)" }

  W "subfolders  :"
  Get-ChildItem $export -Directory | ForEach-Object {
    $n = (Get-ChildItem $_.FullName -Recurse -File | Measure-Object).Count
    $s = (Get-ChildItem $_.FullName -Recurse -File | Measure-Object Length -Sum).Sum
    W ("       {0,-22} files={1,-6} size={2:N2} GB" -f $_.Name, $n, ($s/1GB))
  }

  $all = Get-ChildItem $export -Recurse -File
  W ("total_files : " + $all.Count)
  W ("total_size  : {0:N2} GB" -f (($all | Measure-Object Length -Sum).Sum/1GB))
  W ("oldest      : " + ($all | Sort-Object LastWriteTime | Select-Object -First 1).LastWriteTime)
  W ("newest      : " + ($all | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime)
  W ""

  # Размер вложений без result.json/messages.html — это и есть S из расчёта
  # места под архив (blobs + дерево). Дедупликация его только уменьшит.
  $payload = $all | Where-Object { $_.Name -ne 'result.json' -and $_.Extension -ne '.html' }
  W ("attachments_bytes : {0:N2} GB  <- S, от него считается место под архив" -f (($payload | Measure-Object Length -Sum).Sum/1GB))
  W ""

  W "by_extension:"
  $all | Group-Object Extension | Sort-Object Count -Descending | ForEach-Object {
    $s = ($_.Group | Measure-Object Length -Sum).Sum
    W ("       {0,-10} count={1,-6} size={2:N2} MB" -f $_.Name, $_.Count, ($s/1MB))
  }
  W ""

  W "size_buckets:"
  $b = [ordered]@{ '<100KB'=0; '100KB-1MB'=0; '1-5MB'=0; '5-20MB'=0; '>20MB'=0 }
  foreach ($f in $all) {
    if     ($f.Length -lt 100KB) { $b['<100KB']++ }
    elseif ($f.Length -lt 1MB)   { $b['100KB-1MB']++ }
    elseif ($f.Length -lt 5MB)   { $b['1-5MB']++ }
    elseif ($f.Length -lt 20MB)  { $b['5-20MB']++ }
    else                          { $b['>20MB']++ }
  }
  foreach ($k in $b.Keys) { W ("       {0,-10} {1}" -f $k, $b[$k]) }
  W ""

  W "by_month (по дате файла):"
  $all | Group-Object { $_.LastWriteTime.ToString('yyyy-MM') } | Sort-Object Name | ForEach-Object {
    W ("       {0}  {1}" -f $_.Name, $_.Count)
  }
  W ""

  # Потоковый подсчёт по result.json: файл читается построчно через
  # StreamReader, целиком в память не грузится. Считаются ТОЛЬКО вхождения
  # структурных ключей — ни одного значения в отчёт не попадает.
  if ($hasJson) {
    W "result.json (потоковый подсчёт ключей, эвристика — значения не читаются):"
    $msg = 0; $svc = 0; $file = 0; $photo = 0; $lines = 0
    $reader = [System.IO.StreamReader]::new($rj, [System.Text.Encoding]::UTF8)
    try {
      while ($null -ne ($line = $reader.ReadLine())) {
        $lines++
        if ($line -match '"type":\s*"message"')  { $msg++ }
        if ($line -match '"type":\s*"service"')  { $svc++ }
        if ($line -match '"file":\s*"')          { $file++ }
        if ($line -match '"photo":\s*"')         { $photo++ }
      }
    } finally { $reader.Dispose() }
    W ("       lines            {0}" -f $lines)
    W ("       type=message     {0}" -f $msg)
    W ("       type=service     {0}" -f $svc)
    W ("       file: refs       {0}" -f $file)
    W ("       photo: refs      {0}" -f $photo)
    W ("       вложений всего   {0}   <- сверяется с числом строк attachments в БД" -f ($file + $photo))
  }
}
W ""

# ----------------------------------------------------------------- DISKS ----
W "== DISKS (все тома: 2=съёмный, 3=фиксированный, 4=сетевой) =="
Get-CimInstance Win32_LogicalDisk | ForEach-Object {
  $free = if ($_.Size) { [math]::Round($_.FreeSpace/1GB,1) } else { 0 }
  $tot  = if ($_.Size) { [math]::Round($_.Size/1GB,1) } else { 0 }
  $pct  = if ($_.Size) { [math]::Round(100*$_.FreeSpace/$_.Size,1) } else { 0 }
  W ("{0,-4} type={1}  total_gb={2,-8} free_gb={3,-8} free_pct={4}" -f $_.DeviceID, $_.DriveType, $tot, $free, $pct)
}
W ""

# -------------------------------------------------------------------- OS ----
W "== OS =="
$os = Get-CimInstance Win32_OperatingSystem
W ("caption      : " + $os.Caption)
W ("version      : " + $os.Version)
W ("ram_total_gb : " + [math]::Round($os.TotalVisibleMemorySize/1MB, 1))
W ("ram_free_gb  : " + [math]::Round($os.FreePhysicalMemory/1MB, 1))
W ""

W "== CPU =="
Get-CimInstance Win32_Processor | ForEach-Object {
  W ("name         : " + $_.Name)
  W ("cores        : " + $_.NumberOfCores)
  W ("threads      : " + $_.NumberOfLogicalProcessors)
  W ("max_mhz      : " + $_.MaxClockSpeed)
}
W ""

W "== GPU =="
Get-CimInstance Win32_VideoController | ForEach-Object {
  W ("name         : " + $_.Name)
  W ("driver       : " + $_.DriverVersion)
  W ("vram_mb_hint : " + [math]::Round($_.AdapterRAM/1MB, 0))
}
W ""

W "== WSL =="
$wsl = (wsl.exe -l -v 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or -not $wsl) { W "wsl: not available or no distros" } else { W ($wsl -replace "`0","") }
W ""

W "== TOOLING =="
foreach ($c in 'python','python3','py','pip','git','docker','ollama','tesseract','winget','node','curl') {
  $p = (Get-Command $c -ErrorAction SilentlyContinue)
  if ($p) { W ("{0,-10} : {1}" -f $c, $p.Source) } else { W ("{0,-10} : -" -f $c) }
}
$pv = (python --version 2>&1); if ($pv) { W ("python -V  : " + $pv) }
$ov = (ollama --version 2>&1); if ($ov) { W ("ollama -V  : " + $ov) }
W ""

# ---------------------------------------------------------------- MODELS ----
W "== LOCAL MODELS (имена моделей публичные, не чувствительные) =="
$modelDirs = @(
  (Join-Path $env:USERPROFILE '.ollama\models'),
  (Join-Path $env:LOCALAPPDATA 'Ollama'),
  (Join-Path $env:USERPROFILE '.cache\huggingface\hub'),
  (Join-Path $env:USERPROFILE '.cache\lm-studio\models'),
  (Join-Path $env:USERPROFILE '.lmstudio\models')
)
foreach ($d in $modelDirs) {
  if (Test-Path $d) {
    $sz = (Get-ChildItem $d -Recurse -File | Measure-Object Length -Sum).Sum
    W ("dir  : {0}  ({1} GB)" -f $d, [math]::Round($sz/1GB,2))
    Get-ChildItem $d -Directory | Select-Object -First 25 | ForEach-Object { W ("       - " + $_.Name) }
  }
}
if ($ScanModels) {
  W "-- поиск *.gguf / *.safetensors по всему профилю (медленно, ключ -ScanModels) --"
  Get-ChildItem $env:USERPROFILE -Recurse -File -Include *.gguf,*.safetensors -ErrorAction SilentlyContinue |
    Select-Object -First 40 | ForEach-Object {
      W ("       {0,8:N2} GB  {1}" -f ($_.Length/1GB), $_.Name)
    }
} else {
  W "-- обход профиля пропущен (запусти с -ScanModels, если нужен полный поиск) --"
  W "   известно с прошлого прогона: gemma-3-4b-it-Q4_K_M.gguf  (2.32 GB)"
}
W ""
W "== END =="

Write-Host ""
Write-Host "Готово. Отчёт: $out" -ForegroundColor Green
Write-Host "Проверь его глазами перед тем, как я его прочитаю - там только цифры." -ForegroundColor Yellow
