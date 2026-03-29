#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge analyzer-friendly chat CSV files from different sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


FIELD_ALIASES = {
    "timestamp": "timestamp",
    "ts": "timestamp",
    "datetime": "datetime",
    "sender": "sender",
    "is_sender": "is_sender",
    "type": "type",
    "msg_type": "type",
    "content": "content",
}


def load_meta(csv_path: Path) -> dict:
    meta_path = csv_path.with_suffix(".meta.json")
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    return {}


def normalize_frame(csv_path: Path) -> tuple[pd.DataFrame, dict]:
    df = pd.read_csv(csv_path)
    rename_map = {col: FIELD_ALIASES[col] for col in df.columns if col in FIELD_ALIASES}
    df = df.rename(columns=rename_map)

    required = {"timestamp", "is_sender", "content"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{csv_path} missing columns: {sorted(missing)}")

    if "datetime" not in df.columns:
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
    if "sender" not in df.columns:
        df["sender"] = df["is_sender"].map({1: "我", 0: "对方"}).fillna("未知")
    if "type" not in df.columns:
        df["type"] = 1

    meta = load_meta(csv_path)
    source_name = meta.get("partner_name") or csv_path.stem
    platform = meta.get("source") or ("qqnt" if "qq_" in csv_path.name else "wechat")
    df["source_name"] = source_name
    df["source_platform"] = platform

    keep = [
        "timestamp",
        "datetime",
        "sender",
        "is_sender",
        "type",
        "content",
        "source_name",
        "source_platform",
    ]
    return df[keep].copy(), meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge analyzer-ready chat exports.")
    parser.add_argument("inputs", nargs="+", help="Input CSV files")
    parser.add_argument("--output", required=True, help="Merged CSV output path")
    parser.add_argument("--self-name", default=None, help="Override self name in merged meta")
    parser.add_argument("--partner-name", default=None, help="Override partner name in merged meta")
    args = parser.parse_args()

    frames = []
    metas = []
    for raw_path in args.inputs:
        csv_path = Path(raw_path).expanduser().resolve()
        df, meta = normalize_frame(csv_path)
        frames.append(df)
        metas.append({"csv": str(csv_path), **meta})

    merged = pd.concat(frames, ignore_index=True)
    merged["timestamp"] = pd.to_numeric(merged["timestamp"], errors="coerce")
    merged = merged.dropna(subset=["timestamp"]).copy()
    merged["timestamp"] = merged["timestamp"].astype(int)
    merged["is_sender"] = pd.to_numeric(merged["is_sender"], errors="coerce").fillna(0).astype(int)
    merged["type"] = pd.to_numeric(merged["type"], errors="coerce").fillna(0).astype(int)
    merged = merged.sort_values(["timestamp", "source_platform", "source_name"]).reset_index(drop=True)

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False, encoding="utf-8-sig")

    partner_names = [m.get("partner_name") for m in metas if m.get("partner_name")]
    self_name = args.self_name or next((m.get("self_name") for m in metas if m.get("self_name")), "我")
    partner_name = args.partner_name or " + ".join(dict.fromkeys(partner_names)) or "合并对象"

    merged_meta = {
        "source": "merged",
        "self_name": self_name,
        "partner_name": partner_name,
        "inputs": metas,
        "message_count": int(len(merged)),
        "text_message_count": int((merged["type"] == 1).sum()),
        "first_timestamp": int(merged["timestamp"].min()) if len(merged) else None,
        "last_timestamp": int(merged["timestamp"].max()) if len(merged) else None,
    }
    output_path.with_suffix(".meta.json").write_text(
        json.dumps(merged_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
