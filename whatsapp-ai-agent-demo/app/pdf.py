import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime, timedelta
import random
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfgen import canvas

# -----------------------------
# 1. DATA LOADING (Replace with your actual file)
# -----------------------------
def load_data():
    """Generate realistic sample data for demo, or load from Excel."""
    # --- If you have your actual file, uncomment this line:
    # df = pd.read_excel("DN___PGI_July_31.07.2026.XL.xlsx")
    # return df

    # --- Otherwise, generate 200 sample rows based on your structure ---
    random.seed(42)
    np.random.seed(42)

    divisions = ['Washing Machine', 'Refrigerator', 'Air Conditioner', 'Microwave']
    cities = ['GUJRANWALA', 'DASKA', 'WAZIRABAD', 'RAHWALI CANTT', 'LAHORE', 'KARACHI', 'ISLAMABAD']
    models_wm = ['HWM120-316S6 GC', 'HWM150-316S6', 'HWM 100-826S6 GC']
    models_ref = ['HRF-316IFRA1', 'HRF-316IFGA1', 'HRF-538TIFRA1', 'HRF-458TIFG1U1', 'HRF-458IDGA1']
    materials = ['CBAMF2000', 'CBAMF4000', 'CBAMF6000', 'BL044EE02', 'BL044DE02', 'BL046SE00', 'BL0468E02', 'BL046TE01']

    rows = []
    start_date = datetime(2026, 7, 1)
    for i in range(200):
        div = random.choice(divisions)
        if div == 'Washing Machine':
            model = random.choice(models_wm)
            price = random.choice([63239, 69319, 50261])
        else:
            model = random.choice(models_ref)
            price = random.choice([74746, 98541, 88639, 95466])
        
        city = random.choice(cities)
        qty = random.randint(1, 5)
        dn_date = start_date + timedelta(days=random.randint(0, 30))
        gi_date = dn_date + timedelta(days=1)
        pod_date = gi_date + timedelta(days=random.randint(3, 8))
        
        rows.append({
            'dn_no': 3420000 + i,
            'dn_work': 6243000000 + i,
            'order_type': 'ZSO',
            'division': div,
            'customer_code': 'CUST_NAEEM_ELECTRONI',
            'dealer_code': 'DEAL_NAEEM_ELECTRONI',
            'customer_name': 'Naeem Electronics (Private Limited) GRW',
            'customer_model': model,
            'material_no': random.choice(materials),
            'sales_office': 'Gujranwala Office',
            'ship_to_city': city,
            'warehouse': 'Gujranwala',
            'warehouse_code': 'GJW',
            'dn_qty': qty,
            'dn_amount': qty * price,
            'dn_create_date': dn_date,
            'good_issue_date': gi_date,
            'pod_date': pod_date,
            'delivery_status': random.choices(['Delivered', 'In Transit', 'Pending'], weights=[0.9, 0.07, 0.03])[0],
            'pgi_status': random.choices(['Completed', 'Pending'], weights=[0.95, 0.05])[0],
            'pod_status': random.choices(['Completed', 'Pending'], weights=[0.92, 0.08])[0],
            'pending_flag': False,
            'source_file': 'DN___PGI_July_31.07.2026.XL.xlsx',
            'upload_batch_id': 'BATCH_20260801_110909_e89fee6e',
        })
    df = pd.DataFrame(rows)
    # clean columns
    df.columns = df.columns.str.strip()
    date_cols = ['dn_create_date', 'good_issue_date', 'pod_date']
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    return df

df = load_data()

# -----------------------------
# 2. COMPUTATIONS & PREPARATION
# -----------------------------
total_records = len(df)
total_qty = df['dn_qty'].sum()
total_amount = df['dn_amount'].sum()
avg_order_value = total_amount / total_records if total_records else 0

# Date calculations
df['order_to_gi'] = (df['good_issue_date'] - df['dn_create_date']).dt.days
df['gi_to_pod'] = (df['pod_date'] - df['good_issue_date']).dt.days
avg_ogi = df['order_to_gi'].mean()
avg_gip = df['gi_to_pod'].mean()

# Grouping
div_summary = df.groupby('division').agg(Qty=('dn_qty', 'sum'), Amount=('dn_amount', 'sum')).reset_index()
city_summary = df.groupby('ship_to_city').agg(Qty=('dn_qty', 'sum'), Amount=('dn_amount', 'sum')).reset_index().sort_values('Amount', ascending=False)
daily_trend = df.groupby('dn_create_date').agg(Daily_Amount=('dn_amount', 'sum')).reset_index().sort_values('dn_create_date')
top_models = df.groupby('customer_model').agg(Amount=('dn_amount', 'sum')).reset_index().sort_values('Amount', ascending=False).head(5)
top_customers = df.groupby('customer_name').agg(Amount=('dn_amount', 'sum'), Records=('dn_no', 'count')).reset_index().sort_values('Amount', ascending=False).head(5)

# Status counts
status_counts = df['delivery_status'].value_counts()
pgi_counts = df['pgi_status'].value_counts()
pod_counts = df['pod_status'].value_counts()

# -----------------------------
# 3. MATPLOTLIB GRAPHS (as images)
# -----------------------------
def create_figure(func):
    """Helper to create a matplotlib figure and return as BytesIO."""
    buf = BytesIO()
    fig = func()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf

# Graph 1: Pie - Revenue by Division
def pie_div():
    fig, ax = plt.subplots(figsize=(6, 4))
    if not div_summary.empty:
        ax.pie(div_summary['Amount'], labels=div_summary['division'], autopct='%1.1f%%', startangle=90, colors=plt.cm.Paired.colors)
        ax.set_title('Revenue Share by Division', fontsize=14, fontweight='bold')
    else:
        ax.text(0.5, 0.5, 'No Data', ha='center', va='center')
    return fig

# Graph 2: Bar - Revenue by City (Top 8)
def bar_city():
    fig, ax = plt.subplots(figsize=(6, 4))
    top_cities = city_summary.head(8)
    ax.bar(top_cities['ship_to_city'], top_cities['Amount'], color='teal')
    ax.set_title('Total Revenue by City (Top 8)', fontsize=14, fontweight='bold')
    ax.set_xlabel('City')
    ax.set_ylabel('Amount (₨)')
    plt.xticks(rotation=30, ha='right')
    ax.grid(axis='y', linestyle='--', alpha=0.7)
    return fig

# Graph 3: Bar - Top 5 Models by Revenue
def bar_models():
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(top_models['customer_model'], top_models['Amount'], color='coral')
    ax.set_title('Top 5 Models by Revenue', fontsize=14, fontweight='bold')
    ax.set_xlabel('Model')
    ax.set_ylabel('Amount (₨)')
    plt.xticks(rotation=30, ha='right')
    ax.grid(axis='y', linestyle='--', alpha=0.7)
    return fig

# Graph 4: Line - Daily Revenue Trend
def line_trend():
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(daily_trend['dn_create_date'], daily_trend['Daily_Amount'], marker='o', linestyle='-', color='navy')
    ax.set_title('Daily Revenue Trend (July 2026)', fontsize=14, fontweight='bold')
    ax.set_xlabel('Date')
    ax.set_ylabel('Daily Amount (₨)')
    plt.xticks(rotation=30, ha='right')
    ax.grid(True, linestyle='--', alpha=0.6)
    return fig

# Graph 5: Bar - Quantity by Division
def bar_qty_div():
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(div_summary['division'], div_summary['Qty'], color='forestgreen')
    ax.set_title('Total Quantity Shipped by Division', fontsize=14, fontweight='bold')
    ax.set_xlabel('Division')
    ax.set_ylabel('Quantity')
    ax.grid(axis='y', linestyle='--', alpha=0.7)
    return fig

# -----------------------------
# 4. PDF BUILDING
# -----------------------------
def build_pdf(filename="Comprehensive_5Page_Report.pdf"):
    doc = SimpleDocTemplate(filename, pagesize=letter,
                            rightMargin=50, leftMargin=50,
                            topMargin=50, bottomMargin=50)
    styles = getSampleStyleSheet()
    title_style = styles['Title']
    heading2 = styles['Heading2']
    heading3 = styles['Heading3']
    normal = styles['Normal']
    
    # custom styles
    centered = ParagraphStyle('Centered', parent=normal, alignment=TA_CENTER)
    h2_bold = ParagraphStyle('H2Bold', parent=heading2, spaceAfter=12)
    
    story = []
    
    # ----- PAGE 1: Overview, KPIs, Pie Chart -----
    story.append(Paragraph("SALES DELIVERY PERFORMANCE REPORT", title_style))
    story.append(Paragraph(f"Report Generated: {datetime.now().strftime('%B %d, %Y at %H:%M')}", centered))
    story.append(Spacer(1, 0.2*inch))
    
    # Executive Summary
    story.append(Paragraph("EXECUTIVE SUMMARY", h2_bold))
    summary_text = f"""
    <b>Total Records:</b> {total_records:,} &nbsp;&nbsp;|&nbsp;&nbsp;
    <b>Total Quantity:</b> {total_qty:,} &nbsp;&nbsp;|&nbsp;&nbsp;
    <b>Total Revenue:</b> ₨ {total_amount:,.0f} &nbsp;&nbsp;|&nbsp;&nbsp;
    <b>Avg Order Value:</b> ₨ {avg_order_value:,.0f}
    """
    story.append(Paragraph(summary_text, normal))
    story.append(Spacer(1, 0.1*inch))
    
    # KPIs Table
    kpi_data = [
        ["KPI", "Value"],
        ["Avg Order → GI (days)", f"{avg_ogi:.1f}" if not np.isnan(avg_ogi) else "N/A"],
        ["Avg GI → POD (days)", f"{avg_gip:.1f}" if not np.isnan(avg_gip) else "N/A"],
        ["Order Type", df['order_type'].iloc[0] if 'order_type' in df else "N/A"],
        ["Pending Flags", "0 (All processed)" if not df['pending_flag'].any() else f"{df['pending_flag'].sum()} pending"],
        ["Warehouse", df['warehouse'].iloc[0] if 'warehouse' in df else "N/A"],
        ["Sales Office", df['sales_office'].iloc[0] if 'sales_office' in df else "N/A"],
    ]
    kpi_tbl = Table(kpi_data, colWidths=[2.5*inch, 3*inch])
    kpi_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('BOTTOMPADDING', (0,0), (-1,0), 10),
        ('BACKGROUND', (0,1), (-1,-1), colors.lightgrey),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(kpi_tbl)
    story.append(Spacer(1, 0.3*inch))
    
    # Pie Chart
    story.append(Paragraph("Revenue Share by Division", heading3))
    pie_img = Image(create_figure(pie_div), width=5*inch, height=3.5*inch)
    story.append(pie_img)
    story.append(PageBreak())
    
    # ----- PAGE 2: Revenue Analysis (City + Models) -----
    story.append(Paragraph("REVENUE ANALYSIS", title_style))
    story.append(Spacer(1, 0.1*inch))
    
    # Bar chart: City
    story.append(Paragraph("Revenue by City (Top 8)", heading3))
    city_img = Image(create_figure(bar_city), width=5*inch, height=3.5*inch)
    story.append(city_img)
    story.append(Spacer(1, 0.2*inch))
    
    # Bar chart: Top Models
    story.append(Paragraph("Revenue by Model (Top 5)", heading3))
    model_img = Image(create_figure(bar_models), width=5*inch, height=3.5*inch)
    story.append(model_img)
    story.append(Spacer(1, 0.1*inch))
    
    # Summary tables (optional compact view)
    city_data = [["City", "Qty", "Amount (₨)"]] + [[r['ship_to_city'], f"{r['Qty']:,}", f"{r['Amount']:,.0f}"] for _, r in city_summary.head(6).iterrows()]
    city_tbl = Table(city_data, colWidths=[2*inch, 1.5*inch, 2.5*inch])
    city_tbl.setStyle(TableStyle([('GRID', (0,0), (-1,-1), 0.5, colors.grey), ('BACKGROUND', (0,0), (-1,0), colors.darkgrey), ('TEXTCOLOR', (0,0), (-1,0), colors.white), ('ALIGN', (0,0), (-1,-1), 'CENTER')]))
    story.append(city_tbl)
    story.append(PageBreak())
    
    # ----- PAGE 3: Operational Trends (Line + Qty) -----
    story.append(Paragraph("OPERATIONAL TRENDS", title_style))
    story.append(Spacer(1, 0.1*inch))
    
    # Line chart
    story.append(Paragraph("Daily Revenue Trend", heading3))
    line_img = Image(create_figure(line_trend), width=5*inch, height=3.5*inch)
    story.append(line_img)
    story.append(Spacer(1, 0.2*inch))
    
    # Quantity by Division Bar
    story.append(Paragraph("Quantity Shipped by Division", heading3))
    qty_img = Image(create_figure(bar_qty_div), width=5*inch, height=3.5*inch)
    story.append(qty_img)
    story.append(Spacer(1, 0.1*inch))
    
    # Status distribution text
    status_text = f"<b>Delivery Status:</b> {', '.join([f'{k} ({v})' for k,v in status_counts.items()])}"
    story.append(Paragraph(status_text, normal))
    story.append(PageBreak())
    
    # ----- PAGE 4: Detailed Data Table (first 50 rows, paginated if needed) -----
    story.append(Paragraph("DETAILED TRANSACTION DATA (Sample)", title_style))
    story.append(Paragraph("Showing up to 50 records. Full dataset contains {:,} rows.".format(total_records), normal))
    story.append(Spacer(1, 0.1*inch))
    
    # Select columns
    detail_cols = ['dn_no', 'customer_model', 'material_no', 'ship_to_city', 'dn_qty', 'dn_amount', 'dn_create_date', 'pod_date']
    available = [c for c in detail_cols if c in df.columns]
    detail_df = df[available].head(50).copy()
    # Format dates
    for c in ['dn_create_date', 'pod_date']:
        if c in detail_df.columns:
            detail_df[c] = detail_df[c].dt.strftime('%Y-%m-%d')
    # Convert to list
    data_rows = [available] + detail_df.values.tolist()
    
    # Dynamic column widths
    col_count = len(available)
    width_per_col = 7.0 * inch / col_count
    col_widths = [width_per_col] * col_count
    
    detail_tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
    detail_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 7),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('BACKGROUND', (0,1), (-1,-1), colors.lightgrey),
        ('GRID', (0,0), (-1,-1), 0.3, colors.black),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(detail_tbl)
    story.append(PageBreak())
    
    # ----- PAGE 5: Top Customers, Data Quality, Insights -----
    story.append(Paragraph("INSIGHTS & DATA QUALITY", title_style))
    story.append(Spacer(1, 0.1*inch))
    
    # Top Customers
    story.append(Paragraph("Top 5 Customers by Revenue", heading3))
    cust_data = [["Customer", "Records", "Revenue (₨)"]] + [[r['customer_name'][:30], r['Records'], f"{r['Amount']:,.0f}"] for _, r in top_customers.iterrows()]
    cust_tbl = Table(cust_data, colWidths=[3*inch, 1.5*inch, 2*inch])
    cust_tbl.setStyle(TableStyle([('GRID', (0,0), (-1,-1), 0.5, colors.grey), ('BACKGROUND', (0,0), (-1,0), colors.darkgreen), ('TEXTCOLOR', (0,0), (-1,0), colors.white), ('ALIGN', (0,0), (-1,-1), 'CENTER')]))
    story.append(cust_tbl)
    story.append(Spacer(1, 0.3*inch))
    
    # Data Quality Report
    story.append(Paragraph("Data Quality & Validation", heading3))
    missing = df.isnull().sum().sum()
    date_valid = (df['good_issue_date'] >= df['dn_create_date']).all() and (df['pod_date'] >= df['good_issue_date']).all()
    quality_text = f"""
    <b>Missing Values:</b> {missing} (0 is perfect)<br/>
    <b>Date Logic (GI ≥ DN and POD ≥ GI):</b> {'<font color="green">PASSED ✓</font>' if date_valid else '<font color="red">FAILED ✗</font>'}<br/>
    <b>Unique Customers:</b> {df['customer_name'].nunique():,}<br/>
    <b>Unique Models:</b> {df['customer_model'].nunique():,}<br/>
    <b>Batch ID:</b> {df['upload_batch_id'].iloc[0] if 'upload_batch_id' in df else 'N/A'}
    """
    story.append(Paragraph(quality_text, normal))
    story.append(Spacer(1, 0.2*inch))
    
    # Key Takeaways
    story.append(Paragraph("Key Business Takeaways", heading3))
    top_div = div_summary.sort_values('Amount', ascending=False).iloc[0]['division'] if not div_summary.empty else 'N/A'
    top_city = city_summary.iloc[0]['ship_to_city'] if not city_summary.empty else 'N/A'
    insights = f"""
    1. <b>Top Performing Division:</b> {top_div} – contributes the highest revenue.<br/>
    2. <b>Leading Market:</b> {top_city} is the strongest city in terms of sales volume.<br/>
    3. <b>Operational Efficiency:</b> Average order-to-delivery cycle is <b>{avg_ogi + avg_gip:.1f}</b> days.<br/>
    4. <b>Status:</b> Over <b>{(status_counts.get('Delivered',0)/total_records)*100:.1f}%</b> of shipments are successfully delivered with completed PODs.
    """
    story.append(Paragraph(insights, normal))
    
    # Footer note
    story.append(Spacer(1, 0.5*inch))
    story.append(Paragraph("--- End of Report ---", centered))
    
    # ----- BUILD PDF -----
    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        page_num = canvas.getPageNumber()
        canvas.drawCentredString(5.5*inch, 0.75*inch, f"Page {page_num}")
        canvas.restoreState()
    
    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    print(f"✅ 5-page comprehensive PDF generated: {filename}")

# -----------------------------
# 5. RUN
# -----------------------------
if __name__ == "__main__":
    build_pdf("Comprehensive_5Page_Report.pdf")
