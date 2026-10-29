"""
database.py

Thiet lap ket noi co so du lieu SQLite bang SQLAlchemy.
Cung cap:
- engine: bo may ket noi database
- SessionLocal: tao phien lam viec voi database
- Base: lop cha cho tat ca cac bang (models)
- get_db(): ham de FastAPI tu dong mo/dong ket noi
- init_db(): tao tat ca cac bang khi khoi dong server
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import DATABASE_URL

# Tao engine (bo may ket noi)
# check_same_thread=False: cho phep nhieu luong cung truy cap SQLite
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False  # Dat True de xem cac cau SQL duoc tao ra (debug)
)

# Tao SessionLocal (nha may tao phien lam viec)
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base class cho tat ca cac models
Base = declarative_base()


def get_db():
    """
    Dependency cho FastAPI.
    Tu dong tao phien lam viec va dong sau khi xong.

    Su dung:
        @app.get("/api/data")
        def get_data(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _upgrade_db() -> None:
    """Apply incremental schema changes that create_all cannot add to existing tables."""
    migrations = [
        "ALTER TABLE alert_events ADD COLUMN latency_ms REAL",
        "ALTER TABLE alert_events ADD COLUMN feedback TEXT",
        "ALTER TABLE alert_events ADD COLUMN notification_status TEXT",
        "ALTER TABLE alert_events ADD COLUMN notification_sent_at DATETIME",
        "ALTER TABLE alert_events ADD COLUMN notification_error TEXT",
        # M4: hardware device metadata
        "ALTER TABLE sensor_data ADD COLUMN device_id TEXT",
        "ALTER TABLE sensor_data ADD COLUMN firmware_version TEXT",
        "ALTER TABLE sensor_data ADD COLUMN ntp_synced INTEGER",
        "ALTER TABLE sensor_data ADD COLUMN source_type TEXT DEFAULT 'simulator'",
        # Spatial replay incidents (docs/spatial)
        "ALTER TABLE alert_events ADD COLUMN session_id TEXT",
        "ALTER TABLE alert_events ADD COLUMN source_start_ms INTEGER",
        "ALTER TABLE alert_events ADD COLUMN source_end_ms INTEGER",
        "ALTER TABLE alert_events ADD COLUMN decision_source TEXT",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(__import__("sqlalchemy").text(sql))
                conn.commit()
            except Exception:
                # Column already exists or other harmless error — skip
                conn.rollback()


def init_db():
    """
    Tao tat ca cac bang trong database.
    Goi ham nay 1 lan khi server khoi dong.

    Luu y: Phai import tat ca models TRUOC khi goi ham nay
    de SQLAlchemy biet can tao nhung bang gi.
    """
    # Import models de SQLAlchemy dang ky cac bang
    from app.models import schemas  # noqa: F401  (includes ForecastResult)

    Base.metadata.create_all(bind=engine)
    _upgrade_db()
    print(f"  Database da khoi tao tai: {engine.url}")
