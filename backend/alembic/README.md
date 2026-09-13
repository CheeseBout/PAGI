# Alembic migrations

The app auto-creates the schema on startup (`SQLModel.metadata.create_all`), so a
fresh install needs nothing here. Use Alembic when you change `app/db/models.py`
and need to evolve an existing database:

```bash
alembic revision --autogenerate -m "add column x"
alembic upgrade head
```
