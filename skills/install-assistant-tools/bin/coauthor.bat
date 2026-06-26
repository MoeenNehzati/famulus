@echo off
:: Thin Windows wrapper. Delegates to the Python script next to this file.
:: Requires the Python Launcher (py.exe), which ships with standard Python installs.
py "%~dp0coauthor" %*
