@echo off
rem capture_worker build script (no CMake, direct MSVC compile+link)
rem Prereq: VS2022 with "Desktop development with C++" workload (MSVC + Windows SDK)
rem Uses C++/WinRT projection headers shipped inside the Windows SDK (cppwinrt\winrt).

setlocal
cd /d "%~dp0"

set VCVARS="C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if not exist %VCVARS% (
    echo [build] vcvars64.bat not found. Check VS2022 install path.
    exit /b 1
)

call %VCVARS%
if errorlevel 1 (
    echo [build] vcvars64.bat init failed.
    exit /b 1
)

rem C++/WinRT projection headers (SDK-internal, not in default INCLUDE path)
set "CPPWINRT_INC=%WindowsSdkDir%Include\%WindowsSDKVersion%cppwinrt"

cl /nologo /utf-8 /EHsc /std:c++17 /O2 ^
    /I "%CPPWINRT_INC%" ^
    capture_worker.cpp ^
    /link d3d11.lib dxgi.lib user32.lib ole32.lib runtimeobject.lib windowsapp.lib ^
    /out:capture_worker.exe

if errorlevel 1 (
    echo [build] compile failed.
    exit /b 1
)

echo [build] OK: capture_worker.exe
endlocal
