@echo off
rem 用同级目录的嵌入式 Python 启动（无控制台窗口）。参数会原样传给 main.py，例如 run.bat --mock
start "" "%~dp0..\python-3.14.8-embed-amd64\pythonw.exe" "%~dp0main.py" %*
