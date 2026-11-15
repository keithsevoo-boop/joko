STOCKFLOW - COSMIC ANALYTICS EDITION
===================================

Windows desktop inventory, sales and purchasing manager.
Designed for Windows 10 and Windows 11, using UGX by default.

BUILD THE WINDOWS EXE
---------------------
1. Install Python 3.11 or 3.12 (64-bit) from python.org.
2. During installation, enable the Python Launcher option.
3. Extract this complete folder to a normal writable folder.
4. Double-click build_windows.bat.
5. When the build finishes, open dist\StockFlow.exe.

FIRST LOGIN
-----------
Username: admin
Password: admin123
Change the default credentials after signing in by creating a new
administrator account and deactivating the default account.

DATA LOCATION
-------------
Database: %LOCALAPPDATA%\StockFlow\business_inventory.db
Automatic daily backups: %LOCALAPPDATA%\StockFlow\backups\
The app keeps up to 30 daily backup files. A backup is refreshed when
the app closes, and is also created at startup.
- The desktop window opens maximized, can be restored/resized, and its pages
  have vertical scrolling for shorter laptop displays. Navigation also scrolls
  if the screen height is limited; dashboard metric cards use a responsive grid.

UPGRADED FEATURES
-----------------
- Cosmic liquid-glass inspired dark interface.
- Dashboard 7-day sales pulse.
- Sales revenue, items sold, cost of goods sold and gross profit reports.
- New sales save the product buying cost at checkout for profit reporting.
- Older sales are marked as estimates because their original cost was not
  stored by earlier versions. The migration uses the current product cost.
- Product expiry dates and an expiry alert screen (expired / within 30 days).
- Expiry dates can be entered on product registration or stock purchases.
- Consistent SQLite backups and a manual backup option in Settings.
- Existing database records are migrated in place; the app does not delete
  existing products, users or transaction history during the upgrade.
- Existing barcode, phone scanner, USB scanner, stock, purchase and sales
  workflows remain part of the application.
- Phone scanner now uses HTTPS with a locally generated TLS certificate.
- The app creates a local certificate authority file at
  %LOCALAPPDATA%\StockFlow\phone_ca.crt. In the Phone Scanner screen, use
  Export phone trust certificate to save it, transfer it to your phone, and
  install/trust it as a CA certificate. Android and iOS have different steps;
  on iOS also enable full trust under Certificate Trust Settings. Then scan
  the in-app QR code. Without trusting the CA, the browser may still reject
  camera access even though HTTPS is enabled. Keep the CA private; do not share
  it with other people. The server private key stays on this PC.

IMPORTANT
---------
The phone scanner requires the phone and PC to share the same Wi-Fi network.
Camera access requires HTTPS and a trusted certificate; install phone_ca.crt
on the phone using the Export phone trust certificate button. The phone browser
may need internet access to load its camera scanning library.
Windows Firewall may ask permission for private network access.

Keep your own separate backup before upgrading a computer that contains
important live business records. This package contains source code; the EXE
is compiled on the Windows computer by build_windows.bat.


PHONE SCANNER APP + USB SCANNER
--------------------------------
StockFlow supports both a wireless phone camera scanner and a regular USB barcode scanner. They can be used interchangeably.

PHONE SETUP (Android Chrome recommended)
1. Start StockFlow on the Windows computer and sign in.
2. Open Phone Scanner in the StockFlow left menu.
3. Keep the phone and computer on the same Wi-Fi network.
4. Export the phone trust certificate from StockFlow, transfer phone_ca.crt to the phone, and install/trust it as a CA certificate. Android menu names vary by phone.
5. Open the HTTPS address or scan the QR code shown in StockFlow. Allow camera access and tap Start camera.
6. In Chrome, use the menu and choose Add to Home screen to keep a StockFlow Scanner shortcut like an app.
7. Select Products, Sales, or Purchases on the computer before scanning. When the computer is showing the Phone Scanner page, scans go to the last selected workflow.

The phone scanner uses the browser's built-in BarcodeDetector API and does not download a scanning library. Updated Chrome on Android is recommended. If the phone browser does not support this API, use the USB scanner or type the barcode manually.

USB SCANNER
Plug in a normal USB barcode scanner. Click the barcode field in Products, Sales, or Purchases and scan as usual. Most USB scanners act like a keyboard. Sales and Purchases accept the scanner's Enter key automatically.

Both scanners send codes to the same StockFlow barcode fields.

NATIVE ANDROID APP + BLUETOOTH
-----------------------------
The android/ folder contains a native Android camera scanner. It uses the
phone camera and ML Kit barcode recognition, then sends each barcode over
Bluetooth Classic Serial Port Profile (SPP/RFCOMM) to the Windows computer.
The Wi-Fi camera scanner remains available as a fallback, and a USB keyboard
barcode scanner continues to work as before.

BUILD BOTH WINDOWS EXE AND ANDROID APK
1. Install 64-bit Python 3.11 or 3.12 and enable the Python Launcher.
2. Install Android Studio. In SDK Manager install Android SDK Platform 35 (API 35) and Android SDK Build-Tools 35.0.0. API 37 alone is not sufficient because this project compiles against API 35.
3. Install a 64-bit JDK 17 (not Java 21 or 25). Set JAVA_HOME to the JDK 17 installation folder. The build script checks JAVA_HOME and common JDK 17 install folders.
4. Double-click build_windows.bat. It builds dist\\StockFlow.exe and dist\\StockFlow-Scanner.apk. Gradle 8.9 is downloaded automatically if it is not already installed. Internet is required for first build and dependency downloads.
5. Install dist\\StockFlow-Scanner.apk on the Android phone.

BLUETOOTH PAIRING
1. In Windows Bluetooth settings, pair the phone with DESKTOP-C2527B1.
2. In the phone's Bluetooth settings, pair it with the Windows PC as well.
3. In Windows Bluetooth COM Ports, the PC's Serial Port Profile port must
   be marked Incoming. The app defaults to COM3. If Windows assigned another
   incoming port, set STOCKFLOW_BLUETOOTH_COM=COMx before starting StockFlow.
4. Start StockFlow on the PC, open Phone Scanner in StockFlow, open the Android
   app, select the paired PC and tap Connect to StockFlow PC.
5. Point the camera at a barcode. Keep the desired Products, Sales or Purchases
   screen selected on the PC; scans are routed to the active workflow.

NOTE: The Android app uses Bluetooth Classic SPP/RFCOMM, not BLE. It needs a
Windows Incoming Bluetooth COM port that exposes the Serial Port Profile.
If COM3 is Outgoing, create/configure an Incoming port in Windows Bluetooth
settings. The APK is a debug-signed installable APK; it is suitable for local
installation/testing, not Play Store publishing.


BUILD NOTES
-----------
This package contains source code and a build script; it does not contain a
prebuilt Windows EXE or APK. The build script checks for Python 3.11/3.12 and
JDK 17, then builds both applications on your Windows computer. The Android
project uses Android Gradle Plugin 8.7.3, Kotlin 2.0.21 and Gradle 8.9.
If one build step fails, the command window pauses and displays the error.
The Windows EXE can still be present in dist if the Android build fails.

IMPORTANT DATA SAFETY
---------------------
Your live StockFlow database is stored separately under:
%LOCALAPPDATA%\\StockFlow\\business_inventory.db
Do not delete that folder when replacing the program. Make a separate copy of
the StockFlow folder before testing a new EXE.


STABILITY UPDATE - LIVE BLUETOOTH RECONNECT + SIMPLE ICON
---------------------------------------------------------
- StockFlow checks the configured incoming Bluetooth COM port automatically, including when the phone is not connected at startup.
- Open Phone Scanner and use Connect / Reconnect if you change the incoming COM port or want to retry immediately.
- The connection status is shown in the Phone Scanner page. A disconnected scanner does not require restarting StockFlow.
- The simplified StockFlow box/check icon is applied to the EXE, app window and Windows taskbar.
- Existing database location and USB keyboard scanner behavior are unchanged.
