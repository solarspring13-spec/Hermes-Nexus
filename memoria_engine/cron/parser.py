#!/usr/bin/env python3
"""
cron_parser.py — 自然语言 → RRULE 转换引擎

Hermes Cron Phase 2 核心组件。
支持 5 种输入格式:
  1. 自然语言 (中文/英文) — 模式匹配 + LLM 回退
  2. 相对时间 — "in 5 minutes", "30分钟后"
  3. 间隔重复 — "every 30 minutes", "每2小时"
  4. Cron 表达式 — "0 9 * * *"
  5. ISO 8601 — "2026-05-20T14:30:00"

输出: JSON {rrule, schedule_type, human_readable, confidence, format_detected}

上游参考: nousresearch/hermes-agent cron 模块
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TZ_OFFSET = timedelta(hours=8)  # CST (China Standard Time)

WEEKDAY_CN = {
    "周一": "MO", "星期二": "TU", "周三": "WE", "周四": "TH",
    "周五": "FR", "周六": "SA", "周日": "SU",
    "星期一": "MO", "星期二": "TU", "星期三": "WE", "星期四": "TH",
    "星期五": "FR", "星期六": "SA", "星期日": "SU",
    "礼拜一": "MO", "礼拜二": "TU", "礼拜三": "WE", "礼拜四": "TH",
    "礼拜五": "FR", "礼拜六": "SA", "礼拜天": "SU",
}

WEEKDAY_EN = {
    "monday": "MO", "tuesday": "TU", "wednesday": "WE",
    "thursday": "TH", "friday": "FR", "saturday": "SA", "sunday": "SU",
    "mon": "MO", "tue": "TU", "wed": "WE", "thu": "TH",
    "fri": "FR", "sat": "SA", "sun": "SU",
}

NUMBER_CN = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "两": 2,
}

# ---------------------------------------------------------------------------
# 核心解析函数
# ---------------------------------------------------------------------------

def parse_natural_language(text: str) -> Optional[dict]:
    """
    解析自然语言输入，返回标准化 RRULE 字典。
    优先级: 中文模式 → 英文模式 → LLM 回退

    返回: None 表示无法解析（需 LLM 回退）
    """
    text = text.strip()
    text_lower = text.lower()

    # ---- 中文模式 ----

    # "每天早上/每天上午 N 点/M 分"
    m = re.match(
        r'每\s*天\s*(早上|上午|下午|晚上|中午)?\s*(\d{1,2}|[一二三四五六七八九十]+)?\s*[点时]'
        r'\s*(\d{1,2}|[一二三四五六七八九十]+)?\s*分?',
        text
    )
    if m:
        period = m.group(1) or ""
        hour_str = m.group(2)
        minute_str = m.group(3)
        hour = _parse_hour(hour_str, period) if hour_str else 9
        minute = _parse_number(minute_str) if minute_str else 0
        minute = min(max(minute, 0), 59)
        return _make_result(
            rrule=f"FREQ=DAILY;BYHOUR={hour};BYMINUTE={minute}",
            schedule_type="recurring",
            human_readable=f"每天 {hour:02d}:{minute:02d}",
            confidence=0.95,
            format_detected="natural_language_cn"
        )

    # "每天" (无具体时间，默认 09:00)
    m = re.match(r'每\s*天\s*$', text)
    if m:
        return _make_result(
            rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
            schedule_type="recurring",
            human_readable="每天 09:00",
            confidence=0.90,
            format_detected="natural_language_cn"
        )

    # "每周N / 每周一/二/三..."
    # 注意: 多日表达式 (如 "周一和周五") 需要 LLM 回退
    m = re.match(r'每\s*周\s*([一二三四五六日天1-7])(.*)', text)
    if m:
        day_str = m.group(1)
        rest = m.group(2)
        # 检测多日表达式: 剩余文本含 "和"、"与"、"、" 或第二个星期数字
        if re.search(r'[和与、,]\s*[周一二三四五六日天1-7]', rest):
            return None
        # 继续解析时间
        time_match = re.match(r'\s*(早上|上午|下午|晚上|中午)?\s*(\d{1,2})?\s*[点时]?\s*(\d{1,2})?\s*分?', rest)
        period = time_match.group(1) if time_match else ""
        hour_str = time_match.group(2) if time_match else None
        minute_str = time_match.group(3) if time_match else None
        day_map = {"1": "MO", "2": "TU", "3": "WE", "4": "TH", "5": "FR", "6": "SA", "7": "SU", "0": "SU", "日": "SU", "天": "SU"}
        day_rr = day_map.get(day_str)
        if not day_rr:
            day_rr = WEEKDAY_CN.get(f"周{day_str}", WEEKDAY_CN.get(f"星期{day_str}", "MO"))
        hour = _parse_hour(hour_str, period) if hour_str else 9
        minute = _parse_number(minute_str) if minute_str else 0
        minute = min(max(minute, 0), 59)
        day_label = {"MO": "周一", "TU": "周二", "WE": "周三", "TH": "周四", "FR": "周五", "SA": "周六", "SU": "周日"}.get(day_rr, day_rr)
        return _make_result(
            rrule=f"FREQ=WEEKLY;BYDAY={day_rr};BYHOUR={hour};BYMINUTE={minute}",
            schedule_type="recurring",
            human_readable=f"每{day_label} {hour:02d}:{minute:02d}",
            confidence=0.95,
            format_detected="natural_language_cn"
        )

    # "每N小时"
    m = re.match(r'每\s*(\d+|[一二三四五六七八九十]+)\s*(个)?\s*小?时', text)
    if m:
        n = _parse_number(m.group(1))
        return _make_result(
            rrule=f"FREQ=HOURLY;INTERVAL={n}",
            schedule_type="recurring",
            human_readable=f"每 {n} 小时",
            confidence=0.95,
            format_detected="natural_language_cn"
        )

    # "每N分钟"
    m = re.match(r'每\s*(\d+|[一二三四五六七八九十]+)\s*分', text)
    if m:
        n = _parse_number(m.group(1))
        return _make_result(
            rrule=f"FREQ=MINUTELY;INTERVAL={n}",
            schedule_type="recurring",
            human_readable=f"每 {n} 分钟",
            confidence=0.95,
            format_detected="natural_language_cn"
        )

    # "每小时"
    m = re.match(r'每\s*小?时\s*$', text)
    if m:
        return _make_result(
            rrule="FREQ=HOURLY;INTERVAL=1",
            schedule_type="recurring",
            human_readable="每小时",
            confidence=0.90,
            format_detected="natural_language_cn"
        )

    # "每月N号"
    m = re.match(r'每\s*月\s*(\d{1,2}|[一二三四五六七八九十]+)\s*[号日]\s*(早上|上午|下午|晚上)?\s*(\d{1,2})?\s*[点时]?\s*(\d{1,2})?\s*分?', text)
    if m:
        day = _parse_number(m.group(1))
        period = m.group(2) or ""
        hour_str = m.group(3)
        minute_str = m.group(4)
        hour = _parse_hour(hour_str, period) if hour_str else 9
        minute = _parse_number(minute_str) if minute_str else 0
        minute = min(max(minute, 0), 59)
        return _make_result(
            rrule=f"FREQ=MONTHLY;BYMONTHDAY={day};BYHOUR={hour};BYMINUTE={minute}",
            schedule_type="recurring",
            human_readable=f"每月 {day} 号 {hour:02d}:{minute:02d}",
            confidence=0.95,
            format_detected="natural_language_cn"
        )

    # "N分钟后" (相对时间)
    m = re.match(r'(\d+|[一二三四五六七八九十]+)\s*分?钟?\s*[后以]', text)
    if m:
        n = _parse_number(m.group(1))
        return _make_relative(minutes=n)

    # "N小时后"
    m = re.match(r'(\d+|[一二三四五六七八九十]+)\s*(个)?\s*小?时\s*[后以]', text)
    if m:
        n = _parse_number(m.group(1))
        return _make_relative(minutes=n * 60)

    # ---- 英文模式 ----

    # "every day at HH:MM"
    m = re.match(r'every\s+day\s+(at\s+)?(\d{1,2}):?(\d{2})?\s*(am|pm)?', text_lower)
    if m:
        hour = int(m.group(2))
        minute = int(m.group(3)) if m.group(3) else 0
        ampm = m.group(4)
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        return _make_result(
            rrule=f"FREQ=DAILY;BYHOUR={hour};BYMINUTE={minute}",
            schedule_type="recurring",
            human_readable=f"Daily at {hour:02d}:{minute:02d}",
            confidence=0.95,
            format_detected="natural_language_en"
        )

    # "every N minutes/hours/days/weeks/months"
    m = re.match(r'every\s+(\d+)\s*(minute|minutes|hour|hours|day|days|week|weeks|month|months)s?', text_lower)
    if m:
        n = int(m.group(1))
        unit = m.group(2).rstrip('s')
        freq_map = {"minute": "MINUTELY", "hour": "HOURLY", "day": "DAILY", "week": "WEEKLY", "month": "MONTHLY"}
        freq = freq_map.get(unit, "DAILY")
        return _make_result(
            rrule=f"FREQ={freq};INTERVAL={n}",
            schedule_type="recurring",
            human_readable=f"Every {n} {unit}(s)",
            confidence=0.95,
            format_detected="natural_language_en"
        )

    # "every weekday at HH:MM" / "every monday at HH:MM"
    for day_name, day_rr in WEEKDAY_EN.items():
        m = re.match(rf'every\s+{day_name}\s+(at\s+)?(\d{{1,2}}):?(\d{{2}})?\s*(am|pm)?', text_lower)
        if m:
            hour = int(m.group(2))
            minute = int(m.group(3)) if m.group(3) else 0
            ampm = m.group(4)
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            return _make_result(
                rrule=f"FREQ=WEEKLY;BYDAY={day_rr};BYHOUR={hour};BYMINUTE={minute}",
                schedule_type="recurring",
                human_readable=f"Every {day_name.title()} at {hour:02d}:{minute:02d}",
                confidence=0.95,
                format_detected="natural_language_en"
            )

    # "in N minutes/hours" (relative)
    m = re.match(r'in\s+(\d+)\s*(minute|minutes|min|hour|hours|hr|day|days)s?', text_lower)
    if m:
        n = int(m.group(1))
        unit = m.group(2).rstrip('s')
        if unit in ("minute", "min"):
            return _make_relative(minutes=n)
        elif unit in ("hour", "hr"):
            return _make_relative(minutes=n * 60)
        elif unit in ("day",):
            return _make_relative(minutes=n * 1440)

    # ---- 无匹配 → 需要 LLM 回退 ----
    return None


def parse_cron_expression(text: str) -> Optional[dict]:
    """解析 cron 表达式 (5 字段)"""
    text = text.strip()
    parts = text.split()
    if len(parts) != 5:
        return None
    # 简单验证
    for p in parts:
        if not re.match(r'^[\d*/,\-]+$', p):
            return None
    return _make_result(
        rrule=f"FREQ=MINUTELY;BYMINUTE={parts[0]};BYHOUR={parts[1]};BYMONTHDAY={parts[2]};BYMONTH={parts[3]};BYDAY={parts[4]}",
        schedule_type="recurring",
        human_readable=f"Cron: {' '.join(parts)}",
        confidence=0.99,
        format_detected="cron_expression"
    )


def parse_iso_datetime(text: str) -> Optional[dict]:
    """解析 ISO 8601 日期时间 (一次性)"""
    text = text.strip()
    try:
        dt = datetime.fromisoformat(text)
        return _make_result(
            rrule="",  # 一次性任务无 RRULE
            schedule_type="once",
            human_readable=dt.strftime("%Y-%m-%d %H:%M:%S"),
            scheduled_at=dt.isoformat(),
            confidence=0.99,
            format_detected="iso_8601"
        )
    except (ValueError, TypeError):
        return None


def parse_interval(text: str) -> Optional[dict]:
    """解析间隔表达式 (自动检测)"""
    text_lower = text.strip().lower()

    # "every N minutes"
    m = re.match(r'every\s+(\d+)\s*(minute|minutes|min)s?', text_lower)
    if m:
        n = int(m.group(1))
        return _make_result(
            rrule=f"FREQ=MINUTELY;INTERVAL={n}",
            schedule_type="recurring",
            human_readable=f"Every {n} minutes",
            confidence=0.98,
            format_detected="interval"
        )

    # "every N hours"
    m = re.match(r'every\s+(\d+)\s*(hour|hours|hr)s?', text_lower)
    if m:
        n = int(m.group(1))
        return _make_result(
            rrule=f"FREQ=HOURLY;INTERVAL={n}",
            schedule_type="recurring",
            human_readable=f"Every {n} hours",
            confidence=0.98,
            format_detected="interval"
        )

    return None


# ---------------------------------------------------------------------------
# 主解析入口
# ---------------------------------------------------------------------------

def parse(text: str) -> dict:
    """
    主解析入口 — 按优先级尝试所有解析器。
    返回标准 JSON 字典。
    """
    text = text.strip()
    if not text:
        return _make_error("empty input")

    # 1. Cron 表达式（5 个数字字段）
    result = parse_cron_expression(text)
    if result:
        return result

    # 2. ISO 8601 日期时间
    result = parse_iso_datetime(text)
    if result:
        return result

    # 3. 间隔表达式
    result = parse_interval(text)
    if result:
        return result

    # 4. 自然语言（中文 + 英文模式匹配）
    result = parse_natural_language(text)
    if result:
        return result

    # 5. LLM 回退 — 返回结构化 prompt
    return _make_llm_fallback(text)


def generate_llm_prompt(text: str) -> str:
    """
    生成 LLM 解析 prompt。当模式匹配失败时使用。
    要求 LLM 输出严格的 JSON 格式。
    """
    return f"""You are an RRULE parser. Convert the following natural language schedule into an RFC 5545 RRULE string and a human-readable description.

Schedule text: "{text}"

Rules:
- FREQ must be one of: SECONDLY, MINUTELY, HOURLY, DAILY, WEEKLY, MONTHLY, YEARLY
- For daily: use FREQ=DAILY;BYHOUR=H;BYMINUTE=M
- For weekly: use FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYHOUR=H;BYMINUTE=M
- For monthly: use FREQ=MONTHLY;BYMONTHDAY=D;BYHOUR=H;BYMINUTE=M
- For intervals: use FREQ=HOURLY;INTERVAL=N or FREQ=MINUTELY;INTERVAL=N
- BYHOUR and BYMINUTE should always be included for daily/weekly/monthly
- Default to 09:00 if no time specified
- Chinese time periods: 早上=08, 上午=10, 中午=12, 下午=14, 晚上=20, 深夜=23

Output ONLY this JSON (no other text):
{{"rrule": "FREQ=...", "schedule_type": "recurring", "human_readable": "...", "confidence": 0.X, "format_detected": "llm_fallback"}}"""


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _make_result(
    rrule: str = "",
    schedule_type: str = "recurring",
    human_readable: str = "",
    confidence: float = 1.0,
    format_detected: str = "",
    scheduled_at: str = "",
) -> dict:
    result = {
        "success": True,
        "rrule": rrule,
        "schedule_type": schedule_type,
        "human_readable": human_readable,
        "confidence": round(confidence, 2),
        "format_detected": format_detected,
    }
    if scheduled_at:
        result["scheduled_at"] = scheduled_at
    return result


def _make_error(reason: str) -> dict:
    return {
        "success": False,
        "error": reason,
        "rrule": "",
        "schedule_type": "",
        "human_readable": "",
        "confidence": 0.0,
        "format_detected": "error",
    }


def _make_llm_fallback(text: str) -> dict:
    return {
        "success": False,
        "error": "pattern_match_failed",
        "needs_llm": True,
        "llm_prompt": generate_llm_prompt(text),
        "rrule": "",
        "schedule_type": "",
        "human_readable": "",
        "confidence": 0.0,
        "format_detected": "llm_fallback_required",
    }


def _make_relative(minutes: int) -> dict:
    now = datetime.now(timezone(TZ_OFFSET))
    target = now + timedelta(minutes=minutes)
    return {
        "success": True,
        "rrule": "",  # 相对时间无 RRULE
        "schedule_type": "once",
        "human_readable": f"在 {minutes} 分钟后 ({target.strftime('%H:%M')})",
        "scheduled_at": target.isoformat(),
        "confidence": 0.99,
        "format_detected": "relative_time",
    }


def _parse_hour(hour_str: str, period: str) -> int:
    """解析小时数，处理中文时段偏移"""
    hour = _parse_number(hour_str) if hour_str else 9
    period = period.strip()
    if "下午" in period or "晚上" in period:
        if hour < 12:
            hour += 12
    if "中午" in period:
        hour = 12
    if "深夜" in period:
        hour = 23
    return min(max(hour, 0), 23)


def _parse_number(s: str) -> int:
    """解析中文或阿拉伯数字"""
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    if s in NUMBER_CN:
        return NUMBER_CN[s]
    # 尝试 "二十" 等组合
    if "十" in s:
        parts = s.split("十")
        if parts[0] == "":
            return 10 + (_parse_number(parts[1]) if len(parts) > 1 and parts[1] else 0)
        return _parse_number(parts[0]) * 10 + (_parse_number(parts[1]) if len(parts) > 1 and parts[1] else 0)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Hermes Cron — 自然语言 → RRULE 转换引擎"
    )
    parser.add_argument(
        "text", nargs="?", default="",
        help="自然语言调度文本（如不提供则从 stdin 读取）"
    )
    parser.add_argument(
        "--json", action="store_true", default=True,
        help="JSON 输出（默认）"
    )
    parser.add_argument(
        "--llm-prompt", action="store_true",
        help="仅输出 LLM 解析 prompt（用于回退）"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="运行内置测试用例"
    )
    args = parser.parse_args()

    if args.test:
        _run_tests()
        return

    text = args.text
    if not text:
        text = sys.stdin.read().strip()

    if args.llm_prompt:
        print(generate_llm_prompt(text))
        return

    result = parse(text)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _run_tests():
    """内置测试用例"""
    test_cases = [
        # 中文
        ("每天早上9点", {"rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "schedule_type": "recurring"}),
        ("每天下午3点30分", {"rrule": "FREQ=DAILY;BYHOUR=15;BYMINUTE=30", "schedule_type": "recurring"}),
        ("每周一早上8点", {"rrule": "FREQ=WEEKLY;BYDAY=MO;BYHOUR=8;BYMINUTE=0", "schedule_type": "recurring"}),
        ("每2小时", {"rrule": "FREQ=HOURLY;INTERVAL=2", "schedule_type": "recurring"}),
        ("每30分钟", {"rrule": "FREQ=MINUTELY;INTERVAL=30", "schedule_type": "recurring"}),
        ("每月1号", {"rrule": "FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9;BYMINUTE=0", "schedule_type": "recurring"}),
        ("5分钟后", {"schedule_type": "once", "format_detected": "relative_time"}),
        ("三小时后", {"schedule_type": "once", "format_detected": "relative_time"}),
        # 英文
        ("every day at 9:00", {"rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "schedule_type": "recurring"}),
        ("every monday at 14:30", {"rrule": "FREQ=WEEKLY;BYDAY=MO;BYHOUR=14;BYMINUTE=30"}),
        ("every 30 minutes", {"rrule": "FREQ=MINUTELY;INTERVAL=30", "schedule_type": "recurring"}),
        ("in 5 minutes", {"schedule_type": "once", "format_detected": "relative_time"}),
        # Cron
        ("0 9 * * *", {"rrule": "FREQ=MINUTELY;BYMINUTE=0;BYHOUR=9;BYMONTHDAY=*;BYMONTH=*;BYDAY=*"}),
        # ISO
        ("2026-05-20T14:30:00", {"schedule_type": "once", "format_detected": "iso_8601"}),
        # "每天" 默认 09:00
        ("每天", {"rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"}),
        # "每小时"
        ("每小时", {"rrule": "FREQ=HOURLY;INTERVAL=1"}),
    ]

    passed = 0
    failed = 0
    for text, expected in test_cases:
        result = parse(text)
        ok = True
        for key, val in expected.items():
            if result.get(key) != val:
                ok = False
                break
        if ok:
            passed += 1
            print(f"  ✅ {text:30s} → {result.get('human_readable', result.get('rrule', ''))}")
        else:
            failed += 1
            print(f"  ❌ {text:30s}")
            print(f"     Expected: {json.dumps(expected, ensure_ascii=False)}")
            print(f"     Got:      {json.dumps({k: v for k, v in result.items() if k in expected or k in ('error', 'needs_llm')}, ensure_ascii=False)}")

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(test_cases)}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
