document.addEventListener("DOMContentLoaded", () => {
  fetch("/admin/dashboard/chart-data")
    .then((r) => r.json())
    .then((data) => {
      const navy = "#0B2545";
      const primary = "#1657C7";
      const teal = "#0F9D71";
      const orange = "#F5A623";
      const green = "#22C55E";
      const purple = "#7C3AED";
      const palette = [primary, teal, navy, "#7EA8E0", "#9AD1C6", "#B7791F", "#DC2626"];

      Chart.defaults.font.family = "Inter, sans-serif";
      Chart.defaults.color = "#64748B";

      // Small helper: only build a chart if its <canvas> exists on this page,
      // since not every dashboard page includes every chart.
      const el = (id) => document.getElementById(id);

      // --- Plugin: draws two lines of text in the middle of a doughnut chart ---
      const centerTextPlugin = {
        id: "centerText",
        afterDraw(chart) {
          if (chart.config.type !== "doughnut") return;
          const { ctx, chartArea } = chart;
          if (!chartArea) return;
          const cx = (chartArea.left + chartArea.right) / 2;
          const cy = (chartArea.top + chartArea.bottom) / 2;
          ctx.save();
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillStyle = navy;
          ctx.font = "700 18px Inter, sans-serif";
          ctx.fillText("Revenue", cx, cy - 10);
          ctx.fillStyle = "#64748B";
          ctx.font = "400 11px Inter, sans-serif";
          ctx.fillText("Departments", cx, cy + 10);
          ctx.restore();
        },
      };

      // --- Plugin: writes the numeric value at the end of each horizontal bar ---
      const barValueLabelsPlugin = {
        id: "barValueLabels",
        afterDatasetsDraw(chart) {
          if (chart.config.options.indexAxis !== "y") return;
          const { ctx } = chart;
          chart.data.datasets.forEach((dataset, di) => {
            const meta = chart.getDatasetMeta(di);
            meta.data.forEach((bar, i) => {
              const value = dataset.data[i];
              if (!value) return;
              ctx.save();
              ctx.fillStyle = "#fff";
              ctx.font = "600 11px Inter, sans-serif";
              ctx.textAlign = "right";
              ctx.textBaseline = "middle";
              ctx.fillText(value, bar.x - 6, bar.y);
              ctx.restore();
            });
          });
        },
      };

      Chart.register(centerTextPlugin, barValueLabelsPlugin);

      // ---------------------------------------------------------------
      // Business Growth (bar) — new leads per month, last 12 months
      // ---------------------------------------------------------------
      if (el("businessGrowthChart")) {
        new Chart(el("businessGrowthChart"), {
          type: "bar",
          data: {
            labels: data.business_growth.labels,
            datasets: [{
              label: "New Works",
              data: data.business_growth.values,
              backgroundColor: primary,
              borderRadius: 4,
              maxBarThickness: 28,
            }],
          },
          options: {
            plugins: { legend: { display: false } },
            scales: {
              y: { beginAtZero: true, grid: { color: "#E5E9F0" } },
              x: { grid: { display: false } },
            },
          },
        });
      }

      // ---------------------------------------------------------------
      // Department Revenue (donut) — received-payment total per department
      // ---------------------------------------------------------------
      if (el("departmentRevenueChart")) {
        new Chart(el("departmentRevenueChart"), {
          type: "doughnut",
          data: {
            labels: data.department_revenue.labels,
            datasets: [{
              data: data.department_revenue.values,
              backgroundColor: palette,
              borderWidth: 0,
            }],
          },
          options: {
            cutout: "68%",
            plugins: { legend: { position: "bottom", labels: { boxWidth: 10, padding: 14 } } },
          },
        });
      }

      // ---------------------------------------------------------------
      // Role Wise Pending Load (horizontal bar, 0-100 scale)
      // ---------------------------------------------------------------
      if (el("roleLoadChart")) {
        new Chart(el("roleLoadChart"), {
          type: "bar",
          data: {
            labels: data.role_load.labels,
            datasets: [{
              data: data.role_load.values,
              backgroundColor: orange,
              borderRadius: 4,
              maxBarThickness: 18,
            }],
          },
          options: {
            indexAxis: "y",
            plugins: { legend: { display: false } },
            scales: {
              x: { beginAtZero: true, max: 100, grid: { color: "#E5E9F0" } },
              y: { grid: { display: false } },
            },
          },
        });
      }

      // ---------------------------------------------------------------
      // Department Overdue Load (vertical bar) — overdue stages per department
      // ---------------------------------------------------------------
      if (el("departmentOverdueChart")) {
        new Chart(el("departmentOverdueChart"), {
          type: "bar",
          data: {
            labels: data.department_overdue.labels,
            datasets: [{
              data: data.department_overdue.values,
              backgroundColor: data.department_overdue.labels.map((_, i) => palette[i % palette.length]),
              borderRadius: 4,
              maxBarThickness: 32,
            }],
          },
          options: {
            plugins: { legend: { display: false } },
            scales: {
              y: { beginAtZero: true, grid: { color: "#E5E9F0" } },
              x: { grid: { display: false } },
            },
          },
        });
      }

      // ---------------------------------------------------------------
      // Payment Trend (line) — total logged payment amount per month
      // ---------------------------------------------------------------
      if (el("paymentTrendChart")) {
        new Chart(el("paymentTrendChart"), {
          type: "line",
          data: {
            labels: data.payment_trend.labels,
            datasets: [{
              data: data.payment_trend.values,
              borderColor: green,
              backgroundColor: green,
              tension: 0.4,
              pointRadius: 0,
              borderWidth: 2,
            }],
          },
          options: {
            plugins: { legend: { display: false } },
            scales: {
              y: { beginAtZero: true, grid: { color: "#E5E9F0" } },
              x: { grid: { display: false } },
            },
          },
        });
      }

      // ---------------------------------------------------------------
      // Monthly Revenue (filled area) — received-only amount per month
      // ---------------------------------------------------------------
      if (el("monthlyRevenueChart")) {
        new Chart(el("monthlyRevenueChart"), {
          type: "line",
          data: {
            labels: data.monthly_revenue_trend.labels,
            datasets: [{
              data: data.monthly_revenue_trend.values,
              borderColor: purple,
              backgroundColor: "rgba(124, 58, 237, 0.15)",
              fill: true,
              tension: 0.4,
              pointRadius: 0,
              borderWidth: 2,
            }],
          },
          options: {
            plugins: { legend: { display: false } },
            scales: {
              y: { beginAtZero: true, grid: { color: "#E5E9F0" } },
              x: { grid: { display: false } },
            },
          },
        });
      }

      // ---------------------------------------------------------------
      // Overdue Trend (line) — overdue work-stage count per month, last 6 months
      // ---------------------------------------------------------------
      if (el("overdueTrendChart")) {
        new Chart(el("overdueTrendChart"), {
          type: "line",
          data: {
            labels: data.overdue_trend.labels,
            datasets: [{
              data: data.overdue_trend.values,
              borderColor: orange,
              backgroundColor: orange,
              tension: 0.4,
              pointRadius: 0,
              borderWidth: 2,
            }],
          },
          options: {
            plugins: { legend: { display: false } },
            scales: {
              y: { beginAtZero: true, grid: { color: "#E5E9F0" } },
              x: { grid: { display: false } },
            },
          },
        });
      }
      // ---------------------------------------------------------------
      // Employee Salary vs Work Value (grouped bar)
      // ---------------------------------------------------------------
      if (el("salaryVsWorkValueChart")) {
        new Chart(el("salaryVsWorkValueChart"), {
          type: "bar",
          data: {
            labels: data.salary_vs_work_value.labels,
            datasets: [
              {
                label: "Salary",
                data: data.salary_vs_work_value.salary,
                backgroundColor: "#EF4444",
                borderRadius: 3,
                maxBarThickness: 18,
              },
              {
                label: "Work Value",
                data: data.salary_vs_work_value.work_value,
                backgroundColor: "#22C55E",
                borderRadius: 3,
                maxBarThickness: 18,
              },
            ],
          },
          options: {
            plugins: { legend: { position: "bottom", labels: { boxWidth: 10, padding: 14 } } },
            scales: {
              y: { beginAtZero: true, grid: { color: "#E5E9F0" } },
              x: { grid: { display: false }, ticks: { maxRotation: 60, minRotation: 60, font: { size: 10 } } },
            },
          },
        });
      }
    });
});