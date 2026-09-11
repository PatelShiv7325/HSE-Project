document.addEventListener("DOMContentLoaded", () => {
  fetch("/admin/dashboard/chart-data")
    .then((r) => r.json())
    .then((data) => {
      const navy = "#0B2545";
      const primary = "#1565C0";
      const teal = "#1FA98C";

      new Chart(document.getElementById("businessGrowthChart"), {
        type: "bar",
        data: {
          labels: data.business_growth.labels,
          datasets: [{
            label: "New Works",
            data: data.business_growth.values,
            backgroundColor: primary,
            borderRadius: 4,
          }],
        },
        options: {
          plugins: { legend: { display: false } },
          scales: { y: { beginAtZero: true } },
        },
      });

      new Chart(document.getElementById("departmentRevenueChart"), {
        type: "doughnut",
        data: {
          labels: data.department_revenue.labels,
          datasets: [{
            data: data.department_revenue.values,
            backgroundColor: [primary, teal, navy, "#9AD1C6", "#7EA8E0"],
          }],
        },
        options: { plugins: { legend: { position: "bottom" } } },
      });
    });
});