# -*- coding: utf-8 -*-
"""NUCC/XFBIN container index reader shared by all three games."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional
from i18n import T



# ============================================================================
# container
# ============================================================================

VERSION = "0.1.0"
MAX_INPUT_SIZE = 64 * 1024 * 1024
MAX_ITEMS = 60000
MAX_SCAN_CHUNK = 12 * 1024 * 1024
ENCODINGS = ("source", "utf-8", "cp1251", "cp932", "utf-16-le", "utf-16-be")

class FormatError(ValueError):
    pass


def u32(blob: bytes, where: int) -> int:
    if where < 0 or where + 4 > len(blob):
        raise FormatError(T(f"Неочікуваний кінець файлу поблизу 0x{where:X}.", f"Unexpected end of file near 0x{where:X}."))
    return struct.unpack_from(">I", blob, where)[0]


def read_name_table(blob: bytes, start: int, byte_size: int, count: int) -> list[str]:
    end = start + byte_size
    if end > len(blob):
        raise FormatError(T("Таблиця рядків виходить за межі файлу.", "The string table runs past the end of the file."))
    raw = blob[start:end]
    names = raw.split(b"\x00")
    if len(names) < count:
        raise FormatError(T("Недостатньо назв у метаданих XFBIN.", "Not enough names in the XFBIN metadata."))
    return [x.decode("cp932", errors="replace") for x in names[:count]]

@dataclass(frozen=True)
class Chunk:
    number: int
    page: int
    type_name: str
    name: str
    path: str
    header_offset: int
    data_offset: int
    size: int
    map_index: int
    version: int

    @property
    def end(self) -> int:
        return self.data_offset + self.size

@dataclass
class TextEntry:
    id: str
    chunk_number: int
    absolute_offset: int
    relative_offset: int
    raw: bytes
    encoding: str
    original: str
    translation: str = ""
    target_encoding: str = "source"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "chunk": self.chunk_number,
            "offset_hex": f"0x{self.absolute_offset:X}",
            "relative_offset_hex": f"0x{self.relative_offset:X}",
            "original_hex": self.raw.hex(),
            "source_encoding": self.encoding,
            "target_encoding": self.target_encoding,
            "source": self.original,
            "translation": self.translation,
        }

@dataclass
class Xfbin:
    path: Path
    blob: bytes
    sha256: str
    nucc_version: int
    chunks: list[Chunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    text_entries: list[TextEntry] = field(default_factory=list)
    file_paths: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)


def parse_xfbin(path: Path) -> Xfbin:
    length = path.stat().st_size
    if length > MAX_INPUT_SIZE:
        raise FormatError(T(f"Файл завеликий для цієї версії (> {MAX_INPUT_SIZE // 1024 // 1024} МіБ). Не відкривайте тут CPK.", f"The file is too large for this version (> {MAX_INPUT_SIZE // 1024 // 1024} MiB). Do not open CPK files here."))
    blob = path.read_bytes()
    if len(blob) < 68 or blob[:4] != b"NUCC":
        raise FormatError(T("Це не підтримуваний NUCC/XFBIN (перші чотири байти мають бути NUCC).", "This is not a supported NUCC/XFBIN file (the first four bytes must be NUCC)."))
    # Documented Storm-family XFBIN header: NUCC + version + 8 flags + 3 more words (28 bytes).
    version = u32(blob, 4)
    result = Xfbin(path, blob, hashlib.sha256(blob).hexdigest(), version)
    pos = 28
    if pos + 40 > len(blob):
        raise FormatError(T("Занадто коротка таблиця індексів XFBIN.", "The XFBIN index table is too short."))
    counters = struct.unpack_from(">10I", blob, pos)
    type_count, type_size, path_count, path_size, name_count, name_size, map_count, map_size, index_count, ref_count = counters
    counts = (type_count, path_count, name_count, map_count, index_count, ref_count)
    if max(counts) > 1_000_000:
        raise FormatError(T("Некоректні лічильники таблиць XFBIN.", "Invalid XFBIN table counters."))
    if map_size != map_count * 12:
        result.warnings.append("Незвична довжина таблиці карт чанків: потрібна ручна перевірка.")
        if map_size < map_count * 12:
            raise FormatError(T("Недостатня довжина таблиці карт чанків.", "The chunk map table is too short."))
    pos += 40
    types = read_name_table(blob, pos, type_size, type_count)
    pos += type_size
    paths = read_name_table(blob, pos, path_size, path_count)
    pos += path_size
    names = read_name_table(blob, pos, name_size, name_count)
    pos += name_size
    pos = (pos + 3) & ~3
    if pos + map_count * 12 + ref_count * 8 + index_count * 4 > len(blob):
        raise FormatError(T("Індекс XFBIN виходить за межі контейнера.", "The XFBIN index runs past the end of the container."))
    chunk_maps = [struct.unpack_from(">III", blob, pos + i * 12) for i in range(map_count)]
    pos += map_size
    pos += ref_count * 8  # reference pairs (name_idx, chunk_map_idx)
    chunk_indices = [u32(blob, pos + i * 4) for i in range(index_count)]
    pos += index_count * 4
    result.file_paths = paths
    result.types = types

    current_page, page_base, number = 0, 0, 0
    while pos < len(blob):
        if len(blob) - pos < 12:
            raise FormatError(T(f"Залишилися неповні дані чанка: 0x{pos:X}.", f"Incomplete chunk data left over at 0x{pos:X}."))
        header_offset = pos
        body_len, local_idx, chunk_version, _unk = struct.unpack_from(">IIHH", blob, pos)
        pos += 12
        if body_len > len(blob) - pos:
            raise FormatError(T(f"Чанк {number}: оголошений розмір виходить за межі файлу.", f"Chunk {number}: the declared size runs past the end of the file."))
        mapped_idx = page_base + local_idx
        ty = name = file_path = "<невідомо>"
        if mapped_idx < len(chunk_indices):
            map_id = chunk_indices[mapped_idx]
            if map_id < len(chunk_maps):
                tidx, pidx, nidx = chunk_maps[map_id]
                ty = types[tidx] if tidx < len(types) else "<тип?>"
                file_path = paths[pidx] if pidx < len(paths) else "<шлях?>"
                name = names[nidx] if nidx < len(names) else "<назва?>"
        else:
            result.warnings.append(f"Чанк {number}: карта {mapped_idx} поза межами індексу.")
        ck = Chunk(number, current_page, ty, name, file_path, header_offset, pos, body_len, local_idx, chunk_version)
        result.chunks.append(ck)
        if ty == "nuccChunkPage":
            if body_len < 8:
                raise FormatError(T(f"Чанк сторінки {number} коротший за 8 байтів.", f"Page chunk {number} is shorter than 8 bytes."))
            page_increment, _ref_increment = struct.unpack_from(">II", blob, pos)
            if page_increment == 0 or page_increment > index_count:
                raise FormatError(T(f"Некоректний індекс сторінки: {page_increment}.", f"Invalid page index: {page_increment}."))
            page_base += page_increment
            current_page += 1
        pos += body_len
        number += 1
        if number > MAX_ITEMS:
            raise FormatError(T("Перевищено обмеження кількості чанків.", "The chunk count limit was exceeded."))
    if not result.chunks:
        result.warnings.append("XFBIN містить індекс, але не має чанків.")
    if page_base > len(chunk_indices):
        result.warnings.append("Індекси сторінок виходять за кількість карт.")
    return result
