"""
Telegram bot (aiogram 3.x) — runs as a standalone process.

Start with:  python -m apps.telegram_bot.bot   (after DJANGO_SETTINGS_MODULE is set)
or via the management command:  python manage.py run_bot

Handles (spec §8, §20-22, §42, §43):
  /start   -> ask for contact -> link account
  menu     -> Joriy oylik / Oyliklar tarixi / Profil / Telefonni yangilash / Yordam

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
from aiogram.filters import Command  # noqa: E402
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from django.conf import settings  # noqa: E402

from apps.telegram_bot import data  # noqa: E402

logger = logging.getLogger("apps.telegram_bot")


def _fmt(value) -> str:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return str(value)
    return f"{n:,}".replace(",", " ")


# --- Keyboards ------------------------------------------------------------- #
CONTACT_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📱 Telefon raqamimni yuborish", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

MENU_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💰 Joriy oylik"), KeyboardButton(text="📊 Oyliklar tarixi")],
        [KeyboardButton(text="👤 Profilim"), KeyboardButton(text="🔄 Telefonni yangilash")],
        [KeyboardButton(text="❓ Yordam")],
    ],
    resize_keyboard=True,
)

MONTH_NAMES = [
    "", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
    "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
]

dp = Dispatcher()


def _years_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(y), callback_data=f"hist:year:{y}")]
        for y in years
    ])


def _months_keyboard(year: int, months: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=MONTH_NAMES[m], callback_data=f"hist:month:{year}:{m}")
        for m in months
    ]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="⬅️ Yillarga qaytish", callback_data="hist:years")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- /start ---------------------------------------------------------------- #
@dp.message(Command("start"))
async def cmd_start(message: Message):
    emp = await data.get_employee(message.from_user.id)
    if emp:
        await message.answer(
            f"Assalomu alaykum, {emp['full_name']}! Menyudan foydalaning.",
            reply_markup=MENU_KB,
        )
        return
    await message.answer(
        "Assalomu alaykum!\n\n"
        "Oylik ish haqingiz haqida ma'lumot olish uchun telefon raqamingizni tasdiqlang.",
        reply_markup=CONTACT_KB,
    )


# --- Contact linking ------------------------------------------------------- #
@dp.message(F.contact)
async def on_contact(message: Message):
    contact = message.contact
    # Only accept the user's OWN contact (spec §8 security).
    if contact.user_id != message.from_user.id:
        await message.answer("Iltimos, o'zingizning telefon raqamingizni yuboring.")
        return

    result, name = await data.link_contact(
        phone=contact.phone_number,
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
    )

    if result in ("LINKED", "ALREADY_SAME"):
        await message.answer(
            f"Rahmat, {name}! Akkauntingiz muvaffaqiyatli ulandi.",
            reply_markup=MENU_KB,
        )
        return

    # Every other outcome means THIS attempt didn't (re)link anything — but
    # if this Telegram account is already linked to its own employee (e.g.
    # they tried "Telefonni yangilash" with a number that didn't work out),
    # they still have full access and must not lose their menu over it.
    still_linked = await data.get_employee(message.from_user.id) is not None
    kb = MENU_KB if still_linked else ReplyKeyboardRemove()

    if result == "CONFLICT_EMPLOYEE":
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
    else:  # NOT_FOUND — phone remembered; will auto-link once payroll Excel names it.
        await message.answer(
            "Raqamingiz qabul qilindi. Hozircha sizga tegishli oylik ma'lumoti "
            "tizimga kiritilmagan — buxgalteriya oylik hisobotini yuklagach, "
            "avtomatik ulanasiz va xabar olasiz.",
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


@dp.message(F.text == "🔄 Telefonni yangilash")
async def update_phone(message: Message):
    await message.answer(
        "Telefon raqamini yangilash uchun yangi raqamni tasdiqlang.\n"
        "Eslatma: raqam boshqa profilga tegishli bo'lsa, HR bilan bog'laning.",
        reply_markup=CONTACT_KB,
    )


@dp.message(F.text == "❓ Yordam")
@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "❓ Yordam\n\n"
        "💰 Joriy oylik — eng so'nggi oylik\n"
        "📊 Oyliklar tarixi — oldingi oyliklar\n"
        "👤 Profilim — shaxsiy ma'lumot\n"
        "🔄 Telefonni yangilash — raqamni qayta ulash\n\n"
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
    text = (
        f"📅 {s['period_label']} — {s['unit']}\n\n"
        f"💰 Hisoblangan: {_fmt(s['gross'])} so'm\n"
        f"💳 Avans: {_fmt(s['advance'])} so'm\n"
        f"➖ Ushlanmalar: {_fmt(s['deductions'])} so'm\n\n"
        f"✅ Qo'lga: {_fmt(s['net'])} so'm"
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


async def main() -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    bot = Bot(token=token)
    logger.info("Bot starting (long polling)...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
