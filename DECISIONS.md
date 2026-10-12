# DECISIONS.md

1. **Indexes.** I create a unique index on `employees.emp_code` so concurrent duplicate employee creation has one winner. The unique compound index on `attendance_logs.emp_code` and `date` enforces the attendance natural key. Date/employee ordering, employee/date/status lookup, status/date, late/date, and date/late/employee indexes support the main filters and analytics. I considered indexing every field separately but rejected that because it adds write and storage cost without supporting the compound query patterns as well.

2. **Punch-in race.** The API attempts the insert directly. MongoDB's unique compound index decides which concurrent request wins; the successful insert returns 201, and requests that hit the duplicate-key constraint return 409. A pre-check alone is not treated as a concurrency guarantee.

3. **Ties.** The leaderboard uses competition ranking: equal total late minutes share a rank, and the next rank is skipped. The `limit` is applied to rank values, not the number of rows, so all employees tied at the cutoff rank are included.

4. **Headcount.** The department summary starts from employees whose `joined_on` is on or before the month's end, then attaches attendance data. Employees with no attendance logs are therefore still counted.

5. **100x scale.** I would move trend gap-filling and more of the analytics calculations into MongoDB aggregation pipelines, then measure query plans and latency on representative data before adding further indexes.
