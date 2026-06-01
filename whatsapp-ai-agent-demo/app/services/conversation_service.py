# ==========================================================
# FILE: app/services/conversation_service.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

from datetime import datetime

# ==========================================================
# IN-MEMORY CONVERSATION STORE
# ==========================================================

conversation_store = {}

# ==========================================================
# CREATE CUSTOMER
# ==========================================================

def create_customer(phone_number: str):

    if phone_number not in conversation_store:

        conversation_store[phone_number] = {
            "created_at": datetime.utcnow().isoformat(),
            "messages": []
        }

    return conversation_store[phone_number]

# ==========================================================
# ADD USER MESSAGE
# ==========================================================

def add_user_message(
    phone_number: str,
    message: str
):

    create_customer(phone_number)

    conversation_store[phone_number]["messages"].append(
        {
            "role": "user",
            "content": message,
            "timestamp": datetime.utcnow().isoformat()
        }
    )

# ==========================================================
# ADD AI MESSAGE
# ==========================================================

def add_ai_message(
    phone_number: str,
    message: str
):

    create_customer(phone_number)

    conversation_store[phone_number]["messages"].append(
        {
            "role": "assistant",
            "content": message,
            "timestamp": datetime.utcnow().isoformat()
        }
    )

# ==========================================================
# GET CONVERSATION HISTORY
# ==========================================================

def get_conversation_history(
    phone_number: str
):

    if phone_number not in conversation_store:
        return []

    return conversation_store[phone_number]["messages"]

# ==========================================================
# GET CUSTOMER CONVERSATION
# ==========================================================

def get_customer_conversation(
    phone_number: str
):

    if phone_number not in conversation_store:

        return {
            "phone_number": phone_number,
            "messages": []
        }

    return {
        "phone_number": phone_number,
        "messages": conversation_store[phone_number]["messages"]
    }

# ==========================================================
# GET ALL CONVERSATIONS
# ==========================================================

def get_all_conversations():

    return conversation_store

# ==========================================================
# DELETE CONVERSATION
# ==========================================================

def delete_conversation(
    phone_number: str
):

    if phone_number in conversation_store:
        del conversation_store[phone_number]

        return True

    return False

# ==========================================================
# TOTAL CUSTOMERS
# ==========================================================

def total_customers():

    return len(conversation_store)

# ==========================================================
# TOTAL MESSAGES
# ==========================================================

def total_messages():

    count = 0

    for customer in conversation_store.values():

        count += len(customer["messages"])

    return count

# ==========================================================
# DASHBOARD ANALYTICS
# ==========================================================

def dashboard_stats():

    return {
        "total_customers": total_customers(),
        "total_messages": total_messages(),
        "total_conversations": len(conversation_store)
    }

# ==========================================================
# CLEAR ALL DATA
# ==========================================================

def clear_all_conversations():

    conversation_store.clear()

    return True
