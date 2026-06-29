    
    revenue = business.get('total_revenue', 0)
    if revenue:
        lines.append(f"Total Revenue: PKR {revenue:,.0f}")
        lines.append(f"Avg per DN: PKR {business.get('avg_revenue_per_dn', 0):,.0f}")
    else:
        lines.append(f"Total Revenue: PKR 0")
    
    # ==========================================================
    # SECTION 3: DELIVERY STATUS
    # ==========================================================
    delivery = dashboard.get('delivery_status', {})
    lines.append("")
    lines.append("📦 *DELIVERY STATUS*")
    lines.append(f"✅ Delivered: {delivery.get('delivered', 0)} ({delivery.get('delivered_percent', 0)}%)")
    lines.append(f"🚚 In Transit: {delivery.get('in_transit', 0)} ({delivery.get('in_transit_percent', 0)}%)")
    lines.append(f"⏳ Pending PGI: {delivery.get('pending_pgi', 0)} ({delivery.get('pending_pgi_percent', 0)}%)")
    lines.append(f"📊 Total: {delivery.get('total', 0)}")
    
    # ==========================================================
    # SECTION 4: POD STATUS
    # ==========================================================
    pod = dashboard.get('pod_status', {})
    lines.append("")
    lines.append("📋 *POD STATUS*")
    lines.append(f"POD Completed: {pod.get('pod_completed', 0)}")
    lines.append(f"Pending POD: {pod.get('pending_pod', 0)}")
    lines.append(f"POD Compliance: {pod.get('pod_compliance', 0)}%")
    
    # ==========================================================
    # SECTION 5: PERFORMANCE KPIs
    # ==========================================================
    perf = dashboard.get('performance', {})
    lines.append("")
    lines.append("⚡ *PERFORMANCE*")
    lines.append(f"Delivery Rate: {perf.get('delivery_rate', 0)}%")
    lines.append(f"PGI Rate: {perf.get('pgi_rate', 0)}%")
    lines.append(f"POD Rate: {perf.get('pod_rate', 0)}%")
    lines.append(f"Health Score: {perf.get('health_score', 0)}/100")
    lines.append(f"Risk Level: {perf.get('risk_level', 'Unknown')}")
    lines.append(f"Performance Grade: {perf.get('performance_grade', 'N/A')}")
    
    # ==========================================================
    # SECTION 6: DISTANCE ANALYTICS
    # ==========================================================
    distance = dashboard.get('distance', {})
    lines.append("")
    lines.append("📍 *DISTANCE ANALYTICS*")
    lines.append(f"Warehouse: {distance.get('warehouse', 'N/A')}")
    lines.append(f"Dealer City: {distance.get('dealer_city', 'N/A')}")
    
    road_distance = distance.get('road_distance_km')
    lines.append("")
    lines.append(
        f"Road Distance: {road_distance:,.1f} KM"
        if road_distance is not None
        else "Road Distance: Unknown"
    )

    total_minutes = distance.get('estimated_minutes')
    if total_minutes is None and distance.get('estimated_hours') is not None:
        total_minutes = round(float(distance['estimated_hours']) * 60)

    if total_minutes is None:
        estimated_time = "Unknown"
    else:
        hours, minutes = divmod(int(total_minutes), 60)
        if hours and minutes:
            estimated_time = (
                f"{hours} Hour{'s' if hours != 1 else ''} {minutes} Minutes"
            )
        elif hours:
            estimated_time = f"{hours} Hour{'s' if hours != 1 else ''}"
        else:
            estimated_time = f"{minutes} Minutes"

    lines.append(f"Estimated Time: {estimated_time}")
    dealer_latitude = distance.get('dealer_latitude')
    dealer_longitude = distance.get('dealer_longitude')
    lines.append(
        f"Latitude: {dealer_latitude if dealer_latitude is not None else 'Unknown'}"
    )
    lines.append(
        f"Longitude: {dealer_longitude if dealer_longitude is not None else 'Unknown'}"
    )
    lines.append(f"Provider: {distance.get('provider', 'Unavailable')}")
    lines.append(f"Cached: {'Yes' if distance.get('cached') else 'No'}")
    lines.append(f"Category: {distance.get('distance_category', 'N/A')}")
    lines.append(f"Risk Score: {distance.get('risk_score', 50)}/100")
    
    # ==========================================================
    # SECTION 7: PRODUCT ANALYTICS
    # ==========================================================
    products = dashboard.get('products', {})
    lines.append("")
    lines.append("📦 *PRODUCTS*")
    lines.append(f"Total Products: {products.get('total_products', 0)}")
    lines.append(f"Top Product: {products.get('top_product', 'N/A')}")
    
    top_10 = products.get('top_10_products', [])
    if top_10:
        lines.append("")
        lines.append("🏆 *Top Products*")
        for i, p in enumerate(top_10[:5], 1):
            revenue = p.get('revenue', 0)
            share = p.get('revenue_share', 0)
            lines.append(f"{i}. {p.get('product', 'N/A')} (PKR {revenue:,.0f}, {share}%)")
    
    # ==========================================================
    # SECTION 8: AGING ANALYTICS
    # ==========================================================
    aging = dashboard.get('aging', {})
    lines.append("")
    lines.append("⏳ *AGING*")
    lines.append(f"Avg Delivery Days: {aging.get('avg_delivery_days', 0)}")
    lines.append(f"Avg POD Days: {aging.get('avg_pod_days', 0)}")
    lines.append(f"Avg Cycle Days: {aging.get('avg_cycle_days', 0)}")
    lines.append(f"Oldest Pending: {aging.get('oldest_pending_pod', 'N/A')}")
    
    # ==========================================================
    # SECTION 9: ALERTS
    # ==========================================================
    alerts = dashboard.get('alerts', [])
    if alerts:
        lines.append("")
        lines.append("🚨 *ALERTS*")
        for alert in alerts[:5]:
            severity = alert.get('severity', 'low')
            emoji = "🔴" if severity == 'critical' else "🟠" if severity == 'high' else "🟡"
            lines.append(f"{emoji} {alert.get('message', '')}")
    
    # ==========================================================
    # SECTION 10: EXECUTIVE SUMMARY
    # ==========================================================
    summary = dashboard.get('executive_summary', '')
    if summary:
        lines.append("")
        lines.append("📌 *EXECUTIVE SUMMARY*")
        for line in summary.split('\n'):
            lines.append(line)
    
    # ==========================================================
    # SECTION 11: MANAGEMENT INSIGHTS
    # ==========================================================
    insights = dashboard.get('insights', {})
    if insights:
        lines.append("")
        lines.append("💡 *MANAGEMENT INSIGHTS*")
        lines.append(f"✅ Strength: {insights.get('top_strength', 'N/A')}")
        lines.append(f"⚠️ Risk: {insights.get('biggest_risk', 'N/A')}")
        lines.append(f"🎯 Action: {insights.get('recommended_action', 'N/A')}")
        lines.append(f"📈 Impact: {insights.get('expected_impact', 'N/A')}")
    
    return "\n".join(lines)


# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'Dealer360Dashboard',
    'get_dealer_360_dashboard',
    'format_dealer_360_dashboard'
]

# ==========================================================
# END OF FILE - v5.0 FULLY INTEGRATED
# ==========================================================

