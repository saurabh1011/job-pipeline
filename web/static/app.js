// Job Pipeline SPA
const App = (() => {
  let _apiKey = localStorage.getItem("api_key") || "";
  let _currentStatuses = new Set(["alerted"]);
  let _currentCompanies = new Set();
  let _currentJob = null;
  let _pollTimer = null;
  let _allJobs = [];
  let _selectMode = false;
  let _selectedKeys = new Set();
  let _activeLocations = new Set(["new_york", "remote"]);
  let _dateFilter = "all";

  const LOCATION_BUCKETS = [
    { key: "new_york", label: "New York", pattern: /new york|nyc|new york city/i },
    { key: "remote",   label: "Remote",   pattern: /remote/i },
    { key: "sf",       label: "SF",       pattern: /san francisco|sf,?\s*ca/i },
    { key: "seattle",  label: "Seattle",  pattern: /seattle/i },
    { key: "austin",   label: "Austin",   pattern: /austin/i },
    { key: "chicago",  label: "Chicago",  pattern: /chicago/i },
  ];

  const ALL_STATUSES = [
    { key: "new",          label: "New" },
    { key: "alerted",      label: "Alerted" },
    { key: "approved",     label: "Approved" },
    { key: "applied",      label: "Applied" },
    { key: "skipped",      label: "Skipped" },
    { key: "interviewing", label: "Interviewing" },
    { key: "rejected",     label: "Rejected" },
    { key: "offer",        label: "Offer" },
    { key: "interesting",  label: "Interesting" },
  ];

  // ── Auth ────────────────────────────────────────────────────────────────

  function _headers() {
    return { "Content-Type": "application/json", "x-api-key": _apiKey };
  }

  async function _api(method, path, body) {
    const opts = { method, headers: _headers() };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    if (res.status === 401) {
      _showAuth();
      throw new Error("Unauthorized");
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  }

  function _showAuth() {
    document.getElementById("auth-overlay").style.display = "flex";
  }

  async function submitApiKey() {
    const input = document.getElementById("api-key-input");
    const key = input.value.trim();
    if (!key) return;
    _apiKey = key;
    try {
      await _api("GET", "/api/jobs");
      localStorage.setItem("api_key", key);
      document.getElementById("auth-overlay").style.display = "none";
      loadJobs();
    } catch {
      document.getElementById("auth-error").style.display = "block";
    }
  }

  // ── Job list ─────────────────────────────────────────────────────────────

  async function loadJobs() {
    let data;
    try {
      data = await _api("GET", "/api/jobs");
    } catch {
      return;
    }
    _allJobs = data.jobs;
    _renderDropdowns();
    _renderJobList();
  }

  // ── Filter dropdowns ─────────────────────────────────────────────────────

  function _renderDropdowns() {
    _renderStatusDd();
    _renderCompanyDd();
    _renderLocationDd();
  }

  function _renderStatusDd() {
    const wrap = document.getElementById("dd-status");
    if (!wrap) return;
    const items = ALL_STATUSES.map(s => ({ key: s.key, label: s.label, checked: _currentStatuses.has(s.key) }));
    wrap.innerHTML = _ddHtml("status", "Status", items, "App.toggleStatus", "App.clearStatuses", false);
  }

  function _renderCompanyDd() {
    const wrap = document.getElementById("dd-company");
    if (!wrap) return;
    const companies = [...new Set(_allJobs.map(j => j.company))].sort();
    const items = companies.map(c => ({ key: c, label: c, checked: _currentCompanies.has(c) }));
    wrap.innerHTML = _ddHtml("company", "Company", items, "App.toggleCompany", "App.clearCompanies", true);
  }

  function _renderLocationDd() {
    const wrap = document.getElementById("dd-location");
    if (!wrap) return;
    const present = LOCATION_BUCKETS.filter(b => _allJobs.some(j => b.pattern.test(j.location || "")));
    const items = present.map(b => ({ key: b.key, label: b.label, checked: _activeLocations.has(b.key) }));
    wrap.innerHTML = _ddHtml("location", "Location", items, "App.toggleLocation", "App.clearLocations", false);
  }

  function _ddHtml(id, title, items, toggleFn, clearFn, searchable) {
    const count = items.filter(i => i.checked).length;
    const btnLabel = count ? `${title} (${count})` : title;
    const allItem = `<label class="dd-item dd-item-all">
      <input type="checkbox" class="dd-all-check" ${!count ? "checked" : ""} onclick="event.preventDefault();${clearFn}()">All
    </label>`;
    const listHtml = items.map(item =>
      `<label class="dd-item"><input type="checkbox" ${item.checked ? "checked" : ""} onchange="${toggleFn}('${_esc(item.key)}')">${_esc(item.label)}</label>`
    ).join("");
    return `<div class="filter-dd" onclick="event.stopPropagation()">
      <button class="filter-dd-btn${count ? " active" : ""}" onclick="App.openDropdown(event,'${id}')">
        ${_esc(btnLabel)}<span class="dd-caret">▾</span>
      </button>
      <div class="filter-dd-panel" id="dd-panel-${id}" style="display:none">
        ${searchable ? `<input class="dd-search" type="text" placeholder="Search…" oninput="App.ddSearch('${id}',this.value)">` : ""}
        <div class="dd-list" id="dd-list-${id}">${allItem}${listHtml}</div>
        <button class="dd-clear-btn" id="dd-clear-${id}" onclick="${clearFn}()" style="${count ? "" : "display:none"}">Clear</button>
      </div>
    </div>`;
  }

  function openDropdown(event, id) {
    event.stopPropagation();
    const panel = document.getElementById(`dd-panel-${id}`);
    if (!panel) return;
    const isOpen = panel.style.display !== "none";
    document.querySelectorAll(".filter-dd-panel").forEach(p => { p.style.display = "none"; });
    if (!isOpen) panel.style.display = "block";
  }

  function ddSearch(id, query) {
    const list = document.getElementById(`dd-list-${id}`);
    if (!list) return;
    const q = query.toLowerCase();
    list.querySelectorAll(".dd-item").forEach(el => {
      el.style.display = el.textContent.toLowerCase().includes(q) ? "" : "none";
    });
  }

  function _syncDdBtn(id, title, count) {
    const btn = document.querySelector(`#dd-${id} .filter-dd-btn`);
    if (btn) {
      btn.innerHTML = `${_esc(count ? `${title} (${count})` : title)}<span class="dd-caret">▾</span>`;
      btn.classList.toggle("active", count > 0);
    }
    const clearBtn = document.getElementById(`dd-clear-${id}`);
    if (clearBtn) clearBtn.style.display = count ? "" : "none";
    const allCheck = document.querySelector(`#dd-list-${id} .dd-all-check`);
    if (allCheck) allCheck.checked = count === 0;
  }

  // ── Filter state ──────────────────────────────────────────────────────────

  function toggleStatus(key) {
    if (_currentStatuses.has(key)) _currentStatuses.delete(key);
    else _currentStatuses.add(key);
    _syncDdBtn("status", "Status", _currentStatuses.size);
    _renderJobList();
  }

  function clearStatuses() {
    _currentStatuses.clear();
    _renderStatusDd();
    _renderJobList();
  }

  function toggleCompany(company) {
    if (_currentCompanies.has(company)) _currentCompanies.delete(company);
    else _currentCompanies.add(company);
    _syncDdBtn("company", "Company", _currentCompanies.size);
    _selectedKeys.clear();
    _renderJobList();
  }

  function clearCompanies() {
    _currentCompanies.clear();
    _selectedKeys.clear();
    _renderCompanyDd();
    _renderJobList();
  }

  function toggleLocation(key) {
    if (_activeLocations.has(key)) _activeLocations.delete(key);
    else _activeLocations.add(key);
    _syncDdBtn("location", "Location", _activeLocations.size);
    _selectedKeys.clear();
    _renderJobList();
  }

  function clearLocations() {
    _activeLocations.clear();
    _selectedKeys.clear();
    _renderLocationDd();
    _renderJobList();
  }

  function setDateFilter(value) {
    _dateFilter = value;
    _selectedKeys.clear();
    _renderJobList();
  }

  function _matchesLocation(job) {
    if (_activeLocations.size === 0) return true;
    const loc = (job.location || "").toLowerCase();
    for (const key of _activeLocations) {
      const bucket = LOCATION_BUCKETS.find(b => b.key === key);
      if (bucket && bucket.pattern.test(loc)) return true;
    }
    return false;
  }

  function _matchesDate(job) {
    if (_dateFilter === "all") return true;
    const days = parseInt(_dateFilter, 10);
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    return new Date(job.date_seen) >= cutoff;
  }

  function _filteredJobs() {
    return _allJobs.filter(j =>
      (!_currentStatuses.size || _currentStatuses.has(j.status)) &&
      (!_currentCompanies.size || _currentCompanies.has(j.company)) &&
      _matchesLocation(j) &&
      _matchesDate(j)
    );
  }

  function _renderJobList() {
    const jobs = _filteredJobs();
    const el = document.getElementById("job-list");
    const countEl = document.getElementById("job-count");
    if (countEl) {
      countEl.textContent = jobs.length === 1 ? "1 job" : `${jobs.length} jobs`;
    }
    if (!jobs.length) {
      el.innerHTML = '<div class="empty">No jobs found for this filter.</div>';
      return;
    }
    el.innerHTML = jobs.map(j => _jobCard(j)).join("");
    _updateBulkBar();
  }

  function _scoreClass(score) {
    if (score == null) return "";
    if (score >= 8) return "high";
    if (score >= 6) return "mid";
    return "low";
  }

  function _jobCard(j) {
    const score = j.match_score != null ? `${j.match_score}/10` : "N/A";
    const sc = _scoreClass(j.match_score);
    const key = `${j.company}/${j.job_id}`;
    const isSelected = _currentJob && _currentJob.company === j.company && _currentJob.job_id === j.job_id;
    const isChecked = _selectedKeys.has(key);
    const cardSelected = isSelected ? " selected" : "";
    const cardChecked = isChecked ? " checked" : "";
    const preview = j.description ? _stripHtml(j.description).slice(0, 90) : "";

    if (_selectMode) {
      return `
        <div class="job-card${cardSelected}${cardChecked}" onclick="App.toggleCheck('${_esc(j.company)}', '${_esc(j.job_id)}')">
          <input type="checkbox" class="job-checkbox" ${isChecked ? "checked" : ""} onclick="event.stopPropagation(); App.toggleCheck('${_esc(j.company)}', '${_esc(j.job_id)}')">
          <div class="score-badge ${sc}">${score}</div>
          <div class="job-meta">
            <div class="job-title">${_esc(j.title)}</div>
            <div class="job-sub">${_esc(j.company)} &middot; ${_esc(j.location || "")}</div>
          </div>
          <div class="status-chip status-${j.status}">${j.status}</div>
        </div>`;
    }

    return `
      <div class="job-card${cardSelected}" onclick="App.openJob('${_esc(j.company)}', '${_esc(j.job_id)}')">
        <div class="score-badge ${sc}">${score}</div>
        <div class="job-meta">
          <div class="job-title">${_esc(j.title)}</div>
          <div class="job-sub">${_esc(j.company)} &middot; ${_esc(j.location || "")}</div>
          ${preview ? `<div class="job-sub" style="margin-top:4px">${_esc(preview)}</div>` : ""}
        </div>
        <div class="status-chip status-${j.status}">${j.status}</div>
      </div>`;
  }

  // ── Select mode + bulk ────────────────────────────────────────────────────

  function toggleSelectMode() {
    _selectMode = !_selectMode;
    _selectedKeys.clear();
    document.getElementById("select-btn").textContent = _selectMode ? "Cancel" : "Select";
    _renderJobList();
  }

  function toggleCheck(company, job_id) {
    const key = `${company}/${job_id}`;
    if (_selectedKeys.has(key)) _selectedKeys.delete(key);
    else _selectedKeys.add(key);
    _renderJobList();
  }

  function _updateBulkBar() {
    const bar = document.getElementById("bulk-bar");
    const count = _selectedKeys.size;
    if (_selectMode && count > 0) {
      bar.classList.add("open");
      document.getElementById("bulk-count").textContent = `${count} selected`;
    } else {
      bar.classList.remove("open");
    }
  }

  function clearSelection() {
    _selectedKeys.clear();
    _renderJobList();
  }

  async function bulkStatus(status) {
    if (!_selectedKeys.size) return;
    const jobs = [..._selectedKeys].map(key => {
      const [company, job_id] = key.split("/");
      return { company, job_id };
    });
    try {
      await _api("POST", "/api/jobs/bulk-status", { jobs, status });
      _selectedKeys.clear();
      _selectMode = false;
      document.getElementById("select-btn").textContent = "Select";
      await loadJobs();
    } catch (e) {
      alert(e.message);
    }
  }

  // ── Job detail ────────────────────────────────────────────────────────────

  async function openJob(company, job_id) {
    let job;
    try {
      job = await _api("GET", `/api/jobs/${company}/${job_id}`);
    } catch (e) {
      alert(e.message);
      return;
    }
    _currentJob = job;

    document.getElementById("detail-title").textContent = job.title;
    document.getElementById("detail-sub").innerHTML =
      `${_esc(job.company)} &middot; ${_esc(job.location || "Remote")}` +
      (job.apply_url ? ` &middot; <a class="apply-link" href="${job.apply_url}" target="_blank">Apply ↗</a>` : "");
    document.getElementById("detail-score").textContent =
      job.match_score != null ? `${job.match_score}/10` : "";

    document.getElementById("action-bar").innerHTML = _actionBar(job);

    // Match analysis (summary + strengths + gaps)
    const analysisEl = document.getElementById("match-analysis");
    if (job.match_summary || (job.match_strengths && job.match_strengths.length) || (job.match_gaps && job.match_gaps.length)) {
      const summaryHtml = job.match_summary
        ? `<div class="match-section"><div class="match-section-title">Match Summary</div><p class="match-summary">${_esc(job.match_summary)}</p></div>` : "";
      const strengthsHtml = (job.match_strengths && job.match_strengths.length)
        ? `<div class="match-section"><div class="match-section-title strengths-label">Strengths</div><ul class="match-list strengths-list">${job.match_strengths.map(s => `<li>${_esc(s)}</li>`).join("")}</ul></div>` : "";
      const gapsHtml = (job.match_gaps && job.match_gaps.length)
        ? `<div class="match-section"><div class="match-section-title gaps-label">Gaps</div><ul class="match-list gaps-list">${job.match_gaps.map(g => `<li>${_esc(g)}</li>`).join("")}</ul></div>` : "";
      analysisEl.innerHTML = `<div class="match-analysis-box">${summaryHtml}${strengthsHtml}${gapsHtml}</div>`;
    } else {
      analysisEl.innerHTML = "";
    }

    // Description (HTML stripped)
    const descEl = document.getElementById("desc-content");
    const descText = _stripHtml(job.description || "");
    descEl.innerHTML = descText
      ? `<div class="job-description">${_esc(descText)}</div>`
      : `<div class="section-empty">No description available.</div>`;

    // Cover letter
    const clEl = document.getElementById("cover-letter-content");
    clEl.innerHTML = job.cover_letter
      ? `<div class="cover-letter">${_esc(job.cover_letter)}</div>`
      : `<div class="section-empty">No cover letter yet. Click Regenerate to create one.</div>`;

    // Diff
    const diffEl = document.getElementById("diff-content");
    diffEl.innerHTML = (job.resume_diff && job.resume_diff.trim())
      ? `<div class="diff-view">${_renderDiff(job.resume_diff)}</div>`
      : `<div class="section-empty">No resume diff found.</div>`;

    // Reset to description tab
    document.querySelectorAll(".detail-tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".detail-tab-content").forEach(t => t.classList.remove("active"));
    document.getElementById("tab-desc-btn").classList.add("active");
    document.getElementById("tab-desc").classList.add("active");

    document.getElementById("detail-pane").classList.add("open");
    loadJobs();
  }

  function _actionBar(job) {
    const opts = ALL_STATUSES.map(s =>
      `<option value="${s.key}" ${s.key === job.status ? "selected" : ""}>${s.label}</option>`
    ).join("");
    const pdfBtn = job.pdf_path
      ? `<a href="${job.pdf_path}" target="_blank"><button class="btn-ghost">Download PDF</button></a>`
      : `<button class="btn-ghost" onclick="App.exportPDF()">Export PDF</button>`;
    return `
      <select class="status-select" onchange="App.setStatus(this.value)">${opts}</select>
      <button class="btn-ghost" onclick="App.regenerate()">Regenerate</button>
      ${pdfBtn}`;
  }

  function closeDetail() {
    document.getElementById("detail-pane").classList.remove("open");
    _currentJob = null;
  }

  function showTab(el, tabId) {
    document.querySelectorAll(".detail-tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".detail-tab-content").forEach(t => t.classList.remove("active"));
    el.classList.add("active");
    document.getElementById(tabId).classList.add("active");
  }

  // ── Status updates ────────────────────────────────────────────────────────

  async function setStatus(status) {
    if (!_currentJob || status === _currentJob.status) return;
    try {
      await _api("PATCH", `/api/jobs/${_currentJob.company}/${_currentJob.job_id}`, { status });
      _currentJob.status = status;
      document.getElementById("action-bar").innerHTML = _actionBar(_currentJob);
      loadJobs();
    } catch (e) {
      alert(e.message);
    }
  }

  async function bulkStatusFromSelect() {
    const sel = document.getElementById("bulk-status-select");
    if (!sel || !sel.value) return;
    await bulkStatus(sel.value);
    sel.value = "";
  }

  // ── Per-job actions ───────────────────────────────────────────────────────

  async function regenerate() {
    if (!_currentJob) return;
    const { company, job_id } = _currentJob;
    const data = await _api("POST", `/api/jobs/${company}/${job_id}/generate`);
    _startTask(data.task_id, "Generating cover letter...", () => openJob(company, job_id));
  }

  async function exportPDF() {
    if (!_currentJob) return;
    const { company, job_id } = _currentJob;
    const data = await _api("POST", `/api/jobs/${company}/${job_id}/export`);
    _startTask(data.task_id, "Exporting PDF...", () => openJob(company, job_id));
  }

  // ── Pipeline actions ──────────────────────────────────────────────────────

  async function triggerProcess() {
    const data = await _api("POST", "/api/pipeline/process");
    _startTask(data.task_id, "Processing jobs...", loadJobs);
  }

  async function triggerFullRun() {
    if (!confirm("Full Run fetches new job listings from all companies. This takes several minutes and uses LLM quota. Continue?")) return;
    const data = await _api("POST", "/api/pipeline/run");
    _startTask(data.task_id, "Full run: fetching + scoring + generating...", loadJobs);
  }

  async function triggerRun() {
    const sel = document.getElementById("company-run-select");
    const selected = Array.from(sel.selectedOptions).map(o => o.value);
    const isAll = selected.length === 0 || (selected.length === 1 && selected[0] === "");
    const label = isAll
      ? "All companies"
      : selected.length === 1 ? selected[0] : `${selected.length} companies`;
    if (!confirm(`Run fetch + score for: ${label}? This may take several minutes.`)) return;
    const body = isAll ? {} : { companies: selected };
    const data = await _api("POST", "/api/pipeline/run", body);
    _startTask(data.task_id, `Running: ${label}...`, loadJobs);
  }

  async function _loadCompanyRunSelect() {
    try {
      const companies = await _api("GET", "/api/companies");
      const sel = document.getElementById("company-run-select");
      sel.innerHTML = `<option value="">All companies</option>` +
        companies.map(c => `<option value="${_esc(c)}">${_esc(c)}</option>`).join("");
    } catch (e) { /* non-fatal */ }
  }

  // ── Task progress drawer ──────────────────────────────────────────────────

  function _startTask(taskId, label, onDone) {
    clearInterval(_pollTimer);
    const drawer = document.getElementById("task-drawer");
    const logEl = document.getElementById("task-log");
    document.getElementById("task-drawer-title").innerHTML = `<span class="spinner"></span> ${label}`;
    logEl.innerHTML = "";
    drawer.classList.add("open");

    let lastLogLen = 0;
    _pollTimer = setInterval(async () => {
      let task;
      try {
        task = await _api("GET", `/api/tasks/${taskId}`);
      } catch {
        return;
      }
      const newLines = task.logs.slice(lastLogLen);
      lastLogLen = task.logs.length;
      newLines.forEach(line => {
        const div = document.createElement("div");
        div.className = line.startsWith("ERROR") ? "log-error" : "";
        div.textContent = line;
        logEl.appendChild(div);
        logEl.scrollTop = logEl.scrollHeight;
      });
      if (task.status === "done") {
        clearInterval(_pollTimer);
        document.getElementById("task-drawer-title").innerHTML = `<span style="color:var(--green)">&#10003;</span> ${label} Done.`;
        const done = document.createElement("div");
        done.className = "log-done";
        done.textContent = "Completed.";
        logEl.appendChild(done);
        if (onDone) onDone();
      } else if (task.status === "error") {
        clearInterval(_pollTimer);
        document.getElementById("task-drawer-title").innerHTML = `<span style="color:var(--red)">&#x2717;</span> ${label} Failed.`;
      }
    }, 1500);
  }

  function closeDrawer() {
    clearInterval(_pollTimer);
    document.getElementById("task-drawer").classList.remove("open");
  }

  // ── Diff renderer ────────────────────────────────────────────────────────

  function _renderDiff(text) {
    return text.split("\n").map(line => {
      const cls = line.startsWith("+") && !line.startsWith("+++") ? "diff-add"
                : line.startsWith("-") && !line.startsWith("---") ? "diff-del"
                : line.startsWith("@@") ? "diff-meta"
                : "diff-ctx";
      return `<div class="${cls}">${_esc(line)}</div>`;
    }).join("");
  }

  // ── Utilities ─────────────────────────────────────────────────────────────

  function _stripHtml(html) {
    if (!html) return "";
    const el = document.createElement("div");
    el.innerHTML = html;
    return (el.textContent || el.innerText || "").replace(/\s+/g, " ").trim();
  }

  function _esc(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  async function init() {
    if (!_apiKey) { _showAuth(); return; }
    try {
      await _api("GET", "/api/jobs");
    } catch {
      _showAuth();
      return;
    }
    loadJobs();
    _loadCompanyRunSelect();
  }

  document.addEventListener("DOMContentLoaded", init);

  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("api-key-input").addEventListener("keydown", e => {
      if (e.key === "Enter") submitApiKey();
    });
    document.addEventListener("click", () => {
      document.querySelectorAll(".filter-dd-panel").forEach(p => { p.style.display = "none"; });
    });
  });

  return { loadJobs,
           toggleStatus, clearStatuses,
           toggleCompany, clearCompanies,
           toggleLocation, clearLocations,
           setDateFilter,
           openDropdown, ddSearch,
           openJob, closeDetail, showTab,
           setStatus, bulkStatusFromSelect, regenerate, exportPDF,
           triggerProcess, triggerFullRun, triggerRun,
           closeDrawer, submitApiKey,
           toggleSelectMode, toggleCheck, clearSelection, bulkStatus };
})();
