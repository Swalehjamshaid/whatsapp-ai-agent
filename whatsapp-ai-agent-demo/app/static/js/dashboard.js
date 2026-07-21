// dashboard.js – Executive Command Center (v8.2)
// All data is fetched from /dashboard/api/data; no calculations are done here.

(function() {
    const API_URL = '/dashboard/api/data';

    // ---- Formatting helpers (only display, no logic) ----
    function formatCurrency(v) {
        if (v >= 1e6) return 'PKR ' + (v/1e6).toFixed(2) + 'M';
        if (v >= 1e3) return 'PKR ' + v.toLocaleString();
        return 'PKR ' + (v||0).toFixed(0);
    }
    function formatNumber(v) { return (v||0).toLocaleString(); }
    function formatPct(v) { return (v||0).toFixed(1) + '%'; }
    function formatDays(v) { return (v||0).toFixed(1) + ' days'; }

    // ---- Render KPI Cards ----
    function renderKPIs(cards) {
        const container = document.getElementById('kpiCards');
        container.innerHTML = Object.entries(cards).map(([key, card]) => `
            <div class="col-md-3 col-lg-3">
                <div class="card kpi-card">
                    <div class="card-body">
                        <h6 class="text-muted"><i class="fas ${card.icon}"></i> ${card.label}</h6>
                        <h3>${card.format === 'currency' ? formatCurrency(card.value) :
                             card.format === 'percentage' ? formatPct(card.value) :
                             card.format === 'days' ? formatDays(card.value) :
                             formatNumber(card.value)}</h3>
                        <small>Target: ${formatNumber(card.target)}</small>
                        <div class="progress mt-2"><div class="progress-bar" style="width:${Math.min(100, card.progress)}%"></div></div>
                    </div>
                </div>
            </div>
        `).join('');
    }

    // ---- Render Pipeline ----
    function renderPipeline(pipeline) {
        const container = document.getElementById('pipelineContainer');
        const stages = [
            { label: 'DN Created', value: pipeline.dn_created || 0 },
            { label: 'PGI Completed', value: pipeline.pgi_completed || 0, pct: pipeline.pgi_achievement || 0 },
            { label: 'Delivered', value: pipeline.delivered || 0, pct: pipeline.delivery_achievement || 0 },
            { label: 'POD Received', value: pipeline.pod_received || 0, pct: pipeline.pod_achievement || 0 },
        ];
        let html = '<div class="d-flex flex-wrap align-items-center justify-content-center">';
        stages.forEach((s, idx) => {
            html += `
                <div class="pipeline-stage">
                    <div class="stage-label">${s.label}</div>
                    <div class="stage-value">${formatNumber(s.value)}</div>
                    ${s.pct !== undefined ? `<small class="text-muted">${formatPct(s.pct)}</small>` : ''}
                </div>
            `;
            if (idx < stages.length - 1) html += `<span class="pipeline-arrow">→</span>`;
        });
        html += '</div>';
        container.innerHTML = html;
    }

    // ---- Render Division Dashboard ----
    function renderDivision(divisions) {
        const container = document.getElementById('divisionContainer');
        if (!divisions || !divisions.length) {
            container.innerHTML = '<div class="text-muted">No division data available.</div>';
            return;
        }
        let html = '<div class="d-flex flex-wrap">';
        divisions.forEach(d => {
            html += `
                <div class="division-tag">
                    <strong>${d.division}</strong><br>
                    Rev: ${formatCurrency(d.revenue)}<br>
                    PGI: ${formatPct(d.pgi_achievement)}<br>
                    Gap: ${formatPct(d.gap_percentage)}
                </div>
            `;
        });
        html += '</div>';
        container.innerHTML = html;
    }

    // ---- AG Grid helper ----
    function renderAGGrid(containerId, data, colDefs) {
        const gridDiv = document.getElementById(containerId);
        new agGrid.Grid(gridDiv, {
            columnDefs: colDefs,
            rowData: data,
            pagination: true,
            paginationPageSize: 10,
            domLayout: 'autoHeight',
        });
    }

    // ---- Plotly Charts (from JSON) ----
    function renderPlotlyCharts(containerId, chartDict) {
        const div = document.getElementById(containerId);
        div.innerHTML = '';
        Object.entries(chartDict).forEach(([key, jsonStr]) => {
            if (!jsonStr) return;
            const subDiv = document.createElement('div');
            subDiv.style.height = '300px';
            subDiv.style.width = '100%';
            div.appendChild(subDiv);
            try {
                const fig = JSON.parse(jsonStr);
                Plotly.newPlot(subDiv, fig.data, fig.layout);
            } catch(e) { console.warn('Plotly error', e); }
        });
    }

    // ---- Network (simple display) ----
    function renderNetwork(network) {
        const container = document.getElementById('networkContainer');
        container.innerHTML = `
            <p><strong>Nodes:</strong> ${network.nodes?.length || 0} &nbsp;|&nbsp; <strong>Edges:</strong> ${network.edges?.length || 0}</p>
            <pre style="max-height:300px; overflow:auto; background:#f8f9fa; padding:10px; border-radius:8px;">${JSON.stringify(network, null, 2)}</pre>
        `;
    }

    // ---- Alerts ----
    function renderAlerts(alerts) {
        const container = document.getElementById('alertsContainer');
        container.innerHTML = alerts.map(a => `
            <div class="alert alert-${a.level === 'critical' ? 'danger' : 'warning'}">
                <strong>${a.title}</strong> ${a.message}<br>
                <small>${a.action || ''}</small>
            </div>
        `).join('');
    }

    // ---- AI Recommendations ----
    function renderRecommendations(recs) {
        const container = document.getElementById('recommendationsContainer');
        container.innerHTML = recs.map(r => `
            <div class="card mb-2">
                <div class="card-body">
                    <h6><span class="badge bg-${r.priority === 'Critical' ? 'danger' : 'warning'}">${r.priority}</span> ${r.entity}</h6>
                    <p>${r.recommendation}</p>
                </div>
            </div>
        `).join('');
    }

    // ---- Upload History (AG Grid) ----
    function renderUploadsGrid(data) {
        const gridDiv = document.getElementById('uploadsGrid');
        if (window.uploadsGrid) { window.uploadsGrid.destroy(); }
        const colDefs = [
            { field: 'filename', headerName: 'File Name', flex: 2 },
            { field: 'uploaded_at', headerName: 'Uploaded At', flex: 1.5, valueFormatter: p => p.value ? new Date(p.value).toLocaleString() : 'N/A' },
            { field: 'rows', headerName: 'Rows', flex: 1 },
            { field: 'inserted', headerName: 'Inserted', flex: 1 },
            { field: 'skipped', headerName: 'Skipped', flex: 1 },
            { field: 'status', headerName: 'Status', flex: 1, cellStyle: p => p.value === 'Success' ? { color: '#198754', fontWeight: '600' } : { color: '#dc3545' } },
        ];
        window.uploadsGrid = new agGrid.Grid(gridDiv, {
            columnDefs: colDefs,
            rowData: data,
            pagination: true,
            paginationPageSize: 5,
            domLayout: 'autoHeight',
        });
        const placeholder = document.getElementById('uploadHistoryPlaceholder');
        if (placeholder) placeholder.style.display = data && data.length > 0 ? 'none' : 'block';
    }

    // ---- Master load function ----
    async function loadDashboard() {
        try {
            const res = await fetch(API_URL);
            const data = await res.json();

            // Render all sections
            renderKPIs(data.cards || {});
            renderPipeline(data.pipeline || {});
            renderDivision(data.division || []);

            // AG Grid: Warehouse
            const warehouseCols = [
                { field: 'warehouse_name', headerName: 'Warehouse' },
                { field: 'revenue', headerName: 'Revenue', valueFormatter: p => formatCurrency(p.value) },
                { field: 'pgi_achievement_rate', headerName: 'PGI %', valueFormatter: p => formatPct(p.value) },
                { field: 'pod_completion_rate', headerName: 'POD %', valueFormatter: p => formatPct(p.value) },
                { field: 'health_score', headerName: 'Health', valueFormatter: p => formatPct(p.value) },
                { field: 'risk_level', headerName: 'Risk' },
                { field: 'ranking', headerName: 'Rank' },
            ];
            renderAGGrid('warehouseGrid', data.warehouse || [], warehouseCols);

            // AG Grid: Dealer
            const dealerCols = [
                { field: 'dealer_name', headerName: 'Dealer' },
                { field: 'revenue', headerName: 'Revenue', valueFormatter: p => formatCurrency(p.value) },
                { field: 'units', headerName: 'Units' },
                { field: 'pod_completion_rate', headerName: 'POD %', valueFormatter: p => formatPct(p.value) },
                { field: 'health_score', headerName: 'Health', valueFormatter: p => formatPct(p.value) },
                { field: 'ranking', headerName: 'Rank' },
            ];
            renderAGGrid('dealerGrid', data.dealer || [], dealerCols);

            // AG Grid: Product
            const productCols = [
                { field: 'product_name', headerName: 'Product' },
                { field: 'revenue', headerName: 'Revenue', valueFormatter: p => formatCurrency(p.value) },
                { field: 'units', headerName: 'Units' },
                { field: 'abc_class', headerName: 'ABC' },
                { field: 'slow_moving_flag', headerName: 'Slow', cellRenderer: p => p.value ? '🐢' : '' },
                { field: 'fast_moving_flag', headerName: 'Fast', cellRenderer: p => p.value ? '🚀' : '' },
                { field: 'dead_stock_flag', headerName: 'Dead', cellRenderer: p => p.value ? '💀' : '' },
            ];
            renderAGGrid('productGrid', data.product || [], productCols);

            // AG Grid: City
            const cityCols = [
                { field: 'city', headerName: 'City' },
                { field: 'revenue', headerName: 'Revenue', valueFormatter: p => formatCurrency(p.value) },
                { field: 'units', headerName: 'Units' },
                { field: 'pod_completion_rate', headerName: 'POD %', valueFormatter: p => formatPct(p.value) },
                { field: 'health_score', headerName: 'Health', valueFormatter: p => formatPct(p.value) },
            ];
            renderAGGrid('cityGrid', data.city || [], cityCols);

            // Plotly Charts
            renderPlotlyCharts('warehouseCharts', data.warehouse_charts || {});
            renderPlotlyCharts('dealerCharts', data.dealer_charts || {});
            renderPlotlyCharts('productCharts', data.product_charts || {});
            renderPlotlyCharts('cityCharts', data.city_charts || {});

            // Monthly & Daily Trends (using raw data)
            const monthly = data.monthly_trends || {};
            if (monthly.months && monthly.months.length) {
                const trace1 = { x: monthly.months, y: monthly.revenue, name: 'Revenue', type: 'scatter', mode: 'lines+markers' };
                const trace2 = { x: monthly.months, y: monthly.delivery_notes, name: 'DN', type: 'scatter', mode: 'lines+markers' };
                Plotly.newPlot('monthlyTrends', [trace1, trace2], { title: 'Revenue & DN Trend', barmode: 'group' });
            } else {
                document.getElementById('monthlyTrends').innerHTML = '<p class="text-muted">No monthly data</p>';
            }
            const daily = data.daily_trends || {};
            if (daily.dates && daily.dates.length) {
                const trace1 = { x: daily.dates, y: daily.revenue, name: 'Revenue', type: 'scatter', mode: 'lines+markers' };
                const trace2 = { x: daily.dates, y: daily.delivery_notes, name: 'DN', type: 'scatter', mode: 'lines+markers' };
                Plotly.newPlot('dailyTrends', [trace1, trace2], { title: 'Daily Revenue & DN' });
            } else {
                document.getElementById('dailyTrends').innerHTML = '<p class="text-muted">No daily data</p>';
            }

            renderNetwork(data.network || {});
            renderAlerts(data.alerts || []);
            renderRecommendations(data.recommendations || []);
            renderUploadsGrid(data.latest_uploads || []);

            // Footer
            document.getElementById('footer').innerHTML = `
                <i class="fas fa-code-branch"></i> v8.2 &nbsp;|&nbsp;
                <i class="fas fa-database"></i> PostgreSQL &nbsp;|&nbsp;
                <i class="fas fa-sync"></i> ${new Date().toLocaleString()} &nbsp;|&nbsp;
                <i class="fas fa-cubes"></i> Records: ${data.metadata?.record_count || 0} &nbsp;|&nbsp;
                <i class="fas fa-cloud"></i> ${data.metadata?.environment || 'Production'}
            `;
            document.getElementById('lastRefresh').innerText = new Date().toLocaleString();

        } catch(e) {
            console.error('Dashboard load error:', e);
            document.body.innerHTML = '<div class="alert alert-danger m-3">Failed to load dashboard. See console.</div>';
        }
    }

    // ---- Theme toggle ----
    document.getElementById('themeToggle').addEventListener('click', function() {
        document.documentElement.setAttribute('data-theme',
            document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'
        );
        this.innerHTML = document.documentElement.getAttribute('data-theme') === 'dark' ? '<i class="fas fa-sun"></i>' : '<i class="fas fa-moon"></i>';
    });

    // ---- Sidebar toggle ----
    document.getElementById('hamburgerBtn').addEventListener('click', function() {
        document.querySelector('.sidebar').classList.toggle('open');
    });

    // ---- Today's date ----
    document.getElementById('todayDate').innerText = new Date().toLocaleDateString('en-GB', {
        weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'
    });

    // ---- Interactive Upload (preserved) ----
    document.getElementById('uploadForm').addEventListener('submit', function(e) {
        e.preventDefault();
        const formData = new FormData(this);
        const fileInput = this.querySelector('input[type="file"]');
        if (!fileInput.files.length) {
            Swal.fire({ icon: 'warning', title: 'No File Selected', text: 'Please choose an Excel file.' });
            return;
        }
        const xhr = new XMLHttpRequest();
        xhr.open('POST', this.action, true);
        Swal.fire({
            title: 'Uploading Logistics Report',
            html: `
                <div class="text-start mb-2"><i class="fas fa-spinner fa-spin me-2"></i> Processing...</div>
                <div class="progress"><div id="uploadProgressBar" class="progress-bar progress-bar-striped progress-bar-animated" style="width:0%;">0%</div></div>
                <div id="uploadStatusText" class="mt-2 text-muted">Starting...</div>
            `,
            allowOutsideClick: false, showConfirmButton: false
        });
        xhr.upload.onprogress = function(event) {
            if (event.lengthComputable) {
                const pct = Math.round((event.loaded / event.total) * 100);
                document.getElementById('uploadProgressBar').style.width = pct + '%';
                document.getElementById('uploadProgressBar').textContent = pct + '%';
                document.getElementById('uploadStatusText').textContent = pct < 100 ? `Uploading ${pct}%` : 'Processing...';
            }
        };
        xhr.onload = function() {
            if (xhr.status >= 200 && xhr.status < 300) {
                Swal.fire({ icon: 'success', title: 'Upload Successful', text: 'File imported. Dashboard will refresh.', timer: 2000, showConfirmButton: false })
                    .then(() => window.location.reload());
            } else {
                let msg = 'Upload failed.';
                try { const json = JSON.parse(xhr.responseText); if (json.message) msg = json.message; } catch(e) {}
                Swal.fire({ icon: 'error', title: 'Upload Failed', text: msg });
            }
        };
        xhr.onerror = function() { Swal.fire({ icon: 'error', title: 'Network Error' }); };
        xhr.send(formData);
    });

    // ---- Auto-refresh ----
    loadDashboard();
    setInterval(loadDashboard, 30000);
})();
