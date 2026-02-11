#!/usr/bin/env python3
"""
NostrFácil - Bot de Telegram para directorio de perfiles Nostr
Detecta npubs en mensajes, pide consentimiento para web pública,
y mantiene un mensaje pineado actualizado con la lista completa.
"""

import os
import re
import json
import logging
import sqlite3
import base64
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

# ─── Configuración ───────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ.get("ALLOWED_CHAT_ID", "0"))  # ID del grupo
ALLOWED_THREAD_ID = int(os.environ.get("ALLOWED_THREAD_ID", "0"))  # ID del hilo/tema del foro
GITHUB_PUSH_ENABLED = os.environ.get("GITHUB_PUSH_ENABLED", "false").lower() == "true"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Crackloss/nostrweb")
GITHUB_JSON_PATH = os.environ.get("GITHUB_JSON_PATH", "data/directorio.json")
DB_PATH = os.environ.get("DB_PATH", "data/nostr_directory.db")
MAX_MSG_LENGTH = 4000  # Margen bajo el límite de 4096 de Telegram
NJUMP_BASE = "https://njump.me/"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# Regex para detectar npub (Bech32, 63 chars después del prefijo)
NPUB_REGEX = re.compile(r"\b(npub1[a-z0-9]{58})\b", re.IGNORECASE)


# ─── Base de datos ───────────────────────────────────────────────
def init_db():
    """Crea las tablas si no existen."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            npub TEXT PRIMARY KEY,
            telegram_user_id INTEGER NOT NULL,
            telegram_username TEXT,
            telegram_name TEXT,
            added_at TEXT NOT NULL,
            web_consent INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pinned_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            profile_count INTEGER DEFAULT 0,
            is_current INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()
    log.info("Base de datos inicializada en %s", DB_PATH)


def get_db():
    """Devuelve una conexión a la base de datos."""
    return sqlite3.connect(DB_PATH)


# ─── Funciones de datos ─────────────────────────────────────────
def add_profile(npub: str, user_id: int, username: str | None, name: str) -> bool:
    """Añade un perfil. Devuelve True si es nuevo, False si ya existía."""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO profiles (npub, telegram_user_id, telegram_username, telegram_name, added_at)
               VALUES (?, ?, ?, ?, ?)""",
            (npub, user_id, username, name, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def remove_profile(user_id: int) -> bool:
    """Elimina el perfil de un usuario. Devuelve True si existía."""
    conn = get_db()
    cursor = conn.execute("DELETE FROM profiles WHERE telegram_user_id = ?", (user_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def set_web_consent(user_id: int, consent: bool) -> bool:
    """Establece el consentimiento para aparecer en la web."""
    conn = get_db()
    cursor = conn.execute(
        "UPDATE profiles SET web_consent = ? WHERE telegram_user_id = ?",
        (1 if consent else 0, user_id),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def get_all_profiles() -> list[dict]:
    """Devuelve todos los perfiles ordenados por fecha."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM profiles ORDER BY added_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_web_profiles() -> list[dict]:
    """Devuelve solo perfiles con consentimiento para la web."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM profiles WHERE web_consent = 1 ORDER BY added_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_profile_by_user(user_id: int) -> dict | None:
    """Devuelve el perfil de un usuario concreto."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM profiles WHERE telegram_user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_profile_count() -> int:
    """Devuelve el número total de perfiles."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    conn.close()
    return count


# ─── Gestión de mensajes pineados ────────────────────────────────
def save_pinned_message(chat_id: int, message_id: int, profile_count: int):
    """Guarda referencia a un mensaje pineado."""
    conn = get_db()
    # Marcar todos los anteriores como no actuales
    conn.execute(
        "UPDATE pinned_messages SET is_current = 0 WHERE chat_id = ?",
        (chat_id,),
    )
    conn.execute(
        """INSERT INTO pinned_messages (chat_id, message_id, profile_count, is_current, created_at)
           VALUES (?, ?, ?, 1, ?)""",
        (chat_id, message_id, profile_count, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_current_pinned(chat_id: int) -> dict | None:
    """Devuelve el mensaje pineado actual."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM pinned_messages WHERE chat_id = ? AND is_current = 1",
        (chat_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_pinned(chat_id: int) -> list[dict]:
    """Devuelve todos los mensajes pineados (para encadenamiento)."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM pinned_messages WHERE chat_id = ? ORDER BY id ASC",
        (chat_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Formateo del mensaje pineado ────────────────────────────────
def format_profile_line(idx: int, profile: dict) -> str:
    """Formatea una línea del directorio."""
    name = profile["telegram_username"]
    if name:
        display = f"@{name}"
    else:
        display = profile["telegram_name"] or "Anónimo"
    npub_short = profile["npub"][:16] + "..."
    return f"{idx}. {display} → <a href='{NJUMP_BASE}{profile['npub']}'>{npub_short}</a>"


def build_directory_messages(profiles: list[dict], chat_id: int) -> list[str]:
    """
    Construye los mensajes del directorio, divididos si superan el límite.
    Devuelve una lista de strings (uno por mensaje necesario).
    """
    if not profiles:
        return [
            "🟣 <b>Directorio Nostr del grupo</b>\n\n"
            "<i>Aún no hay perfiles registrados.</i>\n"
            "Envía tu <code>npub1...</code> para aparecer aquí.\n\n"
            f"📊 0 perfiles | Actualizado: {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC"
        ]

    header = "🟣 <b>Directorio Nostr del grupo</b>\n\n"
    footer_template = "\n\n📊 {count} perfiles | Actualizado: {date} UTC"
    date_str = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")

    messages = []
    current_lines = []
    current_length = len(header)
    start_idx = 1

    for i, profile in enumerate(profiles, 1):
        line = format_profile_line(i, profile)
        line_length = len(line) + 1  # +1 por el \n

        footer = footer_template.format(count=len(profiles), date=date_str)
        chain_note = "\n\n⬇️ <i>Continúa en el siguiente mensaje...</i>"

        # Verificar si cabe en el mensaje actual
        if current_length + line_length + len(footer) + len(chain_note) > MAX_MSG_LENGTH:
            # Cerrar mensaje actual con nota de continuación
            msg = header + "\n".join(current_lines) + chain_note
            messages.append(msg)
            # Reiniciar
            current_lines = []
            current_length = len(header)
            header = "🟣 <b>Directorio Nostr (continuación)</b>\n\n"

        current_lines.append(line)
        current_length += line_length

    # Último mensaje
    footer = footer_template.format(count=len(profiles), date=date_str)
    msg = header + "\n".join(current_lines) + footer
    messages.append(msg)

    return messages


async def update_pinned_directory(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Actualiza o crea el mensaje pineado del directorio."""
    profiles = get_all_profiles()
    messages_text = build_directory_messages(profiles, chat_id)

    current_pinned = get_current_pinned(chat_id)

    if len(messages_text) == 1 and current_pinned:
        # Caso simple: editar el mensaje existente
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=current_pinned["message_id"],
                text=messages_text[0],
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            log.info("Mensaje pineado actualizado (ID: %s)", current_pinned["message_id"])
            return
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            log.warning("No se pudo editar mensaje pineado: %s. Creando nuevo.", e)

    # Si hay múltiples mensajes o no hay pineado, crear nuevos
    # Primero, si el actual está lleno, congelarlo con enlace
    if current_pinned and len(messages_text) > 1:
        try:
            # Añadir enlace al primer mensaje del nuevo bloque
            old_msg = await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=current_pinned["message_id"],
                text=messages_text[0],  # El primer bloque (ya tiene nota de continuación)
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except BadRequest:
            pass

    # Enviar mensajes nuevos (o el único si no había pineado)
    last_msg = None
    thread_kwargs = {"message_thread_id": ALLOWED_THREAD_ID} if ALLOWED_THREAD_ID else {}
    for i, text in enumerate(messages_text):
        if i == 0 and current_pinned:
            continue  # Ya editamos el primero
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            **thread_kwargs,
        )
        last_msg = sent

    # Pinear el último mensaje (el más reciente, siempre visible)
    if last_msg:
        try:
            await context.bot.pin_chat_message(
                chat_id=chat_id,
                message_id=last_msg.message_id,
                disable_notification=True,
            )
            save_pinned_message(chat_id, last_msg.message_id, len(profiles))
            log.info("Nuevo mensaje pineado (ID: %s) con %d perfiles", last_msg.message_id, len(profiles))
        except BadRequest as e:
            log.error("No se pudo pinear: %s", e)
    elif not current_pinned:
        # Primera vez: enviar y pinear
        thread_kwargs = {"message_thread_id": ALLOWED_THREAD_ID} if ALLOWED_THREAD_ID else {}
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=messages_text[0],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            **thread_kwargs,
        )
        try:
            await context.bot.pin_chat_message(
                chat_id=chat_id,
                message_id=sent.message_id,
                disable_notification=True,
            )
            save_pinned_message(chat_id, sent.message_id, len(profiles))
            log.info("Primer mensaje pineado creado (ID: %s)", sent.message_id)
        except BadRequest as e:
            log.error("No se pudo pinear: %s", e)


# ─── Exportación JSON para la web ────────────────────────────────
def export_web_json() -> str:
    """Genera el JSON para nostrfacil.com/directorio."""
    profiles = get_web_profiles()
    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_profiles": len(profiles),
        "profiles": [
            {
                "npub": p["npub"],
                "display_name": p["telegram_username"] or p["telegram_name"] or "Anónimo",
                "njump_url": f"{NJUMP_BASE}{p['npub']}",
                "added_at": p["added_at"],
            }
            for p in profiles
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def save_web_json():
    """Guarda el JSON en disco y lo sube a GitHub si está habilitado."""
    json_content = export_web_json()

    # Guardar localmente
    path = Path("data/directorio.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_content, encoding="utf-8")
    log.info("JSON web exportado a %s", path)

    # Push a GitHub
    if GITHUB_PUSH_ENABLED and GITHUB_TOKEN:
        try:
            push_to_github(json_content)
        except Exception as e:
            log.error("Error al subir JSON a GitHub: %s", e)


def push_to_github(content: str):
    """Sube el JSON al repo de GitHub vía API."""
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_JSON_PATH}"

    # Primero, obtener el SHA del archivo actual (si existe) para poder actualizarlo
    sha = None
    req = urllib.request.Request(api_url, method="GET")
    req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            sha = data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        # 404 = archivo no existe todavía, lo crearemos

    # Preparar el contenido en base64
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")

    payload = {
        "message": f"Actualizar directorio Nostr ({datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC)",
        "content": content_b64,
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(api_url, data=body, method="PUT")
    req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req) as resp:
        if resp.status in (200, 201):
            log.info("JSON subido a GitHub exitosamente")
        else:
            log.warning("GitHub respondió con status %s", resp.status)


# ─── Handlers de Telegram ────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detecta npubs en mensajes del grupo."""
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        return

    # Filtrar por hilo específico del foro
    thread_id = getattr(update.message, "message_thread_id", None)
    if ALLOWED_THREAD_ID and thread_id != ALLOWED_THREAD_ID:
        return

    text = update.message.text
    user = update.message.from_user
    matches = NPUB_REGEX.findall(text)

    if not matches:
        return

    npub = matches[0].lower()  # Solo el primero por mensaje
    user_name = user.first_name or ""
    if user.last_name:
        user_name += f" {user.last_name}"

    # Verificar si ya existe
    existing = get_profile_by_user(user.id)
    if existing:
        if existing["npub"] == npub:
            await update.message.reply_text(
                f"✅ Ya estás en el directorio con ese npub.",
                reply_to_message_id=update.message.message_id,
            )
            return
        else:
            # Actualizar npub
            conn = get_db()
            conn.execute(
                "UPDATE profiles SET npub = ? WHERE telegram_user_id = ?",
                (npub, user.id),
            )
            conn.commit()
            conn.close()
            await update.message.reply_text(
                f"🔄 Tu npub ha sido actualizado en el directorio.",
                reply_to_message_id=update.message.message_id,
            )
            await update_pinned_directory(context, chat_id)
            save_web_json()
            return

    # Nuevo perfil
    added = add_profile(npub, user.id, user.username, user_name)
    if not added:
        await update.message.reply_text(
            "⚠️ Ese npub ya está registrado por otro usuario.",
            reply_to_message_id=update.message.message_id,
        )
        return

    # Pedir consentimiento para la web
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Sí, aparecer en la web", callback_data=f"consent_yes_{user.id}"),
            InlineKeyboardButton("❌ Solo Telegram", callback_data=f"consent_no_{user.id}"),
        ]
    ])

    npub_short = npub[:20] + "..."
    await update.message.reply_text(
        f"🟣 <b>¡Perfil añadido al directorio!</b>\n\n"
        f"<code>{npub_short}</code>\n"
        f"🔗 <a href='{NJUMP_BASE}{npub}'>Ver perfil en Nostr</a>\n\n"
        f"¿Quieres aparecer también en el directorio público de "
        f"<a href='https://nostrfacil.com/directorio'>nostrfacil.com</a>?\n\n"
        f"<i>Puedes cambiarlo después con /websi o /webno</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        reply_to_message_id=update.message.message_id,
        disable_web_page_preview=True,
    )

    await update_pinned_directory(context, chat_id)
    save_web_json()
    log.info("Nuevo perfil: %s (%s)", user.username or user_name, npub_short)


async def handle_consent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la respuesta de consentimiento para la web."""
    query = update.callback_query
    data = query.data

    if not data.startswith("consent_"):
        return

    parts = data.split("_")
    action = parts[1]  # "yes" o "no"
    target_user_id = int(parts[2])

    # Solo el propio usuario puede responder
    if query.from_user.id != target_user_id:
        await query.answer("⚠️ Solo el dueño del perfil puede responder.", show_alert=True)
        return

    consent = action == "yes"
    set_web_consent(target_user_id, consent)

    if consent:
        await query.edit_message_text(
            "✅ <b>Perfil añadido al directorio del grupo y de nostrfacil.com</b>\n\n"
            "Tu perfil aparecerá en <a href='https://nostrfacil.com/directorio'>nostrfacil.com/directorio</a>.\n"
            "Puedes quitarte con /webno o eliminarte del todo con /borrarme",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    else:
        await query.edit_message_text(
            "✅ <b>Perfil añadido solo al directorio del grupo</b>\n\n"
            "No aparecerás en la web pública. Si cambias de opinión: /websi",
            parse_mode=ParseMode.HTML,
        )

    save_web_json()
    await query.answer()


# ─── Comandos ─────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start."""
    await update.message.reply_text(
        "🟣 <b>NostrFácil Bot</b>\n\n"
        "Soy el bot del directorio Nostr del grupo.\n\n"
        "<b>Cómo funciona:</b>\n"
        "Envía tu <code>npub1...</code> en el grupo y te añadiré al directorio.\n\n"
        "<b>Comandos:</b>\n"
        "/miperfil — Ver tu perfil registrado\n"
        "/borrarme — Eliminar tu perfil del directorio\n"
        "/websi — Aparecer en nostrfacil.com/directorio\n"
        "/webno — No aparecer en la web pública\n"
        "/stats — Estadísticas del directorio\n\n"
        "Más info: <a href='https://nostrfacil.com'>nostrfacil.com</a>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cmd_miperfil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el perfil del usuario."""
    profile = get_profile_by_user(update.message.from_user.id)
    if not profile:
        await update.message.reply_text(
            "No tienes un perfil registrado. Envía tu <code>npub1...</code> en el grupo.",
            parse_mode=ParseMode.HTML,
        )
        return

    web_status = "✅ Sí" if profile["web_consent"] else "❌ No"
    await update.message.reply_text(
        f"🟣 <b>Tu perfil</b>\n\n"
        f"<b>npub:</b> <code>{profile['npub']}</code>\n"
        f"🔗 <a href='{NJUMP_BASE}{profile['npub']}'>Ver en Nostr</a>\n"
        f"📅 Registrado: {profile['added_at'][:10]}\n"
        f"🌐 En la web: {web_status}\n\n"
        f"Comandos: /borrarme | /websi | /webno",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cmd_borrarme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Elimina el perfil del usuario."""
    deleted = remove_profile(update.message.from_user.id)
    if deleted:
        await update.message.reply_text("✅ Tu perfil ha sido eliminado del directorio.")
        chat_id = update.message.chat_id
        if ALLOWED_CHAT_ID and chat_id == ALLOWED_CHAT_ID:
            await update_pinned_directory(context, chat_id)
        save_web_json()
    else:
        await update.message.reply_text("No tenías un perfil registrado.")


async def cmd_websi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activa la aparición en la web pública."""
    updated = set_web_consent(update.message.from_user.id, True)
    if updated:
        await update.message.reply_text(
            "✅ Ahora apareces en <a href='https://nostrfacil.com/directorio'>nostrfacil.com/directorio</a>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        save_web_json()
    else:
        await update.message.reply_text(
            "No tienes un perfil registrado. Envía tu <code>npub1...</code> primero.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_webno(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Desactiva la aparición en la web pública."""
    updated = set_web_consent(update.message.from_user.id, False)
    if updated:
        await update.message.reply_text("✅ Ya no apareces en la web pública. Sigues en el directorio del grupo.")
        save_web_json()
    else:
        await update.message.reply_text(
            "No tienes un perfil registrado. Envía tu <code>npub1...</code> primero.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra estadísticas del directorio."""
    total = get_profile_count()
    web_count = len(get_web_profiles())
    await update.message.reply_text(
        f"📊 <b>Directorio Nostr</b>\n\n"
        f"👥 Perfiles totales: <b>{total}</b>\n"
        f"🌐 Visibles en web: <b>{web_count}</b>\n"
        f"🔒 Solo Telegram: <b>{total - web_count}</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_directorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fuerza la actualización del mensaje pineado (solo admins)."""
    chat_id = update.message.chat_id
    user = update.message.from_user

    # Verificar que es admin
    try:
        member = await context.bot.get_chat_member(chat_id, user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("⚠️ Solo los administradores pueden usar este comando.")
            return
    except BadRequest:
        pass

    await update_pinned_directory(context, chat_id)
    await update.message.reply_text("✅ Directorio actualizado y pineado.")


# ─── Main ─────────────────────────────────────────────────────────
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("miperfil", cmd_miperfil))
    app.add_handler(CommandHandler("borrarme", cmd_borrarme))
    app.add_handler(CommandHandler("websi", cmd_websi))
    app.add_handler(CommandHandler("webno", cmd_webno))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("directorio", cmd_directorio))

    # Callbacks de botones inline
    app.add_handler(CallbackQueryHandler(handle_consent_callback, pattern=r"^consent_"))

    # Detector de npubs en mensajes
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot iniciado. Escuchando mensajes...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
