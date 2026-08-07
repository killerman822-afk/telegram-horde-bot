import os
import logging
import asyncio
import httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "8567980103"))
HORDE_API_KEY = os.getenv("HORDE_API_KEY", "0000000000")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    await update.message.reply_text(
        "Привет! Напиши промпт.\n"
        "Можно добавить негативный через |\n\n"
        "Пример:\n"
        "redhead chubby woman, large natural breasts, nude | skinny, deformed"
    )

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return

    text = update.message.text.strip()
    if not text:
        return

    if "|" in text:
        positive, negative = [x.strip() for x in text.split("|", 1)]
    else:
        positive = text
        negative = "deformed, bad anatomy, blurry, low quality, watermark"

    await update.message.reply_text("Генерирую... Подожди")

    payload = {
        "prompt": positive,
        "negative_prompt": negative,
        "params": {
            "n": 1,
            "width": 512,
            "height": 512,
            "steps": 20,
            "cfg_scale": 7,
            "sampler_name": "k_euler_a",
        },
        "nsfw": True,
        "censor_nsfw": False,
        "r2": True,
    }

    headers = {
        "apikey": HORDE_API_KEY,
        "Client-Agent": "PrivateBot:1.0"
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Создаём задачу
            r = await client.post(
                "https://stablehorde.net/api/v2/generate/async",
                json=payload,
                headers=headers
            )
            r.raise_for_status()
            job_id = r.json()["id"]
            logger.info(f"Job created: {job_id}")

            # Ждём результат
            for attempt in range(45):  # ~3 минуты
                await asyncio.sleep(4)

                check = await client.get(
                    f"https://stablehorde.net/api/v2/generate/check/{job_id}",
                    headers=headers
                )
                data = check.json()

                if data.get("done"):
                    status = await client.get(
                        f"https://stablehorde.net/api/v2/generate/status/{job_id}",
                        headers=headers
                    )
                    gens = status.json().get("generations", [])
                    if gens and gens[0].get("img"):
                        await update.message.reply_photo(photo=gens[0]["img"])
                        return
                    else:
                        await update.message.reply_text("Картинка не пришла")
                        return

                if data.get("faulted"):
                    await update.message.reply_text("Ошибка на стороне Horde")
                    return

                # Показываем прогресс каждые ~20 секунд
                if attempt % 5 == 0 and attempt > 0:
                    wait_time = data.get("wait_time", 0)
                    queue = data.get("queue_position", "?")
                    await update.message.reply_text(
                        f"Ещё жду... позиция в очереди: {queue}, ~{wait_time} сек"
                    )

            await update.message.reply_text("Слишком долго. Попробуй ещё раз.")

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error: {e.response.status_code} - {e.response.text}")
        await update.message.reply_text(f"Ошибка API: {e.response.status_code}")
    except Exception as e:
        logger.exception("Ошибка генерации")
        await update.message.reply_text(f"Ошибка: {str(e)}")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")
    
    if HORDE_API_KEY == "0000000000":
        logger.warning("Используется анонимный ключ Horde — NSFW может не работать")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_image))
    
    logger.info("Бот запускается...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
