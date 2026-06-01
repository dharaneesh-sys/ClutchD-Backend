web: cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT
worker: cd backend && celery -A app.tasks.worker.celery_app worker --loglevel=info
