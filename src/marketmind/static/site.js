const buttons = document.querySelectorAll("[data-symbol]");
async function showMarket(symbol) {
  const svg = document.getElementById("sample-chart");
  document.getElementById("sample-symbol").textContent = symbol.replace(
    "USDT",
    " / USDT",
  );
  try {
    const response = await fetch(
      "/api/public-market?symbol=" + encodeURIComponent(symbol),
    );
    if (!response.ok) throw new Error("unavailable");
    const data = await response.json(),
      points = data.candles || [];
    if (!points.length) throw new Error("empty");
    const values = points.map((p) => Number(p.close));
    const min = Math.min(...values),
      max = Math.max(...values),
      range = max - min || 1;
    const coords = values.map(
      (value, i) =>
        `${10 + (i * 480) / Math.max(1, values.length - 1)},${190 - ((value - min) * 165) / range}`,
    );
    svg.innerHTML = `<path class="chart-grid" d="M10 30H490 M10 85H490 M10 140H490 M10 195H490"/><polyline class="chart-path" points="${coords.join(" ")}"/><circle class="chart-dot" cx="${coords.at(-1).split(",")[0]}" cy="${coords.at(-1).split(",")[1]}" r="5"/>`;
    document.getElementById("sample-price").textContent = values
      .at(-1)
      .toLocaleString("en-US", { maximumFractionDigits: 2 });
    document.getElementById("sample-date").textContent = new Date(
      points.at(-1).ts * 1000,
    ).toLocaleDateString("fa-IR");
  } catch (_) {
    svg.innerHTML = "";
    document.getElementById("sample-price").textContent = "—";
    document.getElementById("sample-date").textContent =
      "دادهٔ ذخیره‌شده در دسترس نیست";
  }
}
buttons.forEach((button) =>
  button.addEventListener("click", () => {
    buttons.forEach((item) =>
      item.classList.toggle("selected", item === button),
    );
    showMarket(button.dataset.symbol);
  }),
);
showMarket("BTCUSDT");

if (
  "IntersectionObserver" in window &&
  !window.matchMedia("(prefers-reduced-motion: reduce)").matches
) {
  const observer = new IntersectionObserver(
    (entries) =>
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.remove("waiting");
          observer.unobserve(entry.target);
        }
      }),
    { threshold: 0.08 },
  );
  document
    .querySelectorAll(
      ".product-section > *, .process-grid > *, .faq-section > *, .closing .wrap > div",
    )
    .forEach((element) => {
      element.classList.add("reveal", "waiting");
      observer.observe(element);
    });
}
