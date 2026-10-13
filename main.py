"""
Клуб подготовки к ЕГЭ по математике.
Урок -> домашка -> видеоразбор -> следующий урок.
Анастасия управляет уроками и учениками прямо в боте.
"""
import asyncio
import html
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

import covers
import db

# ================== НАСТРОЙКИ ==================
TOKEN = os.getenv("BOT_TOKEN", "")
TEACHER = os.getenv("TEACHER_NAME", "Анастасия")
TEACHER_WITH = os.getenv("TEACHER_NAME_WITH", "Анастасией")      # «с Анастасией»
TEACHER_OF = os.getenv("TEACHER_NAME_OF", "Анастасии")           # «от Анастасии», «напиши Анастасии»
BRAND = os.getenv("BRAND", f"ЕГЭ по математике с {TEACHER_WITH}")
AUTO_ACCESS = os.getenv("AUTO_ACCESS", "0") == "1"   # 0: доступ открывает Анастасия, 1: пускать всех сразу
SEED_DEMO = os.getenv("SEED_DEMO", "1") == "1"       # 1: добавить 3 демо урока при первом запуске
REMIND_DAYS = float(os.getenv("REMIND_DAYS", "3"))   # через сколько дней напомнить про домашку
NOTIFY_HOMEWORK = os.getenv("NOTIFY_HOMEWORK", "0") == "1"  # 1: присылать Анастасии каждую домашку
TZ = ZoneInfo("Europe/Moscow")
TEACHER_FULL = os.getenv("TEACHER_FULL", f"ИП {TEACHER}")          # оператор данных для политики
PRIVACY_URL = os.getenv("PRIVACY_URL", "")                          # ссылка на политику, если есть на сайте
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "")                    # прокси для связи с Телеграмом

logging.basicConfig(level=logging.INFO)
router = Router()
locks: dict[int, asyncio.Lock] = {}

BTN_MY = "📚 Мои уроки"
BTN_ADD = "➕ Добавить урок"
BTN_LESSONS = "📚 Уроки"
BTN_STUDENTS = "👥 Ученики"
BTN_AS_STUDENT = "👀 Как видит ученик"
MENU_BUTTONS = {BTN_MY, BTN_ADD, BTN_LESSONS, BTN_STUDENTS, BTN_AS_STUDENT}

STUDENT_KB = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_MY)]], resize_keyboard=True)
ADMIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_LESSONS)],
        [KeyboardButton(text=BTN_STUDENTS), KeyboardButton(text=BTN_AS_STUDENT)],
    ],
    resize_keyboard=True,
)


class HW(StatesGroup):
    collecting = State()


class AddL(StatesGroup):
    title = State()
    video = State()
    task = State()
    review = State()


class EditL(StatesGroup):
    value = State()


# ================== ПОМОЩНИКИ ==================
def esc(t):
    return html.escape(t or "")


def kb(*rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


def fmt(ts, with_time=True):
    if not ts:
        return "нет"
    d = datetime.fromtimestamp(ts, TZ)
    return d.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def is_admin(uid):
    u = db.get_user(uid)
    return bool(u and u["is_admin"])


def has_access(uid):
    u = db.get_user(uid)
    return bool(u and (u["is_admin"] or u["status"] == "active"))


def display_name(u):
    name = esc(u["name"] or "Без имени")
    return f"{name} @{esc(u['username'])}" if u["username"] else name


def media_of(msg: Message):
    """Достаём видео из сообщения: видео, кружок или файл."""
    if msg.video:
        return msg.video.file_id, "video"
    if msg.video_note:
        return msg.video_note.file_id, "video_note"
    if msg.document:
        return msg.document.file_id, "document"
    return None, None


async def send_media(bot: Bot, chat_id, file_id, kind, caption=None, markup=None):
    common = dict(caption=caption, reply_markup=markup, protect_content=True)
    if kind == "video":
        return await bot.send_video(chat_id, file_id, **common)
    if kind == "document":
        return await bot.send_document(chat_id, file_id, **common)
    if kind == "video_note":
        await bot.send_video_note(chat_id, file_id, protect_content=True)
        if caption or markup:
            return await bot.send_message(chat_id, caption or "👆", reply_markup=markup)
        return None
    return await bot.send_photo(chat_id, file_id, **common)


async def send_cover(bot, chat_id, cache_key, make, caption, markup=None, lesson_id=None, column=None):
    """Отправляем обложку. Первый раз рисуем, потом шлём сохранённую из Телеграма."""
    file_id = None
    if lesson_id:
        file_id = db.lesson(lesson_id)[column]
    else:
        file_id = db.kv_get(cache_key)
    if file_id:
        try:
            return await bot.send_photo(chat_id, file_id, caption=caption, reply_markup=markup, protect_content=True)
        except Exception:
            file_id = None
    data = await asyncio.to_thread(make)
    msg = await bot.send_photo(
        chat_id, BufferedInputFile(data, "cover.png"), caption=caption, reply_markup=markup, protect_content=True
    )
    new_id = msg.photo[-1].file_id
    if lesson_id:
        db.update_lesson(lesson_id, **{column: new_id})
    else:
        db.kv_set(cache_key, new_id)
    return msg


# ================== УРОКИ ДЛЯ УЧЕНИКА ==================
async def send_finish(bot, uid):
    await send_cover(
        bot, uid, "finish_cover", lambda: covers.finish_cover(BRAND),
        "<b>Ты прошёл все уроки, которые сейчас есть 🎉</b>\n\n"
        f"{TEACHER} уже готовит следующие. Как только появится новый урок, я сразу его пришлю 🌿",
    )


async def send_lesson(bot: Bot, uid: int, idx: int):
    l = db.lesson_by_idx(idx)
    if not l:
        return await send_finish(bot, uid)
    u = db.get_user(uid)
    done = db.has_submitted(uid, l["id"])
    is_current = u and u["current_idx"] == idx

    head = f"<b>Урок {idx}. {esc(l['title'])}</b>"
    task = f"📝 <b>Домашнее задание</b>\n\n{esc(l['task_text'])}" if l["task_text"] else "📝 <b>Домашнее задание</b>\n\nЗадание к уроку смотри в видео 👆"
    if done:
        hint = "✅ Домашка по этому уроку уже сдана"
        rows = [[("🎬 Показать разбор", f"rv:{l['id']}")]]
        if is_current:
            rows.append([("➡️ Следующий урок", f"next:{l['id']}")])
    else:
        hint = "Посмотри урок, реши задание и сдай его кнопкой ниже 👇"
        rows = [[("📝 Сдать домашку", f"hw:{l['id']}")]]
    markup = kb(*rows)

    full = f"{head}\n\n{task}\n\n{hint}"
    fits = len(full) <= 1000 and not l["task_photo"]

    # 1. Видео урока (или красивая обложка, пока видео нет)
    cap = full if fits else head
    mk = markup if fits else None
    if l["video_id"]:
        await send_media(bot, uid, l["video_id"], l["video_kind"], cap, mk)
    else:
        await send_cover(
            bot, uid, None, lambda: covers.lesson_cover(idx, l["title"], BRAND), cap, mk,
            lesson_id=l["id"], column="cover_id",
        )
    if fits:
        return
    # 2. Задание отдельно, если длинное или с картинкой
    text = f"{task}\n\n{hint}"
    if l["task_photo"]:
        if len(text) <= 1000:
            await bot.send_photo(uid, l["task_photo"], caption=text, reply_markup=markup, protect_content=True)
            return
        await bot.send_photo(uid, l["task_photo"], protect_content=True)
    await bot.send_message(uid, text, reply_markup=markup, protect_content=True)


async def send_review(bot: Bot, uid: int, lid: int, just_submitted=False):
    l = db.lesson(lid)
    if not l:
        return
    idx = db.idx_of(lid)
    u = db.get_user(uid)
    top = "✅ <b>Домашка принята!</b>\n\n" if just_submitted else ""
    cap = (
        f"{top}<b>Разбор урока {idx}. {esc(l['title'])}</b>\n\n"
        "Сверь своё решение с разбором. Нашёл ошибку? Пересмотри этот момент и реши ещё раз, "
        "так тема запомнится намного лучше 💚"
    )
    markup = kb([("➡️ Следующий урок", f"next:{lid}")]) if u and u["current_idx"] == idx else None
    if l["review_id"]:
        await send_media(bot, uid, l["review_id"], l["review_kind"], cap, markup)
    else:
        await send_cover(
            bot, uid, None, lambda: covers.review_cover(idx, l["title"], BRAND), cap, markup,
            lesson_id=lid, column="review_cover_id",
        )


async def send_welcome(bot: Bot, uid: int, first_name: str):
    await send_cover(
        bot, uid, "welcome_cover", lambda: covers.welcome_cover(BRAND, TEACHER_WITH),
        f"<b>Привет, {esc(first_name)}! Добро пожаловать в клуб 🌿</b>\n\n"
        "Как здесь всё устроено:\n\n"
        "1️⃣ Я присылаю видеоурок\n"
        "2️⃣ Ты решаешь домашку и присылаешь мне фото или текст\n"
        f"3️⃣ Сразу получаешь видеоразбор от {TEACHER_OF} и сверяешь решение\n"
        "4️⃣ Переходишь к следующему уроку\n\n"
        "Все пройденные уроки всегда лежат в кнопке «📚 Мои уроки» внизу.",
    )
    await bot.send_message(uid, "Начинаем с первого урока 👇", reply_markup=STUDENT_KB)


# ================== СТАРТ ==================
@router.message(CommandStart())
async def start(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    uid, name, username = msg.from_user.id, msg.from_user.full_name, msg.from_user.username
    u = db.get_user(uid)

    if not u:
        if not db.has_admins():
            # Первый, кто запустил бота, становится админом
            db.add_user(uid, name, username, "active", is_admin=1)
            db.update_user(uid, consent_at=db.now())
            return await send_admin_home(msg, bot, first=True)
        # Ничего не сохраняем, пока ученик не дал согласие на обработку данных
        return await send_consent(msg)

    db.touch(uid, name, username)
    if u["is_admin"]:
        return await send_admin_home(msg, bot)
    if u["status"] == "pending":
        return await msg.answer(f"Твоя заявка уже у {TEACHER_OF} 🌿 Как только доступ откроется, я пришлю первый урок.")
    if u["status"] == "blocked":
        return await msg.answer(f"Доступ к урокам сейчас закрыт. Если это ошибка, напиши {TEACHER_OF}.")
    await msg.answer("С возвращением! Продолжаем с того места, где ты остановился 👇", reply_markup=STUDENT_KB)
    await send_lesson(bot, uid, u["current_idx"])


# ================== СОГЛАСИЕ НА ОБРАБОТКУ ДАННЫХ ==================
PRIVACY_TEXT = (
    "<b>Политика обработки персональных данных</b>\n\n"
    f"Оператор: {TEACHER_FULL}.\n\n"
    "Какие данные собираются: имя и юзернейм в Телеграме, Телеграм ID, "
    "домашние задания (фото и текст), даты прохождения уроков.\n\n"
    "Зачем: чтобы присылать уроки, принимать домашние задания, вести учёт прогресса "
    "и исполнять условия гарантии.\n\n"
    "Где хранятся: на сервере, расположенном в России. Третьим лицам не передаются.\n\n"
    f"Как удалить данные: напиши {TEACHER_OF}, и все твои данные будут удалены. "
    "Согласие можно отозвать в любой момент."
)


async def send_consent(msg: Message):
    rows = [[("✅ Согласен, начать", "agree")]]
    if PRIVACY_URL:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📄 Политика конфиденциальности", url=PRIVACY_URL)],
            [InlineKeyboardButton(text="✅ Согласен, начать", callback_data="agree")],
        ])
    else:
        markup = kb([("📄 Политика конфиденциальности", "privacy")], *rows)
    await msg.answer(
        f"<b>Привет, {esc(msg.from_user.first_name)}! 🌿</b>\n\n"
        f"Это клуб подготовки к ЕГЭ по математике с {TEACHER_WITH}.\n\n"
        "Чтобы присылать тебе уроки и принимать домашки, мне нужно сохранить твоё имя "
        "и решения. Нажимая «Согласен», ты даёшь согласие на обработку персональных данных "
        "по политике конфиденциальности.\n\n"
        "<i>Если тебе нет 18 лет, покажи это сообщение родителям 🙏</i>",
        reply_markup=markup,
    )


@router.callback_query(F.data == "privacy")
async def privacy(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer(PRIVACY_TEXT, reply_markup=kb([("✅ Согласен, начать", "agree")]))


@router.message(Command("privacy"))
async def privacy_cmd(msg: Message):
    if PRIVACY_URL:
        return await msg.answer(f"Политика конфиденциальности: {PRIVACY_URL}")
    await msg.answer(PRIVACY_TEXT)


@router.callback_query(F.data == "agree")
async def agree(cb: CallbackQuery, bot: Bot):
    fu = cb.from_user
    await cb.answer("Спасибо! 🌿")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if db.get_user(fu.id):
        return
    uid = fu.id
    db.add_user(uid, fu.full_name, fu.username, "active" if AUTO_ACCESS else "pending")
    db.update_user(uid, consent_at=db.now())
    u = db.get_user(uid)
    if u["status"] == "pending":
        await bot.send_message(
            uid,
            f"Я передала твою заявку {TEACHER_OF} 🌿\n\n"
            "Как только доступ откроется, я сразу пришлю первый урок.",
        )
        for a in db.admins():
            try:
                await bot.send_message(
                    a, f"🙋 <b>Новая заявка в клуб</b>\n\n{display_name(u)}",
                    reply_markup=kb([("✅ Открыть доступ", f"ap:{uid}"), ("✖️ Отклонить", f"rj:{uid}")]),
                )
            except Exception:
                pass
        return
    await send_welcome(bot, uid, fu.first_name)
    await send_lesson(bot, uid, 1)


@router.message(Command("myid"))
async def myid(msg: Message):
    await msg.answer(f"Твой ID: <code>{msg.from_user.id}</code>")


# ================== МЕНЮ УЧЕНИКА ==================
@router.message(F.text == BTN_MY)
async def my_lessons(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    if not has_access(uid):
        return await msg.answer("Уроки откроются, как только у тебя будет доступ к клубу 🌿")
    db.touch(uid)
    u = db.get_user(uid)
    total = db.lessons_count()
    upto = min(u["current_idx"], total)
    if upto < 1:
        return await msg.answer("Уроки скоро появятся 🌿")
    rows = []
    for i in range(1, upto + 1):
        l = db.lesson_by_idx(i)
        mark = "✅" if db.has_submitted(uid, l["id"]) else "▶️"
        rows.append([(f"{mark} {i}. {l['title'][:40]}", f"view:{i}")])
    await msg.answer(
        f"<b>Твои уроки</b>\n\nПройдено: {db.submitted_lessons_count(uid)} из {total}\n"
        "Нажми на урок, чтобы пересмотреть его 👇",
        reply_markup=kb(*rows),
    )


@router.callback_query(F.data.startswith("view:"))
async def view_lesson(cb: CallbackQuery, bot: Bot):
    uid = cb.from_user.id
    if not has_access(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    idx = int(cb.data.split(":")[1])
    u = db.get_user(uid)
    if idx > u["current_idx"]:
        return await cb.answer("Этот урок пока закрыт 🔒", show_alert=True)
    await cb.answer()
    db.touch(uid)
    await send_lesson(bot, uid, idx)


# ================== ДОМАШКА ==================
@router.callback_query(F.data.startswith("hw:"))
async def hw_start(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    if not has_access(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    lid = int(cb.data.split(":")[1])
    if not db.lesson(lid):
        return await cb.answer("Урок не найден", show_alert=True)
    if db.has_submitted(uid, lid):
        return await cb.answer("Эта домашка уже сдана ✅", show_alert=True)
    await cb.answer()
    db.touch(uid)
    await state.set_state(HW.collecting)
    await state.update_data(lesson_id=lid, items=[], prompt_id=None)
    m = await cb.message.answer(
        "📸 <b>Пришли своё решение</b>\n\n"
        "Сфотографируй тетрадь или напиши ответы текстом. Можно несколько фото или сообщений.\n\n"
        "Когда всё отправишь, нажми «Отправить» 👇",
        reply_markup=kb([("✅ Отправить домашку", "hw_done")]),
    )
    await state.update_data(prompt_id=m.message_id)


@router.message(HW.collecting, F.photo | F.document | (F.text & ~F.text.in_(MENU_BUTTONS)))
async def hw_collect(msg: Message, state: FSMContext, bot: Bot):
    uid = msg.from_user.id
    lock = locks.setdefault(uid, asyncio.Lock())
    async with lock:
        if msg.photo:
            item = ("photo", msg.photo[-1].file_id)
        elif msg.document:
            item = ("document", msg.document.file_id)
        else:
            if msg.text.startswith("/"):
                return
            item = ("text", msg.text)
        data = await state.get_data()
        items = data.get("items", []) + [item]
        if data.get("prompt_id"):
            try:
                await bot.delete_message(uid, data["prompt_id"])
            except Exception:
                pass
        m = await msg.answer(
            f"Получено: {len(items)} 👌\nДобавь ещё или нажми «Отправить».",
            reply_markup=kb([("✅ Отправить домашку", "hw_done")]),
        )
        await state.update_data(items=items, prompt_id=m.message_id)


@router.message(HW.collecting, ~F.text.in_(MENU_BUTTONS))
async def hw_other(msg: Message):
    await msg.answer("Пришли фото решения или текст, а потом нажми «✅ Отправить домашку» 🙂")


@router.callback_query(F.data == "hw_done")
async def hw_done(cb: CallbackQuery, state: FSMContext, bot: Bot):
    uid = cb.from_user.id
    lock = locks.setdefault(uid, asyncio.Lock())
    async with lock:
        if await state.get_state() != HW.collecting.state:
            return await cb.answer("Нажми «📝 Сдать домашку» под уроком", show_alert=True)
        data = await state.get_data()
        items = data.get("items", [])
        if not items:
            return await cb.answer("Сначала пришли фото или текст с решением 🙂", show_alert=True)
        lid = data["lesson_id"]
        await state.clear()
        await cb.answer("Домашка отправлена ✅")
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        if not db.has_submitted(uid, lid):
            sid = db.save_submission(uid, lid, items)
        else:
            sid = None
        db.touch(uid)
    await send_review(bot, uid, lid, just_submitted=True)

    if NOTIFY_HOMEWORK and sid:
        u = db.get_user(uid)
        for a in db.admins():
            if a == uid:
                continue
            try:
                await bot.send_message(
                    a, f"📝 {display_name(u)} сдал(а) домашку к уроку {db.idx_of(lid)}",
                    reply_markup=kb([("Посмотреть", f"sub:{sid}")]),
                )
            except Exception:
                pass


@router.callback_query(F.data.startswith("rv:"))
async def show_review(cb: CallbackQuery, bot: Bot):
    uid = cb.from_user.id
    lid = int(cb.data.split(":")[1])
    if not has_access(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    if not db.has_submitted(uid, lid):
        return await cb.answer("Разбор откроется после того, как сдашь домашку 📝", show_alert=True)
    await cb.answer()
    await send_review(bot, uid, lid)


@router.callback_query(F.data.startswith("next:"))
async def next_lesson(cb: CallbackQuery, bot: Bot):
    uid = cb.from_user.id
    if not has_access(uid):
        return await cb.answer("Нет доступа", show_alert=True)
    lid = int(cb.data.split(":")[1])
    u = db.get_user(uid)
    idx = db.idx_of(lid)
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if idx and u["current_idx"] == idx and db.has_submitted(uid, lid):
        db.update_user(uid, current_idx=idx + 1)
    db.touch(uid)
    await send_lesson(bot, uid, db.get_user(uid)["current_idx"])


# ================== ПАНЕЛЬ АНАСТАСИИ ==================
async def send_admin_home(msg: Message, bot: Bot, first=False):
    me = await bot.get_me()
    intro = "Ты первая запустила бота, поэтому ты админ 👑\n\n" if first else ""
    await msg.answer(
        f"<b>Панель управления клубом 🌿</b>\n\n{intro}"
        "<b>➕ Добавить урок</b>: название, видео, задание и разбор\n"
        "<b>📚 Уроки</b>: посмотреть, заменить видео или текст, удалить\n"
        "<b>👥 Ученики</b>: заявки, прогресс и все сданные домашки\n"
        "<b>👀 Как видит ученик</b>: пройти бота глазами ученика\n\n"
        f"Ссылка для учеников:\nhttps://t.me/{me.username}",
        reply_markup=ADMIN_KB,
    )


@router.message(Command("admin"))
async def admin_cmd(msg: Message, state: FSMContext, bot: Bot):
    if is_admin(msg.from_user.id):
        await state.clear()
        await send_admin_home(msg, bot)


@router.message(F.text == BTN_AS_STUDENT)
async def as_student(msg: Message, state: FSMContext, bot: Bot):
    if not is_admin(msg.from_user.id):
        return
    await state.clear()
    u = db.get_user(msg.from_user.id)
    await msg.answer(
        "Так бот выглядит для ученика 👇\nПрогресс сохраняется. Начать заново: /reset",
    )
    await send_lesson(bot, msg.from_user.id, u["current_idx"])


@router.message(Command("reset"))
async def reset(msg: Message, state: FSMContext, bot: Bot):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    await state.clear()
    db.run("DELETE FROM submission_items WHERE submission_id IN (SELECT id FROM submissions WHERE user_id=?)", (uid,))
    db.run("DELETE FROM submissions WHERE user_id=?", (uid,))
    db.update_user(uid, current_idx=1)
    await msg.answer("Твой прогресс сброшен, начинаем с урока 1 👇")
    await send_welcome(bot, uid, msg.from_user.first_name)
    await msg.answer("Чтобы вернуться в панель управления: /admin")
    await send_lesson(bot, uid, 1)


# ---------- добавление урока ----------
@router.message(F.text == BTN_ADD)
async def add_start(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    await state.clear()
    await state.set_state(AddL.title)
    n = db.lessons_count() + 1
    await msg.answer(f"<b>Новый урок {n}</b>\n\nШаг 1 из 4. Напиши название урока.\n\nНапример: <i>Задание 1. Планиметрия</i>")


@router.message(AddL.title, F.text & ~F.text.in_(MENU_BUTTONS))
async def add_title(msg: Message, state: FSMContext):
    await state.update_data(title=msg.text.strip()[:120])
    await state.set_state(AddL.video)
    await msg.answer(
        "Шаг 2 из 4. Пришли <b>видео урока</b>.\n\nЕсли видео ещё не снято, нажми «Пропустить». "
        "Вместо него будет красивая обложка, а видео добавишь потом.",
        reply_markup=kb([("Пропустить, добавлю позже", "skip")]),
    )


@router.message(AddL.video, F.video | F.document | F.video_note)
async def add_video(msg: Message, state: FSMContext):
    fid, kind = media_of(msg)
    await state.update_data(video_id=fid, video_kind=kind)
    await ask_task(msg, state)


async def ask_task(msg: Message, state: FSMContext):
    await state.set_state(AddL.task)
    await msg.answer(
        "Шаг 3 из 4. Пришли <b>домашнее задание</b>.\n\nМожно текстом или картинкой с подписью.",
    )


@router.message(AddL.task, F.photo | (F.text & ~F.text.in_(MENU_BUTTONS)))
async def add_task(msg: Message, state: FSMContext):
    if msg.photo:
        await state.update_data(task_photo=msg.photo[-1].file_id, task_text=msg.caption or "")
    else:
        await state.update_data(task_text=msg.text, task_photo=None)
    await state.set_state(AddL.review)
    await msg.answer(
        "Шаг 4 из 4. Пришли <b>видеоразбор</b> этого задания.\n\nНет видео? Нажми «Пропустить».",
        reply_markup=kb([("Пропустить, добавлю позже", "skip")]),
    )


@router.message(AddL.review, F.video | F.document | F.video_note)
async def add_review(msg: Message, state: FSMContext, bot: Bot):
    fid, kind = media_of(msg)
    await state.update_data(review_id=fid, review_kind=kind)
    await finish_add(msg, state, bot)


@router.callback_query(F.data == "skip")
async def add_skip(cb: CallbackQuery, state: FSMContext, bot: Bot):
    st = await state.get_state()
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if st == AddL.video.state:
        await state.update_data(video_id=None, video_kind=None)
        await ask_task(cb.message, state)
    elif st == AddL.review.state:
        await state.update_data(review_id=None, review_kind=None)
        await finish_add(cb.message, state, bot)


@router.message(AddL.video, ~F.text.in_(MENU_BUTTONS))
@router.message(AddL.review, ~F.text.in_(MENU_BUTTONS))
async def add_wrong_media(msg: Message):
    await msg.answer("Пришли видео (можно файлом) или нажми «Пропустить» 🙂")


async def finish_add(msg: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    lid = db.add_lesson(
        d["title"], d.get("video_id"), d.get("video_kind"), d.get("task_text"), d.get("task_photo"),
        d.get("review_id"), d.get("review_kind"),
    )
    idx = db.idx_of(lid)
    await msg.answer(
        f"✅ <b>Урок {idx} добавлен!</b>\n\n«{esc(d['title'])}»",
        reply_markup=kb([("👀 Посмотреть, как выглядит", f"apv:{lid}")]),
    )
    await notify_new_lesson(bot, idx)


async def notify_new_lesson(bot: Bot, idx: int):
    """Ученики, которые прошли всё и ждали, сразу получают новый урок."""
    for u in db.q("SELECT * FROM users WHERE status='active' AND is_admin=0 AND current_idx=?", (idx,)):
        try:
            await bot.send_message(u["id"], "🎉 <b>Новый урок уже в боте!</b>")
            await send_lesson(bot, u["id"], idx)
        except Exception:
            pass


# ---------- список и редактирование уроков ----------
@router.message(F.text == BTN_LESSONS)
async def lessons_list(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    await state.clear()
    ls = db.lessons()
    if not ls:
        return await msg.answer("Уроков пока нет. Нажми «➕ Добавить урок» 🌿")
    rows = []
    for i, l in enumerate(ls, start=1):
        icons = ("🎬" if l["video_id"] else "⏳") + ("🎥" if l["review_id"] else "⏳")
        rows.append([(f"{i}. {l['title'][:38]}  {icons}", f"al:{l['id']}")])
    await msg.answer(
        "<b>Все уроки</b>\n\n🎬 видео урока есть\n🎥 разбор есть\n⏳ пока обложка, видео можно добавить\n\nНажми на урок 👇",
        reply_markup=kb(*rows),
    )


def lesson_card(lid):
    l = db.lesson(lid)
    idx = db.idx_of(lid)
    text = (
        f"<b>Урок {idx}. {esc(l['title'])}</b>\n\n"
        f"Видео урока: {'есть ✅' if l['video_id'] else 'пока нет ⏳'}\n"
        f"Видеоразбор: {'есть ✅' if l['review_id'] else 'пока нет ⏳'}\n\n"
        f"<b>Задание:</b>\n{esc((l['task_text'] or 'картинкой')[:600])}"
    )
    markup = kb(
        [("✏️ Название", f"ae:{lid}:title"), ("📝 Задание", f"ae:{lid}:task")],
        [("🎬 Видео урока", f"ae:{lid}:video"), ("🎥 Разбор", f"ae:{lid}:review")],
        [("👀 Посмотреть", f"apv:{lid}"), ("🗑 Удалить", f"adel:{lid}")],
    )
    return text, markup


@router.callback_query(F.data.startswith("al:"))
async def lesson_open(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    lid = int(cb.data.split(":")[1])
    if not db.lesson(lid):
        return await cb.answer("Урок уже удалён", show_alert=True)
    await cb.answer()
    text, markup = lesson_card(lid)
    await cb.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("apv:"))
async def lesson_preview(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    lid = int(cb.data.split(":")[1])
    l = db.lesson(lid)
    if not l:
        return await cb.answer("Урок уже удалён", show_alert=True)
    await cb.answer()
    idx = db.idx_of(lid)
    await cb.message.answer("👀 <b>Так ученик увидит урок:</b>")
    if l["video_id"]:
        await send_media(bot, cb.from_user.id, l["video_id"], l["video_kind"], f"<b>Урок {idx}. {esc(l['title'])}</b>")
    else:
        await send_cover(bot, cb.from_user.id, None, lambda: covers.lesson_cover(idx, l["title"], BRAND),
                         f"<b>Урок {idx}. {esc(l['title'])}</b>", lesson_id=lid, column="cover_id")
    if l["task_photo"]:
        await bot.send_photo(cb.from_user.id, l["task_photo"], caption=esc(l["task_text"] or ""))
    elif l["task_text"]:
        await cb.message.answer(f"📝 <b>Домашнее задание</b>\n\n{esc(l['task_text'])}")
    await cb.message.answer("👀 <b>А так разбор, после сдачи домашки:</b>")
    if l["review_id"]:
        await send_media(bot, cb.from_user.id, l["review_id"], l["review_kind"], f"<b>Разбор урока {idx}</b>")
    else:
        await send_cover(bot, cb.from_user.id, None, lambda: covers.review_cover(idx, l["title"], BRAND),
                         f"<b>Разбор урока {idx}. {esc(l['title'])}</b>", lesson_id=lid, column="review_cover_id")


EDIT_PROMPTS = {
    "title": "Напиши новое название урока",
    "task": "Пришли новое задание текстом или картинкой с подписью",
    "video": "Пришли видео урока (можно файлом)",
    "review": "Пришли видеоразбор (можно файлом)",
}


@router.callback_query(F.data.startswith("ae:"))
async def lesson_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    _, lid, field = cb.data.split(":")
    await cb.answer()
    await state.set_state(EditL.value)
    await state.update_data(lid=int(lid), field=field)
    await cb.message.answer(EDIT_PROMPTS[field] + " 👇")


@router.message(EditL.value, ~F.text.in_(MENU_BUTTONS))
async def lesson_edit_value(msg: Message, state: FSMContext):
    d = await state.get_data()
    lid, field = d["lid"], d["field"]
    if not db.lesson(lid):
        await state.clear()
        return await msg.answer("Урок уже удалён")
    if field == "title":
        if not msg.text:
            return await msg.answer("Напиши название текстом 🙂")
        db.update_lesson(lid, title=msg.text.strip()[:120], cover_id=None, review_cover_id=None)
    elif field == "task":
        if msg.photo:
            db.update_lesson(lid, task_photo=msg.photo[-1].file_id, task_text=msg.caption or "")
        elif msg.text:
            db.update_lesson(lid, task_text=msg.text, task_photo=None)
        else:
            return await msg.answer("Пришли текст или картинку 🙂")
    else:
        fid, kind = media_of(msg)
        if not fid:
            return await msg.answer("Нужно видео или файл с видео 🙂")
        if field == "video":
            db.update_lesson(lid, video_id=fid, video_kind=kind)
        else:
            db.update_lesson(lid, review_id=fid, review_kind=kind)
    await state.clear()
    text, markup = lesson_card(lid)
    await msg.answer("✅ Сохранено!\n\n" + text, reply_markup=markup)


@router.callback_query(F.data.startswith("adel:"))
async def lesson_delete_ask(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    lid = int(cb.data.split(":")[1])
    await cb.answer()
    await cb.message.answer(
        "Точно удалить этот урок? Ученики больше не увидят его.",
        reply_markup=kb([("🗑 Да, удалить", f"adelok:{lid}"), ("Отмена", "noop")]),
    )


@router.callback_query(F.data.startswith("adelok:"))
async def lesson_delete(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    db.delete_lesson(int(cb.data.split(":")[1]))
    await cb.answer("Урок удалён")
    await cb.message.edit_text("🗑 Урок удалён")


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery):
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass


# ---------- ученики ----------
STATUS_TXT = {"active": "в клубе ✅", "pending": "ждёт доступа 🙋", "blocked": "доступ закрыт 🔒"}


@router.message(F.text == BTN_STUDENTS)
async def students_list(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    await state.clear()
    st = db.students()
    if not st:
        me = await msg.bot.get_me()
        return await msg.answer(
            f"Учеников пока нет 🌿\n\nОтправь ученикам ссылку на бота:\nhttps://t.me/{me.username}"
        )
    total = db.lessons_count()
    rows = []
    for u in st[:80]:
        icon = {"active": "🟢", "pending": "🙋", "blocked": "🔒"}[u["status"]]
        done = db.submitted_lessons_count(u["id"])
        rows.append([(f"{icon} {(u['name'] or 'Без имени')[:22]}  ·  ДЗ {done}/{total}", f"st:{u['id']}")])
    act = len([u for u in st if u["status"] == "active"])
    pen = len([u for u in st if u["status"] == "pending"])
    await msg.answer(
        f"<b>Ученики</b>\n\nВ клубе: {act}\nЖдут доступа: {pen}\n\nНажми на ученика, чтобы увидеть прогресс и домашки 👇",
        reply_markup=kb(*rows),
    )


def student_card(uid):
    u = db.get_user(uid)
    total = db.lessons_count()
    subs = db.submissions_of(uid)
    hist = "\n".join(
        f"• Урок {db.idx_of(s['lesson_id']) or '?'}: {fmt(s['submitted_at'])}" for s in subs[-15:]
    ) or "пока ничего"
    text = (
        f"<b>{display_name(u)}</b>\n\n"
        f"Статус: {STATUS_TXT[u['status']]}\n"
        f"В клубе с: {fmt(u['approved_at'] or u['created_at'], False)}\n"
        f"Сейчас на уроке: {min(u['current_idx'], total) if total else 0} из {total}\n"
        f"Сдано домашек: {db.submitted_lessons_count(uid)} из {total}\n"
        f"Последняя активность: {fmt(u['last_activity'])}\n\n"
        f"<b>Когда сдавал ДЗ:</b>\n{hist}"
    )
    rows = []
    if subs:
        rows.append([("📂 Посмотреть домашки", f"subs:{uid}")])
    if u["status"] == "pending":
        rows.append([("✅ Открыть доступ", f"ap:{uid}"), ("✖️ Отклонить", f"rj:{uid}")])
    elif u["status"] == "active":
        rows.append([("🔒 Закрыть доступ", f"blk:{uid}")])
    else:
        rows.append([("🔓 Открыть доступ", f"ap:{uid}")])
    rows.append([("👑 Сделать админом", f"mkadm:{uid}"), ("🗑 Удалить данные", f"udel:{uid}")])
    return text, kb(*rows)


@router.callback_query(F.data.startswith("st:"))
async def student_open(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    await cb.answer()
    text, markup = student_card(int(cb.data.split(":")[1]))
    await cb.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ap:"))
async def approve(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    uid = int(cb.data.split(":")[1])
    u = db.get_user(uid)
    was = u["status"]
    db.update_user(uid, status="active", approved_at=u["approved_at"] or db.now())
    await cb.answer("Доступ открыт ✅")
    text, markup = student_card(uid)
    await cb.message.edit_text(text, reply_markup=markup)
    try:
        if was == "pending":
            first = (u["name"] or "").split(" ")[0] or "друг"
            await send_welcome(bot, uid, first)
        else:
            await bot.send_message(uid, "🔓 Доступ к урокам снова открыт! Продолжаем 👇", reply_markup=STUDENT_KB)
        await send_lesson(bot, uid, db.get_user(uid)["current_idx"])
    except Exception:
        pass


@router.callback_query(F.data.startswith("rj:"))
async def reject(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    uid = int(cb.data.split(":")[1])
    db.update_user(uid, status="blocked")
    await cb.answer("Заявка отклонена")
    text, markup = student_card(uid)
    await cb.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("blk:"))
async def block(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    uid = int(cb.data.split(":")[1])
    db.update_user(uid, status="blocked")
    await cb.answer("Доступ закрыт 🔒")
    text, markup = student_card(uid)
    await cb.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("mkadm:"))
async def make_admin(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    uid = int(cb.data.split(":")[1])
    db.update_user(uid, is_admin=1, status="active")
    await cb.answer("Готово 👑")
    await cb.message.answer("👑 Теперь это админ клуба. У него появится панель управления после /start")
    try:
        await bot.send_message(uid, "👑 Тебе выдали доступ к панели управления клубом. Нажми /start")
    except Exception:
        pass


@router.callback_query(F.data.startswith("udel:"))
async def user_delete_ask(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    uid = int(cb.data.split(":")[1])
    await cb.answer()
    await cb.message.answer(
        "Удалить ученика и все его данные (прогресс и домашки)? Вернуть будет нельзя.",
        reply_markup=kb([("🗑 Да, удалить", f"udelok:{uid}"), ("Отмена", "noop")]),
    )


@router.callback_query(F.data.startswith("udelok:"))
async def user_delete(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    db.delete_user(int(cb.data.split(":")[1]))
    await cb.answer("Удалено")
    await cb.message.edit_text("🗑 Ученик и все его данные удалены")


@router.callback_query(F.data.startswith("subs:"))
async def subs_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    uid = int(cb.data.split(":")[1])
    subs = db.submissions_of(uid)
    await cb.answer()
    rows = [[(f"Урок {db.idx_of(s['lesson_id']) or '?'}  ·  {fmt(s['submitted_at'])}", f"sub:{s['id']}")] for s in subs[-40:]]
    await cb.message.answer("<b>Сданные домашки</b>\nНажми, чтобы открыть 👇", reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("sub:"))
async def sub_open(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id):
        return await cb.answer()
    sid = int(cb.data.split(":")[1])
    s = db.submission(sid)
    if not s:
        return await cb.answer("Не найдено", show_alert=True)
    await cb.answer()
    u = db.get_user(s["user_id"])
    await cb.message.answer(
        f"📂 <b>{display_name(u)}</b>\nУрок {db.idx_of(s['lesson_id']) or '?'}, сдано {fmt(s['submitted_at'])}"
    )
    for it in db.submission_items(sid):
        try:
            if it["kind"] == "photo":
                await bot.send_photo(cb.from_user.id, it["content"])
            elif it["kind"] == "document":
                await bot.send_document(cb.from_user.id, it["content"])
            else:
                await bot.send_message(cb.from_user.id, esc(it["content"]))
        except Exception:
            pass


# ---------- всё остальное ----------
@router.message()
async def fallback(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    u = db.get_user(uid)
    if not u:
        return await msg.answer("Нажми /start, чтобы начать 🌿")
    if u["is_admin"]:
        return await msg.answer("Выбери действие в меню внизу 👇", reply_markup=ADMIN_KB)
    if u["status"] != "active":
        return await msg.answer(f"Доступ к урокам откроет {TEACHER} 🌿")
    l = db.lesson_by_idx(u["current_idx"])
    if l and not db.has_submitted(uid, l["id"]):
        return await msg.answer(
            "Чтобы сдать домашку, нажми кнопку «📝 Сдать домашку» 🙂",
            reply_markup=kb([("📝 Сдать домашку", f"hw:{l['id']}")]),
        )
    await msg.answer("Все твои уроки в кнопке «📚 Мои уроки» внизу 👇", reply_markup=STUDENT_KB)


# ================== НАПОМИНАНИЯ ==================
async def reminders(bot: Bot):
    while True:
        await asyncio.sleep(3600)
        try:
            border = db.now() - int(REMIND_DAYS * 86400)
            for u in db.q(
                "SELECT * FROM users WHERE status='active' AND is_admin=0 AND reminded=0 AND last_activity<?",
                (border,),
            ):
                l = db.lesson_by_idx(u["current_idx"])
                if not l or db.has_submitted(u["id"], l["id"]):
                    continue
                try:
                    await bot.send_message(
                        u["id"],
                        f"Привет! 🌿 Ты остановился на уроке {u['current_idx']} «{esc(l['title'])}».\n\n"
                        "Давай доделаем домашку, это займёт минут 20, а до ЕГЭ станет на шаг ближе 💚",
                        reply_markup=kb([("▶️ Продолжить", f"view:{u['current_idx']}")]),
                    )
                except Exception:
                    pass
                db.update_user(u["id"], reminded=1)
        except Exception as e:
            logging.exception("reminders: %s", e)


# ================== ДЕМО УРОКИ ==================
DEMO = [
    ("Задание 1. Планиметрия: треугольники и углы",
     "1. В треугольнике ABC угол C равен 90°, AC = 6, BC = 8. Найдите AB.\n"
     "2. В равнобедренном треугольнике угол при вершине равен 40°. Найдите угол при основании.\n"
     "3. Найдите площадь прямоугольного треугольника с катетами 5 и 12."),
    ("Задание 6. Простейшие уравнения",
     "1. Решите уравнение 3x + 7 = 22.\n"
     "2. Решите уравнение x² = 49. В ответе укажите больший корень.\n"
     "3. Решите уравнение log₂(x + 3) = 4."),
    ("Задание 12. Производная и экстремумы",
     "1. Найдите производную функции y = 5x⁴ + 2x.\n"
     "2. Найдите точку минимума функции y = x³ + 3x² + 5.\n"
     "3. Найдите наибольшее значение функции y = 4x + 3 на отрезке [1; 5]."),
]


def seed():
    if SEED_DEMO and not db.kv_get("seeded") and db.lessons_count() == 0:
        for title, task in DEMO:
            db.add_lesson(title, None, None, task, None, None, None)
        db.kv_set("seeded", "1")


async def main():
    if not TOKEN:
        raise SystemExit("Не задан BOT_TOKEN. Добавь его в переменные окружения.")
    db.init()
    seed()
    session = AiohttpSession(proxy=TELEGRAM_PROXY) if TELEGRAM_PROXY else None
    bot = Bot(TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command="start", description="Начать / продолжить")])
    asyncio.create_task(reminders(bot))
    logging.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
