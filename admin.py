from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command, StateFilter
from html import escape
import asyncio

from config import ADMIN_IDS
from database import (
    add_product, get_all_products, get_product,
    delete_product, get_stats, get_all_users, add_balance, get_user
)
from keyboards import (
    admin_menu_kb, admin_main_menu_kb, admin_products_kb,
    confirm_delete_kb, cancel_kb, broadcast_cancel_kb
)

router = Router()


class AddProduct(StatesGroup):
    name = State()
    photo = State()
    description = State()
    price = State()
    file = State()


class BroadcastStates(StatesGroup):
    waiting_for_message = State()


class GiveBalance(StatesGroup):
    user_id = State()
    amount = State()


class DeleteProduct(StatesGroup):
    selecting = State()
    confirming = State()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@router.message(F.text == "Админ панель")
@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Нет доступа')
        return
    await state.clear()
    await message.answer(
        '<b><tg-emoji emoji-id="5870982283724328568">⚙</tg-emoji> Админ панель</b>\n\nВыберите действие:',
        reply_markup=admin_menu_kb()
    )


@router.message(F.text == "Назад", StateFilter(
    DeleteProduct.selecting, DeleteProduct.confirming,
    AddProduct.name, AddProduct.photo, AddProduct.description,
    AddProduct.price, AddProduct.file,
    BroadcastStates.waiting_for_message,
    GiveBalance.user_id, GiveBalance.amount
))
async def admin_fsm_back(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        '<b><tg-emoji emoji-id="5870982283724328568">⚙</tg-emoji> Админ панель</b>\n\nВыберите действие:',
        reply_markup=admin_menu_kb()
    )


@router.message(F.text == "Назад", StateFilter(None))
async def admin_main_back(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        '<tg-emoji emoji-id="5893057118545646106">◀️</tg-emoji> Главное меню',
        reply_markup=admin_main_menu_kb()
    )


@router.message(F.text == "Статистика")
async def admin_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    users, products, purchases, revenue = await get_stats()
    await message.answer(
        f'<b><tg-emoji emoji-id="5870921681735781843">📊</tg-emoji> Статистика</b>\n\n'
        f'<tg-emoji emoji-id="5870772616305839506">👥</tg-emoji> Пользователей: <b>{users}</b>\n'
        f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товаров: <b>{products}</b>\n'
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Покупок: <b>{purchases}</b>\n'
        f'<tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji> Выручка: <b>{revenue} Stars</b>',
        reply_markup=admin_menu_kb()
    )


@router.message(F.text == "Список товаров")
async def admin_products(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    products = await get_all_products()
    if not products:
        await message.answer(
            '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товаров нет</b>',
            reply_markup=admin_menu_kb()
        )
        return
    await state.set_state(DeleteProduct.selecting)
    await state.update_data(
        products_map={f"Удалить: {p['name']}": {"id": p["id"], "name": p["name"]} for p in products}
    )
    await message.answer(
        f'<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товары</b>\n\nВсего: <b>{len(products)}</b> шт.',
        reply_markup=admin_products_kb(products)
    )


@router.message(StateFilter(DeleteProduct.selecting), F.text.startswith("Удалить: "))
async def admin_delete_select(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    product_info = data.get("products_map", {}).get(message.text)
    if not product_info:
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Товар не найден')
        return
    await state.set_state(DeleteProduct.confirming)
    await state.update_data(pending_delete=product_info)
    await message.answer(
        f'<b><tg-emoji emoji-id="5870875489362513438">🗑</tg-emoji> Удалить товар?</b>\n\n'
        f'Товар: <b>{escape(product_info["name"])}</b>\n\nЭто действие необратимо!',
        reply_markup=confirm_delete_kb()
    )


@router.message(StateFilter(DeleteProduct.confirming), F.text == "Подтвердить удаление")
async def admin_confirm_delete(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    product_info = data.get("pending_delete")
    if not product_info:
        return
    await delete_product(product_info["id"])
    await state.clear()
    await message.answer(
        f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Товар удалён!</b>\n<b>{escape(product_info["name"])}</b>',
        reply_markup=admin_menu_kb()
    )


@router.message(F.text == "Добавить товар")
async def admin_add_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AddProduct.name)
    await message.answer(
        '<b><tg-emoji emoji-id="5870676941614354370">🖋</tg-emoji> Добавление товара</b>\n\n'
        'Шаг 1/5 — Введите <b>название</b> товара:\n<i>Например: MOD 2026</i>',
        reply_markup=cancel_kb()
    )


@router.message(StateFilter(AddProduct.name))
async def add_name(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer('Отменено', reply_markup=admin_menu_kb())
        return
    await state.update_data(name=message.text)
    await state.set_state(AddProduct.photo)
    await message.answer(
        '<b><tg-emoji emoji-id="6035128606563241721">🖼</tg-emoji> Шаг 2/5 — Отправьте <b>фото</b> товара:</b>',
        reply_markup=cancel_kb()
    )


@router.message(StateFilter(AddProduct.photo), F.photo)
async def add_photo(message: Message, state: FSMContext):
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await state.set_state(AddProduct.description)
    await message.answer(
        '<b><tg-emoji emoji-id="5870676941614354370">🖋</tg-emoji> Шаг 3/5 — Введите <b>описание</b> товара:</b>',
        reply_markup=cancel_kb()
    )


@router.message(StateFilter(AddProduct.description))
async def add_description(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer('Отменено', reply_markup=admin_menu_kb())
        return
    await state.update_data(description=message.text)
    await state.set_state(AddProduct.price)
    await message.answer(
        '<b><tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji> Шаг 4/5 — Введите <b>цену</b> в Stars:</b>\n<i>Только число, например: 50</i>',
        reply_markup=cancel_kb()
    )


@router.message(StateFilter(AddProduct.price))
async def add_price(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer('Отменено', reply_markup=admin_menu_kb())
        return
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Введите корректное число!', reply_markup=cancel_kb())
        return
    await state.update_data(price=int(message.text))
    await state.set_state(AddProduct.file)
    await message.answer(
        '<b><tg-emoji emoji-id="5870528606328852614">📁</tg-emoji> Шаг 5/5 — Отправьте <b>файл .zip</b>:</b>',
        reply_markup=cancel_kb()
    )


@router.message(StateFilter(AddProduct.file), F.document)
async def add_file(message: Message, state: FSMContext):
    if not message.document.file_name.endswith(".zip"):
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Нужен файл <b>.zip</b>!', reply_markup=cancel_kb())
        return
    data = await state.get_data()
    await add_product(
        name=data["name"],
        description=data["description"],
        price=data["price"],
        photo_file_id=data["photo_file_id"],
        file_id=message.document.file_id
    )
    await state.clear()
    await message.answer(
        f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Товар добавлен!</b>\n\n'
        f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> <b>{escape(data["name"])}</b>\n'
        f'<tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji> Цена: {data["price"]} Stars',
        reply_markup=admin_menu_kb()
    )


@router.message(F.text == "Рассылка")
async def admin_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(BroadcastStates.waiting_for_message)
    await message.answer(
        '<b><tg-emoji emoji-id="6039422865189638057">📣</tg-emoji> Рассылка</b>\n\n'
        'Отправьте сообщение для рассылки.\n<i>Поддерживается текст, фото, видео, документ.</i>',
        reply_markup=broadcast_cancel_kb()
    )


@router.message(StateFilter(BroadcastStates.waiting_for_message))
async def process_broadcast(message: Message, state: FSMContext, bot: Bot):
    if message.text == "Отмена":
        await state.clear()
        await message.answer('Отменено', reply_markup=admin_menu_kb())
        return
    await state.clear()
    users = await get_all_users()
    status_msg = await message.answer(
        f'<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> Рассылка на <b>{len(users)}</b> пользователей...',
        reply_markup=admin_menu_kb()
    )
    success = 0
    failed = 0
    for user in users:
        try:
            await message.copy_to(chat_id=user["id"])
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status_msg.edit_text(
        f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Рассылка завершена!</b>\n\n'
        f'<tg-emoji emoji-id="5891207662678317861">👤</tg-emoji> Доставлено: <b>{success}</b>\n'
        f'<tg-emoji emoji-id="5893192487324880883">👤</tg-emoji> Не доставлено: <b>{failed}</b>'
    )


@router.message(F.text == "Выдать баланс")
async def admin_give_balance_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(GiveBalance.user_id)
    await message.answer(
        '<b><tg-emoji emoji-id="5879814368572478751">🏧</tg-emoji> Выдача баланса</b>\n\nВведите <b>Telegram ID</b> пользователя:',
        reply_markup=cancel_kb()
    )


@router.message(StateFilter(GiveBalance.user_id))
async def give_balance_user(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer('Отменено', reply_markup=admin_menu_kb())
        return
    text = (message.text or "").strip()
    if not text.lstrip("-").isdigit():
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Введите числовой ID!', reply_markup=cancel_kb())
        return
    user_id = int(text)
    user = await get_user(user_id)
    display_name = user["first_name"] if user else str(user_id)
    await state.update_data(target_user_id=user_id, target_name=display_name)
    await state.set_state(GiveBalance.amount)
    await message.answer(
        f'<tg-emoji emoji-id="5870994129244131212">👤</tg-emoji> Пользователь: <b>{escape(display_name)}</b> (<code>{user_id}</code>)\n\nВведите <b>количество Stars</b>:',
        reply_markup=cancel_kb()
    )


@router.message(StateFilter(GiveBalance.amount))
async def give_balance_amount(message: Message, state: FSMContext, bot: Bot):
    if message.text == "Отмена":
        await state.clear()
        await message.answer('Отменено', reply_markup=admin_menu_kb())
        return
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Введите корректное число!', reply_markup=cancel_kb())
        return
    data = await state.get_data()
    amount = int(message.text)
    target_id = data["target_user_id"]
    target_name = data["target_name"]
    await add_balance(target_id, amount, "Выдача от админа")
    await state.clear()
    await message.answer(
        f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Баланс выдан!</b>\n\n'
        f'<tg-emoji emoji-id="5870994129244131212">👤</tg-emoji> Пользователь: <b>{escape(target_name)}</b>\n'
        f'<tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji> Начислено: <b>{amount} Stars</b>',
        reply_markup=admin_menu_kb()
    )
    try:
        await bot.send_message(
            chat_id=target_id,
            text=(
                f'<b><tg-emoji emoji-id="6032644646587338669">🎁</tg-emoji> Вам начислен баланс!</b>\n\n'
                f'<tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji> Зачислено: <b>{amount} Stars</b>'
            )
        )
    except Exception:
        pass
