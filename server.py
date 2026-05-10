"""
Lanyard - 局域网加密文件与消息分享工具
依赖: pip install fastapi uvicorn[standard] python-multipart qrcode pillow cryptography
运行: python server.py [--port 8443] [--name "我的房间"] [--password 123456]
"""

import uuid, json, socket, hashlib, secrets, argparse, datetime, ipaddress
import os
import sys
import asyncio
from pathlib import Path
from typing import Optional
from io import BytesIO
from base64 import b64encode

import uvicorn, qrcode

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

parser = argparse.ArgumentParser(description="Lanyard 局域网加密通信")
parser.add_argument("--port",     type=int, default=8443,      help="监听端口（默认 8443）")
parser.add_argument("--name",     type=str, default="LANYARD", help="房间名称")
parser.add_argument("--password", type=str, default="",        help="房间密码（留空不加密）")
parser.add_argument("--max-file", type=int, default=500,       help="最大文件 MB")
args, _ = parser.parse_known_args()

PORT        = args.port
ROOM_NAME   = args.name
MAX_FILE_MB = args.max_file
ROOM_HAS_PW = bool(args.password)
ROOM_PW_HASH = hashlib.sha256(args.password.encode()).hexdigest() if args.password else ""

BASE_DIR   = Path(__file__).parent
CERT_DIR   = BASE_DIR / "certs"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
CERT_DIR.mkdir(exist_ok=True)

SERVER_AES_KEY = secrets.token_bytes(32)

def generate_self_signed_cert():
    cert_file = CERT_DIR / "cert.pem"
    key_file  = CERT_DIR / "key.pem"
    if cert_file.exists() and key_file.exists():
        return str(cert_file), str(key_file)
    pk  = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    lip = get_local_ip()
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Lanyard Local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Lanyard2"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(pk.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address(lip)),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False)
        .sign(pk, hashes.SHA256())
    )
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    print(f"  ✓ 已生成自签 TLS 证书 → {cert_file}")
    return str(cert_file), str(key_file)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "127.0.0.1"

class ConnectionManager:
    def __init__(self):
        self.connections: dict[str, dict] = {}
        self.history: list[dict] = []
        self.disconnect_tasks: dict[str, asyncio.Task] = {} 

    async def connect(self, ws: WebSocket, nickname: str, color: str, client_ip: str, avatar: str = "") -> str:

        kicked_old_nick = None

        for old_cid, info in list(self.connections.items()):
            if info.get("ip") == client_ip:
                old_ws = info["ws"]
                kicked_old_nick = info["nickname"]
                self.connections.pop(old_cid, None) 
                try:
                    await old_ws.close()
                except:
                    pass
        

        if kicked_old_nick:
            if kicked_old_nick in self.disconnect_tasks:
                self.disconnect_tasks[kicked_old_nick].cancel()
                del self.disconnect_tasks[kicked_old_nick]

            if kicked_old_nick != nickname:
                await self.broadcast_system(f"{kicked_old_nick} 离开了房间")

        await ws.accept()
        ws_id = str(uuid.uuid4())[:8]

        self.connections[ws_id] = {"ws": ws, "nickname": nickname, "color": color, "ip": client_ip, "avatar": avatar}

        if nickname in self.disconnect_tasks:
            self.disconnect_tasks[nickname].cancel()
            del self.disconnect_tasks[nickname]
        else:
            await self.broadcast_system(f"{nickname} 加入了房间", exclude=ws_id)

        await ws.send_json({
            "type": "init", "history": self.history[-50:],
            "users": self._user_list(), "myId": ws_id, "roomName": ROOM_NAME,
        })
        await self.broadcast_users()
        return ws_id

    async def disconnect(self, ws_id: str):
        info = self.connections.pop(ws_id, None)
        if not info: return
        nickname = info["nickname"]
  
        if any(c["nickname"] == nickname for c in self.connections.values()):
            await self.broadcast_users()
            return

        task = asyncio.create_task(self._delayed_disconnect(nickname))
        self.disconnect_tasks[nickname] = task

    async def _delayed_disconnect(self, nickname: str):
        try:
            await asyncio.sleep(2.0) 

            if nickname in self.disconnect_tasks:
                del self.disconnect_tasks[nickname]
            await self.broadcast_system(f"{nickname} 离开了房间")
            await self.broadcast_users()
        except asyncio.CancelledError:
            pass

    async def broadcast(self, message: dict, exclude: Optional[str] = None):
        dead = []
        for cid, info in self.connections.items():
            if cid == exclude: continue
            try: await info["ws"].send_json(message)
            except: dead.append(cid)
        for cid in dead: 
            asyncio.create_task(self.disconnect(cid))

    async def broadcast_system(self, text: str, exclude: Optional[str] = None):
        msg = {"type": "system", "text": text, "ts": _ts()}
        self.history.append(msg)
        await self.broadcast(msg, exclude=exclude)

    async def broadcast_users(self):
        await self.broadcast({"type": "users", "users": self._user_list()})

    def _user_list(self):
        return [{"id": c, "nickname": v["nickname"], "color": v["color"], "avatar": v.get("avatar", "")} for c, v in self.connections.items()]

    def add_history(self, msg: dict):
        self.history.append(msg)
        if len(self.history) > 200: self.history = self.history[-200:]
        
def _ts(): return datetime.datetime.now().strftime("%H:%M")

manager = ConnectionManager()

app = FastAPI(title="Lanyard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def silence_event_loop_closed(loop, context):
    """专门用来拦截并丢弃 Windows 10054 报错"""
    exc = context.get("exception")
    if isinstance(exc, ConnectionResetError) and getattr(exc, 'winerror', None) == 10054:
        return
    loop.default_exception_handler(context)

@app.on_event("startup")
async def on_startup():
    if sys.platform == "win32":
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(silence_event_loop_closed)

@app.get("/api/room")
def room_info():
    return JSONResponse({"name": ROOM_NAME, "hasPassword": ROOM_HAS_PW})

@app.post("/api/auth")
def auth(body: dict):
    pw = body.get("password", "")
    if hashlib.sha256(pw.encode()).hexdigest() != ROOM_PW_HASH:
        raise HTTPException(401, "密码错误")
    return JSONResponse({"key": SERVER_AES_KEY.hex(), "roomName": ROOM_NAME})

@app.get("/api/key")
def get_key():
    if ROOM_HAS_PW: raise HTTPException(403, "此房间需要密码，请 POST /api/auth")
    return JSONResponse({"key": SERVER_AES_KEY.hex(), "roomName": ROOM_NAME})

@app.get("/api/qr")
def get_qr():
    url = f"https://{get_local_ip()}:{PORT}"
    buf = BytesIO(); qrcode.make(url).save(buf, format="PNG")
    return JSONResponse({"qr": f"data:image/png;base64,{b64encode(buf.getvalue()).decode()}", "url": url})

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    sender: str = Form("匿名"),
    filename_enc: str = Form(""),
    nonce: str = Form(""),
    file_nonce: str = Form(""),
    auth_token: str = Form(""),
):
    if ROOM_HAS_PW and auth_token != ROOM_PW_HASH:
        raise HTTPException(403, "未授权")
    
    file_id = str(uuid.uuid4())
    file_path = UPLOAD_DIR / file_id
    
    total_size = 0
    max_bytes = MAX_FILE_MB * 1024 * 1024
    
    with open(file_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            
            total_size += len(chunk)
            if total_size > max_bytes:
                file_path.unlink(missing_ok=True)
                raise HTTPException(413, f"超过 {MAX_FILE_MB}MB")
                
            f.write(chunk)


    msg = {"type": "file", "fileId": file_id, "sender": sender,
           "filenameEnc": filename_enc, "nonce": nonce, "fileNonce": file_nonce,
           "size": total_size, "ts": _ts()}
    manager.add_history(msg)
    await manager.broadcast(msg)
    
    return JSONResponse({"fileId": file_id, "ok": True})

@app.get("/api/download/{file_id}")
def download_file(file_id: str):
    try: uuid.UUID(file_id)
    except ValueError: raise HTTPException(400, "无效 ID")
    path = UPLOAD_DIR / file_id
    if not path.exists(): raise HTTPException(404, "文件不存在")
    return FileResponse(path, media_type="application/octet-stream")

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    token    = ws.query_params.get("token", "")
    nickname = ws.query_params.get("nickname", "匿名用户")
    color    = ws.query_params.get("color", "#6c63ff")
    avatar   = ws.query_params.get("avatar", "")
    
    client_ip = ws.client.host if ws.client else "unknown"
    
    if ROOM_HAS_PW and token != ROOM_PW_HASH:
        await ws.close(code=4001); return
    
    ws_id = await manager.connect(ws, nickname, color, client_ip, avatar)
    
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "message":
                msg = {"type": "message", "id": ws_id, "nickname": nickname,
                       "color": color, "avatar": avatar, "ciphertext": data.get("ciphertext", ""),
                       "nonce": data.get("nonce", ""), "ts": _ts()}
                manager.add_history(msg); await manager.broadcast(msg)
    except WebSocketDisconnect:
        await manager.disconnect(ws_id)

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((BASE_DIR / "static" / "index.html").read_text(encoding="utf-8"))

if __name__ == "__main__":
    cert_file, key_file = generate_self_signed_cert()
    lip = get_local_ip()
    print("\n" + "═" * 54)
    print(f"  🔒 {ROOM_NAME}  局域网加密通信")
    print("═" * 54)
    print(f"  本机地址 → https://{lip}:{PORT}")
    print(f"  本地访问 → https://localhost:{PORT}")
    print(f"  房间密码 → {'已启用 (' + '*'*len(args.password) + ')' if ROOM_HAS_PW else '未设置'}")
    print("═" * 54 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, ssl_certfile=cert_file, ssl_keyfile=key_file, log_level="warning")
