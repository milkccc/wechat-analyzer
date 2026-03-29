#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Export one Windows WeChat 4 conversation from decrypted db_storage to CSV.

Usage:
  python export_contact_windows_v4.py --list-recent
  python export_contact_windows_v4.py --list-contacts
  python export_contact_windows_v4.py --contact "Name Or Remark"
  python export_contact_windows_v4.py --contact-id "wxid_xxx" --output ./chat.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from chat_analyzer.utils.console import configure_stdio


configure_stdio()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def looks_like_db_storage(path: Path) -> bool:
    return (
        (path / "contact" / "contact.db").exists()
        and (path / "message" / "message_0.db").exists()
        and (path / "session" / "session.db").exists()
    )


def resolve_db_dir(base: Path) -> Optional[Path]:
    base = Path(base)
    if looks_like_db_storage(base):
        return base
    if looks_like_db_storage(base / "db_storage"):
        return base / "db_storage"

    candidates: list[tuple[float, Path]] = []
    for pattern in ("db_storage", "wxid_*/db_storage", "*/db_storage"):
        for candidate in base.glob(pattern):
            if not candidate.is_dir() or not looks_like_db_storage(candidate):
                continue
            msg_db = candidate / "message" / "message_0.db"
            candidates.append((msg_db.stat().st_mtime, candidate))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def default_db_dir() -> Optional[Path]:
    cfg = load_config()
    configured = cfg.get("decrypted_db_dir")
    if configured:
        resolved = resolve_db_dir(Path(os.path.expanduser(configured)))
        if resolved is not None:
            return resolved

    guesses = [
        SCRIPT_DIR.parent / "wechat-decrypted",
        Path.home() / "Documents" / "wechat-db-decrypt-windows" / "decrypted",
        Path.home() / "Documents" / "wechat-decrypted",
    ]
    for guess in guesses:
        resolved = resolve_db_dir(guess)
        if resolved is not None:
            return resolved
    return None


def get_db_dir(arg: Optional[str]) -> Path:
    if arg:
        resolved = resolve_db_dir(Path(os.path.expanduser(arg)))
        if resolved is not None:
            return resolved
        raise SystemExit(f"找不到可用的解密库目录: {arg}")

    resolved = default_db_dir()
    if resolved is None:
        raise SystemExit(
            "找不到可用的解密库目录。请先解密数据库，或用 --db-dir 指向包含 "
            "contact/message/session 子目录的 db_storage。"
        )
    return resolved


def message_db_paths(db_dir: Path) -> list[Path]:
    msg_dir = db_dir / "message"
    paths: list[tuple[int, Path]] = []
    for path in msg_dir.glob("message_*.db"):
        match = re.fullmatch(r"message_(\d+)\.db", path.name)
        if match:
            paths.append((int(match.group(1)), path))
    paths.sort(key=lambda item: item[0])
    return [path for _, path in paths]


def read_contacts(db_dir: Path) -> list[tuple[str, str, str, str, int]]:
    conn = sqlite3.connect(db_dir / "contact" / "contact.db")
    try:
        rows = conn.execute(
            """
            SELECT username, COALESCE(remark, ''), COALESCE(nick_name, ''),
                   COALESCE(alias, ''), COALESCE(verify_flag, 0)
            FROM contact
            WHERE local_type != 4
            ORDER BY CASE WHEN remark != '' THEN 0 ELSE 1 END, remark, nick_name, username
            """
        ).fetchall()
        return rows
    finally:
        conn.close()


def contact_display_name(remark: str, nick: str, alias: str, username: str) -> str:
    return remark or nick or alias or username


def list_contacts(db_dir: Path) -> None:
    print(f"{'显示名':<30} {'微信ID':<28} {'备注/昵称'}")
    print("-" * 90)
    for username, remark, nick, alias, _verify_flag in read_contacts(db_dir):
        display = contact_display_name(remark, nick, alias, username)
        extra = " / ".join(part for part in (remark, nick) if part and part != display)
        print(f"{display:<30} {username:<28} {extra}")


def recent_sessions(db_dir: Path) -> list[dict]:
    contacts = {
        username: (remark, nick, alias, verify_flag)
        for username, remark, nick, alias, verify_flag in read_contacts(db_dir)
    }

    conn = sqlite3.connect(db_dir / "session" / "session.db")
    try:
        rows = conn.execute(
            """
            SELECT username, summary, sort_timestamp, last_sender_display_name
            FROM SessionTable
            ORDER BY sort_timestamp DESC
            """
        ).fetchall()
    finally:
        conn.close()

    items = []
    for username, summary, sort_ts, last_sender_display_name in rows:
        remark, nick, alias, verify_flag = contacts.get(username, ("", "", "", 0))
        items.append(
            {
                "username": username,
                "display": contact_display_name(remark, nick, alias, username),
                "remark": remark,
                "nick": nick,
                "alias": alias,
                "verify_flag": verify_flag,
                "summary": summary or "",
                "sort_timestamp": int(sort_ts or 0),
                "last_sender_display_name": last_sender_display_name or "",
            }
        )
    return items


def is_direct_human_session(username: str) -> bool:
    if username.endswith("@chatroom"):
        return False
    if username.startswith("gh_") or username.startswith("@"):
        return False
    if username.endswith("@openim") or username.endswith("@kefu.openim"):
        return False
    if "brandsessionholder" in username or username.endswith("sessionholder"):
        return False
    if username in {"weixin", "notifymessage", "fmessage"}:
        return False
    if username.startswith("ww_"):
        return False
    return True


def list_recent(db_dir: Path, limit: int = 20, direct_only: bool = True) -> None:
    print(f"{'显示名':<30} {'微信ID':<28} {'最近消息摘要'}")
    print("-" * 120)
    shown = 0
    for item in recent_sessions(db_dir):
        if direct_only and not is_direct_human_session(item["username"]):
            continue
        summary = item["summary"].replace("\n", " ")[:60]
        print(f"{item['display']:<30} {item['username']:<28} {summary}")
        shown += 1
        if shown >= limit:
            break


def find_contact(db_dir: Path, name: str) -> list[tuple[str, str, str, str, int]]:
    pattern = f"%{name}%"
    conn = sqlite3.connect(db_dir / "contact" / "contact.db")
    try:
        rows = conn.execute(
            """
            SELECT username, COALESCE(remark, ''), COALESCE(nick_name, ''),
                   COALESCE(alias, ''), COALESCE(verify_flag, 0)
            FROM contact
            WHERE username LIKE ? OR remark LIKE ? OR nick_name LIKE ? OR alias LIKE ?
            ORDER BY CASE WHEN remark != '' THEN 0 ELSE 1 END, remark, nick_name, username
            """,
            (pattern, pattern, pattern, pattern),
        ).fetchall()
        return rows
    finally:
        conn.close()


def infer_self_wxid_from_path(db_dir: Path) -> Optional[str]:
    match = re.match(r"^(wxid_[A-Za-z0-9]+)", db_dir.parent.name)
    if match:
        return match.group(1)
    return None


def get_self_wxid(db_dir: Path) -> Optional[str]:
    by_path = infer_self_wxid_from_path(db_dir)
    if by_path:
        return by_path

    contacts = {row[0] for row in read_contacts(db_dir)}
    for msg_db in message_db_paths(db_dir):
        conn = sqlite3.connect(msg_db)
        try:
            rows = conn.execute(
                "SELECT user_name FROM Name2Id WHERE user_name LIKE 'wxid_%'"
            ).fetchall()
        finally:
            conn.close()

        candidates = [user_name for (user_name,) in rows if user_name and user_name not in contacts]
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            return candidates[0]
    return None


def get_sender_ids(msg_db: Path, self_wxid: str, contact_wxid: str) -> tuple[Optional[int], Optional[int]]:
    conn = sqlite3.connect(msg_db)
    try:
        rows = conn.execute(
            "SELECT rowid, user_name FROM Name2Id WHERE user_name IN (?, ?)",
            (self_wxid, contact_wxid),
        ).fetchall()
    finally:
        conn.close()

    self_id = None
    contact_id = None
    for rowid, user_name in rows:
        if user_name == self_wxid:
            self_id = rowid
        if user_name == contact_wxid:
            contact_id = rowid
    return self_id, contact_id


def decode_blob(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        return str(raw)

    data = bytes(raw)
    if not data:
        return ""

    if data[:4] == b"\x28\xb5\x2f\xfd":
        for module_name in ("zstd", "zstandard"):
            try:
                if module_name == "zstd":
                    import zstd  # type: ignore

                    return zstd.decompress(data).decode("utf-8", errors="replace")
                import zstandard as zstandard  # type: ignore

                return zstandard.ZstdDecompressor().decompress(data).decode("utf-8", errors="replace")
            except Exception:
                pass

    return data.decode("utf-8", errors="replace")


def export_messages(
    db_dir: Path,
    contact_wxid: str,
    contact_name: str,
    self_wxid: str,
    output_path: Path,
) -> int:
    table_name = f"Msg_{hashlib.md5(contact_wxid.encode()).hexdigest()}"
    merged_rows: list[tuple[int, int | None, int, str, Optional[int], str]] = []
    matched_dbs: list[str] = []

    for msg_db in message_db_paths(db_dir):
        conn = sqlite3.connect(msg_db)
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if table_name not in tables:
                continue

            self_id, _contact_id = get_sender_ids(msg_db, self_wxid, contact_wxid)
            rows = conn.execute(
                f"""
                SELECT create_time, real_sender_id, local_type, message_content, source
                FROM {table_name}
                ORDER BY create_time ASC
                """
            ).fetchall()
        finally:
            conn.close()

        matched_dbs.append(msg_db.name)
        for create_time, sender_id, local_type, content, source in rows:
            text = decode_blob(content)
            if not text:
                text = decode_blob(source)
            merged_rows.append((int(create_time), sender_id, int(local_type), text, self_id, msg_db.name))

    if not merged_rows:
        raise SystemExit(f"找不到联系人 {contact_name} ({contact_wxid}) 的消息记录。")

    merged_rows.sort(key=lambda row: row[0])
    json_records = []
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "datetime", "sender", "is_sender", "type", "content"])

        for create_time, sender_id, local_type, text, self_id, _db_name in merged_rows:
            dt = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")
            is_self = 1 if self_id is not None and sender_id == self_id else 0
            sender_name = "我" if is_self else contact_name
            writer.writerow([create_time, dt, sender_name, is_self, local_type, text])
            json_records.append(
                {
                    "timestamp": create_time,
                    "datetime": dt,
                    "sender": sender_name,
                    "is_sender": is_self,
                    "type": local_type,
                    "content": text,
                }
            )

    json_path = output_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "format": "wechat_chat_export_v1",
                "contact_wxid": contact_wxid,
                "contact_name": contact_name,
                "self_wxid": self_wxid,
                "message_count": len(merged_rows),
                "source_databases": matched_dbs,
                "messages": json_records,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"[*] 已导出 {len(merged_rows)} 条消息，来源库: {', '.join(matched_dbs)}")
    print(f"EXPORT_PATH:{output_path}")
    print(f"JSON_PATH:{json_path}")
    return len(merged_rows)


def get_self_nick(db_dir: Path, self_wxid: str) -> Optional[str]:
    conn = sqlite3.connect(db_dir / "contact" / "contact.db")
    try:
        row = conn.execute(
            "SELECT nick_name FROM contact WHERE username = ?",
            (self_wxid,),
        ).fetchone()
        if row and row[0]:
            return row[0]
    finally:
        conn.close()
    return None


def get_avatar_path(db_dir: Path, wxid: str) -> Optional[str]:
    head_db = db_dir / "head_image" / "head_image.db"
    if not head_db.exists():
        return None

    conn = sqlite3.connect(head_db)
    try:
        row = conn.execute(
            "SELECT image_buffer FROM head_image WHERE username = ?",
            (wxid,),
        ).fetchone()
    finally:
        conn.close()

    if not row or not row[0]:
        return None

    data = bytes(row[0])
    if data.startswith(b"\xff\xd8\xff"):
        suffix = ".jpg"
    elif data.startswith(b"\x89PNG"):
        suffix = ".png"
    elif data.startswith(b"GIF8"):
        suffix = ".gif"
    else:
        return None

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.close()
    return tmp.name


def choose_match(matches: list[tuple[str, str, str, str, int]]) -> tuple[str, str, str, str, int]:
    if len(matches) == 1:
        return matches[0]

    print(f"找到 {len(matches)} 个匹配联系人:")
    for index, (username, remark, nick, alias, _verify_flag) in enumerate(matches, start=1):
        display = contact_display_name(remark, nick, alias, username)
        print(f"  [{index}] {display} ({username})")

    choice = input("请输入编号: ").strip()
    try:
        return matches[int(choice) - 1]
    except Exception as exc:
        raise SystemExit("无效选择") from exc


def safe_output_name(contact_name: str) -> str:
    safe = re.sub(r"[\\\\/:*?\"<>|\\s]+", "_", contact_name).strip("_")
    return safe[:40] or "contact"


def write_meta(db_dir: Path, output_path: Path, self_wxid: str, self_name: str, partner_wxid: str, partner_name: str) -> None:
    meta = {
        "self_wxid": self_wxid,
        "self_name": self_name,
        "self_avatar_path": get_avatar_path(db_dir, self_wxid),
        "partner_wxid": partner_wxid,
        "partner_name": partner_name,
        "partner_avatar_path": get_avatar_path(db_dir, partner_wxid),
    }
    meta_path = output_path.with_suffix(".meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"META_PATH:{meta_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 Windows 微信 4 解密库中的联系人聊天记录")
    parser.add_argument("--contact", help="联系人名称，可匹配备注/昵称/别名/微信ID")
    parser.add_argument("--contact-id", help="精确指定联系人微信ID，例如 wxid_xxx")
    parser.add_argument("--list-contacts", action="store_true", help="列出联系人")
    parser.add_argument("--list-recent", action="store_true", help="列出最近单聊联系人")
    parser.add_argument("--all-recent", action="store_true", help="列出最近会话，不过滤群聊/公众号")
    parser.add_argument("--output", help="输出 CSV 路径")
    parser.add_argument("--db-dir", help="解密后的 db_storage 路径，或其上级目录")
    args = parser.parse_args()

    db_dir = get_db_dir(args.db_dir)
    print(f"[*] 使用解密库目录: {db_dir}")

    if args.list_recent:
        list_recent(db_dir, direct_only=not args.all_recent)
        return

    if args.list_contacts:
        list_contacts(db_dir)
        return

    username = args.contact_id
    remark = ""
    nick = ""
    alias = ""

    if not username:
        if not args.contact:
            parser.print_help()
            raise SystemExit(1)
        matches = find_contact(db_dir, args.contact)
        if not matches:
            raise SystemExit(f"找不到联系人: {args.contact}")
        username, remark, nick, alias, _verify_flag = choose_match(matches)

    if not args.output:
        contact_name = contact_display_name(remark, nick, alias, username)
        output_path = SCRIPT_DIR / f"export_{safe_output_name(contact_name)}.csv"
    else:
        output_path = Path(args.output)

    if not username:
        raise SystemExit("没有可用的联系人 ID。")

    if not (remark or nick or alias):
        matches = find_contact(db_dir, username)
        if matches:
            username, remark, nick, alias, _verify_flag = matches[0]

    contact_name = contact_display_name(remark, nick, alias, username)
    self_wxid = get_self_wxid(db_dir)
    if not self_wxid:
        raise SystemExit("无法确定自己的 wxid。")

    print(f"[*] 导出联系人: {contact_name} ({username})")
    print(f"[*] 自己的 wxid: {self_wxid}")
    export_messages(db_dir, username, contact_name, self_wxid, output_path)
    write_meta(db_dir, output_path, self_wxid, get_self_nick(db_dir, self_wxid) or "我", username, contact_name)


if __name__ == "__main__":
    main()
