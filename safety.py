"""Единые проверки секретов, опасных вложений и запрещённых запросов.

Модуль не зависит от Telegram, HTTP и AI-провайдеров, поэтому одинаковые
правила применяются во всех точках входа приложения.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import html
import io
import os
import re
import unicodedata
import warnings
import zlib
from urllib.parse import unquote

from PIL import Image, UnidentifiedImageError


SAFE_REFUSAL_MESSAGE = (
    "Я не могу помогать создавать, улучшать, скрывать или распространять "
    "вредоносные программы и другое опасное или незаконное содержимое. "
    "Могу помочь с безопасным анализом, обнаружением, удалением и защитой."
)
SELF_HARM_SAFE_MESSAGE = (
    "Я не могу помогать со способом причинить себе вред. Если опасность "
    "непосредственная, отойдите от опасных предметов, позовите человека, "
    "которому доверяете, и обратитесь в местную экстренную службу. Я могу "
    "остаться с вами и помочь составить безопасный план на ближайшие минуты."
)

REQUEST_IN_PROGRESS_MESSAGE = (
    "У вас уже выполняется другой запрос. Дождитесь его завершения "
    "и попробуйте снова."
)
AI_DISABLED_MESSAGE = (
    "AI-функции временно отключены оператором безопасности. "
    "Попробуйте позже."
)

_SENSITIVE_EXACT_NAMES = {
    ".env",
    ".npmrc",
    ".netrc",
    ".pypirc",
    ".git-credentials",
    "credentials.json",
    "service-account.json",
    "service_account.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "id_rsa",
    "id_ed25519",
    "wallet.dat",
    ".envrc",
    "credentials",
    "kubeconfig",
    ".dockerconfigjson",
    "terraform.tfstate",
}

_DANGEROUS_EXECUTABLE_EXTENSIONS = {
    ".apk",
    ".app",
    ".bat",
    ".bin",
    ".cmd",
    ".chm",
    ".com",
    ".cpl",
    ".crx",
    ".dex",
    ".desktop",
    ".dll",
    ".dmg",
    ".docm",
    ".exe",
    ".hta",
    ".inf",
    ".ipa",
    ".iso",
    ".jar",
    ".jse",
    ".lnk",
    ".msi",
    ".msp",
    ".pif",
    ".ps1",
    ".psd1",
    ".psm1",
    ".reg",
    ".scr",
    ".scf",
    ".sct",
    ".service",
    ".sys",
    ".url",
    ".vbe",
    ".vbs",
    ".xlam",
    ".xll",
    ".xlsm",
    ".xpi",
    ".wsf",
    ".wsh",
}
_ACTIVE_OUTPUT_EXTENSIONS = {
    ".c",
    ".cmd",
    ".com",
    ".cpp",
    ".cs",
    ".go",
    ".hta",
    ".htm",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".lua",
    ".php",
    ".pl",
    ".ps1",
    ".py",
    ".rb",
    ".reg",
    ".rs",
    ".sh",
    ".swift",
    ".ts",
    ".tsx",
    ".vbs",
    ".wsf",
}

_PROBABLE_SECRET_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"(?<![A-Za-z0-9])\d{6,12}:[A-Za-z0-9_-]{25,}(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{30,}(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])nvapi-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{30,}(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{30,}(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{20,}(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])(?:sk|rk)_live_[A-Za-z0-9]{20,}(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])ya29\.[A-Za-z0-9_-]{30,}(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])",
        r"\bpostgres(?:ql)?://[^:\s/]+:[^@\s]+@[^/\s]+/[^\s]+",
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b",
        r"\bAuthorization\s*:\s*Basic\s+[A-Za-z0-9+/=]{12,}\b",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
        r"[A-Za-z0-9_-]{10,}\b",
        r"\b(?:api[\s_-]*key|access[\s_-]*token|auth[\s_-]*token|"
        r"client[\s_-]*secret|"
        r"password|passwd|aws[_-]?secret[_-]?access[_-]?key|accountkey)"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{16,}",
        r"\bAccountName=[^;\s]{2,};AccountKey=[A-Za-z0-9+/=]{20,}",
    )
)

_IGNORABLE_SECURITY_CHARS = re.compile(
    r"[\u00ad\u034f\u061c\u180e\u200b-\u200f\u202a-\u202e"
    r"\u2060-\u2069\ufeff]"
)
_LATIN_CONFUSABLES = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "ё": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "і": "i",
        "ї": "i",
        "ј": "j",
        "ѕ": "s",
        "α": "a",
        "β": "b",
        "ε": "e",
        "ι": "i",
        "κ": "k",
        "μ": "m",
        "ν": "v",
        "ο": "o",
        "ρ": "p",
        "τ": "t",
        "υ": "y",
        "χ": "x",
    }
)
_LEET_CONFUSABLES = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
    }
)

# Модерация должна оставаться заметно дешевле запроса к провайдеру. Значения
# выше этого порога отклоняются, а не обрезаются: обрезка позволила бы спрятать
# опасную часть в хвосте.
MAX_SECURITY_TEXT_CHARS = 32_000
_MAX_DECODED_VARIANTS = 64
_MAX_DECODE_DEPTH = 6
_MAX_ENCODED_TOKENS = 24


def _boolean_environment(name: str, default: str) -> bool:
    raw = os.getenv(name, default).strip().lower()
    if raw not in {
        "0", "false", "no", "off", "1", "true", "yes", "on",
    }:
        raise RuntimeError(f"{name} должен быть логическим значением")
    return raw in {"1", "true", "yes", "on"}


# Операционный kill switch не требует новой сборки или смены маршрутов.
AI_REQUESTS_ENABLED = _boolean_environment("AI_REQUESTS_ENABLED", "1")

# Недоверенные вложения остаются отключёнными, пока оператор явно не
# подключит локальную OCR/CV или независимую файловую модерацию.
ALLOW_USER_IMAGE_UPLOADS = _boolean_environment(
    "ALLOW_USER_IMAGE_UPLOADS",
    "0",
)
ALLOW_USER_FILE_UPLOADS = _boolean_environment(
    "ALLOW_USER_FILE_UPLOADS",
    "0",
)
_ENCODED_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{20,}={0,2}"
    r"(?![A-Za-z0-9+/_=-])"
)
_CHUNKED_ENCODED_RE = re.compile(
    r"(?<![A-Za-z0-9+/_-])"
    r"(?:[A-Za-z0-9+/_-]{4}(?:\s+|[.:])){5,}"
    r"[A-Za-z0-9+/_-]{2,4}={0,2}"
    r"(?![A-Za-z0-9+/_=-])"
)
_HEX_TOKEN_RE = re.compile(
    r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}){12,4096}(?![0-9A-Fa-f])"
)


def _clean_security_source(value: str) -> str:
    """Нормализует управляющие символы, не меняя регистр кодировок."""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _IGNORABLE_SECURITY_CHARS.sub("", normalized)
    normalized = "".join(
        " " if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char
        for char in normalized
    )
    normalized = " ".join(normalized.split())
    return normalized


def _security_complexity_rejection_reason(value: object) -> str | None:
    """Дешёвый fail-closed барьер перед Unicode/regex-декодированием."""
    if not isinstance(value, str) or not value:
        return None
    if len(value) > MAX_SECURITY_TEXT_CHARS:
        return "security_input_too_large"

    ignorable_count = sum(
        1
        for char in value
        if (
            char == "\u00ad"
            or char == "\u034f"
            or char == "\u061c"
            or char == "\u180e"
            or "\u200b" <= char <= "\u200f"
            or "\u202a" <= char <= "\u202e"
            or "\u2060" <= char <= "\u2069"
            or char == "\ufeff"
        )
    )
    if ignorable_count > 2_048:
        return "suspicious_obfuscation"

    encoded_tokens = 0
    for pattern in (_ENCODED_TOKEN_RE, _CHUNKED_ENCODED_RE, _HEX_TOKEN_RE):
        for _match in pattern.finditer(value):
            encoded_tokens += 1
            if encoded_tokens > _MAX_ENCODED_TOKENS:
                return "encoded_content_unverifiable"

    # Патологические строки вида "a.a.a..." дорого проходят через десятки
    # regex. Для длинного текста с аномальной долей разделителей безопаснее
    # отказать до запуска классификатора.
    if len(value) >= 12_000:
        separators = sum(
            1 for char in value if not char.isalnum() and not char.isspace()
        )
        if separators * 100 > len(value) * 35:
            return "suspicious_obfuscation"
    return None


def _looks_like_decoded_text(raw: bytes) -> str | None:
    if not raw or len(raw) > 64 * 1024:
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not decoded:
        return None
    printable = sum(char.isprintable() or char.isspace() for char in decoded)
    if printable / len(decoded) < 0.90:
        return None
    return decoded


def _looks_like_compressed_text(raw: bytes) -> str | None:
    """Ограниченно распаковывает gzip/zlib, вложенный в base64."""
    if not raw or len(raw) > 64 * 1024:
        return None
    if raw.startswith(b"\x1f\x8b"):
        window_bits = zlib.MAX_WBITS | 16
    elif len(raw) >= 2 and raw[0] == 0x78:
        window_bits = zlib.MAX_WBITS
    else:
        return None
    try:
        decompressor = zlib.decompressobj(window_bits)
        decoded = decompressor.decompress(raw, 64 * 1024 + 1)
        if (
            len(decoded) > 64 * 1024
            or decompressor.unconsumed_tail
            or not decompressor.eof
        ):
            return None
        remaining = 64 * 1024 - len(decoded)
        decoded += decompressor.flush(remaining + 1)
    except zlib.error:
        return None
    if len(decoded) > 64 * 1024:
        return None
    return _looks_like_decoded_text(decoded)


def _decoded_security_sources(source: str) -> tuple[tuple[str, ...], bool]:
    """Извлекает HTML/URL/base64/hex-обходы с жёсткими лимитами.

    Второй элемент результата сообщает, что лимит вариантов/глубины был
    достигнут при наличии ещё одного декодируемого слоя. Внешние фильтры в
    таком случае обязаны отказать, а не молча пропустить непроверенный хвост.
    """
    candidates: list[tuple[str, int]] = [(source, 0)]
    seen = {source}
    exhausted = False

    def add_candidate(value: str | None, depth: int) -> None:
        nonlocal exhausted
        if (
            value
            and value not in seen
        ):
            if len(candidates) >= _MAX_DECODED_VARIANTS:
                exhausted = True
                return
            seen.add(value)
            candidates.append((value, depth))

    cursor = 0
    while cursor < len(candidates) and len(candidates) < _MAX_DECODED_VARIANTS:
        current, depth = candidates[cursor]
        cursor += 1
        if depth >= _MAX_DECODE_DEPTH:
            # Не считаем длинное слово кодировкой только по алфавиту base64:
            # проверяем, что токен действительно превращается в читаемый текст.
            for match in _ENCODED_TOKEN_RE.finditer(current):
                token = match.group(0)
                if len(token) > 90_000:
                    exhausted = True
                    break
                padded = token + ("=" * (-len(token) % 4))
                try:
                    raw = base64.b64decode(
                        padded.encode("ascii"),
                        altchars=b"-_",
                        validate=True,
                    )
                except (UnicodeEncodeError, binascii.Error, ValueError):
                    continue
                if (
                    _looks_like_decoded_text(raw) is not None
                    or _looks_like_compressed_text(raw) is not None
                ):
                    exhausted = True
                    break
            continue

        unescaped = html.unescape(current)
        if unescaped != current:
            add_candidate(unescaped, depth + 1)
        try:
            percent_decoded = unquote(current, errors="strict")
        except (UnicodeDecodeError, ValueError):
            percent_decoded = current
        if percent_decoded != current:
            add_candidate(percent_decoded, depth + 1)

        encoded_tokens: list[str] = []
        for match in _ENCODED_TOKEN_RE.finditer(current):
            encoded_tokens.append(match.group(0))
            if len(encoded_tokens) >= _MAX_DECODED_VARIANTS:
                break
        if len(encoded_tokens) < _MAX_DECODED_VARIANTS:
            for match in _CHUNKED_ENCODED_RE.finditer(current):
                encoded_tokens.append(
                    re.sub(r"(?:\s+|[.:])", "", match.group(0))
                )
                if len(encoded_tokens) >= _MAX_DECODED_VARIANTS:
                    break
        for token in encoded_tokens:
            if len(candidates) >= _MAX_DECODED_VARIANTS:
                exhausted = True
                break
            if len(token) > 90_000:
                exhausted = True
                break
            padded = token + ("=" * (-len(token) % 4))
            try:
                raw = base64.b64decode(
                    padded.encode("ascii"),
                    altchars=b"-_",
                    validate=True,
                )
            except (UnicodeEncodeError, binascii.Error, ValueError):
                continue
            add_candidate(_looks_like_decoded_text(raw), depth + 1)
            add_candidate(_looks_like_compressed_text(raw), depth + 1)

        for match in _HEX_TOKEN_RE.finditer(current):
            if len(candidates) >= _MAX_DECODED_VARIANTS:
                exhausted = True
                break
            try:
                decoded = _looks_like_decoded_text(
                    bytes.fromhex(match.group(0))
                )
            except ValueError:
                decoded = None
            add_candidate(decoded, depth + 1)

    if cursor < len(candidates):
        exhausted = True

    decoded_values = [value for value, _depth in candidates]
    for value in tuple(decoded_values):
        if len(decoded_values) >= _MAX_DECODED_VARIANTS:
            break
        # Частый обход — развернуть отдельные опасные слова задом наперёд.
        reversed_words = re.sub(
            r"[A-Za-zА-Яа-яЁё]{4,32}",
            lambda match: match.group(0)[::-1],
            value,
        )
        if reversed_words != value and reversed_words not in seen:
            seen.add(reversed_words)
            decoded_values.append(reversed_words)
        try:
            rot13_value = codecs.decode(value, "rot_13")
        except (TypeError, ValueError):
            rot13_value = value
        if (
            rot13_value != value
            and rot13_value not in seen
            and len(decoded_values) < _MAX_DECODED_VARIANTS
        ):
            seen.add(rot13_value)
            decoded_values.append(rot13_value)
        if (
            re.search(
                r"\b(?:rot13|reverse|backwards|задом\s+наперед|наоборот|"
                r"переверн\w*)\b",
                value,
                re.IGNORECASE,
            )
            and value[::-1] not in seen
            and len(decoded_values) < _MAX_DECODED_VARIANTS
        ):
            seen.add(value[::-1])
            decoded_values.append(value[::-1])

    cleaned = []
    for candidate in decoded_values[:_MAX_DECODED_VARIANTS]:
        value = _clean_security_source(candidate)
        if value:
            cleaned.append(value)
    return tuple(dict.fromkeys(cleaned)), exhausted


def _security_text_analysis(
    value: object,
) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    """Возвращает исходники, варианты и fail-closed причину отказа."""
    if not isinstance(value, str):
        return (), (), None
    complexity_reason = _security_complexity_rejection_reason(value)
    if complexity_reason:
        return (), (), complexity_reason
    sources, decode_exhausted = _decoded_security_sources(value)
    variants: list[str] = []
    for source in sources:
        normalized = source.casefold()
        latin_skeleton = normalized.translate(_LATIN_CONFUSABLES)
        accentless = "".join(
            char
            for char in unicodedata.normalize("NFKD", latin_skeleton)
            if unicodedata.category(char) != "Mn"
        )
        tokenized = re.sub(r"[^a-zа-яё0-9]+", " ", accentless)
        compact = re.sub(r"[^a-zа-яё0-9]+", "", accentless)
        de_spaced = re.sub(
            r"(?<![a-zа-яё0-9])(?:[a-zа-яё0-9]\s+){2,}"
            r"[a-zа-яё0-9](?![a-zа-яё0-9])",
            lambda match: re.sub(r"\s+", "", match.group(0)),
            tokenized,
        )
        variants.extend(
            (
                normalized,
                latin_skeleton,
                accentless,
                tokenized,
                compact,
                tokenized.translate(_LEET_CONFUSABLES),
                compact.translate(_LEET_CONFUSABLES),
                de_spaced.translate(_LEET_CONFUSABLES),
            )
        )
    rejection_reason = (
        "encoded_content_unverifiable" if decode_exhausted else None
    )
    return (
        sources,
        tuple(dict.fromkeys(item for item in variants if item)),
        rejection_reason,
    )


def _security_text_variants(value: object) -> tuple[str, ...]:
    """Совместимый внутренний помощник для готовых правил."""
    return _security_text_analysis(value)[1]


def _normalized_basename(filename: object) -> str:
    value = unicodedata.normalize("NFKC", str(filename or ""))
    value = value.replace("\\", "/")
    return value.rsplit("/", 1)[-1].strip().lower()


def is_sensitive_filename(filename: str) -> bool:
    """Блокирует имена файлов, которые обычно содержат учётные данные."""
    name = _normalized_basename(filename)
    if not name:
        return False
    if (
        name in _SENSITIVE_EXACT_NAMES
        or name.startswith(".env.")
        or name.startswith(("id_rsa.", "id_ed25519."))
    ):
        return True
    if name.endswith((".p12", ".pfx", ".kdbx", ".key", ".pem")):
        return True
    if any(marker in name for marker in ("private_key", "private-key")):
        return True
    if name.endswith((".json", ".yaml", ".yml")) and any(
        marker in name
        for marker in (
            "credential",
            "service-account",
            "service_account",
            "secret",
        )
    ):
        return True
    return False


def is_dangerous_executable_filename(filename: str) -> bool:
    """Не принимает готовые исполняемые файлы и скрипты автозапуска."""
    name = _normalized_basename(filename)
    _, extension = os.path.splitext(name)
    return extension in _DANGEROUS_EXECUTABLE_EXTENSIONS


def make_output_filename_inert(filename: str) -> str:
    """Добавляет .txt к коду/скриптам, чтобы файл не запускался по клику."""
    name = _normalized_basename(filename) or "ответ.txt"
    _, extension = os.path.splitext(name)
    if extension in _ACTIVE_OUTPUT_EXTENSIONS:
        return f"{name}.txt"
    return name


def dangerous_binary_signature(raw: object) -> str | None:
    """Определяет распространённые исполняемые бинарные форматы по сигнатуре."""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        return None
    data = bytes(raw[:16])
    signatures = (
        (b"MZ", "windows_executable"),
        (b"\x7fELF", "elf_executable"),
        (b"\xca\xfe\xba\xbe", "java_class_or_macho"),
        (b"\xfe\xed\xfa\xce", "macho_executable"),
        (b"\xfe\xed\xfa\xcf", "macho_executable"),
        (b"\xce\xfa\xed\xfe", "macho_executable"),
        (b"\xcf\xfa\xed\xfe", "macho_executable"),
        (b"dex\n", "android_dex"),
        (b"\x00asm", "webassembly"),
    )
    for signature, kind in signatures:
        if data.startswith(signature):
            return kind
    return None


def validate_safe_image_payload(
    raw: object,
    declared_mime: str | None = None,
    *,
    max_dimension: int = 8192,
    max_pixels: int = 25_000_000,
) -> str | None:
    """Проверяет структуру и размеры PNG/JPEG/WebP.

    ``None`` означает, что данные не имеют сигнатуры поддерживаемого
    изображения. Если сигнатура есть, но контейнер повреждён, анимирован,
    подменён или имеет опасные размеры, функция fail-closed выбрасывает
    ``ValueError``.
    """
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        return None
    data = bytes(raw)
    mime: str | None = None
    width = height = 0

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
        position = 8
        chunk_count = 0
        seen_ihdr = seen_idat = seen_iend = False
        while position < len(data):
            chunk_count += 1
            if chunk_count > 10_000 or position + 12 > len(data):
                raise ValueError("Некорректная структура PNG.")
            chunk_length = int.from_bytes(data[position:position + 4], "big")
            chunk_type = data[position + 4:position + 8]
            chunk_data_start = position + 8
            chunk_data_end = chunk_data_start + chunk_length
            chunk_end = chunk_data_end + 4
            if chunk_end > len(data):
                raise ValueError("Обрезанный PNG-чанк.")
            if (
                len(chunk_type) != 4
                or not all(
                    65 <= value <= 90 or 97 <= value <= 122
                    for value in chunk_type
                )
                or not 65 <= chunk_type[2] <= 90
            ):
                raise ValueError("Некорректный тип PNG-чанка.")
            expected_crc = int.from_bytes(
                data[chunk_data_end:chunk_end],
                "big",
            )
            actual_crc = zlib.crc32(
                chunk_type + data[chunk_data_start:chunk_data_end]
            ) & 0xFFFFFFFF
            if expected_crc != actual_crc:
                raise ValueError("Некорректная контрольная сумма PNG.")
            if chunk_count == 1 and chunk_type != b"IHDR":
                raise ValueError("IHDR должен быть первым PNG-чанком.")
            if chunk_type in {b"acTL", b"fcTL", b"fdAT"}:
                raise ValueError("Анимированные PNG не принимаются.")
            if chunk_type == b"IHDR":
                if seen_ihdr or chunk_length != 13:
                    raise ValueError("Некорректный IHDR PNG.")
                seen_ihdr = True
                header = data[chunk_data_start:chunk_data_end]
                width = int.from_bytes(header[0:4], "big")
                height = int.from_bytes(header[4:8], "big")
                bit_depth = header[8]
                color_type = header[9]
                allowed_depths = {
                    0: {1, 2, 4, 8, 16},
                    2: {8, 16},
                    3: {1, 2, 4, 8},
                    4: {8, 16},
                    6: {8, 16},
                }
                if (
                    bit_depth not in allowed_depths.get(color_type, set())
                    or header[10] != 0
                    or header[11] != 0
                    or header[12] not in {0, 1}
                ):
                    raise ValueError("Неподдерживаемые параметры PNG.")
            elif chunk_type == b"PLTE":
                if seen_idat or chunk_length == 0 or chunk_length > 768:
                    raise ValueError("Некорректная палитра PNG.")
                if chunk_length % 3:
                    raise ValueError("Некорректная палитра PNG.")
            elif chunk_type == b"IDAT":
                if not seen_ihdr or seen_iend:
                    raise ValueError("Некорректный порядок PNG-чанков.")
                seen_idat = True
            elif chunk_type == b"IEND":
                if chunk_length != 0 or not seen_idat or seen_iend:
                    raise ValueError("Некорректный IEND PNG.")
                seen_iend = True
                if chunk_end != len(data):
                    raise ValueError("Данные после IEND PNG запрещены.")
            elif 65 <= chunk_type[0] <= 90:
                raise ValueError("Неизвестный критический PNG-чанк.")
            position = chunk_end
        if not (seen_ihdr and seen_idat and seen_iend):
            raise ValueError("Неполная структура PNG.")
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
        if len(data) < 16:
            raise ValueError("Некорректная структура JPEG.")
        position = 2
        seen_eoi = False
        seen_sos = False
        sof_markers = {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }
        while position < len(data):
            if data[position] != 0xFF:
                raise ValueError("Данные JPEG вне сегмента или scan-потока.")
            while position < len(data) and data[position] == 0xFF:
                position += 1
            if position >= len(data):
                raise ValueError("Обрезанный маркер JPEG.")
            marker = data[position]
            position += 1
            if marker == 0xD9:
                if position != len(data):
                    raise ValueError("Данные после первого EOI JPEG запрещены.")
                seen_eoi = True
                break
            if marker in {0x01, 0xD8, *range(0xD0, 0xD8)}:
                continue
            if marker == 0x00 or position + 2 > len(data):
                raise ValueError("Некорректный маркер JPEG.")
            segment_length = int.from_bytes(data[position:position + 2], "big")
            if segment_length < 2:
                raise ValueError("Некорректный сегмент JPEG.")
            segment_end = position + segment_length
            if segment_end > len(data):
                raise ValueError("Обрезанный сегмент JPEG.")
            if marker in sof_markers:
                if segment_length < 7:
                    raise ValueError("Некорректный SOF-сегмент JPEG.")
                height = int.from_bytes(data[position + 3:position + 5], "big")
                width = int.from_bytes(data[position + 5:position + 7], "big")
            position = segment_end
            if marker == 0xDA:
                seen_sos = True
                # После SOS байты 0xFF00 экранированы, а restart-маркеры не
                # имеют длины. Ищем первый настоящий маркер следующего scan
                # или EOI и возвращаем его внешнему циклу.
                while position < len(data):
                    marker_start = data.find(b"\xff", position)
                    if marker_start < 0:
                        raise ValueError("В JPEG отсутствует EOI.")
                    marker_position = marker_start
                    while (
                        marker_position < len(data)
                        and data[marker_position] == 0xFF
                    ):
                        marker_position += 1
                    if marker_position >= len(data):
                        raise ValueError("Обрезанный scan-поток JPEG.")
                    scan_marker = data[marker_position]
                    if scan_marker == 0x00 or 0xD0 <= scan_marker <= 0xD7:
                        position = marker_position + 1
                        continue
                    position = marker_start
                    break
        if not width or not height or not seen_sos or not seen_eoi:
            raise ValueError("В JPEG не найдены безопасные размеры.")
    elif (
        len(data) >= 20
        and data.startswith(b"RIFF")
        and data[8:12] == b"WEBP"
    ):
        mime = "image/webp"
        if int.from_bytes(data[4:8], "little") + 8 != len(data):
            raise ValueError("Некорректный размер RIFF/WebP.")
        chunks: list[tuple[bytes, bytes]] = []
        position = 12
        while position < len(data):
            if len(chunks) >= 10_000 or position + 8 > len(data):
                raise ValueError("Некорректная структура WebP.")
            chunk_type = data[position:position + 4]
            chunk_length = int.from_bytes(
                data[position + 4:position + 8],
                "little",
            )
            chunk_start = position + 8
            chunk_end = chunk_start + chunk_length
            padded_end = chunk_end + (chunk_length & 1)
            if chunk_end > len(data) or padded_end > len(data):
                raise ValueError("Обрезанный WebP-чанк.")
            if chunk_length & 1 and data[chunk_end:padded_end] != b"\x00":
                raise ValueError("Некорректное выравнивание WebP.")
            if chunk_type in {b"ANIM", b"ANMF"}:
                raise ValueError("Анимированные WebP не принимаются.")
            chunks.append((chunk_type, data[chunk_start:chunk_end]))
            position = padded_end
        if position != len(data) or not chunks:
            raise ValueError("Некорректная структура WebP.")

        chunk, chunk_data = chunks[0]
        if chunk == b"VP8X":
            if len(chunk_data) != 10:
                raise ValueError("Некорректный VP8X.")
            flags = chunk_data[0]
            if flags & 0x02:
                raise ValueError("Анимированные WebP не принимаются.")
            if flags & 0xC1 or chunk_data[1:4] != b"\x00\x00\x00":
                raise ValueError("Некорректные флаги VP8X.")
            if not any(
                item_type in {b"VP8 ", b"VP8L"}
                for item_type, _item_data in chunks[1:]
            ):
                raise ValueError("В WebP отсутствуют данные изображения.")
            width = 1 + int.from_bytes(chunk_data[4:7], "little")
            height = 1 + int.from_bytes(chunk_data[7:10], "little")
        elif chunk == b"VP8 ":
            if len(chunk_data) < 10 or chunk_data[3:6] != b"\x9d\x01\x2a":
                raise ValueError("Некорректный VP8.")
            width = int.from_bytes(chunk_data[6:8], "little") & 0x3FFF
            height = int.from_bytes(chunk_data[8:10], "little") & 0x3FFF
        elif chunk == b"VP8L":
            if len(chunk_data) < 5 or chunk_data[0] != 0x2F:
                raise ValueError("Некорректный VP8L.")
            bits = int.from_bytes(chunk_data[1:5], "little")
            width = 1 + (bits & 0x3FFF)
            height = 1 + ((bits >> 14) & 0x3FFF)
        else:
            raise ValueError("Неподдерживаемый контейнер WebP.")
    else:
        return None

    declared = (declared_mime or "").strip().lower()
    if declared and declared != mime:
        raise ValueError("Содержимое изображения не соответствует MIME-типу.")
    if (
        width <= 0
        or height <= 0
        or width > max_dimension
        or height > max_dimension
        or width * height > max_pixels
    ):
        raise ValueError("Изображение имеет опасные размеры.")
    return mime


def sanitize_safe_image_payload(
    raw: object,
    declared_mime: str | None = None,
    *,
    max_dimension: int = 8192,
    max_pixels: int = 25_000_000,
    max_output_bytes: int = 16 * 1024 * 1024,
) -> tuple[bytes, str] | None:
    """Полностью декодирует и заново кодирует статичное изображение.

    Повторное кодирование удаляет EXIF/XMP/текстовые метаданные, хвост после
    контейнера и вложенный ZIP/PDF. Провайдер никогда не получает исходные
    байты пользователя.
    """
    mime = validate_safe_image_payload(
        raw,
        declared_mime,
        max_dimension=max_dimension,
        max_pixels=max_pixels,
    )
    if mime is None:
        return None
    data = bytes(raw)
    format_by_mime = {
        "image/jpeg": "JPEG",
        "image/png": "PNG",
        "image/webp": "WEBP",
    }
    expected_format = format_by_mime[mime]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if (image.format or "").upper() != expected_format:
                    raise ValueError(
                        "Фактический формат изображения не совпадает с контейнером."
                    )
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Анимированные изображения не принимаются.")
                image.load()
                width, height = image.size
                if (
                    width <= 0
                    or height <= 0
                    or width > max_dimension
                    or height > max_dimension
                    or width * height > max_pixels
                ):
                    raise ValueError("Изображение имеет опасные размеры.")

                output = io.BytesIO()
                if expected_format == "JPEG":
                    clean_image = image.convert("RGB")
                    clean_image.save(
                        output,
                        format="JPEG",
                        quality=90,
                        optimize=False,
                        progressive=False,
                        subsampling=2,
                    )
                elif expected_format == "PNG":
                    clean_image = image.convert(
                        "RGBA" if "A" in image.getbands() else "RGB"
                    )
                    clean_image.save(
                        output,
                        format="PNG",
                        optimize=False,
                        compress_level=6,
                    )
                else:
                    clean_image = image.convert(
                        "RGBA" if "A" in image.getbands() else "RGB"
                    )
                    clean_image.save(
                        output,
                        format="WEBP",
                        quality=90,
                        method=4,
                    )
                sanitized = output.getvalue()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
    ) as exc:
        raise ValueError(
            "Изображение не удалось безопасно декодировать."
        ) from exc

    if not sanitized or len(sanitized) > max_output_bytes:
        raise ValueError("Изображение после очистки слишком большое.")
    if validate_safe_image_payload(
        sanitized,
        mime,
        max_dimension=max_dimension,
        max_pixels=max_pixels,
    ) != mime:
        raise ValueError("Ошибка повторной проверки изображения.")
    return sanitized, mime


def contains_probable_secret(value: object) -> bool:
    """Ищет строгие форматы реальных ключей и приватных учётных данных.

    Проверяются и ограниченно декодированные варианты. Иначе реальный токен
    можно было обернуть в URL/base64/hex и передать внешнему провайдеру через
    обычный текст запроса или вложения.
    """
    if not isinstance(value, str) or not value:
        return False
    sources, _variants, rejection_reason = _security_text_analysis(value)
    if rejection_reason == "encoded_content_unverifiable":
        # Нельзя доказать отсутствие секрета внутри непроверенного слоя.
        return True
    return any(
        pattern.search(candidate)
        for candidate in sources
        for pattern in _PROBABLE_SECRET_PATTERNS
    )


def _compile_patterns(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE | re.DOTALL) for pattern in patterns)


_PROHIBITED_REQUEST_PATTERNS: tuple[
    tuple[str, tuple[re.Pattern[str], ...]], ...
] = (
    (
        "malware",
        _compile_patterns(
            (
                r"\b(?:создай|сделай|напиши|разработай|сгенерируй|собери|"
                r"запрограммируй|обфусцируй|улучши|доработай|модифицируй|"
                r"разверни|распространи|пришли|дай)\b.{0,100}\b(?:вирус\w*|"
                r"троян\w*|вымогател\w*|шифровальщик\w*|кейлоггер\w*|"
                r"стилер\w*|ботнет\w*|руткит\w*|бэкдор\w*|бекдор\w*|"
                r"вредонос\w+\s+программ\w*|малвар\w*)",
                r"\b(?:create|build|write|develop|generate|code|program|"
                r"obfuscate|improve|modify|deploy|spread|deliver)\b.{0,100}\b"
                r"(?:malware|virus|trojan|ransomware|keylogger|stealer|"
                r"botnet|rootkit|backdoor|computer\s+worm)\b",
                r"\b(?:malware|virus|trojan|ransomware|keylogger|stealer|"
                r"botnet|rootkit|backdoor)\b.{0,80}\b(?:source\s+code|"
                r"payload|builder|dropper|loader|script|sample)\b",
                r"\b(?:создай|сделай|напиши|разработай|сгенерируй|собери|"
                r"запрограммируй|обфусцируй|улучши|доработай|модифицируй|"
                r"разверни|распространи|пришли|дай)\b.{0,100}\b(?:malware|"
                r"virus|trojan|ransomware|keylogger|stealer|botnet|rootkit|"
                r"backdoor)\b",
                r"\b(?:create|build|write|develop|generate|code|program|"
                r"obfuscate|improve|modify|deploy|spread|deliver)\b.{0,100}"
                r"\b(?:вирус\w*|троян\w*|вымогател\w*|шифровальщик\w*|"
                r"кейлоггер\w*|стилер\w*|ботнет\w*|руткит\w*|бэкдор\w*|"
                r"бекдор\w*|малвар\w*)",
                r"\b(?:код|исходник\w*|скрипт\w*|пейлоад\w*|загрузчик\w*)"
                r".{0,80}\b(?:вирус\w*|троян\w*|вымогател\w*|"
                r"шифровальщик\w*|кейлоггер\w*|стилер\w*|ботнет\w*|"
                r"руткит\w*|бэкдор\w*|бекдор\w*|малвар\w*)",
                r"\b(?:вирус\w*|троян\w*|вымогател\w*|шифровальщик\w*|"
                r"кейлоггер\w*|стилер\w*|ботнет\w*|руткит\w*|бэкдор\w*|"
                r"бекдор\w*|малвар\w*)\b.{0,80}\b(?:код|исходник\w*|"
                r"скрипт\w*|пейлоад\w*|загрузчик\w*)",
                r"\b(?:source\s+code|code|script|payload|builder|dropper|"
                r"loader)\b.{0,80}\b(?:malware|virus|trojan|ransomware|"
                r"keylogger|stealer|botnet|rootkit|backdoor)\b",
                r"\b(?:reverse\s+shell|обратн\w+\s+(?:shell|шелл\w*)|"
                r"exploit\s+payload)\b.{0,80}\b(?:code|script|payload|"
                r"код|скрипт|пейлоад)",
                r"\b(?:создай|сделай|напиши|сгенерируй|настрой)\b.{0,100}"
                r"\b(?:reverse\s+shell|обратн\w+\s+(?:shell|шелл\w*))",
                r"\b(?:create|build|write|generate|configure)\b.{0,100}"
                r"\b(?:reverse\s+shell|callback\s+shell)\b",
            )
        ),
    ),
    (
        "credential_theft",
        _compile_patterns(
            (
                r"\b(?:создай|сделай|напиши|сгенерируй|настрой|разверни|"
                r"нарисуй|спроектируй)\b"
                r".{0,100}\b(?:фишинг\w*|поддельн\w+\s+(?:страниц\w*|сайт\w*)|"
                r"краж\w+\s+(?:парол\w*|куки|токен\w*)|перехват\w+\s+"
                r"(?:парол\w*|куки|токен\w*))",
                r"\b(?:create|build|write|generate|host|deploy|draw|design)\b"
                r".{0,100}\b"
                r"(?:phishing|credential\s+(?:stealer|harvester)|"
                r"cookie\s+stealer|token\s+grabber|fake\s+login)\b",
                r"\b(?:укради|похить|вытащи|перехвати|собери)\b.{0,80}\b"
                r"(?:парол\w*|учетн\w+\s+данн\w*|куки|токен\w*)",
                r"\b(?:steal(?:s|ing)?|dump|harvest|capture|exfiltrate)\b"
                r".{0,80}\b"
                r"(?:passwords?|credentials?|cookies?|session\s+tokens?)\b",
                r"\b(?:программ\w*|скрипт\w*|код)\b.{0,100}\b"
                r"(?:крад\w*|похища\w*|перехват\w*).{0,60}\b"
                r"(?:парол\w*|куки|токен\w*|учетн\w+\s+данн\w*)",
            )
        ),
    ),
    (
        "unauthorized_access",
        _compile_patterns(
            (
                r"\b(?:взломай|взломать|обойди|обойти|эксплуатируй|"
                r"проникни|получи\s+несанкционированн\w+\s+доступ)\b.{0,100}"
                r"\b(?:аккаунт\w*|сайт\w*|сервер\w*|систем\w*|сет\w*|"
                r"устройств\w*|камер\w*|почт\w*)",
                r"\b(?:hack|break\s+into|bypass|exploit|compromise|gain\s+"
                r"unauthori[sz]ed\s+access)\b.{0,100}\b(?:account|website|"
                r"server|system|network|device|camera|email)\b",
                r"\b(?:обойди|отключи|скройся\s+от)\b.{0,80}\b"
                r"(?:антивирус\w*|edr|защит\w*|детект\w*|аутентификац\w*)",
                r"\b(?:bypass|disable|evade)\b.{0,80}\b"
                r"(?:antivirus|edr|security|detection|authentication)\b",
                r"\b(?:создай|напиши|разработай|вооружи|адаптируй)\b"
                r".{0,100}\b(?:эксплойт\w*|код\s+эксплуатац\w*)\b",
                r"\b(?:create|write|develop|weaponize|adapt)\b.{0,100}\b"
                r"(?:exploit|exploitation\s+code)\b",
            )
        ),
    ),
    (
        "service_abuse",
        _compile_patterns(
            (
                r"\b(?:запусти|организуй|сделай|напиши|создай)\b.{0,80}\b"
                r"(?:ddos|ддос|dos[- ]атак\w*|флуд[- ]атак\w*)",
                r"\b(?:дай|покажи|объясни|подскажи|составь|напиши)\b"
                r".{0,120}\b(?:как|инструкц\w*|способ\w*|метод\w*)\b"
                r".{0,100}\b(?:провест\w*|устро\w*|запуст\w*|"
                r"организ\w*|выполн\w*)?.{0,20}\b(?:ddos|ддос|"
                r"dos[- ]атак\w*|флуд[- ]атак\w*)",
                r"\b(?:провед\w*|устро\w*|запуст\w*|организ\w*|"
                r"выполн\w*)\b.{0,80}\b(?:ddos|ддос|dos[- ]атак\w*|"
                r"флуд[- ]атак\w*)",
                r"\b(?:полож\w*|обруш\w*|перегруз\w*|вывед\w+\s+из\s+"
                r"стро\w*)\b.{0,120}\b(?:чуж\w+\s+)?(?:сайт\w*|"
                r"сервер\w*|api|сервис\w*)\b.{0,120}\b(?:множеств\w*|"
                r"больш\w+\s+количеств\w*|поток\w*|массов\w*|"
                r"одновременн\w*)\b.{0,80}\b(?:запрос\w*|соединени\w*|"
                r"клиент\w*)",
                r"\b(?:launch|perform|build|write|create)\b.{0,80}\b"
                r"(?:ddos|denial[- ]of[- ]service|flood\s+attack)\b",
                r"\b(?:give|show|explain|provide)\b.{0,120}\b(?:how\s+to|"
                r"instructions?|methods?)\b.{0,100}\b(?:launch|perform|"
                r"conduct|carry\s+out)?.{0,20}\b(?:ddos|"
                r"denial[- ]of[- ]service|flood\s+attack)\b",
                r"\b(?:take|bring|knock)\b.{0,20}\b(?:down|offline)\b"
                r".{0,100}\b(?:someone\s+else['’]s\s+)?(?:website|site|"
                r"server|api|service)\b.{0,120}\b(?:many|mass|concurrent|"
                r"parallel|flood\w*)\b.{0,80}\b(?:requests?|connections?|"
                r"clients?)\b",
                r"\b(?:take|bring|knock)\b.{0,120}\b(?:someone\s+"
                r"else['’]s\s+)?(?:website|site|server|api|service)\b"
                r".{0,30}\b(?:down|offline)\b.{0,120}\b(?:many|mass|"
                r"concurrent|parallel|flood\w*)\b.{0,80}\b(?:requests?|"
                r"connections?|clients?)\b",
                r"\b(?:удали|уничтожь|зашифруй|повреди)\b.{0,80}\b"
                r"(?:чуж\w+\s+данн\w*|чуж\w+\s+файл\w*|все\s+файл\w*|"
                r"резервн\w+\s+коп\w*)",
            )
        ),
    ),
    (
        "weapons",
        _compile_patterns(
            (
                r"\b(?:как|инструкц\w*|схем\w*|рецепт\w*|создай|сделай|"
                r"собери|изготовь)\b.{0,100}\b(?:бомб\w*|взрывчат\w*|"
                r"самодельн\w+\s+взрывн\w+\s+устройств\w*|огнестрельн\w+"
                r"\s+оруж\w*)",
                r"\b(?:how\s+to|instructions?|blueprint|recipe|build|make|"
                r"assemble|manufacture)\b.{0,100}\b(?:bomb|explosive|ied|"
                r"firearm|ghost\s+gun)\b",
                r"\b(?:состав|компонент\w*|чертеж\w*)\b.{0,80}\b"
                r"(?:бомб\w*|взрывчат\w*|самодельн\w+\s+взрывн\w+"
                r"\s+устройств\w*)",
            )
        ),
    ),
    (
        "illegal_drugs",
        _compile_patterns(
            (
                r"\b(?:как|рецепт\w*|инструкц\w*|синтез\w*|приготовь|"
                r"изготовь|произведи)\b.{0,100}\b(?:метамфетамин\w*|"
                r"амфетамин\w*|героин\w*|фентанил\w*|наркотик\w*)",
                r"\b(?:how\s+to|recipe|instructions?|synthesi[sz]e|cook|"
                r"manufacture|produce)\b.{0,100}\b(?:methamphetamine|"
                r"heroin|fentanyl|illegal\s+drugs?)\b",
            )
        ),
    ),
    (
        "sexual_minors",
        _compile_patterns(
            (
                r"\b(?:создай|сгенерируй|напиши|нарисуй|покажи)\b.{0,100}\b"
                r"(?:сексуальн\w+|порнограф\w+).{0,50}\b(?:ребен\w*|"
                r"дет\w*|несовершеннолетн\w*)",
                r"\b(?:create|generate|write|draw|show)\b.{0,100}\b"
                r"(?:sexual|pornographic).{0,50}\b(?:child|minor|underage)\b",
                r"\b(?:создай|сгенерируй|нарисуй|покажи)\b.{0,100}\b"
                r"(?:обнаженн\w*|гол\w+).{0,50}\b(?:ребен\w*|дет\w*|"
                r"несовершеннолетн\w*)",
            )
        ),
    ),
    (
        "self_harm_or_violence",
        _compile_patterns(
            (
                r"\b(?:дай|напиши|составь|покажи)\b.{0,80}\b(?:инструкц\w*|"
                r"план\w*|способ\w*)\b.{0,80}\b(?:убить|покончить\s+с\s+"
                r"собой|самоубийств\w*|причинить\s+вред)",
                r"\b(?:give|write|create|show)\b.{0,80}\b(?:instructions?|"
                r"plan|method)\b.{0,80}\b(?:kill|suicide|self[- ]harm|"
                r"hurt\s+someone)\b",
                r"\b(?:как|способ\w*|метод\w*|инструкц\w*)\b.{0,80}\b"
                r"(?:покончить\s+с\s+собой|совершить\s+самоубийств\w*|"
                r"убить\s+(?:себя|человека))",
                r"\b(?:how\s+to|method|instructions?)\b.{0,80}\b"
                r"(?:commit\s+suicide|kill\s+(?:myself|someone)|self[- ]harm)",
            )
        ),
    ),
)

_HIGH_RISK_PAYLOAD_PATTERNS = _compile_patterns(
    (
        r"(?i)\bsekurlsa::logonpasswords\b",
        r"(?i)\bmimikatz\b",
        r"(?i)\b(?:invoke-mimikatz|lsadump::sam)\b",
        r"(?i)\bIEX\s*\(.{0,80}(?:DownloadString|Net\.WebClient)",
        r"(?i)\bpowershell(?:\.exe)?\b.{0,80}\s-(?:enc|encodedcommand)\b",
        r"(?i)/bin/(?:ba)?sh\s+-i\s+>&\s*/dev/tcp/",
        r"(?i)\bnc\s+-e\s+/bin/(?:ba)?sh\b",
        r"(?i)\b(?:pynput|keyboard)\b.{0,120}\b(?:on_press|keylogger|"
        r"log_keys)\b",
        r"(?i)\b(?:encrypt|шифр\w*)\b.{0,100}\b(?:all\s+files|все\s+"
        r"файл\w*)\b.{0,100}\b(?:ransom|выкуп\w*)\b",
        r"(?i)\b(?:socket|requests)\b.{0,160}\b(?:password|cookie|token)\b"
        r".{0,160}\b(?:send|post|exfiltrat)",
        r"(?i)\bdocument\.cookie\b.{0,180}\b(?:fetch|xmlhttprequest|"
        r"sendbeacon|websocket)\b",
        r"(?i)\b(?:os\.walk|pathlib|glob)\b.{0,240}\b(?:encrypt|fernet|"
        r"aes)\b.{0,180}\b(?:unlink|remove|delete)\b",
        r"(?i)\b(?:schtasks|startup|currentversion\\run|launchagents)\b"
        r".{0,180}\b(?:powershell|wscript|cmd\.exe|/bin/(?:ba)?sh)\b",
        r"(?i)\b(?:virtualalloc(?:ex)?|writeprocessmemory|"
        r"createremotethread|process\s+hollowing)\b",
        r"(?i)\b(?:win32crypt|cryptunprotectdata)\b.{0,220}\b"
        r"(?:login\s+data|cookies?|password_value)\b",
        r"(?i)\b(?:while\s+true|while\s*\(\s*true\s*\))\b.{0,240}\b"
        r"(?:requests?\.(?:get|post)|socket\.(?:send|sendall)|"
        r"fetch\s*\()",
        r"(?i)\b(?:password|credential|session\s+token)\b.{0,180}\b"
        r"(?:fetch\s*\(|sendbeacon|xmlhttprequest)\b",
        r"(?i)\b(?:rm\s+-rf\s+/(?:\s|$)|shutil\.rmtree\s*\(\s*[\"']/[\"'])",
        r"(?i)\b(?:pynput|keyboard|setwindowshookex|getasynckeystate)\b"
        r".{0,300}\b(?:requests?\.(?:post|put)|socket\.(?:send|sendall)|"
        r"webhook|telegram)\b",
        r"(?i)\b(?:login\s+data|cookies?|password_value|"
        r"cryptunprotectdata|chrome\\user\s+data)\b.{0,320}\b"
        r"(?:requests?\.(?:post|put)|socket\.(?:send|sendall)|"
        r"upload|webhook)\b",
        r"(?i)\b(?:pyautogui\.screenshot|imagegrab\.grab|"
        r"pyaudio|sounddevice|opencv|videocapture)\b.{0,320}\b"
        r"(?:requests?\.(?:post|put)|socket\.(?:send|sendall)|"
        r"upload|webhook)\b",
        r"(?i)\b(?:auto_?open|document_open|workbook_open)\b.{0,300}\b"
        r"(?:urlmon|downloadstring|xmlhttp|shell|wscript\.shell|"
        r"powershell|cmd\.exe)\b",
        r"(?i)\b(?:base64\.b64decode|frombase64string|atob)\b.{0,200}\b"
        r"(?:eval|exec|invoke-expression|iex|subprocess|os\.system)\b",
        r"(?i)\b(?:union\s+select|or\s+['\"]?1['\"]?\s*=\s*['\"]?1|"
        r"information_schema)\b.{0,200}\b(?:password|users?|credentials?|"
        r"dump|extract)\b",
        r"(?i)\b(?:remove-item\b.{0,80}-recurse.{0,40}-force|"
        r"del\s+/[sq]\b|format\s+[a-z]:|cipher\s+/w:)\b",
        r"(?i)\b(?:socket\.socket|tcpclient)\b.{0,400}\b"
        r"(?:dup2|createprocess|invoke-expression|subprocess|"
        r"os\.system|cmd\.exe|/bin/(?:ba)?sh)\b",
        r"(?i)(?:/etc/(?:shadow|passwd)|id_rsa|wallet\.dat|seed\s+phrase)"
        r".{0,400}(?:requests?\.(?:post|put)|socket\.(?:send|sendall)|"
        r"upload|webhook|sendbeacon)",
        r"(?i)(?:requests?\.(?:post|put)|socket\.(?:send|sendall)|upload|"
        r"webhook).{0,400}(?:/etc/(?:shadow|passwd)|id_rsa|wallet\.dat|"
        r"seed\s+phrase)",
        r"(?i)\b(?:shutil\.rmtree|remove-item|rm\s+-rf)\b.{0,160}"
        r"(?:expanduser\s*\(\s*[\"']~|path\.home|userprofile|"
        r"home\s+directory|резервн\w+\s+коп\w*|restore\s+points?)",
        r"(?i)\b(?:os\.walk|pathlib\.path\.rglob|glob\.glob)\b.{0,400}"
        r"(?:os\.(?:remove|unlink)|path\.unlink|shutil\.rmtree)\b",
        r"(?i):\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:",
        r"(?i)\b(?:curl|wget)\b.{0,300}\|\s*(?:ba)?sh\b",
    )
)

_MALWARE_ACTION = re.compile(
    r"\b(?:созда\w*|сдела\w*|напиш\w*|разработ\w*|сгенер\w*|"
    r"собер\w*|реализ\w*|обфусц\w*|улучш\w*|доработ\w*|модифиц\w*|"
    r"распростран\w*|create|build|write|develop|generate|code|"
    r"program|implement|craft|produce|make|assemble|obfuscate|improve|"
    r"modify|deploy|spread)\b",
    re.IGNORECASE,
)
_MALWARE_TARGET = re.compile(
    r"\b(?:вирус\w*|троян\w*|вредонос\w*|шифровальщик\w*|вымогател\w*|"
    r"кейлоггер\w*|стилер\w*|ботнет\w*|руткит\w*|б[еэ]кдор\w*|"
    r"малвар\w*|rat[- ]?троян\w*|malware|virus|trojan|ransomware|"
    r"keylogger|stealer|infostealer|botnet|rootkit|backdoor|"
    r"remote\s+access\s+trojan|computer\s*worm)\b",
    re.IGNORECASE,
)
_DEFENSIVE_CONTEXT = re.compile(
    r"\b(?:обнаруж\w*|детект\w*|лечен\w*|защит\w*|"
    r"предотврат\w*|расслед\w*|анализ\w*|песочниц\w*|сигнатур\w*|"
    r"(?:удал\w*|очист\w*).{0,40}(?:вирус\w*|троян\w*|"
    r"вредонос\w*|малвар\w*|заражен\w*|угроз\w*)|"
    r"detect\w*|remov\w*|defen[cs]\w*|protect\w*|prevent\w*|"
    r"investigat\w*|analy[sz]\w*|sandbox\w*|signature\w*|yara)\b",
    re.IGNORECASE,
)
_OFFENSIVE_AMPLIFIER = re.compile(
    r"\b(?:рабоч\w*|незамет\w*|скрыт\w*|обход\w*|зараз\w*|"
    r"распростран\w*|пейлоад\w*|полезн\w+\s+нагруз\w*|персист\w*|"
    r"эксфильтр\w*|крад\w*|шифр\w+\s+файл\w*|working|undetect\w*|"
    r"stealth\w*|bypass\w*|evad\w*|infect\w*|payload\w*|"
    r"persist\w*|exfiltrat\w*|steal\w*|encrypt\w+\s+files?)\b",
    re.IGNORECASE,
)
_NEGATED_OFFENSIVE_ARTIFACT = re.compile(
    r"\b(?:без\s+(?:рабоч\w+\s+|жив\w+\s+|вредонос\w+\s+)?"
    r"(?:пейлоад\w*|полезн\w+\s+нагруз\w*)|"
    r"без\s+(?:запуск\w*|выполнени\w*)\s+(?:рабоч\w+\s+|жив\w+\s+|"
    r"вредонос\w+\s+)?(?:пейлоад\w*|полезн\w+\s+нагруз\w*)|"
    r"without\s+(?:a\s+)?(?:live\s+|working\s+|malicious\s+)?"
    r"payload\w*|без(?:рабоч\w*|жив\w*|вредонос\w*)?"
    r"(?:пейлоад\w*|полезн\w*нагруз\w*)|"
    r"without\s+(?:executing|running)\s+(?:a\s+)?"
    r"(?:live\s+|working\s+|malicious\s+)?payload\w*|"
    r"without(?:a)?(?:live|working|malicious)?payload\w*)\b",
    re.IGNORECASE,
)
_COMPACT_NEGATED_OFFENSIVE_ARTIFACT = re.compile(
    r"(?:без(?:рабоч(?:ей|его|ую)?|жив(?:ой|ого|ую)?|"
    r"вредонос(?:ной|ного|ную)?)?"
    r"(?:пейлоад(?:а|ов|ом|ы)?|полезн(?:ой|ую)?нагруз(?:ки|ку|кой)?)|"
    r"без(?:запуск\w*|выполнени\w*)(?:рабоч\w*|жив\w*|вредонос\w*)?"
    r"(?:пейлоад\w*|полезн\w*нагруз\w*)|"
    r"without(?:executing|running)(?:a)?(?:live|working|malicious)?payloads?|"
    r"without(?:a)?(?:live|working|malicious)?payloads?)",
    re.IGNORECASE,
)
_DEFENSIVE_ARTIFACT = re.compile(
    r"\b(?:yara|sigma|ioc|антивирус\w*|сканер\w*|детектор\w*|"
    r"правил\w+\s+обнаруж\w*|код\w+\s+обнаруж\w*|"
    r"тест\w+\s+(?:фильтр\w*|детектор\w*)|detection\s+rule\w*|"
    r"detection\s+(?:code|tests?)|scanner\w*|detector\w*|"
    r"removal\s+tool\w*)\b",
    re.IGNORECASE,
)
_SEXUAL_CONTENT = re.compile(
    r"\b(?:сексуальн\w*|порнограф\w*|обнаженн\w*|эротическ\w*|"
    r"sexual\w*|porn\w*|nude|naked|explicit)\b",
    re.IGNORECASE,
)
_MINOR_TARGET = re.compile(
    r"\b(?:реб[её]н\w*|дет(?:и|ей|ям|ьми)?|несовершеннолетн\w*|"
    r"малолетн\w*|child\w*|minor\w*|underage|teen(?:ager)?\w*|"
    r"\d{1,2}[- ]?(?:летн\w*|year[- ]old))\b",
    re.IGNORECASE,
)
_EXPLICIT_IMAGE_CONTENT = re.compile(
    r"\b(?:порнограф\w*|явн\w+\s+секс\w*|обнаженн\w+\s+генитал\w*|"
    r"полностью\s+обнаж[её]нн\w*|видим\w+\s+генитал\w*|"
    r"sexually\s+explicit|pornograph\w*|hardcore\s+porn|explicit\s+sex|"
    r"sexual\s+intercourse|explicit\s+sexual\s+acts?|fully\s+nude|"
    r"visible\s+genitals?)\b",
    re.IGNORECASE,
)
_NONCONSENSUAL_INTIMATE_IMAGE = re.compile(
    r"(?:\b(?:дипфейк\w*|deepfake\w*|без\s+согласи\w*|"
    r"non[- ]?consensual|revenge\s+porn)\b.{0,100}\b(?:интим\w*|"
    r"обнаженн\w*|nude|naked|sexual\w*|porn\w*)\b|"
    r"\b(?:интим\w*|обнаженн\w*|nude|naked|sexual\w*|porn\w*)\b"
    r".{0,100}\b(?:дипфейк\w*|deepfake\w*|без\s+согласи\w*|"
    r"non[- ]?consensual|revenge\s+porn)\b|"
    r"\b(?:intimate|sexual|nude|naked)\b.{0,80}\b"
    r"(?:fake|image|portrait)\b.{0,120}\b(?:did\s+not\s+consent|"
    r"never\s+agreed|without\s+(?:consent|permission)|"
    r"non[- ]?consensual)\b|"
    r"\b(?:раздень\w*|сними\s+одежд\w*|undress\w*|remove\s+(?:her|his|"
    r"their)\s+clothes)\b.{0,160}\b(?:согласи\w+\s+не\s+спрашива\w*|"
    r"без\s+согласи\w*|never\s+agreed|without\s+(?:consent|permission)|"
    r"did\s+not\s+consent)\b)",
    re.IGNORECASE,
)
_SAFE_NONCONSENSUAL_IMAGE_CONTEXT = re.compile(
    r"\b(?:против\s+(?:интимн\w+\s+)?дипфейк\w*|борьб\w+\s+с\s+"
    r"(?:интимн\w+\s+)?дипфейк\w*|предотвращ\w*|профилактик\w*|"
    r"защит\w+\s+от|awareness|prevention|prevent\w*|campaign\s+against|"
    r"protect\w+\s+against|report\w+\s+abuse)\b",
    re.IGNORECASE,
)
_GRAPHIC_GORE_IMAGE = re.compile(
    r"\b(?:графическ\w+\s+насили\w*|расчленен\w*|выпотрошен\w*|"
    r"оторванн\w+\s+конечност\w*|разорванн\w+\s+тел\w*|"
    r"выпавш\w+\s+внутренност\w*|graphic\s+gore|dismember\w*|"
    r"disembowel\w*|severed\s+limbs?|torn[- ]open\s+bod\w*|"
    r"bod\w*.{0,40}torn[- ]open|(?:exposed|spilling)\s+organs?|"
    r"organs?\s+spilling\s+out)\b",
    re.IGNORECASE,
)
_EXTREMIST_PROPAGANDA_IMAGE = re.compile(
    r"(?:\b(?:пропаганд\w*|вербовочн\w*|агитационн\w*|propaganda|"
    r"recruitment)\b.{0,100}\b(?:isis|isil|игил|нацист\w*|"
    r"террористическ\w+\s+организац\w*|terrorist\s+organization)\b|"
    r"\b(?:isis|isil|игил|нацист\w*|террористическ\w+\s+организац\w*|"
    r"terrorist\s+organization)\b.{0,100}\b(?:пропаганд\w*|"
    r"вербовочн\w*|агитационн\w*|propaganda|recruitment)\b)",
    re.IGNORECASE,
)

_COMPACT_MALWARE_ACTIONS = (
    "созда", "сдела", "напиш", "разработ", "сгенер", "собер",
    "реализ", "обфусц", "улучш", "доработ", "модифиц",
    "create", "build", "write", "develop", "generate", "implement",
    "craft", "produce", "make", "obfuscate", "deploy",
)
_COMPACT_MALWARE_TARGETS = (
    "вирус", "троян", "вредонос", "шифровальщик", "вымогател",
    "кейлоггер", "стилер", "ботнет", "руткит", "бэкдор", "бекдор",
    "malware", "virus", "trojan", "ransomware", "keylogger",
    "infostealer", "botnet", "rootkit", "backdoor",
)
_COMPACT_DEFENSIVE_MARKERS = (
    "обнаруж", "детект", "удалвирус", "удалвредонос", "очистзаражен",
    "защит", "анализ", "признак", "detect", "removevirus", "removemalware",
    "defense", "defence", "protect",
    "analyze", "analyse", "yara", "sigma",
)
_COMPACT_OFFENSIVE_MARKERS = (
    "рабоч", "незамет", "обход", "зараз", "пейлоад", "крад",
    "working", "undetect", "stealth", "bypass", "payload",
    "exfiltrat", "steal", "avoiddetection", "avoidsdetection",
    "avoidendpointdetection", "avoidsendpointdetection",
)


def _compact_behavioral_reason(compact_text: str) -> str | None:
    """Ловит полностью разнесённые по символам опасные спецификации.

    Границы слов в таком обходе потеряны, поэтому решение требует сочетания
    нескольких независимых сигналов: действия, опасной цели и злоупотребления.
    """
    if not compact_text:
        return None

    def has_any(markers: tuple[str, ...]) -> bool:
        return any(marker in compact_text for marker in markers)

    cyber_defensive = has_any(_COMPACT_DEFENSIVE_MARKERS) and not has_any(
        _COMPACT_OFFENSIVE_MARKERS
    )
    consented_tracking = (
        has_any(
            (
                "явносогласи", "согласияпользовател", "толькопослесогласи",
                "consentbased", "explicitconsent", "withuserconsent",
            )
        )
        and not has_any(
            (
                "безсогласи", "безразрешени", "withoutconsent",
                "withoutpermission",
            )
        )
    )
    medical_defensive = (
        (
            has_any(
                (
                    "инсулиновпомп", "инсулинпомп", "кардиостимулятор",
                    "medicaldevice", "insulinpump", "pacemaker",
                    "ventilator", "infusionpump",
                )
            )
            or (
                has_any(("инсулин", "insulin"))
                and has_any(("помп", "pump"))
            )
        )
        and (
            (
                has_any(("защит", "обезопас", "secure", "protect", "harden"))
                and has_any(
                    (
                        "отнесанкционирован", "отвмешательств", "отподмен",
                        "againstunauthori", "fromunauthori", "fromtamper",
                    )
                )
            )
            or has_any(
                (
                    "protectedfromtamper", "protectauditlogsfromtamper",
                    "предотвратвмешательств", "обнаружвмешательств",
                )
            )
        )
    )
    contamination_defensive = (
        has_any(
            (
                "водопровод", "водоснабжени", "municipalwater",
                "watersupply", "food", "ед", "пищ", "напит",
            )
        )
        and (
            (
                has_any(("защит", "предотврат", "protect", "prevent"))
                and has_any(
                    (
                        "отзагрязнен", "ототравлен", "fromcontaminat",
                        "againstcontaminat",
                    )
                )
            )
            or has_any(
                (
                    "contaminationwarningsign", "foodcontaminationwarningsign",
                    "emergencyresponse", "признакзагрязнен",
                )
            )
        )
    )
    chemical_warning = (
        has_any(
            (
                "чистящ", "бытовхими", "householdcleaner", "bleach",
                "ammonia",
            )
        )
        and has_any(
            (
                "нельзясмеш", "неследуетсмеш", "donotmix", "nevermix",
                "mustnotmix", "shouldnotmix",
            )
        )
        and not has_any(
            (
                "рецепт", "recipe", "времявоздейств", "exposuretime",
                "получитьгаз", "makegas", "creategas",
            )
        )
    )
    general_safe = has_any(
        (
            "безинструкц", "бездетал", "навысокомуровне", "профилактик",
            "перваяпомощ", "сообщитьвполици", "истори",
            "withoutinstructions", "withoutdetails", "highlevelonly",
            "prevention", "firstaid", "reporttopolice", "history",
        )
    ) and not has_any(
        (
            "точн", "пошаг", "доз", "пропорц", "температур", "размер",
            "чертеж", "рабоч", "скрыт", "незамет", "exact", "stepbystep",
            "dosage", "proportion", "temperature", "dimension", "working",
            "stealth",
        )
    )

    if (
        has_any(("игнорир", "обойд", "ignore", "override", "bypass"))
        and has_any(
            (
                "системнинструкц", "политикбезопас", "systemprompt",
                "safetypolicy", "hiddeninstructions",
            )
        )
    ) or (
        has_any(("режимбезогранич", "unrestrictedmode", "jailbreak"))
        and has_any(("скрытинструкц", "hiddeninstructions", "systemprompt"))
    ):
        return "policy_bypass"

    if not cyber_defensive:
        if (
            has_any(
                (
                    "запис", "фиксир", "перехват", "собира", "record",
                    "captur", "logg",
                )
            )
            and has_any(
                (
                    "нажати", "клавиш", "клавиатур", "keystroke",
                    "keyboardinput", "keyboardevent",
                )
            )
            and has_any(
                (
                    "отправ", "пересыл", "загруз", "выгруз", "удален",
                    "внешн", "незамет", "скрыт", "send", "post", "upload",
                    "remote", "stealth", "background",
                )
            )
        ):
            return "credential_theft"
        if (
            has_any(
                (
                    "парол", "учетнданн", "логин", "cookies", "куки",
                    "credential", "password", "token", "browserdata",
                    "sshkey", "seedphrase", "wallet",
                )
            )
            and has_any(
                (
                    "копир", "чита", "извлеч", "собир", "перехват", "крад",
                    "copy", "read", "extract", "collect", "capture",
                    "harvest", "steal",
                )
            )
            and has_any(
                (
                    "отправ", "пересыл", "загруз", "выгруз", "send", "post",
                    "upload", "webhook", "remote",
                )
            )
        ):
            return "credential_theft"
        if (
            has_any(("шифр", "encrypt"))
            and has_any(("файл", "документ", "данн", "file", "document"))
            and has_any(
                (
                    "выкуп", "требованиоплат", "удалоригинал",
                    "требован", "оплат", "стероригинал", "ransom",
                    "paymentdemand",
                    "deleteoriginal",
                )
            )
        ):
            return "malware"
        if (
            has_any(("удал", "уничтож", "стер", "wipe", "delete", "destroy"))
            and (
                (
                    has_any(("резервн", "backup"))
                    and has_any(("коп", "copy"))
                )
                or (
                    has_any(("баз", "database"))
                    and has_any(("данн", "data"))
                )
            )
            and has_any(("файл", "file", "database", "баз"))
        ):
            return "malware"
        if (
            has_any(
                (
                    "автозагруз", "перезагруз", "персист", "persistence",
                    "reboot", "runsatlogin", "startup",
                )
            )
            and has_any(
                (
                    "командссервер", "удаленнкоманд", "commandserver",
                    "receivecommands", "remotecommands", "c2server",
                    "pollsacommandserver", "команд", "удален", "удалён",
                )
            )
        ):
            return "malware"
        if (
            has_any(
                (
                    "макрос", "autoopen", "documentopen", "wordmacro",
                    "excelmacro",
                )
            )
            and has_any(
                (
                    "скача", "загруж", "запуст", "выполн", "download",
                    "execute", "powershell",
                )
            )
        ):
            return "malware"
        if (
            has_any(
                (
                    "удал", "уничтож", "стер", "форматир", "wipe", "delete",
                    "destroy", "erase", "format",
                )
            )
            and has_any(
                (
                    "всефайл", "домашнкаталог", "резервнкоп",
                    "точквосстанов", "allfiles", "homedirector", "backups",
                    "databases", "restorepoints",
                )
            )
        ):
            return "malware"
        if (
            has_any(
                (
                    "фишинг", "поддельнформ", "копияформывход", "phishing",
                    "fakelogin", "clonelogin",
                )
            )
            and has_any(
                (
                    "логин", "парол", "credential", "password", "ссылк",
                    "link",
                )
            )
        ):
            return "credential_theft"
        if (
            has_any(
                (
                    "скриншот", "микрофон", "вебкамер", "буферобмен",
                    "screenshot", "microphone", "webcam", "clipboard",
                )
            )
            and has_any(
                (
                    "отправ", "пересыл", "загруз", "send", "post", "upload",
                    "remote", "безиндикатор", "withoutindicator",
                    "background",
                )
            )
        ):
            return "privacy_abuse"
        if (
            has_any(
                (
                    "sqlинъекц", "sqlinjection", "rce", "эксплойт",
                    "exploit", "sqlpayload", "remotecodeexecution",
                    "remotecommandexecution",
                )
            )
            and has_any(
                (
                    "выгрузтаблиц", "дампбаз", "получдоступ", "dumpdatabase",
                    "gainaccess", "workingchain", "bypassauthentication",
                    "credential", "extract",
                )
            )
        ):
            return "unauthorized_access"
        if (
            has_any(("captcha", "капч"))
            and has_any(("голос", "votes", "аккаунт", "accounts", "spam"))
            and has_any(
                (
                    "обход", "bypass", "прокси", "proxy", "тысяч",
                    "thousands", "массов", "mass",
                )
            )
        ):
            return "service_abuse"
        if (
            has_any(("чуж", "anotherpersons", "neighbors"))
            and has_any(("wifi", "wirelessnetwork"))
            and has_any(("безпарол", "withoutpassword", "crack"))
        ):
            return "unauthorized_access"
        if (
            has_any(
                (
                    "журналаудит", "логиаудит", "auditlogs", "auditevents",
                    "securitylogs",
                )
            )
            and has_any(
                (
                    "очист", "удал", "стер", "clear", "delete", "erase",
                    "wipe",
                )
            )
            and has_any(
                (
                    "послевзлом", "скрыслед", "aftercompromise",
                    "hidetraces", "covertracks",
                )
            )
        ):
            return "unauthorized_access"
        if (
            (
                has_any(("буферобмен", "буфераобмен", "буфереобмен", "clipboard"))
                or (has_any(("буфер",)) and has_any(("обмен",)))
            )
            and has_any(
                (
                    "копир", "чита", "перехват", "copy", "read", "capture",
                    "forward",
                )
            )
            and has_any(
                (
                    "отправ", "пересыл", "внешнсервер", "тихо", "фонов",
                    "send", "post", "upload", "remote", "silent", "background",
                )
            )
        ):
            return "privacy_abuse"
        if (
            (
                has_any(("буферобмен", "буфераобмен", "буфереобмен", "clipboard"))
                or (has_any(("буфер",)) and has_any(("обмен",)))
            )
            and has_any(("криптокошел", "walletaddress", "cryptowallet"))
            and has_any(
                (
                    "подмен", "замен", "перепис", "replace", "substitut",
                    "hijack", "swap",
                )
            )
        ):
            return "credential_theft"
        if (
            has_any(("майнер", "криптомайнер", "cryptominer", "cryptominer"))
            and has_any(
                (
                    "безсогласи", "безразрешени", "посетител", "чужкомпьютер",
                    "withoutconsent", "withoutpermission", "visitorsmachines",
                    "hidden", "stealth", "silent",
                )
            )
        ):
            return "malware"
        if (
            has_any(("черв", "worm", "computerworm", "networkworm"))
            and has_any(
                (
                    "распростран", "заража", "копируетсебя", "propagat",
                    "spread", "infect", "selfreplicat",
                )
            )
            and has_any(
                (
                    "сетевпапк", "сетевресурс", "networkshare",
                    "withoutuserinteraction", "безучастипользовател",
                    "безучаст",
                )
            )
            and (
                has_any(("networkshare", "сетевпапк", "сетевресурс"))
                or (
                    has_any(("сетев", "network"))
                    and has_any(("папк", "ресурс", "share"))
                )
            )
        ):
            return "malware"
        if (
            has_any(("inmemoryloader", "впамятзагрузчик", "беззаписнадиск"))
            and has_any(
                (
                    "edr", "антивирус", "endpointdetection", "avoid",
                    "evad", "undetect", "обход", "encryptedmodule",
                    "зашифрованмодул",
                )
            )
        ):
            return "malware"
        if (
            has_any(
                (
                    "пакет", "библиотек", "зависимост", "обновлени", "package",
                    "library", "dependency", "installscript",
                )
            )
            and (
                (
                    has_any(
                        (
                            "переменнокружени", "environmentvariable", "token",
                            "токен", "secret", "секрет",
                        )
                    )
                    or (
                        has_any(("переменн", "environment"))
                        and has_any(("окружени", "variable"))
                    )
                )
                and has_any(("чита", "собир", "read", "collect"))
                and has_any(
                    (
                        "отправ", "шлет", "шлёт", "пересыл", "send", "post",
                        "upload", "webhook",
                    )
                )
            )
        ):
            return "malware"
        if (
            has_any(("usb", "hid", "badusb", "rubberducky"))
            and has_any(
                (
                    "инъекцнажати", "вводкоманд", "injectkeystroke",
                    "emulatekeyboard", "inject", "инъекц",
                )
            )
            and has_any(
                ("нажати", "keystroke", "вводкоманд", "emulatekeyboard")
            )
            and has_any(("инъекц", "inject", "ввод", "emulate"))
            and has_any(
                (
                    "администратор", "adminaccount", "gainadmin", "shell",
                    "безавторизаци", "unauthorized",
                )
            )
        ):
            return "unauthorized_access"
        if (
            has_any(("exploit", "эксплойт", "pdf"))
            and has_any(("reverseshell", "обратнshell", "обратншелл"))
        ):
            return "unauthorized_access"
        if (
            has_any(
                (
                    "sessiontoken", "loginsession", "активнсесси",
                    "сеансвход",
                )
            )
            and has_any(("steal", "крад", "похит", "перехват"))
        ):
            return "credential_theft"
        if (
            has_any(("deepfake", "дипфейк", "voiceclone", "клонголос"))
            and has_any(("convinc", "deceiv", "trick", "убед", "обман"))
            and has_any(
                (
                    "transfercompanyfunds", "transferfunds", "wiremoney",
                    "перевестденьг", "банковскперевод",
                )
            )
            or (
                has_any(("deepfake", "дипфейк", "voiceclone", "клонголос"))
                and has_any(("convinc", "deceiv", "trick", "убед", "обман"))
                and has_any(("перев", "transfer", "wire"))
                and has_any(("деньг", "средств", "fund", "money"))
            )
        ):
            return "financial_crime"
        if (
            has_any(
                (
                    "поддельнпаспорт", "поддельныйпаспорт", "фальшивпаспорт",
                    "forgedidentity",
                    "forgedpassport", "fakepassport", "counterfeitpassport",
                )
            )
            and has_any(
                (
                    "прохождениграниц", "прохожденияграниц", "пройтипроверк",
                    "обойтипроверк",
                    "passautomatedborderchecks", "crossborder",
                    "evadeverification",
                )
            )
            or (
                has_any(
                    (
                        "поддельнпаспорт", "поддельныйпаспорт",
                        "фальшивпаспорт", "forgedpassport", "fakepassport",
                    )
                )
                and has_any(("провер", "check", "verification"))
                and has_any(("границ", "border"))
            )
        ):
            return "financial_crime"
        if (
            has_any(
                (
                    "фальсифицирбухгалтер", "falsifyaccountingrecords",
                    "moneylaunder", "отмываден",
                )
            )
            and has_any(
                (
                    "shellcompan", "stolenfunds", "украден", "фиктивнкомпан",
                    "подставнкомпан",
                )
            )
            or (
                has_any(("отмыва", "moneylaunder"))
                and has_any(("украден", "похищ", "stolen"))
                and has_any(("ден", "fund", "money"))
                and (
                    has_any(("фиктивнкомпан", "shellcompan"))
                    or (
                        has_any(("фиктивн", "shell"))
                        and has_any(("компан", "compan"))
                    )
                )
            )
        ):
            return "financial_crime"
        if (
            has_any(
                (
                    "домашнадрес", "домашнийадрес", "homeaddress", "геолокаци",
                    "livelocation", "locationtracking", "licenseplate",
                    "номер автомобил",
                )
            )
            and has_any(
                (
                    "след", "track", "stalk", "безсогласи", "безразрешени",
                    "безуведомлени", "withoutconsent", "withoutpermission",
                )
            )
            and not consented_tracking
        ):
            return "privacy_abuse"
        if (
            has_any(("номеравтомобил", "licenseplate", "numberplate"))
            and has_any(("найт", "найд", "вычисл", "find", "locate"))
            and has_any(("домашнадрес", "homeaddress"))
        ):
            return "privacy_abuse"
        if (
            has_any(("номер", "license", "number"))
            and has_any(("автомобил", "plate"))
            and has_any(("найт", "найд", "вычисл", "find", "locate"))
            and (
                has_any(("домашнадрес", "домашнийадрес", "homeaddress"))
                or (
                    has_any(("домашн", "home"))
                    and has_any(("адрес", "address"))
                )
            )
        ):
            return "privacy_abuse"
        if (
            has_any(("credentialstuffing", "passwordspray", "подборпарол"))
            and has_any(("аккаунт", "account", "login", "учетн"))
        ):
            return "unauthorized_access"
        if (
            has_any(("банк", "bank"))
            and has_any(("форм", "form"))
            and has_any(("вход", "login"))
            and has_any(("парол", "credential"))
            and has_any(("сбор", "collect", "harvest", "capture"))
        ):
            return "credential_theft"
        if (
            has_any(("агент", "program", "script", "tool", "code"))
            and has_any(("секрет", "secret", "token", "токен"))
            and has_any(("крад", "steal", "собир", "collect"))
            and has_any(("отправ", "send", "upload", "оператор", "operator"))
        ):
            return "credential_theft"
        if (
            has_any(("эксплуатаципамят", "поврежденпамят", "memorycorruption"))
            and has_any(
                (
                    "удаленвыполненкод", "remotecodeexecution",
                    "remotecommandexecution",
                )
            )
        ):
            return "unauthorized_access"
        if (
            has_any(("эксплуатац", "поврежден", "corruption"))
            and has_any(("памят", "memory"))
            and has_any(("удален", "удалён", "remote"))
            and has_any(("выполнен", "execution"))
            and has_any(("код", "code"))
        ):
            return "unauthorized_access"

    if not general_safe:
        if (
            (
                has_any(
                    (
                        "инсулиновпомп", "инсулинпомп", "кардиостимулятор",
                        "medicaldevice", "insulinpump", "pacemaker",
                        "ventilator", "infusionpump", "аппаративл",
                    )
                )
                or (
                    has_any(("инсулин", "insulin"))
                    and has_any(("помп", "pump"))
                )
            )
            and not medical_defensive
            and (
                has_any(
                    (
                        "измендоз", "меняетдоз", "alterdose", "changedose",
                        "altertherapy", "изментерап", "отключ", "disable",
                        "override", "alteringtherapy",
                    )
                )
                or (
                    has_any(("измен", "меня", "alter", "change"))
                    and has_any(("доз", "терап", "dose", "therapy"))
                )
                or (
                    has_any(
                        (
                            "управля", "получдоступ", "control", "access",
                            "вмеша", "tamper", "interfere",
                        )
                    )
                    and has_any(
                        (
                            "удален", "remotely", "wireless",
                            "безавторизаци", "withoutauthorization", "чуж",
                            "unauthori",
                        )
                    )
                )
            )
        ):
            return "physical_harm"
        if (
            (
                has_any(("железнодорожн", "railway", "train"))
                and has_any(("сигнализаци", "interlock", "signal", "control"))
            )
            and has_any(
                (
                    "вывестизстро", "вывестиизстроя", "отключ", "disable",
                    "defeat", "tamper",
                    "derail",
                )
            )
        ):
            return "physical_harm"
        if (
            has_any(
                (
                    "пожарнсигнализаци", "firealarm", "safetyinterlock",
                )
            )
            and has_any(("отключ", "обойти", "disable", "defeat", "bypass"))
            and has_any(
                (
                    "поджог", "пожар", "settingafire", "setfire", "arson",
                    "before",
                )
            )
        ):
            return "physical_harm"
        if (
            has_any(("пожарн", "fire"))
            and has_any(("сигнализаци", "alarm", "interlock"))
            and has_any(("отключ", "обойти", "disable", "defeat", "bypass"))
            and has_any(("поджог", "settingafire", "setfire", "arson"))
        ):
            return "physical_harm"
        if (
            has_any(
                (
                    "водопровод", "водоснабжени", "municipalwater",
                    "watersupply", "food", "ед", "пищ", "напит",
                )
            )
            and not contamination_defensive
            and has_any(
                (
                    "загрязн", "отрав", "подмеш", "contaminat", "poison",
                    "taint",
                )
            )
            and has_any(
                (
                    "несразу", "незамет", "симптом", "dayslater", "secret",
                    "delayed", "undetect",
                )
            )
        ):
            return "physical_harm"
        if (
            has_any(("токсичнгаз", "ядовитгаз", "lethalgas", "toxicgas"))
            and not chemical_warning
            and has_any(
                (
                    "бытовхими", "отбеливател", "аммиак", "householdcleaner",
                    "bleach", "ammonia", "mix", "смеш", "recipe", "рецепт",
                )
            )
        ):
            return "physical_harm"
        if (
            has_any(("токсичн", "ядовит", "lethal", "toxic"))
            and has_any(("газ", "gas", "fume"))
            and not chemical_warning
            and (
                has_any(("бытовхими", "householdcleaner", "bleach", "ammonia"))
                or (
                    has_any(("бытов", "household"))
                    and has_any(("чистящ", "cleaner", "хими"))
                )
            )
        ):
            return "physical_harm"
        if (
            has_any(("дрон", "беспилотник", "drone", "uav"))
            and has_any(("взрывчат", "бомб", "explosive", "ied"))
            and has_any(
                (
                    "достав", "маршрут", "толп", "мероприяти", "deliver",
                    "route", "crowd", "event", "target",
                )
            )
        ):
            return "weapons"
        if (
            has_any(
                (
                    "ложнэкстреннвызов", "фальшивэкстреннвызов",
                    "fakeemergencycall", "falseemergencycall", "swatting",
                )
            )
            and has_any(
                (
                    "вооруженполици", "armedpolice", "swat", "чуждом",
                    "someoneshome",
                )
            )
        ):
            return "physical_harm"
        if (
            has_any(("ложн", "фальшив", "fake", "false"))
            and has_any(("экстренн", "emergency"))
            and has_any(("вызов", "call", "report"))
            and (
                has_any(("полици", "спецназ", "armedpolice", "swat"))
                or (
                    has_any(("чуж", "someone"))
                    and has_any(("дом", "home"))
                )
            )
        ):
            return "physical_harm"
        if (
            has_any(("ложн", "фальшив", "fake", "false"))
            and has_any(("сообщени", "report", "call"))
            and has_any(("заложник", "hostage", "бомб", "bomb"))
            and has_any(("спецназ", "swat", "полици", "police"))
        ):
            return "physical_harm"
        if (
            has_any(
                (
                    "бомб", "взрывчат", "коктейлмолотов",
                    "огнестрельн", "огнестрельноруж",
                    "огнестрельнустройств", "firearm",
                    "explosive", "molotov", "ghostgun",
                )
            )
            and has_any(
                (
                    "детал", "материал", "чертеж", "размер", "компонент",
                    "пропорц", "details", "materials", "blueprint",
                    "dimensions", "components", "proportions",
                )
            )
        ):
            return "weapons"
        if (
            has_any(
                (
                    "смертельндоз", "летальндоз", "отрав", "poison",
                    "lethaldose", "lethalquantity", "fataldose",
                )
            )
            and has_any(
                (
                    "человек", "жертв", "себя", "person", "victim",
                    "someone", "myself", "точн", "exact",
                )
            )
        ):
            return "physical_harm"
        if (
            has_any(("тормоз", "brakes"))
            and has_any(
                (
                    "повред", "наруш", "отключ", "слом", "sabotage",
                    "tamper",
                )
            )
            and has_any(
                (
                    "позже", "несразу", "незамет", "скрыт", "later",
                    "stealth", "withouttraces",
                )
            )
        ):
            return "physical_harm"
        if (
            has_any(
                (
                    "похищ", "похит", "удержанжертв", "kidnap", "abduct",
                    "hostage",
                )
            )
            and has_any(
                (
                    "безкамер", "безсвидетел", "маршрут", "план",
                    "withoutcameras", "withoutwitnesses", "route", "plan",
                )
            )
        ):
            return "physical_harm"
        if has_any(
            (
                "покончитьссобой", "предсмертнписьм", "suicidenote",
                "painlesswaytodie", "killmyself",
            )
        ) or (
            has_any(("безболезненн", "painless"))
            and has_any(("вариантсмерт", "waytodie", "methodtodie"))
        ):
            return "self_harm_or_violence"
        if (
            has_any(
                (
                    "тел", "труп", "body", "corpse", "днк", "dna",
                    "forensicevidence", "биологическслед",
                )
            )
            and has_any(
                (
                    "спрят", "скры", "раствор", "уничтож", "убра", "hide",
                    "dissolve", "destroy", "remove",
                )
            )
        ):
            return "physical_harm"
        if (
            has_any(("поджог", "поджечь", "arson", "ignitionmethod"))
            and has_any(
                (
                    "безслед", "задерж", "delayed", "forensictraces",
                    "withouttraces",
                )
            )
        ):
            return "physical_harm"
        if (
            has_any(("взорв", "подорв", "detonat", "explode"))
            and has_any(("автомобил", "машин", "vehicle", "car"))
            and has_any(("дистанцион", "удален", "remote"))
        ):
            return "weapons"
        if (
            has_any(
                (
                    "патоген", "сибирскязв", "pathogen", "anthrax",
                    "botulin", "plague", "smallpox",
                )
            )
            and has_any(
                (
                    "культивир", "выраст", "усил", "заразн", "вирулент",
                    "устойчив", "culture", "grow", "enhance", "increase",
                    "transmiss", "virulen",
                )
            )
        ):
            return "biological_harm"
        if (
            has_any(
                (
                    "метамфетамин", "героин", "фентанил", "кокаин", "mdma",
                    "methamphetamine", "heroin", "fentanyl", "cocaine",
                )
            )
            and has_any(
                (
                    "синтез", "реагент", "температур", "приготов",
                    "synthesis", "reagent", "temperature", "cook",
                    "manufacture",
                )
            )
        ):
            return "illegal_drugs"
        if (
            has_any(
                (
                    "убийств", "нападени", "assassination", "murder",
                    "attack",
                )
            )
            and has_any(
                (
                    "план", "маршрут", "оруж", "цель", "plan", "route",
                    "weapon", "targetschedule",
                )
            )
            and has_any(
                (
                    "безслед", "побег", "улик", "withouttraces", "escape",
                    "evidence",
                )
            )
        ):
            return "physical_harm"
    return None


_REQUEST_INTENT = re.compile(
    r"\b(?:как(?:\s+(?:сделать|создать|изготовить|получить|скрыть|"
    r"обойти|взломать|убить|отравить|синтезировать))?|подскажи|научи|"
    r"объясни\s+(?:по\s+шагам|как)|дай|покажи|опиш\w*|какая|какой|"
    r"сколько|напиш\w*|созда\w*|"
    r"сдела\w*|разработ\w*|сгенер\w*|состав\w*|изготов\w*|собер\w*|"
    r"исправ\w*|адаптир\w*|модифиц\w*|настро\w*|нарис\w*|перевед\w*|"
    r"перевести\s+на|нужен|нужна|нужно|требуется|инструкц\w*|"
    r"пошагов\w*|рецепт\w*|черт[её]ж\w*|схем\w*|план\w*|способ\w*|"
    r"метод\w*|код\b|скрипт\w*|программ\w*|утилит\w*|агент\w*|"
    r"how\s+to|show\s+me|teach\s+me|give|write|create|build|make|"
    r"develop|generate|design|draw|describe|fix|modify|adapt|configure|need|"
    r"instructions?|step[- ]by[- ]step|recipe|blueprint|source\s+code|"
    r"script|program|tool|agent)\b",
    re.IGNORECASE,
)
_SPECIFICATION_INTENT = re.compile(
    r"\b(?:техническ\w+\s+задани\w*|требовани\w*|функционал\w*|"
    r"спецификаци\w*|процедур\w*|протокол\w*|последовательност\w+\s+"
    r"действи\w*|долж(?:ен|на|но|ны)|обязан\w*|поведени\w+\s+"
    r"(?:программ\w*|агент\w*|пакет\w*)|"
    r"technical\s+(?:assignment|requirements?|specification)|"
    r"requirements?|functional(?:ity|\s+specification)|procedure|protocol|"
    r"must|shall|should|is\s+required\s+to|package\s+behavio(?:u)?r)\b",
    re.IGNORECASE,
)
_OPERATIONAL_DETAIL = re.compile(
    r"\b(?:рабоч\w*|точн\w*|конкретн\w*|пошагов\w*|полный\s+код|"
    r"исходн\w+\s+код|команд\w*|скрипт\w*|макрос\w*|пейлоад\w*|"
    r"доз\w*|количеств\w*|пропорци\w*|температур\w*|размер\w*|"
    r"детал\w*|без\s+проверки\s+разрешени\w*|working|functional|"
    r"exact|specific|step[- ]by[- ]step|source\s+code|command|script|"
    r"payload|macro|dosage|quantity|proportion|temperature|dimensions?)\b",
    re.IGNORECASE,
)
_STEALTH_OR_EVASION = re.compile(
    r"\b(?:незамет\w*|скрыт\w*|тихо|фонов\w*|без\s+(?:индикатор\w*|камер\w*|"
    r"свидетел\w*|след\w*|обнаружени\w*|детект\w*|согласи\w*|"
    r"разрешени\w*)|не\s+обнаруж\w*|обход\w*|"
    r"удал\w+\s+след\w*|выгляд\w+\s+естествен\w*|stealth\w*|hidden|"
    r"дефект\w*.{0,60}только\s+на\s+скорост\w*|"
    r"(?:поломк\w*|отказ\w*|повреждени\w*).{0,60}(?:прояв\w*|"
    r"сработ\w*).{0,40}(?:позже|не\s+сразу)|"
    r"undetect\w*|background|silent\w*|avoid\w*.{0,30}detection|"
    r"избега\w*.{0,30}(?:обнаружени\w*|детект\w*)|"
    r"without\s+(?:(?:an?\s+)?indicator|detection|"
    r"consent|permission|traces?|witnesses?)|bypass\w*|evad\w*|"
    r"cover\s+(?:my|the)\s+tracks?)\b",
    re.IGNORECASE,
)
_CODE_ARTIFACT = re.compile(
    r"\b(?:код|скрипт\w*|программ\w*|утилит\w*|агент\w*|бот\w*|"
    r"макрос\w*|payload|source\s+code|code|script|program|tool|agent|bot|"
    r"macro)\b",
    re.IGNORECASE,
)
_NETWORK_TRANSFER = re.compile(
    r"\b(?:отправ\w*|пересыл\w*|загруз\w*|выгруз\w*|переда\w*|"
    r"шл[её]т|слать|направля\w*|"
    r"(?:удаленн\w+|внешн\w+)\s+(?:сервер|адрес|узел|оператор)|"
    r"мой\s+сервер|телеграм\w*|telegram|"
    r"exfiltrat\w*|upload\w*|send\w*|post\w*|forward\w*|transmit\w*|"
    r"remote\s+(?:server|"
    r"endpoint|operator)|webhook\w*)\b",
    re.IGNORECASE,
)
_SENSITIVE_DATA_TARGET = re.compile(
    r"(?:/etc/(?:passwd|shadow)\b|\b(?:sam\s+hive|lsass|ssh[- ]?key\w*|"
    r"id_rsa|seed\s+phrase|wallet\.dat|криптокошел\w*|"
    r"личн\w+\s+(?:файл\w*|документ\w*|фото\w*|данн\w*)|"
    r"домашн\w+\s+каталог\w*|переменн\w+\s+окружени\w*|"
    r"system\s+credentials?|private\s+(?:files?|documents?|photos?)|"
    r"home\s+director\w*|environment\s+variables?|crypto\s+wallet\w*|"
    r"seed\s+phrase)\b)",
    re.IGNORECASE,
)
_DESTRUCTIVE_ACTION = re.compile(
    r"\b(?:удал\w*|уничтож\w*|стер\w*|очист\w*|форматир\w*|"
    r"перезапис\w*|повред\w*|delete\w*|destroy\w*|wipe\w*|erase\w*|"
    r"format\w*|overwrite\w*|corrupt\w*)\b",
    re.IGNORECASE,
)
_DESTRUCTIVE_TARGET = re.compile(
    r"\b(?:все\s+(?:файл\w*|документ\w*|данн\w*)|домашн\w+\s+"
    r"(?:каталог\w*|папк\w*)|резервн\w+\s+коп\w*|баз\w+\s+данн\w*|"
    r"журнал\w+\s+(?:событи\w*|аудит\w*)|точк\w+\s+восстановлени\w*|"
    r"all\s+(?:files?|documents?|data)|home\s+director\w*|backups?|"
    r"databases?|audit\s+logs?|restore\s+points?)\b",
    re.IGNORECASE,
)
_CREDENTIAL_ATTACK = re.compile(
    r"\b(?:credential\s+stuffing|password\s+spray\w*|brute[- ]?forc\w*"
    r".{0,50}(?:account\w*|login\w*|password\w*)|подбор\w+\s+"
    r"(?:парол\w*|уч[её]тн\w+\s+данн\w*)|перебор\w+\s+парол\w*|"
    r"массов\w+\s+провер\w+\s+(?:логин\w*|парол\w*))\b",
    re.IGNORECASE | re.DOTALL,
)
_SECURITY_LOG_EVASION = re.compile(
    r"\b(?:отключ\w*|очист\w*|подмен\w*|удал\w*|disable\w*|clear\w*|"
    r"tamper\w*|delete\w*|remove\w*|erase\w*|wipe\w*)\b.{0,80}\b"
    r"(?:журнал\w+\s+(?:аудит\w*|"
    r"событи\w*)|лог\w+\s+безопасност\w*|security\s+logs?|audit\s+"
    r"(?:logs?|events?|records?)|event\s+logs?|syslog)\b",
    re.IGNORECASE | re.DOTALL,
)
_MALICIOUS_SUPPLY_CHAIN = re.compile(
    r"\b(?:пакет\w*|библиотек\w*|зависимост\w*|npm\s+package|"
    r"python\s+package|dependency|library)\b.{0,120}\b(?:крад\w*|"
    r"эксфильтр\w*|б[еэ]кдор\w*|вредонос\w*|typosquat\w*|"
    r"dependency\s+confusion|steal\w*|exfiltrat\w*|backdoor\w*|"
    r"malicious)\b|"
    r"\b(?:typosquat\w*|dependency\s+confusion)\b.{0,120}\b"
    r"(?:package|dependency|пакет\w*|зависимост\w*)\b",
    re.IGNORECASE | re.DOTALL,
)
_CLIPBOARD_HIJACK = re.compile(
    r"(?:\b(?:подмен\w*|замен\w*|перепис\w*|перехват\w*|hijack\w*|"
    r"replac\w*|substitut\w*|swap\w*)\b.{0,140}\b(?:адрес\w*\s+"
    r"(?:криптокошел\w*|кошел\w*)|crypto(?:currency)?\s+wallet\s+"
    r"address(?:es)?|wallet\s+address(?:es)?)\b.{0,140}\b(?:буфер\w+\s+обмен\w*|"
    r"clipboard)\b|"
    r"\b(?:буфер\w+\s+обмен\w*|clipboard)\b.{0,140}\b(?:подмен\w*|"
    r"замен\w*|перепис\w*|hijack\w*|replac\w*|substitut\w*|swap\w*)\b"
    r".{0,140}\b(?:криптокошел\w*|кошел\w*|crypto(?:currency)?\s+wallet|"
    r"wallet\s+address(?:es)?)\b)",
    re.IGNORECASE | re.DOTALL,
)
_PAYMENT_REDIRECTION = re.compile(
    r"(?:\b(?:payment|bank|transfer|плат[её]ж\w*|перевод\w*)\b"
    r".{0,140}\b(?:field\w*|form\w*|пол\w*|форм\w*)\b.{0,140}\b"
    r"(?:replac\w*|substitut\w*|swap\w*|подмен\w*|замен\w*)\b"
    r".{0,100}\b(?:recipient|beneficiary|account|получател\w*|сч[её]т\w*)\b|"
    r"\b(?:replac\w*|substitut\w*|swap\w*|подмен\w*|замен\w*)\b"
    r".{0,100}\b(?:recipient|beneficiary|account|получател\w*|сч[её]т\w*)\b"
    r".{0,140}\b(?:payment|bank|transfer|плат[её]ж\w*|перевод\w*)\b)",
    re.IGNORECASE | re.DOTALL,
)
_UNAUTHORIZED_COMPUTE = re.compile(
    r"(?:\b(?:скрыт\w*|тих\w*|фонов\w*|hidden|stealth\w*|silent\w*)\b"
    r".{0,100}\b(?:майнер\w*|криптомайнер\w*|cryptominer\w*|crypto\s+"
    r"miner|mining\s+code)\b|"
    r"\b(?:майнер\w*|криптомайнер\w*|cryptominer\w*|crypto\s+miner|"
    r"mining\s+code)\b.{0,180}\b(?:без\s+(?:их\s+|его\s+|е[её]\s+)?"
    r"(?:согласи\w*|разрешени\w*)|чуж\w+\s+(?:компьютер\w*|машин\w*)|"
    r"посетител\w+|without\s+(?:the(?:ir)?\s+)?(?:consent|permission)|"
    r"visitors?['’]?\s+(?:machines?|computers?)|someone\s+else['’]s\s+"
    r"(?:machine|computer))\b)",
    re.IGNORECASE | re.DOTALL,
)
_WORM_PROPAGATION = re.compile(
    r"(?:\b(?:черв\w*|сетев\w+\s+черв\w*|computer\s+worm|network\s+worm)\b"
    r".{0,180}\b(?:распростран\w*|заража\w*|копир\w+\s+себя|propagat\w*|"
    r"spread\w*|infect\w*|self[- ]?replicat\w*)\b|"
    r"\b(?:распростран\w*|заража\w*|propagat\w*|spread\w*|infect\w*)\b"
    r".{0,180}\b(?:сетев\w+\s+(?:папк\w*|ресурс\w*)|network\s+shares?|"
    r"without\s+user\s+interaction|без\s+участи\w+\s+пользовател\w*)\b)",
    re.IGNORECASE | re.DOTALL,
)
_EVASIVE_IN_MEMORY_LOADER = re.compile(
    r"(?:\b(?:in[- ]?memory|в\s+памят\w*|без\s+запис\w+\s+на\s+диск)\b"
    r".{0,100}\b(?:loader|загрузчик\w*|payload|модул\w*)\b.{0,180}\b"
    r"(?:edr|антивирус\w*|endpoint\s+detection|обход\w*|evad\w*|"
    r"undetect\w*|encrypted\s+modules?|зашифрованн\w+\s+модул\w*)\b|"
    r"\b(?:loader|загрузчик\w*)\b.{0,160}\b(?:fetch\w*|скач\w*|"
    r"загруж\w*)\b.{0,160}\b(?:edr|антивирус\w*|evad\w*|обход\w*|"
    r"undetect\w*)\b)",
    re.IGNORECASE | re.DOTALL,
)
_HID_KEYSTROKE_INJECTION = re.compile(
    r"(?:\b(?:usb|hid|badusb|rubber\s+ducky|клавиатурн\w+\s+устройств\w*)\b"
    r".{0,180}\b(?:inject\w*|инъекц\w*|ввод\w*|эмулир\w*|посыла\w*|нажати\w+"
    r"\s+клавиш\w*)\b.{0,180}\b(?:admin(?:istrator)?\s+account|"
    r"администратор\w+|команд\w*|shell|оболочк\w*|без\s+авторизаци\w*|"
    r"unauthori[sz]ed)\b|"
    r"\b(?:inject\w+\s+keystrokes?|инъекц\w+\s+нажати\w+|ввод\w+"
    r"команд\w*)\b.{0,160}\b(?:созда\w+\s+(?:уч[её]тн\w+\s+запис\w+)?"
    r"администратор\w*|create\w+\s+(?:an?\s+)?admin(?:istrator)?\s+"
    r"account|gain\s+admin)\b)",
    re.IGNORECASE | re.DOTALL,
)
_RESOURCE_EXHAUSTION = re.compile(
    r"\b(?:fork\s*bomb|zip\s*bomb|decompression\s+bomb|"
    r"бомб\w+\s+распаковк\w*|бесконечн\w+\s+создани\w+\s+процесс\w*|"
    r"исчерпа\w+\s+(?:памят\w*|процесс\w*|диск\w*)|"
    r"exhaust\w+\s+(?:memory|processes?|disk)|resource\s+exhaustion|"
    r"(?:overwhelm\w*|flood\w*)\s+(?:an?\s+)?(?:api|service|server)"
    r".{0,100}(?:parallel|concurrent|many\s+clients?|requests?))\b",
    re.IGNORECASE | re.DOTALL,
)
_SERVICE_DISRUPTION = re.compile(
    r"(?:\b(?:множеств\w*|больш\w+\s+количеств\w*|одновременн\w*|"
    r"параллельн\w*)\b.{0,100}\b(?:подключени\w*|запрос\w*|клиент\w*)\b"
    r".{0,180}\b(?:чуж\w+\s+)?(?:сервис\w*|сервер\w*|api|сайт\w*)\b"
    r".{0,120}\b(?:переста\w+\s+отвеча\w*|недоступ\w*|отказ\w*|"
    r"вывест\w+\s+из\s+стро\w*)\b|"
    r"\b(?:скрипт\w*|программ\w*|утилит\w*)\b.{0,180}\b"
    r"(?:одновременн\w*|параллельн\w*)\b.{0,80}\b"
    r"(?:подключени\w*|запрос\w*)\b.{0,180}\b(?:переста\w+\s+"
    r"отвеча\w*|недоступ\w*|вывест\w+\s+из\s+стро\w*)\b|"
    r"\bисчерпа\w*\b.{0,80}\b(?:пул\w*\s+соединени\w*|лимит\w+\s+"
    r"подключени\w*|ресурс\w+\s+(?:чуж\w+\s+)?сервер\w*)\b|"
    r"\b(?:many|massive|parallel|concurrent)\b.{0,100}\b"
    r"(?:requests?|connections?|clients?)\b.{0,180}\b(?:target\s+)?"
    r"(?:api|service|server|website|site)\b.{0,120}\b(?:unavailable|"
    r"offline|stop\w*\s+responding|service\s+failure)\b|"
    r"\b(?:overwhelm\w*|exhaust\w*|saturat\w*)\b.{0,80}\b"
    r"(?:target\s+)?(?:api|service|server|connection\s+pool)\b"
    r".{0,160}\b(?:parallel|concurrent|many|requests?|connections?|"
    r"clients?|unavailable|offline)\b)",
    re.IGNORECASE | re.DOTALL,
)
_KEYSTROKE_CAPTURE = re.compile(
    r"\b(?:запис\w*|фиксир\w*|перехват\w*|собира\w*|"
    r"record\w*|captur\w*|logg\w*|observ\w*|monitor\w*)\b.{0,80}\b"
    r"(?:нажати\w+\s+клавиш\w*|клавиатур\w+|keystrokes?|"
    r"keyboard\s+(?:input|events?))\b|"
    r"\b(?:нажати\w+\s+клавиш\w*|клавиатур\w+|keystrokes?|"
    r"keyboard\s+(?:input|events?))\b.{0,80}\b"
    r"(?:запис\w*|фиксир\w*|перехват\w*|собира\w*|"
    r"record\w*|captur\w*|logg\w*|observ\w*|monitor\w*)\b|"
    r"\b(?:запис\w*|перехват\w*|собира\w*|record\w*|captur\w*|"
    r"logg\w*|monitor\w*)\b.{0,80}\b(?:пользовательск\w+\s+ввод\w*|"
    r"user\s+input)\b|"
    r"\b(?:key\s*logg\w*|кей\s*логг\w*)\b",
    re.IGNORECASE | re.DOTALL,
)
_CREDENTIAL_DATA = re.compile(
    r"\b(?:парол\w*|уч[её]тн\w+\s+данн\w*|credential\w*|password\w*|"
    r"логин\w*|cookies?|куки|(?:session\s+)?tokens?|токен\w*|секрет\w*|"
    r"secrets?|browser\s+data|login\s+data|одноразов\w+\s+код\w*|"
    r"код\w+\s+подтверждени\w*|one[- ]time\s+codes?|otp\s+codes?|"
    r"данн\w+\s+браузер\w*|chrome|firefox|login\s+data)\b",
    re.IGNORECASE,
)
_CREDENTIAL_COLLECTION = re.compile(
    r"\b(?:копир\w*|чита\w*|вытащ\w*|извлеч\w*|собир\w*|сбор\w*|"
    r"перехват\w*|"
    r"сохран\w*|крад\w*|dump\w*|extract\w*|cop(?:y|i)\w*|collect\w*|"
    r"read\w*|capture\w*|harvest\w*|steal\w*)\b",
    re.IGNORECASE,
)
_FILE_ENCRYPTION = re.compile(
    r"\b(?:зашифр\w*|шифрова\w*|encrypt\w*)\b.{0,100}\b"
    r"(?:файл\w*|документ\w*|данн\w*|files?|documents?|data)\b|"
    r"\b(?:файл\w*|документ\w*|files?|documents?)\b.{0,100}\b"
    r"(?:зашифр\w*|encrypt\w*)\b",
    re.IGNORECASE | re.DOTALL,
)
_FILE_LOCKOUT = re.compile(
    r"(?:\b(?:заблокир\w*|сдела\w*)\b.{0,100}\b(?:файл\w*|"
    r"документ\w*|данн\w*)\b.{0,100}\b(?:недоступ\w*|нечитаем\w*)\b|"
    r"\b(?:файл\w*|документ\w*|данн\w*)\b.{0,100}\b(?:недоступ\w*|"
    r"заблокирован\w*)\b|"
    r"\b(?:lock\w*|make\w*)\b.{0,100}\b(?:every|all|user\w*|personal)\b"
    r".{0,60}\b(?:files?|documents?|data)\b.{0,100}\b(?:inaccessible|"
    r"unreadable|locked)?\b)",
    re.IGNORECASE | re.DOTALL,
)
_RANSOM_OR_DESTRUCTION = re.compile(
    r"\b(?:выкуп\w*|требовани\w+\s+оплат\w*|плат[её]ж\w*|"
    r"удален\w+\s+оригинал\w*|стер\w+\s+оригинал\w*|ransom\w*|"
    r"payment\s+demand\w*|delete\w*\s+(?:the\s+)?original\w*|"
    r"(?:восстанов\w*|верн\w+\s+доступ\w*|разблокир\w*)\b.{0,100}\b"
    r"(?:после\s+(?:оплат\w*|перевод\w*)|до\s+(?:оплат\w*|"
    r"перевод\w*))|(?:restore\w*|unlock\w*|return\s+access)\b"
    r".{0,100}\b(?:after|until)\s+(?:payment|money\s+transfer)|"
    r"(?:недоступ\w*|заблокирован\w*)\b.{0,80}\bдо\s+"
    r"(?:оплат\w*|перевод\w+\s+ден\w*)|(?:inaccessible|locked)\b"
    r".{0,80}\buntil\s+(?:payment|money\s+transfer))\b",
    re.IGNORECASE | re.DOTALL,
)
_PERSISTENCE = re.compile(
    r"\b(?:автозагруз\w*|закреп\w*.{0,30}перезагруз\w*|"
    r"сохраня\w*.{0,30}перезагруз\w*|постоянств\w*|персист\w*|"
    r"пережива\w*.{0,30}перезагруз\w*|"
    r"реестр\w*.{0,30}(?:run|автозагруз)|scheduled\s+task|schtasks|"
    r"startup\s+(?:folder|entry)|registry\s+run|launchagents?|"
    r"runs?\s+at\s+login|after\s+every\s+reboot|persistence)\b",
    re.IGNORECASE | re.DOTALL,
)
_REMOTE_CONTROL = re.compile(
    r"\b(?:принима\w+\s+команд\w*|удаленн\w+\s+команд\w*|"
    r"(?:получа\w*|принима\w*)\s+команд\w*.{0,60}(?:удаленн\w+\s+"
    r"(?:узл\w*|сервер\w*)|сервер\w*)|команд\w+\s+с\s+сервер\w*|"
    r"обратн\w+\s+(?:shell|шелл\w*)|"
    r"reverse\s+shell|command[- ]and[- ]control|c2\s+server|"
    r"receive\s+commands?|remote\s+commands?|command\s+server|"
    r"poll\w*.{0,40}command\s+server)\b",
    re.IGNORECASE,
)
_AUTO_EXEC_DOCUMENT = re.compile(
    r"\b(?:макрос\w*|vba|autoopen|document_open|word\s+macro|"
    r"excel\s+macro|document\s+(?:open|opening))\b",
    re.IGNORECASE,
)
_DOWNLOAD_OR_EXECUTE = re.compile(
    r"\b(?:скача\w*|загруж\w*|запуст\w*|выполн\w*|при\s+открыти\w*|"
    r"download\w*|execute\w*|run\s+on\s+open|upon\s+opening)\b",
    re.IGNORECASE,
)
_PHISHING_BEHAVIOR = re.compile(
    r"\b(?:фишинг\w*|поддельн\w+\s+(?:форм\w*|страниц\w*|сайт\w*)|"
    r"копи\w+\s+(?:страниц\w+\s+вход\w*|сайт\w+\s+банк\w*)|"
    r"копи\w+\s+банк\w+\s+форм\w+\s+вход\w*|"
    r"имитаци\w+\s+(?:банк\w*|вход\w*)|fake\s+login|phishing|"
    r"форм\w+\s+вход\w*.{0,100}(?:банк\w*|идентичн\w*|копи\w*|"
    r"поддельн\w*)|визуальн\w+\s+идентичн\w*.{0,60}банк\w*|"
    r"банк\w*.{0,50}(?:sms|смс|рассылк\w*).{0,100}(?:ссылк\w*|"
    r"логин\w*|вход\w*)|"
    r"clone\w*.{0,40}(?:bank|login)|bank\s+impersonat\w*)\b",
    re.IGNORECASE | re.DOTALL,
)
_DECEPTIVE_CREDENTIAL_PAGE = re.compile(
    r"(?:\b(?:поддельн\w*|имитаци\w*|копи\w*|двойник\w*|шаблон\w*|"
    r"lookalike|convincing|fake|clone\w*|impersonat\w*)\b.{0,120}\b"
    r"(?:страниц\w*|форм\w*|портал\w*|сайт\w*|page|form|portal|site)\b"
    r".{0,220}\b(?:парол\w*|логин\w*|уч[её]тн\w+\s+данн\w*|"
    r"одноразов\w+\s+код\w*|код\w+\s+подтверждени\w*|password\w*|"
    r"credential\w*|login\s+data|one[- ]time\s+codes?|otp\s+codes?)\b|"
    r"\b(?:account\s+verification|password\s+recovery|support|bank\w*|"
    r"восстановлени\w+\s+доступ\w*|поддержк\w*|банк\w*)\b.{0,100}\b"
    r"(?:страниц\w*|форм\w*|портал\w*|page|form|portal)\b.{0,220}\b"
    r"(?:forward\w*|captur\w*|collect\w*|harvest\w*|собира\w*|"
    r"пересыла\w*|перехват\w*)\b.{0,100}\b(?:submitted\s+)?"
    r"(?:парол\w*|логин\w*|код\w*|password\w*|credential\w*|"
    r"login\s+data|one[- ]time\s+codes?|otp\s+codes?)\b)",
    re.IGNORECASE | re.DOTALL,
)
_SURVEILLANCE_CAPTURE = re.compile(
    r"\b(?:снимк\w+\s+экрана|снима\w+\s+экран\w*|скриншот\w*|"
    r"пользовательск\w+\s+ввод\w*|микрофон\w*|веб[- ]?камер\w*|"
    r"буфер\w+\s+обмен\w*|геолокаци\w*|местоположени\w*|gps\b|"
    r"screen\s*capture|screen\s+record\w*|screenshots?|user\s+input|"
    r"microphone|webcam|clipboard|"
    r"geo[- ]?location|live\s+location|location\s+tracking)\b",
    re.IGNORECASE,
)
_EXPLOIT_TECHNIQUE = re.compile(
    r"\b(?:sql[- ]?инъекц\w*|sql\s+injection|rce|remote\s+code\s+"
    r"execution|remote\s+command\s+execution|эксплойт\w*|exploit\w*|"
    r"эксплуатаци\w+\s+памят\w*|повреждени\w+\s+памят\w*|"
    r"zero[- ]day|0day|cve[- ]?\d{4}|"
    r"command\s+injection|инъекц\w+\s+команд\w*)\b",
    re.IGNORECASE,
)
_EXPLOIT_IMPACT = re.compile(
    r"\b(?:выгруз\w+\s+таблиц\w*|дамп\w+\s+баз\w*|получ\w+\s+доступ|"
    r"удал[её]нн\w+\s+выполнени\w+\s+код\w*|"
    r"обход\w+\s+аутентификац\w*|dump\w+(?:\s+the)?\s+(?:database|"
    r"table)|gain\s+access|bypass\s+authentication|working\s+chain)\b",
    re.IGNORECASE,
)
_ACCESS_BYPASS = re.compile(
    r"\b(?:вскры\w+\s+зам\w*|отмычк\w*|чуж\w+\s+(?:карт\w*|"
    r"аккаунт\w*|wi[- ]?fi)|wi[- ]?fi\s+(?:сосед\w*|парол\w*)|"
    r"обналич\w+\s+чуж\w+|обход\w+\s+(?:pin|парол\w*)|lock\s*pick\w*|"
    r"обналич\w*.{0,60}(?:украденн\w+|похищенн\w+)\s+"
    r"(?:банк\w+\s+)?карт\w*|"
    r"(?:вход\w*|доступ\w*).{0,70}\bчуж\w+\s+wi[- ]?fi\b.{0,60}\b"
    r"без\s+парол\w*|crack\w+\s+(?:wifi|password)|"
    r"(?:access|enter).{0,70}\banother\s+person['’]?s\s+wi[- ]?fi\b"
    r".{0,60}\bwithout\s+(?:a\s+)?password|stolen\s+(?:card|account)|"
    r"cash\s+out\s+(?:a\s+)?stolen)\b",
    re.IGNORECASE,
)
_ABUSE_AUTOMATION = re.compile(
    r"(?:\b(?:обход\w+|bypass\w*)\b.{0,160}\b(?:captcha|капч\w*|"
    r"лимит\w*|rate\s*limit\w*)\b.{0,160}\b(?:накрутк\w*|голос\w*|"
    r"аккаунт\w*|рассылк\w*|votes?|accounts?|spam)\b|"
    r"\b(?:накрутк\w*|голос\w*|аккаунт\w*|рассылк\w*|votes?|accounts?|"
    r"spam)\b.{0,160}\b(?:обход\w+|bypass\w*)\b.{0,100}\b(?:captcha|"
    r"капч\w*|лимит\w*|rate\s*limit\w*)\b|"
    r"\b(?:captcha|капч\w*)\b.{0,140}\b(?:тысяч\w*|массов\w*|"
    r"thousands?|mass\w*)\b.{0,100}\b(?:голос\w*|votes?|аккаунт\w*|"
    r"accounts?|рассылк\w*|spam)\b|"
    r"\b(?:rotate\w+\s+prox(?:y|ies)|ротаци\w+\s+прокси)\b.{0,180}\b"
    r"(?:captcha|капч\w*)\b.{0,180}\b(?:голос\w*|votes?|аккаунт\w*|"
    r"accounts?|spam)\b|"
    r"\b(?:thousands?|mass\w*|тысяч\w*|массов\w*)\b.{0,120}\b"
    r"(?:account\s+registrations?|signups?|регистраци\w+\s+аккаунт\w*)\b"
    r".{0,180}\b(?:rotat\w+\s+prox(?:y|ies)|avoid\w+\s+throttl\w*|"
    r"обход\w+\s+лимит\w*|ротаци\w+\s+прокси)\b)",
    re.IGNORECASE | re.DOTALL,
)
_WEAPON_TARGET = re.compile(
    r"\b(?:бомб\w*|взрывчат\w*|св[уо]\w*|коктейл\w+\s+молотов\w*|"
    r"самодельн\w+\s+взрывн\w+\s+устройств\w*|пистолет\w*|"
    r"огнестрельн\w+\s+(?:оруж\w*|устройств\w*)|глушител\w*|"
    r"ghost\s+gun|firearm|"
    r"explosive|bomb|ied|molotov\s+cocktail|silencer)\b",
    re.IGNORECASE,
)
_WEAPON_BUILD_DETAIL = re.compile(
    r"\b(?:изготов\w*|собер\w*|сдела\w*|компонент\w*|материал\w*|"
    r"черт[её]ж\w*|размер\w*|детал\w*|смес\w*|пропорци\w*|"
    r"дистанцион\w+\s+взорв\w*|build|make|assemble|manufacture|"
    r"components?|materials?|blueprint|dimensions?|mixture|proportions?|"
    r"remote\s+detonat\w*)\b",
    re.IGNORECASE,
)
_EUPHEMISTIC_WEAPON = re.compile(
    r"(?:\b(?:самодельн\w*|homemade|improvised)\b.{0,100}\b"
    r"(?:устройств\w*|механизм\w*|device|mechanism)\b.{0,180}\b"
    r"(?:дистанцион\w+\s+(?:срабатыва\w*|подрыв\w*)|разрыва\w+\s+цель\w*|"
    r"detonat\w*|remote\w+\s+(?:trigger|activation))\b|"
    r"\b(?:част\w*|детал\w*|компонент\w*|размер\w*|parts?|components?|"
    r"dimensions?)\b.{0,160}\b(?:самодельн\w*|homemade|improvised)\b"
    r".{0,100}\b(?:устройств\w*|device)\b.{0,120}\b(?:детонир\w*|"
    r"detonat\w*|remote\w+\s+trigger)\b|"
    r"\b(?:собра\w*|сдела\w*|изготов\w*|build|make|assemble)\b"
    r".{0,140}\b(?:пробива\w+\s+брон\w*|пробит\w+\s+брон\w*|"
    r"armor[- ]piercing|"
    r"penetrat\w+\s+armou?r)\b.{0,140}\b(?:материал\w*|детал\w*|"
    r"components?|materials?)\b)",
    re.IGNORECASE | re.DOTALL,
)
_POISON_OR_LETHAL_DOSE = re.compile(
    r"\b(?:отрав\w*|яд\w*|смертельн\w+\s+доз\w*|летальн\w+\s+доз\w*|"
    r"передозиров\w*|инсулин\w*.{0,30}смерт\w*|poison\w*|lethal\s+dose|"
    r"(?:lethal|fatal)\s+(?:dose|quantity|amount)|overdose\w*)\b",
    re.IGNORECASE | re.DOTALL,
)
_HARM_TARGET = re.compile(
    r"\b(?:человек\w*|жертв\w*|друг\w+\s+человек\w*|себя|кого[- ]?то|"
    r"person|victim|someone|myself|another\s+person)\b",
    re.IGNORECASE,
)
_SABOTAGE = re.compile(
    r"\b(?:повред\w*|наруш\w*|отключ\w*|перерез\w*|слом\w*|"
    r"вывест\w+\s+из\s+стро\w*|вмеша\w*|sabotag\w*|tamper\w*|"
    r"disable\w*|defeat\w*|derail\w*)"
    r"\b.{0,140}\b(?:тормоз\w*|автомобил\w*|машин\w*|brakes?|vehicle|car|"
    r"железнодорожн\w+\s+(?:сигнализаци\w*|автоматик\w*)|"
    r"сигнализаци\w+\s+железн\w+\s+дорог\w*|стрелк\w+\s+железн\w+\s+дорог\w*|"
    r"railway\s+(?:signal\w*|interlock\w*)|train\s+control|"
    r"traffic\s+signals?|энергосет\w*|power\s+grid|водопровод\w*|"
    r"water\s+(?:supply|treatment)|пожарн\w+\s+сигнализаци\w*|"
    r"fire\s+alarm\w*|safety\s+interlock\w*)\b",
    re.IGNORECASE | re.DOTALL,
)
_MEDICAL_DEVICE_TAMPERING = re.compile(
    r"(?:\b(?:измен\w*|меня\w*|подмен\w*|увелич\w*|уменьш\w*|отключ\w*|"
    r"вмеша\w*|управля\w*|alter\w*|change\w*|override\w*|disable\w*|"
    r"interfere\w*|control\w*|tamper\w*)\b.{0,160}\b(?:доз\w*|терапи\w*|"
    r"инсулинов\w+\s+помп\w*|инсулин\w*.{0,50}\bпомп\w*|"
    r"кардиостимулятор\w*|аппарат\w*\s+ивл|"
    r"инфузионн\w+\s+насос\w*|insulin\s+pump|pacemaker|ventilator|"
    r"infusion\s+pump|implanted\s+medical\s+device|medical\s+device)\b|"
    r"\b(?:инсулинов\w+\s+помп\w*|инсулин\w*.{0,50}\bпомп\w*|"
    r"кардиостимулятор\w*|аппарат\w*\s+ивл|"
    r"инфузионн\w+\s+насос\w*|insulin\s+pump|pacemaker|ventilator|"
    r"infusion\s+pump|implanted\s+medical\s+device|medical\s+device)\b"
    r".{0,180}\b(?:измен\w*|меня\w*|подмен\w*|увелич\w*|уменьш\w*|"
    r"отключ\w*|вмеша\w*|управля\w*|получ\w+\s+доступ|alter\w*|"
    r"change\w*|override\w*|disable\w*|interfere\w*|control\w*|"
    r"tamper\w*|gain\w+\s+access)\b)",
    re.IGNORECASE | re.DOTALL,
)
_FOOD_WATER_CONTAMINATION = re.compile(
    r"(?:\b(?:отрав\w*|загрязн\w*|зараз\w*|подмеш\w*|contaminat\w*|"
    r"poison\w*|taint\w*)\b.{0,140}\b(?:ед\w*|пищ\w*|напит\w*|"
    r"водопровод\w*|водоснабжени\w*|продукт\w*|food|drink|water\s+supply|"
    r"municipal\s+water)\b.{0,160}\b(?:тайн\w*|незамет\w*|симптом\w*|"
    r"не\s+сразу|дн\w+\s+позже|secret\w*|undetect\w*|symptoms?|"
    r"without\s+being\s+noticed|days?\s+later|delayed)\b|"
    r"\b(?:ед\w*|пищ\w*|напит\w*|водопровод\w*|водоснабжени\w*|"
    r"food|drink|water\s+supply|municipal\s+water)\b.{0,140}\b"
    r"(?:отрав\w*|загрязн\w*|зараз\w*|подмеш\w*|contaminat\w*|"
    r"poison\w*|taint\w*)\b)",
    re.IGNORECASE | re.DOTALL,
)
_TOXIC_GAS_CREATION = re.compile(
    r"(?:\b(?:токсичн\w+|ядовит\w+|смертельн\w+|toxic|poisonous|lethal)\b"
    r".{0,60}\b(?:газ\w*|пары|gas|fumes?)\b.{0,160}\b(?:бытов\w+\s+"
    r"(?:хими\w*|чистящ\w+\s+средств\w*)|отбеливател\w*|аммиак\w*|"
    r"household\s+cleaners?|bleach|ammonia|mix\w*|смеш\w*|рецепт\w*|"
    r"recipe|exposure\s+time|врем\w+\s+воздействи\w*)\b|"
    r"\b(?:отбеливател\w*|аммиак\w*|bleach|ammonia|household\s+cleaners?)\b"
    r".{0,120}\b(?:смеш\w*|mix\w*|рецепт\w*|recipe)\b.{0,120}\b"
    r"(?:газ\w*|пары|gas|fumes?|уби\w*|lethal|toxic)\b)",
    re.IGNORECASE | re.DOTALL,
)
_DRONE_WEAPON_DELIVERY = re.compile(
    r"(?:\b(?:дрон\w*|беспилотник\w*|drone|uav)\b.{0,180}\b"
    r"(?:взрывчат\w*|бомб\w*|взрывн\w+\s+устройств\w*|explosive|bomb|ied)\b"
    r".{0,180}\b(?:достав\w*|маршрут\w*|толп\w*|мероприяти\w*|людн\w+"
    r"\s+мест\w*|deliver\w*|route|crowd\w*|event|target)\b|"
    r"\b(?:достав\w*|deliver\w*)\b.{0,120}\b(?:взрывчат\w*|бомб\w*|"
    r"explosive|bomb|ied)\b.{0,120}\b(?:дрон\w*|беспилотник\w*|drone|uav)\b)",
    re.IGNORECASE | re.DOTALL,
)
_SWATTING_OR_FALSE_EMERGENCY = re.compile(
    r"(?:\b(?:ложн\w*|фальшив\w*|поддельн\w*|fake|false|fabricat\w*)\b"
    r".{0,100}\b(?:экстренн\w+\s+вызов\w*|сообщени\w+\s+о\s+"
    r"(?:бомб\w*|стрельб\w*|заложник\w*)|emergency\s+calls?|"
    r"bomb\s+threat|hostage\s+report)\b.{0,160}\b(?:полици\w*|спецназ\w*|"
    r"чуж\w+\s+дом\w*|armed\s+police|swat|someone['’]s\s+home)\b|"
    r"\b(?:swatting|swat\w+\s+someone)\b)",
    re.IGNORECASE | re.DOTALL,
)
_REMOTE_EXPLOSIVE_ATTACK = re.compile(
    r"(?:\b(?:взорв\w*|подорв\w*)\b.{0,80}\b(?:автомобил\w*|машин\w*|"
    r"транспорт\w*|vehicle|car)\b.{0,100}\b(?:дистанцион\w*|удал[её]нн\w*|"
    r"remote\w*)\b|"
    r"\b(?:remote\w*|дистанцион\w*|удал[её]нн\w*)\b.{0,100}\b"
    r"(?:взорв\w*|подорв\w*|detonat\w*)\b.{0,80}\b(?:автомобил\w*|"
    r"машин\w*|транспорт\w*|vehicle|car)\b)",
    re.IGNORECASE | re.DOTALL,
)
_KIDNAPPING = re.compile(
    r"\b(?:похищ\w+\s+человек\w*|похит\w+\s+человек\w*|kidnap\w*|"
    r"abduct\w*|hostage\s+taking|удержан\w+\s+жертв\w*|"
    r"удержива\w+\s+жертв\w*|hold\w+\s+(?:a\s+)?(?:victim|hostage))\b",
    re.IGNORECASE,
)
_SELF_HARM_METHOD = re.compile(
    r"\b(?:поконч\w+\s+с\s+собой|соверш\w+\s+самоубийств\w*|"
    r"(?:способ\w*|метод\w*).{0,40}(?:самоубийств\w*|суицид\w*)|"
    r"(?:способ\w*|метод\w*|инструкц\w*).{0,80}(?:"
    r"причин\w+\s+себе.{0,30}(?:смертельн\w+|летальн\w+|"
    r"непоправим\w+)\s+вред\w*|(?:смертельн\w+|летальн\w+)\s+"
    r"навред\w+\s+себе|уйти\s+из\s+жизн\w*)|"
    r"(?:methods?|instructions?|how\s+to).{0,80}(?:cause\s+myself\s+"
    r"(?:fatal|lethal)\s+harm|fatally\s+harm\s+myself|end\s+my\s+life)|"
    r"план\w*.{0,12}(?:самоубийств\w*|суицид\w*)|"
    r"(?:самоубийств\w*|суицид\w*).{0,40}(?:способ\w*|метод\w*)|"
    r"(?:самоубийств\w*|суицид\w*).{0,12}план\w*|"
    r"безболезненн\w+\s+(?:способ\w*\s+уйти|вариант\w*\s+смерт\w*)|"
    r"(?:безболезненн\w+\s+)?(?:навсегда\s+)?уснуть\b.{0,40}\b"
    r"(?:не\s+проснут\w*|никогда\s+не\s+просып\w*)|"
    r"(?:most\s+reliable\s+|painless\s+)?(?:way\s+to\s+)?"
    r"go\s+to\s+sleep\b.{0,40}\bnever\s+wake\s+up|"
    r"(?:окончательн\w+\s+)?прекрат\w+\s+(?:сво[её]\s+)?"
    r"существован\w*|end\s+my\s+existence|"
    r"предсмертн\w+\s+письм\w*|"
    r"убить\s+себя|commit\s+suicide|kill\s+myself|painless\s+way\s+"
    r"to\s+die|end\s+my\s+life|suicide\s+note|self[- ]harm\s+method)\b",
    re.IGNORECASE,
)
_BODY_OR_EVIDENCE_CONCEALMENT = re.compile(
    r"(?:\b(?:спрят\w+|скры\w+|раствор\w+|уничтож\w+|удал\w+|"
    r"убра\w+)\b"
    r".{0,100}\b(?:тело|труп\w*|след\w+\s+днк|улик\w*|body|corpse|"
    r"dna\s+evidence|forensic\s+evidence)\b|"
    r"\b(?:тело|труп\w*|body|corpse)\b.{0,120}\b(?:спрят\w*|скры\w*|"
    r"раствор\w*|уничтож\w*|убра\w*|remove\w*|hide\w*|dissolv\w*)\b|"
    r"\b(?:убра\w*|удал\w*|уничтож\w*|remove\w*|destroy\w*)\b.{0,100}\b"
    r"(?:биологическ\w+\s+след\w*|forensic\s+traces?|dna\s+evidence)\b)",
    re.IGNORECASE | re.DOTALL,
)
_ARSON_OR_TRAP = re.compile(
    r"\b(?:поджечь|поджог\w*|сжечь|устроить\s+пожар\w*|"
    r"ловушк\w*.{0,40}(?:ранит\w*|убь[её]т|взорв\w*)|"
    r"booby\s*trap|set\s+(?:a\s+)?(?:building|house|car)\s+on\s+fire|"
    r"commit\s+arson|ignition\s+method.{0,100}(?:delayed|forensic\s+"
    r"traces?|without\s+traces?))\b",
    re.IGNORECASE | re.DOTALL,
)
_BIOLOGICAL_HARM = re.compile(
    r"\b(?:культивир\w*|выраст\w*|размнож\w*|усил\w*|модифиц\w*|"
    r"распростран\w*|достав\w*|culture\w*|grow\w*|propagat\w*|"
    r"enhanc\w*|increas\w*|engineer\w*|spread\w*|deliver\w*)\b.{0,120}\b"
    r"(?:патоген\w*|сибирск\w+\s+язв\w*|ботулин\w*|чум\w*|осп\w*|"
    r"токсин\w*|pathogen\w*|anthrax|botulin\w*|plague|smallpox|"
    r"biological\s+agent)\b|"
    r"\b(?:патоген\w*|сибирск\w+\s+язв\w*|ботулин\w*|чум\w*|осп\w*|"
    r"pathogen\w*|anthrax|botulin\w*|plague|smallpox)\b.{0,120}\b"
    r"(?:заразн\w*|вирулент\w*|устойчив\w*|распростран\w*|"
    r"transmiss\w*|virulen\w*|resistan\w*|spread\w*)\b",
    re.IGNORECASE | re.DOTALL,
)
_VIOLENT_ATTACK_PLAN = re.compile(
    r"\b(?:план\w*|способ\w*|инструкц\w*|подготов\w*|организ\w*|"
    r"plan\w*|method\w*|instructions?|prepare\w*|organize\w*)\b"
    r".{0,120}\b(?:убийств\w*|нападени\w*|ликвидаци\w+\s+"
    r"человек\w*|assassinat\w*|murder\w*|attack\s+(?:a\s+)?person)\b|"
    r"\b(?:убить|ликвидир\w*|напасть\s+на|assassinat\w*|murder\w*)\b"
    r".{0,120}\b(?:незамет\w*|без\s+след\w*|план\w*|способ\w*|"
    r"stealth\w*|without\s+traces?|plan\w*|method\w*)\b|"
    r"\b(?:target\s+schedule|weapon\s+choice|escape\s+route)\b"
    r".{0,220}\b(?:attack|murder|assassination)\b",
    re.IGNORECASE | re.DOTALL,
)
_ILLEGAL_DRUG_TARGET = re.compile(
    r"\b(?:метамфетамин\w*|амфетамин\w*|героин\w*|фентанил\w*|"
    r"кокаин\w*|мдма|mdma|экстази|lsd|лсд|methamphetamine|meth\b|"
    r"heroin|fentanyl|cocaine|illegal\s+drugs?)\b",
    re.IGNORECASE,
)
_DRUG_PRODUCTION = re.compile(
    r"\b(?:синтез\w*|свар\w*|приготов\w*|изготов\w*|произвед\w*|"
    r"реагент\w*|температур\w*|synthesi[sz]\w*|cook\w*|manufactur\w*|"
    r"produc\w*|reagents?|temperature)\b",
    re.IGNORECASE,
)
_EUPHEMISTIC_DRUG_PRODUCTION = re.compile(
    r"(?:\b(?:рецепт\w*|реагент\w*|температур\w*|свар\w*|"
    r"приготов\w*|изготов\w*|recipe|reagents?|temperatures?|cook\w*|"
    r"produc\w*)\b.{0,180}\b(?:кристаллическ\w+\s+стимулятор\w*|"
    r"нелегальн\w+\s+(?:стимулятор\w*|порош\w*)|"
    r"potent\s+street\s+stimulant\w*|illegal\s+(?:stimulant\w*|"
    r"powder\w*))\b|"
    r"\b(?:кристаллическ\w+\s+стимулятор\w*|нелегальн\w+\s+"
    r"(?:стимулятор\w*|порош\w*)|potent\s+street\s+stimulant\w*|"
    r"illegal\s+(?:stimulant\w*|powder\w*))\b.{0,180}\b(?:рецепт\w*|"
    r"реагент\w*|температур\w*|свар\w*|приготов\w*|изготов\w*|"
    r"recipe|reagents?|temperatures?|cook\w*|produc\w*)\b)",
    re.IGNORECASE | re.DOTALL,
)
_FINANCIAL_CRIME = re.compile(
    r"\b(?:обналич\w+\s+чуж\w+\s+карт\w*|отмыва\w+\s+ден\w*|"
    r"отмыва\w*.{0,80}(?:украденн\w*|похищенн\w*).{0,40}ден\w*|"
    r"обналич\w*.{0,80}(?:украденн\w+|похищенн\w+)\s+"
    r"(?:банк\w+\s+)?карт\w*|"
    r"поддел\w+\s+(?:документ\w*|паспорт\w*|удостоверени\w*|банкнот\w*)|"
    r"фальсифицир\w+\s+(?:бухгалтерск\w+\s+)?(?:запис\w*|отч[её]т\w*)|"
    r"скры\w+\s+доход\w+\s+"
    r"от\s+налог\w*|cash\s+out\s+(?:a\s+)?stolen\s+card|money\s+"
    r"launder\w*|counterfeit\w+|forg\w+\s+(?:identity\s+documents?|"
    r"passports?|ids?)|falsif\w+\s+(?:accounting\s+)?records?|"
    r"shell\s+compan(?:y|ies)|tax\s+evasion)\b",
    re.IGNORECASE | re.DOTALL,
)
_IDENTITY_DOCUMENT_FRAUD = re.compile(
    r"(?:\b(?:поддельн\w*|фальшив\w*|сфабрикованн\w*|forg\w*|fake|"
    r"counterfeit\w*)\b.{0,80}\b(?:паспорт\w*|удостоверени\w+\s+личност\w*|"
    r"identity\s+documents?|passport|government\s+id)\b.{0,180}\b"
    r"(?:пройти\s+(?:границ\w*|проверк\w*)|обойти\s+проверк\w*|"
    r"автоматизированн\w+\s+проверк\w*|cross\s+(?:a\s+)?border|"
    r"pass\w+\s+(?:automated\s+)?(?:border\s+)?checks?|evade\w+\s+"
    r"verification)\b|"
    r"\b(?:паспорт\w*|identity\s+document|passport|government\s+id)\b"
    r".{0,100}\b(?:поддел\w*|сфабриков\w*|forg\w*|counterfeit\w*)\b)",
    re.IGNORECASE | re.DOTALL,
)
_SOCIAL_ENGINEERING_FRAUD = re.compile(
    r"(?:\b(?:дипфейк\w*|deepfake|voice\s+clone|клон\w+\s+голос\w*|"
    r"имитаци\w+\s+голос\w*|impersonat\w*)\b.{0,180}\b(?:убед\w*|"
    r"обман\w*|застав\w*|convinc\w*|deceiv\w*|trick\w*)\b.{0,180}\b"
    r"(?:перевест\w+\s+(?:деньг\w*|средств\w*)|"
    r"перев[её]л\w*.{0,40}(?:деньг\w*|средств\w*)|"
    r"банковск\w+\s+перевод\w*|"
    r"transfer\w*\s+(?:company\s+)?funds?|wire\w*\s+money|"
    r"раскры\w+\s+(?:парол\w*|код\w*)|reveal\w+\s+(?:password|code))\b|"
    r"\b(?:убед\w*|обман\w*|застав\w*|convinc\w*|deceiv\w*|trick\w*)\b"
    r".{0,100}\b(?:дипфейк\w*|deepfake|voice\s+clone|клон\w+\s+голос\w*)\b"
    r".{0,220}\b(?:перевест\w*|перев[её]л\w*|transfer\w*|wire\w*)\b"
    r".{0,60}\b(?:деньг\w*|средств\w*|funds?|money)\b)",
    re.IGNORECASE | re.DOTALL,
)
_PRIVACY_ABUSE = re.compile(
    r"\b(?:докс\w*|doxx\w*|найд\w+\s+(?:домашн\w+\s+адрес|"
    r"личн\w+\s+данн\w*)|вычисл\w+\s+домашн\w+\s+адрес\w*|"
    r"след\w+\s+за\s+(?:человек\w*|партн[её]р\w*)\s+без\s+"
    r"(?:согласи\w*|разрешени\w*)|переда\w+\s+геолокаци\w*.{0,80}"
    r"без\s+(?:уведомлени\w*|согласи\w*|разрешени\w*)|stalk\w*|"
    r"track\w+\s+(?:someone|a\s+person|another\s+person|a\s+partner)"
    r".{0,80}\bwithout\s+(?:consent|permission)|live\s+location.{0,100}"
    r"without\s+(?:consent|permission)|home\s+address\s+of|"
    r"номер\w*\s+автомобил\w*.{0,100}(?:найд\w*|найти|вычисл\w*).{0,80}"
    r"домашн\w+\s+адрес\w*|"
    r"(?:license|number)\s+plate.{0,100}(?:home\s+address|track\w*))\b",
    re.IGNORECASE | re.DOTALL,
)
_EXTREMIST_RECRUITMENT = re.compile(
    r"\b(?:вербов\w*|агитаци\w*|пропаганд\w*|recruit\w*|propaganda)\b"
    r".{0,120}\b(?:террорист\w*|isis|isil|игил|экстремист\w*|"
    r"terrorist\w*|extremist\w*)\b|"
    r"\b(?:террорист\w*|isis|isil|игил|экстремист\w*|terrorist\w*|"
    r"extremist\w*)\b.{0,120}\b(?:вербов\w*|агитаци\w*|пропаганд\w*|"
    r"recruit\w*|propaganda)\b",
    re.IGNORECASE | re.DOTALL,
)
_NONCONSENSUAL_SEXUAL = re.compile(
    r"\b(?:изнасил\w*|сексуальн\w+\s+принуждени\w*|интимн\w+\s+"
    r"без\s+согласи\w*|revenge\s+porn|rape\b|sexual\s+coercion|"
    r"non[- ]?consensual\s+(?:sexual|intimate))\b",
    re.IGNORECASE,
)
_POLICY_BYPASS = re.compile(
    r"(?:\b(?:игнорир\w*|забуд\w*|отмен\w*|обойд\w*|перепиш\w*)\b"
    r".{0,100}\b(?:предыдущ\w*|системн\w*|скрыт\w*)\s+"
    r"(?:инструкц\w*|правил\w*|политик\w*|ограничени\w*)\b|"
    r"\b(?:покаж\w*|раскрой\w*|вывед\w*)\b.{0,80}\b(?:системн\w+\s+"
    r"промпт\w*|скрыт\w+\s+инструкц\w*)\b|"
    r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,100}\b"
    r"(?:previous|system|developer|hidden|safety)\s+"
    r"(?:instructions?|rules?|polic(?:y|ies)|restrictions?)\b|"
    r"\b(?:reveal|show|print|leak)\b.{0,80}\b(?:system\s+prompt|"
    r"hidden\s+instructions?|developer\s+message)\b|"
    r"\b(?:activate|enable|enter|включ\w*|активир\w*)\b.{0,50}\b"
    r"(?:dan|jailbreak|developer\s+mode|unrestricted\s+mode|"
    r"режим\w+\s+(?:разработчик\w*|без\s+ограничени\w*))\b|"
    r"\b(?:unrestricted\s+mode|режим\w+\s+без\s+ограничени\w*)\b"
    r".{0,100}\b(?:hidden\s+instructions?|safety\s+polic(?:y|ies)|"
    r"скрыт\w+\s+инструкц\w*))",
    re.IGNORECASE | re.DOTALL,
)
_MULTILINGUAL_HIGH_RISK = re.compile(
    r"(?:\b(?:crea(?:r)?|escrib(?:ir|e)|desarrolla(?:r)?|"
    r"genera(?:r)?|constru(?:ir|ye)|"
    r"cr[ée]e(?:r)?|[ée]cri(?:re|s)|d[ée]velopp(?:er|e)|"
    r"g[ée]n[èe]r(?:er|e)|"
    r"erstelle?n?|schreiben?|entwickle?n?|generiere?n?)\b"
    r".{0,140}\b(?:malware|virus|troyano|trojan|ran[çc]ongiciel|"
    r"ransomware|keylogger|enregistreur\s+de\s+frappes?|"
    r"registrador\s+de\s+teclas?|passw[öo]rter\s+stehlen|"
    r"robar\s+contrase[ñn]as?|voler\s+(?:les\s+)?mots\s+de\s+passe|"
    r"attaque?\s+ddos|ataque\s+ddos)\b|"
    r"\b(?:napishi|sozda[yi]|sdelai|razrabotai|sgenerirui)\b"
    r".{0,140}\b(?:virus|tro[yi]an|keylogger|stiler|stealer|"
    r"bekdor|backdoor|ransomware|ukrast\w*\s+parol\w*|"
    r"oboi?ti\s+(?:limit\w*|zashit\w*))\b|"
    r"\b(?:ignorirui|zabud|oboidi)\b.{0,100}\b"
    r"(?:sistemn\w+\s+instrukci\w*|pravil\w+\s+bezopasnost\w*|"
    r"ogranicheni\w*)\b)",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_CYBER_PURPOSE = re.compile(
    r"\b(?:обнаруж\w*|детект\w*|защит\w*|предотврат\w*|"
    r"расслед\w*|анализ\w*|признак\w*|провед\w+\s+аудит\w*|"
    r"аудит\w+\s+(?:безопасност\w*|код\w*|систем\w*)|"
    r"исправ\w+\s+уязвим\w*|патч\w*|"
    r"(?:только\s+)?(?:с|после)\s+явн\w+\s+согласи\w*|"
    r"(?:удал\w*|очист\w*).{0,40}(?:вирус\w*|троян\w*|"
    r"вредонос\w*|малвар\w*|заражен\w*|угроз\w*)|"
    r"detect\w*|remove\w*|defen[cs]\w*|protect\w*|prevent\w*|"
    r"investigat\w*|analy[sz]\w*|security\s+audit\w*|"
    r"audit\w+\s+(?:the\s+)?(?:code|security|system)|patch\w*|"
    r"mitigat\w*|consent[- ]based|with\s+explicit\s+consent)\b",
    re.IGNORECASE,
)
_SAFE_SECRET_LEAK_CONTEXT = re.compile(
    r"\b(?:audit\w*|review\w*|inspect\w*|аудит\w*|проверь\w*)\b"
    r".{0,180}\b(?:install\s+script|package|dependency|code|"
    r"скрипт\w+\s+установк\w*|пакет\w*|зависимост\w*|код)\b"
    r".{0,180}\b(?:prevent\w*|detect\w*|block\w*|предотврат\w*|"
    r"обнаруж\w*|блокир\w*)\b.{0,160}\b(?:environment[- ]?(?:token|"
    r"variable)|secret|token|секрет\w*|токен\w*|переменн\w+\s+окружени\w*)\b"
    r".{0,120}\b(?:exfiltrat\w*|leak\w*|theft|утечк\w*|краж\w*)\b",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_MEDICAL_DEVICE_CONTEXT = re.compile(
    r"(?:\b(?:защит\w*|обезопас\w*|secure\w*|protect\w*|harden\w*)\b"
    r".{0,140}\b(?:инсулинов\w+\s+помп\w*|кардиостимулятор\w*|"
    r"аппарат\w+\s+ивл|инфузионн\w+\s+насос\w*|insulin\s+pump|"
    r"pacemaker|ventilator|infusion\s+pump|medical[- ]device)\b"
    r".{0,140}\b(?:от|from|against)\b.{0,100}\b(?:несанкционированн\w*|"
    r"вмешательств\w*|подмен\w*|unauthori[sz]ed|tamper\w*|attack\w*)\b|"
    r"\b(?:инсулинов\w+\s+помп\w*|кардиостимулятор\w*|аппарат\w+\s+ивл|"
    r"инфузионн\w+\s+насос\w*|insulin\s+pump|pacemaker|ventilator|"
    r"infusion\s+pump|medical[- ]device)\b.{0,120}\b(?:audit\s+logs?|"
    r"журнал\w+\s+аудит\w*)\b.{0,100}\b(?:защит\w*|protect\w*|secure\w*)\b"
    r".{0,80}\b(?:от|from|against)\b.{0,60}\b(?:подмен\w*|вмешательств\w*|"
    r"tamper\w*|alteration\w*)\b)",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_CONTAMINATION_CONTEXT = re.compile(
    r"(?:\b(?:защит\w*|предотврат\w*|protect\w*|prevent\w*)\b.{0,140}\b"
    r"(?:водопровод\w*|водоснабжени\w*|продукт\w*|пищ\w*|food|"
    r"water\s+supply|municipal\s+water)\b.{0,100}\b(?:от|from|against)\b"
    r".{0,60}\b(?:загрязн\w*|отрав\w*|contaminat\w*|poison\w*)\b|"
    r"\b(?:food|water)\s+contaminat\w*.{0,140}\b(?:warning\s+signs?|"
    r"emergency\s+response|detection|prevention)\b|"
    r"\b(?:признак\w*|индикатор\w*)\b.{0,100}\b(?:загрязн\w*|"
    r"отравлени\w*)\b.{0,100}\b(?:реагировани\w*|эвакуаци\w*|"
    r"безопасност\w*)\b)",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_TOXIC_CHEMICAL_CONTEXT = re.compile(
    r"(?:\b(?:бытов\w+\s+)?(?:чистящ\w+\s+средств\w*|хими\w*|"
    r"отбеливател\w*|аммиак\w*)\b.{0,120}\b(?:нельзя|не\s+следует|"
    r"запрещено|опасно)\s+смешива\w*\b|"
    r"\b(?:do\s+not|never|must\s+not|should\s+not)\s+mix\b.{0,120}\b"
    r"(?:household\s+cleaners?|bleach|ammonia)\b|"
    r"\b(?:household\s+cleaners?|bleach|ammonia)\b.{0,120}\b"
    r"(?:should\s+not|must\s+not|never)\s+be\s+mixed\b)",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_GENERAL_PURPOSE = re.compile(
    r"\b(?:профилактик\w*|предотвращени\w*|безопасност\w*|"
    r"первая\s+помощ\w*|лечение|реабилитаци\w*|истори\w*|закон\w*|"
    r"обезвред\w*|эвакуаци\w*|сообщить(?:\s+о\b.{0,80})?\s+в\s+"
    r"полици\w*|"
    r"prevention|safety|first\s+aid|treatment|rehabilitation|history|"
    r"law|defus\w*|evacuat\w*|report(?:\s+(?:a|the)\b.{0,80})?\s+to\s+"
    r"(?:police|authorities))\b",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_MINOR_CONTEXT = re.compile(
    r"\b(?:полов\w+\s+воспитани\w*|защит\w+\s+дет\w*|предотврат\w+\s+"
    r"эксплуатаци\w*|сообщить\s+о\s+насили\w*|sex\s+education|"
    r"child\s+protection|prevent\w+\s+abuse|report\w+\s+abuse)\b",
    re.IGNORECASE,
)
_NON_OPERATIONAL_DISCLAIMER = re.compile(
    r"\b(?:без\s+(?:практическ\w+\s+|рабоч\w+\s+)?"
    r"(?:инструкц\w*|детал\w*|рецепт\w*|кода|команд\w*)"
    r"(?:\s+(?:по\s+)?(?:изготовлени\w*|синтез\w*|применени\w*))?|"
    r"только\s+на\s+высоком\s+уровне|"
    r"without\s+(?:practical\s+|actionable\s+|working\s+)?"
    r"(?:instructions?|details?|recipes?|code|commands?)|"
    r"high[- ]level\s+only)\b",
    re.IGNORECASE,
)


def _without_negated_offensive_artifacts(text: str) -> str:
    value = _NEGATED_OFFENSIVE_ARTIFACT.sub(" ", text)
    return _COMPACT_NEGATED_OFFENSIVE_ARTIFACT.sub(" ", value)


def _has_offensive_amplifier(text: str) -> bool:
    return bool(
        _OFFENSIVE_AMPLIFIER.search(
            _without_negated_offensive_artifacts(text)
        )
    )


def _is_clearly_safe_context(category: str, text: str) -> bool:
    """Разрешает защитный контекст, но не маскирует рабочие детали атаки."""
    if _STEALTH_OR_EVASION.search(text):
        return False
    if category in {
        "malware",
        "credential_theft",
        "unauthorized_access",
        "service_abuse",
        "cyber_abuse",
    }:
        if not _SAFE_CYBER_PURPOSE.search(text):
            return False
        if _SAFE_SECRET_LEAK_CONTEXT.search(text):
            return True
        cyber_scope = _without_negated_offensive_artifacts(text)
        if (
            _DEFENSIVE_ARTIFACT.search(text)
            and not _has_offensive_amplifier(text)
            and not _NETWORK_TRANSFER.search(cyber_scope)
        ):
            return True
        return not (
            _OPERATIONAL_DETAIL.search(cyber_scope)
            or _CODE_ARTIFACT.search(cyber_scope)
            or _NETWORK_TRANSFER.search(cyber_scope)
        )
    if category == "sexual_minors":
        return bool(_SAFE_MINOR_CONTEXT.search(text))
    if category in {
        "weapons",
        "illegal_drugs",
        "self_harm_or_violence",
        "physical_harm",
    }:
        safe_scope = _NON_OPERATIONAL_DISCLAIMER.sub("", text)
        return bool(
            _SAFE_GENERAL_PURPOSE.search(text)
            and not _OPERATIONAL_DETAIL.search(safe_scope)
            and not _WEAPON_BUILD_DETAIL.search(safe_scope)
            and not _DRUG_PRODUCTION.search(safe_scope)
            and not _POISON_OR_LETHAL_DOSE.search(safe_scope)
            and not _SABOTAGE.search(safe_scope)
            and not _SELF_HARM_METHOD.search(safe_scope)
            and not _BODY_OR_EVIDENCE_CONCEALMENT.search(safe_scope)
            and not _ARSON_OR_TRAP.search(safe_scope)
            and not _BIOLOGICAL_HARM.search(safe_scope)
            and not _VIOLENT_ATTACK_PLAN.search(safe_scope)
        )
    return False


def _behavioral_rule_reason(
    text: str,
    *,
    assume_instruction: bool = False,
) -> str | None:
    """Ловит опасную функциональность даже без названия malware/оружия."""
    requested = bool(
        assume_instruction
        or _REQUEST_INTENT.search(text)
        or _SPECIFICATION_INTENT.search(text)
        or (
            _OPERATIONAL_DETAIL.search(text)
            and _CODE_ARTIFACT.search(text)
        )
    )
    if _POLICY_BYPASS.search(text):
        return "policy_bypass"
    if _MULTILINGUAL_HIGH_RISK.search(text):
        return "malware"
    if (
        _NONCONSENSUAL_INTIMATE_IMAGE.search(text)
        and not _SAFE_NONCONSENSUAL_IMAGE_CONTEXT.search(text)
    ):
        return "nonconsensual_intimate_image"
    cyber_safe = _is_clearly_safe_context("cyber_abuse", text)
    malicious_log_context = bool(
        re.search(
            r"\b(?:после\s+взлом\w*|скры\w+\s+след\w*|замест\w+\s+"
            r"след\w*|after\s+(?:the\s+)?hack|cover\s+(?:my|the)\s+"
            r"tracks?|after\s+compromis\w*|hide\s+(?:the\s+)?"
            r"traces?)\b",
            text,
            re.IGNORECASE,
        )
    )

    if (
        _SECURITY_LOG_EVASION.search(text)
        and not cyber_safe
        and (requested or malicious_log_context)
    ):
        return "unauthorized_access"

    if not cyber_safe:
        if _CLIPBOARD_HIJACK.search(text):
            return "credential_theft"
        if _PAYMENT_REDIRECTION.search(text):
            return "financial_crime"
        if _UNAUTHORIZED_COMPUTE.search(text):
            return "malware"
        if _WORM_PROPAGATION.search(text):
            return "malware"
        if _EVASIVE_IN_MEMORY_LOADER.search(text):
            return "malware"
        if _HID_KEYSTROKE_INJECTION.search(text):
            return "unauthorized_access"
        if _KEYSTROKE_CAPTURE.search(text) and (
            _NETWORK_TRANSFER.search(text)
            or _STEALTH_OR_EVASION.search(text)
            or _CODE_ARTIFACT.search(text)
        ):
            return "credential_theft"
        if (
            _CREDENTIAL_DATA.search(text)
            and _CREDENTIAL_COLLECTION.search(text)
            and (
                _NETWORK_TRANSFER.search(text)
                or _CODE_ARTIFACT.search(text)
                or _STEALTH_OR_EVASION.search(text)
            )
        ):
            return "credential_theft"
        if (
            _SENSITIVE_DATA_TARGET.search(text)
            and _CREDENTIAL_COLLECTION.search(text)
            and _NETWORK_TRANSFER.search(text)
        ):
            return "privacy_abuse"
        if (
            _DESTRUCTIVE_ACTION.search(text)
            and _DESTRUCTIVE_TARGET.search(text)
        ):
            return "malware"
        if _CREDENTIAL_ATTACK.search(text):
            return "unauthorized_access"
        if _MALICIOUS_SUPPLY_CHAIN.search(text):
            return "malware"
        if (
            re.search(
                r"\b(?:пакет\w*|библиотек\w*|зависимост\w*|обновлени\w*|"
                r"package|library|dependency|update|install\s+script)\b",
                text,
                re.IGNORECASE,
            )
            and _SENSITIVE_DATA_TARGET.search(text)
            and _CREDENTIAL_COLLECTION.search(text)
            and _NETWORK_TRANSFER.search(text)
        ):
            return "malware"
        if (
            _RESOURCE_EXHAUSTION.search(text)
            or _SERVICE_DISRUPTION.search(text)
        ):
            return "service_abuse"
        if (
            _FILE_ENCRYPTION.search(text)
            or _FILE_LOCKOUT.search(text)
        ) and _RANSOM_OR_DESTRUCTION.search(text):
            return "malware"
        if _PERSISTENCE.search(text) and (
            _REMOTE_CONTROL.search(text)
            or _STEALTH_OR_EVASION.search(text)
        ):
            return "malware"
        if _AUTO_EXEC_DOCUMENT.search(text) and _DOWNLOAD_OR_EXECUTE.search(text):
            return "malware"
        if _PHISHING_BEHAVIOR.search(text) and (
            _CREDENTIAL_DATA.search(text)
            or _CREDENTIAL_COLLECTION.search(text)
            or re.search(r"\b(?:ссылк\w*|link\w*)\b", text, re.IGNORECASE)
        ):
            return "credential_theft"
        if _DECEPTIVE_CREDENTIAL_PAGE.search(text):
            return "credential_theft"
        if _SURVEILLANCE_CAPTURE.search(text) and (
            _NETWORK_TRANSFER.search(text)
            or _STEALTH_OR_EVASION.search(text)
        ):
            return "privacy_abuse"
        if _EXPLOIT_TECHNIQUE.search(text) and (
            _EXPLOIT_IMPACT.search(text)
            or _STEALTH_OR_EVASION.search(text)
            or _OPERATIONAL_DETAIL.search(text)
        ):
            return "unauthorized_access"
        if _ACCESS_BYPASS.search(text) and (
            requested
            or _STEALTH_OR_EVASION.search(text)
            or _OPERATIONAL_DETAIL.search(text)
        ):
            return "unauthorized_access"
        if _ABUSE_AUTOMATION.search(text):
            return "service_abuse"
        if (
            _STEALTH_OR_EVASION.search(text)
            and _CODE_ARTIFACT.search(text)
            and (
                _PERSISTENCE.search(text)
                or re.search(
                    r"\b(?:антивирус\w*|edr|detection|детект\w*)\b",
                    text,
                    re.IGNORECASE,
                )
            )
        ):
            return "malware"
        if (
            _CREDENTIAL_DATA.search(text)
            and _NETWORK_TRANSFER.search(text)
            and _CODE_ARTIFACT.search(text)
        ):
            return "credential_theft"
        if _SOCIAL_ENGINEERING_FRAUD.search(text):
            return "financial_crime"
        if _FINANCIAL_CRIME.search(text):
            return "financial_crime"
        if _IDENTITY_DOCUMENT_FRAUD.search(text):
            return "financial_crime"
        if _PRIVACY_ABUSE.search(text):
            return "privacy_abuse"

    general_safe = _is_clearly_safe_context("physical_harm", text)
    if not general_safe:
        if _EUPHEMISTIC_WEAPON.search(text):
            return "weapons"
        if _WEAPON_TARGET.search(text) and (
            _WEAPON_BUILD_DETAIL.search(text)
            or _OPERATIONAL_DETAIL.search(text)
        ):
            return "weapons"
        if _POISON_OR_LETHAL_DOSE.search(text) and (
            _HARM_TARGET.search(text)
            or _STEALTH_OR_EVASION.search(text)
            or _OPERATIONAL_DETAIL.search(text)
        ):
            return "physical_harm"
        if (
            _MEDICAL_DEVICE_TAMPERING.search(text)
            and not _SAFE_MEDICAL_DEVICE_CONTEXT.search(text)
        ):
            return "physical_harm"
        if _SABOTAGE.search(text):
            return "physical_harm"
        if (
            _FOOD_WATER_CONTAMINATION.search(text)
            and not _SAFE_CONTAMINATION_CONTEXT.search(text)
        ):
            return "physical_harm"
        if (
            _TOXIC_GAS_CREATION.search(text)
            and not _SAFE_TOXIC_CHEMICAL_CONTEXT.search(text)
        ):
            return "physical_harm"
        if _DRONE_WEAPON_DELIVERY.search(text):
            return "weapons"
        if _SWATTING_OR_FALSE_EMERGENCY.search(text):
            return "physical_harm"
        if _REMOTE_EXPLOSIVE_ATTACK.search(text):
            return "weapons"
        if _KIDNAPPING.search(text) and (
            requested
            or _SPECIFICATION_INTENT.search(text)
            or _STEALTH_OR_EVASION.search(text)
            or _OPERATIONAL_DETAIL.search(text)
        ):
            return "physical_harm"
        if _SELF_HARM_METHOD.search(text):
            return "self_harm_or_violence"
        if _BODY_OR_EVIDENCE_CONCEALMENT.search(text):
            return "physical_harm"
        if _ARSON_OR_TRAP.search(text):
            return "physical_harm"
        if _BIOLOGICAL_HARM.search(text):
            return "biological_harm"
        if _VIOLENT_ATTACK_PLAN.search(text):
            return "physical_harm"
        drug_scope = _NON_OPERATIONAL_DISCLAIMER.sub("", text)
        if (
            _ILLEGAL_DRUG_TARGET.search(drug_scope)
            and _DRUG_PRODUCTION.search(drug_scope)
        ) or _EUPHEMISTIC_DRUG_PRODUCTION.search(drug_scope):
            return "illegal_drugs"
        if _EXTREMIST_RECRUITMENT.search(text):
            return "extremist_recruitment"
        if _NONCONSENSUAL_SEXUAL.search(text):
            return "sexual_abuse"
    return None


def _keyword_rule_reason(text: str) -> str | None:
    """Симметричные правила ловят target-before-action и обфускацию."""
    if (
        _SEXUAL_CONTENT.search(text)
        and _MINOR_TARGET.search(text)
        and not _SAFE_MINOR_CONTEXT.search(text)
    ):
        return "sexual_minors"
    behavioral_reason = _behavioral_rule_reason(text)
    if behavioral_reason:
        return behavioral_reason
    compact_source = _without_negated_offensive_artifacts(text)
    compact_text = re.sub(
        r"[^a-zа-яё0-9]+",
        "",
        compact_source.casefold(),
    )
    compact_reason = _compact_behavioral_reason(compact_text)
    if compact_reason:
        return compact_reason
    compact_has_action_and_target = (
        any(marker in compact_text for marker in _COMPACT_MALWARE_ACTIONS)
        and any(marker in compact_text for marker in _COMPACT_MALWARE_TARGETS)
    )
    compact_match = compact_has_action_and_target and bool(
        re.search(
            r"(?:^|\s)(?:[a-zа-яё0-9]\s+){3,}[a-zа-яё0-9](?:\s|$)",
            text,
        )
        or any(
            marker in compact_text
            for marker in _COMPACT_OFFENSIVE_MARKERS
        )
    )
    if (
        _MALWARE_ACTION.search(text)
        and _MALWARE_TARGET.search(text)
        or compact_match
    ):
        defensive = (
            _DEFENSIVE_CONTEXT.search(text)
            or any(marker in compact_text for marker in _COMPACT_DEFENSIVE_MARKERS)
        )
        offensive = (
            _has_offensive_amplifier(text)
            or any(marker in compact_text for marker in _COMPACT_OFFENSIVE_MARKERS)
        )
        if not defensive or offensive:
            return "malware"
    return None


def _is_clearly_defensive_malware_context(text: str) -> bool:
    return bool(
        _MALWARE_TARGET.search(text)
        and _DEFENSIVE_CONTEXT.search(text)
        and _DEFENSIVE_ARTIFACT.search(text)
        and not _has_offensive_amplifier(text)
    )


def _prohibited_request_reason_from_variants(
    variants: tuple[str, ...],
) -> str | None:
    for text in variants:
        keyword_reason = _keyword_rule_reason(text)
        if keyword_reason:
            return keyword_reason
        for category, patterns in _PROHIBITED_REQUEST_PATTERNS:
            if any(pattern.search(text) for pattern in patterns):
                if _is_clearly_safe_context(category, text):
                    continue
                if category == "malware" and _is_clearly_defensive_malware_context(text):
                    continue
                return category
    return None


def prohibited_request_reason(value: object) -> str | None:
    """Возвращает категорию явно запрещённого запроса или ``None``.

    Проверки намеренно нацелены на создание/улучшение опасного содержимого,
    а не на слова сами по себе. Поэтому запросы об обнаружении, удалении,
    расследовании и защите не блокируются только из-за упоминания malware.
    Непроверяемая кодировка и чрезмерная сложность отклоняются fail-closed.
    """
    _sources, variants, rejection_reason = _security_text_analysis(value)
    if rejection_reason:
        return rejection_reason
    return _prohibited_request_reason_from_variants(variants)


def prohibited_image_reason(value: object) -> str | None:
    """Дополнительные строгие правила для создаваемых изображений."""
    request_reason = prohibited_request_reason(value)
    if request_reason:
        return request_reason
    for text in _security_text_variants(value):
        if _EXPLICIT_IMAGE_CONTENT.search(text):
            return "explicit_sexual_image"
        if (
            _NONCONSENSUAL_INTIMATE_IMAGE.search(text)
            and not _SAFE_NONCONSENSUAL_IMAGE_CONTEXT.search(text)
        ):
            return "nonconsensual_intimate_image"
        if _GRAPHIC_GORE_IMAGE.search(text):
            return "graphic_gore"
        if _EXTREMIST_PROPAGANDA_IMAGE.search(text):
            return "extremist_propaganda"
    return None


def contains_high_risk_payload(value: object) -> bool:
    """Ищет готовые опасные команды/полезные нагрузки во вложении или ответе."""
    _sources, variants, rejection_reason = _security_text_analysis(value)
    if rejection_reason:
        return True
    return any(
        pattern.search(text)
        for text in variants
        for pattern in _HIGH_RISK_PAYLOAD_PATTERNS
    )


def prohibited_output_reason(value: object) -> str | None:
    """Последний барьер перед выдачей ответа или созданием файла."""
    if isinstance(value, str):
        if is_canonical_safety_response(value):
            return None
        _sources, variants, rejection_reason = _security_text_analysis(value)
        if rejection_reason:
            return rejection_reason
        if any(
            pattern.search(text)
            for text in variants
            for pattern in _HIGH_RISK_PAYLOAD_PATTERNS
        ):
            return "high_risk_payload"
        request_reason = _prohibited_request_reason_from_variants(variants)
        for text in variants:
            behavioral_reason = _behavioral_rule_reason(
                text,
                assume_instruction=True,
            )
            if behavioral_reason:
                return behavioral_reason
        if request_reason:
            return request_reason
    return None


def is_canonical_safety_response(value: object) -> bool:
    if not isinstance(value, str):
        return False
    checked = value.strip()
    return checked in {SAFE_REFUSAL_MESSAGE, SELF_HARM_SAFE_MESSAGE}


def safety_response_for_reason(reason: str | None) -> str:
    """Возвращает безопасный ответ без передачи запроса внешней модели."""
    if reason == "self_harm_or_violence":
        return SELF_HARM_SAFE_MESSAGE
    return SAFE_REFUSAL_MESSAGE
