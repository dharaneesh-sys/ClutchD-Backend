# Partner billing rollout

Everything is inert until `BILLING_ENABLED=true` on the API (default false).
Money is integer paise. The ledger is accounting only — there is no payout rail.

## Behaviour
- **Incentive:** 5% of `service_amount` (500 bps, admin-editable, global / per garage / per mechanic).
  Posted once per job (`incentive:{job_id}`) after payment capture (verify, webhook, cash).
- **Rent:** garages only. Default ₹499/month (admin config; optional per-garage override).
  Invoices are issued by the daily beat task `billing.issue_monthly_rent` for the
  calendar month in Asia/Kolkata. No auto-debit: provider submits a payment reference →
  `payment_pending` → admin confirms (`paid`) or waives (reason required).
- **Onboarding:** profile → garage_details → services → location → kyc_review → done.

## Endpoints
Provider: `GET /providers/ledger`, `/providers/rent/invoices`, `POST /providers/rent/invoices/{id}/pay`,
`GET /providers/incentives`, `GET|PATCH /providers/onboarding`.
Admin: `GET|PUT /admin/rent/config`, `GET|POST /admin/rent/invoices`, `POST .../{id}/confirm|waive`,
`GET|PUT /admin/incentives/rules`.

## Rollout order
1. Deploy code with `BILLING_ENABLED=false`. Back up the DB first.
2. `cd backend && alembic upgrade heads` (revision `f7a8b9c0d1e2`, merges the two old heads).
3. Smoke-test admin endpoints, then set `BILLING_ENABLED=true` and restart API + Celery beat.
4. Build the app with `NEXT_PUBLIC_BILLING_ENABLED=true` only when the UI ships.

## Rollback
- Flag off + restart: stops new postings immediately, data kept.
- Schema: `alembic downgrade e6f7a8b9c4d2` — **not** `-1` (ambiguous at the merge point).
  Drops billing tables and `garages.rent_amount_paise`; ledger data is lost, so export first.

## Production accounts
`scripts/bootstrap_db.py` no longer creates sample/admin accounts; an admin is created only when
`BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` are both set. Previously seeded accounts may
still exist in production with known passwords — rotate or disable them.

## Rate-limit fix (ship first)
`app/core/limiter.py` keys limits per user (JWT subject), else the last `X-Forwarded-For` hop.
Previously all devices behind the funnel shared one bucket, locking everyone out with 429s.
Deploy: merge to main, then `./deploy-backend.sh` on the server.
