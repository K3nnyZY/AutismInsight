import { test, expect } from "@playwright/test";

async function mockApi(page, { failFirst = false } = {}) {
  const requests = [];
  const conversations = new Map();
  let createdCount = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/health/ready")) return route.fulfill({ json: {
      status: "ready", documents: 1, demo_sources: true,
    } });
    if (path === "/api/conversations") {
      const id = `b4e7405d-867b-4700-bb52-${String(++createdCount).padStart(12, "0")}`;
      conversations.set(id, []);
      return route.fulfill({ status: 201, json: { conversation_id: id } });
    }
    if (path === "/api/chat") {
      const body = request.postDataJSON();
      requests.push(body);
      if (failFirst && requests.length === 1) return route.fulfill({ status: 503,
        json: { detail: "Error temporal de prueba." } });
      const response = {
        message_id: crypto.randomUUID(), request_id: body.request_id,
        answer: "El documento menciona la comunicación [1].", evidence_status: "grounded",
        sources: [{ citation: 1, document_id: "document", chunk_id: "chunk", title: "Asperger demo",
          page: 1, trust_level: "demo", url: null, excerpt: "Fragmento de prueba." }],
      };
      conversations.set(body.conversation_id, [{ id: body.request_id, role: "user", content: body.message },
        { id: response.message_id, role: "assistant", content: response.answer, response }]);
      return route.fulfill({ json: response });
    }
    const id = path.split("/").at(-1);
    if (request.method() === "DELETE") { conversations.delete(id); return route.fulfill({ status: 204 }); }
    return route.fulfill({ json: conversations.get(id) || [] });
  });
  return requests;
}

async function send(page) {
  await page.getByLabel("Tu pregunta", { exact: true }).fill("¿Qué dice sobre la comunicación?");
  await expect(page.getByRole("button", { name: "Enviar", exact: true })).toBeDisabled();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Enviar", exact: true }).click();
}

test("consent, preferences, source details, reload and deletion", async ({ page }) => {
  const requests = await mockApi(page);
  await page.goto("/");
  await page.getByLabel("Forma de explicar").selectOption("simple");
  await page.getByLabel("Busco orientación para").selectOption("family");
  await send(page);
  await expect(page.locator(".message.assistant")).toContainText("El documento menciona la comunicación");
  expect(requests[0].style).toBe("simple");
  expect(requests[0].audience).toBe("family");
  await page.getByText("Ver fuentes (1)").click();
  await expect(page.getByText("Material de prueba", { exact: true })).toBeVisible();
  await expect(page.getByText("Fragmento de prueba.")).toBeVisible();
  await page.reload();
  await expect(page.locator(".message")).toHaveCount(2);
  await page.getByRole("button", { name: "Eliminar conversación" }).click();
  await page.getByRole("button", { name: "Eliminar definitivamente" }).click();
  await expect(page.locator(".message")).toHaveCount(0);
  await expect(page.getByRole("checkbox")).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.length)).toBe(0);
});

test("retry preserves request id and does not duplicate the user turn", async ({ page }) => {
  const requests = await mockApi(page, { failFirst: true });
  await page.goto("/");
  await send(page);
  await expect(page.getByRole("alert")).toContainText("Error temporal de prueba.");
  await page.getByRole("button", { name: "Reintentar respuesta" }).click();
  await expect(page.locator(".message")).toHaveCount(2);
  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual(requests[1]);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

for (const status of [404, 422]) {
test(`mobile layout and unavailable session recovery (${status})`, async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockApi(page);
  await page.addInitScript(() => sessionStorage.setItem("autism-insight-conversation", "expired"));
  await page.route("**/api/conversations/expired", (route) => route.fulfill({
    status, json: { detail: "La conversación no está disponible." },
  }));
  await page.goto("/");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("checkbox")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "¿Qué es el autismo?" }).click();
  await expect(page.getByLabel("Tu pregunta", { exact: true })).toHaveValue("¿Qué es el autismo?");
});
}

test("topic suggestions and keyboard submission", async ({ page }) => {
  const requests = await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Educación", exact: true }).click();
  await expect(page.getByRole("button", { name: "Educación", exact: true })).toHaveAttribute("aria-pressed", "true");
  const question = "¿Qué metodologías educativas aparecen en el documento de prueba?";
  await page.getByRole("button", { name: question, exact: true }).click();
  await expect(page.getByLabel("Tu pregunta", { exact: true })).toHaveValue(question);
  await page.getByRole("checkbox").check();
  await page.getByLabel("Tu pregunta", { exact: true }).press("Shift+Enter");
  expect(requests).toHaveLength(0);
  await page.getByLabel("Tu pregunta", { exact: true }).press("Enter");
  await expect(page.locator(".message")).toHaveCount(2);
  expect(requests[0].message).toBe(question);
});

test("inline citation opens evidence, copying and safe deletion", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await mockApi(page);
  await page.goto("/");
  await send(page);
  const citation = page.locator('.message-markdown a[href^="#source-"]');
  await expect(citation).toBeVisible();
  await citation.click();
  await expect(page.getByText("Fragmento de prueba.")).toBeVisible();
  await page.getByRole("button", { name: "Copiar respuesta", exact: true }).click();
  await expect(page.getByRole("button", { name: "Respuesta copiada", exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("[1]");
  await page.getByRole("button", { name: "Eliminar conversación" }).click();
  await page.getByRole("button", { name: "Conservar conversación" }).click();
  await expect(page.locator(".message")).toHaveCount(2);
});

test("new conversations preserve earlier sessions without storing message text", async ({ page }) => {
  const requests = await mockApi(page);
  await page.goto("/");
  await send(page);
  await expect(page.locator(".message")).toHaveCount(2);
  await page.getByRole("button", { name: "Nueva conversación" }).click();
  await expect(page.locator(".message")).toHaveCount(0);
  await page.getByLabel("Tu pregunta", { exact: true }).fill("Una segunda pregunta distinta");
  await page.getByRole("button", { name: "Enviar", exact: true }).click();
  await expect(page.locator(".message")).toHaveCount(2);
  expect(requests[0].conversation_id).not.toBe(requests[1].conversation_id);
  await page.getByRole("navigation", { name: "Tus conversaciones" })
    .getByRole("button", { name: "¿Qué dice sobre la comunicación?", exact: true }).click();
  await expect(page.locator(".message.user")).toContainText("¿Qué dice sobre la comunicación?");
  const stored = await page.evaluate(() => JSON.stringify({ ...sessionStorage }));
  expect(stored).not.toContain("Una segunda pregunta");
  expect(stored).not.toContain("¿Qué dice");
});

test("mobile navigation, reading preferences and focus return", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Abrir menú" }).click();
  await expect(page.getByRole("dialog", { name: "Panel de conversación" })).toBeVisible();
  await page.getByRole("button", { name: "Texto más grande" }).click();
  await page.getByRole("button", { name: "Mayor contraste" }).click();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-reading", "large");
  await expect(page.locator(".app-shell")).toHaveAttribute("data-contrast", "high");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Abrir menú" })).toBeFocused();
  await page.reload();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-reading", "large");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("desktop and mobile visual review", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "¿Qué es el autismo?", exact: true })).toBeVisible();
  await page.screenshot({ path: "../artifacts/frontend-desktop.png", fullPage: true });
  await send(page);
  await expect(page.locator(".message")).toHaveCount(2);
  await page.getByText("Ver fuentes (1)").click();
  await page.screenshot({ path: "../artifacts/frontend-conversation.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Abrir menú" }).click();
  await page.getByRole("button", { name: "Nueva conversación" }).click();
  await page.screenshot({ path: "../artifacts/frontend-mobile.png", fullPage: true });
});
