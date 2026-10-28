param([string]$TextFile, [string]$OutFile, [int]$Rate = 2)
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = $Rate
$zh = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'zh*' } | Select-Object -First 1
if ($zh) { $synth.SelectVoice($zh.VoiceInfo.Name) }
$synth.SetOutputToWaveFile($OutFile)
$synth.Speak($text)
$synth.Dispose()
