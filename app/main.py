import os

import re

import calendar

from datetime import date, datetime, time, timedelta, timezone

from decimal import Decimal, ROUND_HALF_UP

from typing import Optional

from zoneinfo import ZoneInfo



from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Query

from pydantic import BaseModel, ConfigDict, StrictInt, field_validator

from pymongo import MongoClient, ASCENDING, DESCENDING

from pymongo.errors import DuplicateKeyError, PyMongoError



# ---------------------------------------------------------

# Configuration

# ---------------------------------------------------------



load_dotenv()



IST = ZoneInfo("Asia/Kolkata")

UTC = timezone.utc



client = MongoClient(

    os.getenv("MONGO_URI", "mongodb://localhost:27017"),

    serverSelectionTimeoutMS=3000,

    tz_aware=True,

)



db = client[os.getenv("MONGO_DB", "attendance_db")]

employees = db["employees"]

attendance = db["attendance_logs"]



app = FastAPI(

    title="Employee Attendance & Analytics API",

    version="2.0.0",

)





# ---------------------------------------------------------

# General helpers

# ---------------------------------------------------------



def now_utc():

    return datetime.now(UTC).replace(microsecond=0)





def to_ms(value):

    if value is None:

        return None



    if value.tzinfo is None:

        value = value.replace(tzinfo=UTC)



    return int(value.timestamp() * 1000)





def from_ms(value):

    if isinstance(value, bool) or not isinstance(value, int):

        raise ValueError("Timestamp must be an integer in milliseconds")



    if value < 100_000_000_000:

        raise ValueError("Timestamp must use epoch milliseconds")

    if value > 4_102_444_800_000:

        raise ValueError("Timestamp is out of range")



    try:

        result = datetime.fromtimestamp(value / 1000, tz=UTC)

    except (OverflowError, OSError, ValueError):

        raise ValueError("Timestamp is out of range")



    return result.replace(microsecond=0)





def ensure_timestamp(value):

    try:

        return from_ms(value)

    except ValueError as exc:

        raise HTTPException(status_code=422, detail=str(exc))





def round_half_up(value, digits=2):

    quantum = Decimal("1").scaleb(-digits)

    return float(

        Decimal(str(value)).quantize(

            quantum,

            rounding=ROUND_HALF_UP,

        )

    )





def parse_date(value):

    try:

        result = date.fromisoformat(value)



        if result.isoformat() != value:

            raise ValueError



        return result

    except (TypeError, ValueError):

        raise HTTPException(422, "Date must be YYYY-MM-DD")





def parse_month(value):

    try:

        result = datetime.strptime(value, "%Y-%m").date()



        if result.strftime("%Y-%m") != value:

            raise ValueError



        return result.year, result.month

    except (TypeError, ValueError):

        raise HTTPException(422, "Month must be YYYY-MM")





def month_bounds(month):

    year, mon = parse_month(month)



    start = date(year, mon, 1)

    end = date(year, mon, calendar.monthrange(year, mon)[1])



    return start, end





def working_day(value):

    return value.weekday() < 5





def working_days(start, end):

    count = 0

    current = start



    while current <= end:

        if working_day(current):

            count += 1



        current += timedelta(days=1)



    return count





def present_status(status):

    return status in ("PRESENT", "WFH", "ON_DUTY")





def public_employee(doc):

    if not doc:

        return None



    return {

        "emp_code": doc["emp_code"],

        "name": doc["name"],

        "email": doc.get("email", ""),

        "department": doc["department"],

        "joined_on": doc["joined_on"],

        "shift_start": doc["shift_start"],

        "shift_end": doc["shift_end"],

        "created_at": to_ms(doc["created_at"]),

    }





def public_attendance(doc):

    if not doc:

        return None



    history = []



    for entry in doc.get("history", []):

        changes = {}



        for field, change in entry.get("changes", {}).items():

            old = change.get("from")

            new = change.get("to")



            if isinstance(old, datetime):

                old = to_ms(old)



            if isinstance(new, datetime):

                new = to_ms(new)



            changes[field] = {

                "from": old,

                "to": new,

            }



        history.append({

            "at": to_ms(entry["at"]),

            "by": entry["by"],

            "reason": entry["reason"],

            "changes": changes,

        })



    return {

        "emp_code": doc["emp_code"],

        "date": doc["date"],

        "status": doc["status"],

        "punch_in": to_ms(doc.get("punch_in")),

        "punch_out": to_ms(doc.get("punch_out")),

        "work_hours": doc.get("work_hours"),

        "late_minutes": doc.get("late_minutes", 0),

        "overtime_minutes": doc.get("overtime_minutes", 0),

        "half_day": doc.get("half_day", False),

        "history": history,

    }





def require_employee(emp_code):

    employee = employees.find_one({"emp_code": emp_code})



    if not employee:

        raise HTTPException(404, "Employee not found")



    return employee





def require_attendance(emp_code, record_date):

    record = attendance.find_one({

        "emp_code": emp_code,

        "date": record_date,

    })



    if not record:

        raise HTTPException(404, "Attendance record not found")



    return record





def attendance_date(punch_in, employee):

    local = punch_in.astimezone(IST)



    start_time = time.fromisoformat(employee["shift_start"])

    end_time = time.fromisoformat(employee["shift_end"])



    if end_time <= start_time and local.time() < end_time:

        return (local.date() - timedelta(days=1)).isoformat()



    return local.date().isoformat()





def shift_datetimes(record_date, employee):

    day = parse_date(record_date)



    start_time = time.fromisoformat(employee["shift_start"])

    end_time = time.fromisoformat(employee["shift_end"])



    start = datetime.combine(day, start_time, tzinfo=IST)



    end_day = (

        day + timedelta(days=1)

        if end_time <= start_time

        else day

    )



    end = datetime.combine(end_day, end_time, tzinfo=IST)



    return start.astimezone(UTC), end.astimezone(UTC)





def calculate_derived(record, employee):

    punch_in_time = record.get("punch_in")

    punch_out_time = record.get("punch_out")



    work_hours = None

    late_minutes = 0

    overtime_minutes = 0



    if punch_in_time:

        shift_start, shift_end = shift_datetimes(

            record["date"],

            employee,

        )



        late_seconds = (

            punch_in_time - shift_start

        ).total_seconds()



        if late_seconds > 600:

            late_minutes = max(0, int(late_seconds // 60))



        if punch_out_time:

            duration = (

                punch_out_time - punch_in_time

            ).total_seconds()



            work_hours = round_half_up(duration / 3600, 2)



            overtime_seconds = (

                punch_out_time - shift_end

            ).total_seconds()



            if overtime_seconds >= 1800:

                overtime_minutes = int(overtime_seconds // 60)



    return {

        "work_hours": work_hours,

        "late_minutes": late_minutes,

        "overtime_minutes": overtime_minutes,

        "half_day": (

            work_hours is not None and work_hours < 4.5

        ),

    }





def recalculate(record, employee):

    record.update(calculate_derived(record, employee))

    return record





def attendance_filter(date_from=None, date_to=None, emp_code=None):

    query = {}



    if date_from or date_to:

        query["date"] = {}



        if date_from:

            query["date"]["$gte"] = date_from



        if date_to:

            query["date"]["$lte"] = date_to



    if emp_code:

        query["emp_code"] = emp_code



    return query





# ---------------------------------------------------------

# Request models

# ---------------------------------------------------------



class EmployeeIn(BaseModel):

    model_config = ConfigDict(extra="ignore")



    emp_code: str

    name: str

    email: str

    department: str

    joined_on: str

    shift_start: str = "09:30"

    shift_end: str = "18:30"



    @field_validator("emp_code")

    @classmethod

    def validate_emp_code(cls, value):

        if not re.fullmatch(r"EMP\d{4,6}", value):

            raise ValueError(

                "emp_code must match EMP followed by 4–6 digits"

            )



        return value



    @field_validator("email")

    @classmethod

    def validate_email(cls, value):

        if len(value) > 120 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):

            raise ValueError("email must be a valid email address")

        return value



    @field_validator("joined_on")

    @classmethod

    def validate_joined_on(cls, value):

        parse_date(value)

        return value



    @field_validator("shift_start", "shift_end")

    @classmethod

    def validate_shift_time(cls, value):

        try:

            parsed = time.fromisoformat(value)

        except ValueError:

            raise ValueError("Shift time must be HH:MM")



        if parsed.second or parsed.microsecond:

            raise ValueError("Shift time must be HH:MM")



        return value





class PunchInIn(BaseModel):

    model_config = ConfigDict(extra="ignore")



    emp_code: str

    punched_at: Optional[StrictInt] = None

    status: str = "PRESENT"



    @field_validator("punched_at")

    @classmethod

    def validate_punch_time(cls, value):

        ensure_timestamp(value)

        return value



    @field_validator("status")

    @classmethod

    def validate_status(cls, value):

        if value not in ("PRESENT", "WFH", "ON_DUTY"):

            raise ValueError("Invalid punch-in status")



        return value





class PunchOutIn(BaseModel):

    model_config = ConfigDict(extra="ignore")



    emp_code: str

    punched_at: Optional[StrictInt] = None



    @field_validator("punched_at")

    @classmethod

    def validate_punch_time(cls, value):

        ensure_timestamp(value)

        return value





class RegularizeIn(BaseModel):

    model_config = ConfigDict(extra="ignore")



    status: Optional[str] = None

    punch_in: Optional[StrictInt] = None

    punch_out: Optional[StrictInt] = None

    regularized_by: str

    reason: str



    @field_validator("punch_in", "punch_out")

    @classmethod

    def validate_times(cls, value):

        if value is not None:

            ensure_timestamp(value)



        return value



    @field_validator("status")

    @classmethod

    def validate_status(cls, value):

        if value is not None and value not in (

            "PRESENT",

            "WFH",

            "ON_DUTY",

            "ABSENT",

            "LEAVE",

        ):

            raise ValueError("Invalid attendance status")



        return value





# ---------------------------------------------------------

# Startup and health

# ---------------------------------------------------------



@app.on_event("startup")

def create_indexes():

    employees.create_index(

        [("emp_code", ASCENDING)],

        unique=True,

    )



    employees.create_index([

        ("department", ASCENDING),

        ("joined_on", ASCENDING),

    ])



    attendance.create_index(

        [("emp_code", ASCENDING), ("date", ASCENDING)],

        unique=True,

    )



    attendance.create_index([

        ("date", DESCENDING),

        ("emp_code", ASCENDING),

    ])



    attendance.create_index([

        ("status", ASCENDING),

        ("date", ASCENDING),

    ])



    attendance.create_index([

        ("late_minutes", DESCENDING),

        ("date", ASCENDING),

    ])

    attendance.create_index([
        ("date", ASCENDING),
        ("late_minutes", ASCENDING),
        ("emp_code", ASCENDING),
    ])

    attendance.create_index([
        ("emp_code", ASCENDING),
        ("date", ASCENDING),
        ("status", ASCENDING),
    ])





@app.get("/health")

def health():

    try:

        db.command("ping")

        return {"status": "ok"}

    except PyMongoError:

        raise HTTPException(503, "MongoDB unavailable")





# ---------------------------------------------------------

# Employee endpoints

# ---------------------------------------------------------



@app.post("/employees", status_code=201)

def create_employee(payload: EmployeeIn):

    if payload.shift_start == payload.shift_end:

        raise HTTPException(

            422,

            "Shift start and end cannot be equal",

        )



    document = {

        "emp_code": payload.emp_code,

        "name": payload.name,

        "email": payload.email,

        "department": payload.department,

        "joined_on": payload.joined_on,

        "shift_start": payload.shift_start,

        "shift_end": payload.shift_end,

        "created_at": now_utc(),

    }



    try:

        employees.insert_one(document)

    except DuplicateKeyError:

        raise HTTPException(409, "Employee already exists")



    return public_employee(document)





@app.get("/employees")

def list_employees(

    department: Optional[str] = None,

    page: int = Query(1, ge=1),

    page_size: int = Query(20, ge=1, le=100),

):

    query = {}



    if department:

        query["department"] = department



    total = employees.count_documents(query)



    records = employees.find(query).sort(

        "emp_code",

        ASCENDING,

    ).skip(

        (page - 1) * page_size

    ).limit(page_size)



    return {

        "items": [public_employee(item) for item in records],

        "page": page,

        "page_size": page_size,

        "total": total,

    }





@app.get("/employees/{emp_code}")

def get_employee(emp_code: str):

    return public_employee(require_employee(emp_code))





# ---------------------------------------------------------

# Attendance endpoints

# ---------------------------------------------------------



@app.post("/attendance/punch-in", status_code=201)

def punch_in(payload: PunchInIn):

    employee = require_employee(payload.emp_code)

    punched_at = ensure_timestamp(payload.punched_at if payload.punched_at is not None else to_ms(now_utc()))

    record_date = attendance_date(punched_at, employee)



    document = {

        "emp_code": payload.emp_code,

        "date": record_date,

        "status": payload.status,

        "punch_in": punched_at,

        "punch_out": None,

        "history": [],

    }



    recalculate(document, employee)



    try:

        attendance.insert_one(document)

    except DuplicateKeyError:

        raise HTTPException(

            409,

            "Attendance already exists for this employee and date",

        )



    return public_attendance(document)





@app.post("/attendance/punch-out")

def punch_out(payload: PunchOutIn):

    require_employee(payload.emp_code)

    punched_at = ensure_timestamp(payload.punched_at if payload.punched_at is not None else to_ms(now_utc()))



    record = attendance.find_one(

        {

            "emp_code": payload.emp_code,

            "punch_in": {"$lte": punched_at},

        },

        sort=[("punch_in", DESCENDING)],

    )



    if not record:

        raise HTTPException(404, "No matching punch-in found")



    if record.get("punch_out") is not None:

        raise HTTPException(409, "Already punched out")



    duration = (

        punched_at - record["punch_in"]

    ).total_seconds()



    if duration <= 0 or duration > 86400:

        raise HTTPException(422, "Invalid punch-out time")



    employee = require_employee(payload.emp_code)



    record["punch_out"] = punched_at

    recalculate(record, employee)



    result = attendance.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": record["punch_out"],
            "work_hours": record["work_hours"],
            "late_minutes": record["late_minutes"],
            "overtime_minutes": record["overtime_minutes"],
            "half_day": record["half_day"],
        }},
    )
    if result.modified_count != 1:
        raise HTTPException(409, "Already punched out")



    return public_attendance(record)





@app.get("/attendance")

def list_attendance(

    emp_code: Optional[str] = None,

    date_from: Optional[str] = None,

    date_to: Optional[str] = None,

    status: Optional[str] = None,

    page: int = Query(1, ge=1),

    page_size: int = Query(20, ge=1, le=100),

):

    if date_from:

        parse_date(date_from)



    if date_to:

        parse_date(date_to)



    if date_from and date_to and date_from > date_to:

        raise HTTPException(422, "date_from cannot be after date_to")



    query = attendance_filter(date_from, date_to, emp_code)
    if status is not None:
        if status not in ("PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"):
            raise HTTPException(422, "Invalid status")
        query["status"] = status
    total = attendance.count_documents(query)



    records = attendance.find(query).sort([

        ("date", DESCENDING),

        ("emp_code", ASCENDING),

    ]).skip(

        (page - 1) * page_size

    ).limit(page_size)



    return {

        "items": [public_attendance(item) for item in records],

        "page": page,

        "page_size": page_size,

        "total": total,

    }





@app.get("/attendance/{emp_code}/{date}")

def get_attendance(emp_code: str, date: str):

    record_date = date
    parse_date(record_date)

    return public_attendance(

        require_attendance(emp_code, record_date)

    )





@app.patch("/attendance/{emp_code}/{date}")

def regularize_attendance(

    emp_code: str,

    date: str,

    payload: RegularizeIn,

):

    record_date = date
    employee = require_employee(emp_code)

    parse_date(record_date)



    record = require_attendance(emp_code, record_date)



    updates = payload.model_dump(

        exclude_unset=True,

        exclude={"regularized_by", "reason"},

    )



    for field in ("punch_in", "punch_out"):

        if field in updates and updates[field] is not None:

            updates[field] = ensure_timestamp(updates[field])



    new_status = updates.get("status", record["status"])



    if new_status in ("ABSENT", "LEAVE"):

        if ("punch_in" in updates and updates["punch_in"] is not None) or ("punch_out" in updates and updates["punch_out"] is not None):
            raise HTTPException(422, "ABSENT and LEAVE cannot have punch times")

        updates["punch_in"] = None

        updates["punch_out"] = None



    elif present_status(new_status):

        new_punch_in = updates.get(

            "punch_in",

            record.get("punch_in"),

        )

        new_punch_out = updates.get(

            "punch_out",

            record.get("punch_out"),

        )



        if new_punch_in is None:

            raise HTTPException(

                422,

                "Presence statuses require punch_in",

            )



        if attendance_date(new_punch_in, employee) != record_date:

            raise HTTPException(

                422,

                "Punch-in must belong to the attendance date",

            )



        if new_punch_out is not None:

            duration = (

                new_punch_out - new_punch_in

            ).total_seconds()



            if duration <= 0 or duration > 86400:

                raise HTTPException(422, "Invalid punch-out time")



    changes = {}



    for field, value in updates.items():

        old_value = record.get(field)



        if old_value != value:

            changes[field] = {

                "from": old_value,

                "to": value,

            }



    if not changes:

        raise HTTPException(422, "No changes supplied")



    history_changes = {}



    for field, change in changes.items():

        old_value = change["from"]

        new_value = change["to"]



        if field in ("punch_in", "punch_out"):

            old_value = to_ms(old_value)

            new_value = to_ms(new_value)



        history_changes[field] = {

            "from": old_value,

            "to": new_value,

        }



    old_derived = {key: record.get(key) for key in ("work_hours", "late_minutes", "overtime_minutes", "half_day")}
    record.update(updates)
    recalculate(record, employee)
    for key in ("work_hours", "late_minutes", "overtime_minutes", "half_day"):
        if old_derived.get(key) != record.get(key):
            history_changes[key] = {"from": old_derived.get(key), "to": record.get(key)}

    record.setdefault("history", []).append({
        "at": now_utc(),
        "by": payload.regularized_by,
        "reason": payload.reason,
        "changes": history_changes,
    })



    original = require_attendance(emp_code, record_date)
    result = attendance.update_one(
        {"_id": record["_id"], "history": original.get("history", [])},
        {"$set": {
            "status": record["status"],
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out"),
            "work_hours": record["work_hours"],
            "late_minutes": record["late_minutes"],
            "overtime_minutes": record["overtime_minutes"],
            "half_day": record["half_day"],
            "history": record["history"],
        }},
    )
    if result.modified_count != 1:
        raise HTTPException(409, "Attendance record changed concurrently; retry")



    return public_attendance(record)





# ---------------------------------------------------------

# Employee monthly analytics

# ---------------------------------------------------------



@app.get("/analytics/employees/{emp_code}/monthly")

def employee_monthly(emp_code: str, month: str):

    employee = require_employee(emp_code)

    start, end = month_bounds(month)



    joined = parse_date(employee["joined_on"])

    effective_start = max(start, joined)



    logs = list(attendance.find({

        "emp_code": emp_code,

        "date": {

            "$gte": effective_start.isoformat(),

            "$lte": end.isoformat(),

        },

    }))



    present_days = sum(

        (0.5 if item.get("half_day") else 1.0)
        for item in logs
        if present_status(item["status"]) and working_day(parse_date(item["date"]))

    )



    leave_days = sum(

        1 for item in logs

        if item["status"] == "LEAVE"

    )



    late_count = sum(

        1 for item in logs

        if item.get("late_minutes", 0) > 0

    )



    total_working_days = (

        working_days(effective_start, end)

        if effective_start <= end

        else 0

    )



    attendance_pct = (

        round_half_up(

            present_days / total_working_days * 100,

            2,

        )

        if total_working_days

        else None

    )



    return {

        "emp_code": emp_code,

        "month": month,

        "working_days": total_working_days,

        "present_days": present_days,

        "leave_days": leave_days,

        "late_count": late_count,

        "total_late_minutes": sum(item.get("late_minutes", 0) for item in logs),

        "total_overtime_minutes": sum(item.get("overtime_minutes", 0) for item in logs),

        "attendance_pct": attendance_pct,

    }





# ---------------------------------------------------------

# Department summary

# ---------------------------------------------------------



@app.get("/analytics/departments/summary")

def department_summary(

    month: str,

    department: Optional[str] = None,

):

    start, end = month_bounds(month)



    employee_query = {

        "joined_on": {"$lte": end.isoformat()}

    }



    if department:

        employee_query["department"] = department



    employee_list = list(employees.find(employee_query))

    departments = {}



    for employee in employee_list:

        dept = employee["department"]



        if dept not in departments:

            departments[dept] = {

                "department": dept,

                "headcount": 0,

                "present_days": 0.0,

                "work_hours": [],

                "late_count": 0,

                "total_late_minutes": 0,

                "leave_count": 0,

                "on_duty_count": 0,

            }



        departments[dept]["headcount"] += 1



    logs = attendance.find({

        "date": {

            "$gte": start.isoformat(),

            "$lte": end.isoformat(),

        }

    })



    employee_map = {

        item["emp_code"]: item

        for item in employee_list

    }



    for log in logs:

        employee = employee_map.get(log["emp_code"])



        if not employee:

            continue



        data = departments[employee["department"]]



        if present_status(log["status"]) and working_day(parse_date(log["date"])):
            data["present_days"] += 0.5 if log.get("half_day") else 1.0

        if (

            log.get("work_hours") is not None

            and present_status(log["status"])

        ):

            data["work_hours"].append(log["work_hours"])



        if log.get("late_minutes", 0) > 0:

            data["late_count"] += 1

            data["total_late_minutes"] += log["late_minutes"]



        if log["status"] == "LEAVE":

            data["leave_count"] += 1



        if log["status"] == "ON_DUTY":

            data["on_duty_count"] += 1



    result = []



    for dept in sorted(departments):

        data = departments[dept]

        hours = data.pop("work_hours")



        data["avg_work_hours"] = (

            round_half_up(sum(hours) / len(hours), 2)

            if hours

            else None

        )



        result.append(data)



    return {

        "month": month,

        "items": result,

    }





# ---------------------------------------------------------

# Late leaderboard

# ---------------------------------------------------------



@app.get("/analytics/leaderboard/late")

def late_leaderboard(

    month: str,

    limit: int = Query(10, ge=1, le=50),

    department: Optional[str] = None,

):

    start, end = month_bounds(month)



    query = {

        "date": {

            "$gte": start.isoformat(),

            "$lte": end.isoformat(),

        },

        "late_minutes": {"$gt": 0},

    }



    pipeline = [

        {"$match": query},

        {

            "$group": {

                "_id": "$emp_code",

                "late_count": {"$sum": 1},

                "total_late_minutes": {

                    "$sum": "$late_minutes"

                },

            }

        },

        {

            "$lookup": {

                "from": "employees",

                "localField": "_id",

                "foreignField": "emp_code",

                "as": "employee",

            }

        },

        {"$unwind": "$employee"},

    ]



    if department:

        pipeline.append({

            "$match": {

                "employee.department": department

            }

        })



    pipeline.extend([

        {

            "$project": {

                "_id": 0,

                "emp_code": "$_id",

                "name": "$employee.name",

                "department": "$employee.department",

                "late_count": 1,

                "total_late_minutes": 1,

            }

        },

        {

            "$sort": {

                "total_late_minutes": -1,

                "emp_code": 1,

            }

        },

    ])



    results = list(attendance.aggregate(pipeline))



    previous_minutes = None

    rank = 0



    for index, item in enumerate(results, start=1):

        minutes = item["total_late_minutes"]



        if minutes != previous_minutes:

            rank = index

            previous_minutes = minutes



        item["rank"] = rank



    return {

        "month": month,

        "items": [

            item for item in results

            if item["rank"] <= limit

        ],

    }





# ---------------------------------------------------------

# Department trend

# ---------------------------------------------------------



@app.get("/analytics/departments/{department}/trend")

def department_trend(

    department: str,

    from_date: str = Query(..., alias="from"),

    to_date: str = Query(..., alias="to"),

):

    start = parse_date(from_date)

    end = parse_date(to_date)



    if end < start:

        raise HTTPException(

            422,

            "'to' must be on or after 'from'",

        )



    if (end - start).days + 1 > 92:

        raise HTTPException(

            422,

            "Date range cannot exceed 92 days",

        )



    if not employees.find_one({"department": department}):

        raise HTTPException(404, "Department not found")



    dept_employees = list(employees.find({

        "department": department,

        "joined_on": {"$lte": end.isoformat()},

    }))



    emp_codes = [

        item["emp_code"] for item in dept_employees

    ]



    joined_by_code = {

        item["emp_code"]: parse_date(item["joined_on"])

        for item in dept_employees

    }



    logs = list(attendance.find({

        "emp_code": {"$in": emp_codes},

        "date": {

            "$gte": start.isoformat(),

            "$lte": end.isoformat(),

        },

    }))



    logs_by_date = {}



    for log in logs:

        logs_by_date.setdefault(log["date"], []).append(log)



    rows = []

    current = start



    while current <= end:

        day_string = current.isoformat()



        headcount = sum(

            1 for joined in joined_by_code.values()

            if joined <= current

        )



        day_logs = logs_by_date.get(day_string, [])



        present_count = sum(

            0.5 if item.get("half_day") else 1

            for item in day_logs

            if present_status(item["status"])

        )



        late_count = sum(

            1 for item in day_logs

            if item.get("late_minutes", 0) > 0

        )



        is_working = working_day(current)

        rate = None



        if is_working and headcount > 0:

            rate = round_half_up(

                present_count / headcount * 100,

                4,

            )



        rows.append({

            "date": day_string,

            "is_working_day": is_working,

            "headcount": headcount,

            "present_count": present_count,

            "late_count": late_count,

            "attendance_rate": rate,

            "moving_avg_7d": None,

        })



        current += timedelta(days=1)



    for index, row in enumerate(rows):

        rates = [

            item["attendance_rate"]

            for item in rows[max(0, index - 6):index + 1]

            if item["attendance_rate"] is not None

        ]



        if rates:

            row["moving_avg_7d"] = round_half_up(

                sum(rates) / len(rates),

                4,

            )



    return {

        "department": department,

        "items": rows,

    }





# ---------------------------------------------------------

# Query explanation endpoint

# ---------------------------------------------------------



@app.get("/admin/explain/{endpoint}")

def explain(

    endpoint: str,

    month: Optional[str] = None,

    department: Optional[str] = None,

    emp_code: Optional[str] = None,

    date_from: Optional[str] = None,

    date_to: Optional[str] = None,

    from_date: Optional[str] = Query(None, alias="from"),

    to_date: Optional[str] = Query(None, alias="to"),

    status: Optional[str] = None,

    page: int = Query(1, ge=1),

    page_size: int = Query(20, ge=1, le=100),

):

    if endpoint == "attendance_list":

        query = attendance_filter(

            date_from,

            date_to,

            emp_code,

        )
        if status is not None:
            if status not in ("PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"):
                raise HTTPException(422, "Invalid status")
            query["status"] = status


        explanation = attendance.find(query).sort([

            ("date", DESCENDING),

            ("emp_code", ASCENDING),

        ]).skip(

            (page - 1) * page_size

        ).limit(page_size).explain()



    elif endpoint == "employee_monthly":

        if not month or not emp_code:

            raise HTTPException(

                422,

                "month and emp_code are required",

            )



        start, end = month_bounds(month)



        query = {

            "emp_code": emp_code,

            "date": {

                "$gte": start.isoformat(),

                "$lte": end.isoformat(),

            },

        }



        explanation = attendance.find(query).explain()



    elif endpoint in (

        "department_summary",

        "late_leaderboard",

    ):

        if not month:

            raise HTTPException(422, "month is required")



        start, end = month_bounds(month)



        query = {

            "date": {

                "$gte": start.isoformat(),

                "$lte": end.isoformat(),

            }

        }



        if endpoint == "late_leaderboard":

            query["late_minutes"] = {"$gt": 0}



        explanation = attendance.find(query).explain()



    elif endpoint == "department_trend":

        if not department or not from_date or not to_date:

            raise HTTPException(

                422,

                "department, from and to are required",

            )



        parse_date(from_date)

        parse_date(to_date)



        query = {

            "date": {

                "$gte": from_date,

                "$lte": to_date,

            }

        }



        explanation = attendance.find(query).explain()



    else:

        raise HTTPException(422, "Unknown endpoint")



    collection_name = "attendance_logs"
    return {

        "endpoint": endpoint,

        "collection": collection_name,

        "explain": explanation,

    }