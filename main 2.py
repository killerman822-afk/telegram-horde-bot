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

DEFAULT_NEGATIVE = (
    "deformed, bad anatomy, bad hands, missing fingers, extra fingers, "
    "mutated hands, poorly drawn hands, poorly drawn face, mutation, "
    "ugly, disgusting, blurry, low quality, lowres, watermark, text, "
    "signature, username, cropped, out of frame"
)

# Популярные модели (можно добавлять свои)
POPULAR_MODELS = {
    "sdxl": "SDXL 1.0",
    "pony": "Pony Diffusion V6 XL",
    "pony6": "Pony Diffusion V6 XL",
    "realistic": "Realistic Vision",
    "dreamshaper": "DreamShaper",
    "anything": "Anything V5",
    "counterfeit": "Counterfeit-V3.0",
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    await update.message.reply_text(
        "Привет! Напиши промпт.\n\n"
        "Форматы:\n"
        "1. просто промпт\n"
        "2. промпт | негативный\n"
        "3. модель | промпт | негативный\n\n"
        "Примеры моделей:\n"
        "• sdxl\n"
        "• pony\n"
        "• realistic\n"
        "• dreamshaper\n"
        "• anything\n\n"
        "Пример:\n"
        "pony | beautiful redhead chubby woman, large natural breasts, nude, detailed skin | skinny, deformed, bad hands"
    )

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return

    text = update.message.text.strip()
    if not text:
        return

    # Парсим формат: [модель | ] промпт [ | негатив ]
    parts = [p.strip() for p in text.split("|")]

    model = None
    positive = ""
    negative = DEFAULT_NEGATIVE

    if len(parts) == 1:
        positive = parts[0]
    elif len(parts) == 2:
        # либо "модель | промпт", либо "промпт | негатив"
        first = parts[0].lower()
        if first in POPULAR_MODELS or first.startswith("sd") or "diffusion" in first or "xl" in first:
            model = parts[0]
            positive = parts[1]
        else:
            positive = parts[0]
            negative = parts[1]
    else:
        # модель | промпт | негатив
        model = parts[0]
        positive = parts[1]
        negative = parts[2]

    # Нормализуем название модели
    if model:
        model_key = model.lower().strip()
        model = POPULAR_MODELS.get(model_key, model)

    status_msg = await update.message.reply_text(
        f"Генерирую{' на ' + model if model else ''}... Подожди (обычно 30–120 сек)"
    )

    payload = {
        "prompt": positive,
        "negative_prompt": negative,
        "params": {
            "n": 1,
            "width": 768,
            "height": 768,
            "steps": 32,
            "cfg_scale": 7,
            "sampler_name": "k_euler_a",
            "denoising_strength": 1.0,
        },
        "nsfw": True,
        "censor_nsfw": False,
        "r2": True,
    }

    if model:
        payload["models"] = [model]

    headers = {
        "apikey": HORDE_API_KEY,
        "Client-Agent": "PrivateBot:1.0"
    }

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                "https://stablehorde.net/api/v2/generate/async",
                json=payload,
                headers=headers
            )
            r.raise_for_status()
            job_id = r.json()["id"]
            logger.info(f"Job created: {job_id} | model: {model or 'any'}")

            for attempt in range(60):
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
                    result = status.json()
                    gens = result.get("generations", [])

                    if gens and gens[0].get("img"):
                        used_model = gens[0].get("model", model or "unknown")
                        await status_msg.delete()
                        await update.message.reply_photo(
                            photo=gens[0]["img"],
                            caption=f"Модель: {used_model}"
                        )
                        return
                    else:
                        await status_msg.edit_text("Картинка не пришла")
                        return

                if data.get("faulted"):
                    await status_msg.edit_text("Ошибка на стороне Horde (возможно модель недоступна)")
                    return

                if attempt % 5 == 0 and attempt > 0:
                    wait_time = data.get("wait_time", 0)
                    queue = data.get("queue_position", "?")
                    try:
                        await status_msg.edit_text(
                            f"Ещё жду...\n"
                            f"Модель: {model or 'любая'}\n"
                            f"Позиция: {queue}\n"
                            f"Ожидание: ~{wait_time} сек"
                        )
                    except Exception:
                        pass

            await status_msg.edit_text("Слишком долго. Попробуй ещё раз.")

    except httpx.HTTPStatusError as e:
        error_text = e.response.text[:200] if e.response.text else ""
        logger.error(f"HTTP error: {e.response.status_code} - {error_text}")
        try:
            await status_msg.edit_text(f"Ошибка API: {e.response.status_code}\n{error_text}")
        except Exception:
            await update.message.reply_text(f"Ошибка API: {e.response.status_code}")
    except Exception as e:
        logger.exception("Ошибка генерации")
        try:
            await status_msg.edit_text(f"Ошибка: {str(e)}")
        except Exception:
            await update.message.reply_text(f"Ошибка: {str(e)}")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")

    if HORDE_API_KEY == "0000000000":
        logger.warning("Используется анонимный ключ Horde — NSFW и качество будут сильно ограничены")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_image))

    logger.info("Бот запускается...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
