from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove


def remove_kb():
    return ReplyKeyboardRemove()


def btn(text, emoji_id=None, style=None):
    return KeyboardButton(
        text=text,
        icon_custom_emoji_id=emoji_id,
        style=style
    )


def main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                btn("Магазин", "5884479287171485878", "primary"),
                btn("Профиль", "5870994129244131212", "primary"),
            ],
            [
                btn("Разработка", "5940433880585605708", None),
            ],
        ],
        resize_keyboard=True
    )


def admin_main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                btn("Магазин", "5884479287171485878", "primary"),
                btn("Профиль", "5870994129244131212", "primary"),
            ],
            [
                btn("Разработка", "5940433880585605708", "primary"),
            ],
            [
                btn("Админ панель", "5870982283724328568", None),
            ]
        ],
        resize_keyboard=True
    )


def shop_kb(products):
    buttons = []
    for p in products:
        buttons.append([btn(f"Товар: {p['name']}", "5884479287171485878", "primary")])
    buttons.append([btn("Товар: BR NEW 2026", "5884479287171485878", "primary")])
    buttons.append([btn("Назад", "5893057118545646106", None)])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def shop_no_products_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [btn("Товар: BR NEW 2026", "5884479287171485878", "primary")],
            [btn("Назад", "5893057118545646106", None)],
        ],
        resize_keyboard=True
    )


def product_kb(price: int):
    return ReplyKeyboardMarkup(
        keyboard=[
            [btn(f"Купить за {price} Stars", "5904462880941545555", "primary")],
            [btn("Назад в магазин", "5893057118545646106", None)],
        ],
        resize_keyboard=True
    )


def profile_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [btn("Пополнить баланс", "5879814368572478751", "primary")],
            [btn("Мои покупки", "5884479287171485878", None)],
            [btn("Назад", "5893057118545646106", None)],
        ],
        resize_keyboard=True
    )


def purchases_kb(purchases):
    buttons = []
    for p in purchases:
        dt = str(p["created_at"])[:10]
        buttons.append([btn(f"Чек: {p['name']} {dt}", "5870528606328852614", None)])
    buttons.append([btn("Назад", "5893057118545646106", None)])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def receipt_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [btn("Скачать файл", "6039802767931871481", "primary")],
            [btn("Назад", "5893057118545646106", None)],
        ],
        resize_keyboard=True
    )


def topup_cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[btn("Отмена", "5870657884844462243", "danger")]],
        resize_keyboard=True
    )


def admin_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                btn("Добавить товар", "5870676941614354370", "primary"),
                btn("Список товаров", "5884479287171485878", "primary"),
            ],
            [
                btn("Статистика", "5870921681735781843", None),
                btn("Рассылка", "6039422865189638057", None),
            ],
            [
                btn("Выдать баланс", "5879814368572478751", "primary"),
            ],
            [
                btn("Назад", "5893057118545646106", None),
            ],
        ],
        resize_keyboard=True
    )


def admin_products_kb(products):
    buttons = []
    for p in products:
        buttons.append([btn(f"Удалить: {p['name']}", "5870875489362513438", "danger")])
    buttons.append([btn("Назад", "5893057118545646106", None)])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[btn("Отмена", "5870657884844462243", "danger")]],
        resize_keyboard=True
    )


def confirm_delete_kb(product_name: str = ""):
    return ReplyKeyboardMarkup(
        keyboard=[
            [btn("Подтвердить удаление", "5870657884844462243", "danger")],
            [btn("Отмена", "5893057118545646106", None)],
        ],
        resize_keyboard=True
    )


def broadcast_cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[btn("Отмена", "5870657884844462243", "danger")]],
        resize_keyboard=True
    )
