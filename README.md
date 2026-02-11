# NostrFácil Bot — Directorio Nostr para Telegram

Bot de Telegram que detecta `npub1...` en un hilo específico del grupo, mantiene un directorio pineado actualizado, y sincroniza automáticamente los perfiles con [nostrfacil.com/directorio](https://nostrfacil.com/directorio) vía GitHub API.

Parte del proyecto [nostrfacil.com](https://nostrfacil.com) 💜

---

## Cómo funciona

```
Usuario pone npub1... en el hilo de Telegram
        ↓
  Bot lo detecta y lo guarda en SQLite
        ↓
  Pregunta: ¿quieres aparecer en la web pública?
        ↓
  Actualiza el mensaje pineado del grupo
        ↓
  Si aceptó → push del JSON al repo nostrweb (GitHub API)
        ↓
  nostrfacil.com/directorio muestra el perfil
```

## Características

- 🔍 Detección automática de `npub1...` en un hilo específico del foro de Telegram
- 📌 Mensaje pineado con el directorio completo (se actualiza solo)
- 🔗 Enlaces directos a perfiles vía [njump.me](https://njump.me)
- ✅ Sistema de consentimiento (opt-in) para aparecer en la web pública
- 📊 Encadenamiento automático de mensajes al superar el límite de 4096 caracteres
- 💾 SQLite como almacenamiento (ligero, robusto, sin servidor de BD)
- 🔄 Push automático del JSON a GitHub → web se actualiza sola
- 🚀 Desplegado en Fly.io (24/7, free tier)

## Comandos del bot

| Comando | Descripción | Quién |
|---------|-------------|-------|
| `/start` | Información del bot y ayuda | Todos |
| `/miperfil` | Ver tu perfil registrado | Todos |
| `/borrarme` | Eliminar tu perfil del directorio | Todos |
| `/websi` | Activar aparición en nostrfacil.com | Todos |
| `/webno` | Desactivar aparición en la web | Todos |
| `/stats` | Estadísticas del directorio | Todos |
| `/directorio` | Forzar actualización del pin | Solo admins |

## Estructura de archivos

```
nostr-telegram-bot/
│
├── bot.py                 # Bot principal (toda la lógica)
├── requirements.txt       # Dependencias Python
├── Dockerfile             # Imagen Docker para el despliegue
├── fly.toml               # Configuración de Fly.io
├── .env.example           # Variables de entorno de ejemplo
├── .gitignore             # Archivos excluidos de git
├── README.md              # Este archivo
│
└── data/                  # (generado en runtime, no en el repo)
    ├── nostr_directory.db # Base de datos SQLite
    └── directorio.json    # JSON exportado para la web
```

### Descripción de cada archivo

**`bot.py`** — Archivo principal. Contiene:
- Detección de npubs con regex (`npub1[a-z0-9]{58}`)
- Gestión de la base de datos SQLite (perfiles, mensajes pineados)
- Sistema de consentimiento con botones inline de Telegram
- Formateo y encadenamiento del mensaje pineado
- Push automático del JSON a GitHub vía API REST
- Todos los handlers de comandos (`/miperfil`, `/borrarme`, etc.)
- Filtro por `chat_id` (grupo) y `thread_id` (hilo específico del foro)

**`requirements.txt`** — Única dependencia: `python-telegram-bot==21.10`

**`Dockerfile`** — Imagen basada en `python:3.12-slim`. Instala dependencias y ejecuta `bot.py`.

**`fly.toml`** — Configuración de Fly.io:
- Sin servidor web (es un proceso en background, no escucha HTTP)
- `auto_stop = false` para que no se duerma nunca
- Volumen montado en `/data` para persistencia de la SQLite

**`.env.example`** — Plantilla de variables de entorno necesarias:
- `BOT_TOKEN` — Token del bot de Telegram
- `ALLOWED_CHAT_ID` — ID del grupo de Telegram
- `ALLOWED_THREAD_ID` — ID del hilo/tema del foro
- `GITHUB_TOKEN` — Token de GitHub para push del JSON
- `GITHUB_PUSH_ENABLED` — Activar/desactivar push a GitHub

**`.gitignore`** — Excluye: `__pycache__`, `.env`, `data/`, archivos `.db`

## Variables de entorno

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `BOT_TOKEN` | Token de @BotFather | `123456:ABC-DEF...` |
| `ALLOWED_CHAT_ID` | ID del grupo (número negativo) | `-1001234567890` |
| `ALLOWED_THREAD_ID` | ID del hilo del foro | `24` |
| `DB_PATH` | Ruta de la base de datos | `/data/nostr_directory.db` |
| `GITHUB_PUSH_ENABLED` | Activar push a GitHub | `true` |
| `GITHUB_TOKEN` | Personal Access Token de GitHub | `ghp_xxxx...` |
| `GITHUB_REPO` | Repo destino del JSON | `Crackloss/nostrweb` |
| `GITHUB_JSON_PATH` | Ruta del JSON en el repo | `data/directorio.json` |

## Despliegue en Fly.io

### 1. Preparar el bot en Telegram

1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot`
2. Guarda el token (⚠️ nunca lo publiques)
3. Desactiva Privacy Mode: BotFather → `/mybots` → Bot Settings → Group Privacy → Turn off
4. Añade el bot al grupo como **administrador**
5. Permisos necesarios: editar mensajes, pinear mensajes, enviar mensajes

### 2. Obtener Chat ID y Thread ID

```
https://api.telegram.org/bot<TU_TOKEN>/getUpdates
```

Envía un mensaje en el hilo donde quieres que funcione el bot. En la respuesta busca:
- `"chat":{"id":-100XXXXXXXXXX}` → tu `ALLOWED_CHAT_ID`
- `"message_thread_id":XX` → tu `ALLOWED_THREAD_ID`

### 3. Instalar Fly CLI y desplegar

```bash
# Instalar flyctl
curl -L https://fly.io/install.sh | sh

# Login
fly auth login

# Desde la carpeta del proyecto
fly launch

# Crear volumen para la base de datos
fly volumes create bot_data --region cdg --size 1

# Configurar secrets (NUNCA en el código)
fly secrets set BOT_TOKEN=tu_token
fly secrets set ALLOWED_CHAT_ID=-100XXXXXXXXXX
fly secrets set ALLOWED_THREAD_ID=24
fly secrets set GITHUB_TOKEN=ghp_tu_token
fly secrets set GITHUB_PUSH_ENABLED=true

# Desplegar
fly deploy
```

### 4. Verificar

```bash
fly logs
```

Deberías ver `Bot iniciado. Escuchando mensajes...`. Envía un npub en el hilo del grupo para probar.

### Comandos útiles de Fly.io

```bash
fly logs                    # Ver logs en tiempo real
fly status                  # Estado de la app
fly secrets list            # Ver secrets configurados
fly ssh console             # Acceder a la máquina
fly deploy                  # Redesplegar tras cambios
fly machines list           # Ver máquinas
```

## Desarrollo local

```bash
git clone https://github.com/Crackloss/nostr-telegram-bot.git
cd nostr-telegram-bot

pip install -r requirements.txt

cp .env.example .env
# Editar .env con tus valores reales

# Linux/Mac
export $(cat .env | xargs) && python bot.py

# Windows PowerShell
Get-Content .env | ForEach-Object { if ($_ -match '^\s*([^#][^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim()) } }; python bot.py
```

## Base de datos

SQLite con dos tablas principales:

**`profiles`** — Perfiles registrados
| Campo | Tipo | Descripción |
|-------|------|-------------|
| `npub` | TEXT PK | Clave pública Nostr |
| `telegram_user_id` | INTEGER | ID del usuario en Telegram |
| `telegram_username` | TEXT | @username de Telegram |
| `telegram_name` | TEXT | Nombre visible en Telegram |
| `added_at` | TEXT | Fecha de registro (ISO 8601) |
| `web_consent` | INTEGER | 0 = solo Telegram, 1 = también web |

**`pinned_messages`** — Control de mensajes pineados
| Campo | Tipo | Descripción |
|-------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `chat_id` | INTEGER | ID del grupo |
| `message_id` | INTEGER | ID del mensaje pineado |
| `profile_count` | INTEGER | Perfiles cuando se creó |
| `is_current` | INTEGER | 1 = activo, 0 = anterior |
| `created_at` | TEXT | Fecha de creación |

## Roadmap

- [x] Fase 1: Bot con directorio pineado + consentimiento
- [x] Fase 2: Push automático del JSON a GitHub
- [x] Fase 3: Página directorio en nostrfacil.com
- [ ] Página directorio: buscador, avatares desde Nostr
- [ ] Backup periódico de la SQLite
- [ ] Estadísticas avanzadas (perfiles por día, etc.)

## Licencia

Open source. Parte del proyecto [nostrfacil.com](https://nostrfacil.com).

---

**¿Dudas?** Encuéntrame en Nostr → [primal.net/voidhash](https://primal.net/p/nprofile1qqszj8995px29k0t0c06y5cx3wzwqvks0dejpxhnu90sqa708m9lxfs4gnym0)
