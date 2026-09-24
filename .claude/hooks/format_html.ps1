$in = [Console]::In.ReadToEnd() | ConvertFrom-Json
$fp = $in.tool_input.file_path
if (-not $fp -or $fp -notmatch 'task\.(html|md)$') { exit 0 }
& '.venv\Scripts\python.exe' '.claude\hooks\format_html.py' $fp
