@echo off
echo Converting webp to mp4 ...
echo.
"E:\ai\ComfyUI_windows_portable\python_embeded\python.exe" "E:\ai\webp2mp4.py"
if errorlevel 1 (
  echo.
  echo FAILED. Send this window text to Claude.
) else (
  echo.
  echo Done! MP4 files are in the output folder.
)
pause
