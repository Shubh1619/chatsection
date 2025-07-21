from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, func, or_
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
import uvicorn
import bcrypt

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="supersecret")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------- Database Setup ---------------------
DATABASE_URL = 'postgresql://neondb_owner:npg_caB9Uq2oVHfT@ep-wandering-salad-a1li69y8-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require'
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@asynccontextmanager
async def lifespan_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --------------------- Models ---------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(80), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)

    def verify_password(self, password: str) -> bool:
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    sender = Column(String(80), nullable=False)
    recipient = Column(String(80), nullable=False)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, server_default=func.now())

Base.metadata.create_all(bind=engine)

# --------------------- Auth Helpers ---------------------
def create_user(db: Session, username: str, password: str):
    hashed_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(username=username, password_hash=hashed_pw)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def authenticate_user(db: Session, username: str, password: str):
    user = db.query(User).filter(User.username == username).first()
    if user and user.verify_password(password):
        return user
    return None

# --------------------- Chat Manager ---------------------
class ChatManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        self.active_connections[username] = websocket
        print(f"[CONNECTED] {username}")

    def disconnect(self, username: str):
        self.active_connections.pop(username, None)
        print(f"[DISCONNECTED] {username}")

    async def store_and_send(self, db: Session, sender: str, recipient: str, message: str):
        print(f"[MESSAGE] {sender} → {recipient}: {message}")
        db.add(Message(sender=sender, recipient=recipient, content=message))
        db.commit()

        if recipient in self.active_connections:
            await self.active_connections[recipient].send_json({"from": sender, "message": message})
        if sender in self.active_connections:
            await self.active_connections[sender].send_json({"from": "You", "message": message})

chat_manager = ChatManager()

# --------------------- Templates & Static ---------------------
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --------------------- Routes ---------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    username = request.session.get("username")
    if not username:
        return RedirectResponse("/login")
    users = db.query(User).filter(User.username != username).all()
    return templates.TemplateResponse("home.html", {
        "request": request,
        "users": [u.username for u in users],
        "session": request.session
    })

@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
def register(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == username).first():
        return templates.TemplateResponse("register.html", {"request": request, "error": "Username already exists."})
    create_user(db, username, password)
    return RedirectResponse(url="/login", status_code=302)

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = authenticate_user(db, username, password)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid username or password."})
    request.session["username"] = username
    return RedirectResponse(url="/", status_code=302)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)

@app.get("/chat/{username}", response_class=HTMLResponse)
def chat(username: str, request: Request, db: Session = Depends(get_db)):
    current_user = request.session.get("username")
    if not current_user or current_user == username:
        return RedirectResponse(url="/", status_code=302)
    messages = db.query(Message).filter(
        or_(
            (Message.sender == current_user) & (Message.recipient == username),
            (Message.sender == username) & (Message.recipient == current_user)
        )
    ).order_by(Message.timestamp).all()
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "other_user": username,
        "messages": messages,
        "current_user": current_user
    })

@app.websocket("/ws/chat/{recipient}")
async def websocket_endpoint(websocket: WebSocket, recipient: str):
    username = websocket.query_params.get("username")
    if not username:
        await websocket.close(code=1008)
        return

    await chat_manager.connect(websocket, username)

    async with lifespan_db() as db:
        try:
            while True:
                data = await websocket.receive_json()
                sender = data.get("from")
                message = data.get("message")
                await chat_manager.store_and_send(db, sender, recipient, message)
        except WebSocketDisconnect:
            chat_manager.disconnect(username)

# --------------------- Start App ---------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
