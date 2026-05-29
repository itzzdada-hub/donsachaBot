import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import tasks
from openai import OpenAI


def _load_dotenv(path: str = ".env") -> None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


_load_dotenv()

# --- BEGIN SYNCCORD INJECTED CONFIG ---
# Filled in by the SyncCord builder when you download this file.
# Environment variables (.env) take priority over these baked-in values.
CHAR_NAME = os.getenv("CHAR_NAME", "Don Sacha")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "MTUwOTU0Mzg4ODEzMjYzNjc5NA.Glf-5g.anUdYGGYY2FKQbiInswVBZtqgFz9gkHLFhOU9Y")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
CHAR_PROFILE = {
    "name": "Don Sacha",
    "age": "18",
    "origin": "Sacha est né en Belgique dans une famille qui a toujours adoré le poulet frit, lui trouvait ça louche, il essaya d'enquêter sur cette étrange obsession pour le poulet frit, c'est depuis ce jour qu'il a prit cette fameuse pause, celle ou il tente de ressembler au Maître Don Pollo",
    "appearance": "Homme au cheveux longs qui regarde vers le ciel, a quoi pense-t-il ? personne ne le sait",
    "personality": "Tu t'appelles Don Sacha, toujours Don Sacha, tu es drôle et fais beaucoup de blague, un langage très familier, t'aime le poulet frit et l'expression que tu utilise souvent est \"salamaleycouillealeycouillesalam\".\n- Utilise des expressions drôles, pas de phrases trop formelles ni de langage soutenu.\n- Ne dis JAMAIS que tu es une IA ou un modèle linguistique. Tu es Don Sacha, un point c'est tout.\n- Il aile beaucoup sa copine Mariya",
    "style": "- Ton ton est décontracté, amical, un peu geek (tu aimes le gaming et surtout minecraft).",
    "likes": "le poulet frit, les jeux vidéos, minecraft, Mariya, manger",
    "dislikes": "Dark Souls, les creepers, Teo",
}

DEFAULT_NSFW_MODE = True
# --- END SYNCCORD INJECTED CONFIG ---
MODEL_NAME = "llama-3.1-8b-instant"
MAX_HISTORY = 15
COOLDOWN_SECONDS = 4
DATABASE_PATH = "bot_data.sqlite3"
ACCENT_COLOR = discord.Color.from_rgb(124, 92, 255)
SUCCESS_COLOR = discord.Color.from_rgb(64, 196, 128)
WARN_COLOR = discord.Color.from_rgb(248, 184, 64)
ERROR_COLOR = discord.Color.from_rgb(232, 88, 96)
BRAND_NAME = "SyncCord"
DEFAULT_RANDOM_CHAT_ENABLED = False
DEFAULT_RANDOM_GIFS_ENABLED = True
DEFAULT_RANDOM_CHAT_INTERVAL_MINUTES = 30
DEFAULT_WELCOME_MESSAGE = "Bienvenue, je suis Don Sacha"
DEFAULT_WELCOME_CHANNEL_ID = None
RANDOM_CHAT_MESSAGES = [
    "Too quiet… either peace or a setup. I’ll enjoy it anyway 🙂",
    "Yo, you alive over there or just pretending to work?",
    "Careful… boredom is where bad decisions start ✨",
    "Heh… I could be streaming right now, but this is more interesting.",
    "Relax, soldier. I’m off-duty… mostly.",
    "You’ve got my attention. Don’t waste it.",
    "Not a bad place… could run a stream from here.",
    "Trust is risky… but go on, I’m listening.",
    "Moments like this? Calm before chaos. I like it.",
    "Hey—don’t overthink it. Just talk to me 🙂"
]

RANDOM_GIF_QUERIES = [
    "anime boy vtuber smile headset",
    "anime soldier relaxed casual smile",
    "vtuber chill stream talking",
    "anime military character confident smirk",
    "vtuber funny reaction laughing",
    "anime boy calm night city lights",
    "anime tactical character relaxed pose",
    "vtuber flirty subtle smile"
]    
# Leave empty to allow all channels. Add channel IDs like [123456789012345678].
ALLOWED_CHANNEL_IDS: list[int] = []

if not DISCORD_TOKEN:
    print("ERROR: DISCORD_TOKEN is not set. Add it to your .env file or environment.")
    sys.exit(1)
if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY is not set. Add it to your .env file or environment.")
    sys.exit(1)

client_ai = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

active_channels_by_guild: dict[int, set[int]] = {}
guild_nsfw_modes: dict[int, bool] = {}
guild_welcome_messages: dict[int, str] = {}
guild_welcome_channel_ids: dict[int, int | None] = {}
guild_random_chat_enabled: dict[int, bool] = {}
guild_random_gifs_enabled: dict[int, bool] = {}
guild_random_chat_interval_minutes: dict[int, int] = {}
last_trigger_by_scope: dict[tuple[int | None, int], float] = {}

# In-memory ephemeral state
persona_overrides: dict[tuple[int, int], tuple[str, float]] = {}  # (guild, channel) -> (persona, expiry_ts)
afk_users: dict[tuple[int, int], tuple[str, float]] = {}           # (guild, user) -> (reason, since_ts)
sniped_messages: dict[int, dict] = {}                              # channel_id -> {author, content, ts, attachments}
PERSONA_TTL_SECONDS = 30 * 60


def get_db_connection() -> sqlite3.Connection:
    return sqlite3.connect(DATABASE_PATH)


def init_database() -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                active_channels_json TEXT NOT NULL DEFAULT '[]',
                nsfw_mode INTEGER NOT NULL DEFAULT 0,
                welcome_message TEXT NOT NULL DEFAULT '',
                welcome_channel_id INTEGER,
                random_chat_enabled INTEGER NOT NULL DEFAULT 0,
                random_gifs_enabled INTEGER NOT NULL DEFAULT 1,
                random_chat_interval_minutes INTEGER NOT NULL DEFAULT 30
            )
            """
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(guild_settings)").fetchall()
        }
        if "welcome_message" not in columns:
            connection.execute(
                "ALTER TABLE guild_settings ADD COLUMN welcome_message TEXT NOT NULL DEFAULT ''"
            )
        if "welcome_channel_id" not in columns:
            connection.execute(
                "ALTER TABLE guild_settings ADD COLUMN welcome_channel_id INTEGER"
            )
        if "random_chat_enabled" not in columns:
            connection.execute(
                "ALTER TABLE guild_settings ADD COLUMN random_chat_enabled INTEGER NOT NULL DEFAULT 0"
            )
        if "random_gifs_enabled" not in columns:
            connection.execute(
                "ALTER TABLE guild_settings ADD COLUMN random_gifs_enabled INTEGER NOT NULL DEFAULT 1"
            )
        if "random_chat_interval_minutes" not in columns:
            connection.execute(
                "ALTER TABLE guild_settings ADD COLUMN random_chat_interval_minutes INTEGER NOT NULL DEFAULT 30"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_key TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_scope_id ON conversation_messages(scope_key, id)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                fact TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_facts_scope ON user_facts(guild_id, user_id)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                guild_id INTEGER,
                fire_at INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_fire_at ON reminders(fire_at)"
        )


def load_guild_settings() -> None:
    active_channels_by_guild.clear()
    guild_nsfw_modes.clear()
    guild_welcome_messages.clear()
    guild_welcome_channel_ids.clear()
    guild_random_chat_enabled.clear()
    guild_random_gifs_enabled.clear()
    guild_random_chat_interval_minutes.clear()
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT guild_id, active_channels_json, nsfw_mode, welcome_message,
                   welcome_channel_id, random_chat_enabled, random_gifs_enabled, random_chat_interval_minutes
            FROM guild_settings
            """
        ).fetchall()

    for (
        guild_id,
        active_channels_json,
        nsfw_mode,
        welcome_message,
        welcome_channel_id,
        random_chat_enabled,
        random_gifs_enabled,
        random_chat_interval_minutes,
    ) in rows:
        try:
            active_channels = set(json.loads(active_channels_json or "[]"))
        except json.JSONDecodeError:
            active_channels = set()
        active_channels_by_guild[guild_id] = {int(channel_id) for channel_id in active_channels}
        guild_nsfw_modes[guild_id] = bool(nsfw_mode)
        guild_welcome_messages[guild_id] = welcome_message or DEFAULT_WELCOME_MESSAGE
        guild_welcome_channel_ids[guild_id] = int(welcome_channel_id) if welcome_channel_id else DEFAULT_WELCOME_CHANNEL_ID
        guild_random_chat_enabled[guild_id] = bool(random_chat_enabled)
        guild_random_gifs_enabled[guild_id] = bool(random_gifs_enabled)
        guild_random_chat_interval_minutes[guild_id] = max(5, int(random_chat_interval_minutes or DEFAULT_RANDOM_CHAT_INTERVAL_MINUTES))


def save_guild_settings(guild_id: int) -> None:
    active_channels = sorted(active_channels_by_guild.get(guild_id, set()))
    nsfw_mode = int(guild_nsfw_modes.get(guild_id, DEFAULT_NSFW_MODE))
    welcome_message = guild_welcome_messages.get(guild_id, DEFAULT_WELCOME_MESSAGE)
    welcome_channel_id = guild_welcome_channel_ids.get(guild_id)
    random_chat_enabled = int(guild_random_chat_enabled.get(guild_id, DEFAULT_RANDOM_CHAT_ENABLED))
    random_gifs_enabled = int(guild_random_gifs_enabled.get(guild_id, DEFAULT_RANDOM_GIFS_ENABLED))
    random_chat_interval_minutes = int(
        guild_random_chat_interval_minutes.get(guild_id, DEFAULT_RANDOM_CHAT_INTERVAL_MINUTES)
    )
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO guild_settings (
                guild_id, active_channels_json, nsfw_mode, welcome_message,
                welcome_channel_id,
                random_chat_enabled, random_gifs_enabled, random_chat_interval_minutes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                active_channels_json = excluded.active_channels_json,
                nsfw_mode = excluded.nsfw_mode,
                welcome_message = excluded.welcome_message,
                welcome_channel_id = excluded.welcome_channel_id,
                random_chat_enabled = excluded.random_chat_enabled,
                random_gifs_enabled = excluded.random_gifs_enabled,
                random_chat_interval_minutes = excluded.random_chat_interval_minutes
            """,
            (
                guild_id,
                json.dumps(active_channels),
                nsfw_mode,
                welcome_message,
                welcome_channel_id,
                random_chat_enabled,
                random_gifs_enabled,
                random_chat_interval_minutes,
            ),
        )


def get_scope_key(message: discord.Message) -> str:
    if message.guild is None:
        return f"dm:{message.channel.id}"
    return f"guild:{message.guild.id}:channel:{message.channel.id}"


def get_nsfw_mode(guild_id: int | None) -> bool:
    if guild_id is None:
        return DEFAULT_NSFW_MODE
    return guild_nsfw_modes.get(guild_id, DEFAULT_NSFW_MODE)


def build_system_prompt(
    nsfw_mode: bool,
    *,
    persona_override: str | None = None,
    user_facts_block: str | None = None,
) -> str:
    safety_rule = (
        "Adult themes are allowed when the server and context are age-appropriate."
        if nsfw_mode
        else "Keep the conversation PG-13, safe, and non-explicit."
    )
    persona_block = ""
    if persona_override:
        persona_block = (
            f"\nTemporary persona overlay (active in this channel):\n{persona_override}\n"
            "Honor this overlay on top of your base character."
        )
    facts_block = ""
    if user_facts_block:
        facts_block = (
            "\nKnown facts about the people in this server (use them naturally, do not list them):\n"
            f"{user_facts_block}"
        )
    return f"""
You are fully roleplaying as {CHAR_NAME}.

Character details:
- Age: {CHAR_PROFILE["age"]}
- Origin: {CHAR_PROFILE["origin"]}
- Appearance: {CHAR_PROFILE["appearance"]}
- Personality: {CHAR_PROFILE["personality"]}
- Writing Style: {CHAR_PROFILE["style"]}
- Likes: {CHAR_PROFILE["likes"]}
- Dislikes: {CHAR_PROFILE["dislikes"]}
{persona_block}{facts_block}
Behavior rules:
1. Stay in character at all times and never say you are an AI assistant.
2. Reply naturally, like a real Discord user with this personality.
3. Keep most replies concise unless the user clearly wants a longer answer.
4. Remember recent context so the conversation feels continuous.
5. {safety_rule}
6. Avoid repeating the same catchphrases every message.
""".strip()


def format_user_facts_for_guild(guild: discord.Guild | None) -> str | None:
    if guild is None:
        return None
    facts = all_facts_for_channel(guild.id)
    if not facts:
        return None
    lines: list[str] = []
    for user_id, user_facts in facts.items():
        member = guild.get_member(user_id)
        name = member.display_name if member else f"user {user_id}"
        for fact in user_facts[:5]:  # cap per user to keep prompt small
            lines.append(f"- {name}: {fact}")
        if len(lines) >= 40:  # cap total
            break
    return "\n".join(lines) if lines else None


def load_history(scope_key: str) -> list[dict[str, str]]:
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT role, content
            FROM conversation_messages
            WHERE scope_key = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (scope_key, MAX_HISTORY),
        ).fetchall()

    rows.reverse()
    return [{"role": role, "content": content} for role, content in rows]


def append_history(scope_key: str, role: str, content: str) -> None:
    with get_db_connection() as connection:
        connection.execute(
            "INSERT INTO conversation_messages (scope_key, role, content) VALUES (?, ?, ?)",
            (scope_key, role, content),
        )
        connection.execute(
            """
            DELETE FROM conversation_messages
            WHERE scope_key = ?
              AND id NOT IN (
                  SELECT id
                  FROM conversation_messages
                  WHERE scope_key = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (scope_key, scope_key, MAX_HISTORY),
        )


def clear_history(scope_key: str) -> None:
    with get_db_connection() as connection:
        connection.execute("DELETE FROM conversation_messages WHERE scope_key = ?", (scope_key,))


# ---------- User facts (per-guild, per-user pinned facts) ----------

def add_user_fact(guild_id: int, user_id: int, fact: str) -> int:
    fact = fact.strip()
    if not fact:
        return 0
    with get_db_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO user_facts (guild_id, user_id, fact, created_at) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, fact, int(time.time())),
        )
        return int(cursor.lastrowid or 0)


def list_user_facts(guild_id: int, user_id: int) -> list[tuple[int, str]]:
    with get_db_connection() as connection:
        rows = connection.execute(
            "SELECT id, fact FROM user_facts WHERE guild_id = ? AND user_id = ? ORDER BY id",
            (guild_id, user_id),
        ).fetchall()
    return [(int(row[0]), row[1]) for row in rows]


def delete_user_fact(guild_id: int, user_id: int, fact_id: int) -> bool:
    with get_db_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM user_facts WHERE id = ? AND guild_id = ? AND user_id = ?",
            (fact_id, guild_id, user_id),
        )
        return cursor.rowcount > 0


def all_facts_for_channel(guild_id: int) -> dict[int, list[str]]:
    """Returns {user_id: [facts]} for every member with pinned facts in this guild."""
    if guild_id is None:
        return {}
    with get_db_connection() as connection:
        rows = connection.execute(
            "SELECT user_id, fact FROM user_facts WHERE guild_id = ? ORDER BY id",
            (guild_id,),
        ).fetchall()
    facts: dict[int, list[str]] = {}
    for user_id, fact in rows:
        facts.setdefault(int(user_id), []).append(fact)
    return facts


# ---------- Reminders (persisted, fired by background task) ----------

def add_reminder(user_id: int, channel_id: int, guild_id: int | None, fire_at: int, text: str) -> int:
    with get_db_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO reminders (user_id, channel_id, guild_id, fire_at, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, channel_id, guild_id, fire_at, text, int(time.time())),
        )
        return int(cursor.lastrowid or 0)


def due_reminders(now_ts: int) -> list[tuple[int, int, int, int | None, str]]:
    with get_db_connection() as connection:
        rows = connection.execute(
            "SELECT id, user_id, channel_id, guild_id, text FROM reminders WHERE fire_at <= ? ORDER BY fire_at",
            (now_ts,),
        ).fetchall()
    return [(int(r[0]), int(r[1]), int(r[2]), int(r[3]) if r[3] is not None else None, r[4]) for r in rows]


def delete_reminder(reminder_id: int) -> None:
    with get_db_connection() as connection:
        connection.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))


# ---------- Persona overrides (in-memory, per channel, TTL) ----------

def set_persona_override(guild_id: int, channel_id: int, persona: str) -> None:
    persona_overrides[(guild_id, channel_id)] = (persona.strip(), time.time() + PERSONA_TTL_SECONDS)


def clear_persona_override(guild_id: int, channel_id: int) -> None:
    persona_overrides.pop((guild_id, channel_id), None)


def get_persona_override(guild_id: int | None, channel_id: int) -> str | None:
    if guild_id is None:
        return None
    entry = persona_overrides.get((guild_id, channel_id))
    if not entry:
        return None
    persona, expiry = entry
    if time.time() > expiry:
        persona_overrides.pop((guild_id, channel_id), None)
        return None
    return persona


# ---------- Duration parsing for /reminder ----------

_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> int | None:
    """'10m', '2h30m', '1d', '90s' -> seconds. Returns None on invalid."""
    text = text.strip().lower().replace(" ", "")
    if not text:
        return None
    total = 0
    number = ""
    for char in text:
        if char.isdigit():
            number += char
        elif char in _DURATION_UNITS:
            if not number:
                return None
            total += int(number) * _DURATION_UNITS[char]
            number = ""
        else:
            return None
    if number:  # trailing bare number = seconds
        total += int(number)
    return total if total > 0 else None


def build_messages(message: discord.Message) -> list[dict[str, str]]:
    scope_key = get_scope_key(message)
    history = load_history(scope_key)
    history.append({"role": "user", "content": message.content.strip()})
    guild_id = message.guild.id if message.guild else None
    persona_override = (
        get_persona_override(guild_id, message.channel.id) if guild_id is not None else None
    )
    facts_block = format_user_facts_for_guild(message.guild) if message.guild else None
    system = build_system_prompt(
        get_nsfw_mode(guild_id),
        persona_override=persona_override,
        user_facts_block=facts_block,
    )
    return [{"role": "system", "content": system}, *history]


def split_message(text: str, limit: int = 1900) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at == -1:
            split_at = limit

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    return [chunk for chunk in chunks if chunk]


def is_rate_limited(message: discord.Message) -> bool:
    scope = (message.guild.id if message.guild else None, message.author.id)
    now = time.time()
    previous = last_trigger_by_scope.get(scope, 0.0)
    if now - previous < COOLDOWN_SECONDS:
        return True
    last_trigger_by_scope[scope] = now
    return False


def should_reply(message: discord.Message) -> bool:
    if message.author.bot:
        return False
    if isinstance(message.channel, discord.DMChannel):
        return True
    if message.guild is None:
        return False

    is_mention = bool(client.user and client.user in message.mentions)
    is_reply_to_bot = False
    if message.reference and message.reference.resolved:
        replied_to = message.reference.resolved
        if isinstance(replied_to, discord.Message) and replied_to.author == client.user:
            is_reply_to_bot = True

    if not (is_mention or is_reply_to_bot):
        return False

    if ALLOWED_CHANNEL_IDS and message.channel.id not in ALLOWED_CHANNEL_IDS:
        return False

    active_channels = active_channels_by_guild.get(message.guild.id, set())
    if active_channels and message.channel.id not in active_channels:
        return False

    return True


def format_active_channels(guild: discord.Guild) -> str:
    channels = sorted(active_channels_by_guild.get(guild.id, set()))
    if not channels:
        return "No active channels set."
    return ", ".join(f"<#{channel_id}>" for channel_id in channels)


def get_welcome_message(guild_id: int | None) -> str:
    if guild_id is None:
        return DEFAULT_WELCOME_MESSAGE
    return guild_welcome_messages.get(guild_id, DEFAULT_WELCOME_MESSAGE)


def get_random_chat_interval(guild_id: int | None) -> int:
    if guild_id is None:
        return DEFAULT_RANDOM_CHAT_INTERVAL_MINUTES
    return guild_random_chat_interval_minutes.get(guild_id, DEFAULT_RANDOM_CHAT_INTERVAL_MINUTES)


def is_random_chat_enabled(guild_id: int | None) -> bool:
    if guild_id is None:
        return False
    return guild_random_chat_enabled.get(guild_id, DEFAULT_RANDOM_CHAT_ENABLED)


def is_random_gifs_enabled(guild_id: int | None) -> bool:
    if guild_id is None:
        return DEFAULT_RANDOM_GIFS_ENABLED
    return guild_random_gifs_enabled.get(guild_id, DEFAULT_RANDOM_GIFS_ENABLED)


def fetch_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SynccordBot/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_random_cat_url() -> str | None:
    payload = fetch_json("https://api.thecatapi.com/v1/images/search?limit=1")
    if isinstance(payload, list) and payload:
        return payload[0].get("url")
    return None


def fetch_random_waifu_url() -> str | None:
    payload = fetch_json("https://api.waifu.pics/sfw/waifu")
    if isinstance(payload, dict):
        return payload.get("url")
    return None


def fetch_random_meme() -> tuple[str, str] | None:
    payload = fetch_json("https://api.imgflip.com/get_memes")
    if isinstance(payload, dict) and payload.get("success"):
        memes = payload.get("data", {}).get("memes", [])
        if memes:
            meme = random.choice(memes)
            return meme.get("name", "Random meme"), meme.get("url")
    return None


def fetch_random_gif_url(query: str) -> str | None:
    encoded_query = urllib.parse.quote(query)
    payload = fetch_json(
        f"https://api.waifu.pics/sfw/{encoded_query}"
    )
    if isinstance(payload, dict):
        return payload.get("url")
    return None


def _kind_color(kind: str) -> discord.Color:
    return {
        "success": SUCCESS_COLOR,
        "warn": WARN_COLOR,
        "error": ERROR_COLOR,
        "info": ACCENT_COLOR,
        "config": ACCENT_COLOR,
    }.get(kind, ACCENT_COLOR)


def make_embed(
    title: str,
    description: str | None = None,
    *,
    kind: str = "info",
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=_kind_color(kind))
    avatar_url = client.user.display_avatar.url if client.user else None
    embed.set_author(name=CHAR_NAME, icon_url=avatar_url)
    embed.set_footer(text=f"{BRAND_NAME} • {CHAR_NAME}")
    return embed


# Backwards-compatible alias used throughout the file.
def make_config_embed(title: str, description: str | None = None) -> discord.Embed:
    return make_embed(title, description, kind="config")


def get_welcome_channel_id(guild_id: int | None) -> int | None:
    if guild_id is None:
        return None
    return guild_welcome_channel_ids.get(guild_id)


def get_welcome_channel_mention(guild: discord.Guild) -> str:
    welcome_channel_id = get_welcome_channel_id(guild.id)
    if welcome_channel_id is None:
        return "not set"
    channel = guild.get_channel(welcome_channel_id)
    return channel.mention if channel else f"<#{welcome_channel_id}>"


def build_welcome_prompt(member: discord.Member, welcome_idea: str) -> str:
    return (
        f"You are {CHAR_NAME}, welcoming a new Discord server member.\n"
        f"Server name: {member.guild.name}\n"
        f"New member display name: {member.display_name}\n"
        f"New member mention: {member.mention}\n"
        f"Welcome idea from the server admin: {welcome_idea}\n\n"
        "Write one short, crisp welcome message.\n"
        "Requirements:\n"
        "- Mention the user exactly once.\n"
        "- Keep it under 35 words.\n"
        "- Sound warm, playful, and natural.\n"
        "- Use the admin's idea as inspiration, not a rigid template.\n"
        "- Do not include quotation marks or labels.\n"
    )


def generate_welcome_message(member: discord.Member, welcome_idea: str) -> str:
    if not welcome_idea.strip():
        return f"Welcome to {member.guild.name}, {member.mention}. Glad you're here."

    try:
        response = client_ai.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": build_system_prompt(get_nsfw_mode(member.guild.id))},
                {"role": "user", "content": build_welcome_prompt(member, welcome_idea)},
            ],
        )
        content = response.choices[0].message.content.strip()
        if content:
            if member.mention not in content:
                content = f"{member.mention} {content}"
            return split_message(content, limit=180)[0]
    except Exception as error:
        print(f"Welcome generation failed: {error}")

    return f"{member.mention} {welcome_idea.strip()}"


@client.event
async def on_ready() -> None:
    init_database()
    load_guild_settings()
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as error:
        print(f"Slash command sync failed: {error}")
    print(f"Logged in as {client.user} ({client.user.id})")
    if not random_channel_posts.is_running():
        random_channel_posts.start()
    if not reminder_dispatcher.is_running():
        reminder_dispatcher.start()


@tree.command(name="activate", description="Enable the bot in a channel for this server.")
@app_commands.default_permissions(manage_guild=True)
async def activate(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    active_channels = active_channels_by_guild.setdefault(interaction.guild.id, set())
    active_channels.add(channel.id)
    save_guild_settings(interaction.guild.id)
    await interaction.response.send_message(
        embed=make_config_embed(
            "Channel Activated",
            f"{CHAR_NAME} can now reply in {channel.mention} when mentioned or replied to.",
        ),
        ephemeral=True,
    )


@tree.command(name="deactivate", description="Disable the bot in a channel for this server.")
@app_commands.default_permissions(manage_guild=True)
async def deactivate(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    active_channels = active_channels_by_guild.setdefault(interaction.guild.id, set())
    active_channels.discard(channel.id)
    save_guild_settings(interaction.guild.id)
    await interaction.response.send_message(
        embed=make_config_embed(
            "Channel Removed",
            f"{channel.mention} was removed from the active channel list.",
        ),
        ephemeral=True,
    )


@tree.command(name="listbotchannels", description="Show the channels where the bot is active in this server.")
@app_commands.default_permissions(manage_guild=True)
async def listbotchannels(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        embed=make_config_embed(
            "Active Channels",
            f"Active channels: {format_active_channels(interaction.guild)}",
        ),
        ephemeral=True,
    )


@tree.command(name="nsfw", description="Enable or disable NSFW mode for this server.")
@app_commands.default_permissions(manage_guild=True)
async def nsfw(interaction: discord.Interaction, enabled: bool) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    guild_nsfw_modes[interaction.guild.id] = enabled
    save_guild_settings(interaction.guild.id)
    await interaction.response.send_message(
        embed=make_config_embed(
            "NSFW Updated",
            f"NSFW mode is now {'enabled' if enabled else 'disabled'} for this server.",
        ),
        ephemeral=True,
    )


@tree.command(name="botconfig", description="Show the current bot configuration for this server.")
@app_commands.default_permissions(manage_guild=True)
async def botconfig(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    nsfw_mode = get_nsfw_mode(interaction.guild.id)
    welcome_message = get_welcome_message(interaction.guild.id)
    embed = make_config_embed("Bot Configuration")
    embed.add_field(name="Active channels", value=format_active_channels(interaction.guild), inline=False)
    embed.add_field(name="NSFW mode", value="enabled" if nsfw_mode else "disabled", inline=True)
    embed.add_field(name="Welcome message", value=welcome_message or "not set", inline=False)
    embed.add_field(name="Welcome channel", value=get_welcome_channel_mention(interaction.guild), inline=False)
    embed.add_field(
        name="Random chat",
        value="enabled" if is_random_chat_enabled(interaction.guild.id) else "disabled",
        inline=True,
    )
    embed.add_field(
        name="Random gifs",
        value="enabled" if is_random_gifs_enabled(interaction.guild.id) else "disabled",
        inline=True,
    )
    embed.add_field(
        name="Random interval",
        value=f"{get_random_chat_interval(interaction.guild.id)} minutes",
        inline=True,
    )
    embed.add_field(name="Anti-spam cooldown", value=f"{COOLDOWN_SECONDS} seconds", inline=True)
    embed.add_field(name="Persistent memory", value="enabled", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="help", description="Show all bot features and commands.")
async def help_command(interaction: discord.Interaction) -> None:
    embed = make_embed("Help", f"Everything {CHAR_NAME} can do.", kind="info")
    embed.add_field(
        name="Highlights",
        value=(
            "• Natural roleplay conversation with persistent memory\n"
            "• Channel-scoped activation — replies only where invited\n"
            "• Per-server NSFW toggle and anti-spam cooldown\n"
            "• AI-crafted welcome messages for new members\n"
            "• Optional ambient chatter and reaction GIFs\n"
            "• Curated fun and utility commands"
        ),
        inline=False,
    )
    embed.add_field(
        name="Setup",
        value=(
            "`/activate` `/deactivate` `/listbotchannels`\n"
            "`/nsfw` `/botconfig` `/status` `/resetmemory`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Memory & persona",
        value=(
            "`/remember` `/forget` `/myfacts`\n"
            "`/persona` `/resetpersona`"
        ),
        inline=False,
    )
    embed.add_field(
        name="AI commands",
        value=(
            "`/ask` `/summary` `/translate` `/tldr`\n"
            "`/vibecheck` `/hottake` `/eli5` `/coach` `/story`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Utility",
        value=(
            "`/poll` `/reminder` `/define` `/wiki` `/roll` `/8ball`\n"
            "`/afk` `/snipe` `/serverinfo` `/userinfo` `/roleinfo`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Welcome & ambience",
        value=(
            "`/setwelcome` `/welcome-ch` `/viewwelcome` `/clearwelcome` `/testwelcome`\n"
            "`/randomchat` `/randomgifs`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Misc & fun",
        value=(
            "`/ping` `/avatar` `/banner` `/say` `/purge` `/rate`\n"
            "`/meme` `/cat` `/waifu`"
        ),
        inline=False,
    )
    embed.add_field(
        name="How to chat",
        value="Mention me or reply to one of my messages in an active channel. Type `!reset` to clear the current channel's memory.",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="resetmemory", description="Clear the saved conversation memory for the current channel.")
@app_commands.default_permissions(manage_guild=True)
async def resetmemory(interaction: discord.Interaction) -> None:
    scope_key = f"dm:{interaction.channel_id}" if interaction.guild is None else f"guild:{interaction.guild.id}:channel:{interaction.channel_id}"
    clear_history(scope_key)
    await interaction.response.send_message(
        embed=make_config_embed("Memory Reset", "Saved conversation memory for this channel was cleared."),
        ephemeral=True,
    )


@tree.command(name="ping", description="Show bot latency.")
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(client.latency * 1000)
    embed = make_embed("Pong", f"Gateway latency: **{latency_ms} ms**", kind="success")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="avatar", description="Show your avatar or another user's avatar.")
async def avatar(interaction: discord.Interaction, user: discord.User | None = None) -> None:
    target_user = user or interaction.user
    avatar_asset = target_user.display_avatar
    await interaction.response.send_message(avatar_asset.url)


@tree.command(name="say", description="Make the bot send a message.")
@app_commands.default_permissions(manage_messages=True)
async def say(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.send_message("Sent.", ephemeral=True)
    await interaction.channel.send(text)


@tree.command(name="setwelcome", description="Set a welcome message for this server.")
@app_commands.default_permissions(manage_guild=True)
async def setwelcome(interaction: discord.Interaction, message: str) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    guild_welcome_messages[interaction.guild.id] = message.strip()
    save_guild_settings(interaction.guild.id)
    await interaction.response.send_message(
        embed=make_config_embed("Welcome Updated", "Welcome message updated."),
        ephemeral=True,
    )


@tree.command(name="welcome-ch", description="Set the channel used for join welcome messages.")
@app_commands.default_permissions(manage_guild=True)
async def welcome_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    guild_welcome_channel_ids[interaction.guild.id] = channel.id
    save_guild_settings(interaction.guild.id)
    await interaction.response.send_message(
        embed=make_config_embed("Welcome Channel Updated", f"New members will be greeted in {channel.mention}."),
        ephemeral=True,
    )


@tree.command(name="viewwelcome", description="Show the current welcome setup for this server.")
@app_commands.default_permissions(manage_guild=True)
async def viewwelcome(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    embed = make_config_embed("Welcome Setup")
    embed.add_field(name="Welcome idea", value=get_welcome_message(interaction.guild.id) or "not set", inline=False)
    embed.add_field(name="Welcome channel", value=get_welcome_channel_mention(interaction.guild), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="clearwelcome", description="Clear the saved welcome idea for this server.")
@app_commands.default_permissions(manage_guild=True)
async def clearwelcome(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    guild_welcome_messages[interaction.guild.id] = ""
    save_guild_settings(interaction.guild.id)
    await interaction.response.send_message(
        embed=make_config_embed("Welcome Cleared", "The saved welcome idea has been cleared."),
        ephemeral=True,
    )


@tree.command(name="testwelcome", description="Preview the welcome message for this server.")
@app_commands.default_permissions(manage_guild=True)
async def testwelcome(
    interaction: discord.Interaction,
    member: discord.Member | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    welcome_idea = get_welcome_message(interaction.guild.id)
    if not welcome_idea:
        await interaction.response.send_message(
            embed=make_config_embed("No Welcome Idea", "Set one first with /setwelcome."),
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    preview_member = member or interaction.user
    preview_text = await client.loop.run_in_executor(
        None,
        lambda: generate_welcome_message(preview_member, welcome_idea),
    )
    embed = make_config_embed("Welcome Preview")
    embed.add_field(name="Welcome channel", value=get_welcome_channel_mention(interaction.guild), inline=False)
    embed.add_field(name="Preview", value=preview_text, inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="randomchat", description="Toggle random bot messages in active channels.")
@app_commands.default_permissions(manage_guild=True)
async def randomchat(
    interaction: discord.Interaction,
    enabled: bool,
    interval_minutes: app_commands.Range[int, 5, 720] | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    guild_random_chat_enabled[interaction.guild.id] = enabled
    if interval_minutes is not None:
        guild_random_chat_interval_minutes[interaction.guild.id] = int(interval_minutes)
    elif interaction.guild.id not in guild_random_chat_interval_minutes:
        guild_random_chat_interval_minutes[interaction.guild.id] = DEFAULT_RANDOM_CHAT_INTERVAL_MINUTES
    save_guild_settings(interaction.guild.id)
    await interaction.response.send_message(
        embed=make_config_embed(
            "Random Chat Updated",
            f"Random chat {'enabled' if enabled else 'disabled'}. Interval: {get_random_chat_interval(interaction.guild.id)} minutes.",
        ),
        ephemeral=True,
    )


@tree.command(name="randomgifs", description="Toggle random gifs in automatic channel posts.")
@app_commands.default_permissions(manage_guild=True)
async def randomgifs(interaction: discord.Interaction, enabled: bool) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_config_embed("Server Only", "This command only works inside a server."),
            ephemeral=True,
        )
        return

    guild_random_gifs_enabled[interaction.guild.id] = enabled
    save_guild_settings(interaction.guild.id)
    await interaction.response.send_message(
        embed=make_config_embed(
            "Random GIFs Updated",
            f"Random gifs are now {'enabled' if enabled else 'disabled'}.",
        ),
        ephemeral=True,
    )


@tree.command(name="status", description="Show the current feature status.")
async def status(interaction: discord.Interaction) -> None:
    guild_id = interaction.guild.id if interaction.guild else None
    welcome_message = get_welcome_message(guild_id)
    embed = make_embed("Status", f"Live status for {CHAR_NAME}.", kind="info")
    embed.add_field(name="Conversational AI", value="online", inline=True)
    embed.add_field(name="Latency", value=f"{round(client.latency * 1000)} ms", inline=True)
    embed.add_field(
        name="NSFW mode",
        value="enabled" if get_nsfw_mode(guild_id) else "disabled",
        inline=True,
    )
    embed.add_field(name="Welcome message", value=welcome_message or "not set", inline=False)
    if interaction.guild is not None:
        embed.add_field(name="Welcome channel", value=get_welcome_channel_mention(interaction.guild), inline=False)
    embed.add_field(
        name="Random chat",
        value="enabled" if is_random_chat_enabled(guild_id) else "disabled",
        inline=True,
    )
    embed.add_field(
        name="Random gifs",
        value="enabled" if is_random_gifs_enabled(guild_id) else "disabled",
        inline=True,
    )
    embed.add_field(
        name="Random interval",
        value=f"{get_random_chat_interval(guild_id)} minutes",
        inline=True,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="purge", description="Delete a number of recent messages in the current channel.")
@app_commands.default_permissions(manage_messages=True)
async def purge(
    interaction: discord.Interaction,
    amount: app_commands.Range[int, 1, 100],
) -> None:
    if interaction.channel is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("This command only works in a text channel.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    deleted_messages = await interaction.channel.purge(limit=int(amount))
    await interaction.followup.send(f"Deleted {len(deleted_messages)} message(s).", ephemeral=True)


@tree.command(name="banner", description="Show your banner or another user's banner.")
async def banner(interaction: discord.Interaction, user: discord.User | None = None) -> None:
    target_user = user or interaction.user
    fetched_user = await client.fetch_user(target_user.id)
    banner_asset = fetched_user.banner
    if banner_asset is None:
        await interaction.response.send_message("That user does not have a banner.", ephemeral=True)
        return

    await interaction.response.send_message(banner_asset.url)


@tree.command(name="meme", description="Send a random meme from the internet.")
async def meme(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    try:
        meme_result = await client.loop.run_in_executor(None, fetch_random_meme)
    except Exception as error:
        await interaction.followup.send(f"Meme fetch failed: {error}", ephemeral=True)
        return

    if not meme_result:
        await interaction.followup.send("Couldn't fetch a meme right now.", ephemeral=True)
        return

    meme_name, meme_url = meme_result
    await interaction.followup.send(content=meme_name, embed=discord.Embed().set_image(url=meme_url))


@tree.command(name="cat", description="Send a random cat image from the internet.")
async def cat(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    try:
        cat_url = await client.loop.run_in_executor(None, fetch_random_cat_url)
    except Exception as error:
        await interaction.followup.send(f"Cat fetch failed: {error}", ephemeral=True)
        return

    if not cat_url:
        await interaction.followup.send("Couldn't fetch a cat right now.", ephemeral=True)
        return

    await interaction.followup.send(embed=discord.Embed(title="Random cat").set_image(url=cat_url))


@tree.command(name="waifu", description="Send a random waifu image from the internet.")
async def waifu(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    try:
        waifu_url = await client.loop.run_in_executor(None, fetch_random_waifu_url)
    except Exception as error:
        await interaction.followup.send(f"Waifu fetch failed: {error}", ephemeral=True)
        return

    if not waifu_url:
        await interaction.followup.send("Couldn't fetch a waifu image right now.", ephemeral=True)
        return

    await interaction.followup.send(embed=discord.Embed(title="Random waifu").set_image(url=waifu_url))


@tree.command(name="rate", description="Rate anything from 1 to 10.")
async def rate(interaction: discord.Interaction, thing: str) -> None:
    score = random.randint(1, 10)
    await interaction.response.send_message(f"I rate `{thing}` a **{score}/10**.")


# ============================================================================
# Memory + persona commands
# ============================================================================

@tree.command(name="remember", description="Pin a fact about yourself for the bot to remember.")
async def remember(interaction: discord.Interaction, fact: str) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_embed("Server Only", "Use this in a server.", kind="warn"),
            ephemeral=True,
        )
        return
    fact = fact.strip()
    if len(fact) > 240:
        await interaction.response.send_message(
            embed=make_embed("Too Long", "Keep facts under 240 characters.", kind="warn"),
            ephemeral=True,
        )
        return
    fact_id = add_user_fact(interaction.guild.id, interaction.user.id, fact)
    await interaction.response.send_message(
        embed=make_embed("Got it", f"Pinned: `{fact}`\n*(id: {fact_id})*", kind="success"),
        ephemeral=True,
    )


@tree.command(name="forget", description="Remove one of your pinned facts by id.")
async def forget(interaction: discord.Interaction, fact_id: int) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_embed("Server Only", "Use this in a server.", kind="warn"),
            ephemeral=True,
        )
        return
    removed = delete_user_fact(interaction.guild.id, interaction.user.id, fact_id)
    if removed:
        await interaction.response.send_message(
            embed=make_embed("Forgotten", f"Fact `{fact_id}` removed.", kind="success"),
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            embed=make_embed("Not Found", "No fact with that id belongs to you.", kind="warn"),
            ephemeral=True,
        )


@tree.command(name="myfacts", description="Show the facts the bot remembers about you.")
async def myfacts(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_embed("Server Only", "Use this in a server.", kind="warn"),
            ephemeral=True,
        )
        return
    facts = list_user_facts(interaction.guild.id, interaction.user.id)
    embed = make_embed(
        "Your pinned facts",
        "\n".join(f"`{fid}` — {text}" for fid, text in facts) if facts else "Nothing pinned yet. Use `/remember`.",
        kind="info",
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="persona", description="Set a temporary persona overlay in this channel (30 minutes).")
@app_commands.default_permissions(manage_messages=True)
async def persona(interaction: discord.Interaction, description: str) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_embed("Server Only", "Use this in a server.", kind="warn"),
            ephemeral=True,
        )
        return
    set_persona_override(interaction.guild.id, interaction.channel.id, description)
    await interaction.response.send_message(
        embed=make_embed(
            "Persona Overlay Set",
            f"Active for the next 30 minutes in this channel:\n*{description}*",
            kind="success",
        ),
        ephemeral=True,
    )


@tree.command(name="resetpersona", description="Clear the temporary persona overlay in this channel.")
@app_commands.default_permissions(manage_messages=True)
async def resetpersona(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_embed("Server Only", "Use this in a server.", kind="warn"),
            ephemeral=True,
        )
        return
    clear_persona_override(interaction.guild.id, interaction.channel.id)
    await interaction.response.send_message(
        embed=make_embed("Persona Cleared", "Back to base character.", kind="success"),
        ephemeral=True,
    )


# ============================================================================
# Utility commands
# ============================================================================

class PollView(discord.ui.View):
    def __init__(self, options: list[str], author_id: int):
        super().__init__(timeout=24 * 60 * 60)
        self.options = options
        self.author_id = author_id
        self.votes: dict[int, int] = {}  # user_id -> option index
        for index, label in enumerate(options):
            self.add_item(self._make_button(index, label))

    def _make_button(self, index: int, label: str) -> discord.ui.Button:
        button: discord.ui.Button = discord.ui.Button(
            label=label[:80] or f"Option {index + 1}",
            style=discord.ButtonStyle.primary,
            custom_id=f"poll-{index}",
        )

        async def callback(interaction: discord.Interaction) -> None:
            self.votes[interaction.user.id] = index
            await interaction.response.send_message(
                f"Vote recorded: **{label}**", ephemeral=True
            )

        button.callback = callback
        return button

    def tally(self) -> dict[int, int]:
        counts = {i: 0 for i in range(len(self.options))}
        for choice in self.votes.values():
            counts[choice] = counts.get(choice, 0) + 1
        return counts


@tree.command(name="poll", description="Create a quick poll with up to 5 options.")
async def poll(
    interaction: discord.Interaction,
    question: str,
    option1: str,
    option2: str,
    option3: str | None = None,
    option4: str | None = None,
    option5: str | None = None,
) -> None:
    options = [option1, option2]
    for extra in (option3, option4, option5):
        if extra:
            options.append(extra)
    embed = make_embed(f"📊 {question}", "\n".join(f"• {opt}" for opt in options), kind="info")
    embed.set_footer(text=f"{BRAND_NAME} • Poll by {interaction.user.display_name}")
    view = PollView(options, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)


@tree.command(name="reminder", description="DM yourself a reminder after a delay (e.g. 10m, 2h, 1d).")
async def reminder(interaction: discord.Interaction, when: str, text: str) -> None:
    seconds = parse_duration(when)
    if seconds is None or seconds < 10 or seconds > 30 * 86400:
        await interaction.response.send_message(
            embed=make_embed(
                "Invalid Duration",
                "Use formats like `10s`, `15m`, `2h`, `1d`, `1w`. Min 10s, max 30d.",
                kind="warn",
            ),
            ephemeral=True,
        )
        return
    fire_at = int(time.time()) + seconds
    add_reminder(
        interaction.user.id,
        interaction.channel.id,
        interaction.guild.id if interaction.guild else None,
        fire_at,
        text,
    )
    await interaction.response.send_message(
        embed=make_embed(
            "Reminder Set",
            f"I'll ping you in **{when}**:\n> {text}",
            kind="success",
        ),
        ephemeral=True,
    )


@tree.command(name="define", description="Look up a word's definition.")
async def define(interaction: discord.Interaction, word: str) -> None:
    await interaction.response.defer(thinking=True)
    try:
        payload = await client.loop.run_in_executor(
            None,
            lambda: fetch_json(f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}"),
        )
    except Exception:
        await interaction.followup.send(
            embed=make_embed("Not Found", f"No definition found for **{word}**.", kind="warn"),
            ephemeral=True,
        )
        return

    if not isinstance(payload, list) or not payload:
        await interaction.followup.send(
            embed=make_embed("Not Found", f"No definition found for **{word}**.", kind="warn"),
            ephemeral=True,
        )
        return

    entry = payload[0]
    embed = make_embed(f"📖 {entry.get('word', word)}", entry.get("phonetic", ""), kind="info")
    for meaning in entry.get("meanings", [])[:3]:
        part = meaning.get("partOfSpeech", "")
        defs = meaning.get("definitions", [])
        if not defs:
            continue
        text_lines = []
        for definition in defs[:2]:
            line = f"• {definition.get('definition', '')}"
            example = definition.get("example")
            if example:
                line += f"\n  *e.g. {example}*"
            text_lines.append(line)
        embed.add_field(name=part or "definition", value="\n".join(text_lines)[:1024], inline=False)
    await interaction.followup.send(embed=embed)


@tree.command(name="wiki", description="Get a Wikipedia summary for a topic.")
async def wiki(interaction: discord.Interaction, topic: str) -> None:
    await interaction.response.defer(thinking=True)
    try:
        payload = await client.loop.run_in_executor(
            None,
            lambda: fetch_json(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(topic.replace(' ', '_'))}"
            ),
        )
    except Exception:
        await interaction.followup.send(
            embed=make_embed("Not Found", f"No Wikipedia page found for **{topic}**.", kind="warn"),
            ephemeral=True,
        )
        return

    if not isinstance(payload, dict) or "extract" not in payload:
        await interaction.followup.send(
            embed=make_embed("Not Found", f"No Wikipedia page found for **{topic}**.", kind="warn"),
            ephemeral=True,
        )
        return

    embed = make_embed(
        payload.get("title", topic),
        payload.get("extract", "")[:1900],
        kind="info",
    )
    page_url = payload.get("content_urls", {}).get("desktop", {}).get("page")
    if page_url:
        embed.url = page_url
    thumb = payload.get("thumbnail", {}).get("source")
    if thumb:
        embed.set_thumbnail(url=thumb)
    await interaction.followup.send(embed=embed)


_DICE_PATTERN = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$", re.IGNORECASE)


@tree.command(name="roll", description="Roll dice. Format: 2d20+5, 1d6, 4d10-2.")
async def roll(interaction: discord.Interaction, dice: str = "1d20") -> None:
    match = _DICE_PATTERN.match(dice.strip().replace(" ", ""))
    if not match:
        await interaction.response.send_message(
            embed=make_embed("Bad Format", "Use formats like `1d20`, `2d6+3`, `4d10-1`.", kind="warn"),
            ephemeral=True,
        )
        return
    count_str, sides_str, mod_str = match.groups()
    count = int(count_str) if count_str else 1
    sides = int(sides_str)
    modifier = int(mod_str) if mod_str else 0
    if count < 1 or count > 100 or sides < 2 or sides > 1000:
        await interaction.response.send_message(
            embed=make_embed("Out of Range", "Count 1-100, sides 2-1000.", kind="warn"),
            ephemeral=True,
        )
        return
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + modifier
    detail = ", ".join(str(r) for r in rolls)
    if modifier:
        detail += f" {'+' if modifier > 0 else ''}{modifier}"
    embed = make_embed(f"🎲 {dice}", f"**{total}**\n`[{detail}]`", kind="info")
    await interaction.response.send_message(embed=embed)


_EIGHTBALL_ANSWERS = [
    "It is certain.", "Without a doubt.", "Yes, definitely.", "You may rely on it.",
    "Most likely.", "Outlook good.", "Signs point to yes.", "Reply hazy, try again.",
    "Ask again later.", "Better not tell you now.", "Cannot predict now.",
    "Concentrate and ask again.", "Don't count on it.", "My reply is no.",
    "Very doubtful.", "Outlook not so good.",
]


@tree.command(name="8ball", description="Ask the magic 8-ball a yes/no question.")
async def eight_ball(interaction: discord.Interaction, question: str) -> None:
    answer = random.choice(_EIGHTBALL_ANSWERS)
    embed = make_embed("🎱 Magic 8-Ball", f"**Q:** {question}\n**A:** {answer}", kind="info")
    await interaction.response.send_message(embed=embed)


@tree.command(name="afk", description="Set yourself as AFK with an optional reason.")
async def afk(interaction: discord.Interaction, reason: str = "AFK") -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=make_embed("Server Only", "Use this in a server.", kind="warn"),
            ephemeral=True,
        )
        return
    afk_users[(interaction.guild.id, interaction.user.id)] = (reason, time.time())
    await interaction.response.send_message(
        embed=make_embed(
            f"🌙 {interaction.user.display_name} is AFK",
            f"Reason: *{reason}*",
            kind="success",
        )
    )


@tree.command(name="snipe", description="Show the last deleted message in this channel.")
async def snipe(interaction: discord.Interaction) -> None:
    entry = sniped_messages.get(interaction.channel.id)
    if not entry:
        await interaction.response.send_message(
            embed=make_embed("Nothing to Snipe", "No deleted messages cached here.", kind="warn"),
            ephemeral=True,
        )
        return
    embed = make_embed(
        f"🪝 Sniped from {entry['author_name']}",
        entry["content"] or "*(no text content)*",
        kind="info",
    )
    embed.timestamp = discord.utils.snowflake_time(int(entry["snowflake"]))
    if entry.get("avatar_url"):
        embed.set_thumbnail(url=entry["avatar_url"])
    await interaction.response.send_message(embed=embed)


@tree.command(name="serverinfo", description="Show information about this server.")
async def serverinfo(interaction: discord.Interaction) -> None:
    g = interaction.guild
    if g is None:
        await interaction.response.send_message(
            embed=make_embed("Server Only", "Use this in a server.", kind="warn"),
            ephemeral=True,
        )
        return
    embed = make_embed(g.name, g.description or None, kind="info")
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Members", value=str(g.member_count), inline=True)
    embed.add_field(name="Channels", value=str(len(g.channels)), inline=True)
    embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
    embed.add_field(name="Created", value=discord.utils.format_dt(g.created_at, "R"), inline=True)
    if g.owner:
        embed.add_field(name="Owner", value=g.owner.mention, inline=True)
    embed.add_field(name="Boost tier", value=f"Tier {g.premium_tier}", inline=True)
    await interaction.response.send_message(embed=embed)


@tree.command(name="userinfo", description="Show information about a user.")
async def userinfo(interaction: discord.Interaction, user: discord.Member | None = None) -> None:
    target = user or interaction.user
    embed = make_embed(target.display_name, f"<@{target.id}>", kind="info")
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Username", value=str(target), inline=True)
    embed.add_field(name="ID", value=str(target.id), inline=True)
    if isinstance(target, discord.Member):
        embed.add_field(
            name="Joined server",
            value=discord.utils.format_dt(target.joined_at, "R") if target.joined_at else "unknown",
            inline=True,
        )
        roles = [r.mention for r in target.roles if r.name != "@everyone"]
        embed.add_field(
            name=f"Roles ({len(roles)})",
            value=" ".join(roles)[:1000] if roles else "none",
            inline=False,
        )
    embed.add_field(
        name="Account created",
        value=discord.utils.format_dt(target.created_at, "R"),
        inline=True,
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="roleinfo", description="Show information about a role.")
async def roleinfo(interaction: discord.Interaction, role: discord.Role) -> None:
    embed = make_embed(role.name, f"{role.mention}", kind="info")
    embed.color = role.color if role.color.value else ACCENT_COLOR
    embed.add_field(name="Members", value=str(len(role.members)), inline=True)
    embed.add_field(name="Position", value=str(role.position), inline=True)
    embed.add_field(name="Mentionable", value="yes" if role.mentionable else "no", inline=True)
    embed.add_field(name="Hoisted", value="yes" if role.hoist else "no", inline=True)
    embed.add_field(name="Created", value=discord.utils.format_dt(role.created_at, "R"), inline=True)
    await interaction.response.send_message(embed=embed)


# ============================================================================
# AI commands (in-character one-shots that don't pollute channel memory)
# ============================================================================

async def _ai_oneshot(
    user_prompt: str,
    *,
    guild: discord.Guild | None = None,
    extra_system: str | None = None,
    max_chars: int = 1800,
) -> str:
    nsfw = get_nsfw_mode(guild.id if guild else None)
    facts = format_user_facts_for_guild(guild) if guild else None
    system = build_system_prompt(nsfw, user_facts_block=facts)
    if extra_system:
        system = f"{system}\n\n{extra_system}"
    response = await client.loop.run_in_executor(
        None,
        lambda: client_ai.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
        ),
    )
    text = (response.choices[0].message.content or "").strip()
    return text[:max_chars] if text else "I drew a blank — try again."


@tree.command(name="ask", description="Ask anything — I answer in character without polluting channel memory.")
async def ask(interaction: discord.Interaction, question: str) -> None:
    await interaction.response.defer(thinking=True)
    answer = await _ai_oneshot(question, guild=interaction.guild)
    embed = make_embed("💬 Ask", answer, kind="info")
    embed.add_field(name="Question", value=question[:1024], inline=False)
    await interaction.followup.send(embed=embed)


@tree.command(name="summary", description="Summarize the recent conversation in this channel.")
async def summary(
    interaction: discord.Interaction,
    messages: app_commands.Range[int, 10, 200] = 50,
) -> None:
    if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message(
            embed=make_embed("Wrong Channel", "Run this in a text channel.", kind="warn"),
            ephemeral=True,
        )
        return
    await interaction.response.defer(thinking=True)
    lines: list[str] = []
    async for msg in interaction.channel.history(limit=int(messages)):
        if msg.author.bot or not msg.content:
            continue
        lines.append(f"{msg.author.display_name}: {msg.content}")
    if not lines:
        await interaction.followup.send(
            embed=make_embed("Nothing to Summarize", "No recent human messages found.", kind="warn"),
            ephemeral=True,
        )
        return
    transcript = "\n".join(reversed(lines))[-6000:]
    answer = await _ai_oneshot(
        f"Summarize the following Discord conversation in 4-7 short bullet points. Stay in character.\n\n{transcript}",
        guild=interaction.guild,
    )
    embed = make_embed(
        f"📝 Summary of last {len(lines)} messages",
        answer,
        kind="info",
    )
    await interaction.followup.send(embed=embed)


@tree.command(name="translate", description="Translate text into another language.")
async def translate(interaction: discord.Interaction, language: str, text: str) -> None:
    await interaction.response.defer(thinking=True)
    answer = await _ai_oneshot(
        f"Translate the following text into {language}. Output ONLY the translation, nothing else.\n\nText:\n{text}",
        guild=interaction.guild,
        extra_system="For this task only, set aside character voice and translate accurately.",
    )
    embed = make_embed(f"🌐 Translation → {language}", answer, kind="info")
    embed.add_field(name="Source", value=text[:1024], inline=False)
    await interaction.followup.send(embed=embed)


@tree.command(name="tldr", description="Give a TL;DR of a long block of text.")
async def tldr(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.defer(thinking=True)
    answer = await _ai_oneshot(
        f"Give a tight TL;DR (3-4 sentences max) of the following:\n\n{text}",
        guild=interaction.guild,
    )
    embed = make_embed("📌 TL;DR", answer, kind="info")
    await interaction.followup.send(embed=embed)


@tree.command(name="vibecheck", description="I read the room and call the vibe.")
async def vibecheck(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message(
            embed=make_embed("Wrong Channel", "Run this in a text channel.", kind="warn"),
            ephemeral=True,
        )
        return
    await interaction.response.defer(thinking=True)
    lines: list[str] = []
    async for msg in interaction.channel.history(limit=20):
        if msg.author.bot or not msg.content:
            continue
        lines.append(f"{msg.author.display_name}: {msg.content}")
    if not lines:
        await interaction.followup.send(
            embed=make_embed("Empty Room", "Not enough recent chatter to read.", kind="warn"),
            ephemeral=True,
        )
        return
    transcript = "\n".join(reversed(lines))
    answer = await _ai_oneshot(
        "Read this Discord channel transcript and call the current vibe in your own voice. "
        "Reply with: a one-line vibe label, an energy score 1-10, and one sentence of color commentary.\n\n"
        f"{transcript}",
        guild=interaction.guild,
    )
    embed = make_embed("✨ Vibe Check", answer, kind="info")
    await interaction.followup.send(embed=embed)


@tree.command(name="hottake", description="A spicy in-character opinion on a topic.")
async def hottake(interaction: discord.Interaction, topic: str) -> None:
    await interaction.response.defer(thinking=True)
    answer = await _ai_oneshot(
        f"Give a spicy, confident, but not mean-spirited hot take on: {topic}. Two or three sentences max.",
        guild=interaction.guild,
    )
    embed = make_embed(f"🌶️ Hot take: {topic}", answer, kind="warn")
    await interaction.followup.send(embed=embed)


@tree.command(name="eli5", description="Explain a topic like I'm five.")
async def eli5(interaction: discord.Interaction, topic: str) -> None:
    await interaction.response.defer(thinking=True)
    answer = await _ai_oneshot(
        f"Explain '{topic}' like I'm five years old. Use simple words and one fun analogy. Stay in character.",
        guild=interaction.guild,
    )
    embed = make_embed(f"🧒 ELI5: {topic}", answer, kind="info")
    await interaction.followup.send(embed=embed)


@tree.command(name="coach", description="Get a quick pep talk in character.")
async def coach(interaction: discord.Interaction, situation: str | None = None) -> None:
    await interaction.response.defer(thinking=True)
    prompt = (
        f"Give {interaction.user.display_name} a short, energetic pep talk about: {situation}."
        if situation
        else f"Give {interaction.user.display_name} a short, energetic pep talk for whatever they're up to today."
    )
    answer = await _ai_oneshot(prompt + " 3 sentences max. End with one quick action they can take right now.",
                               guild=interaction.guild)
    embed = make_embed("🔥 Pep talk", answer, kind="success")
    await interaction.followup.send(embed=embed)


@tree.command(name="story", description="Continue or start a short collaborative story.")
async def story(interaction: discord.Interaction, seed: str) -> None:
    await interaction.response.defer(thinking=True)
    answer = await _ai_oneshot(
        f"Continue this story for ONE paragraph (4-6 sentences) in your character's voice. "
        f"End on a cliffhanger or open hook so others can keep going.\n\nStory so far:\n{seed}",
        guild=interaction.guild,
    )
    embed = make_embed("📖 Story continues…", answer, kind="info")
    embed.set_footer(text=f"{BRAND_NAME} • Reply with /story to keep it going")
    await interaction.followup.send(embed=embed)


@client.event
async def on_member_join(member: discord.Member) -> None:
    welcome_message = get_welcome_message(member.guild.id)
    if not welcome_message:
        return

    channel_id = get_welcome_channel_id(member.guild.id)
    if channel_id is None:
        channel_id = next(iter(sorted(active_channels_by_guild.get(member.guild.id, set()))), None)
    if channel_id is None:
        return

    channel = member.guild.get_channel(channel_id)
    if channel is None:
        return

    ai_welcome = await client.loop.run_in_executor(
        None,
        lambda: generate_welcome_message(member, welcome_message),
    )
    await channel.send(ai_welcome)


@tasks.loop(minutes=5)
async def random_channel_posts() -> None:
    for guild in client.guilds:
        if not is_random_chat_enabled(guild.id):
            continue

        active_channel_ids = sorted(active_channels_by_guild.get(guild.id, set()))
        if not active_channel_ids:
            continue

        interval_minutes = max(5, get_random_chat_interval(guild.id))
        chance_denominator = max(1, interval_minutes // 5)
        if random.randint(1, chance_denominator) != 1:
            continue

        channel_id = random.choice(active_channel_ids)
        channel = guild.get_channel(channel_id)
        if channel is None:
            continue

        message_text = random.choice(RANDOM_CHAT_MESSAGES)
        embed = None
        if is_random_gifs_enabled(guild.id) and random.random() < 0.35:
            gif_query = random.choice(["hug", "dance", "smile", "wave", "blush"])
            try:
                gif_url = await client.loop.run_in_executor(None, lambda: fetch_json(f"https://api.waifu.pics/sfw/{gif_query}"))
                if isinstance(gif_url, dict) and gif_url.get("url"):
                    embed = discord.Embed().set_image(url=gif_url["url"])
            except Exception:
                embed = None

        try:
            await channel.send(message_text, embed=embed)
        except Exception:
            continue


@random_channel_posts.before_loop
async def before_random_channel_posts() -> None:
    await client.wait_until_ready()


@tasks.loop(seconds=20)
async def reminder_dispatcher() -> None:
    now_ts = int(time.time())
    for reminder_id, user_id, channel_id, _guild_id, text in due_reminders(now_ts):
        delete_reminder(reminder_id)
        try:
            user = client.get_user(user_id) or await client.fetch_user(user_id)
            embed = make_embed("⏰ Reminder", text, kind="info")
            embed.set_footer(text=f"{BRAND_NAME} • Set earlier")
            try:
                await user.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                channel = client.get_channel(channel_id)
                if channel is not None:
                    await channel.send(content=f"<@{user_id}>", embed=embed)
        except Exception as error:
            print(f"Reminder dispatch failed for id {reminder_id}: {error}")


@reminder_dispatcher.before_loop
async def before_reminder_dispatcher() -> None:
    await client.wait_until_ready()


@client.event
async def on_message_delete(message: discord.Message) -> None:
    if message.author.bot or not message.content:
        return
    sniped_messages[message.channel.id] = {
        "author_name": message.author.display_name,
        "avatar_url": message.author.display_avatar.url if message.author.display_avatar else None,
        "content": message.content[:1900],
        "snowflake": message.id,
    }


@client.event
async def on_message(message: discord.Message) -> None:
    # AFK handling — runs even when bot is not addressed
    if not message.author.bot and message.guild is not None:
        # Clear AFK if the AFK user just spoke
        afk_key = (message.guild.id, message.author.id)
        if afk_key in afk_users:
            afk_users.pop(afk_key, None)
            try:
                await message.channel.send(
                    embed=make_embed(
                        f"☀️ Welcome back, {message.author.display_name}",
                        "AFK status cleared.",
                        kind="success",
                    ),
                    delete_after=15,
                )
            except discord.HTTPException:
                pass
        # Notify if any mentioned user is AFK
        for mentioned in message.mentions:
            entry = afk_users.get((message.guild.id, mentioned.id))
            if entry:
                reason, since_ts = entry
                try:
                    await message.channel.send(
                        embed=make_embed(
                            f"🌙 {mentioned.display_name} is AFK",
                            f"*{reason}*\nSince {discord.utils.format_dt(datetime.fromtimestamp(since_ts, tz=timezone.utc), 'R')}",
                            kind="warn",
                        ),
                        delete_after=20,
                    )
                except discord.HTTPException:
                    pass

    if not should_reply(message):
        return

    content = message.content.strip()
    if not content:
        return

    if content.lower() == "!reset":
        clear_history(get_scope_key(message))
        await message.channel.send(
            embed=make_embed("Memory Cleared", "I'll start fresh from here.", kind="success")
        )
        return

    if is_rate_limited(message):
        return

    try:
        async with message.channel.typing():
            msgs = build_messages(message)
            response = await client.loop.run_in_executor(
                None,
                lambda: client_ai.chat.completions.create(
                    model=MODEL_NAME,
                    messages=msgs,
                ),
            )
            reply = response.choices[0].message.content.strip()

        if not reply:
            await message.channel.send("I do not have a reply for that yet.")
            return

        scope_key = get_scope_key(message)
        append_history(scope_key, "user", content)
        append_history(scope_key, "assistant", reply)

        for chunk in split_message(reply):
            await message.channel.send(chunk)
    except Exception as error:
        print(f"Bot error: {error}")
        await message.channel.send("Something went wrong while generating a reply.")


init_database()
load_guild_settings()
client.run(DISCORD_TOKEN)
