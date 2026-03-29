#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export QQNT private chats to analyzer-friendly CSV files."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sqlcipher3 import dbapi2 as sqlcipher

from chat_analyzer.utils.console import configure_stdio
from tools.qqnt_element_pb2 import Elements


configure_stdio()


PRAGMAS = (
    "PRAGMA key = '{key}';",
    "PRAGMA cipher_page_size = 4096;",
    "PRAGMA kdf_iter = 4000;",
    "PRAGMA cipher_hmac_algorithm = HMAC_SHA1;",
    "PRAGMA cipher_default_kdf_algorithm = PBKDF2_HMAC_SHA512;",
    "PRAGMA cipher = 'aes-256-cbc';",
)


@dataclass
class Contact:
    uid: str
    qq_num: int | None
    nickname: str | None
    remark: str | None
    display_name: str
    message_count: int
    first_timestamp: int
    last_timestamp: int


def sanitize_filename(name: str) -> str:
    value = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in name).strip().rstrip(".")
    return value or "unnamed"


def sql_string(value: str) -> str:
    return value.replace("'", "''")


class CipherDatabase:
    def __init__(self, raw_path: Path, key: str, temp_dir: Path):
        self.raw_path = raw_path
        self.key = key
        self.temp_dir = temp_dir
        self.clean_path = temp_dir / f"{raw_path.name}.clean.db"
        self.conn: sqlcipher.Connection | None = None

    def __enter__(self) -> sqlcipher.Connection:
        with self.raw_path.open("rb") as src, self.clean_path.open("wb") as dst:
            src.seek(1024)
            shutil.copyfileobj(src, dst)

        conn = sqlcipher.connect(str(self.clean_path))
        conn.row_factory = sqlcipher.Row
        cur = conn.cursor()
        for stmt in PRAGMAS:
            cur.execute(stmt.format(key=sql_string(self.key)))
        cur.execute("SELECT count(*) FROM sqlite_master")
        cur.fetchone()
        self.conn = conn
        return conn

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn is not None:
            self.conn.close()
        self.clean_path.unlink(missing_ok=True)


def existing_tables(conn: sqlcipher.Connection) -> set[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


def pick_profile_table(tables: set[str]) -> str:
    for candidate in ("profile_info_v6", "profile_info_v2"):
        if candidate in tables:
            return candidate
    raise RuntimeError("No supported profile_info table found.")


def load_profiles(conn: sqlcipher.Connection, profile_table: str) -> dict[str, dict]:
    cur = conn.cursor()
    cur.execute(
        f'SELECT "1000" AS uid, "1002" AS qq_num, "20002" AS nickname, "20009" AS remark '
        f'FROM {profile_table}'
    )
    return {
        row["uid"]: {
            "qq_num": row["qq_num"],
            "nickname": row["nickname"],
            "remark": row["remark"],
        }
        for row in cur.fetchall()
        if row["uid"]
    }


def load_uid_mapping(conn: sqlcipher.Connection) -> dict[str, int]:
    cur = conn.cursor()
    cur.execute('SELECT "48902" AS uid, "1002" AS qq_num FROM nt_uid_mapping_table')
    return {row["uid"]: row["qq_num"] for row in cur.fetchall() if row["uid"]}


def load_contacts(msg_conn: sqlcipher.Connection, profiles: dict[str, dict], uid_map: dict[str, int]) -> list[Contact]:
    cur = msg_conn.cursor()
    cur.execute(
        'SELECT "40021" AS uid, count(*) AS message_count, '
        'min("40050") AS first_timestamp, max("40050") AS last_timestamp '
        'FROM c2c_msg_table GROUP BY "40021" ORDER BY last_timestamp DESC'
    )
    contacts: list[Contact] = []
    for row in cur.fetchall():
        uid = row["uid"]
        profile = profiles.get(uid, {})
        qq_num = profile.get("qq_num") or uid_map.get(uid)
        nickname = profile.get("nickname")
        remark = profile.get("remark")
        display_name = remark or nickname or (str(qq_num) if qq_num else uid)
        contacts.append(
            Contact(
                uid=uid,
                qq_num=qq_num,
                nickname=nickname,
                remark=remark,
                display_name=display_name,
                message_count=row["message_count"],
                first_timestamp=row["first_timestamp"],
                last_timestamp=row["last_timestamp"],
            )
        )
    return contacts


def matches(contact: Contact, query: str) -> bool:
    q = query.strip().lower()
    choices = [contact.uid.lower(), contact.display_name.lower()]
    if contact.nickname:
        choices.append(contact.nickname.lower())
    if contact.remark:
        choices.append(contact.remark.lower())
    if contact.qq_num is not None:
        choices.append(str(contact.qq_num))
    return any(q in choice for choice in choices)


def render_element(element) -> tuple[str | None, bool]:
    etype = element.type
    if etype == 1:
        text = element.text.strip()
        return (text or None), True
    if etype == 2:
        detail = element.imageText or element.fileName or ""
        return f"[图片]{(' ' + detail) if detail else ''}", False
    if etype == 3:
        detail = element.fileName or ""
        return f"[文件]{(' ' + detail) if detail else ''}", False
    if etype == 4:
        detail = element.voiceText or ""
        return f"[语音]{(' ' + detail) if detail else ''}", False
    if etype == 5:
        detail = element.fileName or ""
        return f"[视频]{(' ' + detail) if detail else ''}", False
    if etype == 6:
        detail = element.emojiText or ""
        return f"[表情]{(' ' + detail) if detail else ''}", False
    if etype == 7:
        return None, False
    if etype == 8:
        return "[提示]", False
    if etype == 9:
        detail = " ".join(part for part in (element.redPacket.summary, element.redPacket.prompt) if part)
        return f"[红包]{(' ' + detail) if detail else ''}", False
    if etype == 10:
        return "[应用消息]", False
    if etype == 21:
        detail = " ".join(part for part in (element.callStatus, element.callText) if part)
        return f"[通话]{(' ' + detail) if detail else ''}", False
    if etype == 26:
        detail = " ".join(part for part in (element.feedTitle.text, element.feedContent.text) if part)
        return f"[动态消息]{(' ' + detail) if detail else ''}", False
    return f"[类型{etype}消息]", False


def render_message(body: bytes) -> tuple[str, bool, list[int]]:
    elements = Elements()
    element_types: list[int] = []
    text_parts: list[str] = []
    other_parts: list[str] = []
    try:
        elements.ParseFromString(body)
    except Exception:
        return "[未解析消息]", False, element_types

    for element in elements.elements:
        element_types.append(element.type)
        rendered, is_text = render_element(element)
        if not rendered:
            continue
        if is_text:
            text_parts.append(rendered)
        else:
            other_parts.append(rendered)

    if text_parts:
        return "".join(text_parts), True, element_types
    if other_parts:
        return " ".join(other_parts), False, element_types
    return "[空消息]", False, element_types


def format_dt(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def unique_path(base_dir: Path, stem: str, suffix: str) -> Path:
    path = base_dir / f"{stem}{suffix}"
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = base_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def export_contact(msg_conn: sqlcipher.Connection, contact: Contact, output_dir: Path, self_name: str) -> dict:
    safe_name = sanitize_filename(contact.display_name)
    base_stem = f"qq_{contact.qq_num or contact.uid}_{safe_name}"
    csv_path = unique_path(output_dir, base_stem, ".csv")
    meta_path = csv_path.with_suffix(".meta.json")

    cur = msg_conn.cursor()
    cur.execute(
        'SELECT "40001" AS message_id, "40011" AS raw_msg_type, "40013" AS sender_flag, '
        '"40020" AS sender_uid, "40050" AS ts, "40800" AS body '
        'FROM c2c_msg_table WHERE "40021" = ? ORDER BY "40050", "40001"',
        (contact.uid,),
    )

    rows_written = 0
    text_rows = 0
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=[
                "timestamp",
                "datetime",
                "sender",
                "is_sender",
                "type",
                "raw_msg_type",
                "content",
                "contact_uid",
                "contact_qq",
                "message_id",
                "raw_sender_flag",
                "element_types",
            ],
        )
        writer.writeheader()
        for row in cur.fetchall():
            sender_flag = row["sender_flag"]
            is_sender = 1 if sender_flag in (1, 2, 8) else 0
            content, is_text, element_types = render_message(row["body"] or b"")
            msg_type = 1 if is_text else 0
            if is_text:
                text_rows += 1
            writer.writerow(
                {
                    "timestamp": row["ts"],
                    "datetime": format_dt(row["ts"]),
                    "sender": self_name if is_sender else contact.display_name,
                    "is_sender": is_sender,
                    "type": msg_type,
                    "raw_msg_type": row["raw_msg_type"] or 0,
                    "content": content,
                    "contact_uid": contact.uid,
                    "contact_qq": contact.qq_num or "",
                    "message_id": row["message_id"],
                    "raw_sender_flag": sender_flag,
                    "element_types": json.dumps(element_types, ensure_ascii=False),
                }
            )
            rows_written += 1

    meta = {
        "source": "qqnt",
        "self_name": self_name,
        "partner_name": contact.display_name,
        "partner_uid": contact.uid,
        "partner_qq": contact.qq_num,
        "message_count": rows_written,
        "text_message_count": text_rows,
        "first_timestamp": contact.first_timestamp,
        "first_datetime": format_dt(contact.first_timestamp),
        "last_timestamp": contact.last_timestamp,
        "last_datetime": format_dt(contact.last_timestamp),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"csv": str(csv_path), "meta": str(meta_path), **meta}


def print_contacts(contacts: Iterable[Contact]) -> None:
    for contact in contacts:
        start = format_dt(contact.first_timestamp)
        end = format_dt(contact.last_timestamp)
        qq_num = contact.qq_num if contact.qq_num is not None else "-"
        print(f"{contact.display_name} | qq={qq_num} | uid={contact.uid} | messages={contact.message_count} | {start} -> {end}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export QQNT private chats to analyzer CSV files.")
    parser.add_argument("--db-dir", required=True, help="QQNT nt_db directory")
    parser.add_argument("--key", required=True, help="QQNT SQLCipher key")
    parser.add_argument("--output-dir", default="./exports/qq/c2c", help="Output directory")
    parser.add_argument("--self-name", default="我", help="Self display name written to CSV")
    parser.add_argument("--contact", default=None, help="Filter by contact name / QQ number / uid")
    parser.add_argument("--list-contacts", action="store_true", help="Only print detected private-chat contacts")
    args = parser.parse_args()

    db_dir = Path(args.db_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    required = {
        "nt_msg.db": db_dir / "nt_msg.db",
        "profile_info.db": db_dir / "profile_info.db",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing required database files: {', '.join(missing)}")

    with tempfile.TemporaryDirectory(prefix="qqnt_export_") as temp_root:
        temp_dir = Path(temp_root)
        with CipherDatabase(required["nt_msg.db"], args.key, temp_dir) as msg_conn, CipherDatabase(
            required["profile_info.db"], args.key, temp_dir
        ) as profile_conn:
            profile_table = pick_profile_table(existing_tables(profile_conn))
            profiles = load_profiles(profile_conn, profile_table)
            uid_map = load_uid_mapping(msg_conn)
            contacts = load_contacts(msg_conn, profiles, uid_map)

            if args.contact:
                contacts = [contact for contact in contacts if matches(contact, args.contact)]

            if args.list_contacts:
                print_contacts(contacts)
                return

            exports = [export_contact(msg_conn, contact, output_dir, args.self_name) for contact in contacts]
            index_path = output_dir / "qq_c2c_contacts.json"
            index_path.write_text(json.dumps([asdict(contact) for contact in contacts], ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"exported": len(exports), "index": str(index_path), "files": exports}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
