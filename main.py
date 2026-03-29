#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聊天记录分析工具

默认工作流：
  直接运行后会生成图表、分析输入文件，并自动生成人格结果与完整报告。

如已有人格分析 JSON，也可显式传入覆盖自动生成结果：
  python main.py <CSV路径> --personality-result personality_result.json \
                           --partner-personality-result partner_result.json \
                           --partner-name <联系人名>

选项：
  --output DIR                        输出目录（默认 ./analysis_output）
  --sample-size N                     供后续人格分析使用的采样消息数上限（默认 100）
  --personality-result FILE           自己的人格分析结果 JSON
  --partner-personality-result FILE   对方的人格分析结果 JSON
  --partner-name NAME                 对方的名字（默认"对方"）
"""

import argparse
import base64
import json
import os
import sys
from typing import Optional

from chat_analyzer.analysis import loader, sampler
from chat_analyzer.analysis.insights import analyze_advanced_insights
from chat_analyzer.analysis.loader import _replace_wechat_emoji
from chat_analyzer.analysis.personality import extract_features, generate_result, normalize_result
from chat_analyzer.analysis import stats as stats_mod
from chat_analyzer.reporting import html_report as report_mod
from chat_analyzer.reporting import visualizer
from chat_analyzer.utils.console import configure_stdio


def _fix_emoji(obj):
    """递归替换 JSON 对象中所有字符串里的微信表情占位符"""
    if isinstance(obj, str):
        return _replace_wechat_emoji(obj)
    if isinstance(obj, dict):
        return {k: _fix_emoji(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fix_emoji(v) for v in obj]
    return obj


def _load_meta(csv_path: str) -> dict:
    """尝试读取 export 脚本写出的 meta sidecar JSON"""
    meta_path = csv_path.replace('.csv', '.meta.json')
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _avatar_b64(path: Optional[str]) -> Optional[str]:
    """读取头像文件并转为 base64 data URI，失败返回 None"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data[:4] == b'\x89PNG':
            mime = 'image/png'
        elif data[:3] == b'\xff\xd8\xff':
            mime = 'image/jpeg'
        elif data[:4] == b'GIF8':
            mime = 'image/gif'
        else:
            mime = 'image/jpeg'
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return None


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _is_garbled_text(value) -> bool:
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) < 4:
            return False
        qmarks = stripped.count('?')
        return qmarks >= 4 and qmarks >= max(4, len(stripped) // 4)
    if isinstance(value, dict):
        return any(_is_garbled_text(v) for v in value.values())
    if isinstance(value, list):
        return any(_is_garbled_text(v) for v in value)
    return False


def _build_ai_input(df, stats: dict, sample_size: int) -> Optional[dict]:
    if df is None or len(df) == 0 or stats is None:
        return None

    clean_df = loader.filter_for_personality(df)
    if len(clean_df) == 0:
        return None

    messages = sampler.smart_sample(clean_df, target_n=sample_size)
    features = extract_features(messages)
    top_words = sorted(stats['word_freq'].items(), key=lambda x: x[1], reverse=True)[:30]
    dr = stats['date_range']
    return {
        'sample_messages': messages,
        'top_words': [{'word': w, 'count': c} for w, c in top_words],
        'features': features,
        'stats_summary': {
            'date_range': [dr[0].strftime('%Y-%m-%d'), dr[1].strftime('%Y-%m-%d')],
            'total_messages': stats['total_messages'],
            'avg_length': stats['avg_length'],
            'most_active_hour': int(stats['hourly'].idxmax()),
        },
    }


def main():
    configure_stdio()

    parser = argparse.ArgumentParser(description='聊天记录分析工具')
    parser.add_argument('csv_file', help='CSV 文件路径')
    parser.add_argument('--output', default='./analysis_output')
    parser.add_argument('--sample-size', type=int, default=100,
                        help='供后续人格分析使用的采样消息数上限（默认 100）')
    parser.add_argument('--personality-result', default=None,
                        help='自己的人格分析结果 JSON 文件')
    parser.add_argument('--partner-personality-result', default=None,
                        help='对方的人格分析结果 JSON 文件')
    parser.add_argument('--self-name', default=None,
                        help='自己的名字（默认从 meta JSON 读取，否则"我"）')
    parser.add_argument('--partner-name', default=None,
                        help='对方的名字（默认从 meta JSON 读取，否则"对方"）')
    args = parser.parse_args()

    if not os.path.exists(args.csv_file):
        print(f'❌ 文件不存在：{args.csv_file}')
        sys.exit(1)

    output_dir = args.output
    charts_dir = os.path.join(output_dir, 'charts')
    os.makedirs(charts_dir, exist_ok=True)

    font = visualizer.setup_font()
    if font:
        print(f'🖋  使用字体：{font}')

    # ── 读取 meta sidecar（昵称 + 头像），CLI 参数可覆盖 ────────────────────
    meta = _load_meta(args.csv_file)
    self_name    = args.self_name    or meta.get('self_name', '我')
    partner_name = args.partner_name or meta.get('partner_name', '对方')
    self_avatar_data    = _avatar_b64(meta.get('self_avatar_path'))
    partner_avatar_data = _avatar_b64(meta.get('partner_avatar_path'))

    # ── Step 1: 加载数据（自己 + 对方） ─────────────────────────────────────
    print('\n📂 正在加载数据...')
    try:
        conversation_df = loader.load_full(args.csv_file, text_only=True)
    except ValueError as e:
        print(f'❌ 数据加载失败：{e}')
        sys.exit(1)

    df = conversation_df[conversation_df['is_sender'] == 1].copy().reset_index(drop=True)
    print(f'   自己：{len(df):,} 条文本消息')
    if len(df) < 50:
        print('警告：自己消息数量较少，分析结果可靠性有限。')

    try:
        df_partner = conversation_df[conversation_df['is_sender'] == 0].copy().reset_index(drop=True)
        print(f'   对方：{len(df_partner):,} 条文本消息')
    except Exception:
        df_partner = None
        print('   警告：无法加载对方消息')

    # ── Step 2: 统计分析 ────────────────────────────────────────────────────
    print('\n正在计算统计数据...')
    stats = stats_mod.compute(df)
    dr = stats['date_range']
    print(f'   时间跨度：{dr[0].strftime("%Y-%m-%d")} → {dr[1].strftime("%Y-%m-%d")}')
    print(f'   平均消息长度：{stats["avg_length"]} 字')
    print(f'   最活跃时段：{stats["hourly"].idxmax()}:00')

    partner_stats = stats_mod.compute(df_partner) if df_partner is not None and len(df_partner) > 0 else None

    # ── Step 3: 生成可视化图表 ──────────────────────────────────────────────
    print('\n🎨 正在生成可视化图表...')
    charts = {
        'hourly':        visualizer.hourly(stats),
        'monthly_trend': visualizer.monthly_trend(stats),
        'weekday_bar':   visualizer.weekday_bar(stats),
        'length_dist':   visualizer.length_dist(stats),
    }
    # 词云：有对方数据用双人词云，否则用单人
    if partner_stats is not None:
        charts['word_cloud_pair'] = visualizer.word_cloud_pair(
            stats, partner_stats, self_name, partner_name
        )
    else:
        charts['word_cloud'] = visualizer.word_cloud(stats)
    visualizer.save_all(charts, charts_dir)

    # ── Step 4: 生成分析输入 + 读取/自动生成人格结果 ─────────────────────
    personality_result: dict = {}
    partner_personality: dict = {}
    input_path = os.path.join(output_dir, 'personality_input.json')
    partner_input_path = os.path.join(output_dir, 'partner_input.json')
    generated_self_path = os.path.join(output_dir, 'personality_result_generated.json')
    generated_partner_path = os.path.join(output_dir, 'partner_result_generated.json')

    print('\n正在采样消息，准备人格分析输入...')
    ai_input = _build_ai_input(df, stats, args.sample_size)
    if ai_input is not None:
        _write_json(input_path, ai_input)
        print(f'   自己：已采样 {len(ai_input["sample_messages"])} 条消息 → {input_path}')
    else:
        print('   警告：自己消息过滤后为空，无法生成人格分析输入')

    partner_ai_input = _build_ai_input(df_partner, partner_stats, args.sample_size)
    if partner_ai_input is not None:
        _write_json(partner_input_path, partner_ai_input)
        print(f'   对方：已采样 {len(partner_ai_input["sample_messages"])} 条消息 → {partner_input_path}')
    elif df_partner is not None and len(df_partner) > 0:
        print('   警告：对方消息过滤后为空，跳过对方分析输入生成')
    else:
        print('   警告：对方消息不足，跳过对方分析输入生成')

    if args.personality_result:
        # 优先读取外部结果；若检测到乱码，则用本地生成器修复并回写
        if not os.path.exists(args.personality_result):
            print(f'❌ 找不到人格分析结果文件：{args.personality_result}')
            sys.exit(1)
        with open(args.personality_result, encoding='utf-8') as f:
            personality_result = normalize_result(_fix_emoji(json.load(f)))
        if _is_garbled_text(personality_result):
            if ai_input is None:
                print('❌ 自己的人格结果文件存在乱码，且无法从当前消息重建')
                sys.exit(1)
            personality_result = generate_result(ai_input)
            _write_json(args.personality_result, personality_result)
            print(f'检测到自己的人格结果存在乱码，已重写：{args.personality_result}')
        print(f'\n已读取自己的人格分析结果：{args.personality_result}')

        if args.partner_personality_result:
            if not os.path.exists(args.partner_personality_result):
                print(f'警告：找不到对方人格分析结果文件：{args.partner_personality_result}，将跳过对比')
            else:
                with open(args.partner_personality_result, encoding='utf-8') as f:
                    partner_personality = normalize_result(_fix_emoji(json.load(f)))
                if _is_garbled_text(partner_personality):
                    if partner_ai_input is None:
                        print('警告：对方人格结果文件存在乱码，但无法从当前消息重建，将跳过对比')
                        partner_personality = {}
                    else:
                        partner_personality = generate_result(partner_ai_input)
                        _write_json(args.partner_personality_result, partner_personality)
                        print(f'检测到对方人格结果存在乱码，已重写：{args.partner_personality_result}')
                print(f'已读取对方（{partner_name}）的人格分析结果')
        elif partner_ai_input is not None:
            partner_personality = generate_result(partner_ai_input)
            _write_json(generated_partner_path, partner_personality)
            print(f'未提供对方人格结果，已自动生成：{generated_partner_path}')

        big5 = personality_result.get('big5', {})
        if big5:
            scores = {k: v.get('score', 50) for k, v in big5.items()}
            radar = visualizer.big5_radar(scores)
            visualizer.save_all({'radar': radar}, charts_dir)

    else:
        if ai_input is not None:
            personality_result = generate_result(ai_input)
            _write_json(generated_self_path, personality_result)
            print(f'   自己：已自动生成人格结果 → {generated_self_path}')
            big5 = personality_result.get('big5', {})
            if big5:
                scores = {k: v.get('score', 50) for k, v in big5.items()}
                radar = visualizer.big5_radar(scores)
                visualizer.save_all({'radar': radar}, charts_dir)

        if partner_ai_input is not None:
            partner_personality = generate_result(partner_ai_input)
            _write_json(generated_partner_path, partner_personality)
            print(f'   对方：已自动生成人格结果 → {generated_partner_path}')

    advanced_insights = analyze_advanced_insights(
        conversation_df,
        meta=meta,
        self_name=self_name,
        partner_name=partner_name,
        ai_input=ai_input,
        partner_ai_input=partner_ai_input,
    )
    advanced_path = os.path.join(output_dir, 'advanced_insights.json')
    _write_json(advanced_path, advanced_insights)

    # ── Step 5: 生成报告 ────────────────────────────────────────────────────
    print('\n📝 正在生成 HTML 报告...')
    has_pair_wordcloud = partner_stats is not None
    report_path = report_mod.generate(
        stats, personality_result, output_dir,
        partner_stats=partner_stats,
        partner_personality=partner_personality,
        self_name=self_name,
        partner_name=partner_name,
        self_avatar_data=self_avatar_data,
        partner_avatar_data=partner_avatar_data,
        has_pair_wordcloud=has_pair_wordcloud,
        advanced_insights=advanced_insights,
    )

    json_path = os.path.join(output_dir, 'personality_raw.json')
    _write_json(json_path, personality_result)

    has_partner = bool(partner_personality)
    has_personality = bool(personality_result)
    print(f'''
{"完整对比报告已生成" if has_partner else ("完整报告已生成" if has_personality else "图表已生成，等待人格分析")}！
─────────────────────────────────
  HTML 报告：{report_path}
  图表目录： {charts_dir}
─────────────────────────────────
在浏览器中查看：
  open "{report_path}"
''')


if __name__ == '__main__':
    main()
