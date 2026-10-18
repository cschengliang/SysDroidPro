@echo off
rem 带控制台启动，异常堆栈会打印在这里。例如 run-debug.bat --mock
"%~dp0..\python-3.14.8-embed-amd64\python.exe" "%~dp0main.py" %*
