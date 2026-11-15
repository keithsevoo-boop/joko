@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title StockFlow - Build Windows EXE and Android APK

echo ==================================================
echo   STOCKFLOW - WINDOWS EXE + ANDROID APK BUILDER
echo ==================================================
echo.

REM ---- Find Python 3.11 or 3.12 (64-bit) ----
set "PYTHON_CMD="
py -3.12 --version >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3.12"
if not defined PYTHON_CMD (
  py -3.11 --version >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.11"
)
if not defined PYTHON_CMD (
  echo ERROR: Python 3.11 or 3.12 was not found.
  echo Install 64-bit Python and tick "Install Python Launcher".
  goto failed
)
echo [1/5] Python found: %PYTHON_CMD%

REM ---- Require JDK 17 for Gradle 8.9 / Android Gradle Plugin 8.7.3 ----
echo [2/5] Checking Java JDK 17...
set "SF_JAVA_HOME="
if defined JAVA_HOME call :check_java "%JAVA_HOME%"
if not defined SF_JAVA_HOME if defined ANDROID_STUDIO_JDK call :check_java "%ANDROID_STUDIO_JDK%"
if not defined SF_JAVA_HOME if exist "%ProgramFiles%\Eclipse Adoptium" (
  for /d %%D in ("%ProgramFiles%\Eclipse Adoptium\jdk-17*") do if exist "%%~fD\bin\java.exe" call :check_java "%%~fD"
)
if not defined SF_JAVA_HOME if exist "%ProgramFiles%\Microsoft\jdk-17*" (
  for /d %%D in ("%ProgramFiles%\Microsoft\jdk-17*") do if exist "%%~fD\bin\java.exe" call :check_java "%%~fD"
)
if not defined SF_JAVA_HOME (
  echo ERROR: JDK 17 was not found.
  echo Install a 64-bit JDK 17, then set JAVA_HOME to its install folder.
  echo Example: C:\Program Files\Eclipse Adoptium\jdk-17.x.x-hotspot
  goto failed
)
set "JAVA_HOME=%SF_JAVA_HOME%"
set "PATH=%JAVA_HOME%\bin;%PATH%"
echo Using JDK 17 from: %JAVA_HOME%
"%JAVA_HOME%\bin\java.exe" -version

REM ---- Build Windows desktop app ----
echo.
echo [3/5] Installing Windows app packages...
%PYTHON_CMD% -m pip install --upgrade pip
if errorlevel 1 goto failed
%PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo.
echo [4/5] Building Windows EXE...
%PYTHON_CMD% -m PyInstaller --noconfirm --clean --windowed --onefile --name StockFlow --icon stockflow.ico --add-data "stockflow.ico;." --collect-all qrcode --collect-all cryptography --collect-all cffi --hidden-import=cryptography.hazmat.bindings._rust --hidden-import=serial main.py
if errorlevel 1 goto failed
if not exist "dist\StockFlow.exe" goto failed

REM ---- Locate Android SDK ----
if not defined ANDROID_HOME if defined ANDROID_SDK_ROOT set "ANDROID_HOME=%ANDROID_SDK_ROOT%"
if not defined ANDROID_HOME if exist "%LOCALAPPDATA%\Android\Sdk" set "ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk"
if not defined ANDROID_HOME (
  echo.
  echo WINDOWS EXE BUILT, BUT ANDROID APK WAS NOT BUILT.
  echo Android SDK was not found. Open Android Studio - SDK Manager,
  echo install Android SDK Platform 35 and Build-Tools 35.0.0.
  goto android_failed
)
if not exist "%ANDROID_HOME%\platforms\android-35\android.jar" (
  echo.
  echo WINDOWS EXE BUILT, BUT ANDROID APK WAS NOT BUILT.
  echo Install Android SDK Platform 35 in Android Studio - SDK Manager.
  goto android_failed
)

REM ---- Download Gradle 8.9 locally if needed, then build APK ----
echo.
echo [5/5] Building Android scanner APK...
set "GRADLE_HOME=%CD%\.tools\gradle-8.9"
if not exist "%GRADLE_HOME%\bin\gradle.bat" (
  where gradle >nul 2>nul
  if errorlevel 1 (
    if not exist ".tools" mkdir ".tools"
    echo Downloading Gradle 8.9. Internet is needed for this first build.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://services.gradle.org/distributions/gradle-8.9-bin.zip' -OutFile '.tools\gradle-8.9-bin.zip'; Expand-Archive -Force '.tools\gradle-8.9-bin.zip' '.tools'"
    if errorlevel 1 goto android_failed
  )
)
pushd android
if exist "%GRADLE_HOME%\bin\gradle.bat" (
  call "%GRADLE_HOME%\bin\gradle.bat" --no-daemon assembleDebug
) else (
  call gradle --no-daemon assembleDebug
)
if errorlevel 1 (popd & goto android_failed)
popd
if not exist "android\app\build\outputs\apk\debug\app-debug.apk" goto android_failed
copy /y "android\app\build\outputs\apk\debug\app-debug.apk" "dist\StockFlow-Scanner.apk" >nul
if errorlevel 1 goto android_failed

echo.
echo ==================================================
echo BUILD FINISHED
echo Windows app: %CD%\dist\StockFlow.exe
echo Android app: %CD%\dist\StockFlow-Scanner.apk
echo ==================================================
pause
exit /b 0

:check_java
if not exist "%~1\bin\java.exe" exit /b 0
set "SF_JAVA_VERSION="
for /f "tokens=3" %%V in ('"%~1\bin\java.exe" -version 2^>^&1 ^| findstr /i "version"') do set "SF_JAVA_VERSION=%%~V"
REM %%~V removes quotes, so Java 17 begins with 17.
if "%SF_JAVA_VERSION:~0,2%"=="17" set "SF_JAVA_HOME=%~1"
exit /b 0

:android_failed
echo.
echo Android build did not finish. The Windows EXE may be ready in dist.
echo Confirm JDK 17 and Android SDK Platform 35 are installed.
echo JAVA_HOME: %JAVA_HOME%
pause
exit /b 2

:failed
echo.
echo BUILD FAILED. Read the error above and share a screenshot.
pause
exit /b 1
