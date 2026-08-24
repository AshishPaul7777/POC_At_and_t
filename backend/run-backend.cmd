@echo off
REM Starts the API in its own console so it survives the parent shell exiting.
REM
REM NO --reload, deliberately. uvicorn sets use_subprocess=True when reload is
REM on, which makes it pick a SelectorEventLoop -- and on Windows that loop
REM cannot spawn subprocesses at all. The retrieve stage shells out to the sf
REM CLI, so with --reload the whole pipeline dies at source.retrieve with a
REM bare NotImplementedError. Restart this window by hand after editing code.
cd /d "%~dp0"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
