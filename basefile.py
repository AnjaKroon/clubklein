"""Club Klein when2meet helper.

Pulls latest availability, writes CSV outputs, and selects training options.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests

WHEN2MEET_URL = "https://www.when2meet.com/?34726408-3GULd"
MIN_PEOPLE = 2
SESSION_MINUTES = 60
HALF_DAY_SPLIT = time(12, 0)

# Default location (Amsterdam). Override with env vars if needed.
DEFAULT_LAT = float(os.getenv("CLUBKLEIN_LAT", "52.3676"))
DEFAULT_LON = float(os.getenv("CLUBKLEIN_LON", "4.9041"))
DEFAULT_TZ = os.getenv("CLUBKLEIN_TZ")

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Hard-coded reference data from the spreadsheet.
WHATSAPP_MAPW_COUNTS = {
    "Castelein": 0,
    "Start": 3,
    "Noordsij": 3,
    "Hospers": 2,
    "Pham": 3,
    "Snelder": 4,
    "Boulonois": 3,
    "Vijver": 3,
    "Hultermans": 0,
    "Bakker": 0,
    "Breken": 0,
    "Ferro": 3,
    "Boon": 3,
    "Thomas": 3,
}

PAIRINGS = [
    ("Pair 1", "Castelein", "Start"),
    ("Pair 2", "Pham", "Boulonois"),
    ("Pair 3", "Thomas", "Boon"),
]

# Hard-coded first-name to last-name mapping from the roster.
FIRST_NAME_TO_LAST = {
    "Brechje": "Castelein",
    "Sylke": "Start",
    "Matthieu": "Noordsij",
    "Marieke": "Hospers",
    "Elisa": "Pham",
    "Hugo": "Snelder",
    "Naomi": "Boulonois",
    "Max": "Vijver",
    "Juul": "Hultermans",
    "Simone": "Bakker",
    "Sarah": "Breken",
    "Giovanni": "Ferro",
    "Tijl": "Boon",
    "Thije": "Thomas",
}


@dataclass(frozen=True)
class Slot:
    index: int
    timestamp: int
    start: datetime
    available_count: int


@dataclass(frozen=True)
class Session:
    start: datetime
    end: datetime
    available_count: int
    slot_indices: Tuple[int, ...]


@dataclass(frozen=True)
class Training:
    training_id: int
    training_time: str
    participants: Tuple[str, ...]


def reset_results_dir() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for item in RESULTS_DIR.iterdir():
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)


def fetch_when2meet_html(url: str) -> str:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def parse_time_of_slot(html: str) -> Dict[int, int]:
    pattern = r"TimeOfSlot\[(\d+)\]\s*=\s*(\d+);"
    return {int(idx): int(ts) for idx, ts in re.findall(pattern, html)}


def parse_available_at_slot(html: str, slot_indices: Iterable[int]) -> Dict[int, List[int]]:
    available: Dict[int, List[int]] = {idx: [] for idx in slot_indices}
    pattern = r"AvailableAtSlot\[(\d+)\]\.push\((\d+)\)"
    for idx, user_id in re.findall(pattern, html):
        slot_idx = int(idx)
        if slot_idx not in available:
            continue
        available[slot_idx].append(int(user_id))
    return available


def parse_people(html: str) -> Dict[int, str]:
    id_by_index: Dict[int, int] = {}
    name_by_index: Dict[int, str] = {}
    for idx, value in re.findall(r"PeopleIDs\[(\d+)\]\s*=\s*(\d+);", html):
        id_by_index[int(idx)] = int(value)
    for idx, value in re.findall(r"PeopleNames\[(\d+)\]\s*=\s*'([^']*)';", html):
        name_by_index[int(idx)] = value.strip()
    for idx, value in re.findall(r'PeopleNames\[(\d+)\]\s*=\s*"([^"]*)";', html):
        name_by_index[int(idx)] = value.strip()
    id_to_name: Dict[int, str] = {}
    for idx, user_id in id_by_index.items():
        name = name_by_index.get(idx, "").strip()
        if name:
            id_to_name[user_id] = name
    return id_to_name


def local_tzinfo() -> Optional[datetime.tzinfo]:
    if DEFAULT_TZ:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(DEFAULT_TZ)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo


def infer_slot_length_seconds(timestamps: Sequence[int]) -> int:
    if len(timestamps) < 2:
        return SESSION_MINUTES * 60
    deltas = [b - a for a, b in zip(timestamps, timestamps[1:]) if b > a]
    if not deltas:
        return SESSION_MINUTES * 60
    return min(deltas)


def build_slots(time_map: Dict[int, int], available_map: Dict[int, List[int]], tzinfo) -> List[Slot]:
    slots: List[Slot] = []
    for idx in sorted(time_map):
        ts = time_map[idx]
        start = datetime.fromtimestamp(ts, tz=tzinfo)
        slots.append(
            Slot(
                index=idx,
                timestamp=ts,
                start=start,
                available_count=len(available_map.get(idx, [])),
            )
        )
    return slots


def iter_sessions(slots: Sequence[Slot], slot_length: int) -> List[Session]:
    if not slots:
        return []
    slots_per_session = max(1, int((SESSION_MINUTES * 60) / slot_length))
    sessions: List[Session] = []
    for i in range(0, len(slots) - slots_per_session + 1):
        group = slots[i : i + slots_per_session]
        # Ensure contiguous slots by timestamp.
        if any(
            group[j + 1].timestamp - group[j].timestamp != slot_length
            for j in range(len(group) - 1)
        ):
            continue
        min_available = min(slot.available_count for slot in group)
        if min_available < MIN_PEOPLE:
            continue
        start = group[0].start
        end = start + timedelta(minutes=SESSION_MINUTES)
        sessions.append(
            Session(
                start=start,
                end=end,
                available_count=min_available,
                slot_indices=tuple(slot.index for slot in group),
            )
        )
    return sessions


def sunrise_sunset(date_value, tzinfo) -> Tuple[datetime, datetime]:
    try:
        from astral import Observer
        from astral.sun import sun

        observer = Observer(latitude=DEFAULT_LAT, longitude=DEFAULT_LON)
        data = sun(observer, date=date_value, tzinfo=tzinfo)
        return data["sunrise"], data["sunset"]
    except Exception:
        # Fallback: rough daylight window.
        sunrise = datetime.combine(date_value, time(6, 0), tzinfo=tzinfo)
        sunset = datetime.combine(date_value, time(18, 0), tzinfo=tzinfo)
        return sunrise, sunset


def is_morning(session: Session, sunrise: datetime) -> bool:
    return session.start >= sunrise and session.start.time() < HALF_DAY_SPLIT


def is_afternoon(session: Session, sunset: datetime) -> bool:
    return session.start.time() >= HALF_DAY_SPLIT and session.end <= sunset


def choose_session(sessions: Sequence[Session], prefer: str) -> Optional[Session]:
    if not sessions:
        return None
    max_available = max(s.available_count for s in sessions)
    candidates = [s for s in sessions if s.available_count == max_available]
    if prefer == "earliest":
        return min(candidates, key=lambda s: s.start)
    if prefer == "latest":
        return max(candidates, key=lambda s: s.start)
    return candidates[0]


def write_slots_csv(slots: Sequence[Slot]) -> Path:
    output_path = RESULTS_DIR / "when2meet_slots.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["slot_index", "start_time", "available_count"])
        for slot in slots:
            writer.writerow([slot.index, slot.start.isoformat(), slot.available_count])
    return output_path


def write_sessions_csv(sessions: Sequence[Session]) -> Path:
    output_path = RESULTS_DIR / "training_options.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["start_time", "end_time", "available_count", "slot_indices"])
        for session in sessions:
            writer.writerow(
                [
                    session.start.isoformat(),
                    session.end.isoformat(),
                    session.available_count,
                    ",".join(str(idx) for idx in session.slot_indices),
                ]
            )
    return output_path


def normalize_name(name: str) -> str:
    cleaned = " ".join(name.strip().split())
    if not cleaned:
        return cleaned
    if cleaned in FIRST_NAME_TO_LAST:
        return FIRST_NAME_TO_LAST[cleaned]
    parts = cleaned.split(" ")
    if len(parts) > 1:
        return parts[-1]
    return cleaned


def session_available_names(
    session: Session, available_map: Dict[int, List[int]], id_to_name: Dict[int, str]
) -> List[str]:
    if not session.slot_indices:
        return []
    available_sets = [
        set(available_map.get(slot_idx, [])) for slot_idx in session.slot_indices
    ]
    common_ids = set.intersection(*available_sets) if available_sets else set()
    names = [normalize_name(id_to_name.get(user_id, f"ID {user_id}")) for user_id in common_ids]
    return sorted([name for name in names if name])


def format_session_label(session: Session) -> str:
    return session.start.strftime("%A %H:%M")


def prompt_date(label: str, tzinfo) -> datetime.date:
    while True:
        value = input(f"{label} (YYYY-MM-DD): ").strip()
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            print("Invalid date format. Please use YYYY-MM-DD.")


def prompt_k() -> Optional[int]:
    while True:
        value = input("Max trainings K (blank = no limit): ").strip()
        if not value:
            return None
        try:
            parsed = int(value)
        except ValueError:
            print("Invalid number. Enter an integer or leave blank.")
            continue
        if parsed < 0:
            print("K must be >= 0.")
            continue
        return parsed


def filter_sessions_by_date(
    sessions: Sequence[Session], start_date: datetime.date, end_date: datetime.date
) -> List[Session]:
    return [
        session
        for session in sessions
        if start_date <= session.start.date() <= end_date
    ]


def write_recommendations_csv(
    recommendations: Sequence[Session],
    available_map: Dict[int, List[int]],
    id_to_name: Dict[int, str],
) -> Path:
    output_path = RESULTS_DIR / "training_recommendations.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Training nummer", *[i + 1 for i in range(len(recommendations))]])
        writer.writerow(
            ["Training tijd", *[format_session_label(s) for s in recommendations]]
        )
        rosters = [
            session_available_names(session, available_map, id_to_name)
            for session in recommendations
        ]
        max_rows = max((len(roster) for roster in rosters), default=0)
        for row_idx in range(max_rows):
            row = [f"Roeier {row_idx + 1}"]
            for roster in rosters:
                row.append(roster[row_idx] if row_idx < len(roster) else "")
            writer.writerow(row)
    return output_path


def read_csv_rows(path: Path) -> List[List[str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [row for row in csv.reader(handle)]


def normalize_participant(value: str) -> Optional[str]:
    if value is None:
        return None
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        return None
    if cleaned.lower() in {"nan", "none"}:
        return None
    return cleaned


def parse_trainings_csv(path: Path) -> List[Training]:
    rows = read_csv_rows(path)
    if not rows:
        raise ValueError(f"Training CSV is empty: {path}")
    headers = [cell.strip().lower() for cell in rows[0]]
    if "training_id" in headers and ("participants" in headers or "participant" in headers):
        return parse_trainings_long(rows)
    return parse_trainings_wide(rows)


def parse_trainings_wide(rows: List[List[str]]) -> List[Training]:
    if len(rows) < 2:
        raise ValueError("Wide training CSV must include header rows.")
    training_ids = [normalize_participant(x) for x in rows[0][1:]]
    training_times = [normalize_participant(x) for x in rows[1][1:]]
    trainings: List[Training] = []
    for idx, training_id in enumerate(training_ids):
        if training_id is None:
            continue
        time_value = training_times[idx] if idx < len(training_times) else ""
        participants: List[str] = []
        for row in rows[2:]:
            if idx + 1 >= len(row):
                continue
            participant = normalize_participant(row[idx + 1])
            if participant:
                participants.append(participant)
        unique_participants = sorted(set(participants))
        trainings.append(
            Training(
                training_id=int(training_id),
                training_time=time_value or "",
                participants=tuple(unique_participants),
            )
        )
    return trainings


def parse_trainings_long(rows: List[List[str]]) -> List[Training]:
    headers = [cell.strip().lower() for cell in rows[0]]
    id_idx = headers.index("training_id")
    time_idx = headers.index("training_time") if "training_time" in headers else None
    participants_idx = (
        headers.index("participants")
        if "participants" in headers
        else headers.index("participant")
    )
    grouped: Dict[int, Dict[str, List[str]]] = {}
    for row in rows[1:]:
        if id_idx >= len(row):
            continue
        training_id = normalize_participant(row[id_idx])
        if training_id is None:
            continue
        training_time = ""
        if time_idx is not None and time_idx < len(row):
            training_time = row[time_idx].strip()
        participants_cell = row[participants_idx] if participants_idx < len(row) else ""
        participants_raw = [
            normalize_participant(part)
            for part in participants_cell.replace(";", ",").split(",")
        ]
        participants = [p for p in participants_raw if p]
        grouped.setdefault(int(training_id), {"time": training_time, "participants": []})
        grouped[int(training_id)]["participants"].extend(participants)
        if training_time:
            grouped[int(training_id)]["time"] = training_time
    trainings: List[Training] = []
    for training_id, info in grouped.items():
        unique_participants = sorted(set(info["participants"]))
        trainings.append(
            Training(
                training_id=training_id,
                training_time=info.get("time", ""),
                participants=tuple(unique_participants),
            )
        )
    return sorted(trainings, key=lambda t: t.training_id)


def parse_mapw_csv(path: Optional[Path]) -> Dict[str, float]:
    if path is None:
        return {name: float(weight) for name, weight in WHATSAPP_MAPW_COUNTS.items()}
    rows = read_csv_rows(path)
    if not rows:
        raise ValueError(f"MAPW CSV is empty: {path}")
    headers = [cell.strip().lower() for cell in rows[0]]
    name_idx = headers.index("athlete")
    mapw_idx = headers.index("mapw")
    result: Dict[str, float] = {}
    for row in rows[1:]:
        if name_idx >= len(row) or mapw_idx >= len(row):
            continue
        name = normalize_participant(row[name_idx])
        if not name:
            continue
        try:
            result[name] = float(row[mapw_idx])
        except ValueError:
            result[name] = 0.0
    return result


def parse_desired_csv(path: Optional[Path]) -> Dict[str, int]:
    if path is None:
        return {}
    rows = read_csv_rows(path)
    if not rows:
        raise ValueError(f"Desired trainings CSV is empty: {path}")
    headers = [cell.strip().lower() for cell in rows[0]]
    name_idx = headers.index("athlete")
    desired_idx = headers.index("desiredtrainingsperweek")
    result: Dict[str, int] = {}
    for row in rows[1:]:
        if name_idx >= len(row) or desired_idx >= len(row):
            continue
        name = normalize_participant(row[name_idx])
        if not name:
            continue
        try:
            result[name] = int(float(row[desired_idx]))
        except ValueError:
            result[name] = 0
    return result


def parse_pairs_csv(path: Optional[Path]) -> List[Tuple[str, str]]:
    if path is None:
        return [(pair[1], pair[2]) for pair in PAIRINGS]
    rows = read_csv_rows(path)
    if not rows:
        raise ValueError(f"Preferred pairs CSV is empty: {path}")
    headers = [cell.strip().lower() for cell in rows[0]]
    a_idx = headers.index("athletea")
    b_idx = headers.index("athleteb")
    pairs: List[Tuple[str, str]] = []
    for row in rows[1:]:
        if a_idx >= len(row) or b_idx >= len(row):
            continue
        athlete_a = normalize_participant(row[a_idx])
        athlete_b = normalize_participant(row[b_idx])
        if athlete_a and athlete_b:
            pairs.append((athlete_a, athlete_b))
    return pairs


def size_reward_value(n_participants: int, size_fn: str) -> float:
    if size_fn == "linear":
        return float(n_participants)
    return math.sqrt(n_participants)


def build_training_matrix(
    trainings: Sequence[Training],
    athletes: Sequence[str],
) -> Dict[str, Dict[int, int]]:
    matrix: Dict[str, Dict[int, int]] = {athlete: {} for athlete in athletes}
    for training in trainings:
        participants = set(training.participants)
        for athlete in athletes:
            matrix[athlete][training.training_id] = 1 if athlete in participants else 0
    return matrix


def compute_pair_count(training: Training, pairs: Sequence[Tuple[str, str]]) -> int:
    participants = set(training.participants)
    count = 0
    for athlete_a, athlete_b in pairs:
        if athlete_a in participants and athlete_b in participants:
            count += 1
    return count


def objective_cost(
    selected: Sequence[Training],
    trainings: Sequence[Training],
    athletes: Sequence[str],
    desired: Dict[str, int],
    mapw: Dict[str, float],
    pairs: Sequence[Tuple[str, str]],
    alpha_scale: float,
    delta_size: float,
    gamma_pair: float,
    size_fn: str,
) -> Tuple[float, Dict[str, Dict[str, float]]]:
    selected_ids = {training.training_id for training in selected}
    selected_lookup = {training.training_id: training for training in selected}
    u_i: Dict[str, int] = {}
    y_i: Dict[str, int] = {}
    diagnostics: Dict[str, Dict[str, float]] = {}
    for athlete in athletes:
        available_count = 0
        achieved = 0
        for training in trainings:
            if athlete in training.participants:
                available_count += 1
                if training.training_id in selected_ids:
                    achieved += 1
        u_i[athlete] = available_count
        y_i[athlete] = achieved

    athlete_penalty = 0.0
    for athlete in athletes:
        desired_count = desired.get(athlete, 0)
        effective_target = min(desired_count, u_i[athlete])
        unavoidable_deficit = max(desired_count - u_i[athlete], 0)
        shortage = max(effective_target - y_i[athlete], 0)
        overshoot = max(y_i[athlete] - effective_target, 0)
        weight = alpha_scale * mapw.get(athlete, 0.0)
        athlete_penalty += weight * (shortage + overshoot)
        diagnostics[athlete] = {
            "mapw": mapw.get(athlete, 0.0),
            "desired": desired_count,
            "available": u_i[athlete],
            "effective_target": effective_target,
            "achieved": y_i[athlete],
            "shortage": shortage,
            "overshoot": overshoot,
            "unavoidable_deficit": unavoidable_deficit,
        }

    size_reward = 0.0
    pair_reward = 0.0
    for training in selected:
        size_reward += delta_size * size_reward_value(len(training.participants), size_fn)
        pair_reward += gamma_pair * compute_pair_count(training, pairs)

    cost = athlete_penalty - size_reward - pair_reward
    return cost, diagnostics


def greedy_optimize(
    trainings: Sequence[Training],
    athletes: Sequence[str],
    desired: Dict[str, int],
    mapw: Dict[str, float],
    pairs: Sequence[Tuple[str, str]],
    alpha_scale: float,
    delta_size: float,
    gamma_pair: float,
    size_fn: str,
    k_limit: Optional[int],
) -> Tuple[List[Training], Dict[str, Dict[str, float]]]:
    selected: List[Training] = []
    remaining = list(trainings)
    best_cost, _ = objective_cost(
        selected,
        trainings,
        athletes,
        desired,
        mapw,
        pairs,
        alpha_scale,
        delta_size,
        gamma_pair,
        size_fn,
    )

    while remaining:
        best_candidate = None
        best_candidate_cost = best_cost
        for training in remaining:
            candidate_selection = selected + [training]
            cost, _ = objective_cost(
                candidate_selection,
                trainings,
                athletes,
                desired,
                mapw,
                pairs,
                alpha_scale,
                delta_size,
                gamma_pair,
                size_fn,
            )
            if best_candidate is None or cost < best_candidate_cost:
                best_candidate = training
                best_candidate_cost = cost
        if best_candidate is None:
            break
        if k_limit is not None and len(selected) >= k_limit:
            break
        if best_candidate_cost < best_cost or k_limit is not None:
            selected.append(best_candidate)
            remaining.remove(best_candidate)
            best_cost = best_candidate_cost
        else:
            break

    improved = True
    while improved:
        improved = False
        for training in list(selected):
            if k_limit is not None and len(selected) <= 1:
                continue
            candidate_selection = [t for t in selected if t.training_id != training.training_id]
            cost, _ = objective_cost(
                candidate_selection,
                trainings,
                athletes,
                desired,
                mapw,
                pairs,
                alpha_scale,
                delta_size,
                gamma_pair,
                size_fn,
            )
            if cost < best_cost:
                selected = candidate_selection
                best_cost = cost
                improved = True
                break
        if improved:
            continue
        for training_out in list(selected):
            for training_in in trainings:
                if training_in in selected:
                    continue
                candidate_selection = [
                    t for t in selected if t.training_id != training_out.training_id
                ] + [training_in]
                if k_limit is not None and len(candidate_selection) > k_limit:
                    continue
                cost, _ = objective_cost(
                    candidate_selection,
                    trainings,
                    athletes,
                    desired,
                    mapw,
                    pairs,
                    alpha_scale,
                    delta_size,
                    gamma_pair,
                    size_fn,
                )
                if cost < best_cost:
                    selected = candidate_selection
                    best_cost = cost
                    improved = True
                    break
            if improved:
                break

    final_cost, diagnostics = objective_cost(
        selected,
        trainings,
        athletes,
        desired,
        mapw,
        pairs,
        alpha_scale,
        delta_size,
        gamma_pair,
        size_fn,
    )
    _ = final_cost
    return sorted(selected, key=lambda t: t.training_id), diagnostics


def write_selected_trainings_csv(trainings: Sequence[Training]) -> Path:
    output_path = RESULTS_DIR / "selected_trainings.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["training_id", "training_time", "participants", "n_participants"])
        for training in trainings:
            participants = sorted(set(training.participants))
            writer.writerow(
                [
                    training.training_id,
                    training.training_time,
                    ";".join(participants),
                    len(participants),
                ]
            )
    return output_path


def write_diagnostics_csv(diagnostics: Dict[str, Dict[str, float]]) -> Path:
    output_path = RESULTS_DIR / "diagnostics.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "Athlete",
                "MAPW",
                "desired",
                "available",
                "effective_target",
                "achieved",
                "shortage",
                "overshoot",
                "unavoidable_deficit",
            ]
        )
        for athlete in sorted(diagnostics.keys()):
            data = diagnostics[athlete]
            writer.writerow(
                [
                    athlete,
                    data["mapw"],
                    data["desired"],
                    data["available"],
                    data["effective_target"],
                    data["achieved"],
                    data["shortage"],
                    data["overshoot"],
                    data["unavoidable_deficit"],
                ]
            )
    return output_path


def run_optimizer(args: argparse.Namespace) -> None:
    reset_results_dir()
    k_limit = prompt_k()
    if args.trainings:
        trainings_path = Path(args.trainings)
    else:
        trainings_path = RESULTS_DIR / "training_recommendations.csv"
        if not trainings_path.exists():
            print("No trainings CSV found; running when2meet step first.")
            run_when2meet(reset=False)
        if not trainings_path.exists():
            raise FileNotFoundError(
                "No trainings CSV provided and training_recommendations.csv not found."
            )
        # default trainings path
    trainings = parse_trainings_csv(trainings_path)
    if args.mapw:
        mapw = parse_mapw_csv(Path(args.mapw))
    else:
        # default MAPW values
        mapw = parse_mapw_csv(None)
    desired = parse_desired_csv(Path(args.desired) if args.desired else None)
    if not desired:
        desired = {athlete: int(round(weight)) for athlete, weight in mapw.items()}
        # default desired values
    if args.pairs:
        pairs = parse_pairs_csv(Path(args.pairs))
    else:
        # default preferred pairs
        pairs = parse_pairs_csv(None)

    athlete_names = sorted(
        set(mapw.keys()) | set(desired.keys()) | {p for t in trainings for p in t.participants}
    )
    selected, diagnostics = greedy_optimize(
        trainings=trainings,
        athletes=athlete_names,
        desired=desired,
        mapw=mapw,
        pairs=pairs,
        alpha_scale=args.alpha_scale,
        delta_size=args.delta_size,
        gamma_pair=args.gamma_pair,
        size_fn=args.size_fn,
        k_limit=k_limit,
    )
    write_selected_trainings_csv(selected)
    write_diagnostics_csv(diagnostics)


def run_when2meet(reset: bool = True) -> None:
    if reset:
        reset_results_dir()
    html = fetch_when2meet_html(WHEN2MEET_URL)
    time_map = parse_time_of_slot(html)
    if not time_map:
        raise RuntimeError("No TimeOfSlot entries found. The page format may have changed.")
    available_map = parse_available_at_slot(html, time_map.keys())
    id_to_name = parse_people(html)
    tzinfo = local_tzinfo()
    slots = build_slots(time_map, available_map, tzinfo)
    slots_sorted_by_time = sorted(slots, key=lambda s: s.timestamp)
    slot_length = infer_slot_length_seconds([s.timestamp for s in slots_sorted_by_time])
    sessions = iter_sessions(slots_sorted_by_time, slot_length)

    recommendations: List[Session] = []
    sessions_by_date: Dict[datetime.date, List[Session]] = {}
    for session in sessions:
        sessions_by_date.setdefault(session.start.date(), []).append(session)

    tzinfo = local_tzinfo()
    start_date = prompt_date("Start date", tzinfo)
    end_date = prompt_date("End date", tzinfo)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    sessions = filter_sessions_by_date(sessions, start_date, end_date)
    sessions_by_date = {}
    for session in sessions:
        sessions_by_date.setdefault(session.start.date(), []).append(session)

    for date_value, day_sessions in sorted(sessions_by_date.items()):
        sunrise, sunset = sunrise_sunset(date_value, tzinfo)
        morning = [s for s in day_sessions if is_morning(s, sunrise)]
        afternoon = [s for s in day_sessions if is_afternoon(s, sunset)]
        morning_choice = choose_session(morning, "earliest")
        afternoon_choice = choose_session(afternoon, "latest")
        if morning_choice:
            recommendations.append(morning_choice)
        if afternoon_choice:
            recommendations.append(afternoon_choice)

    write_slots_csv(slots_sorted_by_time)
    write_sessions_csv(sessions)
    write_recommendations_csv(recommendations, available_map, id_to_name)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Club Klein scheduling helper.")
    subparsers = parser.add_subparsers(dest="command")

    when2meet_parser = subparsers.add_parser(
        "when2meet", help="Fetch when2meet data and propose training options."
    )
    when2meet_parser.set_defaults(func=lambda args: run_when2meet())

    optimize_parser = subparsers.add_parser(
        "optimize", help="Select best trainings for a week from CSV inputs."
    )
    optimize_parser.add_argument(
        "--trainings",
        help="Trainings CSV path (defaults to results/training_recommendations.csv).",
    )
    optimize_parser.add_argument("--mapw", help="MAPW CSV path.")
    optimize_parser.add_argument("--desired", help="Desired trainings CSV path.")
    optimize_parser.add_argument("--pairs", help="Preferred pairs CSV path.")
    optimize_parser.add_argument("--k", type=int, help="Maximum number of trainings.")
    optimize_parser.add_argument("--alpha-scale", type=float, default=10.0)
    optimize_parser.add_argument("--delta-size", type=float, default=1.0)
    optimize_parser.add_argument("--gamma-pair", type=float, default=2.0)
    optimize_parser.add_argument("--size-fn", choices=["sqrt", "linear"], default="sqrt")
    optimize_parser.set_defaults(func=run_optimizer)

    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        run_when2meet()


if __name__ == "__main__":
    main()
