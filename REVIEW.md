# REVIEW.md

The starter had several correctness and reliability defects. These are the main ones found while reviewing it against `openapi.yaml` and `DATA_MODEL.md`.

| # | Where (starter) | What is wrong | How you'd notice it | How it was addressed in the revised implementation |
|---|---|---|---|---|
| 1 | `health()` | Returned `{"status":"ok"}` without checking MongoDB. | Stop MongoDB and call `/health`; it still returned 200. | Ping MongoDB and return 503 when the ping fails. |
| 2 | `create_employee()` | Duplicate check followed by insert was race-prone; no unique index guaranteed one winner. | Send concurrent creates with the same `emp_code`. | Create a unique `emp_code` index at startup and convert duplicate-key errors to 409. |
| 3 | `list_employees()` | Used `skip(page * page_size)` and counted all employees, not the filtered set. | Page 1 starts after the first page; filtered `total` is wrong. | Use `(page - 1) * page_size` and count with the same filter. |
| 4 | `punch_in()` | Did not handle unknown employees or validate status/timestamps; treated epoch milliseconds as local time and used the UTC/local date instead of the IST shift date. | Try an unknown `emp_code`, invalid status, seconds, or an overnight shift. | Validate request data, use UTC-aware instants, calculate the IST attendance date, and return the contract status codes. |
| 5 | Time helper functions | Late/overtime calculations did not correctly handle grace, overtime threshold, overnight shifts, and half-up rounding. | Test the exact 10-minute grace boundary, 30-minute overtime boundary, and shifts crossing midnight. | Centralize derived calculations with IST-aware shift boundaries and decimal half-up rounding. |
| 6 | Attendance operations | The starter lacked the complete punch-out, regularization history, attendance filtering, monthly analytics, department summary, leaderboard, trend, and explain behavior required by the contract. | Compare available routes with `openapi.yaml`. | Add the missing routes and their response shapes. |
| 7 | MongoDB access | No indexes supported the natural keys, filtered attendance lists, or common analytics filters. | Inspect indexes or run explain on a large collection. | Create idempotent indexes for employee code, attendance natural key, date sorting, status, late minutes, and employee/date access. |

The original starter's use of `emp_code` as the public employee identifier and its choice to store calendar dates as `YYYY-MM-DD` strings are correct and were retained. MongoDB `_id` remains internal and is not returned by the API.

**Verification note:** syntax compilation, route/path comparison against the supplied OpenAPI file, and basic timestamp/rounding checks were run in the preparation environment. A live MongoDB integration test and the assignment's full hidden edge-case suite have not been run here; run them before submission.
