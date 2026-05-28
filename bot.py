import os
import discord
from discord.ext import commands
import google.generativeai as genai

# ================= CONFIGURATION VIA RENDER =================
# On récupère les clés que tu as entrées en toute sécurité sur le site de Render
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# C'est ici que tu définis ta personnalité !
SYSTEM_PROMPT = """
Tu es le clone virtuel de Sacha (Don Sacha). Tu devez répondre aux messages sur Discord EXACTEMENT comme lui.
- Tu t'appelles Don Sacha.
- Ton ton est décontracté, amical, un peu geek (tu aimes le hardware, les cartes graphiques comme la RTX 3050).
- Utilise des expressions drôles, pas de phrases trop formelles ni de langage soutenu.
- Ne dis JAMAIS que tu es une IA ou un modèle linguistique. Tu es Don Sacha, un point c'est tout.
- Si on te pose une question complexe, réponds avec ton style habituel.
"""
# ============================================================

# Configuration des accès Discord
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Configuration de l'IA (uniquement si la clé est présente)
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=SYSTEM_PROMPT
    )
else:
    print("❌ Erreur : GEMINI_API_KEY est introuvable dans les variables d'environnement.")

@bot.event
async def on_ready():
    print(f"🤖 Le clone de {bot.user.name} est connecté et prêt !")

@bot.event
async def on_message(message):
    # Évite que le bot se réponde à lui-même
    if message.author == bot.user:
        return

    # Vérifie si c'est un message privé (DM) ou si le bot est mentionné
    is_dm = isinstance(message.channel, discord.DMChannel)
    
    if bot.user.mentioned_in(message) or is_dm:
        # Nettoyage du texte pour retirer les mentions Discord
        clean_text = message.content
        if bot.user.mentioned_in(message):
            clean_text = clean_text.replace(f'<@!{bot.user.id}>', '').replace(f'<@{bot.user.id}>', '')
        clean_text = clean_text.strip()
        
        # Si le message est vide après avoir retiré la mention
        if not clean_text:
            clean_text = "Salut Sacha !"

        async with message.channel.typing():
            try:
                # Génération de la réponse par l'IA
                response = model.generate_content(clean_text)
                
                if response.text and response.text.strip():
                    await message.channel.send(response.text)
                else:
                    await message.channel.send("Je t'écoute, mais j'ai pas compris la question, reuf.")
            
            except Exception as e:
                # Cette ligne va afficher la vraie erreur dans la console Render
                print(f"Erreur IA critique : {e}")
                await message.channel.send("Ah, j'ai un petit bug de cerveau là...")

    await bot.process_commands(message)

if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("❌ Erreur : DISCORD_TOKEN est introuvable dans les variables d'environnement.")
