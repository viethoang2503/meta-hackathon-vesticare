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
        "ALTER TABLE alert_events ADD COLUMN spatial_state TEXT",
        "ALTER TABLE alert_events ADD COLUMN wearer_deadline_at DATETIME",
        "ALTER TABLE alert_events ADD COLUMN caregiver_deadline_at DATETIME",
        # A day-5 spatial_analyses table keeps incident_id NOT NULL; advisory analyses need a fresh table.
        "ALTER TABLE spatial_analyses ADD COLUMN advisory_id INTEGER",
        # MVP 2 live sessions
        "ALTER TABLE spatial_sessions ADD COLUMN kind TEXT NOT NULL DEFAULT 'replay'",
        "ALTER TABLE spatial_sessions ADD COLUMN patient_id INTEGER",
        "ALTER TABLE spatial_sessions ADD COLUMN device_id TEXT",
        "ALTER TABLE spatial_sessions ADD COLUMN config_snapshot TEXT",
        "ALTER TABLE spatial_sessions ADD COLUMN live_stats TEXT",
        "ALTER TABLE spatial_sessions ADD COLUMN last_sample_at DATETIME",
        "ALTER TABLE alert_events ADD COLUMN alert_reason TEXT",
        "ALTER TABLE alert_events ADD COLUMN first_notify_at DATETIME",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(__import__("sqlalchemy").text(sql))
                conn.commit()
            except Exception:
                # Column already exists or other harmless error — skip
                conn.rollback()
    from sqlalchemy import text

    from app.models.schemas import SpatialAction, SpatialSession

    with engine.connect() as conn:
        # A live session has no scenario: MVP 1 created scenario_id NOT NULL.
        if any(c[1] == "scenario_id" and c[3] for c in conn.execute(text("PRAGMA table_info(spatial_sessions)"))):
            _rebuild(conn, SpatialSession.__table__)
        # MVP 2 logs many self-reports per incident and keys one-off actions instead.
        ddl = conn.execute(text("SELECT sql FROM sqlite_master WHERE name = 'spatial_actions'")).scalar() or ""
        if "uq_action_once" in ddl:
            _rebuild(conn, SpatialAction.__table__)


def _rebuild(conn, table) -> None:
    """SQLite cannot drop a constraint in place, so the table is rebuilt with SQLite's
    create-copy-drop-rename procedure. Other tables reference it by name, which the rename
    keeps. Columns the new table lacks are dropped; new ones start null."""
    from sqlalchemy import text
    from sqlalchemy.schema import CreateIndex, CreateTable

    name = table.name
    old = {c[1] for c in conn.execute(text(f"PRAGMA table_info({name})"))}
    names = ", ".join(c.name for c in table.columns if c.name in old)
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    ddl = str(CreateTable(table).compile(engine))
    conn.execute(text(ddl.replace(f"CREATE TABLE {name}", f"CREATE TABLE {name}_new", 1)))
    conn.execute(text(f"INSERT INTO {name}_new ({names}) SELECT {names} FROM {name}"))
    conn.execute(text(f"DROP TABLE {name}"))
    conn.execute(text(f"ALTER TABLE {name}_new RENAME TO {name}"))
    for index in table.indexes:
        conn.execute(text(str(CreateIndex(index).compile(engine)).replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS", 1)))
    conn.commit()


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
