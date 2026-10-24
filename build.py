import logging
import tempfile
import asyncio
import subprocess
import zipfile
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter
from aiogram.types import (
    Message, CallbackQuery,
    BufferedInputFile, FSInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)

from config import ADMIN_IDS

router = Router()
log = logging.getLogger(__name__)

BANNER_BUILD_PATH = Path(__file__).parent.parent / "banner_jni.jpg"

# Глобальный лок — одна сборка за раз
build_lock = asyncio.Lock()

MAX_ZIP_SIZE_MB = 50
MAX_ZIP_SIZE_BYTES = MAX_ZIP_SIZE_MB * 1024 * 1024

# NDK пути — настройте под свой сервер
NDK_OPTIONS = [
    ("r16b", "/opt/android-ndk-r16b"),
    ("r21c", "/opt/android-ndk-r21c"),
    ("r25c", "/opt/android-ndk-r25c"),
]


class BuildStates(StatesGroup):
    waiting_zip = State()
    waiting_ndk = State()


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def _available_ndk() -> list[tuple[str, str]]:
    return [(label, path) for label, path in NDK_OPTIONS if (Path(path) / "ndk-build").exists()]


def _ndk_kb(available: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=f"NDK {label}",
            callback_data=f"ndk:{label}",
            icon_custom_emoji_id="5870982283724328568"
        )]
        for label, _ in available
    ]
    buttons.append([InlineKeyboardButton(
        text="Отмена",
        callback_data="ndk:cancel",
        icon_custom_emoji_id="5870657884844462243"
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(
            text="Назад",
            icon_custom_emoji_id="5893057118545646106"
        )]],
        resize_keyboard=True
    )


def detect_project_type(zip_path: Path) -> str:
    """Определяет тип проекта по содержимому архива."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        for name in names:
            if "jni/Android.mk" in name or name.endswith("jni/Android.mk"):
                return "jni"
        return "unknown"
    except Exception:
        return "unknown"


async def run_ndk_build(zip_path: Path, tmp_path: Path, ndk_path: str) -> tuple[Path, str]:
    """Распаковывает zip, запускает ndk-build, возвращает (libs.zip, log)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(src_dir)

    jni_parent = None
    for p in src_dir.rglob("Android.mk"):
        if p.parent.name == "jni":
            jni_parent = p.parent.parent
            break

    if not jni_parent:
        raise BuildError("Не найдена папка jni/Android.mk в архиве")

    ndk_build = Path(ndk_path) / "ndk-build"
    proc = await asyncio.create_subprocess_exec(
        str(ndk_build),
        "-C", str(jni_parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
    log_text = stdout.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        tail = "\n".join(log_text.splitlines()[-40:])
        raise BuildError(f"ndk-build завершился с ошибкой (код {proc.returncode})", log_tail=tail)

    libs_dir = jni_parent / "libs"
    out_zip_path = tmp_path / "libs.zip"
    so_files = list(libs_dir.rglob("*.so")) if libs_dir.exists() else []

    if not so_files:
        raise BuildError("Сборка прошла, но .so файлы не найдены")

    with zipfile.ZipFile(out_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for so in so_files:
            zf.write(so, so.relative_to(jni_parent))

    return out_zip_path, log_text


class BuildError(Exception):
    def __init__(self, message: str, log_tail: str = ""):
        super().__init__(message)
        self.log_tail = log_tail


# ─── Хэндлеры ─────────────────────────────────────────────────────────────────

@router.message(F.text == "Компилятор")
async def build_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(BuildStates.waiting_zip)

    available = _available_ndk()
    if not available:
        await message.answer(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            'На сервере не установлен ни один NDK. Обратитесь к администратору.'
        )
        return

    ndks = ", ".join(f"<b>{label}</b>" for label, _ in available)
    text = (
        f'<b><tg-emoji emoji-id="5940433880585605708">🔨</tg-emoji> Компилятор JNI</b>\n\n'
        f'<tg-emoji emoji-id="5870528606328852614">📁</tg-emoji> Отправьте <b>.zip</b> архив с проектом.\n'
        f'Нужна папка <code>jni/Android.mk</code> внутри.\n\n'
        f'<tg-emoji emoji-id="5870982283724328568">⚙</tg-emoji> Доступные NDK: {ndks}\n'
        f'<tg-emoji emoji-id="6028435952299413210">ℹ</tg-emoji> Максимальный размер: <b>{MAX_ZIP_SIZE_MB} МБ</b>'
    )
    if BANNER_BUILD_PATH.exists():
        await message.answer_photo(
            FSInputFile(BANNER_BUILD_PATH),
            caption=text,
            reply_markup=_cancel_kb()
        )
    else:
        await message.answer(text, reply_markup=_cancel_kb())


@router.message(F.text == "Назад", StateFilter(BuildStates.waiting_zip))
@router.message(F.text == "Назад", StateFilter(BuildStates.waiting_ndk))
async def build_cancel_text(message: Message, state: FSMContext):
    await state.clear()
    from keyboards import admin_main_menu_kb, main_menu_kb
    kb = admin_main_menu_kb() if message.from_user.id in ADMIN_IDS else main_menu_kb()
    await message.answer(
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Сборка отменена.',
        reply_markup=kb
    )


@router.message(F.document, StateFilter(BuildStates.waiting_zip))
async def handle_zip(message: Message, state: FSMContext):
    doc = message.document

    if not (doc.file_name or "").lower().endswith(".zip"):
        await message.answer(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Нужен <b>.zip</b> архив.'
        )
        return

    if doc.file_size and doc.file_size > MAX_ZIP_SIZE_BYTES:
        await message.answer(
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            f'Архив слишком большой. Максимум <b>{MAX_ZIP_SIZE_MB} МБ</b>.'
        )
        return

    if build_lock.locked():
        await message.answer(
            '<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
            'Сейчас идёт другая сборка — ваш архив встанет в очередь.'
        )

    async with build_lock:
        status = await message.answer(
            '<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
            'Принял архив, определяю тип проекта...'
        )

        tmp_dir = tempfile.mkdtemp(prefix="jnibuild_")
        tmp_path = Path(tmp_dir)
        zip_path = tmp_path / "input.zip"

        tg_file = await message.bot.get_file(doc.file_id)
        await message.bot.download_file(tg_file.file_path, destination=zip_path)

        project_type = detect_project_type(zip_path)
        if project_type == "unknown":
            await status.edit_text(
                '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
                'Не найдена папка <code>jni/Android.mk</code> в архиве.'
            )
            return

        available = _available_ndk()
        if not available:
            await status.edit_text(
                '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
                'На сервере не установлен ни один NDK.'
            )
            return

        await status.edit_text(
            '<tg-emoji emoji-id="5870982283724328568">⚙</tg-emoji> '
            'Архив принят! Выберите версию NDK для сборки:',
            reply_markup=_ndk_kb(available)
        )
        await state.set_data({"zip_path": str(zip_path), "tmp_path": str(tmp_dir)})
        await state.set_state(BuildStates.waiting_ndk)


@router.callback_query(F.data.startswith("ndk:"))
async def ndk_selected(callback: CallbackQuery, state: FSMContext):
    label = callback.data.split(":", 1)[1]

    if label == "cancel":
        await state.clear()
        await callback.answer()
        from keyboards import admin_main_menu_kb, main_menu_kb
        kb = admin_main_menu_kb() if callback.from_user.id in ADMIN_IDS else main_menu_kb()
        await callback.message.answer(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Сборка отменена.',
            reply_markup=kb
        )
        return

    ndk_path = None
    for opt_label, opt_path in _available_ndk():
        if opt_label == label:
            ndk_path = opt_path
            break

    if not ndk_path:
        await callback.answer("Неизвестная версия NDK", show_alert=True)
        return

    data = await state.get_data()
    zip_path_str = data.get("zip_path")
    tmp_path_str = data.get("tmp_path")
    await state.clear()

    if not zip_path_str:
        await callback.answer("Сессия истекла, загрузите архив снова.", show_alert=True)
        return

    await callback.answer()
    status = await callback.message.edit_text(
        f'<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
        f'Выбран NDK <b>{label}</b>. Компилирую, подождите...'
    )

    try:
        out_zip, build_log = await run_ndk_build(
            Path(zip_path_str), Path(tmp_path_str), ndk_path
        )
    except BuildError as exc:
        await status.edit_text(
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> <b>Ошибка сборки</b>\n\n{exc}'
        )
        if exc.log_tail:
            await status.answer_document(
                BufferedInputFile(exc.log_tail.encode("utf-8", errors="replace"), filename="build_log.txt"),
                caption='<tg-emoji emoji-id="5870528606328852614">📁</tg-emoji> Лог сборки'
            )
        return
    except asyncio.TimeoutError:
        await status.edit_text(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            'Превышено время сборки (5 минут). Попробуйте упростить проект.'
        )
        return
    except Exception as exc:
        log.exception("Ошибка JNI-сборки")
        await status.edit_text(
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Внутренняя ошибка: {exc}'
        )
        return

    await status.edit_text(
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
        f'Сборка завершена! NDK <b>{label}</b>\n\n'
        f'<tg-emoji emoji-id="6039802767931871481">⬇</tg-emoji> Скачайте библиотеки:'
    )
    await status.answer_document(
        BufferedInputFile(out_zip.read_bytes(), filename="libs.zip"),
        caption=(
            '<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> '
            f'Собранные <b>.so</b> библиотеки (NDK {label})'
        )
    )

