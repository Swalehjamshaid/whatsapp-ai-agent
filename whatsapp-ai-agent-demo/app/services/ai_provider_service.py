import re
import uuid
from loguru import logger
from app.database import SessionLocal
from app.models import DeliveryReport
from sqlalchemy import func

def process_whatsapp_query(question, session_factory, phone_number=None, user_id=None, request_id=None):
    """Minimal working version"""
    logger.info(f"Processing: {question[:100]}")
    
    db = None
    try:
        db = session_factory()
        
        msg_lower = question.lower()
        
        # Help
        if 'help' in msg_lower or 'menu' in msg_lower:
            return "🤖 Type any dealer name, warehouse name, or DN number to get started!"
        
        # DN query
        dn_match = re.search(r'\b(\d{8,12})\b', question)
        if dn_match:
            dn = dn_match.group()
            record = db.query(DeliveryReport).filter(DeliveryReport.dn_no == dn).first()
            if record:
                return f"📄 DN {dn}: {record.customer_name} | Amount: PKR {float(record.dn_amount or 0):,.0f}"
            return f"❌ DN {dn} not found"
        
        # Warehouse query
        warehouses = ['lahore', 'karachi', 'rawalpindi', 'sargodha', 'islamabad']
        for wh in warehouses:
            if wh in msg_lower:
                records = db.query(DeliveryReport).filter(DeliveryReport.warehouse.ilike(f"%{wh}%")).all()
                if records:
                    total = sum(float(r.dn_amount or 0) for r in records)
                    return f"🏭 Warehouse {wh.title()}: {len(records)} deliveries | Revenue: PKR {total:,.0f}"
                return f"❌ No data for {wh} warehouse"
        
        # Dealer query (default)
        records = db.query(DeliveryReport).filter(DeliveryReport.customer_name.ilike(f"%{question}%")).all()
        if records:
            total = sum(float(r.dn_amount or 0) for r in records)
            return f"🏪 {records[0].customer_name}: {len(records)} deliveries | Revenue: PKR {total:,.0f}"
        
        return f"❌ No data found for '{question}'. Type 'Help' for commands."
        
    except Exception as e:
        logger.error(f"Error: {e}")
        return f"❌ Error: {str(e)}"
    finally:
        if db:
            db.close()
