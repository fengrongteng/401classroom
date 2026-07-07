"""
401教室使用情况 - 预约管理系统 (重构周视图版)
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import hashlib
from collections import defaultdict
from supabase import create_client, Client

ADMIN_PASSWORD = "985211"
CLASSROOMS = ["教室1", "教室2", "教室3", "教室5", "教室6", "自习室"]

TIME_SLOTS = []
current = 8.0
while current <= 21.0:
    start_hour = int(current)
    start_min = "00" if current == int(current) else "30"
    end_val = current + 2
    end_hour = int(end_val)
    end_min = "00" if end_val == int(end_val) else "30"
    TIME_SLOTS.append(f"{start_hour:02d}:{start_min}-{end_hour:02d}:{end_min}")
    current += 0.5

HALF_HOUR_SLOTS = []
current = 8.0
while current < 23.0:
    start_hour = int(current)
    start_min = "00" if current == int(current) else "30"
    end_val = current + 0.5
    end_hour = int(end_val)
    end_min = "00" if end_val == int(end_val) else "30"
    HALF_HOUR_SLOTS.append(f"{start_hour:02d}:{start_min}-{end_hour:02d}:{end_min}")
    current += 0.5

SUPABASE_URL = st.secrets.get("SUPABASE_URL", "") if hasattr(st, "secrets") else ""
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "") if hasattr(st, "secrets") else ""
SUPABASE_TABLE = st.secrets.get("SUPABASE_TABLE", "reservations") if hasattr(st, "secrets") else "reservations"

if SUPABASE_URL:
    SUPABASE_URL = SUPABASE_URL.strip().rstrip("/")
    if SUPABASE_URL.endswith("/rest/v1"):
        SUPABASE_URL = SUPABASE_URL[:-8]


def check_password(password):
    return hashlib.md5(password.encode()).hexdigest() == hashlib.md5(ADMIN_PASSWORD.encode()).hexdigest()


def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase 未配置：请在 Streamlit secrets 中设置 SUPABASE_URL 和 SUPABASE_KEY")
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        raise RuntimeError(
            f"Supabase 客户端初始化失败。请检查 SUPABASE_URL 是否为项目根地址（应类似 https://xxx.supabase.co，而不是 /rest/v1 结尾），并确认 SUPABASE_KEY 正确。原始错误：{e}"
        )


def normalize_reservations_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["日期", "时间段", "教室", "使用人", "创建时间"])
    rename_map = {
        "date": "日期",
        "time_slot": "时间段",
        "classroom": "教室",
        "person": "使用人",
        "created_at": "创建时间",
    }
    result = df.rename(columns=rename_map).copy()
    for col in ["日期", "时间段", "教室", "使用人", "创建时间"]:
        if col not in result.columns:
            result[col] = ""
    result = result[["日期", "时间段", "教室", "使用人", "创建时间"]]
    result["日期"] = result["日期"].astype(str)
    result["时间段"] = result["时间段"].astype(str)
    result["教室"] = result["教室"].astype(str)
    result["使用人"] = result["使用人"].astype(str)
    result["创建时间"] = result["创建时间"].astype(str)
    return result


def load_reservations():
    client = get_supabase_client()
    response = client.table(SUPABASE_TABLE).select("date,time_slot,classroom,person,created_at").execute()
    data = response.data or []
    return normalize_reservations_df(pd.DataFrame(data))


def get_conflicting_reservations(date, time_slot, classroom, df=None):
    if df is None:
        df = load_reservations()
    day_df = df[(df["日期"] == date) & (df["教室"] == classroom)].copy()
    if day_df.empty:
        return []
    conflicts = []
    for _, row in day_df.iterrows():
        existing_slot = row["时间段"]
        if overlaps(time_slot, existing_slot):
            conflicts.append({
                "日期": row["日期"],
                "时间段": existing_slot,
                "教室": row["教室"],
                "使用人": row["使用人"],
            })
    return conflicts


def save_reservation(date, time_slot, classroom, person):
    client = get_supabase_client()
    payload = {
        "date": date,
        "time_slot": time_slot,
        "classroom": classroom,
        "person": person,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    client.table(SUPABASE_TABLE).upsert(payload, on_conflict="date,time_slot,classroom").execute()


def format_slot_from_values(start_val, end_val):
    start_hour = int(start_val)
    start_min = "00" if start_val == int(start_val) else "30"
    end_hour = int(end_val)
    end_min = "00" if end_val == int(end_val) else "30"
    return f"{start_hour:02d}:{start_min}-{end_hour:02d}:{end_min}"


def subtract_slot(existing_slot, remove_slot):
    a_start, a_end = slot_start_value(existing_slot), slot_end_value(existing_slot)
    b_start, b_end = slot_start_value(remove_slot), slot_end_value(remove_slot)
    if b_end <= a_start or b_start >= a_end:
        return [existing_slot]
    remaining = []
    if b_start > a_start:
        remaining.append(format_slot_from_values(a_start, min(b_start, a_end)))
    if b_end < a_end:
        remaining.append(format_slot_from_values(max(b_end, a_start), a_end))
    return [x for x in remaining if slot_start_value(x) < slot_end_value(x)]


def delete_batch(to_delete):
    df = load_reservations()
    updated_rows = []
    rows_to_replace = []
    for _, row in df.iterrows():
        current_date = row["日期"]
        current_slot = row["时间段"]
        current_room = row["教室"]
        matched_remove_slots = [slot for ds, slot, room in to_delete if ds == current_date and room == current_room and overlaps(slot, current_slot)]
        if not matched_remove_slots:
            continue
        rows_to_replace.append((current_date, current_slot, current_room))
        remaining_slots = [current_slot]
        for remove_slot in matched_remove_slots:
            next_remaining = []
            for rs in remaining_slots:
                next_remaining.extend(subtract_slot(rs, remove_slot))
            remaining_slots = next_remaining
        for rs in remaining_slots:
            updated_rows.append({
                "date": row["日期"],
                "time_slot": rs,
                "classroom": row["教室"],
                "person": row["使用人"],
                "created_at": row["创建时间"],
            })

    if not rows_to_replace:
        return

    client = get_supabase_client()
    unique_rows = list(dict.fromkeys(rows_to_replace))
    for date_value, slot_value, room_value in unique_rows:
        client.table(SUPABASE_TABLE).delete().eq("date", date_value).eq("time_slot", slot_value).eq("classroom", room_value).execute()
    if updated_rows:
        client.table(SUPABASE_TABLE).insert(updated_rows).execute()


def get_week_dates(ref):
    monday = ref - timedelta(days=ref.weekday())
    return [monday + timedelta(days=i) for i in range(7)]


def get_person_colors(person):
    palette = [
        ("#BFDBFE", "#1D4ED8"),
        ("#DBEAFE", "#1E40AF"),
        ("#DDD6FE", "#5B21B6"),
        ("#E9D5FF", "#6B21A8"),
        ("#F5D0FE", "#A21CAF"),
        ("#FBCFE8", "#9D174D"),
        ("#FED7AA", "#9A3412"),
        ("#FDE68A", "#92400E"),
        ("#FEF3C7", "#B45309"),
        ("#CFFAFE", "#0F766E"),
        ("#BAE6FD", "#0369A1"),
        ("#E0E7FF", "#4338CA"),
    ]
    text = str(person)
    idx = 0
    for i, ch in enumerate(text):
        idx += (i + 1) * ord(ch)
    return palette[idx % len(palette)]


def normalize_slot_text(slot):
    text = str(slot).strip()
    text = text.replace("：", ":").replace(" – ", "-").replace("—", "-").replace("－", "-")
    text = text.replace(" ", "")
    return text


def slot_start_value(slot):
    normalized = normalize_slot_text(slot)
    start = normalized.split("-")[0]
    hour, minute = start.split(":")
    return int(hour) + (0.5 if minute == "30" else 0)


def slot_end_value(slot):
    normalized = normalize_slot_text(slot)
    end = normalized.split("-")[1]
    hour, minute = end.split(":")
    return int(hour) + (0.5 if minute == "30" else 0)


def overlaps(slot_a, slot_b):
    return slot_start_value(slot_a) < slot_end_value(slot_b) and slot_start_value(slot_b) < slot_end_value(slot_a)


def slot_to_half_hour_segments(slot):
    start_val = slot_start_value(slot)
    end_val = slot_end_value(slot)
    segments = []
    current_val = start_val
    while current_val < end_val:
        start_hour = int(current_val)
        start_min = "00" if current_val == int(current_val) else "30"
        next_val = current_val + 0.5
        end_hour = int(next_val)
        end_min = "00" if next_val == int(next_val) else "30"
        segments.append(f"{start_hour:02d}:{start_min}-{end_hour:02d}:{end_min}")
        current_val = next_val
    return segments


def find_overlapping_bookings(df, room, date_str, slot):
    day_df = df[(df["教室"] == room) & (df["日期"] == date_str)].copy()
    if day_df.empty:
        return []
    overlaps_found = []
    for _, row in day_df.iterrows():
        other_slot = row["时间段"]
        if overlaps(slot, other_slot):
            overlaps_found.append(row)
    return overlaps_found


def build_room_week_blocks(df, room, week_dates):
    week_start = week_dates[0].strftime("%Y-%m-%d")
    week_end = week_dates[-1].strftime("%Y-%m-%d")
    room_df = df[(df["教室"] == room) & (df["日期"] >= week_start) & (df["日期"] <= week_end)].copy()
    if room_df.empty:
        return [], {}, {}

    segment_owners = defaultdict(list)
    exact_person_map = defaultdict(list)
    for _, row in room_df.iterrows():
        date_str = row["日期"]
        person = row["使用人"]
        raw_slot = row["时间段"]
        for segment in slot_to_half_hour_segments(raw_slot):
            segment_owners[(date_str, segment)].append({"person": person, "raw_slot": raw_slot})
            exact_person_map[(date_str, segment, person)].append(raw_slot)

    merged = []
    covered_cells = set()
    for day in week_dates:
        date_str = day.strftime("%Y-%m-%d")
        day_index = (datetime.strptime(date_str, "%Y-%m-%d") - week_dates[0]).days
        for slot_idx, segment in enumerate(HALF_HOUR_SLOTS):
            if (date_str, segment) in covered_cells:
                continue
            owners = segment_owners.get((date_str, segment), [])
            if not owners:
                continue
            unique_persons = sorted(set(x["person"] for x in owners))
            if len(unique_persons) != 1:
                continue
            person = unique_persons[0]
            row_span = 1
            raw_slots = set(x["raw_slot"] for x in owners if x["person"] == person)
            next_idx = slot_idx + 1
            while next_idx < len(HALF_HOUR_SLOTS):
                next_segment = HALF_HOUR_SLOTS[next_idx]
                next_owners = segment_owners.get((date_str, next_segment), [])
                next_persons = sorted(set(x["person"] for x in next_owners))
                if next_persons == [person]:
                    row_span += 1
                    raw_slots.update(x["raw_slot"] for x in next_owners if x["person"] == person)
                    covered_cells.add((date_str, next_segment))
                    next_idx += 1
                else:
                    break
            covered_cells.add((date_str, segment))
            merged.append({
                "person": person,
                "slot": segment,
                "day_index": day_index,
                "row_start": slot_idx,
                "row_span": row_span,
                "conflict": False,
                "date": date_str,
                "raw_slots": sorted(raw_slots),
            })

    blocked_map = {}
    for day in week_dates:
        date_str = day.strftime("%Y-%m-%d")
        for segment in HALF_HOUR_SLOTS:
            owners = segment_owners.get((date_str, segment), [])
            if owners:
                blocked_map[(date_str, segment)] = owners

    return merged, covered_cells, blocked_map


def calc_room_stats(df, room, week_dates):
    week_start = week_dates[0].strftime("%Y-%m-%d")
    week_end = week_dates[-1].strftime("%Y-%m-%d")
    room_df = df[(df["教室"] == room) & (df["日期"] >= week_start) & (df["日期"] <= week_end)].copy()
    occupied_segments = set()
    for _, row in room_df.iterrows():
        for segment in slot_to_half_hour_segments(row["时间段"]):
            occupied_segments.add((row["日期"], segment))
    occupied = len(occupied_segments)
    total = len(week_dates) * len(HALF_HOUR_SLOTS)
    free = total - occupied
    occ_ratio = occupied / total if total else 0
    free_ratio = free / total if total else 0
    return occupied, free, occ_ratio, free_ratio


def merge_selected_half_hour_slots(selected_slots):
    normalized = sorted(set(selected_slots), key=lambda x: slot_start_value(x))
    if not normalized:
        return []
    merged = []
    current_start = slot_start_value(normalized[0])
    current_end = slot_end_value(normalized[0])
    for slot in normalized[1:]:
        start_val = slot_start_value(slot)
        end_val = slot_end_value(slot)
        if start_val == current_end:
            current_end = end_val
        else:
            start_hour = int(current_start)
            start_min = "00" if current_start == int(current_start) else "30"
            end_hour = int(current_end)
            end_min = "00" if current_end == int(current_end) else "30"
            merged.append(f"{start_hour:02d}:{start_min}-{end_hour:02d}:{end_min}")
            current_start = start_val
            current_end = end_val
    start_hour = int(current_start)
    start_min = "00" if current_start == int(current_start) else "30"
    end_hour = int(current_end)
    end_min = "00" if current_end == int(current_end) else "30"
    merged.append(f"{start_hour:02d}:{start_min}-{end_hour:02d}:{end_min}")
    return merged


def calc_slot_hours(slot):
    return slot_end_value(slot) - slot_start_value(slot)


def get_person_statistics(df, person, start_date, end_date):
    filtered = df[
        (df["使用人"] == person)
        & (df["日期"] >= start_date.strftime("%Y-%m-%d"))
        & (df["日期"] <= end_date.strftime("%Y-%m-%d"))
    ].copy()
    if filtered.empty:
        return {
            "count": 0,
            "total_hours": 0,
            "details": filtered,
        }
    filtered["课时小时数"] = filtered["时间段"].apply(calc_slot_hours)
    total_hours = filtered["课时小时数"].sum()
    return {
        "count": len(filtered),
        "total_hours": total_hours,
        "details": filtered.sort_values(["日期", "时间段", "教室"]),
    }


def render_delete_dialog():
    if not (st.session_state.show_confirm and st.session_state.to_delete):
        return
    with st.container(border=True):
        st.error("⚠️ 确认删除")
        st.write(f"确定要删除/拆分以下 {len(st.session_state.to_delete)} 个预约区间吗？")
        for ds, slot, room in st.session_state.to_delete:
            d = datetime.strptime(ds, "%Y-%m-%d")
            st.markdown(f"- **{room}** · {d.month}/{d.day}（{week_labels[d.weekday()]}） · `{slot}`")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ 确认删除", type="primary", key=f"confirm_delete_{st.session_state.confirm_source}"):
                delete_batch(st.session_state.to_delete)
                st.session_state.to_delete = []
                st.session_state.show_confirm = False
                st.session_state.confirm_source = ""
                st.success("删除成功")
                st.rerun()
        with c2:
            if st.button("❌ 取消", key=f"cancel_delete_{st.session_state.confirm_source}"):
                st.session_state.to_delete = []
                st.session_state.show_confirm = False
                st.session_state.confirm_source = ""
                st.rerun()


st.set_page_config(page_title="401教室使用情况", page_icon="🏫", layout="wide")

st.markdown("""
<style>
    section[data-testid="stSidebar"] { width: 430px !important; min-width: 430px !important; }
    .main-title { font-size: 1.7rem; font-weight: 700; text-align: center; color: #1f2937; margin: 0.2rem 0 0.8rem; }
    .legend { display: flex; justify-content: center; gap: 1rem; padding: 0.6rem; background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 8px; margin: 0.4rem 0 1rem; }
    .legend-item { display: flex; align-items: center; gap: 0.35rem; font-size: 0.82rem; color: #374151; }
    .legend-color { width: 14px; height: 14px; border-radius: 4px; }
    .stat-card { border: 1px solid #e5e7eb; border-radius: 8px; padding: 0.7rem 0.8rem; background: #ffffff; min-height: 92px; }
    .stat-title { font-size: 0.82rem; font-weight: 700; color: #374151; }
    .stat-value { font-size: 1.1rem; font-weight: 700; color: #111827; margin-top: 0.2rem; }
    .stat-sub { font-size: 0.76rem; color: #6b7280; margin-top: 0.15rem; }
    .room-panel { border: 1px solid #dbe2ea; border-radius: 10px; background: #ffffff; padding: 0.6rem; margin-bottom: 0.8rem; }
    .room-title { font-size: 0.95rem; font-weight: 700; color: #111827; margin-bottom: 0.5rem; }
    .week-grid { display: grid; grid-template-columns: 72px repeat(7, 1fr); gap: 3px; }
    .grid-head { background: #f3f4f6; color: #374151; font-size: 0.72rem; font-weight: 700; text-align: center; padding: 0.28rem 0.15rem; border-radius: 6px; }
    .grid-head-weekend { background: #fee2e2; color: #b91c1c; }
    .grid-time { background: #f9fafb; color: #4b5563; font-size: 0.66rem; font-weight: 700; text-align: center; padding: 0.2rem 0.1rem; border-radius: 6px; min-height: 28px; display: flex; align-items: center; justify-content: center; line-height: 1.05; }
    .grid-cell-empty { background: #dcfce7; border: 1px solid #86efac; border-radius: 6px; min-height: 28px; display: flex; align-items: center; justify-content: center; font-size: 0.64rem; font-weight: 700; color: #166534; line-height: 1; }
    .grid-cell-blocked { background: #e5e7eb; border: 1px solid #cbd5e1; border-radius: 6px; min-height: 28px; display: flex; align-items: center; justify-content: center; font-size: 0; color: transparent; line-height: 1; }
    .grid-cell-booked { border-radius: 6px; min-height: 28px; display: flex; align-items: center; justify-content: center; font-size: 0.64rem; font-weight: 700; text-align: center; padding: 0.1rem 0.18rem; overflow: hidden; line-height: 1; }
    .grid-cell-conflict { box-shadow: inset 0 0 0 2px #dc2626; }
    .section-title { font-size: 1rem; font-weight: 700; color: #111827; margin: 0.2rem 0 0.6rem; }
    .weekday-label { font-size: 0.8rem; font-weight: 700; text-align: center; color: #374151; margin-bottom: 0.2rem; }
    .weekday-sat { color: #b45309; }
    .weekday-sun { color: #b91c1c; }
    div[data-testid="stButton"] > button[kind="secondary"], div[data-testid="stButton"] > button[kind="primary"] { min-height: 40px; width: 100%; white-space: nowrap; font-size: 0.84rem; padding: 0.2rem 0.45rem; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

if "auth" not in st.session_state:
    st.session_state.auth = False
if "week" not in st.session_state:
    st.session_state.week = datetime.now()
if "selected_dates" not in st.session_state:
    st.session_state.selected_dates = []
if "selected_slots" not in st.session_state:
    st.session_state.selected_slots = []
if "to_delete" not in st.session_state:
    st.session_state.to_delete = []
if "show_confirm" not in st.session_state:
    st.session_state.show_confirm = False
if "selected_room_card" not in st.session_state:
    st.session_state.selected_room_card = CLASSROOMS[0]
if "confirm_source" not in st.session_state:
    st.session_state.confirm_source = ""


df = load_reservations()
week_dates = get_week_dates(st.session_state.week)
booking_dates = [week_dates[0] + timedelta(days=i) for i in range(28)]
week_labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


st.markdown('<div class="main-title">🏫 401教室使用情况</div>', unsafe_allow_html=True)

nav_left, nav1, nav2, nav3, nav_right = st.columns([2.2, 1, 1, 1, 2.2])
with nav1:
    if st.button("◀ 上周", use_container_width=True):
        st.session_state.week -= timedelta(weeks=1)
        st.rerun()
with nav2:
    if st.button("📆 本周", use_container_width=True):
        st.session_state.week = datetime.now()
        st.rerun()
with nav3:
    if st.button("下周 ▶", use_container_width=True):
        st.session_state.week += timedelta(weeks=1)
        st.rerun()

st.markdown(f"<p style='text-align:center; color:#6b7280; margin:0.1rem 0 0.8rem;'>本周：{week_dates[0].strftime('%Y.%m.%d')} - {week_dates[-1].strftime('%Y.%m.%d')}</p>", unsafe_allow_html=True)

st.markdown("<div class='legend'><div class='legend-item'><div class='legend-color' style='background:#dbeafe;'></div><span>预约色块</span></div><div class='legend-item'><div class='legend-color' style='background:#fff; border:2px solid #dc2626;'></div><span>时间冲突预警</span></div><div class='legend-item'><div class='legend-color' style='background:#f8fafc; border:1px dashed #e5e7eb;'></div><span>空闲</span></div></div>", unsafe_allow_html=True)

st.markdown("<div class='section-title'>本周概览（点击教室切换下方周视图）</div>", unsafe_allow_html=True)
stat_cols = st.columns(6)
for idx, room in enumerate(CLASSROOMS):
    occupied, free, occ_ratio, free_ratio = calc_room_stats(df, room, week_dates)
    is_selected = st.session_state.selected_room_card == room
    with stat_cols[idx]:
        st.markdown(
            f"<div class='stat-card' style='border:{'2px solid #2563eb' if is_selected else '1px solid #e5e7eb'}; background:{'#eff6ff' if is_selected else '#ffffff'};'>"
            f"<div class='stat-title'>{room}</div>"
            f"<div class='stat-value'>{occ_ratio * 100:.0f}%</div>"
            f"<div class='stat-sub'>占用 {occupied} / 空闲 {free}</div>"
            f"<div class='stat-sub'>空闲比例 {free_ratio * 100:.0f}%</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button(f"查看{room}", key=f"room_card_{room}", use_container_width=True, type="primary" if is_selected else "secondary"):
            st.session_state.selected_room_card = room
            st.rerun()

st.markdown("<div class='section-title'>预约统计</div>", unsafe_allow_html=True)
all_persons = sorted([p for p in df["使用人"].dropna().astype(str).unique().tolist() if p.strip()]) if not df.empty else []
stat_c1, stat_c2, stat_c3 = st.columns([1.2, 1, 1])
with stat_c1:
    stats_person = st.selectbox("选择预约人", all_persons, key="stats_person") if all_persons else None
with stat_c2:
    stats_start = st.date_input("开始日期", value=week_dates[0], key="stats_start")
with stat_c3:
    stats_end = st.date_input("结束日期", value=week_dates[-1], key="stats_end")

if stats_person and stats_start <= stats_end:
    stats_result = get_person_statistics(df, stats_person, stats_start, stats_end)
    sum_c1, sum_c2 = st.columns(2)
    with sum_c1:
        st.markdown(f"<div class='stat-card'><div class='stat-title'>预约条数</div><div class='stat-value'>{stats_result['count']}</div></div>", unsafe_allow_html=True)
    with sum_c2:
        st.markdown(f"<div class='stat-card'><div class='stat-title'>总预约小时数</div><div class='stat-value'>{stats_result['total_hours']:.1f}</div></div>", unsafe_allow_html=True)
    if stats_result["count"] > 0:
        show_df = stats_result["details"][["日期", "时间段", "教室", "使用人", "课时小时数"]].copy()
        st.dataframe(show_df, use_container_width=True, hide_index=True)
    else:
        st.caption("该预约人在所选日期范围内暂无预约记录")
elif stats_person:
    st.warning("开始日期不能晚于结束日期")

st.markdown("<div class='section-title'>本周视图</div>", unsafe_allow_html=True)
main_week_room = st.session_state.selected_room_card
merged_blocks, covered_cells, blocked_map = build_room_week_blocks(df, main_week_room, week_dates)
block_map = {}
for block in merged_blocks:
    block_map[(block["date"], block["slot"])] = block

st.markdown(f"<div class='room-panel'><div class='room-title'>{main_week_room}</div>", unsafe_allow_html=True)
html = ["<div class='week-grid'>"]
html.append("<div class='grid-head' style='grid-column:1; grid-row:1;'>时间</div>")
for day_idx, d in enumerate(week_dates):
    weekend_class = " grid-head-weekend" if d.weekday() in [5, 6] else ""
    html.append(
        f"<div class='grid-head{weekend_class}' style='grid-column:{day_idx + 2}; grid-row:1;'>"
        f"{week_labels[d.weekday()]}<br>{d.month}.{d.day}</div>"
    )

for slot_idx, slot in enumerate(HALF_HOUR_SLOTS):
    row_num = slot_idx + 2
    html.append(f"<div class='grid-time' style='grid-column:1; grid-row:{row_num};'>{slot}</div>")
    for day_idx in range(7):
        date_str = week_dates[day_idx].strftime("%Y-%m-%d")
        if (date_str, slot) in covered_cells and (date_str, slot) not in block_map:
            continue
        block = block_map.get((date_str, slot))
        owners = blocked_map.get((date_str, slot), [])

        if block is not None:
            bg, fg = get_person_colors(block['person'])
            tooltip = f"老师：{block['person']}&#10;教室：{main_week_room}&#10;日期：{datetime.strptime(block['date'], '%Y-%m-%d').strftime('%m.%d')}&#10;时间：{'、'.join(block['raw_slots'])}"
            html.append(
                f"<div class='grid-cell-booked' title='{tooltip}' "
                f"style='grid-column:{day_idx + 2}; grid-row:{row_num} / {row_num + block['row_span']}; background:{bg}; color:{fg};'>{block['person']}</div>"
            )
        elif owners:
            tooltip_lines = [f"{x['person']}：{x['raw_slot']}" for x in owners]
            tooltip = f"教室：{main_week_room}&#10;日期：{week_dates[day_idx].strftime('%m.%d')}&#10;该半小时不可用，关联预约：{' | '.join(tooltip_lines)}"
            html.append(
                f"<div class='grid-cell-blocked' title='{tooltip}' "
                f"style='grid-column:{day_idx + 2}; grid-row:{row_num};'></div>"
            )
        else:
            html.append(
                f"<div class='grid-cell-empty' style='grid-column:{day_idx + 2}; grid-row:{row_num};'>有空闲</div>"
            )
html.append("</div></div>")
st.markdown("".join(html), unsafe_allow_html=True)

with st.sidebar:
    st.markdown("## 🏫 预约管理")
    if not st.session_state.auth:
        st.markdown("### 🔐 登录")
        pwd = st.text_input("密码", type="password")
        if st.button("登录"):
            if check_password(pwd):
                st.session_state.auth = True
                st.success("登录成功！")
                st.rerun()
            else:
                st.error("密码错误")
    else:
        st.success("✅ 已登录")
        if st.button("退出"):
            st.session_state.auth = False
            st.rerun()

    st.markdown("---")

    if st.session_state.auth:
        st.markdown("### ➕ 批量添加预约")
        st.caption("支持连续多日、多时段、单教室批量预约")
        st.markdown("**📅 选择日期（最多15天）**")
        header_cols = st.columns(7)
        for i, header in enumerate(week_labels):
            cls = "weekday-label"
            if header == "周六":
                cls += " weekday-sat"
            if header == "周日":
                cls += " weekday-sun"
            with header_cols[i]:
                st.markdown(f"<div class='{cls}'>{header}</div>", unsafe_allow_html=True)

        for week_start in range(0, len(booking_dates), 7):
            week_slice = booking_dates[week_start:week_start + 7]
            date_cols = st.columns([1.08, 1.08, 1.08, 1.08, 1.08, 1.08, 1.08])
            for col_idx, date_item in enumerate(week_slice):
                date_key = date_item.strftime("%Y-%m-%d")
                is_selected = date_key in st.session_state.selected_dates
                with date_cols[col_idx]:
                    if st.button(f"{date_item.month}.{date_item.day}", key=f"date_btn_{date_key}", use_container_width=True, type="primary" if is_selected else "secondary"):
                        if is_selected:
                            st.session_state.selected_dates.remove(date_key)
                        elif len(st.session_state.selected_dates) < 15:
                            st.session_state.selected_dates.append(date_key)
                        else:
                            st.warning("最多只能选择15天")
                        st.rerun()

        st.markdown("**⏰ 选择时间段**")
        st.caption("按上午 / 下午 / 晚上分组展示，半小时一格，可多选；提交时自动合并连续时段")
        morning_slots, afternoon_slots, evening_slots = [], [], []
        for idx, slot in enumerate(HALF_HOUR_SLOTS):
            start_hour = int(slot.split(':')[0])
            if start_hour < 12:
                morning_slots.append((idx, slot))
            elif start_hour < 18:
                afternoon_slots.append((idx, slot))
            else:
                evening_slots.append((idx, slot))

        for group_name, group_slots in [("上午", morning_slots), ("下午", afternoon_slots), ("晚上", evening_slots)]:
            st.markdown(f"**{group_name}**")
            for row_start in range(0, len(group_slots), 3):
                row_items = group_slots[row_start:row_start + 3]
                row_cols = st.columns(3)
                for col_idx, (real_idx, slot) in enumerate(row_items):
                    with row_cols[col_idx]:
                        is_selected = real_idx in st.session_state.selected_slots
                        if st.button(slot, key=f"slot_btn_{real_idx}", use_container_width=True, type="primary" if is_selected else "secondary"):
                            if is_selected:
                                st.session_state.selected_slots.remove(real_idx)
                            else:
                                st.session_state.selected_slots.append(real_idx)
                            st.rerun()

        selected_date_values = sorted(st.session_state.selected_dates)
        selected_half_hour_values = [HALF_HOUR_SLOTS[i] for i in sorted(st.session_state.selected_slots)]
        merged_slot_values = merge_selected_half_hour_slots(selected_half_hour_values)
        days_count = len(selected_date_values)
        if days_count > 0:
            st.info(f"📅 已选 {days_count} 天 × ⏰ {len(selected_half_hour_values)} 个半小时格")
            if merged_slot_values:
                st.caption(f"提交后将自动合并为：{'，'.join(merged_slot_values)}")
        else:
            st.caption("当前未选择日期")

        with st.form("add", clear_on_submit=True):
            add_room = st.selectbox("🏫 教室", CLASSROOMS)
            add_person = st.text_input("👤 使用人")
            if st.form_submit_button("确认添加"):
                if add_person.strip() and selected_date_values and merged_slot_values:
                    df_current = load_reservations()
                    all_conflicts = []
                    for date_str in selected_date_values:
                        for s in merged_slot_values:
                            conflicts = get_conflicting_reservations(date_str, s, add_room, df_current)
                            if conflicts:
                                all_conflicts.append({
                                    "日期": date_str,
                                    "申请时间段": s,
                                    "冲突明细": conflicts,
                                })

                    if all_conflicts:
                        st.error("❌ 时间有冲突，不能预约")
                        for item in all_conflicts:
                            show_date = datetime.strptime(item["日期"], "%Y-%m-%d").strftime("%m.%d")
                            st.markdown(f"- **{show_date}** 申请时段：`{item['申请时间段']}`")
                            for c in item["冲突明细"]:
                                st.markdown(f"  - 已有预约：`{c['时间段']}` · 使用人：**{c['使用人']}**")
                    else:
                        count = 0
                        for date_str in selected_date_values:
                            for s in merged_slot_values:
                                save_reservation(date_str, s, add_room, add_person.strip())
                                count += 1
                        st.session_state.selected_dates = []
                        st.session_state.selected_slots = []
                        st.success(f"✅ 成功添加 {count} 个预约！")
                        st.rerun()
                else:
                    st.warning("请选择日期、时间段并输入姓名")

        render_delete_dialog()
        st.markdown("---")
        st.markdown("### ➖ 批量取消预约")
        st.caption("按已选日期、半小时格和教室批量删除匹配预约")
        cancel_room = st.selectbox("取消预约教室", CLASSROOMS, key="cancel_room")
        selected_cancel_slots = merge_selected_half_hour_slots([HALF_HOUR_SLOTS[i] for i in sorted(st.session_state.selected_slots)])
        if st.button("批量取消预约", use_container_width=True, type="secondary"):
            if selected_date_values and selected_cancel_slots:
                delete_targets = []
                current_df = load_reservations()
                for date_str in selected_date_values:
                    room_df = current_df[(current_df["日期"] == date_str) & (current_df["教室"] == cancel_room)]
                    for _, row in room_df.iterrows():
                        for req_slot in selected_cancel_slots:
                            if overlaps(req_slot, row["时间段"]):
                                item = (row["日期"], req_slot, row["教室"])
                                if item not in delete_targets:
                                    delete_targets.append(item)
                if delete_targets:
                    st.session_state.to_delete = delete_targets
                    st.session_state.show_confirm = True
                    st.session_state.confirm_source = "sidebar"
                    st.rerun()
                else:
                    st.warning("未找到可取消的预约")
            else:
                st.warning("请选择日期和时间段")

st.markdown("---")
with st.expander("📅 当日视图"):
    render_delete_dialog()
    selected_day = st.selectbox(
        "选择日期",
        booking_dates,
        format_func=lambda d: d.strftime('%Y年%m月%d日（') + week_labels[d.weekday()] + '）',
        key="daily_view_date",
    )
    selected_day_str = selected_day.strftime('%Y-%m-%d')
    room_cols = st.columns(6)
    for room_idx, room in enumerate(CLASSROOMS):
        with room_cols[room_idx]:
            st.markdown(f"### {room}")
            room_bookings = df[(df["教室"] == room) & (df["日期"] == selected_day_str)].sort_values(["时间段"])
            if len(room_bookings) == 0:
                st.markdown("<div class='grid-cell' style='background:#dcfce7; color:#166534;'>有空闲</div>", unsafe_allow_html=True)
            else:
                for row_idx, (_, row) in enumerate(room_bookings.iterrows()):
                    person = row["使用人"]
                    bg, fg = get_person_colors(person)
                    st.markdown(
                        f"<div class='grid-cell booked' style='background:{bg}; color:{fg}; margin-bottom:6px;'>"
                        f"<span class='person' style='color:{fg};'>{row['时间段']}</span>"
                        f"<span class='person' style='color:{fg};'>👤 {person}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    if st.button(f"删除 {row['时间段']}", key=f"delete_single_{room}_{selected_day_str}_{row_idx}", use_container_width=True):
                        st.session_state.to_delete = [(row["日期"], row["时间段"], row["教室"])]
                        st.session_state.show_confirm = True
                        st.session_state.confirm_source = f"single_{room}_{selected_day_str}_{row_idx}"
                        st.rerun()

st.markdown(f"<div style='text-align:center; color:#999; font-size:0.75rem; padding:0.5rem;'>🏫 401教室使用情况 · {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>", unsafe_allow_html=True)
