"""
Хендлер «Купить Проект» — 200 звёзд.

Пайплайн после оплаты:
  1. Бот просит IP и порт сервера
  2. Просит название сервера
  3. Просит данные MySQL (host / user / pass / db)
  4. Просит donate и money (из server_settings.ini)
  5. Бот авто-заменяет mysql_settings.ini и server_settings.ini в моде
  6. Просит данные FTP → заливает мод
  7. Заменяет IP+порт в jni/game/hooks.cpp → компилирует NDK
  8. Вставляет libplugin.so в лаунчер APK
  9. Отправляет APK покупателю временной ссылкой на 48 ч
"""

import asyncio
import ftplib
import ipaddress
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

import aiohttp
import aiomysql
from aiohttp import web
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, FSInputFile,
    LabeledPrice, PreCheckoutQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import ADMIN_IDS, PROJECT_PUBLIC_URL, PROJECT_WEB_HOST, PROJECT_WEB_PORT

router = Router()
log = logging.getLogger(__name__)

# ─── Пути к ресурсам ────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent.parent
MOD_ZIP_PATH    = BASE_DIR / "project_assets" / "mod.zip"        # архив мода
JNI_ZIP_PATH    = BASE_DIR / "project_assets" / "jni.zip"        # джини (исходники)
LAUNCHER_PATH   = BASE_DIR / "project_assets" / "launcher.apk"   # шаблон лаунчера
DONE_DIR        = BASE_DIR / "projects_done"                      # готовые APK
WORK_DIR        = BASE_DIR / "tmp_projects"                       # временные сборки

PROJECT_PRICE   = 200          # звёзд
LINK_TTL        = 48 * 3600    # время жизни ссылки (секунды)

# Публичный адрес VDS для раздачи APK
PUBLIC_URL = PROJECT_PUBLIC_URL.rstrip("/")
WEB_HOST   = PROJECT_WEB_HOST
WEB_PORT   = PROJECT_WEB_PORT

# ndk-build: ищем по стандартным путям
NDK_BUILD_TIMEOUT = 25 * 60   # 25 мин


# ─── Вспомогательные ────────────────────────────────────────────────────────────

def _e(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отмена", icon_custom_emoji_id="5870657884844462243", style="danger")]],
        resize_keyboard=True,
    )


def main_menu_kb(is_admin: bool = False):
    from keyboards import admin_main_menu_kb, main_menu_kb as _main
    return admin_main_menu_kb() if is_admin else _main()


# ─── FSM ────────────────────────────────────────────────────────────────────────

class ProjectStates(StatesGroup):
    server_ip    = State()   # IP:порт сервера
    server_name  = State()   # название сервера
    mysql_host   = State()
    mysql_user   = State()
    mysql_pass   = State()
    mysql_db     = State()
    donate_money = State()   # "donate: 1000\nmoney: 50000000"
    ftp_host     = State()
    ftp_port     = State()
    ftp_user     = State()
    ftp_pass     = State()
    building     = State()   # идёт сборка


# ─── Оплата (invoice из магазина) ───────────────────────────────────────────────

@router.message(F.text.startswith("Купить Проект —"))
async def buy_project(message: Message, state: FSMContext):
    """Резервный обработчик на случай старых кнопок."""
    uid = message.from_user.id
    if uid in ADMIN_IDS:
        await _start_collection(message, state)
        return
    await message.answer_invoice(
        title="Проект BR NEW 2026",
        description="Готовая сборка под ваш сервер. Бот настроит, зальёт и отдаст лаунчер автоматически.",
        payload="project_brnew2026",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Проект BR NEW 2026", amount=PROJECT_PRICE)],
    )


@router.pre_checkout_query(F.invoice_payload == "project_brnew2026")
async def project_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def project_paid(message: Message, state: FSMContext):
    if message.successful_payment.invoice_payload != "project_brnew2026":
        return
    await _start_collection(message, state)


# ─── Сбор данных ────────────────────────────────────────────────────────────────

async def _start_collection(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ProjectStates.server_ip)
    await message.answer(
        f'{_e("5870633910337015697","✅")} <b>Оплата прошла! Начинаем настройку.</b>\n\n'
        f'{_e("5870982283724328568","⚙")} <b>Шаг 1/7 — IP сервера</b>\n\n'
        f'{_e("6042011682497106307","📍")} Введи <b>IP и порт</b> своего сервера:\n'
        f'<i>Пример: 185.189.255.97:7777</i>',
        reply_markup=cancel_kb(),
    )


@router.message(F.text == "Отмена", StateFilter(
    ProjectStates.server_ip, ProjectStates.server_name,
    ProjectStates.mysql_host, ProjectStates.mysql_user,
    ProjectStates.mysql_pass, ProjectStates.mysql_db,
    ProjectStates.donate_money,
    ProjectStates.ftp_host, ProjectStates.ftp_port,
    ProjectStates.ftp_user, ProjectStates.ftp_pass,
))
async def project_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f'{_e("5870657884844462243","❌")} Настройка отменена.',
        reply_markup=main_menu_kb(message.from_user.id in ADMIN_IDS),
    )


# Шаг 1: IP:порт
@router.message(ProjectStates.server_ip)
async def project_step_ip(message: Message, state: FSMContext):
    text = message.text.strip()
    parsed = _parse_ip_port(text)
    if not parsed:
        await message.answer(
            f'{_e("5870657884844462243","❌")} Неверный формат. Пример: <b>185.189.255.97:7777</b>',
            reply_markup=cancel_kb(),
        )
        return
    await state.update_data(server_ip=parsed[0], server_port=parsed[1])
    await state.set_state(ProjectStates.server_name)
    await message.answer(
        f'{_e("5870982283724328568","⚙")} <b>Шаг 2/7 — Название сервера</b>\n\n'
        f'Введи <b>название</b> своего сервера:\n'
        f'<i>Пример: VoltmensBot</i>',
        reply_markup=cancel_kb(),
    )


# Шаг 2: название сервера
@router.message(ProjectStates.server_name)
async def project_step_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2 or len(name) > 64:
        await message.answer(f'{_e("5870657884844462243","❌")} Название от 2 до 64 символов.')
        return
    await state.update_data(server_name=name)
    await state.set_state(ProjectStates.mysql_host)
    await message.answer(
        f'{_e("5870982283724328568","⚙")} <b>Шаг 3/7 — MySQL хост</b>\n\n'
        f'Введи <b>хост базы данных</b>:\n'
        f'<i>Пример: 185.189.255.97</i>',
        reply_markup=cancel_kb(),
    )


# Шаг 3: MySQL host
@router.message(ProjectStates.mysql_host)
async def project_step_mysql_host(message: Message, state: FSMContext):
    host = message.text.strip()
    if not _valid_host(host):
        await message.answer(f'{_e("5870657884844462243","❌")} Неверный хост. Пример: <b>185.189.255.97</b>')
        return
    await state.update_data(mysql_host=host)
    await state.set_state(ProjectStates.mysql_user)
    await message.answer(
        f'{_e("5870982283724328568","⚙")} <b>Шаг 4/7 — MySQL логин</b>\n\nВведи <b>логин</b> базы данных:',
        reply_markup=cancel_kb(),
    )


# Шаг 4: MySQL user
@router.message(ProjectStates.mysql_user)
async def project_step_mysql_user(message: Message, state: FSMContext):
    await state.update_data(mysql_user=message.text.strip())
    await state.set_state(ProjectStates.mysql_pass)
    await message.answer(
        f'{_e("5870982283724328568","⚙")} <b>Шаг 5/7 — MySQL пароль</b>\n\nВведи <b>пароль</b> базы данных:',
        reply_markup=cancel_kb(),
    )


# Шаг 5: MySQL pass → запрашиваем db = user (авто)
@router.message(ProjectStates.mysql_pass)
async def project_step_mysql_pass(message: Message, state: FSMContext):
    await state.update_data(mysql_pass=message.text.strip())
    data = await state.get_data()
    # база данных = логин (стандарт на игровых хостингах)
    await state.update_data(mysql_db=data["mysql_user"])
    await state.set_state(ProjectStates.donate_money)
    await message.answer(
        f'{_e("5870982283724328568","⚙")} <b>Шаг 6/7 — Бонусы игрокам</b>\n\n'
        f'Введи значения <b>donate</b> и <b>money</b> для server_settings.ini:\n\n'
        f'<i>Пример:\n'
        f'money: 1000\n'
        f'donate: 1000\n'
        f'nameserver: Test Server</i>',
        reply_markup=cancel_kb(),
    )


# Шаг 6: donate/money/nameserver
@router.message(ProjectStates.donate_money)
async def project_step_donate(message: Message, state: FSMContext):
    parsed = _parse_settings(message.text.strip())
    if parsed is None:
        await message.answer(
            f'{_e("5870657884844462243","❌")} Неверный формат. Пример:\n'
            f'<code>money: 1000\ndonate: 1000\nnameserver: Test Server</code>',
            reply_markup=cancel_kb(),
        )
        return
    await state.update_data(**parsed)
    await state.set_state(ProjectStates.ftp_host)
    await message.answer(
        f'{_e("5870982283724328568","⚙")} <b>Шаг 7/7 — FTP данные</b>\n\n'
        f'Введи <b>хост FTP</b>:\n'
        f'<i>Пример: 185.189.255.97</i>',
        reply_markup=cancel_kb(),
    )


# Шаг 7a: FTP host
@router.message(ProjectStates.ftp_host)
async def project_step_ftp_host(message: Message, state: FSMContext):
    host = message.text.strip()
    if not _valid_host(host):
        await message.answer(f'{_e("5870657884844462243","❌")} Неверный хост FTP.')
        return
    await state.update_data(ftp_host=host)
    await state.set_state(ProjectStates.ftp_port)
    await message.answer(
        f'Введи <b>порт FTP</b> (обычно 21):',
        reply_markup=cancel_kb(),
    )


# Шаг 7b: FTP port
@router.message(ProjectStates.ftp_port)
async def project_step_ftp_port(message: Message, state: FSMContext):
    try:
        port = int(message.text.strip())
        assert 1 <= port <= 65535
    except Exception:
        await message.answer(f'{_e("5870657884844462243","❌")} Порт должен быть числом от 1 до 65535.')
        return
    await state.update_data(ftp_port=port)
    await state.set_state(ProjectStates.ftp_user)
    await message.answer(f'Введи <b>логин FTP</b>:', reply_markup=cancel_kb())


# Шаг 7c: FTP user
@router.message(ProjectStates.ftp_user)
async def project_step_ftp_user(message: Message, state: FSMContext):
    await state.update_data(ftp_user=message.text.strip())
    await state.set_state(ProjectStates.ftp_pass)
    await message.answer(f'Введи <b>пароль FTP</b>:', reply_markup=cancel_kb())


# Шаг 7d: FTP pass → всё собрано, начинаем сборку
@router.message(ProjectStates.ftp_pass)
async def project_step_ftp_pass(message: Message, state: FSMContext):
    await state.update_data(ftp_pass=message.text.strip())
    data = await state.get_data()
    await state.set_state(ProjectStates.building)

    status = await message.answer(
        f'{_e("5345906554510012647","🔄")} Все данные получены. Начинаю сборку...\n\n'
        f'{_e("5870982283724328568","⚙")} Это может занять до 5 минут.',
        reply_markup=main_menu_kb(message.from_user.id in ADMIN_IDS),
    )

    asyncio.create_task(_build_project(message, status, data, state))


# ─── Основная сборка ─────────────────────────────────────────────────────────────

async def _build_project(message: Message, status: Message, data: dict, state: FSMContext):
    uid = message.from_user.id
    work = None
    try:
        work = Path(tempfile.mkdtemp(dir=WORK_DIR, prefix=f"proj_{uid}_"))
        DONE_DIR.mkdir(parents=True, exist_ok=True)

        # ── 1. Распаковка мода ──────────────────────────────────────────────────
        await _edit(status, f'{_e("5345906554510012647","🔄")} Шаг 1/5 — Распаковка мода...')
        mod_dir = work / "mod"
        await asyncio.to_thread(_extract, MOD_ZIP_PATH, mod_dir)

        # ── 2. Правка конфигов ──────────────────────────────────────────────────
        await _edit(status, f'{_e("5345906554510012647","🔄")} Шаг 2/6 — Настройка конфигов...')
        await asyncio.to_thread(_patch_configs, mod_dir, data)

        # ── 3. Импорт базы данных ───────────────────────────────────────────────
        await _edit(status, f'{_e("5345906554510012647","🔄")} Шаг 3/6 — Импорт базы данных...')
        sql_file = _find_sql(mod_dir)
        if sql_file:
            await _import_sql(sql_file, data)
        else:
            log.warning("SQL файл не найден в моде, пропускаем импорт")

        # ── 4. Залив мода на FTP ────────────────────────────────────────────────
        await _edit(status, f'{_e("5345906554510012647","🔄")} Шаг 4/6 — Заливка мода на FTP...')
        uploaded = await asyncio.to_thread(
            _ftp_upload, mod_dir,
            data["ftp_host"], data["ftp_port"],
            data["ftp_user"], data["ftp_pass"],
        )

        # ── 5. Сборка JNI ───────────────────────────────────────────────────────
        await _edit(status, f'{_e("5345906554510012647","🔄")} Шаг 5/6 — Компиляция клиента...')
        lib_path = await asyncio.to_thread(_build_jni, work, data["server_ip"], data["server_port"])

        # ── 6. Сборка APK ───────────────────────────────────────────────────────
        await _edit(status, f'{_e("5345906554510012647","🔄")} Шаг 6/6 — Сборка лаунчера...')
        apk_path = DONE_DIR / f"{uid}_{int(time.time())}.apk"
        await asyncio.to_thread(_patch_apk, lib_path, apk_path)

        # ── Отдаём APK ──────────────────────────────────────────────────────────
        await _edit(status, f'{_e("5870633910337015697","✅")} Готово! Отправляю лаунчер...')
        await _deliver_apk(message, apk_path, data, uploaded)

    except Exception as e:
        log.exception("Ошибка сборки проекта uid=%s", uid)
        await _edit(status, f'{_e("5870657884844462243","❌")} Ошибка сборки:\n<code>{e}</code>')
    finally:
        await state.clear()
        if work and work.exists():
            await asyncio.to_thread(shutil.rmtree, str(work), True)


async def _edit(msg: Message, text: str):
    try:
        await msg.edit_text(text)
    except Exception:
        pass


# ─── Патч конфигов ──────────────────────────────────────────────────────────────

def _patch_configs(mod_dir: Path, data: dict):
    # mysql_settings.ini
    mysql_ini = mod_dir / "scriptfiles" / "mysql_settings.ini"
    if mysql_ini.exists():
        _patch_ini(mysql_ini, {
            "host":     data["mysql_host"],
            "username": data["mysql_user"],
            "password": data["mysql_pass"],
        })

    # server_settings.ini
    srv_ini = mod_dir / "scriptfiles" / "server_settings.ini"
    if srv_ini.exists():
        _patch_ini(srv_ini, {
            "nameserver": data.get("nameserver", data["server_name"]),
            "donate":     str(data.get("donate", 1000)),
            "money":      str(data.get("money", 1000)),
        })


def _patch_ini(path: Path, updates: dict):
    raw = path.read_bytes()
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            pass
    else:
        text = raw.decode("latin-1")

    lines = []
    seen = set()
    for line in text.splitlines(keepends=True):
        m = re.match(r"^(\s*)(\w+)(\s*=\s*)(.*)(\r?\n?)$", line)
        if m and m.group(2) in updates:
            key = m.group(2)
            lines.append(f"{m.group(1)}{key}{m.group(3)}{updates[key]}{m.group(5)}")
            seen.add(key)
        else:
            lines.append(line)
    # добавляем недостающие
    eol = "\r\n" if "\r\n" in text else "\n"
    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k} = {v}{eol}")
    path.write_bytes("".join(lines).encode(enc, errors="replace"))


# ─── SQL Импорт ──────────────────────────────────────────────────────────────────

def _find_sql(mod_dir: Path) -> Path | None:
    """Ищет SQL файл в архиве мода — сначала по имени sql.sql, потом самый большой .sql"""
    # Ищем в корне мода
    for name in ("sql.sql", "bd.sql", "database.sql", "dump.sql"):
        p = mod_dir / name
        if p.exists():
            return p
    # Ищем самый большой .sql во всём дереве
    sql_files = list(mod_dir.rglob("*.sql"))
    if not sql_files:
        return None
    return max(sql_files, key=lambda f: f.stat().st_size)


async def _import_sql(sql_file: Path, data: dict):
    """Импортирует SQL дамп в MySQL покупателя."""
    host    = data["mysql_host"]
    user    = data["mysql_user"]
    password = data["mysql_pass"]
    db      = data["mysql_db"]

    sql_text = sql_file.read_bytes()

    conn = await aiomysql.connect(
        host=host, port=3306,
        user=user, password=password,
        db=db,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=15,
    )
    try:
        async with conn.cursor() as cur:
            # Разбиваем по ; и выполняем каждый запрос
            for enc in ("utf-8", "cp1251", "latin-1"):
                try:
                    text = sql_text.decode(enc)
                    break
                except Exception:
                    pass
            else:
                text = sql_text.decode("latin-1")

            # Убираем комментарии и разбиваем на запросы
            statements = _split_sql(text)
            for stmt in statements:
                stmt = stmt.strip()
                if stmt:
                    try:
                        await cur.execute(stmt)
                    except aiomysql.Error as e:
                        # Игнорируем «таблица уже существует» и «дублирующая колонка»
                        if e.args[0] not in (1050, 1060, 1061, 1062):
                            log.warning("SQL ошибка (пропускаем): %s", e)
    finally:
        conn.close()


def _split_sql(text: str) -> list:
    """Разбивает SQL дамп на отдельные запросы, игнорируя ; внутри строк."""
    statements = []
    current = []
    in_string = False
    string_char = ""
    i = 0
    while i < len(text):
        ch = text[i]
        # однострочный комментарий
        if not in_string and text[i:i+2] in ("--", "# "):
            end = text.find("\n", i)
            i = end + 1 if end != -1 else len(text)
            continue
        # многострочный комментарий
        if not in_string and text[i:i+2] == "/*":
            end = text.find("*/", i + 2)
            i = end + 2 if end != -1 else len(text)
            continue
        if ch in ("'", '"', "`") and not in_string:
            in_string = True
            string_char = ch
        elif in_string and ch == string_char and (i == 0 or text[i-1] != "\\"):
            in_string = False
        elif ch == ";" and not in_string:
            current.append(ch)
            statements.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    if "".join(current).strip():
        statements.append("".join(current))
    return statements


# ─── FTP Upload ──────────────────────────────────────────────────────────────────

def _ftp_clean(ftp: ftplib.FTP, path: str = "/"):
    """Удаляет все папки и server.cfg в корне FTP."""
    try:
        items = []
        ftp.retrlines(f"LIST {path}", items.append)
        for item in items:
            parts = item.split(None, 8)
            if len(parts) < 9:
                continue
            name = parts[8]
            is_dir = item.startswith("d")
            if is_dir:
                try:
                    _ftp_rmtree(ftp, f"{path}/{name}".replace("//", "/"))
                except Exception as e:
                    log.warning("FTP rmtree %s: %s", name, e)
            elif name == "server.cfg":
                try:
                    ftp.delete(f"{path}/{name}".replace("//", "/"))
                except Exception as e:
                    log.warning("FTP delete server.cfg: %s", e)
    except Exception as e:
        log.warning("FTP clean: %s", e)


def _ftp_rmtree(ftp: ftplib.FTP, path: str):
    """Рекурсивно удаляет папку на FTP."""
    items = []
    try:
        ftp.retrlines(f"LIST {path}", items.append)
    except Exception:
        pass
    for item in items:
        parts = item.split(None, 8)
        if len(parts) < 9:
            continue
        name = parts[8]
        full = f"{path}/{name}"
        if item.startswith("d"):
            _ftp_rmtree(ftp, full)
        else:
            try:
                ftp.delete(full)
            except Exception:
                pass
    try:
        ftp.rmd(path)
    except Exception:
        pass


def _ftp_upload(mod_dir: Path, host: str, port: int, user: str, password: str) -> int:
    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=30)
    ftp.login(user, password)
    ftp.encoding = "utf-8"

    # Очищаем перед заливом
    _ftp_clean(ftp, "/")

    created = set()
    count = 0

    def ensure_dir(parts):
        path = ""
        for p in parts:
            path = f"{path}/{p}" if path else p
            if path not in created:
                try:
                    ftp.mkd(path)
                except ftplib.error_perm:
                    pass
                created.add(path)

    for local in mod_dir.rglob("*"):
        if local.is_dir():
            continue
        rel = local.relative_to(mod_dir)
        parts = list(rel.parts)
        ensure_dir(parts[:-1])
        remote = "/".join(parts)
        with open(local, "rb") as f:
            ftp.storbinary(f"STOR {remote}", f)
        count += 1

    try:
        ftp.quit()
    except Exception:
        pass
    return count


# ─── JNI Сборка ─────────────────────────────────────────────────────────────────

_CONNECT_RE = re.compile(
    r'(pRakClient->Connect\(\s*xorstr\(\s*")([^"]+)("\s*\)\s*,\s*)(\d+)(\s*,)'
)


def _build_jni(work: Path, server_ip: str, server_port: int) -> Path:
    jni_dir = work / "jni"
    _extract(JNI_ZIP_PATH, jni_dir)

    # ищем hooks.cpp — может быть во вложенной папке
    hooks_files = list(jni_dir.rglob("hooks.cpp"))
    if not hooks_files:
        raise RuntimeError("hooks.cpp не найден в архиве джини")
    hooks = hooks_files[0]

    src = hooks.read_text(encoding="utf-8", errors="surrogateescape")

    def replacer(m):
        return f"{m.group(1)}{server_ip}{m.group(3)}{server_port}{m.group(5)}"

    new_src, n = _CONNECT_RE.subn(replacer, src)
    if n == 0:
        # Прямая замена заглушек «тут айпи» / «тут порт»
        new_src = re.sub(r'xorstr\("тут айпи"\)', f'xorstr("{server_ip}")', src)
        new_src = re.sub(r',\s*тут порт\s*,', f', {server_port},', new_src)
    hooks.write_text(new_src, encoding="utf-8", errors="surrogateescape")

    # Ищем корень проекта (папка с jni/Android.mk)
    proj_root = hooks.parent.parent.parent  # jni/game/hooks.cpp → ../../../
    android_mk = proj_root / "jni" / "Android.mk"
    if not android_mk.exists():
        # ищем
        mk_files = list(jni_dir.rglob("Android.mk"))
        if not mk_files:
            raise RuntimeError("Android.mk не найден в архиве джини")
        proj_root = mk_files[0].parent.parent

    ndk = _find_ndk()
    if not ndk:
        raise RuntimeError("ndk-build не найден на сервере")

    out_libs = proj_root / "libs"
    cmd = [
        ndk,
        f"NDK_PROJECT_PATH={proj_root}",
        f"APP_BUILD_SCRIPT={proj_root / 'jni' / 'Android.mk'}",
        f"NDK_OUT={proj_root / 'obj'}",
        f"NDK_LIBS_OUT={out_libs}",
        f"-j{max(1, os.cpu_count() or 1)}",
    ]
    r = subprocess.run(cmd, cwd=str(proj_root), capture_output=True, text=True,
                       timeout=NDK_BUILD_TIMEOUT, errors="replace")
    if r.returncode != 0:
        tail = "\n".join((r.stdout + "\n" + r.stderr).strip().splitlines()[-20:])
        raise RuntimeError(f"ndk-build error:\n{tail[-1000:]}")

    # ищем скомпилированную библиотеку
    for abi in ("arm64-v8a", "armeabi-v7a", "armeabi"):
        lib = out_libs / abi / "libplugin.so"
        if lib.exists():
            return lib
    # ищем любую .so
    libs = list(out_libs.rglob("*.so"))
    if not libs:
        raise RuntimeError("Скомпилированная .so не найдена")
    return libs[0]


def _find_ndk():
    explicit = os.environ.get("NDK_BUILD")
    if explicit and os.path.isfile(explicit):
        return explicit
    for key in ("ANDROID_NDK_HOME", "ANDROID_NDK_ROOT", "ANDROID_NDK"):
        root = os.environ.get(key)
        if root:
            p = os.path.join(root, "ndk-build")
            if os.path.isfile(p):
                return p
    return shutil.which("ndk-build")


# ─── APK Патч ───────────────────────────────────────────────────────────────────

def _patch_apk(lib_path: Path, out_apk: Path):
    """Заменяет libplugin.so в лаунчере."""
    tmp = out_apk.with_suffix(".tmp.apk")
    with zipfile.ZipFile(LAUNCHER_PATH, "r") as zin, \
         zipfile.ZipFile(tmp, "w") as zout:
        for info in zin.infolist():
            name = info.filename
            # убираем старую подпись
            if re.match(r"META-INF/(MANIFEST\.MF|[^/]+\.(SF|RSA|DSA|EC))$", name, re.I):
                continue
            if name.endswith("libplugin.so"):
                zout.write(str(lib_path), name,
                           compress_type=zipfile.ZIP_STORED)
            else:
                zout.writestr(info, zin.read(info))
    tmp.rename(out_apk)


# ─── Доставка APK ───────────────────────────────────────────────────────────────

async def _deliver_apk(message: Message, apk_path: Path, data: dict, uploaded: int):
    caption = (
        f'{_e("5870633910337015697","✅")} <b>Проект готов!</b>\n\n'
        f'{_e("6042011682497106307","📍")} Сервер: <code>{data["server_ip"]}:{data["server_port"]}</code>\n'
        f'{_e("5886285355279193209","🏷")} Название: <b>{data["server_name"]}</b>\n'
        f'{_e("5870921681735781843","📊")} MySQL: <code>{data["mysql_host"]}</code> / <code>{data["mysql_user"]}</code>\n'
        f'{_e("5870633910337015697","✅")} База данных импортирована\n'
        f'{_e("5870528606328852614","📁")} Файлов залито на FTP: <b>{uploaded}</b>\n\n'
        f'{_e("6028435952299413210","ℹ")} Установите лаунчер и заходите на сервер!'
    )

    size = apk_path.stat().st_size

    # Если меньше 50 МБ — шлём напрямую
    if size <= 49 * 1024 * 1024:
        await message.answer_document(
            FSInputFile(str(apk_path), filename="launcher.apk"),
            caption=caption,
        )
        return

    # Иначе — временная ссылка
    if not PUBLIC_URL:
        await message.answer(
            caption + f'\n\n{_e("5870657884844462243","❌")} Не удалось отдать APK (нет PUBLIC_URL). '
                      'Обратитесь к администратору.'
        )
        return

    token = uuid.uuid4().hex
    exp = int(time.time()) + LINK_TTL
    link_file = DONE_DIR / f"link_{token}.txt"
    link_file.write_text(str(apk_path))

    url = f"{PUBLIC_URL}/dl/{token}/{exp}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Скачать лаунчер",
            url=url,
            icon_custom_emoji_id="6039802767931871481"
        )
    ]])
    await message.answer(
        caption + f'\n\n{_e("5963103826075456248","⬆")} Ссылка действует 48 часов.',
        reply_markup=kb,
    )


# ─── HTTP сервер для раздачи APK ────────────────────────────────────────────────

async def _apk_handler(request: web.Request):
    token = request.match_info["token"]
    exp   = int(request.match_info.get("exp", 0))
    if exp and time.time() > exp:
        raise web.HTTPGone(text="Ссылка устарела.")
    link_file = DONE_DIR / f"link_{token}.txt"
    if not link_file.exists():
        raise web.HTTPNotFound()
    apk_path = Path(link_file.read_text().strip())
    if not apk_path.exists():
        raise web.HTTPNotFound()
    return web.FileResponse(
        str(apk_path),
        headers={"Content-Disposition": 'attachment; filename="launcher.apk"'},
    )


async def start_download_server():
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    if not PUBLIC_URL:
        log.warning("PROJECT_PUBLIC_URL не задан — APK > 50 МБ нельзя отдать ссылкой")
        return
    app = web.Application()
    app.router.add_get("/dl/{token}/{exp}", _apk_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, WEB_HOST, WEB_PORT).start()
    log.info("Сервер APK: %s:%s → %s", WEB_HOST, WEB_PORT, PUBLIC_URL)


# ─── Утилиты ────────────────────────────────────────────────────────────────────

def _extract(zip_path: Path, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(str(dest))


def _parse_ip_port(text: str):
    """'185.189.255.97:7777' → ('185.189.255.97', 7777) или None"""
    m = re.match(r"^([\d.]+):(\d+)$", text.strip())
    if not m:
        return None
    ip_s, port_s = m.group(1), m.group(2)
    try:
        ipaddress.IPv4Address(ip_s)
        port = int(port_s)
        assert 1 <= port <= 65535
        return ip_s, port
    except Exception:
        return None


def _valid_host(host: str) -> bool:
    host = host.strip()
    try:
        ipaddress.IPv4Address(host)
        return True
    except Exception:
        pass
    # hostname
    return bool(re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", host))


def _parse_settings(text: str):
    """
    Парсит:
        money: 1000
        donate: 1000
        nameserver: Test Server
    → dict или None
    """
    result = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(\w+)\s*:\s*(.+)$", line.strip())
        if m:
            result[m.group(1).lower()] = m.group(2).strip()
    if not result:
        return None
    # числа
    for key in ("money", "donate"):
        if key in result:
            try:
                result[key] = int(re.sub(r"[^\d]", "", result[key]))
            except Exception:
                result[key] = 1000
    return result

