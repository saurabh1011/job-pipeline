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
      _loadGroupSelect();
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
    zillow: "ZI", amazon: "AZ", linkedin: "LI",
  };

  const _CHIP_FIELDS = [
    "title_keywords", "title_exclude_keywords",
    "preferred_locations", "acceptable_locations", "excluded_location_keywords",
  ];

  function openSettings() {
    document.getElementById("settings-panel").classList.add("open");
    _loadSettingsData();
  }

  function closeSettings() {
    document.getElementById("settings-panel").classList.remove("open");
  }

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
    ["companies", "preferences", "runs", "logs"].forEach(t => {
      document.getElementById(`settings-tab-${t}`).style.display = t === tab ? "" : "none";
      const btn = document.querySelector(`.settings-tab[data-tab="${t}"]`);
      if (btn) btn.classList.toggle("active", t === tab);
    });
    if (tab === "runs") _loadRunsTab();
    if (tab === "logs") _loadLogsTab();
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
    list.innerHTML = _settingsCompanies.map((c, i) => {
      const badge = _ATS_LABELS[c.ats] || c.ats.slice(0, 2).toUpperCase();
      return `
        <div class="settings-company-row">
          <span class="ats-badge">${_esc(badge)}</span>
          <div class="company-info">
            <span class="company-name-text">${_esc(c.name)}</span>
            ${c.board_slug ? `<span class="company-slug">${_esc(c.board_slug)}</span>` : ""}
          </div>
          <button class="btn-danger btn-sm" onclick="App.removeCompany(${i})">Remove</button>
        </div>`;
    }).join("");
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

  async function addCompany() {
    const name = document.getElementById("new-company-name").value.trim();
    const ats  = document.getElementById("new-ats").value;
    const slug = document.getElementById("new-slug").value.trim();
    if (!name || !ats) { alert("Name and ATS are required."); return; }
    try {
      await _api("POST", "/api/settings/companies", { name, ats, board_slug: slug || null });
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
    _loadGroupSelect();
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
           setStatus, bulkStatusFromSelect, rescore, analyze, generateCoverLetter, exportCoverLetterPdf, saveCoverLetter,
           triggerRun,
           closeDrawer, submitApiKey,
           toggleSelectMode, toggleCheck, clearSelection, bulkStatus,
           openSettings, closeSettings, settingsTab,
           removeCompany, detectAts, addCompany,
           removeChip, chipKeydown, savePreferences,
           _loadRunsTab,
           loadLogFile, closeLogFile };
})();
