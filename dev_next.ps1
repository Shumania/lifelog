$diagUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/diag_album.py"
$diagPath = "$env:TEMP\diag_album.py"
Invoke-WebRequest -Uri $diagUrl -OutFile $diagPath -UseBasicParsing
python $diagPath
