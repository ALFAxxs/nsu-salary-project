# Employee Salary Notification System

Multi-branch, secure, production-ready platform that imports monthly payroll from
Excel and delivers each employee **only their own** salary via a Telegram bot.
HR/accounting drive everything from a Bootstrap web admin panel.

Built with Django 5.2 LTS, DRF, PostgreSQL, Celery + Redis, and aiogram 3.

---

## 1. Architecture

```
        WEB ADMIN PANEL (HR / Accounting)
                  |
            DJANGO BACKEND  --- DRF API (/api/, scoped by role)
             /         \
       PostgreSQL      Redis --- Celery worker (batch notifications, retry)
             |                         |
             |                    Telegram Bot API
             |                         |
        Salary data  ------>  TELEGRAM BOT (aiogram, separate process)
                                       |
                                   EMPLOYEES (self-service, own data only)
```

Three surfaces, one database:
- **Web Admin Panel** — HR/accounting: import, preview, confirm, send, reports.
- **Telegram Bot** — employees: link account, view own current/past salary.
- **DRF API** — same role-scoped data programmatically.

Bot runs as its **own process** (long polling) — see `deploy/salary-bot.service`.

## 2. Data model (ERD)

```
OrganizationUnit (HEAD_OFFICE | BRANCH, no hard-coded IDs)
   1───* User          (SUPER_ADMIN / HEAD_OFFICE_ADMIN / BRANCH_ADMIN / ACCOUNTANT / HR)
   1───* Employee      (normalized_phone = Excel↔Telegram join key; telegram_id unique)
              1───* Salary   (unique current per employee+year+month; revisions kept)
                        1───* TelegramMessage  (unique per salary+employee → idempotent)
OrganizationUnit 1───* SalaryImport (status lifecycle; validation payload cached)
                 1───1 ColumnMapping (per-branch Excel headers)
AuditLog (every sensitive action; no salary amounts stored)
```

## 3. Security & data isolation (the core requirement)

All scoping lives in **`apps/accounts/permissions.py`** and is applied through
selectors used by **both** web views and the API:

- `scope_employees / scope_salaries / scope_imports / scope_messages` filter every
  queryset by the user's `organization_unit`.
- Branch users get `accessible_unit_ids() == [their unit]`; head office / super
  admin get `None` (= all units).
- **ID tampering is blocked**: object lookups run against the scoped queryset, so
  `/employees/<other-branch-id>/` returns **404**, and `/api/employees/<id>/`
  is not found. Covered by tests in `tests/test_core.py::BranchIsolationTests`.
- Upload path also calls `assert_can_access_unit()` so a branch admin cannot
  upload for another branch even by tampering the form.

Other hardening (prod settings): HTTPS redirect, HSTS, secure/HTTP-only cookies,
CSRF, XFO=DENY, login throttling (DRF), file MIME/extension/size validation,
password hashing, session lifetime. Secrets come only from `.env`.

## 4. Import workflow (strict, spec §31)

```
Upload Excel → Validate (no DB writes) → Preview (green/yellow/red)
→ Confirm → Commit to DB (revisions) → Prepare notifications
→ "Send notifications" → Celery dispatch → per-message status
```

Validation catches: missing columns, empty rows, duplicate employee, bad phone,
unknown employee, **another branch's employee**, non-numeric/negative salary,
formula cells. Errors are downloadable as an Excel report; a template is
downloadable too.

## 5. Bulk data-loading commands (terminal, not the web Excel upload)

One-time/ongoing HR setup — loading branches and the employee registry in
bulk. Run from the terminal (`python manage.py <command> file.xlsx`), not the
web UI's monthly payroll upload. Safe to re-run: a matched row is updated in
place, an unmatched one is created, nothing is ever deleted. Add `--dry-run`
to preview without writing to the database.

### `import_branches` — load/update branches

```bash
python manage.py import_branches path/to/filiallar.xlsx
python manage.py import_branches path/to/filiallar.xlsx --dry-run
```

| Column | Required? | Notes |
|---|---|---|
| `Filial_nomi` | **majburiy** | branch name |
| `type` | ixtiyoriy | `branch` (default) or `head_office` |
| `is_active` | ixtiyoriy | TRUE/FALSE, blank defaults to TRUE |

### `import_employees` — load/update the HR employee registry

```bash
python manage.py import_employees path/to/xodimlar.xlsx
python manage.py import_employees path/to/xodimlar.xlsx --dry-run
```

| Column | Required? | Notes |
|---|---|---|
| `JShShIR` | ikkitadan kamida bittasi (JShShIR yoki Telefon) **majburiy** | 14 digits |
| `Telefon raqamlari` | ikkitadan kamida bittasi (JShShIR yoki Telefon) **majburiy** | |
| `To'liq ism` | **majburiy** | full name |
| `Filial nomi` | **majburiy** | must already exist — run `import_branches` first |
| `Passport seria va raqami` | ixtiyoriy | |
| `Tug'ilgan sana` | ixtiyoriy | date |
| `Jinsi` | ixtiyoriy | "Erkak"/"Ayol" |
| `Bo'lim` | ixtiyoriy | department |
| `Lavozim` | ixtiyoriy | position |
| `Shartnoma turi` | ixtiyoriy | contract type |

Identity match: phone first, then JSHSHIR — same dual-key rule the payroll
importer uses (`apps/imports/validators.py`).

## 6. Notifications (spec §29, §30, §42)

- Never sent synchronously — Celery fans out one task per message, throttled to
  `TELEGRAM_SEND_RATE_LIMIT`/sec.
- **Idempotent**: one `TelegramMessage` per (salary, employee); pressing "Send"
  twice does not duplicate. "Resend" explicitly re-queues one.
- Employees with no `telegram_id` are marked `TELEGRAM_NOT_CONNECTED` and never
  dispatched (a bot cannot message a user who hasn't pressed /start).
- Retry policy: transient errors (429/5xx/network) retried up to
  `TELEGRAM_MAX_ATTEMPTS`; permanent (403 blocked) → `BLOCKED`/`FAILED`.

## 7. Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # dev uses SQLite automatically if no POSTGRES_PASSWORD
python manage.py migrate
python manage.py seed_demo    # head office + 15 branches + employees + admins
python manage.py runserver
```

Seeded logins (passwords in `apps/telegram_bot/management/commands/seed_demo.py`):
`superadmin`, `hqadmin`, `branch5`.

Run the bot locally (needs a real token in `.env`):
```bash
python manage.py run_bot
```

Run tests:
```bash
python manage.py test tests
```

## 8. Production deployment (Ubuntu + Nginx + Gunicorn)

```bash
# 1. System packages
sudo apt install python3-venv postgresql redis nginx

# 2. App
sudo mkdir -p /opt/salary_system && cd /opt/salary_system
# (copy project here)
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env    # fill in real secrets, DEBUG=False, ALLOWED_HOSTS

# 3. DB + static
./.venv/bin/python manage.py migrate --settings=config.settings.prod
./.venv/bin/python manage.py collectstatic --noinput --settings=config.settings.prod
./.venv/bin/python manage.py createsuperuser --settings=config.settings.prod

# 4. Services (copy unit files from deploy/)
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now salary-web salary-worker salary-beat salary-bot

# 5. Nginx + TLS
sudo cp deploy/nginx.conf /etc/nginx/sites-available/salary
sudo ln -s /etc/nginx/sites-available/salary /etc/nginx/sites-enabled/
sudo certbot --nginx -d example.com -d www.example.com
sudo systemctl restart nginx
```

Four long-running processes: **web** (gunicorn), **worker** (celery),
**beat** (celery scheduler), **bot** (aiogram). All defined in `deploy/`.

## 9. Project layout

```
config/         settings (base/dev/prod), celery, wsgi/asgi, urls
apps/
  accounts/     User + roles + permissions (scoping) + admin management
  organizations/ OrganizationUnit (head office + branches)
  employees/    Employee + phone normalization + Telegram linking service
  salaries/     Salary model (revisions, JSON components) + selectors
  imports/      SalaryImport + Excel validator + importer + template/error report
  notifications/ TelegramMessage + service + Celery send tasks
  reports/      scoped dashboard/report aggregates
  audit/        AuditLog + middleware + signals
  telegram_bot/ aiogram bot + data layer + run_bot / seed_demo commands
  api/          DRF serializers + scoped viewsets
templates/      Bootstrap 5 web UI
deploy/         systemd units + nginx config
tests/          test suite (branch isolation is mandatory)
```

## 10. Extensibility

The `Salary.components` JSON field and service-layer design allow adding bonus,
tax, pension, overtime, PDF payslips, SMS/email channels, attendance, etc.
without reworking the core. New branches need no code change — just create an
`OrganizationUnit`.
