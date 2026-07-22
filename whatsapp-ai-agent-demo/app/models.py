# ==========================================================
# FILE: app/models.py (v2.0 - ENTERPRISE PRODUCTION)
# ==========================================================
# PURPOSE: SQLAlchemy Models - PostgreSQL Integration
# VERSION: 2.0 - ENTERPRISE PRODUCTION READY
#
# COMPATIBLE WITH: upload.py, excel_import_service.py, all analytics services
# INTEGRATION: Railway PostgreSQL, FastAPI, WhatsApp AI Agent
#
# IMPROVEMENTS v2.0:
# - ✅ Fixed upload_batch_id type (String → supports batch IDs)
# - ✅ Preserved all existing columns, relationships, indexes
# - ✅ Enhanced PostgreSQL compatibility
# - ✅ Improved documentation
# - ✅ 100% backward compatible
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
    """
    Customer model for WhatsApp AI Agent.
    
    Stores customer information and maintains relationship
    with conversations and messages.
    """
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
    """
    Conversation model tracking customer interactions.
    
    Maintains conversation state and relationships with
    messages, images, and AI logs.
    """
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
    """
    Individual message within a conversation.
    
    Stores sender, content, and timestamp for each message.
    """
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
    """
    Uploaded image with AI analysis results.
    
    Stores image URL and AI-generated analysis for
    visual content processing.
    """
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
    """
    AI response logging for audit and debugging.
    
    Captures prompts, responses, and success/failure status
    for all AI interactions.
    """
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
    """
    Delivery Report model - PostgreSQL Single Source of Truth.
    
    Stores all delivery data imported from Excel files.
    Comprehensive indexing for analytics and query performance.
    
    Date fields preserve Excel dates exactly (NO timezone conversion).
    upload_batch_id supports descriptive batch IDs from import service.
    """
    __tablename__ = "delivery_reports"

    id = Column(Integer, primary_key=True, index=True)

    # Core delivery document fields
    dn_no = Column(
        String(100),
        index=True,
        nullable=False,
        comment="Delivery Note Number - Primary identifier"
    )

    dn_work = Column(
        String(100),
        nullable=True,
        index=True,
        comment="Delivery Work/Work Order Reference"
    )

    order_type = Column(
        String(100),
        nullable=True,
        comment="Order Type Classification"
    )

    division = Column(
        String(100),
        nullable=True,
        index=True,
        comment="Business Division/Department"
    )

    # Customer Information
    customer_code = Column(
        String(100),
        nullable=True,
        index=True,
        comment="Customer/Account Code"
    )

    dealer_code = Column(
        String(100),
        nullable=True,
        index=True,
        comment="Dealer/Distributor Code"
    )

    customer_name = Column(
        Text,
        nullable=True,
        comment="Customer/Dealer Name"
    )

    customer_model = Column(
        String(255),
        nullable=True,
        comment="Customer Model/Product Code"
    )

    # Material Information
    material_no = Column(
        String(100),
        nullable=True,
        index=True,
        comment="Material/Product Number"
    )

    storage_location = Column(
        String(100),
        nullable=True,
        index=True,
        comment="Storage Location/Bin"
    )

    # Sales Information
    sales_office = Column(
        String(255),
        nullable=True,
        comment="Sales Office/Region"
    )

    sales_manager = Column(
        String(255),
        nullable=True,
        index=True,
        comment="Sales Manager/Representative"
    )

    # Location Information
    ship_to_city = Column(
        String(255),
        nullable=True,
        index=True,
        comment="Ship To/Destination City"
    )

    warehouse = Column(
        String(255),
        nullable=True,
        index=True,
        comment="Warehouse/Plant"
    )

    warehouse_code = Column(
        String(100),
        nullable=True,
        index=True,
        comment="Warehouse Code/Identifier"
    )

    delivery_location = Column(
        String(255),
        nullable=True,
        index=True,
        comment="Delivery Location/Address"
    )

    # Quantity and Amount
    dn_qty = Column(
        Integer,
        nullable=True,
        comment="Delivery Quantity (units)"
    )

    dn_amount = Column(
        Float,
        nullable=True,
        comment="Delivery Amount/Value"
    )

    # Date Fields - EXACT PRESERVATION from Excel
    # These are business dates (no timezone, no time component)
    dn_create_date = Column(
        Date,
        nullable=True,
        comment="DN Creation Date (Excel date preserved exactly)"
    )

    good_issue_date = Column(
        Date,
        nullable=True,
        comment="Good Issue/PGI Date (Excel date preserved exactly)"
    )

    pod_date = Column(
        Date,
        nullable=True,
        comment="Proof of Delivery Date (Excel date preserved exactly)"
    )

    # Status Fields
    delivery_status = Column(
        String(50),
        default="Pending",
        index=True,
        comment="Delivery Status"
    )

    pgi_status = Column(
        String(50),
        default="Pending",
        index=True,
        comment="PGI/Goods Issue Status"
    )

    pod_status = Column(
        String(50),
        default="Pending",
        index=True,
        comment="POD/Proof of Delivery Status"
    )

    pending_flag = Column(
        Boolean,
        default=True,
        index=True,
        comment="Pending Flag (True if pending)"
    )

    # Remarks
    remarks = Column(
        Text,
        nullable=True,
        comment="Additional Remarks/Notes"
    )

    # Upload Tracking - FIXED: String type supports descriptive batch IDs
    source_file = Column(
        String(500),
        nullable=True,
        comment="Original Excel file name"
    )

    upload_batch_id = Column(
        String(100),
        nullable=True,
        index=True,
        comment="Upload Batch ID (e.g., BATCH_20260626_064222_98962553)"
    )

    imported_at = Column(
        DateTime,
        default=datetime.utcnow,
        comment="Import timestamp"
    )

    # System Timestamps
    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        comment="Record creation timestamp"
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        comment="Record last update timestamp"
    )

    # ==========================================================
    # COMPOSITE INDEXES - Optimized for Analytics Performance
    # ==========================================================

    __table_args__ = (
        # City and status queries
        Index('idx_city_status', 'ship_to_city', 'delivery_status'),
        
        # Dealer analytics
        Index('idx_dealer_status', 'dealer_code', 'delivery_status'),
        
        # Customer analytics
        Index('idx_customer_code_status', 'customer_code', 'pending_flag'),
        
        # Material/product analytics
        Index('idx_material_status', 'material_no', 'pgi_status'),
        
        # Sales manager performance
        Index('idx_sales_manager_status', 'sales_manager', 'pgi_status'),
        
        # Pending queries
        Index('idx_pending_queries', 'pending_flag', 'delivery_status'),
        
        # Delivery location analytics
        Index('idx_delivery_location_status', 'delivery_location', 'pending_flag'),
        
        # Work order tracking
        Index('idx_dn_work_status', 'dn_work', 'pgi_status'),
        
        # Storage location analytics
        Index('idx_storage_location', 'storage_location', 'pending_flag'),
        
        # Division analytics
        Index('idx_division_status', 'division', 'pending_flag'),
        
        # Warehouse code queries
        Index('idx_warehouse_code_status', 'warehouse_code', 'pending_flag'),
        
        # Batch import tracking
        Index('idx_import_batch', 'imported_at', 'upload_batch_id'),
    )


# ==========================================================
# SYSTEM SETTING
# ==========================================================

class SystemSetting(Base):
    """
    System Settings for application configuration.
    
    Key-value store for runtime settings with descriptions.
    """
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True)

    key = Column(
        String(100),
        unique=True,
        nullable=False,
        comment="Setting key/identifier"
    )

    value = Column(
        String(255),
        nullable=False,
        comment="Setting value"
    )

    description = Column(
        String(500),
        nullable=True,
        comment="Human-readable description of this setting"
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        comment="Creation timestamp"
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        comment="Last update timestamp"
    )


# ==========================================================
# MODULE INITIALIZATION
# ==========================================================

# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'Base',
    'Customer',
    'Conversation',
    'Message',
    'UploadedImage',
    'AIResponseLog',
    'DeliveryReport',
    'SystemSetting'
]

# ==========================================================
# END OF FILE
# ==========================================================
