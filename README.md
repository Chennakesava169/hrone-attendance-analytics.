# Employee Attendance & Analytics API

FastAPI + MongoDB candidate assignment. The HTTP contract is in `openapi.yaml`; stored field types and sample documents are in `DATA_MODEL.md`.

## Windows PowerShell setup

Extract this ZIP and open PowerShell in the `candidate_kit` folder (the folder containing `app`, `requirements.txt`, and `sample_seed.py`). If an old virtual environment exists and you see errors recreating it, close the server first and remove only the `.venv` folder before recreating it.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If `.env` already exists, do not overwrite it; check that it contains `MONGO_URI` and `MONGO_DB`. Environment variables override values in `.env`. Keep `.env` private and never commit it.

Make sure MongoDB 6.0+ is running locally, or set `MONGO_URI` to your MongoDB Atlas connection string in `.env`.

## Run the API

```powershell
python -m py_compile app/main.py
uvicorn app.main:app --port 8000 --reload
```

Open `http://127.0.0.1:8000/docs` and call `GET /health`. A 503 response means the API cannot connect to MongoDB.

## Optional sample data

```powershell
python sample_seed.py
```

**Warning:** the seed script clears the `employees` and `attendance_logs` collections in the database selected by your environment. Use it only with a disposable development database.

## Before submission

Compare every endpoint with `openapi.yaml`, run the edge cases in the assignment, and confirm the explain endpoint's query plans on the seeded large dataset. `REVIEW.md` and `DECISIONS.md` are drafts to read and adjust so they accurately reflect your own review. Publish the required files to a public Git repository; do not commit `.env`, credentials, or a Dockerfile.
