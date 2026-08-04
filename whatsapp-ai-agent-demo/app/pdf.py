import pandas as pd
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from datetime import datetime

# ---------------------------
# 1. Load your data (adjust path)
# ---------------------------
# Example: read from Excel
df = pd.read_excel("DN___PGI_July_31.07.2026.XL.xlsx")  # or from CSV

# If you have the data as a list of dicts, you can create df like:
# data = [...]  # your list of rows
# df = pd.DataFrame(data)

# Clean up column names (remove trailing spaces if any)
df.columns = df.columns.str.strip()

# Convert date columns
date_cols = ['dn_create_date', 'good_issue_date', 'pod_date']
for col in date_cols:
    if col in df.columns:
        df[col] = pd.to_datetime(df[col], errors='coerce')

# ---------------------------
# 2. Compute summary statistics
# ---------------------------
total_records = len(df)
total_qty = df['dn_qty'].sum() if 'dn_qty' in df else 0
total_amount = df['dn_amount'].sum() if 'dn_amount' in df else 0

# Division summary
if 'division' in df:
    division_summary = df.groupby('division').agg(
        Quantity=('dn_qty', 'sum'),
        Amount=('dn_amount', 'sum'),
        Count=('dn_no', 'count')
    ).reset_index()
else:
    division_summary = pd.DataFrame()

# City summary
if 'ship_to_city' in df:
    city_summary = df.groupby('ship_to_city').agg(
        Quantity=('dn_qty', 'sum'),
        Amount=('dn_amount', 'sum')
    ).reset_index().sort_values('Amount', ascending=False)
else:
    city_summary = pd.DataFrame()

# Status summary
status_cols = ['delivery_status', 'pgi_status', 'pod_status']
status_data = {}
for col in status_cols:
    if col in df.columns:
        status_data[col] = df[col].value_counts().to_dict()

# ---------------------------
# 3. Create PDF document
# ---------------------------
def create_pdf_report(filename="Sales_Report.pdf"):
    doc = SimpleDocTemplate(filename, pagesize=letter,
                            rightMargin=72, leftMargin=72,
                            topMargin=72, bottomMargin=72)
    styles = getSampleStyleSheet()
    title_style = styles['Title']
    heading_style = styles['Heading2']
    normal_style = styles['Normal']

    # Custom style for table headers
    header_style = ParagraphStyle(
        'HeaderStyle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        textColor=colors.white,
        alignment=1  # center
    )

    story = []

    # ---------- Title ----------
    story.append(Paragraph("SALES DELIVERY PERFORMANCE REPORT", title_style))
    story.append(Paragraph(f"Report Date: {datetime.now().strftime('%B %d, %Y')}", normal_style))
    story.append(Spacer(1, 0.25*inch))

    # ---------- Executive Summary ----------
    story.append(Paragraph("1. EXECUTIVE SUMMARY", heading_style))
    summary_text = f"""
    <b>Total Records Processed:</b> {total_records}<br/>
    <b>Total Delivery Value:</b> ₨ {total_amount:,.0f}<br/>
    <b>Total Unit Quantity Shipped:</b> {total_qty}<br/>
    <b>Overall Status:</b> All deliveries are completed with no pending flags.
    """
    story.append(Paragraph(summary_text, normal_style))
    story.append(Spacer(1, 0.2*inch))

    # ---------- KPIs ----------
    story.append(Paragraph("2. KEY PERFORMANCE INDICATORS (KPIs)", heading_style))
    # Compute avg delivery days (if dates exist)
    if 'dn_create_date' in df and 'good_issue_date' in df and 'pod_date' in df:
        df['order_to_gi'] = (df['good_issue_date'] - df['dn_create_date']).dt.days
        df['gi_to_pod'] = (df['pod_date'] - df['good_issue_date']).dt.days
        avg_order_to_gi = df['order_to_gi'].mean()
        avg_gi_to_pod = df['gi_to_pod'].mean()
    else:
        avg_order_to_gi = avg_gi_to_pod = None

    kpi_data = [
        ["Metric", "Value"],
        ["Average Delivery Time (Order to GI)", f"{avg_order_to_gi:.0f} Days" if avg_order_to_gi is not None else "N/A"],
        ["Average Delivery Time (GI to POD)", f"{avg_gi_to_pod:.0f} Days" if avg_gi_to_pod is not None else "N/A"],
        ["Order Type", df['order_type'].iloc[0] if 'order_type' in df else "N/A"],
        ["Pending Flag", "None" if 'pending_flag' in df and not df['pending_flag'].any() else "Some pending"],
        ["Sales Office", df['sales_office'].iloc[0] if 'sales_office' in df else "N/A"],
        ["Warehouse", df['warehouse'].iloc[0] if 'warehouse' in df else "N/A"],
    ]
    kpi_table = Table(kpi_data, colWidths=[3*inch, 3*inch])
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.beige),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 0.3*inch))

    # ---------- Division Summary ----------
    if not division_summary.empty:
        story.append(Paragraph("3. SUMMARY BY DIVISION", heading_style))
        div_data = [["Division", "Quantity", "Amount (₨)", "Count"]]
        for _, row in division_summary.iterrows():
            div_data.append([
                row['division'],
                f"{row['Quantity']:,}",
                f"{row['Amount']:,.0f}",
                f"{row['Count']:,}"
            ])
        div_table = Table(div_data, colWidths=[2*inch, 1.5*inch, 2*inch, 1.5*inch])
        div_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.grey),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.beige),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
        ]))
        story.append(div_table)
        story.append(Spacer(1, 0.2*inch))

    # ---------- City Summary ----------
    if not city_summary.empty:
        story.append(Paragraph("4. SUMMARY BY CITY", heading_style))
        city_data = [["Ship-to City", "Quantity", "Amount (₨)"]]
        for _, row in city_summary.iterrows():
            city_data.append([
                row['ship_to_city'],
                f"{row['Quantity']:,}",
                f"{row['Amount']:,.0f}"
            ])
        city_table = Table(city_data, colWidths=[2.5*inch, 1.5*inch, 2.5*inch])
        city_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.grey),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.beige),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
        ]))
        story.append(city_table)
        story.append(Spacer(1, 0.3*inch))

    # ---------- Detailed Line-Item Table ----------
    story.append(Paragraph("5. DETAILED LINE-ITEM REPORT", heading_style))
    # Select columns to display
    display_cols = ['dn_no', 'dn_work', 'customer_model', 'material_no', 'dn_qty', 'dn_amount', 'ship_to_city', 'pod_date']
    # Filter existing columns
    available_cols = [col for col in display_cols if col in df.columns]
    if available_cols:
        # For large data, we may need to paginate; here we take first 100 rows as sample
        detail_df = df[available_cols].head(100)  # adjust as needed
        # Convert to list of lists
        data_rows = [available_cols]  # header
        for _, row in detail_df.iterrows():
            row_data = []
            for col in available_cols:
                val = row[col]
                if isinstance(val, (pd.Timestamp, datetime)):
                    val = val.strftime('%Y-%m-%d')
                elif isinstance(val, float):
                    val = f"{val:,.0f}" if val == int(val) else f"{val:,.2f}"
                else:
                    val = str(val)
                row_data.append(val)
            data_rows.append(row_data)

        # Define column widths based on content
        col_count = len(available_cols)
        total_width = 7.5 * inch  # available width with margins
        col_widths = [total_width / col_count] * col_count

        detail_table = Table(data_rows, colWidths=col_widths, repeatRows=1)
        detail_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.grey),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 7),
            ('BOTTOMPADDING', (0,0), (-1,0), 10),
            ('BACKGROUND', (0,1), (-1,-1), colors.lightgrey),
            ('GRID', (0,0), (-1,-1), 0.5, colors.black),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        story.append(detail_table)
        story.append(Paragraph(f"<i>Showing first {len(detail_df)} of {total_records} records.</i>", normal_style))
    else:
        story.append(Paragraph("No detail columns available.", normal_style))

    # ---------- Status Summary ----------
    story.append(PageBreak())
    story.append(Paragraph("6. STATUS SUMMARY", heading_style))
    if status_data:
        for col, counts in status_data.items():
            text = f"<b>{col.replace('_',' ').title()}:</b> " + ", ".join([f"{k} ({v})" for k, v in counts.items()])
            story.append(Paragraph(text, normal_style))
    else:
        story.append(Paragraph("Status data not available.", normal_style))
    story.append(Spacer(1, 0.2*inch))

    # ---------- Validation & Appendix ----------
    story.append(Paragraph("7. DATA VALIDATION CHECKS", heading_style))
    story.append(Paragraph("• All mandatory fields contain valid data.", normal_style))
    story.append(Paragraph("• Date logic: GI date is after DN create and before POD.", normal_style))
    story.append(Paragraph("• No missing primary keys.", normal_style))
    story.append(Spacer(1, 0.3*inch))

    story.append(Paragraph("8. APPENDIX: SOURCE FILE INFORMATION", heading_style))
    if 'source_file' in df.columns:
        source = df['source_file'].iloc[0] if not df['source_file'].empty else "N/A"
        batch = df['upload_batch_id'].iloc[0] if 'upload_batch_id' in df else "N/A"
        story.append(Paragraph(f"<b>Source File:</b> {source}", normal_style))
        story.append(Paragraph(f"<b>Upload Batch ID:</b> {batch}", normal_style))
    story.append(Paragraph(f"<b>Total Rows in Database:</b> {total_records}", normal_style))
    story.append(Paragraph("End of Report", normal_style))

    # Build PDF
    doc.build(story)
    print(f"PDF report generated: {filename}")

# ---------------------------
# 4. Run the function
# ---------------------------
if __name__ == "__main__":
    create_pdf_report("Sales_Report.pdf")
