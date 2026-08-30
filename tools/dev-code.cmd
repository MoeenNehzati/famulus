@echo off
setlocal DisableDelayedExpansion
for %%I in ("%~dp0..") do set "FAMULUS_CHECKOUT=%%~fI"
set "CODE_EXE="
for /f "delims=" %%I in ('where code 2^>nul') do if not defined CODE_EXE set "CODE_EXE=%%~fI"
if not defined CODE_EXE (
  echo dev-code: code executable not found on the host PATH 1>&2
  exit /b 2
)
python "%FAMULUS_CHECKOUT%\skills\dev-activation\_rtx\_development_activation.py" exec --checkout "%FAMULUS_CHECKOUT%" -- "%CODE_EXE%" "%FAMULUS_CHECKOUT%" %*
exit /b %ERRORLEVEL%
