import os, sys, sqlite3, hashlib, secrets, csv, socket, threading, queue, json, ssl, shutil, time
from datetime import datetime, date
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from ipaddress import ip_address
from datetime import timedelta

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QFont, QPixmap, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget,
    QTableWidgetItem, QMessageBox, QStackedWidget, QFrame, QFormLayout, QDialog,
    QDialogButtonBox, QFileDialog, QHeaderView, QDateEdit, QAbstractItemView, QTextEdit, QProgressBar, QScrollArea, QGridLayout)

APP_NAME = 'StockFlow'

def resource_path(name):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / name
BASE_DIR = Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'StockFlow'
BASE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE_DIR / 'business_inventory.db'
BACKUP_DIR = BASE_DIR / 'backups'
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
PHONE_SCANS = queue.Queue()
PHONE_ACTIONS = queue.Queue()
BLUETOOTH_STATUS = queue.Queue()

def automatic_backup():
    """Make one consistent local SQLite backup per day; retain the latest 30."""
    if not DB_PATH.exists(): return None
    target = BACKUP_DIR / f"business_inventory_{date.today().isoformat()}.db"
    source = sqlite3.connect(DB_PATH); dest = sqlite3.connect(target)
    try: source.backup(dest)
    finally: dest.close(); source.close()
    backups = sorted(BACKUP_DIR.glob('business_inventory_*.db'))
    for old in backups[:-30]:
        try: old.unlink()
        except OSError: pass
    return target

# ---------- Database and security ----------
def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=5000')
    return con

def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 310000)
    return salt.hex(), digest.hex()

def verify_password(password, salt_hex, hash_hex):
    _, candidate = hash_password(password, bytes.fromhex(salt_hex))
    return secrets.compare_digest(candidate, hash_hex)

def init_db():
    with connect_db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'staff', active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, barcode TEXT UNIQUE, name TEXT NOT NULL, category TEXT DEFAULT '', buy_price REAL NOT NULL DEFAULT 0, sell_price REAL NOT NULL DEFAULT 0, quantity INTEGER NOT NULL DEFAULT 0, image_path TEXT DEFAULT '', created_at TEXT NOT NULL, expiry_date TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY, tx_type TEXT NOT NULL, product_id INTEGER, product_name TEXT NOT NULL, barcode TEXT, quantity INTEGER NOT NULL, unit_price REAL NOT NULL, total REAL NOT NULL, party TEXT DEFAULT '', username TEXT NOT NULL, created_at TEXT NOT NULL, cost_price REAL NOT NULL DEFAULT 0, cost_estimated INTEGER NOT NULL DEFAULT 0, expiry_date TEXT DEFAULT '', FOREIGN KEY(product_id) REFERENCES products(id));
        ''')
        # Migrate existing installations without deleting products or transaction history.
        product_cols = {r['name'] for r in c.execute('PRAGMA table_info(products)').fetchall()}
        tx_cols = {r['name'] for r in c.execute('PRAGMA table_info(transactions)').fetchall()}
        legacy_cost_data = ('cost_price' not in tx_cols or 'cost_estimated' not in tx_cols)
        if 'expiry_date' not in product_cols: c.execute("ALTER TABLE products ADD COLUMN expiry_date TEXT DEFAULT ''")
        if 'cost_price' not in tx_cols: c.execute('ALTER TABLE transactions ADD COLUMN cost_price REAL NOT NULL DEFAULT 0')
        if 'cost_estimated' not in tx_cols: c.execute('ALTER TABLE transactions ADD COLUMN cost_estimated INTEGER NOT NULL DEFAULT 1')
        if 'expiry_date' not in tx_cols: c.execute("ALTER TABLE transactions ADD COLUMN expiry_date TEXT DEFAULT ''")
        # Earlier sales did not save unit cost; use current product cost and mark it as an estimate.
        if legacy_cost_data:
            c.execute("UPDATE transactions SET cost_price=COALESCE((SELECT buy_price FROM products WHERE products.id=transactions.product_id),0), cost_estimated=1 WHERE tx_type='SALE'")
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
            salt, ph = hash_password('admin123')
            c.execute('INSERT INTO users(username,salt,password_hash,role) VALUES(?,?,?,?)', ('admin',salt,ph,'admin'))

# ---------- Phone scanner HTTP service ----------
PHONE_HTML = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0c1426"><title>StockFlow Scanner</title><link rel="manifest" href="/manifest.json"><style>\n*{box-sizing:border-box}body{font-family:system-ui;background:#0c1426;color:#f7f9ff;margin:0;padding:18px;text-align:center;min-height:100vh}main{max-width:560px;margin:0 auto;background:#17233b;border:1px solid #344664;border-radius:24px;padding:22px}h1{margin:4px;font-size:26px}p{color:#bdc9df;line-height:1.45}#camera{width:100%;max-height:55vh;object-fit:cover;background:#080e1b;border-radius:16px;margin:14px auto}button{background:#6c8cff;color:white;border:0;border-radius:12px;padding:14px 20px;font-size:16px;font-weight:700;margin:5px;min-width:130px}button.secondary{background:#344664}#status{padding:12px;color:#a9e7c0;min-height:44px}#last{font-size:20px;font-weight:800;overflow-wrap:anywhere;color:#fff}small{color:#9aacca}input{width:100%;padding:13px;border-radius:10px;border:1px solid #53698c;background:#0d1930;color:white;font-size:18px;margin:8px 0}\n</style></head><body><main><h1>◈ StockFlow Scanner</h1><p>Wireless barcode scanner for your StockFlow computer. Keep your phone and computer on the same Wi-Fi.</p><video id="camera" playsinline muted></video><div><button id="start">Start camera</button><button id="stop" class="secondary">Stop camera</button></div><div id="status">Tap Start camera and allow camera access.</div><div id="last"></div><p><small>Scans are sent to StockFlow automatically. You can also enter a code manually below.</small></p><input id="manual" inputmode="numeric" autocomplete="off" placeholder="Enter barcode manually"><button id="send">Send barcode</button></main>\n<script>\nconst video=document.getElementById(\'camera\'),statusEl=document.getElementById(\'status\'),lastEl=document.getElementById(\'last\');let stream=null,detector=null,running=false,lastCode=\'\',lastAt=0;\nasync function sendCode(code){code=String(code||\'\').trim();if(!code)return;lastEl.textContent=\'Last scan: \'+code;statusEl.textContent=\'Sending barcode to StockFlow…\';try{const r=await fetch(\'/scan\',{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify({barcode:code})});if(!r.ok)throw Error(\'Computer rejected the scan\');statusEl.textContent=\'Sent successfully. Ready for next barcode.\';}catch(e){statusEl.textContent=\'Could not reach StockFlow. Check Wi-Fi and that StockFlow is open.\';}}\nasync function scanLoop(){if(!running)return;try{if(video.readyState>=2){const codes=await detector.detect(video);if(codes.length){const code=codes[0].rawValue,now=Date.now();if(code&&(code!==lastCode||now-lastAt>1800)){lastCode=code;lastAt=now;await sendCode(code);}}}}catch(e){}if(running)requestAnimationFrame(scanLoop);}\ndocument.getElementById(\'start\').onclick=async()=>{try{if(!(\'BarcodeDetector\'in window)){statusEl.textContent=\'This phone browser does not support built-in barcode scanning. Please use updated Chrome on Android or the USB scanner.\';return;}detector=new BarcodeDetector({formats:[\'ean_13\',\'ean_8\',\'upc_a\',\'upc_e\',\'code_128\',\'code_39\',\'itf\',\'qr_code\',\'data_matrix\']});stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:\'environment\'}},audio:false});video.srcObject=stream;await video.play();running=true;statusEl.textContent=\'Camera ready. Point it at a barcode.\';scanLoop();}catch(e){statusEl.textContent=\'Camera could not start: \'+e.message+\'. Check camera permission and the phone trust certificate.\';}};\ndocument.getElementById(\'stop\').onclick=()=>{running=false;if(stream)stream.getTracks().forEach(t=>t.stop());stream=null;video.srcObject=null;statusEl.textContent=\'Camera stopped.\';};\ndocument.getElementById(\'send\').onclick=()=>{sendCode(document.getElementById(\'manual\').value);document.getElementById(\'manual\').value=\'\';};\ndocument.getElementById(\'manual\').addEventListener(\'keydown\',e=>{if(e.key===\'Enter\')document.getElementById(\'send\').click();});\nif(\'serviceWorker\'in navigator)window.addEventListener(\'load\',()=>navigator.serviceWorker.register(\'/sw.js\').catch(()=>{}));\n</script></body></html>\n'
PHONE_MANIFEST = b'{"name":"StockFlow Scanner","short_name":"StockFlow","start_url":"/","display":"standalone","background_color":"#0c1426","theme_color":"#0c1426","icons":[{"src":"/app-icon.png","sizes":"512x512","type":"image/png"}]}'
PHONE_SW = b"const CACHE='stockflow-scanner-v1';self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(['/','/manifest.json','/app-icon.png']))));self.addEventListener('fetch',e=>{if(e.request.method==='GET')e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)));});"

class PhoneHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path=urlparse(self.path).path
        if path in ('/', '/index.html'):
            body=PHONE_HTML.encode('utf-8'); content_type='text/html; charset=utf-8'
        elif path=='/manifest.json':
            body=PHONE_MANIFEST; content_type='application/manifest+json'
        elif path=='/sw.js':
            body=PHONE_SW; content_type='application/javascript'
        elif path=='/app-icon.png':
            icon=Path(__file__).with_name('phone_scanner_icon.png')
            try: body=icon.read_bytes(); content_type='image/png'
            except OSError: self.send_error(404); return
        else:
            self.send_error(404); return
        self.send_response(200); self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store' if path=='/sw.js' else 'public, max-age=3600'); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        if self.path != '/scan': self.send_error(404); return
        try:
            data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            code=str(data.get('barcode','')).strip()[:120]
            if code: PHONE_SCANS.put(code)
            body=b'{"ok":true}'; self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        except Exception: self.send_error(400)
    def log_message(self,*args): pass

def ensure_phone_certificates():
    """Create a local CA and LAN-IP TLS certificate. Install phone_ca.crt on the phone to trust it."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption
    from cryptography.x509 import SubjectAlternativeName, IPAddress, DNSName, BasicConstraints, KeyUsage, ExtendedKeyUsage
    from cryptography.x509.oid import ExtendedKeyUsageOID
    ca_key_path=BASE_DIR/'phone_ca_key.pem'; ca_cert_path=BASE_DIR/'phone_ca.crt'
    server_key_path=BASE_DIR/'phone_server_key.pem'; server_cert_path=BASE_DIR/'phone_server.crt'
    ip=local_ip()
    if not all(p.exists() for p in (ca_key_path,ca_cert_path,server_key_path,server_cert_path)):
        ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'StockFlow Local Scanner CA')])
        now_utc=datetime.utcnow()
        ca_cert=(x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now_utc-timedelta(minutes=5)).not_valid_after(now_utc+timedelta(days=3650)).add_extension(BasicConstraints(ca=True,path_length=0),critical=True).add_extension(KeyUsage(digital_signature=True,key_encipherment=False,key_cert_sign=True,key_agreement=False,content_commitment=False,data_encipherment=False,crl_sign=True,encipher_only=False,decipher_only=False),critical=True).sign(ca_key,hashes.SHA256()))
        server_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        san=[IPAddress(ip_address(ip)),DNSName('localhost')]
        server_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,ip)])
        server_cert=(x509.CertificateBuilder().subject_name(server_name).issuer_name(ca_cert.subject).public_key(server_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now_utc-timedelta(minutes=5)).not_valid_after(now_utc+timedelta(days=825)).add_extension(SubjectAlternativeName(san),critical=False).add_extension(ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False).sign(ca_key,hashes.SHA256()))
        ca_key_path.write_bytes(ca_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        server_key_path.write_bytes(server_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        server_cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    return server_cert_path,server_key_path,ca_cert_path

class PhoneServer:
    def __init__(self): self.server=None; self.thread=None; self.port=8765; self.ca_path=None
    def start(self):
        if self.server: return
        cert,key,self.ca_path=ensure_phone_certificates()
        self.server=ThreadingHTTPServer(('0.0.0.0',self.port),PhoneHandler)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(cert),str(key))
        self.server.socket=context.wrap_socket(self.server.socket,server_side=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
    def stop(self):
        if self.server: self.server.shutdown(); self.server.server_close(); self.server=None

def local_ip():
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try: s.connect(('8.8.8.8',80)); ip=s.getsockname()[0]
    except Exception: ip='127.0.0.1'
    finally: s.close()
    return ip

# ---------- UI helpers ----------
STYLE='''
*{font-family:'Segoe UI';font-size:10pt;}
QWidget{background:transparent;color:#edf4ff;}
QMainWindow{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #07101f,stop:.48 #101a35,stop:1 #1b1740);}
QDialog{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #101b34,stop:1 #211d48);}
QLabel{color:#e8efff;background:transparent;}
QLabel#title{font-size:21pt;font-weight:800;color:#ffffff;padding:2px 0;}
QLabel#muted{color:#9eafd0;font-size:10pt;}
QFrame#sidebar{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #172b4c,stop:.52 #111d38,stop:1 #251b4c);border:1px solid rgba(154,190,255,75);border-radius:25px;}
QLabel#brand{color:#ffffff;font-size:19pt;font-weight:900;padding:14px 10px 20px 10px;}
QPushButton#nav{background:rgba(255,255,255,7);color:#b9c9e8;text-align:left;padding:14px 16px;border:1px solid rgba(255,255,255,0);border-radius:15px;font-weight:650;}
QPushButton#nav:hover{background:rgba(91,151,255,38);border:1px solid rgba(139,190,255,75);color:white;}
QPushButton#nav[active="true"]{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 rgba(44,132,255,190),stop:1 rgba(133,83,255,155));border:1px solid rgba(185,218,255,150);color:white;}
QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #39a8ff,stop:1 #765cff);color:white;border:1px solid rgba(206,231,255,105);border-radius:13px;padding:11px 17px;font-weight:750;}
QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #65c5ff,stop:1 #987cff);border:1px solid rgba(255,255,255,190);}
QPushButton:pressed{background:#4165d8;}
QPushButton#secondary{background:rgba(139,177,255,22);color:#dceaff;border:1px solid rgba(164,199,255,85);}
QPushButton#secondary:hover{background:rgba(139,177,255,48);}
QPushButton#danger{background:rgba(255,91,119,28);color:#ffb8c4;border:1px solid rgba(255,120,145,95);}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QDateEdit,QTextEdit{background:rgba(226,238,255,15);color:#f4f7ff;border:1px solid rgba(164,194,245,75);border-radius:12px;padding:10px;selection-background-color:#557fff;}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus,QDateEdit:focus,QTextEdit:focus{background:rgba(226,238,255,23);border:1px solid #70caff;}
QLineEdit::placeholder{color:#8195b8;}
QComboBox QAbstractItemView{background:#172542;color:#edf4ff;selection-background-color:#426bc0;border:1px solid #6385c5;}
QTableWidget{background:rgba(10,22,43,205);alternate-background-color:rgba(91,124,181,24);color:#eaf1ff;border:1px solid rgba(151,187,245,65);border-radius:18px;gridline-color:rgba(150,180,230,28);selection-background-color:rgba(63,139,255,115);selection-color:white;}
QHeaderView::section{background:rgba(73,112,177,90);color:#d9e8ff;padding:12px;border:0;border-bottom:1px solid rgba(172,205,255,75);font-weight:800;}
QFrame#card{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(70,112,174,100),stop:.55 rgba(37,57,103,155),stop:1 rgba(84,57,145,100));border:1px solid rgba(180,211,255,100);border-radius:21px;}
QLabel#metric{font-size:18pt;font-weight:900;color:#ffffff;padding-top:4px;}
QLabel#metriclabel{color:#c2d6f7;font-weight:700;font-size:9pt;}
QFrame#hero{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(34,135,255,150),stop:.5 rgba(93,81,210,125),stop:1 rgba(24,211,220,70));border:1px solid rgba(190,228,255,130);border-radius:24px;}
QLabel#heroTitle{font-size:20pt;font-weight:900;color:white;}
QLabel#heroSub{font-size:10pt;color:#e0edff;}
QScrollBar:vertical{background:rgba(255,255,255,12);width:10px;margin:4px;border-radius:5px;}
QScrollBar::handle:vertical{background:rgba(135,178,255,120);min-height:25px;border-radius:5px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
QToolTip{background:#18294a;color:white;border:1px solid #82baff;padding:6px;border-radius:7px;}
'''

def money(v): return 'UGX {:,.0f}'.format(float(v or 0))
def now(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def info(parent, title, msg): QMessageBox.information(parent,title,msg)
def error(parent, title, msg): QMessageBox.warning(parent,title,msg)

class ProductDialog(QDialog):
    def __init__(self,parent,product=None,barcode=''):
        super().__init__(parent); self.setWindowTitle('Product details'); self.setMinimumWidth(430); self.image_path=''
        form=QFormLayout(self)
        self.barcode=QLineEdit(barcode); self.barcode.setPlaceholderText('Scan with phone / USB scanner or type barcode'); self.name=QLineEdit(); self.category=QLineEdit()
        self.buy=QDoubleSpinBox(); self.buy.setMaximum(1e9); self.buy.setDecimals(2)
        self.sell=QDoubleSpinBox(); self.sell.setMaximum(1e9); self.sell.setDecimals(2)
        self.qty=QSpinBox(); self.qty.setMaximum(100000000)
        self.expiry=QLineEdit(); self.expiry.setPlaceholderText('YYYY-MM-DD (optional)')
        self.image=QLabel('No product image selected'); self.pick=QPushButton('Choose product picture'); self.pick.clicked.connect(self.choose_image)
        form.addRow('Barcode / code',self.barcode); form.addRow('Product name',self.name); form.addRow('Category',self.category); form.addRow('Buying price (UGX)',self.buy); form.addRow('Selling price (UGX)',self.sell); form.addRow('Opening stock',self.qty); form.addRow('Expiry date',self.expiry); form.addRow('Product photo',self.pick); form.addRow('',self.image)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); form.addRow(buttons)
        if product:
            self.barcode.setText(product['barcode'] or ''); self.name.setText(product['name']); self.category.setText(product['category'] or '')
            self.buy.setValue(product['buy_price']); self.sell.setValue(product['sell_price']); self.qty.setValue(product['quantity']); self.expiry.setText(product['expiry_date'] or ''); self.image_path=product['image_path'] or ''
            if self.image_path: self.image.setText(Path(self.image_path).name)
    def choose_image(self):
        p,_=QFileDialog.getOpenFileName(self,'Choose product picture','','Images (*.png *.jpg *.jpeg *.bmp)')
        if p: self.image_path=p; self.image.setText(Path(p).name)
    def values(self): return (self.barcode.text().strip() or None,self.name.text().strip(),self.category.text().strip(),self.buy.value(),self.sell.value(),self.qty.value(),self.image_path,self.expiry.text().strip())

class LoginDialog(QDialog):
    def __init__(self):
        super().__init__(); self.setWindowTitle('Sign in to StockFlow'); self.setFixedSize(410,290); self.user=None
        l=QVBoxLayout(self); title=QLabel('◈ StockFlow'); title.setObjectName('title'); l.addWidget(title)
        l.addWidget(QLabel('Sign in to your business workspace'))
        self.username=QLineEdit(); self.username.setPlaceholderText('Username'); self.password=QLineEdit(); self.password.setPlaceholderText('Password'); self.password.setEchoMode(QLineEdit.Password)
        l.addWidget(self.username); l.addWidget(self.password); b=QPushButton('Sign in'); b.clicked.connect(self.login); l.addWidget(b)
        self.password.returnPressed.connect(self.login)
    def login(self):
        with connect_db() as c: u=c.execute('SELECT * FROM users WHERE username=? AND active=1',(self.username.text().strip(),)).fetchone()
        if u and verify_password(self.password.text(),u['salt'],u['password_hash']): self.user=dict(u); self.accept()
        else: error(self,'Sign in failed','Incorrect username or password, or this account is inactive.')

class BluetoothSerialReader:
    """Continuously monitor an incoming Bluetooth serial port and allow live reconnects."""
    def __init__(self, port=None):
        self.port = (port or os.getenv('STOCKFLOW_BLUETOOTH_COM', 'COM3')).strip().upper()
        self.stop_event = threading.Event()
        self.thread = None
        self._start_worker()

    def _start_worker(self):
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def set_port(self, port):
        port = (port or '').strip().upper()
        if not re.fullmatch(r'COM[1-9][0-9]*', port):
            BLUETOOTH_STATUS.put(('error', 'Enter a valid Windows port, for example COM3.'))
            return False
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)
        self.port = port
        self.stop_event = threading.Event()
        self._start_worker()
        BLUETOOTH_STATUS.put(('connecting', f'Connecting to {self.port}…'))
        return True

    def _run(self):
        import serial
        last_state = None
        while not self.stop_event.is_set():
            try:
                with serial.Serial(self.port, 9600, timeout=1) as ser:
                    if last_state != 'connected':
                        BLUETOOTH_STATUS.put(('connected', f'Connected to {self.port}. Ready to scan.'))
                        last_state = 'connected'
                    while not self.stop_event.is_set():
                        raw = ser.readline()
                        if raw:
                            code = raw.decode('utf-8', errors='ignore').strip()
                            if code:
                                if code.startswith('{'):
                                    try: PHONE_ACTIONS.put(json.loads(code))
                                    except Exception: PHONE_SCANS.put(code[:120])
                                else: PHONE_SCANS.put(code[:120])
            except Exception as exc:
                if last_state != 'waiting':
                    BLUETOOTH_STATUS.put(('waiting', f'Waiting for {self.port}. You can connect the scanner now; no restart needed.'))
                    last_state = 'waiting'
                self.stop_event.wait(3)

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)

class MainWindow(QMainWindow):
    def __init__(self,user):
        super().__init__(); self.user=user; self.phone=PhoneServer(); self.phone.start(); self.bluetooth=BluetoothSerialReader(); self.cart=[]; self.pending_product_barcode=''; self.last_scan_target=1
        self.setWindowTitle('StockFlow — Business Inventory & Sales Manager'); self.setWindowIcon(QIcon(str(resource_path('stockflow.ico'))))
        self.setMinimumSize(820,540); self.resize(1180,760)
        root=QWidget(); self.setCentralWidget(root); outer=QHBoxLayout(root); outer.setContentsMargins(10,10,10,10); outer.setSpacing(10)
        side=QFrame(); side.setObjectName('sidebar'); side.setFixedWidth(205); sl=QVBoxLayout(side); sl.setContentsMargins(10,12,10,12); sl.setSpacing(8)
        brand=QLabel('◈ StockFlow'); brand.setObjectName('brand'); sl.addWidget(brand)
        self.nav=[]; self.stack=QStackedWidget(); self.pages=[]
        names=['Dashboard','Products & Stock','Sales / POS','Purchases','Sales Reports','Transaction History','Phone Scanner','Expiry Alerts']
        if user['role']=='admin': names+=['Staff Accounts','Settings']
        nav_content=QWidget(); nav_layout=QVBoxLayout(nav_content); nav_layout.setContentsMargins(0,0,0,0); nav_layout.setSpacing(6)
        for i,n in enumerate(names):
            b=QPushButton(('⌂  ' if i==0 else '▦  ' if i==1 else '＋  ' if i in (2,3) else '▤  ')+n); b.setObjectName('nav'); b.setProperty('active',i==0); b.setMinimumHeight(42); b.clicked.connect(lambda checked=False,x=i:self.navigate(x)); nav_layout.addWidget(b); self.nav.append(b)
        nav_layout.addStretch()
        nav_scroll=QScrollArea(); nav_scroll.setWidgetResizable(True); nav_scroll.setFrameShape(QFrame.NoFrame); nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); nav_scroll.setWidget(nav_content); sl.addWidget(nav_scroll,1)
        who=QLabel(f"Signed in as\n{user['username']} · {user['role'].title()}"); who.setWordWrap(True); who.setStyleSheet('color:#aab9d1;padding:8px'); sl.addWidget(who)
        logout=QPushButton('Sign out'); logout.setObjectName('nav'); logout.clicked.connect(self.close); sl.addWidget(logout)
        outer.addWidget(side); outer.addWidget(self.stack,1)
        builders=[self.dashboard_page,self.products_page,self.sales_page,self.purchase_page,self.reports_page,self.history_page,self.phone_page,self.expiry_page]
        if user['role']=='admin': builders += [self.staff_page,self.settings_page]
        for build in builders: self.stack.addWidget(build())
        self.navigate(0)
        self.poll=QTimer(self); self.poll.timeout.connect(self.receive_phone_scans); self.poll.start(250)
    def navigate(self,i):
        if i in (1,2,3): self.last_scan_target=i
        self.stack.setCurrentIndex(i)
        for j,b in enumerate(self.nav): b.setProperty('active',j==i); b.style().unpolish(b); b.style().polish(b)
        if i==0: self.refresh_dashboard()
        if i==1: self.refresh_products()
        if i==4: self.refresh_reports()
        if i==5: self.refresh_history()
        if i==7: self.refresh_expiry()
    def page(self,title,subtitle=''):
        # Each workspace page scrolls vertically on shorter laptop displays.
        outer=QWidget(); outer_layout=QVBoxLayout(outer); outer_layout.setContentsMargins(0,0,0,0); outer_layout.setSpacing(0)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded); scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content=QWidget(); l=QVBoxLayout(content); l.setContentsMargins(12,8,12,12); l.setSpacing(12)
        h=QLabel(title); h.setObjectName('title'); h.setWordWrap(True); l.addWidget(h)
        if subtitle: s=QLabel(subtitle); s.setObjectName('muted'); s.setWordWrap(True); l.addWidget(s)
        scroll.setWidget(content); outer_layout.addWidget(scroll)
        return outer,l
    def card(self,label,value):
        f=QFrame(); f.setObjectName('card'); v=QVBoxLayout(f); v.setContentsMargins(19,17,19,17); v.setSpacing(7); a=QLabel(label); a.setObjectName('metriclabel'); b=QLabel(value); b.setObjectName('metric'); v.addWidget(a); v.addWidget(b); return f,b
    def dashboard_page(self):
        w,l=self.page('Business overview','Your business, beautifully in focus.')
        hero=QFrame(); hero.setObjectName('hero'); hv=QVBoxLayout(hero); hv.setContentsMargins(24,19,24,19)
        hello=QLabel('Your business at a glance'); hello.setObjectName('heroTitle'); hv.addWidget(hello)
        sub=QLabel('Live inventory intelligence  •  Sales pulse  •  Stock movement'); sub.setObjectName('heroSub'); hv.addWidget(sub)
        l.addWidget(hero)
        self.metric_labels=[]; metrics=QGridLayout(); metrics.setHorizontalSpacing(10); metrics.setVerticalSpacing(10)
        for index,label in enumerate(['Total Products','Stock Units','Stock Value','Sales Total','Purchases Total']):
            f,x=self.card(label,'—'); metrics.addWidget(f,index//3,index%3); self.metric_labels.append(x)
        l.addLayout(metrics)
        self.dash_note=QLabel(''); self.dash_note.setObjectName('muted'); l.addWidget(self.dash_note)
        pulse=QFrame(); pulse.setObjectName('card'); pv=QVBoxLayout(pulse); pv.setContentsMargins(18,14,18,14); pv.addWidget(QLabel('7-day sales pulse')); self.sales_pulse=[]
        for _ in range(7):
            line=QHBoxLayout(); day=QLabel('—'); day.setMinimumWidth(90); bar=QProgressBar(); bar.setRange(0,100); bar.setTextVisible(False); bar.setFixedHeight(11); amount=QLabel('UGX 0'); amount.setMinimumWidth(115); line.addWidget(day); line.addWidget(bar,1); line.addWidget(amount); pv.addLayout(line); self.sales_pulse.append((day,bar,amount))
        l.addWidget(pulse)
        activity=QLabel('Recent activity'); activity.setStyleSheet('font-size:14pt;font-weight:800;color:#f3f7ff;padding:4px 2px;'); l.addWidget(activity)
        self.dash_table=self.table(['Date & time','Type','Product','Quantity','Total','User']); l.addWidget(self.dash_table,1)
        self.refresh_dashboard(); return w
    def refresh_dashboard(self):
        with connect_db() as c:
            products=c.execute('SELECT COUNT(*) n FROM products').fetchone()['n']; units=c.execute('SELECT COALESCE(SUM(quantity),0) n FROM products').fetchone()['n']
            stock=c.execute('SELECT COALESCE(SUM(quantity*buy_price),0) n FROM products').fetchone()['n']
            sales=c.execute("SELECT COALESCE(SUM(total),0) n FROM transactions WHERE tx_type='SALE'").fetchone()['n']
            buys=c.execute("SELECT COALESCE(SUM(total),0) n FROM transactions WHERE tx_type='PURCHASE'").fetchone()['n']
            rows=c.execute('SELECT created_at,tx_type,product_name,quantity,total,username FROM transactions ORDER BY id DESC LIMIT 12').fetchall()
            daily=c.execute("SELECT substr(created_at,1,10) day,COALESCE(SUM(total),0) total FROM transactions WHERE tx_type='SALE' AND created_at>=? GROUP BY substr(created_at,1,10)",((date.today()-__import__('datetime').timedelta(days=6)).strftime('%Y-%m-%d'),)).fetchall()
        for x,v in zip(self.metric_labels,[str(products),f'{units:,}',money(stock),money(sales),money(buys)]): x.setText(v)
        self.dash_note.setText(f"Database stored locally: {DB_PATH}")
        daily_map={r['day']:float(r['total']) for r in daily}; days=[date.today()-__import__('datetime').timedelta(days=i) for i in range(6,-1,-1)]; peak=max(daily_map.values(),default=0)
        for (day_lbl,bar,val),d in zip(self.sales_pulse,days):
            amount=daily_map.get(d.strftime('%Y-%m-%d'),0); day_lbl.setText(d.strftime('%a %d %b')); bar.setValue(int(amount/peak*100) if peak else 0); val.setText(money(amount))
        self.fill_table(self.dash_table,[[r['created_at'],r['tx_type'],r['product_name'],r['quantity'],money(r['total']),r['username']] for r in rows])
    def table(self,headers):
        t=QTableWidget(0,len(headers)); t.setHorizontalHeaderLabels(headers); t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); t.setEditTriggers(QAbstractItemView.NoEditTriggers); t.setSelectionBehavior(QAbstractItemView.SelectRows); t.setAlternatingRowColors(True); t.setWordWrap(True); t.setMinimumHeight(230); t.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel); return t
    def fill_table(self,t,rows):
        t.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,val in enumerate(row): t.setItem(i,j,QTableWidgetItem(str(val)))
    def products_page(self):
        w,l=self.page('Products & stock','Register products, manage prices and keep stock quantities accurate.')
        bar=QHBoxLayout(); self.product_search=QLineEdit(); self.product_search.setPlaceholderText('Search name, category or scan/type barcode…'); self.product_search.textChanged.connect(self.refresh_products)
        add=QPushButton('＋ Add product'); add.clicked.connect(self.add_product); edit=QPushButton('Edit selected'); edit.setObjectName('secondary'); edit.clicked.connect(self.edit_product); delete=QPushButton('Delete selected'); delete.setObjectName('danger'); delete.clicked.connect(self.delete_product)
        bar.addWidget(self.product_search,1); bar.addWidget(add); bar.addWidget(edit); bar.addWidget(delete); l.addLayout(bar)
        self.product_table=self.table(['ID','Barcode','Product','Category','Buying price','Selling price','In stock','Expiry']); l.addWidget(self.product_table,1); self.refresh_products(); return w
    def refresh_products(self):
        if not hasattr(self,'product_table'): return
        q=self.product_search.text().strip() if hasattr(self,'product_search') else ''
        with connect_db() as c: rows=c.execute('SELECT * FROM products WHERE name LIKE ? OR barcode LIKE ? OR category LIKE ? ORDER BY name',tuple(['%'+q+'%']*3)).fetchall()
        self.fill_table(self.product_table,[[r['id'],r['barcode'] or '',r['name'],r['category'],money(r['buy_price']),money(r['sell_price']),r['quantity'],r['expiry_date'] or '—'] for r in rows])
    def selected_product(self):
        r=self.product_table.currentRow()
        if r<0: return None
        pid=int(self.product_table.item(r,0).text())
        with connect_db() as c: return c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
    def add_product(self):
        d=ProductDialog(self, barcode=self.pending_product_barcode)
        self.active_product_dialog=d
        result=d.exec()
        self.active_product_dialog=None
        if result!=QDialog.Accepted: return
        self.pending_product_barcode=''
        barcode,name,cat,buy,sell,qty,img,expiry=d.values()
        if not name: error(self,'Missing name','Enter a product name.'); return
        if expiry:
            try: datetime.strptime(expiry,'%Y-%m-%d')
            except ValueError: error(self,'Invalid expiry date','Use YYYY-MM-DD or leave it blank.'); return
        try:
            with connect_db() as c: c.execute('INSERT INTO products(barcode,name,category,buy_price,sell_price,quantity,image_path,created_at,expiry_date) VALUES(?,?,?,?,?,?,?,?,?)',(barcode,name,cat,buy,sell,qty,img,now(),expiry))
            self.refresh_products(); self.refresh_dashboard()
        except sqlite3.IntegrityError: error(self,'Already in system','That barcode is already registered. Search for the existing product instead.')
    def edit_product(self):
        p=self.selected_product()
        if not p: error(self,'Select product','Choose a product first.'); return
        d=ProductDialog(self,p)
        if d.exec()!=QDialog.Accepted: return
        barcode,name,cat,buy,sell,qty,img,expiry=d.values()
        if not name: error(self,'Missing name','Enter a product name.'); return
        if expiry:
            try: datetime.strptime(expiry,'%Y-%m-%d')
            except ValueError: error(self,'Invalid expiry date','Use YYYY-MM-DD or leave it blank.'); return
        try:
            with connect_db() as c: c.execute('UPDATE products SET barcode=?,name=?,category=?,buy_price=?,sell_price=?,quantity=?,image_path=?,expiry_date=? WHERE id=?',(barcode,name,cat,buy,sell,qty,img,expiry,p['id']))
            self.refresh_products(); self.refresh_dashboard()
        except sqlite3.IntegrityError: error(self,'Duplicate barcode','That barcode belongs to another product.')
    def delete_product(self):
        p=self.selected_product()
        if not p: error(self,'Select product','Choose a product first.'); return
        if QMessageBox.question(self,'Delete product',f"Delete {p['name']}? Historical transactions will remain.")==QMessageBox.Yes:
            with connect_db() as c: c.execute('DELETE FROM products WHERE id=?',(p['id'],))
            self.refresh_products(); self.refresh_dashboard()
    def sales_page(self):
        w,l=self.page('Sales / point of sale','Scan or select products, build a sale and complete checkout.')
        form=QHBoxLayout(); self.sale_barcode=QLineEdit(); self.sale_barcode.setPlaceholderText('Scan barcode here or type barcode…'); self.sale_barcode.returnPressed.connect(self.add_sale_barcode)
        self.sale_qty=QSpinBox(); self.sale_qty.setRange(1,100000); self.sale_qty.setValue(1); b=QPushButton('Add item'); b.clicked.connect(self.add_sale_barcode)
        form.addWidget(self.sale_barcode,1); form.addWidget(QLabel('Qty')); form.addWidget(self.sale_qty); form.addWidget(b); l.addLayout(form)
        self.sale_table=self.table(['Barcode','Product','Qty','Unit price','Line total']); l.addWidget(self.sale_table,1)
        bottom=QHBoxLayout(); self.sale_customer=QLineEdit(); self.sale_customer.setPlaceholderText('Customer name (optional)'); self.sale_total=QLabel('Total: UGX 0'); self.sale_total.setObjectName('title'); clear=QPushButton('Clear cart'); clear.setObjectName('secondary'); clear.clicked.connect(self.clear_cart); checkout=QPushButton('Complete sale'); checkout.clicked.connect(self.complete_sale)
        bottom.addWidget(self.sale_customer,1); bottom.addWidget(clear); bottom.addWidget(self.sale_total); bottom.addWidget(checkout); l.addLayout(bottom); return w
    def add_sale_barcode(self):
        code=self.sale_barcode.text().strip()
        if not code: return
        with connect_db() as c: p=c.execute('SELECT * FROM products WHERE barcode=?',(code,)).fetchone()
        if not p: error(self,'Product not found','This barcode is not registered. Add the product in Products & Stock first.'); self.sale_barcode.clear(); return
        qty=self.sale_qty.value()
        already=sum(x['qty'] for x in self.cart if x['id']==p['id'])
        if already+qty>p['quantity']: error(self,'Insufficient stock',f"Available stock: {p['quantity']-already}"); return
        found=next((x for x in self.cart if x['id']==p['id']),None)
        if found: found['qty']+=qty
        else: self.cart.append({'id':p['id'],'barcode':p['barcode'] or '', 'name':p['name'],'qty':qty,'price':p['sell_price']})
        self.sale_barcode.clear(); self.sale_qty.setValue(1); self.refresh_cart()
    def refresh_cart(self):
        self.fill_table(self.sale_table,[[x['barcode'],x['name'],x['qty'],money(x['price']),money(x['qty']*x['price'])] for x in self.cart]); self.sale_total.setText('Total: '+money(sum(x['qty']*x['price'] for x in self.cart)))
    def clear_cart(self): self.cart=[]; self.refresh_cart()
    def complete_sale(self):
        if not self.cart: error(self,'Empty sale','Add at least one product.'); return
        with connect_db() as c:
            # Validate the entire cart before changing any stock.
            for item in self.cart:
                product=c.execute('SELECT quantity FROM products WHERE id=?',(item['id'],)).fetchone()
                if not product or product['quantity']<item['qty']:
                    error(self,'Stock changed',f"Not enough stock for {item['name']}. Refresh and try again."); return
            for item in self.cart:
                product=c.execute('SELECT buy_price FROM products WHERE id=?',(item['id'],)).fetchone()
                unit_cost=float(product['buy_price'] or 0)
                line_total=item['qty']*item['price']
                c.execute('UPDATE products SET quantity=quantity-? WHERE id=?',(item['qty'],item['id']))
                c.execute('INSERT INTO transactions(tx_type,product_id,product_name,barcode,quantity,unit_price,total,party,username,created_at,cost_price,cost_estimated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('SALE',item['id'],item['name'],item['barcode'],item['qty'],item['price'],line_total,self.sale_customer.text().strip(),self.user['username'],now(),unit_cost,0))
        total=sum(item['qty']*item['price'] for item in self.cart)
        count=sum(item['qty'] for item in self.cart)
        self.clear_cart(); self.sale_customer.clear(); self.refresh_dashboard(); self.refresh_products()
        info(self,'Sale completed',f'Sale recorded successfully.\nItems sold: {count}\nTotal: {money(total)}')
    def purchase_page(self):
        w,l=self.page('Stock purchases','Record supplier deliveries. Purchased quantities are added to available stock.')
        form=QFormLayout(); self.purchase_product=QComboBox(); self.purchase_qty=QSpinBox(); self.purchase_qty.setRange(1,1000000); self.purchase_qty.setValue(1); self.purchase_price=QDoubleSpinBox(); self.purchase_price.setMaximum(1e9); self.purchase_price.setDecimals(2); self.purchase_supplier=QLineEdit(); self.purchase_supplier.setPlaceholderText('Supplier name (optional)'); self.purchase_expiry=QLineEdit(); self.purchase_expiry.setPlaceholderText('YYYY-MM-DD (optional)')
        self.purchase_barcode=QLineEdit(); self.purchase_barcode.setPlaceholderText('Scan/type barcode to select a registered product'); self.purchase_barcode.returnPressed.connect(self.select_purchase_barcode)
        form.addRow('Scan product barcode',self.purchase_barcode); form.addRow('Registered product',self.purchase_product); form.addRow('Quantity received',self.purchase_qty); form.addRow('Unit buying price (UGX)',self.purchase_price); form.addRow('Supplier',self.purchase_supplier); form.addRow('Expiry date (optional)',self.purchase_expiry); l.addLayout(form)
        self.purchase_product.currentIndexChanged.connect(self.purchase_product_changed)
        b=QPushButton('Record purchase & add stock'); b.clicked.connect(self.record_purchase); l.addWidget(b,alignment=Qt.AlignLeft)
        l.addWidget(QLabel('Recent purchases')); self.purchase_table=self.table(['Date & time','Product','Quantity','Unit cost','Total','Supplier','Recorded by']); l.addWidget(self.purchase_table,1); self.refresh_purchase_page(); return w
    def refresh_purchase_page(self):
        with connect_db() as c:
            ps=c.execute('SELECT id,name,buy_price FROM products ORDER BY name').fetchall(); rows=c.execute("SELECT created_at,product_name,quantity,unit_price,total,party,username FROM transactions WHERE tx_type='PURCHASE' ORDER BY id DESC LIMIT 30").fetchall()
        self.purchase_product.blockSignals(True); self.purchase_product.clear()
        for p in ps: self.purchase_product.addItem(p['name'],(p['id'],p['buy_price']))
        self.purchase_product.blockSignals(False); self.purchase_product_changed(); self.fill_table(self.purchase_table,[[r['created_at'],r['product_name'],r['quantity'],money(r['unit_price']),money(r['total']),r['party'],r['username']] for r in rows])
    def select_purchase_barcode(self):
        code=self.purchase_barcode.text().strip()
        with connect_db() as c: p=c.execute('SELECT id FROM products WHERE barcode=?',(code,)).fetchone()
        if not p: error(self,'Product not found','Register this barcode in Products & Stock first.'); return
        idx=self.purchase_product.findData((p['id'], self.purchase_product.currentData()[1] if self.purchase_product.currentData() else 0))
        if idx < 0:
            for i in range(self.purchase_product.count()):
                data=self.purchase_product.itemData(i)
                if data and data[0]==p['id']: idx=i; break
        if idx>=0: self.purchase_product.setCurrentIndex(idx)
        self.purchase_barcode.clear()
    def purchase_product_changed(self):
        data=self.purchase_product.currentData()
        if data: self.purchase_price.setValue(float(data[1]))
    def record_purchase(self):
        data=self.purchase_product.currentData()
        if not data: error(self,'No products','Register a product before recording a purchase.'); return
        pid,_=data; qty=self.purchase_qty.value(); price=self.purchase_price.value(); total=qty*price; expiry=self.purchase_expiry.text().strip()
        if expiry:
            try: datetime.strptime(expiry,'%Y-%m-%d')
            except ValueError: error(self,'Invalid expiry date','Use YYYY-MM-DD or leave it blank.'); return
        with connect_db() as c:
            p=c.execute('SELECT name,barcode FROM products WHERE id=?',(pid,)).fetchone()
            c.execute("UPDATE products SET quantity=quantity+?,buy_price=?,expiry_date=COALESCE(NULLIF(?,''),expiry_date) WHERE id=?",(qty,price,expiry,pid))
            c.execute('INSERT INTO transactions(tx_type,product_id,product_name,barcode,quantity,unit_price,total,party,username,created_at,cost_price,cost_estimated,expiry_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',('PURCHASE',pid,p['name'],p['barcode'],qty,price,total,self.purchase_supplier.text().strip(),self.user['username'],now(),price,0,expiry))
        self.purchase_supplier.clear(); self.purchase_expiry.clear(); self.refresh_purchase_page(); self.refresh_dashboard(); self.refresh_products(); info(self,'Purchase recorded',f'{qty} units added. Total purchase: {money(total)}')
    def reports_page(self):
        w,l=self.page('Sales reports','Review revenue and item volumes over a selected period.')
        bar=QHBoxLayout(); self.report_from=QDateEdit(); self.report_from.setCalendarPopup(True); self.report_from.setDate(date.today().replace(day=1)); self.report_to=QDateEdit(); self.report_to.setCalendarPopup(True); self.report_to.setDate(date.today()); go=QPushButton('Generate report'); go.clicked.connect(self.refresh_reports); export=QPushButton('Export CSV'); export.setObjectName('secondary'); export.clicked.connect(lambda:self.export_transactions('SALE'))
        bar.addWidget(QLabel('From')); bar.addWidget(self.report_from); bar.addWidget(QLabel('To')); bar.addWidget(self.report_to); bar.addWidget(go); bar.addWidget(export); bar.addStretch(); l.addLayout(bar)
        row=QHBoxLayout()
        self.report_revenue_card,self.report_revenue=self.card('Sales revenue','UGX 0')
        self.report_items_card,self.report_items=self.card('Items sold','0')
        self.report_count_card,self.report_count=self.card('Sales lines','0')
        self.report_cost_card,self.report_cost=self.card('Cost of goods sold','UGX 0')
        self.report_profit_card,self.report_profit=self.card('Gross profit','UGX 0')
        for card in [self.report_revenue_card,self.report_items_card,self.report_count_card,self.report_cost_card,self.report_profit_card]: row.addWidget(card)
        l.addLayout(row); hint=QLabel('Gross profit = sales revenue minus the recorded cost of goods sold. Historical sales from older versions may not contain an original cost snapshot.'); hint.setObjectName('muted'); hint.setWordWrap(True); l.addWidget(hint); self.report_table=self.table(['Date & time','Product','Quantity sold','Unit price','Sales total','Customer','Staff']); l.addWidget(self.report_table,1); return w
    def refresh_reports(self):
        if not all(hasattr(self,n) for n in ('report_table','report_from','report_to','report_revenue','report_items','report_count','report_cost','report_profit')): return
        try:
            import shiboken6
            if not all(shiboken6.isValid(getattr(self,n)) for n in ('report_table','report_from','report_to','report_revenue','report_items','report_count','report_cost','report_profit')): return
        except (ImportError, RuntimeError): return
        start=self.report_from.date().toString('yyyy-MM-dd')+' 00:00:00'; end=self.report_to.date().toString('yyyy-MM-dd')+' 23:59:59'
        with connect_db() as c:
            rows=c.execute("SELECT created_at,product_name,quantity,unit_price,total,party,username,cost_price,cost_estimated FROM transactions WHERE tx_type='SALE' AND created_at BETWEEN ? AND ? ORDER BY id DESC",(start,end)).fetchall()
        revenue=sum(r['total'] for r in rows); cogs=sum(r['quantity']*r['cost_price'] for r in rows); profit=revenue-cogs; estimated=sum(1 for r in rows if r['cost_estimated'])
        self.report_revenue.setText(money(revenue)); self.report_items.setText(f"{sum(r['quantity'] for r in rows):,}"); self.report_count.setText(str(len(rows))); self.report_cost.setText(money(cogs)); self.report_profit.setText(money(profit)); self.report_profit.setToolTip(f'{estimated} older sales line(s) use estimated cost values.')
        self.fill_table(self.report_table,[[r['created_at'],r['product_name'],r['quantity'],money(r['unit_price']),money(r['total']),r['party'],r['username']] for r in rows])
    def expiry_page(self):
        w,l=self.page('Expiry alerts','Track expired products and stock approaching its expiry date.')
        self.expiry_summary=QLabel(''); self.expiry_summary.setObjectName('muted'); l.addWidget(self.expiry_summary)
        note=QLabel('Enter dates when registering products or recording supplier deliveries.'); note.setObjectName('muted'); l.addWidget(note)
        self.expiry_table=self.table(['Product','Barcode','Quantity','Expiry date','Status','Days remaining']); l.addWidget(self.expiry_table,1)
        self.refresh_expiry(); return w
    def refresh_expiry(self):
        if not hasattr(self,'expiry_table'): return
        today=date.today()
        with connect_db() as c: rows=c.execute("SELECT name,barcode,quantity,expiry_date FROM products WHERE COALESCE(expiry_date,'')<>'' ORDER BY expiry_date").fetchall()
        out=[]; expired=0; soon=0
        for r in rows:
            try: exp=date.fromisoformat(r['expiry_date']); days=(exp-today).days
            except ValueError: continue
            status='EXPIRED' if days<0 else ('EXPIRING SOON' if days<=30 else 'OK')
            expired += int(days<0); soon += int(0<=days<=30)
            out.append([r['name'],r['barcode'] or '',r['quantity'],exp.isoformat(),status,days])
        self.expiry_summary.setText(f'{expired} expired  •  {soon} expiring within 30 days  •  {len(out)} products tracked')
        self.fill_table(self.expiry_table,out)
    def history_page(self):
        w,l=self.page('Transaction history','Sales and purchases are stored as separate transaction types.')
        bar=QHBoxLayout(); self.history_filter=QComboBox(); self.history_filter.addItems(['ALL','SALE','PURCHASE']); self.history_filter.currentIndexChanged.connect(self.refresh_history); self.history_search=QLineEdit(); self.history_search.setPlaceholderText('Search product, customer or staff…'); self.history_search.textChanged.connect(self.refresh_history); exp=QPushButton('Export visible CSV'); exp.setObjectName('secondary'); exp.clicked.connect(self.export_visible_history)
        bar.addWidget(self.history_filter); bar.addWidget(self.history_search,1); bar.addWidget(exp); l.addLayout(bar)
        self.history_table=self.table(['Date & time','Type','Product','Barcode','Quantity','Unit price','Total','Customer / supplier','User']); l.addWidget(self.history_table,1); self.refresh_history(); return w
    def refresh_history(self):
        if not hasattr(self,'history_table'): return
        typ=self.history_filter.currentText() if hasattr(self,'history_filter') else 'ALL'; q=self.history_search.text().strip() if hasattr(self,'history_search') else ''
        sql='SELECT created_at,tx_type,product_name,barcode,quantity,unit_price,total,party,username FROM transactions WHERE (product_name LIKE ? OR party LIKE ? OR username LIKE ? OR barcode LIKE ?)'; args=['%'+q+'%']*4
        if typ!='ALL': sql+=' AND tx_type=?'; args.append(typ)
        sql+=' ORDER BY id DESC LIMIT 5000'
        with connect_db() as c: rows=c.execute(sql,args).fetchall()
        self.fill_table(self.history_table,[[r['created_at'],r['tx_type'],r['product_name'],r['barcode'] or '',r['quantity'],money(r['unit_price']),money(r['total']),r['party'],r['username']] for r in rows])
    def export_rows(self,rows,headers):
        path,_=QFileDialog.getSaveFileName(self,'Export CSV',f'StockFlow_{datetime.now():%Y%m%d}.csv','CSV files (*.csv)')
        if not path:return
        with open(path,'w',newline='',encoding='utf-8-sig') as f:
            wr=csv.writer(f); wr.writerow(headers); wr.writerows(rows)
        info(self,'Export complete',f'CSV saved to:\n{path}')
    def export_transactions(self,typ):
        with connect_db() as c: rows=c.execute('SELECT created_at,tx_type,product_name,barcode,quantity,unit_price,total,party,username FROM transactions WHERE tx_type=? ORDER BY id DESC',(typ,)).fetchall()
        self.export_rows([list(r) for r in rows],['Date & time','Type','Product','Barcode','Quantity','Unit price','Total','Customer / supplier','User'])
    def export_visible_history(self):
        rows=[]
        for i in range(self.history_table.rowCount()): rows.append([self.history_table.item(i,j).text() if self.history_table.item(i,j) else '' for j in range(self.history_table.columnCount())])
        self.export_rows(rows,['Date & time','Type','Product','Barcode','Quantity','Unit price','Total','Customer / supplier','User'])
    def phone_page(self):
        w,l=self.page('Phone barcode scanner','Use the Android StockFlow Scanner app over Bluetooth, or use the Wi-Fi camera scanner as a fallback. Your USB scanner continues to work.')
        ip=local_ip(); self.phone_url=f'https://{ip}:{self.phone.port}/'
        box=QFrame(); box.setObjectName('card'); v=QVBoxLayout(box); title=QLabel('1. Connect your phone to the same Wi-Fi as this computer'); title.setStyleSheet('font-weight:700;font-size:14pt'); v.addWidget(title)
        v.addWidget(QLabel('2. Open this address on your phone:')); url=QLabel(self.phone_url); url.setStyleSheet('font-size:18pt;color:#315fce;font-weight:700'); v.addWidget(url)
        v.addWidget(QLabel('3. Allow camera access and scan. Scanned barcodes are sent directly to this computer.'))
        bt_row=QHBoxLayout(); bt_row.addWidget(QLabel('Bluetooth incoming port:'))
        self.bluetooth_port_input=QLineEdit(self.bluetooth.port); self.bluetooth_port_input.setMaximumWidth(110); self.bluetooth_port_input.setPlaceholderText('COM3'); bt_row.addWidget(self.bluetooth_port_input)
        self.bluetooth_connect_btn=QPushButton('Connect / Reconnect'); self.bluetooth_connect_btn.clicked.connect(self.reconnect_bluetooth); bt_row.addWidget(self.bluetooth_connect_btn); bt_row.addStretch(); v.addLayout(bt_row)
        self.phone_status=QLabel('Bluetooth is checking '+self.bluetooth.port+' automatically. You can connect your phone later without restarting StockFlow.'); self.phone_status.setWordWrap(True); v.addWidget(self.phone_status)
        note=QLabel('ANDROID APK: Build it with build_windows.bat after installing Android Studio (Android SDK 35) and Java 17. Pair your phone with this PC in Bluetooth settings, choose the PC in the app, and connect. Windows Bluetooth COM3 must be the Incoming port. If your incoming port is different, set STOCKFLOW_BLUETOOTH_COM=COMx before launching StockFlow. Wi-Fi fallback: HTTPS is enabled for camera access. IMPORTANT: install the StockFlow Local Scanner CA certificate on your phone and trust it before opening the QR link. Copy phone_ca.crt from %LOCALAPPDATA%\\StockFlow to the phone (USB/email), install it as a CA certificate, then reopen the scanner. Windows Firewall must allow private-network access. The scanner uses the phone browser built-in barcode detector; no external scanning library download is needed. On Android Chrome, use the browser menu and choose Add to Home screen to install it like an app.'); note.setWordWrap(True); note.setObjectName('muted'); v.addWidget(note)
        ca_btn=QPushButton('Export phone trust certificate (phone_ca.crt)'); ca_btn.setObjectName('secondary')
        def export_phone_ca():
            target,_=QFileDialog.getSaveFileName(self,'Save phone trust certificate','phone_ca.crt','Certificate (*.crt)')
            if target:
                shutil.copy2(self.phone.ca_path,target); info(self,'Certificate exported','Transfer this .crt file to your phone and install it as a CA certificate. Then reopen the HTTPS scanner.')
        ca_btn.clicked.connect(export_phone_ca); v.addWidget(ca_btn)
        try:
            import qrcode
            qr_path=BASE_DIR/'phone_scanner_qr.png'; qrcode.make(self.phone_url).save(qr_path)
            qr=QLabel(); qr.setPixmap(QPixmap(str(qr_path)).scaled(190,190,Qt.KeepAspectRatio,Qt.SmoothTransformation)); qr.setAlignment(Qt.AlignLeft); v.addWidget(QLabel('Or scan this QR code with your phone:')); v.addWidget(qr)
        except Exception: pass
        l.addWidget(box); l.addWidget(QLabel('Latest phone scans'))
        self.phone_table=self.table(['Received at','Barcode','Action']); l.addWidget(self.phone_table,1)
        return w
    def reconnect_bluetooth(self):
        port = self.bluetooth_port_input.text() if hasattr(self, 'bluetooth_port_input') else self.bluetooth.port
        if self.bluetooth.set_port(port):
            self.phone_status.setText(f'Reconnecting to {port.strip().upper()}… Keep StockFlow open; it will listen for the phone scanner.')

    def receive_phone_scans(self):
        while not BLUETOOTH_STATUS.empty():
            state, message = BLUETOOTH_STATUS.get()
            if hasattr(self, 'phone_status'):
                self.phone_status.setText(('Bluetooth: ' if state != 'error' else 'Bluetooth setup: ') + message)
        while not PHONE_ACTIONS.empty():
            action = PHONE_ACTIONS.get()
            try:
                kind = str(action.get('type', '')).lower()
                barcode = str(action.get('barcode', '')).strip()[:120]
                if kind == 'scan':
                    if barcode: PHONE_SCANS.put(barcode)
                    continue
                if kind == 'product':
                    name = str(action.get('name', '')).strip()[:200]
                    if not name: raise ValueError('Product name is required.')
                    category = str(action.get('category', '')).strip()[:120]
                    buy = max(0.0, float(action.get('buy_price', 0) or 0))
                    sell = max(0.0, float(action.get('sell_price', 0) or 0))
                    qty = max(0, int(action.get('quantity', 0) or 0))
                    expiry = str(action.get('expiry_date', '')).strip()[:20]
                    if expiry: datetime.strptime(expiry, '%Y-%m-%d')
                    with connect_db() as c:
                        existing = c.execute('SELECT id FROM products WHERE barcode=?', (barcode,)).fetchone() if barcode else None
                        if existing:
                            c.execute('UPDATE products SET name=?,category=?,buy_price=?,sell_price=?,quantity=?,expiry_date=? WHERE id=?', (name,category,buy,sell,qty,expiry,existing['id']))
                        else:
                            c.execute('INSERT INTO products(barcode,name,category,buy_price,sell_price,quantity,created_at,expiry_date) VALUES(?,?,?,?,?,?,?,?)', (barcode or None,name,category,buy,sell,qty,now(),expiry))
                    if hasattr(self, 'phone_status'): self.phone_status.setText(f'Phone product saved: {name}.')
                elif kind in ('sale', 'purchase'):
                    qty = int(action.get('quantity', 0) or 0); price = float(action.get('unit_price', 0) or 0)
                    party = str(action.get('party', '')).strip()[:160]
                    if not barcode or qty < 1 or price < 0: raise ValueError('Barcode, positive quantity and valid price are required.')
                    with connect_db() as c:
                        product = c.execute('SELECT * FROM products WHERE barcode=?', (barcode,)).fetchone()
                        if not product: raise ValueError(f'Barcode {barcode} is not registered. Add the product first.')
                        if kind == 'sale' and product['quantity'] < qty: raise ValueError(f"Not enough stock for {product['name']}.")
                        delta = -qty if kind == 'sale' else qty
                        c.execute('UPDATE products SET quantity=quantity+? WHERE id=?', (delta,product['id']))
                        cost = float(product['buy_price'] or 0)
                        c.execute('INSERT INTO transactions(tx_type,product_id,product_name,barcode,quantity,unit_price,total,party,username,created_at,cost_price,cost_estimated,expiry_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (kind.upper(),product['id'],product['name'],barcode,qty,price,qty*price,party,self.user['username'],now(),cost,0,product['expiry_date'] or ''))
                    if hasattr(self, 'phone_status'): self.phone_status.setText(f'Phone {kind} saved: {product["name"]} × {qty}.')
                else: raise ValueError('Unknown phone action.')
                if hasattr(self, 'refresh_products'): self.refresh_products()
                if hasattr(self, 'refresh_dashboard'): self.refresh_dashboard()
            except Exception as exc:
                if hasattr(self, 'phone_status'): self.phone_status.setText('Phone update not saved: ' + str(exc))
        while not PHONE_SCANS.empty():
            code=PHONE_SCANS.get()
            if hasattr(self,'phone_table'):
                r=self.phone_table.rowCount(); self.phone_table.insertRow(r)
                for j,val in enumerate([now(),code,'Received']): self.phone_table.setItem(r,j,QTableWidgetItem(val))
            # Route scans to the visible workflow, or the last selected workflow
            # while the desktop shows the Phone Scanner instructions.
            idx=self.stack.currentIndex()
            if idx==6: idx=self.last_scan_target
            if idx==1:
                self.pending_product_barcode=code
                # If the Add Product dialog is already open, insert the phone scan
                # directly into its Barcode / code field. Otherwise stage it for
                # the next Add Product dialog and make it searchable in the list.
                dialog=getattr(self,'active_product_dialog',None)
                if dialog is not None:
                    dialog.barcode.setText(code)
                    dialog.barcode.setFocus()
                    self.phone_status.setText(f'Barcode {code} inserted into Product Details.') if hasattr(self,'phone_status') else None
                else:
                    self.product_search.setText(code)
                    self.phone_status.setText(f'Barcode received: {code}. It will be prefilled when you click Add product.') if hasattr(self,'phone_status') else None
            elif idx==2: self.sale_barcode.setText(code); self.add_sale_barcode()
            elif idx==3: self.purchase_barcode.setText(code); self.select_purchase_barcode()
            elif hasattr(self,'phone_status'): self.phone_status.setText(f'Barcode received: {code}. Switch to Products, Sales or Purchases to use it.')
    def staff_page(self):
        w,l=self.page('Staff accounts','Create employee logins and deactivate accounts when needed.')
        form=QHBoxLayout(); self.staff_user=QLineEdit(); self.staff_user.setPlaceholderText('New username'); self.staff_pass=QLineEdit(); self.staff_pass.setPlaceholderText('Password (minimum 5 characters)'); self.staff_role=QComboBox(); self.staff_role.addItems(['staff','admin']); add=QPushButton('Create account'); add.clicked.connect(self.create_staff)
        form.addWidget(self.staff_user); form.addWidget(self.staff_pass); form.addWidget(self.staff_role); form.addWidget(add); l.addLayout(form)
        deact=QPushButton('Deactivate selected account'); deact.setObjectName('danger'); deact.clicked.connect(self.deactivate_staff); l.addWidget(deact,alignment=Qt.AlignLeft)
        self.staff_table=self.table(['ID','Username','Role','Active']); l.addWidget(self.staff_table,1); self.refresh_staff(); return w
    def refresh_staff(self):
        with connect_db() as c: rows=c.execute('SELECT id,username,role,active FROM users ORDER BY username').fetchall()
        self.fill_table(self.staff_table,[[r['id'],r['username'],r['role'],'Yes' if r['active'] else 'No'] for r in rows])
    def create_staff(self):
        username=self.staff_user.text().strip(); password=self.staff_pass.text()
        if not username or len(password)<5: error(self,'Invalid account','Enter a username and a password of at least 5 characters.'); return
        salt,ph=hash_password(password)
        try:
            with connect_db() as c: c.execute('INSERT INTO users(username,salt,password_hash,role) VALUES(?,?,?,?)',(username,salt,ph,self.staff_role.currentText()))
            self.staff_user.clear(); self.staff_pass.clear(); self.refresh_staff(); info(self,'Account created',f'Account {username} created.')
        except sqlite3.IntegrityError: error(self,'Username exists','Choose another username.')
    def deactivate_staff(self):
        r=self.staff_table.currentRow()
        if r<0: error(self,'Select account','Choose an account first.'); return
        uid=int(self.staff_table.item(r,0).text()); username=self.staff_table.item(r,1).text()
        if username==self.user['username']: error(self,'Cannot deactivate','You cannot deactivate the account currently signed in.'); return
        if QMessageBox.question(self,'Deactivate account',f'Deactivate {username}?')==QMessageBox.Yes:
            with connect_db() as c: c.execute('UPDATE users SET active=0 WHERE id=?',(uid,))
            self.refresh_staff()
    def settings_page(self):
        w,l=self.page('Settings & data','Local database, automatic backups and business data protection.')
        l.addWidget(QLabel('Currency: UGX (Ugandan Shilling)')); l.addWidget(QLabel(f'Database location: {DB_PATH}'))
        l.addWidget(QLabel(f'Automatic daily backups are stored in: {BACKUP_DIR}'))
        b=QPushButton('Back up database now'); b.clicked.connect(self.backup_db); l.addWidget(b,alignment=Qt.AlignLeft)
        l.addWidget(QLabel('Default administrator login on first installation: admin / admin123. Change this by creating a new administrator account, then deactivate the default account.'))
        l.addStretch(); return w
    def backup_db(self):
        path,_=QFileDialog.getSaveFileName(self,'Back up database','business_inventory_backup.db','SQLite database (*.db)')
        if path:
            src=sqlite3.connect(DB_PATH); dst=sqlite3.connect(path)
            try: src.backup(dst)
            finally: dst.close(); src.close()
            info(self,'Backup complete',f'Database backup saved to:\n{path}')
    def closeEvent(self,event):
        try: automatic_backup()
        except Exception: pass
        self.phone.stop(); self.bluetooth.stop(); event.accept()

def main():
    automatic_backup()  # preserve the pre-migration database on first upgrade
    init_db(); automatic_backup(); app=QApplication(sys.argv); app.setWindowIcon(QIcon(str(resource_path('stockflow.ico')))); app.setStyle('Fusion'); app.setStyleSheet(STYLE); app.setFont(QFont('Segoe UI',10))
    login=LoginDialog()
    if login.exec()!=QDialog.Accepted: return
    win=MainWindow(login.user); win.showMaximized(); sys.exit(app.exec())

if __name__=='__main__': main()
