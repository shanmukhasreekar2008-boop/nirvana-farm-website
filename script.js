const accountModal = document.querySelector("#account-modal");
const requestModal = document.querySelector("#request-modal");
const toast = document.querySelector("#toast");
const cart = new Map();
const availableStock = new Map();
const availablePrices = new Map();
const unitLabels = new Map();
const productNames = new Map([
  ["garden-tomatoes", "Garden tomatoes"],
  ["leafy-greens", "Leafy greens"],
  ["root-vegetables", "Root vegetables"]
]);
let authMode = "signup";
let csrfToken = "";
let currentUser = null;
let toastTimer;

function formatMoney(paise) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2
  }).format(Number(paise) / 100);
}

function openModal(modal) {
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  const firstInput = modal.querySelector("input");
  if (firstInput) window.setTimeout(() => firstInput.focus(), 100);
}

function closeModal(modal) {
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
  if (!document.querySelector(".overlay.open")) document.body.style.overflow = "";
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("show"), 3200);
}

async function apiRequest(path, options = {}) {
  const headers = { Accept: "application/json" };
  const requestOptions = { method: options.method || "GET", headers, credentials: "same-origin" };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    requestOptions.body = JSON.stringify(options.body);
  }
  if (options.csrf) {
    if (!csrfToken) await refreshSession();
    headers["X-CSRF-Token"] = csrfToken;
  }
  let response;
  try {
    response = await fetch(path, requestOptions);
  } catch {
    throw new Error("Cannot reach Nirvana's local server. Start it and open http://127.0.0.1:5000.");
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("The server returned an invalid response.");
  }
  if (!response.ok) throw new Error(data.error || `The request failed (${response.status}).`);
  return data;
}

async function refreshSession() {
  const data = await apiRequest("/api/session");
  const wasAdmin = currentUser?.role === "admin";
  csrfToken = data.csrfToken;
  currentUser = data.user;
  updateAccountButton();
  updateRequestCustomer();
  if (applyRoleView()) renderAdmin().catch((error) => showToast(error.message));
  else if (wasAdmin) initializeCustomerView();
  return data;
}

function updateAccountButton() {
  document.querySelector("#account-open").textContent = currentUser ? "Sign out" : "Sign in";
}

function updateRequestCustomer() {
  const nameInput = document.querySelector("#request-name");
  const emailInput = document.querySelector("#request-email");
  nameInput.value = currentUser ? currentUser.name : "";
  emailInput.value = currentUser ? currentUser.email : "";
  nameInput.readOnly = Boolean(currentUser);
  emailInput.readOnly = Boolean(currentUser);
}

function setAuthMode(mode) {
  authMode = mode;
  document.querySelectorAll(".auth-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.mode === mode);
  });
  const nameInput = document.querySelector("#account-name");
  nameInput.required = mode === "signup";
  nameInput.hidden = mode === "login";
  document.querySelector("#name-label").hidden = mode === "login";
  document.querySelector("#account-password").autocomplete = mode === "signup" ? "new-password" : "current-password";
  document.querySelector("#modal-title").innerHTML = mode === "signup" ? "Come join <em>us.</em>" : "Good to see <em>you.</em>";
  document.querySelector("#account-message").textContent = "";
}

document.querySelector("#account-open").addEventListener("click", async () => {
  if (!currentUser) {
    setAuthMode("login");
    openModal(accountModal);
    return;
  }
  try {
    const wasAdmin = currentUser?.role === "admin";
    const data = await apiRequest("/api/session", { method: "DELETE", csrf: true });
    currentUser = data.user;
    csrfToken = "";
    updateAccountButton();
    updateRequestCustomer();
    applyRoleView();
    if (wasAdmin) initializeCustomerView();
    showToast("You have signed out.");
  } catch (error) {
    showToast(error.message);
  }
});
document.querySelectorAll("[data-close-modal]").forEach((button) => {
  button.addEventListener("click", () => closeModal(button.closest(".overlay")));
});
document.querySelectorAll(".overlay").forEach((modal) => {
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal(modal);
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.querySelectorAll(".overlay.open").forEach(closeModal);
});

document.querySelectorAll(".auth-tab").forEach((tab) => {
  tab.addEventListener("click", () => setAuthMode(tab.dataset.mode));
});

document.querySelector("#account-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  const submitButton = event.currentTarget.querySelector(".modal-submit");
  const message = document.querySelector("#account-message");
  const originalLabel = submitButton.innerHTML;
  submitButton.disabled = true;
  message.textContent = "";
  try {
    const body = {
      name: String(formData.get("name") || "").trim(),
      email: String(formData.get("email") || "").trim(),
      password: String(formData.get("password") || "")
    };
    const data = await apiRequest(`/api/auth/${authMode}`, { method: "POST", body, csrf: true });
    csrfToken = data.csrfToken;
    currentUser = data.user;
    updateAccountButton();
    updateRequestCustomer();
    const isAdmin = applyRoleView();
    closeModal(accountModal);
    showToast(authMode === "signup" ? `Welcome to Nirvana, ${currentUser.name}.` : `Welcome back, ${currentUser.name}.`);
    if (isAdmin) renderAdmin().catch((error) => showToast(error.message));
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("is-error");
  } finally {
    submitButton.disabled = false;
    submitButton.innerHTML = originalLabel;
  }
});

function renderRequestItems() {
  const container = document.querySelector("#request-items");
  container.replaceChildren();
  if (cart.size === 0) {
    const empty = document.createElement("p");
    empty.className = "order-empty";
    empty.textContent = "Choose something from the current harvest to get started.";
    container.append(empty);
    return;
  }
  let totalPaise = 0;
  for (const [slug, quantity] of cart) {
    const product = productNames.get(slug) || slug;
    const unitPricePaise = availablePrices.get(slug);
    const unitLabel = unitLabels.get(slug);
    totalPaise += unitPricePaise * quantity;
    const row = document.createElement("div");
    row.className = "request-line";
    const name = document.createElement("span");
    name.textContent = `${product} · ${formatMoney(unitPricePaise)} / ${unitLabel}`;
    const controls = document.createElement("span");
    controls.className = "quantity-control";
    const minus = document.createElement("button");
    minus.type = "button";
    minus.textContent = "−";
    minus.setAttribute("aria-label", `Remove one ${product}`);
    const count = document.createElement("span");
    count.textContent = String(quantity);
    const plus = document.createElement("button");
    plus.type = "button";
    plus.textContent = "+";
    plus.setAttribute("aria-label", `Add one ${product}`);
    plus.disabled = quantity >= (availableStock.get(slug) || 0);
    minus.addEventListener("click", () => adjustQuantity(slug, -1));
    plus.addEventListener("click", () => adjustQuantity(slug, 1));
    controls.append(minus, count, plus);
    row.append(name, controls);
    container.append(row);
  }
  const total = document.createElement("div");
  total.className = "request-total";
  const label = document.createElement("strong");
  label.textContent = "Order total";
  const amount = document.createElement("strong");
  amount.textContent = formatMoney(totalPaise);
  total.append(label, amount);
  container.append(total);
}

function adjustQuantity(product, amount) {
  if (!availablePrices.has(product) || !unitLabels.get(product)) {
    showToast("The farm has not set a price and sale unit for this product yet.");
    return;
  }
  const next = (cart.get(product) || 0) + amount;
  if (next > (availableStock.get(product) || 0)) {
    showToast("There is not enough stock for that quantity.");
    return;
  }
  if (next <= 0) cart.delete(product);
  else cart.set(product, next);
  renderRequestItems();
}

document.querySelectorAll(".add-button").forEach((button) => {
  button.addEventListener("click", () => {
    adjustQuantity(button.dataset.product, 1);
    showToast(`${productNames.get(button.dataset.product)} added to your pickup request.`);
    document.querySelector("#request-title").scrollIntoView?.({ block: "nearest" });
    openModal(requestModal);
    renderRequestItems();
  });
});

document.querySelector("#request-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (cart.size === 0) {
    showToast("Add at least one available item first.");
    return;
  }
  if (!currentUser) {
    closeModal(requestModal);
    setAuthMode("login");
    openModal(accountModal);
    document.querySelector("#account-message").textContent = "Sign in or create an account before placing an order.";
    return;
  }
  const form = event.currentTarget;
  const formData = new FormData(form);
  const submitButton = form.querySelector(".modal-submit");
  const originalLabel = submitButton.innerHTML;
  submitButton.disabled = true;
  try {
    const result = await apiRequest("/api/orders", {
      method: "POST",
      csrf: true,
      body: {
        note: String(formData.get("note") || "").trim(),
        items: Array.from(cart, ([product, quantity]) => ({ product, quantity }))
      }
    });
    if (!result.checkoutUrl) throw new Error("PhonePe did not provide a checkout link. Contact the farm.");
    window.location.assign(result.checkoutUrl);
  } catch (error) {
    showToast(error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.innerHTML = originalLabel;
  }
});

const adminView = document.querySelector("#admin-view");
const publicSections = Array.from(document.querySelectorAll("main > section:not(#admin-view)"));

function applyRoleView() {
  const isAdmin = currentUser?.role === "admin";
  document.body.classList.toggle("admin-mode", isAdmin);
  document.body.classList.remove("session-pending");
  publicSections.forEach((section) => section.classList.toggle("view-hidden", isAdmin));
  adminView.hidden = !isAdmin;
  return isAdmin;
}

function renderStock(products) {
  const productBySlug = new Map(products.map((product) => [product.slug, product]));
  availablePrices.clear();
  unitLabels.clear();
  document.querySelectorAll("[data-stock]").forEach((label) => {
    const slug = label.dataset.stock;
    const product = productBySlug.get(slug);
    const available = product ? Number(product.available) : 0;
    const unitPricePaise = product?.unit_price_paise == null ? null : Number(product.unit_price_paise);
    const unitLabel = product?.unit_label?.trim() || "";
    availableStock.set(slug, available);
    if (Number.isSafeInteger(unitPricePaise) && unitPricePaise > 0) {
      availablePrices.set(slug, unitPricePaise);
    }
    if (unitLabel) unitLabels.set(slug, unitLabel);
    label.textContent = product
      ? available > 0 ? `${available} available` : "Out of stock"
      : "Stock unavailable";
    label.classList.toggle("available", available > 0);
    label.classList.toggle("limited", available > 0 && available <= 5);
    const addButton = document.querySelector(`.add-button[data-product="${slug}"]`);
    if (addButton) addButton.disabled = available <= 0 || !availablePrices.has(slug) || !unitLabels.has(slug);
    const priceLabel = document.querySelector(`[data-price="${slug}"]`);
    if (priceLabel) {
      priceLabel.textContent = availablePrices.has(slug) && unitLabels.has(slug)
        ? `${formatMoney(unitPricePaise)} / ${unitLabel}`
        : "Price to be announced";
    }
  });
  renderRequestItems();
}

async function loadProducts() {
  const data = await apiRequest("/api/products");
  renderStock(data.products);
}

function initializeCustomerView() {
  loadProducts().catch(() => {
    document.querySelectorAll("[data-stock]").forEach((label) => {
      label.textContent = "Stock unavailable";
    });
    availableStock.clear();
    renderRequestItems();
  });
}

function renderInventory(inventory) {
  const list = document.querySelector("#admin-inventory-list");
  list.replaceChildren();
  for (const product of inventory) {
    const row = document.createElement("form");
    row.className = "inventory-row";
    const details = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = product.name;
    const available = document.createElement("span");
    available.textContent = `${product.available} available · ${product.reserved_quantity} reserved`;
    details.append(name, available);
    const stockField = document.createElement("div");
    stockField.className = "inventory-field";
    const stockLabel = document.createElement("label");
    stockLabel.textContent = "On hand";
    const stockInput = document.createElement("input");
    stockInput.type = "number";
    stockInput.min = String(product.reserved_quantity);
    stockInput.max = "100000";
    stockInput.step = "1";
    stockInput.required = true;
    stockInput.value = String(product.on_hand);
    stockInput.setAttribute("aria-label", `${product.name} stock on hand`);
    stockField.append(stockLabel, stockInput);
    const unitField = document.createElement("div");
    unitField.className = "inventory-field";
    const unitLabel = document.createElement("label");
    unitLabel.textContent = "Sale unit";
    const unitInput = document.createElement("input");
    unitInput.type = "text";
    unitInput.maxLength = 24;
    unitInput.value = product.unit_label || "";
    unitInput.placeholder = "kg, bunch, pack";
    unitInput.setAttribute("aria-label", `${product.name} sale unit`);
    unitField.append(unitLabel, unitInput);
    const priceField = document.createElement("div");
    priceField.className = "inventory-field";
    const priceLabel = document.createElement("label");
    priceLabel.textContent = "Selling price (₹)";
    const priceInput = document.createElement("input");
    priceInput.type = "number";
    priceInput.min = "0.01";
    priceInput.max = "1000000";
    priceInput.step = "0.01";
    priceInput.value = product.unit_price_paise == null
      ? ""
      : (Number(product.unit_price_paise) / 100).toFixed(2);
    priceInput.setAttribute("aria-label", `${product.name} selling price in rupees`);
    priceField.append(priceLabel, priceInput);
    const costField = document.createElement("div");
    costField.className = "inventory-field";
    const costLabel = document.createElement("label");
    costLabel.textContent = "Unit cost (₹)";
    const costInput = document.createElement("input");
    costInput.type = "number";
    costInput.min = "0";
    costInput.max = "1000000";
    costInput.step = "0.01";
    costInput.value = product.unit_cost_paise == null
      ? ""
      : (Number(product.unit_cost_paise) / 100).toFixed(2);
    costInput.setAttribute("aria-label", `${product.name} unit cost in rupees`);
    costField.append(costLabel, costInput);
    const save = document.createElement("button");
    save.type = "submit";
    save.className = "inventory-save";
    save.textContent = "Update";
    row.append(details, stockField, unitField, priceField, costField, save);
    row.addEventListener("submit", async (event) => {
      event.preventDefault();
      save.disabled = true;
      try {
        const toPaise = (value, label, allowZero = false) => {
          const text = value.trim();
          if (!text) return null;
          const match = /^(\d{1,7})(?:\.(\d{1,2}))?$/.exec(text);
          if (!match) throw new Error(`${label} must use rupees with up to two decimals.`);
          const paise = Number(match[1]) * 100 + Number((match[2] || "").padEnd(2, "0"));
          if (!Number.isSafeInteger(paise) || paise > 100000000 || (!allowZero && paise < 1)) {
            throw new Error(`${label} must be ${allowZero ? "zero or " : ""}up to ₹1,000,000.`);
          }
          return paise;
        };
        const unitLabelValue = unitInput.value.trim();
        await apiRequest(`/api/admin/inventory/${product.slug}`, {
          method: "PUT",
          csrf: true,
          body: {
            onHand: Number(stockInput.value),
            unitLabel: unitLabelValue || null,
            unitPricePaise: toPaise(priceInput.value, "Selling price"),
            unitCostPaise: toPaise(costInput.value, "Unit cost", true)
          }
        });
        await Promise.all([renderAdmin(), loadProducts()]);
        showToast(`${product.name} details updated.`);
      } catch (error) {
        showToast(error.message);
      } finally {
        save.disabled = false;
      }
    });
    list.append(row);
  }
}

function renderOrders(orders) {
  const list = document.querySelector("#admin-order-list");
  list.replaceChildren();
  if (!orders.length) {
    const empty = document.createElement("p");
    empty.className = "order-empty";
    empty.textContent = "No pickup requests yet.";
    list.append(empty);
    return;
  }
  for (const order of orders) {
    const row = document.createElement("article");
    row.className = "order-item";
    const identity = document.createElement("div");
    const customer = document.createElement("strong");
    customer.textContent = order.name;
    const orderId = document.createElement("span");
    orderId.textContent = `${order.email} · #${order.id}`;
    identity.append(customer, orderId);
    const items = document.createElement("div");
    const itemTitle = document.createElement("strong");
    itemTitle.textContent = "Order items";
    const itemList = document.createElement("span");
    itemList.textContent = order.items.map((item) =>
      `${item.product} · ${item.quantity} ${item.unitLabel || "unit"} × ${formatMoney(item.unitPricePaise)}`
    ).join(", ");
    items.append(itemTitle, itemList);
    if (order.note) {
      const note = document.createElement("span");
      note.className = "order-note";
      note.textContent = `Customer note: ${order.note}`;
      items.append(note);
    }
    const date = document.createElement("div");
    const dateTitle = document.createElement("strong");
    dateTitle.textContent = "Received";
    const dateValue = document.createElement("span");
    dateValue.textContent = new Date(order.createdAt).toLocaleString();
    date.append(dateTitle, dateValue);
    const statusCell = document.createElement("div");
    const status = document.createElement("span");
    status.className = "order-status";
    status.textContent = `Pickup: ${order.status}`;
    const payment = document.createElement("span");
    payment.textContent = `Payment: ${order.paymentStatus} · ${formatMoney(order.totalAmountPaise)}`;
    statusCell.append(status);
    const actions = document.createElement("div");
    actions.className = "order-actions";
    statusCell.append(payment);
    const possibleActions = [];
    if (["initiating", "pending"].includes(order.paymentStatus)) {
      possibleActions.push(["check-payment", "Check payment"]);
    }
    if (order.status === "pending") {
      if (order.paymentStatus === "paid") possibleActions.push(["confirmed", "Confirm"]);
      possibleActions.push(["cancelled", "Cancel"]);
    } else if (order.status === "confirmed") {
      if (order.paymentStatus === "paid") possibleActions.push(["collected", "Mark collected"]);
      possibleActions.push(["cancelled", "Cancel"]);
    }
    if (order.paymentStatus === "refund_due") possibleActions.push(["refund", "Refund via PhonePe"]);
    if (order.paymentStatus === "refunding") possibleActions.push(["refund", "Check refund"]);
    for (const [statusValue, label] of possibleActions) {
      const action = document.createElement("button");
      action.type = "button";
      action.textContent = label;
      action.addEventListener("click", () => {
        if (statusValue === "check-payment") checkOrderPayment(order.id);
        else if (statusValue === "refund") processRefund(order.id, order.paymentStatus === "refund_due");
        else updateOrderStatus(order.id, statusValue);
      });
      actions.append(action);
    }
    statusCell.append(actions);
    row.append(identity, items, date, statusCell);
    list.append(row);
  }
}

async function renderAdmin() {
  const [summaryData, ordersData, inventoryData] = await Promise.all([
    apiRequest("/api/admin/summary", { csrf: true }),
    apiRequest("/api/admin/orders", { csrf: true }),
    apiRequest("/api/admin/inventory", { csrf: true })
  ]);
  renderStatistics(summaryData);
  renderOrders(ordersData.orders);
  renderInventory(inventoryData.inventory);
}

function renderStatistics(summary) {
  const kpis = document.querySelector("#admin-kpis");
  kpis.replaceChildren();
  const metrics = [
    ["Paid revenue", formatMoney(summary.payments.revenue_paise)],
    ["Product cost recorded", formatMoney(summary.payments.recorded_cost_paise)],
    ["Refunds to process", formatMoney(summary.payments.refund_due_paise)],
    ["Orders", summary.orders.total],
    ["Awaiting pickup confirmation", summary.orders.pending],
    ["Confirmed pickups", summary.orders.confirmed],
    ["Collected", summary.orders.collected],
    ["Missing cost entries", summary.payments.missing_cost_lines]
  ];
  for (const [label, value] of metrics) {
    const card = document.createElement("article");
    card.className = "admin-kpi";
    const number = document.createElement("strong");
    number.textContent = String(value);
    const caption = document.createElement("span");
    caption.textContent = label;
    card.append(number, caption);
    kpis.append(card);
  }

  const inventorySummary = document.querySelector("#admin-stock-summary");
  inventorySummary.replaceChildren();
  const stockMetrics = [
    ["Units on hand", summary.inventory.on_hand],
    ["Available", summary.inventory.available],
    ["Reserved", summary.inventory.reserved],
    ["Products in stock", `${summary.inventory.products_in_stock} / ${summary.inventory.product_count}`]
  ];
  for (const [label, value] of stockMetrics) {
    const item = document.createElement("div");
    item.className = "admin-mini-stat";
    const number = document.createElement("strong");
    number.textContent = String(value);
    const caption = document.createElement("span");
    caption.textContent = label;
    item.append(number, caption);
    inventorySummary.append(item);
  }

  const topProducts = document.querySelector("#admin-top-products");
  topProducts.replaceChildren();
  if (!summary.topProducts.length) {
    const empty = document.createElement("p");
    empty.className = "order-empty";
    empty.textContent = "Product request stats will appear after the first order.";
    topProducts.append(empty);
    return;
  }
  const maximum = Math.max(...summary.topProducts.map((item) => Number(item.quantity)));
  summary.topProducts.forEach((product, index) => {
    const row = document.createElement("div");
    row.className = "admin-top-product";
    const label = document.createElement("span");
    label.textContent = `${String(index + 1).padStart(2, "0")} · ${product.name}`;
    const count = document.createElement("strong");
    count.textContent = String(product.quantity);
    const bar = document.createElement("span");
    bar.className = "admin-top-bar";
    const fill = document.createElement("span");
    fill.style.width = `${Math.max(4, Number(product.quantity) / maximum * 100)}%`;
    bar.append(fill);
    row.append(label, count, bar);
    topProducts.append(row);
  });
}

async function updateOrderStatus(orderId, status) {
  try {
    await apiRequest(`/api/admin/orders/${orderId}`, {
      method: "PATCH",
      csrf: true,
      body: { status }
    });
    await Promise.all([renderAdmin(), loadProducts()]);
    showToast(`Pickup request #${orderId} marked ${status}.`);
  } catch (error) {
    showToast(error.message);
  }
}

async function checkOrderPayment(orderId) {
  try {
    const result = await apiRequest(`/api/orders/${orderId}/payment`);
    await renderAdmin();
    showToast(`Order #${orderId} payment: ${result.paymentStatus}.`);
  } catch (error) {
    showToast(error.message);
  }
}

async function processRefund(orderId, startRefund) {
  if (startRefund && !window.confirm(`Start a full PhonePe refund for order #${orderId}?`)) return;
  try {
    const result = await apiRequest(`/api/admin/orders/${orderId}/refund`, {
      method: "POST",
      csrf: true,
      body: {}
    });
    await renderAdmin();
    showToast(`Order #${orderId} refund: ${result.paymentStatus}.`);
  } catch (error) {
    showToast(error.message);
  }
}

document.querySelector("#admin-open").addEventListener("click", async () => {
  if (!currentUser || currentUser.role !== "admin") {
    if (currentUser) {
      showToast("Farm desk access is restricted to admin accounts.");
      return;
    }
    setAuthMode("login");
    openModal(accountModal);
    document.querySelector("#account-message").textContent = "Sign in with the farm admin account to open the Farm Desk.";
    return;
  }
  applyRoleView();
  window.scrollTo({ top: 0, behavior: "smooth" });
  try {
    await renderAdmin();
  } catch (error) {
    showToast(error.message);
  }
});

document.querySelector("#year").textContent = String(new Date().getFullYear());

const revealObserver = "IntersectionObserver" in window
  ? new IntersectionObserver((entries, observer) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("revealed");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 })
  : null;

document.querySelectorAll("[data-reveal]").forEach((element) => {
  if (revealObserver) revealObserver.observe(element);
  else element.classList.add("revealed");
});

const menuToggle = document.querySelector("#menu-toggle");
menuToggle.addEventListener("click", () => {
  const isOpen = menuToggle.getAttribute("aria-expanded") === "true";
  menuToggle.setAttribute("aria-expanded", String(!isOpen));
  document.querySelector(".main-nav").classList.toggle("open", !isOpen);
});

document.querySelectorAll(".main-nav a").forEach((link) => {
  link.addEventListener("click", () => {
    document.querySelector(".main-nav").classList.remove("open");
    menuToggle.setAttribute("aria-expanded", "false");
  });
});

async function resumePhonePePayment() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("payment") !== "phonepe") return false;
  const orderId = params.get("order");
  if (!orderId || !/^\d+$/.test(orderId)) {
    showToast("The payment return link is incomplete. Check your order with the farm.");
    return true;
  }
  if (!currentUser) {
    showToast("Sign in to verify your PhonePe payment.");
    return true;
  }

  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      const result = await apiRequest(`/api/orders/${orderId}/payment`);
      if (result.paymentStatus === "paid") {
        cart.clear();
        renderRequestItems();
        history.replaceState(null, "", `${window.location.pathname}${window.location.hash}`);
        showToast(`Payment received for order #${result.id}. The farm will confirm pickup.`);
        return true;
      }
      if (result.paymentStatus === "failed") {
        history.replaceState(null, "", `${window.location.pathname}${window.location.hash}`);
        showToast("Payment was not completed; the order reservation was released.");
        return true;
      }
      if (result.paymentStatus === "refund_due" || result.paymentStatus === "refunding") {
        history.replaceState(null, "", `${window.location.pathname}${window.location.hash}`);
        showToast("The farm needs to process a refund for this order. Please contact the farm.");
        return true;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
    } catch (error) {
      showToast(error.message);
      return true;
    }
  }
  showToast("PhonePe is still processing the payment. Reload this page to check again.");
  return true;
}

refreshSession()
  .then(async () => {
    if (currentUser?.role !== "admin") initializeCustomerView();
    await resumePhonePePayment();
  })
  .catch((error) => {
    document.querySelector("#account-message").textContent = error.message;
    document.body.classList.remove("session-pending");
    initializeCustomerView();
  });
