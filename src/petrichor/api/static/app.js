/* Petrichor UI — the 4 steps of the mockup wired to the real API (spec §9, issue #18).
   Server state is the source of truth: every mutation goes through the API and
   the views are rebuilt from the responses. No verdict is ever invented here. */
"use strict";

const state = {
  apiKey: localStorage.getItem("petrichor.apiKey") || "",
  sessionId: localStorage.getItem("petrichor.sessionId") || "",
  modelId: null,
};

function headers(extra) {
  return Object.assign({ "X-API-Key": state.apiKey }, extra || {});
}

async function api(path, options) {
  const response = await fetch(path, Object.assign({ headers: headers() }, options || {}));
  if (response.status === 401) {
    throw new Error("Clé API invalide (401).");
  }
  return response;
}

function showStep(step) {
  for (const section of document.querySelectorAll("main section")) {
    section.hidden = section.id !== "step-" + step;
  }
  for (const button of document.querySelectorAll("#steps button")) {
    if (button.dataset.step === step) {
      button.setAttribute("aria-current", "step");
    } else {
      button.removeAttribute("aria-current");
    }
  }
}

async function importModelAndCreateSession() {
  const file = document.getElementById("pnml-file").files[0];
  if (!file) return;
  const text = await file.text();
  const imported = await api("/v1/models", {
    method: "POST",
    headers: headers({ "Content-Type": "application/pnml+xml" }),
    body: text,
  });
  if (imported.status !== 201) throw new Error("Import du modèle refusé: " + imported.status);
  state.modelId = (await imported.json()).id;
  const created = await api("/v1/sessions", {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ model_id: state.modelId }),
  });
  if (created.status !== 201) throw new Error("Création de session refusée: " + created.status);
  state.sessionId = (await created.json()).session_id;
  localStorage.setItem("petrichor.sessionId", state.sessionId);
  await loadProfile();
}

async function loadProfile() {
  const response = await api("/v1/sessions/" + state.sessionId + "/profile");
  const container = document.getElementById("profile");
  if (response.status === 404) {
    container.textContent = "Aucun profil proposé pour cette session.";
    return;
  }
  const profile = await response.json();
  container.textContent = "";
  for (const entry of profile.proposed.entries) {
    const row = document.createElement("div");
    row.className = "profile-entry";
    const label = document.createElement("span");
    label.textContent = entry.id + " — rôle proposé : " + entry.role + " (conf " + entry.conf + ")";
    const accept = document.createElement("button");
    accept.type = "button";
    accept.textContent = "Accepter";
    accept.addEventListener("click", () => arbitrate(entry.id, true, entry.role));
    const refuse = document.createElement("button");
    refuse.type = "button";
    refuse.textContent = "Refuser";
    refuse.addEventListener("click", () => arbitrate(entry.id, false, entry.role));
    row.append(label, accept, refuse);
    container.append(row);
  }
}

async function arbitrate(entryId, accepted, chosenRole) {
  await api("/v1/sessions/" + state.sessionId + "/profile/arbitrate", {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ entry_id: entryId, accepted: accepted, chosen_role: chosenRole }),
  });
  await loadProfile();
  await loadQuestions();
}

async function loadQuestions() {
  const response = await api("/v1/sessions/" + state.sessionId + "/contract", {
    headers: headers({ Accept: "application/contract+json; version=1.0" }),
  });
  if (response.status !== 200) return;
  const contract = await response.json();
  const list = document.getElementById("question-cards");
  list.textContent = "";
  for (const question of contract.questions) {
    const card = document.createElement("li");
    card.className = "question-card";
    const formula = document.createElement("p");
    formula.textContent = question.formula + " (" + question.family + ", triage " + question.triage + ")";
    const status = document.createElement("p");
    status.className = "status";
    status.textContent = "Statut : " + question.status;
    const cross = document.createElement("p");
    cross.className = "cross-status";
    if (question.cross_status === "verified_by_n") {
      cross.textContent = "Vérifiée par " + question.verified_by + " outils.";
    } else if (question.cross_status === "controversial") {
      cross.textContent = "Clause controversée — remontée à l'ingénieur, jamais tranchée ici.";
      const camps = document.createElement("ul");
      camps.className = "controversy-camps";
      for (const proof of question.proofs) {
        if (proof.status !== "done" || !proof.verdict) continue;
        const camp = document.createElement("li");
        camp.textContent = proof.tool_version + " : verdict " + proof.verdict;
        camps.append(camp);
      }
      cross.append(camps);
    } else {
      cross.textContent = "Non vérifiée.";
    }
    const decisions = document.createElement("div");
    decisions.className = "decisions";
    for (const decision of ["refuse", "accept", "verify-first"]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = { refuse: "Impossible", accept: "C'est normal", "verify-first": "Vérifier d'abord" }[decision];
      button.addEventListener("click", () => decide(question.question_id, decision));
      decisions.append(button);
    }
    card.append(formula, status, cross, decisions);
    list.append(card);
  }
}

async function decide(questionId, decision) {
  await api("/v1/sessions/" + state.sessionId + "/arbitration-requests", {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ question_id: questionId, decision: decision }),
  });
  await loadQuestions();
}

async function loadRuns() {
  const session = await (await api("/v1/sessions/" + state.sessionId)).json();
  const list = document.getElementById("run-list");
  list.textContent = "";
  // Live per-session MC budget (spec §12.4, issue #33): consumed / cap, no carry-over.
  const budgetResponse = await api("/v1/sessions/" + state.sessionId + "/budget");
  if (budgetResponse.status === 200) {
    const budget = await budgetResponse.json();
    const budgetItem = document.createElement("li");
    budgetItem.className = "budget-line";
    budgetItem.textContent = "Budget MC — consommé " + budget.consumed_seconds.toFixed(3)
      + " s / plafond " + budget.budget_seconds.toFixed(3)
      + " s — restant " + budget.remaining_seconds.toFixed(3) + " s (aucun report).";
    list.append(budgetItem);
  }
  for (const runId of session.run_ids) {
    const run = await (await api("/v1/runs/" + runId)).json();
    const item = document.createElement("li");
    const verdict = run.output && run.output.verdict ? String(run.output.verdict) : "—";
    item.innerHTML = "";
    const label = document.createElement("span");
    label.textContent = runId + " — " + run.family + " / " + run.kind + " — statut : " + run.status + " — triage : " + run.triage.kind + " — verdict : ";
    const verdictSpan = document.createElement("span");
    verdictSpan.className = "run-verdict";
    verdictSpan.textContent = verdict;
    item.append(label, verdictSpan);
    if (run.output && run.output.witness) {
      item.append(await renderWitness(runId));
    }
    list.append(item);
  }
}

/* Witness step reader (spec §16, issue #32): the structure comes from the
   tool, the narration is an LLM proposition labeled provenance: llm — never
   a verdict. The fired transition of each step is highlighted. */
async function renderWitness(runId) {
  const response = await api("/v1/runs/" + runId + "/witness");
  const container = document.createElement("div");
  container.className = "witness-reader";
  if (response.status !== 200) {
    container.textContent = "Témoin indisponible (" + response.status + ").";
    return container;
  }
  const witness = await response.json();
  const title = document.createElement("p");
  title.className = "witness-title";
  title.textContent = "Témoin " + witness.witness_id + " — " + witness.kind
    + (witness.cyclic ? " — cyclique (back-edge " + witness.back_edge + ")" : " — acyclique");
  const steps = document.createElement("ol");
  steps.className = "witness-steps";
  const narrations = new Map((witness.narrations || []).map((n) => [n.step_index, n]));
  for (const step of witness.steps) {
    const entry = document.createElement("li");
    const marking = document.createElement("span");
    marking.className = "witness-marking";
    marking.textContent = "marquage : " + step.marking.join(", ");
    entry.append(marking);
    if (step.fired) {
      const fired = document.createElement("mark");
      fired.className = "fired-transition";
      fired.textContent = "tire : " + step.fired;
      entry.append(" ", fired);
    }
    const narration = narrations.get(step.index);
    if (narration) {
      const line = document.createElement("p");
      line.className = "witness-narration";
      line.textContent = narration.text + " ";
      const provenance = document.createElement("span");
      provenance.className = "narration-provenance";
      provenance.textContent = "(provenance : " + narration.provenance + ")";
      line.append(provenance);
      entry.append(line);
    }
    steps.append(entry);
  }
  container.append(title, steps);
  return container;
}

async function loadContractMarkdown() {
  const response = await api("/v1/sessions/" + state.sessionId + "/contract", {
    headers: headers({ Accept: "text/markdown" }),
  });
  const preview = document.getElementById("contract-preview");
  if (response.status !== 200) {
    preview.textContent = "Contrat indisponible (" + response.status + ").";
    return;
  }
  preview.textContent = await response.text();
  await loadJournal();
}

async function loadJournal() {
  const response = await api("/v1/sessions/" + state.sessionId + "/contract", {
    headers: headers({ Accept: "application/contract+json; version=1.0" }),
  });
  const list = document.getElementById("journal-list");
  if (!list) return;
  list.textContent = "";
  if (response.status !== 200) return;
  const contract = await response.json();
  for (const entry of contract.journal) {
    const item = document.createElement("li");
    const detail = entry.detail && typeof entry.detail === "object"
      ? Object.entries(entry.detail).map(([key, value]) => key + "=" + value).join(", ")
      : "";
    item.textContent = entry.event + (detail ? " — " + detail : "");
    list.append(item);
  }
}

function downloadMarkdown() {
  const text = document.getElementById("contract-preview").textContent;
  const blob = new Blob([text], { type: "text/markdown" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "contrat-" + state.sessionId + ".md";
  link.click();
  URL.revokeObjectURL(link.href);
}

async function resumeSession() {
  state.sessionId = document.getElementById("session-id").value.trim();
  localStorage.setItem("petrichor.sessionId", state.sessionId);
  const response = await api("/v1/sessions/" + state.sessionId);
  if (response.status !== 200) {
    document.getElementById("profile").textContent = "Session inconnue (404).";
    return;
  }
  const session = await response.json();
  state.modelId = session.model_id;
  await loadProfile();
  await loadQuestions();
  await loadRuns();
}

function init() {
  document.getElementById("api-key").value = state.apiKey;
  document.getElementById("session-id").value = state.sessionId;
  document.getElementById("api-key").addEventListener("change", (event) => {
    state.apiKey = event.target.value;
    localStorage.setItem("petrichor.apiKey", state.apiKey);
  });
  document.getElementById("import-model").addEventListener("click", () => {
    importModelAndCreateSession().catch((error) => {
      document.getElementById("profile").textContent = String(error);
    });
  });
  document.getElementById("resume-session").addEventListener("click", () => {
    resumeSession().catch((error) => {
      document.getElementById("profile").textContent = String(error);
    });
  });
  document.getElementById("export-markdown").addEventListener("click", downloadMarkdown);
  for (const button of document.querySelectorAll("#steps button")) {
    button.addEventListener("click", () => {
      showStep(button.dataset.step);
      if (button.dataset.step === "questions") loadQuestions();
      if (button.dataset.step === "verifications") loadRuns();
      if (button.dataset.step === "synthese") loadContractMarkdown();
    });
  }
  showStep("amorcage");
}

document.addEventListener("DOMContentLoaded", init);
