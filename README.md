# taibah-university-schedule-maker

Parses your university's subjects table HTML and finds every conflict-free
timetable combination that gives you a target number of off days per week.

---

## Requirements

- Python 3.8 or newer
- The subjects table HTML file exported from your university's course portal

Dependencies (`flask`, `beautifulsoup4`)

```
pip install flask beautifulsoup4
```
---

## Setup

1. Download or export the subjects table HTML from your university portal.
2. Place it in the same folder as `run.py`.
3. Run:

```
python run.py
```

The browser will open automatically at `http://127.0.0.1:5050`.

Alternatively, pass the HTML file path as an argument:

```
python run.py path/to/SubjectsTable.html
```

---

## Usage

1. Enter your subject codes separated by commas (e.g. `CS112, MATH320, EE222`).
2. Set the number of off days you want per week (0–5).
3. Click **Find Schedules**.
4. Browse results — each card shows a weekly grid and a section detail table.
5. Click **Load 20 More Schedules** to paginate through additional options.
