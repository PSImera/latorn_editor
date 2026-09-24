$in = [Console]::In.ReadToEnd() | ConvertFrom-Json
$fp = $in.tool_input.file_path
if (-not $fp -or $fp -notmatch '\.json$') { exit 0 }
& '.venv\Scripts\python.exe' '.claude\hooks\format_json.py' $fp
