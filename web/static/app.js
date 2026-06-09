// Job Pipeline SPA
const App = (() => {
  let _currentUser = null;
  let _currentStatuses = new Set(["alerted"]);
  let _currentCompanies = new Set();
  let _currentJob = null;
  let _pollTimer = null;
  let _allJobs = [];
  let _selectMode = false;
  let _selectedKeys = new Set();
  let _activeLocations = new Set(["new_york", "remote"]);
  let _dateFilter = "all";
  let _profiles = [];
  let _activeProfileId = null;
  let _activeSettingsTab = "companies";

  const LOCATION_BUCKETS = [
    { key: "new_york", label: "New York", pattern: /new york|nyc|new york city/i },
    { key: "remote",   label: "Remote",   pattern: /remote/i },
    { key: "sf",       label: "SF",       pattern: /san francisco|sf,?\s*ca/i },
    { key: "seattle",  label: "Seattle",  pattern: /seattle/i },
    { key: "austin",   label: "Austin",   pattern: /austin/i },
    { key: "chicago",  label: "Chicago",  pattern: /chicago/i },
  ];

  const ALL_STATUSES = [
    { key: "new",           label: "New" },
    { key: "alerted",       label: "Alerted" },
    { key: "approved",      label: "Approved" },
    { key: "applied",       label: "Applied" },
    { key: "skipped",       label: "Skipped" },
    { key: "interviewing",  label: "Interviewing" },
    { key: "rejected",      label: "Rejected" },
    { key: "offer",         label: "Offer" },
    { key: "interesting",   label: "Interesting" },
    { key: "not_available", label: "Not Available" },
  ];

  // ── Auth ────────────────────────────────────────────────────────────────

  function _headers() {
    return { "Content-Type": "application/json" };
  }

  async function _api(method, path, body) {
    const opts = { method, headers: _headers(), credentials: "same-origin" };
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
    const params = new URLSearchParams(window.location.search);
    const errType = params.get("auth_error");
    if (errType) {
      document.getElementById("auth-error").style.display = "block";
    }
    document.getElementById("auth-overlay").style.display = "flex";
  }

  function _updateUserUI() {
    if (!_currentUser) return;
    const nameEl = document.getElementById("user-name");
    if (nameEl) nameEl.textContent = _currentUser.name || _currentUser.email;
    const adminBtn = document.querySelector('.settings-tab[data-tab="admin"]');
    if (adminBtn && _currentUser.is_admin) adminBtn.style.display = "";
  }

  async function logout() {
    try { await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }); } catch {}
    window.location.href = "/";
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

    const dateLine = _dateLine(j);
    return `
      <div class="job-card${cardSelected}" onclick="App.openJob('${_esc(j.company)}', '${_esc(j.job_id)}')">
        <div class="score-badge ${sc}">${score}</div>
        <div class="job-meta">
          <div class="job-title">${_esc(j.title)}</div>
          <div class="job-sub">${_esc(j.company)} &middot; ${_esc(j.location || "")}</div>
          ${dateLine ? `<div class="job-dates">${_esc(dateLine)}</div>` : ""}
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
    const detailDateLine = _dateLine(job);
    document.getElementById("detail-sub").innerHTML =
      `${_esc(job.company)} &middot; ${_esc(job.location || "Remote")}` +
      (job.apply_url ? ` &middot; <a class="apply-link" href="${job.apply_url}" target="_blank">Apply ↗</a>` : "") +
      (detailDateLine ? `<br><span class="detail-dates">${_esc(detailDateLine)}</span>` : "");
    document.getElementById("detail-score").textContent =
      job.match_score != null ? `${job.match_score}/10` : "";

    document.getElementById("action-bar").innerHTML = _actionBar(job);

    // Match analysis (summary + strengths + gaps + deep analysis if available)
    const analysisEl = document.getElementById("match-analysis");
    const hasBasic = job.match_summary || (job.match_strengths && job.match_strengths.length) || (job.match_gaps && job.match_gaps.length);
    const hasDeep  = job.match_requirements && job.match_requirements.length;
    if (hasBasic || hasDeep) {
      const summaryHtml = job.match_summary
        ? `<div class="match-section"><div class="match-section-title">Match Summary</div><p class="match-summary">${_esc(job.match_summary)}</p></div>` : "";
      const strengthsHtml = (job.match_strengths && job.match_strengths.length)
        ? `<div class="match-section"><div class="match-section-title strengths-label">Strengths</div><ul class="match-list strengths-list">${job.match_strengths.map(s => `<li>${_esc(s)}</li>`).join("")}</ul></div>` : "";
      const gapsHtml = (job.match_gaps && job.match_gaps.length)
        ? `<div class="match-section"><div class="match-section-title gaps-label">Gaps</div><ul class="match-list gaps-list">${job.match_gaps.map(g => `<li>${_esc(g)}</li>`).join("")}</ul></div>` : "";
      const reqHtml  = hasDeep ? _renderRequirements(job.match_requirements) : "";
      const suggHtml = (job.match_resume_suggestions && job.match_resume_suggestions.length)
        ? `<div class="match-section"><div class="match-section-title">Resume Suggestions</div><ul class="match-list">${job.match_resume_suggestions.map(s => `<li>${_esc(s)}</li>`).join("")}</ul></div>` : "";
      analysisEl.innerHTML = `<div class="match-analysis-box">${summaryHtml}${strengthsHtml}${gapsHtml}${reqHtml}${suggHtml}</div>`;
    } else {
      analysisEl.innerHTML = "";
    }

    // Description (HTML stripped + formatted)
    const descEl = document.getElementById("desc-content");
    const descText = _formatDescription(_stripHtml(job.description || ""));
    descEl.innerHTML = descText
      ? `<div class="job-description">${_esc(descText)}</div>`
      : `<div class="section-empty">No description available.</div>`;

    // Cover letter — editable textarea
    const clEl = document.getElementById("cover-letter-content");
    clEl.innerHTML = `
      <textarea id="cover-letter-editor" class="cover-letter-editor" placeholder="No cover letter yet. Click Generate Cover Letter to create one, or type here and Save.">${_esc(job.cover_letter || "")}</textarea>
      <div class="cover-letter-save-bar">
        <button class="btn-primary btn-sm" onclick="App.saveCoverLetter()">Save</button>
        <span id="cover-letter-save-status" class="save-status"></span>
      </div>`;

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
    const pdfBtn = `<button class="btn-ghost" onclick="App.exportCoverLetterPdf()">Export PDF</button>`;
    const analyzeLabel = (job.match_requirements && job.match_requirements.length)
      ? "Re-analyze" : "Deep Analysis";
    return `
      <select class="status-select" onchange="App.setStatus(this.value)">${opts}</select>
      <button class="btn-ghost" onclick="App.rescore()">Rescore</button>
      <button class="btn-ghost" onclick="App.analyze()">${_esc(analyzeLabel)}</button>
      <button class="btn-ghost" onclick="App.generateCoverLetter()">Generate Cover Letter</button>
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

  async function rescore() {
    if (!_currentJob) return;
    const { company, job_id } = _currentJob;
    const data = await _api("POST", `/api/jobs/${company}/${job_id}/rescore`);
    _startTask(data.task_id, "Rescoring...", () => openJob(company, job_id));
  }

  async function analyze() {
    if (!_currentJob) return;
    const { company, job_id } = _currentJob;
    const data = await _api("POST", `/api/jobs/${company}/${job_id}/analyze`);
    _startTask(data.task_id, "Deep analysis...", () => openJob(company, job_id));
  }

  async function saveCoverLetter() {
    if (!_currentJob) return;
    const { company, job_id } = _currentJob;
    const editor = document.getElementById("cover-letter-editor");
    const statusEl = document.getElementById("cover-letter-save-status");
    if (!editor) return;
    statusEl.textContent = "Saving…";
    statusEl.className = "save-status";
    try {
      await _api("PUT", `/api/jobs/${company}/${job_id}/cover-letter`, { content: editor.value });
      _currentJob.cover_letter = editor.value;
      statusEl.textContent = "Saved";
      statusEl.className = "save-status save-ok";
      setTimeout(() => { statusEl.textContent = ""; }, 2500);
    } catch (e) {
      statusEl.textContent = "Save failed";
      statusEl.className = "save-status save-error";
    }
  }

  async function generateCoverLetter() {
    if (!_currentJob) return;
    const { company, job_id } = _currentJob;
    const data = await _api("POST", `/api/jobs/${company}/${job_id}/generate-cover-letter`);
    _startTask(data.task_id, "Generating cover letter...", () => openJob(company, job_id));
  }

  async function exportCoverLetterPdf() {
    if (!_currentJob) return;
    const { company, job_id } = _currentJob;
    const data = await _api("POST", `/api/jobs/${company}/${job_id}/export-cover-letter-pdf`);
    _startTask(data.task_id, "Exporting PDF...", () => {
      openJob(company, job_id);
      const a = document.createElement("a");
      a.href = `/output/${company}_${job_id}/cover_letter.pdf`;
      a.download = `${company}_${job_id}_cover_letter.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    });
  }

  // ── Pipeline actions ──────────────────────────────────────────────────────

  async function triggerRun() {
    const actionSel = document.getElementById("action-select");
    const groupSel  = document.getElementById("group-select");
    const action    = actionSel.value;
    const groupVal  = groupSel.value;
    const actionLabel = actionSel.options[actionSel.selectedIndex].text;
    const groupLabel  = groupSel.options[groupSel.selectedIndex].text;

    const needsLLM = action !== "source";
    const warning  = needsLLM ? " Uses LLM quota." : "";
    if (!confirm(`${actionLabel} for: ${groupLabel}?${warning} This may take several minutes.`)) return;

    const body = { action };
    if (groupVal.startsWith("c:"))       body.companies = [groupVal.slice(2)];
    else if (groupVal === "http")        body.group = "http";
    else if (groupVal === "playwright")  body.group = "playwright";

    const data = await _api("POST", "/api/pipeline/run", body);
    _startTask(data.task_id, `${actionLabel}: ${groupLabel}...`, loadJobs);
  }

  async function _loadGroupSelect() {
    try {
      const companies = await _api("GET", "/api/companies");
      const sorted = [...companies].sort((a, b) => a.name.localeCompare(b.name));
      const sel = document.getElementById("group-select");
      sel.innerHTML =
        `<option value="">All</option>` +
        `<option value="http">HTTP</option>` +
        `<option value="playwright">Playwright</option>` +
        `<option disabled>──────────────</option>` +
        sorted.map(c => {
          const label = c.playwright ? `${c.name} (Playwright)` : c.name;
          return `<option value="c:${_esc(c.name)}">${_esc(label)}</option>`;
        }).join("");
    } catch (e) {
      console.error("Failed to load company list:", e);
    }
  }

  // ── Settings panel ────────────────────────────────────────────────────────

  let _prefs = null;
  let _settingsCompanies = [];

  const _ATS_LABELS = {
    greenhouse: "GH", ashby: "AS", lever: "LV", google: "GO", apple: "AP",
    meta: "ME", microsoft: "MS", uber: "UB", walmart: "WM", netflix: "NF",
    zillow: "ZI", amazon: "AZ", linkedin: "LI", jsearch: "JS",
  };

  const _ATS_OPTIONS = [
    ["greenhouse", "Greenhouse"], ["ashby", "Ashby"], ["lever", "Lever"],
    ["jsearch", "JSearch"],
    ["google", "Google (custom)"], ["apple", "Apple (custom)"], ["meta", "Meta (custom)"],
    ["microsoft", "Microsoft (custom)"], ["uber", "Uber (custom)"],
    ["walmart", "Walmart (custom)"], ["netflix", "Netflix (custom)"],
    ["zillow", "Zillow (custom)"], ["amazon", "Amazon (custom)"], ["linkedin", "LinkedIn (custom)"],
  ];

  const _CHIP_FIELDS = [
    "title_keywords", "title_exclude_keywords",
    "preferred_locations", "acceptable_locations", "excluded_location_keywords",
  ];

  function switchView(view) {
    ["jobs", "settings"].forEach(v => {
      const el = document.getElementById(`${v}-view`);
      const btn = document.querySelector(`.main-tab[data-view="${v}"]`);
      const active = v === view;
      if (el) el.classList.toggle("active", active);
      if (btn) btn.classList.toggle("active", active);
    });
    if (view === "settings") {
      // Close job detail so fixed-position panel doesn't overlay settings
      document.getElementById("detail-pane").classList.remove("open");
      _currentJob = null;
      _loadSettingsData();
    }
  }

  function openSettings() { switchView("settings"); }
  function closeSettings() { switchView("jobs"); }

  async function _loadSettingsData() {
    try {
      [_settingsCompanies, _prefs] = await Promise.all([
        _api("GET", "/api/settings/companies"),
        _api("GET", "/api/settings/preferences"),
      ]);
      _renderSettingsCompanies();
      _renderSettingsPreferences();
    } catch (e) {
      console.error("Failed to load settings:", e);
    }
  }

  function settingsTab(tab) {
    _activeSettingsTab = tab;
    ["companies", "preferences", "runs", "logs", "admin", "resume", "schedule", "profile"].forEach(t => {
      const el = document.getElementById(`settings-tab-${t}`);
      if (el) el.classList.toggle("settings-body--active", t === tab);
      const btn = document.querySelector(`.settings-tab[data-tab="${t}"]`);
      if (btn) btn.classList.toggle("active", t === tab);
    });
    if (tab === "runs")     _loadRunsTab();
    if (tab === "logs")     _loadLogsTab();
    if (tab === "admin")    _loadAdminTab();
    if (tab === "resume")   _loadResumeTab();
    if (tab === "schedule") _loadScheduleTab();
    if (tab === "profile")  _loadProfileTab();
  }

  async function _loadAdminTab() {
    const emailEl = document.getElementById("admin-email-list");
    const userEl  = document.getElementById("admin-user-list");
    emailEl.innerHTML = '<div class="settings-empty">Loading...</div>';
    userEl.innerHTML  = '<div class="settings-empty">Loading...</div>';
    try {
      const [emails, users] = await Promise.all([
        _api("GET", "/api/admin/allowed-emails"),
        _api("GET", "/api/admin/users"),
      ]);
      emailEl.innerHTML = emails.length
        ? emails.map(e => `
            <div class="settings-company-row">
              <div class="company-info"><span class="company-name-text">${_esc(e.email)}</span>
                <span class="company-slug">added by ${_esc(e.added_by || "—")}</span></div>
              <button class="btn-danger btn-sm" onclick="App.adminRemoveEmail('${_esc(e.email)}')">Remove</button>
            </div>`).join("")
        : '<div class="settings-empty">No emails in allowlist.</div>';
      userEl.innerHTML = users.length
        ? `<table class="runs-table"><thead><tr><th>Email</th><th>Name</th><th>Admin</th><th>Since</th></tr></thead><tbody>${
            users.map(u => `<tr>
              <td>${_esc(u.email)}</td>
              <td>${_esc(u.name)}</td>
              <td>${u.is_admin ? "✓" : ""}</td>
              <td>${u.created_at.slice(0,10)}</td>
            </tr>`).join("")
          }</tbody></table>`
        : '<div class="settings-empty">No users yet.</div>';
    } catch (e) {
      emailEl.innerHTML = '<div class="settings-empty">Failed to load.</div>';
    }
  }

  async function adminAddEmail() {
    const input = document.getElementById("new-allowed-email");
    const email = input.value.trim();
    if (!email) return;
    try {
      await _api("POST", "/api/admin/allowed-emails", { email });
      input.value = "";
      _loadAdminTab();
    } catch (e) { alert(`Error: ${e.message}`); }
  }

  async function adminRemoveEmail(email) {
    if (!confirm(`Remove ${email} from allowlist?`)) return;
    try {
      await _api("DELETE", `/api/admin/allowed-emails/${encodeURIComponent(email)}`);
      _loadAdminTab();
    } catch (e) { alert(`Error: ${e.message}`); }
  }

  async function _loadRunsTab() {
    const el = document.getElementById("runs-list");
    el.innerHTML = '<div class="settings-empty">Loading...</div>';
    try {
      const runs = await _api("GET", "/api/runs?limit=30");
      _renderRunsTable(runs);
    } catch (e) {
      el.innerHTML = '<div class="settings-empty">Failed to load run history.</div>';
    }
  }

  function _fmtDuration(start, end) {
    const s = Math.round((new Date(end) - new Date(start)) / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60), r = s % 60;
    return r > 0 ? `${m}m ${r}s` : `${m}m`;
  }

  function _renderRunsTable(runs) {
    const el = document.getElementById("runs-list");
    if (!runs.length) {
      el.innerHTML = '<div class="settings-empty">No runs recorded yet. Run the pipeline to see history here.</div>';
      return;
    }
    const ACTION_LABELS = {
      source_and_score: "Source+Score", source: "Source",
      score: "Score", rescore: "Rescore",
    };
    const rows = runs.map(r => {
      const dt = new Date(r.started_at);
      const dateStr = dt.toLocaleDateString([], {month: "short", day: "numeric"});
      const timeStr = dt.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
      const dur = r.ended_at ? _fmtDuration(r.started_at, r.ended_at) : "—";
      const statusCls = r.status === "done" ? "run-status-done"
                      : r.status === "error" ? "run-status-error"
                      : "run-status-running";
      const statusLabel = r.status === "error" && r.error_msg
        ? `<span class="${statusCls}" title="${_esc(r.error_msg)}">error ⚠</span>`
        : `<span class="${statusCls}">${r.status}</span>`;
      return `<tr>
        <td class="run-when">${dateStr}<br><span class="run-time">${timeStr}</span></td>
        <td><span class="run-badge">${_esc(ACTION_LABELS[r.action] || r.action)}</span></td>
        <td><span class="run-badge run-group-badge">${_esc(r.group_type)}</span></td>
        <td class="run-num">${r.companies_count}</td>
        <td class="run-num">${r.jobs_fetched}</td>
        <td class="run-num">${r.jobs_new}</td>
        <td class="run-num">${r.jobs_scored}</td>
        <td class="run-num run-dur">${dur}</td>
        <td>${statusLabel}</td>
      </tr>`;
    }).join("");
    el.innerHTML = `<div class="runs-scroll"><table class="runs-table">
      <thead><tr>
        <th>When</th><th>Action</th><th>Group</th>
        <th title="Companies targeted">Cos</th>
        <th title="Jobs fetched from ATS">Fetched</th>
        <th title="New (not seen before)">New</th>
        <th title="Scored this run">Scored</th>
        <th>Duration</th><th>Status</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  }

  // ── Settings: logs ────────────────────────────────────────────────────────

  async function _loadLogsTab() {
    const listEl = document.getElementById("logs-file-list");
    const contentEl = document.getElementById("logs-file-content");
    contentEl.style.display = "none";
    listEl.style.display = "";
    listEl.innerHTML = '<div class="settings-empty">Loading...</div>';
    try {
      const files = await _api("GET", "/api/logs");
      if (!files.length) {
        listEl.innerHTML = '<div class="settings-empty">No log files found. Run the pipeline to generate logs.</div>';
        return;
      }
      listEl.innerHTML = files.map(f => {
        const kb = (f.size_bytes / 1024).toFixed(1);
        return `<div class="log-file-row" onclick="App.loadLogFile('${_esc(f.filename)}')">
          <div class="log-file-info">
            <span class="log-file-date">${_esc(f.date)}</span>
            <span class="log-file-id">${_esc(f.task_id)}</span>
          </div>
          <span class="log-file-size">${kb} KB</span>
        </div>`;
      }).join("");
    } catch (e) {
      listEl.innerHTML = `<div class="settings-empty">Failed to load logs: ${_esc(e.message)}</div>`;
    }
  }

  async function loadLogFile(filename) {
    const listEl = document.getElementById("logs-file-list");
    const contentEl = document.getElementById("logs-file-content");
    const titleEl = document.getElementById("logs-content-title");
    const bodyEl = document.getElementById("logs-content-body");
    listEl.style.display = "none";
    contentEl.style.display = "";
    titleEl.textContent = filename;
    bodyEl.textContent = "Loading…";
    try {
      const data = await _api("GET", `/api/logs/${encodeURIComponent(filename)}`);
      bodyEl.textContent = data.content;
    } catch (e) {
      bodyEl.textContent = `Error: ${e.message}`;
    }
  }

  function closeLogFile() {
    document.getElementById("logs-file-list").style.display = "";
    document.getElementById("logs-file-content").style.display = "none";
  }

  // ── Settings: companies ────────────────────────────────────────────────────

  function _renderSettingsCompanies() {
    const list = document.getElementById("settings-company-list");
    if (!_settingsCompanies.length) {
      list.innerHTML = '<div class="settings-empty">No companies configured.</div>';
      return;
    }
    const isAdmin = _currentUser && _currentUser.is_admin;
    list.innerHTML = _settingsCompanies.map((c, i) => {
      const badge = _ATS_LABELS[c.ats] || c.ats.slice(0, 2).toUpperCase();
      const atsOpts = _ATS_OPTIONS.map(([val, label]) =>
        `<option value="${val}"${c.ats === val ? " selected" : ""}>${_esc(label)}</option>`
      ).join("");
      const sourceControls = isAdmin ? `
        <div class="company-source-row">
          <select class="settings-select settings-select--sm" onchange="App.onCompanyAtsChange(this,${i})">
            ${atsOpts}
          </select>
          <input class="settings-input settings-input--sm" placeholder="Employer (JSearch)"
            value="${_esc(c.employer || "")}"
            style="${c.ats === "jsearch" ? "" : "display:none"}"
            data-employer-idx="${i}">
          <button class="btn-primary btn-sm" onclick="App.updateCompanySource(${i})">Save</button>
        </div>` : "";
      return `
        <div class="settings-company-row">
          <span class="ats-badge">${_esc(badge)}</span>
          <div class="company-info">
            <span class="company-name-text">${_esc(c.name)}</span>
            ${c.board_slug ? `<span class="company-slug">${_esc(c.board_slug)}</span>` : ""}
            ${sourceControls}
          </div>
          <button class="btn-danger btn-sm" onclick="App.removeCompany(${i})">Remove</button>
        </div>`;
    }).join("");
  }

  function onCompanyAtsChange(selectEl, idx) {
    const row = selectEl.closest(".settings-company-row");
    const employerInput = row.querySelector(`[data-employer-idx="${idx}"]`);
    if (employerInput) employerInput.style.display = selectEl.value === "jsearch" ? "" : "none";
  }

  async function updateCompanySource(idx) {
    const company = _settingsCompanies[idx];
    const row = document.querySelectorAll(".settings-company-row")[idx];
    const ats = row.querySelector("select").value;
    const employerInput = row.querySelector(`[data-employer-idx="${idx}"]`);
    const employer = employerInput ? employerInput.value.trim() : "";
    try {
      const updated = await _api("PATCH", `/api/settings/companies/${encodeURIComponent(company.name)}/source`,
        { ats, employer: employer || null });
      _settingsCompanies[idx] = updated.company;
      _renderSettingsCompanies();
    } catch (e) {
      alert(e.message);
    }
  }

  async function removeCompany(idx) {
    const company = _settingsCompanies[idx];
    if (!company || !confirm(`Remove ${company.name}?`)) return;
    try {
      await _api("DELETE", `/api/settings/companies/${encodeURIComponent(company.name)}`);
      _settingsCompanies.splice(idx, 1);
      _renderSettingsCompanies();
      _loadGroupSelect();
    } catch (e) {
      alert(e.message);
    }
  }

  async function detectAts() {
    const name = document.getElementById("new-company-name").value.trim();
    if (!name) return;
    const btn = document.getElementById("detect-btn");
    btn.disabled = true;
    btn.textContent = "Detecting…";
    document.getElementById("detect-result").style.display = "none";
    try {
      const result = await _api("POST", "/api/companies/detect", { name });
      const statusEl = document.getElementById("detect-status");
      document.getElementById("detect-result").style.display = "";
      if (result.ats) {
        document.getElementById("new-ats").value = result.ats;
        document.getElementById("new-slug").value = result.board_slug || "";
        statusEl.textContent = `Detected: ${result.ats}${result.board_slug ? " / " + result.board_slug : ""}`;
        statusEl.className = "detect-status detect-ok";
      } else {
        statusEl.textContent = result.error || "Could not detect ATS. Fill in manually.";
        statusEl.className = "detect-status detect-error";
      }
    } catch (e) {
      alert(e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Auto-detect";
    }
  }

  function onNewAtsChange() {
    const ats = document.getElementById("new-ats").value;
    const employerEl = document.getElementById("new-employer");
    if (employerEl) employerEl.style.display = ats === "jsearch" ? "" : "none";
  }

  async function addCompany() {
    const name     = document.getElementById("new-company-name").value.trim();
    const ats      = document.getElementById("new-ats").value;
    const slug     = document.getElementById("new-slug").value.trim();
    const employer = document.getElementById("new-employer").value.trim();
    if (!name || !ats) { alert("Name and ATS are required."); return; }
    try {
      await _api("POST", "/api/settings/companies",
        { name, ats, board_slug: slug || null, employer: employer || null });
      document.getElementById("new-company-name").value = "";
      document.getElementById("new-slug").value = "";
      document.getElementById("detect-result").style.display = "none";
      _settingsCompanies = await _api("GET", "/api/settings/companies");
      _renderSettingsCompanies();
      _loadGroupSelect();
    } catch (e) {
      alert(e.message);
    }
  }

  // ── Settings: preferences ──────────────────────────────────────────────────

  function _renderSettingsPreferences() {
    if (!_prefs) return;
    document.getElementById("pref-threshold").value = _prefs.match_threshold ?? 7;
    document.getElementById("pref-llm").value = _prefs.llm_provider || "gemini";
    document.getElementById("pref-us-only").checked = !!_prefs.us_only;
    _CHIP_FIELDS.forEach(key => _renderChips(key));
  }

  function _renderChips(key) {
    const container = document.getElementById(`chips-${key}`);
    if (!container) return;
    const items = (_prefs && _prefs[key]) || [];
    container.innerHTML =
      items.map((item, i) =>
        `<span class="chip">${_esc(item)}<button class="chip-x" onclick="App.removeChip('${_esc(key)}',${i})">&#xd7;</button></span>`
      ).join("") +
      `<input class="chip-input" placeholder="Add…" onkeydown="App.chipKeydown(event,'${_esc(key)}',this)">`;
  }

  function removeChip(key, idx) {
    if (!_prefs || !_prefs[key]) return;
    _prefs[key] = _prefs[key].filter((_, i) => i !== idx);
    _renderChips(key);
  }

  function chipKeydown(event, key, input) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const val = input.value.trim();
    if (!val) return;
    if (!_prefs[key]) _prefs[key] = [];
    if (!_prefs[key].includes(val)) _prefs[key].push(val);
    _renderChips(key);
  }

  async function savePreferences() {
    const updates = {
      match_threshold: parseInt(document.getElementById("pref-threshold").value, 10) || 7,
      llm_provider:   document.getElementById("pref-llm").value,
      us_only:        document.getElementById("pref-us-only").checked,
    };
    _CHIP_FIELDS.forEach(key => { updates[key] = (_prefs && _prefs[key]) || []; });

    const btn = document.getElementById("save-prefs-btn");
    btn.disabled = true;
    try {
      await _api("PUT", "/api/settings/preferences", updates);
      btn.textContent = "Saved!";
      setTimeout(() => { btn.textContent = "Save Preferences"; btn.disabled = false; }, 2000);
    } catch (e) {
      alert(e.message);
      btn.disabled = false;
    }
  }

  // ── Task progress drawer ──────────────────────────────────────────────────

  function _elapsed(startedAt) {
    if (!startedAt) return "";
    const secs = Math.floor((Date.now() - new Date(startedAt)) / 1000);
    const m = Math.floor(secs / 60), s = secs % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  }

  function _startTask(taskId, label, onDone) {
    clearInterval(_pollTimer);
    const drawer = document.getElementById("task-drawer");
    const logEl = document.getElementById("task-log");
    const titleEl = document.getElementById("task-drawer-title");
    titleEl.innerHTML = `<span class="spinner"></span> ${label}`;
    logEl.innerHTML = "";
    drawer.classList.add("open");

    let lastLogLen = 0;
    let startedAt = null;

    _pollTimer = setInterval(async () => {
      let task;
      try {
        task = await _api("GET", `/api/tasks/${taskId}`);
      } catch {
        return;
      }
      if (task.started_at) startedAt = task.started_at;

      const newLines = task.logs.slice(lastLogLen);
      lastLogLen = task.logs.length;
      newLines.forEach(line => {
        const div = document.createElement("div");
        div.className = line.includes("ERROR") ? "log-error" : "";
        div.textContent = line;
        logEl.appendChild(div);
        logEl.scrollTop = logEl.scrollHeight;
      });

      if (task.status === "running" && startedAt) {
        titleEl.innerHTML = `<span class="spinner"></span> ${label} <span class="elapsed">(${_elapsed(startedAt)})</span>`;
      }

      if (task.status === "done") {
        clearInterval(_pollTimer);
        const dur = startedAt && task.ended_at
          ? ` in ${_elapsed(startedAt)}`
          : "";
        titleEl.innerHTML = `<span style="color:var(--green)">&#10003;</span> ${label} Done${dur}.`;
        const done = document.createElement("div");
        done.className = "log-done";
        done.textContent = `Completed${dur}.`;
        logEl.appendChild(done);
        logEl.scrollTop = logEl.scrollHeight;
        if (onDone) onDone();
      } else if (task.status === "error") {
        clearInterval(_pollTimer);
        const dur = startedAt ? ` after ${_elapsed(startedAt)}` : "";
        titleEl.innerHTML = `<span style="color:var(--red)">&#x2717;</span> ${label} Failed${dur}.`;
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

  // ── Requirements breakdown renderer ──────────────────────────────────────

  function _renderRequirements(requirements) {
    if (!requirements || !requirements.length) return "";
    const rows = requirements.map(r => {
      const fit = r.fit || "Partial";
      const cls = fit === "Strong" ? "fit-strong" : fit === "Gap" ? "fit-gap" : "fit-partial";
      const suggestion = r.resume_suggestion
        ? `<div class="req-suggestion">${_esc(r.resume_suggestion)}</div>` : "";
      return `
        <div class="req-row">
          <span class="fit-badge ${cls}">${_esc(fit)}</span>
          <div class="req-body">
            <div class="req-text">${_esc(r.requirement || r.text || "")}</div>
            <div class="req-evidence">${_esc(r.evidence || "")}</div>
            ${suggestion}
          </div>
        </div>`;
    }).join("");
    return `<div class="match-section">
      <div class="match-section-title">Requirements Breakdown</div>
      <div class="req-list">${rows}</div>
    </div>`;
  }

  // ── Utilities ─────────────────────────────────────────────────────────────

  function _fmtDate(isoStr) {
    if (!isoStr) return null;
    const d = new Date(isoStr);
    if (isNaN(d)) return null;
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  function _dateLine(job) {
    const posted = _fmtDate(job.date_posted);
    const sourced = _fmtDate(job.date_last_sourced);
    if (!posted && !sourced) return "";
    const parts = [];
    if (posted) parts.push(`Posted ${posted}`);
    if (sourced) parts.push(`Sourced ${sourced}`);
    return parts.join(" · ");
  }

  function _formatDescription(text) {
    if (!text) return "";
    // Convert inline bullet characters to newline + dash
    text = text.replace(/([^\n])\s*[•·◦‣▪▸]\s*/g, "$1\n- ");
    // Ensure section headers start on their own line
    text = text.replace(
      /([^\n])\s*(Responsibilities|Requirements?|Qualifications?|About [Yy]ou|About [Tt]he [Rr]ole|What [Yy]ou'?ll|What [Ww]e'?re|Who [Yy]ou|Nice to [Hh]ave|Preferred|Benefits|Minimum Qualifications|Basic Qualifications|Your Impact|What [Ww]e [Oo]ffer|The Role|What [Yy]ou'?ll [Bb]ring)/g,
      "$1\n\n$2"
    );
    // Collapse excess blank lines
    text = text.replace(/\n{3,}/g, "\n\n");
    return text.trim();
  }

  function _stripHtml(html) {
    if (!html) return "";
    // Plain text (no HTML tags) — return as-is so newlines are preserved
    if (!/<[a-z]/i.test(html)) return html;
    const el = document.createElement("div");
    el.innerHTML = html;
    // innerText preserves block-level newlines; textContent does not
    return (el.innerText || el.textContent || "")
      .replace(/[ \t]+/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function _esc(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── Profile switcher ─────────────────────────────────────────────────────

  function _getActiveProfileCookie() {
    const m = document.cookie.match(/(?:^|;\s*)active_profile_id=([^;]+)/);
    return m ? m[1] : null;
  }

  function _setProfileCookie(id) {
    document.cookie = `active_profile_id=${encodeURIComponent(id)}; path=/; max-age=31536000`;
  }

  async function _loadProfiles() {
    try {
      _profiles = await _api("GET", "/api/profiles");
    } catch { return; }
    const cookieId = _getActiveProfileCookie();
    const found = _profiles.find(p => p.profile_id === cookieId);
    _activeProfileId = found ? found.profile_id : (_profiles[0] ? _profiles[0].profile_id : null);
    _renderProfileSwitcher();
  }

  function _renderProfileSwitcher() {
    const group = document.getElementById("profile-group");
    const sel = document.getElementById("profile-select");
    if (!group || !sel) return;
    if (!_profiles.length) { group.style.display = "none"; return; }
    sel.innerHTML = _profiles.map(p =>
      `<option value="${_esc(p.profile_id)}" ${p.profile_id === _activeProfileId ? "selected" : ""}>${_esc(p.name)}</option>`
    ).join("");
    group.style.display = "flex";
  }

  async function switchProfile(id) {
    if (id === _activeProfileId) return;
    _activeProfileId = id;
    _setProfileCookie(id);
    await loadJobs();
    const settingsView = document.getElementById("settings-view");
    if (settingsView && settingsView.classList.contains("active")) {
      settingsTab(_activeSettingsTab);
    }
  }

  async function createProfile() {
    const name = prompt("Profile name:");
    if (!name || !name.trim()) return;
    try {
      const p = await _api("POST", "/api/profiles", { name: name.trim() });
      await _loadProfiles();
      await switchProfile(p.profile_id);
    } catch (e) { alert(e.message); }
  }

  async function createProfileFromForm() {
    const input = document.getElementById("new-profile-name");
    const name = (input ? input.value : "").trim();
    if (!name) return;
    try {
      const p = await _api("POST", "/api/profiles", { name });
      if (input) input.value = "";
      await _loadProfiles();
      await switchProfile(p.profile_id);
      _loadProfileTab();
    } catch (e) { alert(e.message); }
  }

  async function renameProfile(id, currentName) {
    const name = prompt("New name:", currentName);
    if (!name || !name.trim() || name.trim() === currentName) return;
    try {
      await _api("PATCH", `/api/profiles/${id}`, { name: name.trim() });
      await _loadProfiles();
      _loadProfileTab();
    } catch (e) { alert(e.message); }
  }

  async function deleteProfile(id, name) {
    if (!confirm(`Delete profile "${name}"? This cannot be undone.`)) return;
    try {
      await _api("DELETE", `/api/profiles/${id}`);
      if (_activeProfileId === id) _activeProfileId = null;
      await _loadProfiles();
      if (!_activeProfileId && _profiles.length) {
        _activeProfileId = _profiles[0].profile_id;
        _setProfileCookie(_activeProfileId);
        loadJobs();
      }
      _loadProfileTab();
    } catch (e) { alert(e.message); }
  }

  function _loadProfileTab() {
    const section = document.getElementById("profile-list-section");
    if (!section) return;
    if (!_profiles.length) {
      section.innerHTML = '<div class="settings-empty">No profiles found.</div>';
      return;
    }
    section.innerHTML = _profiles.map(p => `
      <div class="settings-company-row">
        <div class="company-info">
          <span class="company-name-text">${_esc(p.name)}</span>
          ${p.is_legacy ? '<span class="company-slug">legacy</span>' : ''}
          ${p.profile_id === _activeProfileId ? '<span class="company-slug active-badge">active</span>' : ''}
        </div>
        <div style="display:flex;gap:6px">
          <button class="btn-ghost btn-sm" onclick="App.renameProfile('${_esc(p.profile_id)}','${_esc(p.name).replace(/'/g,"\\'")}')">Rename</button>
          <button class="btn-danger btn-sm" onclick="App.deleteProfile('${_esc(p.profile_id)}','${_esc(p.name).replace(/'/g,"\\'")}')">Delete</button>
        </div>
      </div>`).join("");
  }

  // ── Resume settings ───────────────────────────────────────────────────────

  async function _apiUpload(path, formData) {
    const res = await fetch(path, { method: "POST", body: formData, credentials: "same-origin" });
    if (res.status === 401) { _showAuth(); throw new Error("Unauthorized"); }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  }

  async function _loadResumeTab() {
    const section = document.getElementById("resume-info-section");
    if (!section) return;
    section.innerHTML = '<div class="settings-empty">Loading...</div>';
    try {
      const res = await fetch("/api/resume", { credentials: "same-origin" });
      if (res.status === 404) {
        section.innerHTML = '<div class="settings-empty">No resume uploaded yet.</div>';
        return;
      }
      if (!res.ok) throw new Error(res.statusText);
      const info = await res.json();
      const kb = (info.size_bytes / 1024).toFixed(1);
      section.innerHTML = `
        <div class="resume-info-row">
          <div class="resume-file-info">
            <span class="resume-filename">${_esc(info.filename)}</span>
            <span class="resume-size">${kb} KB</span>
          </div>
          <button class="btn-danger btn-sm" onclick="App.deleteResume()">Delete</button>
        </div>`;
    } catch {
      section.innerHTML = '<div class="settings-empty">Failed to load resume info.</div>';
    }
  }

  async function uploadResume(input) {
    const file = input.files[0];
    if (!file) return;
    const statusEl = document.getElementById("resume-upload-status");
    if (statusEl) { statusEl.textContent = "Uploading…"; statusEl.className = "save-status"; }
    const formData = new FormData();
    formData.append("file", file);
    try {
      await _apiUpload("/api/resume", formData);
      input.value = "";
      if (statusEl) { statusEl.textContent = "Uploaded!"; statusEl.className = "save-status save-ok"; setTimeout(() => { statusEl.textContent = ""; }, 2500); }
      _loadResumeTab();
    } catch (e) {
      if (statusEl) { statusEl.textContent = `Failed: ${e.message}`; statusEl.className = "save-status save-error"; }
    }
  }

  async function deleteResume() {
    if (!confirm("Delete the uploaded resume?")) return;
    try {
      await _api("DELETE", "/api/resume");
      _loadResumeTab();
    } catch (e) { alert(e.message); }
  }

  // ── Schedule settings ─────────────────────────────────────────────────────

  async function _loadScheduleTab() {
    if (!_activeProfileId) return;
    const statusEl = document.getElementById("sched-status");
    try {
      const res = await fetch(`/api/profiles/${_activeProfileId}/schedule`, { credentials: "same-origin" });
      if (res.status === 404) {
        if (statusEl) { statusEl.textContent = "Scheduling is not available for this profile type."; statusEl.className = "save-status"; }
        return;
      }
      if (!res.ok) throw new Error(res.statusText);
      const s = await res.json();
      document.getElementById("sched-enabled").checked = !!s.enabled;
      document.getElementById("sched-time-1").value = s.time_1 || "";
      document.getElementById("sched-time-2").value = s.time_2 || "";
      const tzSel = document.getElementById("sched-timezone");
      if (tzSel) tzSel.value = s.timezone || "UTC";
      if (statusEl) statusEl.textContent = "";
    } catch (e) { console.error("Failed to load schedule:", e); }
  }

  async function saveSchedule() {
    if (!_activeProfileId) return;
    const statusEl = document.getElementById("sched-status");
    const enabled  = document.getElementById("sched-enabled").checked;
    const time_1   = document.getElementById("sched-time-1").value || null;
    const time_2   = document.getElementById("sched-time-2").value || null;
    const timezone = document.getElementById("sched-timezone").value;
    if (statusEl) { statusEl.textContent = "Saving…"; statusEl.className = "save-status"; }
    try {
      await _api("PUT", `/api/profiles/${_activeProfileId}/schedule`, { time_1, time_2, timezone, enabled });
      if (statusEl) { statusEl.textContent = "Saved!"; statusEl.className = "save-status save-ok"; setTimeout(() => { statusEl.textContent = ""; }, 2500); }
    } catch (e) {
      if (statusEl) { statusEl.textContent = `Error: ${e.message}`; statusEl.className = "save-status save-error"; }
    }
  }

  async function clearSchedule() {
    if (!_activeProfileId) return;
    if (!confirm("Clear the schedule for this profile?")) return;
    try {
      await _api("DELETE", `/api/profiles/${_activeProfileId}/schedule`);
      document.getElementById("sched-enabled").checked = false;
      document.getElementById("sched-time-1").value = "";
      document.getElementById("sched-time-2").value = "";
      const statusEl = document.getElementById("sched-status");
      if (statusEl) { statusEl.textContent = "Schedule cleared."; statusEl.className = "save-status"; setTimeout(() => { statusEl.textContent = ""; }, 2500); }
    } catch (e) { alert(e.message); }
  }

  // ── Feedback ──────────────────────────────────────────────────────────────

  function openFeedback() {
    document.getElementById("feedback-title").value = "";
    document.getElementById("feedback-body").value = "";
    document.getElementById("feedback-status").textContent = "";
    document.getElementById("feedback-overlay").style.display = "flex";
    document.getElementById("feedback-body").focus();
  }

  function closeFeedback() {
    document.getElementById("feedback-overlay").style.display = "none";
  }

  async function submitFeedback() {
    const title = document.getElementById("feedback-title").value.trim();
    const body = document.getElementById("feedback-body").value.trim();
    const statusEl = document.getElementById("feedback-status");
    if (!body) {
      statusEl.textContent = "Please enter a description.";
      statusEl.className = "save-status save-error";
      return;
    }
    statusEl.textContent = "Submitting…";
    statusEl.className = "save-status";
    try {
      const data = await _api("POST", "/api/feedback", { title, body });
      statusEl.textContent = `Submitted! Issue #${data.issue_number}`;
      statusEl.className = "save-status save-ok";
      setTimeout(closeFeedback, 2000);
    } catch (e) {
      statusEl.textContent = `Failed: ${e.message}`;
      statusEl.className = "save-status save-error";
    }
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  async function init() {
    try {
      _currentUser = await _api("GET", "/api/auth/me");
      _updateUserUI();
    } catch {
      _showAuth();
      return;
    }
    await _loadProfiles();
    loadJobs();
    _loadGroupSelect();
  }

  document.addEventListener("DOMContentLoaded", init);

  document.addEventListener("DOMContentLoaded", () => {
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
           setStatus, bulkStatusFromSelect, rescore, analyze, generateCoverLetter, exportCoverLetterPdf, saveCoverLetter,
           triggerRun,
           closeDrawer,
           toggleSelectMode, toggleCheck, clearSelection, bulkStatus,
           switchView, openSettings, closeSettings, settingsTab,
           removeCompany, detectAts, addCompany, onNewAtsChange, onCompanyAtsChange, updateCompanySource,
           removeChip, chipKeydown, savePreferences,
           _loadRunsTab,
           loadLogFile, closeLogFile,
           logout, adminAddEmail, adminRemoveEmail,
           switchProfile, createProfile, createProfileFromForm, renameProfile, deleteProfile,
           uploadResume, deleteResume,
           saveSchedule, clearSchedule,
           openFeedback, closeFeedback, submitFeedback };
})();
