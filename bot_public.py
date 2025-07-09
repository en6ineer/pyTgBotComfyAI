import logging
import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler,
)

# --- Константы ---
API_TOKEN = "YOUR_TOKEN_FOR_COMFYAI"
WORKFLOW_ID = "ID"
BASE_URL = "https://api.comfyonline.app/api"
PROMPT_DEFAULT = "The pair of images highlights a clothing and its styling on a model, high resolution, 4K, 8K; [IMAGE1] Detailed product shot of a clothing [IMAGE2] The same cloth is worn by a model in a lifestyle setting. Dress these clothes on the model, if you need to stretch in size. If these are trousers, then put them on the bottom of the body. Recognize for the top this clothing or for the bottom."

# --- Состояния ---
PHOTO_MODEL, PHOTO_CLOTHES, TEXT_PROMPT = range(3)

# --- Логгинг ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("👗 Примерить одежду", callback_data="try_on")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! Выберите действие:", reply_markup=reply_markup)


# --- Обработка нажатия кнопки ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "try_on":
        await query.edit_message_text("Пожалуйста, отправьте фото модели (человека).")
        return PHOTO_MODEL


# --- Получение фото модели ---
async def handle_model_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    model_path = f"model_{update.message.from_user.id}.jpg"
    await photo_file.download_to_drive(model_path)
    context.user_data["model_path"] = model_path

    await update.message.reply_text("Теперь отправьте фото одежды.")
    return PHOTO_CLOTHES


# --- Получение фото одежды ---
async def handle_clothes_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    clothes_path = f"clothes_{update.message.from_user.id}.jpg"
    await photo_file.download_to_drive(clothes_path)
    context.user_data["clothes_path"] = clothes_path

    await update.message.reply_text(
        "Можете ввести текстовый промпт (например: realistic look). Или отправьте /skip для промпта по умолчанию."
    )
    return TEXT_PROMPT


# --- Пропуск ввода текста ---
async def skip_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["prompt"] = PROMPT_DEFAULT
    return await send_to_api(update, context)


# --- Обработка текста ---
async def handle_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["prompt"] = update.message.text
    return await send_to_api(update, context)


# --- Отправка запроса к API comfyAI ---
async def send_to_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import base64
    import time

    user_id = update.message.from_user.id
    model_path = context.user_data["model_path"]
    clothes_path = context.user_data["clothes_path"]
    prompt = context.user_data["prompt"]

    # --- Кодировка изображения в base64 ---
    def encode_image_base64(file_path):
        try:
            with open(file_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            print(f"✅ Изображение {file_path} закодировано в base64.")
            return f"data:image/jpeg;base64,{encoded}"
        except Exception as e:
            print(f"❌ Ошибка при кодировании {file_path}: {e}")
            return None

    model_b64 = encode_image_base64(model_path)
    clothes_b64 = encode_image_base64(clothes_path)

    if not model_b64 or not clothes_b64:
        await update.message.reply_text("Ошибка при кодировании изображений.")
        return ConversationHandler.END

    # --- Формирование запроса ---
    payload = {
        "workflow_id": WORKFLOW_ID,
        "input": {
            "CLIPTextEncode_text_6": prompt,
            "LoadImage_image_45": clothes_b64,
            "LoadImage_image_95": model_b64,
        },
    }

    print("\n=== 🔄 Отправка запроса в comfyAI ===")
    print("📤 Payload:", payload)

    try:
        res = requests.post(
            f"{BASE_URL}/run_workflow",
            json=payload,
            headers={
                "Authorization": f"Bearer {API_TOKEN}",
                "Content-Type": "application/json"
            },
        )
    except Exception as e:
        print("❌ Ошибка запроса к comfyAI:", e)
        await update.message.reply_text("Не удалось подключиться к сервису comfyAI.")
        return ConversationHandler.END

    if res.status_code != 200:
        print(f"❌ Ошибка run_workflow: {res.status_code}, ответ: {res.text}")
        await update.message.reply_text("Ошибка при запуске задачи 😔")
        return ConversationHandler.END

    task_response = res.json()
    print("📩 Ответ от run_workflow:", task_response)
    task_id = task_response.get("data", {}).get("task_id")

    if not task_id:
        await update.message.reply_text("❌ Не удалось получить task_id от comfyAI.")
        return ConversationHandler.END

    await update.message.reply_text("Фото отправлены! Ждём результат...")

    # --- Проверка статуса (до 30 сек) ---
    start_time = time.time()
    timeout = 300  # секунд

    while time.time() - start_time < timeout:
        print(f"⏳ Проверка статуса... (прошло {int(time.time() - start_time)} сек)")
        time.sleep(60)

        try:
            status_res = requests.post(
                f"{BASE_URL}/query_run_workflow_status",
                json={"task_id": task_id},
                headers={"Authorization": f"Bearer {API_TOKEN}"},
            )
            data = status_res.json()
        except Exception as e:
            print("❌ Ошибка при получении статуса:", e)
            await update.message.reply_text("⚠️ Не удалось получить статус задачи.")
            return ConversationHandler.END

        print("📥 Ответ от comfyAI (query_run_workflow_status):", data)

        status = data.get("status") or data.get("data", {}).get("state")  # fallback для 'state'
        if status == "COMPLETED":
            output = data.get("output", {}) or data.get("data", {}).get("output", {})
            url_list = output.get("output_url_list", [])
            if url_list:
                # кнопка «Новая примерка»
                keyboard = [
    [InlineKeyboardButton("👗 Сделать новую примерку", callback_data="try_on")],
]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_photo(
                    url_list[0],
                    caption="Вот результат 🎉",
                    reply_markup=reply_markup,
                )
            else:
                await update.message.reply_text("⚠️ Результат готов, но изображение не получено.")
            break
        elif status == "ERROR" or status == "FAILED":
            await update.message.reply_text("❌ Задача завершилась с ошибкой.")
            break
    else:
        await update.message.reply_text("⏳ Время ожидания истекло. Попробуйте снова позже.")

    return ConversationHandler.END



# --- Отмена ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Операция отменена.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# --- main ---
def main():
    application = ApplicationBuilder().token("TELEGRAM_TOKEN").build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler)],
        states={
            PHOTO_MODEL: [MessageHandler(filters.PHOTO, handle_model_photo)],
            PHOTO_CLOTHES: [MessageHandler(filters.PHOTO, handle_clothes_photo)],
            TEXT_PROMPT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_prompt),
                CommandHandler("skip", skip_prompt),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    application.run_polling()


if __name__ == "__main__":
    main()

