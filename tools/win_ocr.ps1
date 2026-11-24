param(
    [Parameter(Mandatory = $true)][string]$Path,
    [switch]$Words
)

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

function Await($op, $type) {
    $m = $asTaskGeneric.MakeGenericMethod($type)
    $task = $m.Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
Write-Output ("ENGINE_LANG=" + $engine.RecognizerLanguage.LanguageTag)

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

if ($Words) {
    foreach ($line in $result.Lines) {
        $ws = @($line.Words)
        if ($ws.Count -eq 0) { continue }
        foreach ($w in $ws) {
            Write-Output ("{0}`t{1}`t{2}`t{3}" -f $w.Text, [int]$w.BoundingRect.X, [int]$w.BoundingRect.Y, [int]$w.BoundingRect.Width)
        }
    }
}
else {
    Write-Output '--- LINES ---'
    foreach ($line in $result.Lines) {
        $ws = @($line.Words)
        if ($ws.Count -eq 0) { continue }
        $w = $ws[0]
        Write-Output ("({0},{1})`t{2}" -f [int]$w.BoundingRect.X, [int]$w.BoundingRect.Y, $line.Text)
    }
}
