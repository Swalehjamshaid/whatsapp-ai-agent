# ==========================================================
# FILE: app/models.py
# ==========================================================

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Date,
    Float,
    ForeignKey,
    Boolean,
    Index
)
from sqlalchemy.orm import relationship
from datetime import datetime

from app.database import Base


# ==========================================================
# CUSTOMER
# ==========================================================

class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(
        String(255),
        nullable=False
    )

    phone_number = Column(
        String(50),
        unique=True,
        nullable=False,
        index=True
    )

    email = Column(
        String(255),
        nullable=True
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    conversations = relationship(
        "Conversation",
        back_populates="customer",
        cascade="all, delete-orphan"
    )


# ==========================================================
# CONVERSATION
# ==========================================================

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)

    customer_id = Column(
        Integer,
        ForeignKey(
            "customers.id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    status = Column(
        String(50),
        default="active"
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    customer = relationship(
        "Customer",
        back_populates="conversations"
    )

    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan"
    )

    images = relationship(
        "UploadedImage",
        cascade="all, delete-orphan"
    )

    ai_logs = relationship(
        "AIResponseLog",
        cascade="all, delete-orphan"
    )


# ==========================================================
# MESSAGE
# ==========================================================

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)

    conversation_id = Column(
        Integer,
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    sender = Column(
        String(50),
        nullable=False
    )

    content = Column(
        Text,
        nullable=False
    )

    message_type = Column(
        String(50),
        default="text"
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    conversation = relationship(
        "Conversation",
        back_populates="messages"
    )


# ==========================================================
# IMAGE ANALYSIS
# ==========================================================

class UploadedImage(Base):
    __tablename__ = "uploaded_images"

    id = Column(Integer, primary_key=True, index=True)

    conversation_id = Column(
        Integer,
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    image_url = Column(
        Text,
        nullable=False
    )

    ai_analysis = Column(
        Text,
        nullable=True
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )


# ==========================================================
# AI RESPONSE LOGS
# ==========================================================

class AIResponseLog(Base):
    __tablename__ = "ai_response_logs"

    id = Column(Integer, primary_key=True, index=True)

    conversation_id = Column(
        Integer,
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    prompt = Column(
        Text,
        nullable=False
    )

    ai_response = Column(
        Text,
        nullable=False
    )

    model_name = Column(
        String(100),
        default="gpt-5.5"
    )

    success = Column(
        Boolean,
        default=True
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )


# ==========================================================
# DELIVERY REPORT
# ==========================================================

class DeliveryReport(Base):
    __tablename__ = "delivery_reports"

    id = Column(Integer, primary_key=True, index=True)

    # Core delivery document fields
    dn_no = Column(
        String(100),
        index=True
    )

    dn_work = Column(
        String(100),
        nullable=True,
        index=True
    )

    order_type = Column(String(100))

    division = Column(
        String(100),
        index=True
    )

    customer_code = Column(
        String(100),
        nullable=True,
        index=True
    )

    dealer_code = Column(
        String(100),
        nullable=True,
        index=True
    )

    customer_name = Column(Text)

    customer_model = Column(String(255))

    # Material information
    material_no = Column(
        String(100),
        nullable=True,
        index=True
    )

    storage_location = Column(
        String(100),
        nullable=True,
        index=True
    )

    # Sales information
    sales_office = Column(
        String(255),
        nullable=True
    )

    sales_manager = Column(
        String(255),
        index=True
    )

    # Location information
    ship_to_city = Column(
        String(255),
        index=True
    )

    warehouse = Column(
        String(255),
        index=True
    )

    warehouse_code = Column(
        String(100),
        nullable=True,
        index=True
    )

    delivery_location = Column(
        String(255),
        nullable=True,
        index=True
    )

    # Quantity and amount
    dn_qty = Column(Integer)

    dn_amount = Column(Float)

    # Date fields
    dn_create_date = Column(Date)

    good_issue_date = Column(Date)

    pod_date = Column(Date, nullable=True)

    remarks = Column(
        Text,
        nullable=True
    )

    # Status fields
    delivery_status = Column(
        String(50),
        default="Pending",
        index=True
    )

    pgi_status = Column(
        String(50),
        default="Pending",
        index=True
    )

    pod_status = Column(
        String(50),
        default="Pending",
        index=True
    )

    pending_flag = Column(
        Boolean,
        default=True,
        index=True
    )

    # Upload tracking
    source_file = Column(
        String(500),
        nullable=True
    )

    upload_batch_id = Column(
        Integer,
        nullable=True,
        index=True
    )

    imported_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    # Timestamps
    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    # Composite indexes for common query patterns
    __table_args__ = (
        Index('idx_city_status', 'ship_to_city', 'delivery_status'),
        Index('idx_dealer_status', 'dealer_code', 'delivery_status'),
        Index('idx_customer_code_status', 'customer_code', 'pending_flag'),
        Index('idx_material_status', 'material_no', 'pgi_status'),
        Index('idx_sales_manager_status', 'sales_manager', 'pgi_status'),
        Index('idx_pending_queries', 'pending_flag', 'delivery_status'),
        Index('idx_delivery_location_status', 'delivery_location', 'pending_flag'),
        Index('idx_dn_work_status', 'dn_work', 'pgi_status'),
        Index('idx_storage_location', 'storage_location', 'pending_flag'),
        Index('idx_division_status', 'division', 'pending_flag'),
        Index('idx_warehouse_code_status', 'warehouse_code', 'pending_flag'),
        Index('idx_import_batch', 'imported_at', 'upload_batch_id'),
    )


# ==========================================================
# SYSTEM SETTING (Enhanced with Schema Logic)
# ==========================================================

class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True)

    key = Column(String(100), unique=True, nullable=False)

    value = Column(String(255), nullable=False)

    description = Column(
        String(500),
        nullable=True,
        help_text="Human-readable description of this setting"
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )
