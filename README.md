# Weekly Training & Coaching Planner — Kleine Nummers Community

This tool automatically generates a weekly rowing training and coaching schedule for Club Klein based on athlete availability submitted through When2Meet.

The goal is to turn raw availability data into a practical weekly plan:
- which trainings will run,
- which athletes attend each session,
- and who is assigned to coach.

---

## Pipeline Overview

The planner operates in three main stages:

### 1. Candidate Training Extraction
Athlete availability is pulled from a When2Meet sheet and grouped into fixed-duration sessions (typically 60 minutes).

A session is considered feasible if:
- all time slots are consecutive,
- athletes are available for the full duration,
- and at least a minimum number of athletes can attend.

Each feasible session becomes a candidate training, along with the roster of athletes who can attend it in full.

---

### 2. Training Selection
From all feasible candidate trainings, the model selects a subset to run that best matches each athlete’s requested weekly training frequency.

The selection:
- assigns athletes approximately their requested number of sessions,
- avoids large over- or under-assignment,
- slightly prefers sessions with more athletes (efficiency),
- slightly prefers sessions where predefined rowing pairs can train together (e.g. 2x / 2-).

If an athlete requests more sessions than their availability allows, their weekly target is adjusted automatically.

---

### 3. Coaching Assignment
For each selected training, a fixed number of athletes must act as coach.

Assignments are made such that:
- coaches are available for the session,
- coaches are not simultaneously rowing,
- athletes with only one weekly session are preferably allowed to row,
- preferred rowing pairs are not split unnecessarily,
- coaching duties are distributed as evenly as possible.

The assignment runs in two passes:
1. Attempt to assign each athlete to coach at most once per week.
2. If this is infeasible, allow repeat coaching but penalize it so it is used only when necessary.

---

## Input

- When2Meet availability link  
- Athlete weekly training preferences  
- Preferred rowing pairs (optional)  
- Required number of coaches per session  
- Planning date window  

---

## Output

- Selected weekly training schedule  
- Athlete assignments per training  
- Coaching assignments per training  

---

## Notes

- The When2Meet link must be manually updated in the script each week.
- Availability is interpreted as continuous attendance over full sessions.
- The planner balances fairness across athletes with staffing requirements.
