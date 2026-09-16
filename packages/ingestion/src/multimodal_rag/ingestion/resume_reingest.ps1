$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$inputRoot = 'D:\CMO Intelligence Platform\Data'
$outputRoot = 'runtime-data\users\demo-user\artifacts\ingestion'
$completed = @(
  '2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf',
  'AI-marketing-playbook 4.pdf',
  'ANAQ12025Programmatic.pdf',
  'IAB_Outlook_-Study_January_16_2025_v2.pdf',
  'IAB_State_of_Data_2025_March__V2.pdf',
  'Meet-your-new-MOM.pdf',
  'modern-marketing-what-it-is-what-it-isnt.pdf',
  'state-of-marketing-europe-2026.pdf'
)
$targets = @(Get-ChildItem -LiteralPath $inputRoot -Filter '*.pdf' -File | Where-Object { $completed -notcontains $_.Name } | Sort-Object Name)
foreach ($pdf in $targets) {
  Write-Output "Starting $($pdf.Name)"
  & .\.venv\Scripts\python.exe -m multimodal_rag.cli.ingest $pdf.FullName --output-dir $outputRoot
  if ($LASTEXITCODE -ne 0) { throw "Ingestion failed for $($pdf.Name)" }
}
Write-Output "Resume ingestion complete."
