# ==========================================================
# FILE: app/services/report_generator_service.py
# ==========================================================

from typing import Dict, Any, List, Optional
from datetime import date, datetime


class ReportGeneratorService:
    """
    Report generator service.
    ONLY this service formats data for WhatsApp output.
    All other services return raw data dictionaries.
    """
    
    def format_response(
        self,
        data: Dict[str, Any],
        intent: 'IntentType',
        format_type: str = "whatsapp"
    ) -> str:
        """Format response based on intent and data"""
        
        if format_type != "whatsapp":
            return str(data)
        
        # Check for error
        if data.get("error"):
            return self._format_error(data["error"])
        
        # Route to appropriate formatter
        intent_str = intent.value if hasattr(intent, 'value') else str(intent)
        
        formatters = {
            "dn_lookup": self._format_dn_intelligence,
            "dn_timeline": self._format_dn_timeline,
            "dn_products": self._format_dn_products,
            "dealer_dashboard": self._format_dealer_dashboard,
            "dealer_ranking": self._format_dealer_ranking,
            "product_dashboard": self._format_product_dashboard,
            "product_ranking": self._format_product_ranking,
            "warehouse_dashboard": self._format_warehouse_dashboard,
            "warehouse_ranking": self._format_warehouse_ranking,
            "city_dashboard": self._format_city_dashboard,
            "city_ranking": self._format_city_ranking,
            "executive_kpi": self._format_executive_kpi,
            "revenue_analysis": self._format_revenue_analysis,
            "revenue_at_risk": self._format_revenue_at_risk,
            "pod_pending": self._format_pod_pending,
            "pgi_pending": self._format_pgi_pending,
            "control_tower": self._format_control_tower,
            "help": lambda d: self._get_help_message(),
        }
        
        formatter = formatters.get(intent_str)
        if formatter:
            return formatter(data)
        
        # Default: pretty print dict
        return self._format_dict(data)
    
    # ==========================================================
    # DN FORMATTERS
    # ==========================================================
    
    def _format_dn_intelligence(self, data: Dict) -> str:
        """Format complete DN intelligence report"""
        return f"""╔══════════════════════════════════════════════════════════════════════════════╗
║                         📦 DN COMPLETE INTELLIGENCE REPORT                                 ║
║                                    {data.get('dn_no', 'N/A')}                                            ║
╚══════════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *DN SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Dealer: {data.get('dealer', 'N/A')}
   • City: {data.get('city', 'N/A')}
   • Warehouse: {data.get('warehouse', 'N/A')}
   • Division: {data.get('division', 'N/A')}
   • Status: {data.get('status_icon', '')} {data.get('status', 'N/A')}
   • Delay: {data.get('delay_icon', '')} {data.get('delay_bucket', 'N/A')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AGING & SLA ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Delivery Aging: {data.get('delivery_aging', 0)} days ({data.get('delivery_sla', {}).get('icon', '')} {data.get('delivery_sla', {}).get('status', 'N/A')})
   • Pending Delivery: {data.get('pending_delivery_aging', 0)} days
   • POD Aging: {data.get('pod_aging', 0)} days ({data.get('pod_sla', {}).get('icon', '')} {data.get('pod_sla', {}).get('status', 'N/A')})
   • Pending POD: {data.get('pending_pod_aging', 0)} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *SCORES*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Health Score: {data.get('health_score', 0)}/100
   • Risk Score: {data.get('risk_score', 0)}/100 ({data.get('risk_level', 'N/A')})

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{self._format_list(data.get('recommendations', []))}

💡 Type "timeline" for journey details, "products" for items in this DN"""
    
    def _format_dn_timeline(self, data: Dict) -> str:
        """Format DN timeline"""
        events = data.get('events', [])
        if not events:
            return "No timeline events available"
        
        response = "📅 *DN JOURNEY TIMELINE*\n\n"
        for i, event in enumerate(events, 1):
            date_str = self._format_date(event.get('date'))
            response += f"{i}. {event.get('icon', '📌')} *{event.get('stage', 'Event')}*\n"
            response += f"   📅 {date_str}\n"
            if event.get('aging_days'):
                response += f"   ⏱️ After {event['aging_days']} days\n"
            response += "\n"
        
        return response
    
    def _format_dn_products(self, data: List) -> str:
        """Format DN products"""
        if not data:
            return "No products found in this DN"
        
        response = "📦 *PRODUCTS IN DN*\n\n"
        total_qty = 0
        total_value = 0
        
        for p in data:
            qty = p.get('qty', 0)
            value = p.get('value', 0)
            response += f"• *{p.get('product', 'Unknown')}*\n"
            response += f"  📦 {qty:,.0f} units | 💰 Rs {value:,.2f}\n\n"
            total_qty += qty
            total_value += value
        
        response += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        response += f"📊 *TOTAL:* {total_qty:,.0f} units | Rs {total_value:,.2f}"
        
        return response
    
    # ==========================================================
    # DEALER FORMATTERS
    # ==========================================================
    
    def _format_dealer_dashboard(self, data: Dict) -> str:
        """Format dealer dashboard"""
        return f"""🏪 *DEALER DASHBOARD: {data.get('dealer', 'N/A')}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total DNs: {data.get('total_dns', 0)}
   • Completed: {data.get('completed_dns', 0)}
   • POD Pending: {data.get('pod_pending', 0)}
   • Completion Rate: {data.get('completion_rate', 0):.1f}%
   • Health Score: {data.get('health_score', 0):.1f}/100

💰 *FINANCIAL METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Value: Rs {data.get('total_value', 0):,.2f}
   • Pending Value: Rs {data.get('pending_value', 0):,.2f}
   • Realized Value: Rs {data.get('realized_value', 0):,.2f}

📈 *TRENDING*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Last 30 Days: Rs {data.get('last_30_days', 0):,.2f}
   • Growth: {data.get('growth_percentage', 0):+.1f}%

💡 Type "Follow up" to see action items for this dealer"""
    
    def _format_dealer_ranking(self, data: List) -> str:
        """Format dealer ranking"""
        if not data:
            return "No dealer ranking data available"
        
        response = "🏆 *TOP 10 DEALERS*\n\n"
        for i, d in enumerate(data[:10], 1):
            response += f"{i}. *{d.get('name', 'N/A')[:35]}*\n"
            response += f"   💰 Rs {d.get('total_value', 0):,.2f}\n"
            response += f"   📦 {d.get('total_dns', 0)} DNs"
            if d.get('completion_rate'):
                response += f" | ✅ {d['completion_rate']:.0f}%"
            response += "\n\n"
        
        return response
    
    # ==========================================================
    # PRODUCT FORMATTERS
    # ==========================================================
    
    def _format_product_dashboard(self, data: Dict) -> str:
        """Format product dashboard"""
        fill_icon = "🟢" if data.get('fill_rate', 0) >= 80 else "🟡" if data.get('fill_rate', 0) >= 50 else "🔴"
        
        return f"""📦 *PRODUCT DASHBOARD: {data.get('product', 'N/A')}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *ORDER SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Ordered Qty: {data.get('ordered_qty', 0):,.0f}
   • Delivered Qty: {data.get('delivered_qty', 0):,.0f} ✅
   • Pending Qty: {data.get('pending_qty', 0):,.0f} ⏳
   • Fill Rate: {fill_icon} {data.get('fill_rate', 0):.1f}%

💰 *VALUE SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Value: Rs {data.get('total_value', 0):,.2f}
   • Total DNs: {data.get('total_dns', 0)}
   • Unique Dealers: {data.get('unique_dealers', 0)}"""
    
    def _format_product_ranking(self, data: List) -> str:
        """Format product ranking"""
        if not data:
            return "No product ranking data available"
        
        response = "🏆 *TOP 10 PRODUCTS*\n\n"
        for i, p in enumerate(data[:10], 1):
            response += f"{i}. *{p.get('product', 'N/A')[:35]}*\n"
            response += f"   💰 Rs {p.get('total_value', 0):,.2f}\n"
            response += f"   📦 {p.get('total_qty', 0):,.0f} units | {p.get('total_dns', 0)} DNs\n\n"
        
        return response
    
    # ==========================================================
    # WAREHOUSE FORMATTERS
    # ==========================================================
    
    def _format_warehouse_dashboard(self, data: Dict) -> str:
        """Format warehouse dashboard"""
        return f"""🏭 *WAREHOUSE DASHBOARD: {data.get('warehouse', 'N/A')}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total DNs: {data.get('total_dns', 0)}
   • Completed: {data.get('completed_dns', 0)}
   • Completion Rate: {data.get('completion_rate', 0):.1f}%
   • Avg Lead Time: {data.get('avg_lead_time', 0):.1f} days
   • Efficiency: {data.get('efficiency', 0):.1f}%

💰 *FINANCIAL METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Value: Rs {data.get('total_value', 0):,.2f}
   • Pending Value: Rs {data.get('pending_value', 0):,.2f}

📋 *POD PERFORMANCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • POD Compliance: {data.get('pod_compliance', 0):.1f}%
   • Avg POD Time: {data.get('avg_pod_time', 0):.1f} days"""
    
    def _format_warehouse_ranking(self, data: List) -> str:
        """Format warehouse ranking"""
        if not data:
            return "No warehouse ranking data available"
        
        response = "🏭 *TOP 10 WAREHOUSES*\n\n"
        for i, w in enumerate(data[:10], 1):
            response += f"{i}. *{w.get('warehouse', 'N/A')[:35]}*\n"
            response += f"   💰 Rs {w.get('total_value', 0):,.2f}\n"
            response += f"   📦 {w.get('total_dns', 0)} DNs"
            if w.get('completion_rate'):
                response += f" | ✅ {w['completion_rate']:.0f}%"
            response += "\n\n"
        
        return response
    
    # ==========================================================
    # CITY FORMATTERS
    # ==========================================================
    
    def _format_city_dashboard(self, data: Dict) -> str:
        """Format city dashboard"""
        return f"""🌆 *CITY DASHBOARD: {data.get('city', 'N/A')}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total DNs: {data.get('total_dns', 0)}
   • Completed: {data.get('completed_dns', 0)}
   • Completion Rate: {data.get('completion_rate', 0):.1f}%

💰 *FINANCIAL METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Value: Rs {data.get('total_value', 0):,.2f}
   • Pending Value: Rs {data.get('pending_value', 0):,.2f}
   • Risk Score: {data.get('risk_score', 0):.1f}/100

📈 *GROWTH METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Active Dealers: {data.get('active_dealers', 0)}
   • Avg Delivery Time: {data.get('avg_delivery_time', 0):.1f} days"""
    
    def _format_city_ranking(self, data: List) -> str:
        """Format city ranking"""
        if not data:
            return "No city ranking data available"
        
        response = "🌆 *TOP 10 CITIES*\n\n"
        for i, c in enumerate(data[:10], 1):
            response += f"{i}. *{c.get('city', 'N/A')[:35]}*\n"
            response += f"   💰 Rs {c.get('total_value', 0):,.2f}\n"
            response += f"   📦 {c.get('total_dns', 0)} DNs"
            if c.get('completion_rate'):
                response += f" | ✅ {c['completion_rate']:.0f}%"
            response += "\n\n"
        
        return response
    
    # ==========================================================
    # EXECUTIVE FORMATTERS
    # ==========================================================
    
    def _format_executive_kpi(self, data: Dict) -> str:
        """Format executive KPI dashboard"""
        return f"""👑 *EXECUTIVE KPI DASHBOARD*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *TODAY'S PERFORMANCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Sales Today: Rs {data.get('sales_today', 0):,.2f}
   • DNs Created: {data.get('dns_created_today', 0)}
   • DNs Delivered: {data.get('dns_delivered_today', 0)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *MONTH-TO-DATE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Sales MTD: Rs {data.get('sales_mtd', 0):,.2f}
   • Target Progress: {data.get('target_progress', 0):.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *CRITICAL METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • DNs Pending: {data.get('dns_pending', 0)}
   • POD Pending: {data.get('pod_pending', 0)}
   • Revenue at Risk: Rs {data.get('revenue_at_risk', 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *OVERALL HEALTH*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Network Health: {data.get('network_health', 0):.1f}/100
   • SLA Compliance: {data.get('sla_compliance', 0):.1f}%

💡 Type "Network health" for detailed analysis"""
    
    # ==========================================================
    # REVENUE FORMATTERS
    # ==========================================================
    
    def _format_revenue_analysis(self, data: Dict) -> str:
        """Format revenue analysis"""
        return f"""💰 *REVENUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Revenue: Rs {data.get('total_revenue', 0):,.2f}
   • Realized: Rs {data.get('realized_revenue', 0):,.2f} ✅
   • Pending Delivery: Rs {data.get('pending_delivery', 0):,.2f} ⏳
   • POD Pending: Rs {data.get('pod_pending', 0):,.2f} 📋

📈 *REALIZATION RATE: {data.get('realization_rate', 0):.1f}%*

💡 Focus on pending POD collection to improve realization."""
    
    def _format_revenue_at_risk(self, data: Dict) -> str:
        """Format revenue at risk"""
        return f"""⚠️ *REVENUE AT RISK*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *EXPOSURE BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total at Risk: Rs {data.get('total_at_risk', 0):,.2f}
   • Pending Delivery: Rs {data.get('pending_delivery', 0):,.2f}
   • POD Pending: Rs {data.get('pod_pending', 0):,.2f}
   • High-Risk Dealers: {data.get('high_risk_dealers', 0)}

🎯 *TOP RISK DEALERS*
{self._format_list(data.get('top_risk_dealers', []), max_items=5)}

💡 Type "Follow up" for action items."""
    
    # ==========================================================
    # POD/PGI FORMATTERS
    # ==========================================================
    
    def _format_pod_pending(self, data: List) -> str:
        """Format pending POD list"""
        if not data:
            return "✅ No pending PODs found."
        
        response = "📋 *PENDING PODs*\n\n"
        for i, pod in enumerate(data[:15], 1):
            response += f"{i}. *DN: {pod.get('dn_no', 'N/A')}*\n"
            response += f"   🏪 {pod.get('dealer', 'N/A')[:30]}\n"
            response += f"   💰 Rs {pod.get('value', 0):,.2f}\n"
            response += f"   ⏱️ {pod.get('pending_days', 0)} days pending\n\n"
        
        if len(data) > 15:
            response += f"\n*+{len(data) - 15} more pending PODs*"
        
        return response
    
    def _format_pgi_pending(self, data: List) -> str:
        """Format pending PGI list"""
        if not data:
            return "✅ No pending PGI found."
        
        response = "⏳ *PENDING PGI DNs*\n\n"
        for i, pgi in enumerate(data[:15], 1):
            response += f"{i}. *DN: {pgi.get('dn_no', 'N/A')}*\n"
            response += f"   🏪 {pgi.get('dealer', 'N/A')[:30]}\n"
            response += f"   💰 Rs {pgi.get('value', 0):,.2f}\n"
            response += f"   ⏱️ {pgi.get('pending_days', 0)} days pending\n\n"
        
        if len(data) > 15:
            response += f"\n*+{len(data) - 15} more pending DNs*"
        
        return response
    
    # ==========================================================
    # CONTROL TOWER FORMATTERS
    # ==========================================================
    
    def _format_control_tower(self, data: Dict) -> str:
        """Format control tower dashboard"""
        return f"""🚨 *CONTROL TOWER | CRITICAL ALERTS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ *TIME-SENSITIVE ALERTS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Critical DNs: {data.get('critical_dns_count', 0)}
   • Severe Delays: {data.get('severe_delays_count', 0)}
   • High-Risk DNs: {data.get('high_risk_count', 0)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *POD ALERTS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Critical PODs: {data.get('critical_pods_count', 0)}
   • Pending >15 days: {data.get('pending_pod_over_15', 0)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIAL ALERTS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • High-Value Pending: {data.get('high_value_pending_count', 0)}
   • Value at Risk: Rs {data.get('value_at_risk', 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 *TOP 5 CRITICAL DNs*
{self._format_list(data.get('critical_dns', []), max_items=5)}

Type "Critical DNs" for full list."""
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    def _format_list(self, items: List, max_items: int = 10) -> str:
        """Format a list of items"""
        if not items:
            return "   • None"
        
        result = ""
        for i, item in enumerate(items[:max_items], 1):
            if isinstance(item, dict):
                result += f"   {i}. {item.get('name', item.get('dn_no', 'N/A'))}\n"
                if item.get('value'):
                    result += f"      💰 Rs {item['value']:,.2f}\n"
                if item.get('pending_days'):
                    result += f"      ⏱️ {item['pending_days']} days\n"
            else:
                result += f"   {i}. {item}\n"
            result += "\n"
        
        if len(items) > max_items:
            result += f"\n*+{len(items) - max_items} more items*"
        
        return result
    
    def _format_date(self, date_val) -> str:
        """Format date for display"""
        if not date_val:
            return "N/A"
        if isinstance(date_val, datetime):
            return date_val.strftime("%d-%b-%Y")
        if isinstance(date_val, date):
            return date_val.strftime("%d-%b-%Y")
        return str(date_val)
    
    def _format_dict(self, data: Dict, indent: int = 0) -> str:
        """Pretty print dictionary"""
        if not data:
            return "No data available"
        
        result = ""
        prefix = "   " * indent
        for key, value in data.items():
            if isinstance(value, dict):
                result += f"{prefix}• *{key.replace('_', ' ').title()}:*\n"
                result += self._format_dict(value, indent + 1)
            elif isinstance(value, list):
                result += f"{prefix}• *{key.replace('_', ' ').title()}:*\n"
                for item in value[:5]:
                    if isinstance(item, dict):
                        result += f"{prefix}   • {item.get('name', item.get('dn_no', 'N/A'))}\n"
                    else:
                        result += f"{prefix}   • {item}\n"
                if len(value) > 5:
                    result += f"{prefix}   • *+{len(value) - 5} more*\n"
            else:
                if isinstance(value, (int, float)):
                    if 'value' in key or 'revenue' in key or 'amount' in key:
                        result += f"{prefix}• *{key.replace('_', ' ').title()}:* Rs {value:,.2f}\n"
                    else:
                        result += f"{prefix}• *{key.replace('_', ' ').title()}:* {value:,}\n"
                else:
                    result += f"{prefix}• *{key.replace('_', ' ').title()}:* {value}\n"
        
        return result
    
    def _format_error(self, error: str) -> str:
        """Format error message"""
        return f"""⚠️ *Error*

{error}

💡 Try:
   • "Help" for available commands
   • "DN <number>" for DN status
   • "Top dealers" for rankings"""
    
    def _get_help_message(self) -> str:
        """Get help message"""
        from app.services.ai_query_service import WELCOME_MESSAGE
        return WELCOME_MESSAGE
