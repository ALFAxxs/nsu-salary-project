"""
Telegram bot (aiogram 3.x) — runs as a standalone process.

Start with:  python -m apps.telegram_bot.bot   (after DJANGO_SETTINGS_MODULE is set)
or via the management command:  python manage.py run_bot

Handles (spec §8, §20-22, §42, §43):
  /start, /restart -> ask for contact -> ask for JSHSHIR -> link account
             (both must match the SAME on-file Employee, or nothing is
             linked); /restart is just /start again, for anyone who gets
             stuck mid-flow. Both, plus /salary, /history, /profile, /help,
             are registered with Telegram's own "/" command menu (see
             BOT_COMMANDS, set via bot.set_my_commands in main()).
  menu     -> Joriy oylik / Oyliklar tarixi / Profil / Yordam

An employee only ever sees their own data (keyed by telegram_id).
"""
from __future__ import annotations

import asyncio
import logging
import os

import django

# Configure Django before importing anything that touches the ORM.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
django.setup()

from aiogram import Bot, Dispatcher, F  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.filters import Command  # noqa: E402
from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.state import State, StatesGroup  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import (  # noqa: E402
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from django.conf import settings  # noqa: E402

from apps.common.jshshir import is_valid_jshshir, normalize_jshshir  # noqa: E402
from apps.common.money import format_money as _fmt  # noqa: E402
from apps.salaries.models import BREAKDOWN_FIELDS  # noqa: E402
from apps.telegram_bot import data  # noqa: E402

logger = logging.getLogger("apps.telegram_bot")


class LinkStates(StatesGroup):
    """Two-step /start verification: contact-share, then typed JSHSHIR."""
    waiting_for_jshshir = State()


# --- Keyboards ------------------------------------------------------------- #
CONTACT_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📱 Telefon raqamimni yuborish", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

MENU_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💰 Joriy oylik"), KeyboardButton(text="📊 Oyliklar tarixi")],
        [KeyboardButton(text="👤 Profilim"), KeyboardButton(text="❓ Yordam")],
    ],
    resize_keyboard=True,
)

MONTH_NAMES = [
    "", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
    "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
]

# --- Personal-data processing consent (spec: shaxsga doir ma'lumotlarni
# qayta ishlashga rozilik) — shown once, before /start ever asks for phone
# or JSHSHIR. Wording is exact as approved; do not paraphrase. ---------- #
CONSENT_INTRO_TEXT = (
    "Assalomu alaykum!\n"
    "Botdan foydalanishni boshlashdan oldin shaxsga doir ma’lumotlarni qayta ishlash "
    "shartlari bilan tanishib chiqing.\n"
    "Bot quyidagi ma’lumotlarni qayta ishlashi mumkin:\n"
    "Telefon raqami;\n"
    "PINFL/JSHSHIR;\n"
    "Oylik daromad va boshqa moliyaviy ma’lumotlar;\n"
    "Botdan foydalanish uchun zarur texnik ma’lumotlar.\n"
    "Ma’lumotlar foydalanuvchini identifikatsiya qilish, bot xizmatlarini taqdim etish "
    "va foydalanuvchi so‘rovlariga muvofiq moliyaviy hisob-kitoblarni amalga oshirish "
    "maqsadida qayta ishlanadi.\n"
    "To‘liq rozilik matni bilan tanishib chiqing. Rozilik berish ixtiyoriy bo‘lib, "
    "ma’lumotlardan foydalanish ko‘rsatilgan maqsadlar bilan cheklanadi."
)

CONSENT_FULL_TEXT = (
    "SHAXSGA DOIR MA’LUMOTLARNI QAYTA ISHLASHGA ROZILIK\n\n"
    "Men, Telegram botdan foydalanuvchi jismoniy shaxs sifatida, mazkur rozilik matni "
    "bilan tanishib chiqib, uning mazmunini tushungan holda, o‘z xohish-irodam bilan va "
    "ixtiyoriy ravishda o‘zimga tegishli shaxsga doir ma’lumotlarni Telegram bot orqali "
    "qayta ishlashga rozilik beraman.\n\n"
    "1. Qayta ishlanadigan shaxsga doir ma’lumotlar\n"
    "Men Telegram botdan foydalanish jarayonida quyidagi shaxsga doir ma’lumotlarim "
    "qayta ishlanishiga rozilik beraman:\n"
    "- telefon raqamim;\n"
    "- jismoniy shaxsning shaxsiy identifikatsiya raqami (PINFL/JSHSHIR);\n"
    "- oylik daromadim hamda boshqa moliyaviy ma’lumotlarim;\n"
    "- Telegram akkauntimga oid botdan foydalanish uchun zarur bo‘lgan texnik "
    "ma’lumotlar;\n"
    "- bot xizmatlaridan foydalanish jarayonida o‘zim tomonidan taqdim etilgan, ushbu "
    "xizmatlarni ko‘rsatish uchun zarur bo‘lgan boshqa ma’lumotlar.\n\n"
    "2. Shaxsga doir ma’lumotlardan foydalanish maqsadlari\n"
    "Men o‘zimga tegishli yuqorida ko‘rsatilgan shaxsga doir ma’lumotlardan quyidagi "
    "maqsadlarda foydalanilishiga rozilik beraman:\n"
    "1. Meni ro‘yxatdan o‘tkazish va shaxsni identifikatsiya qilish;\n"
    "2. Telegram bot orqali xizmatlarni taqdim etish;\n"
    "3. Mening so‘rovimga asosan daromad va boshqa moliyaviy ma’lumotlarimni qayta "
    "ishlash, hisob-kitoblarni amalga oshirish hamda tegishli axborotlarni "
    "shakllantirish;\n"
    "4. Telegram botning ishlashini ta’minlash, texnik nosozliklarni aniqlash va "
    "bartaraf etish;\n"
    "5. Axborot xavfsizligini ta’minlash va ruxsatsiz foydalanish holatlarining oldini "
    "olish;\n"
    "6. O‘zbekiston Respublikasi qonunchiligida nazarda tutilgan boshqa qonuniy "
    "maqsadlarda.\n\n"
    "3. Mening huquq va majburiyatlarim\n"
    "Men Telegram botga taqdim etayotgan shaxsga doir ma’lumotlarimning to‘g‘riligi, "
    "haqqoniyligi va dolzarbligi uchun javob beraman.\n"
    "Men o‘z telefon raqamim, Telegram akkauntim, tasdiqlash kodlari, parollar va "
    "boshqa maxfiy ma’lumotlarimni begona shaxslarga bermaslik hamda ulardan xavfsiz "
    "foydalanish choralarini ko‘rishim lozimligini tushunaman.\n"
    "Men o‘zimga tegishli shaxsga doir ma’lumotlarni qayta ishlashga berilgan "
    "roziligimni qonunchilikda belgilangan tartibda qaytarib olish, o‘zim haqimda "
    "qayta ishlanayotgan shaxsga doir ma’lumotlar to‘g‘risida axborot olish, noto‘g‘ri "
    "yoki eskirgan ma’lumotlarni tuzatishni talab qilish hamda qonunchilikda nazarda "
    "tutilgan boshqa huquqlardan foydalanish huquqiga ega ekanligimni bilaman.\n\n"
    "4. Shaxsga doir ma’lumotlarni himoya qilish\n"
    "Men shaxsga doir ma’lumotlarim qonunchilikda belgilangan talablar asosida himoya "
    "qilinishini tushunaman.\n"
    "Shuningdek, o‘z aybim yoki ehtiyotsizligim natijasida shaxsga doir "
    "ma’lumotlarimni uchinchi shaxslarga oshkor qilishim, Telegram akkauntimga, "
    "tasdiqlash kodlarimga yoki boshqa maxfiy ma’lumotlarimga begona shaxslarning "
    "kirishiga yo‘l qo‘yishim natijasida yuzaga kelishi mumkin bo‘lgan oqibatlar uchun "
    "qonunchilikda belgilangan tartibda javobgar bo‘lishim mumkinligini tushunaman.\n"
    "Mazkur rozilik bot egasining shaxsga doir ma’lumotlarni qonunchilik talablariga "
    "muvofiq himoya qilish, ularning maxfiyligini ta’minlash va ulardan qonuniy "
    "foydalanish bo‘yicha majburiyatlarini bekor qilmasligini tushunaman.\n\n"
    "5. Rozilik berish\n"
    "Men ushbu rozilik matnini to‘liq o‘qib chiqdim va uning mazmunini tushundim.\n"
    "Men Telegram botdan foydalanish jarayonida o‘zimga tegishli telefon raqami, "
    "PINFL/JSHSHIR, oylik daromad va boshqa moliyaviy ma’lumotlarimni yuqorida "
    "ko‘rsatilgan maqsadlarda qayta ishlashga ixtiyoriy ravishda rozilik beraman."
)

CONSENT_INTRO_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📄 To'liq tanishish", callback_data="consent:full")]
])
CONSENT_DECISION_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="✅ Roziman", callback_data="consent:agree")],
    [InlineKeyboardButton(text="❌ Rad etaman", callback_data="consent:decline")],
])

dp = Dispatcher(storage=MemoryStorage())


def _years_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(y), callback_data=f"hist:year:{y}")]
        for y in years
    ])


def _months_keyboard(year: int, months: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"{m}-{MONTH_NAMES[m]}", callback_data=f"hist:month:{year}:{m}")
        for m in months
    ]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="⬅️ Yillarga qaytish", callback_data="hist:years")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- /start ------------------------------------------------------------ #
@dp.message(Command("start"))
@dp.message(Command("restart"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()  # /start always resets any half-finished verification
    emp = await data.get_employee(message.from_user.id)
    if emp:
        await message.answer(
            f"Assalomu alaykum, {emp['full_name']}! Menyudan foydalaning.",
            reply_markup=MENU_KB,
        )
        return
    if not await data.has_consent(message.from_user.id):
        # Gate everything else behind the consent text — asked once, not
        # re-shown on every /start once agreed (see consent_agree below).
        await message.answer(CONSENT_INTRO_TEXT, reply_markup=CONSENT_INTRO_KB)
        return
    await message.answer(
        "Oylik ish haqingiz haqida ma'lumot olish uchun telefon raqamingizni tasdiqlang.",
        reply_markup=CONTACT_KB,
    )


@dp.callback_query(F.data == "consent:full")
async def consent_show_full(callback: CallbackQuery):
    # A separate message, not an edit — the short intro stays visible above it.
    await callback.message.answer(CONSENT_FULL_TEXT, reply_markup=CONSENT_DECISION_KB)
    await callback.answer()


@dp.callback_query(F.data == "consent:agree")
async def consent_agree(callback: CallbackQuery):
    await data.record_consent(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=None)  # buttons no longer clickable
    await callback.message.answer(
        "Rahmat! Endi telefon raqamingizni tasdiqlang.",
        reply_markup=CONTACT_KB,
    )
    await callback.answer("Rozilik qabul qilindi.")


@dp.callback_query(F.data == "consent:decline")
async def consent_decline(callback: CallbackQuery, state: FSMContext):
    # No consent recorded -> the bot cannot be used at all (spec: rad etsa
    # botdan foydalana olmasin). Send them back to the very start of the
    # consent flow instead of leaving them stuck with no way forward.
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)  # buttons no longer clickable
    await callback.message.answer(
        "Siz shaxsga doir ma'lumotlarni qayta ishlashga rozilik bermadingiz. "
        "Botdan foydalanish uchun rozilik shart."
    )
    await callback.message.answer(CONSENT_INTRO_TEXT, reply_markup=CONSENT_INTRO_KB)
    await callback.answer("Rad etildi.")


# --- Contact + JSHSHIR verification (two-factor, spec §8) ------------------ #
@dp.message(F.contact)
async def on_contact(message: Message, state: FSMContext):
    contact = message.contact
    # Defensive: CONTACT_KB is only ever shown after consent, but guard the
    # underlying data-collection step itself too, in case an old keyboard
    # is still sitting in someone's chat from before this gate existed.
    if not await data.has_consent(message.from_user.id):
        await message.answer(CONSENT_INTRO_TEXT, reply_markup=CONSENT_INTRO_KB)
        return
    # Only accept the user's OWN contact (spec §8 security).
    if contact.user_id != message.from_user.id:
        await message.answer("Iltimos, o'zingizning telefon raqamingizni yuboring.")
        return

    # Phone alone never links anymore — hold it and ask for JSHSHIR next;
    # on_jshshir below does the actual verification+linking.
    await state.update_data(
        phone=contact.phone_number, username=message.from_user.username or "",
    )
    await state.set_state(LinkStates.waiting_for_jshshir)
    await message.answer(
        "Rahmat! Endi tasdiqlash uchun JSHSHIR (PINFL) raqamingizni kiriting "
        "— 14 ta raqam.",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(LinkStates.waiting_for_jshshir)
async def on_jshshir(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    jshshir = normalize_jshshir(raw)
    if not is_valid_jshshir(jshshir):
        await message.answer(
            "JSHSHIR aynan 14 ta raqamdan iborat bo'lishi kerak. Qaytadan kiriting."
        )
        return  # stay in the same state — let them retry without re-sharing contact

    stored = await state.get_data()
    phone = stored.get("phone", "")
    username = stored.get("username", "")
    await state.clear()

    result, name = await data.link_contact(
        phone=phone, jshshir=jshshir,
        telegram_id=message.from_user.id, username=username,
    )

    if result in ("LINKED", "ALREADY_SAME"):
        await message.answer(
            f"Rahmat, {name}! Akkauntingiz muvaffaqiyatli tasdiqlandi va ulandi.",
            reply_markup=MENU_KB,
        )
        return

    # Every other outcome means nothing was (re)linked — per policy, a
    # phone/JSHSHIR mismatch never partially links; the person simply gets
    # nothing until HR sorts it out. If this Telegram account is already
    # linked to its OWN employee, keep their existing menu instead of
    # stripping it (defensive — there's no menu button that re-triggers
    # contact-share for an already-linked user anymore, but /start always
    # can).
    still_linked = await data.get_employee(message.from_user.id) is not None
    kb = MENU_KB if still_linked else ReplyKeyboardRemove()

    if result == "JSHSHIR_MISMATCH":
        await message.answer(
            "Telefon raqami va JSHSHIR mos kelmadi (yoki xodim ma'lumotlarida "
            "JSHSHIR hali kiritilmagan). Hech qanday ma'lumot ulanmadi — "
            "HR bo'limiga murojaat qiling.",
            reply_markup=kb,
        )
    elif result == "CONFLICT_EMPLOYEE":
        await message.answer(
            "Ushbu xodim profili boshqa Telegram akkauntiga ulangan. "
            "HR bilan bog'laning.",
            reply_markup=kb,
        )
    elif result == "CONFLICT_TELEGRAM":
        await message.answer(
            "Bu Telegram akkaunt allaqachon boshqa xodimga ulangan. "
            "HR bilan bog'laning.",
            reply_markup=kb,
        )
    elif result == "AMBIGUOUS_PHONE":
        await message.answer(
            "Bu telefon raqami bir nechta profilga mos keladi. "
            "Aniqlashtirish uchun HR bo'limiga murojaat qiling.",
            reply_markup=kb,
        )
    else:  # NOT_FOUND — phone/JSHSHIR remembered; will auto-link once HR registers a match.
        await message.answer(
            "Ma'lumotlaringiz qabul qilindi. Hozircha sizga tegishli xodim yozuvi "
            "tizimga kiritilmagan — HR ro'yxatga olgach, avtomatik ulanasiz va "
            "xabar olasiz.",
            reply_markup=kb,
        )


# --- Menu buttons ---------------------------------------------------------- #
async def _require_linked(message: Message) -> bool:
    emp = await data.get_employee(message.from_user.id)
    if emp is None:
        await message.answer(
            "Avval telefon raqamingizni tasdiqlang.", reply_markup=CONTACT_KB
        )
        return False
    return True


@dp.message(F.text == "💰 Joriy oylik")
@dp.message(Command("salary"))
async def current_salary(message: Message):
    if not await _require_linked(message):
        return
    salaries = await data.get_current_salary(message.from_user.id)
    if not salaries:
        await message.answer("Hozircha oylik ma'lumoti mavjud emas.", reply_markup=MENU_KB)
        return
    # Each branch's salary is its own normal, independent message — no
    # combining, no total. If two branches paid the same month, that's
    # simply two ordinary messages, one per branch. The menu keyboard rides
    # on the last one, re-showing it in case it was ever dismissed.
    for i, s in enumerate(salaries):
        last = i == len(salaries) - 1
        await message.answer(_one_salary_text(s), reply_markup=MENU_KB if last else None)


@dp.message(F.text == "📊 Oyliklar tarixi")
@dp.message(Command("history"))
async def salary_history(message: Message):
    if not await _require_linked(message):
        return
    years = await data.list_salary_years(message.from_user.id)
    if not years:
        await message.answer("Oyliklar tarixi bo'sh.", reply_markup=MENU_KB)
        return
    # An inline keyboard (year buttons) here is independent of the persistent
    # reply-keyboard menu, so this doesn't remove/replace the menu.
    await message.answer("📊 Qaysi yilni ko'rmoqchisiz?", reply_markup=_years_keyboard(years))


@dp.callback_query(F.data == "hist:years")
async def history_back_to_years(callback: CallbackQuery):
    years = await data.list_salary_years(callback.from_user.id)
    if not years:
        await callback.message.edit_text("Oyliklar tarixi bo'sh.")
    else:
        await callback.message.edit_text(
            "📊 Qaysi yilni ko'rmoqchisiz?", reply_markup=_years_keyboard(years)
        )
    await callback.answer()


@dp.callback_query(F.data.startswith("hist:year:"))
async def history_pick_year(callback: CallbackQuery):
    year = int(callback.data.split(":")[2])
    months = await data.list_salary_months(callback.from_user.id, year)
    if not months:
        await callback.answer("Bu yil uchun ma'lumot yo'q.", show_alert=True)
        return
    await callback.message.edit_text(
        f"📅 {year}-yil, qaysi oyni ko'rmoqchisiz?",
        reply_markup=_months_keyboard(year, months),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("hist:month:"))
async def history_pick_month(callback: CallbackQuery):
    _, _, year_s, month_s = callback.data.split(":")
    year, month = int(year_s), int(month_s)
    salaries = await data.get_salary_detail(callback.from_user.id, year, month)
    if not salaries:
        await callback.answer("Ma'lumot topilmadi.", show_alert=True)
        return
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Oylarga qaytish", callback_data=f"hist:year:{year}")]
    ])
    # Same rule here: no combining, no total. First branch's figure replaces
    # the "pick a month" prompt; any additional branch for that same month
    # (rare) follows as its own separate, normal message. The back button
    # rides along on whichever message is last.
    last = len(salaries) - 1
    await callback.message.edit_text(
        _one_salary_text(salaries[0]), reply_markup=back_kb if last == 0 else None
    )
    for i, s in enumerate(salaries[1:], start=1):
        await callback.message.answer(_one_salary_text(s), reply_markup=back_kb if i == last else None)
    await callback.answer()


@dp.message(F.text == "👤 Profilim")
@dp.message(Command("profile"))
async def profile(message: Message):
    emp = await data.get_employee(message.from_user.id)
    if emp is None:
        await message.answer("Avval telefon raqamingizni tasdiqlang.", reply_markup=CONTACT_KB)
        return
    await message.answer(
        f"👤 Profil\n\nF.I.Sh: {emp['full_name']}\n"
        f"Kod: {emp['employee_code']}\nBo'lim: {emp['unit']}",
        reply_markup=MENU_KB,
    )


@dp.message(F.text == "❓ Yordam")
@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "❓ Yordam\n\n"
        "💰 Joriy oylik — eng so'nggi oylik\n"
        "📊 Oyliklar tarixi — oldingi oyliklar\n"
        "👤 Profilim — shaxsiy ma'lumot\n\n"
        "Muammo bo'lsa HR bo'limiga murojaat qiling.",
        reply_markup=MENU_KB,
    )


TELEGRAM_MESSAGE_LIMIT = 4096


def _one_salary_text(s: dict) -> str:
    """
    One branch's salary, formatted as its own normal, standalone message —
    same shape whether it's the only one for that period or one of several.
    No combining, no total: if two branches paid the same month, the
    employee just gets two ordinary messages like this one, one per branch.

    Everything else the Excel had for this row (bonuses, allowances,
    itemized deductions, ...) is listed below the core figures, exactly as
    labeled in the source file — whatever a branch's payroll export
    contains for an employee is theirs to see in full.
    """
    # BREAKDOWN_FIELDS entries, split by category and shown only when
    # nonzero — same reasoning as apps.notifications.services.
    # format_salary_message. "employer_only" (social_tax) never shown:
    # it's the employer's own cost, not withheld from or paid to this
    # employee.
    accrual_lines = "".join(
        f"   • {label}: {_fmt(s.get(name))} so'm\n"
        for name, label, category, _ in BREAKDOWN_FIELDS
        if category == "accrual" and s.get(name)
    )
    deduction_lines = "".join(
        f"   • {label}: {_fmt(s.get(name))} so'm\n"
        for name, label, category, _ in BREAKDOWN_FIELDS
        if category == "deduction" and s.get(name)
    )
    text = (
        f"📅 {s['period_label']} — {s['unit']}\n\n"
        f"💰 Hisoblangan: {_fmt(s['gross'])} so'm\n"
        f"{accrual_lines}"
        f"💳 Avans: {_fmt(s['advance'])} so'm\n"
        f"➖ Ushlanmalar: {_fmt(s['deductions'])} so'm\n"
        f"{deduction_lines}"
        f"\n✅ Qo'lga: {_fmt(s['net'])} so'm"
    )
    components = s.get("components") or []
    if components:
        lines = ["\n\n📋 Qo'shimcha ma'lumotlar:"]
        for c in components:
            lines.append(f"• {c['label']}: {_fmt(c['value'])}")
        text += "\n".join(lines)
    # Telegram hard-caps a message at 4096 chars — a very wide payroll
    # export could in principle exceed that; trim rather than fail to send.
    if len(text) > TELEGRAM_MESSAGE_LIMIT:
        text = text[: TELEGRAM_MESSAGE_LIMIT - 20].rstrip() + "\n… (davomi qisqartirildi)"
    return text


BOT_COMMANDS = [
    BotCommand(command="start", description="Botni ishga tushirish / ro'yxatdan o'tish"),
    BotCommand(command="restart", description="Botni qayta boshlash (holatni tozalash)"),
    BotCommand(command="salary", description="💰 Joriy oylik"),
    BotCommand(command="history", description="📊 Oyliklar tarixi"),
    BotCommand(command="profile", description="👤 Profilim"),
    BotCommand(command="help", description="❓ Yordam"),
]


async def main() -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    # protect_content: every message this bot sends (salary figures, JSHSHIR
    # prompts, etc.) is forward/save-protected in Telegram clients by default,
    # applied bot-wide so individual handlers don't need to repeat it.
    bot = Bot(token=token, default=DefaultBotProperties(protect_content=True))
    await bot.set_my_commands(BOT_COMMANDS)
    logger.info("Bot starting (long polling)...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
