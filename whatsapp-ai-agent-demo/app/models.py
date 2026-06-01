from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Boolean
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

    name = Column(String(255), nullable=False)

    phone_number = Column(
        String(50),
        unique=True,
        nullable=False
    )

    email = Column(
        String(255),
        nullable=True
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    conversations = relationship(
        "Conversation",
        back_populates="customer"
    )


# ==========================================================
# CONVERSATION
# ==========================================================

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)

    customer_id = Column(
        Integer,
        ForeignKey("customers.id")
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
        default=datetime.utcnow
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


# ==========================================================
# MESSAGE
# ==========================================================

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)

    conversation_id = Column(
        Integer,
        ForeignKey("conversations.id")
    )

    sender = Column(
        String(50)
    )  # customer / ai

    content = Column(
        Text
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
        ForeignKey("conversations.id")
    )

    image_url = Column(Text)

    ai_analysis = Column(Text)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )


# ==========================================================
# AI LOGS
# ==========================================================

class AIResponseLog(Base):
    __tablename__ = "ai_response_logs"

    id = Column(Integer, primary_key=True, index=True)

    conversation_id = Column(
        Integer,
        ForeignKey("conversations.id")
    )

    prompt = Column(Text)

    ai_response = Column(Text)

    model_name = Column(
        String(100),
        default="claude"
    )

    success = Column(
        Boolean,
        default=True
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )
