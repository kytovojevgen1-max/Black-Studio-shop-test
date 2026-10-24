import asyncio
import logging
import os
import tempfile
import zipfile
from collections import deque
from pathlib import Path

import aioftp
import aiomysql
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, FSInputFile,
    ReplyKeyboardMarkup, KeyboardButton
)

from config import ADMIN_IDS

router = Router()
log = logging.getLogger(__name__)

BANNER_DEV_PATH  = Path(__file__).parent.parent / "banner_razrabotka.jpg"
BANNER_JNI_PATH  = Path(__file__).parent.parent / "banner_jni.jpg"

# ─── Очередь задач ─────────────────────────────────────────────────────────────
# Каждый элемент: asyncio.Task
_task_queue: deque = deque()
_queue_lock = asyncio.Lock()
_current_task: asyncio.Task | None = None


# ─── Клавиатуры ────────────────────────────────────────────────────────────────

def dev_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Компилятор",         icon_custom_emoji_id="5940433880585605708", style="primary")],
            [KeyboardButton(text="Залив мода",         icon_custom_emoji_id="5940433880585605708", style="primary")],
            [KeyboardButton(text="Импорт базы (.sql)", icon_custom_emoji_id="5870528606328852614", style="primary")],
            [KeyboardButton(text="Назад",              icon_custom_emoji_id="5893057118545646106")],
        ],
        resize_keyboard=True
    )


def cancel_dev_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отмена", icon_custom_emoji_id="5870657884844462243", style="danger")]],
        resize_keyboard=True
    )


# ─── FSM ───────────────────────────────────────────────────────────────────────

class DevStates(StatesGroup):
    # Залив мода
    mod_host    = State()
    mod_port    = State()
    mod_user    = State()
    mod_pass    = State()
    mod_zip     = State()
    # Импорт SQL
    sql_host    = State()
    sql_port    = State()
    sql_user    = State()
    sql_pass    = State()
    sql_db      = State()
    sql_file    = State()


# ─── Вспомогалки ───────────────────────────────────────────────────────────────

def _e(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


async def _ask(message: Message, text: str, state, next_state):
    await state.set_state(next_state)
    await message.answer(text, reply_markup=cancel_dev_kb())


# ══════════════════════════════════════════════════════════════════════
#  ГЛАВНОЕ МЕНЮ РАЗДЕЛА
# ══════════════════════════════════════════════════════════════════════

@router.message(F.text == "Разработка")
async def dev_menu(message: Message, state: FSMContext):
    await state.clear()
    text = (
        f'<b>{_e("5940433880585605708","🔨")} Разработка</b>\n\n'
        f'{_e("5870676941614354370","🖋")} Выбери действие:'
    )
    if BANNER_DEV_PATH.exists():
        await message.answer_photo(photo=FSInputFile(BANNER_DEV_PATH), caption=text, reply_markup=dev_menu_kb())
    else:
        await message.answer(text, reply_markup=dev_menu_kb())


@router.message(F.text == "Назад", StateFilter(
    DevStates.mod_host, DevStates.mod_port, DevStates.mod_user,
    DevStates.mod_pass, DevStates.mod_zip,
    DevStates.sql_host, DevStates.sql_port, DevStates.sql_user,
    DevStates.sql_pass, DevStates.sql_db, DevStates.sql_file,
))
async def dev_back_from_state(message: Message, state: FSMContext):
    await state.clear()
    await dev_menu(message, state)


@router.message(F.text == "Назад")
async def dev_back(message: Message, state: FSMContext):
    from keyboards import main_menu_kb, admin_main_menu_kb
    await state.clear()
    kb = admin_main_menu_kb() if message.from_user.id in ADMIN_IDS else main_menu_kb()
    await message.answer(
        f'{_e("5893057118545646106","◀️")} Главное меню',
        reply_markup=kb
    )


@router.message(F.text == "Отмена", StateFilter(
    DevStates.mod_host, DevStates.mod_port, DevStates.mod_user,
    DevStates.mod_pass, DevStates.mod_zip,
    DevStates.sql_host, DevStates.sql_port, DevStates.sql_user,
    DevStates.sql_pass, DevStates.sql_db, DevStates.sql_file,
))
async def dev_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f'{_e("5870633910337015697","✅")} Отменено.',
        reply_markup=dev_menu_kb()
    )


# ══════════════════════════════════════════════════════════════════════
#  ЗАЛИВ МОДА  →  шаги: хост → порт → юзер → пароль → zip
# ══════════════════════════════════════════════════════════════════════

@router.message(F.text == "Залив мода")
async def mod_step_host(message: Message, state: FSMContext):
    await _ask(message,
        f'<b>{_e("5940433880585605708","🔨")} Залив мода</b>\n\n'
        f'{_e("5870982283724328568","⚙")} Шаг 1/4 — Введи <b>IP или домен</b> сервера:',
        state, DevStates.mod_host)


@router.message(StateFilter(DevStates.mod_host))
async def mod_step_port(message: Message, state: FSMContext):
    await state.update_data(mod_host=message.text.strip())
    await _ask(message,
        f'{_e("5870982283724328568","⚙")} Шаг 2/4 — Введи <b>порт FTP</b> (обычно 21):',
        state, DevStates.mod_port)


@router.message(StateFilter(DevStates.mod_port))
async def mod_step_user(message: Message, state: FSMContext):
    port = message.text.strip()
    if not port.isdigit():
        await message.answer(f'{_e("5870657884844462243","❌")} Порт должен быть числом!')
        return
    await state.update_data(mod_port=int(port))
    await _ask(message,
        f'{_e("5870982283724328568","⚙")} Шаг 3/4 — Введи <b>логин FTP</b>:',
        state, DevStates.mod_user)


@router.message(StateFilter(DevStates.mod_user))
async def mod_step_pass(message: Message, state: FSMContext):
    await state.update_data(mod_user=message.text.strip())
    await _ask(message,
        f'{_e("5870982283724328568","⚙")} Шаг 4/4 — Введи <b>пароль FTP</b>:',
        state, DevStates.mod_pass)


@router.message(StateFilter(DevStates.mod_pass))
async def mod_step_zip(message: Message, state: FSMContext):
    await state.update_data(mod_pass=message.text.strip(), mod_path="/")
    data = await state.get_data()
    await state.set_state(DevStates.mod_zip)
    await message.answer(
        f'{_e("5870528606328852614","📁")} Теперь отправь <b>.zip</b> архив с модом.\n\n'
        f'{_e("6028435952299413210","ℹ")} Параметры:\n'
        f'Хост: <code>{data["mod_host"]}:{data["mod_port"]}</code>\n'
        f'Юзер: <code>{data["mod_user"]}</code>\n'
        f'Путь: <code>/</code>\n\n'
        f'Бот удалит все <b>папки</b> и <code>server.cfg</code>, затем распакует архив.',
        reply_markup=cancel_dev_kb()
    )


@router.message(StateFilter(DevStates.mod_zip), F.document)
async def mod_upload(message: Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.lower().endswith(".zip"):
        await message.answer(f'{_e("5870657884844462243","❌")} Нужен файл <b>.zip</b>!')
        return

    data = await state.get_data()
    await state.clear()

    # Позиция в очереди
    queue_pos = len(_task_queue) + (1 if _current_task and not _current_task.done() else 0)
    if queue_pos > 0:
        status = await message.answer(
            f'{_e("5983150113483134607","⏰")} Задача добавлена в очередь. '
            f'Позиция: <b>{queue_pos + 1}</b>\nОжидайте...'
        )
    else:
        status = await message.answer(f'{_e("5345906554510012647","🔄")} Скачиваю архив...')

    task = asyncio.create_task(_run_mod_upload(message, status, data, doc))
    _task_queue.append(task)
    asyncio.create_task(_process_queue())


async def _process_queue():
    global _current_task
    async with _queue_lock:
        if _current_task and not _current_task.done():
            return
        if not _task_queue:
            return
        _current_task = _task_queue.popleft()

    await _current_task


async def _run_mod_upload(message: Message, status: Message, data: dict, doc):
    global _current_task

    # Обновляем статус если была очередь
    try:
        await status.edit_text(f'{_e("5345906554510012647","🔄")} Скачиваю архив...')
    except Exception:
        pass

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "mod.zip")
        extract_path = os.path.join(tmpdir, "extracted")
        os.makedirs(extract_path)

        try:
            file = await message.bot.get_file(doc.file_id)
            await message.bot.download_file(file.file_path, zip_path)
        except Exception as e:
            await status.edit_text(
                f'{_e("5870657884844462243","❌")} Ошибка скачивания:\n<code>{e}</code>',
            )
            await message.answer(_e("5870982283724328568","⚙"), reply_markup=dev_menu_kb())
            return

        await status.edit_text(f'{_e("5345906554510012647","🔄")} Распаковываю архив...')

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_path)
        except zipfile.BadZipFile:
            await status.edit_text(
                f'{_e("5870657884844462243","❌")} Архив повреждён!',
            )
            await message.answer(_e("5870982283724328568","⚙"), reply_markup=dev_menu_kb())
            return

        await status.edit_text(f'{_e("5345906554510012647","🔄")} Подключаюсь по FTP...')

        ftp_path = "/"

        try:
            async with aioftp.Client.context(
                data["mod_host"],
                port=data["mod_port"],
                user=data["mod_user"],
                password=data["mod_pass"],
            ) as ftp:
                await status.edit_text(f'{_e("5345906554510012647","🔄")} Удаляю старые файлы...')

                try:
                    async for path, info in ftp.list(ftp_path):
                        name = path.name
                        try:
                            if info["type"] == "dir":
                                await ftp.remove(path)
                            elif name == "server.cfg":
                                await ftp.remove(path)
                        except Exception as e:
                            log.warning(f"Удаление {name}: {e}")
                except Exception as e:
                    log.warning(f"Листинг FTP: {e}")

                await status.edit_text(f'{_e("5345906554510012647","🔄")} Заливаю мод...')

                uploaded = 0
                for root, dirs, files in os.walk(extract_path):
                    rel_root = os.path.relpath(root, extract_path)
                    remote_root = ftp_path if rel_root == "." else f"{ftp_path}/{rel_root.replace(os.sep, '/')}"

                    try:
                        await ftp.make_directory(remote_root)
                    except Exception:
                        pass

                    for fname in files:
                        local = os.path.join(root, fname)
                        remote = f"{remote_root}/{fname}"
                        await ftp.upload(local, remote, write_into=True)
                        uploaded += 1

                await status.edit_text(
                    f'{_e("5870633910337015697","✅")} <b>Мод залит успешно!</b>\n\n'
                    f'{_e("5870528606328852614","📁")} Файлов загружено: <b>{uploaded}</b>\n'
                    f'{_e("5870982283724328568","⚙")} Путь: <code>{ftp_path}</code>',
                )
                await message.answer(_e("5870982283724328568","⚙"), reply_markup=dev_menu_kb())

        except aioftp.StatusCodeError as e:
            await status.edit_text(
                f'{_e("5870657884844462243","❌")} Ошибка FTP:\n<code>{e}</code>',
            )
            await message.answer(_e("5870982283724328568","⚙"), reply_markup=dev_menu_kb())
        except Exception as e:
            log.exception("mod_upload")
            await status.edit_text(
                f'{_e("5870657884844462243","❌")} Ошибка:\n<code>{e}</code>',
            )
            await message.answer(_e("5870982283724328568","⚙"), reply_markup=dev_menu_kb())

    # Следующая задача в очереди
    asyncio.create_task(_process_queue())


@router.message(StateFilter(DevStates.mod_zip))
async def mod_wrong(message: Message):
    await message.answer(f'{_e("5870657884844462243","❌")} Отправь <b>.zip</b> файл!')


# ══════════════════════════════════════════════════════════════════════
#  ИМПОРТ БАЗЫ (.sql) → шаги: хост → порт → юзер → пароль → БД → файл
# ══════════════════════════════════════════════════════════════════════

@router.message(F.text == "Импорт базы (.sql)")
async def sql_step_host(message: Message, state: FSMContext):
    await _ask(message,
        f'<b>{_e("5870528606328852614","📁")} Импорт базы данных</b>\n\n'
        f'{_e("5870982283724328568","⚙")} Шаг 1/5 — Введи <b>хост MySQL</b>:\n'
        f'Пример: <code>localhost</code> или <code>127.0.0.1</code>',
        state, DevStates.sql_host)


@router.message(StateFilter(DevStates.sql_host))
async def sql_step_port(message: Message, state: FSMContext):
    await state.update_data(sql_host=message.text.strip())
    await _ask(message,
        f'{_e("5870982283724328568","⚙")} Шаг 2/5 — Введи <b>порт MySQL</b> (обычно 3306):',
        state, DevStates.sql_port)


@router.message(StateFilter(DevStates.sql_port))
async def sql_step_user(message: Message, state: FSMContext):
    port = message.text.strip()
    if not port.isdigit():
        await message.answer(f'{_e("5870657884844462243","❌")} Порт должен быть числом!')
        return
    await state.update_data(sql_port=int(port))
    await _ask(message,
        f'{_e("5870982283724328568","⚙")} Шаг 3/5 — Введи <b>логин MySQL</b>:',
        state, DevStates.sql_user)


@router.message(StateFilter(DevStates.sql_user))
async def sql_step_pass(message: Message, state: FSMContext):
    await state.update_data(sql_user=message.text.strip())
    await _ask(message,
        f'{_e("5870982283724328568","⚙")} Шаг 4/5 — Введи <b>пароль MySQL</b>:',
        state, DevStates.sql_pass)


@router.message(StateFilter(DevStates.sql_pass))
async def sql_step_db(message: Message, state: FSMContext):
    await state.update_data(sql_pass=message.text.strip())
    await _ask(message,
        f'{_e("5870982283724328568","⚙")} Шаг 5/5 — Введи <b>название базы данных</b>:',
        state, DevStates.sql_db)


@router.message(StateFilter(DevStates.sql_db))
async def sql_step_file(message: Message, state: FSMContext):
    await state.update_data(sql_db=message.text.strip())
    data = await state.get_data()
    await state.set_state(DevStates.sql_file)
    await message.answer(
        f'{_e("5870676941614354370","🖋")} Теперь отправь <b>.sql</b> файл дампа.\n\n'
        f'{_e("6028435952299413210","ℹ")} Параметры:\n'
        f'Хост: <code>{data["sql_host"]}:{data["sql_port"]}</code>\n'
        f'Юзер: <code>{data["sql_user"]}</code>\n'
        f'База: <code>{data["sql_db"]}</code>\n\n'
        f'{_e("5870657884844462243","❌")} <b>Данные в базе будут перезаписаны!</b>',
        reply_markup=cancel_dev_kb()
    )


@router.message(StateFilter(DevStates.sql_file), F.document)
async def sql_upload(message: Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.lower().endswith(".sql"):
        await message.answer(f'{_e("5870657884844462243","❌")} Нужен файл <b>.sql</b>!')
        return

    data = await state.get_data()
    await state.clear()
    status = await message.answer(f'{_e("5345906554510012647","🔄")} Скачиваю дамп...')

    with tempfile.TemporaryDirectory() as tmpdir:
        sql_path = os.path.join(tmpdir, "dump.sql")
        file = await message.bot.get_file(doc.file_id)
        await message.bot.download_file(file.file_path, sql_path)

        await status.edit_text(f'{_e("5345906554510012647","🔄")} Импортирую в MySQL...')

        try:
            with open(sql_path, "r", encoding="utf-8", errors="replace") as f:
                sql_content = f.read()

            statements = [s.strip() for s in sql_content.split(";") if s.strip()]

            conn = await aiomysql.connect(
                host=data["sql_host"], port=data["sql_port"],
                user=data["sql_user"], password=data["sql_pass"],
                db=data["sql_db"], charset="utf8mb4", autocommit=True
            )

            executed = errors = 0
            async with conn.cursor() as cur:
                for stmt in statements:
                    try:
                        await cur.execute(stmt)
                        executed += 1
                    except Exception as e:
                        log.warning(f"SQL: {e}")
                        errors += 1
            conn.close()

            err_line = f'\n{_e("5870657884844462243","❌")} Ошибок: <b>{errors}</b>' if errors else ""
            await status.edit_text(
                f'{_e("5870633910337015697","✅")} <b>База импортирована!</b>\n\n'
                f'{_e("5870921681735781843","📊")} Запросов выполнено: <b>{executed}</b>{err_line}',
            )
            await message.answer(_e("5870982283724328568","⚙"), reply_markup=dev_menu_kb())

        except aiomysql.Error as e:
            await status.edit_text(
                f'{_e("5870657884844462243","❌")} Ошибка MySQL:\n<code>{e}</code>',
            )
            await message.answer(_e("5870982283724328568","⚙"), reply_markup=dev_menu_kb())
        except Exception as e:
            log.exception("sql_upload")
            await status.edit_text(
                f'{_e("5870657884844462243","❌")} Ошибка:\n<code>{e}</code>',
            )
            await message.answer(_e("5870982283724328568","⚙"), reply_markup=dev_menu_kb())


@router.message(StateFilter(DevStates.sql_file))
async def sql_wrong(message: Message):
    await message.answer(f'{_e("5870657884844462243","❌")} Отправь <b>.sql</b> файл!')
