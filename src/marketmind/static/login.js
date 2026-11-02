document
  .getElementById("login-form")
  .addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget,
      button = form.querySelector("button"),
      error = document.getElementById("error");
    error.textContent = "";
    button.disabled = true;
    try {
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: form.username.value.trim(),
          password: form.password.value,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "ورود انجام نشد.");
      location.assign("/app");
    } catch (e) {
      error.textContent = e.message || "ارتباط با سرور برقرار نشد.";
    } finally {
      button.disabled = false;
    }
  });
