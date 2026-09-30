$uri = 'http://127.0.0.1:8000'
$deadline = (Get-Date).AddSeconds(90)

while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -Uri $uri -TimeoutSec 2 -UseBasicParsing
        if ($response.StatusCode -eq 200) {
            Start-Process $uri
            exit 0
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
