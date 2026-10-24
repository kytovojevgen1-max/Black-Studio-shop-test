from aiogram import Router, F
from aiogram.types import Message, LabeledPrice, PreCheckoutQuery, ContentType, FSInputFile
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from html import escape
from pathlib import Path

from config import ADMIN_IDS
from database import (
    get_all_products, get_product, save_purchase,
    get_user_purchases, get_purchase, register_user,
    get_balance, deduct_balance, topup_via_stars
)
from keyboards import (
    main_menu_kb, admin_main_menu_kb, shop_kb, shop_no_products_kb, product_kb,
    profile_kb, purchases_kb, receipt_kb, topup_cancel_kb
)

router = Router()

BANNER_PATH      = Path(__file__).parent.parent / "banner.png"
BANNER_SHOP_PATH = Path(__file__).parent.parent / "banner_shop.jpg"
BANNER_JNI_PATH  = Path(__file__).parent.parent / "banner_jni.jpg"

PROJECT_NAME  = "BR NEW 2026"
PROJECT_PRICE = 200   # Stars

GREETING_TEXT = (
    '<b><tg-emoji emoji-id="6041731551845159060">🎉</tg-emoji> VOLTMENS SHOP</b>\n\n'
    '<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Добро пожаловать в магазин!\n'
    'Здесь ты можешь купить цифровые товары за внутренний баланс.'
)


class TopupStates(StatesGroup):
    waiting_for_amount = State()


class ShopStates(StatesGroup):
    browsing = State()
    viewing_product = State()
    my_purchases = State()
    viewing_receipt = State()


class ProfileStates(StatesGroup):
    viewing = State()


def home_kb(user_id):
    return admin_main_menu_kb() if user_id in ADMIN_IDS else main_menu_kb()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )
    if BANNER_PATH.exists():
        await message.answer_photo(
            FSInputFile(BANNER_PATH),
            caption=GREETING_TEXT,
            reply_markup=home_kb(message.from_user.id)
        )
    else:
        await message.answer(GREETING_TEXT, reply_markup=home_kb(message.from_user.id))


@router.message(F.text == "Магазин")
async def shop_handler(message: Message, state: FSMContext):
    await state.clear()
    products = await get_all_products()

    await state.set_state(ShopStates.browsing)
    await state.update_data(products={p["name"]: p["id"] for p in products})
    text = (
        '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Магазин</b>\n\n'
        '<tg-emoji emoji-id="5870676941614354370">🖋</tg-emoji> Выбери товар:'
    )
    if BANNER_SHOP_PATH.exists():
        await message.answer_photo(
            FSInputFile(BANNER_SHOP_PATH),
            caption=text,
            reply_markup=shop_kb(products)
        )
    else:
        await message.answer(text, reply_markup=shop_kb(products))


@router.message(StateFilter(ShopStates.browsing), F.text == "Назад")
async def shop_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        '<tg-emoji emoji-id="5893057118545646106">◀️</tg-emoji> Главное меню',
        reply_markup=home_kb(message.from_user.id)
    )


@router.message(StateFilter(ShopStates.browsing), F.text == f"Товар: {PROJECT_NAME}")
async def project_detail(message: Message, state: FSMContext):
    """Карточка товара BR NEW 2026 — оплата через Telegram Stars."""
    await state.set_state(ShopStates.viewing_product)
    await state.update_data(current_product_id="__project__")
    text = (
        f'<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> {PROJECT_NAME}</b>\n\n'
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Фулл БП\n'
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Фулл кейс & ревард\n'
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Фулл смерть (фикс урон)\n'
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Фулл маркетплейс & ПВЗ\n'
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Фулл тюнинг\n'
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Фулл аукцион, гараж, трейд\n'
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Фулл инвентарь, семья, фракции\n'
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Система материалов, динамита, КПП\n'
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> И многое другое...\n\n'
        f'<tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji> <b>Цена: {PROJECT_PRICE} Stars</b>\n\n'
        f'<tg-emoji emoji-id="6028435952299413210">ℹ</tg-emoji> После оплаты бот автоматически настроит '
        f'и зальёт мод на ваш сервер, скомпилирует клиент и отдаст лаунчер.'
    )
    from keyboards import product_kb
    await message.answer(text, reply_markup=product_kb(PROJECT_PRICE))


@router.message(StateFilter(ShopStates.browsing), F.text.startswith("Товар: "))
async def product_detail(message: Message, state: FSMContext):
    name = message.text[7:]
    data = await state.get_data()
    product_id = data.get("products", {}).get(name)
    if not product_id:
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Товар не найден')
        return
    product = await get_product(product_id)
    balance = await get_balance(message.from_user.id)
    can_buy = balance >= product["price"]
    balance_line = (
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Баланс: <b>{balance} Stars</b> — достаточно'
        if can_buy else
        f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Баланс: <b>{balance} Stars</b> — недостаточно'
    )
    await state.set_state(ShopStates.viewing_product)
    await state.update_data(current_product_id=product["id"])
    await message.answer_photo(
        photo=product["photo_file_id"],
        caption=(
            f'<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> {escape(product["name"])}</b>\n\n'
            f'<tg-emoji emoji-id="5870676941614354370">🖋</tg-emoji> <b>Описание:</b>\n{escape(product["description"])}\n\n'
            f'<tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji> <b>Цена:</b> {product["price"]} Stars\n'
            f'{balance_line}'
        ),
        reply_markup=product_kb(product["price"])
    )


@router.message(StateFilter(ShopStates.viewing_product), F.text == "Назад в магазин")
async def back_to_shop(message: Message, state: FSMContext):
    await state.clear()
    await shop_handler(message, state)


@router.message(StateFilter(ShopStates.viewing_product), F.text.startswith("Купить за "))
async def buy_product(message: Message, state: FSMContext):
    data = await state.get_data()

    # ── Виртуальный товар BR NEW 2026 — оплата с внутреннего баланса ──
    if data.get("current_product_id") == "__project__":
        balance = await get_balance(message.from_user.id)
        if balance < PROJECT_PRICE:
            await message.answer(
                f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Недостаточно баланса!\n'
                f'У вас <b>{balance} Stars</b>, нужно <b>{PROJECT_PRICE} Stars</b>.'
            )
            return
        ok = await deduct_balance(message.from_user.id, PROJECT_PRICE, f"Покупка: {PROJECT_NAME}")
        if not ok:
            await message.answer(f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Ошибка списания')
            return
        await state.clear()
        from handlers.project import _start_collection
        await message.answer(
            f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> <b>Оплата прошла! Списано {PROJECT_PRICE} Stars.</b>'
        )
        await _start_collection(message, state)
        return

    product = await get_product(data.get("current_product_id"))
    if not product:
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Товар не найден')
        return
    balance = await get_balance(message.from_user.id)
    if balance < product["price"]:
        await message.answer(
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Недостаточно баланса!\n'
            f'У вас <b>{balance} Stars</b>, нужно <b>{product["price"]} Stars</b>.'
        )
        return
    ok = await deduct_balance(message.from_user.id, product["price"], f"Покупка: {product['name']}")
    if not ok:
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Ошибка списания')
        return
    purchase_id = await save_purchase(message.from_user.id, product["id"])
    new_balance = await get_balance(message.from_user.id)
    await state.clear()
    from datetime import datetime
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    await message.answer(
        f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Покупка успешна!</b>\n\n'
        f'<tg-emoji emoji-id="5870528606328852614">📁</tg-emoji> <b>Чек #{purchase_id}</b>\n'
        f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товар: <b>{escape(product["name"])}</b>\n'
        f'<tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji> Списано: <b>{product["price"]} Stars</b>\n'
        f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Остаток: <b>{new_balance} Stars</b>\n'
        f'<tg-emoji emoji-id="5890937706803894250">📅</tg-emoji> Дата: <b>{now}</b>',
        reply_markup=home_kb(message.from_user.id)
    )
    await message.answer_document(
        document=product["file_id"],
        caption=f'<tg-emoji emoji-id="6039802767931871481">⬇</tg-emoji> <b>{escape(product["name"])}</b>'
    )


@router.message(F.text == "Профиль")
async def profile_handler(message: Message, state: FSMContext):
    await state.clear()
    balance = await get_balance(message.from_user.id)
    purchases = await get_user_purchases(message.from_user.id)
    name = escape(message.from_user.first_name or "")
    await state.set_state(ProfileStates.viewing)
    await message.answer(
        f'<b><tg-emoji emoji-id="5870994129244131212">👤</tg-emoji> Профиль</b>\n\n'
        f'<b>ID:</b> <code>{message.from_user.id}</code>\n'
        f'<tg-emoji emoji-id="5870676941614354370">🖋</tg-emoji> <b>Имя:</b> {name}\n'
        f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> <b>Баланс:</b> {balance} Stars\n'
        f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> <b>Покупок:</b> {len(purchases)} шт.',
        reply_markup=profile_kb()
    )


@router.message(StateFilter(ProfileStates.viewing), F.text == "Назад")
async def profile_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        '<tg-emoji emoji-id="5893057118545646106">◀️</tg-emoji> Главное меню',
        reply_markup=home_kb(message.from_user.id)
    )


@router.message(F.text == "Мои покупки")
async def my_purchases(message: Message, state: FSMContext):
    purchases = await get_user_purchases(message.from_user.id)
    if not purchases:
        await message.answer(
            '<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Мои покупки</b>\n\n'
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Покупок пока нет',
            reply_markup=profile_kb()
        )
        return
    await state.set_state(ShopStates.my_purchases)
    await state.update_data(
        purchases_map={f"Чек: {p['name']} {str(p['created_at'])[:10]}": p["id"] for p in purchases}
    )
    await message.answer(
        f'<b><tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Мои покупки</b>\n\n'
        f'Всего: <b>{len(purchases)}</b> шт.',
        reply_markup=purchases_kb(purchases)
    )


@router.message(StateFilter(ShopStates.my_purchases), F.text == "Назад")
async def purchases_back(message: Message, state: FSMContext):
    await state.clear()
    await profile_handler(message, state)


@router.message(StateFilter(ShopStates.my_purchases), F.text.startswith("Чек: "))
async def show_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    purchase_id = data.get("purchases_map", {}).get(message.text)
    if not purchase_id:
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Чек не найден')
        return
    purchase = await get_purchase(purchase_id)
    if not purchase or purchase["user_id"] != message.from_user.id:
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Нет доступа')
        return
    from datetime import datetime
    dt = str(purchase["created_at"])
    try:
        dt_fmt = datetime.strptime(dt[:19], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
    except Exception:
        dt_fmt = dt[:16]
    await state.set_state(ShopStates.viewing_receipt)
    await state.update_data(current_purchase_id=purchase_id)
    await message.answer(
        f'<b><tg-emoji emoji-id="5870528606328852614">📁</tg-emoji> Чек #{purchase["id"]}</b>\n\n'
        f'<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> Товар: <b>{escape(purchase["name"])}</b>\n'
        f'<tg-emoji emoji-id="5870676941614354370">🖋</tg-emoji> {escape(purchase["description"])}\n\n'
        f'<tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji> Оплачено: <b>{purchase["price"]} Stars</b>\n'
        f'<tg-emoji emoji-id="5890937706803894250">📅</tg-emoji> Дата: <b>{dt_fmt}</b>',
        reply_markup=receipt_kb()
    )


@router.message(StateFilter(ShopStates.viewing_receipt), F.text == "Назад")
async def receipt_back(message: Message, state: FSMContext):
    await state.clear()
    await my_purchases(message, state)


@router.message(StateFilter(ShopStates.viewing_receipt), F.text == "Скачать файл")
async def download_file(message: Message, state: FSMContext):
    data = await state.get_data()
    purchase = await get_purchase(data.get("current_purchase_id"))
    if not purchase or purchase["user_id"] != message.from_user.id:
        await message.answer('<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Нет доступа')
        return
    await message.answer_document(
        document=purchase["file_id"],
        caption=f'<tg-emoji emoji-id="6039802767931871481">⬇</tg-emoji> <b>{escape(purchase["name"])}</b>'
    )


@router.message(F.text == "Пополнить баланс")
async def topup_menu(message: Message, state: FSMContext):
    await state.clear()
    balance = await get_balance(message.from_user.id)
    await state.set_state(TopupStates.waiting_for_amount)
    await message.answer(
        f'<b><tg-emoji emoji-id="5879814368572478751">🏧</tg-emoji> Пополнение баланса</b>\n\n'
        f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Текущий баланс: <b>{balance} Stars</b>\n\n'
        f'Введите сумму пополнения:',
        reply_markup=topup_cancel_kb()
    )


@router.message(StateFilter(TopupStates.waiting_for_amount), F.text == "Отмена")
async def topup_cancel(message: Message, state: FSMContext):
    await state.clear()
    await profile_handler(message, state)


@router.message(StateFilter(TopupStates.waiting_for_amount))
async def process_topup_amount(message: Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Введите корректное число!',
            reply_markup=topup_cancel_kb()
        )
        return
    amount = int(message.text)
    await state.clear()
    await message.answer_invoice(
        title="Пополнение баланса",
        description=f"После оплаты на ваш счёт будет зачислено {amount} Stars",
        payload=f"topup_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} Stars", amount=amount)]
    )


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    charge_id = message.successful_payment.telegram_payment_charge_id
    if payload.startswith("topup_"):
        amount = int(payload.split("_")[1])
        await topup_via_stars(message.from_user.id, amount, charge_id)
        balance = await get_balance(message.from_user.id)
        await message.answer(
            f'<b><tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> Баланс пополнен!</b>\n\n'
            f'<tg-emoji emoji-id="5879814368572478751">🏧</tg-emoji> Зачислено: <b>{amount} Stars</b>\n'
            f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> Новый баланс: <b>{balance} Stars</b>',
            reply_markup=home_kb(message.from_user.id)
        )
