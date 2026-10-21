import os
import secrets
import json
from datetime import datetime, timezone

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.context import CryptContext
from sqlalchemy import create_engine, String, Integer, BigInteger, DateTime, ForeignKey, Text, Boolean, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session
from dotenv import load_dotenv

load_dotenv()

APP_NAME = "BotPanel"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./botpanel.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

SESSION_SECRET = os.getenv("SESSION_SECRET")
FERNET_KEY = os.getenv("FERNET_KEY")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"

if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(32)
if not FERNET_KEY:
    FERNET_KEY = Fernet.generate_key().decode()

try:
    cipher = Fernet(FERNET_KEY.encode())
except Exception as exc:
    raise RuntimeError("FERNET_KEY must be a valid Fernet key") from exc

serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="botpanel-session")
passwords = CryptContext(schemes=["bcrypt"], deprecated="auto")

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    bots: Mapped[list["Bot"]] = relationship(back_populates="owner", cascade="all, delete-orphan")

class Bot(Base):
    __tablename__ = "bots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(150), default="")
    token_encrypted: Mapped[str] = mapped_column(Text)
    webhook_secret: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    welcome_text: Mapped[str] = mapped_column(Text, default="Привет! Я бот.")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    owner: Mapped[User] = relationship(back_populates="bots")

class BotCommand(Base):
    __tablename__ = "bot_commands"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), index=True)
    command: Mapped[str] = mapped_column(String(50))
    reply_text: Mapped[str] = mapped_column(Text)
    buttons_json: Mapped[str] = mapped_column(Text, default="[]")

class UserState(Base):
    __tablename__ = "user_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    waiting_for: Mapped[str] = mapped_column(String(100), default="")

engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="BotPanel", version="0.1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def current_user(request: Request, db: Session):
    signed = request.cookies.get("bp_session")
    if not signed:
        return None
    try:
        payload = serializer.loads(signed, max_age=60 * 60 * 24 * 7)
        return db.get(User, int(payload["uid"]))
    except (BadSignature, SignatureExpired, ValueError, KeyError, TypeError):
        return None

def token_decrypt(value: str) -> str:
    try:
        return cipher.decrypt(value.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Token encryption configuration is invalid") from exc

async def telegram_call(token: str, method: str, payload: dict | None = None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=12.0) as client:
        response = await client.post(url, json=payload or {})
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise HTTPException(status_code=400, detail="Telegram API rejected the request.")
    return data["result"]

async def send_telegram_message(token: str, chat_id: int, text: str, reply_markup: dict = None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    await telegram_call(token, "sendMessage", payload)

def render(request: Request, name: str, **context):
    return templates.TemplateResponse(request=request, name=name, context=context)

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return render(request, "landing.html", user=None, demo_mode=DEMO_MODE)
        bots = db.query(Bot).filter(Bot.owner_id == user.id).order_by(Bot.id.desc()).all()
        return render(request, "dashboard.html", user=user, bots=bots, demo_mode=DEMO_MODE)

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return render(request, "auth.html", mode="register", error=None)

@app.post("/register")
def register(email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if len(email) > 255 or "@" not in email or len(password) < 10:
        return HTMLResponse("Use a valid email and a password of at least 10 characters.", status_code=400)
    with SessionLocal() as db:
        if db.query(User).filter(User.email == email).first():
            return HTMLResponse("An account with this email already exists.", status_code=409)
        user = User(email=email, password_hash=passwords.hash(password))
        db.add(user)
        db.commit()
        signed = serializer.dumps({"uid": user.id})
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("bp_session", signed, httponly=True, secure=bool(PUBLIC_BASE_URL.startswith("https://")), samesite="lax", max_age=604800)
    return response

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "auth.html", mode="login", error=None)

@app.post("/login")
def login(email: str = Form(...), password: str = Form(...)):
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email.strip().lower()).first()
        if not user or not passwords.verify(password, user.password_hash):
            return HTMLResponse("Incorrect email or password.", status_code=401)
        signed = serializer.dumps({"uid": user.id})
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("bp_session", signed, httponly=True, secure=bool(PUBLIC_BASE_URL.startswith("https://")), samesite="lax", max_age=604800)
    return response

@app.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("bp_session")
    return response

@app.get("/bots/new", response_class=HTMLResponse)
def new_bot_page(request: Request):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return RedirectResponse("/login", status_code=303)
        return render(request, "add_bot.html", user=user, error=None, demo_mode=DEMO_MODE)

@app.post("/bots/new")
async def add_bot(request: Request, token: str = Form(...), display_name: str = Form("")):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return RedirectResponse("/login", status_code=303)
        token = token.strip()
        if len(token) < 30 or len(token) > 200:
            return HTMLResponse("Token format looks invalid.", status_code=400)
        try:
            me = await telegram_call(token, "getMe")
        except Exception:
            return HTMLResponse("Telegram could not verify this token. Check it and try again.", status_code=400)
        
        existing = db.query(Bot).filter(Bot.telegram_id == int(me["id"])).first()
        if existing:
            return HTMLResponse("This bot is already connected to a BotPanel account.", status_code=409)
        
        webhook_secret = secrets.token_urlsafe(32)
        bot = Bot(
            owner_id=user.id,
            telegram_id=int(me["id"]),
            username=me.get("username", ""),
            display_name=(display_name.strip()[:150] or me.get("first_name", "Telegram bot")),
            token_encrypted=cipher.encrypt(token.encode()).decode(),
            webhook_secret=webhook_secret,
            welcome_text="Привет! Добро пожаловать.",
            active=False,
        )
        db.add(bot)
        db.commit()
        
        if PUBLIC_BASE_URL.startswith("https://"):
            try:
                await telegram_call(token, "setWebhook", {
                    "url": f"{PUBLIC_BASE_URL}/webhook/{webhook_secret}",
                    "secret_token": webhook_secret[:40],
                    "allowed_updates": ["message", "callback_query"],
                })
                bot.active = True
                db.commit()
            except Exception:
                bot.active = False
                db.commit()
        return RedirectResponse("/", status_code=303)

@app.get("/bots/{bot_id}", response_class=HTMLResponse)
def bot_detail(request: Request, bot_id: int):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return RedirectResponse("/login", status_code=303)
        bot = db.query(Bot).filter(Bot.id == bot_id, Bot.owner_id == user.id).first()
        if not bot:
            raise HTTPException(status_code=404, detail="Bot not found")
        commands = db.query(BotCommand).filter_by(bot_id=bot.id).all()
        return render(request, "bot_detail.html", user=user, bot=bot, commands=commands, demo_mode=DEMO_MODE)

# НОВЫЙ РОУТ ДЛЯ СХЕМЫ
@app.get("/bots/{bot_id}/flow", response_class=HTMLResponse)
def bot_flow(request: Request, bot_id: int):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return RedirectResponse("/login", status_code=303)
        bot = db.query(Bot).filter(Bot.id == bot_id, Bot.owner_id == user.id).first()
        if not bot:
            raise HTTPException(status_code=404, detail="Bot not found")
        commands = db.query(BotCommand).filter_by(bot_id=bot.id).all()
        return render(request, "flow.html", user=user, bot=bot, commands=commands)

@app.post("/bots/{bot_id}/settings")
def update_bot_settings(request: Request, bot_id: int, display_name: str = Form(...), welcome_text: str = Form(...)):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return RedirectResponse("/login", status_code=303)
        bot = db.query(Bot).filter(Bot.id == bot_id, Bot.owner_id == user.id).first()
        if not bot:
            raise HTTPException(status_code=404, detail="Bot not found")
        if len(display_name.strip()) > 150 or not welcome_text.strip() or len(welcome_text) > 3500:
            return HTMLResponse("Invalid text length.", status_code=400)
        bot.display_name = display_name.strip()
        bot.welcome_text = welcome_text.strip()
        db.commit()
        return RedirectResponse(f"/bots/{bot_id}", status_code=303)

@app.post("/bots/{bot_id}/commands/add")
def add_command(request: Request, bot_id: int, command: str = Form(...), reply_text: str = Form(...), btn_text: str = Form(""), btn_action: str = Form("")):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return RedirectResponse("/login", status_code=303)
        bot = db.query(Bot).filter(Bot.id == bot_id, Bot.owner_id == user.id).first()
        if not bot:
            raise HTTPException(404, "Bot not found")
        
        buttons = []
        if btn_text and btn_action:
            if btn_action.startswith("http"):
                buttons = [[{"text": btn_text, "url": btn_action}]]
            else:
                buttons = [[{"text": btn_text, "callback_data": f"input:{btn_action}"}]]
        
        cmd = BotCommand(
            bot_id=bot.id, 
            command=command.lower().strip(), 
            reply_text=reply_text, 
            buttons_json=json.dumps(buttons, ensure_ascii=False)
        )
        db.add(cmd)
        db.commit()
        return RedirectResponse(f"/bots/{bot_id}", status_code=303)

@app.post("/bots/{bot_id}/remove")
async def remove_bot(request: Request, bot_id: int):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return RedirectResponse("/login", status_code=303)
        bot = db.query(Bot).filter(Bot.id == bot_id, Bot.owner_id == user.id).first()
        if not bot:
            raise HTTPException(status_code=404, detail="Bot not found")
        token = token_decrypt(bot.token_encrypted)
        try:
            await telegram_call(token, "deleteWebhook", {"drop_pending_updates": False})
        except Exception:
            pass
        db.delete(bot)
        db.commit()
        return RedirectResponse("/", status_code=303)

@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    with SessionLocal() as db:
        bot = db.query(Bot).filter(Bot.webhook_secret == secret, Bot.active == True).first()
        if not bot:
            raise HTTPException(status_code=404, detail="Webhook not found")
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret[:40]:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")
        
        update = await request.json()
        token = token_decrypt(bot.token_encrypted)
        message = update.get("message") or {}
        callback = update.get("callback_query") or {}
        
        chat_id = (message.get("chat") or callback.get("message", {}).get("chat") or {}).get("id")
        if not chat_id:
            return {"ok": True}

        if callback:
            data = callback.get("data", "")
            await telegram_call(token, "answerCallbackQuery", {"callback_query_id": callback["id"]})
            if data.startswith("input:"):
                field_name = data.split(":")[1]
                state = db.query(UserState).filter_by(bot_id=bot.id, chat_id=chat_id).first()
                if not state:
                    state = UserState(bot_id=bot.id, chat_id=chat_id)
                    db.add(state)
                state.waiting_for = field_name
                db.commit()
                await send_telegram_message(token, chat_id, "Пожалуйста, введите текст:")
            return {"ok": True}

        text = (message.get("text") or "").strip()
        if not text:
            return {"ok": True}

        state = db.query(UserState).filter_by(bot_id=bot.id, chat_id=chat_id).first()
        if state and state.waiting_for:
            print(f"ДАННЫЕ от {chat_id}: {text}")
            state.waiting_for = "" 
            db.commit()
            await send_telegram_message(token, chat_id, "✅ Спасибо! Ваши данные сохранены.")
            return {"ok": True}

        cmd_text = text.split()[0].lower() 
        
        if cmd_text == "/start":
            await send_telegram_message(token, chat_id, bot.welcome_text)
        else:
            command_obj = db.query(BotCommand).filter_by(bot_id=bot.id, command=cmd_text).first()
            if command_obj:
                buttons = json.loads(command_obj.buttons_json)
                reply_markup = {"inline_keyboard": buttons} if buttons else None
                await send_telegram_message(token, chat_id, command_obj.reply_text, reply_markup)
            else:
                await send_telegram_message(token, chat_id, "Неизвестная команда. Попробуйте /start")
                
        return {"ok": True}

@app.get("/health")
def health():
    return {"ok": True, "app": APP_NAME, "mode": "demo" if DEMO_MODE else "configured"}

@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return PlainTextResponse("BotPanel prototype privacy note.", media_type="text/plain")
