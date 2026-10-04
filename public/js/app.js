const App = (() => {
  const state = { user: null, token: localStorage.getItem("itailaew_token"), authMode: "login", menuPage: 1, polling: null, qrTable: new URLSearchParams(location.search).get("table") };
  const $ = id => document.getElementById(id);

  async function api(path, options = {}) {
    const headers = {"Content-Type":"application/json", ...(options.headers || {})};
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    if (options.method && options.method !== "GET" && options.method !== "PATCH" && !headers["Idempotency-Key"]) {
      headers["Idempotency-Key"] = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    const res = await fetch(path, {...options, headers});
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok || data.ok === false) throw new Error(data.message || "เกิดข้อผิดพลาด");
    return data;
  }

  function toast(message) {
    const el = $("toast"); el.textContent = message; el.classList.add("show");
    setTimeout(() => el.classList.remove("show"), 2800);
  }

  function go(id) {
    const protectedPages = ["home","menu","reservation","customer","admin","staff"];
    if (protectedPages.includes(id) && !state.user) {
      document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
      $("login")?.classList.add("active");
      refreshNav();
      toast("กรุณา Login ก่อนจึงจะเข้าเว็บไซต์ได้");
      return;
    }
    if (id === "login" && state.user) {
      id = state.user.role === "admin" ? "admin" : state.user.role === "staff" ? "staff" : "home";
    }
    if (id === "admin" && state.user?.role !== "admin") { id = "home"; toast("ไม่มีสิทธิ์เข้าถึง"); }
    if (id === "staff" && !["admin","staff"].includes(state.user?.role)) { id = "home"; toast("ไม่มีสิทธิ์เข้าถึง"); }
    document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
    $(id)?.classList.add("active");
    if (id === "menu") loadMenus();
    if (id === "reservation") loadReservations();
    if (id === "customer") loadCustomer();
    if (id === "admin") loadDashboard();
    if (id === "staff") loadStaff();
    if (id !== "staff" && state.polling) { clearInterval(state.polling); state.polling = null; }
    if (id === "staff" && !state.polling) state.polling = setInterval(loadStaff, 3000);
    window.scrollTo({top:0,behavior:"smooth"});
  }

  function authMode(mode) {
    state.authMode = mode;
    $("name-label").classList.toggle("hidden", mode === "login");
    $("auth-title").textContent = mode === "login" ? "Welcome back" : "Create your account";
    document.querySelectorAll(".tab").forEach((x,i) => x.classList.toggle("active", (mode==="login"&&i===0)||(mode==="register"&&i===1)));
  }

  async function submitAuth(event) {
    event.preventDefault();
    try {
      const mode = state.authMode;
      const body = {email:$("auth-email").value, password:$("auth-password").value};
      if (mode === "register") body.name = $("auth-name").value;
      const data = await api(`/api/auth/${mode === "login" ? "login" : "register"}`, {method:"POST",body:JSON.stringify(body)});
      state.token = data.token; state.user = data.user; localStorage.setItem("itailaew_token", state.token);
      refreshNav(); toast(data.message);
      if (state.user.role === "admin") go("admin"); else if (state.user.role === "staff") go("staff"); else go("customer");
    } catch(e) { toast(e.message); }
  }

  function refreshNav() {
    const loggedIn = !!state.user;
    $("main-nav").classList.toggle("hidden", !loggedIn);
    $("login-nav").classList.toggle("hidden", loggedIn);
    $("logout-nav").classList.toggle("hidden", !loggedIn);
  }

  function logout() {
    if (state.polling) { clearInterval(state.polling); state.polling = null; }
    state.user = null; state.token = null; localStorage.removeItem("itailaew_token");
    refreshNav();
    document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
    $("login")?.classList.add("active");
    authMode("login");
    toast("ออกจากระบบแล้ว");
  }

  async function restore() {
    refreshNav();
    if (state.qrTable && $("qr-banner")) {
      $("qr-banner").classList.remove("hidden");
      $("qr-banner").innerHTML = `<b>🍽️ itailaew — TABLE ${escapeHtml(state.qrTable)}</b><div class="small">กำลังสั่งจากโต๊ะนี้ ออเดอร์จะส่งเข้าครัวทันที</div>`;
    }
    if (!state.token) {
      document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
      $("login")?.classList.add("active");
      authMode("login");
      return;
    }
    try {
      const data = await api("/api/me");
      state.user = data.user;
      refreshNav();
      const target = state.user.role === "admin" ? "admin" : state.user.role === "staff" ? "staff" : "home";
      document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
      $(target)?.classList.add("active");
      if (target === "admin") loadDashboard();
      if (target === "staff") { loadStaff(); if (!state.polling) state.polling = setInterval(loadStaff, 3000); }
    } catch (_) {
      logout();
    }
  }

  async function loadMenus(page = state.menuPage) {
    try {
      state.menuPage = page;
      const q = encodeURIComponent($("menu-search")?.value || "");
      const sort = $("menu-sort")?.value || "name";
      const data = await api(`/api/menus?q=${q}&sort=${sort}&page=${page}&page_size=8`);
      const grid = $("menu-grid");
      grid.innerHTML = data.items.map(m => `<article class="menu-card"><div class="menu-image">${m.image_url ? `<img src="${escapeHtml(m.image_url)}" style="width:100%;height:100%;object-fit:cover">` : (m.category==="Pizza"?"🍕":m.category==="Dessert"?"🍰":"🍝")}</div><div class="menu-body"><h3>${escapeHtml(m.name)}</h3><p>${escapeHtml(m.category)}${m.is_out_of_stock ? ' <span class="sold">หมด</span>':''}</p><span class="price">฿${Number(m.price).toFixed(2)}</span>${state.user?.role !== "admin" ? `<button class="secondary" style="float:right;padding:7px 12px" ${m.is_out_of_stock?"disabled":""} onclick="App.quickOrder('${m.id}')">+</button>`:""}</div></article>`).join("") || `<div class="list-card">ยังไม่มีเมนู</div>`;
      $("menu-pages").innerHTML = Array.from({length:data.total_pages},(_,i)=>`<button onclick="App.loadMenus(${i+1})">${i+1}</button>`).join("");
    } catch(e) { $("menu-grid").innerHTML = `<div class="list-card">${escapeHtml(e.message)}</div>`; }
  }

  async function quickOrder(menuId) {
    if (!state.user) return go("login");
    if (state.user.role !== "customer") return toast("ฟังก์ชันนี้สำหรับ Customer");
    if (!state.qrTable) return toast("กรุณาเข้าหน้าเมนูผ่าน QR ของโต๊ะ");
    try {
      const data = await api("/api/customer/orders", {method:"POST", body:JSON.stringify({
        table_id: `table_${String(state.qrTable).padStart(2,"0")}`,
        items: [{menu_id: menuId, quantity: 1, options: {}}]
      })});
      toast(`ส่งออเดอร์โต๊ะ ${state.qrTable} เข้าครัวแล้ว`);
      go("customer");
    } catch(e) { toast(e.message); }
  }

  async function reserve(event) {
    event.preventDefault();
    if (!state.user) return go("login");
    const phone = $("res-phone").value.trim();
    const name = $("res-name").value.trim();
    if (!/^[0-9]{10}$/.test(phone) || !phone.startsWith("0")) return toast("เบอร์โทรต้องเป็นตัวเลข 10 หลักและขึ้นต้นด้วย 0");
    if (!/(?=.*[A-Za-zก-๙])/.test(name)) return toast("ชื่อต้องมีตัวอักษรอย่างน้อย 1 ตัว");
    const selected = new Date($("res-date").value);
    if (Number.isNaN(selected.getTime()) || selected.getTime() < Date.now() + 30 * 60 * 1000) return toast("กรุณาจองล่วงหน้าอย่างน้อย 30 นาที");
    try {
      const data = await api("/api/reservations",{method:"POST",body:JSON.stringify({
        customer_name:name, phone,
        table_number:$("res-table").value, datetime:$("res-date").value
      })});
      toast("จองโต๊ะสำเร็จ"); event.target.reset(); loadReservations();
    } catch(e) { toast(e.message); }
  }

  async function loadReservations() {
    if (!state.user) return;
    try {
      const data = await api("/api/reservations");
      const html = data.reservations.map(r=>`<div class="table-row"><div><b>โต๊ะ ${escapeHtml(r.table_number)}</b><div class="small">${escapeHtml(r.datetime)} · ${escapeHtml(r.customer_name)}</div></div><span class="badge">${escapeHtml(r.status)}</span></div>`).join("") || `<p class="small">ยังไม่มีรายการ</p>`;
      $("my-reservations").innerHTML = html;
      if ($("staff-reservations")) $("staff-reservations").innerHTML = html;
    } catch(e) { toast(e.message); }
  }

  async function loadCustomer() {
    if (!state.user) return;
    $("customer-name").textContent = state.user.name;
    $("customer-points").textContent = state.user.member_points || 0;
    try {
      const data = await api("/api/orders");
      $("customer-orders").innerHTML = `<h3>My Orders</h3>` + (data.orders.map(o=>`<div class="table-row"><div><b>โต๊ะ ${escapeHtml(o.table_number||"-")}</b><div class="small">${escapeHtml(o.created_at||"")}</div></div><span class="badge">${escapeHtml(o.status)}</span></div>`).join("") || `<p class="small">ยังไม่มีออเดอร์</p>`);
    } catch(e) {}
  }

  async function loadDashboard() {
    try {
      const d = await api("/api/dashboard");
      $("stat-today").textContent = `฿${d.today_sales.toFixed(2)}`;
      $("stat-total").textContent = `฿${d.total_sales.toFixed(2)}`;
      $("stat-orders").textContent = d.closed_orders;
      $("best-sellers").innerHTML = d.best_sellers.map((x,i)=>`<div class="table-row"><span>${i+1}. ${escapeHtml(x.name)}</span><b>${x.quantity}</b></div>`).join("") || `<p class="small">ยังไม่มีข้อมูลยอดขาย</p>`;
    } catch(e) { toast(e.message); }
  }

  async function loadStaff() {
    try {
      const [t,k,r,o] = await Promise.all([api("/api/tables"),api("/api/kitchen"),api("/api/reservations"),api("/api/orders")]);
      $("tables-list").innerHTML = t.tables.map(x=>`<div class="table-row"><div><b>โต๊ะ ${escapeHtml(x.table_number)}</b></div><span class="badge ${x.status}">${escapeHtml(x.status)}</span></div>`).join("");
      $("kitchen-list").innerHTML = k.items.map(x=>`<div class="table-row"><div><b>${escapeHtml(x.menu_name)}</b><div class="small">โต๊ะ ${escapeHtml(x.table_number)} × ${x.quantity}</div></div><select onchange="App.updateKitchen('${x.id}',this.value)"><option ${x.status==="pending"?"selected":""}>pending</option><option ${x.status==="cooking"?"selected":""}>cooking</option><option ${x.status==="done"?"selected":""}>done</option></select></div>`).join("") || `<p class="small">ยังไม่มีออเดอร์เข้าครัว</p>`;
      $("staff-reservations").innerHTML = r.reservations.map(x=>`<div class="table-row"><div><b>โต๊ะ ${escapeHtml(x.table_number)}</b><div class="small">${escapeHtml(x.customer_name)} · ${escapeHtml(x.datetime)}</div></div><span class="badge">${escapeHtml(x.status)}</span></div>`).join("") || `<p class="small">ไม่มีคิว</p>`;
      $("staff-orders").innerHTML = o.orders.map(x=>`<div class="table-row"><div><b>โต๊ะ ${escapeHtml(x.table_number||"-")}</b><div class="small">Total ฿${Number(x.total||0).toFixed(2)}</div></div><span class="badge">${escapeHtml(x.status)}</span></div>`).join("") || `<p class="small">ไม่มีออเดอร์</p>`;
    } catch(e) { toast(e.message); }
  }

  async function updateKitchen(id,status) {
    try { await api(`/api/kitchen/${id}`,{method:"PATCH",body:JSON.stringify({status})}); toast("อัปเดตครัวแล้ว"); }
    catch(e){toast(e.message)}
  }

  async function adminPage(type) {
    const c=$("admin-content");
    if(type==="menus") {
      try {
        const data=await api("/api/menus?page=1&page_size=100");
        c.innerHTML=`<div class="list-card"><div class="section-head" style="margin:0 0 15px"><h3>Menu Management</h3><button class="primary" onclick="App.showMenuForm()">+ Add Menu</button></div><table><tr><th>Menu</th><th>Category</th><th>Price</th><th>Status</th><th></th></tr>${data.items.map(m=>`<tr><td>${escapeHtml(m.name)}</td><td>${escapeHtml(m.category)}</td><td>฿${Number(m.price).toFixed(2)}</td><td>${m.is_out_of_stock?"หมด":"พร้อมขาย"}</td><td><button class="secondary" onclick='App.showMenuForm(${JSON.stringify(m)})'>Edit</button><button class="secondary" onclick="App.deleteMenu('${m.id}')">Delete</button></td></tr>`).join("")}</table></div>`;
      }catch(e){toast(e.message)}
    }
    if(type==="users") {
      try {
        const d=await api("/api/users");
        c.innerHTML=`<div class="list-card"><div class="section-head" style="margin:0 0 15px"><h3>User Management</h3><button class="primary" onclick="App.showStaffForm()">+ Add Staff</button></div><table><tr><th>Name</th><th>Email</th><th>Role</th><th>Active</th><th></th></tr>${d.users.map(u=>`<tr><td>${escapeHtml(u.name)}</td><td>${escapeHtml(u.email)}</td><td>${escapeHtml(u.role)}</td><td>${u.active?"Yes":"No"}</td><td>${u.role!=="admin"?`<button class="secondary" onclick="App.toggleUser('${u.id}',${!u.active})">${u.active?"Disable":"Enable"}</button>`:""}</td></tr>`).join("")}</table></div>`;
      }catch(e){toast(e.message)}
    }
    if(type==="logs") {
      try {
        const d=await api("/api/audit-logs");
        c.innerHTML=`<div class="list-card"><h3>Audit Logs</h3><table><tr><th>Time</th><th>User</th><th>Action</th><th>Target</th><th>Detail</th></tr>${d.logs.map(l=>`<tr><td>${escapeHtml(l.timestamp)}</td><td>${escapeHtml(l.user_name)}</td><td>${escapeHtml(l.action)}</td><td>${escapeHtml(l.target_type)} / ${escapeHtml(l.target_id)}</td><td>${escapeHtml(l.detail)}</td></tr>`).join("")}</table></div>`;
      }catch(e){toast(e.message)}
    }
  }

  function showMenuForm(menu={}) {
    $("modal").classList.remove("hidden");
    $("modal").innerHTML=`<div><h2>${menu.id?"Edit":"Add"} Menu</h2><form onsubmit="App.saveMenu(event,'${menu.id||""}')"><label>ชื่อเมนู<input id="mf-name" value="${escapeAttr(menu.name||"")}" required></label><label>หมวดหมู่<input id="mf-category" value="${escapeAttr(menu.category||"Pasta")}" required></label><label>ราคา<input id="mf-price" type="number" min="0" step="0.01" value="${menu.price||""}" required></label><label>Image URL<input id="mf-image" value="${escapeAttr(menu.image_url||"")}" placeholder="https://..."></label><label><input id="mf-stock" type="checkbox" ${menu.is_out_of_stock?"checked":""}> สินค้าหมด</label><div class="actions"><button class="primary">Save</button><button type="button" class="secondary" onclick="App.closeModal()">Cancel</button></div></form></div>`;
  }

  async function saveMenu(e,id){e.preventDefault();try{const body={name:$("mf-name").value,category:$("mf-category").value,price:$("mf-price").value,image_url:$("mf-image").value,is_out_of_stock:$("mf-stock").checked};await api(id?`/api/menus/${id}`:"/api/menus",{method:id?"PATCH":"POST",body:JSON.stringify(body)});closeModal();toast("บันทึกเมนูสำเร็จ");adminPage("menus")}catch(x){toast(x.message)}}
  async function deleteMenu(id){if(!confirm("ลบเมนูนี้หรือไม่?"))return;try{await api(`/api/menus/${id}`,{method:"DELETE"});toast("ลบเมนูสำเร็จ");adminPage("menus")}catch(e){toast(e.message)}}
  function showStaffForm(){$("modal").classList.remove("hidden");$("modal").innerHTML=`<div><h2>Add Staff</h2><form onsubmit="App.saveStaff(event)"><label>ชื่อ<input id="sf-name" minlength="2" maxlength="100" pattern="(?=.*[A-Za-zก-๙])[A-Za-zก-๙0-9 .'-]+" required></label><label>Email<input id="sf-email" type="email" required></label><label>Password<input id="sf-pass" type="password" minlength="8" maxlength="128" required></label><div class="actions"><button class="primary">Create Staff</button><button type="button" class="secondary" onclick="App.closeModal()">Cancel</button></div></form></div>`}
  async function saveStaff(e){e.preventDefault();try{await api("/api/users/staff",{method:"POST",body:JSON.stringify({name:$("sf-name").value,email:$("sf-email").value,password:$("sf-pass").value})});closeModal();toast("เพิ่ม Staff สำเร็จ");adminPage("users")}catch(x){toast(x.message)}}
  async function toggleUser(id,active){try{await api(`/api/users/${id}`,{method:"PATCH",body:JSON.stringify({active})});toast("อัปเดตผู้ใช้แล้ว");adminPage("users")}catch(e){toast(e.message)}}
  function closeModal(){$("modal").classList.add("hidden");$("modal").innerHTML=""}
  function escapeHtml(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]))}
  function escapeAttr(v){return escapeHtml(v)}

  restore();
  return {go,authMode,submitAuth,logout,loadMenus,reserve,loadReservations,loadCustomer,loadDashboard,loadStaff,updateKitchen,adminPage,showMenuForm,saveMenu,deleteMenu,showStaffForm,saveStaff,toggleUser,closeModal,quickOrder};
})();
