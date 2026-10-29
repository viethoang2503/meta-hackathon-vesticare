"""
schemas.py

Dinh nghia cau truc cac bang trong database bang SQLAlchemy ORM.
Moi lop (class) tuong ung voi 1 bang. Moi thuoc tinh (attribute) tuong ung voi 1 cot.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class Patient(Base):
    """Bang thong tin benh nhan"""
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    age = Column(Integer)
    weight = Column(Float)  # kg
    height = Column(Float)  # cm
    emergency_contact_name = Column(String)
    emergency_contact_phone = Column(String)

    # Mối quan hệ với các bảng khác (1 bệnh nhân có nhiều dữ liệu)
    sensor_data = relationship("SensorData", back_populates="patient")
    alerts = relationship("AlertEvent", back_populates="patient")
    daily_logs = relationship("DailyLog", back_populates="patient")
    forecasts = relationship("ForecastResult", back_populates="patient")
    rehab_sessions = relationship("RehabSession", back_populates="patient")


class SensorData(Base):
    """Bang luu du lieu cam bien tho theo thoi gian"""
    __tablename__ = "sensor_data"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Du lieu gia toc (Accelerometer - don vi G)
    ax = Column(Float)
    ay = Column(Float)
    az = Column(Float)
    
    # Du lieu con quay (Gyroscope - don vi deg/s)
    gx = Column(Float)
    gy = Column(Float)
    gz = Column(Float)
    
    # Signal Vector Magnitude (Dac trung quan trong nhat)
    svm = Column(Float)

    # Hardware metadata (nullable — simulator khong gui)
    device_id        = Column(String, nullable=True)
    firmware_version = Column(String, nullable=True)
    ntp_synced       = Column(Boolean, nullable=True)
    source_type      = Column(String, nullable=True)  # "hardware" | "simulator"

    patient = relationship("Patient", back_populates="sensor_data")


class AlertEvent(Base):
    """Bang luu tru cac canh bao khi phat hien Nga hoac Lao dao"""
    __tablename__ = "alert_events"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    
    # "FALL" (Nga) hoac "VESTIBULAR_INSTABILITY" (Mat thang bang; ban cu ghi "SWAY")
    alert_type = Column(String, index=True)
    
    # Confidence tu model ML (vd: 0.95 = 95% chac chan)
    confidence = Column(Float)
    
    # Gia tri SVM lon nhat ghi nhan duoc trong luc su kien xay ra
    peak_svm = Column(Float)
    
    # Nguoi cham soc da xac nhan chua? (False = Chua xem, True = Da xem)
    is_acknowledged = Column(Boolean, default=False)
    
    # Thoi diem xac nhan
    acknowledged_at = Column(DateTime, nullable=True)
    
    # Ghi chu cua nguoi xac nhan (vd: "Canh bao gia", hoac "Da do cu day")
    notes = Column(Text, nullable=True)

    # Do tre phat hien (ms) - thoi gian tu khi sensor gui data den khi canh bao duoc tao
    latency_ms = Column(Float, nullable=True)

    # Phan hoi cua nguoi dung: "valid" (su kien that) hoac "false" (canh bao gia)
    feedback = Column(String, nullable=True)

    # Trang thai gui thong bao Telegram: pending / sent / failed / skipped
    notification_status = Column(String, nullable=True)
    notification_sent_at = Column(DateTime, nullable=True)
    notification_error = Column(String, nullable=True)

    # Spatial replay incidents (docs/spatial). Null on legacy sensor-route alerts.
    session_id = Column(String, ForeignKey("spatial_sessions.id"), nullable=True, index=True)
    source_start_ms = Column(Integer, nullable=True)
    source_end_ms = Column(Integer, nullable=True)
    decision_source = Column(String, nullable=True)  # "model" | "svm_rule"

    patient = relationship("Patient", back_populates="alerts")


class SpatialSession(Base):
    """One Start of a replay scenario. Status: Running | Stopped | Interrupted."""
    __tablename__ = "spatial_sessions"

    id = Column(String, primary_key=True)
    scenario_id = Column(String, nullable=False)
    status = Column(String, nullable=False, index=True)
    end_reason = Column(String, nullable=True)  # user_stop | timeout | session_interrupted
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    source_offset_ms = Column(Integer, nullable=False, default=0)
    recording_ended = Column(Boolean, nullable=False, default=False)
    model_source = Column(String, nullable=False)


class SpatialAction(Base):
    """Append-only log of what the policy did: notifications, acks, self-reports, advisories."""
    __tablename__ = "spatial_actions"
    __table_args__ = (UniqueConstraint("incident_id", "kind", "target", name="uq_action_once"),)

    id = Column(Integer, primary_key=True)
    session_id = Column(String, ForeignKey("spatial_sessions.id"), nullable=False, index=True)
    incident_id = Column(Integer, ForeignKey("alert_events.id"), nullable=True)
    kind = Column(String, nullable=False)  # notify | escalate | ack | self_report | advisory
    target = Column(String, nullable=True)  # contact_1 | contact_2 | zone id
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)


class DailyLog(Base):
    """Nhat ky hang ngay cua benh nhan (Phuc vu cho Du bao - Sprint 5)"""
    __tablename__ = "daily_logs"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    date = Column(DateTime, default=datetime.utcnow)  # Ngay ghi nhan
    
    sleep_hours = Column(Float)
    stress_level = Column(Integer)  # 1-10
    medication_taken = Column(Boolean, default=False)
    
    # Trieu chung chong mat (Tu danh gia: 0 = Khong co, 1 = Nhe, 2 = Vua, 3 = Nang)
    vertigo_symptoms = Column(Integer, default=0)
    
    notes = Column(Text, nullable=True)
    
    patient = relationship("Patient", back_populates="daily_logs")


class RehabSession(Base):
    """
    [PROTOTYPE] Phase 8.2 — Vestibular rehabilitation exercise session.

    Stores sensor-derived metrics and heuristic scores per session.
    Not clinically validated — for research and demo only.
    """
    __tablename__ = "rehab_sessions"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), index=True)
    exercise_type = Column(String)          # head_turns | tandem_stance | gaze_stabilization | single_leg_stance
    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    duration_seconds = Column(Float)

    # Sensor-derived metrics (summary provided by client)
    mean_svm = Column(Float)
    peak_svm = Column(Float)
    sway_count = Column(Integer, default=0)

    # Heuristic scores (0–100, higher = better)
    sway_score = Column(Integer)
    balance_score = Column(Integer)

    completion_rate = Column(Float)         # 0.0–1.0
    notes = Column(Text, nullable=True)
    therapist_rating = Column(Integer, nullable=True)  # 1–5

    patient = relationship("Patient", back_populates="rehab_sessions")


class ForecastResult(Base):
    """
    [PROTOTYPE] Ket qua du bao nguy co nga cua benh nhan.

    Nguon: rule-based scoring v1. Chua duoc xac thuc lam sang.
    Chi su dung cho muc dich nghien cuu va demo.
    """
    __tablename__ = "forecast_results"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    generated_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Horizon du bao: "next_24h"
    forecast_horizon = Column(String, default="next_24h")

    # Diem nguy co tong hop: 0-100
    risk_score = Column(Integer)

    # Muc do nguy co: LOW | MEDIUM | HIGH
    risk_level = Column(String)

    # JSON chua danh sach cac yeu to dong gop vao diem nguy co
    factors = Column(Text)

    # Nhan nguon de minh bach: luon la "v1_prototype_rules" hien tai
    source = Column(String, default="v1_prototype_rules")

    patient = relationship("Patient", back_populates="forecasts")
