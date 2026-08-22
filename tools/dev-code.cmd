@echo off
setlocal DisableDelayedExpansion
for %%I in ("%~dp0..") do set "FAMULUS_CHECKOUT=%%~fI"
set "FAMULUS_ENV=%FAMULUS_CHECKOUT%\.famulus\bin\famulus-env.cmd"
if not exist "%FAMULUS_ENV%" (
  echo dev-code: development runtime is not installed; run the Famulus development install first 1>&2
  exit /b 2
)
set "CODE_EXE="
for /f "delims=" %%I in ('where code 2^>nul') do if not defined CODE_EXE set "CODE_EXE=%%~fI"
if not defined CODE_EXE (
  echo dev-code: code executable not found on the host PATH 1>&2
  exit /b 2
)
call "%FAMULUS_ENV%" exec -- "%CODE_EXE%" "%FAMULUS_CHECKOUT%" %*
exit /b %ERRORLEVEL%
