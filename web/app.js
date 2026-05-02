const state = {
  offset: 0,
  limit: 100,
  groups: [],
  currentJob: null,
};

const $ = (id) => document.getElementById(id);

function fmtBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Number(bytes);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

async function getJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function loadConfig() {
  const cfg = await getJson("/api/config");
  $("config-summary").textContent = `${cfg.exclude_dirnames.length} dirname excludes, machine ${cfg.machine_name}`;
}

async function loadMachines() {
  const machines = await getJson("/api/machines");
  $("machine").innerHTML = machines.map((m) => `<option value="${m}">${m}</option>`).join("");
}

async function loadVolumes() {
  const volumes = await getJson("/api/volumes");
  $("drive").innerHTML = volumes.map((v) => `<option value="${v.mountpoint}">${v.label}</option>`).join("");
}

async function loadScans() {
  const machine = $("machine").value;
  const scans = await getJson(`/api/scans?machine=${encodeURIComponent(machine)}`);
  $("scan").innerHTML = scans
    .map((s) => `<option value="${s.id}">#${s.id} ${s.volume} ${s.status} ${s.files_inserted || 0} files</option>`)
    .join("");
  if (scans.length) {
    await loadDuplicates();
    await loadTreemap();
  } else {
    $("groups").innerHTML = "";
    $("members").innerHTML = "";
  }
}

async function loadDuplicates() {
  const scanId = $("scan").value;
  if (!scanId) return;
  const machine = $("machine").value;
  const mode = $("mode").value;
  const rows = await getJson(
    `/api/duplicates?scan_id=${scanId}&machine=${encodeURIComponent(machine)}&mode=${mode}&limit=${state.limit}&offset=${state.offset}`,
  );
  state.groups = rows;
  $("groups").innerHTML = rows
    .map((r, i) => {
      const key = mode === "sha256" ? r.sha256 : r.name;
      return `<tr data-index="${i}"><td>${r.n}</td><td>${fmtBytes(r.size)}</td><td>${key || ""}</td><td>${r.sample_path || ""}</td></tr>`;
    })
    .join("");
  [...$("groups").querySelectorAll("tr")].forEach((row) => {
    row.addEventListener("click", () => loadMembers(state.groups[Number(row.dataset.index)]));
  });
}

async function loadMembers(group) {
  const scanId = $("scan").value;
  const machine = $("machine").value;
  const mode = $("mode").value;
  const key = mode === "sha256" ? group.sha256 : group.name;
  const rows = await getJson(
    `/api/duplicates/members?scan_id=${scanId}&machine=${encodeURIComponent(machine)}&mode=${mode}&size=${group.size}&key=${encodeURIComponent(key)}`,
  );
  $("members").innerHTML = rows.map((r) => `<div>${r.dirpath}\\${r.name}<br><span class="label">${fmtBytes(r.size)}</span></div>`).join("");
}

async function loadTreemap() {
  const scanId = $("scan").value;
  if (!scanId) return;
  const machine = $("machine").value;
  const rows = await getJson(`/api/treemap?scan_id=${scanId}&machine=${encodeURIComponent(machine)}&limit=80`);
  $("treemap-note").textContent = `${rows.length} largest top-level folders`;
  const chart = echarts.init($("treemap"));
  chart.setOption({
    tooltip: { formatter: (info) => `${info.name}<br>${fmtBytes(info.value)}` },
    series: [
      {
        type: "treemap",
        roam: false,
        breadcrumb: { show: false },
        label: { formatter: "{b}" },
        data: rows.map((r) => ({ name: r.name, value: r.value })),
      },
    ],
  });
}

async function startScan() {
  const drive = $("drive").value;
  const machine = $("machine").value;
  const job = await getJson(`/api/scan?drive=${encodeURIComponent(drive)}&machine=${encodeURIComponent(machine)}`, { method: "POST" });
  watchJob(job.job_id);
}

async function startHash() {
  const scanId = $("scan").value;
  if (!scanId) return;
  const machine = $("machine").value;
  const job = await getJson(`/api/hash?scan_id=${scanId}&machine=${encodeURIComponent(machine)}`, { method: "POST" });
  watchJob(job.job_id);
}

async function resetLatest() {
  const machine = $("machine").value;
  await getJson(`/api/reset?machine=${encodeURIComponent(machine)}&scope=latest`, { method: "POST" });
  state.offset = 0;
  await loadScans();
}

async function watchJob(jobId) {
  state.currentJob = jobId;
  const timer = setInterval(async () => {
    const job = await getJson(`/api/jobs/${jobId}`);
    $("status").textContent = `${job.kind} ${job.status}`;
    if (job.status === "completed" || job.status === "failed") {
      clearInterval(timer);
      await loadScans();
    }
  }, 1500);
}

async function refreshAll() {
  await loadConfig();
  await loadMachines();
  await loadVolumes();
  await loadScans();
  $("status").textContent = "Idle";
}

$("refresh").addEventListener("click", refreshAll);
$("machine").addEventListener("change", loadScans);
$("scan").addEventListener("change", async () => {
  state.offset = 0;
  await loadDuplicates();
  await loadTreemap();
});
$("mode").addEventListener("change", loadDuplicates);
$("next").addEventListener("click", async () => {
  state.offset += state.limit;
  await loadDuplicates();
});
$("prev").addEventListener("click", async () => {
  state.offset = Math.max(0, state.offset - state.limit);
  await loadDuplicates();
});
$("start-scan").addEventListener("click", startScan);
$("start-hash").addEventListener("click", startHash);
$("reset-latest").addEventListener("click", resetLatest);

refreshAll().catch((err) => {
  $("status").textContent = err.message;
});
