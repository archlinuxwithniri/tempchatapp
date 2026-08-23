# --- setupNlaunch.py ---
# Run: python setupNlaunch.py
# Make sure .token.zip exists in your GitHub repo (with token.txt inside)

import os
import subprocess
import shlex
import time
import re
import requests
import threading
import pyzipper
from flask import Flask, Response, request, send_from_directory
from getpass import getpass
import uuid

# ----------------------------
# 0️⃣ Ask whether to update GitHub
# ----------------------------
UPDATE_GITHUB = input("📡 Do you want to update URL in github ? (Y/N): ").strip().lower() == "y"

# ----------------------------
# 1️⃣ Download & decrypt .token.zip
# ----------------------------
if UPDATE_GITHUB:
    ZIP_URL = "https://github.com/archlinuxwithniri/tempchatapp/raw/main/.token.zip"
    ZIP_PATH = "/content/.token.zip"
    TOKEN_TXT = "/content/token.txt"

    print("⬇️ Downloading encrypted .token.zip ...")
    r = requests.get(ZIP_URL)
    if r.status_code != 200:
        raise SystemExit(f"❌ Failed to download zip file: HTTP {r.status_code}")
    with open(ZIP_PATH, "wb") as f:
        f.write(r.content)
    print("✅ Download complete.")

    password = getpass("🔑 Enter password to decrypt .token.zip: ")

    def try_extract(zip_class, pwd):
        try:
            with zip_class(ZIP_PATH) as zf:
                zf.pwd = pwd.encode('utf-8')
                zf.extractall("/content")
            return True
        except Exception:
            return False

    ok = try_extract(pyzipper.AESZipFile, password) or try_extract(pyzipper.ZipFile, password)
    if not ok:
        raise SystemExit("❌ Wrong password or unsupported ZIP encryption format.")

    if not os.path.exists(TOKEN_TXT):
        raise SystemExit("❌ token.txt not found after extraction!")

    with open(TOKEN_TXT, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
        GITHUB_TOKEN = lines[0] if len(lines) > 0 else None
        EMAIL = lines[1] if len(lines) > 1 else None

    os.remove(TOKEN_TXT)
    os.remove(ZIP_PATH)
    print("🔐 Token successfully loaded and ready to use!")
else:
    GITHUB_TOKEN = None
    EMAIL = None
    print("⏭️ GitHub README updates disabled.")

# ----------------------------
# 2️⃣ Ensure dependencies installed
# ----------------------------
def ensure_installed(pkgs):
    subprocess.run([os.sys.executable, "-m", "pip", "install", "-q"] + pkgs, check=True)

ensure_installed(["flask", "requests", "pyzipper"])

# Download cloudflared binary manually (since pip one is not the tunnel CLI)
CLOUDFLARED_BIN = "/usr/local/bin/cloudflared"
if not os.path.exists(CLOUDFLARED_BIN):
    print("📦 Downloading cloudflared binary...")
    subprocess.run([
        "wget", "-q", "-O", CLOUDFLARED_BIN,
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    ], check=True)
    subprocess.run(["chmod", "+x", CLOUDFLARED_BIN], check=True)

# ----------------------------
# 3️⃣ Flask Chat + Tunnel Setup
# ----------------------------
USERNAME = "archlinuxwithniri"
EMAIL = EMAIL
REPO_NAME = "tempchatapp"
UPDATE_SCRIPT_PATH = "/content/update_github_status.py"
RAW_PY_URL = f"https://raw.githubusercontent.com/{USERNAME}/{REPO_NAME}/main/update_github_status.py"
GITHUB_HTML = f"https://raw.githubusercontent.com/{USERNAME}/{REPO_NAME}/main/index.html"
CHAT_FILE = "chat.txt"
PHOTO_DIR = "/content/photos"
os.makedirs(PHOTO_DIR, exist_ok=True)
PORT = 8000

# --- Download update script ---
if UPDATE_GITHUB:
    print("📥 Downloading update_github_status.py...")
    try:
        r = requests.get(RAW_PY_URL, timeout=10)
        r.raise_for_status()
        with open(UPDATE_SCRIPT_PATH, "w", encoding="utf-8") as f:
            f.write(r.text)
        print("✅ update_github_status.py downloaded.")
    except Exception as e:
        print("❌ Failed to download update script:", e)

# --- Flask app setup ---
app = Flask(__name__)

@app.route("/")
def index():
    try:
        html = requests.get(GITHUB_HTML, timeout=5).text
    except Exception as e:
        html = f"<h3>Failed to fetch HTML: {e}</h3>"
    return Response(html, mimetype="text/html")

@app.route("/post", methods=["POST"])
def post():
    msg = request.get_json(force=True).get("msg", "").strip()
    if msg:
        with open(CHAT_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    return {"status": "ok"}

@app.route("/messages")
def messages():
    try:
        with open(CHAT_FILE, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="text/plain")
    except FileNotFoundError:
        return Response("", mimetype="text/plain")

# --- Serve uploaded photos ---
@app.route("/photos/<filename>")
def serve_photo(filename):
    return send_from_directory(PHOTO_DIR, filename)

# --- Upload photo with optional caption ---
@app.route("/upload_photo", methods=["POST"])
def upload_photo():
    file = request.files.get("photo")
    caption = request.form.get("caption", "").strip()
    username = request.form.get("username", "Anon")
    if file:
        ext = os.path.splitext(file.filename)[1]
        fname = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(PHOTO_DIR, fname)
        file.save(file_path)

        # Save record in chat.txt
        if caption:
            line = f"{username} : [photo]{fname}|{caption}"
        else:
            line = f"{username} : [photo]{fname}"
        with open(CHAT_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return {"status": "ok", "filename": fname}
    return {"status": "error", "msg": "No file uploaded"}, 400

def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

threading.Thread(target=run_flask, daemon=True).start()
time.sleep(2)

# --- Start Cloudflared ---
print("🚀 Starting Cloudflared tunnel...")
logfile = "/content/cloudflared.log"
cmd = f"{CLOUDFLARED_BIN} tunnel --url http://localhost:{PORT} --no-autoupdate"
proc = subprocess.Popen(shlex.split(cmd), stdout=open(logfile, "w"), stderr=subprocess.STDOUT)

def find_public_url(timeout=30):
    for _ in range(timeout):
        time.sleep(1)
        if not os.path.exists(logfile):
            continue
        txt = open(logfile, "r", encoding="utf-8", errors="ignore").read()
        match = re.search(r"https://[a-z0-9\-]+\.trycloudflare\.com", txt)
        if match:
            return match.group(0)
    return None

# --- Wait for tunnel URL ---
url = find_public_url()
if not url:
    print("⚠️ Could not find public URL. Check /content/cloudflared.log")
else:
    print(f"🌍 Public URL: {url}")

    # Mark online
    if GITHUB_TOKEN and UPDATE_GITHUB:
        print("🔵 Updating README to Online...")
        subprocess.run([
            "python", UPDATE_SCRIPT_PATH,
            GITHUB_TOKEN, USERNAME, EMAIL, REPO_NAME, url, "true"
        ], check=False)
        print("✅ README updated: Online.")
    else:
        print("⏭️ Skipping GitHub README update.")

print("⏳ Tunnel active. Keep this running. Stop to end session.")

# --- When stopped ---
try:
    proc.wait()
except KeyboardInterrupt:
    print("\n🟥 Interrupted by user.")
finally:
    try:
        if GITHUB_TOKEN and UPDATE_GITHUB:
            print("🔴 Updating README to Offline...")
            subprocess.run([
                "python", UPDATE_SCRIPT_PATH,
                GITHUB_TOKEN, USERNAME, EMAIL, REPO_NAME, "none", "false"
            ], check=False)
            print("✅ README updated: Offline.")
    except Exception as e:
        print("❌ Failed to update README to Offline:", e)

    try:
        proc.kill()
    except Exception:
        pass

print("🏁 Session ended.")
