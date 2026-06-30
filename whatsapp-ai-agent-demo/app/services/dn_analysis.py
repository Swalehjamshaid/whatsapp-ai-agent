You are a Senior Python Enterprise Software Architect.

Your task is to completely rewrite and modernize ONLY the following file:

app/services/dn_analysis.py

This file is part of a FastAPI + PostgreSQL + WhatsApp AI Agent.

The project architecture is already established.

DO NOT change any other file.

============================================================
PROJECT ARCHITECTURE
============================================================

Flow:

WhatsApp User

↓

app/routes/webhook.py

↓

app/services/ai_provider_service.py

↓

app/services/ai_provider_service_intents.py

↓

RoutingDecision

↓

app/services/dn_analysis.py

↓

PostgreSQL

↓

Structured Business Result

↓

app/services/groq_service.py

↓

app/services/whatsapp_service.py

↓

WhatsApp User

============================================================
ROLE OF dn_analysis.py
============================================================

This file is ONLY responsible for Delivery Note (DN) analytics.

It must NEVER

• Detect Intent
• Perform AI Chat
• Route Requests
• Send WhatsApp Messages
• Call webhook.py
• Detect Dealer Intent
• Detect Warehouse Intent

It ONLY performs DN business logic.

============================================================
DATABASE
============================================================

PostgreSQL is the ONLY source of truth.

Do NOT use

CSV

Excel

JSON

Hardcoded Data

Fake Data

============================================================
TABLE
============================================================

delivery_reports

============================================================
AVAILABLE COLUMNS
============================================================

dn_no

dn_work

order_type

division

customer_code

dealer_code

customer_name

customer_model

material_no

storage_location

sales_office

sales_manager

ship_to_city

warehouse

warehouse_code

delivery_location

dn_qty

dn_amount

dn_create_date

good_issue_date

pod_date

remarks

delivery_status

pgi_status

pod_status

pending_flag

source_file

upload_batch_id

imported_at

created_at

updated_at

============================================================
IMPORTANT BUSINESS RULES
============================================================

One DN can contain multiple rows.

Example

DN

6243700253

may have

5 products

The dashboard MUST aggregate ALL rows.

Never treat each row as a separate DN.

============================================================
AGGREGATIONS
============================================================

Calculate

Total Units

SUM(dn_qty)

Total Revenue

SUM(dn_amount)

Material Count

COUNT(DISTINCT material_no)

Model Count

COUNT(DISTINCT customer_model)

============================================================
RETURN REQUIRED FIELDS
============================================================

DN Number

Dealer Name

Dealer Code

Customer Code

Warehouse

Warehouse Code

City

Delivery Location

Sales Office

Sales Manager

Division

Order Type

DN Work

DN Create Date

Good Issue Date

POD Date

PGI Status

POD Status

Delivery Status

Pending Flag

Total Units

Total Revenue

Material Count

Model Count

Average Revenue Per Unit

Transit Days

POD Days

Delivery Days

Distance (KM)

Estimated Delivery Time

Remarks

============================================================
DATE CALCULATIONS
============================================================

Calculate

DN Age

Days Since DN Created

Transit Days

Good Issue Date
↓

POD Date

PGI Days

DN Create Date
↓

Good Issue Date

POD Delay

Good Issue Date
↓

POD Date

Pending Days

If Pending

Calculate until Today

============================================================
DISTANCE
============================================================

Use

openrouteservice

If API unavailable

Fallback

geopy

If geopy unavailable

Fallback

Haversine

Return

Distance KM

Estimated Delivery Time

============================================================
DELIVERY STATUS
============================================================

Automatically determine

Pending DN

Pending PGI

Pending POD

Delivered

In Transit

Delayed

Completed

============================================================
REQUIRED PUBLIC METHODS
============================================================

get_dn_dashboard(dn_no)

get_pending_dns()

get_pending_pgi()

get_pending_pod()

get_recent_dns()

get_oldest_pending()

get_delivery_timeline()

get_transit_analysis()

get_service_metadata()

health_check()

validation_query()

============================================================
RESPONSE MODEL
============================================================

Every public method returns

{
    "success": bool,
    "data": {},
    "whatsapp_message": "",
    "error": "",
    "metadata": {}
}

Never return raw SQLAlchemy objects.

============================================================
WHATSAPP OUTPUT
============================================================

The service should generate a professional WhatsApp dashboard.

Example

📦 DN Dashboard

DN
6243700253

Dealer
Commercial Electronics Abbottabad

Dealer Code
DEAL_COMMERCIAL

Customer Code
CUST_COMMERCIAL

Warehouse
Rawalpindi

Warehouse Code
RWP01

City
Abbottabad

Sales Office
Rawalpindi

Sales Manager
Traditional Channel

Division
Home Air Conditioner

Order Type
Normal

────────────────────

Units
18

Revenue
PKR 1,245,000

Models
4

Materials
4

────────────────────

DN Date
2026-06-22

PGI Date
2026-06-23

POD Date
2026-06-25

Transit
2 Days

Delivery
3 Days

Distance
185 KM

Estimated Time
4 Hours

────────────────────

Delivery Status
Delivered

PGI Status
Completed

POD Status
Completed

Pending
No

============================================================
PERFORMANCE
============================================================

Use

SQLAlchemy

Polars

PyArrow

CacheTools

Redis (optional)

Avoid unnecessary loops.

Aggregate inside PostgreSQL where possible.

============================================================
ERROR HANDLING
============================================================

Handle

DN Not Found

Database Timeout

Invalid DN

Connection Lost

SQL Error

Validation Error

Never crash.

Always return structured errors.

============================================================
LOGGING
============================================================

Use Loguru.

Log

Request ID

DN Number

Execution Time

SQL Time

Rows Returned

Errors

============================================================
TYPE HINTS
============================================================

Use

Python 3.12

Pydantic

Dataclasses

Type Hints

============================================================
CODE QUALITY
============================================================

Follow

SOLID

DRY

Repository Pattern

Single Responsibility

Enterprise Architecture

============================================================
DO NOT
============================================================

Do NOT perform Intent Detection.

Do NOT call Groq.

Do NOT send WhatsApp.

Do NOT detect Dealer queries.

Do NOT detect Warehouse queries.

Do NOT detect Product queries.

Only perform DN analytics.

============================================================
OUTPUT
============================================================

Generate COMPLETE production-ready code.

No placeholders.

No TODOs.

No pseudocode.

Generate the file in logical code blocks with explanations for each block.

Ensure it is fully compatible with:

app/services/ai_provider_service.py

app/services/ai_provider_service_intents.py

PostgreSQL delivery_reports table

FastAPI

Python 3.12
