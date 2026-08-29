import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clavier = [
        ["🔧 Dépannage plomberie", "🔥 Entretien chaudière"],
        ["📝 Demande de devis", "📞 Être rappelé"]
    ]

    await update.message.reply_text(
        "Bonjour 👋\nBienvenue chez Plomberie Aqualeo.\nComment puis-je vous aider ?",
        reply_markup=ReplyKeyboardMarkup(clavier, resize_keyboard=True)
    )

async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texte = update.message.text

    if texte == "🔧 Dépannage plomberie":
        reponse = "Décrivez votre problème et indiquez votre ville."
    elif texte == "🔥 Entretien chaudière":
        reponse = "Indiquez la marque de votre chaudière et votre ville."
    elif texte == "📝 Demande de devis":
        reponse = "Décrivez les travaux souhaités. Vous pouvez envoyer des photos."
    elif texte == "📞 Être rappelé":
        reponse = "Envoyez votre numéro de téléphone et vos disponibilités."
    else:
        reponse = "Merci 👍 J'ai bien reçu votre message."

    await update.message.reply_text(reponse)

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))
    application.run_polling()

if __name__ == "__main__":
    main()
