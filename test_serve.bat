@echo off
rem 手动测试 capture_worker --serve（echo 提供 stdin，验证 shot/quit 协议）
cd /d "%~dp0capture_worker"
(echo shot & echo quit) | capture_worker.exe --serve %1 > serve_test.bin 2> serve_err.txt
echo EXIT=%ERRORLEVEL%
echo --- stderr ---
type serve_err.txt
echo --- stdout bytes ---
for %%F in (serve_test.bin) do echo %%~zF
